"""Election positions on the wall (web/wall_bets.py + wall/bets.py + wall/election.py): only opted-in members, never
delayed, the called highlight in the board + data.json chips, one rate-limited poller. No network: Kalshi, the DB and
the wall service are stubbed; the results are CNN's 2026 files with a few races counted / called by hand."""
import json

from shared import kalshi_wall as kw
from test_kalshi_election import MARKETS, RACES, _count
from test_wall_bets import env, login, poll  # noqa: F401 — env is the fixture
from wall.bets import BetsFeed
from wall.election import OFFICES, Results, balance, board, holders, trades

GA, ME, CTRL = "SENATEGA-26-D", "SENATEME-26-R", "CONTROLH-2026-D"
HANSEN = "688445043975127051"


def elec_env(env, delay_clock=True):
    client, b, clock, k, optin = env
    optin[HANSEN] = {"share": True, "exact": False, "name": "Hansen"}
    optin["3"]["share"] = False                                       # Kim holds Georgia too, never opted in
    k.pos[HANSEN] = {GA: ("YES", 400, 280.0), CTRL: ("YES", 1200, 1020.0)}
    k.pos["3"] = {GA: ("YES", 5, 2.0)}
    k.pos["1"] = {**k.pos["1"], ME: ("NO", 10, 4.0)}
    k.px.update({GA: (97, 98), ME: (27, 28), CTRL: (93, 94)})

    async def markets(tickers):
        k.calls.append(("mkt", tuple(tickers)))
        out = []
        for t in tickers:
            if t in MARKETS:
                bid, ask = k.px[t]
                out.append({**MARKETS[t], "yes_bid_dollars": f"{bid / 100:.4f}", "yes_ask_dollars": f"{ask / 100:.4f}"})
            elif t in k.px:
                out.append({"ticker": t, "event_ticker": kw.event_ticker_of(t), "floor_strike": 3.5,
                            "yes_bid_dollars": f"{k.px[t][0] / 100:.4f}", "yes_ask_dollars": f"{k.px[t][1] / 100:.4f}"})
        return out
    b.markets = markets

    async def get_account(uid):
        return ("kid", b"x") if uid in ("1", "2", "3", HANSEN) else None
    import web.wall_bets as wb
    wb.queries.get_kalshi_account = get_account
    return client, b, clock, k, optin


def test_only_opted_in_members_election_positions_appear(env):
    client, b, clock, k, optin = elec_env(env)
    poll(b)
    login(client, "1", "David")
    rows = client.get("/api/v1/wall/bets").json()["election"]
    assert {(r["who"], r["ticker"]) for r in rows} == {("Hansen", GA), ("Hansen", CTRL), ("David", ME)}
    assert ("pos", "3") not in k.calls                               # a non-sharer's account isn't even read
    feed = client.post("/api/v1/wall/bets/feed", json={"games": [], "election": {}}, headers={"X-Api-Key": "sekrit"}).json()
    assert all("uid" not in r for r in feed["election"]) and {r["who"] for r in feed["election"]} == {"Hansen", "David"}
    assert "S-GA" in feed["need"] and "S-ME" in feed["need"]
    # Hansen turns sharing off: gone at once, from both
    login(client, HANSEN, "Hansen")
    client.post("/api/v1/wall/bets/me", json={"share": False})
    assert {r["who"] for r in client.get("/api/v1/wall/bets").json()["election"]} == {"David"}
    feed = client.post("/api/v1/wall/bets/feed", json={"games": [], "election": {}}, headers={"X-Api-Key": "sekrit"}).json()
    assert {r["who"] for r in feed["election"]} == {"David"}


def test_without_the_election_flag_the_feed_has_no_election_rows(env):
    client, b, clock, k, optin = elec_env(env)
    poll(b)
    feed = client.post("/api/v1/wall/bets/feed", json={"games": []}, headers={"X-Api-Key": "sekrit"}).json()
    assert "election" not in feed


def test_election_rows_are_not_delayed(env):
    """Sports rows are built as of now − D; election rows as of now: a price seen a second ago is already there."""
    client, b, clock, k, optin = elec_env(env)
    poll(b)
    clock.t += 1
    k.px[GA] = (99, 100)
    poll(b)
    login(client, "1", "David")
    d = client.get("/api/v1/wall/bets").json()
    ga = next(r for r in d["election"] if r["ticker"] == GA)
    assert ga["mark"] == 99.0
    feed = client.post("/api/v1/wall/bets/feed", json={"games": [], "election": {}}, headers={"X-Api-Key": "sekrit"}).json()
    assert next(r for r in feed["election"] if r["ticker"] == GA)["mark"] == 99.0


def test_race_status_sent_by_the_wall_reaches_the_watch_page(env):
    client, b, clock, k, optin = elec_env(env)
    poll(b)
    races = {"S-GA": _count("S-GA", winner="Ossoff", pct=93)}
    offices = {o: balance(list(RACES.values()), o) for o in OFFICES}
    client.post("/api/v1/wall/bets/feed", json={"games": [], "election": {"races": races, "offices": offices}},
                headers={"X-Api-Key": "sekrit"})
    login(client, "1", "David")
    ga = next(r for r in client.get("/api/v1/wall/bets").json()["election"] if r["ticker"] == GA)
    assert ga["won"] is True and ga["highlight"] == "✓ CALLED · Hansen wins +39%" and ga["status"].startswith("✓ Ossoff (D)")


def test_election_tickers_share_the_one_rate_limited_poll(env):
    client, b, clock, k, optin = elec_env(env)
    slept = []

    async def sleep(s):
        slept.append(s)
    b.sleep = sleep
    b.limiter = kw.RateLimit(rate=2, burst=1, clock=clock)   # 3 sharers + 1 batch = 4 calls, clock frozen
    poll(b)
    assert slept == [0.5, 1.0, 1.5]
    mk = [c for c in k.calls if c[0] == "mkt"]
    assert len(mk) == 1 and {GA, ME, CTRL} <= set(mk[0][1])   # sports + election tickers in one /markets batch
    n = len(k.calls)
    login(client, "1", "David")
    for _ in range(5):
        client.get("/api/v1/wall/bets")
        client.post("/api/v1/wall/bets/feed", json={"games": [], "election": {}}, headers={"X-Api-Key": "sekrit"})
    assert len(k.calls) == n                                  # viewers read the shared book


# ---- the wall service side ---------------------------------------------------------------------------------------------
def _rows():
    return [{"who": "Hansen", "ticker": GA, "label": "GA Senate · Ossoff", "side": "YES", "entry": 70.0, "mark": 98.0, "pct": 40.0,
             "dir": "up", "size": "250+ ct", "kind": "election", "election": {"class": {"type": "race", "office": "S", "state": "GA",
             "district": None, "special": False, "year": 2026}, "pick": {"party": "D", "name": "Jon Ossoff"}, "label": "GA Senate · Ossoff",
             "strike": {}}}]


def _snap(races):
    r = Results([])
    r.races, r.source = races, "CNN"
    return r.snapshot()


def test_board_trades_and_called_highlight():
    snap = _snap([*[x for x in RACES.values() if x["id"] != "S-GA"], _count("S-GA", pct=40)])
    t = board(snap, _rows())["trades"]
    assert t[0]["status"].startswith("40% in · ") and t[0]["highlight"] == "" and t[0]["chip"] == "YES D"
    snap = _snap([*[x for x in RACES.values() if x["id"] != "S-GA"], _count("S-GA", winner="Ossoff", pct=93)])
    t = board(snap, _rows())["trades"]
    assert t[0]["highlight"] == "✓ CALLED · Hansen wins +40%" and t[0]["won"] is True
    assert holders(t) == {"S-GA": [{"who": "Hansen", "chip": "YES D", "won": True}]}
    assert board(snap, None)["trades"] == [] and trades([], snap) == []


def test_bets_feed_asks_for_election_rows_only_when_wanted():
    sent = []

    def http(method, path, body):
        sent.append(body)
        return 200, {"games": {}, "election": _rows(), "need": ["S-GA"]}
    f = BetsFeed(http=http)
    f.poll([])
    assert sent == [] and f.election == []                   # no games, no election night: nothing asked
    f.poll([], {"races": {}, "offices": {}})
    assert sent[-1] == {"games": [], "election": {"races": {}, "offices": {}}} and f.election and f.need == ["S-GA"]
    f.poll([{"league": "nba", "id": "1"}])
    assert "election" not in sent[-1] and f.election == []


def test_service_board_and_data_json_carry_trades_and_chips(tmp_path):
    from starlette.testclient import TestClient
    from wall.llm import LLMGuard
    from wall.server import Cfg, build
    cfg = Cfg({"WALL_STATE_DIR": str(tmp_path), "WALL_TV": "0", "WALL_DISCORD": "0", "WALL_IMPORT_FROM": ""})
    res = Results([])
    res.races, res.source = [*[x for x in RACES.values() if x["id"] != "S-GA"], _count("S-GA", winner="Ossoff", pct=93)], "CNN"
    res.touch = lambda: None
    bets = BetsFeed(http=lambda *a: (200, {"games": {}}))
    bets.election = _rows()
    app = build(cfg, {}, guard=LLMGuard(mode="off"), results=res, bets=bets)
    c = TestClient(app, base_url="http://127.0.0.1:7795", client=("127.0.0.1", 5555))
    b = c.get("/p/election/board.json").json()
    assert b["trades"][0]["highlight"] == "✓ CALLED · Hansen wins +40%"
    assert c.get("/p/election/data.json").json()["holders"]["S-GA"][0]["chip"] == "YES D"
    app.state.sync.show = False                               # bets hidden on the overlay: no trades, no chips
    assert c.get("/p/election/board.json").json()["trades"] == [] and c.get("/p/election/data.json").json()["holders"] == {}
    assert json.dumps(b).count("688445043975127051") == 0     # never a Discord id on the TV
