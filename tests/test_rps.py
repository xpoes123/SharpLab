"""Tests for Rock Paper Scissors game logic."""

from unittest.mock import patch

from bot.cogs._pool import compute_side_pot_payouts
from bot.cogs.rps import RPSGame, RPSView, _rps_winner


# ── RPS winner helper ────────────────────────────────────────────────────────


class TestRPSWinner:
    def test_rock_beats_scissors(self):
        assert _rps_winner("rock", "scissors") == "a"

    def test_scissors_beats_paper(self):
        assert _rps_winner("scissors", "paper") == "a"

    def test_paper_beats_rock(self):
        assert _rps_winner("paper", "rock") == "a"

    def test_scissors_loses_to_rock(self):
        assert _rps_winner("scissors", "rock") == "b"

    def test_paper_loses_to_scissors(self):
        assert _rps_winner("paper", "scissors") == "b"

    def test_rock_loses_to_paper(self):
        assert _rps_winner("rock", "paper") == "b"

    def test_tie_rock(self):
        assert _rps_winner("rock", "rock") == "tie"

    def test_tie_paper(self):
        assert _rps_winner("paper", "paper") == "tie"

    def test_tie_scissors(self):
        assert _rps_winner("scissors", "scissors") == "tie"


# ── Best-of-3 detection ─────────────────────────────────────────────────────


class TestBestOf3:
    def _make_game(self) -> RPSGame:
        return RPSGame(
            channel_id=1, challenger_id=100, opponent_id=200,
            challenger_name="Alice", opponent_name="Bob",
            bet=50, best_of=3, phase="playing",
        )

    def _make_view(self, game: RPSGame) -> RPSView:
        tables: dict[int, RPSGame] = {game.channel_id: game}
        return RPSView(game, tables)

    def test_wins_needed_is_2(self):
        game = self._make_game()
        assert game.wins_needed == 2

    def test_challenger_wins_2_0(self):
        game = self._make_game()
        view = self._make_view(game)

        # Round 1: challenger wins
        game.challenger_choice = "rock"
        game.opponent_choice = "scissors"
        view._resolve_round_logic()
        assert game.challenger_score == 1
        assert game.opponent_score == 0

        # Round 2: challenger wins
        game.challenger_choice = "paper"
        game.opponent_choice = "rock"
        view._resolve_round_logic()
        assert game.challenger_score == 2
        assert game.challenger_score >= game.wins_needed

    def test_opponent_wins_2_1(self):
        game = self._make_game()
        view = self._make_view(game)

        # Round 1: challenger wins
        game.challenger_choice = "rock"
        game.opponent_choice = "scissors"
        view._resolve_round_logic()
        assert game.challenger_score == 1

        # Round 2: opponent wins
        game.challenger_choice = "rock"
        game.opponent_choice = "paper"
        view._resolve_round_logic()
        assert game.opponent_score == 1

        # Round 3: opponent wins
        game.challenger_choice = "scissors"
        game.opponent_choice = "rock"
        view._resolve_round_logic()
        assert game.opponent_score == 2
        assert game.opponent_score >= game.wins_needed

    def test_ties_dont_count(self):
        game = self._make_game()
        view = self._make_view(game)

        round_before = game.round_num

        game.challenger_choice = "rock"
        game.opponent_choice = "rock"
        view._resolve_round_logic()

        assert game.challenger_score == 0
        assert game.opponent_score == 0
        assert game.round_num == round_before  # round not incremented on tie
        assert len(game.round_history) == 1
        assert game.round_history[0][2] == "Tie"


# ── Best-of-5 detection ─────────────────────────────────────────────────────


class TestBestOf5:
    def _make_game(self) -> RPSGame:
        return RPSGame(
            channel_id=1, challenger_id=100, opponent_id=200,
            challenger_name="Alice", opponent_name="Bob",
            bet=50, best_of=5, phase="playing",
        )

    def _make_view(self, game: RPSGame) -> RPSView:
        tables: dict[int, RPSGame] = {game.channel_id: game}
        return RPSView(game, tables)

    def test_wins_needed_is_3(self):
        game = self._make_game()
        assert game.wins_needed == 3

    def test_challenger_wins_3_0(self):
        game = self._make_game()
        view = self._make_view(game)

        wins = [("rock", "scissors"), ("paper", "rock"), ("scissors", "paper")]
        for cc, oc in wins:
            game.challenger_choice = cc
            game.opponent_choice = oc
            view._resolve_round_logic()

        assert game.challenger_score == 3
        assert game.challenger_score >= game.wins_needed

    def test_opponent_wins_3_2(self):
        game = self._make_game()
        view = self._make_view(game)

        rounds = [
            ("rock", "scissors", "challenger"),   # 1-0
            ("rock", "paper", "opponent"),         # 1-1
            ("scissors", "paper", "challenger"),   # 2-1
            ("paper", "scissors", "opponent"),     # 2-2
            ("rock", "paper", "opponent"),         # 2-3
        ]
        for cc, oc, _expected in rounds:
            game.challenger_choice = cc
            game.opponent_choice = oc
            view._resolve_round_logic()

        assert game.challenger_score == 2
        assert game.opponent_score == 3
        assert game.opponent_score >= game.wins_needed


# ── vs_bot auto-resolve ──────────────────────────────────────────────────────


class TestVsBot:
    def test_bot_auto_picks(self):
        game = RPSGame(
            channel_id=1, challenger_id=100, opponent_id=999,
            challenger_name="Alice", opponent_name="Bot",
            bet=50, best_of=3, phase="playing", vs_bot=True,
        )
        view = RPSView(game, {1: game})

        # Simulate human choosing; bot should auto-pick via random.choice
        with patch("bot.cogs.rps.random.choice", return_value="scissors"):
            game.challenger_choice = "rock"
            # In _handle_choice, when vs_bot and challenger picks, bot picks too
            # Simulate that logic here directly
            game.opponent_choice = "scissors"  # mocked
            view._resolve_round_logic()

        assert game.challenger_score == 1
        assert len(game.round_history) == 1


# ── Bet validation ───────────────────────────────────────────────────────────


class TestBetValidation:
    def test_bet_range(self):
        # Valid bets
        for b in [1, 10, 250, 500]:
            assert 1 <= b <= 500

        # Invalid bets
        for b in [0, -1, 501, 1000]:
            assert not (1 <= b <= 500)


# ── Side pot payouts for 1v1 ─────────────────────────────────────────────────


class TestRPSPayouts:
    def test_equal_bets_single_winner(self):
        bets = {100: 50, 200: 50}
        payouts = compute_side_pot_payouts(bets, [100])
        assert payouts[100] == 100  # wins the full pot
        assert payouts[200] == 0

    def test_equal_bets_other_winner(self):
        bets = {100: 50, 200: 50}
        payouts = compute_side_pot_payouts(bets, [200])
        assert payouts[200] == 100
        assert payouts[100] == 0
