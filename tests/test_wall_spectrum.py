"""Election night "Spectrum" (wall/spectrum.py): the catalog's shape, the coverage gate (the wall service refuses a tile at a
break, the web doesn't open the vote), verified viewers only, and ad-skip listening to the lineup. No network."""
import json

import pytest

from tests.test_wall_service import FakeDriver, FakeScores, api, client  # noqa: F401 — the api fixture
from tests.test_wall_web import Clock
from wall import adwatch, spectrum
from web import wall

OWN = "http://127.0.0.1:7795/p/election/"
NBC = "https://www.youtube.com/@NBCNews/live"
FOX = "https://www.youtube.com/@FoxNews/live"
GROUPS = ["Hard left", "Left", "Center", "Right", "Hard right"]


def test_catalog_shape():
    j = json.loads(spectrum.PATH.read_text())
    assert [g["label"] for g in j["groups"]] == GROUPS
    urls = spectrum.urls()
    assert len(urls) == len(set(urls)) and all(g["streams"] for g in j["groups"])
    for g in j["groups"]:
        for s in g["streams"]:
            assert set(s) <= {"name", "url", "note", "hosts"} and s["name"] and isinstance(s.get("note", ""), str)
            assert s["url"].startswith("https://www.youtube.com/@") and s["url"].endswith("/live")
    assert len(urls) + 2 <= adwatch.MAX   # the whole lineup + a couple of other wall streams fit ad-skip's listening budget
    assert spectrum.find(NBC)["group"] == "Center" and spectrum.find("https://x.example/") is None


def test_gate():
    assert spectrum.gate("https://x.example/", None) == ""                       # not a Spectrum stream: not ours to gate
    assert spectrum.gate(NBC, {"label": "coverage", "live": True}) == ""
    assert spectrum.gate(NBC, {"label": "break", "live": True}) == "NBC News is on a break — try again in a minute."
    assert spectrum.gate(NBC, {"label": None, "live": True}).endswith("try again in a minute.")   # no fresh label yet
    assert spectrum.gate(FOX, {"label": None, "live": False}) == "Fox News isn't live yet."
    assert spectrum.gate(NBC, None)


def test_service_refuses_a_spectrum_tile_at_a_break(api):  # noqa: F811
    app, _ = api
    c = client(app)
    st = c.post("/i/tv/start", json={"urls": [OWN + "races", OWN + "map?view=S", "https://a.tv"]}).json()
    assert set(st["spectrum"]) == set(spectrum.urls()) and st["spectrum"][NBC]["label"] is None   # listed for adwatch + the page
    r = c.post("/i/tv/tile", json={"i": 3, "url": NBC})
    assert r.status_code == 409 and "NBC News is on a break" in r.json()["error"]
    c.post("/adwatch", json={"streams": {NBC: {"label": "break", "confidence": 0.9, "since": 1}}})
    assert c.post("/i/tv/tile", json={"i": 3, "url": NBC}).status_code == 409
    c.post("/adwatch", json={"streams": {NBC: {"label": "coverage", "confidence": 0.9, "since": 1}}})
    st = c.post("/i/tv/tile", json={"i": 3, "url": NBC}).json()
    assert st["urls"][3] == NBC and st["spectrum"][NBC]["label"] == "coverage"
    assert c.post("/i/tv/tile", json={"i": 2, "url": "https://b.tv"}).status_code == 200   # other pages: no gate


def test_service_no_gate_outside_election_mode(api):  # noqa: F811
    app, _ = api
    c = client(app)
    st = c.post("/i/tv/start", json={"urls": ["https://a.tv"]}).json()
    assert "spectrum" not in st
    assert c.post("/i/tv/tile", json={"i": 1, "url": NBC}).json()["urls"][1] == NBC


def elec_state(label):
    return {"running": True, "urls": [OWN + "races", OWN + "map?view=S", "https://a.tv"], "overlay": {"board": "election"},
            "spectrum": {NBC: {"label": label, "live": True}}}


def test_web_noop_gates_on_the_label():
    assert wall.noop({"op": "tile", "i": 2, "url": NBC}, elec_state("break")) == "NBC News is on a break — try again in a minute."
    assert wall.noop({"op": "tile", "i": 2, "url": NBC}, elec_state(None))
    assert wall.noop({"op": "tile", "i": 2, "url": NBC}, elec_state("coverage")) == ""
    assert "tiles 3–4" in wall.noop({"op": "tile", "i": 0, "url": NBC}, elec_state("coverage"))   # streams stay off 1–2
    assert wall.noop({"op": "tile", "i": 2, "url": NBC}, {"running": True, "urls": ["https://a.tv"]}) == ""   # no lineup: no gate


@pytest.mark.anyio
async def test_verified_viewers_only_and_no_vote_at_a_break():
    done = []

    async def execute(a):
        done.append(a)
        return True, ""

    async def fetch_state():
        return hub.wall

    hub = wall.Hub(clock=Clock(), execute=execute, fetch_state=fetch_state)
    hub.wall = elec_state("coverage")
    member, guest = {"id": "300", "name": "Steph"}, {"id": "g-abc", "name": "Kim", "guest": True}
    friend = {"id": "g-fr", "name": "Pal", "guest": True, "tier": "friend"}
    for cid, u in ((1, member), (2, guest), (3, friend)):
        hub.presence.join(cid, u, 1000)
    act = {"op": "tile", "i": 2, "url": NBC, "label": "Center · NBC News"}
    p, err = await hub.propose(guest, act)
    assert not p and "verified" in err
    p, err = await hub.propose(friend, act)
    assert p and not err, err
    hub.votes = wall.Votes()
    hub.wall = elec_state("break")
    p, err = await hub.propose(member, act)
    assert not p and "on a break" in err   # the vote isn't even opened
    hub.wall = elec_state("coverage")
    p, err = await hub.propose(member, {**act, "i": 3})   # (the same proposal again would be the guard's replay)
    assert p and not err, err
    assert wall.verified(member) and wall.verified(friend) and not wall.verified(guest)


def test_adwatch_listens_to_the_live_lineup():
    other = "https://www.youtube.com/@Other/live"
    w = adwatch.Watcher(env={"WALL_ADWATCH_POOL": other}, stt=lambda pcm: "", post=lambda b: None,
                        walls=lambda: {"tv": {"running": True, "urls": [OWN + "races", "https://www.youtube.com/@WSBTV/live"],
                                              "spectrum": {NBC: {"live": True}, FOX: {"live": False},
                                                           "https://www.youtube.com/@Unk/live": {"live": None}}}},
                        live=lambda u: True, clock=lambda: 5000.0)
    assert w.max == adwatch.MAX == 16
    assert w.wanted() == ["https://www.youtube.com/@WSBTV/live", NBC, "https://www.youtube.com/@Unk/live", other]
    w.max = 2
    assert w.wanted() == ["https://www.youtube.com/@WSBTV/live", NBC]   # walls first, then the lineup, then the pool
