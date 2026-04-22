"""Craps cog — multiplayer table with full side bets."""
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

HARDWAY_PAYOUTS: dict[int, int] = {4: 7, 6: 9, 8: 9, 10: 7}

MAX_ODDS_MULTIPLIER = 5
MAX_SIDE_BETS = 5
MAX_PLAYERS = 8

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
    kind: str
    amount: int
    come_point: int = 0


@dataclass
class PlayerBets:
    user_id: int
    display_name: str
    bet_type: BetType | None = None
    bet: int = 0
    odds_bet: int = 0
    side_bets: list[SideBet] = field(default_factory=list)
    side_log: list[str] = field(default_factory=list)
    coins_in: int = 0
    coins_out: int = 0
    payout: int = 0


@dataclass
class CrapsTable:
    channel_id: int
    shooter_id: int
    shooter_name: str
    phase: Phase = Phase.COME_OUT
    point: int = 0
    players: dict[int, PlayerBets] = field(default_factory=dict)
    roll_history: list[str] = field(default_factory=list)
    outcome: str = ""
    message: discord.Message | None = None


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


# ── Payout helpers ──────────────────────────────────────────────────────────


def _pass_odds_win(odds_bet: int, point: int) -> int:
    num, den = TRUE_ODDS[point]
    return odds_bet * num // den


def _dp_odds_win(odds_bet: int, point: int) -> int:
    num, den = TRUE_ODDS[point]
    return odds_bet * den // num


# ── Per-player side bet resolution ──────────────────────────────────────────


def _resolve_side_bets_for_player(player: PlayerBets, d1: int, d2: int) -> int:
    """Resolve all side bets for one player on a roll. Returns coins to credit."""
    total = d1 + d2
    credit = 0
    remaining: list[SideBet] = []

    for sb in player.side_bets:
        label = SIDE_BET_LABELS[sb.kind]
        tag = f"**{player.display_name}** "

        if sb.kind == "field":
            if total in (2, 12):
                win = sb.amount * 2
                credit += sb.amount + win
                player.side_log.append(f"\u2705 {tag}Field: **+{win}**")
            elif total in (3, 4, 9, 10, 11):
                credit += sb.amount * 2
                player.side_log.append(f"\u2705 {tag}Field: **+{sb.amount}**")
            else:
                player.side_log.append(f"\u274c {tag}Field: **-{sb.amount}**")
            continue

        if sb.kind == "any7":
            if total == 7:
                win = sb.amount * 4
                credit += sb.amount + win
                player.side_log.append(f"\u2705 {tag}Any 7: **+{win}**")
            else:
                player.side_log.append(f"\u274c {tag}Any 7: **-{sb.amount}**")
            continue

        if sb.kind == "any_craps":
            if total in (2, 3, 12):
                win = sb.amount * 7
                credit += sb.amount + win
                player.side_log.append(f"\u2705 {tag}Any Craps: **+{win}**")
            else:
                player.side_log.append(f"\u274c {tag}Any Craps: **-{sb.amount}**")
            continue

        if sb.kind == "yo":
            if total == 11:
                win = sb.amount * 15
                credit += sb.amount + win
                player.side_log.append(f"\u2705 {tag}Yo: **+{win}**")
            else:
                player.side_log.append(f"\u274c {tag}Yo: **-{sb.amount}**")
            continue

        if sb.kind == "come":
            if sb.come_point == 0:
                if total in (7, 11):
                    credit += sb.amount * 2
                    player.side_log.append(f"\u2705 {tag}Come: **+{sb.amount}**")
                elif total in (2, 3, 12):
                    player.side_log.append(f"\u274c {tag}Come: **-{sb.amount}**")
                else:
                    sb.come_point = total
                    player.side_log.append(f"\U0001f3af {tag}Come: point \u2192 **{total}**")
                    remaining.append(sb)
            else:
                if total == sb.come_point:
                    credit += sb.amount * 2
                    player.side_log.append(f"\u2705 {tag}Come ({sb.come_point}): **+{sb.amount}**")
                elif total == 7:
                    player.side_log.append(f"\u274c {tag}Come ({sb.come_point}): **-{sb.amount}**")
                else:
                    remaining.append(sb)
            continue

        if sb.kind == "dont_come":
            if sb.come_point == 0:
                if total in (2, 3):
                    credit += sb.amount * 2
                    player.side_log.append(f"\u2705 {tag}DC: **+{sb.amount}**")
                elif total == 12:
                    credit += sb.amount
                    player.side_log.append(f"\u2796 {tag}DC: push (12)")
                elif total in (7, 11):
                    player.side_log.append(f"\u274c {tag}DC: **-{sb.amount}**")
                else:
                    sb.come_point = total
                    player.side_log.append(f"\U0001f3af {tag}DC: point \u2192 **{total}**")
                    remaining.append(sb)
            else:
                if total == 7:
                    credit += sb.amount * 2
                    player.side_log.append(f"\u2705 {tag}DC ({sb.come_point}): **+{sb.amount}**")
                elif total == sb.come_point:
                    player.side_log.append(f"\u274c {tag}DC ({sb.come_point}): **-{sb.amount}**")
                else:
                    remaining.append(sb)
            continue

        if sb.kind.startswith("place_"):
            num = int(sb.kind.split("_")[1])
            if total == num:
                pn, pd = PLACE_PAYOUTS[num]
                win = sb.amount * pn // pd
                credit += sb.amount + win
                player.side_log.append(f"\u2705 {tag}{label}: **+{win}**")
            elif total == 7:
                player.side_log.append(f"\u274c {tag}{label}: **-{sb.amount}**")
            else:
                remaining.append(sb)
            continue

        if sb.kind.startswith("hard_"):
            num = int(sb.kind.split("_")[1])
            if d1 == d2 and d1 + d2 == num:
                win = sb.amount * HARDWAY_PAYOUTS[num]
                credit += sb.amount + win
                player.side_log.append(f"\u2705 {tag}{label}: **+{win}**")
            elif (d1 != d2 and d1 + d2 == num) or total == 7:
                player.side_log.append(f"\u274c {tag}{label}: **-{sb.amount}**")
            else:
                remaining.append(sb)
            continue

    player.side_bets = remaining
    player.coins_out += credit
    return credit


def _refund_player(player: PlayerBets) -> int:
    """Refund unresolved side bets for one player. Returns total refund."""
    refund = 0
    for sb in player.side_bets:
        refund += sb.amount
        label = SIDE_BET_LABELS[sb.kind]
        extra = f" ({sb.come_point})" if sb.kind in ("come", "dont_come") and sb.come_point else ""
        player.side_log.append(f"\u21a9\ufe0f {label}{extra}: refunded **{sb.amount}**")
    player.side_bets = []
    return refund


# ── Embed ───────────────────────────────────────────────────────────────────


def _table_embed(
    table: CrapsTable, *, balances: dict[int, int] | None = None,
) -> discord.Embed:
    finished = table.phase == Phase.FINISHED

    if finished:
        colour = discord.Colour.gold()
        title = f"Craps Table \u2014 {table.outcome}"
    else:
        colour = discord.Colour.blurple()
        title = (
            "Craps Table \u2014 Come-Out Roll"
            if table.phase == Phase.COME_OUT
            else f"Craps Table \u2014 Point is {table.point}"
        )

    embed = discord.Embed(title=title, colour=colour)
    embed.set_footer(text=f"\U0001f3b2 Shooter: {table.shooter_name}")

    # Players
    if table.players:
        lines = []
        for p in table.players.values():
            if p.bet_type:
                emoji = "\U0001f3b2" if p.bet_type == BetType.PASS_LINE else "\U0001f6ab"
                main = f"{p.bet_type.value} {p.bet}c"
                if p.odds_bet:
                    main += f" + {p.odds_bet}c odds"
            else:
                emoji = "\U0001f3b0"
                main = "side bets"
            sides = ", ".join(
                f"{SIDE_BET_LABELS[sb.kind]}{f' ({sb.come_point})' if sb.kind in ('come','dont_come') and sb.come_point else ''} {sb.amount}c"
                for sb in p.side_bets
            )
            line = f"{emoji} **{p.display_name}** \u2014 {main}"
            if sides:
                line += f" | {sides}"
            # Finished: show result
            if finished and balances:
                net = p.coins_out - p.coins_in
                sign = "+" if net > 0 else ""
                bal = balances.get(p.user_id, 0)
                line += f"\n\u2003\u2192 **{sign}{net}c** (bal: {bal}c)"
            lines.append(line)
        embed.add_field(name="Players", value="\n".join(lines[:8]), inline=False)
    else:
        embed.add_field(
            name="Players",
            value="*No players yet \u2014 click Join Pass or Join Don't Pass!*",
            inline=False,
        )

    if table.point:
        embed.add_field(name="Point", value=f"**{table.point}**", inline=True)

    # Roll history
    if table.roll_history:
        history = table.roll_history[-8:]
        if len(table.roll_history) > 8:
            history = ["..."] + history
        embed.add_field(name="Rolls", value="\n".join(history), inline=False)

    # Collect side bet logs from all players (most recent across everyone)
    all_logs: list[str] = []
    for p in table.players.values():
        all_logs.extend(p.side_log)
    if all_logs:
        show = all_logs[-6:]
        if len(all_logs) > 6:
            show = ["..."] + show
        embed.add_field(name="Side Bet Results", value="\n".join(show), inline=False)

    return embed


# ── Modals ──────────────────────────────────────────────────────────────────


class JoinModal(ui.Modal):
    amount = ui.TextInput(label="Bet amount (coins)", placeholder="e.g. 50", required=True, max_length=10)

    def __init__(self, table: CrapsTable, bet_type: BetType, view: "CrapsTableView") -> None:
        super().__init__(title=f"Join \u2014 {bet_type.value}")
        self.table = table
        self.bet_type = bet_type
        self.table_view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            amt = int(self.amount.value)
        except ValueError:
            await interaction.response.send_message("Enter a whole number.", ephemeral=True)
            return
        if amt < 1:
            await interaction.response.send_message("Must be at least 1 coin.", ephemeral=True)
            return
        uid = interaction.user.id
        # Check if already has main bet
        existing = self.table.players.get(uid)
        if existing and existing.bet_type is not None:
            await interaction.response.send_message("You already have a main bet!", ephemeral=True)
            return
        try:
            await queries.update_casino_balance(str(uid), -amt)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(f"Not enough coins! (have {bal})", ephemeral=True)
            return
        if existing:
            existing.bet_type = self.bet_type
            existing.bet = amt
            existing.coins_in += amt
        else:
            self.table.players[uid] = PlayerBets(
                user_id=uid, display_name=interaction.user.display_name,
                bet_type=self.bet_type, bet=amt, coins_in=amt,
            )
        await interaction.response.edit_message(embed=_table_embed(self.table), view=self.table_view)


class OddsBetModal(ui.Modal):
    amount = ui.TextInput(label="Odds bet amount", placeholder="e.g. 50", required=True, max_length=10)

    def __init__(self, table: CrapsTable, player: PlayerBets, view: "CrapsTableView") -> None:
        max_odds = player.bet * MAX_ODDS_MULTIPLIER
        super().__init__(title=f"Place Odds (max {max_odds}c)")
        self.table = table
        self.player = player
        self.table_view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            amt = int(self.amount.value)
        except ValueError:
            await interaction.response.send_message("Enter a whole number.", ephemeral=True)
            return
        max_odds = self.player.bet * MAX_ODDS_MULTIPLIER
        if amt < 1 or amt > max_odds:
            await interaction.response.send_message(f"Must be 1\u2013{max_odds} coins.", ephemeral=True)
            return
        try:
            await queries.update_casino_balance(str(self.player.user_id), -amt)
        except ValueError:
            await interaction.response.send_message("Not enough coins!", ephemeral=True)
            return
        self.player.odds_bet = amt
        self.player.coins_in += amt
        await interaction.response.edit_message(embed=_table_embed(self.table), view=self.table_view)


class SideBetModal(ui.Modal):
    amount = ui.TextInput(label="Amount (coins)", placeholder="e.g. 25", required=True, max_length=10)

    def __init__(self, table: CrapsTable, kind: str, view: "CrapsTableView") -> None:
        super().__init__(title=f"{SIDE_BET_LABELS[kind]} Bet")
        self.table = table
        self.kind = kind
        self.table_view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            amt = int(self.amount.value)
        except ValueError:
            await interaction.response.send_message("Enter a whole number.", ephemeral=True)
            return
        if amt < 1:
            await interaction.response.send_message("Must be at least 1 coin.", ephemeral=True)
            return
        uid = interaction.user.id
        try:
            await queries.update_casino_balance(str(uid), -amt)
        except ValueError:
            await interaction.response.send_message("Not enough coins!", ephemeral=True)
            return
        player = self.table.players.get(uid)
        if player is None:
            player = PlayerBets(user_id=uid, display_name=interaction.user.display_name)
            self.table.players[uid] = player
        player.side_bets.append(SideBet(kind=self.kind, amount=amt))
        player.coins_in += amt
        await interaction.response.edit_message(embed=_table_embed(self.table), view=self.table_view)


# ── Views ───────────────────────────────────────────────────────────────────


class SideBetSelect(ui.Select):
    def __init__(self, table: CrapsTable, table_view: "CrapsTableView") -> None:
        super().__init__(placeholder="Place a side bet...", options=SELECT_OPTIONS, row=2)
        self.table = table
        self.table_view = table_view

    async def callback(self, interaction: discord.Interaction) -> None:
        kind = self.values[0]
        if kind in POINT_PHASE_ONLY and self.table.phase != Phase.POINT:
            await interaction.response.send_message(
                "That bet is only available during the point phase.", ephemeral=True,
            )
            return
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if player:
            active_multi = sum(1 for sb in player.side_bets if sb.kind in POINT_PHASE_ONLY)
            if kind in POINT_PHASE_ONLY and active_multi >= MAX_SIDE_BETS:
                await interaction.response.send_message(
                    f"Max {MAX_SIDE_BETS} active multi-roll side bets.", ephemeral=True,
                )
                return
        if len(self.table.players) >= MAX_PLAYERS and uid not in self.table.players:
            await interaction.response.send_message("Table is full!", ephemeral=True)
            return
        # Ensure wallet exists
        await queries.get_or_create_casino_wallet(str(uid))
        await interaction.response.send_modal(SideBetModal(self.table, kind, self.table_view))


class CrapsTableView(ui.View):
    def __init__(self, table: CrapsTable, active_tables: dict[int, "CrapsTable"]) -> None:
        super().__init__(timeout=120)
        self.table = table
        self.active_tables = active_tables
        self.add_item(SideBetSelect(table, self))

    # ── Row 0: Roll + Place Odds ──────────────────────────────────

    @ui.button(label="Roll", style=discord.ButtonStyle.primary, emoji="\U0001f3b2", row=0)
    async def roll_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.table.shooter_id:
            await interaction.response.send_message("Only the shooter can roll!", ephemeral=True)
            return
        # Must have at least one main bet on the table
        has_main = any(p.bet_type is not None for p in self.table.players.values())
        if not has_main:
            await interaction.response.send_message(
                "No bets on the table yet! Someone needs to join first.", ephemeral=True,
            )
            return

        d1, d2 = _roll_dice()
        total = d1 + d2
        self.table.roll_history.append(_fmt_dice(d1, d2))

        # Resolve side bets for ALL players
        for player in self.table.players.values():
            side_credit = _resolve_side_bets_for_player(player, d1, d2)
            if side_credit > 0:
                await queries.update_casino_balance(str(player.user_id), side_credit)

        # Resolve main bet phase
        if self.table.phase == Phase.COME_OUT:
            finished = self._resolve_come_out(total)
        else:
            finished = self._resolve_point(total)

        if finished:
            await self._finish(interaction)
        else:
            await interaction.response.edit_message(embed=_table_embed(self.table), view=self)

    @ui.button(label="Place Odds", style=discord.ButtonStyle.success, emoji="\U0001f4b0", row=0)
    async def odds_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if player is None or player.bet_type is None:
            await interaction.response.send_message("You need a main bet first!", ephemeral=True)
            return
        if self.table.phase != Phase.POINT:
            await interaction.response.send_message("Wait for a point first.", ephemeral=True)
            return
        if player.odds_bet > 0:
            await interaction.response.send_message("You already placed odds.", ephemeral=True)
            return
        await interaction.response.send_modal(OddsBetModal(self.table, player, self))

    # ── Row 1: Join + Leave ───────────────────────────────────────

    @ui.button(label="Join Pass", style=discord.ButtonStyle.primary, emoji="\U0001f3b2", row=1)
    async def join_pass_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._handle_join(interaction, BetType.PASS_LINE)

    @ui.button(label="Join Don't Pass", style=discord.ButtonStyle.danger, emoji="\U0001f6ab", row=1)
    async def join_dp_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._handle_join(interaction, BetType.DONT_PASS)

    @ui.button(label="Leave", style=discord.ButtonStyle.secondary, emoji="\U0001f6aa", row=1)
    async def leave_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if player is None:
            await interaction.response.send_message("You're not at this table.", ephemeral=True)
            return

        # Shooter leaving = abort entire table
        if uid == self.table.shooter_id:
            await self._abort(interaction, "Shooter left \u2014 all bets refunded.")
            return

        # Refund this player's bets
        refund = player.bet + player.odds_bet + sum(sb.amount for sb in player.side_bets)
        if refund > 0:
            await queries.update_casino_balance(str(uid), refund)
        del self.table.players[uid]

        if not self.table.players:
            await self._abort(interaction, "All players left \u2014 table closed.")
            return

        await interaction.response.edit_message(embed=_table_embed(self.table), view=self)

    async def _handle_join(self, interaction: discord.Interaction, bet_type: BetType) -> None:
        uid = interaction.user.id
        if self.table.phase != Phase.COME_OUT:
            await interaction.response.send_message(
                "Can't place a main bet after the come-out. Use side bets instead!", ephemeral=True,
            )
            return
        existing = self.table.players.get(uid)
        if existing and existing.bet_type is not None:
            await interaction.response.send_message("You already have a main bet!", ephemeral=True)
            return
        if len(self.table.players) >= MAX_PLAYERS and uid not in self.table.players:
            await interaction.response.send_message("Table is full!", ephemeral=True)
            return
        await queries.get_or_create_casino_wallet(str(uid))
        await interaction.response.send_modal(JoinModal(self.table, bet_type, self))

    # ── Resolution ────────────────────────────────────────────────

    def _resolve_come_out(self, total: int) -> bool:
        table = self.table
        if total in (7, 11, 2, 3, 12):
            table.phase = Phase.FINISHED
            if total in (7, 11):
                table.outcome = "Natural!"
            else:
                table.outcome = "Craps!"
            for p in table.players.values():
                if p.bet_type is None:
                    continue
                if p.bet_type == BetType.PASS_LINE:
                    p.payout = p.bet * 2 if total in (7, 11) else 0
                else:
                    if total in (2, 3):
                        p.payout = p.bet * 2
                    elif total == 12:
                        p.payout = p.bet  # push
                    else:
                        p.payout = 0
            return True
        table.point = total
        table.phase = Phase.POINT
        return False

    def _resolve_point(self, total: int) -> bool:
        table = self.table
        if total == table.point:
            table.phase = Phase.FINISHED
            table.outcome = f"Point {table.point}!"
            for p in table.players.values():
                if p.bet_type is None:
                    continue
                if p.bet_type == BetType.PASS_LINE:
                    odds_win = _pass_odds_win(p.odds_bet, table.point) if p.odds_bet else 0
                    p.payout = p.bet * 2 + p.odds_bet + odds_win
                else:
                    p.payout = 0
            return True
        if total == 7:
            table.phase = Phase.FINISHED
            table.outcome = "Seven-out!"
            for p in table.players.values():
                if p.bet_type is None:
                    continue
                if p.bet_type == BetType.PASS_LINE:
                    p.payout = 0
                else:
                    odds_win = _dp_odds_win(p.odds_bet, table.point) if p.odds_bet else 0
                    p.payout = p.bet * 2 + p.odds_bet + odds_win
            return True
        return False

    async def _finish(self, interaction: discord.Interaction) -> None:
        table = self.table
        balances: dict[int, int] = {}

        for player in table.players.values():
            refund = _refund_player(player)
            total_credit = player.payout + refund
            player.coins_out += total_credit
            if total_credit > 0:
                balances[player.user_id] = await queries.update_casino_balance(
                    str(player.user_id), total_credit,
                )
            else:
                balances[player.user_id] = (
                    await queries.get_casino_balance(str(player.user_id))
                ) or 0

        embed = _table_embed(table, balances=balances)
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_tables.pop(table.channel_id, None)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _abort(self, interaction: discord.Interaction, reason: str) -> None:
        """End table early, refund everyone."""
        table = self.table
        for player in table.players.values():
            refund = player.bet + player.odds_bet + sum(sb.amount for sb in player.side_bets)
            if refund > 0:
                try:
                    await queries.update_casino_balance(str(player.user_id), refund)
                except Exception:
                    pass
        embed = discord.Embed(
            title="Craps Table \u2014 Closed",
            description=reason,
            colour=discord.Colour.dark_grey(),
        )
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_tables.pop(table.channel_id, None)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        table = self.table
        if table.phase == Phase.FINISHED:
            return
        for player in table.players.values():
            refund = player.bet + player.odds_bet + sum(sb.amount for sb in player.side_bets)
            if refund > 0:
                try:
                    await queries.update_casino_balance(str(player.user_id), refund)
                except Exception:
                    pass
        self.active_tables.pop(table.channel_id, None)
        if table.message:
            try:
                embed = discord.Embed(
                    title="Craps Table \u2014 Timed Out",
                    description="Shooter went AFK. All bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                pass


# ── Cog ─────────────────────────────────────────────────────────────────────


class CrapsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, CrapsTable] = {}

    @app_commands.command(name="craps", description="Open a craps table (multiplayer)")
    async def craps(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            await interaction.response.send_message(
                "There's already a craps table in this channel! Use the buttons to join.",
                ephemeral=True,
            )
            return

        # Ensure shooter has a casino wallet
        await queries.get_or_create_casino_wallet(str(interaction.user.id))

        table = CrapsTable(
            channel_id=channel_id,
            shooter_id=interaction.user.id,
            shooter_name=interaction.user.display_name,
        )
        self.active_tables[channel_id] = table

        view = CrapsTableView(table, self.active_tables)
        embed = _table_embed(table)
        embed.description = (
            "**Join the table** with Pass Line or Don't Pass, "
            "then the shooter rolls!\n"
            "Side bets available to everyone via the dropdown."
        )
        await interaction.response.send_message(embed=embed, view=view)
        table.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CrapsCog(bot))
