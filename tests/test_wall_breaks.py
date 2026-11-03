"""Election night, preemptive break handling: the break log + clock model (wall/breaks.py), the preemptive sound move in
wall/onair.py (incl. "never on the clock alone", the important-check and the return when no break comes), the listener
passing "soon" through and the web's classify answering soon / important with a stubbed Jev. No network."""
import asyncio
import json

from starlette.testclient import TestClient

from tests.test_wall_adwatch import ABC, ELEC, NBC, StubStream, T, label, wall
from wall import adwatch, breaks, onair

H = 3600 * 500_000   # a whole hour


def history(board, url, hours=3, minutes=(18, 48), length=150):
    """Coverage → break at :18 and :48 for a few hours, as the listener would post it."""
    for h in range(hours):
        for m in minutes:
            start = H + h * 3600 + m * 60
            board.clock.t = start + 1
            label(board, url, "break", start)
            board.clock.t = start + length + 1
            label(board, url, "coverage", start + length)


def test_break_log_records_breaks_persists_and_reloads(tmp_path):
    path = tmp_path / "breaks.jsonl"
    b = onair.Board(T(H), log=breaks.BreakLog(path))
    label(b, NBC, "coverage", H - 100)
    label(b, NBC, "ad", H)
    label(b, NBC, "break", H + 60)          # ad → break: still the same break
    label(b, NBC, "unknown", H + 90)        # no answer doesn't end it
    b.clock.t = H + 200
    label(b, NBC, "coverage", H + 200)
    rows = [json.loads(x) for x in path.read_text().splitlines()]
    assert rows == [{"stream": NBC, "start": H, "end": H + 200, "label": "ad"}]
    again = breaks.BreakLog(path)
    assert again.of(NBC) == rows and again.of(ABC) == []


def test_clock_model_needs_three_breaks_and_finds_the_next_one(tmp_path):
    b = onair.Board(T(H), log=breaks.BreakLog(tmp_path / "b.jsonl"))
    history(b, NBC, hours=1)   # two breaks: says nothing yet
    assert b.breaks.predict(NBC, H + 3600 + 46 * 60) == {"next_likely": None, "p_soon": 0.0, "n_breaks": 2, "len_s": None}
    history(b, NBC, hours=3)
    at_46 = b.breaks.predict(NBC, H + 3 * 3600 + 46 * 60 + 45)   # 75 s ahead is 48:00
    assert at_46["n_breaks"] == 8 and at_46["next_likely"] == "~:48" and at_46["p_soon"] == 1.0 and at_46["len_s"] == 150
    at_30 = b.breaks.predict(NBC, H + 3 * 3600 + 30 * 60)
    assert at_30["p_soon"] == 0.0 and at_30["next_likely"] == "~:48"
    assert b.breaks.predict(NBC, H + 3 * 3600 + 50 * 60)["next_likely"] == "~:18"
    b.clock.t = H + 3 * 3600 + 30 * 60
    label(b, NBC, "coverage", b.clock.t - 100)
    assert b.view(NBC)["clock"] == {"next_likely": "~:48", "p_soon": 0.0, "n_breaks": 8}


def soon(board, url, p, since, conf=0.9, recent="and we're tracking the count"):
    board.put({url: {"label": "coverage", "confidence": conf, "since": since, "soon": p, "recent": recent}})


def setup(tmp_path, important=lambda u, t: 0.1, t=H + 10 * 3600 + 30 * 60):
    clock = T(t)
    b = onair.Board(clock, log=breaks.BreakLog(tmp_path / "b.jsonl"))
    i = wall(tmp_path, ELEC, sound=2)
    asked = []
    p = onair.Policy(on=True, important=(lambda u, txt: asked.append((u, txt)) or important(u, txt)) if important else None)
    label(b, ABC, "coverage", clock.t - 300)
    soon(b, NBC, None, clock.t - 300)
    p.tick(i, b, [], clock.t)
    return clock, b, i, p, asked


def test_preempts_the_sound_when_jev_says_a_break_is_coming(tmp_path):
    clock, b, i, p, asked = setup(tmp_path)
    now = clock.t
    soon(b, NBC, 0.8, now - 300)
    assert p.tick(i, b, [], now) == [] and asked == []          # 0.8 alone (no clock history) isn't enough
    soon(b, NBC, 0.9, now - 300)
    assert p.tick(i, b, [], now) == ["sound 3→4 (break soon)"] and i.driver.sound == 3
    assert asked == [(NBC, "and we're tracking the count")] and p.auto_sound and p.pre["from"] == NBC
    assert i.urls[2] == NBC   # tiles never move on a forecast


def test_never_on_the_clock_alone(tmp_path):
    clock, b, i, p, asked = setup(tmp_path, t=H + 10 * 3600 + 46 * 60 + 45)
    history(b, NBC, hours=3)   # the clock is sure a break starts at :48
    now = clock.t = H + 10 * 3600 + 46 * 60 + 45
    assert b.breaks.predict(NBC, now)["p_soon"] == 1.0
    label(b, ABC, "coverage", now - 300)
    soon(b, NBC, None, now - 300)                     # no Jev "soon" at all
    assert p.tick(i, b, [], now) == [] and i.driver.sound == 2
    soon(b, NBC, 0.3, now - 300)                      # Jev says no
    assert p.tick(i, b, [], now) == [] and i.driver.sound == 2 and asked == []
    soon(b, NBC, 0.65, now - 300)                     # both lean yes: clock ≥ 0.6 and Jev ≥ 0.6
    assert p.tick(i, b, [], now) == ["sound 3→4 (break soon)"]


def test_important_check_blocks_then_rechecks_and_failures_mean_stay(tmp_path):
    answers = [0.8, None]
    clock, b, i, p, asked = setup(tmp_path, important=lambda u, t: answers.pop(0) if answers else 0.2)
    now = clock.t
    soon(b, NBC, 0.95, now - 300)
    assert p.tick(i, b, [], now) == [] and len(asked) == 1        # a race call on air: stay
    assert p.tick(i, b, [], now + 2) == [] and len(asked) == 1    # re-checked at most every 5 s
    clock.t = now + 6
    assert p.tick(i, b, [], now + 6) == [] and len(asked) == 2    # the check failed / timed out: stay
    clock.t = now + 12
    assert p.tick(i, b, [], now + 12) == ["sound 3→4 (break soon)"] and len(asked) == 3

    def boom(u, t):
        raise TimeoutError
    clock2, b2, i2, p2, _ = setup(tmp_path / "x", important=boom)
    soon(b2, NBC, 0.95, clock2.t - 300)
    assert p2.tick(i2, b2, [], clock2.t) == [] and i2.driver.sound == 2
    clock3, b3, i3, p3, _ = setup(tmp_path / "y", important=None)   # no web configured: never preempt
    soon(b3, NBC, 0.95, clock3.t - 300)
    assert p3.tick(i3, b3, [], clock3.t) == [] and i3.driver.sound == 2


def test_sound_returns_if_the_break_never_comes(tmp_path):
    clock, b, i, p, _ = setup(tmp_path)
    now = clock.t
    soon(b, NBC, 0.95, now - 300)
    p.tick(i, b, [], now)
    assert i.driver.sound == 3
    for dt in (30, 90):   # NBC stays on air: the sound holds at ABC (no early return to the manual pick)
        clock.t = now + dt
        soon(b, NBC, 0.95, now - 300)
        label(b, ABC, "coverage", now - 300)
        assert p.tick(i, b, [], now + dt) == [] and i.driver.sound == 3
    clock.t = now + 121
    soon(b, NBC, 0.95, now - 300)
    label(b, ABC, "coverage", now - 300)
    assert p.tick(i, b, [], now + 121) == ["sound 4→3 (no break came)"] and i.driver.sound == 2 and not p.auto_sound
    assert p.tick(i, b, [], now + 130) == []   # not again on this stream for 5 min


def test_preempted_then_the_break_comes_and_the_sound_returns_after_it(tmp_path):
    clock, b, i, p, _ = setup(tmp_path)
    now = clock.t
    soon(b, NBC, 0.95, now - 300)
    p.tick(i, b, [], now)
    clock.t = now + 40
    label(b, NBC, "break", now + 30)
    label(b, ABC, "coverage", now - 300)
    assert p.tick(i, b, [], now + 40) == [] and p.pre is None and i.driver.sound == 3
    clock.t = now + 200
    label(b, NBC, "coverage", now + 180)
    label(b, ABC, "coverage", now - 300)
    assert p.tick(i, b, [], now + 200) == ["sound 4→3"] and i.driver.sound == 2
    assert b.breaks.of(NBC)[0]["start"] == now + 30   # and the break went into the log


def test_a_person_moving_the_sound_cancels_the_preemption(tmp_path):
    clock, b, i, p, _ = setup(tmp_path)
    soon(b, NBC, 0.95, clock.t - 300)
    p.tick(i, b, [], clock.t)
    i.sound(2)
    p.tick(i, b, [], clock.t + 1)
    assert p.pre is None and not p.auto_sound


# ---- the listener passes "soon" (and the last ~20 s) through -------------------------------------------------------------
def test_listener_passes_soon_and_recent_through(monkeypatch):
    w = adwatch.Watcher(env={"WALL_ADWATCH_POOL": NBC}, stt=lambda pcm: "", classify=lambda u, t: ("coverage", 0.9, 0.87),
                        post=lambda b: None, walls=lambda: {"tv": {"running": True, "urls": [NBC]}}, live=lambda u: True,
                        clock=lambda: 5000.0)
    monkeypatch.setattr(adwatch, "Stream", lambda u, q: StubStream(u))
    w.sync_streams([NBC])
    w.streams[NBC].add("early words", False, 4960)
    w.streams[NBC].add("stay with us", False, 4995)
    st = w.cycle(5000.0)["streams"][NBC]
    assert st["soon"] == 0.87 and st["recent"] == "stay with us"
    w.classify = lambda u, t: ("coverage", 0.9)   # no soon answered
    w.streams[NBC].add("more", False, 5025)
    assert w.cycle(5030.0)["streams"][NBC]["soon"] is None


# ---- web: soon + important from a stubbed Jev ----------------------------------------------------------------------------
def test_classify_answer_carries_soon_and_the_important_check(monkeypatch):
    from fastapi import FastAPI

    from web import wall as web_wall
    from web import wall_election as wel
    monkeypatch.setattr(web_wall, "check_api_key", lambda k: k == "sekrit")
    sent = []

    async def jev(state, questions, timeout=4):
        sent.append((state, questions, timeout))
        if "now" in questions:
            return {"now": {"choice": "yes", "confidence": 0.9, "probabilities": {"yes": 0.9, "no": 0.1}}}
        return {"segment": {"choice": "coverage", "confidence": 0.95},
                "soon": {"choice": "soon", "confidence": 0.88},   # no probabilities: P(soon) from the confidence
                "important": {"choice": "yes", "confidence": 0.6, "probabilities": {"yes": 0.83, "no": 0.17}}}

    async def llm(text):
        raise AssertionError("Jev was sure")

    monkeypatch.setattr(wel, "_jev", jev)
    monkeypatch.setattr(wel, "_classify_llm", llm)
    wel._cls["cache"].clear()
    wel._cls["last"].clear()
    app = FastAPI()
    app.include_router(wel.router)
    c = TestClient(app)
    h, path = {"X-Api-Key": "sekrit"}, "/api/v1/wall/election/classify"
    r = c.post(path, json={"stream": NBC, "text": "the long window ... stay with us", "recent": "stay with us"}, headers=h).json()
    assert r == {"label": "coverage", "confidence": 0.95, "soon": 0.88, "important": 0.83, "src": "jev"}
    state, qs, _ = sent[-1]
    assert len(sent) == 1   # important rides along in the same Jev request
    assert set(qs) == {"segment", "soon", "important"} and set(qs["important"]["criteria"]) == {"yes", "no"} and set(qs["soon"]["criteria"]) == {"soon", "not"} and state.endswith("stay with us")
    assert c.post(path, json={"check": "important", "stream": NBC, "text": "we can now project"}, headers=h).json() == \
        {"important": 0.9}
    assert sent[-1][2] <= 1.5 and set(sent[-1][1]["now"]["criteria"]) == {"yes", "no"}

    async def slow(state, questions, timeout=4):
        await asyncio.sleep(5)

    monkeypatch.setattr(wel, "_jev", slow)
    monkeypatch.setattr(wel, "IMPORTANT_TIMEOUT_S", 0.05)
    assert c.post(path, json={"check": "important", "stream": NBC, "text": "x"}, headers=h).json() == {}   # timeout: no answer

    async def none(state, questions, timeout=4):
        return None

    monkeypatch.setattr(wel, "_jev", none)
    assert c.post(path, json={"check": "important", "stream": NBC, "text": "x"}, headers=h).json() == {}   # no key


def test_jev_p_reads_probabilities_or_confidence():
    from web.wall_election import _jev_p
    assert _jev_p({"choice": "not", "confidence": 0.7}, "soon") == 0.3
    assert _jev_p({"choice": "soon", "confidence": 0.2, "probabilities": {"soon": 0.65}}, "soon") == 0.65
    assert _jev_p(None, "soon") is None and _jev_p({"choice": "soon"}, "soon") is None


def test_wall_important_check_is_none_when_the_web_is_down():
    from wall.server import important_check
    assert important_check("http://127.0.0.1:9", "k")(NBC, "text") is None
