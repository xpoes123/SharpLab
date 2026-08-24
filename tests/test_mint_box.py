"""mint_box: 36-pack box open — charge, draw, guaranteed hit, atomicity."""
from __future__ import annotations

import asyncio

import pytest

import db.schema as _schema
import db.queries as _queries
from shared import cards as engine


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


async def _seed_set(base_cost: int, total_packs: int, designs: list[dict]) -> int:
    set_id = await _queries.create_card_set("nba", 2003, "Test Set", total_packs, base_cost, "2026-01-01T00:00:00Z")
    await _queries.insert_card_designs(set_id, designs)
    return set_id


async def _fund(user: str, amount: int) -> None:
    # NOTE: brief called update_casino_balance() directly on a brand-new user, but its
    # positive-delta branch only UPDATEs an existing casino_wallets row (no upsert) — on a
    # fresh wallet that's 0 rows affected, then the balance readback is None and blows up
    # with TypeError. get_or_create_casino_wallet() (see test_economy.py's established
    # pattern) creates the row first; update_casino_balance() then tops it up to `amount`.
    await _queries.get_or_create_casino_wallet(user)
    await _queries.update_casino_balance(user, amount - _queries.CASINO_STARTING_COINS)


def _common_only_designs(n: int, copies: int) -> list[dict]:
    return [{
        "subject_key": f"c{i}", "subject_name": f"C{i}", "team": None,
        "rarity": "common", "is_rookie": False, "career_fame": 0.0,
        "total_copies": copies, "stats": {}, "headshot_url": None,
        "book_value": engine.BOOK["common"],
    } for i in range(n)]


def test_box_charges_and_opens_180(tmp_db):
    async def go():
        # rich enough pool: 200 commons x 3 copies + some epics
        designs = _common_only_designs(200, 3)
        designs += [{
            "subject_key": f"e{i}", "subject_name": f"E{i}", "team": None,
            "rarity": "epic", "is_rookie": False, "career_fame": 0.0,
            "total_copies": 10, "stats": {}, "headshot_url": None,
            "book_value": engine.BOOK["epic"],
        } for i in range(10)]
        set_id = await _seed_set(base_cost=100, total_packs=50, designs=designs)
        await _fund("u1", 1_000_000)
        res = await _queries.mint_box("u1", set_id, "2026-01-01T00:00:00Z")
        assert len(res["cards"]) >= 180  # 36*5, plus maybe a guaranteed bonus
        bal = await _queries.get_casino_balance("u1")
        assert bal == 1_000_000 - engine.box_price(100)  # 3600 charged
        cset = await _queries.get_card_set("nba", 2003)
        assert cset["packs_opened"] == 36
    _run(go())


def test_box_always_contains_epic_when_set_has_one(tmp_db):
    # Invariant (the user-facing guarantee), deterministic regardless of draw path:
    # a set that contains an epic yields at least one epic+ in every box.
    async def go():
        designs = _common_only_designs(300, 5)
        designs.append({
            "subject_key": "epic1", "subject_name": "GrailEpic", "team": None,
            "rarity": "epic", "is_rookie": False, "career_fame": 0.0,
            "total_copies": 5, "stats": {}, "headshot_url": None,
            "book_value": engine.BOOK["epic"],
        })
        set_id = await _seed_set(base_cost=10, total_packs=50, designs=designs)
        await _fund("u2", 1_000_000)
        res = await _queries.mint_box("u2", set_id, "2026-01-01T00:00:00Z")
        assert any(c["rarity"] in ("epic", "legendary") for c in res["cards"])
    _run(go())


def test_box_no_epic_available_does_not_crash(tmp_db):
    # Commons-only set: guarantee can't fire (no epic pool), stays False, no crash.
    async def go():
        designs = _common_only_designs(300, 5)
        set_id = await _seed_set(base_cost=10, total_packs=50, designs=designs)
        await _fund("u5", 1_000_000)
        res = await _queries.mint_box("u5", set_id, "2026-01-01T00:00:00Z")
        assert res["guaranteed_upgraded"] is False
        assert all(c["rarity"] == "common" for c in res["cards"])
    _run(go())


def test_box_refuses_when_fewer_than_36_packs_left(tmp_db):
    async def go():
        designs = _common_only_designs(50, 2)  # 100 cards = 20 packs of pool
        set_id = await _seed_set(base_cost=10, total_packs=20, designs=designs)
        await _fund("u3", 1_000_000)
        with pytest.raises(ValueError):
            await _queries.mint_box("u3", set_id, "2026-01-01T00:00:00Z")
        # charge rolled back
        assert await _queries.get_casino_balance("u3") == 1_000_000
    _run(go())


def test_box_refuses_when_broke(tmp_db):
    async def go():
        designs = _common_only_designs(300, 5)
        set_id = await _seed_set(base_cost=100000, total_packs=50, designs=designs)
        await _fund("u4", 500)
        with pytest.raises(ValueError):
            await _queries.mint_box("u4", set_id, "2026-01-01T00:00:00Z")
    _run(go())
