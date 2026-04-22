"""Tests for the shared side-pot payout logic."""

from bot.cogs._pool import compute_side_pot_payouts


class TestSingleWinner:
    def test_equal_bets(self):
        bets = {1: 100, 2: 100, 3: 100}
        payouts = compute_side_pot_payouts(bets, [1], house_edge=0.0)
        assert payouts[1] == 300  # wins entire pot
        assert payouts[2] == 0
        assert payouts[3] == 0

    def test_winner_bet_less_gets_partial(self):
        """Winner bet 1c — can only win 1c from each opponent."""
        bets = {1: 1, 2: 500, 3: 500}
        payouts = compute_side_pot_payouts(bets, [1], house_edge=0.0)
        assert payouts[1] == 3  # 1c from each of 3 players
        # Remaining 499 each goes back (side pot, 2 eligible, no winner)
        assert payouts[2] == 499
        assert payouts[3] == 499

    def test_winner_bet_middle(self):
        """Winner bet 50, others bet 10 and 200."""
        bets = {1: 10, 2: 50, 3: 200}
        payouts = compute_side_pot_payouts(bets, [2], house_edge=0.0)
        # Layer 1: 10 * 3 = 30, winner = 2 → 2 gets 30
        # Layer 2: 40 * 2 = 80, winner = 2 → 2 gets 80
        # Layer 3: 150 * 1 = 150, only 3 eligible → refund 150 to 3
        assert payouts[1] == 0
        assert payouts[2] == 30 + 80  # 110
        assert payouts[3] == 150

    def test_winner_bet_most(self):
        """Winner bet more than everyone — wins full pot."""
        bets = {1: 10, 2: 50, 3: 200}
        payouts = compute_side_pot_payouts(bets, [3], house_edge=0.0)
        # Layer 1: 10*3=30 → 3 wins 30
        # Layer 2: 40*2=80 → 3 wins 80
        # Layer 3: 150*1=150 → only 3, refund 150
        assert payouts[1] == 0
        assert payouts[2] == 0
        assert payouts[3] == 30 + 80 + 150  # 260


class TestMultipleWinners:
    def test_two_winners_equal_bets(self):
        bets = {1: 100, 2: 100, 3: 100}
        payouts = compute_side_pot_payouts(bets, [1, 2], house_edge=0.0)
        assert payouts[1] == 150
        assert payouts[2] == 150
        assert payouts[3] == 0

    def test_two_winners_different_bets(self):
        """Low-bettor wins less from the pool than high-bettor."""
        bets = {1: 10, 2: 500, 3: 500}
        payouts = compute_side_pot_payouts(bets, [1, 2], house_edge=0.0)
        # Layer 1: 10*3=30, winners [1,2] → 15 each
        # Layer 2: 490*2=980, winners [2] (1 not eligible) → 980 to 2
        assert payouts[1] == 15
        assert payouts[2] == 15 + 980  # 995
        assert payouts[3] == 0


class TestHouseEdge:
    def test_rake_applied_to_won_pots(self):
        bets = {1: 100, 2: 100}
        payouts = compute_side_pot_payouts(bets, [1], house_edge=0.05)
        # Pot = 200, rake = 10, winner gets 190
        assert payouts[1] == 190
        assert payouts[2] == 0

    def test_no_rake_on_refunded_pots(self):
        """Excess from higher bettor is refunded without rake."""
        bets = {1: 10, 2: 500}
        payouts = compute_side_pot_payouts(bets, [1], house_edge=0.05)
        # Layer 1: 10*2=20, rake=1, winner gets 19
        # Layer 2: 490*1=490, only 2 eligible → refund 490 (no rake)
        assert payouts[1] == 19
        assert payouts[2] == 490

    def test_no_rake_when_zero_edge(self):
        bets = {1: 100, 2: 100}
        payouts = compute_side_pot_payouts(bets, [1], house_edge=0.0)
        assert payouts[1] == 200


class TestEdgeCases:
    def test_empty_bets(self):
        assert compute_side_pot_payouts({}, [1]) == {}

    def test_empty_winners(self):
        bets = {1: 100, 2: 100}
        payouts = compute_side_pot_payouts(bets, [], house_edge=0.0)
        # No winners — everyone refunded
        assert payouts[1] == 100
        assert payouts[2] == 100

    def test_single_player(self):
        """One player only — gets their money back."""
        bets = {1: 100}
        payouts = compute_side_pot_payouts(bets, [1], house_edge=0.05)
        assert payouts[1] == 100  # only 1 eligible, refund, no rake

    def test_all_same_bet_single_winner(self):
        bets = {1: 50, 2: 50, 3: 50, 4: 50}
        payouts = compute_side_pot_payouts(bets, [3], house_edge=0.0)
        assert payouts[3] == 200
        assert payouts[1] == 0
        assert payouts[2] == 0
        assert payouts[4] == 0
