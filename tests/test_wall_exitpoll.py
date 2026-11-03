"""wall/exitpoll.py: NBC's exit poll page (__NEXT_DATA__) → All voters questions; empty before release; keeps the last good."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wall.exitpoll import ExitPoll, parse

FIX = Path(__file__).parent / "fixtures" / "wall" / "election"
P2022 = (FIX / "nbc_exitpoll_2022.html").read_text()
P2026 = (FIX / "nbc_exitpoll_2026_empty.html").read_text()


def test_parse_2022():
    d = parse(P2022)
    assert d["as_of"] == "9 AM ET" and len(d["questions"]) == 12
    q = d["questions"][0]
    assert q["q"] == "Gender"
    assert q["answers"][0] == {"label": "Male", "share": 48.0, "d": 42.0, "r": 56.0}
    assert q["answers"][1] == {"label": "Female", "share": 52.0, "d": 53.0, "r": 45.0}
    assert [g["title"] for g in d["groups"]] == ["Women"] and d["groups"][0]["questions"]


def test_parse_2026_empty_before_release():
    d = parse(P2026)
    assert d == {"phase": "PRE_RACE_START", "as_of": "", "questions": [], "groups": []}


def test_parse_rejects_a_page_without_data():
    with pytest.raises(ValueError):
        parse("<html>Access denied</html>")


def test_keeps_the_last_good_poll():
    pages = [P2026, P2022, "<html>blocked</html>", P2026]
    ep = ExitPoll(fetch=lambda: pages.pop(0), clock=lambda: 1000.0)
    ep.poll()
    s = ep.snapshot()
    assert s["phase"] == "PRE_RACE_START" and s["questions"] == [] and s["error"] == "" and s["updated"] == 1000.0
    ep.poll()
    assert len(ep.snapshot()["questions"]) == 12
    ep.poll()   # a failed fetch: same numbers, says why
    s = ep.snapshot()
    assert len(s["questions"]) == 12 and "__NEXT_DATA__" in s["error"]
    ep.poll()   # an empty page after a full one: a blip, keep the numbers
    s = ep.snapshot()
    assert len(s["questions"]) == 12 and s["error"] == "" and s["group_titles"] == ["Women"] and "groups" not in s
    assert ep.snapshot(groups=True)["groups"][0]["title"] == "Women"
    assert s["source"] == "NBC News exit poll (Edison)"


def test_endpoint(tmp_path, monkeypatch):
    from wall.server import Cfg, build
    from wall.turnout import Turnout
    ep = ExitPoll(fetch=lambda: P2022)
    ep.poll()
    monkeypatch.setattr(ep, "touch", lambda: None)
    cfg = Cfg({"WALL_STATE_DIR": str(tmp_path), "WALL_API_TOKEN": "s3cret", "WALL_IMPORT_FROM": "", "WALL_TV_OUTPUT": ""})
    app = build(cfg, {}, turnout=Turnout(sources={}), exitpoll=ep)
    c = TestClient(app, base_url="http://127.0.0.1:7795", client=("127.0.0.1", 5555))
    j = c.get("/p/election/exitpoll.json").json()
    assert j["questions"][0]["q"] == "Gender" and "groups" not in j
    assert c.get("/p/election/exitpoll.json?groups=1").json()["groups"][0]["title"] == "Women"
    remote = TestClient(app, base_url="http://127.0.0.1:7795", client=("100.81.31.34", 5555),
                        headers={"Authorization": "Bearer s3cret"})
    assert remote.get("/p/election/exitpoll.json").status_code == 200
    assert c.get("/p/election/exitpoll").status_code == 200
