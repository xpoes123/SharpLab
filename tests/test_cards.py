"""Unit tests for the pure card-economics engine (shared/cards.py) plus the
DB-backed set-completion + dupe trade-up features (bot/cogs/cards.py)."""

import asyncio
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import cards


def _subjects(n=200):
    return [
        {
            "key": f"p{i}",
            "name": f"p{i}",
            "season": 2003,
            "stardom": float(i),
            "is_rookie": False,
        }
        for i in range(n)
    ]


# --- book value / bands ------------------------------------------------------


def test_book_value_is_band_floor():
    for r in cards.RARITIES:
        assert cards.band(r, False, None)[0] == cards.book_value(r, False, None)


def test_rarity_book_ordering():
    vals = [cards.book_value(r, False, None) for r in cards.RARITIES]
    assert vals == sorted(vals) and len(set(vals)) == len(vals)  # strictly increasing


def test_holo_doubles_book():
    assert cards.book_value("rare", True, None) == 2 * cards.book_value("rare", False, None)


def test_gem_floor_applies():
    b = cards.book_value("common", False, "sapphire")
    assert b == max(
        cards.book_value("common", False, None) * cards.GEMS["sapphire"][1],
        cards.GEMS["sapphire"][2],
    )
    assert b >= cards.GEMS["sapphire"][2]


def test_ceiling_above_floor():
    lo, hi = cards.band("epic", False, None)
    assert hi > lo


# --- rolls -------------------------------------------------------------------


def test_gem_roll_deterministic_and_rare():
    rng = random.Random(0)
    n = 100_000
    counts = {}
    for _ in range(n):
        g = cards.roll_gem(rng)
        counts[g] = counts.get(g, 0) + 1
    assert counts.get("black_lotus", 0) < n * 0.001  # extremely rare, not guaranteed
    assert counts.get(None, 0) > n * 0.9  # most cards have no gem


# --- manifest ----------------------------------------------------------------


def test_build_manifest_sums_and_orders():
    m = cards.build_manifest(_subjects(200), total_cards=5000)
    assert sum(d["copies"] for d in m) == 5000  # total copies == requested
    by_key = {d["key"]: d for d in m}
    assert by_key["p199"]["rarity"] == "legendary"  # highest stardom
    assert by_key["p0"]["rarity"] == "common"  # lowest stardom


def test_rarer_tiers_get_fewer_copies_per_design():
    """Per-design copy counts must decrease monotonically as tiers get rarer."""
    m = cards.build_manifest(_subjects(400), total_cards=8000)
    avg = {}
    for r in cards.RARITIES:
        designs = [d for d in m if d["rarity"] == r]
        assert designs, f"no {r} designs"
        avg[r] = sum(d["copies"] for d in designs) / len(designs)
    # common >= uncommon >= rare >= epic >= legendary
    ordered = [avg[r] for r in cards.RARITIES]  # common..legendary
    assert ordered == sorted(ordered, reverse=True)
    assert avg["legendary"] <= avg["common"]


def test_legendaries_can_be_forced_to_one_of_one():
    """With a small print run the seeder short-prints legendaries down to 1-of-1;
    build_manifest floors every design at >= 1 copy, so a low total_cards yields exactly 1."""
    m = cards.build_manifest(_subjects(200), total_cards=250)
    legs = [d for d in m if d["rarity"] == "legendary"]
    assert legs
    assert all(d["copies"] == 1 for d in legs)  # 1-of-1 legendaries
    assert all(d["copies"] >= 1 for d in m)  # nothing is unmintable


# --- pack open ---------------------------------------------------------------


def test_open_pack_size_and_depletes_pool():
    m = cards.build_manifest(_subjects(200), 5000)
    pool = {i: d["copies"] for i, d in enumerate(m)}
    before = sum(pool.values())
    out = cards.open_pack(pool, m, random.Random(1), pack_size=5)
    assert len(out) == 5
    assert sum(pool.values()) == before - 5
    for c in out:
        assert c["book_value"] >= cards.BOOK[m[c["design_index"]]["rarity"]]


def test_open_pack_never_exceeds_finite_pool():
    """A pack can never draw more cards than the whole finite pool holds."""
    m = cards.build_manifest(_subjects(50), 60)  # tiny pool
    pool = {i: d["copies"] for i, d in enumerate(m)}
    supply = sum(pool.values())
    out = cards.open_pack(pool, m, random.Random(2), pack_size=supply + 100)
    assert len(out) == supply  # capped at what exists
    assert all(c >= 0 for c in pool.values())
    assert sum(pool.values()) == 0  # fully drained


# --- expected value ----------------------------------------------------------


def test_expected_pack_value_positive_and_sane():
    m = cards.build_manifest(_subjects(200), 5000)
    ev = cards.expected_pack_value(m, pack_size=5)
    assert ev > 0
    assert 30 < ev < 70  # ballpark of the RTP target


# --- notable pulls -----------------------------------------------------------


def test_is_notable_pull_true_cases():
    assert cards.is_notable_pull({"rarity": "legendary", "is_holo": False, "gem": None})
    assert cards.is_notable_pull({"rarity": "epic", "is_holo": False, "gem": None})
    assert cards.is_notable_pull({"rarity": "common", "is_holo": False, "gem": "sapphire"})
    assert cards.is_notable_pull({"rarity": "common", "is_holo": False, "gem": "black_lotus"})
    assert cards.is_notable_pull({"rarity": "rare", "is_holo": True, "gem": None})


def test_is_notable_pull_false_for_plain_common():
    assert not cards.is_notable_pull({"rarity": "common", "is_holo": False, "gem": None})
    assert not cards.is_notable_pull({"rarity": "common", "is_holo": True, "gem": None})
    assert not cards.is_notable_pull({"rarity": "rare", "is_holo": False, "gem": None})
    assert not cards.is_notable_pull({"rarity": "uncommon", "is_holo": False, "gem": "chrome"})


def test_describe_pull_includes_season_and_tags():
    tag = cards.describe_pull(
        {"total_copies": 1, "gem": None, "is_holo": True, "rarity": "legendary",
         "name": "LeBron James", "season": 2003}
    )
    assert "1-of-1" in tag and "Holo" in tag and "Legendary" in tag
    assert "LeBron James" in tag and "(2003)" in tag


# --- vintage pricing (SharpLab addition) -------------------------------------


def test_pack_cost_current_year_equals_base():
    assert cards.pack_cost(2026, 2026) == cards.BASE_PACK_COST
    assert cards.pack_cost(2030, 2026) == cards.BASE_PACK_COST  # future clamps to base


def test_pack_cost_monotonic_increasing_with_age():
    current = 2026
    costs = [cards.pack_cost(y, current) for y in range(current, current - 30, -1)]
    for a, b in zip(costs, costs[1:]):
        assert b >= a  # older season -> not cheaper
    assert costs[-1] > costs[0]  # 29y older strictly pricier than current
    # spec sanity: ~108 at 10y, ~235 at 20y
    assert cards.pack_cost(current - 10, current) == round(50 * 1.08 ** 10)
    assert cards.pack_cost(current - 20, current) == round(50 * 1.08 ** 20)


# ─────────────────────────────────────────────────────────────────────────────
# DB-backed: set-completion rewards + dupe trade-up (bot/cogs/cards.py)
# ─────────────────────────────────────────────────────────────────────────────

import aiosqlite  # noqa: E402

import db.schema as _schema  # noqa: E402
import db.queries as _queries  # noqa: E402
from bot.cogs.cards import _completion_reward_for, _tradeup_dupes  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def tmp_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    orig_s, orig_q = _schema.DB_PATH, _queries.DB_PATH
    _schema.DB_PATH = _queries.DB_PATH = db_path
    _run(_schema.init_db())
    yield db_path
    _schema.DB_PATH, _queries.DB_PATH = orig_s, orig_q


async def _make_set(sport, season, specs):
    """specs: list of (rarity, count, total_copies). Returns (set_id, designs) where
    designs are the catalog dicts (each has design_id + rarity)."""
    sid = await _queries.create_card_set(sport, season, f"{sport} {season}", 1000, 50, "t")
    designs, i = [], 0
    for rarity, count, total_copies in specs:
        for _ in range(count):
            designs.append({
                "subject_key": f"{sport}-{season}-{i}",
                "subject_name": f"Player {i}",
                "team": "TST",
                "rarity": rarity,
                "total_copies": total_copies,
                "book_value": cards.BOOK[rarity],
            })
            i += 1
    await _queries.insert_card_designs(sid, designs)
    cat = await _queries.get_catalog(sid)
    return sid, cat["designs"]


def _dids(designs, rarity):
    return [d["design_id"] for d in designs if d["rarity"] == rarity]


async def _give(user, design_id, serial, rarity, is_holo=0, gem=None):
    book = cards.book_value(rarity, bool(is_holo), gem)
    async with aiosqlite.connect(_queries.DB_PATH) as db:
        await db.execute(
            "INSERT INTO card_instances "
            "(design_id, owner_id, serial, is_holo, gem, book_value, acquired_cost, source, acquired_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, 'test', 't')",
            (design_id, user, serial, is_holo, gem, book),
        )
        await db.commit()


async def _count_instances(user, set_id, rarity=None):
    async with aiosqlite.connect(_queries.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        sql = ("SELECT COUNT(*) c FROM card_instances i JOIN card_designs d ON i.design_id = d.design_id "
               "WHERE i.owner_id = ? AND d.set_id = ?")
        args = [user, set_id]
        if rarity:
            sql += " AND d.rarity = ?"
            args.append(rarity)
        return (await (await db.execute(sql, args)).fetchone())["c"]


# --- set completion ----------------------------------------------------------


def test_completion_reward_formula():
    # 2 legendaries -> 0.25 * (2*260) = 130, below the 250 floor -> floored to 250.
    designs = [{"book_value": cards.BOOK["legendary"]}] * 2
    assert _completion_reward_for(designs) == 250
    # floored at the minimum for small book totals
    assert _completion_reward_for([{"book_value": 10}]) == 250
    # scales up with a big book total
    big = [{"book_value": 1000}] * 10
    assert _completion_reward_for(big) == round(0.25 * 10000)


def test_set_completion_detection(tmp_db):
    async def go():
        sid, designs = await _make_set("nba", 2003, [("common", 3, 50)])
        dids = _dids(designs, "common")
        u = "user1"
        # own none
        c0 = await _queries.get_set_completion(u, sid)
        assert c0 == {"owned": 0, "total": 3, "complete": False}
        # own 2 of 3 -> incomplete
        await _give(u, dids[0], 1, "common")
        await _give(u, dids[1], 1, "common")
        c1 = await _queries.get_set_completion(u, sid)
        assert c1["owned"] == 2 and not c1["complete"]
        # own the last design -> complete
        await _give(u, dids[2], 1, "common")
        c2 = await _queries.get_set_completion(u, sid)
        assert c2["owned"] == 3 and c2["complete"]
        # claim is one-time: True once, then False
        assert await _queries.mark_set_completion(u, sid) is True
        assert await _queries.mark_set_completion(u, sid) is False
        assert await _queries.set_completion_claimed(u, sid) is True

    _run(go())


# --- trade-up ----------------------------------------------------------------


def test_tradeup_consumes_dupes_and_mints_next_rarity(tmp_db):
    async def go():
        # 1 common design (plenty of copies) + 1 uncommon design to mint into.
        sid, designs = await _make_set("nba", 2010, [("common", 1, 100), ("uncommon", 1, 100)])
        common_did = _dids(designs, "common")[0]
        u = "trader"
        # 6 copies of the one common design -> 5 dupes (best copy protected).
        for s in range(1, 7):
            await _give(u, common_did, s, "common")
        collection, _ = await _queries.get_collection(u)
        dupes = _tradeup_dupes(collection, "nba", 2010, "common")
        assert len(dupes) == 5  # 6 copies minus the 1 protected best copy
        cost = 5
        consume_ids = [c["instance_id"] for c in dupes[:cost]]
        # mint first, then consume (mirrors the cog)
        minted = await _queries.mint_tradeup_card(u, sid, "uncommon", "t")
        assert minted is not None and minted["rarity"] == "uncommon"
        assert await _queries.consume_card_instances(u, consume_ids) is True
        # left with: 1 common (protected) + 1 uncommon
        assert await _count_instances(u, sid, "common") == 1
        assert await _count_instances(u, sid, "uncommon") == 1

    _run(go())


def test_tradeup_rejects_when_not_enough_dupes(tmp_db):
    async def go():
        sid, designs = await _make_set("nba", 2011, [("common", 1, 100)])
        did = _dids(designs, "common")[0]
        u = "poor"
        for s in range(1, 5):  # 4 copies -> only 3 dupes, cost is 5
            await _give(u, did, s, "common")
        collection, _ = await _queries.get_collection(u)
        dupes = _tradeup_dupes(collection, "nba", 2011, "common")
        assert len(dupes) == 3 and len(dupes) < 5

    _run(go())


def test_tradeup_protects_only_copies(tmp_db):
    async def go():
        # 3 distinct common designs, exactly one copy each -> zero dupes.
        sid, designs = await _make_set("nba", 2012, [("common", 3, 100)])
        u = "collector"
        for i, did in enumerate(_dids(designs, "common"), start=1):
            await _give(u, did, i, "common")
        collection, _ = await _queries.get_collection(u)
        assert _tradeup_dupes(collection, "nba", 2012, "common") == []
        # completion stays intact (nothing consumable)
        assert (await _queries.get_set_completion(u, sid))["complete"] is True

    _run(go())


def test_tradeup_selects_cheapest_and_keeps_best(tmp_db):
    async def go():
        sid, designs = await _make_set("nba", 2013, [("common", 1, 100)])
        u = "picky"
        did = _dids(designs, "common")[0]
        # one holo (higher book, should be KEPT) + two plain copies (dupes).
        await _give(u, did, 1, "common", is_holo=1)
        await _give(u, did, 2, "common")
        await _give(u, did, 3, "common")
        collection, _ = await _queries.get_collection(u)
        dupes = _tradeup_dupes(collection, "nba", 2013, "common")
        assert len(dupes) == 2
        # the protected (kept) copy is the holo; dupes are the two plain copies
        assert all(not c["is_holo"] for c in dupes)

    _run(go())


# --- reveal helpers (set_odds / reveal_order / pull_label) -------------------


def test_set_odds_pull_rates_sum_and_cover():
    designs = [
        {"rarity": "common", "total_copies": 330},
        {"rarity": "rare", "total_copies": 100},
        {"rarity": "legendary", "total_copies": 10},
    ]
    o = cards.set_odds(designs)
    assert o["holo_pct"] == round(cards.HOLO_RATE * 100, 1)
    assert set(o["pull_rates"]) == {"common", "rare", "legendary"}
    assert abs(sum(o["pull_rates"].values()) - 100) < 0.6  # rounding slack


def test_reveal_order_is_ascending():
    hand = [
        {"rarity": "legendary", "is_holo": False, "book_value": 260},
        {"rarity": "common", "is_holo": False, "book_value": 3.5},
        {"rarity": "rare", "is_holo": True, "book_value": 70},
    ]
    assert [c["rarity"] for c in cards.reveal_order(hand)] == ["common", "rare", "legendary"]


def test_is_rare_pull_threshold():
    # rarer than ~1% -> announce
    assert cards.is_rare_pull({"rarity": "legendary", "is_holo": False, "gem": None})
    assert cards.is_rare_pull({"rarity": "rare", "is_holo": False, "gem": "ruby"})
    assert cards.is_rare_pull({"rarity": "epic", "is_holo": True, "gem": None})
    # ~1% or more -> quiet
    assert not cards.is_rare_pull({"rarity": "epic", "is_holo": False, "gem": None})
    assert not cards.is_rare_pull({"rarity": "rare", "is_holo": True, "gem": None})
    assert not cards.is_rare_pull({"rarity": "common", "is_holo": False, "gem": "chrome"})


def test_pull_label_formats():
    pr = {"common": 62.0, "legendary": 2.3}
    assert cards.pull_label({"rarity": "common", "is_holo": False, "gem": None}, pr).endswith("%")
    assert cards.pull_label({"rarity": "legendary", "is_holo": False, "gem": None}, pr) == "1 in 43"
    lab = cards.pull_label({"rarity": "legendary", "is_holo": True, "gem": "ruby"}, pr)
    assert "ruby 1 in 2,000" in lab and "holo 1 in 5" in lab


# --- web open endpoint -------------------------------------------------------

import web.cards as _webcards  # noqa: E402


class _FakeReq:
    pass


async def _fund(user, amount):
    async with aiosqlite.connect(_queries.DB_PATH) as db:
        await db.execute(
            "INSERT INTO casino_wallets (discord_user, balance) VALUES (?, ?) "
            "ON CONFLICT(discord_user) DO UPDATE SET balance = ?",
            (user, amount, amount),
        )
        await db.commit()


def test_web_open_mints_and_debits(tmp_db, monkeypatch):
    async def go():
        sid, _ = await _make_set("nba", 2024, [("common", 6, 50), ("rare", 4, 30)])
        u = "webuser"
        await _fund(u, 500)
        monkeypatch.setattr(_webcards.auth, "read_session", lambda req: {"id": u})
        res = await _webcards.open_pack(_FakeReq(), _webcards.OpenBody(sport="nba", season=2024, n=2))
        assert len(res["cards"]) == 10  # 2 packs x 5
        idx = [cards.RARITIES.index(c["rarity"]) for c in res["cards"]]
        assert idx == sorted(idx)  # ascending reveal order
        assert res["odds"]["pull_rates"]
        assert await _queries.get_casino_balance(u) == 500 - 50 * 2  # debited base_cost per pack

    _run(go())


def test_web_open_insufficient_coins_400(tmp_db, monkeypatch):
    async def go():
        await _make_set("nba", 2024, [("common", 6, 50)])
        await _fund("broke", 10)  # base_cost is 50 — can't afford one pack
        monkeypatch.setattr(_webcards.auth, "read_session", lambda req: {"id": "broke"})
        res = await _webcards.open_pack(_FakeReq(), _webcards.OpenBody(sport="nba", season=2024, n=1))
        assert getattr(res, "status_code", None) == 400

    _run(go())


def test_web_sell_credits_and_removes(tmp_db, monkeypatch):
    async def go():
        sid, designs = await _make_set("nba", 2024, [("rare", 1, 30)])
        did = designs[0]["design_id"]
        u = "seller"
        await _give(u, did, 1, "rare")  # book = BOOK["rare"] = 35
        monkeypatch.setattr(_webcards.auth, "read_session", lambda req: {"id": u})
        res = await _webcards.sell_card(_FakeReq(), _webcards.SellBody(instance_id=1))
        assert res["coins"] == round(cards.BOOK["rare"] * _queries.QUICK_SELL_FRACTION)  # 26
        # first wallet credit seeds the starting balance, then adds the sale (existing sell_instance behavior)
        assert res["balance"] == _queries.CASINO_STARTING_COINS + res["coins"]
        # card is gone — collection now empty
        left, _ = await _queries.get_collection(u)
        assert left == []

    _run(go())


def test_web_sell_rejects_unowned_400(tmp_db, monkeypatch):
    async def go():
        monkeypatch.setattr(_webcards.auth, "read_session", lambda req: {"id": "nobody"})
        res = await _webcards.sell_card(_FakeReq(), _webcards.SellBody(instance_id=999))
        assert getattr(res, "status_code", None) == 400

    _run(go())


def test_list_collectors_ranks_by_value(tmp_db):
    async def go():
        sid, designs = await _make_set("nba", 2024, [("rare", 1, 30), ("common", 1, 50)])
        rare_did = _dids(designs, "rare")[0]
        common_did = _dids(designs, "common")[0]
        await _give("rich", rare_did, 1, "rare")      # 35
        await _give("rich", rare_did, 2, "rare")      # +35 = 70
        await _give("poor", common_did, 1, "common")  # 3.5
        await _queries.upsert_discord_user("rich", "RichUser", None)
        collectors = await _queries.list_collectors()
        assert [c["user_id"] for c in collectors] == ["rich", "poor"]  # ranked by value desc
        assert collectors[0]["username"] == "RichUser"       # cached name resolved
        assert collectors[0]["cards"] == 2
        assert collectors[1]["username"].startswith("Player ")  # uncached -> id fallback

    _run(go())


def test_get_public_user_falls_back(tmp_db):
    async def go():
        await _queries.upsert_discord_user("known", "KnownUser", "http://a/x.png")
        assert (await _queries.get_public_user("known"))["username"] == "KnownUser"
        assert (await _queries.get_public_user("ghost"))["username"].startswith("Player ")

    _run(go())


# --- Coin ledger + new coin sources ------------------------------------------


def test_credit_coins_logs_and_balances(tmp_db):
    async def go():
        bal = await _queries.credit_coins("u1", 150, "Achievement: Test", "t")
        assert bal == _queries.CASINO_STARTING_COINS + 150  # new wallet seeded + reward
        led = await _queries.get_coin_ledger("u1")
        assert len(led) == 1 and led[0]["amount"] == 150 and led[0]["reason"] == "Achievement: Test"
        # non-positive is a no-op (no ledger spam)
        await _queries.credit_coins("u1", 0, "nope", "t")
        assert len(await _queries.get_coin_ledger("u1")) == 1

    _run(go())


def test_add_xp_level_up_awards_coins(tmp_db):
    async def go():
        res = await _queries.add_xp("lvl", 1_000_000)  # guaranteed multi-level jump from 1
        gained = res["level"] - res["old_level"]
        assert gained > 0
        assert res["coins_awarded"] == 100 * gained
        assert await _queries.get_casino_balance("lvl") == _queries.CASINO_STARTING_COINS + 100 * gained
        led = await _queries.get_coin_ledger("lvl")
        assert led and led[0]["reason"] == f"Reached level {res['level']}"
        # more XP that doesn't cross a level → no coins, no new ledger row
        res2 = await _queries.add_xp("lvl", 1)
        assert res2["coins_awarded"] == 0
        assert len(await _queries.get_coin_ledger("lvl")) == 1

    _run(go())


def test_multiplayer_winner_only_gets_coins(tmp_db):
    async def go():
        from bot.cogs._elo_helpers import update_elo_multiplayer
        # 3 players, distinct scores → single winner (100)
        await update_elo_multiplayer([10, 20, 30], "bingo", "test",
                                     scores={10: 100.0, 20: 50.0, 30: 10.0})
        assert (await _queries.get_coin_ledger("10"))[0]["reason"] == "Won bingo"
        assert await _queries.get_casino_balance("10") == _queries.CASINO_STARTING_COINS + 50
        assert await _queries.get_coin_ledger("20") == []  # losers earn nothing
        assert await _queries.get_coin_ledger("30") == []

    _run(go())


def test_web_coins_endpoint(tmp_db, monkeypatch):
    async def go():
        await _queries.credit_coins("web", 40, "Won bingo", "t")
        monkeypatch.setattr(_webcards.auth, "read_session", lambda req: {"id": "web"})
        res = await _webcards.coin_history(_FakeReq())
        assert res["balance"] == _queries.CASINO_STARTING_COINS + 40
        assert res["ledger"][0]["reason"] == "Won bingo"

    _run(go())


if __name__ == "__main__":
    import inspect

    # Fallback self-check when pytest is unavailable. Skip fixture-requiring tests.
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    ran = 0
    for fn in fns:
        if inspect.signature(fn).parameters:
            continue  # needs a fixture (tmp_db) — pytest only
        fn()
        ran += 1
        print(f"ok  {fn.__name__}")
    print(f"\n{ran} FIXTURE-FREE CHECKS PASSED")
