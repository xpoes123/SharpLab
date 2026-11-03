"""The watch page's abuse guard (web/wall_guard.py + its wiring in web/wall.py): token buckets, chat duplicates, debounce,
timeout escalation, and who's exempt (owner, admins, the bot's X-API-Key)."""
import asyncio

from web import wall
from web.wall_guard import CHAT_DUP_S, READ, STRIKES, TIMEOUT_S, WRITE, Buckets, Guard

from test_wall_web import ADMIN, OWNER, Clock, connect, cookie, env, setup, until  # noqa: F401 — env/setup are fixtures


# ---- pure ------------------------------------------------------------------------------------------------------------
def test_token_bucket_burst_then_refill():
    b = Buckets(0.5, 6)
    assert all(b.take("a", 0) == 0 for _ in range(6))
    assert b.take("a", 0) == 2.0             # empty: one token every 2 s
    assert b.take("b", 0) == 0               # per key
    assert b.take("a", 2.0) == 0             # refilled one
    assert b.take("a", 2.0) > 0
    assert all(b.take("a", 100) == 0 for _ in range(6)) and b.take("a", 100) > 0   # never above the burst


def test_chat_has_no_gap_but_a_bucket_and_drops_duplicates():
    g = Guard()
    assert g.chat_ok("u:1", "hi", 0) == ""
    assert g.chat_ok("u:1", "hi", 1) == "You just said that."
    assert g.chat_ok("u:1", "hi there", 1) == ""                     # different text right away: fine
    assert g.chat_ok("u:1", "hi", 1 + CHAT_DUP_S) == ""              # the same text later: fine
    for i in range(5):   # the bucket is full again (6): "hi" + 5 more, then refused
        assert g.chat_ok("u:1", f"m{i}", 11) == ""
    assert g.chat_ok("u:1", "one too many", 11) == "Slow down a little."
    assert g.chat_ok("u:2", "someone else", 11) == ""


def test_debounce_remembers_for_a_moment():
    g = Guard()
    assert g.seen(("1", "x"), 0) is None
    g.remember(("1", "x"), ("p", ""), 0)
    assert g.seen(("1", "x"), 1.0) == ("p", "")
    assert g.seen(("1", "x"), 1.6) is None


def test_timeout_escalation_expiry_lift_and_exemption():
    g = Guard()
    for i in range(STRIKES[0]):
        assert g.strike("u:9", i * 0.1, "flood", "1.2.3.4", "Spammer") is None
    t = g.strike("u:9", 2.5, "flood", "1.2.3.4", "Spammer")
    assert t and t["reason"] == "flood" and t["ip"] == "1.2.3.4"
    assert g.timed_out("u:9", 3) > TIMEOUT_S - 1
    assert g.timed_out("u:9", 2.5 + TIMEOUT_S) == 0                  # expires on its own
    for i in range(STRIKES[0] + 1):
        g.strike("u:9", 1000 + i, "flood", name="Spammer")
    assert g.timed_out("u:9", 1030)
    assert g.lift("spammer") == ["u:9"] and not g.timed_out("u:9", 1030)
    # spread out: never more than 20 in 60 s
    assert all(g.strike("u:8", 5000 + i * 4, "x") is None for i in range(100))
    # exempt: never
    assert all(g.strike("u:7", 9000, "x", exempt=True) is None for _ in range(100))


def test_ip_keys_are_looser_than_identities():
    g = Guard()
    n_user = sum(g.limit("u:1", True, 0) == 0 for _ in range(200))
    n_ip = sum(g.limit("ip:1.2.3.4", True, 0) == 0 for _ in range(200))
    assert n_user == WRITE[1] and n_ip > 100


# ---- hub: debounce on proposals and votes ----------------------------------------------------------------------------
def test_same_proposal_twice_is_one_proposal_and_owner_applies_once():
    async def go():
        done = []

        async def execute(a):
            done.append(a)
            return True, ""

        async def state():
            return {"running": True, "urls": ["https://a.example"]}

        clock = Clock()
        h = wall.reset_hub(clock=clock, execute=execute, fetch_state=state)
        h.presence.join(1, {"id": "1", "name": "One"}, clock())
        h.presence.join(2, {"id": "2", "name": "Two"}, clock())
        h.presence.join(3, {"id": "3", "name": "Three"}, clock())
        a = {"op": "layout", "layout": "full"}
        p1, e1 = await h.propose({"id": "1", "name": "One"}, a)
        clock.t += 0.5
        p2, e2 = await h.propose({"id": "1", "name": "One"}, dict(a))
        assert p1 is p2 and not e1 and not e2 and len(h.votes.props) == 1
        v1 = await h.vote({"id": "2"}, p1.id, True)
        v2 = await h.vote({"id": "2"}, p1.id, True)
        assert v1 == v2
        clock.t += 2
        _, e3 = await h.propose({"id": "1", "name": "One"}, a)   # after the window: the normal rules again
        assert e3
        b = {"op": "board", "board": "nba"}
        await h.propose({"id": OWNER, "name": "David"}, b)
        await h.propose({"id": OWNER, "name": "David"}, b)        # owner double tap
        assert sum(1 for x in done if x.get("board") == "nba") == 1
    asyncio.run(go())


# ---- HTTP ------------------------------------------------------------------------------------------------------------
def test_http_reads_are_generous_writes_tight_and_429_has_retry_after(setup):
    client, hub, clock, _ = setup
    client.cookies.clear()
    for k, v in cookie("300", "Kim").items():
        client.cookies.set(k, v)
    assert all(client.get("/api/v1/wall/me").status_code == 200 for _ in range(READ[1]))
    r = client.get("/api/v1/wall/me")
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    clock.t += 1   # 10 more a second
    assert sum(client.get("/api/v1/wall/me").status_code == 200 for _ in range(15)) == READ[0]


def test_http_strikes_time_out_writes_but_not_reads(setup):
    client, hub, clock, _ = setup
    client.cookies.clear()
    for k, v in cookie("300", "Kim").items():
        client.cookies.set(k, v)
    for _ in range(READ[1] + STRIKES[0] + 1):
        client.get("/api/v1/wall/me")
    assert hub.guard.timed_out("u:300", clock())
    clock.t += 30   # buckets full again, still in the timeout
    assert client.get("/api/v1/wall/me").status_code == 200                 # can still watch
    r = client.post("/api/v1/wall/propose", json={})                        # any write: refused before the handler
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 500
    clock.t += TIMEOUT_S
    assert client.post("/api/v1/wall/propose", json={}).status_code == 401   # past the guard again (no bot key)


def test_api_key_owner_and_admin_are_exempt(setup):
    client, hub, clock, _ = setup
    client.cookies.clear()
    assert all(client.get("/api/v1/wall/chat", headers={"X-Api-Key": "sekrit"}).status_code == 200 for _ in range(200))
    for uid in (OWNER, ADMIN):
        client.cookies.clear()
        for k, v in cookie(uid, "Boss").items():
            client.cookies.set(k, v)
        assert all(client.get("/api/v1/wall/me").status_code == 200 for _ in range(200))
        assert not hub.guard.timed_out("u:" + uid, clock())


def test_wall_service_token_is_exempt(setup, monkeypatch):
    client, hub, clock, _ = setup
    monkeypatch.setenv("WALL_API_TOKEN", "svc-token")
    client.cookies.clear()
    h = {"Authorization": "Bearer svc-token"}
    assert all(client.get("/api/v1/wall/me", headers=h).status_code != 429 for _ in range(300))
    assert any(client.get("/api/v1/wall/me", headers={"Authorization": "Bearer nope"}).status_code == 429 for _ in range(300))


# ---- socket ----------------------------------------------------------------------------------------------------------
def test_socket_flood_times_out_alerts_the_owner_and_owner_can_lift(setup):
    client, hub, clock, _ = setup
    with connect(client, OWNER, "David") as boss:
        until(boss, "hello")
        with connect(client, "300", "Spammer") as ws:
            until(ws, "hello")
            for i in range(WRITE[1] + STRIKES[0] + 5):
                ws.send_json({"t": "vote", "id": 999, "yes": bool(i % 2)})
            ws.send_json({"t": "ping", "c": 1})
            until(ws, "pong")                                       # reads still answered
            assert hub.guard.timed_out("u:300", clock())
            alert = until(boss, "chat")["msg"]
            while not alert.get("private"):
                alert = until(boss, "chat")["msg"]
            assert "Spammer" in alert["text"] and "u:300" in alert["text"]
            assert not any(m.get("private") for m in hub.chat.log)  # not in the scrollback / on the TV
            clock.t += 30
            ws.send_json({"t": "chat", "text": "let me talk"})
            assert "timeout" in until(ws, "error")["text"]
            boss.send_json({"t": "chat", "text": "/lift u:300"})
            assert "lifted" in until(boss, "chat")["msg"]["text"]
            assert not hub.guard.timed_out("u:300", clock())
            ws.send_json({"t": "chat", "text": "thanks"})
            assert until(ws, "chat")["msg"]["text"] == "thanks"


def test_socket_chat_flood_gets_slow_down_only_to_the_sender(setup):
    client, hub, clock, _ = setup
    with connect(client, "300", "Fast") as ws:
        until(ws, "hello")
        for i in range(7):
            ws.send_json({"t": "chat", "text": f"msg {i}"})
        texts = []
        while len(texts) < 7:
            m = ws.receive_json()
            if m["t"] in ("chat", "error"):
                texts.append((m["t"], m.get("text") or m["msg"]["text"]))
        assert texts[:6] == [("chat", f"msg {i}") for i in range(6)]
        assert texts[6] == ("error", "Slow down a little.")


def test_owner_never_times_out_on_the_socket(setup):
    client, hub, clock, _ = setup
    with connect(client, OWNER, "David") as ws:
        until(ws, "hello")
        for _ in range(60):
            ws.send_json({"t": "vote", "id": 999, "yes": True})
        ws.send_json({"t": "ping", "c": 1})
        until(ws, "pong")
    assert not hub.guard.timeouts
