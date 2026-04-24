"""Solitaire Chess puzzle logic — pure functions, no I/O.

4x4 board with chess pieces. Every move must be a capture.
Goal: reduce to exactly 1 piece remaining.
"""

import random

# ── Piece types (plain strings) ──────────────────────────────────────────────

KING = "K"
QUEEN = "Q"
ROOK = "R"
BISHOP = "B"
KNIGHT = "N"
PAWN = "P"

ALL_PIECES = [KING, QUEEN, ROOK, BISHOP, KNIGHT, PAWN]

PIECE_EMOJI: dict[str, str] = {
    KING: "\u265a",    # ♚
    QUEEN: "\u265b",   # ♛
    ROOK: "\u265c",    # ♜
    BISHOP: "\u265d",  # ♝
    KNIGHT: "\u265e",  # ♞
    PAWN: "\u265f",    # ♟
}

PIECE_NAME: dict[str, str] = {
    KING: "King",
    QUEEN: "Queen",
    ROOK: "Rook",
    BISHOP: "Bishop",
    KNIGHT: "Knight",
    PAWN: "Pawn",
}

# ── Types ────────────────────────────────────────────────────────────────────

BOARD_SIZE = 4

Pos = tuple[int, int]          # (row, col), 0-indexed
Board = list[list[str | None]]  # 4x4 grid, cell is piece letter or None

# ── Board helpers ────────────────────────────────────────────────────────────


def empty_board() -> Board:
    return [[None] * BOARD_SIZE for _ in range(BOARD_SIZE)]


def copy_board(board: Board) -> Board:
    return [row[:] for row in board]


def count_pieces(board: Board) -> int:
    return sum(1 for r in board for c in r if c is not None)


def get_pieces(board: Board) -> list[tuple[Pos, str]]:
    pieces: list[tuple[Pos, str]] = []
    for r in range(BOARD_SIZE):
        for c in range(BOARD_SIZE):
            if board[r][c] is not None:
                pieces.append(((r, c), board[r][c]))
    return pieces


def _in_bounds(r: int, c: int) -> bool:
    return 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE


# ── Movement / capture rules ────────────────────────────────────────────────


def get_captures(board: Board, pos: Pos) -> list[Pos]:
    """Return positions this piece can legally capture."""
    r, c = pos
    piece = board[r][c]
    if piece is None:
        return []

    targets: list[Pos] = []

    if piece == KING:
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0:
                    continue
                nr, nc = r + dr, c + dc
                if _in_bounds(nr, nc) and board[nr][nc] is not None:
                    targets.append((nr, nc))

    elif piece == QUEEN:
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0:
                    continue
                for dist in range(1, BOARD_SIZE):
                    nr, nc = r + dr * dist, c + dc * dist
                    if not _in_bounds(nr, nc):
                        break
                    if board[nr][nc] is not None:
                        targets.append((nr, nc))
                        break

    elif piece == ROOK:
        for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            for dist in range(1, BOARD_SIZE):
                nr, nc = r + dr * dist, c + dc * dist
                if not _in_bounds(nr, nc):
                    break
                if board[nr][nc] is not None:
                    targets.append((nr, nc))
                    break

    elif piece == BISHOP:
        for dr, dc in [(-1, -1), (-1, 1), (1, -1), (1, 1)]:
            for dist in range(1, BOARD_SIZE):
                nr, nc = r + dr * dist, c + dc * dist
                if not _in_bounds(nr, nc):
                    break
                if board[nr][nc] is not None:
                    targets.append((nr, nc))
                    break

    elif piece == KNIGHT:
        for dr, dc in [(-2, -1), (-2, 1), (-1, -2), (-1, 2),
                        (1, -2), (1, 2), (2, -1), (2, 1)]:
            nr, nc = r + dr, c + dc
            if _in_bounds(nr, nc) and board[nr][nc] is not None:
                targets.append((nr, nc))

    elif piece == PAWN:
        # Solitaire chess: pawns capture 1 square diagonally in ANY direction
        for dr, dc in [(-1, -1), (-1, 1), (1, -1), (1, 1)]:
            nr, nc = r + dr, c + dc
            if _in_bounds(nr, nc) and board[nr][nc] is not None:
                targets.append((nr, nc))

    return targets


def get_all_moves(board: Board) -> list[tuple[Pos, Pos]]:
    """Return all valid (from_pos, to_pos) capture moves."""
    moves: list[tuple[Pos, Pos]] = []
    for pos, _ in get_pieces(board):
        for target in get_captures(board, pos):
            moves.append((pos, target))
    return moves


# ── Move execution ───────────────────────────────────────────────────────────


def make_move(board: Board, from_pos: Pos, to_pos: Pos) -> str:
    """Execute a capture. Returns the captured piece type. Modifies board in place."""
    captured = board[to_pos[0]][to_pos[1]]
    assert captured is not None
    board[to_pos[0]][to_pos[1]] = board[from_pos[0]][from_pos[1]]
    board[from_pos[0]][from_pos[1]] = None
    return captured


def undo_move(board: Board, from_pos: Pos, to_pos: Pos, captured: str) -> None:
    """Undo a capture. Restores the captured piece."""
    board[from_pos[0]][from_pos[1]] = board[to_pos[0]][to_pos[1]]
    board[to_pos[0]][to_pos[1]] = captured


# ── Solver ───────────────────────────────────────────────────────────────────


def solve(board: Board) -> list[tuple[Pos, Pos]] | None:
    """Solve via backtracking. Returns move sequence or None if unsolvable."""
    if count_pieces(board) == 1:
        return []

    for from_pos, to_pos in get_all_moves(board):
        captured = board[to_pos[0]][to_pos[1]]
        moving = board[from_pos[0]][from_pos[1]]
        assert captured is not None and moving is not None

        board[to_pos[0]][to_pos[1]] = moving
        board[from_pos[0]][from_pos[1]] = None

        result = solve(board)
        if result is not None:
            return [(from_pos, to_pos), *result]

        board[from_pos[0]][from_pos[1]] = moving
        board[to_pos[0]][to_pos[1]] = captured

    return None


def get_hint(board: Board) -> tuple[Pos, Pos] | None:
    """Return one valid next move from the solution, or None if stuck."""
    solution = solve(copy_board(board))
    if solution:
        return solution[0]
    return None


# ── Puzzle generation ────────────────────────────────────────────────────────

# Weights: fewer queens, more pawns/knights for interesting puzzles
_PIECE_WEIGHTS = [10, 8, 15, 15, 20, 30]  # K, Q, R, B, N, P


def generate_puzzle(num_pieces: int, max_attempts: int = 1000) -> Board | None:
    """Generate a solvable puzzle with the given number of pieces.

    Returns None if no solvable puzzle found within max_attempts.
    """
    all_positions = [(r, c) for r in range(BOARD_SIZE) for c in range(BOARD_SIZE)]

    for _ in range(max_attempts):
        board = empty_board()
        positions = random.sample(all_positions, num_pieces)
        pieces = random.choices(ALL_PIECES, weights=_PIECE_WEIGHTS, k=num_pieces)

        # Cap queens at 1
        queen_indices = [i for i, p in enumerate(pieces) if p == QUEEN]
        for idx in queen_indices[1:]:
            pieces[idx] = random.choices(
                [KING, ROOK, BISHOP, KNIGHT, PAWN],
                weights=[10, 15, 15, 20, 30],
            )[0]

        for (r, c), piece in zip(positions, pieces):
            board[r][c] = piece

        # Quick check: at least one move must exist
        if not get_all_moves(board):
            continue

        if solve(copy_board(board)) is not None:
            return board

    return None


# ── Display / parsing ────────────────────────────────────────────────────────


def format_board(board: Board) -> str:
    """Format board as a code-block-ready string with coordinates."""
    lines = [
        "    A   B   C   D",
        "  \u250c\u2500\u2500\u2500\u252c\u2500\u2500\u2500\u252c\u2500\u2500\u2500\u252c\u2500\u2500\u2500\u2510",
    ]
    for r in range(BOARD_SIZE):
        cells: list[str] = []
        for c in range(BOARD_SIZE):
            piece = board[r][c]
            cells.append(f" {piece} " if piece else "   ")
        lines.append(f"{r + 1} \u2502{'\u2502'.join(cells)}\u2502")
        if r < BOARD_SIZE - 1:
            lines.append(
                "  \u251c\u2500\u2500\u2500\u253c\u2500\u2500\u2500"
                "\u253c\u2500\u2500\u2500\u253c\u2500\u2500\u2500\u2524"
            )
    lines.append(
        "  \u2514\u2500\u2500\u2500\u2534\u2500\u2500\u2500"
        "\u2534\u2500\u2500\u2500\u2534\u2500\u2500\u2500\u2518"
    )
    return "\n".join(lines)


def format_board_emoji(board: Board) -> str:
    """Format board using emoji for richer Discord display."""
    lines = ["`  A  B  C  D`"]
    for r in range(BOARD_SIZE):
        row_str = f"`{r + 1}` "
        for c in range(BOARD_SIZE):
            piece = board[r][c]
            if piece:
                row_str += PIECE_EMOJI[piece] + " "
            else:
                row_str += "\u2b1b "  # black square
        lines.append(row_str)
    return "\n".join(lines)


def piece_legend() -> str:
    """Return a compact piece legend string."""
    return " ".join(f"{PIECE_EMOJI[p]} {p}={PIECE_NAME[p]}" for p in ALL_PIECES)


def parse_pos(s: str) -> Pos | None:
    """Parse 'A1' to (row=0, col=0). Returns None if invalid."""
    s = s.strip().upper()
    if len(s) != 2:
        return None
    col_ch, row_ch = s[0], s[1]
    if col_ch not in "ABCD" or row_ch not in "1234":
        return None
    return (int(row_ch) - 1, ord(col_ch) - ord("A"))


def pos_to_str(pos: Pos) -> str:
    """Convert (row, col) to 'A1' format."""
    return f"{chr(ord('A') + pos[1])}{pos[0] + 1}"


def parse_move(s: str) -> tuple[Pos, Pos] | None:
    """Parse a move string like 'A1 C3', 'a1c3', 'A1 to C3'."""
    s = s.strip().upper()
    s = s.replace("TO", " ").replace("->", " ").replace("\u2192", " ").replace(",", " ")
    parts = s.split()
    if len(parts) >= 2:
        f = parse_pos(parts[0])
        t = parse_pos(parts[1])
        if f is not None and t is not None:
            return (f, t)
    # Try as 4 consecutive chars: A1C3
    cleaned = s.replace(" ", "")
    if len(cleaned) == 4:
        f = parse_pos(cleaned[:2])
        t = parse_pos(cleaned[2:])
        if f is not None and t is not None:
            return (f, t)
    return None
