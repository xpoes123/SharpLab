"""Pure Figgie logic shared between the Discord cog and the web game engine."""

import random

# ── Constants ────────────────────────────────────────────────────────────────

MIN_PLAYERS = 3
MAX_PLAYERS = 5
NUM_ROUNDS = 6
ROUND_SECONDS = 45
STARTING_CHIPS = 200
GOAL_VALUE = 10

SUITS = ["\u2660", "\u2665", "\u2666", "\u2663"]
SUIT_COLORS = {"\u2660": "black", "\u2663": "black", "\u2665": "red", "\u2666": "red"}
SUIT_NAMES = {"\u2660": "Spades", "\u2663": "Clubs", "\u2665": "Hearts", "\u2666": "Diamonds"}

SUIT_ALIASES: dict[str, str] = {
    "s": "\u2660", "spades": "\u2660", "\u2660": "\u2660",
    "h": "\u2665", "hearts": "\u2665", "\u2665": "\u2665",
    "d": "\u2666", "diamonds": "\u2666", "\u2666": "\u2666",
    "c": "\u2663", "clubs": "\u2663", "\u2663": "\u2663",
}


# ── Deck & Dealing ───────────────────────────────────────────────────────────


def setup_deck() -> tuple[str, str, dict[str, int]]:
    """Create a Figgie deck. Returns (goal_suit, common_suit, suit_counts)."""
    common = random.choice(SUITS)
    goal = [s for s in SUITS if SUIT_COLORS[s] == SUIT_COLORS[common] and s != common][0]
    others = [s for s in SUITS if s not in (common, goal)]
    return goal, common, {common: 12, goal: 10, others[0]: 8, others[1]: 8}


def deal_cards(
    suit_counts: dict[str, int], player_ids: list,
) -> tuple[dict, int]:
    """Deal cards to players. Returns (hands, hidden_count)."""
    deck: list[str] = []
    for suit, count in suit_counts.items():
        deck.extend([suit] * count)
    random.shuffle(deck)

    n = len(player_ids)
    per_player = len(deck) // n
    hidden = len(deck) - per_player * n

    hands: dict = {}
    for i, pid in enumerate(player_ids):
        dealt = deck[i * per_player : (i + 1) * per_player]
        hand: dict[str, int] = {s: 0 for s in SUITS}
        for c in dealt:
            hand[c] += 1
        hands[pid] = hand
    return hands, hidden


# ── Order Matching ───────────────────────────────────────────────────────────


def try_match(new_action, new_suit, new_price, new_player_id, new_player_name,
              book, players) -> dict | None:
    """Try to match an order against the resting book.

    *book* is a list of dicts {player_id, player_name, action, suit, price}.
    *players* is a dict {id: player} where player has .chips and .hand attrs.

    Returns a trade dict or None.
    """
    if new_action == "buy":
        candidates = sorted(
            [o for o in book if o["action"] == "sell" and o["suit"] == new_suit
             and o["player_id"] != new_player_id],
            key=lambda o: o["price"],
        )
        for resting in candidates:
            if new_price < resting["price"]:
                break
            seller = players.get(resting["player_id"])
            buyer = players.get(new_player_id)
            if not seller or not buyer:
                book.remove(resting)
                continue
            if seller.hand.get(new_suit, 0) < 1:
                book.remove(resting)
                continue
            if buyer.chips < resting["price"]:
                return None
            # Execute at resting price
            buyer.chips -= resting["price"]
            seller.chips += resting["price"]
            buyer.hand[new_suit] = buyer.hand.get(new_suit, 0) + 1
            seller.hand[new_suit] -= 1
            book.remove(resting)
            return {
                "buyer_id": new_player_id, "buyer_name": buyer.display_name,
                "seller_id": resting["player_id"], "seller_name": seller.display_name,
                "suit": new_suit, "price": resting["price"],
            }
    else:  # sell
        candidates = sorted(
            [o for o in book if o["action"] == "buy" and o["suit"] == new_suit
             and o["player_id"] != new_player_id],
            key=lambda o: o["price"], reverse=True,
        )
        for resting in candidates:
            if new_price > resting["price"]:
                break
            buyer = players.get(resting["player_id"])
            seller = players.get(new_player_id)
            if not buyer or not seller:
                book.remove(resting)
                continue
            if seller.hand.get(new_suit, 0) < 1:
                return None
            if buyer.chips < resting["price"]:
                book.remove(resting)
                continue
            buyer.chips -= resting["price"]
            seller.chips += resting["price"]
            buyer.hand[new_suit] = buyer.hand.get(new_suit, 0) + 1
            seller.hand[new_suit] -= 1
            book.remove(resting)
            return {
                "buyer_id": resting["player_id"], "buyer_name": buyer.display_name,
                "seller_id": new_player_id, "seller_name": seller.display_name,
                "suit": new_suit, "price": resting["price"],
            }
    return None


def clean_stale_orders(book: list[dict], players: dict) -> list[dict]:
    """Remove orders that can no longer be fulfilled."""
    return [
        o for o in book
        if o["player_id"] in players
        and (
            (o["action"] == "buy" and players[o["player_id"]].chips >= o["price"])
            or (o["action"] == "sell" and players[o["player_id"]].hand.get(o["suit"], 0) >= 1)
        )
    ]


def score_player(player, goal_suit: str) -> int:
    """Final score = chips + goal_suit_cards * GOAL_VALUE."""
    return player.chips + player.hand.get(goal_suit, 0) * GOAL_VALUE


def hand_str(hand: dict[str, int]) -> str:
    return "  ".join(f"{s}{hand.get(s, 0)}" for s in SUITS)
