from bot.cogs import markets as mk


def _row(ticker, unreal, sport="NBA"):
    return {"ticker": ticker, "title": "Portland wins", "pick": "Portland", "game": "Portland vs Los Angeles L", "sport": sport,
            "side": "YES", "qty": 100, "cost": 45.0, "fees": 1.0, "mark": 0.6, "value": 60.0, "unreal": unreal}


async def test_position_pages_and_view_paging():
    games = [_row(f"KXNBAGAME-26OCT29PORLAL-{i}", 14.0) for i in range(7)]
    acct = {"balance": 100.0, "open": games + [_row("KXNBA-27-OKC", -5.0, "Tennis")],
            "today": {"total": 10.0, "settled": 4.0, "n_settled": 1, "open_move": 6.0}}
    pages = mk._position_pages(acct, "xpoes")
    assert [len(pages[k]) for k in ("overview", "games", "futures")] == [1, 2, 1]
    first = pages["games"][0].description
    assert first.count("🟢") == 6 and pages["games"][1].description.count("🟢") == 1
    assert "**[YES Portland](https://kalshi.com/markets/" in first and "Portland vs Los Angeles L" in first
    assert "45¢ → 60¢ · $46.00 → $60.00 · **+14.00** (+30%)" in first

    v = mk._PositionsView(pages, owner_id=1)
    assert v.current() is pages["overview"][0] and v.prev_btn not in v.children  # single page: no pager
    v.tab = "games"; v._sync()
    assert v.prev_btn in v.children and v.count_btn.label == "1/2" and not v.next_btn.disabled
    v.page = 1; v._sync()
    assert v.next_btn.disabled and not v.prev_btn.disabled


async def test_empty_book():
    pages = mk._position_pages({"balance": 0.0, "open": [], "today": {"total": 0.0, "settled": 0.0,
                                "n_settled": 0, "open_move": 0.0}}, "x")
    assert pages["games"][0].description == "Nothing here."


def test_event_name_and_when():
    from shared import kalshi_account as ka
    assert ka.event_name("Golden State vs Phoenix: Total Points") == "Golden State vs Phoenix"
    assert ka.event_name("Pro Basketball: Minnesota Total Wins") == "Minnesota Total Wins"
    assert ka.event_name("WI-03 House winner?") == "WI-03 House winner?"
    assert mk._when("KXNBA-27-OKC") == ""
