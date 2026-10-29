from bot.cogs import markets as mk


def _row(ticker, unreal, sport="NBA"):
    return {"ticker": ticker, "title": "Portland wins", "game": "Portland vs Los Angeles L", "sport": sport,
            "side": "YES", "qty": 100, "cost": 45.0, "fees": 1.0, "mark": 0.6, "value": 60.0, "unreal": unreal}


async def test_position_pages_and_view_paging():
    games = [_row(f"KXNBAGAME-26OCT29PORLAL-{i}", 14.0) for i in range(7)]
    acct = {"balance": 100.0, "open": games + [_row("KXNBA-27-OKC", -5.0, "Tennis")],
            "today": {"total": 10.0, "settled": 4.0, "n_settled": 1, "open_move": 6.0}}
    pages = mk._position_pages(acct, "xpoes")
    assert [len(pages[k]) for k in ("overview", "games", "futures")] == [1, 2, 1]
    assert len(pages["games"][0].fields) == 5 and len(pages["games"][1].fields) == 2
    f = pages["games"][0].fields[0]
    assert "Portland vs Los Angeles L — Portland wins" in f.name
    assert "45¢ → **60¢**" in f.value and "+14.00** (+30%)" in f.value and "kalshi.com/markets" in f.value

    v = mk._PositionsView(pages, owner_id=1)
    assert v.current() is pages["overview"][0] and v.prev_btn.disabled and v.next_btn.disabled
    v.tab = "games"; v._sync()
    assert v.count_btn.label == "1/2" and not v.next_btn.disabled
    v.page = 1; v._sync()
    assert v.next_btn.disabled and not v.prev_btn.disabled


async def test_empty_book():
    pages = mk._position_pages({"balance": 0.0, "open": [], "today": {"total": 0.0, "settled": 0.0,
                                "n_settled": 0, "open_move": 0.0}}, "x")
    assert pages["games"][0].description == "Nothing here."
