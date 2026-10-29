import base64

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from shared import kalshi_account as ka


def _pem(key) -> str:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                             serialization.NoEncryption()).decode()


def test_settled_pnl_matches_vigil_reconciliation():
    assert ka.settled_pnl({"revenue": 50000, "yes_count_fp": "500", "no_count_fp": "0", "fee_cost": "0.0115",
                           "yes_total_cost_dollars": "455", "no_total_cost_dollars": "0"}) == pytest.approx(44.9885)
    # hedged pair: revenue 0, the 2000 matched pairs pay $1 each outside revenue
    assert ka.settled_pnl({"revenue": 0, "yes_count_fp": "2000", "no_count_fp": "2000", "fee_cost": "32",
                           "yes_total_cost_dollars": "675", "no_total_cost_dollars": "1800"}) == pytest.approx(-507)


def test_sport_and_marks():
    assert ka.sport("KXNBAPTS-26OCT21MILWAS-X") == "NBA" and ka.sport("KXWNBAGAME-X") == "WNBA"
    assert ka.sport("KXZZZ-1") == "Other"
    yes = ka.mark_position({"ticker": "KXNBAGAME-A", "position_fp": "10", "market_exposure_dollars": "4"},
                           {"yes_bid_dollars": "0.55", "yes_ask_dollars": "0.57"})
    assert yes["side"] == "YES" and yes["unreal"] == pytest.approx(1.5)
    no = ka.mark_position({"ticker": "KXNBAGAME-A", "position_fp": "-10", "market_exposure_dollars": "4"},
                          {"yes_bid_dollars": "0.55", "yes_ask_dollars": "0.57"})
    assert no["side"] == "NO" and no["value"] == pytest.approx(4.3)


def test_summarize_window():
    st = [{"ticker": "KXNBAGAME-A", "revenue": 1000, "yes_count_fp": "10", "no_count_fp": "0",
           "yes_total_cost_dollars": "6", "no_total_cost_dollars": "0", "fee_cost": "0.1",
           "settled_time": "2026-10-20T00:00:00Z"},
          {"ticker": "KXMLBGAME-B", "revenue": 0, "yes_count_fp": "5", "no_count_fp": "0",
           "yes_total_cost_dollars": "2", "no_total_cost_dollars": "0", "fee_cost": "0",
           "settled_time": "2026-01-01T00:00:00Z"}]
    s = ka.summarize(st + [dict(st[0], ticker="KXNHLGAME-X")], [{"unreal": 1.0, "sport": "NBA"}, {"unreal": 9.0, "sport": "Crypto"}])  # every sport counts
    assert s["n"] == 3 and s["won"] == 2 and s["realized"] == pytest.approx(5.8) and s["unrealized"] == 10.0
    assert ka.summarize(st, [], since_iso="2026-10-01")["n"] == 1


def test_encrypt_roundtrip_and_flattened_pem(monkeypatch):
    monkeypatch.setenv("KALSHI_CRED_KEY", Fernet.generate_key().decode())
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = _pem(key)
    blob = ka.encrypt(pem)
    assert pem.encode() not in blob and ka.decrypt(blob) == pem
    flat = " ".join(pem.split("\n"))  # Discord paste that lost its newlines
    assert ka.load_key(flat).private_numbers() == key.private_numbers()


def test_signature_verifies_against_full_path():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    h = ka.sign_headers("kid", key, "/portfolio/positions?limit=200", ts_ms=123)
    key.public_key().verify(base64.b64decode(h["KALSHI-ACCESS-SIGNATURE"]),
                            b"123GET/trade-api/v2/portfolio/positions",
                            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
                            hashes.SHA256())


def test_slate_grouping():
    assert ka.slate_date("KXNBAPTS-26OCT21MILWAS-MILTHERRO11-25") == "2026-10-21"
    assert ka.slate_date("KXNBA-26-OKC") is None and ka.slate_date("KXNBAGAME") is None
    st = lambda t, rev, cost: {"ticker": t, "revenue": rev, "yes_count_fp": "10", "no_count_fp": "0",
                               "yes_total_cost_dollars": str(cost), "no_total_cost_dollars": "0", "fee_cost": "0"}
    s = ka.slates([st("KXNBAGAME-26OCT21MILWAS-MIL", 1000, 6), st("KXNBAPTS-26OCT21MILWAS-X", 0, 4),
                   st("KXMLBGAME-26OCT21NYYLAD-NYY", 1000, 5), st("KXNBA-26-OKC", 1000, 1),
                   st("KXNHLGAME-26OCT21BOSTOR-BOS", 1000, 5), st("KXATPMATCH-26OCT21X-Y", 1000, 5)])
    assert list(s) == ["2026-10-21"]  # undated future without settled_time is skipped
    d = s["2026-10-21"]
    assert d["n"] == 5 and d["won"] == 4 and d["pnl"] == pytest.approx(15) and d["cost"] == 25
    assert d["sports"] == {"NBA": [2, 1, pytest.approx(0)], "MLB": [1, 1, pytest.approx(5)],
                           "NHL": [1, 1, pytest.approx(5)], "Tennis": [1, 1, pytest.approx(5)]}
    fut = dict(st("KXNBA-26-OKC", 1000, 1), settled_time="2026-10-22T06:00:00Z")  # 2am ET on the 22nd
    assert list(ka.slates([fut])) == ["2026-10-21"]  # before the 4am rollover


def test_group_fills_weights_partials_and_handles_cent_prices():
    rows = ka.group_fills([
        {"ticker": "KXNBAGAME-A", "action": "buy", "side": "yes", "count_fp": "10", "yes_price_dollars": "0.40", "ts": 2},
        {"ticker": "KXNBAGAME-A", "action": "buy", "side": "yes", "count": 30, "yes_price": 60, "ts": 1},
        {"ticker": "KXNBAGAME-A", "action": "sell", "side": "no", "count": 5, "no_price": 20, "ts": 3},
    ])
    buy, sell = rows
    assert (buy["qty"], buy["avg"]) == (40, pytest.approx(0.55))
    assert (sell["action"], sell["side"], sell["avg"]) == ("sell", "no", pytest.approx(0.20))


def test_market_url():
    assert ka.market_url("KXNBAGAME-26OCT23LALGSW-LAL") == "https://kalshi.com/markets/kxnbagame/kxnbagame-26oct23lalgsw"
    assert ka.market_url("KXNBA-26") == "https://kalshi.com/markets/kxnba/kxnba-26"


def test_resolutions_groups_holders_and_skips_old():
    st = lambda t, when, y, n: {"ticker": t, "settled_time": when, "market_result": "yes", "revenue": y * 100,
                                "yes_count_fp": str(y), "no_count_fp": str(n), "yes_total_cost_dollars": str(y * 0.5),
                                "no_total_cost_dollars": str(n * 0.5), "fee_cost": "0"}
    new, old = "2026-10-23T22:00:00Z", "2026-10-23T20:00:00Z"
    cut = ka._iso_ts("2026-10-23T21:00:00Z")
    res = ka.resolutions({
        "a": [st("KXNBAGAME-26OCT23LALGSW-LAL", new, 10, 0), st("KXNBAGAME-26OCT22X-X", old, 5, 0),
              st("KXBTC-26OCT23-B", new, 3, 0)],
        "b": [st("KXNBAGAME-26OCT23LALGSW-LAL", new, 0, 4)],
    }, {"a": cut, "b": cut})
    assert list(res) == ["KXNBAGAME-26OCT23LALGSW-LAL", "KXBTC-26OCT23-B"]  # all markets, old one skipped
    assert res["KXNBAGAME-26OCT23LALGSW-LAL"]["holders"] == [("a", "YES", pytest.approx(5)), ("b", "NO", pytest.approx(-2))]


async def test_event_titles_strips_suffix_and_skips_failures():
    import httpx

    def handler(req: httpx.Request) -> httpx.Response:
        t = req.url.path.rsplit("/", 1)[-1]
        if t == "BAD":
            return httpx.Response(404)
        return httpx.Response(200, json={"event": {"title": "Golden State vs Phoenix: Total Points"}})

    ka._EVENT_TITLES.clear()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        got = await ka.event_titles(c, {"KXNBATOTAL-26OCT24GSWPHX", "BAD"})
    assert got == {"KXNBATOTAL-26OCT24GSWPHX": "Golden State vs Phoenix"}


def test_today_pnl():
    held = {"ticker": "A", "qty": 10, "mark": 0.6, "prev_mark": 0.5, "unreal": 3.0}   # held overnight: 24h move
    new = {"ticker": "B", "qty": 10, "mark": 0.6, "prev_mark": 0.0, "unreal": -1.0}   # new market: cost basis
    traded = {"ticker": "C", "qty": 5, "mark": 0.4, "prev_mark": 0.3, "unreal": 0.25}  # bought today: cost basis
    st = {"revenue": 1000, "yes_count_fp": "10", "no_count_fp": "0", "yes_total_cost_dollars": "6",
          "no_total_cost_dollars": "0", "fee_cost": "0"}
    t = ka.today_pnl([held, new, traded], [st], {"C"})
    assert t["open_move"] == pytest.approx(1.0 - 1.0 + 0.25)
    assert t["settled"] == pytest.approx(4.0) and t["n_settled"] == 1
    assert t["total"] == pytest.approx(4.25)


def test_day_start_rolls_at_4am_et():
    from datetime import datetime
    late = datetime(2026, 10, 29, 1, 10, tzinfo=ka.ET)   # 1:10am still counts as the 28th
    assert ka.day_start(late) == datetime(2026, 10, 28, 4, 0, tzinfo=ka.ET)
    morning = datetime(2026, 10, 29, 9, 0, tzinfo=ka.ET)
    assert ka.day_start(morning) == datetime(2026, 10, 29, 4, 0, tzinfo=ka.ET)


def test_fees_reduce_unrealized_and_sum_on_fills():
    r = ka.mark_position({"ticker": "KXNBAGAME-A", "position_fp": "10", "market_exposure_dollars": "4",
                          "fees_paid_dollars": "0.25"}, {"yes_bid_dollars": "0.55"})
    assert r["fees"] == 0.25 and r["unreal"] == pytest.approx(1.25)
    g = ka.group_fills([{"ticker": "A", "count_fp": "5", "yes_price_dollars": "0.4", "fee_cost": "0.1", "ts": 1},
                        {"ticker": "A", "count_fp": "5", "yes_price_dollars": "0.4", "fee_cost": "0.15", "ts": 2}])
    assert g[0]["fee"] == pytest.approx(0.25)
