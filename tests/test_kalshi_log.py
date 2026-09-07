"""Pure logic for the Kalshi auto-logger: ticker parsing, position direction, and the
YES-space metric math (drift / excursions / realized P&L). No I/O — fast unit tests."""
from __future__ import annotations

from shared.kalshi_log import (
    parse_kalshi_ticker,
    long_yes,
    signed_move,
    realized_pnl_c,
    excursions,
    drift_at,
)


# --- ticker parsing (real Kalshi NFL formats) ---

def test_parse_game_winner():
    p = parse_kalshi_ticker("KXNFLGAME-26SEP21NYGLAR-NYG")
    assert (p.market, p.side, p.line, p.event) == ("moneyline", "NYG", None, "26SEP21NYGLAR")


def test_parse_spread_line_is_n_minus_half():
    # KC8 = "Kansas City wins by over 7.5"
    p = parse_kalshi_ticker("KXNFLSPREAD-26SEP14DENKC-KC8")
    assert p.market == "spread" and p.side == "KC" and p.line == 7.5


def test_parse_total_over_line():
    # 64 = "Over 63.5 points"
    p = parse_kalshi_ticker("KXNFLTOTAL-26SEP14DENKC-64")
    assert p.market == "total" and p.side == "over" and p.line == 63.5


def test_parse_unknown_series_falls_back_never_raises():
    p = parse_kalshi_ticker("KXWEIRDTHING-XYZ-ABC")
    assert p.market == "kalshi" and p.side == "ABC"  # logged, not dropped
    p2 = parse_kalshi_ticker("GARBAGE")
    assert p2.market == "kalshi"  # even malformed tickers parse to something


# --- position direction: which way am I effectively long? ---

def test_long_yes_direction():
    assert long_yes("buy", "yes") is True    # bought YES -> long YES
    assert long_yes("buy", "no") is False    # bought NO  -> long NO
    assert long_yes("sell", "yes") is False  # sold YES   -> long NO
    assert long_yes("sell", "no") is True    # sold NO    -> long YES


# --- YES-space metric math ---

def test_signed_move_favorable_direction():
    # long YES: price up = favorable
    assert signed_move(42, 48, long_yes=True) == 6
    assert signed_move(42, 40, long_yes=True) == -2
    # long NO: price down = favorable
    assert signed_move(42, 40, long_yes=False) == 2
    assert signed_move(42, 48, long_yes=False) == -6


def test_realized_pnl_per_contract():
    # long YES, entry 42c: wins -> +58, loses -> -42
    assert realized_pnl_c(42, result_yes=True, long_yes=True) == 58
    assert realized_pnl_c(42, result_yes=False, long_yes=True) == -42
    # long NO, entry(YES) 42c i.e. paid 58c for NO: NO wins (result no) -> +42, loses -> -58
    assert realized_pnl_c(42, result_yes=False, long_yes=False) == 42
    assert realized_pnl_c(42, result_yes=True, long_yes=False) == -58


def test_excursions_max_favorable_and_adverse():
    mids = [45, 50, 38, 44]
    mfe, mae = excursions(42, mids, long_yes=True)
    assert mfe == 8 and mae == -4   # best +8 (@50), worst -4 (@38)


def test_drift_at_first_snapshot_past_horizon():
    traj = [(10, 43), (35, 47), (140, 51)]  # (age_seconds, yes_mid_c)
    assert drift_at(42, traj, long_yes=True, seconds=30) == 5    # @35s mid 47 -> +5
    assert drift_at(42, traj, long_yes=True, seconds=120) == 9   # @140s mid 51 -> +9
    assert drift_at(42, traj, long_yes=True, seconds=600) is None  # no snapshot that old yet
