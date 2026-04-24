"""Party game cog — /stockguess.

Players guess a stock's YTD percentage change across multiple rounds.
Closest guess each round wins that round.
"""

import asyncio
import random
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.cogs._elo_helpers import update_elo_multiplayer

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

MEDALS = ["\U0001f947", "\U0001f948", "\U0001f949"]  # gold, silver, bronze

# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class StockGuessPlayer:
    user_id: int
    display_name: str


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


def compute_round_winner(
    rankings: list[tuple[int, float, float]],
) -> int | None:
    """Return the user_id of the closest guesser, or None if nobody guessed."""
    for uid, _guess, error in rankings:
        if error != float("inf"):
            return uid
    return None


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

    embed = discord.Embed(
        title="\U0001f4c8 Stock Guess",
        description=(
            f"**{table.total_rounds} mystery stocks** \u2014 guess each one's YTD change!\n"
            "Closest guess each round wins.\n"
            "Stocks are revealed when guessing begins."
        ),
        colour=0xF1C40F,
    )

    embed.add_field(name="Rounds", value=str(table.total_rounds), inline=True)
    embed.add_field(name="Players", value=f"{n}/{MAX_PLAYERS}", inline=True)

    if table.players:
        lines = [
            f"\U0001f4c8 **{p.display_name}**"
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
    winner_uid: int | None,
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
        medal = MEDALS[i] if i < len(MEDALS) and error != float("inf") else "\u25aa\ufe0f"
        is_winner = uid == winner_uid and error != float("inf")

        if error == float("inf"):
            lines.append(f"{medal} **{p.display_name}** \u2014 no guess")
        else:
            g_sign = "+" if guess >= 0 else ""
            winner_tag = " \U0001f947" if is_winner else ""
            lines.append(
                f"{medal} **{p.display_name}** \u2014 "
                f"guessed {g_sign}{guess:.1f}% (off by {error:.1f}%){winner_tag}"
            )

    embed.add_field(name="Round Results", value="\n".join(lines), inline=False)
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


def _final_embed(table: StockGuessTable) -> discord.Embed:
    embed = discord.Embed(
        title="\U0001f4c8 Stock Guess \u2014 Final Results",
        colour=0xF1C40F,
    )

    # Sort by total rounds won descending
    sorted_players = sorted(
        table.players.items(),
        key=lambda kv: table.round_scores.get(kv[0], 0),
        reverse=True,
    )

    lines: list[str] = []
    for i, (uid, p) in enumerate(sorted_players):
        medal = MEDALS[i] if i < len(MEDALS) else "\u25aa\ufe0f"
        rounds_won = table.round_scores.get(uid, 0)
        lines.append(
            f"{medal} **{p.display_name}** \u2014 {rounds_won} round(s) won"
        )

    embed.add_field(
        name=f"Final Leaderboard ({table.total_rounds} rounds)",
        value="\n".join(lines),
        inline=False,
    )
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


# ── Modals ───────────────────────────────────────────────────────────────────


    # JoinStockGuessModal removed — Join button now directly adds player (no coins needed)


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
        self.active_tables.pop(self.table.channel_id, None)

    @ui.button(label="Join", style=discord.ButtonStyle.success, emoji="\U0001f4c8", row=0)
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
        self.table.players[uid] = StockGuessPlayer(
            user_id=uid,
            display_name=interaction.user.display_name,
        )
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
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
    """Reveal results for a single round and track the winner."""
    rankings = compute_rankings(table.players, table.guesses, table.ytd_pct)

    # If nobody guessed, no winner this round
    all_no_guess = bool(rankings) and all(r[2] == float("inf") for r in rankings)
    if all_no_guess:
        await interaction.followup.send(
            f"Round {table.round_num}: Nobody guessed!"
        )
        return

    winner_uid = compute_round_winner(rankings)
    if winner_uid is not None:
        table.round_scores[winner_uid] = table.round_scores.get(winner_uid, 0) + 1

    embed = _round_reveal_embed(table, rankings, winner_uid)
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
            pass

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
        rounds=f"Number of rounds (default {DEFAULT_ROUNDS}, max 10)",
    )
    async def stockguess(
        self,
        interaction: discord.Interaction,
        rounds: int = DEFAULT_ROUNDS,
    ) -> None:
        cid = interaction.channel_id
        if cid in self.active_tables:
            await interaction.response.send_message(
                "A Stock Guess game is already running in this channel!",
                ephemeral=True,
            )
            return

        if not 1 <= rounds <= 10:
            await interaction.response.send_message(
                "Rounds must be between 1 and 10.", ephemeral=True,
            )
            return

        uid = interaction.user.id

        table = StockGuessTable(
            channel_id=cid,
            host_id=uid,
            host_name=interaction.user.display_name,
            total_rounds=rounds,
        )
        table.players[uid] = StockGuessPlayer(
            user_id=uid,
            display_name=interaction.user.display_name,
        )

        self.active_tables[cid] = table
        view = BettingView(table, self.active_tables)
        await interaction.response.send_message(
            embed=_betting_embed(table), view=view,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StockGuessCog(bot))
