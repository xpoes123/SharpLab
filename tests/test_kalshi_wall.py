"""Members' Kalshi positions on the wall (shared/kalshi_wall.py): ESPN game ↔ Kalshi event matching on fixtures, the
spoiler-safe timeline (nothing seen after `as_of` is ever shown), opt-in filtering, size buckets and the rate limiter."""
from shared import kalshi_wall as kw

# ESPN games as the wall service describes them (abbreviations as ESPN spells them, UTC start)
LAL = {"league": "nba", "id": "401909921", "away": "MIA", "home": "LAL", "start": "2026-11-03T03:30Z"}   # 10:30 PM ET Nov 2
GSW = {"league": "nba", "id": "401909919", "away": "PHX", "home": "GS", "start": "2026-11-03T03:00Z"}
NYK = {"league": "nba", "id": "401909930", "away": "NY", "home": "SA", "start": "2026-11-04T00:30Z"}
RAMS = {"league": "nfl", "id": "401772900", "away": "LAR", "home": "WSH", "start": "2026-11-02T18:00Z"}


def test_match_event_teams_date_and_aliases():
    assert kw.match_event("KXNBAGAME-26NOV02MIALAL", LAL)
    assert kw.match_event("KXNBASPREAD-26NOV02MIALAL", LAL)
    assert kw.match_event("KXNBAPTS-26NOV02MIALAL", LAL)              # player props: same event key
    assert kw.match_event("KXNBAGAME-26NOV02PHXGSW", GSW)             # ESPN GS = Kalshi GSW
    assert kw.match_event("KXNBATOTAL-26NOV03NYKSAS", NYK)            # NY/SA aliases, 7:30 PM ET Nov 3
    assert kw.match_event("KXNFLGAME-26NOV02LAWAS", RAMS)             # NFL Rams LA, Commanders WAS
    assert kw.match_event("KXNBAGAME-26NOV02LALMIA", LAL)             # either order


def test_match_event_rejects_wrong_game():
    assert not kw.match_event("KXNBAGAME-26NOV04MIALAL", LAL)          # another day (the rematch)
    assert not kw.match_event("KXNBAGAME-26NOV02MIABOS", LAL)          # another opponent
    assert not kw.match_event("KXNFLGAME-26NOV02MIALAL", LAL)          # wrong league
    assert not kw.match_event("KXWNBAGAME-26NOV02MIALAL", LAL)
    assert not kw.match_event("KXNBAGAME-26NOV02MIALALX", LAL)
    assert not kw.match_event("garbage", LAL)
    assert not kw.match_event("KXNBAWINS-LAL-26", LAL)                 # season futures have no game date


def test_event_parts_with_start_time():
    assert kw.event_parts("KXNFLGAME-26NOV021300LAWAS") == ("KXNFLGAME", "26NOV02", "LAWAS")
    assert kw.match_event("KXNFLGAME-26NOV021300LAWAS", RAMS)
    assert kw.event_ticker_of("KXNBASPREAD-26NOV02MIALAL-LAL5") == "KXNBASPREAD-26NOV02MIALAL"


def test_market_labels():
    assert kw.market_label("KXNBAGAME-26NOV02MIALAL-LAL") == "LAL win"
    assert kw.market_label("KXNBASPREAD-26NOV02MIALAL-LAL3", {"floor_strike": 3.5}) == "LAL −3.5"
    assert kw.market_label("KXNBASPREAD-26NOV02MIALAL-LAL3") == "LAL −3.5"
    assert kw.market_label("KXNBATOTAL-26NOV02MIALAL-224", {"floor_strike": 224.5}) == "Over 224.5"
    assert kw.market_label("KXNBAPTS-26NOV02MIALAL-LALLJAMES23-25", {"yes_sub_title": "LeBron James: 25+"}) == "LeBron James: 25+"
    assert kw.kind("KXNFLSPREAD-26NOV02LAWAS-LA7") == "spread"


def test_size_buckets_and_marks():
    assert [kw.size_bucket(q) for q in (3, 10, 75, 300, 5000)] == ["<10", "10+", "50+", "250+", "1k+"]
    assert kw.side_mark("YES", 51, 53, 52) == 51
    assert kw.side_mark("NO", 51, 53, 52) == 47
    assert kw.side_mark("YES", None, None, 40) == 40


def _book(exact=False):
    b = kw.Book()
    b.members["u1"] = {"name": "David", "exact": exact}
    b.members["u2"] = {"name": "Steph", "exact": False}
    m = {"ticker": "KXNBASPREAD-26NOV02MIALAL-LAL3", "event_ticker": "KXNBASPREAD-26NOV02MIALAL", "floor_strike": 3.5,
         "yes_bid_dollars": "0.4200", "yes_ask_dollars": "0.4400", "last_price_dollars": "0.4300"}
    b.see_market(1000.0, m)
    b.see_positions(1000.0, "u1", {m["ticker"]: ("YES", 120, 50.4)})   # 120 @ 42¢
    b.see_positions(1000.0, "u2", {m["ticker"]: ("NO", 8, 4.48)})      # 8 @ 56¢
    return b, m


def test_a_price_seen_at_t_is_invisible_before_t_plus_delay():
    b, m = _book()
    D = 45.0
    b.see_market(1100.0, {**m, "yes_bid_dollars": "0.5100", "yes_ask_dollars": "0.5300"})   # the play happens at t=1100
    for now in (1100.0, 1120.0, 1144.9):
        row = kw.game_rows(b, LAL, now - D, {"u1"})[0]
        assert row["mark"] == 42 and row["dir"] == ""       # still the old price, no ▲
        assert max(row["spark"]) == 42
    row = kw.game_rows(b, LAL, 1145.0 - D, {"u1"})[0]
    assert row["mark"] == 51 and row["dir"] == "up" and row["pct"] == 21.4
    assert row["spark"][-1] == 51


def test_a_new_position_is_invisible_before_t_plus_delay():
    b, m = _book()
    b.see_positions(1200.0, "u1", {m["ticker"]: ("YES", 120, 50.4), "KXNBAGAME-26NOV02MIALAL-LAL": ("YES", 10, 6.0)})
    assert len(kw.game_rows(b, LAL, 1200.0 - 1, {"u1"})) == 1
    assert len(kw.game_rows(b, LAL, 1200.0, {"u1"})) == 2


def test_only_sharers_show_and_sizes_are_bucketed_unless_exact():
    b, _ = _book(exact=False)
    rows = kw.game_rows(b, LAL, 2000.0, {"u1"})
    assert [r["who"] for r in rows] == ["David"]            # Steph has positions but didn't opt in
    assert rows[0]["size"] == "50+ ct" and "pnl" not in rows[0]
    assert kw.game_rows(b, LAL, 2000.0, set()) == []
    b2, _ = _book(exact=True)
    r = kw.game_rows(b2, LAL, 2000.0, {"u1", "u2"})
    david = next(x for x in r if x["who"] == "David")
    steph = next(x for x in r if x["who"] == "Steph")
    assert david["size"] == "120 ct" and david["pnl"] == 0.0
    assert steph["size"] == "<10 ct" and "pnl" not in steph and steph["side"] == "NO" and steph["mark"] == 56


def test_other_games_dont_get_this_games_positions():
    b, _ = _book()
    assert kw.game_rows(b, GSW, 2000.0, {"u1", "u2"}) == []


def test_forget_drops_history():
    b, _ = _book()
    b.forget("u1")
    assert kw.game_rows(b, LAL, 2000.0, {"u1"}) == []


def test_tape_keeps_order_and_window():
    t = kw.Tape(keep_s=100)
    t.add(10, "a"); t.add(30, "c"); t.add(20, "b"); t.add(30, "c")
    assert t.at(9) is None and t.at(10) == "a" and t.at(25) == "b" and t.at(99) == "c"
    t.add(200, "d")
    assert t.at(50) is None and t.at(200) == "d"


def test_rate_limit_bucket():
    now = [0.0]
    rl = kw.RateLimit(rate=2, burst=3, clock=lambda: now[0])
    assert [rl.take() for _ in range(3)] == [0, 0, 0]
    assert rl.take() == 0.5
    now[0] = 10
    assert rl.take() == 0
