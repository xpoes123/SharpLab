"""Casino cog — /blackjack and /balance commands with a virtual coin economy."""
import random
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries

# ── Card helpers ──────────────────────────────────────────────────────────────

SUITS = ("♠", "♥", "♦", "♣")
RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
RANK_VALUES = {
    "A": 11, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7,
    "8": 8, "9": 9, "10": 10, "J": 10, "Q": 10, "K": 10,
}
SHOE_DECKS = 6
RESHUFFLE_THRESHOLD = 60


def _new_shoe() -> list[str]:
    """Create and shuffle a 6-deck shoe."""
    cards = [f"{r}{s}" for s in SUITS for r in RANKS] * SHOE_DECKS
    random.shuffle(cards)
    return cards


def _hand_value(hand: list[str]) -> int:
    """Best blackjack value for a hand (aces count 11 or 1)."""
    total = 0
    aces = 0
    for card in hand:
        rank = card[:-1]  # strip suit symbol
        total += RANK_VALUES[rank]
        if rank == "A":
            aces += 1
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def _fmt_card(card: str) -> str:
    """Format a card for display: `A♠`."""
    return f"`{card}`"


def _fmt_hand(hand: list[str]) -> str:
    return " ".join(_fmt_card(c) for c in hand)


def _is_blackjack(hand: list[str]) -> bool:
    return len(hand) == 2 and _hand_value(hand) == 21


# ── Game state ────────────────────────────────────────────────────────────────

@dataclass
class BlackjackGame:
    user_id: int
    bet: int
    shoe: list[str]
    player_hand: list[str] = field(default_factory=list)
    dealer_hand: list[str] = field(default_factory=list)

    def deal_initial(self) -> None:
        self.player_hand = [self.shoe.pop(), self.shoe.pop()]
        self.dealer_hand = [self.shoe.pop(), self.shoe.pop()]

    def hit_player(self) -> str:
        card = self.shoe.pop()
        self.player_hand.append(card)
        return card

    def play_dealer(self) -> None:
        """Dealer hits until 17+."""
        while _hand_value(self.dealer_hand) < 17:
            self.dealer_hand.append(self.shoe.pop())


# ── Embeds ────────────────────────────────────────────────────────────────────

def _game_embed(
    game: BlackjackGame,
    *,
    reveal: bool = False,
    outcome: str | None = None,
    payout: int = 0,
    new_balance: int = 0,
) -> discord.Embed:
    pval = _hand_value(game.player_hand)
    dval = _hand_value(game.dealer_hand) if reveal else RANK_VALUES[game.dealer_hand[0][:-1]]

    if outcome:
        colour = {
            "Blackjack!": discord.Colour.gold(),
            "You win!": discord.Colour.green(),
            "Push": discord.Colour.light_grey(),
            "Bust!": discord.Colour.red(),
            "Dealer wins": discord.Colour.red(),
        }.get(outcome, discord.Colour.blurple())
        title = f"Blackjack — {outcome}"
    else:
        colour = discord.Colour.blurple()
        title = "Blackjack"

    embed = discord.Embed(title=title, colour=colour)

    # Dealer hand
    if reveal:
        dealer_str = f"{_fmt_hand(game.dealer_hand)}  ({dval})"
    else:
        dealer_str = f"{_fmt_card(game.dealer_hand[0])} `??`"
    embed.add_field(name="Dealer", value=dealer_str, inline=False)

    # Player hand
    embed.add_field(
        name="Your Hand",
        value=f"{_fmt_hand(game.player_hand)}  ({pval})",
        inline=False,
    )

    embed.add_field(name="Bet", value=f"{game.bet} coins", inline=True)

    if outcome:
        sign = "+" if payout > 0 else ""
        embed.add_field(name="Payout", value=f"{sign}{payout} coins", inline=True)
        embed.add_field(name="Balance", value=f"{new_balance} coins", inline=True)

    return embed


# ── Button view ───────────────────────────────────────────────────────────────

class BlackjackView(ui.View):
    def __init__(self, game: BlackjackGame, active_games: dict[int, "BlackjackGame"]) -> None:
        super().__init__(timeout=120)
        self.game = game
        self.active_games = active_games
        self._update_buttons()

    def _update_buttons(self) -> None:
        pval = _hand_value(self.game.player_hand)
        can_double = len(self.game.player_hand) == 2 and pval < 21
        self.double_btn.disabled = not can_double

    async def _finish(
        self, interaction: discord.Interaction, outcome: str, payout: int
    ) -> None:
        """Resolve the game, pay out, clean up."""
        new_balance = 0
        if payout != 0:
            new_balance = await queries.update_casino_balance(
                str(self.game.user_id), payout
            )
        else:
            bal = await queries.get_casino_balance(str(self.game.user_id))
            new_balance = bal or 0

        embed = _game_embed(
            self.game,
            reveal=True,
            outcome=outcome,
            payout=payout,
            new_balance=new_balance,
        )
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_games.pop(self.game.user_id, None)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _check_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.game.user_id:
            await interaction.response.send_message(
                "This isn't your game!", ephemeral=True
            )
            return False
        return True

    @ui.button(label="Hit", style=discord.ButtonStyle.primary, emoji="👊")
    async def hit_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if not await self._check_owner(interaction):
            return
        self.game.hit_player()
        pval = _hand_value(self.game.player_hand)
        if pval > 21:
            await self._finish(interaction, "Bust!", 0)
        elif pval == 21:
            # Auto-stand on 21
            await self._stand(interaction)
        else:
            self._update_buttons()
            embed = _game_embed(self.game)
            await interaction.response.edit_message(embed=embed, view=self)

    @ui.button(label="Stand", style=discord.ButtonStyle.secondary, emoji="✋")
    async def stand_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if not await self._check_owner(interaction):
            return
        await self._stand(interaction)

    @ui.button(label="Double Down", style=discord.ButtonStyle.success, emoji="💰")
    async def double_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if not await self._check_owner(interaction):
            return
        # Try to deduct the extra bet
        try:
            await queries.update_casino_balance(str(self.game.user_id), -self.game.bet)
        except ValueError:
            await interaction.response.send_message(
                "Not enough coins to double down!", ephemeral=True
            )
            return
        self.game.bet *= 2
        self.game.hit_player()
        pval = _hand_value(self.game.player_hand)
        if pval > 21:
            await self._finish(interaction, "Bust!", 0)
        else:
            await self._stand(interaction)

    async def _stand(self, interaction: discord.Interaction) -> None:
        game = self.game
        game.play_dealer()
        pval = _hand_value(game.player_hand)
        dval = _hand_value(game.dealer_hand)

        if _is_blackjack(game.player_hand) and not _is_blackjack(game.dealer_hand):
            payout = game.bet + (game.bet * 3 // 2)  # 3:2
            await self._finish(interaction, "Blackjack!", payout)
        elif dval > 21:
            await self._finish(interaction, "You win!", game.bet * 2)
        elif pval > dval:
            await self._finish(interaction, "You win!", game.bet * 2)
        elif pval == dval:
            await self._finish(interaction, "Push", game.bet)  # return bet
        else:
            await self._finish(interaction, "Dealer wins", 0)

    async def on_timeout(self) -> None:
        self.active_games.pop(self.game.user_id, None)


# ── Cog ───────────────────────────────────────────────────────────────────────

class CasinoCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_games: dict[int, BlackjackGame] = {}
        self.shoe = _new_shoe()

    def _draw_shoe(self) -> list[str]:
        """Return the shared shoe, reshuffling if low."""
        if len(self.shoe) < RESHUFFLE_THRESHOLD:
            self.shoe = _new_shoe()
        return self.shoe

    @app_commands.command(name="blackjack", description="Play a hand of blackjack")
    @app_commands.describe(bet="Number of coins to wager")
    async def blackjack(self, interaction: discord.Interaction, bet: int) -> None:
        user_id = interaction.user.id

        if user_id in self.active_games:
            await interaction.response.send_message(
                "You already have a game in progress! Finish it first.",
                ephemeral=True,
            )
            return

        if bet < 1:
            await interaction.response.send_message(
                "Bet must be at least 1 coin.", ephemeral=True
            )
            return

        balance = await queries.get_or_create_casino_wallet(str(user_id))

        if bet > balance:
            await interaction.response.send_message(
                f"You only have **{balance}** casino coins.", ephemeral=True,
            )
            return

        # Deduct bet
        await queries.update_casino_balance(str(user_id), -bet)

        # Deal
        shoe = self._draw_shoe()
        game = BlackjackGame(user_id=user_id, bet=bet, shoe=shoe)
        game.deal_initial()
        self.active_games[user_id] = game

        # Check for instant blackjack
        if _is_blackjack(game.player_hand):
            payout = bet + (bet * 3 // 2)  # 3:2
            if _is_blackjack(game.dealer_hand):
                # Both blackjack — push
                new_balance = await queries.update_casino_balance(str(user_id), bet)
                embed = _game_embed(
                    game, reveal=True, outcome="Push", payout=bet, new_balance=new_balance
                )
            else:
                new_balance = await queries.update_casino_balance(str(user_id), payout)
                embed = _game_embed(
                    game, reveal=True, outcome="Blackjack!", payout=payout, new_balance=new_balance
                )
            self.active_games.pop(user_id, None)
            await interaction.response.send_message(embed=embed)
            return

        # Check if dealer has blackjack
        if _is_blackjack(game.dealer_hand):
            embed = _game_embed(
                game, reveal=True, outcome="Dealer wins", payout=0,
                new_balance=(await queries.get_casino_balance(str(user_id))) or 0,
            )
            self.active_games.pop(user_id, None)
            await interaction.response.send_message(embed=embed)
            return

        # Normal play
        view = BlackjackView(game, self.active_games)
        embed = _game_embed(game)
        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(name="balance", description="Check your coin balance")
    @app_commands.describe(user="Check another user's balance (optional)")
    async def balance(
        self, interaction: discord.Interaction, user: discord.User | None = None
    ) -> None:
        target = user or interaction.user
        is_self = target.id == interaction.user.id

        if is_self:
            bal = await queries.get_or_create_casino_wallet(str(target.id))
            msg = f"**{target.display_name}** has **{bal}** casino coins."
        else:
            bal = await queries.get_casino_balance(str(target.id))
            if bal is None:
                msg = f"**{target.display_name}** hasn't played yet."
            else:
                msg = f"**{target.display_name}** has **{bal}** casino coins."

        await interaction.response.send_message(msg)

    @app_commands.command(name="give-coins", description="Give casino coins to a user (admin)")
    @app_commands.describe(user="User to give coins to", amount="Number of coins to give")
    async def give_coins(
        self, interaction: discord.Interaction, user: discord.User, amount: int,
    ) -> None:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Admin only.", ephemeral=True)
            return
        if amount < 1:
            await interaction.response.send_message("Amount must be at least 1.", ephemeral=True)
            return
        new_balance = await queries.give_casino_coins(str(user.id), amount)
        await interaction.response.send_message(
            f"Gave **{amount}** casino coins to **{user.display_name}**. "
            f"Their balance: **{new_balance}** coins."
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CasinoCog(bot))
