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


# --- premium/pokemon seeding + boxes (SharpLab addition) --------------------


def test_draft_tiers_are_valid_and_premium():
    keys = [k for k, _ in cards.DRAFT_TIERS]
    assert set(keys) == set(cards.RARITIES)
    frac = dict(cards.DRAFT_TIERS)
    assert abs(sum(frac.values()) - 1.0) < 1e-9
    # premium: more legendary/epic than the default player mix
    default = dict(cards.PLAYER_TIERS)
    assert frac["legendary"] > default["legendary"]
    assert frac["epic"] > default["epic"]


def test_pokemon_rarity_map_is_total():
    src = ["Common", "Uncommon", "Rare", "Double rare", "Illustration rare",
           "Ultra Rare", "ACE SPEC Rare", "Special illustration rare",
           "Hyper rare", "Mega Hyper Rare", "Black White Rare"]
    for s in src:
        assert cards.map_pokemon_rarity(s) in cards.RARITIES
    assert cards.map_pokemon_rarity("Common") == "common"
    assert cards.map_pokemon_rarity("Hyper rare") == "legendary"
    assert cards.map_pokemon_rarity("Ultra Rare") == "epic"
    # unknown falls back to common (never crash a seed run)
    assert cards.map_pokemon_rarity("Nonsense") == "common"


def test_pokemon_book_value_uses_price_with_tier_floor():
    # $397 Charizard -> 39700 coins
    assert cards.pokemon_book_value(397.07, "legendary") == 39707.0
    # a $0.02 common (2 coins at COIN_PER_USD=100) floors at the common book value (3.5), never below.
    # NOTE: brief used $0.19 here, but 0.19 * 100 = 19 > BOOK["common"] (3.5), so it never
    # actually exercised the floor; corrected the price to genuinely test the floor path.
    assert cards.pokemon_book_value(0.02, "common") == cards.BOOK["common"]


def test_needs_guaranteed_hit():
    assert cards.needs_guaranteed_hit([{"rarity": "common"}, {"rarity": "rare"}])
    assert not cards.needs_guaranteed_hit([{"rarity": "common"}, {"rarity": "epic"}])
    assert not cards.needs_guaranteed_hit([{"rarity": "legendary"}])
    assert cards.needs_guaranteed_hit([])  # empty box would need a hit (defensive)


def test_box_price():
    assert cards.PACKS_PER_BOX == 36
    # NOTE: brief's expected value (49_996) isn't a multiple of 36 and can't match
    # box_price = 36 * base_cost for any integer base_cost; corrected to 36 * 1389 = 50_004.
    assert cards.box_price(1389) == 50_004
    assert cards.box_price(6944) == 249_984


def test_box_summary_embed_counts_and_notables():
    from bot.cogs.cards import _box_summary_embed
    haul = (
        [{"rarity": "common", "name": "C", "is_holo": False, "gem": None, "book_value": 3.5, "serial": 1, "total_copies": 99}] * 170
        + [{"rarity": "epic", "name": "Grail", "is_holo": False, "gem": None, "book_value": 100, "serial": 1, "total_copies": 6}]
        + [{"rarity": "legendary", "name": "Big", "is_holo": True, "gem": "ruby", "book_value": 5000, "serial": 1, "total_copies": 1}] * 1
    )
    emb = _box_summary_embed(haul, guaranteed=False, title="Test Box", set_name="Test Set")
    body = emb.description + "".join(f.name + f.value for f in emb.fields)
    assert "171" in body or "170" in body  # commons count shown
    assert "Grail" in body      # epic highlighted
    assert "Big" in body        # legendary highlighted


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
        await _queries.credit_coins("web", 60, "Won bingo", "t")   # ≥50 → shown on the page
        await _queries.credit_coins("web", 10, "Message", "t")     # <50 → hidden from the page
        monkeypatch.setattr(_webcards.auth, "read_session", lambda req: {"id": "web"})
        res = await _webcards.coin_history(_FakeReq())
        assert res["balance"] == _queries.CASINO_STARTING_COINS + 70  # balance counts everything
        assert [e["reason"] for e in res["ledger"]] == ["Won bingo"]  # trickle hidden

    _run(go())


# --- Trade market: coin-sweetened trades + listings --------------------------


async def _two_traders(tmp_db):
    """A owns instance 1, B owns instance 2 (both 'rare'). Returns nothing; ids are 1 and 2."""
    _sid, designs = await _make_set("nba", 2024, [("rare", 2, 30)])
    d = _dids(designs, "rare")
    await _give("A", d[0], 1, "rare")  # instance_id 1
    await _give("B", d[1], 1, "rare")  # instance_id 2


def test_accept_trade_moves_coins_positive(tmp_db):
    async def go():
        await _two_traders(tmp_db)
        await _fund("A", 500)
        await _fund("B", 500)
        tid = await _queries.create_card_trade("A", "B", [1], [2], "t", coins=100)  # A adds 100
        res = await _queries.accept_card_trade(tid, "B")
        assert res["coins"] == 100
        assert (await _queries.get_instances_public([1]))[1]["owner_id"] == "B"  # cards swapped
        assert (await _queries.get_instances_public([2]))[2]["owner_id"] == "A"
        assert await _queries.get_casino_balance("A") == 400  # paid 100
        assert await _queries.get_casino_balance("B") == 600  # received 100

    _run(go())


def test_accept_trade_moves_coins_negative(tmp_db):
    async def go():
        await _two_traders(tmp_db)
        await _fund("A", 500)
        await _fund("B", 500)
        tid = await _queries.create_card_trade("A", "B", [1], [2], "t", coins=-100)  # A requests 100
        await _queries.accept_card_trade(tid, "B")
        assert await _queries.get_casino_balance("A") == 600  # received 100
        assert await _queries.get_casino_balance("B") == 400  # paid 100

    _run(go())


def test_accept_trade_insufficient_coins_rolls_back(tmp_db):
    async def go():
        await _two_traders(tmp_db)
        await _fund("A", 50)  # A can't cover a 1000-coin sweetener
        await _fund("B", 50)
        tid = await _queries.create_card_trade("A", "B", [1], [2], "t", coins=1000)
        with pytest.raises(ValueError):
            await _queries.accept_card_trade(tid, "B")
        # nothing moved — transaction rolled back
        assert (await _queries.get_instances_public([1]))[1]["owner_id"] == "A"
        assert await _queries.get_casino_balance("A") == 50
        assert (await _queries.get_card_trade(tid))["status"] == "pending"

    _run(go())


def test_pure_coin_offer_transfers_card(tmp_db):
    async def go():
        await _two_traders(tmp_db)
        await _fund("A", 500)
        # A offers NO cards, just 300 coins, for B's instance 2
        tid = await _queries.create_card_trade("A", "B", [], [2], "t", coins=300)
        await _queries.accept_card_trade(tid, "B")
        assert (await _queries.get_instances_public([2]))[2]["owner_id"] == "A"
        assert await _queries.get_casino_balance("A") == 200

    _run(go())


def test_trade_listing_lifecycle_and_selfheal(tmp_db):
    async def go():
        await _two_traders(tmp_db)
        await _queries.create_trade_listing(1, "A", "want rookies", "t")
        mkt = await _queries.list_trade_market()
        assert len(mkt) == 1 and mkt[0]["instance_id"] == 1 and mkt[0]["note"] == "want rookies"
        with pytest.raises(ValueError):  # can't list a card you don't own
            await _queries.create_trade_listing(1, "B", None, "t")
        # self-heal: move the card to B; the stale listing (owner A) drops out of the market
        async with aiosqlite.connect(_queries.DB_PATH) as db:
            await db.execute("UPDATE card_instances SET owner_id = 'B' WHERE instance_id = 1")
            await db.commit()
        assert await _queries.list_trade_market() == []
        await _queries.remove_trade_listing(1, "A")

    _run(go())


def test_accept_trade_clears_listing(tmp_db):
    async def go():
        await _two_traders(tmp_db)
        await _queries.create_trade_listing(2, "B", None, "t")  # B lists their card
        tid = await _queries.create_card_trade("A", "B", [1], [2], "t")
        await _queries.accept_card_trade(tid, "B")
        assert await _queries.list_trade_market() == []  # traded card left the board

    _run(go())


def test_sell_clears_listing(tmp_db):
    async def go():
        _sid, designs = await _make_set("nba", 2024, [("rare", 1, 30)])
        await _give("A", _dids(designs, "rare")[0], 1, "rare")
        await _queries.create_trade_listing(1, "A", None, "t")
        await _queries.sell_instance("A", 1)
        assert await _queries.list_trade_market() == []

    _run(go())


def test_web_list_offer_accept_roundtrip(tmp_db, monkeypatch):
    async def go():
        await _two_traders(tmp_db)  # A owns 1, B owns 2
        await _fund("A", 500)
        monkeypatch.setattr(_webcards.auth, "read_session", lambda req: {"id": "B"})
        assert await _webcards.list_for_trade(_FakeReq(), _webcards.ListBody(instance_id=2, note="open")) == {"ok": True}
        # A offers card 1 + 50 coins for B's listed card 2
        monkeypatch.setattr(_webcards.auth, "read_session", lambda req: {"id": "A"})
        r = await _webcards.make_offer(_FakeReq(), _webcards.TradeBody(want_ids=[2], offer_ids=[1], coins=50))
        tid = r["trade_id"]
        # B sees it incoming (with previews) and accepts
        monkeypatch.setattr(_webcards.auth, "read_session", lambda req: {"id": "B"})
        tr = await _webcards.my_trades(_FakeReq())
        assert len(tr["incoming"]) == 1
        assert tr["incoming"][0]["want_cards"][0]["instance_id"] == 2
        assert tr["incoming"][0]["coins"] == 50
        res = await _webcards.accept_offer(tid, _FakeReq())
        assert res["ok"]
        assert (await _queries.get_instances_public([2]))[2]["owner_id"] == "A"  # card moved to A
        assert await _queries.list_trade_market() == []                          # listing cleared
        assert await _queries.get_casino_balance("A") == 450                     # A paid 50

    _run(go())


def test_web_list_rejects_unowned(tmp_db, monkeypatch):
    async def go():
        await _two_traders(tmp_db)
        monkeypatch.setattr(_webcards.auth, "read_session", lambda req: {"id": "A"})
        res = await _webcards.list_for_trade(_FakeReq(), _webcards.ListBody(instance_id=2))  # A doesn't own 2
        assert getattr(res, "status_code", None) == 400

    _run(go())


def test_web_offer_on_unlisted_rejected(tmp_db, monkeypatch):
    async def go():
        await _two_traders(tmp_db)
        monkeypatch.setattr(_webcards.auth, "read_session", lambda req: {"id": "A"})
        res = await _webcards.make_offer(_FakeReq(), _webcards.TradeBody(want_ids=[2], offer_ids=[1]))
        assert getattr(res, "status_code", None) == 400  # card 2 isn't listed

    _run(go())


def test_activity_reward_override_scales_caps_and_logs(tmp_db):
    async def go():
        # amount_override sets the per-event amount (the NBA-guess earliness bonus), still
        # capped at the source's daily cap, and logged to the coin ledger.
        r = await _queries.grant_activity_reward("g1", "nba_guess", "d1", amount_override=55, reason="NBA player guess")
        assert r == 55
        led = await _queries.get_coin_ledger("g1")
        assert led[0]["amount"] == 55 and led[0]["reason"] == "NBA player guess"
        # nba_guess daily cap is 200 → further grants clip to it
        for _ in range(4):
            await _queries.grant_activity_reward("g1", "nba_guess", "d1", amount_override=55, reason="NBA player guess")
        assert sum(e["amount"] for e in await _queries.get_coin_ledger("g1")) == 200

    _run(go())


def test_premium_pack_achievements_registered_and_wired():
    from collections import defaultdict
    from shared.achievements import ACHIEVEMENTS_BY_ID
    from bot.cogs.progression import _achievement_checks
    assert {"box_opener", "card_set_complete"} <= set(ACHIEVEMENTS_BY_ID)
    # present in the evaluation rules
    ids = {aid for aid, _ in _achievement_checks(defaultdict(int))}
    assert {"box_opener", "card_set_complete"} <= ids
    # wired to the right stats: setting those stats earns exactly these
    s = defaultdict(int); s["cards_has_box"] = 1; s["cards_completed_sets"] = 1
    earned = {aid for aid, ok in _achievement_checks(s) if ok}
    assert {"box_opener", "card_set_complete"} <= earned


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
