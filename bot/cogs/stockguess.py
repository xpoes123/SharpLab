"""Casino cog — /stockguess party game.

Players guess a stock's YTD percentage change across multiple rounds.
Closest guess each round wins that round's pot.
"""

import asyncio
import random
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.cogs._elo_helpers import update_elo_multiplayer
from db import queries
import logging
log = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

CURATED_TICKERS: dict[str, str] = {
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "TSLA": "Tesla",
    "AMZN": "Amazon",
    "GOOG": "Alphabet",
    "META": "Meta Platforms",
    "NVDA": "NVIDIA",
    "JPM": "JPMorgan Chase",
    "WMT": "Walmart",
    "DIS": "Walt Disney",
    "NFLX": "Netflix",
    "KO": "Coca-Cola",
    "MCD": "McDonald's",
    "NKE": "Nike",
    "BA": "Boeing",
    "F": "Ford",
    "GM": "General Motors",
    "SBUX": "Starbucks",
    "PFE": "Pfizer",
    "JNJ": "Johnson & Johnson",
    "V": "Visa",
    "MA": "Mastercard",
    "HD": "Home Depot",
    "COST": "Costco",
    "PEP": "PepsiCo",
    "INTC": "Intel",
    "AMD": "AMD",
    "CRM": "Salesforce",
    "PYPL": "PayPal",
    "UBER": "Uber",
    "ABNB": "Airbnb",
    "SQ": "Block (Square)",
    "SNAP": "Snap",
    "SPOT": "Spotify",
    "ROKU": "Roku",
    "ZM": "Zoom",
    "COIN": "Coinbase",
    "RIVN": "Rivian",
    "LMT": "Lockheed Martin",
    "GS": "Goldman Sachs",
    "XOM": "ExxonMobil",
    "CVX": "Chevron",
    "UNH": "UnitedHealth",
    "PG": "Procter & Gamble",
    "T": "AT&T",
    "VZ": "Verizon",
}

MAX_PLAYERS = 8
MIN_PLAYERS = 1
GUESS_WINDOW = 45       # seconds players have to submit guesses each round
DEFAULT_ROUNDS = 5      # rounds per game when not specified
ROUND_PAUSE = 5         # seconds between rounds
COUNTDOWN_INTERVAL = 10  # how often to refresh the countdown display (seconds)

PAYTABLE: dict[int, list[float]] = {
    1: [1.0],
    2: [1.0],
    3: [0.70, 0.30],
    4: [0.55, 0.30, 0.15],
    5: [0.45, 0.25, 0.18, 0.12],
    6: [0.40, 0.24, 0.16, 0.12, 0.08],
    7: [0.36, 0.22, 0.16, 0.12, 0.08, 0.06],
    8: [0.33, 0.21, 0.16, 0.12, 0.08, 0.06, 0.04],
}

MEDALS = ["\U0001f947", "\U0001f948", "\U0001f949"]  # gold, silver, bronze

# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class StockGuessPlayer:
    user_id: int
    display_name: str
    bet: int  # per-round bet


@dataclass
class StockGuessTable:
    channel_id: int
    host_id: int
    host_name: str
    total_rounds: int = DEFAULT_ROUNDS
    phase: str = "betting"  # betting | guessing | finished
    players: dict[int, StockGuessPlayer] = field(default_factory=dict)
    guesses: dict[int, float] = field(default_factory=dict)  # uid -> guess %
    round_num: int = 0  # 0 = not started, 1-N during game
    round_scores: dict[int, int] = field(default_factory=dict)  # cumulative payouts
    used_tickers: set[str] = field(default_factory=set)
    # Set at the start of each round
    ticker: str = ""
    company: str = ""
    ytd_pct: float = 0.0


# ── YTD fetch ────────────────────────────────────────────────────────────────


async def fetch_ytd_change(ticker: str) -> float:
    """Fetch the YTD percentage change for a ticker using yfinance.

    Returns the change as a percentage (e.g. 12.5 for +12.5%).
    yfinance is synchronous, so we run it in a thread executor.
    """
    import yfinance as yf
    def _fetch() -> float:
        tk = yf.Ticker(ticker)
        hist = tk.history(period="ytd")
        if hist.empty or len(hist) < 2:
            raise ValueError(f"No YTD data available for {ticker}")
        start_price = hist["Close"].iloc[0]
        current_price = hist["Close"].iloc[-1]
        return (current_price - start_price) / start_price * 100

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch)


# ── Guess parsing ────────────────────────────────────────────────────────────


def parse_guess(raw: str) -> float:
    """Parse a guess string like '+12.5', '-8.3', '12.5%' into a float."""
    cleaned = raw.strip().replace("%", "").replace(" ", "")
    return float(cleaned)


# ── Ranking / payout logic ───────────────────────────────────────────────────


def compute_rankings(
    players: dict[int, StockGuessPlayer],
    guesses: dict[int, float],
    actual: float,
) -> list[tuple[int, float, float]]:
    """Return sorted list of (user_id, guess, error) by accuracy.

    Players who didn't guess are appended at the end with error=inf.
    """
    ranked: list[tuple[int, float, float]] = []
    no_guess: list[int] = []

    for uid in players:
        if uid in guesses:
            guess = guesses[uid]
            error = abs(guess - actual)
            ranked.append((uid, guess, error))
        else:
            no_guess.append(uid)

    ranked.sort(key=lambda t: t[2])

    for uid in no_guess:
        ranked.append((uid, float("nan"), float("inf")))

    return ranked


def compute_payouts(
    rankings: list[tuple[int, float, float]],
    players: dict[int, StockGuessPlayer],
) -> dict[int, int]:
    """Distribute the prize pool based on rankings using the paytable."""
    n = len(players)
    prize_pool = sum(p.bet for p in players.values())
    pct_table = PAYTABLE.get(n, PAYTABLE[8])
    payouts: dict[int, int] = {uid: 0 for uid in players}

    for i, (uid, _guess, _error) in enumerate(rankings):
        if i < len(pct_table) and _error != float("inf"):
            payouts[uid] = int(prize_pool * pct_table[i])

    # Leftover from rounding goes to first place
    total_paid = sum(payouts.values())
    leftover = prize_pool - total_paid
    if leftover > 0 and rankings:
        first_uid = rankings[0][0]
        if rankings[0][2] != float("inf"):
            payouts[first_uid] += leftover

    return payouts


# ── Stock picker ─────────────────────────────────────────────────────────────


async def _pick_next_stock(table: StockGuessTable) -> tuple[str, str, float]:
    """Pick a random unused ticker for the next round and fetch its YTD change.

    Resets the used set if all tickers have been used.
    """
    available = [t for t in CURATED_TICKERS if t not in table.used_tickers]
    if not available:
        table.used_tickers.clear()
        available = list(CURATED_TICKERS.keys())
    ticker = random.choice(available)
    table.used_tickers.add(ticker)
    company = CURATED_TICKERS[ticker]
    ytd_pct = await fetch_ytd_change(ticker)
    return ticker, company, ytd_pct


# ── Embeds ───────────────────────────────────────────────────────────────────


def _betting_embed(table: StockGuessTable) -> discord.Embed:
    n = len(table.players)
    # Total pot = sum of (per-round bets × rounds) for all joined players
    total_pot = sum(p.bet * table.total_rounds for p in table.players.values())

    embed = discord.Embed(
        title="\U0001f4c8 Stock Guess",
        description=(
            f"**{table.total_rounds} mystery stocks** \u2014 guess each one's YTD change!\n"
            "Closest guess each round wins the pot.\n"
            "Stocks are revealed when guessing begins."
        ),
        colour=0xF1C40F,
    )

    embed.add_field(name="Rounds", value=str(table.total_rounds), inline=True)
    if total_pot:
        embed.add_field(name="Total Pot", value=f"{total_pot}c", inline=True)
    embed.add_field(name="Players", value=f"{n}/{MAX_PLAYERS}", inline=True)

    if n >= MIN_PLAYERS:
        pt = PAYTABLE.get(n, PAYTABLE[8])
        pt_parts = [
            f"{MEDALS[i] if i < 3 else chr(0x25aa) + chr(0xfe0f)} {int(s * 100)}%"
            for i, s in enumerate(pt)
        ]
        embed.add_field(name="Paytable (per round)", value=" | ".join(pt_parts), inline=False)

    if table.players:
        lines = [
            f"\U0001f4b0 **{p.display_name}** \u2014 {p.bet}c/round ({p.bet * table.total_rounds}c total)"
            for p in table.players.values()
        ]
        embed.add_field(name="Joined", value="\n".join(lines), inline=False)
    else:
        embed.add_field(
            name="Joined",
            value="*No players yet \u2014 click Join!*",
            inline=False,
        )

    embed.set_footer(text=f"Host: {table.host_name} \u2502 Min {MIN_PLAYERS} players")
    return embed


def _guessing_embed(table: StockGuessTable, remaining: int | None = None) -> discord.Embed:
    secs = remaining if remaining is not None else GUESS_WINDOW

    embed = discord.Embed(
        title=(
            f"\U0001f4c8 Stock Guess \u2014 Round {table.round_num}/{table.total_rounds}"
        ),
        description=(
            f"**How much has {table.company} changed YTD?**\n\n"
            "Click **Submit Guess** and enter your prediction as a percentage.\n"
            "Example: `+12.5` or `-8.3`"
        ),
        colour=0x3498DB,
    )

    embed.add_field(name="\u23f1\ufe0f Time", value=f"**{secs}s**", inline=True)
    pot = sum(p.bet for p in table.players.values())
    embed.add_field(name="Round Pot", value=f"{pot}c", inline=True)

    guessed = [
        p.display_name for uid, p in table.players.items()
        if uid in table.guesses
    ]
    waiting = [
        p.display_name for uid, p in table.players.items()
        if uid not in table.guesses
    ]

    status_lines: list[str] = []
    for name in guessed:
        status_lines.append(f"\u2705 **{name}** \u2014 locked in")
    for name in waiting:
        status_lines.append(f"\u23f3 **{name}** \u2014 waiting\u2026")
    embed.add_field(name="Status", value="\n".join(status_lines), inline=False)

    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


def _round_reveal_embed(
    table: StockGuessTable,
    rankings: list[tuple[int, float, float]],
    payouts: dict[int, int],
    balances: dict[int, int],
) -> discord.Embed:
    actual = table.ytd_pct
    sign = "+" if actual >= 0 else ""
    colour = 0x2ECC71 if actual >= 0 else 0xE74C3C

    embed = discord.Embed(
        title=f"\U0001f4c8 Round {table.round_num}/{table.total_rounds} \u2014 Results",
        colour=colour,
    )

    embed.description = (
        f"**{table.company}** (`{table.ticker}`)\n"
        f"YTD Change: **{sign}{actual:.1f}%**"
    )

    lines: list[str] = []
    for i, (uid, guess, error) in enumerate(rankings):
        p = table.players[uid]
        payout = payouts.get(uid, 0)
        bal = balances.get(uid, 0)
        net = payout - p.bet
        net_sign = "+" if net >= 0 else ""
        medal = MEDALS[i] if i < len(MEDALS) and error != float("inf") else "\u25aa\ufe0f"

        if error == float("inf"):
            lines.append(
                f"{medal} **{p.display_name}** \u2014 no guess \u2014 "
                f"0c (**{net_sign}{net}c**) \u2014 bal: {bal}c"
            )
        else:
            g_sign = "+" if guess >= 0 else ""
            lines.append(
                f"{medal} **{p.display_name}** \u2014 "
                f"guessed {g_sign}{guess:.1f}% (off by {error:.1f}%) \u2014 "
                f"{payout}c (**{net_sign}{net}c**) \u2014 bal: {bal}c"
            )

    embed.add_field(name="Round Results", value="\n".join(lines), inline=False)
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


def _final_embed(table: StockGuessTable) -> discord.Embed:
    embed = discord.Embed(
        title="\U0001f4c8 Stock Guess \u2014 Final Results",
        colour=0xF1C40F,
    )

    total_paid_per_player = {
        uid: p.bet * table.total_rounds for uid, p in table.players.items()
    }

    # Sort by total winnings descending
    sorted_players = sorted(
        table.players.items(),
        key=lambda kv: table.round_scores.get(kv[0], 0),
        reverse=True,
    )

    lines: list[str] = []
    for i, (uid, p) in enumerate(sorted_players):
        medal = MEDALS[i] if i < len(MEDALS) else "\u25aa\ufe0f"
        total_won = table.round_scores.get(uid, 0)
        paid = total_paid_per_player[uid]
        net = total_won - paid
        net_sign = "+" if net >= 0 else ""
        lines.append(
            f"{medal} **{p.display_name}** \u2014 "
            f"paid {paid}c \u2192 won {total_won}c (**{net_sign}{net}c**)"
        )

    embed.add_field(
        name=f"Final Leaderboard ({table.total_rounds} rounds)",
        value="\n".join(lines),
        inline=False,
    )
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


# ── Modals ───────────────────────────────────────────────────────────────────


class JoinStockGuessModal(ui.Modal):
    amount = ui.TextInput(
        label="Bet amount per round (coins)",
        placeholder="e.g. 10",
        required=True,
        max_length=10,
    )

    def __init__(
        self, table: StockGuessTable, view: "BettingView", balance: int,
    ) -> None:
        super().__init__(title="Join Stock Guess")
        self.table = table
        self.betting_view = view
        total_needed = self.table.total_rounds
        self.amount.placeholder = (
            f"e.g. 10/round \u2192 {10 * total_needed}c total (bal: {balance}c)"
        )

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
                "Must be at least 1 coin per round.", ephemeral=True,
            )
            return
        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message(
                "You're already in this game!", ephemeral=True,
            )
            return

        total_cost = amt * self.table.total_rounds
        try:
            await queries.update_casino_balance(str(uid), -total_cost)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins! Need {total_cost}c for {self.table.total_rounds} rounds "
                f"at {amt}c/round (have {bal}c)",
                ephemeral=True,
            )
            return

        self.table.players[uid] = StockGuessPlayer(
            user_id=uid,
            display_name=interaction.user.display_name,
            bet=amt,
        )

        self.betting_view._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self.betting_view,
        )


class GuessModal(ui.Modal):
    guess_input = ui.TextInput(
        label="Your YTD % guess",
        placeholder="e.g. +12.5 or -8.3",
        required=True,
        max_length=10,
    )

    def __init__(
        self, table: StockGuessTable, guess_view: "GuessView",
    ) -> None:
        super().__init__(title="Your Guess")
        self.table = table
        self.guess_view = guess_view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        uid = interaction.user.id
        if uid not in self.table.players:
            await interaction.response.send_message(
                "You're not in this game!", ephemeral=True,
            )
            return
        if uid in self.table.guesses:
            await interaction.response.send_message(
                "You've already submitted a guess!", ephemeral=True,
            )
            return

        try:
            guess = parse_guess(self.guess_input.value)
        except ValueError:
            await interaction.response.send_message(
                "Invalid guess. Enter a number like `+12.5` or `-8.3`.",
                ephemeral=True,
            )
            return

        self.table.guesses[uid] = guess

        await interaction.response.edit_message(
            embed=_guessing_embed(self.table), view=self.guess_view,
        )

        # If all players have guessed, trigger reveal early
        if len(self.table.guesses) >= len(self.table.players):
            self.guess_view.stop()


# ── Views ────────────────────────────────────────────────────────────────────


class BettingView(ui.View):
    def __init__(
        self, table: StockGuessTable, active_tables: dict[int, StockGuessTable],
    ) -> None:
        super().__init__(timeout=120)
        self.table = table
        self.active_tables = active_tables
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.start_btn.disabled = len(self.table.players) < MIN_PLAYERS
        self.join_btn.disabled = len(self.table.players) >= MAX_PLAYERS

    async def on_timeout(self) -> None:
        # Refund total cost (per-round bet × total rounds) for each player
        for p in self.table.players.values():
            await queries.update_casino_balance(
                str(p.user_id), p.bet * self.table.total_rounds
            )
        self.active_tables.pop(self.table.channel_id, None)

    @ui.button(label="Join", style=discord.ButtonStyle.success, emoji="\U0001f4b0", row=0)
    async def join_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
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
            JoinStockGuessModal(self.table, self, bal),
        )

    @ui.button(label="Start", style=discord.ButtonStyle.primary, emoji="\u25b6\ufe0f", row=0)
    async def start_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can start!", ephemeral=True,
            )
            return
        if len(self.table.players) < MIN_PLAYERS:
            await interaction.response.send_message(
                f"Need at least {MIN_PLAYERS} player(s)!", ephemeral=True,
            )
            return

        self.table.phase = "guessing"
        self.stop()

        # Defer immediately — fetching stock data takes time
        await interaction.response.defer()
        await _run_game(interaction, self.table, self.active_tables)


class GuessView(ui.View):
    def __init__(
        self, table: StockGuessTable, active_tables: dict[int, StockGuessTable],
    ) -> None:
        super().__init__(timeout=GUESS_WINDOW)
        self.table = table
        self.active_tables = active_tables
        self.message: discord.Message | None = None
        self._countdown_task: asyncio.Task | None = None  # type: ignore[type-arg]

    def stop(self) -> None:
        if self._countdown_task is not None and not self._countdown_task.done():
            self._countdown_task.cancel()
        super().stop()

    async def on_timeout(self) -> None:
        if self._countdown_task is not None and not self._countdown_task.done():
            self._countdown_task.cancel()

    async def start_countdown(self) -> None:
        """Update the timer display every COUNTDOWN_INTERVAL seconds."""
        elapsed = 0
        while elapsed < GUESS_WINDOW:
            await asyncio.sleep(COUNTDOWN_INTERVAL)
            elapsed += COUNTDOWN_INTERVAL
            if self.is_finished():
                break
            remaining = max(0, GUESS_WINDOW - elapsed)
            if self.message is not None:
                try:
                    await self.message.edit(
                        embed=_guessing_embed(self.table, remaining)
                    )
                except (discord.NotFound, discord.HTTPException):
                    break

    @ui.button(label="Submit Guess", style=discord.ButtonStyle.success, emoji="\U0001f4dd", row=0)
    async def guess_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        uid = interaction.user.id
        if uid not in self.table.players:
            await interaction.response.send_message(
                "You're not in this game!", ephemeral=True,
            )
            return
        if uid in self.table.guesses:
            await interaction.response.send_message(
                "You've already guessed!", ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            GuessModal(self.table, self),
        )


# ── Game loop ────────────────────────────────────────────────────────────────


async def _run_game(
    interaction: discord.Interaction,
    table: StockGuessTable,
    active_tables: dict[int, StockGuessTable],
) -> None:
    """Run all rounds of a multi-round stock guess game.

    Called after the betting view closes. Uses interaction.followup for all
    messages since the original response was deferred in start_btn.
    """
    for round_num in range(1, table.total_rounds + 1):
        table.round_num = round_num
        table.guesses.clear()
        table.phase = "guessing"

        # Fetch this round's stock (hidden during betting, revealed here)
        try:
            ticker, company, ytd_pct = await _pick_next_stock(table)
        except Exception:
            await interaction.followup.send(
                f"\u26a0\ufe0f Failed to fetch stock data for round {round_num}. Skipping."
            )
            continue

        table.ticker = ticker
        table.company = company
        table.ytd_pct = ytd_pct

        guess_view = GuessView(table, active_tables)

        if round_num == 1:
            # Edit the original (deferred betting) message into the round 1 guessing embed
            await interaction.edit_original_response(
                embed=_guessing_embed(table, GUESS_WINDOW), view=guess_view
            )
            msg = await interaction.original_response()
        else:
            msg = await interaction.followup.send(
                embed=_guessing_embed(table, GUESS_WINDOW), view=guess_view
            )

        guess_view.message = msg
        guess_view._countdown_task = asyncio.create_task(guess_view.start_countdown())

        await guess_view.wait()
        await _do_round_reveal(interaction, table)

        if round_num < table.total_rounds:
            await asyncio.sleep(ROUND_PAUSE)

    await _do_final_summary(interaction, table, active_tables)


# ── Round / game reveal helpers ──────────────────────────────────────────────


async def _do_round_reveal(
    interaction: discord.Interaction,
    table: StockGuessTable,
) -> None:
    """Reveal results for a single round and award per-round payouts."""
    rankings = compute_rankings(table.players, table.guesses, table.ytd_pct)

    # If nobody guessed, refund this round's bets
    all_no_guess = bool(rankings) and all(r[2] == float("inf") for r in rankings)
    if all_no_guess:
        for uid, p in table.players.items():
            await queries.update_casino_balance(str(uid), p.bet)
            await queries.log_casino_result(str(uid), "stockguess", p.bet, p.bet)
            table.round_scores[uid] = table.round_scores.get(uid, 0) + p.bet
        await interaction.followup.send(
            f"Round {table.round_num}: Nobody guessed \u2014 all bets refunded for this round!"
        )
        return

    payouts = compute_payouts(rankings, table.players)

    balances: dict[int, int] = {}
    for uid, p in table.players.items():
        payout = payouts.get(uid, 0)
        if payout > 0:
            await queries.update_casino_balance(str(uid), payout)
        await queries.log_casino_result(str(uid), "stockguess", p.bet, payout)
        table.round_scores[uid] = table.round_scores.get(uid, 0) + payout
        balances[uid] = await queries.get_or_create_casino_wallet(str(uid))

    embed = _round_reveal_embed(table, rankings, payouts, balances)
    await interaction.followup.send(embed=embed)


async def _do_final_summary(
    interaction: discord.Interaction,
    table: StockGuessTable,
    active_tables: dict[int, StockGuessTable],
) -> None:
    """Post the final leaderboard after all rounds complete."""
    table.phase = "finished"

    if len(table.players) >= 2:
        sorted_p = sorted(
            table.players.items(),
            key=lambda kv: table.round_scores.get(kv[0], 0),
            reverse=True,
        )
        finish_order = [uid for uid, _ in sorted_p]
        try:
            await update_elo_multiplayer(finish_order, "stockguess", "stockguess")
        except Exception:
            log.exception("Unhandled error in stockguess.py")

    active_tables.pop(table.channel_id, None)
    await interaction.followup.send(embed=_final_embed(table))


# ── Cog ──────────────────────────────────────────────────────────────────────


class StockGuessCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, StockGuessTable] = {}

    @app_commands.command(
        name="stockguess",
        description="Guess stocks' YTD performance across multiple rounds \u2014 closest wins!",
    )
    @app_commands.describe(
        bet="Your bet amount in coins per round (default 10)",
        rounds=f"Number of rounds (default {DEFAULT_ROUNDS}, max 10)",
    )
    async def stockguess(
        self,
        interaction: discord.Interaction,
        bet: int = 10,
        rounds: int = DEFAULT_ROUNDS,
    ) -> None:
        cid = interaction.channel_id
        if cid in self.active_tables:
            await interaction.response.send_message(
                "A Stock Guess game is already running in this channel!",
                ephemeral=True,
            )
            return

        if bet < 1:
            await interaction.response.send_message(
                "Bet must be at least 1 coin per round.", ephemeral=True,
            )
            return

        if not 1 <= rounds <= 10:
            await interaction.response.send_message(
                "Rounds must be between 1 and 10.", ephemeral=True,
            )
            return

        uid = interaction.user.id
        total_cost = bet * rounds

        # Deduct total cost upfront (per-round bet × number of rounds)
        try:
            await queries.update_casino_balance(str(uid), -total_cost)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins! Need {total_cost}c for {rounds} rounds at {bet}c/round "
                f"(have {bal}c)",
                ephemeral=True,
            )
            return

        table = StockGuessTable(
            channel_id=cid,
            host_id=uid,
            host_name=interaction.user.display_name,
            total_rounds=rounds,
        )
        table.players[uid] = StockGuessPlayer(
            user_id=uid,
            display_name=interaction.user.display_name,
            bet=bet,
        )

        self.active_tables[cid] = table
        view = BettingView(table, self.active_tables)
        await interaction.response.send_message(
            embed=_betting_embed(table), view=view,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StockGuessCog(bot))
