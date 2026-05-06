"""Crapless Craps cog — variant where 2/3/11/12 set a point on come-out (no craps loss)."""
import asyncio
import random
from dataclasses import dataclass, field
from enum import Enum

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries
import logging
log = logging.getLogger(__name__)

# ── Dice ────────────────────────────────────────────────────────────────────

DICE_EMOJI = {1: "\u2680", 2: "\u2681", 3: "\u2682", 4: "\u2683", 5: "\u2684", 6: "\u2685"}


def _roll_dice() -> tuple[int, int]:
    return random.randint(1, 6), random.randint(1, 6)


def _fmt_dice(d1: int, d2: int) -> str:
    return f"{DICE_EMOJI[d1]} {DICE_EMOJI[d2]}  **({d1 + d2})**"


# ── Payout tables ───────────────────────────────────────────────────────────

# Crapless extends true odds to cover 2, 3, 11, 12 as valid point numbers
TRUE_ODDS: dict[int, tuple[int, int]] = {
    2: (6, 1), 3: (3, 1),
    4: (2, 1), 5: (3, 2), 6: (6, 5),
    8: (6, 5), 9: (3, 2), 10: (2, 1),
    11: (3, 1), 12: (6, 1),
}

PLACE_PAYOUTS: dict[int, tuple[int, int]] = {
    2: (11, 2), 3: (11, 4),
    4: (9, 5), 5: (7, 5), 6: (7, 6),
    8: (7, 6), 9: (7, 5), 10: (9, 5),
    11: (11, 4), 12: (11, 2),
}

# Only 4, 6, 8, 10 can be made the hard way (two matching dice)
HARDWAY_PAYOUTS: dict[int, int] = {4: 7, 6: 9, 8: 9, 10: 7}

MAX_ODDS_MULTIPLIER = 5
MAX_PLAYERS = 8

# ── Game state ──────────────────────────────────────────────────────────────


class BetType(Enum):
    PASS_LINE = "Pass Line"
    # Don't Pass is not offered in crapless craps


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
    last_sides: list[tuple[str, int]] = field(default_factory=list)  # snapshot for repeat


@dataclass
class CraplessTable:
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
    "come": "Come",
    # Place bets — extended to include 2, 3, 11, 12
    "place_2": "Place 2", "place_3": "Place 3",
    "place_4": "Place 4", "place_5": "Place 5", "place_6": "Place 6",
    "place_8": "Place 8", "place_9": "Place 9", "place_10": "Place 10",
    "place_11": "Place 11", "place_12": "Place 12",
    # Hardways — only 4, 6, 8, 10 can be rolled the hard way
    "hard_4": "Hard 4", "hard_6": "Hard 6", "hard_8": "Hard 8", "hard_10": "Hard 10",
}

POINT_PHASE_ONLY = {
    "come",
    "place_2", "place_3", "place_4", "place_5", "place_6",
    "place_8", "place_9", "place_10", "place_11", "place_12",
    "hard_4", "hard_6", "hard_8", "hard_10",
}

PLACE_NUMBERS = (2, 3, 4, 5, 6, 8, 9, 10, 11, 12)
HARDWAY_NUMBERS = (4, 6, 8, 10)


# ── Payout helpers ──────────────────────────────────────────────────────────


def _pass_odds_win(odds_bet: int, point: int) -> int:
    num, den = TRUE_ODDS[point]
    return odds_bet * num // den


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
                # Crapless: only 7 wins immediately on come-out sub-roll.
                # All other numbers (2-6, 8-12) establish a come point.
                if total == 7:
                    credit += sb.amount * 2
                    player.side_log.append(f"\u2705 {tag}Come: **+{sb.amount}**")
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
        extra = f" ({sb.come_point})" if sb.kind == "come" and sb.come_point else ""
        player.side_log.append(f"\u21a9\ufe0f {label}{extra}: refunded **{sb.amount}**")
    player.side_bets = []
    return refund


# ── Embed ───────────────────────────────────────────────────────────────────


def _table_embed(
    table: CraplessTable, *, balances: dict[int, int] | None = None,
) -> discord.Embed:
    finished = table.phase == Phase.FINISHED

    if finished:
        colour = discord.Colour.gold()
        title = f"Crapless Craps \u2014 {table.outcome}"
    else:
        colour = discord.Colour.blurple()
        title = (
            "Crapless Craps \u2014 Come-Out Roll"
            if table.phase == Phase.COME_OUT
            else f"Crapless Craps \u2014 Point is {table.point}"
        )

    embed = discord.Embed(title=title, colour=colour)
    embed.set_footer(
        text=f"\U0001f3b2 Shooter: {table.shooter_name} \u00b7 Variant: No craps on come-out"
    )

    # Players
    if table.players:
        lines = []
        for p in table.players.values():
            if p.bet_type:
                emoji = "\U0001f3b2"
                main = f"{p.bet_type.value} {p.bet}c"
                if p.odds_bet:
                    main += f" + {p.odds_bet}c odds"
            else:
                emoji = "\U0001f3b0"
                main = "side bets"
            from collections import Counter
            sb_counts: Counter[str] = Counter()
            for sb in p.side_bets:
                lbl = SIDE_BET_LABELS[sb.kind]
                if sb.kind == "come" and sb.come_point:
                    lbl += f" ({sb.come_point})"
                key = f"{lbl} {sb.amount}c"
                sb_counts[key] += 1
            side_parts = []
            for key, count in sb_counts.items():
                side_parts.append(f"{key} x{count}" if count > 1 else key)
            sides = ", ".join(side_parts)
            line = f"{emoji} **{p.display_name}** \u2014 {main}"
            if sides:
                line += f" | {sides}"
            if finished and balances:
                net = p.coins_out - p.coins_in
                sign = "+" if net > 0 else ""
                bal = balances.get(p.user_id, 0)
                line += f"\n\u2003\u2192 **{sign}{net}c** (bal: {bal}c)"
            lines.append(line)
        players_value = "\n".join(lines[:8])
        if len(players_value) > 1024:
            players_value = players_value[:1021] + "…"
        embed.add_field(name="Players", value=players_value, inline=False)
    else:
        embed.add_field(
            name="Players",
            value="*No players yet \u2014 click Join Pass to join!*",
            inline=False,
        )

    if table.point:
        embed.add_field(name="Point", value=f"**{table.point}**", inline=True)

    if table.roll_history:
        history = table.roll_history[-8:]
        if len(table.roll_history) > 8:
            history = ["..."] + history
        embed.add_field(name="Rolls", value="\n".join(history), inline=False)

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

    def __init__(
        self,
        table: CraplessTable,
        view: "CraplessTableView",
        balance: int,
        default_bet: int | None = None,
    ) -> None:
        super().__init__(title="Join \u2014 Pass Line")
        self.table = table
        self.table_view = view
        self.amount.placeholder = f"e.g. 50 (bal: {balance}c)"
        if default_bet is not None:
            self.amount.default = str(default_bet)

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
            existing.bet_type = BetType.PASS_LINE
            existing.bet = amt
            existing.coins_in += amt
        else:
            self.table.players[uid] = PlayerBets(
                user_id=uid, display_name=interaction.user.display_name,
                bet_type=BetType.PASS_LINE, bet=amt, coins_in=amt,
            )
        await interaction.response.edit_message(embed=_table_embed(self.table), view=self.table_view)


class OddsBetModal(ui.Modal):
    amount = ui.TextInput(label="Odds bet amount", placeholder="e.g. 50", required=True, max_length=10)

    def __init__(self, table: CraplessTable, player: PlayerBets, view: "CraplessTableView", balance: int) -> None:
        max_odds = player.bet * MAX_ODDS_MULTIPLIER
        super().__init__(title=f"Place Odds (max {max_odds}c)")
        self.table = table
        self.player = player
        self.table_view = view
        self.amount.placeholder = f"e.g. 50 (bal: {balance}c)"

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

    def __init__(self, table: CraplessTable, kind: str, view: "CraplessTableView", balance: int) -> None:
        super().__init__(title=f"{SIDE_BET_LABELS[kind]} Bet")
        self.table = table
        self.kind = kind
        self.table_view = view
        self.amount.placeholder = f"e.g. 25 (bal: {balance}c)"

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


class PlaceHardwayModal(ui.Modal):
    """Combined modal for Place and Hardway bets.

    Crapless extends place bets to all 10 point numbers (2-6, 8-12),
    so we group symmetrical pairs to fit within Discord's 5-field limit.
    """

    place_2_12 = ui.TextInput(
        label="Place 2 & 12 (each)", placeholder="blank to skip",
        required=False, max_length=10,
    )
    place_3_11 = ui.TextInput(
        label="Place 3 & 11 (each)", placeholder="blank to skip",
        required=False, max_length=10,
    )
    place_4_5_9_10 = ui.TextInput(
        label="Place 4/5/9/10 (each)", placeholder="blank to skip",
        required=False, max_length=10,
    )
    place_6_8 = ui.TextInput(
        label="Place 6 & 8 (each)", placeholder="blank to skip",
        required=False, max_length=10,
    )
    hard_4_6_8_10 = ui.TextInput(
        label="Hard 4/6/8/10 (each)", placeholder="blank to skip",
        required=False, max_length=10,
    )

    def __init__(self, table: CraplessTable, view: "CraplessTableView", balance: int) -> None:
        super().__init__(title=f"Place & Hardway Bets ({balance}c)")
        self.table = table
        self.table_view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bets: list[tuple[str, int]] = []
        field_map: list[tuple[ui.TextInput, list[str]]] = [
            (self.place_2_12, ["place_2", "place_12"]),
            (self.place_3_11, ["place_3", "place_11"]),
            (self.place_4_5_9_10, ["place_4", "place_5", "place_9", "place_10"]),
            (self.place_6_8, ["place_6", "place_8"]),
            (self.hard_4_6_8_10, ["hard_4", "hard_6", "hard_8", "hard_10"]),
        ]
        for text_input, kinds in field_map:
            val = text_input.value.strip()
            if not val:
                continue
            try:
                amt = int(val)
            except ValueError:
                await interaction.response.send_message(
                    f"Invalid amount in **{text_input.label}**: `{val}`", ephemeral=True,
                )
                return
            if amt < 1:
                await interaction.response.send_message("All amounts must be at least 1.", ephemeral=True)
                return
            for kind in kinds:
                bets.append((kind, amt))

        if not bets:
            await interaction.response.send_message("You didn't enter any bets!", ephemeral=True)
            return

        total_cost = sum(a for _, a in bets)
        uid = interaction.user.id
        try:
            await queries.update_casino_balance(str(uid), -total_cost)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins! Need **{total_cost}**, have **{bal}**.", ephemeral=True,
            )
            return

        player = self.table.players.get(uid)
        if player is None:
            player = PlayerBets(user_id=uid, display_name=interaction.user.display_name)
            self.table.players[uid] = player
        for kind, amt in bets:
            player.side_bets.append(SideBet(kind=kind, amount=amt))
        player.coins_in += total_cost
        await interaction.response.edit_message(
            embed=_table_embed(self.table), view=self.table_view,
        )


# ── Views ───────────────────────────────────────────────────────────────────


class CraplessTableView(ui.View):
    def __init__(self, table: CraplessTable, active_tables: dict[int, "CraplessTable"]) -> None:
        super().__init__(timeout=120)
        self.table = table
        self.active_tables = active_tables

    # ── Row 0: Roll + Place Odds ──────────────────────────────────

    @ui.button(label="Roll", style=discord.ButtonStyle.primary, emoji="\U0001f3b2", row=0)
    async def roll_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.table.shooter_id:
            await interaction.response.send_message("Only the shooter can roll!", ephemeral=True)
            return
        has_main = any(p.bet_type is not None for p in self.table.players.values())
        if not has_main:
            await interaction.response.send_message(
                "No bets on the table yet! Someone needs to join first.", ephemeral=True,
            )
            return

        d1, d2 = _roll_dice()
        total = d1 + d2

        # Disable all controls during animation
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True  # type: ignore[union-attr]

        r1, r2 = random.randint(1, 6), random.randint(1, 6)
        self.table.roll_history.append(
            f"{DICE_EMOJI[r1]} {DICE_EMOJI[r2]}  \U0001f3b2 *rolling...*"
        )
        await interaction.response.edit_message(
            embed=_table_embed(self.table), view=self,
        )

        await asyncio.sleep(0.7)
        while True:
            r1, r2 = random.randint(1, 6), random.randint(1, 6)
            if (r1, r2) != (d1, d2):
                break
        self.table.roll_history[-1] = (
            f"{DICE_EMOJI[r1]} {DICE_EMOJI[r2]}  \U0001f3b2 *rolling...*"
        )
        await interaction.edit_original_response(
            embed=_table_embed(self.table), view=self,
        )

        await asyncio.sleep(0.7)
        self.table.roll_history[-1] = _fmt_dice(d1, d2)

        # Snapshot bets for repeat, then resolve side bets
        for player in self.table.players.values():
            player.last_sides = [(sb.kind, sb.amount) for sb in player.side_bets]
        for player in self.table.players.values():
            side_credit = _resolve_side_bets_for_player(player, d1, d2)
            if side_credit > 0:
                await queries.update_casino_balance(str(player.user_id), side_credit)

        if self.table.phase == Phase.COME_OUT:
            finished = self._resolve_come_out(total)
        else:
            finished = self._resolve_point(total)

        if finished:
            await self._finish(interaction, followup=True)
        else:
            for child in self.children:
                if hasattr(child, "disabled"):
                    child.disabled = False  # type: ignore[union-attr]
            try:
                await interaction.edit_original_response(
                    embed=_table_embed(self.table), view=self,
                )
            except Exception:
                log.exception("crapless: failed to re-enable controls after roll — aborting table")
                self.stop()
                self.active_tables.pop(self.table.channel_id, None)

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
        bal = await queries.get_or_create_casino_wallet(str(uid))
        await interaction.response.send_modal(OddsBetModal(self.table, player, self, bal))

    # ── Row 1: Join + Leave ───────────────────────────────────────

    @ui.button(label="Join Pass", style=discord.ButtonStyle.primary, emoji="\U0001f3b2", row=1)
    async def join_pass_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
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
        # Run both DB lookups in parallel so the combined latency doesn't
        # push us past Discord's 3-second interaction response deadline.
        bal, default_bet = await asyncio.gather(
            queries.get_or_create_casino_wallet(str(uid)),
            queries.get_crapless_default_bet(str(uid)),
        )
        await interaction.response.send_modal(JoinModal(self.table, self, bal, default_bet))

    @ui.button(label="Leave", style=discord.ButtonStyle.secondary, emoji="\U0001f6aa", row=1)
    async def leave_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if player is None:
            await interaction.response.send_message("You're not at this table.", ephemeral=True)
            return

        if uid == self.table.shooter_id:
            await self._abort(interaction, "Shooter left \u2014 all bets refunded.")
            return

        refund = player.bet + player.odds_bet + sum(sb.amount for sb in player.side_bets)
        if refund > 0:
            await queries.update_casino_balance(str(uid), refund)
        del self.table.players[uid]

        if not self.table.players:
            await self._abort(interaction, "All players left \u2014 table closed.")
            return

        await interaction.response.edit_message(embed=_table_embed(self.table), view=self)

    # ── Row 2: One-roll side bets ──────────────────────────────────

    @ui.button(label="Field", style=discord.ButtonStyle.secondary, emoji="\U0001f3b2", row=2)
    async def field_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._handle_side_bet_button(interaction, "field")

    @ui.button(label="Any 7", style=discord.ButtonStyle.secondary, emoji="7\ufe0f\u20e3", row=2)
    async def any7_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._handle_side_bet_button(interaction, "any7")

    @ui.button(label="Any Craps", style=discord.ButtonStyle.secondary, emoji="\U0001f480", row=2)
    async def any_craps_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._handle_side_bet_button(interaction, "any_craps")

    @ui.button(label="Yo (11)", style=discord.ButtonStyle.secondary, emoji="\U0001f3af", row=2)
    async def yo_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._handle_side_bet_button(interaction, "yo")

    # ── Row 3: Multi-roll side bets ────────────────────────────────

    @ui.button(label="Come", style=discord.ButtonStyle.secondary, row=3)
    async def come_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._handle_side_bet_button(interaction, "come")

    @ui.button(label="Place/Hards", style=discord.ButtonStyle.secondary, emoji="\U0001f4cd", row=3)
    async def place_hardway_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.table.phase != Phase.POINT:
            await interaction.response.send_message(
                "Place and hardway bets are only available during the point phase.", ephemeral=True,
            )
            return
        uid = interaction.user.id
        if len(self.table.players) >= MAX_PLAYERS and uid not in self.table.players:
            await interaction.response.send_message("Table is full!", ephemeral=True)
            return
        bal = await queries.get_or_create_casino_wallet(str(uid))
        await interaction.response.send_modal(PlaceHardwayModal(self.table, self, bal))

    # ── Row 4: Clear + Repeat ────────────────────────────────────

    @ui.button(label="Clear My Bets", style=discord.ButtonStyle.danger, emoji="\U0001f5d1\ufe0f", row=4)
    async def clear_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if player is None:
            await interaction.response.send_message("You're not at this table.", ephemeral=True)
            return
        refund = player.odds_bet + sum(sb.amount for sb in player.side_bets)
        if refund == 0:
            await interaction.response.send_message("No side bets or odds to clear.", ephemeral=True)
            return
        player.odds_bet = 0
        player.side_bets.clear()
        player.coins_in -= refund
        await queries.update_casino_balance(str(uid), refund)
        await interaction.response.edit_message(embed=_table_embed(self.table), view=self)

    @ui.button(label="Repeat Bets", style=discord.ButtonStyle.primary, emoji="\U0001f501", row=4)
    async def repeat_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if player is None:
            await interaction.response.send_message("You're not at this table.", ephemeral=True)
            return
        if not player.last_sides:
            await interaction.response.send_message("No previous bets to repeat.", ephemeral=True)
            return

        active_kinds = {sb.kind for sb in player.side_bets}
        to_place = [
            (k, a) for k, a in player.last_sides
            if k not in active_kinds
            and (k not in POINT_PHASE_ONLY or self.table.phase == Phase.POINT)
        ]
        if not to_place:
            await interaction.response.send_message(
                "All previous bets are still active (nothing to repeat).", ephemeral=True,
            )
            return

        total_cost = sum(a for _, a in to_place)
        try:
            await queries.update_casino_balance(str(uid), -total_cost)
        except ValueError:
            await interaction.response.send_message("Not enough coins!", ephemeral=True)
            return

        for kind, amt in to_place:
            player.side_bets.append(SideBet(kind=kind, amount=amt))
        player.coins_in += total_cost
        await interaction.response.edit_message(embed=_table_embed(self.table), view=self)

    # ── Helpers ────────────────────────────────────────────────────

    async def _handle_side_bet_button(self, interaction: discord.Interaction, kind: str) -> None:
        if kind in POINT_PHASE_ONLY and self.table.phase != Phase.POINT:
            await interaction.response.send_message(
                "That bet is only available during the point phase.", ephemeral=True,
            )
            return
        uid = interaction.user.id
        if len(self.table.players) >= MAX_PLAYERS and uid not in self.table.players:
            await interaction.response.send_message("Table is full!", ephemeral=True)
            return
        bal = await queries.get_or_create_casino_wallet(str(uid))
        await interaction.response.send_modal(SideBetModal(self.table, kind, self, bal))

    # ── Resolution ────────────────────────────────────────────────

    def _resolve_come_out(self, total: int) -> bool:
        """Crapless come-out: only 7 is a natural. Everything else sets a point."""
        table = self.table
        if total == 7:
            table.phase = Phase.FINISHED
            table.outcome = "Natural 7!"
            for p in table.players.values():
                if p.bet_type == BetType.PASS_LINE:
                    p.payout = p.bet * 2
            return True
        # All other numbers (2-6, 8-12) establish a point — no craps on come-out
        table.point = total
        table.phase = Phase.POINT
        return False

    def _resolve_point(self, total: int) -> bool:
        table = self.table
        if total == table.point:
            table.phase = Phase.FINISHED
            table.outcome = f"Point {table.point}!"
            for p in table.players.values():
                if p.bet_type == BetType.PASS_LINE:
                    odds_win = _pass_odds_win(p.odds_bet, table.point) if p.odds_bet else 0
                    p.payout = p.bet * 2 + p.odds_bet + odds_win
            return True
        if total == 7:
            table.phase = Phase.FINISHED
            table.outcome = "Seven-out!"
            for p in table.players.values():
                if p.bet_type == BetType.PASS_LINE:
                    p.payout = 0
            return True
        return False

    async def _finish(self, interaction: discord.Interaction, *, followup: bool = False) -> None:
        table = self.table
        balances: dict[int, int] = {}

        # Always clean up first — prevents stuck tables even if DB calls fail.
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_tables.pop(table.channel_id, None)

        for player in table.players.values():
            refund = _refund_player(player)
            total_credit = player.payout + refund
            player.coins_out += total_credit
            try:
                if total_credit > 0:
                    balances[player.user_id] = await queries.update_casino_balance(
                        str(player.user_id), total_credit,
                    )
                else:
                    balances[player.user_id] = (
                        await queries.get_casino_balance(str(player.user_id))
                    ) or 0
                await queries.log_casino_result(
                    str(player.user_id), "crapless", player.coins_in, player.coins_out,
                )
            except Exception:
                log.exception("crapless: failed to settle player %s", player.user_id)
                balances[player.user_id] = 0

        embed = _table_embed(table, balances=balances)
        try:
            if followup:
                await interaction.edit_original_response(embed=embed, view=self)
            else:
                await interaction.response.edit_message(embed=embed, view=self)
        except Exception:
            log.exception("crapless: failed to update message after finish")

    async def _abort(self, interaction: discord.Interaction, reason: str) -> None:
        """End table early, refund everyone."""
        table = self.table
        for player in table.players.values():
            refund = player.bet + player.odds_bet + sum(sb.amount for sb in player.side_bets)
            if refund > 0:
                try:
                    await queries.update_casino_balance(str(player.user_id), refund)
                except Exception:
                    log.exception("crapless: unhandled error in _abort refund for player %s", player.user_id)
        embed = discord.Embed(
            title="Crapless Craps \u2014 Closed",
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
        table.phase = Phase.FINISHED
        self.active_tables.pop(table.channel_id, None)
        for player in table.players.values():
            refund = player.bet + player.odds_bet + sum(sb.amount for sb in player.side_bets)
            if refund > 0:
                try:
                    await queries.update_casino_balance(str(player.user_id), refund)
                except Exception:
                    log.exception("crapless: failed to refund player %s on timeout", player.user_id)
        if table.message:
            try:
                embed = discord.Embed(
                    title="Crapless Craps \u2014 Timed Out",
                    description="Shooter went AFK. All bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                log.exception("crapless: failed to update message on timeout")


# ── Cog ─────────────────────────────────────────────────────────────────────


class CraplessCrapsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, CraplessTable] = {}

    crapless_group = app_commands.Group(name="crapless", description="Crapless Craps table commands")

    @crapless_group.command(name="play", description="Open a Crapless Craps table — 7 is the only natural on come-out")
    async def crapless_play(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            existing = self.active_tables[channel_id]
            if existing.phase != Phase.FINISHED:
                await interaction.response.send_message(
                    "There's already a Crapless Craps table in this channel! Use the buttons to join.",
                    ephemeral=True,
                )
                return
            del self.active_tables[channel_id]

        await queries.get_or_create_casino_wallet(str(interaction.user.id))

        table = CraplessTable(
            channel_id=channel_id,
            shooter_id=interaction.user.id,
            shooter_name=interaction.user.display_name,
        )
        view = CraplessTableView(table, self.active_tables)
        embed = _table_embed(table)
        embed.description = (
            "**Crapless Craps** \u2014 7 is the only natural on come-out!\n"
            "All other numbers (2\u201312) establish a point. No Don't Pass.\n"
            "Join with Pass Line, then the shooter rolls!"
        )
        try:
            await interaction.response.send_message(embed=embed, view=view)
        except discord.NotFound:
            return  # interaction expired — don't leave a ghost table
        self.active_tables[channel_id] = table
        table.message = await interaction.original_response()

    @crapless_group.command(
        name="close",
        description="Force-close a stuck Crapless Craps table and refund all players (admin only)",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def crapless_close(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        table = self.active_tables.get(channel_id)
        if table is None:
            await interaction.response.send_message(
                "No active Crapless Craps table in this channel.", ephemeral=True,
            )
            return

        table.phase = Phase.FINISHED
        del self.active_tables[channel_id]

        for player in table.players.values():
            refund = player.bet + player.odds_bet + sum(sb.amount for sb in player.side_bets)
            if refund > 0:
                try:
                    await queries.update_casino_balance(str(player.user_id), refund)
                except Exception:
                    log.exception("crapless close: failed to refund player %s", player.user_id)

        if table.message:
            try:
                embed = discord.Embed(
                    title="Crapless Craps \u2014 Closed by Admin",
                    description=f"Table force-closed by {interaction.user.display_name}. All bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                log.exception("crapless close: failed to update table message")

        await interaction.response.send_message(
            f"Crapless Craps table closed. All bets refunded to {len(table.players)} player(s).",
            ephemeral=True,
        )

    @crapless_group.command(name="setdefault", description="Set your default Crapless Craps bet amount")
    @app_commands.describe(amount="Default bet in coins (1\u2013100 000)")
    async def crapless_setdefault(self, interaction: discord.Interaction, amount: int) -> None:
        if amount < 1:
            await interaction.response.send_message(
                "Default bet must be at least 1 coin.", ephemeral=True
            )
            return
        if amount > 100_000:
            await interaction.response.send_message(
                "Default bet cannot exceed 100 000 coins.", ephemeral=True
            )
            return
        await queries.set_crapless_default_bet(str(interaction.user.id), amount)
        await interaction.response.send_message(
            f"Default Crapless Craps bet set to **{amount:,}** coins. "
            "It will be pre-filled next time you join a table.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CraplessCrapsCog(bot))
