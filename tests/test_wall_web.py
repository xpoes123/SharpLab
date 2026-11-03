"""The wall watch page backend (web/wall.py): session gate, chat, who's watching, votes, bot API, login links."""
import pytest
from fastapi import FastAPI, WebSocket
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from web import auth, wall
from web.wall import Chat, Presence, Proposal, Votes, check_action, clean_text, need, verdict

OWNER, ADMIN = "100", "200"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("WALL_OWNER_ID", OWNER)
    monkeypatch.setenv("WALL_ADMIN_IDS", ADMIN)
    monkeypatch.setenv("WALL_SOUND_APPROVALS", "2")
    monkeypatch.setenv("WALL_VOTE_S", "60")
    monkeypatch.setenv("WALL_JOIN_NOTICES", "0")
    monkeypatch.delenv("WALL_PUBLIC", raising=False)
    monkeypatch.delenv("WALL_GUEST_OPEN", raising=False)
    monkeypatch.setattr(wall, "check_api_key", lambda k: k == "sekrit")


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def setup():
    clock, done = Clock(), []

    async def execute(action):
        done.append(action)
        return True, ""

    async def fetch_state():
        return {"running": True, "urls": ["https://a.example", "https://b.example"], "presets": ["Live NFL"]}

    hub = wall.reset_hub(clock=clock, execute=execute, fetch_state=fetch_state)
    app = FastAPI()
    app.include_router(wall.router)

    @app.websocket("/ws/wall")
    async def ws(websocket: WebSocket):
        await wall.wall_websocket(websocket)

    return TestClient(app), hub, clock, done


def cookie(uid, name="User", avatar=None):
    return {auth.COOKIE_NAME: auth.make_session_cookie({"id": uid, "username": name, "avatar": avatar})}


def connect(client, uid, name="User"):
    client.cookies.clear()
    for k, v in cookie(uid, name).items():
        client.cookies.set(k, v)
    return client.websocket_connect("/ws/wall")


def until(ws, t):
    while True:
        m = ws.receive_json()
        if m["t"] == t:
            return m


# ---- socket auth -----------------------------------------------------------------------------------------------------
def test_socket_without_cookie_is_refused(setup):
    client, *_ = setup
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/ws/wall") as ws:
            ws.receive_json()
    assert e.value.code == 4401


def test_socket_with_forged_cookie_is_refused(setup):
    client, *_ = setup
    client.cookies.set(auth.COOKIE_NAME, "eyJpZCI6IjEifQ.forged.sig")
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/ws/wall") as ws:
            ws.receive_json()
    assert e.value.code == 4401


def test_socket_with_session_gets_hello_and_viewer_list(setup):
    client, *_ = setup
    with connect(client, "300", "Steph") as ws:
        hello = ws.receive_json()
        assert hello["t"] == "hello" and hello["me"]["id"] == "300" and hello["me"]["name"] == "Steph"
        assert hello["me"]["initials"] == "S" and not hello["me"]["admin"]
        assert hello["cfg"]["delay"] == 6 and hello["wall"]["running"]
        v = until(ws, "viewers")["list"]
        assert [x["name"] for x in v] == ["Steph"] and v[0]["away"] is False


def test_chat_roundtrip_strips_control_chars(setup):
    client, *_ = setup
    with connect(client, "300", "Steph") as ws:
        ws.receive_json()
        ws.send_json({"t": "chat", "text": "hi‮ <b>there</b>\x07"})
        m = until(ws, "chat")["msg"]
        assert m["text"] == "hi <b>there</b>" and m["name"] == "Steph"   # markup kept as text; the page uses textContent


def test_moderation_needs_admin(setup):
    client, hub, *_ = setup
    with connect(client, "300") as ws:
        ws.receive_json()
        ws.send_json({"t": "chat", "text": "hello"})
        mid = until(ws, "chat")["msg"]["id"]
        ws.send_json({"t": "del", "id": mid})
        assert "admins" in until(ws, "error")["text"]
    assert any(m["id"] == mid for m in hub.chat.log)
    with connect(client, ADMIN, "Mod") as ws:
        ws.receive_json()
        ws.send_json({"t": "del", "id": mid})
        assert until(ws, "del")["id"] == mid
        ws.send_json({"t": "mute", "uid": "300", "minutes": 5})
        assert "muted" in until(ws, "chat")["msg"]["text"]
    assert not any(m["id"] == mid for m in hub.chat.log) and hub.chat.is_muted("300", hub.clock())


def test_ping_returns_server_time(setup):
    client, _, clock, _ = setup
    with connect(client, "300") as ws:
        ws.receive_json()
        ws.send_json({"t": "ping", "c": 5})
        p = until(ws, "pong")
        assert p["c"] == 5 and p["s"] == clock.t * 1000


def test_vote_over_socket_passes_and_executes(setup):
    client, hub, clock, done = setup
    with connect(client, "300", "A") as ws:
        ws.receive_json()
        ws.send_json({"t": "propose", "action": {"op": "layout", "layout": "full"}})   # solo viewer: 1 of 1 = majority
        p = until(ws, "proposal")["p"]
        assert p["status"] == "passed"
    assert done == [{"op": "layout", "layout": "full"}]


# ---- pure: chat ------------------------------------------------------------------------------------------------------
def test_chat_limits():
    c, u = Chat(), {"id": "1", "name": "A"}
    assert c.post(u, "", 0)[0] is None
    m, _ = c.post(u, "x" * 999, 0)
    assert len(m["text"]) == 300
    for i in range(5):   # no gap between messages: a burst of 6 …
        assert c.post(u, f"quick {i}", 0.1)[0]
    assert c.post(u, "seventh", 0.1)[1] == "Slow down a little."   # … then one per 2 s
    assert c.post(u, "later", 2.2)[0]
    c.mute("1", 1, 10)
    assert c.post(u, "muted", 20)[1] == "You're muted for now."


def test_scrollback_keeps_last_200():
    c = Chat()
    for i in range(250):
        c.system(f"m{i}", i)
    assert len(c.log) == 200 and c.log[0]["text"] == "m50"


def test_clean_text():
    assert clean_text("a\nb\tc   d") == "a b c d"
    assert clean_text(None) == ""


# ---- pure: presence --------------------------------------------------------------------------------------------------
def test_presence_away_when_hidden_or_silent():
    p = Presence()
    p.join(1, {"id": "a", "name": "Ann"}, 0)
    p.join(2, {"id": "b", "name": "Bob"}, 0)
    p.join(3, {"id": "b", "name": "Bob"}, 0)   # second tab
    assert p.active(10) == {"a", "b"}
    p.beat(1, True, 10)
    assert p.active(10) == {"b"}
    assert p.active(61) == set()               # Bob: no heartbeat for 61 s
    p.beat(3, False, 61)
    assert p.active(61) == {"b"}
    v = {x["name"]: x for x in p.listing(61)}
    assert v["Ann"]["away"] and not v["Bob"]["away"] and v["Ann"]["initials"] == "A"
    assert p.leave(2) is None and p.leave(3) == "b"


# ---- pure: votes -----------------------------------------------------------------------------------------------------
def prop(kind="screen", uid="a", yes=None, no=None, expires=60):
    return Proposal(1, uid, "A", {"op": "sound", "i": 1} if kind == "sound" else {"op": "stop"}, kind, 0, expires,
                    yes=set(yes or {uid}), no=set(no or ()))


def test_majority_of_active_viewers():
    active = {"a", "b", "c"}
    assert verdict(prop(), active, 1, 2) == "open"                  # 1 of 3
    assert verdict(prop(yes={"a", "b"}), active, 1, 2) == "passed"  # 2 of 3
    assert verdict(prop(no={"b", "c"}), active, 1, 2) == "failed"   # can't reach 2


def test_away_viewers_dont_count():
    # 4 watching, 2 away: the electorate is the 2 active ones
    assert need(prop(), {"a", "b"}, 2) == 2
    assert verdict(prop(yes={"a", "b"}), {"a", "b"}, 1, 2) == "passed"


def test_tie_fails():
    active = {"a", "b", "c", "d"}
    assert need(prop(), active, 2) == 3
    assert verdict(prop(yes={"a", "b"}, no={"c", "d"}), active, 1, 2) == "failed"
    assert verdict(prop(yes={"a", "b"}), {"a", "b", "c", "d"}, 61, 2) == "expired"


def test_expiry():
    assert verdict(prop(), {"a", "b", "c"}, 59, 2) == "open"
    assert verdict(prop(), {"a", "b", "c"}, 60, 2) == "expired"


def test_proposer_counts_yes_even_from_discord():
    # proposed from Discord by someone not on the page: they join the electorate as a yes
    p = prop(uid="z")
    assert "z" in p.yes and need(p, {"a"}, 2) == 2


def test_sound_needs_two_or_one_when_solo():
    assert verdict(prop("sound"), {"a"}, 1, 2) == "passed"                 # alone: 1 is enough
    assert verdict(prop("sound"), {"a", "b", "c"}, 1, 2) == "open"
    assert verdict(prop("sound", yes={"a", "c"}), {"a", "b", "c"}, 1, 2) == "passed"
    assert verdict(prop("sound", yes={"a", "c"}), {"a", "b", "c", "d", "e"}, 1, 2) == "passed"   # not a majority, just 2
    assert verdict(prop("sound", yes={"a", "c"}), {"a", "b", "c"}, 1, 3) == "open"            # threshold is config


def test_proposal_rate_limit_and_owner_override():
    v, a = Votes(), {"id": "a", "name": "A"}
    p, _ = v.propose(a, {"op": "stop"}, 0, 60)
    assert p and p.yes == {"a"}
    assert v.propose(a, {"op": "stop"}, 1, 60)[1] == "You already have a proposal open."
    p.status = "failed"
    assert "every" in v.propose(a, {"op": "stop"}, 5, 60)[1]
    assert v.propose(a, {"op": "stop"}, 21, 60)[0]
    o, _ = v.propose({"id": OWNER, "name": "David"}, {"op": "stop"}, 21, 60, owner=True)
    assert o.status == "override"


def test_check_action():
    assert check_action({"op": "tile", "i": 0, "url": "https://x.y/z"})["url"] == "https://x.y/z"
    for bad in ({"op": "tile", "i": 0, "url": "javascript:alert(1)"}, {"op": "tile", "i": 5, "url": "https://x"},
                {"op": "layout", "layout": "weird"}, {"op": "rm -rf"}, "stop", {"op": "start", "preset": True},
                {"op": "start", "urls": ["file:///etc/passwd"]}, {"op": "start"}, {"op": "board"}, {"op": "remove", "i": 9}):
        with pytest.raises(ValueError):
            check_action(bad)


def test_check_action_mirror_start_follow_and_origin():
    assert check_action({"op": "start", "mirror": True}) == {"op": "start", "mirror": True}
    assert check_action({"op": "start", "mirror": True, "origin": {"guild": "12", "channel": 34}}) == \
        {"op": "start", "mirror": True, "origin": {"guild": "12", "channel": "34"}}
    assert check_action({"op": "start", "preset": "Live NFL", "origin": {"channel": "1; drop"}}) == {"op": "start", "preset": "Live NFL"}
    assert check_action({"op": "follow", "x": 1}) == {"op": "follow"}
    assert wall.describe({"op": "start", "mirror": True}) == "start the wall: same as the TV"
    assert wall.describe({"op": "follow"}) == "follow the TV again"
    assert wall._route(check_action({"op": "start", "mirror": True, "origin": {"guild": "1", "channel": "2"}})) == \
        ("POST", "/i/discord/start", {"mirror": True, "origin": {"guild": "1", "channel": "2"}})
    assert wall._route({"op": "follow"}) == ("POST", "/i/discord/follow", {})
    with pytest.raises(ValueError, match="mirror"):
        check_action({"op": "start", "mirror": "yes"})


@pytest.mark.anyio
async def test_a_mirror_start_goes_to_a_vote_and_the_owner_applies_it():
    done, states = [], [{"running": False}]

    async def execute(a):
        done.append(a)
        return True, ""

    async def fetch_state():
        return states[-1]

    hub = wall.Hub(clock=Clock(), execute=execute, fetch_state=fetch_state)
    for cid, uid in ((1, "a"), (2, "b")):
        hub.presence.join(cid, {"id": uid, "name": uid}, 1000)
    p, err = await hub.propose({"id": "a", "name": "a"}, {"op": "start", "mirror": True})
    assert not err and p.status == "open" and not done and "same as the TV" in hub.chat.log[-1]["text"]
    await hub.vote({"id": "b", "name": "b"}, p.id, True)
    assert p.status == "passed" and done == [{"op": "start", "mirror": True}]
    o, _ = await hub.propose({"id": OWNER, "name": "David"}, {"op": "start", "mirror": True})
    assert o.status == "override" and len(done) == 2


@pytest.mark.anyio
async def test_wall_notices_reach_the_chat_once():
    states = [{"running": True, "mirror": True, "notice": {"id": 1, "text": "old news", "at": 5}}]

    async def fetch_state():
        return states[-1]

    async def execute(a):
        return True, ""

    hub = wall.Hub(clock=Clock(), execute=execute, fetch_state=fetch_state)
    await hub.refresh_wall()
    assert not any("old news" in m["text"] for m in hub.chat.log)   # what was there before we looked isn't news
    states.append({"running": True, "mirror": False, "notice": {"id": 2, "text": "No longer mirroring the TV", "at": 9}})
    await hub.refresh_wall()
    await hub.refresh_wall()
    assert [m["text"] for m in hub.chat.log].count("No longer mirroring the TV") == 1


@pytest.mark.anyio
async def test_hub_owner_applies_instantly_and_others_wait():
    clock, done = Clock(), []

    async def execute(a):
        done.append(a)
        return True, ""

    async def fetch_state():
        return {}

    hub = wall.Hub(clock=clock, execute=execute, fetch_state=fetch_state)
    for cid, uid in ((1, "a"), (2, "b"), (3, "c")):
        hub.presence.join(cid, {"id": uid, "name": uid}, 0)
    clock.t = 1
    p, _ = await hub.propose({"id": "a", "name": "a"}, {"op": "stop"})
    assert p.status == "open" and not done
    await hub.vote({"id": "b", "name": "b"}, p.id, True)
    assert p.status == "passed" and done == [{"op": "stop"}]
    q, _ = await hub.propose({"id": "c", "name": "c"}, {"op": "layout", "layout": "lbar"})
    clock.t = 62
    for cid in (1, 2, 3):
        hub.presence.beat(cid, False, 62)   # all three still here
    await hub.tick()
    assert q.status == "expired" and len(done) == 1
    o, _ = await hub.propose({"id": OWNER, "name": "David"}, {"op": "board", "board": "nba"})
    assert o.status == "override" and done[-1] == {"op": "board", "board": "nba"}
    assert any("owner" in m["text"] for m in hub.chat.log)


@pytest.mark.anyio
async def test_hub_failed_service_call_marks_error():
    async def execute(a):
        return False, "boom"

    async def fetch_state():
        return {}

    hub = wall.Hub(clock=Clock(), execute=execute, fetch_state=fetch_state)
    hub.presence.join(1, {"id": "a", "name": "a"}, 1000)
    p, _ = await hub.propose({"id": "a", "name": "a"}, {"op": "stop"})
    assert p.status == "error" and "boom" in hub.chat.log[-1]["text"]


def test_route_maps_to_service_api():
    assert wall._route({"op": "start", "preset": "Live NFL"}) == ("POST", "/i/discord/start", {"preset": "Live NFL"})
    assert wall._route(check_action({"op": "start", "live": "nfl", "tiles": 3, "board": "nfl"})) == \
        ("POST", "/i/discord/start", {"urls": [], "live": "nfl", "tiles": 3, "board": "nfl"})
    assert wall._route({"op": "board", "board": ""}) == ("POST", "/i/discord/board", {"visible": False})
    assert wall._route({"op": "board", "board": "nba"}) == ("POST", "/i/discord/board", {"board": "nba", "visible": True})
    assert wall._route({"op": "layout", "layout": "full"}) == ("POST", "/i/discord/board", {"layout": "full", "visible": True})
    assert wall._route({"op": "sound", "i": 2}) == ("POST", "/i/discord/sound", {"i": 2})
    assert wall._route(check_action({"op": "remove", "i": 1})) == ("DELETE", "/i/discord/tile/1", {})
    assert wall._route(check_action({"op": "tile", "i": 4, "url": "https://x.y", "label": "BUF"})) == \
        ("POST", "/i/discord/tile", {"i": 4, "url": "https://x.y"})   # i == len adds a tile; the label is ours


PLAYLIST = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-TARGETDURATION:1
#EXT-X-MEDIA-SEQUENCE:7
#EXTINF:1.000000,
#EXT-X-PROGRAM-DATE-TIME:2026-11-02T20:20:44.760-0500
seg000007.ts
#EXTINF:1.000000,
#EXT-X-PROGRAM-DATE-TIME:2026-11-02T20:20:45.760-0500
seg000008.ts
"""


def test_playlist_edge_is_last_pdt_plus_duration():
    from datetime import datetime
    want = datetime.fromisoformat("2026-11-02T20:20:46.760-05:00").timestamp() * 1000
    assert wall.playlist_edge(PLAYLIST) == pytest.approx(want)
    assert wall.playlist_edge("#EXTM3U\n") is None


def test_edge_clock_keeps_freshest_sample_and_resets_on_jump():
    c = wall.EdgeClock()
    assert c.observe(14_000, 10_000) and c.off == 4000          # encoder 4 s ahead of the box clock
    assert not c.observe(14_400, 10_900) and c.off == 4000      # a staler playlist (3.5 s) doesn't drag it down
    assert c.observe(15_200, 11_000) and c.off == 4200
    assert c.observe(30_000, 12_000) and c.off == 18_000        # encoder restarted: start over
    assert not c.observe(80_000 + 18_000, 80_000)


@pytest.mark.anyio
async def test_hub_broadcasts_clock_from_proxied_playlist():
    async def fetch_state():
        return {}

    hub = wall.Hub(clock=Clock(1793665000.0), fetch_state=fetch_state)
    await hub.observe_playlist(PLAYLIST)
    assert hub.edge.off is not None


# ---- HTTP: bot API, links, HLS gate ----------------------------------------------------------------------------------
def test_bot_api_requires_key(setup):
    client, *_ = setup
    assert client.get("/api/v1/wall/state").status_code == 401
    assert client.get("/api/v1/wall/state", headers={"X-Api-Key": "nope"}).status_code == 401
    assert client.post("/api/v1/wall/link", json={"discord_id": "1"}).status_code == 401
    r = client.get("/api/v1/wall/state", headers={"X-Api-Key": "sekrit"})
    assert r.status_code == 200 and r.json()["viewers"] == [] and r.json()["watch_url"].endswith("/wall.html")


def test_bot_propose_and_vote(setup):
    client, hub, clock, done = setup
    h = {"X-Api-Key": "sekrit"}
    for cid, uid in ((1, "1"), (2, "2"), (3, "3")):
        hub.presence.join(cid, {"id": uid, "name": uid}, clock.t)
    p = client.post("/api/v1/wall/propose", headers=h, json={"discord_id": "1", "name": "One", "action": {"op": "stop"}}).json()
    assert p["status"] == "open" and p["needed"] == 2 and p["summary"] == "stop the wall" and p["yes"] == 1 and not p["applied"]
    st = client.get("/api/v1/wall/state", headers=h).json()
    assert st["proposals"][0]["id"] == p["id"] and st["proposals"][0]["expires"] == clock.t + 60
    assert {"id", "name", "avatar", "away"} <= set(st["viewers"][0])
    p = client.post("/api/v1/wall/vote", headers=h, json={"discord_id": "2", "proposal_id": p["id"], "yes": True}).json()
    assert p["passed"] and p["status"] == "passed" and done == [{"op": "stop"}]
    o = client.post("/api/v1/wall/propose", headers=h, json={"discord_id": OWNER, "name": "David", "action": {"op": "remove", "i": 1}}).json()
    assert o["applied"] and done[-1] == {"op": "remove", "i": 1}
    r = client.post("/api/v1/wall/propose", headers=h, json={"discord_id": "x", "action": {"op": "stop"}})
    assert r.status_code == 422


def test_login_link_mint_and_redeem_once(setup):
    client, *_ = setup
    url = client.post("/api/v1/wall/link", headers={"X-Api-Key": "sekrit"},
                      json={"discord_id": "555", "username": "Steph", "avatar": "abc"}).json()["url"]
    path = url[url.index("/api/v1/wall/login"):]
    client.cookies.clear()
    r = client.get(path, follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"].endswith("/wall.html")
    sess = auth._session_signer.loads(r.cookies[auth.COOKIE_NAME])
    assert sess["id"] == "555" and sess["username"] == "Steph"
    r2 = client.get(path, follow_redirects=False)
    assert "link_expired" in r2.headers["location"]
    assert "link_expired" in client.get("/api/v1/wall/login?t=garbage", follow_redirects=False).headers["location"]


def test_login_link_expires(monkeypatch):
    t = wall._link_signer.dumps({"id": "1", "username": "x", "avatar": None, "n": "n1"})
    assert wall.redeem_link(t)
    monkeypatch.setattr(wall, "LINK_TTL", -1)
    assert wall.redeem_link(wall._link_signer.dumps({"id": "2", "username": "x", "avatar": None, "n": "n2"})) is None


def test_login_link_respelled_signature_is_still_spent():
    """base64's spare low bits: flipping the last character of the signature verifies the same, but it's the same link."""
    t = wall.mint_link("7", "x", None).split("t=", 1)[1]
    alpha = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    twin = t[:-1] + alpha[alpha.index(t[-1]) ^ 1]
    assert wall._link_signer.loads(twin)   # the respelling really does verify
    assert wall.redeem_link(t) and wall.redeem_link(twin) is None
    assert wall.redeem_link(wall._link_signer.dumps({"id": "8", "username": "x", "avatar": None})) is None   # no nonce


def test_socket_refuses_other_origins(setup):
    """Every *.djiang.xyz page sends the (SameSite=Lax) session cookie along: only our own origin may open the socket."""
    client, *_ = setup
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/wall", headers={"Origin": "https://share.djiang.xyz"}) as ws:
            ws.receive_json()
    assert wall.origin_ok(None) and wall.origin_ok(wall.auth.WEB_BASE_URL) and not wall.origin_ok("https://evil.example")


def test_hls_needs_session_and_caches(setup, monkeypatch):
    client, *_ = setup
    calls = []

    async def upstream(name):
        calls.append(name)
        return 200, b"#EXTM3U\n", "application/vnd.apple.mpegurl"

    monkeypatch.setattr(wall, "fetch_upstream", upstream)
    wall._cache.clear()
    client.cookies.clear()
    assert client.get("/api/v1/wall/hls/index.m3u8").status_code == 401
    for k, v in cookie("300").items():
        client.cookies.set(k, v)
    assert client.get("/api/v1/wall/hls/../secret").status_code == 404
    assert client.get("/api/v1/wall/hls/anything.ts").status_code == 404   # only index.m3u8 / segNNNNNN.ts
    assert client.get("/api/v1/wall/hls/index.m3u8").text == "#EXTM3U\n"
    client.get("/api/v1/wall/hls/index.m3u8")
    assert calls == ["index.m3u8"]   # second viewer within 0.5 s: cached
    assert client.get("/api/v1/wall/me").json()["id"] == "300"


def test_me_without_session_points_to_login(setup):
    client, *_ = setup
    client.cookies.clear()
    r = client.get("/api/v1/wall/me")
    assert r.status_code == 401 and r.json()["login"].endswith("next=/wall.html")


def test_me_logged_out_peek_is_only_name_and_count(setup):
    client, hub, *_ = setup
    client.cookies.clear()
    assert client.get("/api/v1/wall/me").json()["peek"] == {"name": None, "watching": 0}
    hub.wall = {"running": True, "name": "NFL Sunday", "urls": ["https://secret.example/stream"]}
    hub.presence.join(1, {"id": "300", "name": "Kim"}, hub.clock())
    assert client.get("/api/v1/wall/me").json()["peek"] == {"name": "NFL Sunday", "watching": 1}
    hub.wall = {"running": False, "name": "NFL Sunday"}
    assert client.get("/api/v1/wall/me").json()["peek"]["name"] is None


def test_routes_are_wired_into_the_app():
    from web.api import app
    paths = {getattr(r, "path", "") for r in app.routes}
    assert {"/ws/wall", "/api/v1/wall/hls/{name}", "/api/v1/wall/state"} <= paths


# ---- demo mode: guests with the keyed link ----
KEY = "k" * 8 + "EY-demo-0123456789"


@pytest.fixture
def demo(monkeypatch, setup):
    monkeypatch.setenv("WALL_DEMO", "1")
    monkeypatch.setenv("WALL_DEMO_KEY", KEY)
    wall.guest_joins.hits.clear()
    for rl in (wall.guest_joins_ip, wall.guest_names, wall.guest_names_ip):
        rl.hits.clear()
    return setup


def join_as_guest(client, name, k=KEY):
    client.cookies.clear()
    r = client.post("/api/v1/wall/guest", json={"k": k, "name": name})
    if r.status_code == 200:   # the cookie is Secure; the test client talks http, so carry it by hand
        raw = r.headers["set-cookie"].split(";")[0].split("=", 1)[1]
        client.cookies.set(wall.GUEST_COOKIE, raw)
    return r


def test_demo_off_means_login_required(setup, monkeypatch):
    client, *_ = setup
    monkeypatch.setenv("WALL_DEMO_KEY", KEY)   # a key alone doesn't open anything
    r = client.get(f"/api/v1/wall/me?k={KEY}")
    assert r.status_code == 401 and r.json()["guest_ok"] is False
    assert client.post("/api/v1/wall/guest", json={"k": KEY, "name": "Kim"}).status_code == 403
    monkeypatch.setenv("WALL_DEMO", "1")
    monkeypatch.setenv("WALL_DEMO_KEY", "short")              # too short a key: demo stays off
    assert client.get("/api/v1/wall/me?k=short").json()["guest_ok"] is False


def test_demo_needs_the_key(demo):
    client, *_ = demo
    assert client.get("/api/v1/wall/me").json()["guest_ok"] is False
    assert client.get("/api/v1/wall/me?k=nope").json()["guest_ok"] is False
    assert client.get(f"/api/v1/wall/me?k={KEY}").json()["guest_ok"] is True
    assert join_as_guest(client, "Kim", k="nope").status_code == 403
    assert client.get("/api/v1/wall/hls/index.m3u8").status_code == 401


def test_nicknames():
    assert wall.clean_nick("  Kim  ") == "Kim" and wall.clean_nick("Mr. O'Neil-2") == "Mr. O'Neil-2"
    assert wall.clean_nick("a\u202eb") == "ab"   # bidi / control characters are stripped
    for bad in ("K", "x" * 25, "<script>", "..", "", None, "a\u202e"):
        with pytest.raises(ValueError):
            wall.clean_nick(bad)
    assert wall.unique_name("Kim", {"Steph"}) == "Kim"
    assert wall.unique_name("kim", {"Kim"}) == "kim (guest)"
    assert wall.unique_name("Kim", {"Kim", "Kim (guest)"}) == "Kim (guest 2)"


def test_friend_watches_chats_and_changes_the_wall_like_a_member(demo, monkeypatch):
    client, hub, clock, done = demo
    assert join_as_guest(client, "<b>").status_code == 422
    r = join_as_guest(client, "Kim")
    assert r.status_code == 200 and r.json()["guest"]
    me = client.get("/api/v1/wall/me").json()
    assert me["authenticated"] and me["guest"] and me["id"].startswith("g-") and me["name"] == "Kim"
    assert me["tier"] == "friend"

    async def upstream(name):
        return 200, b"#EXTM3U\n", "application/vnd.apple.mpegurl"
    monkeypatch.setattr(wall, "fetch_upstream", upstream)
    wall._cache.clear()
    assert client.get("/api/v1/wall/hls/index.m3u8").status_code == 200   # guests watch

    with client.websocket_connect("/ws/wall") as ws:
        hello = ws.receive_json()
        assert hello["me"]["guest"] and not hello["me"]["admin"] and not hello["me"]["owner"]
        assert until(ws, "viewers")["list"][0]["guest"] is True
        ws.send_json({"t": "chat", "text": "hi"})
        assert until(ws, "chat")["msg"]["guest"] is True
        ws.send_json({"t": "propose", "action": {"op": "tile", "i": 3, "url": "https://news.example/live"}})
        assert until(ws, "proposal")["p"]["status"] == "passed"   # alone: their own yes is the majority, like a member's
        assert done[-1] == {"op": "tile", "i": 3, "url": "https://news.example/live"}
        ws.send_json({"t": "del", "id": 1})
        assert "moderate" in until(ws, "error")["text"]           # owner / admin powers stay Discord-only


@pytest.mark.anyio
async def test_guest_votes_count():
    done = []

    async def execute(a):
        done.append(a)
        return True, ""

    async def fetch_state():
        return {}

    hub = wall.Hub(clock=Clock(), execute=execute, fetch_state=fetch_state)
    guest = {"id": "g-abc", "name": "Kim", "guest": True}
    for cid, u in ((1, {"id": "300", "name": "Steph"}), (2, guest), (3, {"id": "g-def", "name": "Lu", "guest": True})):
        hub.presence.join(cid, u, 1000)
    p, _ = await hub.propose({"id": "300", "name": "Steph"}, {"op": "stop"})
    assert p.status == "open"
    await hub.vote(guest, p.id, True)
    assert p.status == "passed" and done == [{"op": "stop"}]
    q, err = await hub.propose(guest, {"op": "sound", "i": 1})   # sound / layout / board: fine for guests
    assert q and not err
    lu = {"id": "g-def", "name": "Lu", "guest": True}
    q, err = await hub.propose(lu, {"op": "tile", "i": 0, "url": "https://x.example/any"})   # friends: any page, like members
    assert q and not err, err
    q, err = await hub.propose({"id": "g-xyz", "name": "Mo", "guest": True}, {"op": "adwatch", "on": True})
    assert not q and "admins" in err                                                         # admin powers: no


def test_guest_names_are_unique_and_connects_are_rate_limited(demo):
    client, hub, *_ = demo
    hub.presence.join(99, {"id": "300", "name": "Kim"}, hub.clock())   # a member called Kim is watching
    assert join_as_guest(client, "kim").json()["name"] == "kim (guest)"
    for _ in range(20):
        with client.websocket_connect("/ws/wall") as ws:
            ws.receive_json()
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/ws/wall") as ws:
            ws.receive_json()
    assert e.value.code == 4429


def test_guest_nicknames_limit_per_browser_not_per_ip_with_retry_after(demo):
    client, *_ = demo
    r = join_as_guest(client, "Kim")
    assert r.status_code == 200
    gb = r.headers.get_list("set-cookie")[1].split(";")[0].split("=", 1)[1]
    for _ in range(12):   # rejoining with the nickname this browser holds never counts (and keeps the same guest)
        again = client.post("/api/v1/wall/guest", json={"k": KEY, "name": "kim"})
        assert again.status_code == 200 and again.json()["id"] == r.json()["id"] and "set-cookie" not in again.headers
    for i in range(7):   # one browser: 8 new nicknames per 5 min
        client.cookies.clear(); client.cookies.set(wall.GUEST_BROWSER, gb)
        assert client.post("/api/v1/wall/guest", json={"k": KEY, "name": f"Nick{i}"}).status_code == 200
    client.cookies.clear(); client.cookies.set(wall.GUEST_BROWSER, gb)
    r = client.post("/api/v1/wall/guest", json={"k": KEY, "name": "Again"})
    assert r.status_code == 429 and 0 < int(r.headers["retry-after"]) <= 300 and r.json()["retry_after"]
    assert "Discord" in r.json()["error"]
    for i in range(40):   # many friends behind one IP, each their own browser: fine
        assert join_as_guest(client, f"Friend{i}").status_code == 200
    for i in range(12):   # … up to the per-IP flood cap
        join_as_guest(client, f"Flood{i}")
    assert join_as_guest(client, "Late").status_code == 429


def test_rotating_the_key_ends_guest_sessions_and_the_link_is_keyed(demo, monkeypatch):
    client, hub, *_ = demo
    join_as_guest(client, "Kim")
    assert client.get("/api/v1/wall/me").status_code == 200
    assert hub.state()["watch_url"].endswith(f"/wall.html?k={KEY}")
    monkeypatch.setenv("WALL_DEMO_KEY", "another-long-demo-key-xyz")
    assert client.get("/api/v1/wall/me").status_code == 401
    monkeypatch.setenv("WALL_DEMO", "0")
    assert hub.state()["watch_url"].endswith("/wall.html")


# ---- the TV wall's chat (bot API: the homelab wall service polls it, the TV's phones post through it) -----------------
def test_chat_api_requires_key(setup):
    client, *_ = setup
    assert client.get("/api/v1/wall/chat").status_code == 401
    assert client.get("/api/v1/wall/chat", headers={"X-Api-Key": "nope"}).status_code == 401
    assert client.post("/api/v1/wall/chat", json={"discord_id": OWNER, "text": "hi"}).status_code == 401
    assert client.post("/api/v1/wall/chat", headers={"X-Api-Key": "nope"}, json={"discord_id": OWNER, "text": "hi"}).status_code == 401
    r = client.get("/api/v1/wall/chat", headers={"X-Api-Key": "sekrit"})
    assert r.status_code == 200 and r.json()["messages"] == [] and r.json()["last"] == 0


def test_chat_api_feed_after_viewers_and_post_as_owner(setup):
    client, hub, clock, _ = setup
    h = {"X-Api-Key": "sekrit"}
    hub.presence.join(1, {"id": OWNER, "name": "David"}, clock.t)
    hub.presence.join(2, {"id": "g-1", "name": "Kim (guest)", "guest": True}, clock.t)
    hub.chat.post({"id": "g-1", "name": "Kim (guest)", "guest": True}, "hello", clock.t)
    feed = client.get("/api/v1/wall/chat", headers=h).json()
    assert [m["text"] for m in feed["messages"]] == ["hello"] and feed["messages"][0]["guest"] is True
    assert "uid" not in feed["messages"][0] and "avatar" not in feed["messages"][0]
    assert feed["watching"] == 2 and {v["name"] for v in feed["viewers"]} == {"David", "Kim (guest)"}
    clock.t += 2
    r = client.post("/api/v1/wall/chat", headers=h, json={"discord_id": OWNER, "name": "Dave (TV)", "text": "from the couch"})
    assert r.status_code == 200 and r.json()["msg"]["name"] == "David"   # the name he watches under wins
    after = client.get(f"/api/v1/wall/chat?after={feed['last']}", headers=h).json()
    assert [m["text"] for m in after["messages"]] == ["from the couch"] and len(after["ids"]) == 2
    # the owner's own chat bucket applies (a burst of 6, then one per 2 s)
    for i in range(5):
        assert client.post("/api/v1/wall/chat", headers=h, json={"discord_id": OWNER, "text": f"again {i}"}).status_code == 200
    r = client.post("/api/v1/wall/chat", headers=h, json={"discord_id": OWNER, "text": "again"})
    assert r.status_code == 429
    assert client.post("/api/v1/wall/chat", headers=h, json={"discord_id": "x", "text": "hi"}).status_code == 422
    clock.t += 2
    assert client.post("/api/v1/wall/chat", headers=h, json={"discord_id": OWNER, "text": "  "}).status_code == 422


def test_chat_api_post_reaches_the_page(setup):
    client, hub, clock, _ = setup
    with connect(client, "1", "One") as ws:
        until(ws, "hello")
        client.post("/api/v1/wall/chat", headers={"X-Api-Key": "sekrit"}, json={"discord_id": OWNER, "name": "David", "text": "yo"})
        assert until(ws, "chat")["msg"]["text"] == "yo"


def test_chat_api_respects_deletes_and_mutes(setup):
    client, hub, clock, _ = setup
    h = {"X-Api-Key": "sekrit"}
    a = hub.chat.post({"id": "1", "name": "One"}, "keep", clock.t)[0]
    b = hub.chat.post({"id": "2", "name": "Two"}, "delete me", clock.t)[0]
    hub.chat.post({"id": "3", "name": "Three"}, "muted soon", clock.t)
    hub.chat.delete(b["id"])
    hub.chat.mute("3", 10, clock.t)
    feed = client.get("/api/v1/wall/chat", headers=h).json()
    assert [m["text"] for m in feed["messages"]] == ["keep"] and feed["ids"] == [a["id"]]
    r = client.post("/api/v1/wall/chat", headers=h, json={"discord_id": "3", "text": "still here?"})
    assert r.status_code == 403
def test_noop_proposals_are_refused():
    from web.wall import noop
    w = {"running": True, "mirror": False, "urls": ["https://a.tv", "https://b.tv"], "sound": 1,
         "overlay": {"board": "nfl", "visible": True, "layout": "lbar"}}
    assert noop({"op": "sound", "i": 1}, w) and not noop({"op": "sound", "i": 0}, w)
    assert noop({"op": "sound", "i": 3}, w) and noop({"op": "remove", "i": 2}, w)
    assert noop({"op": "tile", "i": 0, "url": "https://a.tv"}, w) and not noop({"op": "tile", "i": 0, "url": "https://c.tv"}, w)
    assert not noop({"op": "tile", "i": 2, "url": "https://c.tv"}, w)   # adding a new game is a change
    assert noop({"op": "layout", "layout": "lbar"}, w) and not noop({"op": "layout", "layout": "full"}, w)
    assert noop({"op": "board", "board": "nfl"}, w) and not noop({"op": "board", "visible": False}, w)
    assert not noop({"op": "follow"}, w) and noop({"op": "follow"}, {**w, "mirror": True})
    assert noop({"op": "start", "mirror": True}, {**w, "mirror": True}) and not noop({"op": "stop"}, w)
    assert noop({"op": "stop"}, {"running": False}) and not noop({"op": "start", "preset": "x"}, {"running": False})


def test_live_games_need_a_viewer(setup):
    client, *_ = setup
    assert client.get("/api/v1/wall/games").status_code == 401


def test_a_game_already_on_the_wall_cant_be_added_again():
    w = {"running": True, "urls": ["https://a.tv", "https://b.tv"]}
    assert wall.noop({"op": "tile", "i": 0, "url": "https://b.tv"}, w) == "That's already on the wall."
    assert wall.noop({"op": "tile", "i": 2, "url": "https://a.tv"}, w) and not wall.noop({"op": "tile", "i": 2, "url": "https://c.tv"}, w)
def test_promote_moves_a_tile_to_the_main_spot():
    a = wall.check_action({"op": "promote", "i": 2})
    assert a == {"op": "promote", "i": 2} and wall.describe(a) == "make tile 3 the main tile"
    assert wall._route(a) == ("POST", "/i/discord/move", {"i": 2, "j": 0})
    w = {"running": True, "urls": ["https://a.tv", "https://b.tv"]}
    assert wall.noop({"op": "promote", "i": 0}, w) and wall.noop({"op": "promote", "i": 3}, w) and not wall.noop({"op": "promote", "i": 1}, w)


def test_election_default_and_dashboard_tiles_guard():
    from web.wall import check_action, noop, describe, _route
    assert check_action({"op": "default"}) == {"op": "default"} and _route({"op": "default"})[1] == "/i/discord/default"
    assert describe({"op": "default"}) == "put the default election screens back"
    w = {"running": True, "urls": ["http://127.0.0.1:7795/p/election/races", "http://127.0.0.1:7795/p/election/map", "x", "y"],
         "overlay": {"board": "election"}}
    stream = check_action({"op": "tile", "i": 0, "url": "https://www.youtube.com/@WCVB/live"})
    assert "dashboards" in noop(stream, w)
    assert noop(check_action({"op": "tile", "i": 3, "url": "https://www.youtube.com/@WCVB/live"}), w) == ""
    assert noop(check_action({"op": "tile", "i": 0, "url": "http://127.0.0.1:7795/p/election/turnout"}), w) == ""


def test_only_the_keyed_link_joins_public_mode_never_does(monkeypatch):
    from web.wall import key_ok, public_mode
    monkeypatch.setenv("WALL_DEMO", "1")
    monkeypatch.setenv("WALL_DEMO_KEY", "k" * 20)
    assert key_ok("k" * 20) and not key_ok("") and not key_ok(None) and not public_mode()
    monkeypatch.setenv("WALL_GUEST_OPEN", "1")                   # the old name turns public mode on …
    assert public_mode() and not key_ok("") and not key_ok(None)  # … and no longer lets the plain page join
    monkeypatch.delenv("WALL_GUEST_OPEN")
    monkeypatch.setenv("WALL_PUBLIC", "1")
    assert public_mode()
    monkeypatch.setenv("WALL_DEMO", "0")
    assert not key_ok("k" * 20)                                  # never without demo mode


# ---- tiers: the keyed link = friends (a member's powers here), the plain page = public (read-only) ----------------------
@pytest.fixture
def public(demo, monkeypatch):
    monkeypatch.setenv("WALL_GUEST_OPEN", "1")   # what the VPS has set
    return demo


# ---- pop-out: the wall's election dashboards through /api/v1/wall/p/* ---------------------------------------------------
@pytest.fixture
def pop_upstream(monkeypatch):
    calls = []
    files = {"/p/election/map": b"<!doctype html>map", "/p/election/election.css": b"body{}", "/p/election/election.js": b"1",
             "/p/election/data.json": b'{"races":[],"holders":{"S-PA":[{"who":"Kim","chip":"YES D","won":null}]}}',
             "/p/election/odds.json": b'{"odds":{}}', "/p/us-states.json": b'{"PA":"M0"}'}

    async def upstream(path):
        calls.append(path)
        return (200, files[path]) if path in files else (404, b"")
    monkeypatch.setattr(wall, "fetch_pop", upstream)
    wall._pop.clear()
    return calls


def test_pop_allow_list_types_and_cache(public, pop_upstream, monkeypatch):
    client, *_ = public
    client.cookies.clear()
    for path, ctype in [("p/election/map", "text/html"), ("p/election/election.css", "text/css"), ("p/election/election.js", "text/javascript"),
                        ("p/election/odds.json", "application/json"), ("p/us-states.json", "application/json")]:
        r = client.get("/api/v1/wall/" + path)
        assert r.status_code == 200 and r.headers["content-type"].startswith(ctype) and r.headers["x-content-type-options"] == "nosniff", path
    assert client.get("/api/v1/wall/p/election/map?view=H").status_code == 200   # the page reads ?view itself
    n = len(pop_upstream)
    for bad in ("p/election/board.json", "p/election/map.html", "p/election/other", "p/election/..%2Fsecret", "p/election/data.json.bak",
                "p/other", "p/us-states.json/x"):
        assert client.get("/api/v1/wall/" + bad).status_code == 404, bad
    assert len(pop_upstream) == n                                          # refused here, never asked upstream
    assert client.get("/api/v1/wall/p/election/swing").status_code == 404  # upstream 404 passes through
    # caching: json a few seconds, the page files a minute, one upstream fetch shared by every viewer
    t = [1000.0]
    monkeypatch.setattr(wall.time, "monotonic", lambda: t[0])
    wall._pop.clear()
    pop_upstream.clear()
    for _ in range(3):
        client.get("/api/v1/wall/p/election/odds.json")
        client.get("/api/v1/wall/p/election/map")
    assert pop_upstream == ["/p/election/odds.json", "/p/election/map"]
    t[0] += wall.POP_JSON_S + 0.1
    client.get("/api/v1/wall/p/election/odds.json")
    client.get("/api/v1/wall/p/election/map")
    assert pop_upstream[2:] == ["/p/election/odds.json"]
    t[0] += wall.POP_STATIC_S
    client.get("/api/v1/wall/p/election/map")
    assert pop_upstream[-1] == "/p/election/map"
    assert 2 <= wall.POP_JSON_S <= 5 and wall.POP_STATIC_S == 60


def test_pop_every_tier_but_holders_only_for_members_and_friends(public, pop_upstream):
    client, *_ = public
    client.cookies.clear()
    assert client.get("/api/v1/wall/p/election/data.json").json()["holders"] == {}   # public: the data, no member positions
    join_as_guest(client, "Kim")
    assert client.get("/api/v1/wall/p/election/data.json").json()["holders"]["S-PA"][0]["who"] == "Kim"   # friend
    client.cookies.clear()
    for k, v in cookie("300", "Steph").items():
        client.cookies.set(k, v)
    assert client.get("/api/v1/wall/p/election/data.json").json()["holders"]["S-PA"]                      # member
    assert client.get("/api/v1/wall/p/election/map").status_code == 200


def test_pop_needs_a_viewer_outside_public_mode_and_survives_an_outage(setup, pop_upstream, monkeypatch):
    client, *_ = setup
    client.cookies.clear()
    assert client.get("/api/v1/wall/p/election/map").status_code == 401
    for k, v in cookie("300").items():
        client.cookies.set(k, v)
    assert client.get("/api/v1/wall/p/election/map").status_code == 200

    async def down(path):
        raise wall.httpx.ConnectError("down")
    monkeypatch.setattr(wall, "fetch_pop", down)
    wall._pop.clear()
    assert client.get("/api/v1/wall/p/election/map").status_code == 502
    assert {"/api/v1/wall/p/election/{name}", "/api/v1/wall/p/us-states.json"} <= {getattr(r, "path", "") for r in wall.router.routes}


def test_pop_is_rate_limited(setup, pop_upstream):
    client, *_ = setup
    for k, v in cookie("300").items():
        client.cookies.set(k, v)
    codes = [client.get("/api/v1/wall/p/election/odds.json").status_code for _ in range(60)]
    assert codes[0] == 200 and 429 in codes   # the wall_guard read bucket (burst 40) applies here too


def test_public_me_is_data_only_and_the_key_still_offers_the_join(public):
    client, *_ = public
    client.cookies.clear()
    r = client.get("/api/v1/wall/me")
    assert r.status_code == 200 and r.json()["public"] is True and r.json()["tier"] == "public"
    assert not r.json()["authenticated"] and "guest_ok" not in r.json()
    k = client.get(f"/api/v1/wall/me?k={KEY}")
    assert k.status_code == 401 and k.json()["guest_ok"] is True   # the friend link: the nickname form
    assert join_as_guest(client, "Kim", k="").status_code == 403   # no joining from the plain page
    join_as_guest(client, "Kim")
    assert client.get("/api/v1/wall/me").json()["tier"] == "friend"
    for k_, v in cookie("300", "Steph").items():
        client.cookies.set(k_, v)
    assert client.get("/api/v1/wall/me").json()["tier"] == "member"


def test_public_gets_the_stream_but_no_live_games(public, monkeypatch):
    client, *_ = public
    calls = []

    async def upstream(name):
        calls.append(name)
        return 200, b"#EXTM3U\n", "application/vnd.apple.mpegurl"
    monkeypatch.setattr(wall, "fetch_upstream", upstream)
    wall._cache.clear()
    client.cookies.clear()
    assert client.get("/api/v1/wall/hls/index.m3u8").status_code != 401
    assert client.get("/api/v1/wall/hls/seg000001.ts").status_code != 401
    assert client.get("/api/v1/wall/games").status_code == 401
    assert calls == ["index.m3u8", "seg000001.ts"]   # public mode: the stream is shared too
    join_as_guest(client, "Kim")
    assert client.get("/api/v1/wall/hls/index.m3u8").status_code == 200   # a friend watches


def test_old_guest_cookies_without_a_tier_are_public(public):
    client, *_ = public
    old = wall._guest_signer.dumps({"id": "g-0123456789ab", "name": "Kim", "k": wall._key_tag()})   # before tiers
    client.cookies.clear()
    client.cookies.set(wall.GUEST_COOKIE, old)
    assert client.get("/api/v1/wall/me").json()["tier"] == "public"
    assert client.get("/api/v1/wall/hls/index.m3u8").status_code == 200
    forged = wall._guest_signer.dumps({"id": "g-0123456789ab", "name": "Kim", "k": "nope", "t": "friend"})
    client.cookies.set(wall.GUEST_COOKIE, forged)
    assert client.get("/api/v1/wall/me").json()["tier"] == "public"   # a friend cookie for another key: public too


def test_public_socket_reads_chat_but_cant_write_or_see_who(public):
    client, hub, clock, done = public
    hub.chat.post({"id": "300", "name": "Steph", "avatar": "https://cdn.example/a.png"}, "before you came", clock.t)
    client.cookies.clear()
    with client.websocket_connect("/ws/wall") as pub:
        hello = pub.receive_json()
        assert hello["t"] == "hello" and hello["me"]["public"] is True
        assert [m["text"] for m in hello["history"]] == ["before you came"]
        assert "uid" not in hello["history"][0] and "avatar" not in hello["history"][0]   # no Discord ids on the public page
        assert hello["viewers"] == [] and hello["proposals"] == [] and hello["wall"] is None
        assert until(pub, "count")["watching"] == 0
        with connect(client, "300", "Steph") as m:
            m.receive_json()
            c = until(pub, "count")
            assert c["watching"] == 1 and "list" not in c                               # a count, never names
            m.send_json({"t": "chat", "text": "go team"})
            got = until(pub, "chat")["msg"]
            assert got["text"] == "go team" and got["name"] == "Steph" and "uid" not in got   # live, read-only
            m.send_json({"t": "propose", "action": {"op": "board", "board": "nba"}})
            seen = until(pub, "chat")["msg"]                                            # the proposal line is just text
            assert "proposes" in seen["text"] and "proposal" not in seen
            n_before = len(hub.chat.log)
            pub.send_json({"t": "chat", "text": "let me in"})
            err = until(pub, "error")
            assert "read-only" in err["text"] and err["chat"] is True
            for x in ({"t": "propose", "action": {"op": "stop"}}, {"t": "vote", "id": 1, "yes": True},
                      {"t": "del", "id": 1}, {"t": "mute", "uid": "300"}):
                pub.send_json(x)
            pub.send_json({"t": "ping", "c": 1})
            assert until(pub, "pong")["c"] == 1
            assert len(hub.chat.log) == n_before                                        # nothing it sent landed
            p = next(iter(hub.votes.props.values()))
            assert p.yes == {"300"} and not p.no                                        # its vote didn't count
            assert [v["id"] for v in hub.viewers()] == ["300"]                          # never in the viewer list


def test_public_socket_is_refused_without_public_mode(demo):
    client, *_ = demo
    client.cookies.clear()
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/ws/wall") as ws:
            ws.receive_json()
    assert e.value.code == 4401


def test_public_msg_filters_broadcasts():
    assert wall.public_msg({"t": "viewers", "list": [{"id": "1"}]}) is None
    assert wall.public_msg({"t": "proposal", "p": {}}) is None
    assert wall.public_msg({"t": "wall", "state": {"running": True, "origin": {"channel": "1"}}}) == {"t": "wall", "state": {"running": True}}
    assert wall.public_msg({"t": "chat", "msg": {"id": -1, "text": "⏱ x", "private": True}}) is None   # owner alerts
    assert wall.public_msg({"t": "del", "id": 4}) == {"t": "del", "id": 4}


def test_wall_page_nav_is_hidden_until_a_member_and_has_no_discord_invite():
    from pathlib import Path
    static = Path(__file__).resolve().parents[1] / "web" / "static"
    html = (static / "wall.html").read_text()
    assert '<nav class="nav" id="topNav" hidden>' in html and "Live election results. Chat and votes are for the watch party." in html
    assert 'j.tier === "member"' in (static / "wall.js").read_text()
    assert 'class="nav" id="topNav" hidden' in (static / "pulse.html").read_text()
    for f in static.glob("*.html"):
        assert "discord.gg/" not in f.read_text() and "nav-discord" not in f.read_text(), f.name


def test_tvchat_op_routes_and_is_admin_only(monkeypatch):
    import asyncio
    from web.wall import _route, describe, noop
    monkeypatch.setenv("WALL_OWNER_ID", OWNER)
    monkeypatch.setenv("WALL_ADMIN_IDS", ADMIN)
    assert check_action({"op": "tvchat", "on": False}) == {"op": "tvchat", "on": False}
    with pytest.raises(ValueError):
        check_action({"op": "tvchat", "on": 1})
    assert _route({"op": "tvchat", "on": True}) == ("POST", "/i/discord/tvchat", {"on": True})
    assert describe({"op": "tvchat", "on": False}) == "turn the TV chat off"
    w = {"running": True, "urls": [], "tvchat": {"on": True}}
    assert noop({"op": "tvchat", "on": True}, w) and not noop({"op": "tvchat", "on": False}, w)
    done = []

    async def execute(a):
        done.append(a)
        return True, ""

    async def fetch_state():
        return {"running": True, "urls": [], "tvchat": {"on": True}}

    async def go():
        hub = wall.Hub(clock=Clock(), execute=execute, fetch_state=fetch_state)
        p, err = await hub.propose({"id": "300", "name": "Steph"}, {"op": "tvchat", "on": False})
        assert p is None and "admins" in err
        p, err = await hub.propose({"id": ADMIN, "name": "Ad"}, {"op": "tvchat", "on": False})
        assert p and not err
        p, err = await hub.propose({"id": OWNER, "name": "David"}, {"op": "tvchat", "on": False})
        assert p and done and done[-1] == {"op": "tvchat", "on": False}
    asyncio.run(go())
