"""Tests for prediction market queries — schema, orders, matching, resolution."""
import os
import tempfile
import pytest
import pytest_asyncio
from db import schema, queries


@pytest_asyncio.fixture(autouse=True)
async def _fresh_db(tmp_path):
    """Create fresh DB for each test."""
    db_path = str(tmp_path / "test_predictions.db")
    schema.DB_PATH = db_path
    queries.DB_PATH = db_path
    await schema.init_db()
    yield


async def _fund_user(user: str, amount: int):
    """Give a user casino coins for testing."""
    await queries.give_casino_coins(user, amount)


async def _create_binary_market(creator: str = "creator") -> tuple[int, int, int]:
    """Create a Yes/No binary market. Returns (market_id, yes_oid, no_oid)."""
    mid = await queries.create_prediction_market(creator, "Will it rain?", ["Yes", "No"])
    market = await queries.get_prediction_market(mid)
    yes_oid = market["outcomes"][0]["outcome_id"]
    no_oid = market["outcomes"][1]["outcome_id"]
    return mid, yes_oid, no_oid


# ── Market creation ──────────────────────────────────────────────────────────


class TestMarketCreation:
    @pytest.mark.asyncio
    async def test_create_and_get(self):
        mid = await queries.create_prediction_market("user1", "Will it rain?", ["Yes", "No"])
        market = await queries.get_prediction_market(mid)
        assert market is not None
        assert market["question"] == "Will it rain?"
        assert market["status"] == "open"
        assert len(market["outcomes"]) == 2
        assert market["outcomes"][0]["label"] == "Yes"
        assert market["outcomes"][1]["label"] == "No"

    @pytest.mark.asyncio
    async def test_create_three_outcomes(self):
        mid = await queries.create_prediction_market("user1", "Who wins?", ["Alice", "Bob", "Carol"])
        market = await queries.get_prediction_market(mid)
        assert len(market["outcomes"]) == 3

    @pytest.mark.asyncio
    async def test_get_nonexistent(self):
        result = await queries.get_prediction_market(9999)
        assert result is None

    @pytest.mark.asyncio
    async def test_list_open_markets(self):
        await queries.create_prediction_market("u1", "Q1?", ["A", "B"])
        await queries.create_prediction_market("u2", "Q2?", ["X", "Y"])
        markets = await queries.list_open_markets()
        assert len(markets) == 2
        # Newest first
        assert markets[0]["question"] == "Q2?"


# ── Order placement ──────────────────────────────────────────────────────────


class TestOrderPlacement:
    @pytest.mark.asyncio
    async def test_place_order_deducts_escrow(self):
        await _fund_user("alice", 5000)
        mid, yes_oid, _ = await _create_binary_market()
        await queries.place_market_order(mid, yes_oid, "alice", "buy", 60, 5)
        bal = await queries.get_casino_balance("alice")
        assert bal == 5000 - 300  # 60 * 5 = 300

    @pytest.mark.asyncio
    async def test_invalid_price_too_low(self):
        await _fund_user("alice", 5000)
        mid, yes_oid, _ = await _create_binary_market()
        with pytest.raises(ValueError, match="Price must be 1-99"):
            await queries.place_market_order(mid, yes_oid, "alice", "buy", 0, 5)

    @pytest.mark.asyncio
    async def test_invalid_price_too_high(self):
        await _fund_user("alice", 5000)
        mid, yes_oid, _ = await _create_binary_market()
        with pytest.raises(ValueError, match="Price must be 1-99"):
            await queries.place_market_order(mid, yes_oid, "alice", "buy", 100, 5)

    @pytest.mark.asyncio
    async def test_insufficient_funds(self):
        await _fund_user("alice", 100)
        mid, yes_oid, _ = await _create_binary_market()
        with pytest.raises(ValueError):
            await queries.place_market_order(mid, yes_oid, "alice", "buy", 60, 5)  # 300 > 100

    @pytest.mark.asyncio
    async def test_order_appears_in_book(self):
        await _fund_user("alice", 5000)
        mid, yes_oid, _ = await _create_binary_market()
        await queries.place_market_order(mid, yes_oid, "alice", "buy", 55, 5)
        book = await queries.get_order_book(mid, yes_oid)
        assert len(book["buys"]) == 1
        assert book["buys"][0]["price"] == 55
        assert book["buys"][0]["quantity"] == 5
        assert len(book["sells"]) == 0

    @pytest.mark.asyncio
    async def test_user_orders(self):
        await _fund_user("alice", 5000)
        mid, yes_oid, no_oid = await _create_binary_market()
        await queries.place_market_order(mid, yes_oid, "alice", "buy", 60, 3)
        await queries.place_market_order(mid, no_oid, "alice", "buy", 30, 2)
        orders = await queries.get_market_orders_for_user(mid, "alice")
        assert len(orders) == 2

    @pytest.mark.asyncio
    async def test_sell_rejected_on_binary_market(self):
        """Binary markets only match buy-vs-buy; sell orders must be rejected."""
        await _fund_user("alice", 5000)
        mid, yes_oid, _ = await _create_binary_market()
        with pytest.raises(ValueError, match="Binary markets only support 'buy' orders"):
            await queries.place_market_order(mid, yes_oid, "alice", "sell", 60, 5)
        assert await queries.get_casino_balance("alice") == 5000

    @pytest.mark.asyncio
    async def test_invalid_side_rejected(self):
        await _fund_user("alice", 5000)
        mid, yes_oid, _ = await _create_binary_market()
        with pytest.raises(ValueError, match="side must be"):
            await queries.place_market_order(mid, yes_oid, "alice", "long", 60, 5)

    @pytest.mark.asyncio
    async def test_order_rejected_on_resolved_market(self):
        """Cannot place orders on a market that is no longer open."""
        await _fund_user("alice", 5000)
        await _fund_user("bob", 5000)
        mid, yes_oid, no_oid = await _create_binary_market()
        await queries.place_market_order(mid, yes_oid, "alice", "buy", 60, 1)
        await queries.place_market_order(mid, no_oid, "bob", "buy", 40, 1)
        await queries.match_orders(mid)
        await queries.resolve_market(mid, yes_oid, "resolver")
        with pytest.raises(ValueError, match="Market is not open"):
            await queries.place_market_order(mid, yes_oid, "alice", "buy", 50, 1)
        # Alice: 5000 - 60 (escrow) + 100 (payout) = 5040
        assert await queries.get_casino_balance("alice") == 5040


# ── Order matching — binary markets ─────────────────────────────────────────


class TestBinaryMatching:
    @pytest.mark.asyncio
    async def test_exact_match_at_100(self):
        """Buy Yes@60 + Buy No@40 = match (60+40=100)."""
        await _fund_user("alice", 5000)
        await _fund_user("bob", 5000)
        mid, yes_oid, no_oid = await _create_binary_market()

        await queries.place_market_order(mid, yes_oid, "alice", "buy", 60, 5)
        await queries.place_market_order(mid, no_oid, "bob", "buy", 40, 5)

        fills = await queries.match_orders(mid)
        assert len(fills) == 1
        _, _, fill_qty, _ = fills[0]
        assert fill_qty == 5

    @pytest.mark.asyncio
    async def test_prices_sum_over_100(self):
        """Prices summing > 100 should still match (house keeps overage)."""
        await _fund_user("alice", 5000)
        await _fund_user("bob", 5000)
        mid, yes_oid, no_oid = await _create_binary_market()

        await queries.place_market_order(mid, yes_oid, "alice", "buy", 70, 3)
        await queries.place_market_order(mid, no_oid, "bob", "buy", 50, 3)

        fills = await queries.match_orders(mid)
        assert len(fills) == 1
        assert fills[0][2] == 3  # fill_qty

    @pytest.mark.asyncio
    async def test_no_match_below_100(self):
        """Prices summing < 100 should NOT match."""
        await _fund_user("alice", 5000)
        await _fund_user("bob", 5000)
        mid, yes_oid, no_oid = await _create_binary_market()

        await queries.place_market_order(mid, yes_oid, "alice", "buy", 40, 5)
        await queries.place_market_order(mid, no_oid, "bob", "buy", 30, 5)

        fills = await queries.match_orders(mid)
        assert len(fills) == 0

    @pytest.mark.asyncio
    async def test_partial_fill(self):
        """Mismatched quantities produce a partial fill."""
        await _fund_user("alice", 5000)
        await _fund_user("bob", 5000)
        mid, yes_oid, no_oid = await _create_binary_market()

        await queries.place_market_order(mid, yes_oid, "alice", "buy", 60, 10)
        await queries.place_market_order(mid, no_oid, "bob", "buy", 40, 3)

        fills = await queries.match_orders(mid)
        assert len(fills) == 1
        assert fills[0][2] == 3  # only 3 matched

        # Alice's order should be partial, Bob's filled
        book = await queries.get_order_book(mid, yes_oid)
        assert len(book["buys"]) == 1
        assert book["buys"][0]["filled_qty"] == 3
        assert book["buys"][0]["status"] == "partial"

    @pytest.mark.asyncio
    async def test_multiple_fills(self):
        """Multiple orders can fill against each other."""
        await _fund_user("alice", 10000)
        await _fund_user("bob", 10000)
        await _fund_user("carol", 10000)
        mid, yes_oid, no_oid = await _create_binary_market()

        await queries.place_market_order(mid, yes_oid, "alice", "buy", 60, 5)
        await queries.place_market_order(mid, yes_oid, "carol", "buy", 55, 3)
        await queries.place_market_order(mid, no_oid, "bob", "buy", 45, 10)  # covers both

        fills = await queries.match_orders(mid)
        assert len(fills) >= 1
        total_filled = sum(f[2] for f in fills)
        assert total_filled == 8  # 5 from alice + 3 from carol


# ── Cancellation ─────────────────────────────────────────────────────────────


class TestCancellation:
    @pytest.mark.asyncio
    async def test_cancel_unfilled_order(self):
        await _fund_user("alice", 5000)
        mid, yes_oid, _ = await _create_binary_market()
        oid = await queries.place_market_order(mid, yes_oid, "alice", "buy", 60, 5)
        refund = await queries.cancel_market_order(oid, "alice")
        assert refund == 300  # 60 * 5
        assert await queries.get_casino_balance("alice") == 5000  # fully refunded

    @pytest.mark.asyncio
    async def test_cancel_partial_order(self):
        await _fund_user("alice", 5000)
        await _fund_user("bob", 5000)
        mid, yes_oid, no_oid = await _create_binary_market()
        oid = await queries.place_market_order(mid, yes_oid, "alice", "buy", 60, 10)
        await queries.place_market_order(mid, no_oid, "bob", "buy", 40, 3)
        await queries.match_orders(mid)
        # 3 filled, 7 unfilled
        refund = await queries.cancel_market_order(oid, "alice")
        assert refund == 420  # 60 * 7

    @pytest.mark.asyncio
    async def test_cancel_wrong_user(self):
        await _fund_user("alice", 5000)
        mid, yes_oid, _ = await _create_binary_market()
        oid = await queries.place_market_order(mid, yes_oid, "alice", "buy", 60, 5)
        with pytest.raises(ValueError, match="Not your order"):
            await queries.cancel_market_order(oid, "bob")

    @pytest.mark.asyncio
    async def test_cancel_already_filled(self):
        await _fund_user("alice", 5000)
        await _fund_user("bob", 5000)
        mid, yes_oid, no_oid = await _create_binary_market()
        oid = await queries.place_market_order(mid, yes_oid, "alice", "buy", 60, 5)
        await queries.place_market_order(mid, no_oid, "bob", "buy", 40, 5)
        await queries.match_orders(mid)
        with pytest.raises(ValueError, match="Cannot cancel"):
            await queries.cancel_market_order(oid, "alice")

    @pytest.mark.asyncio
    async def test_cancel_nonexistent(self):
        with pytest.raises(ValueError, match="Order not found"):
            await queries.cancel_market_order(99999, "alice")


# ── Resolution ───────────────────────────────────────────────────────────────


class TestResolution:
    @pytest.mark.asyncio
    async def test_resolve_binary_market(self):
        """Winner gets 100 coins per share."""
        await _fund_user("alice", 5000)
        await _fund_user("bob", 5000)
        mid, yes_oid, no_oid = await _create_binary_market()

        await queries.place_market_order(mid, yes_oid, "alice", "buy", 60, 5)
        await queries.place_market_order(mid, no_oid, "bob", "buy", 40, 5)
        await queries.match_orders(mid)

        payouts = await queries.resolve_market(mid, yes_oid, "admin")
        assert payouts["alice"] == 500  # 5 * 100
        assert "bob" not in payouts

        market = await queries.get_prediction_market(mid)
        assert market["status"] == "resolved"
        assert market["winning_outcome_id"] == yes_oid

    @pytest.mark.asyncio
    async def test_resolve_with_open_orders_refunded(self):
        """Unfilled orders get escrow refunded on resolution."""
        await _fund_user("alice", 5000)
        mid, yes_oid, no_oid = await _create_binary_market()

        # Place order but nobody matches
        await queries.place_market_order(mid, yes_oid, "alice", "buy", 60, 5)
        # 5000 - 300 = 4700
        assert await queries.get_casino_balance("alice") == 4700

        await queries.resolve_market(mid, yes_oid, "admin")
        # Should get escrow back
        assert await queries.get_casino_balance("alice") == 5000

    @pytest.mark.asyncio
    async def test_resolve_multiple_winners(self):
        """Multiple users with winning shares all get paid."""
        await _fund_user("alice", 10000)
        await _fund_user("bob", 10000)
        await _fund_user("carol", 10000)
        mid, yes_oid, no_oid = await _create_binary_market()

        await queries.place_market_order(mid, yes_oid, "alice", "buy", 60, 5)
        await queries.place_market_order(mid, yes_oid, "carol", "buy", 55, 3)
        # Bob matches both
        await queries.place_market_order(mid, no_oid, "bob", "buy", 45, 8)
        await queries.match_orders(mid)

        payouts = await queries.resolve_market(mid, yes_oid, "admin")
        assert payouts.get("alice", 0) == 500  # 5 * 100
        assert payouts.get("carol", 0) == 300  # 3 * 100

    @pytest.mark.asyncio
    async def test_resolve_rejects_foreign_outcome_id(self):
        """Cannot resolve with an outcome_id from a different market."""
        mid1, _, _ = await _create_binary_market("creator1")
        mid2, yes2, _ = await _create_binary_market("creator2")
        with pytest.raises(ValueError, match="does not belong to market"):
            await queries.resolve_market(mid1, yes2, "admin")
        market = await queries.get_prediction_market(mid1)
        assert market["status"] == "open"

    @pytest.mark.asyncio
    async def test_resolve_rejects_nonexistent_outcome_id(self):
        mid, _, _ = await _create_binary_market()
        with pytest.raises(ValueError, match="does not belong to market"):
            await queries.resolve_market(mid, 999999, "admin")

    @pytest.mark.asyncio
    async def test_resolve_rejects_already_resolved(self):
        """Cannot resolve a market twice."""
        await _fund_user("alice", 5000)
        await _fund_user("bob", 5000)
        mid, yes_oid, no_oid = await _create_binary_market()
        await queries.place_market_order(mid, yes_oid, "alice", "buy", 60, 1)
        await queries.place_market_order(mid, no_oid, "bob", "buy", 40, 1)
        await queries.match_orders(mid)
        await queries.resolve_market(mid, yes_oid, "admin")
        with pytest.raises(ValueError, match="already"):
            await queries.resolve_market(mid, yes_oid, "admin")


# ── Positions ───────────────────────────────────────────────────────────────


class TestPositions:
    @pytest.mark.asyncio
    async def test_positions_after_fill(self):
        await _fund_user("alice", 5000)
        await _fund_user("bob", 5000)
        mid, yes_oid, no_oid = await _create_binary_market()

        await queries.place_market_order(mid, yes_oid, "alice", "buy", 60, 5)
        await queries.place_market_order(mid, no_oid, "bob", "buy", 40, 5)
        await queries.match_orders(mid)

        positions = await queries.get_market_positions(mid)
        assert len(positions) == 2
        alice_pos = next(p for p in positions if p["discord_user"] == "alice")
        assert alice_pos["shares"] == 5
        assert alice_pos["label"] == "Yes"

    @pytest.mark.asyncio
    async def test_no_positions_unfilled(self):
        await _fund_user("alice", 5000)
        mid, yes_oid, _ = await _create_binary_market()
        await queries.place_market_order(mid, yes_oid, "alice", "buy", 60, 5)
        positions = await queries.get_market_positions(mid)
        assert len(positions) == 0


# ── Multi-outcome markets ───────────────────────────────────────────────────


class TestMultiOutcome:
    @pytest.mark.asyncio
    async def test_create_three_outcome(self):
        mid = await queries.create_prediction_market("u1", "Who wins?", ["A", "B", "C"])
        market = await queries.get_prediction_market(mid)
        assert len(market["outcomes"]) == 3

    @pytest.mark.asyncio
    async def test_direct_matching_buy_sell(self):
        """In multi-outcome markets, buy matches sell within the same outcome."""
        await _fund_user("alice", 5000)
        await _fund_user("bob", 5000)
        mid = await queries.create_prediction_market("u1", "Who wins?", ["A", "B", "C"])
        market = await queries.get_prediction_market(mid)
        oid_a = market["outcomes"][0]["outcome_id"]

        await queries.place_market_order(mid, oid_a, "alice", "buy", 60, 5)
        await queries.place_market_order(mid, oid_a, "bob", "sell", 55, 5)

        fills = await queries.match_orders(mid)
        assert len(fills) == 1
        assert fills[0][2] == 5

    @pytest.mark.asyncio
    async def test_no_direct_match_price_gap(self):
        """Buy below sell price should not match."""
        await _fund_user("alice", 5000)
        await _fund_user("bob", 5000)
        mid = await queries.create_prediction_market("u1", "Who wins?", ["A", "B", "C"])
        market = await queries.get_prediction_market(mid)
        oid_a = market["outcomes"][0]["outcome_id"]

        await queries.place_market_order(mid, oid_a, "alice", "buy", 40, 5)
        await queries.place_market_order(mid, oid_a, "bob", "sell", 55, 5)

        fills = await queries.match_orders(mid)
        assert len(fills) == 0


# ── Cancel all open orders ──────────────────────────────────────────────────


class TestCancelAll:
    @pytest.mark.asyncio
    async def test_cancel_all_credits_wallets(self):
        """cancel_all_open_orders must credit unfilled escrow back to each user."""
        await _fund_user("alice", 5000)
        await _fund_user("bob", 5000)
        mid, yes_oid, no_oid = await _create_binary_market()

        await queries.place_market_order(mid, yes_oid, "alice", "buy", 60, 5)
        await queries.place_market_order(mid, no_oid, "bob", "buy", 30, 3)

        await queries.cancel_all_open_orders(mid)

        # Wallets should be fully restored
        assert await queries.get_casino_balance("alice") == 5000
        assert await queries.get_casino_balance("bob") == 5000
