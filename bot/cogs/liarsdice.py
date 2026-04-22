"""Casino cog — multiplayer /liarsdice game."""

import asyncio
import random
import time
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries

# ── Constants ────────────────────────────────────────────────────────────────

MAX_PLAYERS = 8
MAX_BET = 500
MIN_PLAYERS = 2
HOUSE_EDGE = 0.05
STARTING_DICE = 5
SHOT_CLOCK = 60  # seconds per turn

DICE_EMOJI = {1: "\u2680", 2: "\u2681", 3: "\u2682", 4: "\u2683", 5: "\u2684", 6: "\u2685"}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _roll_dice(count: int) -> list[int]:
    """Roll *count* six-sided dice."""
    return [random.randint(1, 6) for _ in range(count)]


def _dice_str(dice: list[int]) -> str:
    """Render a list of die values as emoji."""
    return " ".join(DICE_EMOJI[d] for d in sorted(dice))


def _hidden_dice_str(count: int) -> str:
    """Show dice-count hidden dice."""
    return "\U0001f3b2" * count


def _bid_str(quantity: int, face: int) -> str:
    """Human-readable bid description."""
    face_name = f"{DICE_EMOJI[face]} ({face}s)"
    if face == 1:
        face_name = f"{DICE_EMOJI[1]} (aces/wild)"
    return f"**{quantity}x** {face_name}"


def _count_matching(all_dice: dict[int, list[int]], face: int) -> int:
    """Count dice matching *face* across all players, with aces wild."""
    total = 0
    for dice in all_dice.values():
        for d in dice:
            if d == face or (face != 1 and d == 1):
                total += 1
    return total


def _bid_is_higher(new_qty: int, new_face: int, old_qty: int, old_face: int) -> bool:
    """Return True if (new_qty, new_face) is strictly higher than (old_qty, old_face)."""
    if new_qty > old_qty:
        return True
    if new_qty == old_qty and new_face > old_face:
        return True
    return False


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class LiarPlayer:
    user_id: int
    display_name: str
    bet: int
    dice: list[int] = field(default_factory=list)
    dice_count: int = STARTING_DICE
    eliminated: bool = False
    payout: int = 0


@dataclass
class LiarTable:
    channel_id: int
    host_id: int
    host_name: str
    phase: str = "betting"  # betting | playing | finished
    players: dict[int, LiarPlayer] = field(default_factory=dict)
    turn_order: list[int] = field(default_factory=list)  # user_ids in seat order
    current_turn_idx: int = 0
    current_bid: tuple[int, int] | None = None  # (quantity, face)
    current_bidder: int | None = None
    message: discord.Message | None = None
    round_num: int = 1
    last_bets: dict[int, tuple[str, int]] = field(default_factory=dict)
    winners: list[int] = field(default_factory=list)
    shot_clock_expires: float | None = None


# ── Embeds ───────────────────────────────────────────────────────────────────


def _betting_embed(table: LiarTable) -> discord.Embed:
    pot = sum(p.bet for p in table.players.values())
    embed = discord.Embed(
        title=f"Liar's Dice \u2014 Join the Table (Round {table.round_num})",
        description=(
            "Bluff, bid, and call out liars! Last player with dice wins the pot.\n"
            "Aces (1s) are **wild** and count toward any face."
        ),
        colour=discord.Colour.dark_orange(),
    )
    if pot:
        embed.add_field(name="Pot", value=f"{pot}c (5% house rake)", inline=True)
    if table.players:
        lines = [
            f"\U0001f3b2 **{p.display_name}** \u2014 {p.bet}c"
            for p in table.players.values()
        ]
        embed.add_field(name="Players", value="\n".join(lines), inline=False)
    else:
        embed.add_field(
            name="Players",
            value="*No players yet \u2014 click Join!*",
            inline=False,
        )
    embed.set_footer(text=f"Host: {table.host_name} \u2502 Min {MIN_PLAYERS} players")
    return embed


def _playing_embed(table: LiarTable) -> discord.Embed:
    current_uid = _current_player_uid(table)
    total_dice = sum(p.dice_count for p in table.players.values() if not p.eliminated)

    embed = discord.Embed(
        title=f"Liar's Dice \u2014 Round {table.round_num}",
        colour=discord.Colour.orange(),
    )

    # Current bid
    if table.current_bid:
        qty, face = table.current_bid
        bidder = table.players[table.current_bidder]
        embed.description = (
            f"Current bid: {_bid_str(qty, face)}\n"
            f"Bid by: **{bidder.display_name}**"
        )
    else:
        embed.description = "No bids yet \u2014 first player must open the bidding."

    if table.shot_clock_expires:
        ts = int(table.shot_clock_expires)
        embed.description += f"\n\u23f0 <t:{ts}:R>"

    # Players & dice counts
    lines: list[str] = []
    for uid in table.turn_order:
        p = table.players[uid]
        if p.eliminated:
            lines.append(f"\u274c ~~{p.display_name}~~ \u2014 eliminated")
        elif uid == current_uid:
            lines.append(
                f"\u27a1\ufe0f **{p.display_name}** \u2014 {_hidden_dice_str(p.dice_count)}"
            )
        else:
            lines.append(f"\U0001f3b2 {p.display_name} \u2014 {_hidden_dice_str(p.dice_count)}")

    embed.add_field(name="Players", value="\n".join(lines), inline=False)
    embed.add_field(name="Total dice in play", value=str(total_dice), inline=True)

    turn_player = table.players[current_uid]
    embed.set_footer(text=f"{turn_player.display_name}'s turn \u2502 Host: {table.host_name}")
    return embed


def _challenge_embed(
    table: LiarTable,
    caller_uid: int,
    bidder_uid: int,
    bid_qty: int,
    bid_face: int,
    actual_count: int,
    loser_uid: int,
    all_dice: dict[int, list[int]],
    lost_last_die: bool,
) -> discord.Embed:
    caller = table.players[caller_uid]
    bidder = table.players[bidder_uid]
    loser = table.players[loser_uid]

    bid_met = actual_count >= bid_qty
    if bid_met:
        verdict = f"Bid was **met** ({actual_count} >= {bid_qty}) \u2014 caller **{caller.display_name}** loses a die!"
    else:
        verdict = f"Bid was **NOT met** ({actual_count} < {bid_qty}) \u2014 bidder **{bidder.display_name}** loses a die!"

    embed = discord.Embed(
        title="Liar's Dice \u2014 LIAR! Called!",
        colour=discord.Colour.red(),
    )

    embed.description = (
        f"**{caller.display_name}** calls **LIAR!** on "
        f"**{bidder.display_name}**'s bid of {_bid_str(bid_qty, bid_face)}\n\n"
        f"{verdict}"
    )

    # Reveal all dice
    reveal_lines: list[str] = []
    for uid in table.turn_order:
        p = table.players[uid]
        dice = all_dice.get(uid, [])
        if not dice:
            reveal_lines.append(f"\u274c ~~{p.display_name}~~ \u2014 eliminated")
            continue
        highlighted: list[str] = []
        for d in sorted(dice):
            if d == bid_face or (bid_face != 1 and d == 1):
                highlighted.append(f"**{DICE_EMOJI[d]}**")
            else:
                highlighted.append(DICE_EMOJI[d])
        reveal_lines.append(f"{p.display_name}: {' '.join(highlighted)}")

    embed.add_field(name="All Dice Revealed", value="\n".join(reveal_lines), inline=False)

    # Count summary
    wild_note = " (aces are wild)" if bid_face != 1 else ""
    embed.add_field(
        name="Count",
        value=f"{DICE_EMOJI[bid_face]} matching: **{actual_count}** vs bid of **{bid_qty}**{wild_note}",
        inline=False,
    )

    if lost_last_die:
        embed.add_field(
            name="Eliminated!",
            value=f"**{loser.display_name}** has lost all their dice and is out!",
            inline=False,
        )
    else:
        embed.add_field(
            name="Result",
            value=f"**{loser.display_name}** loses a die ({loser.dice_count} remaining).",
            inline=False,
        )

    return embed


def _finished_embed(
    table: LiarTable, *, balances: dict[int, int] | None = None,
) -> discord.Embed:
    winner = table.players[table.winners[0]]
    embed = discord.Embed(
        title=f"Liar's Dice \u2014 Winner! (Round {table.round_num})",
        description=f"**{winner.display_name}** is the last player standing and wins **{winner.payout}c**!",
        colour=discord.Colour.gold(),
    )

    lines: list[str] = []
    for uid in table.turn_order:
        p = table.players[uid]
        bal = balances.get(uid, 0) if balances else 0
        net = p.payout - p.bet
        sign = "+" if net >= 0 else ""
        if p.payout > 0:
            lines.append(
                f"\U0001f3c6 **{p.display_name}** \u2014 {p.bet}c \u2192 {p.payout}c "
                f"(**{sign}{net}c**) \u2014 bal: {bal}c"
            )
        else:
            lines.append(
                f"\u274c **{p.display_name}** \u2014 {p.bet}c \u2192 0c "
                f"(**-{p.bet}c**) \u2014 bal: {bal}c"
            )
    embed.add_field(name="Results", value="\n".join(lines), inline=False)
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


# ── Turn helpers ─────────────────────────────────────────────────────────────


def _current_player_uid(table: LiarTable) -> int:
    """Return the user_id of whoever's turn it is."""
    return table.turn_order[table.current_turn_idx]


def _advance_turn(table: LiarTable) -> None:
    """Advance to the next non-eliminated player."""
    n = len(table.turn_order)
    for _ in range(n):
        table.current_turn_idx = (table.current_turn_idx + 1) % n
        uid = table.turn_order[table.current_turn_idx]
        if not table.players[uid].eliminated:
            return


def _set_turn_to(table: LiarTable, uid: int) -> None:
    """Set the turn to a specific player (e.g. loser of challenge)."""
    try:
        idx = table.turn_order.index(uid)
        table.current_turn_idx = idx
    except ValueError:
        pass


def _alive_players(table: LiarTable) -> list[int]:
    """Return user_ids of non-eliminated players."""
    return [uid for uid in table.turn_order if not table.players[uid].eliminated]


# ── Modals ───────────────────────────────────────────────────────────────────


class JoinLiarModal(ui.Modal):
    amount = ui.TextInput(
        label="Bet amount (coins)",
        placeholder="e.g. 100",
        required=True,
        max_length=10,
    )

    def __init__(
        self, table: LiarTable, view: "LiarTableView", balance: int,
    ) -> None:
        super().__init__(title="Join Liar's Dice")
        self.table = table
        self.table_view = view
        self.amount.placeholder = f"e.g. 100 (bal: {balance}c)"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            amt = int(self.amount.value)
        except ValueError:
            await interaction.response.send_message(
                "Enter a whole number.", ephemeral=True,
            )
            return
        if amt < 1:
            await interaction.response.send_message(
                "Must be at least 1 coin.", ephemeral=True,
            )
            return
        if amt > MAX_BET:
            await interaction.response.send_message(
                f"Max bet is {MAX_BET}c.", ephemeral=True,
            )
            return

        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message(
                "You're already in this round!", ephemeral=True,
            )
            return

        try:
            await queries.update_casino_balance(str(uid), -amt)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins! (have {bal}c)", ephemeral=True,
            )
            return

        self.table.players[uid] = LiarPlayer(
            user_id=uid,
            display_name=interaction.user.display_name,
            bet=amt,
        )

        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self.table_view,
        )


class BidModal(ui.Modal):
    quantity_input = ui.TextInput(
        label="Quantity (how many dice)",
        placeholder="e.g. 3",
        required=True,
        max_length=3,
    )
    face_input = ui.TextInput(
        label="Face value (1-6, aces/1s are wild)",
        placeholder="e.g. 5",
        required=True,
        max_length=1,
    )

    def __init__(self, table: LiarTable, view: "LiarTableView") -> None:
        super().__init__(title="Place Your Bid")
        self.table = table
        self.table_view = view
        # Hint the current bid in placeholder
        if table.current_bid:
            qty, face = table.current_bid
            self.quantity_input.placeholder = f"Current: {qty}x {face}s \u2014 must go higher"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # Parse inputs
        try:
            new_qty = int(self.quantity_input.value)
        except ValueError:
            await interaction.response.send_message(
                "Quantity must be a whole number.", ephemeral=True,
            )
            return
        try:
            new_face = int(self.face_input.value)
        except ValueError:
            await interaction.response.send_message(
                "Face must be a number 1-6.", ephemeral=True,
            )
            return

        if new_face < 1 or new_face > 6:
            await interaction.response.send_message(
                "Face value must be between 1 and 6.", ephemeral=True,
            )
            return
        if new_qty < 1:
            await interaction.response.send_message(
                "Quantity must be at least 1.", ephemeral=True,
            )
            return

        total_dice = sum(
            p.dice_count for p in self.table.players.values() if not p.eliminated
        )
        if new_qty > total_dice:
            await interaction.response.send_message(
                f"Quantity can't exceed total dice in play ({total_dice}).",
                ephemeral=True,
            )
            return

        # Validate bid is strictly higher
        if self.table.current_bid:
            old_qty, old_face = self.table.current_bid
            if not _bid_is_higher(new_qty, new_face, old_qty, old_face):
                await interaction.response.send_message(
                    f"Bid must be higher than current: {_bid_str(old_qty, old_face)}.\n"
                    "Either raise the quantity, or keep the same quantity with a higher face.",
                    ephemeral=True,
                )
                return

        # Verify it's still this player's turn (shot clock may have fired)
        uid = interaction.user.id
        if _current_player_uid(self.table) != uid:
            await interaction.response.send_message(
                "Your turn has passed!", ephemeral=True,
            )
            return

        # Accept the bid
        self.table.current_bid = (new_qty, new_face)
        self.table.current_bidder = uid
        _advance_turn(self.table)

        self.table_view._start_shot_clock()
        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_playing_embed(self.table), view=self.table_view,
        )


# ── View ─────────────────────────────────────────────────────────────────────


class LiarTableView(ui.View):
    def __init__(
        self, table: LiarTable, active_tables: dict[int, LiarTable],
    ) -> None:
        super().__init__(timeout=600)
        self.table = table
        self.active_tables = active_tables
        self._shot_clock_task: asyncio.Task | None = None
        self._update_buttons()

    def _update_buttons(self) -> None:
        phase = self.table.phase
        betting = phase == "betting"
        playing = phase == "playing"
        finished = phase == "finished"

        # Row 0: Start, Join, Re-bet, Leave
        self.start_btn.disabled = (
            not betting or len(self.table.players) < MIN_PLAYERS
        )
        self.join_btn.disabled = not betting
        self.rebet_btn.disabled = not betting or not self.table.last_bets
        self.leave_btn.disabled = playing

        # Row 1: Bid, Liar!, My Dice
        self.bid_btn.disabled = not playing
        self.liar_btn.disabled = not playing or self.table.current_bid is None
        self.my_dice_btn.disabled = not playing

        # Row 2: New Round, Close Table
        self.new_round_btn.disabled = not finished
        self.close_btn.disabled = playing

    # ── Shot clock ────────────────────────────────────────────────────────

    def _start_shot_clock(self) -> None:
        """Start (or restart) the per-turn shot clock."""
        self._cancel_shot_clock()
        self.table.shot_clock_expires = time.time() + SHOT_CLOCK
        self._shot_clock_task = asyncio.create_task(self._shot_clock_coro())

    def _cancel_shot_clock(self) -> None:
        if self._shot_clock_task and not self._shot_clock_task.done():
            self._shot_clock_task.cancel()
        self._shot_clock_task = None
        self.table.shot_clock_expires = None

    async def _shot_clock_coro(self) -> None:
        try:
            await asyncio.sleep(SHOT_CLOCK)
        except asyncio.CancelledError:
            return
        if self.table.phase != "playing":
            return
        self.table.shot_clock_expires = None
        await self._handle_timeout()

    async def _handle_timeout(self) -> None:
        """Handle shot clock expiry for current player."""
        table = self.table
        current_uid = _current_player_uid(table)
        current_player = table.players[current_uid]

        if table.current_bid is not None:
            # Auto-call Liar
            await self._resolve_challenge(None, caller_uid=current_uid, auto_liar=True)
        else:
            # No bid to call — lose a die for stalling
            current_player.dice_count -= 1
            lost_last = current_player.dice_count <= 0
            if lost_last:
                current_player.eliminated = True

            alive = _alive_players(table)
            if len(alive) <= 1:
                await self._resolve_game(None, None, alive)
                return

            # New sub-round: re-roll, advance turn
            for uid in alive:
                table.players[uid].dice = _roll_dice(table.players[uid].dice_count)
            table.current_bid = None
            table.current_bidder = None
            if current_player.eliminated:
                _set_turn_to(table, current_uid)
                _advance_turn(table)
            else:
                _advance_turn(table)

            embed = _playing_embed(table)
            timeout_msg = f"\u23f0 **{current_player.display_name}** ran out of time"
            if lost_last:
                timeout_msg += " and is eliminated!"
            else:
                timeout_msg += f" and loses a die! ({current_player.dice_count} remaining)"
            embed.description = timeout_msg + "\n\n" + (embed.description or "")

            self._start_shot_clock()
            self._update_buttons()
            if table.message:
                await table.message.edit(embed=embed, view=self)

    async def _edit_msg(self, interaction: discord.Interaction | None, **kwargs) -> None:
        """Edit the game message via interaction response or direct message edit."""
        if interaction:
            await interaction.response.edit_message(**kwargs)
        elif self.table.message:
            await self.table.message.edit(**kwargs)

    # ── Row 0 ────────────────────────────────────────────────────────────────

    @ui.button(
        label="Start", style=discord.ButtonStyle.success, emoji="\u25b6\ufe0f", row=0,
    )
    async def start_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can start!", ephemeral=True,
            )
            return
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Already started!", ephemeral=True,
            )
            return
        if len(self.table.players) < MIN_PLAYERS:
            await interaction.response.send_message(
                f"Need at least {MIN_PLAYERS} players!", ephemeral=True,
            )
            return
        await self._start_game(interaction)

    @ui.button(
        label="Join", style=discord.ButtonStyle.primary, emoji="\U0001f3b2", row=0,
    )
    async def join_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Game in progress! Wait for the next round.", ephemeral=True,
            )
            return
        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message(
                "You're already in!", ephemeral=True,
            )
            return
        if len(self.table.players) >= MAX_PLAYERS:
            await interaction.response.send_message(
                "Table is full!", ephemeral=True,
            )
            return
        bal = await queries.get_or_create_casino_wallet(str(uid))
        await interaction.response.send_modal(
            JoinLiarModal(self.table, self, bal),
        )

    @ui.button(
        label="Re-bet", style=discord.ButtonStyle.primary, emoji="\U0001f504", row=0,
    )
    async def rebet_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Game in progress!", ephemeral=True,
            )
            return
        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message(
                "You're already in!", ephemeral=True,
            )
            return
        last = self.table.last_bets.get(uid)
        if last is None:
            await interaction.response.send_message(
                "No previous bet \u2014 use Join instead.", ephemeral=True,
            )
            return
        if len(self.table.players) >= MAX_PLAYERS:
            await interaction.response.send_message(
                "Table is full!", ephemeral=True,
            )
            return
        name, amt = last
        try:
            await queries.update_casino_balance(str(uid), -amt)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins for {amt}c re-bet! (have {bal}c)",
                ephemeral=True,
            )
            return
        self.table.players[uid] = LiarPlayer(
            user_id=uid, display_name=name, bet=amt,
        )
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    @ui.button(
        label="Leave", style=discord.ButtonStyle.secondary, emoji="\U0001f6aa", row=0,
    )
    async def leave_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if player is None:
            await interaction.response.send_message(
                "You're not at this table.", ephemeral=True,
            )
            return
        if self.table.phase == "playing":
            await interaction.response.send_message(
                "Can't leave mid-game!", ephemeral=True,
            )
            return
        if self.table.phase == "betting":
            await queries.update_casino_balance(str(uid), player.bet)
            del self.table.players[uid]
            self._update_buttons()
            await interaction.response.edit_message(
                embed=_betting_embed(self.table), view=self,
            )
            return
        await interaction.response.send_message(
            "Round is over. Wait for New Round or close.", ephemeral=True,
        )

    # ── Row 1 ────────────────────────────────────────────────────────────────

    @ui.button(
        label="Bid", style=discord.ButtonStyle.success, emoji="\U0001f4e2", row=1,
    )
    async def bid_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "playing":
            await interaction.response.send_message(
                "No game in progress.", ephemeral=True,
            )
            return
        uid = interaction.user.id
        if uid not in self.table.players or self.table.players[uid].eliminated:
            await interaction.response.send_message(
                "You're not in this game!", ephemeral=True,
            )
            return
        current = _current_player_uid(self.table)
        if uid != current:
            await interaction.response.send_message(
                "Not your turn!", ephemeral=True,
            )
            return
        await interaction.response.send_modal(BidModal(self.table, self))

    @ui.button(
        label="Liar!", style=discord.ButtonStyle.danger, emoji="\U0001f4a5", row=1,
    )
    async def liar_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "playing":
            await interaction.response.send_message(
                "No game in progress.", ephemeral=True,
            )
            return
        uid = interaction.user.id
        if uid not in self.table.players or self.table.players[uid].eliminated:
            await interaction.response.send_message(
                "You're not in this game!", ephemeral=True,
            )
            return
        current = _current_player_uid(self.table)
        if uid != current:
            await interaction.response.send_message(
                "Not your turn!", ephemeral=True,
            )
            return
        if self.table.current_bid is None:
            await interaction.response.send_message(
                "Nothing to challenge \u2014 no bid has been made yet!", ephemeral=True,
            )
            return
        self._cancel_shot_clock()
        await self._resolve_challenge(interaction, caller_uid=uid)

    @ui.button(
        label="My Dice", style=discord.ButtonStyle.secondary, emoji="\U0001f440", row=1,
    )
    async def my_dice_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if player is None or player.eliminated:
            await interaction.response.send_message(
                "You're not in this game!", ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"**Your Dice:** {_dice_str(player.dice)}",
            ephemeral=True,
        )

    # ── Row 2 ────────────────────────────────────────────────────────────────

    @ui.button(
        label="New Round", style=discord.ButtonStyle.success, emoji="\u25b6\ufe0f", row=2,
    )
    async def new_round_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can start a new round!", ephemeral=True,
            )
            return
        if self.table.phase != "finished":
            await interaction.response.send_message(
                "Round still in progress!", ephemeral=True,
            )
            return
        self._start_new_round()
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    @ui.button(
        label="Close Table", style=discord.ButtonStyle.danger, emoji="\u2716\ufe0f", row=2,
    )
    async def close_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can close the table!", ephemeral=True,
            )
            return
        if self.table.phase == "playing":
            await interaction.response.send_message(
                "Can't close mid-game!", ephemeral=True,
            )
            return
        if self.table.phase == "betting":
            for p in self.table.players.values():
                try:
                    await queries.update_casino_balance(str(p.user_id), p.bet)
                except Exception:
                    pass
        await self._close(interaction, "Table closed by host.")

    # ── Game logic ───────────────────────────────────────────────────────────

    async def _start_game(self, interaction: discord.Interaction) -> None:
        table = self.table
        table.phase = "playing"

        # Establish turn order and deal dice
        table.turn_order = list(table.players.keys())
        random.shuffle(table.turn_order)
        for p in table.players.values():
            p.dice_count = STARTING_DICE
            p.dice = _roll_dice(STARTING_DICE)
            p.eliminated = False
            p.payout = 0

        table.current_turn_idx = 0
        table.current_bid = None
        table.current_bidder = None

        self._start_shot_clock()
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_playing_embed(table), view=self,
        )

    async def _resolve_challenge(
        self, interaction: discord.Interaction | None, *, caller_uid: int, auto_liar: bool = False,
    ) -> None:
        table = self.table
        bid_qty, bid_face = table.current_bid  # type: ignore[misc]
        bidder_uid = table.current_bidder  # type: ignore[assignment]

        # Collect all dice from non-eliminated players (snapshot before any changes)
        all_dice: dict[int, list[int]] = {}
        for uid in table.turn_order:
            p = table.players[uid]
            if not p.eliminated:
                all_dice[uid] = list(p.dice)

        actual_count = _count_matching(all_dice, bid_face)
        bid_met = actual_count >= bid_qty

        # Determine loser
        if bid_met:
            loser_uid = caller_uid  # caller was wrong, bid was legit
        else:
            loser_uid = bidder_uid  # bidder was bluffing

        loser = table.players[loser_uid]
        loser.dice_count -= 1
        lost_last_die = loser.dice_count <= 0
        if lost_last_die:
            loser.eliminated = True

        # Build challenge embed
        challenge_embed = _challenge_embed(
            table, caller_uid, bidder_uid, bid_qty, bid_face,
            actual_count, loser_uid, all_dice, lost_last_die,
        )

        # Check if game is over
        alive = _alive_players(table)
        if len(alive) <= 1:
            # Game over — resolve payouts
            await self._resolve_game(interaction, challenge_embed, alive)
            return

        # Game continues — new sub-round of bidding
        # Re-roll all remaining players' dice
        for uid in alive:
            p = table.players[uid]
            p.dice = _roll_dice(p.dice_count)

        # Reset bid; loser goes first (or next alive if just eliminated)
        table.current_bid = None
        table.current_bidder = None
        if not table.players[loser_uid].eliminated:
            _set_turn_to(table, loser_uid)
        else:
            # Loser eliminated — next alive player in turn order after loser
            _set_turn_to(table, loser_uid)
            _advance_turn(table)

        # Add a continuation note to the challenge embed
        next_player = table.players[_current_player_uid(table)]
        challenge_embed.add_field(
            name="Next up",
            value=f"New bidding round! **{next_player.display_name}** opens.",
            inline=False,
        )

        if auto_liar:
            timeout_name = table.players[caller_uid].display_name
            challenge_embed.description = (
                f"\u23f0 **{timeout_name}** ran out of time \u2014 auto Liar!\n\n"
                + (challenge_embed.description or "")
            )

        self._start_shot_clock()
        self._update_buttons()
        await self._edit_msg(interaction, embed=challenge_embed, view=self)

    async def _resolve_game(
        self,
        interaction: discord.Interaction | None,
        challenge_embed: discord.Embed | None,
        alive: list[int],
    ) -> None:
        table = self.table
        table.phase = "finished"
        self._cancel_shot_clock()

        if alive:
            winner_uid = alive[0]
        else:
            # Shouldn't happen, but fallback to last bidder
            winner_uid = table.turn_order[0]

        table.winners = [winner_uid]

        total_pot = sum(p.bet for p in table.players.values())
        house_take = max(1, int(total_pot * HOUSE_EDGE))
        prize_pool = total_pot - house_take
        table.players[winner_uid].payout = prize_pool

        # Credit winner and log results
        balances: dict[int, int] = {}
        for uid, player in table.players.items():
            if player.payout > 0:
                balances[uid] = await queries.update_casino_balance(
                    str(uid), player.payout,
                )
            else:
                bal = await queries.get_casino_balance(str(uid))
                balances[uid] = bal or 0
            await queries.log_casino_result(
                str(uid), "liarsdice", player.bet, player.payout,
            )

        # Save last bets for re-bet
        for uid, player in table.players.items():
            table.last_bets[uid] = (player.display_name, player.bet)

        # Show the challenge result first, then update to finished embed
        # We show the challenge embed with an added "Game Over" field, then the view
        # updates to show finished state buttons
        finished_embed = _finished_embed(table, balances=balances)

        self._update_buttons()
        if challenge_embed:
            # Show challenge result first, then finished embed after delay
            winner = table.players[winner_uid]
            challenge_embed.add_field(
                name="\U0001f3c6 Game Over!",
                value=f"**{winner.display_name}** wins **{prize_pool}c**!",
                inline=False,
            )
            await self._edit_msg(interaction, embed=challenge_embed, view=self)

            if table.message:
                try:
                    await asyncio.sleep(5)
                    await table.message.edit(embed=finished_embed, view=self)
                except discord.HTTPException:
                    pass
        else:
            # Direct to finished embed (e.g. timeout elimination)
            await self._edit_msg(interaction, embed=finished_embed, view=self)

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def _start_new_round(self) -> None:
        self._cancel_shot_clock()
        table = self.table
        table.players.clear()
        table.phase = "betting"
        table.round_num += 1
        table.turn_order.clear()
        table.current_turn_idx = 0
        table.current_bid = None
        table.current_bidder = None
        table.winners.clear()

    async def _refund_all(self) -> None:
        for p in self.table.players.values():
            try:
                await queries.update_casino_balance(str(p.user_id), p.bet)
            except Exception:
                pass

    async def _close(
        self, interaction: discord.Interaction, reason: str,
    ) -> None:
        self._cancel_shot_clock()
        embed = discord.Embed(
            title="Liar's Dice Table \u2014 Closed",
            description=reason,
            colour=discord.Colour.dark_grey(),
        )
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_tables.pop(self.table.channel_id, None)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        self._cancel_shot_clock()
        table = self.table

        if table.phase == "finished":
            self.active_tables.pop(table.channel_id, None)
            if table.message:
                try:
                    embed = discord.Embed(
                        title="Liar's Dice Table \u2014 Timed Out",
                        description="Table timed out between rounds.",
                        colour=discord.Colour.dark_grey(),
                    )
                    await table.message.edit(embed=embed, view=None)
                except Exception:
                    pass
            return

        # Betting or playing — refund all
        await self._refund_all()
        self.active_tables.pop(table.channel_id, None)
        if table.message:
            try:
                embed = discord.Embed(
                    title="Liar's Dice Table \u2014 Timed Out",
                    description="Table timed out. All bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                pass


# ── Cog ──────────────────────────────────────────────────────────────────────


class LiarDiceCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, LiarTable] = {}

    @app_commands.command(
        name="liarsdice", description="Open a Liar's Dice table (multiplayer)",
    )
    async def liarsdice(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            await interaction.response.send_message(
                "There's already a Liar's Dice table in this channel!",
                ephemeral=True,
            )
            return

        await queries.get_or_create_casino_wallet(str(interaction.user.id))

        table = LiarTable(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
        )
        self.active_tables[channel_id] = table

        view = LiarTableView(table, self.active_tables)
        embed = _betting_embed(table)
        await interaction.response.send_message(embed=embed, view=view)
        table.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LiarDiceCog(bot))
