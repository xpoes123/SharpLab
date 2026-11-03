"""Spoiler sync on the wall service: the per-tile delay store, ESPN play-by-play timing, tap calibration, the scorebug
tracker (fake OCR), real OCR on frames grabbed from the box's tiles (skipped without tesseract), and the /sync +
overlay-bets endpoints."""
import json
from pathlib import Path

import pytest
from PIL import Image

from tests.test_wall_service import FakeDriver, FakeHeadless, FakeScores, board, client, url
from wall import scorebug, sync
from wall.bets import BetsFeed
from wall.instance import Instance
from wall.engine import Session
from wall.llm import LLMGuard
from wall.presets import PresetStore
from wall.server import Cfg, build

FX = Path(__file__).parent / "fixtures" / "wall_frames"
GSW = sync.score_plays(json.loads((FX / "pbp_gsw.json").read_text()))
LAC = sync.score_plays(json.loads((FX / "pbp_lac.json").read_text()))


# ---- the store ------------------------------------------------------------------------------------------------------------
def test_store_manual_beats_auto_beats_default_and_persists(tmp_path):
    s = sync.SyncStore(tmp_path / "sync.json")
    assert s.delay("u") == (45.0, "default")
    for lag in (38, 40, 130, 42, 41, 39, 400, -3):   # 130 / 400 / −3: misreads, dropped
        s.add_sample("u", lag)
    assert s.delay("u") == (40.0, "auto")              # median of the last 5 kept
    assert s.nudge("u", 5) == 45 and s.delay("u") == (45, "manual")
    assert s.nudge("u", -60) == 0                      # clamped 0–120
    s.set_show(False)
    again = sync.SyncStore(tmp_path / "sync.json")
    assert again.delay("u") == (0, "manual") and again.show is False
    again.use_auto("u")
    assert again.delay("u") == (40.0, "auto")
    assert sync.SyncStore(tmp_path / "nope.json").show is True


def test_store_survives_a_corrupt_file(tmp_path):
    (tmp_path / "sync.json").write_text("{nope")
    assert sync.SyncStore(tmp_path / "sync.json").delay("x") == (45.0, "default")


# ---- ESPN timing --------------------------------------------------------------------------------------------------------------
def test_score_plays_first_time_each_score_appears_in_time_order():
    assert all(a[0] <= b[0] for a, b in zip(GSW, GSW[1:]))
    assert len({(a, h) for _, a, h in GSW}) == len(GSW)
    assert sync.when(GSW, (97, 104)) == pytest.approx(1793683476.0)
    nfl = {"drives": {"previous": [{"plays": [{"wallclock": "2026-11-02T18:10:00Z", "awayScore": 7, "homeScore": 0}]}]}}
    assert sync.score_plays(nfl) == [(sync._ts("2026-11-02T18:10:00Z"), 7, 0)]


def test_tap_picks_the_scoring_play_nearest_the_current_delay():
    t = sync.when(GSW, (100, 104))
    assert sync.tap_delay(GSW, t + 40, current=45) == 40
    assert sync.tap_delay(GSW, t + 1, current=45) is None or sync.tap_delay(GSW, t + 1, current=45) >= 2
    assert sync.tap_delay([], t, 45) is None


# ---- the tracker (fake OCR: the frame *is* the score) ----------------------------------------------------------------------
@pytest.fixture
def fake_ocr(monkeypatch):
    bug = scorebug.Bug(scorebug.Word(0, .4, .85, .03, .05), scorebug.Word(0, .6, .85, .03, .05), (.3, .8, .7, .9))
    monkeypatch.setattr(sync.scorebug, "find", lambda img, pairs, ocr=None, width=0: bug if img in pairs else None)
    monkeypatch.setattr(sync.scorebug, "read", lambda img, b, ocr=None, part=None: img)


def run(frames, plays, t0=0.0, step=2.0):
    tr, lags, t = sync.Tracker(), [], t0
    for f in frames:
        lag = tr.step(lambda: f, t, plays)
        if lag is not None:
            lags.append(round(lag, 1))
        t += step
    return tr, lags


def test_tracker_times_a_score_change_from_its_first_sighting(fake_ocr):
    t97 = sync.when(GSW, (97, 104))
    t100 = sync.when(GSW, (100, 104))
    # the picture shows 97–104, then 100–104 appears 40 s after ESPN's play (and is read twice running)
    start = t100 + 40 - 6
    tr, lags = run([(97, 104), (97, 104), (97, 104), (100, 104), (100, 104), (100, 104)], GSW, t0=start)
    assert lags == [40.0]
    assert t97 < t100


def test_tracker_ignores_one_off_misreads_older_scores_and_unknown_pairs(fake_ocr):
    t100 = sync.when(GSW, (100, 104))
    frames = [(100, 104), (102, 104), (100, 104), (97, 104), (100, 104), (55, 55), (100, 104)]
    assert run(frames, GSW, t0=t100 + 30)[1] == []


def test_tracker_searches_again_after_losing_the_bug(fake_ocr):
    tr, _ = run([(100, 104)] + [None] * sync.LOST_AFTER, GSW, t0=sync.when(GSW, (100, 104)) + 30)
    assert tr.bug is None


# ---- real OCR on the box's frames ----------------------------------------------------------------------------------------
def frame(name):
    """The fixtures keep only the bottom band of a 1920×1080 grab: put it back where it was."""
    band = Image.open(FX / f"{name}.jpg").convert("RGB")
    img = Image.new("RGB", (1920, 1080))
    img.paste(band, (0, 1080 - band.height))
    return img


@pytest.mark.skipif(not scorebug.available(), reason="tesseract not installed (it is on the homelab)")
@pytest.mark.parametrize("name,pair,plays,width", [
    ("gsw_97_104", (97, 104), GSW, 1920), ("gsw_100_104", (100, 104), GSW, 1920),
    # the Clippers' bug (dark digits on light grey) only reads on some grabs: the tracker retries every 20 s, so it's
    # a known-flaky case here, not a gate
    pytest.param("lac_98_105", (98, 105), LAC, 1920, marks=pytest.mark.xfail(strict=False, reason="dark-on-grey bug, OCR hit-or-miss")),
    ("lal_114_134", (114, 134), None, 2880)])
def test_real_scorebugs_are_found_and_read(name, pair, plays, width):
    img = frame(name)
    pairs = sync.recent_pairs(plays, sync.when(plays, pair) + 60) if plays else [pair, (112, 134), (114, 131)]
    bug = scorebug.find(img, pairs, width=width)
    assert bug is not None
    assert scorebug.read(img, bug) == pair
    part = img.crop(tuple(round(v * s) for v, s in zip(bug.box, (1920, 1080, 1920, 1080))))   # only the bug, as grabbed
    assert scorebug.read(part, bug, part=bug.box) == pair


def test_parse_tsv_keeps_confident_short_numbers():
    tsv = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n" \
          "5\t1\t1\t1\t1\t1\t10\t20\t30\t40\t91.5\t105\n5\t1\t1\t1\t1\t2\t0\t0\t9\t9\t12\t98\n" \
          "5\t1\t1\t1\t1\t3\t0\t0\t9\t9\t95\tLAC\n5\t1\t1\t1\t1\t4\t0\t0\t9\t9\t95\t1058\n5\t1\t1\t1\t1\t5\t1\t2\t3\t4\t88\t'98|\n"
    assert scorebug.parse_tsv(tsv) == [(105, 10.0, 20.0, 30.0, 40.0), (98, 1.0, 2.0, 3.0, 4.0)]


# ---- the service: /sync + the overlay's bets ------------------------------------------------------------------------------
class FakeHttp:
    def __init__(self):
        self.asked = []

    def __call__(self, method, path, body):
        self.asked.append(body)
        return 200, {"games": {f"nfl:{g['id']}": [{"who": "David", "side": "YES", "label": "BUF win", "entry": 42.0,
                                                   "mark": 51.0, "dir": "up", "pct": 21.4, "size": "50+ ct"}]
                               for g in body["games"]}}


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr("wall.server.tv_active", lambda api: None)
    cfg = Cfg({"WALL_STATE_DIR": str(tmp_path), "WALL_API_TOKEN": "s3cret", "WALL_IMPORT_FROM": "", "WALL_TV_OUTPUT": ""})
    store = PresetStore(str(tmp_path))
    data = {"nfl": board("nfl")}
    tv = Instance("tv", Session("tv"), store, FakeScores(data), driver=FakeDriver(), run=lambda f: None)
    feed = BetsFeed(http=FakeHttp())
    feed.configured = True
    app = build(cfg, {"tv": tv}, FakeScores(data), store, guard=LLMGuard(mode="off"), bets=feed)
    live = [g for g in data["nfl"]["games"] if g["state"] == "in"]
    tile = url("nfl", sync.stream_urls("nfl", live[0])[0].rsplit("/", 1)[1])
    c = client(app)
    c.post("/i/tv/start", json={"urls": [tile, "https://a.tv/x"]})
    return app, c, tile, live[0], feed


def test_sync_endpoints_and_state(svc):
    app, c, tile, g, _ = svc
    st = c.get("/i/tv").json()["sync"]
    assert st["show"] is True and st["tiles"][0]["delay"] == 45 and st["tiles"][0]["game"]["id"] == g["id"]
    assert st["tiles"][1]["game"] is None
    assert c.post("/sync", json={"url": tile, "nudge": -1}).json()["walls"]["tv"][0]["delay"] == 40
    assert c.post("/sync", json={"url": "https://elsewhere", "nudge": 1}).status_code == 422
    assert c.post("/sync", json={"url": tile, "nudge": 99}).status_code == 422
    assert c.post("/sync", json={"url": tile, "auto": True}).json()["walls"]["tv"][0]["src"] == "default"
    assert c.post("/sync", json={"show": False}).json()["show"] is False
    assert c.post("/sync", json={"default": 30}).json()["default"] == 30
    assert c.post("/sync", json={"url": tile, "tap": True}).status_code == 409   # the fixture game has no play-by-play here
    remote = client(app, "100.81.31.34", "s3cret")
    assert remote.post("/sync", json={"show": True}).status_code == 200            # the watch page's owner, via the web
    assert remote.get("/i/tv/api/overlay/bets").status_code == 403


def test_overlay_bets_carry_rows_delays_and_obey_the_hide_toggle(svc):
    app, c, tile, g, feed = svc
    sync_store = app.state.sync
    sync_store.nudge(tile, -15)   # 30 s on that tile
    feed.poll([{"league": "nfl", "id": g["id"], "away": g["away"]["abbr"], "home": g["home"]["abbr"], "start": g["start"],
                "delay": sync_store.delay(tile)[0]}])
    assert feed.http.asked[-1]["games"][0]["delay"] == 30
    b = c.get("/i/tv/api/overlay/bets").json()
    assert b["n"] == 2 and b["delays"] == {g["id"]: 30} and b["tiles"][0]["rows"][0]["who"] == "David"
    assert b["tiles"][0]["rows"][0]["pct"] == 21 and b["tiles"][1]["rows"] == []
    c.post("/sync", json={"show": False})
    b = c.get("/i/tv/api/overlay/bets").json()
    assert b["show"] is False and all(t["rows"] == [] for t in b["tiles"])
    assert b["delays"] == {g["id"]: 30}   # the board stays delayed with bets hidden


def test_feed_goes_blank_when_the_web_stays_down():
    now = [1000.0]
    calls = iter([(200, {"games": {"nfl:1": [{"who": "D"}]}}), (502, None), (502, None)])
    f = BetsFeed(http=lambda m, p, b: next(calls), clock=lambda: now[0])
    f.poll([{"id": "1"}])
    now[0] += 30
    f.poll([{"id": "1"}])
    assert f.games                      # a short blip keeps the last answer
    now[0] += 60
    f.poll([{"id": "1"}])
    assert f.games == {}
