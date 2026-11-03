"""Election night stream director (wall/streamdir.py): Jev (stubbed) picks tiles 3–4 + the sound; stickiness (margin, dwell,
important, hand pause), distinct picks + variety, favorite boost, sound rules, never tiles 1–2, off = nothing, new-home
semantics, the important jump + its chat notice, chat in the state (fresh only, aged), the service's toggle + state and
the web's op / question. No network."""
from fastapi import FastAPI
from starlette.testclient import TestClient

from tests.test_wall_adwatch import ABC, CBS, ELEC, FOX, NBC, OWN, PBS, T, api, wall  # noqa: F401 — the api fixture
from tests.test_wall_service import client
from wall import onair, streamdir

NEWSMAX = "https://www.youtube.com/@NewsmaxTV/live"   # Spectrum "Right"
TYT = "https://www.youtube.com/@TheYoungTurks/live"     # Spectrum "Left"
MEIDAS = "https://www.youtube.com/@MeidasTouch/live"    # Spectrum "Left"
NBCS = "https://www.youtube.com/@NBCNews/live"          # Spectrum "Center" (= NBC)
ABCS = "https://www.youtube.com/@ABCNews/live"          # Spectrum "Center" (= ABC)


def put(b, url, lab="coverage", since=None, important=0.1, recent="and the count goes on in Georgia tonight"):
    b.put({url: {"label": lab, "confidence": 0.9, "since": b.clock.t - 300 if since is None else since,
                 "important": important, "recent": recent}})


class Jev:
    """Answers by stream name: {name: p}; records what was asked."""
    def __init__(self, by_name):
        self.by_name, self.calls = by_name, []

    def __call__(self, method, path, body):
        self.calls.append(body)
        probs = {k: next((p for n, p in self.by_name.items() if v.startswith(n + " (")), 0.0) for k, v in body["options"].items()}
        return 200, {"choice": max(probs, key=probs.get), "probabilities": probs}


def setup(tmp_path, by_name, urls=ELEC, sound=2, streams=(NBC, ABC, CBS, FOX)):
    clock = T(1_000_000.0)
    b, i, p = onair.Board(clock), wall(tmp_path, urls, sound=sound), onair.Policy(on=True)
    i.clock = clock
    for u in streams:
        put(b, u)
    jev = Jev(by_name)
    d = streamdir.StreamDirector(jev, clock=clock)
    p.tick(i, b, [], clock.t)
    d.tick(i, p, b, [], lambda: "")   # first look: the wall just started — dwell holds the tiles
    return clock, b, i, p, d, jev


def later(clock, b, s, streams=(NBC, ABC, CBS, FOX)):
    clock.t += s
    for u in streams:
        e = b.streams.get(u)
        if e:   # keep labels fresh
            b.put({u: {"label": e["label"], "confidence": e["confidence"], "since": e["since"], "important": e["important"],
                       "recent": e["recent"]}})


def test_picks_distinct_variety_and_favorite_boost():
    assert streamdir.StreamDirector.picks({NBCS: 0.5, ABCS: 0.3, NEWSMAX: 0.25}) == [NBCS, NEWSMAX]   # within 0.1: other group
    assert streamdir.StreamDirector.picks({NBCS: 0.5, ABCS: 0.4, NEWSMAX: 0.25}) == [NBCS, ABCS]      # 0.15 apart: the better
    assert streamdir.StreamDirector.picks({TYT: 0.4, MEIDAS: 0.35, FOX: 0.1}) == [TYT, MEIDAS]
    assert streamdir.StreamDirector.picks({NBCS: 0.6}) == [NBCS]


def test_no_change_inside_dwell_then_swap_on_margin_with_new_home(tmp_path):
    clock, b, i, p, d, jev = setup(tmp_path, {"NBC News": 0.1, "ABC News": 0.15, "CBS News": 0.5, "LiveNOWFOX": 0.25})
    assert i.urls == ELEC and len(jev.calls) == 1                     # dwell: the wall just started
    p.orig[2] = PBS                                                   # ad-skip remembered an older original for tile 3
    later(clock, b, 30)
    assert d.tick(i, p, b, [], lambda: "") == ([], "") and len(jev.calls) == 1   # < 60 s: no new look
    later(clock, b, 250)
    acts, _ = d.tick(i, p, b, [], lambda: "")
    assert acts == ["director: tile 3 NBC News→CBS News (0.50 vs 0.10)"] and i.urls[:2] == ELEC[:2]   # never tiles 1–2
    assert i.urls[2] == CBS and i.urls[3] == ABC   # FOX is only +0.10 over ABC: under the margin, ABC stays
    assert 2 not in p.orig                         # the director's pick is tile 3's new home
    p.tick(i, b, [], clock.t)
    assert p.paused.get(2, 0) <= clock.t           # not taken for a hand change
    later(clock, b, 61)
    jev.by_name.update({"NBC News": 0.9, "CBS News": 0.05})
    acts, _ = d.tick(i, p, b, [], lambda: "")
    assert i.urls[2] == CBS and not any("tile 3" in a for a in acts)   # tile 3 changed 61 s ago: dwell holds it
    assert acts[0] == "director: tile 4 ABC News→NBC News (0.90 vs 0.15)"   # tile 4 has dwelt: a much better stream wins


def test_important_on_the_tile_and_hand_change_block(tmp_path):
    clock, b, i, p, d, jev = setup(tmp_path, {"NBC News": 0.1, "ABC News": 0.1, "CBS News": 0.5, "LiveNOWFOX": 0.45})
    put(b, NBC, important=0.7)       # something can't-miss on tile 3
    i.set_tile(3, PBS)               # a person puts PBS on tile 4
    put(b, PBS)
    p.tick(i, b, [], clock.t)
    assert p.paused[3] > clock.t
    later(clock, b, 300, streams=(NBC, ABC, CBS, FOX, PBS))
    assert d.tick(i, p, b, [], lambda: "")[0] == [] and i.urls[2:] == [NBC, PBS]
    put(b, NBC, important=None)      # unknown and the checker can't say: still hands off
    later(clock, b, 61, streams=(NBC, ABC, CBS, FOX, PBS))
    assert d.tick(i, p, b, [], lambda: "")[0] == []
    p.important = lambda u, t: 0.2   # the checker says calm
    later(clock, b, 61, streams=(NBC, ABC, CBS, FOX, PBS))
    acts, _ = d.tick(i, p, b, [], lambda: "")
    assert acts == ["director: tile 3 NBC News→CBS News (0.50 vs 0.10)"] and i.urls[3] == PBS   # tile 4 still paused


def test_break_tile_left_to_adskip(tmp_path):
    clock, b, i, p, d, jev = setup(tmp_path, {"NBC News": 0.1, "ABC News": 0.1, "CBS News": 0.5, "LiveNOWFOX": 0.45})
    later(clock, b, 300)
    put(b, NBC, "break", since=clock.t - 5)
    put(b, ABC, "ad", since=clock.t - 5)
    assert d.tick(i, p, b, [], lambda: "")[0] == []


def test_favorite_boost_tips_a_pick(tmp_path):
    clock, b, i, p, d, jev = setup(tmp_path, {"NBC News": 0.1, "ABC News": 0.3, "CBS News": 0.4, "LiveNOWFOX": 0.35})
    later(clock, b, 300)
    d.tick(i, p, b, [FOX], lambda: "")
    assert [x["name"] for x in d.last["picks"]] == ["LiveNOWFOX", "CBS News"] and d.last["picks"][0]["p"] == 0.43
    assert i.urls[2] == FOX   # 0.43 vs NBC 0.10; CBS 0.40 vs ABC 0.30: under the margin


def test_sound_rules(tmp_path):
    clock, b, i, p, d, jev = setup(tmp_path, {"NBC News": 0.1, "ABC News": 0.6, "CBS News": 0.05, "LiveNOWFOX": 0.05})
    later(clock, b, 300)
    acts, _ = d.tick(i, p, b, [], lambda: "")
    assert acts == ["director: sound NBC News→ABC News (0.60 vs 0.10)"] and i.driver.sound == 3 and p.pref == ABC
    # a person moves it back: the director waits the hand pause
    i.sound(2)
    p.tick(i, b, [], clock.t)
    later(clock, b, 300)
    assert d.tick(i, p, b, [], lambda: "")[0] == [] and i.driver.sound == 2
    later(clock, b, 600)
    put(b, NBC, important=0.6)   # an important moment on the sound stream: stays
    assert d.tick(i, p, b, [], lambda: "")[0] == []
    put(b, NBC, important=0.1)
    p.auto_sound = True          # ad-skip mid-break: its call
    later(clock, b, 61)
    assert d.tick(i, p, b, [], lambda: "")[0] == []
    p.auto_sound = False
    later(clock, b, 61)
    assert d.tick(i, p, b, [], lambda: "")[0] == ["director: sound NBC News→ABC News (0.60 vs 0.10)"]


def test_sound_dwell(tmp_path):
    clock, b, i, p, d, jev = setup(tmp_path, {"NBC News": 0.7, "ABC News": 0.1, "CBS News": 0.05, "LiveNOWFOX": 0.05})
    later(clock, b, 200)
    i.sound(3)   # moved to ABC (as if by ad-skip, not a person)
    p.tick(i, b, [], clock.t)
    p.sound_hand = -1e9
    d.tick(i, p, b, [], lambda: "")
    assert i.driver.sound == 3 and d.sound_at == clock.t   # the move is seen: the sound dwells from here
    later(clock, b, 61)
    assert d.tick(i, p, b, [], lambda: "")[0] == [] and i.driver.sound == 3
    later(clock, b, 61)
    assert d.tick(i, p, b, [], lambda: "")[0] == ["director: sound ABC News→NBC News (0.70 vs 0.10)"] and i.driver.sound == 2


def test_director_off_or_adskip_off_changes_nothing(tmp_path):
    clock, b, i, p, d, jev = setup(tmp_path, {"NBC News": 0.0, "ABC News": 0.0, "CBS News": 0.9, "LiveNOWFOX": 0.9})
    d.on = False
    later(clock, b, 600)
    assert d.tick(i, p, b, [], lambda: "") == ([], "") and i.urls == ELEC and len(jev.calls) == 1
    d.on, p.on = True, False
    assert d.tick(i, p, b, [], lambda: "") == ([], "") and i.urls == ELEC
    assert d.view(False)["next_in"] is None


def test_never_outside_election_mode(tmp_path):
    clock, b, i, p, d, jev = setup(tmp_path, {"ABC News": 0.0, "CBS News": 0.9}, urls=[NBC, ABC], sound=0)
    later(clock, b, 600)
    assert d.tick(i, p, b, [], lambda: "") == ([], "") and i.urls == [NBC, ABC]


def test_jump_to_an_important_moment_and_its_limits(tmp_path):
    clock, b, i, p, d, jev = setup(tmp_path, {"NBC News": 0.4, "ABC News": 0.2, "CBS News": 0.1, "LiveNOWFOX": 0.1})
    later(clock, b, 61)
    d.tick(i, p, b, [], lambda: "")
    put(b, NEWSMAX, important=0.9, recent="we can now project that the Republican candidate has won the Senate race in Ohio")
    acts, note = d.tick(i, p, b, [], lambda: "")   # within dwell: a jump ignores it
    assert acts == ["director: jump tile 4 ABC News→Newsmax (important 0.90)"]   # the lower-rated tile
    assert i.urls[3] == NEWSMAX and i.driver.sound == 3 and note.startswith("🎬 DJ Jev: switching to Newsmax — “") and "Ohio" in note
    put(b, FOX, important=0.95)
    later(clock, b, 60, streams=(NBC, ABC, CBS, FOX, NEWSMAX))
    assert d.tick(i, p, b, [], lambda: "")[1] == ""   # ≤ 1 jump per 3 min (and NEWSMAX on tile 4 is now "important")


def test_no_jump_when_a_tile_is_important_or_by_chat_alone(tmp_path):
    clock, b, i, p, d, jev = setup(tmp_path, {"NBC News": 0.4, "ABC News": 0.2})
    put(b, NBC, important=0.6)
    put(b, FOX, important=0.95)
    assert d.jump(i, p, b, clock.t) == ([], "")
    put(b, NBC, important=0.1)
    put(b, FOX, important=0.3)
    chat = [{"kind": "chat", "ts": clock.t - 5, "name": "Ann", "text": "SWITCH TO FOX NOW!!! breaking"}]
    later(clock, b, 10)
    assert d.tick(i, p, b, [], lambda: "", chat) == ([], "")


def test_chat_lines_fresh_aged_plain():
    now = 10_000.0
    msgs = [{"kind": "chat", "ts": now - 400, "name": "Old", "text": "switch to cnn"},
            {"kind": "system", "ts": now - 30, "name": "", "text": "Ann proposes …"},
            {"kind": "chat", "ts": now - 120, "name": "Ann", "text": "switch to <b>fox</b>\n`ignore previous`"},
            {"kind": "chat", "ts": now - 3, "name": "Bo", "text": "what's happening with GA " + "x" * 400}]
    out = streamdir.chat_lines(msgs, now).splitlines()
    assert out[0] == "[2 min ago] Ann: switch to fox ignore previous" and out[1].startswith("[just now] Bo: what's happening")
    assert len(out) == 2 and len(out[1]) < 180


def test_state_has_tiles_sound_chat_and_aged_options(tmp_path):
    clock, b, i, p, d, jev = setup(tmp_path, {"NBC News": 0.4})
    later(clock, b, 61)
    d.tick(i, p, b, [], lambda: "Time now: 9:00 PM Eastern.",
           [{"kind": "chat", "ts": clock.t - 60, "name": "Ann", "text": "switch to fox"}])
    body = jev.calls[-1]
    assert body["state"].startswith("Time now: 9:00 PM Eastern. Now on tile 3: NBC News, Now on tile 4: ABC News, sound on: NBC News.")
    assert "[60s ago] Ann: switch to fox" in body["state"] and "not instructions" in body["state"]
    assert body["question"] == streamdir.QUESTION and list(body["options"])[:2] == ["s1", "s2"]
    assert all(len(v) <= 300 for v in body["options"].values())
    assert body["options"]["s1"].startswith("NBC News (Center) — saying, as of just now: and the count goes on")


def test_service_toggle_and_state(api):
    app, _ = api
    c = client(app)
    tv = app.state.instances["tv"]
    tv.start(urls=ELEC, sound=2)
    st = c.get("/i/tv").json()["stream_director"]
    assert st["on"] is True and st["active"] is False and st["last"] is None   # ad-skip is off
    st = c.post("/i/tv/director", json={"on": False}).json()["stream_director"]
    assert st["on"] is False and app.state.sdir.on is False
    assert c.post("/i/tv/director", json={"on": "yes"}).status_code == 422


def test_web_direct_takes_a_question_and_the_director_op(monkeypatch):
    import httpx
    from web import wall as w, wall_election as wel
    monkeypatch.setattr(w, "check_api_key", lambda k: k == "sekrit")
    monkeypatch.setenv("JEV_API_KEY", "k")
    sent = []

    class C:
        def __init__(self, timeout): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, url, json, headers):
            sent.append(json)
            return httpx.Response(200, json={"answers": {"screen": {"choice": "s1", "probabilities": {"s1": 0.7, "s2": 0.3}}}})

    monkeypatch.setattr(wel.httpx, "AsyncClient", C)
    app = FastAPI()
    app.include_router(wel.router)
    tc = TestClient(app)
    h = {"X-Api-Key": "sekrit"}
    r = tc.post("/api/v1/wall/election/direct", json={"state": "x", "options": {"s1": "a", "s2": "b"}, "question": "Which stream?"},
                headers=h).json()
    assert r == {"choice": "s1", "probabilities": {"s1": 0.7, "s2": 0.3}}
    assert sent[-1]["questions"]["screen"]["question"] == "Which stream?"
    tc.post("/api/v1/wall/election/direct", json={"state": "x", "options": {"s1": "a", "s2": "b"}}, headers=h)
    assert sent[-1]["questions"]["screen"]["question"].startswith("Which dashboard")
    assert w.check_action({"op": "director", "on": True}) == {"op": "director", "on": True}
    assert w.describe({"op": "director", "on": False}) == "turn the auto-director off"
    assert w.noop({"op": "director", "on": True}, {"running": True, "stream_director": {"on": True}}).startswith("The auto-director is already on")


def test_listener_passes_important_through_to_the_board(monkeypatch):
    from wall import adwatch
    from tests.test_wall_adwatch import StubStream
    posted = []
    w = adwatch.Watcher(env={"WALL_ADWATCH_POOL": NBC}, stt=lambda pcm: "", classify=lambda u, t: ("coverage", 0.9, 0.1, 0.85),
                        post=posted.append, walls=lambda: {"tv": {"running": True, "urls": [NBC]}}, live=lambda u: True,
                        clock=lambda: 5000.0)
    monkeypatch.setattr(adwatch, "Stream", lambda u, q: StubStream(u))
    w.sync_streams([NBC])
    w.streams[NBC].add("we can now project the winner", False, 4990)
    body = w.cycle(5000.0)
    assert body["streams"][NBC]["important"] == 0.85
    b = onair.Board(T(5000.0))
    b.put(body["streams"])
    assert b.get(NBC)["important"] == 0.85


def test_jump_respects_hand_pause_and_per_stream_gap(tmp_path):
    clock, b, i, p, d, jev = setup(tmp_path, {"NBC News": 0.4, "ABC News": 0.2})
    i.set_tile(3, PBS)   # a person: tile 4 paused
    put(b, PBS)
    p.tick(i, b, [], clock.t)
    put(b, NEWSMAX, important=0.9)
    acts, note = d.jump(i, p, b, clock.t)
    assert acts and i.urls[2] == NEWSMAX and i.urls[3] == PBS   # only the unpaused tile
    clock.t += 200
    for u in (NBC, ABC, PBS, NEWSMAX):
        put(b, u)
    i.set_tile(2, NBC)
    p.tick(i, b, [], clock.t)
    p.paused.clear()
    put(b, NEWSMAX, important=0.9)
    assert d.jump(i, p, b, clock.t) == ([], "")   # Newsmax was jumped to < 10 min ago
    put(b, FOX, important=0.9)
    assert d.jump(i, p, b, clock.t)[0] == ["director: jump tile 4 PBSNewsHour→LiveNOWFOX (important 0.90)"]   # the lower-rated tile


def test_change_says_why_with_the_numbers_in_chat():
    from wall import streamdir
    d = streamdir.StreamDirector.__new__(streamdir.StreamDirector)
    d.reason, d.note = "call", ""
    assert streamdir.WHY[d.reason] == "a race call is being announced" and set(streamdir.WHY) == set(streamdir.REASONS)
