"""Casino cog — /battleship  Full 10x10 Battleship with ship placement."""

import asyncio
import random
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries
from bot.cogs._elo_helpers import update_elo_1v1, update_elo_draw, fmt_elo_change
from bot.cogs._pool import compute_side_pot_payouts

# ── Constants ────────────────────────────────────────────────────────────────

BOARD_SIZE = 10
COLS = "ABCDEFGHIJ"
PLACE_TIMEOUT = 180  # seconds for ship placement
MOVE_TIMEOUT = 300  # seconds for the entire firing phase
MAX_BET = 500

# Ships: (name, size, emoji)
SHIPS = [
    ("Carrier", 5, "\U0001f6a2"),
    ("Battleship", 4, "\u2693"),
    ("Cruiser", 3, "\U0001f6e5"),
    ("Submarine", 3, "\U0001f30a"),
    ("Destroyer", 2, "\u26f5"),
]
TOTAL_SHIP_CELLS = sum(s[1] for s in SHIPS)

# Board cell states
WATER = 0
SHIP = 1
HIT = 2
MISS = 3
SUNK = 4

# Display chars
CELL_DISPLAY = {
    WATER: "\U0001f7e6",    # blue square
    SHIP: "\u2b1c",          # white square
    HIT: "\U0001f7e5",      # red square
    MISS: "\u2b1b",          # black square
    SUNK: "\U0001f4a5",     # explosion
}

# Tracking board (opponent's board from your perspective)
TRACK_DISPLAY = {
    WATER: "\U0001f7e6",    # unknown = blue
    HIT: "\U0001f7e5",      # hit = red
    MISS: "\u2b1b",          # miss = black
    SUNK: "\U0001f4a5",     # sunk = explosion
}


# ── Board logic ──────────────────────────────────────────────────────────────


def _empty_board() -> list[list[int]]:
    return [[WATER] * BOARD_SIZE for _ in range(BOARD_SIZE)]


def _can_place(board: list[list[int]], row: int, col: int, size: int, horizontal: bool) -> bool:
    for i in range(size):
        r = row if horizontal else row + i
        c = col + i if horizontal else col
        if r >= BOARD_SIZE or c >= BOARD_SIZE:
            return False
        if board[r][c] != WATER:
            return False
    return True


def _place_ship(board: list[list[int]], row: int, col: int, size: int, horizontal: bool) -> list[tuple[int, int]]:
    cells = []
    for i in range(size):
        r = row if horizontal else row + i
        c = col + i if horizontal else col
        board[r][c] = SHIP
        cells.append((r, c))
    return cells


def _random_placement() -> tuple[list[list[int]], dict[str, list[tuple[int, int]]]]:
    board = _empty_board()
    ship_cells: dict[str, list[tuple[int, int]]] = {}
    for name, size, _ in SHIPS:
        placed = False
        for _ in range(1000):
            horizontal = random.choice([True, False])
            row = random.randint(0, BOARD_SIZE - 1)
            col = random.randint(0, BOARD_SIZE - 1)
            if _can_place(board, row, col, size, horizontal):
                cells = _place_ship(board, row, col, size, horizontal)
                ship_cells[name] = cells
                placed = True
                break
        if not placed:
            # Shouldn't happen with 1000 tries, but fallback
            return _random_placement()
    return board, ship_cells


def _render_board(board: list[list[int]], show_ships: bool = True) -> str:
    """Render board as monospace text."""
    header = "\u2003 " + " ".join(COLS)
    lines = [header]
    for r in range(BOARD_SIZE):
        row_label = f"`{r+1:>2}`"
        cells = []
        for c in range(BOARD_SIZE):
            val = board[r][c]
            if not show_ships and val == SHIP:
                cells.append(CELL_DISPLAY[WATER])
            else:
                cells.append(CELL_DISPLAY.get(val, CELL_DISPLAY[WATER]))
        lines.append(row_label + "".join(cells))
    return "\n".join(lines)


def _render_tracking(tracking: list[list[int]]) -> str:
    """Render tracking board (what you've shot at)."""
    header = "\u2003 " + " ".join(COLS)
    lines = [header]
    for r in range(BOARD_SIZE):
        row_label = f"`{r+1:>2}`"
        cells = []
        for c in range(BOARD_SIZE):
            val = tracking[r][c]
            cells.append(TRACK_DISPLAY.get(val, TRACK_DISPLAY[WATER]))
        lines.append(row_label + "".join(cells))
    return "\n".join(lines)


def _fire(
    board: list[list[int]],
    tracking: list[list[int]],
    ship_cells: dict[str, list[tuple[int, int]]],
    row: int,
    col: int,
) -> tuple[str, str | None]:
    """Fire at (row, col). Returns (result, sunk_ship_name or None)."""
    if board[row][col] == SHIP:
        board[row][col] = HIT
        tracking[row][col] = HIT
        # Check if a ship is sunk
        for name, cells in ship_cells.items():
            if (row, col) in cells and all(board[r][c] == HIT for r, c in cells):
                # Mark as sunk on tracking
                for r, c in cells:
                    tracking[r][c] = SUNK
                    board[r][c] = SUNK
                return "sunk", name
        return "hit", None
    elif board[row][col] in (HIT, SUNK, MISS):
        return "already", None
    else:
        board[row][col] = MISS
        tracking[row][col] = MISS
        return "miss", None


def _all_sunk(board: list[list[int]]) -> bool:
    """Check if all ships are sunk (no SHIP cells remaining)."""
    return not any(board[r][c] == SHIP for r in range(BOARD_SIZE) for c in range(BOARD_SIZE))


def _bot_fire(tracking: list[list[int]]) -> tuple[int, int]:
    """Simple hunt/target AI for solo play."""
    # First, look for hits to follow up on
    for r in range(BOARD_SIZE):
        for c in range(BOARD_SIZE):
            if tracking[r][c] == HIT:
                # Try adjacent cells
                for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < BOARD_SIZE and 0 <= nc < BOARD_SIZE and tracking[nr][nc] == WATER:
                        return nr, nc

    # Random shot on checkerboard pattern for efficiency
    candidates = [
        (r, c) for r in range(BOARD_SIZE) for c in range(BOARD_SIZE)
        if tracking[r][c] == WATER and (r + c) % 2 == 0
    ]
    if not candidates:
        candidates = [
            (r, c) for r in range(BOARD_SIZE) for c in range(BOARD_SIZE)
            if tracking[r][c] == WATER
        ]
    return random.choice(candidates) if candidates else (0, 0)


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class BSPlayer:
    user_id: int
    display_name: str
    bet: int
    board: list[list[int]] = field(default_factory=_empty_board)
    tracking: list[list[int]] = field(default_factory=_empty_board)
    ship_cells: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    ships_placed: int = 0
    is_bot: bool = False


@dataclass
class BSGame:
    channel_id: int
    challenger_id: int
    opponent_id: int
    challenger_name: str
    opponent_name: str
    bet: int
    phase: str = "pending"  # pending | placing | playing | finished
    p1: BSPlayer | None = None
    p2: BSPlayer | None = None
    turn: int = 0  # uid of whose turn
    message: discord.Message | None = None
    # Placement state
    selected_col: int | None = None
    selected_row: int | None = None
    fire_event: asyncio.Event = field(default_factory=asyncio.Event)
    placement_events: dict[int, asyncio.Event] = field(default_factory=dict)


# ── Embeds ───────────────────────────────────────────────────────────────────


def _pending_embed(game: BSGame) -> discord.Embed:
    embed = discord.Embed(
        title="\U0001f6a2 Battleship \u2014 Challenge",
        description=(
            f"**{game.challenger_name}** challenges **{game.opponent_name}** "
            f"to Battleship for **{game.bet}c**!\n\n"
            "10x10 grid, 5 ships. Sink 'em all to win!"
        ),
        colour=discord.Colour.dark_blue(),
    )
    embed.add_field(
        name="Ships",
        value="\n".join(f"{e} **{n}** ({s} cells)" for n, s, e in SHIPS),
        inline=False,
    )
    return embed


def _placing_embed(game: BSGame, player: BSPlayer) -> discord.Embed:
    remaining = [
        (n, s, e) for i, (n, s, e) in enumerate(SHIPS)
        if i >= player.ships_placed
    ]
    if remaining:
        current = remaining[0]
        title = f"\U0001f6a2 Place your {current[2]} {current[0]} ({current[1]} cells)"
    else:
        title = "\U0001f6a2 All ships placed!"

    embed = discord.Embed(title=title, colour=discord.Colour.dark_blue())
    embed.description = _render_board(player.board, show_ships=True)

    if remaining:
        placed = [f"\u2705 {n}" for i, (n, _, _) in enumerate(SHIPS) if i < player.ships_placed]
        todo = [f"\u23f3 {n} ({s})" for n, s, _ in remaining]
        embed.add_field(
            name="Ships",
            value="\n".join(placed + todo),
            inline=False,
        )
    return embed


def _game_embed(game: BSGame, viewer_uid: int) -> discord.Embed:
    """Game embed from viewer's perspective."""
    viewer = game.p1 if viewer_uid == game.p1.user_id else game.p2
    opponent = game.p2 if viewer_uid == game.p1.user_id else game.p1

    is_turn = game.turn == viewer_uid
    turn_name = game.p1.display_name if game.turn == game.p1.user_id else game.p2.display_name

    embed = discord.Embed(
        title=f"\U0001f6a2 Battleship \u2014 {'Your turn!' if is_turn else turn_name + chr(39) + 's turn'}",
        colour=discord.Colour.blue() if is_turn else discord.Colour.dark_blue(),
    )
    embed.add_field(
        name=f"Tracking ({opponent.display_name})",
        value=_render_tracking(viewer.tracking),
        inline=False,
    )
    return embed


def _public_game_embed(game: BSGame) -> discord.Embed:
    turn_name = game.p1.display_name if game.turn == game.p1.user_id else game.p2.display_name
    p1_hits = sum(1 for r in range(BOARD_SIZE) for c in range(BOARD_SIZE) if game.p1.tracking[r][c] in (HIT, SUNK))
    p2_hits = sum(1 for r in range(BOARD_SIZE) for c in range(BOARD_SIZE) if game.p2.tracking[r][c] in (HIT, SUNK))

    p1_sunk = sum(
        1 for cells in game.p2.ship_cells.values()
        if all(game.p2.board[r][c] in (HIT, SUNK) for r, c in cells)
    )
    p2_sunk = sum(
        1 for cells in game.p1.ship_cells.values()
        if all(game.p1.board[r][c] in (HIT, SUNK) for r, c in cells)
    )

    embed = discord.Embed(
        title=f"\U0001f6a2 Battleship \u2014 {turn_name}'s turn",
        colour=discord.Colour.dark_blue(),
    )
    embed.add_field(
        name=game.p1.display_name,
        value=f"Hits: {p1_hits} | Ships sunk: {p1_sunk}/5",
        inline=True,
    )
    embed.add_field(
        name=game.p2.display_name,
        value=f"Hits: {p2_hits} | Ships sunk: {p2_sunk}/5",
        inline=True,
    )
    embed.set_footer(text=f"Use 'View Board' to see your tracking grid \u2022 Bet: {game.bet}c")
    return embed


def _result_embed(game: BSGame, winner: BSPlayer, loser: BSPlayer, payouts: dict[int, int]) -> discord.Embed:
    embed = discord.Embed(
        title="\U0001f3c6 Battleship \u2014 Game Over",
        description=f"**{winner.display_name}** sinks all of **{loser.display_name}**'s ships!",
        colour=discord.Colour.gold(),
    )
    for p in (winner, loser):
        payout = payouts.get(p.user_id, 0)
        net = payout - p.bet
        embed.add_field(
            name=p.display_name,
            value=f"{'Winner!' if p == winner else 'Defeated'} \u2014 {payout}c ({net:+,}c)",
            inline=True,
        )
    return embed


# ── View ─────────────────────────────────────────────────────────────────────


class BSView(ui.View):
    def __init__(self, game: BSGame, active_games: dict[int, BSGame]) -> None:
        super().__init__(timeout=MOVE_TIMEOUT)
        self.game = game
        self.active_games = active_games
        self._game_task: asyncio.Task | None = None
        self._build_selects()
        self._update_buttons()

    def _build_selects(self) -> None:
        """Build column and row select menus."""
        self.col_select = ui.Select(
            placeholder="Column (A-J)",
            options=[discord.SelectOption(label=c, value=str(i)) for i, c in enumerate(COLS)],
            row=0,
        )
        self.col_select.callback = self._col_callback

        self.row_select = ui.Select(
            placeholder="Row (1-10)",
            options=[discord.SelectOption(label=str(i+1), value=str(i)) for i in range(BOARD_SIZE)],
            row=1,
        )
        self.row_select.callback = self._row_callback

        self.add_item(self.col_select)
        self.add_item(self.row_select)

    def _update_buttons(self) -> None:
        pending = self.game.phase == "pending"
        placing = self.game.phase == "placing"
        playing = self.game.phase == "playing"
        finished = self.game.phase == "finished"

        self.accept_btn.disabled = not pending
        self.decline_btn.disabled = not pending

        # Selects are used for both placement and firing
        self.col_select.disabled = not placing and not playing
        self.row_select.disabled = not placing and not playing

        self.fire_btn.disabled = not playing
        self.place_btn.disabled = not placing
        self.dir_h_btn.disabled = not placing
        self.dir_v_btn.disabled = not placing
        self.view_board_btn.disabled = not playing
        self.random_place_btn.disabled = not placing

    async def _col_callback(self, interaction: discord.Interaction) -> None:
        self.game.selected_col = int(self.col_select.values[0])
        await interaction.response.defer()

    async def _row_callback(self, interaction: discord.Interaction) -> None:
        self.game.selected_row = int(self.row_select.values[0])
        await interaction.response.defer()

    # ── Pending buttons ──────────────────────────────────────────────────

    @ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="\u2705", row=2)
    async def accept_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.game.opponent_id:
            await interaction.response.send_message("Not your challenge!", ephemeral=True)
            return
        if self.game.phase != "pending":
            await interaction.response.send_message("Already started!", ephemeral=True)
            return

        try:
            await queries.update_casino_balance(str(self.game.opponent_id), -self.game.bet)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(self.game.opponent_id))
            await interaction.response.send_message(
                f"Not enough coins! (have {bal}c)", ephemeral=True,
            )
            return

        self.game.p1 = BSPlayer(
            user_id=self.game.challenger_id,
            display_name=self.game.challenger_name,
            bet=self.game.bet,
        )
        self.game.p2 = BSPlayer(
            user_id=self.game.opponent_id,
            display_name=self.game.opponent_name,
            bet=self.game.bet,
        )

        self.game.phase = "placing"
        self.game.placement_events[self.game.challenger_id] = asyncio.Event()
        self.game.placement_events[self.game.opponent_id] = asyncio.Event()

        self._update_buttons()
        embed = discord.Embed(
            title="\U0001f6a2 Battleship \u2014 Ship Placement",
            description=(
                "Both players: use **select menus** to pick Column + Row, "
                "then click **H** (horizontal) or **V** (vertical) to place.\n"
                "Or click **Random** for auto-placement.\n\n"
                "Click **View Board** to see your current placement."
            ),
            colour=discord.Colour.dark_blue(),
        )
        embed.add_field(
            name="Ships to place",
            value="\n".join(f"{e} **{n}** ({s} cells)" for n, s, e in SHIPS),
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=self)
        self._game_task = asyncio.create_task(self._wait_for_placement())

    @ui.button(label="Decline", style=discord.ButtonStyle.danger, emoji="\u2716", row=2)
    async def decline_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.game.opponent_id:
            await interaction.response.send_message("Not your challenge!", ephemeral=True)
            return
        # Refund challenger
        await queries.update_casino_balance(str(self.game.challenger_id), self.game.bet)
        self.active_games.pop(self.game.channel_id, None)
        embed = discord.Embed(
            title="\u2716 Battleship \u2014 Declined",
            description=f"{self.game.opponent_name} declined. {self.game.challenger_name}'s coins refunded.",
            colour=discord.Colour.dark_grey(),
        )
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()

    # ── Placement buttons ────────────────────────────────────────────────

    @ui.button(label="H", style=discord.ButtonStyle.primary, emoji="\u2194", row=3)
    async def dir_h_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._place_ship_handler(interaction, horizontal=True)

    @ui.button(label="V", style=discord.ButtonStyle.primary, emoji="\u2195", row=3)
    async def dir_v_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._place_ship_handler(interaction, horizontal=False)

    @ui.button(label="Random", style=discord.ButtonStyle.secondary, emoji="\U0001f3b2", row=3)
    async def random_place_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        player = self._get_player(uid)
        if not player:
            await interaction.response.send_message("You're not in this game!", ephemeral=True)
            return
        if player.ships_placed >= len(SHIPS):
            await interaction.response.send_message("All ships already placed!", ephemeral=True)
            return

        # Reset and randomly place all ships
        player.board = _empty_board()
        player.ship_cells.clear()
        board, ship_cells = _random_placement()
        player.board = board
        player.ship_cells = ship_cells
        player.ships_placed = len(SHIPS)

        evt = self.game.placement_events.get(uid)
        if evt:
            evt.set()

        embed = _placing_embed(self.game, player)
        embed.title = "\U0001f6a2 All ships placed! Waiting for opponent..."
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="View Board", style=discord.ButtonStyle.secondary, emoji="\U0001f440", row=4)
    async def view_board_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        player = self._get_player(uid)
        if not player:
            await interaction.response.send_message("You're not in this game!", ephemeral=True)
            return

        if self.game.phase == "placing":
            embed = _placing_embed(self.game, player)
            await interaction.response.send_message(embed=embed, ephemeral=True)
        elif self.game.phase == "playing":
            embed = _game_embed(self.game, uid)
            # Also show own board
            embed.add_field(
                name="Your Fleet",
                value=_render_board(player.board, show_ships=True),
                inline=False,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message("No board to show.", ephemeral=True)

    @ui.button(label="Fire!", style=discord.ButtonStyle.danger, emoji="\U0001f4a5", row=4)
    async def fire_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        if uid != self.game.turn:
            await interaction.response.send_message("Not your turn!", ephemeral=True)
            return
        if self.game.selected_col is None or self.game.selected_row is None:
            await interaction.response.send_message(
                "Select a column and row first!", ephemeral=True,
            )
            return

        row = self.game.selected_row
        col = self.game.selected_col

        # Fire at opponent
        shooter = self._get_player(uid)
        target = self.game.p2 if uid == self.game.p1.user_id else self.game.p1

        # Check if already shot there
        if shooter.tracking[row][col] != WATER:
            await interaction.response.send_message(
                f"You already fired at {COLS[col]}{row+1}!", ephemeral=True,
            )
            return

        result, sunk_name = _fire(target.board, shooter.tracking, target.ship_cells, row, col)

        coord = f"{COLS[col]}{row+1}"
        if result == "sunk":
            msg = f"\U0001f4a5 **{coord}** \u2014 Hit and sunk the **{sunk_name}**!"
        elif result == "hit":
            msg = f"\U0001f7e5 **{coord}** \u2014 Hit!"
        else:
            msg = f"\u2b1b **{coord}** \u2014 Miss."

        await interaction.response.send_message(msg, ephemeral=True)

        # Check win condition
        if _all_sunk(target.board):
            self.game.fire_event.set()
            return

        # Switch turn
        self.game.turn = (
            self.game.p2.user_id if uid == self.game.p1.user_id
            else self.game.p1.user_id
        )
        self.game.selected_col = None
        self.game.selected_row = None

        # Bot's turn
        if self.game.turn == self.game.p2.user_id and self.game.p2.is_bot:
            await self._bot_turn()
            return

        self.game.fire_event.set()

    async def _place_ship_handler(self, interaction: discord.Interaction, horizontal: bool) -> None:
        uid = interaction.user.id
        player = self._get_player(uid)
        if not player:
            await interaction.response.send_message("You're not in this game!", ephemeral=True)
            return
        if player.ships_placed >= len(SHIPS):
            await interaction.response.send_message("All ships placed!", ephemeral=True)
            return
        if self.game.selected_col is None or self.game.selected_row is None:
            await interaction.response.send_message(
                "Select a column and row first!", ephemeral=True,
            )
            return

        ship_name, ship_size, _ = SHIPS[player.ships_placed]
        row = self.game.selected_row
        col = self.game.selected_col

        if not _can_place(player.board, row, col, ship_size, horizontal):
            direction = "horizontally" if horizontal else "vertically"
            await interaction.response.send_message(
                f"Can't place **{ship_name}** ({ship_size} cells) at "
                f"{COLS[col]}{row+1} {direction}. Out of bounds or overlapping!",
                ephemeral=True,
            )
            return

        cells = _place_ship(player.board, row, col, ship_size, horizontal)
        player.ship_cells[ship_name] = cells
        player.ships_placed += 1

        if player.ships_placed >= len(SHIPS):
            evt = self.game.placement_events.get(uid)
            if evt:
                evt.set()
            embed = _placing_embed(self.game, player)
            embed.title = "\U0001f6a2 All ships placed! Waiting for opponent..."
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            embed = _placing_embed(self.game, player)
            await interaction.response.send_message(embed=embed, ephemeral=True)

    def _get_player(self, uid: int) -> BSPlayer | None:
        if self.game.p1 and self.game.p1.user_id == uid:
            return self.game.p1
        if self.game.p2 and self.game.p2.user_id == uid:
            return self.game.p2
        return None

    async def _wait_for_placement(self) -> None:
        """Wait for both players to place ships, then start firing phase."""
        game = self.game
        msg = game.message

        # Handle bot placement
        if game.p2.is_bot:
            board, ship_cells = _random_placement()
            game.p2.board = board
            game.p2.ship_cells = ship_cells
            game.p2.ships_placed = len(SHIPS)
            game.placement_events[game.p2.user_id].set()

        try:
            await asyncio.wait_for(
                asyncio.gather(
                    game.placement_events[game.challenger_id].wait(),
                    game.placement_events[game.opponent_id].wait(),
                ),
                timeout=PLACE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            # Auto-place for anyone who hasn't finished
            for uid, evt in game.placement_events.items():
                if not evt.is_set():
                    player = self._get_player(uid)
                    if player:
                        player.board = _empty_board()
                        player.ship_cells.clear()
                        board, ship_cells = _random_placement()
                        player.board = board
                        player.ship_cells = ship_cells
                        player.ships_placed = len(SHIPS)

        # Start firing phase
        game.phase = "playing"
        game.turn = game.challenger_id
        self._update_buttons()

        embed = _public_game_embed(game)
        try:
            await msg.edit(embed=embed, view=self)
        except Exception:
            return

        await self._run_firing()

    async def _run_firing(self) -> None:
        game = self.game
        msg = game.message

        while True:
            game.fire_event.clear()

            # Check win
            if _all_sunk(game.p2.board):
                await self._finish_game(game.p1, game.p2)
                return
            if _all_sunk(game.p1.board):
                await self._finish_game(game.p2, game.p1)
                return

            # Update display
            embed = _public_game_embed(game)
            self._update_buttons()
            try:
                await msg.edit(embed=embed, view=self)
            except Exception:
                return

            try:
                await asyncio.wait_for(game.fire_event.wait(), timeout=MOVE_TIMEOUT)
            except asyncio.TimeoutError:
                await self._timeout_game()
                return

    async def _bot_turn(self) -> None:
        """Execute bot's turn."""
        game = self.game
        await asyncio.sleep(1.5)

        row, col = _bot_fire(game.p2.tracking)
        target = game.p1
        result, sunk_name = _fire(target.board, game.p2.tracking, target.ship_cells, row, col)

        # Check win
        if _all_sunk(target.board):
            game.fire_event.set()
            return

        # Switch back to human
        game.turn = game.p1.user_id
        game.fire_event.set()

    async def _finish_game(self, winner: BSPlayer, loser: BSPlayer) -> None:
        game = self.game
        game.phase = "finished"

        bets = {winner.user_id: winner.bet, loser.user_id: loser.bet}
        payouts = compute_side_pot_payouts(bets, [winner.user_id])

        for uid, payout in payouts.items():
            if payout > 0 and not self._get_player(uid).is_bot:
                await queries.update_casino_balance(str(uid), payout)

        for p in (winner, loser):
            if not p.is_bot:
                await queries.log_casino_result(
                    str(p.user_id), "battleship", p.bet, payouts.get(p.user_id, 0),
                )

        # ELO
        elo_text = ""
        if not winner.is_bot and not loser.is_bot:
            try:
                wo, wn, lo, ln = await update_elo_1v1(
                    str(winner.user_id), str(loser.user_id), "battleship", "battleship",
                )
                elo_text = (
                    f"**{winner.display_name}**: {fmt_elo_change(wo, wn)}\n"
                    f"**{loser.display_name}**: {fmt_elo_change(lo, ln)}"
                )
            except Exception:
                pass

        embed = _result_embed(game, winner, loser, payouts)
        if elo_text:
            embed.add_field(name="ELO", value=elo_text, inline=False)

        self._update_buttons()
        self.active_games.pop(game.channel_id, None)
        try:
            await game.message.edit(embed=embed, view=None)
        except Exception:
            pass
        self.stop()

    async def _timeout_game(self) -> None:
        game = self.game
        game.phase = "finished"
        # Refund both
        for p in (game.p1, game.p2):
            if p and not p.is_bot:
                try:
                    await queries.update_casino_balance(str(p.user_id), p.bet)
                except Exception:
                    pass
        self.active_games.pop(game.channel_id, None)
        embed = discord.Embed(
            title="\u2716 Battleship \u2014 Timed Out",
            description="Game timed out. Bets refunded.",
            colour=discord.Colour.dark_grey(),
        )
        try:
            await game.message.edit(embed=embed, view=None)
        except Exception:
            pass
        self.stop()

    async def on_timeout(self) -> None:
        if self._game_task and not self._game_task.done():
            self._game_task.cancel()

        game = self.game
        if game.phase == "pending":
            try:
                await queries.update_casino_balance(str(game.challenger_id), game.bet)
            except Exception:
                pass
        elif game.phase in ("placing", "playing"):
            for p in (game.p1, game.p2):
                if p and not p.is_bot:
                    try:
                        await queries.update_casino_balance(str(p.user_id), p.bet)
                    except Exception:
                        pass

        self.active_games.pop(game.channel_id, None)
        if game.message:
            try:
                embed = discord.Embed(
                    title="\u2716 Battleship \u2014 Timed Out",
                    description="Game timed out. Bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await game.message.edit(embed=embed, view=None)
            except Exception:
                pass


# ── Cog ──────────────────────────────────────────────────────────────────────


BOT_SENTINEL = -99


class BattleshipCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_games: dict[int, BSGame] = {}

    @app_commands.command(
        name="battleship",
        description="Challenge someone to Battleship \u2014 sink the enemy fleet!",
    )
    @app_commands.describe(
        opponent="Who to challenge (leave empty for solo vs bot)",
        bet="Coin wager (1-500)",
    )
    async def battleship(
        self,
        interaction: discord.Interaction,
        bet: int,
        opponent: discord.User | None = None,
    ) -> None:
        uid = interaction.user.id
        channel_id = interaction.channel_id

        if channel_id in self.active_games:
            await interaction.response.send_message(
                "There's already a Battleship game here!", ephemeral=True,
            )
            return
        if bet < 1 or bet > MAX_BET:
            await interaction.response.send_message(f"Bet must be 1\u2013{MAX_BET}c.", ephemeral=True)
            return
        if opponent and opponent.bot:
            await interaction.response.send_message("Can't challenge a Discord bot!", ephemeral=True)
            return
        if opponent and opponent.id == uid:
            await interaction.response.send_message("Can't challenge yourself!", ephemeral=True)
            return

        await queries.get_or_create_casino_wallet(str(uid))
        try:
            await queries.update_casino_balance(str(uid), -bet)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins! (have {bal}c)", ephemeral=True,
            )
            return

        solo = opponent is None
        opp_id = BOT_SENTINEL if solo else opponent.id
        opp_name = "Captain Bot" if solo else opponent.display_name

        if not solo:
            await queries.get_or_create_casino_wallet(str(opp_id))

        game = BSGame(
            channel_id=channel_id,
            challenger_id=uid,
            opponent_id=opp_id,
            challenger_name=interaction.user.display_name,
            opponent_name=opp_name,
            bet=bet,
        )
        self.active_games[channel_id] = game

        view = BSView(game, self.active_games)

        if solo:
            # Skip accept phase — auto-start
            game.p1 = BSPlayer(
                user_id=uid,
                display_name=interaction.user.display_name,
                bet=bet,
            )
            game.p2 = BSPlayer(
                user_id=BOT_SENTINEL,
                display_name="Captain Bot",
                bet=0,
                is_bot=True,
            )
            game.phase = "placing"
            game.placement_events[uid] = asyncio.Event()
            game.placement_events[BOT_SENTINEL] = asyncio.Event()

            embed = discord.Embed(
                title="\U0001f6a2 Battleship vs Captain Bot",
                description=(
                    "Place your ships using the select menus + H/V buttons.\n"
                    "Or click **Random** for auto-placement.\n"
                    "Click **View Board** to see your placement."
                ),
                colour=discord.Colour.dark_blue(),
            )
            embed.add_field(
                name="Ships to place",
                value="\n".join(f"{e} **{n}** ({s} cells)" for n, s, e in SHIPS),
                inline=False,
            )
            view._update_buttons()
            await interaction.response.send_message(embed=embed, view=view)
            game.message = await interaction.original_response()
            view._game_task = asyncio.create_task(view._wait_for_placement())
        else:
            embed = _pending_embed(game)
            await interaction.response.send_message(
                content=f"<@{opponent.id}>", embed=embed, view=view,
            )
            game.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BattleshipCog(bot))
