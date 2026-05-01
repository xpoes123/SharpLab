"""Tests for Crapless Craps game logic: payouts, phase transitions, side bets."""
from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, ".")

from bot.cogs.crapless import (
    BetType,
    CraplessTable,
    CraplessTableView,
    Phase,
    PlayerBets,
    SideBet,
    _pass_odds_win,
    _refund_player,
    _resolve_side_bets_for_player,
    TRUE_ODDS,
    PLACE_PAYOUTS,
    HARDWAY_PAYOUTS,
)


def run(coro):
    return asyncio.run(coro)


# ── _pass_odds_win ──────────────────────────────────────────────────────────


class TestPassOddsWin:
    """Verify true-odds payouts for every crapless point number."""

    def test_point_2_odds(self):
        # 6:1 odds — 100 bet → 600 win
        assert _pass_odds_win(100, 2) == 600

    def test_point_3_odds(self):
        # 3:1 odds — 100 bet → 300 win
        assert _pass_odds_win(100, 3) == 300

    def test_point_4_odds(self):
        # 2:1 odds — 100 bet → 200 win
        assert _pass_odds_win(100, 4) == 200

    def test_point_5_odds(self):
        # 3:2 odds — 100 bet → 150 win
        assert _pass_odds_win(100, 5) == 150

    def test_point_6_odds(self):
        # 6:5 odds — 100 bet → 120 win
        assert _pass_odds_win(100, 6) == 120

    def test_point_8_odds(self):
        # 6:5 odds — 100 bet → 120 win
        assert _pass_odds_win(100, 8) == 120

    def test_point_9_odds(self):
        # 3:2 odds — 100 bet → 150 win
        assert _pass_odds_win(100, 9) == 150

    def test_point_10_odds(self):
        # 2:1 odds — 100 bet → 200 win
        assert _pass_odds_win(100, 10) == 200

    def test_point_11_odds(self):
        # 3:1 odds — crapless extension
        assert _pass_odds_win(100, 11) == 300

    def test_point_12_odds(self):
        # 6:1 odds — crapless extension
        assert _pass_odds_win(100, 12) == 600

    def test_zero_odds_bet(self):
        assert _pass_odds_win(0, 6) == 0

    def test_integer_division_truncates(self):
        # 3 * 6 // 5 = 3 (not 3.6)
        assert _pass_odds_win(3, 6) == 3


# ── _resolve_come_out ────────────────────────────────────────────────────────


class TestResolveComeOut:
    """Crapless come-out: only 7 is a natural; everything else sets a point."""

    def _make_view_with_player(self, bet: int = 100) -> tuple[CraplessTableView, PlayerBets]:
        table = CraplessTable(channel_id=1, shooter_id=42, shooter_name="Alice")
        active_tables: dict = {1: table}
        view = CraplessTableView(table, active_tables)
        player = PlayerBets(
            user_id=42, display_name="Alice",
            bet_type=BetType.PASS_LINE, bet=bet, coins_in=bet,
        )
        table.players[42] = player
        return view, player

    def test_natural_7_finishes_game(self):
        view, player = self._make_view_with_player()
        finished = view._resolve_come_out(7)
        assert finished is True
        assert view.table.phase == Phase.FINISHED
        assert view.table.outcome == "Natural 7!"

    def test_natural_7_pays_pass_line_even_money(self):
        view, player = self._make_view_with_player(bet=100)
        view._resolve_come_out(7)
        assert player.payout == 200  # bet returned + 1:1 win

    def test_roll_2_sets_point(self):
        view, _ = self._make_view_with_player()
        finished = view._resolve_come_out(2)
        assert finished is False
        assert view.table.phase == Phase.POINT
        assert view.table.point == 2

    def test_roll_3_sets_point(self):
        view, _ = self._make_view_with_player()
        view._resolve_come_out(3)
        assert view.table.point == 3

    def test_roll_11_sets_point(self):
        """Crapless: 11 is NOT a natural — it sets a point."""
        view, player = self._make_view_with_player()
        finished = view._resolve_come_out(11)
        assert finished is False
        assert view.table.point == 11
        assert player.payout == 0  # no payout yet

    def test_roll_12_sets_point(self):
        """Crapless: 12 is NOT craps — it sets a point."""
        view, _ = self._make_view_with_player()
        finished = view._resolve_come_out(12)
        assert finished is False
        assert view.table.point == 12

    def test_all_non_seven_totals_set_point(self):
        for total in range(2, 13):
            if total == 7:
                continue
            view, _ = self._make_view_with_player()
            finished = view._resolve_come_out(total)
            assert finished is False, f"total={total} should set point, not finish"
            assert view.table.point == total

    def test_no_payout_when_point_set(self):
        view, player = self._make_view_with_player()
        view._resolve_come_out(6)
        assert player.payout == 0

    def test_side_bet_only_player_not_paid_on_natural(self):
        table = CraplessTable(channel_id=1, shooter_id=42, shooter_name="Alice")
        view = CraplessTableView(table, {1: table})
        side_player = PlayerBets(user_id=99, display_name="Bob")  # bet_type=None
        table.players[99] = side_player
        view._resolve_come_out(7)
        assert side_player.payout == 0


# ── _resolve_point ───────────────────────────────────────────────────────────


class TestResolvePoint:
    """Point phase: point hit wins, 7 loses, other rolls continue."""

    def _make_view_at_point(self, point: int, bet: int = 100, odds: int = 0):
        table = CraplessTable(channel_id=1, shooter_id=42, shooter_name="Alice")
        table.phase = Phase.POINT
        table.point = point
        active_tables: dict = {1: table}
        view = CraplessTableView(table, active_tables)
        player = PlayerBets(
            user_id=42, display_name="Alice",
            bet_type=BetType.PASS_LINE, bet=bet, odds_bet=odds,
            coins_in=bet + odds,
        )
        table.players[42] = player
        return view, player

    def test_hitting_point_finishes_game(self):
        view, _ = self._make_view_at_point(6)
        finished = view._resolve_point(6)
        assert finished is True
        assert view.table.phase == Phase.FINISHED

    def test_hitting_point_pays_even_money(self):
        view, player = self._make_view_at_point(point=6, bet=100, odds=0)
        view._resolve_point(6)
        assert player.payout == 200  # 100 * 2

    def test_hitting_point_includes_odds_payout(self):
        # Point 6: 6:5 odds. Bet 100 + odds 50 → odds win = 50*6//5 = 60
        view, player = self._make_view_at_point(point=6, bet=100, odds=50)
        view._resolve_point(6)
        assert player.payout == 100 * 2 + 50 + 60  # 310

    def test_hitting_point_11_includes_odds(self):
        # Point 11: 3:1 odds. Bet 100 + odds 90 → odds win = 90*3//1 = 270
        view, player = self._make_view_at_point(point=11, bet=100, odds=90)
        view._resolve_point(11)
        assert player.payout == 100 * 2 + 90 + 270  # 660

    def test_hitting_point_12_includes_odds(self):
        # Point 12: 6:1 odds. Bet 50 + odds 30 → odds win = 30*6//1 = 180
        view, player = self._make_view_at_point(point=12, bet=50, odds=30)
        view._resolve_point(12)
        assert player.payout == 50 * 2 + 30 + 180  # 310

    def test_seven_out_finishes_game(self):
        view, _ = self._make_view_at_point(6)
        finished = view._resolve_point(7)
        assert finished is True
        assert view.table.outcome == "Seven-out!"

    def test_seven_out_pass_line_loses(self):
        view, player = self._make_view_at_point(6, bet=100)
        view._resolve_point(7)
        assert player.payout == 0

    def test_other_roll_continues(self):
        view, _ = self._make_view_at_point(6)
        finished = view._resolve_point(5)
        assert finished is False
        assert view.table.phase == Phase.POINT

    def test_other_roll_no_payout(self):
        view, player = self._make_view_at_point(6)
        view._resolve_point(5)
        assert player.payout == 0

    def test_point_2_hits(self):
        view, player = self._make_view_at_point(2, bet=50)
        finished = view._resolve_point(2)
        assert finished is True
        assert player.payout == 100  # 50 * 2

    def test_all_crapless_points_resolve_correctly(self):
        for point in (2, 3, 4, 5, 6, 8, 9, 10, 11, 12):
            view, player = self._make_view_at_point(point, bet=100)
            finished = view._resolve_point(point)
            assert finished is True, f"point={point} should finish game"
            assert player.payout == 200, f"point={point} should pay even money"


# ── _resolve_side_bets_for_player ────────────────────────────────────────────


class TestResolveSideBetsField:
    """Field bet: pays 2:1 on 2/12, 1:1 on 3/4/9/10/11, loses on 5/6/7/8."""

    def _player(self, amount: int = 20) -> PlayerBets:
        p = PlayerBets(user_id=1, display_name="Alice")
        p.side_bets.append(SideBet(kind="field", amount=amount))
        p.coins_in = amount
        return p

    def test_field_wins_double_on_2(self):
        p = self._player(20)
        credit = _resolve_side_bets_for_player(p, 1, 1)  # total=2
        assert credit == 60  # 20 + 20*2=40+20=60 → net +40

    def test_field_wins_double_on_12(self):
        p = self._player(20)
        credit = _resolve_side_bets_for_player(p, 6, 6)  # total=12
        assert credit == 60

    def test_field_wins_even_money_on_3(self):
        p = self._player(10)
        credit = _resolve_side_bets_for_player(p, 1, 2)  # total=3
        assert credit == 20  # net +10

    def test_field_wins_even_money_on_11(self):
        p = self._player(10)
        credit = _resolve_side_bets_for_player(p, 5, 6)  # total=11
        assert credit == 20

    def test_field_loses_on_5(self):
        p = self._player(20)
        credit = _resolve_side_bets_for_player(p, 2, 3)  # total=5
        assert credit == 0

    def test_field_loses_on_6(self):
        p = self._player(20)
        credit = _resolve_side_bets_for_player(p, 3, 3)  # total=6
        assert credit == 0

    def test_field_loses_on_7(self):
        p = self._player(20)
        credit = _resolve_side_bets_for_player(p, 3, 4)  # total=7
        assert credit == 0

    def test_field_loses_on_8(self):
        p = self._player(20)
        credit = _resolve_side_bets_for_player(p, 4, 4)  # total=8
        assert credit == 0

    def test_field_is_one_roll_bet(self):
        """Field bet is consumed each roll regardless of outcome."""
        p = self._player(20)
        _resolve_side_bets_for_player(p, 3, 4)  # total=7, field loses
        assert len(p.side_bets) == 0

    def test_field_win_updates_coins_out(self):
        p = self._player(10)
        _resolve_side_bets_for_player(p, 1, 2)  # total=3, field wins
        assert p.coins_out == 20


class TestResolveSideBetsAny7:
    """Any 7: pays 4:1, one-roll bet."""

    def _player(self, amount: int = 10) -> PlayerBets:
        p = PlayerBets(user_id=1, display_name="Alice")
        p.side_bets.append(SideBet(kind="any7", amount=amount))
        p.coins_in = amount
        return p

    def test_any7_wins_on_7(self):
        p = self._player(10)
        credit = _resolve_side_bets_for_player(p, 3, 4)  # total=7
        assert credit == 50  # 10 + 10*4 = 50, net +40

    def test_any7_loses_on_non_7(self):
        for d1, d2 in [(1, 1), (1, 2), (3, 3), (5, 6)]:
            p = self._player(10)
            credit = _resolve_side_bets_for_player(p, d1, d2)
            assert credit == 0, f"any7 should lose on {d1+d2}"

    def test_any7_is_one_roll(self):
        p = self._player(10)
        _resolve_side_bets_for_player(p, 3, 4)
        assert len(p.side_bets) == 0


class TestResolveSideBetsAnyCraps:
    """Any Craps: pays 7:1 on 2/3/12."""

    def _player(self, amount: int = 10) -> PlayerBets:
        p = PlayerBets(user_id=1, display_name="Alice")
        p.side_bets.append(SideBet(kind="any_craps", amount=amount))
        p.coins_in = amount
        return p

    def test_any_craps_wins_on_2(self):
        p = self._player(10)
        credit = _resolve_side_bets_for_player(p, 1, 1)
        assert credit == 80  # 10 + 10*7 = 80, net +70

    def test_any_craps_wins_on_3(self):
        p = self._player(10)
        credit = _resolve_side_bets_for_player(p, 1, 2)
        assert credit == 80

    def test_any_craps_wins_on_12(self):
        p = self._player(10)
        credit = _resolve_side_bets_for_player(p, 6, 6)
        assert credit == 80

    def test_any_craps_loses_on_7(self):
        p = self._player(10)
        credit = _resolve_side_bets_for_player(p, 3, 4)
        assert credit == 0

    def test_any_craps_loses_on_other(self):
        p = self._player(10)
        credit = _resolve_side_bets_for_player(p, 2, 4)  # 6
        assert credit == 0


class TestResolveSideBetsYo:
    """Yo (11): pays 15:1."""

    def _player(self, amount: int = 10) -> PlayerBets:
        p = PlayerBets(user_id=1, display_name="Alice")
        p.side_bets.append(SideBet(kind="yo", amount=amount))
        p.coins_in = amount
        return p

    def test_yo_wins_on_11(self):
        p = self._player(10)
        credit = _resolve_side_bets_for_player(p, 5, 6)
        assert credit == 160  # 10 + 10*15 = 160, net +150

    def test_yo_loses_on_non_11(self):
        for d1, d2 in [(1, 1), (3, 4), (6, 6)]:
            p = self._player(10)
            credit = _resolve_side_bets_for_player(p, d1, d2)
            assert credit == 0, f"yo should lose on {d1+d2}"


class TestResolveSideBetsCome:
    """Come bet: crapless rules — only 7 wins immediately; all else sets come point."""

    def _player(self, amount: int = 50) -> PlayerBets:
        p = PlayerBets(user_id=1, display_name="Alice")
        p.side_bets.append(SideBet(kind="come", amount=amount, come_point=0))
        p.coins_in = amount
        return p

    def test_come_wins_on_initial_7(self):
        p = self._player(50)
        credit = _resolve_side_bets_for_player(p, 3, 4)  # total=7
        assert credit == 100  # even money, net +50

    def test_come_sets_point_on_2(self):
        """Crapless: 2 is NOT craps for come bets — it establishes a come point."""
        p = self._player(50)
        credit = _resolve_side_bets_for_player(p, 1, 1)  # total=2
        assert credit == 0
        assert len(p.side_bets) == 1
        assert p.side_bets[0].come_point == 2

    def test_come_sets_point_on_11(self):
        """Crapless: 11 is NOT a natural for come bets — it establishes a come point."""
        p = self._player(50)
        credit = _resolve_side_bets_for_player(p, 5, 6)  # total=11
        assert credit == 0
        assert p.side_bets[0].come_point == 11

    def test_come_sets_point_on_12(self):
        """Crapless: 12 establishes a come point."""
        p = self._player(50)
        credit = _resolve_side_bets_for_player(p, 6, 6)  # total=12
        assert credit == 0
        assert p.side_bets[0].come_point == 12

    def test_come_sets_point_on_6(self):
        p = self._player(50)
        _resolve_side_bets_for_player(p, 3, 3)  # total=6
        assert p.side_bets[0].come_point == 6

    def test_come_point_wins_when_hit(self):
        p = PlayerBets(user_id=1, display_name="Alice")
        p.side_bets.append(SideBet(kind="come", amount=50, come_point=6))
        p.coins_in = 50
        credit = _resolve_side_bets_for_player(p, 3, 3)  # total=6
        assert credit == 100  # even money
        assert len(p.side_bets) == 0  # consumed

    def test_come_point_loses_on_seven_out(self):
        p = PlayerBets(user_id=1, display_name="Alice")
        p.side_bets.append(SideBet(kind="come", amount=50, come_point=6))
        p.coins_in = 50
        credit = _resolve_side_bets_for_player(p, 3, 4)  # total=7
        assert credit == 0
        assert len(p.side_bets) == 0  # consumed (lost)

    def test_come_point_survives_other_roll(self):
        p = PlayerBets(user_id=1, display_name="Alice")
        p.side_bets.append(SideBet(kind="come", amount=50, come_point=6))
        p.coins_in = 50
        credit = _resolve_side_bets_for_player(p, 2, 3)  # total=5 — neither 6 nor 7
        assert credit == 0
        assert len(p.side_bets) == 1  # still active

    def test_unpointed_come_wins_on_seven_out(self):
        """When shooter sevens out, a come bet without a point should WIN (standard craps rule)."""
        p = self._player(50)
        credit = _resolve_side_bets_for_player(p, 3, 4)  # total=7
        assert credit == 100


class TestResolveSideBetsPlace:
    """Place bets: extended to all 10 numbers in crapless craps."""

    def _player(self, num: int, amount: int) -> PlayerBets:
        p = PlayerBets(user_id=1, display_name="Alice")
        p.side_bets.append(SideBet(kind=f"place_{num}", amount=amount))
        p.coins_in = amount
        return p

    def test_place_6_wins(self):
        # 7:6 payout — bet 12: win = 12*7//6 = 14, credit = 26
        p = self._player(6, 12)
        credit = _resolve_side_bets_for_player(p, 3, 3)  # total=6
        assert credit == 26

    def test_place_8_wins(self):
        # 7:6 — bet 12: win=14, credit=26
        p = self._player(8, 12)
        credit = _resolve_side_bets_for_player(p, 4, 4)  # total=8
        assert credit == 26

    def test_place_5_wins(self):
        # 7:5 — bet 10: win=14, credit=24
        p = self._player(5, 10)
        credit = _resolve_side_bets_for_player(p, 2, 3)  # total=5
        assert credit == 24

    def test_place_4_wins(self):
        # 9:5 — bet 10: win=18, credit=28
        p = self._player(4, 10)
        credit = _resolve_side_bets_for_player(p, 2, 2)  # total=4
        assert credit == 28

    def test_place_2_wins(self):
        # 11:2 — bet 20: win=110, credit=130
        p = self._player(2, 20)
        credit = _resolve_side_bets_for_player(p, 1, 1)  # total=2
        assert credit == 130

    def test_place_3_wins(self):
        # 11:4 — bet 40: win=110, credit=150
        p = self._player(3, 40)
        credit = _resolve_side_bets_for_player(p, 1, 2)  # total=3
        assert credit == 150

    def test_place_11_wins(self):
        # 11:4 — bet 40: win=110, credit=150
        p = self._player(11, 40)
        credit = _resolve_side_bets_for_player(p, 5, 6)  # total=11
        assert credit == 150

    def test_place_12_wins(self):
        # 11:2 — bet 20: win=110, credit=130
        p = self._player(12, 20)
        credit = _resolve_side_bets_for_player(p, 6, 6)  # total=12
        assert credit == 130

    def test_place_loses_on_seven(self):
        p = self._player(6, 10)
        credit = _resolve_side_bets_for_player(p, 3, 4)  # total=7
        assert credit == 0
        assert len(p.side_bets) == 0  # consumed

    def test_place_survives_other_roll(self):
        p = self._player(6, 10)
        credit = _resolve_side_bets_for_player(p, 2, 3)  # total=5 — not 6 or 7
        assert credit == 0
        assert len(p.side_bets) == 1  # still active


class TestResolveSideBetsHardway:
    """Hardway bets: only 4, 6, 8, 10. Pays on doubles, loses on easy way or 7."""

    def _player(self, num: int, amount: int = 10) -> PlayerBets:
        p = PlayerBets(user_id=1, display_name="Alice")
        p.side_bets.append(SideBet(kind=f"hard_{num}", amount=amount))
        p.coins_in = amount
        return p

    def test_hard_4_wins_on_double_2(self):
        # 7:1 — bet 10: win=70, credit=80
        p = self._player(4, 10)
        credit = _resolve_side_bets_for_player(p, 2, 2)  # hard 4
        assert credit == 80

    def test_hard_6_wins_on_double_3(self):
        # 9:1 — bet 10: win=90, credit=100
        p = self._player(6, 10)
        credit = _resolve_side_bets_for_player(p, 3, 3)  # hard 6
        assert credit == 100

    def test_hard_8_wins_on_double_4(self):
        # 9:1
        p = self._player(8, 10)
        credit = _resolve_side_bets_for_player(p, 4, 4)  # hard 8
        assert credit == 100

    def test_hard_10_wins_on_double_5(self):
        # 7:1
        p = self._player(10, 10)
        credit = _resolve_side_bets_for_player(p, 5, 5)  # hard 10
        assert credit == 80

    def test_hard_6_loses_on_easy_6(self):
        # Rolled easy way (not doubles)
        p = self._player(6, 10)
        credit = _resolve_side_bets_for_player(p, 2, 4)  # total=6 but 2+4
        assert credit == 0
        assert len(p.side_bets) == 0

    def test_hard_6_loses_on_7(self):
        p = self._player(6, 10)
        credit = _resolve_side_bets_for_player(p, 3, 4)  # total=7
        assert credit == 0
        assert len(p.side_bets) == 0

    def test_hard_6_survives_irrelevant_roll(self):
        p = self._player(6, 10)
        credit = _resolve_side_bets_for_player(p, 2, 3)  # total=5
        assert credit == 0
        assert len(p.side_bets) == 1  # still active


# ── _refund_player ───────────────────────────────────────────────────────────


class TestRefundPlayer:
    def test_refund_single_side_bet(self):
        p = PlayerBets(user_id=1, display_name="Alice")
        p.side_bets.append(SideBet(kind="place_6", amount=60))
        refund = _refund_player(p)
        assert refund == 60
        assert len(p.side_bets) == 0

    def test_refund_multiple_side_bets(self):
        p = PlayerBets(user_id=1, display_name="Alice")
        p.side_bets.append(SideBet(kind="place_6", amount=60))
        p.side_bets.append(SideBet(kind="come", amount=30, come_point=4))
        p.side_bets.append(SideBet(kind="hard_4", amount=10))
        refund = _refund_player(p)
        assert refund == 100
        assert len(p.side_bets) == 0

    def test_refund_empty_side_bets(self):
        p = PlayerBets(user_id=1, display_name="Alice")
        refund = _refund_player(p)
        assert refund == 0

    def test_refund_logs_come_point(self):
        p = PlayerBets(user_id=1, display_name="Alice")
        p.side_bets.append(SideBet(kind="come", amount=50, come_point=8))
        _refund_player(p)
        assert any("8" in entry for entry in p.side_log)


# ── _finish: settlement and cleanup ─────────────────────────────────────────


class TestFinish:
    """_finish must clean up active_tables and credit all payouts correctly."""

    def _make_table_at_finish(self) -> tuple[CraplessTableView, dict, PlayerBets]:
        table = CraplessTable(channel_id=7, shooter_id=99, shooter_name="Bob")
        table.phase = Phase.FINISHED
        table.outcome = "Point 6!"
        player = PlayerBets(
            user_id=99, display_name="Bob",
            bet_type=BetType.PASS_LINE, bet=50, coins_in=50, payout=100,
        )
        table.players[99] = player
        active_tables: dict = {7: table}
        view = CraplessTableView(table, active_tables)
        return view, active_tables, player

    def test_active_table_removed(self):
        view, active_tables, _ = self._make_table_at_finish()
        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.edit_message = AsyncMock()
        interaction.edit_original_response = AsyncMock()

        async def _run():
            with patch("bot.cogs.crapless.queries") as mock_q:
                mock_q.update_casino_balance = AsyncMock(return_value=1000)
                mock_q.get_casino_balance = AsyncMock(return_value=1000)
                mock_q.log_casino_result = AsyncMock()
                await view._finish(interaction, followup=False)

        run(_run())
        assert 7 not in active_tables

    def test_player_credited_with_payout(self):
        view, _, player = self._make_table_at_finish()
        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.edit_message = AsyncMock()

        credited = []

        async def _run():
            with patch("bot.cogs.crapless.queries") as mock_q:
                mock_q.update_casino_balance = AsyncMock(side_effect=lambda uid, amt: credited.append(amt) or 1000)
                mock_q.log_casino_result = AsyncMock()
                await view._finish(interaction, followup=False)

        run(_run())
        assert 100 in credited  # payout = 100

    def test_active_table_removed_even_if_db_fails(self):
        view, active_tables, _ = self._make_table_at_finish()
        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.edit_message = AsyncMock()

        async def _run():
            with patch("bot.cogs.crapless.queries") as mock_q:
                mock_q.update_casino_balance = AsyncMock(side_effect=RuntimeError("DB down"))
                mock_q.get_casino_balance = AsyncMock(return_value=0)
                mock_q.log_casino_result = AsyncMock()
                await view._finish(interaction, followup=False)

        run(_run())
        assert 7 not in active_tables

    def test_unresolved_side_bets_refunded(self):
        table = CraplessTable(channel_id=7, shooter_id=99, shooter_name="Bob")
        table.phase = Phase.FINISHED
        player = PlayerBets(
            user_id=99, display_name="Bob",
            bet_type=BetType.PASS_LINE, bet=50, coins_in=110, payout=100,
        )
        # Unresolved place bet
        player.side_bets.append(SideBet(kind="place_8", amount=60))
        table.players[99] = player
        active_tables: dict = {7: table}
        view = CraplessTableView(table, active_tables)

        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.edit_message = AsyncMock()

        credited = []

        async def _run():
            with patch("bot.cogs.crapless.queries") as mock_q:
                mock_q.update_casino_balance = AsyncMock(side_effect=lambda uid, amt: credited.append(amt) or 1000)
                mock_q.log_casino_result = AsyncMock()
                await view._finish(interaction, followup=False)

        run(_run())
        # Should credit payout (100) + refund (60) = 160
        assert 160 in credited


# ── on_timeout ───────────────────────────────────────────────────────────────


class TestOnTimeout:
    def _make_table(self) -> CraplessTable:
        return CraplessTable(channel_id=1, shooter_id=42, shooter_name="Alice")

    def test_phase_set_to_finished(self):
        from bot.cogs.crapless import CraplessTableView
        table = self._make_table()
        active_tables: dict = {1: table}
        view = CraplessTableView(table, active_tables)

        async def _run():
            with patch("bot.cogs.crapless.queries") as mock_q:
                mock_q.update_casino_balance = AsyncMock()
                table.message = None
                await view.on_timeout()

        run(_run())
        assert table.phase == Phase.FINISHED

    def test_table_removed_on_timeout(self):
        table = self._make_table()
        active_tables: dict = {1: table}
        view = CraplessTableView(table, active_tables)

        async def _run():
            with patch("bot.cogs.crapless.queries") as mock_q:
                mock_q.update_casino_balance = AsyncMock()
                table.message = None
                await view.on_timeout()

        run(_run())
        assert 1 not in active_tables

    def test_noop_if_already_finished(self):
        table = self._make_table()
        table.phase = Phase.FINISHED
        active_tables: dict = {}
        view = CraplessTableView(table, active_tables)

        called = []

        async def _run():
            with patch("bot.cogs.crapless.queries") as mock_q:
                mock_q.update_casino_balance = AsyncMock(side_effect=lambda *a, **kw: called.append(1))
                await view.on_timeout()

        run(_run())
        assert not called

    def test_players_refunded_on_timeout(self):
        table = self._make_table()
        table.phase = Phase.POINT
        table.point = 6
        player = PlayerBets(user_id=42, display_name="Alice",
                            bet_type=BetType.PASS_LINE, bet=100, odds_bet=50,
                            coins_in=150)
        player.side_bets.append(SideBet(kind="place_6", amount=60))
        table.players[42] = player
        active_tables: dict = {1: table}
        view = CraplessTableView(table, active_tables)

        credited = []

        async def _run():
            with patch("bot.cogs.crapless.queries") as mock_q:
                mock_q.update_casino_balance = AsyncMock(side_effect=lambda uid, amt: credited.append(amt) or 0)
                table.message = None
                await view.on_timeout()

        run(_run())
        # Should refund bet + odds + place = 100 + 50 + 60 = 210
        assert 210 in credited


# ── Game flow integration ────────────────────────────────────────────────────


class TestGameFlow:
    """End-to-end game logic without Discord interactions."""

    def test_natural_7_credits_pass_line(self):
        """Come-out 7: pass line players should win even money."""
        table = CraplessTable(channel_id=1, shooter_id=1, shooter_name="Alice")
        view = CraplessTableView(table, {1: table})
        player = PlayerBets(user_id=1, display_name="Alice",
                            bet_type=BetType.PASS_LINE, bet=100, coins_in=100)
        table.players[1] = player

        # Simulate no side bets, resolve come-out 7
        _resolve_side_bets_for_player(player, 3, 4)  # total=7, no side bets
        finished = view._resolve_come_out(7)

        assert finished is True
        assert player.payout == 200

    def test_point_established_then_hit(self):
        table = CraplessTable(channel_id=1, shooter_id=1, shooter_name="Alice")
        view = CraplessTableView(table, {1: table})
        player = PlayerBets(user_id=1, display_name="Alice",
                            bet_type=BetType.PASS_LINE, bet=100, coins_in=100)
        table.players[1] = player

        # Come-out: set point to 8
        finished = view._resolve_come_out(8)
        assert finished is False
        assert table.point == 8

        # Point phase: hit the point
        finished = view._resolve_point(8)
        assert finished is True
        assert player.payout == 200

    def test_point_then_seven_out(self):
        table = CraplessTable(channel_id=1, shooter_id=1, shooter_name="Alice")
        view = CraplessTableView(table, {1: table})
        player = PlayerBets(user_id=1, display_name="Alice",
                            bet_type=BetType.PASS_LINE, bet=100, coins_in=100)
        table.players[1] = player

        view._resolve_come_out(6)
        finished = view._resolve_point(7)

        assert finished is True
        assert player.payout == 0

    def test_crapless_point_2_cycle(self):
        """Point 2 should work end-to-end including odds payout."""
        table = CraplessTable(channel_id=1, shooter_id=1, shooter_name="Alice")
        view = CraplessTableView(table, {1: table})
        player = PlayerBets(user_id=1, display_name="Alice",
                            bet_type=BetType.PASS_LINE, bet=50, odds_bet=10, coins_in=60)
        table.players[1] = player

        view._resolve_come_out(2)
        assert table.point == 2

        view._resolve_point(2)
        # payout = 50*2 + 10 + 10*6//1 = 100+10+60 = 170
        assert player.payout == 170

    def test_crapless_point_11_cycle(self):
        """Point 11: 3:1 odds."""
        table = CraplessTable(channel_id=1, shooter_id=1, shooter_name="Alice")
        view = CraplessTableView(table, {1: table})
        player = PlayerBets(user_id=1, display_name="Alice",
                            bet_type=BetType.PASS_LINE, bet=50, odds_bet=30, coins_in=80)
        table.players[1] = player

        view._resolve_come_out(11)
        assert table.point == 11

        view._resolve_point(11)
        # payout = 50*2 + 30 + 30*3//1 = 100+30+90 = 220
        assert player.payout == 220

    def test_side_only_player_gets_refund_on_finish(self):
        """Side-bet-only player with unresolved place bet should be refunded."""
        table = CraplessTable(channel_id=1, shooter_id=1, shooter_name="Alice")
        table.phase = Phase.FINISHED
        view = CraplessTableView(table, {1: table})
        side_player = PlayerBets(user_id=2, display_name="Bob")
        side_player.side_bets.append(SideBet(kind="place_4", amount=10))
        side_player.coins_in = 10
        table.players[2] = side_player

        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.edit_message = AsyncMock()

        credited = []

        async def _run():
            with patch("bot.cogs.crapless.queries") as mock_q:
                mock_q.update_casino_balance = AsyncMock(side_effect=lambda uid, amt: credited.append((uid, amt)) or 0)
                mock_q.get_casino_balance = AsyncMock(return_value=0)
                mock_q.log_casino_result = AsyncMock()
                await view._finish(interaction, followup=False)

        run(_run())
        assert ("2", 10) in credited  # refund of place bet


# ── Close command ────────────────────────────────────────────────────────────


class TestCraplessClose:
    def test_close_removes_table(self):
        from bot.cogs.crapless import CraplessCrapsCog

        table = CraplessTable(channel_id=5, shooter_id=10, shooter_name="Charlie")
        cog = CraplessCrapsCog.__new__(CraplessCrapsCog)
        cog.active_tables = {5: table}

        interaction = MagicMock()
        interaction.channel_id = 5
        interaction.user = MagicMock()
        interaction.user.display_name = "Admin"
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        async def _run():
            with patch("bot.cogs.crapless.queries") as mock_q:
                mock_q.update_casino_balance = AsyncMock(return_value=1000)
                table.message = None
                await cog.crapless_close.callback(cog, interaction)

        run(_run())
        assert 5 not in cog.active_tables
        assert table.phase == Phase.FINISHED

    def test_close_no_active_table(self):
        from bot.cogs.crapless import CraplessCrapsCog

        cog = CraplessCrapsCog.__new__(CraplessCrapsCog)
        cog.active_tables = {}

        interaction = MagicMock()
        interaction.channel_id = 99
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        run(cog.crapless_close.callback(cog, interaction))
        interaction.response.send_message.assert_called_once()
        assert interaction.response.send_message.call_args.kwargs.get("ephemeral") is True
