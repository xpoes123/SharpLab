"""Craps cog — /craps command with interactive button-based gameplay."""
import random
from dataclasses import dataclass, field
from enum import Enum

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries

# ── Dice helpers ─────────────────────────────────────────────────────────────

DICE_EMOJI = {1: "\u2680", 2: "\u2681", 3: "\u2682", 4: "\u2683", 5: "\u2684", 6: "\u2685"}

# True odds payouts for odds bets (point -> (numerator, denominator))
TRUE_ODDS = {
    4: (2, 1),
    5: (3, 2),
    6: (6, 5),
    8: (6, 5),
    9: (3, 2),
    10: (2, 1),
}

MAX_ODDS_MULTIPLIER = 3  # max odds bet = 3x the pass/don't pass bet


def _roll_dice() -> tuple[int, int]:
    """Roll two six-sided dice."""
    return random.randint(1, 6), random.randint(1, 6)


def _fmt_dice(d1: int, d2: int) -> str:
    """Format two dice as emoji with their total."""
    return f"{DICE_EMOJI[d1]} {DICE_EMOJI[d2]}  **({d1 + d2})**"


# ── Game state ───────────────────────────────────────────────────────────────

class BetType(Enum):
    PASS_LINE = "Pass Line"
    DONT_PASS = "Don't Pass"


class Phase(Enum):
    COME_OUT = "come_out"
    POINT = "point"
    FINISHED = "finished"


@dataclass
class CrapsGame:
    user_id: int
    bet: int
    bet_type: BetType
    phase: Phase = Phase.COME_OUT
    point: int = 0
    odds_bet: int = 0
    roll_history: list[str] = field(default_factory=list)
    outcome: str = ""
    payout: int = 0


# ── Payout logic ─────────────────────────────────────────────────────────────

def _compute_pass_odds_payout(odds_bet: int, point: int) -> int:
    """Compute the winnings (profit only) for a pass-line odds bet."""
    num, den = TRUE_ODDS[point]
    return odds_bet * num // den


def _compute_dont_pass_odds_payout(odds_bet: int, point: int) -> int:
    """Compute the winnings (profit only) for a don't-pass lay-odds bet.

    Lay odds: you risk more to win less — the inverse of true odds.
    For point 4/10 at 2:1 true odds, lay pays 1:2 (risk 2 to win 1).
    """
    num, den = TRUE_ODDS[point]
    # Inverse: pay den:num
    return odds_bet * den // num


# ── Embed builder ────────────────────────────────────────────────────────────

def _game_embed(
    game: CrapsGame,
    *,
    latest_roll: str | None = None,
    new_balance: int = 0,
) -> discord.Embed:
    finished = game.phase == Phase.FINISHED

    if finished:
        if game.payout > 0:
            colour = discord.Colour.green()
        elif game.payout == 0 and "Push" in game.outcome:
            colour = discord.Colour.light_grey()
        else:
            colour = discord.Colour.red()
        title = f"Craps — {game.outcome}"
    else:
        colour = discord.Colour.blurple()
        if game.phase == Phase.COME_OUT:
            title = "Craps — Come-Out Roll"
        else:
            title = f"Craps — Point is {game.point}"

    embed = discord.Embed(title=title, colour=colour)

    # Bet info
    bet_info = f"{game.bet_type.value}: **{game.bet}** coins"
    if game.odds_bet > 0:
        bet_info += f"\nOdds bet: **{game.odds_bet}** coins"
    embed.add_field(name="Bets", value=bet_info, inline=True)

    if game.point > 0:
        embed.add_field(name="Point", value=f"**{game.point}**", inline=True)

    # Roll history
    if game.roll_history:
        # Show last 10 rolls to avoid embed overflow
        history = game.roll_history[-10:]
        if len(game.roll_history) > 10:
            history = ["..."] + history
        embed.add_field(name="Roll History", value="\n".join(history), inline=False)

    if finished:
        total_wagered = game.bet + game.odds_bet
        net = game.payout - total_wagered
        sign = "+" if net > 0 else ""
        embed.add_field(name="Payout", value=f"{sign}{net} coins", inline=True)
        embed.add_field(name="Balance", value=f"{new_balance} coins", inline=True)

    return embed


# ── Odds bet modal ───────────────────────────────────────────────────────────

class OddsBetModal(ui.Modal, title="Place Odds Bet"):
    amount = ui.TextInput(
        label="Odds bet amount (coins)",
        placeholder="e.g. 50",
        required=True,
        max_length=10,
    )

    def __init__(self, game: CrapsGame, view: "CrapsView") -> None:
        super().__init__()
        max_odds = game.bet * MAX_ODDS_MULTIPLIER
        self.amount.label = f"Odds bet (max {max_odds} coins)"
        self.game = game
        self.craps_view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        game = self.game
        try:
            amount = int(self.amount.value)
        except ValueError:
            await interaction.response.send_message(
                "Enter a whole number.", ephemeral=True
            )
            return

        max_odds = game.bet * MAX_ODDS_MULTIPLIER
        if amount < 1:
            await interaction.response.send_message(
                "Odds bet must be at least 1 coin.", ephemeral=True
            )
            return
        if amount > max_odds:
            await interaction.response.send_message(
                f"Max odds bet is **{max_odds}** coins (3x your {game.bet_type.value} bet).",
                ephemeral=True,
            )
            return

        # Deduct from wallet
        try:
            await queries.update_balance(str(game.user_id), -amount)
        except ValueError:
            await interaction.response.send_message(
                "Not enough coins for that odds bet!", ephemeral=True
            )
            return

        game.odds_bet = amount

        # Disable the odds button now that odds are placed
        self.craps_view.odds_btn.disabled = True
        self.craps_view.odds_btn.label = f"Odds: {amount}"

        embed = _game_embed(game)
        await interaction.response.edit_message(embed=embed, view=self.craps_view)


# ── Button views ─────────────────────────────────────────────────────────────

class BetTypeView(ui.View):
    """Initial view to choose Pass Line or Don't Pass."""

    def __init__(self, cog: "CrapsCog", user_id: int, bet: int) -> None:
        super().__init__(timeout=120)
        self.cog = cog
        self.user_id = user_id
        self.bet = bet
        self.chosen: BetType | None = None

    async def _check_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This isn't your game!", ephemeral=True
            )
            return False
        return True

    @ui.button(label="Pass Line", style=discord.ButtonStyle.primary, emoji="\U0001f3b2")
    async def pass_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if not await self._check_owner(interaction):
            return
        self.chosen = BetType.PASS_LINE
        self.stop()
        await self.cog._start_game(interaction, self.user_id, self.bet, BetType.PASS_LINE)

    @ui.button(label="Don't Pass", style=discord.ButtonStyle.danger, emoji="\U0001f6ab")
    async def dont_pass_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if not await self._check_owner(interaction):
            return
        self.chosen = BetType.DONT_PASS
        self.stop()
        await self.cog._start_game(interaction, self.user_id, self.bet, BetType.DONT_PASS)

    async def on_timeout(self) -> None:
        if self.chosen is None:
            # Refund the bet
            try:
                await queries.update_balance(str(self.user_id), self.bet)
            except Exception:
                pass
            self.cog.active_games.pop(self.user_id, None)


class CrapsView(ui.View):
    """Main game view with Roll and Place Odds buttons."""

    def __init__(self, game: CrapsGame, active_games: dict[int, "CrapsGame"]) -> None:
        super().__init__(timeout=120)
        self.game = game
        self.active_games = active_games
        self._update_buttons()

    def _update_buttons(self) -> None:
        # Odds button only available during point phase and if no odds bet yet
        in_point_phase = self.game.phase == Phase.POINT
        no_odds_yet = self.game.odds_bet == 0
        self.odds_btn.disabled = not (in_point_phase and no_odds_yet)

    async def _check_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.game.user_id:
            await interaction.response.send_message(
                "This isn't your game!", ephemeral=True
            )
            return False
        return True

    async def _finish(self, interaction: discord.Interaction) -> None:
        """Resolve the game, pay out, clean up."""
        game = self.game
        new_balance = 0
        if game.payout > 0:
            new_balance = await queries.update_balance(str(game.user_id), game.payout)
        else:
            bal = await queries.get_balance(str(game.user_id))
            new_balance = bal or 0

        embed = _game_embed(game, new_balance=new_balance)
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_games.pop(game.user_id, None)
        await interaction.response.edit_message(embed=embed, view=self)

    def _resolve_come_out(self, total: int) -> bool:
        """Handle come-out roll logic. Returns True if game is finished."""
        game = self.game

        if game.bet_type == BetType.PASS_LINE:
            if total in (7, 11):
                game.phase = Phase.FINISHED
                game.outcome = "Natural! You win!"
                game.payout = game.bet * 2  # return bet + 1:1
                return True
            elif total in (2, 3, 12):
                game.phase = Phase.FINISHED
                game.outcome = "Craps! You lose."
                game.payout = 0
                return True
        else:  # Don't Pass
            if total in (2, 3):
                game.phase = Phase.FINISHED
                game.outcome = "You win!"
                game.payout = game.bet * 2
                return True
            elif total == 12:
                game.phase = Phase.FINISHED
                game.outcome = "Push (12 is a bar)"
                game.payout = game.bet  # return bet only
                return True
            elif total in (7, 11):
                game.phase = Phase.FINISHED
                game.outcome = "Seven-out! You lose."
                game.payout = 0
                return True

        # Point established
        game.point = total
        game.phase = Phase.POINT
        return False

    def _resolve_point_roll(self, total: int) -> bool:
        """Handle point phase roll. Returns True if game is finished."""
        game = self.game

        if total == game.point:
            # Point hit
            game.phase = Phase.FINISHED
            if game.bet_type == BetType.PASS_LINE:
                game.outcome = f"Point {game.point} hit! You win!"
                odds_winnings = _compute_pass_odds_payout(game.odds_bet, game.point) if game.odds_bet else 0
                game.payout = game.bet * 2 + game.odds_bet + odds_winnings
            else:
                game.outcome = f"Point {game.point} hit. You lose."
                game.payout = 0  # lose both pass and odds
            return True
        elif total == 7:
            # Seven-out
            game.phase = Phase.FINISHED
            if game.bet_type == BetType.PASS_LINE:
                game.outcome = "Seven-out! You lose."
                game.payout = 0
            else:
                game.outcome = "Seven-out! You win!"
                odds_winnings = _compute_dont_pass_odds_payout(game.odds_bet, game.point) if game.odds_bet else 0
                game.payout = game.bet * 2 + game.odds_bet + odds_winnings
            return True

        return False

    @ui.button(label="Roll", style=discord.ButtonStyle.primary, emoji="\U0001f3b2")
    async def roll_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if not await self._check_owner(interaction):
            return

        d1, d2 = _roll_dice()
        total = d1 + d2
        roll_str = _fmt_dice(d1, d2)
        self.game.roll_history.append(roll_str)

        if self.game.phase == Phase.COME_OUT:
            finished = self._resolve_come_out(total)
        else:
            finished = self._resolve_point_roll(total)

        if finished:
            await self._finish(interaction)
        else:
            # Point was just established or continuing point phase
            self._update_buttons()
            embed = _game_embed(self.game)
            await interaction.response.edit_message(embed=embed, view=self)

    @ui.button(label="Place Odds", style=discord.ButtonStyle.success, emoji="\U0001f4b0")
    async def odds_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if not await self._check_owner(interaction):
            return
        if self.game.phase != Phase.POINT:
            await interaction.response.send_message(
                "You can only place odds after a point is established.", ephemeral=True
            )
            return
        if self.game.odds_bet > 0:
            await interaction.response.send_message(
                "You already have an odds bet placed.", ephemeral=True
            )
            return

        modal = OddsBetModal(self.game, self)
        await interaction.response.send_modal(modal)

    async def on_timeout(self) -> None:
        game = self.game
        if game.phase != Phase.FINISHED:
            # Refund all bets on timeout
            refund = game.bet + game.odds_bet
            if refund > 0:
                try:
                    await queries.update_balance(str(game.user_id), refund)
                except Exception:
                    pass
            self.active_games.pop(game.user_id, None)


# ── Cog ──────────────────────────────────────────────────────────────────────

class CrapsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_games: dict[int, CrapsGame] = {}

    @app_commands.command(name="craps", description="Play a game of craps")
    @app_commands.describe(bet="Number of coins to wager")
    async def craps(self, interaction: discord.Interaction, bet: int) -> None:
        user_id = interaction.user.id

        if user_id in self.active_games:
            await interaction.response.send_message(
                "You already have a craps game in progress! Finish it first.",
                ephemeral=True,
            )
            return

        if bet < 1:
            await interaction.response.send_message(
                "Bet must be at least 1 coin.", ephemeral=True
            )
            return

        # Ensure wallet exists + credit daily
        balance, daily_credited = await queries.get_or_create_wallet(str(user_id))

        if bet > balance:
            msg = f"You only have **{balance}** coins."
            if daily_credited:
                msg = f"Daily **100 coins** credited! {msg}"
            await interaction.response.send_message(msg, ephemeral=True)
            return

        # Deduct bet
        await queries.update_balance(str(user_id), -bet)

        # Reserve the slot so user can't start another game
        self.active_games[user_id] = CrapsGame(user_id=user_id, bet=bet, bet_type=BetType.PASS_LINE)

        daily_note = ""
        if daily_credited:
            daily_note = "Daily **100 coins** credited! "

        # Show bet type selection
        embed = discord.Embed(
            title="Craps — Choose Your Bet",
            description=(
                f"Wager: **{bet}** coins\n\n"
                "**Pass Line** — Win on 7/11 come-out, lose on 2/3/12. "
                "After a point, win if point hits before 7.\n\n"
                "**Don't Pass** — Win on 2/3 come-out, push on 12, lose on 7/11. "
                "After a point, win if 7 rolls before point."
            ),
            colour=discord.Colour.blurple(),
        )
        view = BetTypeView(self, user_id, bet)
        await interaction.response.send_message(
            content=daily_note if daily_note else None,
            embed=embed,
            view=view,
        )

    async def _start_game(
        self,
        interaction: discord.Interaction,
        user_id: int,
        bet: int,
        bet_type: BetType,
    ) -> None:
        """Called after bet type is selected. Sets up the game and first roll prompt."""
        game = CrapsGame(user_id=user_id, bet=bet, bet_type=bet_type)
        self.active_games[user_id] = game

        view = CrapsView(game, self.active_games)
        embed = _game_embed(game)
        embed.description = f"You chose **{bet_type.value}**. Roll the dice!"
        await interaction.response.edit_message(embed=embed, view=view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CrapsCog(bot))
