"""Casino cog — 1v1 /tictactoe (Tic Tac Toe) duel game."""

from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.cogs._pool import compute_side_pot_payouts
from db import queries

# ── Constants ────────────────────────────────────────────────────────────────

EMPTY = "\u2b1c"  # white square
X_EMOJI = "\u274c"  # X mark
O_EMOJI = "\u2b55"  # O circle

WIN_LINES = [
    # Rows
    (0, 1, 2),
    (3, 4, 5),
    (6, 7, 8),
    # Columns
    (0, 3, 6),
    (1, 4, 7),
    (2, 5, 8),
    # Diagonals
    (0, 4, 8),
    (2, 4, 6),
]

MOVE_TIMEOUT = 180  # seconds, view timeout


# ── Helpers ──────────────────────────────────────────────────────────────────


def _check_winner(board: list[int]) -> int:
    """Return 0 (none), 1 (X wins), 2 (O wins), or -1 (draw if board full)."""
    for a, b, c in WIN_LINES:
        if board[a] != 0 and board[a] == board[b] == board[c]:
            return board[a]
    if all(cell != 0 for cell in board):
        return -1  # draw
    return 0  # game still going


def _render_board(board: list[int]) -> str:
    """Render the 3x3 grid as emoji string."""
    symbols = {0: EMPTY, 1: X_EMOJI, 2: O_EMOJI}
    rows = []
    for r in range(3):
        cells = [symbols[board[r * 3 + c]] for c in range(3)]
        rows.append(" ".join(cells))
    return "\n".join(rows)


def _game_embed(game: "TTTGame") -> discord.Embed:
    """Build embed showing board, whose turn, score line."""
    board_str = _render_board(game.board)
    turn_name = game.player_name(game.turn)
    turn_emoji = X_EMOJI if game.turn == game.challenger_id else O_EMOJI

    embed = discord.Embed(
        title="\u2716\ufe0f Tic Tac Toe",
        description=(
            f"**{game.challenger_name}** {X_EMOJI} vs {O_EMOJI} **{game.opponent_name}**\n"
            f"Pot: **{game.bet * 2}c**\n\n"
            f"{board_str}\n\n"
            f"{turn_emoji} **{turn_name}**'s turn"
        ),
        colour=discord.Colour.orange(),
    )
    return embed


def _pending_embed(game: "TTTGame") -> discord.Embed:
    embed = discord.Embed(
        title="\u2716\ufe0f Tic Tac Toe — Challenge!",
        description=(
            f"**{game.challenger_name}** challenges **{game.opponent_name}** "
            f"to Tic Tac Toe for **{game.bet}c**!\n\n"
            f"Pot: **{game.bet * 2}c**\n"
            f"{game.challenger_name} plays {X_EMOJI}, {game.opponent_name} plays {O_EMOJI}."
        ),
        colour=discord.Colour.green(),
    )
    embed.set_footer(text=f"Waiting for {game.opponent_name} to accept or decline")
    return embed


def _result_embed(game: "TTTGame", winner_id: int) -> discord.Embed:
    """Final state embed showing winner or draw."""
    board_str = _render_board(game.board)

    if winner_id == 0:
        # Draw
        embed = discord.Embed(
            title="\u2716\ufe0f Tic Tac Toe — Draw!",
            description=(
                f"**{game.challenger_name}** {X_EMOJI} vs {O_EMOJI} **{game.opponent_name}**\n\n"
                f"{board_str}\n\n"
                f"It's a draw! Both players refunded **{game.bet}c**."
            ),
            colour=discord.Colour.greyple(),
        )
    else:
        winner_name = game.player_name(winner_id)
        loser_id = game.opponent_id if winner_id == game.challenger_id else game.challenger_id
        loser_name = game.player_name(loser_id)
        payout = game.bet * 2

        embed = discord.Embed(
            title="\u2716\ufe0f Tic Tac Toe — Final Result",
            description=(
                f"**{game.challenger_name}** {X_EMOJI} vs {O_EMOJI} **{game.opponent_name}**\n\n"
                f"{board_str}\n\n"
                f"\U0001f3c6 **{winner_name}** wins **{payout}c**!"
            ),
            colour=discord.Colour.gold(),
        )
        embed.add_field(
            name="Results",
            value=(
                f"\U0001f3c6 **{winner_name}** — {game.bet}c → {payout}c "
                f"(**+{payout - game.bet}c**)\n"
                f"\u274c **{loser_name}** — {game.bet}c → 0c "
                f"(**-{game.bet}c**)"
            ),
            inline=False,
        )

    return embed


# ── Dataclass ────────────────────────────────────────────────────────────────


@dataclass
class TTTGame:
    channel_id: int
    challenger_id: int
    opponent_id: int
    challenger_name: str
    opponent_name: str
    bet: int
    phase: str = "pending"  # pending | playing | finished
    board: list[int] = field(default_factory=lambda: [0] * 9)  # 0=empty, 1=X, 2=O
    turn: int = 0  # user_id of whose turn it is
    message: discord.Message | None = None

    def player_name(self, uid: int) -> str:
        if uid == self.challenger_id:
            return self.challenger_name
        return self.opponent_name

    def player_mark(self, uid: int) -> int:
        """Return 1 (X) for challenger, 2 (O) for opponent."""
        return 1 if uid == self.challenger_id else 2


# ── View ─────────────────────────────────────────────────────────────────────


class TTTView(ui.View):
    def __init__(self, game: TTTGame, active_games: dict[int, TTTGame]) -> None:
        super().__init__(timeout=MOVE_TIMEOUT)
        self.game = game
        self.active_games = active_games
        self._board_buttons: list[ui.Button] = []

        # Create 9 board buttons (rows 0-2)
        for i in range(9):
            btn = ui.Button(
                label=EMPTY,
                style=discord.ButtonStyle.secondary,
                row=i // 3,
                custom_id=f"ttt_{i}",
            )
            btn.callback = self._make_board_callback(i)
            self._board_buttons.append(btn)
            self.add_item(btn)

        self._update_buttons()

    def _make_board_callback(self, index: int):
        async def callback(interaction: discord.Interaction) -> None:
            await self._handle_board_click(interaction, index)
        return callback

    async def _handle_board_click(self, interaction: discord.Interaction, index: int) -> None:
        game = self.game
        if game.phase != "playing":
            await interaction.response.send_message("Game isn't active!", ephemeral=True)
            return
        if interaction.user.id != game.turn:
            await interaction.response.send_message("It's not your turn!", ephemeral=True)
            return
        if game.board[index] != 0:
            await interaction.response.send_message("That cell is taken!", ephemeral=True)
            return

        # Place mark
        mark = game.player_mark(interaction.user.id)
        game.board[index] = mark

        # Update button appearance
        btn = self._board_buttons[index]
        if mark == 1:
            btn.label = X_EMOJI
            btn.style = discord.ButtonStyle.primary  # blurple for X
        else:
            btn.label = O_EMOJI
            btn.style = discord.ButtonStyle.danger  # red for O
        btn.disabled = True

        # Check for winner/draw
        result = _check_winner(game.board)
        if result != 0:
            await self._finish_game(interaction, result)
            return

        # Swap turn
        game.turn = game.opponent_id if game.turn == game.challenger_id else game.challenger_id
        self._update_buttons()
        await interaction.response.edit_message(embed=_game_embed(game), view=self)

    async def _finish_game(self, interaction: discord.Interaction, result: int) -> None:
        game = self.game
        game.phase = "finished"

        # Determine winner_id: result 1 = X (challenger), 2 = O (opponent), -1 = draw
        if result == 1:
            winner_id = game.challenger_id
        elif result == 2:
            winner_id = game.opponent_id
        else:
            winner_id = 0  # draw

        # Compute payouts via side pot system
        bets = {game.challenger_id: game.bet, game.opponent_id: game.bet}
        if winner_id != 0:
            winner_uids = [winner_id]
        else:
            winner_uids = []  # no winner — draw triggers refund path
        payouts = compute_side_pot_payouts(bets, winner_uids)

        # Credit payouts
        for uid, payout in payouts.items():
            if payout > 0:
                await queries.update_casino_balance(str(uid), payout)

        # Log results
        for uid in (game.challenger_id, game.opponent_id):
            await queries.log_casino_result(
                str(uid), "tictactoe", game.bet, payouts.get(uid, 0),
            )

        # Build result embed
        embed = _result_embed(game, winner_id)

        # Disable all board buttons, show rematch
        self._update_buttons()
        await interaction.response.edit_message(embed=embed, view=self)
        self.active_games.pop(game.channel_id, None)

    # ── Accept / Decline (row=3) ─────────────────────────────────────────

    @ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="\u2705", row=3)
    async def accept_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.game.opponent_id:
            await interaction.response.send_message("Not your challenge!", ephemeral=True)
            return
        if self.game.phase != "pending":
            await interaction.response.send_message("Already started!", ephemeral=True)
            return

        # Deduct opponent's coins
        try:
            await queries.update_casino_balance(str(self.game.opponent_id), -self.game.bet)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(self.game.opponent_id))
            await interaction.response.send_message(
                f"Not enough coins! (have {bal}c)", ephemeral=True,
            )
            return

        # Start game — challenger (X) goes first
        self.game.phase = "playing"
        self.game.turn = self.game.challenger_id
        self._update_buttons()
        await interaction.response.edit_message(embed=_game_embed(self.game), view=self)

    @ui.button(label="Decline", style=discord.ButtonStyle.danger, emoji="\u274c", row=3)
    async def decline_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.game.opponent_id:
            await interaction.response.send_message("Not your challenge!", ephemeral=True)
            return
        if self.game.phase != "pending":
            await interaction.response.send_message("Already started!", ephemeral=True)
            return

        # Refund challenger
        await queries.update_casino_balance(str(self.game.challenger_id), self.game.bet)
        embed = discord.Embed(
            title="\u2716\ufe0f Tic Tac Toe — Cancelled",
            description=f"{self.game.opponent_name} declined the challenge.",
            colour=discord.Colour.dark_grey(),
        )
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_games.pop(self.game.channel_id, None)
        await interaction.response.edit_message(embed=embed, view=self)

    # ── Rematch (row=4) ──────────────────────────────────────────────────

    @ui.button(label="Rematch", style=discord.ButtonStyle.primary, emoji="\U0001f504", row=4)
    async def rematch_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        game = self.game
        if game.phase != "finished":
            await interaction.response.send_message("Game isn't over yet!", ephemeral=True)
            return
        clicker = interaction.user.id
        if clicker not in (game.challenger_id, game.opponent_id):
            await interaction.response.send_message("You weren't in this game!", ephemeral=True)
            return

        # Clicker becomes the new challenger; the other player becomes opponent.
        # This ensures the accept_btn (gated on opponent_id) always works correctly.
        other = game.opponent_id if clicker == game.challenger_id else game.challenger_id
        other_name = game.opponent_name if clicker == game.challenger_id else game.challenger_name
        clicker_name = game.challenger_name if clicker == game.challenger_id else game.opponent_name

        new_challenger_id = clicker
        new_opponent_id = other
        new_challenger_name = clicker_name
        new_opponent_name = other_name

        # Deduct clicker's coins
        try:
            await queries.update_casino_balance(str(clicker), -game.bet)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(clicker))
            await interaction.response.send_message(
                f"Not enough coins! (have {bal}c)", ephemeral=True,
            )
            return

        # Disable old view
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()

        # Create new game
        new_game = TTTGame(
            channel_id=game.channel_id,
            challenger_id=new_challenger_id,
            opponent_id=new_opponent_id,
            challenger_name=new_challenger_name,
            opponent_name=new_opponent_name,
            bet=game.bet,
        )
        self.active_games[game.channel_id] = new_game

        new_view = TTTView(new_game, self.active_games)
        embed = _pending_embed(new_game)

        # Edit old message to disable it, send new challenge
        await interaction.response.edit_message(view=self)
        msg = await interaction.followup.send(
            content=f"<@{other}>", embed=embed, view=new_view,
        )
        new_game.message = msg

    # ── Button state management ──────────────────────────────────────────

    def _update_buttons(self) -> None:
        pending = self.game.phase == "pending"
        playing = self.game.phase == "playing"
        finished = self.game.phase == "finished"

        # Board buttons (rows 0-2)
        for i, btn in enumerate(self._board_buttons):
            if pending:
                btn.disabled = True
            elif playing:
                btn.disabled = self.game.board[i] != 0  # disable taken cells
            else:
                btn.disabled = True  # finished

        # Accept / Decline visible only in pending
        self.accept_btn.disabled = not pending
        self.decline_btn.disabled = not pending
        if not pending:
            self.accept_btn.row = None  # type: ignore[assignment]
            self.decline_btn.row = None  # type: ignore[assignment]
        else:
            self.accept_btn.row = 3
            self.decline_btn.row = 3

        # Rematch visible only when finished
        self.rematch_btn.disabled = not finished
        if not finished:
            self.rematch_btn.row = None  # type: ignore[assignment]
        else:
            self.rematch_btn.row = 4

    # ── Timeout ──────────────────────────────────────────────────────────

    async def on_timeout(self) -> None:
        game = self.game

        if game.phase == "pending":
            # Refund challenger
            try:
                await queries.update_casino_balance(str(game.challenger_id), game.bet)
            except Exception:
                pass
            self.active_games.pop(game.channel_id, None)
            if game.message:
                try:
                    embed = discord.Embed(
                        title="\u2716\ufe0f Tic Tac Toe — Expired",
                        description=f"Challenge expired. {game.challenger_name}'s coins refunded.",
                        colour=discord.Colour.dark_grey(),
                    )
                    await game.message.edit(embed=embed, view=None)
                except Exception:
                    pass
            return

        if game.phase == "playing":
            # Refund both players
            try:
                await queries.update_casino_balance(str(game.challenger_id), game.bet)
                await queries.update_casino_balance(str(game.opponent_id), game.bet)
            except Exception:
                pass

        self.active_games.pop(game.channel_id, None)
        if game.message:
            try:
                embed = discord.Embed(
                    title="\u2716\ufe0f Tic Tac Toe — Timed Out",
                    description="Game timed out. All bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await game.message.edit(embed=embed, view=None)
            except Exception:
                pass


# ── Cog ──────────────────────────────────────────────────────────────────────


class TicTacToeCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_games: dict[int, TTTGame] = {}

    @app_commands.command(
        name="tictactoe",
        description="Challenge someone to Tic Tac Toe!",
    )
    @app_commands.describe(
        opponent="Who to challenge",
        bet="Coin amount to wager (1-500)",
    )
    async def tictactoe(
        self, interaction: discord.Interaction,
        opponent: discord.User, bet: int,
    ) -> None:
        uid = interaction.user.id
        channel_id = interaction.channel_id

        # Validations
        if opponent.bot:
            await interaction.response.send_message("Can't challenge a bot!", ephemeral=True)
            return
        if opponent.id == uid:
            await interaction.response.send_message("Can't challenge yourself!", ephemeral=True)
            return
        if channel_id in self.active_games:
            await interaction.response.send_message(
                "There's already a Tic Tac Toe game in this channel!", ephemeral=True,
            )
            return
        if bet < 1:
            await interaction.response.send_message("Bet must be at least 1c.", ephemeral=True)
            return
        if bet > 500:
            await interaction.response.send_message("Bet cannot exceed 500c.", ephemeral=True)
            return

        # Deduct challenger's coins
        await queries.get_or_create_casino_wallet(str(uid))
        try:
            await queries.update_casino_balance(str(uid), -bet)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins! (have {bal}c)", ephemeral=True,
            )
            return

        # Ensure opponent has a wallet
        await queries.get_or_create_casino_wallet(str(opponent.id))

        game = TTTGame(
            channel_id=channel_id,
            challenger_id=uid,
            opponent_id=opponent.id,
            challenger_name=interaction.user.display_name,
            opponent_name=opponent.display_name,
            bet=bet,
        )
        self.active_games[channel_id] = game

        view = TTTView(game, self.active_games)
        embed = _pending_embed(game)
        await interaction.response.send_message(
            content=f"<@{opponent.id}>", embed=embed, view=view,
        )
        game.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TicTacToeCog(bot))
