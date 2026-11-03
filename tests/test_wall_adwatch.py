"""Election-night ad-skip: the keyword pass (wall/adwatch.py), the wall's policy + favorites (wall/onair.py), the service's
POST /adwatch + state + toggles (wall/server.py), the web's classify endpoint and watch-page ops. No network, no GPU."""
from collections import deque
import asyncio

import pytest
from starlette.testclient import TestClient

from tests.test_wall_service import FakeDriver, FakeScores, api, client  # noqa: F401 — the api fixture
from wall import adwatch, onair
from wall.engine import Session
from wall.instance import Instance
from wall.presets import PresetStore

OWN = "http://127.0.0.1:7795/p/election/"
NBC, ABC, CBS, FOX, PBS = (f"https://www.youtube.com/@{c}/live" for c in ("NBCNews", "ABCNews", "CBSNews", "LiveNOWFOX", "PBSNewsHour"))
LOCAL = "https://www.youtube.com/@WSBTV/live"


# ---- the keyword pass -------------------------------------------------------------------------------------------------------
def test_movie_arg_escapes_colons_and_quotes():
    assert adwatch.movie_arg("https://h/x:y/it's") == "'https\\://h/x\\:y/it\\'\\''s'"


def test_rms_and_wav():
    assert adwatch.rms(b"\0\0" * 100) == 0 and adwatch.rms((1000).to_bytes(2, "little", signed=True) * 100) == 1000
    assert adwatch.wav(b"\0\0" * 16000)[:4] == b"RIFF"


class StubStream:
    def __init__(self, url):
        self.url = url
        self.text, self.status, self.last_classify = deque(), {"label": "unknown", "confidence": 0, "since": 0, "text_tail": ""}, 0.0

    window = adwatch.Stream.window
    add = adwatch.Stream.add
    close = lambda self: None  # noqa: E731


def test_watcher_always_asks_the_model_and_posts(monkeypatch):
    asked, posted = [], []
    w = adwatch.Watcher(env={"WALL_ADWATCH_POOL": f"{NBC},{PBS}"}, stt=lambda pcm: "",
                        classify=lambda u, t: asked.append(u) or (("ad", 0.95) if "doctor" in t else ("nonsense", 0) if "zzz" in t else ("coverage", 0.7)),
                        post=posted.append,
                        walls=lambda: {"tv": {"running": True, "urls": [OWN + "races", OWN + "map?view=S", LOCAL, NBC],
                                              "favorites": [ABC], "adwatch": {"orig": {"3": CBS}}}},
                        live=lambda u: u == NBC, clock=lambda: 5000.0)
    assert w.wanted() == [LOCAL, NBC, CBS, ABC]   # tiles, the swapped-away original, favorites, then the live pool
    monkeypatch.setattr(adwatch, "Stream", lambda u, q: StubStream(u))
    w.sync_streams(w.wanted())
    w.streams[LOCAL].add("Ask your doctor. Side effects include. Call now.", False, 4990)
    w.streams[NBC].add("Of course, what happens next is anyone's guess", False, 4990)
    body = w.cycle(5000.0)
    assert body["streams"][LOCAL]["label"] == "ad" and body["streams"][NBC]["label"] == "coverage"
    assert sorted(asked) == sorted([LOCAL, NBC]) and posted == [body]   # every label is the model's, keywords decide nothing
    w.streams[NBC].add("zzz", False, 5100)
    assert w.cycle(5100.0)["streams"][NBC]["label"] == "unknown"        # no usable answer: unknown, never a keyword guess
    w.sync_streams([NBC])
    assert list(w.streams) == [NBC]
    w.walls = lambda: {"tv": {"running": False, "urls": []}}
    assert w.wanted() == []   # no wall up: listen to nothing


# ---- the policy ----------------------------------------------------------------------------------------------------------------
def wall(tmp_path, urls, sound=2):
    i = Instance("tv", Session("tv"), PresetStore(str(tmp_path)), FakeScores({}), driver=FakeDriver(), run=lambda f: None)
    i.start(urls=urls, sound=sound)
    i.driver.sound = sound
    return i


class T:
    def __init__(self, t=10_000.0):
        self.t = t

    def __call__(self):
        return self.t


def label(board, url, lab, since, conf=0.9):
    board.put({url: {"label": lab, "confidence": conf, "since": since}})


ELEC = [OWN + "races", OWN + "map?view=S", NBC, ABC]


def test_sound_leaves_an_ad_and_returns_to_the_manual_pick(tmp_path):
    clock = T()
    b, i, p = onair.Board(clock), wall(tmp_path, ELEC, sound=2), onair.Policy(on=True)
    now = clock.t
    label(b, NBC, "coverage", now - 100)
    label(b, ABC, "coverage", now - 100)
    assert p.tick(i, b, [], now) == []
    label(b, NBC, "ad", now - 5)
    assert p.tick(i, b, [], now) == [] and i.driver.sound == 2   # not 10 s yet
    label(b, NBC, "ad", now - 11)
    assert p.tick(i, b, [], now) == ["sound 3→4"] and i.driver.sound == 3
    assert p.tick(i, b, [], now) == []
    label(b, NBC, "coverage", now - 3)
    assert p.tick(i, b, [], now) == []   # back on air, but not for 10 s yet
    label(b, NBC, "coverage", now - 12)
    assert p.tick(i, b, [], now) == ["sound 4→3"] and i.driver.sound == 2   # home to the stream people picked
    assert not p.auto_sound


def test_sound_never_to_a_data_page_and_a_manual_move_sets_the_preference(tmp_path):
    clock = T()
    b, i, p = onair.Board(clock), wall(tmp_path, ELEC, sound=2), onair.Policy(on=True)
    now = clock.t
    p.tick(i, b, [], now)
    i.sound(3)   # a person moves the sound to ABC
    p.tick(i, b, [], now)
    assert p.pref == ABC and not p.auto_sound
    label(b, ABC, "break", now - 20)
    label(b, NBC, "unknown", now)
    assert p.tick(i, b, [], now) == [] and i.driver.sound == 3   # nowhere on air to go (data pages have no sound)


def test_tile_swaps_after_60s_and_swaps_back_after_30s_of_coverage(tmp_path):
    clock = T()
    b, i, p = onair.Board(clock), wall(tmp_path, ELEC, sound=3), onair.Policy(on=True)
    now = clock.t
    for u in (NBC, ABC, CBS):
        label(b, u, "coverage", now - 300)
    label(b, FOX, "coverage", now - 300, conf=0.6)
    p.tick(i, b, [], now)
    label(b, NBC, "break", now - 50)
    assert p.tick(i, b, [], now) == []
    label(b, NBC, "break", now - 61)
    assert p.tick(i, b, [], now) == [f"tile 3 break → {CBS}"]   # the surest on-air stream not up already
    assert i.urls[2] == CBS and p.orig == {2: NBC} and i.urls[:2] == ELEC[:2]
    label(b, NBC, "coverage", now - 40)
    now += 60
    clock.t = now
    assert p.tick(i, b, [], now) == []   # one swap per tile per 2 min
    now += 61
    clock.t = now
    label(b, NBC, "coverage", now - 40)
    assert p.tick(i, b, [], now) == [f"tile 3 home → {NBC}"] and i.urls[2] == NBC and p.orig == {}


def test_never_the_dashboards_or_our_pages_and_off_does_nothing(tmp_path):
    clock = T()
    urls = [NBC, OWN + "races", OWN + "turnout", ABC]   # a stream on tile 1 in election mode stays put
    b, i = onair.Board(clock), wall(tmp_path, urls, sound=0)
    now = clock.t
    for u in (CBS, FOX):
        label(b, u, "coverage", now - 300)
    label(b, NBC, "offtopic", now - 500)
    label(b, ABC, "offtopic", now - 500)
    off = onair.Policy(on=False)
    assert off.tick(i, b, [], now) == [] and i.urls == urls
    p = onair.Policy(on=True)
    p.tick(i, b, [], now)
    assert i.urls[0] == NBC and i.urls[1:3] == urls[1:3] and i.urls[3] in (CBS, FOX)
    assert onair.stream_tiles(urls, True) == [3] and onair.stream_tiles([NBC, ABC], False) == [0, 1]


def test_hand_swaps_win_and_pause_the_tile_for_10_min(tmp_path):
    clock = T()
    b, i, p = onair.Board(clock), wall(tmp_path, ELEC, sound=3), onair.Policy(on=True)
    now = clock.t
    label(b, CBS, "coverage", now - 300)
    p.tick(i, b, [], now)
    label(b, NBC, "ad", now - 100)
    p.tick(i, b, [], now)
    assert i.urls[2] == CBS and p.orig == {2: NBC}
    i.set_tile(2, LOCAL)   # a vote puts a local station there (noticed on the next tick, every 2 s)
    p.tick(i, b, [], now)
    label(b, LOCAL, "ad", now - 100)
    label(b, FOX, "coverage", now - 300)
    now += 300
    assert p.tick(i, b, [], now) == [] and i.urls[2] == LOCAL and p.orig == {}   # forgot NBC; paused
    now += 301
    label(b, LOCAL, "ad", now - 100)
    label(b, FOX, "coverage", now - 300)
    assert p.tick(i, b, [], now) == [f"tile 3 ad → {CBS}"]   # the longest on air of the equally sure ones


def test_stale_labels_count_as_unknown(tmp_path):
    clock = T()
    b = onair.Board(clock)
    label(b, NBC, "ad", clock.t - 100)
    assert b.get(NBC)
    clock.t += onair.STALE_S + 1
    assert b.get(NBC) is None and b.view(NBC) is None
    assert b.put({"javascript:x": {"label": "ad"}, NBC: {"label": "nope"}}) == 0
    with pytest.raises(ValueError):
        b.put([])


# ---- favorites ------------------------------------------------------------------------------------------------------------------
def test_favorites_persist_and_cap_at_two(tmp_path):
    f = onair.Favorites(tmp_path / "favorites.json")
    f.set(NBC, True)
    f.set(ABC, True)
    assert f.set(CBS, True) == [ABC, CBS]   # a third star replaces the oldest
    assert onair.Favorites(tmp_path / "favorites.json").urls == [ABC, CBS]
    assert f.set(ABC, False) == [CBS]
    assert onair.fav_tile(ELEC, [NBC], CBS, True) == 3 and onair.fav_tile(ELEC, [ABC], CBS, True) == 2
    assert onair.fav_tile(ELEC[:2], [], CBS, True) == 2


def test_a_swapped_tile_goes_home_to_its_favorite_after_20s(tmp_path):
    clock = T()
    b, i, p = onair.Board(clock), wall(tmp_path, ELEC, sound=3), onair.Policy(on=True)
    now = clock.t
    favs = [FOX]   # FOX is starred but not up; NBC (not a favorite) is on tile 3
    label(b, CBS, "coverage", now - 300)
    label(b, FOX, "ad", now - 5)
    p.tick(i, b, favs, now)
    label(b, NBC, "break", now - 70)
    assert p.tick(i, b, favs, now) == [f"tile 3 break → {CBS}"] and p.orig == {2: NBC}
    now += 130
    label(b, NBC, "coverage", now - 25)   # the original is back for 25 s: not enough (30 s) — and the favorite comes first
    label(b, FOX, "coverage", now - 21)   # the favorite is on air ≥ 20 s
    label(b, CBS, "coverage", now - 400)
    assert p.tick(i, b, favs, now) == [f"tile 3 home → {FOX}"] and i.urls[2] == FOX and p.orig == {}


def test_the_sound_prefers_a_favorite_on_air(tmp_path):
    clock = T()
    b, i, p = onair.Board(clock), wall(tmp_path, [NBC, CBS, ABC], sound=0), onair.Policy(on=True)
    now = clock.t
    label(b, CBS, "coverage", now - 100, conf=0.95)
    p.tick(i, b, [ABC], now)
    label(b, NBC, "ad", now - 15)
    label(b, ABC, "coverage", now - 100, conf=0.5)
    assert p.tick(i, b, [ABC], now) == ["sound 1→3"]   # the favorite, though it's the less sure one
    label(b, NBC, "coverage", now - 15)
    assert p.tick(i, b, [ABC], now) == []   # it stays on the favorite: favorites first


# ---- the wall service: POST /adwatch, state, toggle, favorites ------------------------------------------------------------------
def test_service_adwatch_post_state_toggle_and_favorites(api):  # noqa: F811
    app, tmp = api
    c = client(app)
    st = c.post("/i/tv/start", json={"urls": [OWN + "races", OWN + "map?view=S", NBC, ABC]}).json()
    assert st["onair"] == [None] * 4 and st["favorites"] == [] and st["adwatch"]["on"] is False
    r = c.post("/adwatch", json={"streams": {NBC: {"label": "ad", "confidence": 0.9, "since": 1, "text_tail": "call now"},
                                             CBS: {"label": "coverage", "confidence": 0.8}}})
    assert r.json() == {"ok": True, "n": 2}
    st = c.get("/i/tv").json()
    assert st["onair"][2]["label"] == "ad" and st["onair"][3] is None
    assert [x["url"] for x in st["adwatch"]["pool"]] == [CBS]
    assert c.post("/i/tv/adwatch", json={"on": "yes"}).status_code == 422
    assert c.post("/i/tv/adwatch", json={"on": True}).json()["adwatch"]["on"] is True
    # tailnet: the web may toggle the discord wall, but /adwatch (the listener's posts) is loopback only
    remote = client(app, "100.81.31.34", "s3cret")
    assert remote.post("/adwatch", json={"streams": {}}).status_code == 403
    # star CBS: it isn't up, so it goes on tile 3 or 4; it persists
    st = c.post("/i/tv/favorite", json={"url": CBS, "on": True}).json()
    assert st["favorites"] == [CBS] and CBS in st["urls"][2:] and st["urls"][:2] == [OWN + "races", OWN + "map?view=S"]
    assert (tmp / "favorites.json").exists()
    assert c.post("/i/tv/favorite", json={"url": OWN + "races", "on": True}).status_code == 422
    st = c.post("/i/tv/favorite", json={"url": NBC if NBC in st["urls"] else ABC, "on": True}).json()
    assert len(st["favorites"]) == 2 and st["urls"][2:].count(CBS) == 1
    assert c.post("/i/tv/favorite", json={"url": CBS, "on": False}).json()["favorites"] == st["favorites"][1:]


def test_discord_mirror_toggles_the_tvs_policy(api, monkeypatch):  # noqa: F811
    app, _ = api
    c = client(app)
    monkeypatch.setattr("wall.server.tv_active", lambda a: "wall")
    c.post("/i/tv/start", json={"urls": [NBC, ABC]})
    disc = app.state.instances["discord"]
    disc.follow = True
    disc.urls = [NBC, ABC]
    remote = client(app, "100.81.31.34", "s3cret")
    st = remote.post("/i/discord/adwatch", json={"on": True}).json()
    assert app.state.policies["tv"].on and not app.state.policies["discord"].on and st["adwatch"]["on"]
    disc.urls = []


# ---- web: classify + ops ------------------------------------------------------------------------------------------------------
def test_classify_endpoint_caches_rate_limits_and_survives_failures(monkeypatch):
    from fastapi import FastAPI

    from web import wall as web_wall, wall_election as wel
    monkeypatch.setattr(web_wall, "check_api_key", lambda k: k == "sekrit")
    calls = []

    async def llm(text):
        calls.append(text)
        return {"label": "ad", "confidence": 0.93} if "doctor" in text else None

    monkeypatch.setattr(wel, "_classify_llm", llm)
    wel._cls["cache"].clear()
    wel._cls["last"].clear()
    app = FastAPI()
    app.include_router(wel.router)
    c = TestClient(app)
    path = "/api/v1/wall/election/classify"
    assert c.post(path, json={"stream": NBC, "text": "x"}).status_code == 401
    h = {"X-Api-Key": "sekrit"}
    assert c.post(path, json={"stream": NBC}, headers=h).status_code == 422
    r = c.post(path, json={"stream": NBC, "text": "ask your doctor"}, headers=h).json()
    assert r == {"label": "ad", "confidence": 0.93, "src": "llm"}
    assert c.post(path, json={"stream": ABC, "text": "ask your doctor"}, headers=h).json()["src"] == "cache"
    assert c.post(path, json={"stream": NBC, "text": "something else"}, headers=h).json() == \
        {"label": "ad", "confidence": 0.93, "src": "limited"}   # ≤ 1 call per stream per 15 s
    assert c.post(path, json={"stream": CBS, "text": "garbled"}, headers=h).json()["label"] == "unknown"   # bad answer
    assert len(calls) == 2

    async def slow(text):
        await asyncio.sleep(30)

    monkeypatch.setattr(wel, "_classify_llm", slow)
    monkeypatch.setattr(wel, "CLASSIFY_TIMEOUT_S", -1.9)   # wait_for(…, 0.1)
    assert c.post(path, json={"stream": FOX, "text": "anything"}, headers=h).json()["label"] == "unknown"


def test_web_ops_adwatch_and_favorite():
    from web import wall as web_wall
    from web.wall import check_action, describe, noop
    assert check_action({"op": "adwatch", "on": True}) == {"op": "adwatch", "on": True}
    with pytest.raises(ValueError):
        check_action({"op": "adwatch", "on": 1})
    fav = check_action({"op": "favorite", "url": NBC, "on": True, "label": "NBC News"})
    assert fav == {"op": "favorite", "url": NBC, "on": True, "label": "NBC News"}
    with pytest.raises(ValueError):
        check_action({"op": "favorite", "url": "javascript:x", "on": True})
    assert web_wall._route(fav) == ("POST", "/i/discord/favorite", {"url": NBC, "on": True})
    assert web_wall._route({"op": "adwatch", "on": False}) == ("POST", "/i/discord/adwatch", {"on": False})
    assert describe({"op": "adwatch", "on": True}) == "turn auto ad-skip on" and describe(fav) == "star NBC News"
    w = {"running": True, "urls": [NBC], "adwatch": {"on": True}, "favorites": [NBC]}
    assert noop({"op": "adwatch", "on": True}, w) and not noop({"op": "adwatch", "on": False}, w)
    assert noop(fav, w) and not noop({**fav, "on": False}, w)


@pytest.mark.anyio
async def test_only_the_owner_and_admins_switch_ad_skip(monkeypatch):
    from tests.test_wall_web import ADMIN, OWNER, Clock
    from web import wall as web_wall
    monkeypatch.setenv("WALL_OWNER_ID", OWNER)
    monkeypatch.setenv("WALL_ADMIN_IDS", ADMIN)
    done = []

    async def execute(a):
        done.append(a)
        return True, ""

    async def fetch_state():
        return {"running": True, "urls": [NBC], "adwatch": {"on": False}, "favorites": []}

    hub = web_wall.Hub(clock=Clock(), execute=execute, fetch_state=fetch_state)
    p, err = await hub.propose({"id": "x", "name": "x"}, {"op": "adwatch", "on": True})
    assert p is None and "owner" in err
    p, err = await hub.propose({"id": OWNER, "name": "David"}, {"op": "adwatch", "on": True})
    assert p and done == [{"op": "adwatch", "on": True}]


def test_classify_jev_first_escalates_when_unsure(monkeypatch):
    from web import wall_election as wel
    llm_calls = []

    async def llm(text):
        llm_calls.append(text)
        return {"label": "break", "confidence": 0.8}

    answers = {"anchors": {"label": "coverage", "confidence": 0.97}, "mumble": {"label": "ad", "confidence": 0.4}}

    async def jev(text):
        return answers.get(text)

    monkeypatch.setattr(wel, "_classify_llm", llm)
    monkeypatch.setattr(wel, "_classify_jev", jev)
    wel._cls["cache"].clear()
    wel._cls["last"].clear()
    assert asyncio.run(wel.classify(NBC, "anchors", now=0)) == {"label": "coverage", "confidence": 0.97, "src": "jev"}
    assert asyncio.run(wel.classify(ABC, "mumble", now=0)) == {"label": "break", "confidence": 0.8, "src": "llm"}   # unsure → Haiku
    assert asyncio.run(wel.classify(CBS, "no key", now=0))["src"] == "llm"                                          # no Jev answer → Haiku
    assert len(llm_calls) == 2
