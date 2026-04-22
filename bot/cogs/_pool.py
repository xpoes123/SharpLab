"""Shared side-pot payout logic for multiplayer casino games.

Implements poker-style side pots: a player can only win from each
opponent up to the amount they themselves bet.  Excess money from
higher bettors is refunded when no winner can cover it.
"""


def compute_side_pot_payouts(
    bets: dict[int, int],
    winner_uids: list[int] | set[int],
    house_edge: float = 0.0,
) -> dict[int, int]:
    """Return ``{uid: payout}`` using poker-style side pots.

    Parameters
    ----------
    bets:
        Mapping of ``user_id`` → amount wagered.
    winner_uids:
        The user(s) who won the game.
    house_edge:
        Fraction taken as rake.  Default 0 (no rake).
        Only applied to pots that are actually won (not refunded).

    Returns
    -------
    dict mapping every uid in *bets* to the coins they should receive
    back (0 for losers who are fully covered by winners, >0 for winners
    and for higher-bettors whose excess wasn't covered).
    """
    if not bets:
        return {}

    winner_set = set(winner_uids)

    # Sort players by bet amount (ascending)
    sorted_bets = sorted(bets.items(), key=lambda x: x[1])

    payouts: dict[int, int] = {uid: 0 for uid in bets}
    prev_level = 0

    for _uid, bet_level in sorted_bets:
        increment = bet_level - prev_level
        if increment <= 0:
            continue

        # Everyone who bet >= this level contributed to this layer
        eligible = {uid for uid, b in bets.items() if b >= bet_level}
        pot_amount = increment * len(eligible)

        if len(eligible) == 1:
            # Only one person at this level — return their own money
            sole = next(iter(eligible))
            payouts[sole] += pot_amount
        else:
            eligible_winners = [w for w in winner_set if w in eligible]
            if eligible_winners:
                # Rake only on contested pots that are won
                if house_edge > 0 and pot_amount > 1:
                    rake = max(1, int(pot_amount * house_edge))
                else:
                    rake = 0
                distributable = pot_amount - rake
                share = distributable // len(eligible_winners)
                for w in eligible_winners:
                    payouts[w] += share
            else:
                # No winner eligible for this layer — refund equally
                refund_each = pot_amount // len(eligible)
                for uid in eligible:
                    payouts[uid] += refund_each

        prev_level = bet_level

    return payouts
