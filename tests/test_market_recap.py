"""Tests for market recap — embed builder and recently resolved query."""
import pytest
import pytest_asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from db import schema, queries


@pytest_asyncio.fixture(autouse=True)
async def _fresh_db(tmp_path):
    db_path = str(tmp_path / "test_recap.db")
    schema.DB_PATH = db_path
    queries.DB_PATH = db_path
    await schema.init_db()
    yield


async def _fund_user(user: str, amount: int):
    await queries.give_casino_coins(user, amount)


async def _create_and_resolve_market() -> tuple[int, int]:
    """Create a binary market, match orders, resolve it. Returns (market_id, winning_oid)."""
    await _fund_user("alice", 5000)
    await _fund_user("bob", 5000)
    mid = await queries.create_prediction_market("creator", "Will it rain?", ["Yes", "No"])
    market = await queries.get_prediction_market(mid)
    yes_oid = market["outcomes"][0]["outcome_id"]
    no_oid = market["outcomes"][1]["outcome_id"]
    await queries.place_market_order(mid, yes_oid, "alice", "buy", 60, 5)
    await queries.place_market_order(mid, no_oid, "bob", "buy", 40, 5)
    await queries.match_orders(mid)
    await queries.resolve_market(mid, yes_oid, "admin")
    return mid, yes_oid


class TestRecentlyResolvedQuery:
    @pytest.mark.asyncio
    async def test_returns_resolved_markets(self):
        mid, _ = await _create_and_resolve_market()
        results = await queries.get_recently_resolved_markets(hours=24)
        assert len(results) == 1
        assert results[0]["market_id"] == mid
        assert results[0]["status"] == "resolved"
        assert results[0]["winning_label"] == "Yes"

    @pytest.mark.asyncio
    async def test_excludes_open_markets(self):
        await queries.create_prediction_market("u1", "Open market?", ["A", "B"])
        results = await queries.get_recently_resolved_markets(hours=24)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_returns_multiple_resolved(self):
        await _fund_user("alice", 50000)
        await _fund_user("bob", 50000)

        for i in range(3):
            mid = await queries.create_prediction_market("creator", f"Q{i}?", ["Yes", "No"])
            market = await queries.get_prediction_market(mid)
            yes_oid = market["outcomes"][0]["outcome_id"]
            no_oid = market["outcomes"][1]["outcome_id"]
            await queries.place_market_order(mid, yes_oid, "alice", "buy", 60, 1)
            await queries.place_market_order(mid, no_oid, "bob", "buy", 40, 1)
            await queries.match_orders(mid)
            await queries.resolve_market(mid, yes_oid, "admin")

        results = await queries.get_recently_resolved_markets(hours=24)
        assert len(results) == 3


class TestBuildRecapEmbed:
    @pytest.mark.asyncio
    async def test_empty_state(self):
        """No markets at all produces a minimal embed."""
        from bot.cogs.predictions import PredictionsCog
        bot = MagicMock()
        cog = PredictionsCog.__new__(PredictionsCog)
        cog.bot = bot
        cog.recap_channel_id = None
        cog._last_recap_date = None

        embed = await cog._build_recap_embed()
        assert "No open or recently resolved" in embed.description

    @pytest.mark.asyncio
    async def test_with_open_market(self):
        """Open market shows up in the recap."""
        await _fund_user("alice", 5000)
        mid = await queries.create_prediction_market("u1", "Will it snow?", ["Yes", "No"])
        market = await queries.get_prediction_market(mid)
        yes_oid = market["outcomes"][0]["outcome_id"]
        await queries.place_market_order(mid, yes_oid, "alice", "buy", 65, 3)

        from bot.cogs.predictions import PredictionsCog
        bot = MagicMock()
        cog = PredictionsCog.__new__(PredictionsCog)
        cog.bot = bot
        cog.recap_channel_id = None
        cog._last_recap_date = None

        embed = await cog._build_recap_embed()
        # Should have at least the open markets field
        field_names = [f.name for f in embed.fields]
        assert any("Open Markets" in n for n in field_names)
        # Market question should appear in the field value
        open_field = next(f for f in embed.fields if "Open Markets" in f.name)
        assert "Will it snow?" in open_field.value
        assert "65c" in open_field.value  # best bid price

    @pytest.mark.asyncio
    async def test_with_resolved_market(self):
        """Resolved market shows up in the recap."""
        await _create_and_resolve_market()

        from bot.cogs.predictions import PredictionsCog
        bot = MagicMock()
        cog = PredictionsCog.__new__(PredictionsCog)
        cog.bot = bot
        cog.recap_channel_id = None
        cog._last_recap_date = None

        embed = await cog._build_recap_embed()
        field_names = [f.name for f in embed.fields]
        assert any("Resolved" in n for n in field_names)
        resolved_field = next(f for f in embed.fields if "Resolved" in f.name)
        assert "Will it rain?" in resolved_field.value
        assert "Yes" in resolved_field.value  # winning outcome
