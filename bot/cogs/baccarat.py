"""Baccarat cog — /baccarat with interactive card peeling."""
import random
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries

# ── Card helpers ──────────────────────────────────────────────────────────────

SUITS = ("♠", "♥", "♦", "♣")
RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
BACCARAT_VALUES = {
    "A": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7,
    "8": 8, "9": 9, "10": 0, "J": 0, "Q": 0, "K": 0,
}
SHOE_DECKS = 8
RESHUFFLE_THRESHOLD = 80
CARD_BACK = "🂠"


def _new_shoe() -> list[str]:
    """Create and shuffle an 8-deck shoe."""
    cards = [f"{r}{s}" for s in SUITS for r in RANKS] * SHOE_DECKS
    random.shuffle(cards)
    return cards


def _card_value(card: str) -> int:
    rank = card[:-1]
    return BACCARAT_VALUES[rank]


def _hand_value(hand: list[str]) -> int:
    return sum(_card_value(c) for c in hand) % 10


def _fmt_card(card: str) -> str:
    return f"`{card}`"


def _fmt_hand(hand: list[str]) -> str:
    return " ".join(_fmt_card(c) for c in hand)


# ── Dealing logic ────────────────────────────────────────────────────────────

def _play_hand(shoe: list[str]) -> tuple[list[str], list[str]]:
    """Deal a full baccarat hand. Returns (player_hand, banker_hand)."""
    player = [shoe.pop(), shoe.pop()]
    banker = [shoe.pop(), shoe.pop()]

    p_val = _hand_value(player)
    b_val = _hand_value(banker)

    # Natural — no more cards
    if p_val >= 8 or b_val >= 8:
        return player, banker

    # Player third-card rule
    player_drew = False
    player_third_value = -1

    if p_val <= 5:
        third = shoe.pop()
        player.append(third)
        player_drew = True
        player_third_value = _card_value(third)

    # Banker third-card rule
    b_val = _hand_value(banker)

    if not player_drew:
        if b_val <= 5:
            banker.append(shoe.pop())
    else:
        if b_val <= 2:
            banker.append(shoe.pop())
        elif b_val == 3:
            if player_third_value != 8:
                banker.append(shoe.pop())
        elif b_val == 4:
            if player_third_value in (2, 3, 4, 5, 6, 7):
                banker.append(shoe.pop())
        elif b_val == 5:
            if player_third_value in (4, 5, 6, 7):
                banker.append(shoe.pop())
        elif b_val == 6:
            if player_third_value in (6, 7):
                banker.append(shoe.pop())

    return player, banker


# ── Game state ───────────────────────────────────────────────────────────────

def _build_reveal_seq(
    player_hand: list[str], banker_hand: list[str],
) -> list[tuple[str, int]]:
    """Build the card reveal order: P1, P2, B1, B2, then third cards."""
    seq: list[tuple[str, int]] = [
        ("player", 0), ("player", 1), ("banker", 0), ("banker", 1),
    ]
    if len(player_hand) == 3:
        seq.append(("player", 2))
    if len(banker_hand) == 3:
        seq.append(("banker", 2))
    return seq


@dataclass
class BaccaratGame:
    user_id: int
    wager: int
    bet_type: str  # "player" | "banker" | "tie"
    player_hand: list[str]
    banker_hand: list[str]
    reveal_seq: list[tuple[str, int]] = field(default_factory=list)
    reveal_pos: int = 0

    @property
    def player_revealed(self) -> int:
        return sum(1 for h, _ in self.reveal_seq[:self.reveal_pos] if h == "player")

    @property
    def banker_revealed(self) -> int:
        return sum(1 for h, _ in self.reveal_seq[:self.reveal_pos] if h == "banker")

    @property
    def all_revealed(self) -> bool:
        return self.reveal_pos >= len(self.reveal_seq)

    @property
    def initial_done(self) -> bool:
        """First 4 cards (2 per side) all revealed."""
        return self.reveal_pos >= 4


# ── Embeds ───────────────────────────────────────────────────────────────────

BET_CHOICES = [
    app_commands.Choice(name="Player", value="player"),
    app_commands.Choice(name="Banker", value="banker"),
    app_commands.Choice(name="Tie", value="tie"),
]


def _display_hand(hand: list[str], revealed: int, show_all: bool) -> str:
    """Format a hand with revealed cards and face-down backs."""
    positions = len(hand) if show_all else min(2, len(hand))
    parts = []
    for i in range(positions):
        if i < revealed:
            parts.append(_fmt_card(hand[i]))
        else:
            parts.append(CARD_BACK)
    display = " ".join(parts)
    if revealed >= 2:
        display += f"  ({_hand_value(hand[:revealed])})"
    return display


def _peel_embed(game: BaccaratGame) -> discord.Embed:
    """Embed for the peeling phase — partially revealed cards."""
    embed = discord.Embed(
        title="Baccarat \u2014 Peel your cards!",
        colour=discord.Colour.blurple(),
    )

    p_str = _display_hand(game.player_hand, game.player_revealed, game.initial_done)
    b_str = _display_hand(game.banker_hand, game.banker_revealed, game.initial_done)

    embed.add_field(name="Player", value=p_str, inline=False)
    embed.add_field(name="Banker", value=b_str, inline=False)
    embed.add_field(
        name="Your Bet",
        value=f"{game.bet_type.capitalize()} \u2014 {game.wager} coins",
        inline=True,
    )

    if not game.all_revealed:
        cards_left = len(game.reveal_seq) - game.reveal_pos
        next_hand, _ = game.reveal_seq[game.reveal_pos]
        if cards_left == 1:
            embed.set_footer(text=f"\U0001f941 Final card: {next_hand.capitalize()}")
        else:
            embed.set_footer(
                text=f"Next: {next_hand.capitalize()} \u2022 {cards_left} cards left",
            )

    return embed


def _result_embed(
    *,
    player_hand: list[str],
    banker_hand: list[str],
    bet_type: str,
    wager: int,
    outcome: str,
    payout: int,
    new_balance: int,
) -> discord.Embed:
    p_val = _hand_value(player_hand)
    b_val = _hand_value(banker_hand)

    if p_val > b_val:
        winner = "Player wins!"
    elif b_val > p_val:
        winner = "Banker wins!"
    else:
        winner = "Tie!"

    colour = {
        "win": discord.Colour.green(),
        "lose": discord.Colour.red(),
        "push": discord.Colour.light_grey(),
    }.get(outcome, discord.Colour.blurple())

    result_label = {
        "win": "You win!",
        "lose": "You lose",
        "push": "Push \u2014 bet returned",
    }[outcome]

    embed = discord.Embed(title=f"Baccarat \u2014 {result_label}", colour=colour)

    p_third = " *(drew third)*" if len(player_hand) == 3 else ""
    embed.add_field(
        name="Player",
        value=f"{_fmt_hand(player_hand)}  ({p_val}){p_third}",
        inline=False,
    )

    b_third = " *(drew third)*" if len(banker_hand) == 3 else ""
    embed.add_field(
        name="Banker",
        value=f"{_fmt_hand(banker_hand)}  ({b_val}){b_third}",
        inline=False,
    )

    if len(player_hand) == 2 and p_val >= 8:
        embed.add_field(name="", value=f"Player natural **{p_val}**!", inline=False)
    if len(banker_hand) == 2 and b_val >= 8:
        embed.add_field(name="", value=f"Banker natural **{b_val}**!", inline=False)

    embed.add_field(name="Result", value=winner, inline=True)
    embed.add_field(
        name="Your Bet",
        value=f"{bet_type.capitalize()} \u2014 {wager} coins",
        inline=True,
    )

    sign = "+" if payout > 0 else ""
    embed.add_field(name="Payout", value=f"{sign}{payout} coins", inline=True)
    embed.add_field(name="Balance", value=f"{new_balance} coins", inline=True)

    return embed


# ── View ─────────────────────────────────────────────────────────────────────

class BaccaratView(ui.View):
    def __init__(
        self, game: BaccaratGame, active_games: dict[int, "BaccaratGame"],
    ) -> None:
        super().__init__(timeout=120)
        self.game = game
        self.active_games = active_games

    async def _check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.game.user_id:
            await interaction.response.send_message("Not your game!", ephemeral=True)
            return False
        return True

    async def _finish(self, interaction: discord.Interaction) -> None:
        game = self.game
        p_val = _hand_value(game.player_hand)
        b_val = _hand_value(game.banker_hand)

        if p_val > b_val:
            winner = "player"
        elif b_val > p_val:
            winner = "banker"
        else:
            winner = "tie"

        # Calculate payout
        if game.bet_type == "tie":
            if winner == "tie":
                payout = game.wager + 8 * game.wager
                outcome = "win"
            else:
                payout = 0
                outcome = "lose"
        elif game.bet_type == winner:
            if game.bet_type == "banker":
                commission = game.wager * 5 // 100
                payout = game.wager + (game.wager - commission)
            else:
                payout = game.wager * 2
            outcome = "win"
        elif winner == "tie":
            payout = game.wager
            outcome = "push"
        else:
            payout = 0
            outcome = "lose"

        if payout > 0:
            new_balance = await queries.update_casino_balance(str(game.user_id), payout)
        else:
            new_balance = (await queries.get_casino_balance(str(game.user_id))) or 0

        embed = _result_embed(
            player_hand=game.player_hand,
            banker_hand=game.banker_hand,
            bet_type=game.bet_type,
            wager=game.wager,
            outcome=outcome,
            payout=payout - game.wager,
            new_balance=new_balance,
        )

        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_games.pop(game.user_id, None)
        await interaction.response.edit_message(embed=embed, view=self)

    @ui.button(label="Peel", style=discord.ButtonStyle.primary, emoji="\U0001f0cf")
    async def peel_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if not await self._check(interaction):
            return
        self.game.reveal_pos += 1
        if self.game.all_revealed:
            await self._finish(interaction)
        else:
            await interaction.response.edit_message(
                embed=_peel_embed(self.game), view=self,
            )

    @ui.button(label="Reveal All", style=discord.ButtonStyle.secondary, emoji="\U0001f441")
    async def reveal_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if not await self._check(interaction):
            return
        self.game.reveal_pos = len(self.game.reveal_seq)
        await self._finish(interaction)

    async def on_timeout(self) -> None:
        game = self.game
        if not game.all_revealed:
            try:
                await queries.update_casino_balance(str(game.user_id), game.wager)
            except Exception:
                pass
        self.active_games.pop(game.user_id, None)


# ── Cog ──────────────────────────────────────────────────────────────────────

class BaccaratCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.shoe = _new_shoe()
        self.active_games: dict[int, BaccaratGame] = {}

    def _ensure_shoe(self) -> list[str]:
        if len(self.shoe) < RESHUFFLE_THRESHOLD:
            self.shoe = _new_shoe()
        return self.shoe

    @app_commands.command(name="baccarat", description="Play a hand of baccarat")
    @app_commands.describe(
        bet="Player, Banker, or Tie",
        wager="Number of coins to wager",
    )
    @app_commands.choices(bet=BET_CHOICES)
    async def baccarat(
        self,
        interaction: discord.Interaction,
        bet: app_commands.Choice[str],
        wager: int,
    ) -> None:
        user_id = interaction.user.id
        bet_type = bet.value

        if user_id in self.active_games:
            await interaction.response.send_message(
                "Finish your current game first!", ephemeral=True,
            )
            return

        if wager < 1:
            await interaction.response.send_message(
                "Wager must be at least 1 coin.", ephemeral=True,
            )
            return

        balance = await queries.get_or_create_casino_wallet(str(user_id))
        if wager > balance:
            await interaction.response.send_message(
                f"You only have **{balance}** casino coins.", ephemeral=True,
            )
            return

        await queries.update_casino_balance(str(user_id), -wager)

        shoe = self._ensure_shoe()
        player_hand, banker_hand = _play_hand(shoe)

        game = BaccaratGame(
            user_id=user_id,
            wager=wager,
            bet_type=bet_type,
            player_hand=player_hand,
            banker_hand=banker_hand,
            reveal_seq=_build_reveal_seq(player_hand, banker_hand),
        )
        self.active_games[user_id] = game

        view = BaccaratView(game, self.active_games)
        embed = _peel_embed(game)
        await interaction.response.send_message(embed=embed, view=view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BaccaratCog(bot))
