"""Craps cog — /craps with full side bets, odds, and interactive gameplay."""
import random
from dataclasses import dataclass, field
from enum import Enum

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries

# ── Dice ────────────────────────────────────────────────────────────────────

DICE_EMOJI = {1: "\u2680", 2: "\u2681", 3: "\u2682", 4: "\u2683", 5: "\u2684", 6: "\u2685"}


def _roll_dice() -> tuple[int, int]:
    return random.randint(1, 6), random.randint(1, 6)


def _fmt_dice(d1: int, d2: int) -> str:
    return f"{DICE_EMOJI[d1]} {DICE_EMOJI[d2]}  **({d1 + d2})**"


# ── Payout tables ───────────────────────────────────────────────────────────

TRUE_ODDS: dict[int, tuple[int, int]] = {
    4: (2, 1), 5: (3, 2), 6: (6, 5),
    8: (6, 5), 9: (3, 2), 10: (2, 1),
}

PLACE_PAYOUTS: dict[int, tuple[int, int]] = {
    4: (9, 5), 5: (7, 5), 6: (7, 6),
    8: (7, 6), 9: (7, 5), 10: (9, 5),
}

HARDWAY_PAYOUTS: dict[int, int] = {4: 7, 6: 9, 8: 9, 10: 7}  # N:1

MAX_ODDS_MULTIPLIER = 5
MAX_SIDE_BETS = 5

# ── Game state ──────────────────────────────────────────────────────────────


class BetType(Enum):
    PASS_LINE = "Pass Line"
    DONT_PASS = "Don't Pass"


class Phase(Enum):
    COME_OUT = "come_out"
    POINT = "point"
    FINISHED = "finished"


@dataclass
class SideBet:
    kind: str   # field, any7, any_craps, yo, come, dont_come, place_N, hard_N
    amount: int
    come_point: int = 0  # come/dont_come only; 0 = still in come-out


@dataclass
class CrapsGame:
    user_id: int
    bet: int
    bet_type: BetType
    phase: Phase = Phase.COME_OUT
    point: int = 0
    odds_bet: int = 0
    side_bets: list[SideBet] = field(default_factory=list)
    roll_history: list[str] = field(default_factory=list)
    side_log: list[str] = field(default_factory=list)
    outcome: str = ""
    payout: int = 0
    coins_in: int = 0
    coins_out: int = 0


# ── Side bet definitions ────────────────────────────────────────────────────

SIDE_BET_LABELS: dict[str, str] = {
    "field": "Field", "any7": "Any 7", "any_craps": "Any Craps", "yo": "Yo (11)",
    "come": "Come", "dont_come": "Don't Come",
    "place_4": "Place 4", "place_5": "Place 5", "place_6": "Place 6",
    "place_8": "Place 8", "place_9": "Place 9", "place_10": "Place 10",
    "hard_4": "Hard 4", "hard_6": "Hard 6", "hard_8": "Hard 8", "hard_10": "Hard 10",
}

POINT_PHASE_ONLY = {
    "come", "dont_come",
    "place_4", "place_5", "place_6", "place_8", "place_9", "place_10",
    "hard_4", "hard_6", "hard_8", "hard_10",
}

SELECT_OPTIONS = [
    discord.SelectOption(label="Field", value="field", description="1:1 (2:1 on 2 & 12). One roll.", emoji="\U0001f3b2"),
    discord.SelectOption(label="Any 7", value="any7", description="4:1. One roll.", emoji="7\ufe0f\u20e3"),
    discord.SelectOption(label="Any Craps", value="any_craps", description="7:1 on 2/3/12. One roll.", emoji="\U0001f480"),
    discord.SelectOption(label="Yo (11)", value="yo", description="15:1. One roll.", emoji="\U0001f3af"),
    discord.SelectOption(label="Come", value="come", description="Like Pass Line from next roll."),
    discord.SelectOption(label="Don't Come", value="dont_come", description="Like Don't Pass from next roll."),
    discord.SelectOption(label="Place 4", value="place_4", description="9:5 \u2014 4 before 7."),
    discord.SelectOption(label="Place 5", value="place_5", description="7:5 \u2014 5 before 7."),
    discord.SelectOption(label="Place 6", value="place_6", description="7:6 \u2014 6 before 7."),
    discord.SelectOption(label="Place 8", value="place_8", description="7:6 \u2014 8 before 7."),
    discord.SelectOption(label="Place 9", value="place_9", description="7:5 \u2014 9 before 7."),
    discord.SelectOption(label="Place 10", value="place_10", description="9:5 \u2014 10 before 7."),
    discord.SelectOption(label="Hard 4", value="hard_4", description="7:1 \u2014 2+2 before 7 or easy 4."),
    discord.SelectOption(label="Hard 6", value="hard_6", description="9:1 \u2014 3+3 before 7 or easy 6."),
    discord.SelectOption(label="Hard 8", value="hard_8", description="9:1 \u2014 4+4 before 7 or easy 8."),
    discord.SelectOption(label="Hard 10", value="hard_10", description="7:1 \u2014 5+5 before 7 or easy 10."),
]


# ── Payout logic ────────────────────────────────────────────────────────────


def _pass_odds_win(odds_bet: int, point: int) -> int:
    num, den = TRUE_ODDS[point]
    return odds_bet * num // den


def _dp_odds_win(odds_bet: int, point: int) -> int:
    """Lay odds: inverse of true odds."""
    num, den = TRUE_ODDS[point]
    return odds_bet * den // num


def _resolve_side_bets(game: CrapsGame, d1: int, d2: int) -> int:
    """Resolve all side bets for a roll. Returns total coins to credit back."""
    total = d1 + d2
    credit = 0
    remaining: list[SideBet] = []

    for sb in game.side_bets:
        label = SIDE_BET_LABELS[sb.kind]

        # ── One-roll bets ───────────────────────────────────────
        if sb.kind == "field":
            if total in (2, 12):
                win = sb.amount * 2  # 2:1
                credit += sb.amount + win
                game.side_log.append(f"\u2705 {label}: **+{win}**")
            elif total in (3, 4, 9, 10, 11):
                credit += sb.amount * 2  # 1:1
                game.side_log.append(f"\u2705 {label}: **+{sb.amount}**")
            else:
                game.side_log.append(f"\u274c {label}: **-{sb.amount}**")
            continue

        if sb.kind == "any7":
            if total == 7:
                win = sb.amount * 4
                credit += sb.amount + win
                game.side_log.append(f"\u2705 {label}: **+{win}**")
            else:
                game.side_log.append(f"\u274c {label}: **-{sb.amount}**")
            continue

        if sb.kind == "any_craps":
            if total in (2, 3, 12):
                win = sb.amount * 7
                credit += sb.amount + win
                game.side_log.append(f"\u2705 {label}: **+{win}**")
            else:
                game.side_log.append(f"\u274c {label}: **-{sb.amount}**")
            continue

        if sb.kind == "yo":
            if total == 11:
                win = sb.amount * 15
                credit += sb.amount + win
                game.side_log.append(f"\u2705 {label}: **+{win}**")
            else:
                game.side_log.append(f"\u274c {label}: **-{sb.amount}**")
            continue

        # ── Come ────────────────────────────────────────────────
        if sb.kind == "come":
            if sb.come_point == 0:
                if total in (7, 11):
                    credit += sb.amount * 2
                    game.side_log.append(f"\u2705 {label}: **+{sb.amount}** (natural)")
                elif total in (2, 3, 12):
                    game.side_log.append(f"\u274c {label}: **-{sb.amount}** (craps)")
                else:
                    sb.come_point = total
                    game.side_log.append(f"\U0001f3af {label}: point \u2192 **{total}**")
                    remaining.append(sb)
            else:
                if total == sb.come_point:
                    credit += sb.amount * 2
                    game.side_log.append(f"\u2705 {label} ({sb.come_point}): **+{sb.amount}**")
                elif total == 7:
                    game.side_log.append(f"\u274c {label} ({sb.come_point}): **-{sb.amount}**")
                else:
                    remaining.append(sb)
            continue

        # ── Don't Come ──────────────────────────────────────────
        if sb.kind == "dont_come":
            if sb.come_point == 0:
                if total in (2, 3):
                    credit += sb.amount * 2
                    game.side_log.append(f"\u2705 {label}: **+{sb.amount}**")
                elif total == 12:
                    credit += sb.amount  # push
                    game.side_log.append(f"\u2796 {label}: push (12)")
                elif total in (7, 11):
                    game.side_log.append(f"\u274c {label}: **-{sb.amount}**")
                else:
                    sb.come_point = total
                    game.side_log.append(f"\U0001f3af {label}: point \u2192 **{total}**")
                    remaining.append(sb)
            else:
                if total == 7:
                    credit += sb.amount * 2
                    game.side_log.append(f"\u2705 {label} ({sb.come_point}): **+{sb.amount}**")
                elif total == sb.come_point:
                    game.side_log.append(f"\u274c {label} ({sb.come_point}): **-{sb.amount}**")
                else:
                    remaining.append(sb)
            continue

        # ── Place bets ──────────────────────────────────────────
        if sb.kind.startswith("place_"):
            num = int(sb.kind.split("_")[1])
            if total == num:
                pn, pd = PLACE_PAYOUTS[num]
                win = sb.amount * pn // pd
                credit += sb.amount + win
                game.side_log.append(f"\u2705 {label}: **+{win}**")
            elif total == 7:
                game.side_log.append(f"\u274c {label}: **-{sb.amount}**")
            else:
                remaining.append(sb)
            continue

        # ── Hardways ────────────────────────────────────────────
        if sb.kind.startswith("hard_"):
            num = int(sb.kind.split("_")[1])
            if d1 == d2 and d1 + d2 == num:
                win = sb.amount * HARDWAY_PAYOUTS[num]
                credit += sb.amount + win
                game.side_log.append(f"\u2705 {label}: **+{win}**")
            elif (d1 != d2 and d1 + d2 == num) or total == 7:
                game.side_log.append(f"\u274c {label}: **-{sb.amount}**")
            else:
                remaining.append(sb)
            continue

    game.side_bets = remaining
    game.coins_out += credit
    return credit


def _refund_remaining(game: CrapsGame) -> int:
    """Refund all unresolved side bets when game ends."""
    refund = 0
    for sb in game.side_bets:
        refund += sb.amount
        label = SIDE_BET_LABELS[sb.kind]
        extra = f" ({sb.come_point})" if sb.kind in ("come", "dont_come") and sb.come_point else ""
        game.side_log.append(f"\u21a9\ufe0f {label}{extra}: refunded **{sb.amount}**")
    game.side_bets = []
    return refund


# ── Embed ───────────────────────────────────────────────────────────────────


def _game_embed(game: CrapsGame, *, new_balance: int = 0) -> discord.Embed:
    finished = game.phase == Phase.FINISHED

    if finished:
        net = game.coins_out - game.coins_in
        if net > 0:
            colour = discord.Colour.green()
        elif net == 0:
            colour = discord.Colour.light_grey()
        else:
            colour = discord.Colour.red()
        title = f"Craps \u2014 {game.outcome}"
    else:
        colour = discord.Colour.blurple()
        title = "Craps \u2014 Come-Out Roll" if game.phase == Phase.COME_OUT else f"Craps \u2014 Point is {game.point}"

    embed = discord.Embed(title=title, colour=colour)

    # Main bet
    bet_info = f"{game.bet_type.value}: **{game.bet}**c"
    if game.odds_bet:
        bet_info += f"\nOdds: **{game.odds_bet}**c"
    embed.add_field(name="Main Bet", value=bet_info, inline=True)

    if game.point:
        embed.add_field(name="Point", value=f"**{game.point}**", inline=True)

    # Active side bets
    if game.side_bets:
        lines = []
        for sb in game.side_bets:
            lbl = SIDE_BET_LABELS[sb.kind]
            extra = f" ({sb.come_point})" if sb.kind in ("come", "dont_come") and sb.come_point else ""
            lines.append(f"{lbl}{extra}: {sb.amount}c")
        embed.add_field(name="Side Bets", value="\n".join(lines[:8]), inline=True)

    # Roll history
    if game.roll_history:
        history = game.roll_history[-8:]
        if len(game.roll_history) > 8:
            history = ["..."] + history
        embed.add_field(name="Rolls", value="\n".join(history), inline=False)

    # Side bet results
    if game.side_log:
        log = game.side_log[-6:]
        if len(game.side_log) > 6:
            log = ["..."] + log
        embed.add_field(name="Side Bet Results", value="\n".join(log), inline=False)

    if finished:
        net = game.coins_out - game.coins_in
        sign = "+" if net > 0 else ""
        embed.add_field(name="Net", value=f"**{sign}{net}** coins", inline=True)
        embed.add_field(name="Balance", value=f"**{new_balance}** coins", inline=True)

    return embed


# ── Modals ──────────────────────────────────────────────────────────────────


class OddsBetModal(ui.Modal):
    amount = ui.TextInput(label="Odds bet amount", placeholder="e.g. 50", required=True, max_length=10)

    def __init__(self, game: CrapsGame, view: "CrapsView") -> None:
        max_odds = game.bet * MAX_ODDS_MULTIPLIER
        super().__init__(title=f"Place Odds (max {max_odds}c)")
        self.game = game
        self.craps_view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            amt = int(self.amount.value)
        except ValueError:
            await interaction.response.send_message("Enter a whole number.", ephemeral=True)
            return
        max_odds = self.game.bet * MAX_ODDS_MULTIPLIER
        if amt < 1 or amt > max_odds:
            await interaction.response.send_message(f"Must be 1\u2013{max_odds} coins.", ephemeral=True)
            return
        try:
            await queries.update_casino_balance(str(self.game.user_id), -amt)
        except ValueError:
            await interaction.response.send_message("Not enough coins!", ephemeral=True)
            return
        self.game.odds_bet = amt
        self.game.coins_in += amt
        self.craps_view.odds_btn.disabled = True
        self.craps_view.odds_btn.label = f"Odds: {amt}"
        await interaction.response.edit_message(embed=_game_embed(self.game), view=self.craps_view)


class SideBetModal(ui.Modal):
    amount = ui.TextInput(label="Amount (coins)", placeholder="e.g. 25", required=True, max_length=10)

    def __init__(self, game: CrapsGame, kind: str, view: "CrapsView") -> None:
        super().__init__(title=f"{SIDE_BET_LABELS[kind]} Bet")
        self.game = game
        self.kind = kind
        self.craps_view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            amt = int(self.amount.value)
        except ValueError:
            await interaction.response.send_message("Enter a whole number.", ephemeral=True)
            return
        if amt < 1:
            await interaction.response.send_message("Must be at least 1 coin.", ephemeral=True)
            return
        try:
            await queries.update_casino_balance(str(self.game.user_id), -amt)
        except ValueError:
            await interaction.response.send_message("Not enough coins!", ephemeral=True)
            return
        self.game.side_bets.append(SideBet(kind=self.kind, amount=amt))
        self.game.coins_in += amt
        await interaction.response.edit_message(embed=_game_embed(self.game), view=self.craps_view)


# ── Views ───────────────────────────────────────────────────────────────────


class SideBetSelect(ui.Select):
    def __init__(self, game: CrapsGame, craps_view: "CrapsView") -> None:
        super().__init__(placeholder="Place a side bet...", options=SELECT_OPTIONS, row=2)
        self.game = game
        self.craps_view = craps_view

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.game.user_id:
            await interaction.response.send_message("Not your game!", ephemeral=True)
            return
        kind = self.values[0]
        if kind in POINT_PHASE_ONLY and self.game.phase != Phase.POINT:
            await interaction.response.send_message(
                "That bet is only available during the point phase.", ephemeral=True,
            )
            return
        active_multi = sum(1 for sb in self.game.side_bets if sb.kind in POINT_PHASE_ONLY)
        if kind in POINT_PHASE_ONLY and active_multi >= MAX_SIDE_BETS:
            await interaction.response.send_message(
                f"Max {MAX_SIDE_BETS} active multi-roll side bets.", ephemeral=True,
            )
            return
        await interaction.response.send_modal(SideBetModal(self.game, kind, self.craps_view))


class BetTypeView(ui.View):
    def __init__(self, cog: "CrapsCog", user_id: int, bet: int) -> None:
        super().__init__(timeout=120)
        self.cog = cog
        self.user_id = user_id
        self.bet = bet
        self.chosen = False

    async def _check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your game!", ephemeral=True)
            return False
        return True

    @ui.button(label="Pass Line", style=discord.ButtonStyle.primary, emoji="\U0001f3b2")
    async def pass_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if not await self._check(interaction):
            return
        self.chosen = True
        self.stop()
        await self.cog._start_game(interaction, self.user_id, self.bet, BetType.PASS_LINE)

    @ui.button(label="Don't Pass", style=discord.ButtonStyle.danger, emoji="\U0001f6ab")
    async def dp_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if not await self._check(interaction):
            return
        self.chosen = True
        self.stop()
        await self.cog._start_game(interaction, self.user_id, self.bet, BetType.DONT_PASS)

    async def on_timeout(self) -> None:
        if not self.chosen:
            try:
                await queries.update_casino_balance(str(self.user_id), self.bet)
            except Exception:
                pass
            self.cog.active_games.pop(self.user_id, None)


class CrapsView(ui.View):
    def __init__(self, game: CrapsGame, active_games: dict[int, "CrapsGame"]) -> None:
        super().__init__(timeout=120)
        self.game = game
        self.active_games = active_games
        self.add_item(SideBetSelect(game, self))
        self._update_buttons()

    def _update_buttons(self) -> None:
        in_point = self.game.phase == Phase.POINT
        self.odds_btn.disabled = not (in_point and self.game.odds_bet == 0)

    async def _check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.game.user_id:
            await interaction.response.send_message("Not your game!", ephemeral=True)
            return False
        return True

    async def _finish(self, interaction: discord.Interaction) -> None:
        game = self.game
        refund = _refund_remaining(game)
        total_credit = game.payout + refund
        game.coins_out += total_credit
        if total_credit > 0:
            new_balance = await queries.update_casino_balance(str(game.user_id), total_credit)
        else:
            new_balance = (await queries.get_casino_balance(str(game.user_id))) or 0
        embed = _game_embed(game, new_balance=new_balance)
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_games.pop(game.user_id, None)
        await interaction.response.edit_message(embed=embed, view=self)

    def _resolve_come_out(self, total: int) -> bool:
        game = self.game
        if game.bet_type == BetType.PASS_LINE:
            if total in (7, 11):
                game.phase = Phase.FINISHED
                game.outcome = "Natural! You win!"
                game.payout = game.bet * 2
                return True
            if total in (2, 3, 12):
                game.phase = Phase.FINISHED
                game.outcome = "Craps! You lose."
                game.payout = 0
                return True
        else:
            if total in (2, 3):
                game.phase = Phase.FINISHED
                game.outcome = "You win!"
                game.payout = game.bet * 2
                return True
            if total == 12:
                game.phase = Phase.FINISHED
                game.outcome = "Push (12 barred)"
                game.payout = game.bet
                return True
            if total in (7, 11):
                game.phase = Phase.FINISHED
                game.outcome = "Seven-out! You lose."
                game.payout = 0
                return True
        game.point = total
        game.phase = Phase.POINT
        return False

    def _resolve_point(self, total: int) -> bool:
        game = self.game
        if total == game.point:
            game.phase = Phase.FINISHED
            if game.bet_type == BetType.PASS_LINE:
                game.outcome = f"Point {game.point}! You win!"
                odds_win = _pass_odds_win(game.odds_bet, game.point) if game.odds_bet else 0
                game.payout = game.bet * 2 + game.odds_bet + odds_win
            else:
                game.outcome = f"Point {game.point} hit. You lose."
                game.payout = 0
            return True
        if total == 7:
            game.phase = Phase.FINISHED
            if game.bet_type == BetType.PASS_LINE:
                game.outcome = "Seven-out! You lose."
                game.payout = 0
            else:
                game.outcome = "Seven-out! You win!"
                odds_win = _dp_odds_win(game.odds_bet, game.point) if game.odds_bet else 0
                game.payout = game.bet * 2 + game.odds_bet + odds_win
            return True
        return False

    @ui.button(label="Roll", style=discord.ButtonStyle.primary, emoji="\U0001f3b2", row=0)
    async def roll_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if not await self._check(interaction):
            return
        d1, d2 = _roll_dice()
        total = d1 + d2
        self.game.roll_history.append(_fmt_dice(d1, d2))

        # Resolve side bets first (credits returned immediately)
        side_credit = _resolve_side_bets(self.game, d1, d2)
        if side_credit > 0:
            await queries.update_casino_balance(str(self.game.user_id), side_credit)

        # Resolve main bet
        if self.game.phase == Phase.COME_OUT:
            finished = self._resolve_come_out(total)
        else:
            finished = self._resolve_point(total)

        if finished:
            await self._finish(interaction)
        else:
            self._update_buttons()
            await interaction.response.edit_message(embed=_game_embed(self.game), view=self)

    @ui.button(label="Place Odds", style=discord.ButtonStyle.success, emoji="\U0001f4b0", row=0)
    async def odds_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if not await self._check(interaction):
            return
        if self.game.phase != Phase.POINT:
            await interaction.response.send_message("Wait for a point first.", ephemeral=True)
            return
        if self.game.odds_bet > 0:
            await interaction.response.send_message("Already placed.", ephemeral=True)
            return
        await interaction.response.send_modal(OddsBetModal(self.game, self))

    async def on_timeout(self) -> None:
        game = self.game
        if game.phase != Phase.FINISHED:
            refund = game.bet + game.odds_bet + sum(sb.amount for sb in game.side_bets)
            if refund > 0:
                try:
                    await queries.update_casino_balance(str(game.user_id), refund)
                except Exception:
                    pass
            self.active_games.pop(game.user_id, None)


# ── Cog ─────────────────────────────────────────────────────────────────────


class CrapsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_games: dict[int, CrapsGame] = {}

    @app_commands.command(name="craps", description="Play craps with full side bets")
    @app_commands.describe(bet="Coins to wager on Pass/Don't Pass")
    async def craps(self, interaction: discord.Interaction, bet: int) -> None:
        user_id = interaction.user.id
        if user_id in self.active_games:
            await interaction.response.send_message("Finish your current game first!", ephemeral=True)
            return
        if bet < 1:
            await interaction.response.send_message("Bet must be at least 1 coin.", ephemeral=True)
            return

        balance = await queries.get_or_create_casino_wallet(str(user_id))
        if bet > balance:
            await interaction.response.send_message(
                f"You only have **{balance}** casino coins.", ephemeral=True,
            )
            return

        await queries.update_casino_balance(str(user_id), -bet)
        self.active_games[user_id] = CrapsGame(
            user_id=user_id, bet=bet, bet_type=BetType.PASS_LINE, coins_in=bet,
        )

        embed = discord.Embed(
            title="Craps \u2014 Choose Your Bet",
            description=(
                f"Wager: **{bet}** coins\n\n"
                "**Pass Line** \u2014 Win on 7/11, lose on 2/3/12. "
                "Point: win if hit before 7.\n"
                "**Don't Pass** \u2014 Win on 2/3, push 12, lose on 7/11. "
                "Point: win if 7 before hit."
            ),
            colour=discord.Colour.blurple(),
        )
        await interaction.response.send_message(
            embed=embed, view=BetTypeView(self, user_id, bet),
        )

    async def _start_game(
        self, interaction: discord.Interaction, user_id: int, bet: int, bet_type: BetType,
    ) -> None:
        game = CrapsGame(user_id=user_id, bet=bet, bet_type=bet_type, coins_in=bet)
        self.active_games[user_id] = game
        view = CrapsView(game, self.active_games)
        embed = _game_embed(game)
        embed.description = f"**{bet_type.value}** selected. Roll the dice!"
        await interaction.response.edit_message(embed=embed, view=view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CrapsCog(bot))
