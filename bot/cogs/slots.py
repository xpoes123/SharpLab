"""Casino cog — interactive /slots machine with free spins, bonus rounds, and gamble."""

import asyncio
import random
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries

# ── Symbols ──────────────────────────────────────────────────────────────────

SYMBOLS = {
    "CH": "\U0001f352",  # Cherry
    "LM": "\U0001f34b",  # Lemon
    "GR": "\U0001f347",  # Grape
    "BL": "\U0001f514",  # Bell
    "DI": "\U0001f48e",  # Diamond
    "SV": "7\ufe0f\u20e3",  # Seven
    "WD": "\U0001f0cf",  # Wild (joker)
    "SC": "\u2b50",  # Scatter (star)
    "BN": "\U0001f381",  # Bonus (gift)
}

SYMBOL_NAMES = {
    "CH": "Cherry", "LM": "Lemon", "GR": "Grape", "BL": "Bell",
    "DI": "Diamond", "SV": "Seven", "WD": "Wild", "SC": "Scatter",
    "BN": "Bonus",
}

SPIN_EMOJI = "\u2753"  # question mark for spinning reels

# ── Reel weights ─────────────────────────────────────────────────────────────

REEL_WEIGHTS: list[tuple[str, int]] = [
    ("CH", 8), ("LM", 8), ("GR", 6), ("BL", 4),
    ("DI", 2), ("SV", 1), ("WD", 2), ("SC", 1), ("BN", 1),
]

# Free spins: same base weights, no bonus symbol (replaced with extra wild)
FREE_SPIN_REEL_WEIGHTS: list[tuple[str, int]] = [
    ("CH", 8), ("LM", 8), ("GR", 6), ("BL", 4),
    ("DI", 2), ("SV", 1), ("WD", 3), ("SC", 1),
]  # WD 3 vs base 2 = slight boost during free spins

_REEL_SYMS = [s for s, _ in REEL_WEIGHTS]
_REEL_WTS = [w for _, w in REEL_WEIGHTS]
_FS_SYMS = [s for s, _ in FREE_SPIN_REEL_WEIGHTS]
_FS_WTS = [w for _, w in FREE_SPIN_REEL_WEIGHTS]

# ── Paylines (20 lines) ─────────────────────────────────────────────────────

PAYLINES: list[list[int]] = [
    [1, 1, 1, 1, 1],  # 1  center
    [0, 0, 0, 0, 0],  # 2  top
    [2, 2, 2, 2, 2],  # 3  bottom
    [0, 1, 2, 1, 0],  # 4  V
    [2, 1, 0, 1, 2],  # 5  ^
    [0, 0, 1, 0, 0],  # 6  dip
    [2, 2, 1, 2, 2],  # 7  bump
    [1, 0, 0, 0, 1],  # 8  U top
    [1, 2, 2, 2, 1],  # 9  U bot
    [0, 1, 0, 1, 0],  # 10 zig top
    [2, 1, 2, 1, 2],  # 11 zig bot
    [1, 0, 1, 0, 1],  # 12 wave top
    [1, 2, 1, 2, 1],  # 13 wave bot
    [0, 1, 1, 1, 0],  # 14 soft V
    [2, 1, 1, 1, 2],  # 15 soft ^
    [1, 1, 0, 1, 1],  # 16 peak
    [1, 1, 2, 1, 1],  # 17 valley
    [0, 0, 2, 2, 2],  # 18 step down
    [2, 2, 0, 0, 0],  # 19 step up
    [0, 2, 1, 2, 0],  # 20 W
]

NUM_LINES = len(PAYLINES)

# ── Paytable (multiplier of per-line bet) ────────────────────────────────────

PAYTABLE: dict[str, dict[int, int]] = {
    "CH": {3: 4, 4: 12, 5: 28},
    "LM": {3: 4, 4: 12, 5: 28},
    "GR": {3: 6, 4: 18, 5: 45},
    "BL": {3: 10, 4: 30, 5: 70},
    "DI": {3: 18, 4: 45, 5: 130},
    "SV": {3: 28, 4: 90, 5: 450},
    "WD": {3: 28, 4: 90, 5: 650},
}

# Scatter pays (multiplier of TOTAL bet, not per-line)
SCATTER_PAY: dict[int, int] = {3: 2, 4: 3, 5: 10}

# ── Free spins config ────────────────────────────────────────────────────────

FREE_SPIN_OPTIONS: list[tuple[int, int]] = [
    (5, 2),   # 5 spins at 2x multiplier — high risk
    (10, 1),  # 10 spins at 1x multiplier — balanced
    (15, 1),  # 15 spins at 1x multiplier — safe grind
]

# ── Bonus config ─────────────────────────────────────────────────────────────

BONUS_TRIGGER = 3
NUM_BOXES = 8
# Multipliers for bonus prizes (of total bet). Shuffled each bonus.
BONUS_PRIZE_POOL = [1, 1, 2, 2, 3, 5, 8]
NUM_TRAPS = 5  # skull = game over
# 3 prizes + 5 traps = 8 boxes
BONUS_PRIZES_USED = 3

# ── Gamble config ────────────────────────────────────────────────────────────

MAX_GAMBLE = 3
GAMBLE_CARDS = [
    ("A\u2665", "red"), ("K\u2666", "red"), ("Q\u2665", "red"), ("J\u2666", "red"),
    ("10\u2665", "red"), ("9\u2666", "red"), ("8\u2665", "red"),
    ("A\u2660", "black"), ("K\u2663", "black"), ("Q\u2660", "black"), ("J\u2663", "black"),
    ("10\u2660", "black"), ("9\u2663", "black"), ("8\u2660", "black"),
]

# ── Bet config ───────────────────────────────────────────────────────────────

MAX_BET = 500
MIN_BET = 10
BET_STEP = 10
DEFAULT_BET = 20

ANIM_DELAY = 0.6  # seconds between reel lock frames


# ── Helpers ──────────────────────────────────────────────────────────────────


def _spin_reels(free_spin: bool = False) -> list[list[str]]:
    """Generate a 5×3 grid. Returns grid[reel][row]."""
    syms = _FS_SYMS if free_spin else _REEL_SYMS
    wts = _FS_WTS if free_spin else _REEL_WTS
    grid: list[list[str]] = []
    for _ in range(5):
        col = random.choices(syms, weights=wts, k=3)
        grid.append(col)
    return grid


def _render_grid(
    grid: list[list[str]], *, locked_reels: int = 5,
) -> str:
    """Render 5×3 grid as emoji text. Reels beyond locked_reels show SPIN_EMOJI."""
    lines: list[str] = []
    for row in range(3):
        cells: list[str] = []
        for reel in range(5):
            if reel < locked_reels:
                cells.append(SYMBOLS[grid[reel][row]])
            else:
                cells.append(SPIN_EMOJI)
        line = " \u2502 ".join(cells)
        lines.append(f"\u2503 {line} \u2503")
    top = "\u250f" + "\u2501" * 34 + "\u2513"
    bot = "\u2517" + "\u2501" * 34 + "\u251b"
    return f"{top}\n" + "\n".join(lines) + f"\n{bot}"


def _eval_paylines(
    grid: list[list[str]],
) -> list[tuple[int, str, int, int]]:
    """Evaluate all paylines. Returns [(line_num, symbol, count, multiplier), ...]."""
    wins: list[tuple[int, str, int, int]] = []
    for i, line in enumerate(PAYLINES):
        symbols_on_line = [grid[reel][line[reel]] for reel in range(5)]
        # Find the first non-wild symbol
        base = None
        for s in symbols_on_line:
            if s != "WD" and s != "SC" and s != "BN":
                base = s
                break
        if base is None:
            # All wilds
            base = "WD"

        # Count consecutive matches from left (wilds match anything)
        count = 0
        for s in symbols_on_line:
            if s == base or s == "WD":
                count += 1
            else:
                break

        if count >= 3 and base in PAYTABLE:
            mult = PAYTABLE[base].get(count, 0)
            if mult > 0:
                wins.append((i + 1, base, count, mult))
    return wins


def _count_symbol(grid: list[list[str]], sym: str) -> int:
    """Count total occurrences of a symbol anywhere on the grid."""
    total = 0
    for reel in grid:
        for s in reel:
            if s == sym:
                total += 1
    return total


def _generate_bonus_boxes(total_bet: int) -> list[tuple[str, int]]:
    """Generate 8 bonus boxes: 6 prizes + 2 traps.

    Returns [(type, value), ...] where type is 'prize' or 'trap'.
    """
    prizes = random.sample(BONUS_PRIZE_POOL, BONUS_PRIZES_USED)
    boxes: list[tuple[str, int]] = [("prize", m * total_bet) for m in prizes]
    boxes.extend([("trap", 0)] * NUM_TRAPS)
    random.shuffle(boxes)
    return boxes


# ── Session ──────────────────────────────────────────────────────────────────


@dataclass
class SlotsSession:
    user_id: int
    display_name: str
    channel_id: int
    bet: int = DEFAULT_BET
    message: discord.Message | None = None
    grid: list[list[str]] = field(default_factory=list)
    # Spin results
    line_wins: list[tuple[int, str, int, int]] = field(default_factory=list)
    scatter_count: int = 0
    bonus_count: int = 0
    last_win: int = 0
    last_scatter_pay: int = 0
    total_spins: int = 0
    total_wagered: int = 0
    total_won: int = 0
    # Free spins
    free_spins_left: int = 0
    free_spin_mult: int = 1
    free_spin_total: int = 0
    free_spin_trigger: bool = False  # just triggered, need pick
    # Bonus
    bonus_active: bool = False
    bonus_boxes: list[tuple[str, int]] = field(default_factory=list)
    bonus_picked: list[bool] = field(default_factory=list)
    bonus_total: int = 0
    bonus_picks_made: int = 0
    bonus_ended: bool = False
    # Gamble
    gamble_amount: int = 0
    gamble_count: int = 0
    gamble_active: bool = False
    gamble_card: str = ""
    gamble_color: str = ""
    # Auto spin
    auto_spins_left: int = 0
    # Phase
    phase: str = "idle"  # idle | result | free_pick | free_spinning | bonus | gamble

    @property
    def line_bet(self) -> int:
        return max(1, self.bet // NUM_LINES)

    def calc_line_win(self) -> int:
        """Total from payline wins."""
        return sum(self.line_bet * mult for _, _, _, mult in self.line_wins)

    def calc_scatter_win(self) -> int:
        """Total from scatter pays."""
        return SCATTER_PAY.get(self.scatter_count, 0) * self.bet

    def calc_total_win(self) -> int:
        """Total win for this spin."""
        base = self.calc_line_win() + self.calc_scatter_win()
        if self.free_spin_mult > 1:
            return base * self.free_spin_mult
        return base


# ── Embeds ───────────────────────────────────────────────────────────────────


def _idle_embed(session: SlotsSession, balance: int) -> discord.Embed:
    embed = discord.Embed(
        title="\U0001f3b0 Fortune Reels \U0001f3b0",
        colour=discord.Colour.dark_purple(),
    )
    if session.grid:
        embed.description = _render_grid(session.grid)
    else:
        # Show an empty/welcome grid
        embed.description = (
            "Welcome to **Fortune Reels**!\n"
            "Set your bet and hit **Spin** to play.\n\n"
            "\u2b50 3+ Scatters = **Free Spins**\n"
            "\U0001f381 3+ Bonus = **Pick Bonus**\n"
            "\U0001f0cf Wilds substitute for any symbol"
        )
    embed.add_field(
        name="",
        value=f"Bet: **{session.bet}c** ({session.line_bet}c \u00d7 {NUM_LINES} lines)  \u2502  Balance: **{balance}c**",
        inline=False,
    )
    if session.total_spins > 0:
        net = session.total_won - session.total_wagered
        sign = "+" if net >= 0 else ""
        embed.set_footer(
            text=f"Spins: {session.total_spins}  \u2022  Net: {sign}{net}c",
        )
    return embed


def _result_embed(session: SlotsSession, balance: int) -> discord.Embed:
    win = session.last_win
    if win >= session.bet * 50:
        title = "\U0001f3b0 \U0001f525 MEGA WIN \U0001f525 \U0001f3b0"
        colour = discord.Colour.gold()
    elif win >= session.bet * 10:
        title = "\U0001f3b0 \U0001f389 BIG WIN \U0001f389 \U0001f3b0"
        colour = discord.Colour.orange()
    elif win > 0:
        title = "\U0001f3b0 Fortune Reels \u2014 WIN \U0001f3b0"
        colour = discord.Colour.green()
    else:
        title = "\U0001f3b0 Fortune Reels \U0001f3b0"
        colour = discord.Colour.dark_purple()

    embed = discord.Embed(title=title, colour=colour)
    embed.description = _render_grid(session.grid)

    # Win details
    parts: list[str] = []
    if session.line_wins:
        # Group by symbol for cleaner display
        sym_wins: dict[str, list[tuple[int, int, int]]] = {}
        for line_num, sym, count, mult in session.line_wins:
            sym_wins.setdefault(sym, []).append((line_num, count, mult))
        for sym, hits in sym_wins.items():
            total_mult = sum(m for _, _, m in hits)
            emoji = SYMBOLS[sym]
            name = SYMBOL_NAMES[sym]
            lines_str = ", ".join(f"L{ln}" for ln, _, _ in hits)
            parts.append(f"{emoji} {name} \u00d7{len(hits)} ({lines_str}) \u2192 **{session.line_bet * total_mult}c**")

    if session.scatter_count >= 3:
        scatter_pay = session.calc_scatter_win()
        parts.append(f"\u2b50 Scatter \u00d7{session.scatter_count} \u2192 **{scatter_pay}c**")

    if session.free_spin_mult > 1:
        parts.append(f"\U0001f4ab Free Spin **{session.free_spin_mult}x** multiplier active!")

    if parts:
        embed.add_field(name="Wins", value="\n".join(parts), inline=False)

    # Triggers
    triggers: list[str] = []
    if session.free_spin_trigger:
        triggers.append(f"\u2b50 **FREE SPINS TRIGGERED!** Pick your bonus below!")
    if session.bonus_count >= BONUS_TRIGGER:
        triggers.append(f"\U0001f381 **BONUS ROUND TRIGGERED!** Pick your prizes!")
    if triggers:
        embed.add_field(name="", value="\n".join(triggers), inline=False)

    # Totals
    if win > 0:
        mult_str = ""
        if session.free_spin_mult > 1:
            mult_str = f" (\u00d7{session.free_spin_mult})"
        embed.add_field(
            name="",
            value=f"\U0001f4b0 Won **{win}c**{mult_str}  \u2502  Balance: **{balance}c**",
            inline=False,
        )
    else:
        embed.add_field(
            name="",
            value=f"No win  \u2502  Balance: **{balance}c**",
            inline=False,
        )

    # Free spins counter
    if session.free_spins_left > 0:
        embed.set_footer(
            text=f"Free Spins: {session.free_spins_left} remaining  \u2022  FS Total: {session.free_spin_total}c",
        )
    else:
        net = session.total_won - session.total_wagered
        sign = "+" if net >= 0 else ""
        embed.set_footer(
            text=f"Spins: {session.total_spins}  \u2022  Net: {sign}{net}c",
        )

    return embed


def _free_pick_embed(session: SlotsSession) -> discord.Embed:
    embed = discord.Embed(
        title="\u2b50 FREE SPINS \u2014 Choose Your Bonus! \u2b50",
        colour=discord.Colour.gold(),
    )
    embed.description = _render_grid(session.grid)
    lines: list[str] = []
    for i, (spins, mult) in enumerate(FREE_SPIN_OPTIONS):
        lines.append(f"**Option {i + 1}:** {spins} Free Spins \u00d7 **{mult}x** Multiplier")
    embed.add_field(name="Pick One", value="\n".join(lines), inline=False)
    return embed


def _bonus_embed(session: SlotsSession, balance: int) -> discord.Embed:
    embed = discord.Embed(
        title="\U0001f381 BONUS ROUND \u2014 Pick a Box! \U0001f381",
        colour=discord.Colour.gold(),
    )

    # Show boxes as a grid
    box_lines: list[str] = []
    for i in range(NUM_BOXES):
        if session.bonus_picked[i]:
            btype, val = session.bonus_boxes[i]
            if btype == "trap":
                box_lines.append(f"~~Box {i + 1}~~ \U0001f480 Trap!")
            else:
                box_lines.append(f"~~Box {i + 1}~~ \U0001f4b0 **{val}c**")
        else:
            box_lines.append(f"\U0001f381 Box {i + 1}")

    embed.description = "\n".join(box_lines)

    if session.bonus_total > 0:
        embed.add_field(
            name="Collected",
            value=f"\U0001f4b0 **{session.bonus_total}c** so far",
            inline=False,
        )

    if session.bonus_ended:
        embed.colour = discord.Colour.dark_gold()
        embed.title = "\U0001f381 BONUS ROUND \u2014 Complete!"
        embed.add_field(
            name="",
            value=f"Total bonus: **{session.bonus_total}c**  \u2502  Balance: **{balance}c**",
            inline=False,
        )
    else:
        picks_left = BONUS_PRIZES_USED - session.bonus_picks_made
        embed.set_footer(text=f"Pick a box! {picks_left} safe boxes remain.")

    return embed


def _gamble_embed(
    session: SlotsSession, balance: int, *, result: str | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title="\U0001f3b2 Gamble \u2014 Double or Nothing!",
        colour=discord.Colour.gold(),
    )

    if result is None:
        embed.description = (
            f"Your winnings: **{session.gamble_amount}c**\n\n"
            f"\U0001f0cf Pick **Red** or **Black** to double!\n"
            f"Gamble {session.gamble_count}/{MAX_GAMBLE}"
        )
    elif result == "win":
        embed.description = (
            f"Card: `{session.gamble_card}`\n\n"
            f"\u2705 **Correct!** Winnings doubled to **{session.gamble_amount}c**!\n"
            f"Gamble {session.gamble_count}/{MAX_GAMBLE}"
        )
        embed.colour = discord.Colour.green()
    else:
        embed.description = (
            f"Card: `{session.gamble_card}`\n\n"
            f"\u274c **Wrong!** You lost **{session.gamble_amount}c**!"
        )
        embed.colour = discord.Colour.red()

    embed.add_field(
        name="",
        value=f"Balance: **{balance}c**",
        inline=False,
    )
    return embed


def _free_spins_summary_embed(session: SlotsSession, balance: int) -> discord.Embed:
    embed = discord.Embed(
        title="\u2b50 FREE SPINS COMPLETE! \u2b50",
        colour=discord.Colour.gold(),
    )
    embed.description = _render_grid(session.grid)
    embed.add_field(
        name="Results",
        value=(
            f"Total won: **{session.free_spin_total}c**\n"
            f"Multiplier: **{session.free_spin_mult}x**\n"
            f"Balance: **{balance}c**"
        ),
        inline=False,
    )
    net = session.total_won - session.total_wagered
    sign = "+" if net >= 0 else ""
    embed.set_footer(text=f"Spins: {session.total_spins}  \u2022  Net: {sign}{net}c")
    return embed


# ── Paytable embed ───────────────────────────────────────────────────────────


def _paytable_embed() -> discord.Embed:
    embed = discord.Embed(
        title="\U0001f3b0 Fortune Reels \u2014 Paytable",
        colour=discord.Colour.dark_purple(),
    )

    lines: list[str] = []
    for sym in ("CH", "LM", "GR", "BL", "DI", "SV", "WD"):
        pays = PAYTABLE[sym]
        emoji = SYMBOLS[sym]
        name = SYMBOL_NAMES[sym]
        pay_str = " / ".join(f"{c}\u00d7={p}x" for c, p in sorted(pays.items()))
        lines.append(f"{emoji} **{name}**: {pay_str}")

    embed.add_field(name="Line Pays (per line bet)", value="\n".join(lines), inline=False)

    scatter_lines = [
        f"\u2b50 **Scatter**: {c}\u00d7 = {p}x total bet + Free Spins"
        for c, p in sorted(SCATTER_PAY.items())
    ]
    embed.add_field(name="Scatter Pays", value="\n".join(scatter_lines), inline=False)

    embed.add_field(
        name="Special Features",
        value=(
            "\U0001f0cf **Wild** \u2014 Substitutes for all symbols (except Scatter/Bonus)\n"
            "\u2b50 **Scatter** \u2014 3+ anywhere triggers **Free Spins** with multiplier\n"
            "\U0001f381 **Bonus** \u2014 3+ anywhere triggers **Pick Bonus** round\n"
            "\U0001f3b2 **Gamble** \u2014 After any win, double or nothing (up to 3x)"
        ),
        inline=False,
    )

    fs_lines = [f"**{s} Spins \u00d7 {m}x** multiplier" for s, m in FREE_SPIN_OPTIONS]
    embed.add_field(
        name="Free Spin Options",
        value="\n".join(fs_lines),
        inline=False,
    )

    embed.set_footer(text=f"{NUM_LINES} paylines  \u2022  Min bet: {MIN_BET}c  \u2022  Max bet: {MAX_BET}c")
    return embed


# ── Views ────────────────────────────────────────────────────────────────────


class SlotsMainView(ui.View):
    """Main slots view — spin, bet adjust, gamble, quit."""

    def __init__(
        self, session: SlotsSession, active: dict[int, "SlotsSession"],
        cog: "SlotsCog",
    ) -> None:
        super().__init__(timeout=180)
        self.session = session
        self.active = active
        self.cog = cog
        self._update_buttons()

    def _update_buttons(self) -> None:
        s = self.session
        can_spin = s.phase in ("idle", "result") and not s.free_spin_trigger
        has_win = s.phase == "result" and s.last_win > 0 and not s.free_spin_trigger
        in_free = s.free_spins_left > 0

        self.spin_btn.disabled = not can_spin
        self.auto_btn.disabled = not can_spin or in_free
        self.bet_down_btn.disabled = not can_spin or s.bet <= MIN_BET or in_free
        self.bet_up_btn.disabled = not can_spin or s.bet >= MAX_BET or in_free
        self.max_bet_btn.disabled = not can_spin or s.bet >= MAX_BET or in_free
        self.gamble_btn.disabled = not has_win or s.gamble_count >= MAX_GAMBLE or in_free
        self.paytable_btn.disabled = False
        self.quit_btn.disabled = False

        if in_free:
            self.spin_btn.label = f"Free Spin ({s.free_spins_left})"
            self.spin_btn.emoji = "\u2b50"
        else:
            self.spin_btn.label = "Spin"
            self.spin_btn.emoji = "\U0001f3b0"

    # ── Row 0: Spin / Auto ──────────────────────────────────────

    @ui.button(label="Spin", style=discord.ButtonStyle.success, emoji="\U0001f3b0", row=0)
    async def spin_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.session.user_id:
            await interaction.response.send_message("Not your machine!", ephemeral=True)
            return
        await self._do_spin(interaction)

    @ui.button(label="Auto \u00d75", style=discord.ButtonStyle.primary, emoji="\u25b6\ufe0f", row=0)
    async def auto_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.session.user_id:
            await interaction.response.send_message("Not your machine!", ephemeral=True)
            return
        if self.session.free_spins_left > 0:
            await interaction.response.send_message("Can't auto-spin during free spins!", ephemeral=True)
            return
        self.session.auto_spins_left = 5
        await self._do_spin(interaction)

    # ── Row 1: Bet controls ─────────────────────────────────────

    @ui.button(label="Bet -", style=discord.ButtonStyle.secondary, emoji="\u2796", row=1)
    async def bet_down_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.session.user_id:
            await interaction.response.send_message("Not your machine!", ephemeral=True)
            return
        self.session.bet = max(MIN_BET, self.session.bet - BET_STEP)
        bal = await queries.get_casino_balance(str(self.session.user_id)) or 0
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_idle_embed(self.session, bal), view=self,
        )

    @ui.button(label="Bet +", style=discord.ButtonStyle.secondary, emoji="\u2795", row=1)
    async def bet_up_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.session.user_id:
            await interaction.response.send_message("Not your machine!", ephemeral=True)
            return
        self.session.bet = min(MAX_BET, self.session.bet + BET_STEP)
        bal = await queries.get_casino_balance(str(self.session.user_id)) or 0
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_idle_embed(self.session, bal), view=self,
        )

    @ui.button(label="Max Bet", style=discord.ButtonStyle.secondary, emoji="\U0001f4b0", row=1)
    async def max_bet_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.session.user_id:
            await interaction.response.send_message("Not your machine!", ephemeral=True)
            return
        self.session.bet = MAX_BET
        bal = await queries.get_casino_balance(str(self.session.user_id)) or 0
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_idle_embed(self.session, bal), view=self,
        )

    # ── Row 2: Gamble / Paytable / Quit ─────────────────────────

    @ui.button(label="Gamble", style=discord.ButtonStyle.danger, emoji="\U0001f3b2", row=2)
    async def gamble_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.session.user_id:
            await interaction.response.send_message("Not your machine!", ephemeral=True)
            return
        s = self.session
        if s.last_win <= 0:
            await interaction.response.send_message("Nothing to gamble!", ephemeral=True)
            return
        s.gamble_amount = s.last_win
        s.gamble_active = True
        s.phase = "gamble"
        bal = await queries.get_casino_balance(str(s.user_id)) or 0
        view = GambleView(s, self.active, self.cog)
        await interaction.response.edit_message(
            embed=_gamble_embed(s, bal), view=view,
        )

    @ui.button(label="Paytable", style=discord.ButtonStyle.secondary, emoji="\U0001f4cb", row=2)
    async def paytable_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        await interaction.response.send_message(
            embed=_paytable_embed(), ephemeral=True,
        )

    @ui.button(label="Quit", style=discord.ButtonStyle.secondary, emoji="\u274c", row=2)
    async def quit_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.session.user_id:
            await interaction.response.send_message("Not your machine!", ephemeral=True)
            return
        await self._close(interaction)

    # ── Spin logic ──────────────────────────────────────────────

    async def _do_spin(self, interaction: discord.Interaction) -> None:
        s = self.session
        in_free = s.free_spins_left > 0

        # Deduct bet (free spins don't cost)
        if not in_free:
            try:
                await queries.update_casino_balance(str(s.user_id), -s.bet)
            except ValueError:
                bal = await queries.get_or_create_casino_wallet(str(s.user_id))
                await interaction.response.send_message(
                    f"Not enough coins! (have {bal}c)", ephemeral=True,
                )
                s.auto_spins_left = 0
                return
            s.total_wagered += s.bet
        else:
            s.free_spins_left -= 1

        s.total_spins += 1

        # Generate result
        grid = _spin_reels(free_spin=in_free)
        s.grid = grid

        # Evaluate
        s.line_wins = _eval_paylines(grid)
        s.scatter_count = _count_symbol(grid, "SC")
        s.bonus_count = _count_symbol(grid, "BN")

        # Check triggers
        s.free_spin_trigger = s.scatter_count >= 3 and not in_free
        bonus_triggered = s.bonus_count >= BONUS_TRIGGER and not in_free

        # Calculate win
        win = s.calc_total_win()
        s.last_win = win
        s.total_won += win

        if in_free:
            s.free_spin_total += win

        # Credit win
        if win > 0:
            await queries.update_casino_balance(str(s.user_id), win)

        # Phase 1: Show spinning animation
        anim_grid = _spin_reels()  # random noise for anim frame
        self._update_buttons()
        # Disable all buttons during animation
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True  # type: ignore[union-attr]

        anim_embed = discord.Embed(
            title="\U0001f3b0 Fortune Reels \u2014 Spinning... \U0001f3b0",
            description=_render_grid(anim_grid, locked_reels=0),
            colour=discord.Colour.dark_purple(),
        )
        bet_label = "FREE SPIN" if in_free else f"Bet: {s.bet}c"
        anim_embed.add_field(name="", value=bet_label, inline=False)
        await interaction.response.edit_message(embed=anim_embed, view=self)

        # Phase 2: Lock reels left to right
        for locked in (2, 4):
            await asyncio.sleep(ANIM_DELAY)
            embed = discord.Embed(
                title="\U0001f3b0 Fortune Reels \u2014 Spinning... \U0001f3b0",
                description=_render_grid(grid, locked_reels=locked),
                colour=discord.Colour.dark_purple(),
            )
            embed.add_field(name="", value=bet_label, inline=False)
            if s.message:
                try:
                    await s.message.edit(embed=embed, view=self)
                except discord.HTTPException:
                    pass

        # Phase 3: Show result
        await asyncio.sleep(ANIM_DELAY)
        s.phase = "result"
        s.gamble_count = 0
        bal = await queries.get_casino_balance(str(s.user_id)) or 0

        # Decide what to show
        if s.free_spin_trigger:
            s.phase = "free_pick"
            view = FreeSpinPickView(s, self.active, self.cog)
            embed = _free_pick_embed(s)
        elif bonus_triggered:
            s.bonus_active = True
            s.bonus_boxes = _generate_bonus_boxes(s.bet)
            s.bonus_picked = [False] * NUM_BOXES
            s.bonus_total = 0
            s.bonus_picks_made = 0
            s.bonus_ended = False
            s.phase = "bonus"
            view = BonusPickView(s, self.active, self.cog)
            embed = _bonus_embed(s, bal)
        elif s.free_spins_left == 0 and in_free:
            # Free spins just ended — show summary
            embed = _free_spins_summary_embed(s, bal)
            s.free_spin_mult = 1
            s.phase = "result"
            self._update_buttons()
            view = self
        else:
            embed = _result_embed(s, bal)
            self._update_buttons()
            view = self

        if s.message:
            try:
                await s.message.edit(embed=embed, view=view)
            except discord.HTTPException:
                pass

        # Auto-spin continuation
        if (
            s.auto_spins_left > 0
            and s.phase == "result"
            and not s.free_spin_trigger
            and not bonus_triggered
            and s.free_spins_left == 0
        ):
            s.auto_spins_left -= 1
            if s.auto_spins_left > 0:
                await asyncio.sleep(1.0)
                # Create a fake interaction context — re-use the message
                await self._auto_spin_tick()

        # Free spin auto-play
        if s.free_spins_left > 0 and s.phase == "result":
            await asyncio.sleep(1.0)
            await self._free_spin_tick()

    async def _auto_spin_tick(self) -> None:
        """Continue auto-spin without a new interaction."""
        s = self.session
        in_free = s.free_spins_left > 0

        if not in_free:
            try:
                await queries.update_casino_balance(str(s.user_id), -s.bet)
            except ValueError:
                s.auto_spins_left = 0
                return
            s.total_wagered += s.bet

        s.total_spins += 1
        grid = _spin_reels(free_spin=in_free)
        s.grid = grid
        s.line_wins = _eval_paylines(grid)
        s.scatter_count = _count_symbol(grid, "SC")
        s.bonus_count = _count_symbol(grid, "BN")
        s.free_spin_trigger = s.scatter_count >= 3 and not in_free
        bonus_triggered = s.bonus_count >= BONUS_TRIGGER and not in_free

        win = s.calc_total_win()
        s.last_win = win
        s.total_won += win
        if win > 0:
            await queries.update_casino_balance(str(s.user_id), win)

        s.phase = "result"
        s.gamble_count = 0
        bal = await queries.get_casino_balance(str(s.user_id)) or 0

        # Check if triggers stop auto-spin
        if s.free_spin_trigger or bonus_triggered:
            s.auto_spins_left = 0
            if s.free_spin_trigger:
                s.phase = "free_pick"
                view = FreeSpinPickView(s, self.active, self.cog)
                embed = _free_pick_embed(s)
            else:
                s.bonus_active = True
                s.bonus_boxes = _generate_bonus_boxes(s.bet)
                s.bonus_picked = [False] * NUM_BOXES
                s.bonus_total = 0
                s.bonus_picks_made = 0
                s.bonus_ended = False
                s.phase = "bonus"
                view = BonusPickView(s, self.active, self.cog)
                embed = _bonus_embed(s, bal)
            if s.message:
                try:
                    await s.message.edit(embed=embed, view=view)
                except discord.HTTPException:
                    pass
            return

        embed = _result_embed(s, bal)
        self._update_buttons()
        if s.message:
            try:
                await s.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass

        # Continue auto
        if s.auto_spins_left > 0:
            s.auto_spins_left -= 1
            if s.auto_spins_left > 0:
                await asyncio.sleep(1.0)
                await self._auto_spin_tick()

    async def _free_spin_tick(self) -> None:
        """Auto-play one free spin."""
        s = self.session
        s.free_spins_left -= 1
        s.total_spins += 1

        grid = _spin_reels(free_spin=True)
        s.grid = grid
        s.line_wins = _eval_paylines(grid)
        s.scatter_count = _count_symbol(grid, "SC")
        s.bonus_count = 0  # no bonus during free spins

        win = s.calc_total_win()
        s.last_win = win
        s.total_won += win
        s.free_spin_total += win

        if win > 0:
            await queries.update_casino_balance(str(s.user_id), win)

        # Re-trigger: 3+ scatters during free spins awards more spins
        if s.scatter_count >= 3:
            extra = FREE_SPIN_OPTIONS[1][0]  # +10 spins at same multiplier
            s.free_spins_left += extra

        s.phase = "result"
        bal = await queries.get_casino_balance(str(s.user_id)) or 0

        if s.free_spins_left == 0:
            # Done — show summary
            embed = _free_spins_summary_embed(s, bal)
            s.free_spin_mult = 1
            self._update_buttons()
            if s.message:
                try:
                    await s.message.edit(embed=embed, view=self)
                except discord.HTTPException:
                    pass
            return

        embed = _result_embed(s, bal)
        self._update_buttons()
        if s.message:
            try:
                await s.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass

        await asyncio.sleep(1.2)
        await self._free_spin_tick()

    # ── Close / timeout ─────────────────────────────────────────

    async def _close(self, interaction: discord.Interaction) -> None:
        s = self.session
        net = s.total_won - s.total_wagered
        sign = "+" if net >= 0 else ""
        bal = await queries.get_casino_balance(str(s.user_id)) or 0
        embed = discord.Embed(
            title="\U0001f3b0 Fortune Reels \u2014 Session Over",
            description=(
                f"**{s.display_name}** played {s.total_spins} spins.\n"
                f"Net result: **{sign}{net}c**\n"
                f"Balance: **{bal}c**"
            ),
            colour=discord.Colour.dark_grey(),
        )
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active.pop(s.channel_id, None)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        s = self.session
        self.active.pop(s.channel_id, None)
        if s.message:
            try:
                bal = await queries.get_casino_balance(str(s.user_id)) or 0
                net = s.total_won - s.total_wagered
                sign = "+" if net >= 0 else ""
                embed = discord.Embed(
                    title="\U0001f3b0 Fortune Reels \u2014 Timed Out",
                    description=(
                        f"{s.total_spins} spins  \u2022  Net: {sign}{net}c"
                        f"  \u2022  Balance: {bal}c"
                    ),
                    colour=discord.Colour.dark_grey(),
                )
                await s.message.edit(embed=embed, view=None)
            except Exception:
                pass


# ── Free Spin Pick View ──────────────────────────────────────────────────────


class FreeSpinPickView(ui.View):
    """Pick your free spin option."""

    def __init__(
        self, session: SlotsSession, active: dict[int, "SlotsSession"],
        cog: "SlotsCog",
    ) -> None:
        super().__init__(timeout=60)
        self.session = session
        self.active = active
        self.cog = cog

    @ui.button(
        label="5 Spins \u00d72x", style=discord.ButtonStyle.danger,
        emoji="\U0001f525", row=0,
    )
    async def pick_1(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        await self._pick(interaction, 0)

    @ui.button(
        label="10 Spins \u00d71x", style=discord.ButtonStyle.primary,
        emoji="\u2b50", row=0,
    )
    async def pick_2(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        await self._pick(interaction, 1)

    @ui.button(
        label="15 Spins \u00d71x", style=discord.ButtonStyle.success,
        emoji="\U0001f4ab", row=0,
    )
    async def pick_3(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        await self._pick(interaction, 2)

    async def _pick(self, interaction: discord.Interaction, idx: int) -> None:
        if interaction.user.id != self.session.user_id:
            await interaction.response.send_message("Not your machine!", ephemeral=True)
            return
        s = self.session
        spins, mult = FREE_SPIN_OPTIONS[idx]
        s.free_spins_left = spins
        s.free_spin_mult = mult
        s.free_spin_total = 0
        s.free_spin_trigger = False
        s.phase = "result"

        bal = await queries.get_casino_balance(str(s.user_id)) or 0
        embed = discord.Embed(
            title=f"\u2b50 Free Spins Starting! \u2b50",
            description=(
                f"**{spins} Free Spins** with **{mult}x** multiplier!\n\n"
                f"Sit back and watch the reels spin..."
            ),
            colour=discord.Colour.gold(),
        )
        embed.add_field(name="", value=f"Balance: **{bal}c**", inline=False)

        main_view = SlotsMainView(s, self.active, self.cog)
        main_view._update_buttons()
        await interaction.response.edit_message(embed=embed, view=main_view)

        # Start auto-playing free spins
        await asyncio.sleep(1.5)
        await main_view._free_spin_tick()

    async def on_timeout(self) -> None:
        # Auto-pick option 2 (balanced)
        s = self.session
        spins, mult = FREE_SPIN_OPTIONS[1]
        s.free_spins_left = spins
        s.free_spin_mult = mult
        s.free_spin_total = 0
        s.free_spin_trigger = False
        s.phase = "result"
        main_view = SlotsMainView(s, self.active, self.cog)
        main_view._update_buttons()

        if s.message:
            try:
                embed = discord.Embed(
                    title="\u2b50 Free Spins Auto-Selected! \u2b50",
                    description=f"**{spins} Free Spins** with **{mult}x** multiplier!",
                    colour=discord.Colour.gold(),
                )
                await s.message.edit(embed=embed, view=main_view)
                await asyncio.sleep(1.5)
                await main_view._free_spin_tick()
            except Exception:
                pass


# ── Bonus Pick View ──────────────────────────────────────────────────────────


class BonusPickView(ui.View):
    """Pick-a-box bonus round."""

    def __init__(
        self, session: SlotsSession, active: dict[int, "SlotsSession"],
        cog: "SlotsCog",
    ) -> None:
        super().__init__(timeout=60)
        self.session = session
        self.active = active
        self.cog = cog
        self._build_buttons()

    def _build_buttons(self) -> None:
        self.clear_items()
        s = self.session
        for i in range(NUM_BOXES):
            row = 0 if i < 4 else 1
            if s.bonus_picked[i]:
                btype, val = s.bonus_boxes[i]
                if btype == "trap":
                    btn = ui.Button(
                        label="\U0001f480 Trap!", style=discord.ButtonStyle.danger,
                        disabled=True, row=row,
                    )
                else:
                    btn = ui.Button(
                        label=f"{val}c", style=discord.ButtonStyle.success,
                        disabled=True, row=row,
                    )
            else:
                if s.bonus_ended:
                    # Reveal all remaining
                    btype, val = s.bonus_boxes[i]
                    if btype == "trap":
                        btn = ui.Button(
                            label="\U0001f480", style=discord.ButtonStyle.secondary,
                            disabled=True, row=row,
                        )
                    else:
                        btn = ui.Button(
                            label=f"({val}c)", style=discord.ButtonStyle.secondary,
                            disabled=True, row=row,
                        )
                else:
                    btn = ui.Button(
                        label=f"Box {i + 1}", style=discord.ButtonStyle.primary,
                        emoji="\U0001f381", row=row,
                        custom_id=f"bonus_box_{i}",
                    )
                    btn.callback = self._make_callback(i)
            self.add_item(btn)

        # Collect button on row 2 (only when bonus ended)
        if s.bonus_ended:
            collect = ui.Button(
                label="Continue", style=discord.ButtonStyle.success,
                emoji="\u25b6\ufe0f", row=2,
            )
            collect.callback = self._collect
            self.add_item(collect)

    def _make_callback(self, idx: int):
        async def callback(interaction: discord.Interaction) -> None:
            await self._pick_box(interaction, idx)
        return callback

    async def _pick_box(self, interaction: discord.Interaction, idx: int) -> None:
        if interaction.user.id != self.session.user_id:
            await interaction.response.send_message("Not your machine!", ephemeral=True)
            return
        s = self.session
        if s.bonus_picked[idx] or s.bonus_ended:
            return

        s.bonus_picked[idx] = True
        btype, val = s.bonus_boxes[idx]

        if btype == "trap":
            s.bonus_ended = True
        else:
            s.bonus_total += val
            s.bonus_picks_made += 1
            # Check if all prizes found
            if s.bonus_picks_made >= BONUS_PRIZES_USED:
                s.bonus_ended = True

        if s.bonus_ended:
            # Credit bonus winnings
            if s.bonus_total > 0:
                await queries.update_casino_balance(str(s.user_id), s.bonus_total)
                s.total_won += s.bonus_total
                s.last_win += s.bonus_total

        bal = await queries.get_casino_balance(str(s.user_id)) or 0
        self._build_buttons()
        await interaction.response.edit_message(
            embed=_bonus_embed(s, bal), view=self,
        )

    async def _collect(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.session.user_id:
            await interaction.response.send_message("Not your machine!", ephemeral=True)
            return
        s = self.session
        s.bonus_active = False
        s.phase = "result"
        bal = await queries.get_casino_balance(str(s.user_id)) or 0
        main_view = SlotsMainView(s, self.active, self.cog)
        main_view._update_buttons()
        await interaction.response.edit_message(
            embed=_result_embed(s, bal), view=main_view,
        )

    async def on_timeout(self) -> None:
        s = self.session
        if not s.bonus_ended:
            s.bonus_ended = True
            if s.bonus_total > 0:
                await queries.update_casino_balance(str(s.user_id), s.bonus_total)
                s.total_won += s.bonus_total
        s.bonus_active = False
        s.phase = "result"
        bal = await queries.get_casino_balance(str(s.user_id)) or 0
        main_view = SlotsMainView(s, self.active, self.cog)
        main_view._update_buttons()
        if s.message:
            try:
                await s.message.edit(
                    embed=_result_embed(s, bal), view=main_view,
                )
            except Exception:
                pass


# ── Gamble View ──────────────────────────────────────────────────────────────


class GambleView(ui.View):
    """Double or nothing gamble feature."""

    def __init__(
        self, session: SlotsSession, active: dict[int, "SlotsSession"],
        cog: "SlotsCog",
    ) -> None:
        super().__init__(timeout=30)
        self.session = session
        self.active = active
        self.cog = cog

    @ui.button(label="Red", style=discord.ButtonStyle.danger, emoji="\U0001f534", row=0)
    async def red_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        await self._gamble(interaction, "red")

    @ui.button(label="Black", style=discord.ButtonStyle.secondary, emoji="\u26ab", row=0)
    async def black_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        await self._gamble(interaction, "black")

    @ui.button(label="Collect", style=discord.ButtonStyle.success, emoji="\U0001f4b0", row=0)
    async def collect_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.session.user_id:
            await interaction.response.send_message("Not your machine!", ephemeral=True)
            return
        await self._collect(interaction)

    async def _gamble(self, interaction: discord.Interaction, pick: str) -> None:
        if interaction.user.id != self.session.user_id:
            await interaction.response.send_message("Not your machine!", ephemeral=True)
            return
        s = self.session
        card_name, card_color = random.choice(GAMBLE_CARDS)
        s.gamble_card = card_name
        s.gamble_color = card_color
        s.gamble_count += 1

        if pick == card_color:
            # Win — double
            old_amount = s.gamble_amount
            s.gamble_amount *= 2
            gained = s.gamble_amount - old_amount
            await queries.update_casino_balance(str(s.user_id), gained)
            s.total_won += gained
            bal = await queries.get_casino_balance(str(s.user_id)) or 0

            if s.gamble_count >= MAX_GAMBLE:
                # Max gambles — auto-collect
                s.last_win = s.gamble_amount
                s.phase = "result"
                main_view = SlotsMainView(s, self.active, self.cog)
                main_view._update_buttons()
                embed = discord.Embed(
                    title="\U0001f3b2 Max Gamble Reached!",
                    description=(
                        f"Card: `{card_name}`\n\n"
                        f"\u2705 **Correct!** Collected **{s.gamble_amount}c**!"
                    ),
                    colour=discord.Colour.green(),
                )
                embed.add_field(name="", value=f"Balance: **{bal}c**", inline=False)
                await interaction.response.edit_message(embed=embed, view=main_view)
            else:
                # Can gamble again
                await interaction.response.edit_message(
                    embed=_gamble_embed(s, bal, result="win"), view=self,
                )
        else:
            # Lose — take back the gamble amount (it was already credited as a win)
            await queries.update_casino_balance(str(s.user_id), -s.gamble_amount)
            s.total_won -= s.gamble_amount
            s.last_win = 0
            lost = s.gamble_amount
            s.gamble_amount = 0
            s.phase = "result"
            bal = await queries.get_casino_balance(str(s.user_id)) or 0
            main_view = SlotsMainView(s, self.active, self.cog)
            main_view._update_buttons()
            embed = _gamble_embed(s, bal, result="lose")
            # Override the amount display in the embed
            embed.description = (
                f"Card: `{card_name}`\n\n"
                f"\u274c **Wrong!** You lost **{lost}c**!"
            )
            await interaction.response.edit_message(embed=embed, view=main_view)

    async def _collect(self, interaction: discord.Interaction) -> None:
        s = self.session
        s.last_win = s.gamble_amount
        s.phase = "result"
        bal = await queries.get_casino_balance(str(s.user_id)) or 0
        main_view = SlotsMainView(s, self.active, self.cog)
        main_view._update_buttons()
        await interaction.response.edit_message(
            embed=_result_embed(s, bal), view=main_view,
        )

    async def on_timeout(self) -> None:
        # Auto-collect on timeout
        s = self.session
        s.last_win = s.gamble_amount
        s.phase = "result"
        main_view = SlotsMainView(s, self.active, self.cog)
        main_view._update_buttons()
        if s.message:
            try:
                bal = await queries.get_casino_balance(str(s.user_id)) or 0
                await s.message.edit(
                    embed=_result_embed(s, bal), view=main_view,
                )
            except Exception:
                pass


# ── Cog ──────────────────────────────────────────────────────────────────────


class SlotsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_sessions: dict[int, SlotsSession] = {}

    @app_commands.command(
        name="slots",
        description="Play Fortune Reels \u2014 interactive slots with free spins & bonus rounds!",
    )
    async def slots(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_sessions:
            await interaction.response.send_message(
                "There's already a slot machine running in this channel!",
                ephemeral=True,
            )
            return

        uid = str(interaction.user.id)
        bal = await queries.get_or_create_casino_wallet(uid)

        session = SlotsSession(
            user_id=interaction.user.id,
            display_name=interaction.user.display_name,
            channel_id=channel_id,
        )
        self.active_sessions[channel_id] = session

        view = SlotsMainView(session, self.active_sessions, self)
        embed = _idle_embed(session, bal)
        await interaction.response.send_message(embed=embed, view=view)
        session.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SlotsCog(bot))
