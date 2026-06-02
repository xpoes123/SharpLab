"""Market-signal detection: arb, middles, steam, divergence."""
from __future__ import annotations

from shared.signals import (
    find_ml_arb, find_total_middle, find_spread_middle, detect_ml_moves,
    middle_profit, estimate_middle_hit, detect_live_swing,
)


def test_live_swing_fires_on_big_move_within_window():
    # 50¢ → 68¢ over 60s: an 18¢ swing inside the 90s window, threshold 15.
    readings = [(1000.0, 50.0), (1030.0, 58.0), (1060.0, 68.0)]
    res = detect_live_swing(readings, now=1060.0, window_seconds=90, threshold_cents=15)
    assert res == (50.0, 68.0, 18.0)  # delta>0 → home firmed


def test_live_swing_delta_sign_when_side_fades():
    readings = [(1000.0, 60.0), (1060.0, 42.0)]
    res = detect_live_swing(readings, now=1060.0, window_seconds=90, threshold_cents=15)
    assert res is not None and res[2] == -18.0  # negative → away firmed


def test_live_swing_ignores_old_readings_outside_window():
    # The 50→70 jump is 200s apart; only the 70→72 pair sits inside a 90s window.
    readings = [(1000.0, 50.0), (1200.0, 70.0), (1240.0, 72.0)]
    assert detect_live_swing(readings, now=1240.0, window_seconds=90, threshold_cents=15) is None


def test_live_swing_below_threshold_is_none():
    readings = [(1000.0, 50.0), (1060.0, 60.0)]  # 10¢ < 15¢ threshold
    assert detect_live_swing(readings, now=1060.0, window_seconds=90, threshold_cents=15) is None


def test_live_swing_needs_two_readings():
    assert detect_live_swing([(1000.0, 50.0)], now=1000.0, window_seconds=90, threshold_cents=15) is None
    assert detect_live_swing([], now=1000.0, window_seconds=90, threshold_cents=15) is None


def test_ml_arb_detected():
    # Book A prices home +110, Book B prices away +110 → 47.6% + 47.6% = 95.2% < 100%.
    odds = {"A": {"ml_home": 110, "ml_away": -130}, "B": {"ml_home": -130, "ml_away": 110}}
    arb = find_ml_arb(odds)
    assert arb and arb["kind"] == "arb"
    assert arb["home_book"] == "A" and arb["away_book"] == "B"
    assert arb["roi_pct"] > 0
    assert round(arb["home_stake_pct"] + arb["away_stake_pct"]) == 100


def test_ml_no_arb_when_vig_present():
    odds = {"A": {"ml_home": -130, "ml_away": 110}, "B": {"ml_home": -135, "ml_away": 115}}
    assert find_ml_arb(odds) is None


def test_arb_suspect_when_one_book_off_market():
    # The real Royals/Reds case: the field agrees on the away side (~+106..+124) but one
    # soft book (mybookieag) is way off at +157, which is the only thing creating the arb.
    odds = {
        "fanduel":    {"ml_home": -127, "ml_away": 106},
        "fanatics":   {"ml_home": -132, "ml_away": 110},
        "kalshi":     {"ml_home": -148, "ml_away": 124},
        "mybookieag": {"ml_home": -180, "ml_away": 157},   # stale outlier
    }
    arb = find_ml_arb(odds)
    assert arb and arb["away_book"] == "mybookieag"
    assert arb["suspect"] is True       # cog suppresses these


def test_arb_not_suspect_when_edge_is_tight():
    # A genuine thin arb: the best prices are only slightly better than the field, no
    # single book wildly off → not flagged.
    odds = {
        "A": {"ml_home": 102, "ml_away": -108},
        "B": {"ml_home": -106, "ml_away": 104},
        "C": {"ml_home": -104, "ml_away": 100},
    }
    arb = find_ml_arb(odds)
    assert arb and arb["suspect"] is False


def test_total_middle():
    odds = {"A": {"total": 220.5, "total_over_odds": -110},
            "B": {"total": 223.5, "total_under_odds": -110}}
    m = find_total_middle(odds)
    assert m and m["kind"] == "total_middle"
    assert m["over_book"] == "A" and m["under_book"] == "B"
    assert m["gap"] == 3.0


def test_total_no_middle_when_lines_equal_and_vigged():
    odds = {"A": {"total": 220.5, "total_over_odds": -110},
            "B": {"total": 220.5, "total_under_odds": -110}}
    assert find_total_middle(odds) is None


def test_total_arb_same_line_plus_prices():
    odds = {"A": {"total": 220.5, "total_over_odds": 105},
            "B": {"total": 220.5, "total_under_odds": 105}}
    m = find_total_middle(odds)
    assert m and m["kind"] == "total_arb"


def test_spread_middle_carries_both_side_prices():
    odds = {"A": {"spread": -2.5, "spread_odds": -110, "spread_away_odds": -110},
            "B": {"spread": -3.5, "spread_odds": -105, "spread_away_odds": -115}}
    m = find_spread_middle(odds)
    assert m and m["gap"] == 1.0
    assert m["home_book"] == "A" and m["home_line"] == -2.5 and m["home_odds"] == -110
    assert m["away_book"] == "B" and m["away_line"] == 3.5 and m["away_odds"] == -115


def test_middle_profit_matches_losing_runline_example():
    # Cards +1.5 @ -200, Cubs +1.5 @ -195, MLB 1-run rate ~28.6% → ~-3% ROI.
    hit = estimate_middle_hit("mlb", "spread", 1.5, -1.5)
    p = middle_profit(-200, -195, hit)
    assert abs(p["breakeven"] - 0.328) < 0.005
    assert -4 < p["ev_roi"] < -2  # negative — should NOT be flagged as a good bet


def test_middle_profit_positive_when_well_priced():
    hit = estimate_middle_hit("mlb", "spread", 1.5, -1.5)
    p = middle_profit(-150, -150, hit)  # cheaper juice → +EV
    assert p["ev_roi"] > 0


def test_total_middle_window_count_scales_hit():
    # over 7.5 / under 9.5 → integers 8,9 hit; wider window = higher prob.
    narrow = estimate_middle_hit("mlb", "total", 7.5, 8.5)   # just 8
    wide = estimate_middle_hit("mlb", "total", 7.5, 10.5)    # 8,9,10
    assert wide > narrow


def test_steam_same_direction():
    base = {b: {"ml_home": -130} for b in ("A", "B", "C", "D")}
    cur = {b: {"ml_home": -150} for b in ("A", "B", "C", "D")}  # all home steamed
    s = detect_ml_moves(base, cur)
    assert s and s["kind"] == "steam" and s["toward"] == "home"
    assert len(s["books"]) == 4


def test_divergence_books_disagree():
    base = {b: {"ml_home": -130} for b in ("A", "B", "C", "D")}
    cur = {"A": {"ml_home": -150}, "B": {"ml_home": -148},   # home
           "C": {"ml_home": -110}, "D": {"ml_home": -112}}   # away
    s = detect_ml_moves(base, cur)
    assert s and s["kind"] == "divergence"
    assert set(s["home_books"]) == {"A", "B"} and set(s["away_books"]) == {"C", "D"}


def test_no_move_below_threshold():
    base = {b: {"ml_home": -130} for b in ("A", "B", "C")}
    cur = {b: {"ml_home": -135} for b in ("A", "B", "C")}  # only 5c, below 12
    assert detect_ml_moves(base, cur) is None


def test_steam_reports_lagging_books():
    base = {b: {"ml_home": -130} for b in ("A", "B", "C", "D")}
    cur = {"A": {"ml_home": -150}, "B": {"ml_home": -150}, "C": {"ml_home": -148},
           "D": {"ml_home": -132}}  # D barely moved → lagging
    s = detect_ml_moves(base, cur)
    assert s["kind"] == "steam"
    assert s["lagging"] == ["D"]


def test_arb_min_roi_filters_marginal():
    # best home +110 (47.6%) + best away +110 (47.6%) = 95.2% → ~5% ROI.
    odds = {"A": {"ml_home": 110, "ml_away": -130}, "B": {"ml_home": -130, "ml_away": 110}}
    assert find_ml_arb(odds) is not None             # no floor → detected
    assert find_ml_arb(odds, min_roi=6.0) is None    # 6% floor > ~5% edge → filtered
