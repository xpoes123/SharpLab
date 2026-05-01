"""Tests for Crapless Craps game logic — resolution, payouts, side bets."""
import pytest
from bot.cogs.crapless import (
    _resolve_side_bets_for_player,
    _pass_odds_win,
    _table_embed,
    CraplessTable,
    CraplessTableView,
    PlayerBets,
    BetType,
    Phase,
    SideBet,
    TRUE_ODDS,
    PLACE_PAYOUTS,
    HARDWAY_PAYOUTS,
    SIDE_BET_LABELS,
    POINT_PHASE_ONLY,
)

# ── Come-out resolution ──────────────────────────────────────────────────


class FakeActiveTables(dict):
    """Stand-in for the cog's active_tables dict."""
    pass


def _make_view(table: CraplessTable) -> CraplessTableView:
    tables = FakeActiveTables()
    tables[table.channel_id] = table
    return CraplessTableView(table, tables)


def _make_table(**kw) -> CraplessTable:
    defaults = dict(channel_id=1, shooter_id=100, shooter_name="Shooter")
    defaults.update(kw)
    return CraplessTable(**defaults)


def _make_player(**kw) -> PlayerBets:
    defaults = dict(user_id=200, display_name="Player1")
    defaults.update(kw)
    return PlayerBets(**defaults)


class TestComeOut:
    """Crapless come-out: only 7 is a natural. Everything else sets a point."""

    def test_natural_7_wins(self):
        table = _make_table()
        p = _make_player(bet_type=BetType.PASS_LINE, bet=100, coins_in=100)
        table.players[p.user_id] = p
        view = _make_view(table)

        finished = view._resolve_come_out(7)
        assert finished is True
        assert table.phase == Phase.FINISHED
        assert table.outcome == "Natural 7!"
        assert p.payout == 200  # bet * 2

    @pytest.mark.parametrize("total", [2, 3, 4, 5, 6, 8, 9, 10, 11, 12])
    def test_non_seven_sets_point(self, total):
        table = _make_table()
        p = _make_player(bet_type=BetType.PASS_LINE, bet=100, coins_in=100)
        table.players[p.user_id] = p
        view = _make_view(table)

        finished = view._resolve_come_out(total)
        assert finished is False
        assert table.phase == Phase.POINT
        assert table.point == total
        assert p.payout == 0  # no payout yet

    def test_2_sets_point_not_craps(self):
        """In crapless, 2 is NOT a craps loss on come-out — it sets a point."""
        table = _make_table()
        p = _make_player(bet_type=BetType.PASS_LINE, bet=100, coins_in=100)
        table.players[p.user_id] = p
        view = _make_view(table)

        finished = view._resolve_come_out(2)
        assert finished is False
        assert table.point == 2

    def test_12_sets_point_not_craps(self):
        """In crapless, 12 is NOT a craps loss on come-out — it sets a point."""
        table = _make_table()
        p = _make_player(bet_type=BetType.PASS_LINE, bet=100, coins_in=100)
        table.players[p.user_id] = p
        view = _make_view(table)

        finished = view._resolve_come_out(12)
        assert finished is False
        assert table.point == 12

    def test_side_bet_only_player_no_payout(self):
        """A player without a main bet gets payout=0."""
        table = _make_table()
        p = _make_player(bet_type=None, bet=0)
        table.players[p.user_id] = p
        view = _make_view(table)

        finished = view._resolve_come_out(7)
        assert finished is True
        assert p.payout == 0


# ── Point resolution ─────────────────────────────────────────────────────


class TestPointPhase:
    @pytest.mark.parametrize("point", [2, 3, 4, 5, 6, 8, 9, 10, 11, 12])
    def test_point_hit_wins_pass_line(self, point):
        table = _make_table(phase=Phase.POINT, point=point)
        p = _make_player(bet_type=BetType.PASS_LINE, bet=100, coins_in=100)
        table.players[p.user_id] = p
        view = _make_view(table)

        finished = view._resolve_point(point)
        assert finished is True
        assert table.phase == Phase.FINISHED
        assert p.payout == 200  # bet * 2

    @pytest.mark.parametrize("point", [2, 3, 4, 5, 6, 8, 9, 10, 11, 12])
    def test_seven_out_loses_pass_line(self, point):
        table = _make_table(phase=Phase.POINT, point=point)
        p = _make_player(bet_type=BetType.PASS_LINE, bet=100, coins_in=100)
        table.players[p.user_id] = p
        view = _make_view(table)

        finished = view._resolve_point(7)
        assert finished is True
        assert table.outcome == "Seven-out!"
        assert p.payout == 0

    def test_non_point_non_seven_continues(self):
        table = _make_table(phase=Phase.POINT, point=4)
        p = _make_player(bet_type=BetType.PASS_LINE, bet=100, coins_in=100)
        table.players[p.user_id] = p
        view = _make_view(table)

        finished = view._resolve_point(5)
        assert finished is False
        assert table.phase == Phase.POINT

    @pytest.mark.parametrize("point", list(TRUE_ODDS.keys()))
    def test_odds_bet_payout(self, point):
        table = _make_table(phase=Phase.POINT, point=point)
        p = _make_player(
            bet_type=BetType.PASS_LINE, bet=100, odds_bet=100, coins_in=200,
        )
        table.players[p.user_id] = p
        view = _make_view(table)

        finished = view._resolve_point(point)
        assert finished is True
        num, den = TRUE_ODDS[point]
        expected_odds_win = 100 * num // den
        assert p.payout == 200 + 100 + expected_odds_win  # bet*2 + odds_bet + odds_win

    def test_odds_bet_lost_on_seven_out(self):
        table = _make_table(phase=Phase.POINT, point=4)
        p = _make_player(
            bet_type=BetType.PASS_LINE, bet=100, odds_bet=100, coins_in=200,
        )
        table.players[p.user_id] = p
        view = _make_view(table)

        finished = view._resolve_point(7)
        assert finished is True
        assert p.payout == 0  # loses everything


# ── Side bet resolution ──────────────────────────────────────────────────


class TestFieldBet:
    @pytest.mark.parametrize("total", [2, 12])
    def test_field_double_pay(self, total):
        """2 and 12 pay 2:1 on field."""
        p = _make_player()
        p.side_bets = [SideBet(kind="field", amount=50)]
        d1, d2 = (1, 1) if total == 2 else (6, 6)
        credit = _resolve_side_bets_for_player(p, d1, d2)
        assert credit == 50 + 100  # original + 2x win
        assert len(p.side_bets) == 0  # field is one-roll

    @pytest.mark.parametrize("total", [3, 4, 9, 10, 11])
    def test_field_even_pay(self, total):
        """3, 4, 9, 10, 11 pay 1:1 on field."""
        p = _make_player()
        p.side_bets = [SideBet(kind="field", amount=50)]
        # Pick dice that sum to total
        d1 = min(total - 1, 6)
        d2 = total - d1
        credit = _resolve_side_bets_for_player(p, d1, d2)
        assert credit == 100  # 2x bet (original + 1:1 win)

    @pytest.mark.parametrize("total", [5, 6, 7, 8])
    def test_field_loss(self, total):
        """5, 6, 7, 8 lose on field."""
        p = _make_player()
        p.side_bets = [SideBet(kind="field", amount=50)]
        d1 = min(total - 1, 6)
        d2 = total - d1
        credit = _resolve_side_bets_for_player(p, d1, d2)
        assert credit == 0


class TestAny7:
    def test_any7_wins(self):
        p = _make_player()
        p.side_bets = [SideBet(kind="any7", amount=25)]
        credit = _resolve_side_bets_for_player(p, 3, 4)
        assert credit == 25 + 100  # bet + 4x win

    def test_any7_loses(self):
        p = _make_player()
        p.side_bets = [SideBet(kind="any7", amount=25)]
        credit = _resolve_side_bets_for_player(p, 3, 3)
        assert credit == 0


class TestAnyCraps:
    @pytest.mark.parametrize("d1,d2", [(1, 1), (1, 2), (6, 6)])
    def test_any_craps_wins(self, d1, d2):
        p = _make_player()
        p.side_bets = [SideBet(kind="any_craps", amount=10)]
        credit = _resolve_side_bets_for_player(p, d1, d2)
        assert credit == 10 + 70  # bet + 7x win

    def test_any_craps_loses(self):
        p = _make_player()
        p.side_bets = [SideBet(kind="any_craps", amount=10)]
        credit = _resolve_side_bets_for_player(p, 3, 4)  # 7
        assert credit == 0


class TestYo:
    def test_yo_wins(self):
        p = _make_player()
        p.side_bets = [SideBet(kind="yo", amount=10)]
        credit = _resolve_side_bets_for_player(p, 5, 6)
        assert credit == 10 + 150  # bet + 15x win

    def test_yo_loses(self):
        p = _make_player()
        p.side_bets = [SideBet(kind="yo", amount=10)]
        credit = _resolve_side_bets_for_player(p, 5, 5)
        assert credit == 0


class TestComeBet:
    def test_come_wins_on_7(self):
        """New come bet (no point yet) wins on 7."""
        p = _make_player()
        p.side_bets = [SideBet(kind="come", amount=50)]
        credit = _resolve_side_bets_for_player(p, 3, 4)
        assert credit == 100  # 2x bet

    def test_come_establishes_point(self):
        """Non-7 roll on come bet with no point establishes a come point."""
        p = _make_player()
        p.side_bets = [SideBet(kind="come", amount=50)]
        credit = _resolve_side_bets_for_player(p, 2, 3)  # 5
        assert credit == 0
        assert len(p.side_bets) == 1
        assert p.side_bets[0].come_point == 5

    def test_come_point_hit_wins(self):
        """Come bet with established point wins when point is hit."""
        p = _make_player()
        p.side_bets = [SideBet(kind="come", amount=50, come_point=8)]
        credit = _resolve_side_bets_for_player(p, 3, 5)  # 8
        assert credit == 100
        assert len(p.side_bets) == 0

    def test_come_point_loses_on_7(self):
        """Come bet with established point loses on 7."""
        p = _make_player()
        p.side_bets = [SideBet(kind="come", amount=50, come_point=8)]
        credit = _resolve_side_bets_for_player(p, 3, 4)  # 7
        assert credit == 0
        assert len(p.side_bets) == 0

    def test_come_point_stays_on_other(self):
        """Come bet with established point stays if neither point nor 7."""
        p = _make_player()
        p.side_bets = [SideBet(kind="come", amount=50, come_point=8)]
        credit = _resolve_side_bets_for_player(p, 2, 3)  # 5
        assert credit == 0
        assert len(p.side_bets) == 1

    def test_come_2_establishes_point_in_crapless(self):
        """In crapless, 2 on come bet come-out establishes a come point (not craps loss)."""
        p = _make_player()
        p.side_bets = [SideBet(kind="come", amount=50)]
        credit = _resolve_side_bets_for_player(p, 1, 1)  # 2
        assert credit == 0
        assert p.side_bets[0].come_point == 2

    def test_come_12_establishes_point_in_crapless(self):
        """In crapless, 12 on come bet come-out establishes a come point (not craps loss)."""
        p = _make_player()
        p.side_bets = [SideBet(kind="come", amount=50)]
        credit = _resolve_side_bets_for_player(p, 6, 6)  # 12
        assert credit == 0
        assert p.side_bets[0].come_point == 12


class TestPlaceBet:
    @pytest.mark.parametrize("num", [2, 3, 4, 5, 6, 8, 9, 10, 11, 12])
    def test_place_wins(self, num):
        kind = f"place_{num}"
        p = _make_player()
        p.side_bets = [SideBet(kind=kind, amount=100)]
        d1 = min(num - 1, 6)
        d2 = num - d1
        credit = _resolve_side_bets_for_player(p, d1, d2)
        pn, pd = PLACE_PAYOUTS[num]
        expected_win = 100 * pn // pd
        assert credit == 100 + expected_win

    @pytest.mark.parametrize("num", [2, 3, 4, 5, 6, 8, 9, 10, 11, 12])
    def test_place_loses_on_7(self, num):
        kind = f"place_{num}"
        p = _make_player()
        p.side_bets = [SideBet(kind=kind, amount=100)]
        credit = _resolve_side_bets_for_player(p, 3, 4)  # 7
        assert credit == 0
        assert len(p.side_bets) == 0

    def test_place_stays_on_other(self):
        p = _make_player()
        p.side_bets = [SideBet(kind="place_4", amount=100)]
        credit = _resolve_side_bets_for_player(p, 4, 5)  # 9
        assert credit == 0
        assert len(p.side_bets) == 1


class TestHardway:
    @pytest.mark.parametrize("num", [4, 6, 8, 10])
    def test_hardway_wins(self, num):
        kind = f"hard_{num}"
        p = _make_player()
        p.side_bets = [SideBet(kind=kind, amount=10)]
        half = num // 2
        credit = _resolve_side_bets_for_player(p, half, half)
        expected_win = 10 * HARDWAY_PAYOUTS[num]
        assert credit == 10 + expected_win

    @pytest.mark.parametrize("num", [4, 6, 8, 10])
    def test_hardway_loses_easy_way(self, num):
        """Rolling the number the easy way (not doubles) loses the hardway."""
        kind = f"hard_{num}"
        p = _make_player()
        p.side_bets = [SideBet(kind=kind, amount=10)]
        # Easy way: non-matching dice summing to num
        d1 = num // 2 - 1
        d2 = num - d1
        assert d1 != d2  # confirm easy way
        credit = _resolve_side_bets_for_player(p, d1, d2)
        assert credit == 0
        assert len(p.side_bets) == 0

    @pytest.mark.parametrize("num", [4, 6, 8, 10])
    def test_hardway_loses_on_7(self, num):
        kind = f"hard_{num}"
        p = _make_player()
        p.side_bets = [SideBet(kind=kind, amount=10)]
        credit = _resolve_side_bets_for_player(p, 3, 4)
        assert credit == 0
        assert len(p.side_bets) == 0

    def test_hardway_stays_on_other(self):
        p = _make_player()
        p.side_bets = [SideBet(kind="hard_4", amount=10)]
        credit = _resolve_side_bets_for_player(p, 4, 5)  # 9, not 4 and not 7
        assert credit == 0
        assert len(p.side_bets) == 1


# ── Odds payout calculation ──────────────────────────────────────────────


class TestPassOddsWin:
    @pytest.mark.parametrize("point,expected_ratio", [
        (2, (6, 1)), (3, (3, 1)),
        (4, (2, 1)), (5, (3, 2)), (6, (6, 5)),
        (8, (6, 5)), (9, (3, 2)), (10, (2, 1)),
        (11, (3, 1)), (12, (6, 1)),
    ])
    def test_odds_payout(self, point, expected_ratio):
        num, den = expected_ratio
        assert _pass_odds_win(100, point) == 100 * num // den


# ── Embed generation ─────────────────────────────────────────────────────


class TestEmbed:
    def test_embed_no_players(self):
        table = _make_table()
        embed = _table_embed(table)
        assert "No players yet" in embed.fields[0].value

    def test_embed_come_out_title(self):
        table = _make_table()
        embed = _table_embed(table)
        assert "Come-Out Roll" in embed.title

    def test_embed_point_title(self):
        table = _make_table(phase=Phase.POINT, point=4)
        embed = _table_embed(table)
        assert "Point is 4" in embed.title

    def test_embed_finished_title(self):
        table = _make_table(phase=Phase.FINISHED, outcome="Natural 7!")
        embed = _table_embed(table)
        assert "Natural 7!" in embed.title

    def test_embed_with_player(self):
        table = _make_table()
        p = _make_player(bet_type=BetType.PASS_LINE, bet=100, coins_in=100)
        table.players[p.user_id] = p
        embed = _table_embed(table)
        assert "Player1" in embed.fields[0].value

    def test_embed_side_bet_results(self):
        table = _make_table()
        p = _make_player()
        p.side_log = ["Test log entry"]
        table.players[p.user_id] = p
        embed = _table_embed(table)
        found = any(f.name == "Side Bet Results" for f in embed.fields)
        assert found

    def test_embed_truncates_long_players(self):
        """Players field value must not exceed 1024 characters."""
        table = _make_table()
        for i in range(8):
            p = PlayerBets(
                user_id=i,
                display_name=f"VeryLongPlayerName_{i}_{'x' * 50}",
                bet_type=BetType.PASS_LINE,
                bet=999999,
                coins_in=999999,
            )
            # Add many side bets to inflate the line length
            for j in range(5):
                p.side_bets.append(SideBet(kind="field", amount=j * 1000))
            table.players[i] = p
        embed = _table_embed(table)
        players_field = embed.fields[0]
        assert len(players_field.value) <= 1024


# ── Multiple side bets in one roll ───────────────────────────────────────


class TestMultipleSideBets:
    def test_multiple_field_bets(self):
        p = _make_player()
        p.side_bets = [SideBet(kind="field", amount=25), SideBet(kind="field", amount=50)]
        credit = _resolve_side_bets_for_player(p, 1, 1)  # 2 → double pay
        # 25 * 2 = 50 win + 25 return = 75
        # 50 * 2 = 100 win + 50 return = 150
        assert credit == 75 + 150

    def test_mixed_side_bets_one_roll(self):
        """Field + Any Craps + Yo on a roll of 12: field wins 2:1, any craps wins 7:1, yo loses."""
        p = _make_player()
        p.side_bets = [
            SideBet(kind="field", amount=10),
            SideBet(kind="any_craps", amount=10),
            SideBet(kind="yo", amount=10),
        ]
        credit = _resolve_side_bets_for_player(p, 6, 6)  # 12
        # Field: 2:1 → 10 + 20 = 30
        # Any Craps: 7:1 → 10 + 70 = 80
        # Yo: loses
        assert credit == 30 + 80

    def test_come_and_place_on_same_number(self):
        """Come bet (point 8) and Place 8 both win when 8 is rolled."""
        p = _make_player()
        p.side_bets = [
            SideBet(kind="come", amount=50, come_point=8),
            SideBet(kind="place_8", amount=100),
        ]
        credit = _resolve_side_bets_for_player(p, 3, 5)  # 8
        # Come: 50 * 2 = 100
        pn, pd = PLACE_PAYOUTS[8]
        place_win = 100 * pn // pd
        # Place 8: 100 + win
        assert credit == 100 + 100 + place_win


# ── coins tracking ───────────────────────────────────────────────────────


class TestCoinsTracking:
    def test_side_bet_credit_updates_coins_out(self):
        p = _make_player(coins_out=0)
        p.side_bets = [SideBet(kind="field", amount=50)]
        _resolve_side_bets_for_player(p, 1, 1)  # field wins
        assert p.coins_out > 0

    def test_losing_side_bet_no_coins_out(self):
        p = _make_player(coins_out=0)
        p.side_bets = [SideBet(kind="field", amount=50)]
        _resolve_side_bets_for_player(p, 3, 4)  # field loses on 7
        assert p.coins_out == 0
