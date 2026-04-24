"""Casino cog — multiplayer /solitaire-chess puzzle race."""

import asyncio
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries
from bot.cogs._pool import compute_side_pot_payouts
from shared.solitairechess_logic import (
    Board, Pos,
    copy_board, count_pieces, format_board_emoji, get_all_moves,
    get_captures, get_hint, generate_puzzle, make_move, parse_move,
    piece_legend, pos_to_str, undo_move,
    PIECE_EMOJI, PIECE_NAME,
)

# ── Constants ────────────────────────────────────────────────────────────────

MAX_PLAYERS = 8
MIN_PLAYERS = 1
ROUND_TIMEOUT = 300  # 5 minutes

DIFFICULTY_CONFIG: dict[str, dict] = {
    "easy":   {"pieces": 4, "label": "Easy (4 pieces, 3 moves)"},
    "medium": {"pieces": 5, "label": "Medium (5 pieces, 4 moves)"},
    "hard":   {"pieces": 6, "label": "Hard (6 pieces, 5 moves)"},
    "expert": {"pieces": 7, "label": "Expert (7 pieces, 6 moves)"},
}

DIFFICULTY_CHOICES = [
    app_commands.Choice(name="Easy \u2014 4 pieces, 3 moves", value="easy"),
    app_commands.Choice(name="Medium \u2014 5 pieces, 4 moves", value="medium"),
    app_commands.Choice(name="Hard \u2014 6 pieces, 5 moves", value="hard"),
    app_commands.Choice(name="Expert \u2014 7 pieces, 6 moves", value="expert"),
]

# Solo payout multiplier (bet * N) for solving
SOLO_PAYOUT = 2

# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class SolChessPlayer:
    user_id: int
    display_name: str
    bet: int
    board: Board = field(default_factory=list)
    # History: (from_pos, to_pos, captured_piece_type)
    history: list[tuple[Pos, Pos, str]] = field(default_factory=list)
    solved: bool = False
    gave_up: bool = False
    move_count: int = 0

    @property
    def pieces_left(self) -> int:
        return count_pieces(self.board)

    @property
    def done(self) -> bool:
        return self.solved or self.gave_up

    @property
    def stuck(self) -> bool:
        return not self.done and self.pieces_left > 1 and not get_all_moves(self.board)


@dataclass
class SolChessTable:
    channel_id: int
    host_id: int
    host_name: str
    difficulty: str
    starting_board: Board = field(default_factory=list)
    phase: str = "betting"  # betting | playing | finished
    players: dict[int, SolChessPlayer] = field(default_factory=dict)
    message: discord.Message | None = None
    round_num: int = 1
    last_bets: dict[int, tuple[str, int]] = field(default_factory=dict)
    winners: list[int] = field(default_factory=list)
    round_task: asyncio.Task | None = field(default=None, repr=False)


# ── Embeds ───────────────────────────────────────────────────────────────────


def _betting_embed(table: SolChessTable) -> discord.Embed:
    cfg = DIFFICULTY_CONFIG[table.difficulty]
    pot = sum(p.bet for p in table.players.values())
    embed = discord.Embed(
        title=f"\u265e Solitaire Chess \u2014 Join the Table (Round {table.round_num})",
        description=(
            "Capture pieces until only **one** remains!\n"
            f"Difficulty: **{cfg['label']}**"
        ),
        colour=discord.Colour.dark_teal(),
    )
    if pot:
        embed.add_field(name="Pot", value=f"{pot}c", inline=True)
    if table.players:
        lines = [
            f"\u265e **{p.display_name}** \u2014 {p.bet}c"
            for p in table.players.values()
        ]
        embed.add_field(name="Players", value="\n".join(lines), inline=False)
    else:
        embed.add_field(
            name="Players",
            value="*No players yet \u2014 click Join!*",
            inline=False,
        )
    embed.set_footer(
        text=f"Host: {table.host_name} \u2502 {ROUND_TIMEOUT // 60}-min time limit",
    )
    return embed


def _playing_embed(table: SolChessTable) -> discord.Embed:
    cfg = DIFFICULTY_CONFIG[table.difficulty]
    embed = discord.Embed(
        title=f"\u265e Solitaire Chess \u2014 Round {table.round_num}",
        description=(
            f"**{cfg['label']}** \u2014 Capture until 1 piece remains!\n\n"
            f"{format_board_emoji(table.starting_board)}\n\n"
            f"{piece_legend()}"
        ),
        colour=discord.Colour.dark_teal(),
    )

    lines: list[str] = []
    for p in table.players.values():
        if p.solved:
            lines.append(
                f"\U0001f3c6 **{p.display_name}** \u2014 "
                f"Solved in **{p.move_count}** moves!"
            )
        elif p.gave_up:
            lines.append(f"\U0001f3f3\ufe0f **{p.display_name}** \u2014 Gave up")
        elif p.stuck:
            lines.append(
                f"\U0001f6d1 **{p.display_name}** \u2014 "
                f"Stuck! {p.pieces_left} pieces left ({p.move_count} moves)"
            )
        elif p.move_count > 0:
            lines.append(
                f"\U0001f9e9 **{p.display_name}** \u2014 "
                f"{p.pieces_left} pieces left ({p.move_count} moves)"
            )
        else:
            lines.append(f"\u2b1c **{p.display_name}** \u2014 No moves yet")

    embed.add_field(name="Players", value="\n".join(lines), inline=False)
    embed.set_footer(
        text=f"Host: {table.host_name} \u2502 Click Move to play",
    )
    return embed


def _finished_embed(
    table: SolChessTable, *,
    balances: dict[int, int] | None = None,
    payouts: dict[int, int] | None = None,
) -> discord.Embed:
    if table.winners:
        winner_names = [table.players[uid].display_name for uid in table.winners]
        moves = table.players[table.winners[0]].move_count
        if len(winner_names) == 1:
            title_text = (
                f"\U0001f3c6 **{winner_names[0]}** solved it in **{moves}** moves!"
            )
        else:
            title_text = (
                f"\U0001f3c6 **{' & '.join(winner_names)}** "
                f"solved it in **{moves}** moves!"
            )
    else:
        title_text = "Nobody solved it this round."

    embed = discord.Embed(
        title=f"\u265e Solitaire Chess \u2014 Round {table.round_num} Complete",
        description=title_text,
        colour=discord.Colour.gold() if table.winners else discord.Colour.dark_red(),
    )

    winner_set = set(table.winners)
    lines: list[str] = []
    for p in table.players.values():
        bal = balances.get(p.user_id, 0) if balances else 0
        payout = payouts.get(p.user_id, 0) if payouts else 0
        net = payout - p.bet
        sign = "+" if net >= 0 else ""
        if p.user_id in winner_set:
            lines.append(
                f"\U0001f3c6 **{p.display_name}** \u2014 "
                f"Solved in {p.move_count} moves \u2014 "
                f"{p.bet}c \u2192 {payout}c (**{sign}{net}c**) \u2014 bal: {bal}c"
            )
        elif p.gave_up:
            lines.append(
                f"\U0001f3f3\ufe0f **{p.display_name}** \u2014 "
                f"Gave up ({p.pieces_left} left) \u2014 "
                f"{p.bet}c \u2192 {payout}c (**{sign}{net}c**) \u2014 bal: {bal}c"
            )
        else:
            lines.append(
                f"\u274c **{p.display_name}** \u2014 "
                f"{p.pieces_left} pieces left \u2014 "
                f"{p.bet}c \u2192 {payout}c (**{sign}{net}c**) \u2014 bal: {bal}c"
            )

    embed.add_field(name="Results", value="\n".join(lines), inline=False)
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


# ── Modals ───────────────────────────────────────────────────────────────────


class JoinSolChessModal(ui.Modal):
    amount = ui.TextInput(
        label="Bet amount (coins)",
        placeholder="e.g. 100",
        required=True,
        max_length=10,
    )

    def __init__(
        self, table: SolChessTable, view: "SolChessView", balance: int,
    ) -> None:
        super().__init__(title="Join Solitaire Chess")
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

        self.table.players[uid] = SolChessPlayer(
            user_id=uid,
            display_name=interaction.user.display_name,
            bet=amt,
        )
        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self.table_view,
        )


class MoveModal(ui.Modal):
    move_input = ui.TextInput(
        label="Your move (e.g. A1 C3)",
        placeholder="From To \u2014 e.g. A1 C3",
        required=True,
        max_length=12,
    )

    def __init__(self, table: SolChessTable, view: "SolChessView") -> None:
        super().__init__(title="Solitaire Chess \u2014 Make a Move")
        self.table = table
        self.table_view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if player is None:
            await interaction.response.send_message(
                "You're not in this game!", ephemeral=True,
            )
            return
        if player.done:
            await interaction.response.send_message(
                "You're already done this round!", ephemeral=True,
            )
            return

        parsed = parse_move(self.move_input.value)
        if parsed is None:
            await interaction.response.send_message(
                "Invalid format! Use `A1 C3` (column + row for each square).\n"
                "Columns: A\u2013D, Rows: 1\u20134",
                ephemeral=True,
            )
            return

        from_pos, to_pos = parsed

        # Validate the move
        piece = player.board[from_pos[0]][from_pos[1]]
        if piece is None:
            await interaction.response.send_message(
                f"No piece at {pos_to_str(from_pos)}!", ephemeral=True,
            )
            return

        target = player.board[to_pos[0]][to_pos[1]]
        if target is None:
            await interaction.response.send_message(
                f"No piece at {pos_to_str(to_pos)} to capture! "
                "Every move must be a capture.",
                ephemeral=True,
            )
            return

        valid_targets = get_captures(player.board, from_pos)
        if to_pos not in valid_targets:
            piece_name = PIECE_NAME[piece]
            valid_strs = [pos_to_str(t) for t in valid_targets]
            if valid_strs:
                hint = f"Valid captures for {piece_name} at {pos_to_str(from_pos)}: {', '.join(valid_strs)}"
            else:
                hint = f"{piece_name} at {pos_to_str(from_pos)} has no valid captures."
            await interaction.response.send_message(
                f"The {piece_name} can't reach {pos_to_str(to_pos)}!\n{hint}",
                ephemeral=True,
            )
            return

        # Execute the move
        captured = make_move(player.board, from_pos, to_pos)
        player.history.append((from_pos, to_pos, captured))
        player.move_count += 1

        # Check win
        if count_pieces(player.board) == 1:
            player.solved = True

        # Build ephemeral response
        piece_emoji = PIECE_EMOJI[piece]
        cap_emoji = PIECE_EMOJI[captured]
        resp_lines = [
            f"{piece_emoji} {PIECE_NAME[piece]} {pos_to_str(from_pos)} captures "
            f"{cap_emoji} {PIECE_NAME[captured]} at {pos_to_str(to_pos)}",
            "",
            format_board_emoji(player.board),
            "",
        ]

        if player.solved:
            resp_lines.append(
                f"\U0001f3c6 **Solved in {player.move_count} moves!**"
            )
        elif player.stuck:
            resp_lines.append(
                f"\U0001f6d1 **Stuck!** {player.pieces_left} pieces left, no valid moves. "
                "Use **Undo** to go back."
            )
        else:
            remaining = player.pieces_left - 1  # moves needed
            resp_lines.append(
                f"{player.pieces_left} pieces left \u2014 {remaining} more capture(s) to win"
            )

        await interaction.response.send_message(
            "\n".join(resp_lines), ephemeral=True,
        )

        # Update public embed
        await self.table_view._update_main_embed()

        # Check if round should end
        if player.solved:
            await self.table_view._check_round_end()


# ── View ─────────────────────────────────────────────────────────────────────


class SolChessView(ui.View):
    def __init__(
        self, table: SolChessTable, active_tables: dict[int, SolChessTable],
    ) -> None:
        super().__init__(timeout=600)
        self.table = table
        self.active_tables = active_tables
        self._update_buttons()

    def _update_buttons(self) -> None:
        phase = self.table.phase
        betting = phase == "betting"
        playing = phase == "playing"
        finished = phase == "finished"

        self.start_btn.disabled = (
            not betting or len(self.table.players) < MIN_PLAYERS
        )
        self.join_btn.disabled = not betting
        self.rebet_btn.disabled = not betting or not self.table.last_bets
        self.leave_btn.disabled = playing

        self.move_btn.disabled = not playing
        self.undo_btn.disabled = not playing
        self.hint_btn.disabled = not playing
        self.giveup_btn.disabled = not playing

        self.new_round_btn.disabled = not finished
        self.rules_btn.disabled = False
        self.close_btn.disabled = playing

    async def _update_main_embed(self) -> None:
        if self.table.message and self.table.phase == "playing":
            try:
                await self.table.message.edit(
                    embed=_playing_embed(self.table), view=self,
                )
            except discord.HTTPException:
                pass

    async def _check_round_end(self) -> None:
        table = self.table
        if table.phase != "playing":
            return

        solvers = [uid for uid, p in table.players.items() if p.solved]
        if solvers:
            min_moves = min(table.players[uid].move_count for uid in solvers)
            winners = [uid for uid in solvers if table.players[uid].move_count == min_moves]
            await self._finish_round(winners)
            return

        # Check if everyone is done (gave up or stuck with no undo possible)
        all_done = all(p.done for p in table.players.values())
        if all_done:
            await self._finish_round([])

    async def _finish_round(self, winner_uids: list[int]) -> None:
        table = self.table
        table.phase = "finished"
        table.winners = winner_uids

        if table.round_task and not table.round_task.done():
            table.round_task.cancel()

        # Compute payouts
        bets = {uid: p.bet for uid, p in table.players.items()}

        if len(table.players) == 1 and winner_uids:
            # Solo: house pays multiplier
            uid = winner_uids[0]
            payouts = {uid: table.players[uid].bet * SOLO_PAYOUT}
        elif winner_uids:
            payouts = compute_side_pot_payouts(bets, winner_uids)
        else:
            # No winner — everyone loses
            payouts = {uid: 0 for uid in table.players}

        balances: dict[int, int] = {}
        for uid, player in table.players.items():
            payout = payouts.get(uid, 0)
            if payout > 0:
                balances[uid] = await queries.update_casino_balance(
                    str(uid), payout,
                )
            else:
                bal = await queries.get_casino_balance(str(uid))
                balances[uid] = bal or 0
            await queries.log_casino_result(
                str(uid), "solitaire-chess", player.bet, payout,
            )

        for uid, player in table.players.items():
            table.last_bets[uid] = (player.display_name, player.bet)

        self._update_buttons()
        if table.message:
            try:
                await table.message.edit(
                    embed=_finished_embed(
                        table, balances=balances, payouts=payouts,
                    ),
                    view=self,
                )
            except discord.HTTPException:
                pass

    async def _round_timer(self) -> None:
        try:
            await asyncio.sleep(ROUND_TIMEOUT)
        except asyncio.CancelledError:
            return

        table = self.table
        if table.phase != "playing":
            return

        # Time's up — check if anyone solved
        solvers = [uid for uid, p in table.players.items() if p.solved]
        if solvers:
            min_moves = min(table.players[uid].move_count for uid in solvers)
            winners = [uid for uid in solvers if table.players[uid].move_count == min_moves]
            await self._finish_round(winners)
        else:
            # Closest: fewest pieces remaining (tiebreak: most moves = more effort)
            active = [
                (uid, p) for uid, p in table.players.items() if not p.gave_up
            ]
            if active:
                min_pieces = min(p.pieces_left for _, p in active)
                closest = [uid for uid, p in active if p.pieces_left == min_pieces]
                await self._finish_round(closest)
            else:
                await self._finish_round([])

    # ── Row 0: Start / Join / Re-bet / Leave ─────────────────────────────

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
                f"Need at least {MIN_PLAYERS} player(s)!", ephemeral=True,
            )
            return
        await self._start_game(interaction)

    @ui.button(
        label="Join", style=discord.ButtonStyle.primary, emoji="\u265e", row=0,
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
            JoinSolChessModal(self.table, self, bal),
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
        self.table.players[uid] = SolChessPlayer(
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
                "Can't leave mid-game! Use Give Up first.", ephemeral=True,
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

    # ── Row 1: Move / Undo / Hint / Give Up ──────────────────────────────

    @ui.button(
        label="Move", style=discord.ButtonStyle.success, emoji="\U0001f3af", row=1,
    )
    async def move_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "playing":
            await interaction.response.send_message(
                "No game in progress.", ephemeral=True,
            )
            return
        player = self.table.players.get(interaction.user.id)
        if player is None:
            await interaction.response.send_message(
                "You're not in this game!", ephemeral=True,
            )
            return
        if player.done:
            await interaction.response.send_message(
                "You're already done this round!", ephemeral=True,
            )
            return
        await interaction.response.send_modal(MoveModal(self.table, self))

    @ui.button(
        label="Undo", style=discord.ButtonStyle.secondary, emoji="\u21a9\ufe0f", row=1,
    )
    async def undo_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        player = self.table.players.get(interaction.user.id)
        if player is None:
            await interaction.response.send_message(
                "You're not in this game!", ephemeral=True,
            )
            return
        if player.done:
            await interaction.response.send_message(
                "You're already done this round!", ephemeral=True,
            )
            return
        if not player.history:
            await interaction.response.send_message(
                "No moves to undo!", ephemeral=True,
            )
            return

        from_pos, to_pos, captured = player.history.pop()
        undo_move(player.board, from_pos, to_pos, captured)
        player.move_count -= 1

        piece = player.board[from_pos[0]][from_pos[1]]
        assert piece is not None
        resp = (
            f"\u21a9\ufe0f Undid: {PIECE_EMOJI[piece]} {pos_to_str(from_pos)} "
            f"\u2192 {pos_to_str(to_pos)}\n\n"
            f"{format_board_emoji(player.board)}\n\n"
            f"{player.pieces_left} pieces left"
        )
        await interaction.response.send_message(resp, ephemeral=True)
        await self._update_main_embed()

    @ui.button(
        label="Hint", style=discord.ButtonStyle.secondary, emoji="\U0001f4a1", row=1,
    )
    async def hint_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        player = self.table.players.get(interaction.user.id)
        if player is None:
            await interaction.response.send_message(
                "You're not in this game!", ephemeral=True,
            )
            return
        if player.done:
            await interaction.response.send_message(
                "You're already done this round!", ephemeral=True,
            )
            return

        hint = get_hint(player.board)
        if hint is None:
            await interaction.response.send_message(
                "\U0001f6d1 No solution from this position! Use **Undo** to go back.",
                ephemeral=True,
            )
            return

        from_pos, to_pos = hint
        piece = player.board[from_pos[0]][from_pos[1]]
        target = player.board[to_pos[0]][to_pos[1]]
        assert piece is not None and target is not None
        await interaction.response.send_message(
            f"\U0001f4a1 Try: {PIECE_EMOJI[piece]} {PIECE_NAME[piece]} "
            f"{pos_to_str(from_pos)} \u2192 "
            f"{PIECE_EMOJI[target]} {pos_to_str(to_pos)}",
            ephemeral=True,
        )

    @ui.button(
        label="Give Up", style=discord.ButtonStyle.danger, emoji="\U0001f3f3\ufe0f", row=1,
    )
    async def giveup_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        player = self.table.players.get(interaction.user.id)
        if player is None:
            await interaction.response.send_message(
                "You're not in this game!", ephemeral=True,
            )
            return
        if player.done:
            await interaction.response.send_message(
                "You're already done!", ephemeral=True,
            )
            return

        player.gave_up = True
        await interaction.response.send_message(
            "\U0001f3f3\ufe0f You gave up. Better luck next round!",
            ephemeral=True,
        )
        await self._update_main_embed()
        await self._check_round_end()

    # ── Row 2: New Round / Rules / Close ──────────────────────────────────

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
        label="Rules", style=discord.ButtonStyle.secondary, emoji="\U0001f4d6", row=2,
    )
    async def rules_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        await interaction.response.send_message(
            "**Solitaire Chess \u2014 Rules**\n\n"
            "A 4\u00d74 board has several chess pieces placed on it.\n"
            "**Every move must be a capture** \u2014 one piece takes another.\n"
            "Goal: reduce the board to **exactly 1 piece**.\n\n"
            "**Piece movement:**\n"
            "\u265a **King** \u2014 1 square any direction\n"
            "\u265b **Queen** \u2014 any distance, any direction\n"
            "\u265c **Rook** \u2014 any distance, straight lines\n"
            "\u265d **Bishop** \u2014 any distance, diagonals\n"
            "\u265e **Knight** \u2014 L-shape (2+1), can jump\n"
            "\u265f **Pawn** \u2014 1 square diagonally (any direction)\n\n"
            "**How to play:** Click **Move** and type coordinates like `A1 C3`.\n"
            "Use **Undo** to take back moves, **Hint** for a suggestion.\n\n"
            f"**Time limit:** {ROUND_TIMEOUT // 60} minutes per round.\n"
            "First player to solve it wins the pot!",
            ephemeral=True,
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

    # ── Game logic ────────────────────────────────────────────────────────

    async def _start_game(self, interaction: discord.Interaction) -> None:
        table = self.table
        cfg = DIFFICULTY_CONFIG[table.difficulty]
        num_pieces = cfg["pieces"]

        # Generate the puzzle
        board = generate_puzzle(num_pieces)
        if board is None:
            await interaction.response.send_message(
                "Failed to generate a solvable puzzle. Try again!",
                ephemeral=True,
            )
            return

        table.starting_board = board
        table.phase = "playing"
        table.winners.clear()

        # Give each player their own copy
        for p in table.players.values():
            p.board = copy_board(board)
            p.history.clear()
            p.solved = False
            p.gave_up = False
            p.move_count = 0

        self._update_buttons()
        await interaction.response.edit_message(
            embed=_playing_embed(table), view=self,
        )

        table.round_task = asyncio.create_task(self._round_timer())

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def _start_new_round(self) -> None:
        table = self.table
        table.players.clear()
        table.phase = "betting"
        table.round_num += 1
        table.starting_board = []
        table.winners.clear()
        if table.round_task and not table.round_task.done():
            table.round_task.cancel()
        table.round_task = None

    async def _refund_all(self) -> None:
        for p in self.table.players.values():
            try:
                await queries.update_casino_balance(str(p.user_id), p.bet)
            except Exception:
                pass

    async def _close(
        self, interaction: discord.Interaction, reason: str,
    ) -> None:
        if self.table.round_task and not self.table.round_task.done():
            self.table.round_task.cancel()
        embed = discord.Embed(
            title="\u265e Solitaire Chess \u2014 Closed",
            description=reason,
            colour=discord.Colour.dark_grey(),
        )
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_tables.pop(self.table.channel_id, None)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        table = self.table
        if table.round_task and not table.round_task.done():
            table.round_task.cancel()

        if table.phase == "finished":
            self.active_tables.pop(table.channel_id, None)
            if table.message:
                try:
                    embed = discord.Embed(
                        title="\u265e Solitaire Chess \u2014 Timed Out",
                        description="Table timed out between rounds.",
                        colour=discord.Colour.dark_grey(),
                    )
                    await table.message.edit(embed=embed, view=None)
                except Exception:
                    pass
            return

        await self._refund_all()
        self.active_tables.pop(table.channel_id, None)
        if table.message:
            try:
                embed = discord.Embed(
                    title="\u265e Solitaire Chess \u2014 Timed Out",
                    description="Table timed out. All bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                pass


# ── Cog ──────────────────────────────────────────────────────────────────────


class SolChessCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, SolChessTable] = {}

    @app_commands.command(
        name="solitaire-chess",
        description="Solitaire Chess puzzle race \u2014 capture until 1 piece remains",
    )
    @app_commands.describe(difficulty="Puzzle difficulty (number of pieces)")
    @app_commands.choices(difficulty=DIFFICULTY_CHOICES)
    async def solitaire_chess(
        self,
        interaction: discord.Interaction,
        difficulty: str = "medium",
    ) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            await interaction.response.send_message(
                "There's already a Solitaire Chess table in this channel!",
                ephemeral=True,
            )
            return

        if difficulty not in DIFFICULTY_CONFIG:
            difficulty = "medium"

        await queries.get_or_create_casino_wallet(str(interaction.user.id))

        table = SolChessTable(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
            difficulty=difficulty,
        )
        self.active_tables[channel_id] = table

        view = SolChessView(table, self.active_tables)
        embed = _betting_embed(table)
        await interaction.response.send_message(embed=embed, view=view)
        table.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SolChessCog(bot))
