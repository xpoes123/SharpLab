"""Unit tests for prediction market query layer."""
import os
import tempfile

import pytest
import pytest_asyncio

# Patch DB_PATH before importing anything from db
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
_TEST_DB = _tmp.name

import db.schema as schema
import db.queries as queries

schema.DB_PATH = _TEST_DB
queries.DB_PATH = _TEST_DB


@pytest_asyncio.fixture(autouse=True)
async def _fresh_db():
    """Re-create the DB for every test."""
    # Remove stale file if present
    if os.path.exists(_TEST_DB):
        os.unlink(_TEST_DB)
    await schema.init_db()
    yield
    if os.path.exists(_TEST_DB):
        os.unlink(_TEST_DB)


# ── Helpers ────────────────────────────────────────────────────────────────


async def _fund_user(user: str, amount: int = 10_000) -> None:
    """Give a user casino coins for testing."""
    await queries.give_casino_coins(user, amount)


async def _create_binary_market(creator: str = "creator1") -> tuple[int, int, int]:
    """Create a binary Yes/No market. Returns (market_id, yes_oid, no_oid)."""
    mid = await queries.create_prediction_market(creator, "Will it rain?", ["Yes", "No"])
    m = await queries.get_prediction_market(mid)
    assert m is not None
    yes_oid = m["outcomes"][0]["outcome_id"]
    no_oid = m["outcomes"][1]["outcome_id"]
    return mid, yes_oid, no_oid


# ── Market creation + retrieval ─────────────────────────────────────────────


class TestMarketCreation:
    @pytest.mark.asyncio
    async def test_create_and_get(self):
        mid = await queries.create_prediction_market("user1", "Who wins?", ["TeamA", "TeamB"])
        market = await queries.get_prediction_market(mid)
        assert market is not None
        assert market["question"] == "Who wins?"
        assert market["status"] == "open"
        assert market["creator_id"] == "user1"
        assert len(market["outcomes"]) == 2
        labels = [o["label"] for o in market["outcomes"]]
        assert labels == ["TeamA", "TeamB"]

    @pytest.mark.asyncio
    async def test_create_three_outcomes(self):
        mid = await queries.create_prediction_market("user1", "Who wins MVP?", ["A", "B", "C"])
        market = await queries.get_prediction_market(mid)
        assert market is not None
        assert len(market["outcomes"]) == 3

    @pytest.mark.asyncio
    async def test_get_nonexistent(self):
        result = await queries.get_prediction_market(999)
        assert result is None

    @pytest.mark.asyncio
    async def test_list_open_markets(self):
        await queries.create_prediction_market("u1", "Q1?", ["Yes", "No"])
        await queries.create_prediction_market("u2", "Q2?", ["Yes", "No"])
        markets = await queries.list_open_markets()
        assert len(markets) == 2
        # Newest first
        assert markets[0]["question"] == "Q2?"
        assert markets[1]["question"] == "Q1?"
        # Each has outcomes
        assert len(markets[0]["outcomes"]) == 2


# ── Order placement + escrow ────────────────────────────────────────────────


class TestOrderPlacement:
    @pytest.mark.asyncio
    async def test_place_order_deducts_escrow(self):
        await _fund_user("alice", 5000)
        mid, yes_oid, no_oid = await _create_binary_market()
        oid = await queries.place_market_order(mid, yes_oid, "alice", "buy", 60, 10)
        assert oid is not None
        # Escrow = 60 * 10 = 600 deducted
        bal = await queries.get_casino_balance("alice")
        assert bal == 5000 - 600

    @pytest.mark.asyncio
    async def test_invalid_price_too_low(self):
        await _fund_user("alice", 5000)
        mid, yes_oid, _ = await _create_binary_market()
        with pytest.raises(ValueError, match="Price must be 1-99"):
            await queries.place_market_order(mid, yes_oid, "alice", "buy", 0, 1)

    @pytest.mark.asyncio
    async def test_invalid_price_too_high(self):
        await _fund_user("alice", 5000)
        mid, yes_oid, _ = await _create_binary_market()
        with pytest.raises(ValueError, match="Price must be 1-99"):
            await queries.place_market_order(mid, yes_oid, "alice", "buy", 100, 1)

    @pytest.mark.asyncio
    async def test_insufficient_funds(self):
        await _fund_user("bob", 1050)
        mid, yes_oid, _ = await _create_binary_market()
        with pytest.raises(ValueError, match="Insufficient"):
            await queries.place_market_order(mid, yes_oid, "bob", "buy", 60, 100)

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
        """Binary markets only match buy-vs-buy; sell orders must be rejected to prevent locked escrow."""
        await _fund_user("alice", 5000)
        mid, yes_oid, _ = await _create_binary_market()
        with pytest.raises(ValueError, match="Binary markets only support 'buy' orders"):
            await queries.place_market_order(mid, yes_oid, "alice", "sell", 60, 5)
        # Escrow must NOT have been deducted
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
        # Place matching orders and resolve
        await queries.place_market_order(mid, yes_oid, "alice", "buy", 60, 1)
        await queries.place_market_order(mid, no_oid, "bob", "buy", 40, 1)
        await queries.match_orders(mid)
        await queries.resolve_market(mid, yes_oid, "resolver")
        # Now try to place an order on the resolved market
        with pytest.raises(ValueError, match="Market is not open"):
            await queries.place_market_order(mid, yes_oid, "alice", "buy", 50, 1)
        # Balance should be unchanged (escrow never deducted)
        bal = await queries.get_casino_balance("alice")
        assert bal is not None
        # Alice started with 5000, spent 60 on order, got 100 from winning
        assert bal == 5000 - 60 + 100


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
        order_a, order_b, qty, price = fills[0]
        assert qty == 5

    @pytest.mark.asyncio
    async def test_prices_sum_over_100(self):
        """Buy Yes@65 + Buy No@45 = match (65+45=110 >= 100)."""
        await _fund_user("alice", 5000)
        await _fund_user("bob", 5000)
        mid, yes_oid, no_oid = await _create_binary_market()

        await queries.place_market_order(mid, yes_oid, "alice", "buy", 65, 3)
        await queries.place_market_order(mid, no_oid, "bob", "buy", 45, 3)

        fills = await queries.match_orders(mid)
        assert len(fills) == 1
        assert fills[0][2] == 3  # fill_qty

    @pytest.mark.asyncio
    async def test_no_match_below_100(self):
        """Buy Yes@50 + Buy No@40 = no match (50+40=90 < 100)."""
        await _fund_user("alice", 5000)
        await _fund_user("bob", 5000)
        mid, yes_oid, no_oid = await _create_binary_market()

        await queries.place_market_order(mid, yes_oid, "alice", "buy", 50, 5)
        await queries.place_market_order(mid, no_oid, "bob", "buy", 40, 5)

        fills = await queries.match_orders(mid)
        assert len(fills) == 0

    @pytest.mark.asyncio
    async def test_partial_fill(self):
        """Buy Yes@60 x10 + Buy No@40 x4 = partial fill of 4."""
        await _fund_user("alice", 5000)
        await _fund_user("bob", 5000)
        mid, yes_oid, no_oid = await _create_binary_market()

        oid_a = await queries.place_market_order(mid, yes_oid, "alice", "buy", 60, 10)
        oid_b = await queries.place_market_order(mid, no_oid, "bob", "buy", 40, 4)

        fills = await queries.match_orders(mid)
        assert len(fills) == 1
        assert fills[0][2] == 4  # fill_qty

        # Check order statuses
        book_yes = await queries.get_order_book(mid, yes_oid)
        # Alice's order should be partial (6 remaining)
        assert len(book_yes["buys"]) == 1
        assert book_yes["buys"][0]["filled_qty"] == 4
        assert book_yes["buys"][0]["status"] == "partial"

        # Bob's order should be fully filled (not in open book)
        book_no = await queries.get_order_book(mid, no_oid)
        assert len(book_no["buys"]) == 0  # fully filled, no longer open

    @pytest.mark.asyncio
    async def test_multiple_fills(self):
        """Multiple orders match in sequence."""
        await _fund_user("alice", 10000)
        await _fund_user("bob", 10000)
        await _fund_user("carol", 10000)
        mid, yes_oid, no_oid = await _create_binary_market()

        await queries.place_market_order(mid, yes_oid, "alice", "buy", 60, 5)
        await queries.place_market_order(mid, no_oid, "bob", "buy", 40, 3)
        await queries.place_market_order(mid, no_oid, "carol", "buy", 45, 5)

        fills = await queries.match_orders(mid)
        # carol@45 matches alice@60 (45+60=105>=100), bob@40 matches alice@60 (40+60=100)
        # Order: carol first (higher price desc), then bob
        total_filled = sum(f[2] for f in fills)
        assert total_filled == 5  # alice has 5 shares, all should fill


# ── Cancellation + refund ───────────────────────────────────────────────────


class TestCancellation:
    @pytest.mark.asyncio
    async def test_cancel_unfilled_order(self):
        await _fund_user("alice", 5000)
        mid, yes_oid, _ = await _create_binary_market()
        oid = await queries.place_market_order(mid, yes_oid, "alice", "buy", 50, 10)
        # Balance after escrow: 5000 - 500 = 4500
        refund = await queries.cancel_market_order(oid, "alice")
        assert refund == 500
        bal = await queries.get_casino_balance("alice")
        assert bal == 5000  # fully refunded

    @pytest.mark.asyncio
    async def test_cancel_partial_order(self):
        """Cancel a partially filled order; only unfilled portion refunded."""
        await _fund_user("alice", 5000)
        await _fund_user("bob", 5000)
        mid, yes_oid, no_oid = await _create_binary_market()

        oid_a = await queries.place_market_order(mid, yes_oid, "alice", "buy", 60, 10)
        await queries.place_market_order(mid, no_oid, "bob", "buy", 40, 3)
        await queries.match_orders(mid)

        # Alice's order: 3 filled, 7 unfilled
        refund = await queries.cancel_market_order(oid_a, "alice")
        assert refund == 60 * 7  # 420

    @pytest.mark.asyncio
    async def test_cancel_wrong_user(self):
        await _fund_user("alice", 5000)
        mid, yes_oid, _ = await _create_binary_market()
        oid = await queries.place_market_order(mid, yes_oid, "alice", "buy", 50, 5)
        with pytest.raises(ValueError, match="Not your order"):
            await queries.cancel_market_order(oid, "bob")

    @pytest.mark.asyncio
    async def test_cancel_already_filled(self):
        await _fund_user("alice", 5000)
        await _fund_user("bob", 5000)
        mid, yes_oid, no_oid = await _create_binary_market()

        oid_a = await queries.place_market_order(mid, yes_oid, "alice", "buy", 60, 5)
        await queries.place_market_order(mid, no_oid, "bob", "buy", 40, 5)
        await queries.match_orders(mid)

        with pytest.raises(ValueError, match="Cannot cancel"):
            await queries.cancel_market_order(oid_a, "alice")

    @pytest.mark.asyncio
    async def test_cancel_nonexistent(self):
        with pytest.raises(ValueError, match="Order not found"):
            await queries.cancel_market_order(9999, "alice")


# ── Resolution + payouts ────────────────────────────────────────────────────


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

        # Alice bet 60*5=300 on Yes. Bob bet 40*5=200 on No.
        alice_bal_before = await queries.get_casino_balance("alice")
        bob_bal_before = await queries.get_casino_balance("bob")

        payouts = await queries.resolve_market(mid, yes_oid, "admin")

        assert "alice" in payouts
        assert payouts["alice"] == 500  # 5 shares * 100
        assert "bob" not in payouts  # loser

        alice_bal_after = await queries.get_casino_balance("alice")
        assert alice_bal_after == alice_bal_before + 500

        market = await queries.get_prediction_market(mid)
        assert market["status"] == "resolved"
        assert market["winning_outcome_id"] == yes_oid

    @pytest.mark.asyncio
    async def test_resolve_with_open_orders_refunded(self):
        """Open orders are cancelled and refunded on resolution."""
        await _fund_user("alice", 5000)
        await _fund_user("bob", 5000)
        mid, yes_oid, no_oid = await _create_binary_market()

        # Alice places order but it doesn't match (no counterparty)
        await queries.place_market_order(mid, yes_oid, "alice", "buy", 60, 5)
        # Bob places matched order
        await queries.place_market_order(mid, yes_oid, "bob", "buy", 70, 3)

        alice_bal_before = await queries.get_casino_balance("alice")
        bob_bal_before = await queries.get_casino_balance("bob")

        payouts = await queries.resolve_market(mid, yes_oid, "admin")

        # Both orders were unfilled, so both get refunded and no payouts
        assert payouts == {}
        alice_bal_after = await queries.get_casino_balance("alice")
        bob_bal_after = await queries.get_casino_balance("bob")
        assert alice_bal_after == alice_bal_before + 60 * 5  # refund
        assert bob_bal_after == bob_bal_before + 70 * 3  # refund

    @pytest.mark.asyncio
    async def test_resolve_multiple_winners(self):
        """Multiple users with winning shares all get paid."""
        await _fund_user("alice", 10000)
        await _fund_user("bob", 10000)
        await _fund_user("carol", 10000)
        mid, yes_oid, no_oid = await _create_binary_market()

        # Alice buys 5 Yes@60, Carol buys 3 Yes@55
        await queries.place_market_order(mid, yes_oid, "alice", "buy", 60, 5)
        await queries.place_market_order(mid, yes_oid, "carol", "buy", 55, 3)

        # Bob buys No to match both — needs prices that sum to >=100
        # For Alice@60: need No >= 40. For Carol@55: need No >= 45.
        await queries.place_market_order(mid, no_oid, "bob", "buy", 45, 8)
        await queries.match_orders(mid)

        payouts = await queries.resolve_market(mid, yes_oid, "admin")
        assert payouts.get("alice", 0) == 500  # 5 * 100
        assert payouts.get("carol", 0) == 300  # 3 * 100

    @pytest.mark.asyncio
    async def test_resolve_rejects_foreign_outcome_id(self):
        """resolve_market must reject a winning_outcome_id that belongs to a different market."""
        mid1, yes_oid1, _ = await _create_binary_market("creator1")
        mid2, yes_oid2, _ = await _create_binary_market("creator2")
        with pytest.raises(ValueError, match="does not belong to market"):
            await queries.resolve_market(mid1, yes_oid2, "admin")
        # Market must still be open after the failed resolve
        market = await queries.get_prediction_market(mid1)
        assert market["status"] == "open"

    @pytest.mark.asyncio
    async def test_resolve_rejects_nonexistent_outcome_id(self):
        """resolve_market must reject a winning_outcome_id that doesn't exist at all."""
        mid, _, _ = await _create_binary_market()
        with pytest.raises(ValueError, match="does not belong to market"):
            await queries.resolve_market(mid, 999999, "admin")


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
        user_shares = {p["discord_user"]: p for p in positions}
        assert user_shares["alice"]["shares"] == 5
        assert user_shares["alice"]["label"] == "Yes"
        assert user_shares["bob"]["shares"] == 5
        assert user_shares["bob"]["label"] == "No"

    @pytest.mark.asyncio
    async def test_no_positions_unfilled(self):
        await _fund_user("alice", 5000)
        mid, yes_oid, _ = await _create_binary_market()
        await queries.place_market_order(mid, yes_oid, "alice", "buy", 60, 5)
        # No matching, so no fills
        positions = await queries.get_market_positions(mid)
        assert len(positions) == 0


# ── 3+ outcome markets ─────────────────────────────────────────────────────


class TestMultiOutcome:
    @pytest.mark.asyncio
    async def test_create_three_outcome(self):
        mid = await queries.create_prediction_market(
            "user1", "MVP?", ["Player A", "Player B", "Player C"]
        )
        market = await queries.get_prediction_market(mid)
        assert market is not None
        assert len(market["outcomes"]) == 3

    @pytest.mark.asyncio
    async def test_direct_matching_buy_sell(self):
        """In 3+ outcome markets, buy vs sell within same outcome."""
        await _fund_user("alice", 10000)
        await _fund_user("bob", 10000)
        mid = await queries.create_prediction_market(
            "user1", "MVP?", ["A", "B", "C"]
        )
        market = await queries.get_prediction_market(mid)
        oid_a = market["outcomes"][0]["outcome_id"]

        # Alice buys outcome A at 40
        await queries.place_market_order(mid, oid_a, "alice", "buy", 40, 5)
        # Bob sells outcome A at 35 (willing to sell at 35)
        await queries.place_market_order(mid, oid_a, "bob", "sell", 35, 5)

        fills = await queries.match_orders(mid)
        assert len(fills) == 1
        assert fills[0][2] == 5  # all 5 matched

    @pytest.mark.asyncio
    async def test_no_direct_match_price_gap(self):
        """Buy at 30 vs sell at 40 = no match."""
        await _fund_user("alice", 10000)
        await _fund_user("bob", 10000)
        mid = await queries.create_prediction_market(
            "user1", "MVP?", ["A", "B", "C"]
        )
        market = await queries.get_prediction_market(mid)
        oid_a = market["outcomes"][0]["outcome_id"]

        await queries.place_market_order(mid, oid_a, "alice", "buy", 30, 5)
        await queries.place_market_order(mid, oid_a, "bob", "sell", 40, 5)

        fills = await queries.match_orders(mid)
        assert len(fills) == 0


# ── Cancel all open orders ──────────────────────────────────────────────────


class TestCancelAll:
    @pytest.mark.asyncio
    async def test_cancel_all_credits_wallets(self):
        """cancel_all_open_orders must credit unfilled escrow back to each user's wallet."""
        await _fund_user("alice", 5000)
        await _fund_user("bob", 5000)
        mid, yes_oid, no_oid = await _create_binary_market()

        await queries.place_market_order(mid, yes_oid, "alice", "buy", 60, 5)  # escrow 300
        await queries.place_market_order(mid, no_oid, "bob", "buy", 30, 3)    # escrow 90

        await queries.cancel_all_open_orders(mid)

        # Wallets should be fully restored
        assert await queries.get_casino_balance("alice") == 5000
        assert await queries.get_casino_balance("bob") == 5000
