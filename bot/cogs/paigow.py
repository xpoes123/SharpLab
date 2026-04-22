"""Pai Gow Poker cog — /paigow with open-face dealer cards."""
import itertools
import random
from collections import Counter
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries

# ── Card helpers ──────────────────────────────────────────────────────────────

SUITS = ("♠", "♥", "♦", "♣")
RANKS = ("2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A")
RANK_VAL: dict[str, int] = {r: i for i, r in enumerate(RANKS, 2)}  # 2..14
RANK_NAME: dict[int, str] = {v: k for k, v in RANK_VAL.items()}
JOKER = "JK"


def _new_deck() -> list[str]:
    """53-card deck: 52 normal + 1 joker, shuffled."""
    cards = [f"{r}{s}" for s in SUITS for r in RANKS]
    cards.append(JOKER)
    random.shuffle(cards)
    return cards


def _rank(card: str) -> str:
    if card == JOKER:
        return JOKER
    return card[:-1]


def _suit(card: str) -> str:
    if card == JOKER:
        return ""
    return card[-1]


def _rank_val(card: str) -> int:
    return RANK_VAL.get(_rank(card), 14)  # joker defaults to Ace value


def _is_joker(card: str) -> bool:
    return card == JOKER


def _fmt_card(card: str) -> str:
    if card == JOKER:
        return "`🃏`"
    return f"`{card}`"


def _fmt_hand(hand: list[str]) -> str:
    return " ".join(_fmt_card(c) for c in hand)


# ── 5-card poker hand evaluation ─────────────────────────────────────────────

def _evaluate_5_no_joker(cards: list[str]) -> tuple[int, ...]:
    """Evaluate a 5-card hand with no joker. Returns a comparable tuple."""
    ranks = sorted([_rank_val(c) for c in cards], reverse=True)
    suits = [_suit(c) for c in cards]
    is_flush = len(set(suits)) == 1

    unique = sorted(set(ranks), reverse=True)
    is_straight = False
    high = ranks[0]

    if len(unique) == 5:
        if unique[0] - unique[4] == 4:
            is_straight = True
            high = unique[0]
        elif unique == [14, 5, 4, 3, 2]:  # ace-low straight
            is_straight = True
            high = 5

    counts = Counter(ranks)
    freq = sorted(counts.values(), reverse=True)

    if is_straight and is_flush:
        return (9, high)  # straight flush (royal = high 14)
    if freq == [4, 1]:
        quad = [r for r, c in counts.items() if c == 4][0]
        kick = [r for r, c in counts.items() if c == 1][0]
        return (7, quad, kick)
    if freq == [3, 2]:
        trip = [r for r, c in counts.items() if c == 3][0]
        pair = [r for r, c in counts.items() if c == 2][0]
        return (6, trip, pair)
    if is_flush:
        return (5,) + tuple(ranks)
    if is_straight:
        return (4, high)
    if freq == [3, 1, 1]:
        trip = [r for r, c in counts.items() if c == 3][0]
        kicks = sorted([r for r, c in counts.items() if c == 1], reverse=True)
        return (3, trip) + tuple(kicks)
    if freq == [2, 2, 1]:
        pairs = sorted([r for r, c in counts.items() if c == 2], reverse=True)
        kick = [r for r, c in counts.items() if c == 1][0]
        return (2, pairs[0], pairs[1], kick)
    if freq == [2, 1, 1, 1]:
        pair = [r for r, c in counts.items() if c == 2][0]
        kicks = sorted([r for r, c in counts.items() if c == 1], reverse=True)
        return (1, pair) + tuple(kicks)
    return (0,) + tuple(ranks)


# All 52 normal cards for joker substitution
_ALL_CARDS = [f"{r}{s}" for s in SUITS for r in RANKS]


def _evaluate_5(cards: list[str]) -> tuple[int, ...]:
    """Evaluate a 5-card hand, handling joker by trying all substitutions."""
    joker_indices = [i for i, c in enumerate(cards) if _is_joker(c)]
    if not joker_indices:
        return _evaluate_5_no_joker(cards)

    # Joker present — try all 52 possible substitutions
    idx = joker_indices[0]
    other_cards = {c for i, c in enumerate(cards) if i != idx}
    best = (0,)
    for sub in _ALL_CARDS:
        if sub in other_cards:
            continue  # can't duplicate a card already in hand
        trial = list(cards)
        trial[idx] = sub
        score = _evaluate_5_no_joker(trial)
        if score > best:
            best = score
    # Five aces: 4 aces + joker
    ace_count = sum(1 for c in cards if _rank(c) == "A")
    if ace_count == 4:
        return (10,)  # five aces — highest possible
    return best


def _evaluate_2(cards: list[str]) -> tuple[int, ...]:
    """Evaluate a 2-card low hand. Joker = Ace."""
    vals = sorted([_rank_val(c) for c in cards], reverse=True)
    # Joker is already valued at 14 (Ace) via _rank_val
    if vals[0] == vals[1]:
        return (1, vals[0])  # pair
    return (0, vals[0], vals[1])


# ── Hand name strings ────────────────────────────────────────────────────────

_RANK_WORD: dict[int, str] = {
    2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven",
    8: "Eight", 9: "Nine", 10: "Ten", 11: "Jack", 12: "Queen", 13: "King", 14: "Ace",
}
_RANK_PLURAL: dict[int, str] = {
    2: "Twos", 3: "Threes", 4: "Fours", 5: "Fives", 6: "Sixes", 7: "Sevens",
    8: "Eights", 9: "Nines", 10: "Tens", 11: "Jacks", 12: "Queens", 13: "Kings", 14: "Aces",
}


def _hand_name_5(score: tuple[int, ...]) -> str:
    tier = score[0]
    if tier == 10:
        return "Five Aces"
    if tier == 9:
        return "Royal Flush" if score[1] == 14 else f"Straight Flush ({_RANK_WORD[score[1]]}-high)"
    if tier == 7:
        return f"Four {_RANK_PLURAL[score[1]]}"
    if tier == 6:
        return f"Full House ({_RANK_PLURAL[score[1]]} over {_RANK_PLURAL[score[2]]})"
    if tier == 5:
        return f"Flush ({_RANK_WORD[score[1]]}-high)"
    if tier == 4:
        return f"Straight ({_RANK_WORD[score[1]]}-high)"
    if tier == 3:
        return f"Three {_RANK_PLURAL[score[1]]}"
    if tier == 2:
        return f"Two Pair ({_RANK_PLURAL[score[1]]} and {_RANK_PLURAL[score[2]]})"
    if tier == 1:
        return f"Pair of {_RANK_PLURAL[score[1]]}"
    return f"{_RANK_WORD[score[1]]}-high"


def _hand_name_2(score: tuple[int, ...]) -> str:
    if score[0] == 1:
        return f"Pair of {_RANK_PLURAL[score[1]]}"
    return f"{_RANK_WORD[score[1]]}-{_RANK_WORD[score[2]]}"


# ── House way (brute-force optimal split) ────────────────────────────────────

def _valid_setting(high: list[str], low: list[str]) -> bool:
    """High hand must rank at least as high as low hand."""
    h = _evaluate_5(high)
    lo = _evaluate_2(low)
    # Compare: 5-card tier >= 2-card tier is almost always true,
    # but check the foul case: low is a pair, high has no pair
    if lo[0] == 1 and h[0] == 0:
        return False
    return True


def _house_way(cards: list[str]) -> tuple[list[str], list[str]]:
    """Set 7 cards into (high_5, low_2) using optimal split."""
    best_score: tuple[tuple[int, ...], tuple[int, ...]] | None = None
    best_split: tuple[list[str], list[str]] | None = None

    for combo in itertools.combinations(range(7), 2):
        low_idx = set(combo)
        low = [cards[i] for i in combo]
        high = [cards[i] for i in range(7) if i not in low_idx]

        if not _valid_setting(high, low):
            continue

        lo_score = _evaluate_2(low)
        hi_score = _evaluate_5(high)
        score = (lo_score, hi_score)

        if best_score is None or score > best_score:
            best_score = score
            best_split = (high, low)

    # Fallback: should never happen with 7 cards, but just in case
    if best_split is None:
        return cards[:5], cards[5:]
    return best_split


# ── Fortune Bonus ────────────────────────────────────────────────────────────

FORTUNE_TABLE: list[tuple[int, int, str]] = [
    # (min_tier, payout_multiplier, label)
    (10, 400, "Five Aces"),
    (9, 150, "Royal Flush"),     # royal = tier 9, high 14
    (8, 50, "Straight Flush"),   # tier 8-9 but non-royal covered below
    (7, 25, "Four of a Kind"),
    (6, 5, "Full House"),
    (5, 4, "Flush"),
    (4, 2, "Straight"),
    (3, 3, "Three of a Kind"),
]


def _best_5_from_7(cards: list[str]) -> tuple[int, ...]:
    """Best possible 5-card hand from 7 cards."""
    best = (0,)
    for combo in itertools.combinations(range(7), 5):
        hand = [cards[i] for i in combo]
        score = _evaluate_5(hand)
        if score > best:
            best = score
    return best


def _fortune_payout(cards: list[str], bet: int) -> tuple[int, str]:
    """Returns (payout_amount, label). Payout is net win (0 if nothing)."""
    score = _best_5_from_7(cards)
    tier = score[0]

    # Royal flush special check (tier 9, high 14)
    if tier == 9 and score[1] == 14:
        return bet * 150, "Royal Flush"

    for min_tier, mult, label in FORTUNE_TABLE:
        if tier >= min_tier:
            return bet * mult, label

    return 0, ""


# ── Game state ───────────────────────────────────────────────────────────────

@dataclass
class PaiGowGame:
    user_id: int
    wager: int
    fortune_bet: int
    player_cards: list[str]   # 7 cards
    dealer_cards: list[str]   # 7 cards
    selected: list[int] = field(default_factory=list)  # indices for low hand (max 2)


# ── Embeds ───────────────────────────────────────────────────────────────────

def _sort_for_display(cards: list[str]) -> list[str]:
    """Sort cards by rank descending for display."""
    return sorted(cards, key=lambda c: _rank_val(c), reverse=True)


def _setting_embed(game: PaiGowGame) -> discord.Embed:
    """Embed during the hand-setting phase."""
    embed = discord.Embed(
        title="Pai Gow Poker \u2014 Set Your Hands",
        colour=discord.Colour.blurple(),
    )

    # Dealer's cards (open face — sorted for readability)
    dealer_sorted = _sort_for_display(game.dealer_cards)
    embed.add_field(
        name="\U0001f3e0 Dealer's Cards",
        value=_fmt_hand(dealer_sorted),
        inline=False,
    )

    # Player's cards
    embed.add_field(
        name="Your Cards",
        value=_fmt_hand(game.player_cards),
        inline=False,
    )

    # Show proposed split if 2 cards selected
    if len(game.selected) == 2:
        low = [game.player_cards[i] for i in game.selected]
        high = [game.player_cards[i] for i in range(7) if i not in game.selected]
        lo_score = _evaluate_2(low)
        hi_score = _evaluate_5(high)

        valid = _valid_setting(high, low)
        status = "" if valid else " \u26a0\ufe0f *foul*"

        embed.add_field(
            name="Low Hand (2 cards)",
            value=f"{_fmt_hand(low)}  \u2014 {_hand_name_2(lo_score)}{status}",
            inline=False,
        )
        embed.add_field(
            name="High Hand (5 cards)",
            value=f"{_fmt_hand(high)}  \u2014 {_hand_name_5(hi_score)}",
            inline=False,
        )
    else:
        picked = len(game.selected)
        embed.add_field(
            name="Low Hand",
            value=f"Pick {2 - picked} more card{'s' if 2 - picked > 1 else ''}",
            inline=True,
        )

    bet_line = f"{game.wager} coins"
    if game.fortune_bet > 0:
        bet_line += f" (+{game.fortune_bet} Fortune)"
    embed.add_field(name="Bet", value=bet_line, inline=True)

    embed.set_footer(text="Select 2 cards for your Low Hand, then Set Hands. Or click House Way.")
    return embed


def _result_embed(
    *,
    game: PaiGowGame,
    dealer_high: list[str],
    dealer_low: list[str],
    player_high: list[str],
    player_low: list[str],
    outcome: str,
    net_payout: int,
    fortune_win: int,
    fortune_label: str,
    new_balance: int,
) -> discord.Embed:
    d_hi = _evaluate_5(dealer_high)
    d_lo = _evaluate_2(dealer_low)
    p_hi = _evaluate_5(player_high)
    p_lo = _evaluate_2(player_low)

    # Per-hand results (ties go to dealer)
    hi_win = p_hi > d_hi
    lo_win = p_lo > d_lo

    hi_mark = "\u2705" if hi_win else "\u274c"
    lo_mark = "\u2705" if lo_win else "\u274c"

    colour = {
        "win": discord.Colour.green(),
        "lose": discord.Colour.red(),
        "push": discord.Colour.light_grey(),
    }.get(outcome, discord.Colour.blurple())

    result_label = {
        "win": "You Win!",
        "lose": "You Lose",
        "push": "Push \u2014 bet returned",
    }[outcome]

    embed = discord.Embed(
        title=f"Pai Gow Poker \u2014 {result_label}",
        colour=colour,
    )

    # Dealer hands
    embed.add_field(
        name="\U0001f3e0 Dealer",
        value=(
            f"High: {_fmt_hand(dealer_high)}  \u2014 {_hand_name_5(d_hi)}\n"
            f"Low: {_fmt_hand(dealer_low)}  \u2014 {_hand_name_2(d_lo)}"
        ),
        inline=False,
    )

    # Player hands
    embed.add_field(
        name="\U0001f464 You",
        value=(
            f"High: {_fmt_hand(player_high)}  \u2014 {_hand_name_5(p_hi)}  {hi_mark}\n"
            f"Low: {_fmt_hand(player_low)}  \u2014 {_hand_name_2(p_lo)}  {lo_mark}"
        ),
        inline=False,
    )

    if hi_win and lo_win:
        summary = "Win both hands!"
    elif not hi_win and not lo_win:
        # Check if dealer ace-high push
        if outcome == "push" and d_hi[0] == 0 and d_hi[1] == 14:
            summary = "Dealer ace-high \u2014 push!"
        else:
            summary = "Dealer wins both hands."
    else:
        summary = "Split \u2014 one hand each."
    embed.add_field(name="Result", value=summary, inline=True)

    # Payout
    sign = "+" if net_payout > 0 else ""
    payout_text = f"{sign}{net_payout} coins"
    embed.add_field(name="Payout", value=payout_text, inline=True)

    # Fortune bonus
    if game.fortune_bet > 0:
        if fortune_win > 0:
            embed.add_field(
                name="\U0001f3b0 Fortune Bonus",
                value=f"{fortune_label}! **+{fortune_win}** coins",
                inline=False,
            )
        else:
            embed.add_field(
                name="\U0001f3b0 Fortune Bonus",
                value=f"No qualifying hand \u2014 -{game.fortune_bet} coins",
                inline=False,
            )

    embed.add_field(name="Balance", value=f"{new_balance} coins", inline=True)
    return embed


# ── View ─────────────────────────────────────────────────────────────────────

class CardButton(ui.Button["PaiGowView"]):
    def __init__(self, card: str, index: int, selected: bool, row: int) -> None:
        label = "\U0001f0cf" if _is_joker(card) else card
        style = discord.ButtonStyle.primary if selected else discord.ButtonStyle.secondary
        super().__init__(label=label, style=style, row=row)
        self.index = index

    async def callback(self, interaction: discord.Interaction) -> None:
        view: PaiGowView = self.view  # type: ignore[assignment]
        game = view.game

        if interaction.user.id != game.user_id:
            await interaction.response.send_message("Not your game!", ephemeral=True)
            return

        if self.index in game.selected:
            game.selected.remove(self.index)
        elif len(game.selected) < 2:
            game.selected.append(self.index)
        else:
            # Already 2 selected — swap out the oldest
            game.selected.pop(0)
            game.selected.append(self.index)

        view.rebuild()
        embed = _setting_embed(game)
        await interaction.response.edit_message(embed=embed, view=view)


class PaiGowView(ui.View):
    def __init__(
        self, game: PaiGowGame, active_games: dict[int, "PaiGowGame"],
    ) -> None:
        super().__init__(timeout=180)
        self.game = game
        self.active_games = active_games
        self._add_card_buttons()
        self._add_action_buttons()

    def _add_card_buttons(self) -> None:
        for i, card in enumerate(self.game.player_cards):
            selected = i in self.game.selected
            row = 0 if i < 5 else 1
            self.add_item(CardButton(card, i, selected, row))

    def _add_action_buttons(self) -> None:
        can_set = len(self.game.selected) == 2
        # Validate foul if 2 selected
        if can_set:
            low = [self.game.player_cards[i] for i in self.game.selected]
            high = [self.game.player_cards[i] for i in range(7) if i not in self.game.selected]
            if not _valid_setting(high, low):
                can_set = False

        set_btn = ui.Button(
            label="Set Hands", style=discord.ButtonStyle.success,
            emoji="\u2705", row=2, disabled=not can_set,
        )
        set_btn.callback = self._set_hands
        self.add_item(set_btn)

        hw_btn = ui.Button(
            label="House Way", style=discord.ButtonStyle.secondary,
            emoji="\U0001f3e0", row=2,
        )
        hw_btn.callback = self._house_way
        self.add_item(hw_btn)

    def rebuild(self) -> None:
        """Rebuild all buttons to reflect current selection state."""
        self.clear_items()
        self._add_card_buttons()
        self._add_action_buttons()

    async def _set_hands(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.game.user_id:
            await interaction.response.send_message("Not your game!", ephemeral=True)
            return
        if len(self.game.selected) != 2:
            await interaction.response.send_message("Select exactly 2 cards for your low hand.", ephemeral=True)
            return

        player_low = [self.game.player_cards[i] for i in self.game.selected]
        player_high = [self.game.player_cards[i] for i in range(7) if i not in self.game.selected]

        if not _valid_setting(player_high, player_low):
            await interaction.response.send_message(
                "Foul! Your high hand must rank higher than your low hand. Re-pick.",
                ephemeral=True,
            )
            return

        await self._resolve(interaction, player_high, player_low)

    async def _house_way(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.game.user_id:
            await interaction.response.send_message("Not your game!", ephemeral=True)
            return
        player_high, player_low = _house_way(self.game.player_cards)
        await self._resolve(interaction, player_high, player_low)

    async def _resolve(
        self,
        interaction: discord.Interaction,
        player_high: list[str],
        player_low: list[str],
    ) -> None:
        game = self.game

        # Dealer sets house way
        dealer_high, dealer_low = _house_way(game.dealer_cards)

        # Compare
        p_hi = _evaluate_5(player_high)
        p_lo = _evaluate_2(player_low)
        d_hi = _evaluate_5(dealer_high)
        d_lo = _evaluate_2(dealer_low)

        hi_win = p_hi > d_hi  # tie goes to dealer
        lo_win = p_lo > d_lo

        if hi_win and lo_win:
            outcome = "win"
            net_payout = game.wager
        elif not hi_win and not lo_win:
            # No-commission rule: dealer ace-high (no pair) = push
            if d_hi[0] == 0 and d_hi[1] == 14:
                outcome = "push"
                net_payout = 0
            else:
                outcome = "lose"
                net_payout = -game.wager
        else:
            outcome = "push"
            net_payout = 0

        # Fortune bonus
        fortune_win = 0
        fortune_label = ""
        if game.fortune_bet > 0:
            fortune_win, fortune_label = _fortune_payout(game.player_cards, game.fortune_bet)

        # Credit winnings
        credit = 0
        if outcome == "win":
            credit = game.wager * 2  # original bet + 1:1 win
        elif outcome == "push":
            credit = game.wager  # return bet
        # loss = 0 credit (already deducted)

        credit += fortune_win
        if credit > 0:
            new_balance = await queries.update_casino_balance(str(game.user_id), credit)
        else:
            new_balance = (await queries.get_casino_balance(str(game.user_id))) or 0

        # Sort hands for display (high cards first)
        player_high = _sort_for_display(player_high)
        player_low = _sort_for_display(player_low)
        dealer_high = _sort_for_display(dealer_high)
        dealer_low = _sort_for_display(dealer_low)

        total_net = net_payout + (fortune_win - game.fortune_bet if game.fortune_bet else 0)
        embed = _result_embed(
            game=game,
            dealer_high=dealer_high,
            dealer_low=dealer_low,
            player_high=player_high,
            player_low=player_low,
            outcome=outcome,
            net_payout=total_net,
            fortune_win=fortune_win,
            fortune_label=fortune_label,
            new_balance=new_balance,
        )

        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_games.pop(game.user_id, None)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        game = self.game
        # Refund wager + fortune on timeout
        refund = game.wager + game.fortune_bet
        try:
            await queries.update_casino_balance(str(game.user_id), refund)
        except Exception:
            pass
        self.active_games.pop(game.user_id, None)


# ── Cog ──────────────────────────────────────────────────────────────────────

class PaiGowCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_games: dict[int, PaiGowGame] = {}

    @app_commands.command(name="paigow", description="Play a hand of Pai Gow Poker")
    @app_commands.describe(
        bet="Number of coins to wager",
        fortune="Optional Fortune Bonus side bet",
    )
    async def paigow(
        self,
        interaction: discord.Interaction,
        bet: int,
        fortune: int = 0,
    ) -> None:
        user_id = interaction.user.id

        if user_id in self.active_games:
            await interaction.response.send_message(
                "Finish your current game first!", ephemeral=True,
            )
            return

        if bet < 1:
            await interaction.response.send_message(
                "Bet must be at least 1 coin.", ephemeral=True,
            )
            return

        if fortune < 0:
            await interaction.response.send_message(
                "Fortune bet can't be negative.", ephemeral=True,
            )
            return

        total_cost = bet + fortune
        balance = await queries.get_or_create_casino_wallet(str(user_id))
        if total_cost > balance:
            await interaction.response.send_message(
                f"You only have **{balance}** casino coins.", ephemeral=True,
            )
            return

        await queries.update_casino_balance(str(user_id), -total_cost)

        # Deal
        deck = _new_deck()
        player_cards = [deck.pop() for _ in range(7)]
        dealer_cards = [deck.pop() for _ in range(7)]

        game = PaiGowGame(
            user_id=user_id,
            wager=bet,
            fortune_bet=fortune,
            player_cards=player_cards,
            dealer_cards=dealer_cards,
        )
        self.active_games[user_id] = game

        view = PaiGowView(game, self.active_games)
        embed = _setting_embed(game)
        await interaction.response.send_message(embed=embed, view=view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PaiGowCog(bot))
