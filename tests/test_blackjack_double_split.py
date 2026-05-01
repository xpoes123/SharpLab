"""Regression tests for the double-down + split active_hand bug.

When a player has split and then doubles down on the *main* hand, the call to
hit_active() can invoke _advance(), changing active_hand from 0 to 1 (split).
The old code then mistakenly auto-stood the split hand, preventing the player
from ever playing it.

These tests verify the pure game-state logic (PlayerHand + BlackjackTable)
without needing Discord or DB stubs.
"""

import sys
import types
from unittest.mock import AsyncMock

# Stub out db.queries before importing the cog module
_queries_stub = types.ModuleType("db.queries")
_queries_stub.get_or_create_casino_wallet = AsyncMock(return_value=1000)
_queries_stub.update_casino_balance = AsyncMock()
_queries_stub.get_casino_balance = AsyncMock(return_value=1000)
_queries_stub.log_casino_result = AsyncMock()
_queries_stub.record_geo_attempt = AsyncMock()
_queries_stub.get_geo_stats_by_region = AsyncMock(return_value=[])
_queries_stub.get_elo_rating = AsyncMock(return_value={"rating": 1000, "games_played": 0})
_queries_stub.get_casino_stats = AsyncMock(return_value=None)
sys.modules.setdefault("db", types.ModuleType("db"))
sys.modules["db.queries"] = _queries_stub

if "bot.cogs.casino" in sys.modules:
    del sys.modules["bot.cogs.casino"]

from bot.cogs.casino import PlayerHand, BlackjackTable, _new_shoe  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_player(bet: int = 100) -> PlayerHand:
    return PlayerHand(user_id=1, display_name="Alice", bet=bet, original_bet=bet)


def _simulate_fixed_double_stand(p: PlayerHand, was_on_split: bool) -> None:
    """Apply the fixed force-stand logic (mirrors the patched double_btn handler)."""
    if was_on_split:
        if not p.split_busted and not p.split_stood:
            p.stand_active()
    else:
        if not p.busted and not p.stood:
            p.stand_active()


# ---------------------------------------------------------------------------
# Test: double-down on main hand busts → split hand must NOT be auto-stood
# ---------------------------------------------------------------------------


def test_double_main_bust_does_not_auto_stand_split():
    """
    Scenario: player splits, doubles main hand, main hand busts.
    _advance() moves active_hand to 1. The split hand must remain unresolved
    so the player can still play it.
    """
    p = _make_player(bet=100)
    # Main hand: 8+10=18 (can double), split hand: 8+7=15 (unplayed)
    p.hand = ["8♠", "10♦"]
    p.split_hand = ["8♥", "7♣"]
    p.has_split = True
    p.split_bet = 100
    p.active_hand = 0

    p.bet *= 2
    p.doubled = True

    # Capture BEFORE hit_active (the fix)
    was_on_split = p.has_split and p.active_hand == 1
    assert not was_on_split, "sanity: active hand is main before double"

    # Hit main hand to bust: 8+10+10 = 28
    p.hit_active("10♣")

    assert p.busted, "main hand should be busted"
    assert p.active_hand == 1, "_advance should have moved to split hand"

    _simulate_fixed_double_stand(p, was_on_split)

    # Split hand must remain unresolved
    assert not p.split_stood, "split hand must NOT be auto-stood after main hand bust"
    assert not p.split_busted
    assert not p.done, "player is not done — split hand still needs playing"


# ---------------------------------------------------------------------------
# Test: double-down on main hand hits 21 → split hand must NOT be auto-stood
# ---------------------------------------------------------------------------


def test_double_main_21_does_not_auto_stand_split():
    """
    Scenario: player splits, doubles main hand, main hand hits exactly 21.
    hit_active() sets stood=True and _advance() moves active_hand to 1.
    The split hand must remain unresolved.
    """
    p = _make_player(bet=100)
    p.hand = ["5♠", "6♦"]        # value = 11, good double candidate
    p.split_hand = ["5♥", "4♣"]  # value = 9, unplayed
    p.has_split = True
    p.split_bet = 100
    p.active_hand = 0

    p.bet *= 2
    p.doubled = True

    was_on_split = p.has_split and p.active_hand == 1
    assert not was_on_split

    # Hit main hand to exactly 21: 5+6+10 = 21
    p.hit_active("10♥")

    assert p.stood, "main hand at 21 should be stood"
    assert p.active_hand == 1, "_advance should have moved to split hand"

    _simulate_fixed_double_stand(p, was_on_split)

    assert not p.split_stood, "split hand must NOT be auto-stood after main hand 21"
    assert not p.done, "player is not done — split hand still needs playing"


# ---------------------------------------------------------------------------
# Test: double-down on split hand (active_hand == 1) still auto-stands it
# ---------------------------------------------------------------------------


def test_double_split_hand_stands_correctly():
    """
    Doubling down on the split hand should auto-stand that split hand.
    was_on_split is True, so stand_active is called on the split hand.
    """
    p = _make_player(bet=100)
    p.hand = ["8♠", "5♦"]        # value = 13, already stood
    p.split_hand = ["8♥", "3♣"]  # value = 11, good double candidate
    p.has_split = True
    p.split_bet = 100
    p.stood = True                # main hand already stood
    p.active_hand = 1             # playing split hand

    p.split_bet *= 2
    p.doubled = True

    was_on_split = p.has_split and p.active_hand == 1
    assert was_on_split, "sanity: should be on split hand"

    # Hit split hand to 18: 8+3+7 = 18
    p.hit_active("7♦")

    assert not p.split_busted
    assert not p.split_stood  # hit_active only auto-stands at 21

    _simulate_fixed_double_stand(p, was_on_split)

    assert p.split_stood, "split hand should be stood after doubling it"
    assert p.done, "both hands are now done"


# ---------------------------------------------------------------------------
# Test: double-down on solo hand (no split) still force-stands correctly
# ---------------------------------------------------------------------------


def test_double_no_split_stands_correctly():
    """Regression: doubling a solo (no-split) hand must still force-stand it."""
    p = _make_player(bet=100)
    p.hand = ["6♠", "5♦"]  # value = 11
    p.active_hand = 0
    p.has_split = False

    p.bet *= 2
    p.doubled = True

    was_on_split = p.has_split and p.active_hand == 1
    assert not was_on_split

    # Hit to 20: 6+5+9 = 20
    p.hit_active("9♣")

    assert not p.busted
    assert not p.stood  # not auto-stood at 20

    _simulate_fixed_double_stand(p, was_on_split)

    assert p.stood, "solo hand must be stood after double"
    assert p.done
