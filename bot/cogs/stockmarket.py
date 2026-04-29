"""Casino cog — multiplayer /stockmarket party game."""

import asyncio
import random
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries
from bot.cogs._pool import compute_side_pot_payouts
import logging

log = logging.getLogger(__name__)
# ── Constants ────────────────────────────────────────────────────────────────

MAX_PLAYERS = 8
MIN_PLAYERS = 1
NUM_ROUNDS = 5
STARTING_CASH = 1000
TRADE_WINDOW = 30  # seconds
MIN_STOCK_PRICE = 10

# ── Stocks ───────────────────────────────────────────────────────────────────

STOCKS: list[tuple[str, str, str]] = [
    ("TECH", "\U0001f4bb", "TechCorp"),
    ("ENRG", "\u26a1", "EnergyX"),
    ("FOOD", "\U0001f354", "FoodCo"),
    ("BANK", "\U0001f3e6", "BankFirst"),
    ("PHMA", "\U0001f48a", "PharmaCo"),
    ("MEME", "\U0001f680", "MemeStonk"),
]

VALID_TICKERS = {t for t, _, _ in STOCKS}


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class Stock:
    ticker: str
    emoji: str
    name: str
    price: int = 100
    history: list[int] = field(default_factory=lambda: [100])


@dataclass
class EventCard:
    name: str
    emoji: str
    description: str
    effects: dict[str, float]  # ticker -> percentage change (e.g. 0.30 = +30%)


@dataclass
class StockPlayer:
    user_id: int
    display_name: str
    bet: int  # buy-in amount (goes to pot)
    cash: int = STARTING_CASH  # in-game cash
    holdings: dict[str, int] = field(default_factory=dict)  # ticker -> shares

    def portfolio_value(self, stocks: dict[str, Stock]) -> int:
        """Total value = cash + sum(shares * price)."""
        total = self.cash
        for ticker, shares in self.holdings.items():
            if ticker in stocks:
                total += shares * stocks[ticker].price
        return total


@dataclass
class StockTable:
    channel_id: int
    host_id: int
    host_name: str
    phase: str = "betting"  # betting | playing | finished
    players: dict[int, StockPlayer] = field(default_factory=dict)
    message: discord.Message | None = None
    round_num: int = 0  # 0 = pre-game, 1-5 during play
    stocks: dict[str, Stock] = field(default_factory=dict)
    current_event: EventCard | None = None
    event_deck: list[EventCard] = field(default_factory=list)
    trade_task: asyncio.Task | None = field(default=None, repr=False)
    winners: list[int] = field(default_factory=list)
    last_bets: dict[int, tuple[str, int]] = field(default_factory=dict)
    trade_locked: bool = False
    game_num: int = 1  # tracks table rounds (New Game increments this)


# ── Event Cards ──────────────────────────────────────────────────────────────

EVENT_CARDS: list[EventCard] = [
    EventCard(
        "\U0001f4bb Tech Boom!", "\U0001f4bb",
        "Silicon Valley is buzzing \u2014 tech stocks surge!",
        {"TECH": 0.30, "MEME": 0.20, "BANK": -0.10},
    ),
    EventCard(
        "\u26a1 Energy Crisis!", "\u26a1",
        "Supply drops as energy prices skyrocket.",
        {"ENRG": 0.40, "FOOD": -0.15, "TECH": -0.10},
    ),
    EventCard(
        "\U0001f4c9 Market Crash!", "\U0001f4c9",
        "Panic selling across all sectors!",
        {"TECH": -0.25, "ENRG": -0.20, "FOOD": -0.20, "BANK": -0.30, "PHMA": -0.20, "MEME": -0.30},
    ),
    EventCard(
        "\U0001f4c8 Bull Run!", "\U0001f4c8",
        "Investor confidence is sky-high. Everything goes up!",
        {"TECH": 0.20, "ENRG": 0.15, "FOOD": 0.15, "BANK": 0.25, "PHMA": 0.20, "MEME": 0.20},
    ),
    EventCard(
        "\U0001f354 Food Shortage!", "\U0001f354",
        "Crop failures drive food stocks through the roof.",
        {"FOOD": 0.35, "PHMA": 0.10, "ENRG": 0.05},
    ),
    EventCard(
        "\U0001f3e6 Interest Rate Hike!", "\U0001f3e6",
        "The Fed raises rates \u2014 banks love it, growth stocks hate it.",
        {"BANK": 0.25, "TECH": -0.20, "MEME": -0.30},
    ),
    EventCard(
        "\U0001f680 Meme Mania!", "\U0001f680",
        "Reddit goes wild. Meme stocks to the moon!",
        {"MEME": 0.50, "BANK": -0.15, "PHMA": -0.05},
    ),
    EventCard(
        "\U0001f48a Drug Approval!", "\U0001f48a",
        "FDA fast-tracks a blockbuster drug.",
        {"PHMA": 0.40, "FOOD": -0.10, "MEME": 0.10},
    ),
    EventCard(
        "\U0001f525 Corporate Scandal!", "\U0001f525",
        "A major CEO is caught in a fraud scandal.",
        {"BANK": -0.35, "TECH": -0.15, "MEME": 0.15},
    ),
    EventCard(
        "\U0001f4b0 Stimulus Package!", "\U0001f4b0",
        "Government pumps money into the economy.",
        {"TECH": 0.15, "ENRG": 0.10, "FOOD": 0.15, "BANK": 0.10, "PHMA": 0.15, "MEME": 0.20},
    ),
    EventCard(
        "\U0001f30d Global Expansion!", "\U0001f30d",
        "Trade deals open new international markets.",
        {"TECH": 0.15, "FOOD": 0.20, "ENRG": 0.20, "BANK": 0.10},
    ),
    EventCard(
        "\U0001f916 AI Revolution!", "\U0001f916",
        "Artificial intelligence changes everything overnight.",
        {"TECH": 0.35, "BANK": 0.10, "PHMA": 0.15, "FOOD": -0.10, "ENRG": -0.10},
    ),
    EventCard(
        "\U0001f3ed Supply Chain Collapse!", "\U0001f3ed",
        "Global shipping grinds to a halt.",
        {"FOOD": -0.25, "TECH": -0.20, "ENRG": 0.25, "PHMA": -0.15},
    ),
    EventCard(
        "\U0001f489 Pandemic Scare!", "\U0001f489",
        "A new virus variant spooks the markets.",
        {"PHMA": 0.35, "FOOD": 0.15, "TECH": 0.10, "BANK": -0.20, "MEME": -0.25, "ENRG": -0.15},
    ),
    EventCard(
        "\u2600\ufe0f Green Energy Boom!", "\u2600\ufe0f",
        "Renewable energy subsidies send ENRG soaring.",
        {"ENRG": 0.35, "TECH": 0.10, "BANK": -0.05, "MEME": 0.05},
    ),
    EventCard(
        "\U0001f4b8 Crypto Crash!", "\U0001f4b8",
        "Crypto contagion spills into traditional markets.",
        {"MEME": -0.40, "TECH": -0.15, "BANK": -0.10, "ENRG": 0.05},
    ),
    EventCard(
        "\U0001f3e0 Housing Boom!", "\U0001f3e0",
        "Real estate goes crazy. Banks issue record mortgages.",
        {"BANK": 0.30, "FOOD": 0.10, "TECH": -0.05, "ENRG": 0.05},
    ),
    EventCard(
        "\U0001f4f0 Earnings Season!", "\U0001f4f0",
        "Mixed earnings reports \u2014 some winners, some losers.",
        {"TECH": 0.15, "FOOD": -0.10, "PHMA": 0.20, "MEME": -0.15, "BANK": 0.05},
    ),
    EventCard(
        "\u26d4 Regulatory Crackdown!", "\u26d4",
        "New regulations hit big corporations hard.",
        {"TECH": -0.25, "MEME": -0.20, "PHMA": -0.15, "BANK": 0.15, "FOOD": 0.10},
    ),
    EventCard(
        "\U0001f389 IPO Frenzy!", "\U0001f389",
        "A wave of hot IPOs drives speculative buying.",
        {"MEME": 0.35, "TECH": 0.20, "BANK": 0.05, "PHMA": -0.10},
    ),
    EventCard(
        "\U0001f30b Natural Disaster!", "\U0001f30b",
        "A major earthquake disrupts supply chains.",
        {"FOOD": -0.20, "ENRG": 0.30, "TECH": -0.15, "PHMA": 0.10, "BANK": -0.10},
    ),
    EventCard(
        "\U0001f4ca Insider Trading Bust!", "\U0001f4ca",
        "The SEC catches a ring of insider traders.",
        {"BANK": -0.20, "MEME": -0.25, "PHMA": 0.15, "FOOD": 0.05},
    ),
    EventCard(
        "\U0001f30a Oil Spill!", "\U0001f30a",
        "A tanker spill tanks energy stocks but boosts pharma.",
        {"ENRG": -0.35, "PHMA": 0.20, "FOOD": -0.10, "TECH": 0.05},
    ),
    EventCard(
        "\U0001f4e1 5G Rollout!", "\U0001f4e1",
        "Nationwide 5G deployment supercharges tech.",
        {"TECH": 0.25, "MEME": 0.15, "ENRG": 0.10, "BANK": -0.05},
    ),
]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _init_stocks() -> dict[str, Stock]:
    """Create a fresh set of stocks at starting prices."""
    return {
        ticker: Stock(ticker=ticker, emoji=emoji, name=name)
        for ticker, emoji, name in STOCKS
    }


def _apply_event(stocks: dict[str, Stock], event: EventCard) -> dict[str, int]:
    """Apply event effects to stock prices. Returns dict of ticker -> change amount."""
    changes: dict[str, int] = {}
    for ticker, stock in stocks.items():
        pct = event.effects.get(ticker, 0.0)
        if pct == 0.0:
            changes[ticker] = 0
            stock.history.append(stock.price)
            continue
        delta = round(stock.price * pct)
        new_price = max(MIN_STOCK_PRICE, stock.price + delta)
        actual_change = new_price - stock.price
        changes[ticker] = actual_change
        stock.price = new_price
        stock.history.append(new_price)
    return changes


def _pct_str(change: int, old_price: int) -> str:
    """Format a price change as a percentage string with arrow."""
    if old_price == 0:
        return "\u27a1\ufe0f 0%"
    pct = round(change / old_price * 100)
    if pct > 0:
        return f"\U0001f4c8 +{pct}%"
    elif pct < 0:
        return f"\U0001f4c9 {pct}%"
    return "\u27a1\ufe0f 0%"


def _spark(history: list[int]) -> str:
    """Tiny text sparkline from price history."""
    if len(history) < 2:
        return ""
    chars = ["\u2581", "\u2582", "\u2583", "\u2584", "\u2585", "\u2586", "\u2587", "\u2588"]
    lo = min(history)
    hi = max(history)
    rng = hi - lo
    if rng == 0:
        return chars[3] * len(history)
    return "".join(
        chars[min(7, int((v - lo) / rng * 7))] for v in history
    )


# ── Embeds ───────────────────────────────────────────────────────────────────


def _betting_embed(table: StockTable) -> discord.Embed:
    pot = sum(p.bet for p in table.players.values())
    embed = discord.Embed(
        title=f"\U0001f4ca Stock Market \u2014 Join the Table (Game {table.game_num})",
        description=(
            "Invest in fictional stocks over 5 rounds. "
            "Buy low, sell high \u2014 highest portfolio value wins the pot!\n"
            f"Starting cash: **{STARTING_CASH}c** (in-game). Buy-in goes to the pot."
        ),
        colour=discord.Colour.blurple(),
    )
    if pot:
        embed.add_field(name="Pot", value=f"{pot}c", inline=True)
    if table.players:
        lines = [
            f"\U0001f4b5 **{p.display_name}** \u2014 {p.bet}c"
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
        text=(
            f"Host: {table.host_name} \u2502 "
            f"Min {MIN_PLAYERS} players"
        ),
    )
    return embed


def _round_embed(
    table: StockTable, changes: dict[str, int], time_remaining: int,
) -> discord.Embed:
    event = table.current_event
    embed = discord.Embed(
        title=f"\U0001f4ca Stock Market \u2014 Round {table.round_num}/{NUM_ROUNDS}",
        colour=discord.Colour.gold(),
    )

    # Event card
    if event:
        embed.description = (
            f"## {event.emoji} EVENT: {event.name}\n"
            f"{event.description}"
        )

    # Stock table
    lines: list[str] = []
    lines.append("```")
    lines.append(f"{'Ticker':<10}{'Stock':<12}{'Price':>7}{'Change':>10}  Trend")
    lines.append("-" * 52)
    for ticker, emoji, name in STOCKS:
        stock = table.stocks[ticker]
        change = changes.get(ticker, 0)
        old_price = stock.price - change
        if old_price == 0:
            pct_num = 0
        else:
            pct_num = round(change / old_price * 100)
        if pct_num > 0:
            chg_str = f"+{pct_num}%"
        elif pct_num < 0:
            chg_str = f"{pct_num}%"
        else:
            chg_str = "0%"
        spark = _spark(stock.history)
        lines.append(
            f"{emoji} {ticker:<6}{name:<12}{stock.price:>5}c{chg_str:>9}  {spark}"
        )
    lines.append("```")
    embed.add_field(name="Market", value="\n".join(lines), inline=False)

    # Timer
    if time_remaining > 0:
        embed.add_field(
            name="Trading",
            value=f"Open! **{time_remaining}s** remaining... Use Buy/Sell buttons.",
            inline=False,
        )
    else:
        embed.add_field(
            name="Trading",
            value="**CLOSED** \u2014 prices locked.",
            inline=False,
        )

    embed.set_footer(
        text=(
            f"Host: {table.host_name} \u2502 "
            f"{len(table.players)} players \u2502 "
            "Click Portfolio to see your holdings"
        ),
    )
    return embed


def _final_embed(
    table: StockTable, *, balances: dict[int, int] | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"\U0001f4ca Stock Market \u2014 Final Results (Game {table.game_num})",
        colour=discord.Colour.gold(),
    )

    # Final stock prices
    stock_lines: list[str] = []
    stock_lines.append("```")
    stock_lines.append(f"{'Ticker':<10}{'Stock':<12}{'Final':>7}{'vs Start':>10}  Trend")
    stock_lines.append("-" * 52)
    for ticker, emoji, name in STOCKS:
        stock = table.stocks[ticker]
        change = stock.price - 100
        if change > 0:
            chg_str = f"+{change}c"
        elif change < 0:
            chg_str = f"{change}c"
        else:
            chg_str = "0c"
        spark = _spark(stock.history)
        stock_lines.append(
            f"{emoji} {ticker:<6}{name:<12}{stock.price:>5}c{chg_str:>9}  {spark}"
        )
    stock_lines.append("```")
    embed.add_field(name="Final Prices", value="\n".join(stock_lines), inline=False)

    # Player rankings
    ranked = sorted(
        table.players.values(),
        key=lambda p: p.portfolio_value(table.stocks),
        reverse=True,
    )

    result_lines: list[str] = []
    for i, p in enumerate(ranked):
        val = p.portfolio_value(table.stocks)
        bal = balances.get(p.user_id, 0) if balances else 0
        net = p.payout - p.bet if hasattr(p, "payout") else 0

        # Holdings breakdown
        holdings_parts: list[str] = []
        for ticker, emoji, _ in STOCKS:
            shares = p.holdings.get(ticker, 0)
            if shares > 0:
                stock_val = shares * table.stocks[ticker].price
                holdings_parts.append(f"{emoji}{shares}x{table.stocks[ticker].price}c={stock_val}c")

        holdings_str = ", ".join(holdings_parts) if holdings_parts else "no stocks"
        cash_str = f"cash: {p.cash}c"

        payout = getattr(p, "payout", 0)
        net = payout - p.bet
        sign = "+" if net >= 0 else ""
        if p.user_id in table.winners:
            medal = "\U0001f3c6" if i == 0 else "\U0001f91d"
            result_lines.append(
                f"{medal} **{p.display_name}** \u2014 "
                f"portfolio: **{val}c** ({cash_str}, {holdings_str})\n"
                f"    {p.bet}c \u2192 {payout}c "
                f"(**{sign}{net}c**) \u2014 bal: {bal}c"
            )
        elif payout > 0:
            result_lines.append(
                f"\U0001f4b0 **{p.display_name}** \u2014 "
                f"portfolio: **{val}c** ({cash_str}, {holdings_str})\n"
                f"    {p.bet}c \u2192 {payout}c "
                f"(**{sign}{net}c**) \u2014 bal: {bal}c"
            )
        else:
            result_lines.append(
                f"\u274c **{p.display_name}** \u2014 "
                f"portfolio: **{val}c** ({cash_str}, {holdings_str})\n"
                f"    {p.bet}c \u2192 0c "
                f"(**-{p.bet}c**) \u2014 bal: {bal}c"
            )

    embed.add_field(name="Results", value="\n".join(result_lines), inline=False)

    # Winner announcement
    if table.winners:
        winner_names = [table.players[uid].display_name for uid in table.winners]
        if len(winner_names) == 1:
            p = table.players[table.winners[0]]
            embed.description = f"**{p.display_name}** wins **{p.payout}c**!"
        else:
            winner_pays = [table.players[uid].payout for uid in table.winners]
            if len(set(winner_pays)) == 1:
                embed.description = (
                    f"**{' & '.join(winner_names)}** tie and split the pot! "
                    f"({winner_pays[0]}c each)"
                )
            else:
                parts = [
                    f"**{table.players[uid].display_name}** {table.players[uid].payout}c"
                    for uid in table.winners
                ]
                embed.description = f"{', '.join(parts)} split the pot!"

    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


# ── Modals ───────────────────────────────────────────────────────────────────


class JoinStockModal(ui.Modal):
    amount = ui.TextInput(
        label="Buy-in amount (coins)",
        placeholder="e.g. 100",
        required=True,
        max_length=10,
    )

    def __init__(
        self, table: StockTable, view: "StockTableView", balance: int,
    ) -> None:
        super().__init__(title="Join Stock Market")
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
                "You're already in this game!", ephemeral=True,
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

        self.table.players[uid] = StockPlayer(
            user_id=uid,
            display_name=interaction.user.display_name,
            bet=amt,
        )

        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self.table_view,
        )


class BuyModal(ui.Modal):
    ticker_input = ui.TextInput(
        label="Stock ticker",
        placeholder="TECH, ENRG, FOOD, BANK, PHMA, or MEME",
        required=True,
        max_length=4,
    )
    quantity_input = ui.TextInput(
        label="Number of shares",
        placeholder="e.g. 5",
        required=True,
        max_length=10,
    )

    def __init__(self, table: StockTable, view: "StockTableView") -> None:
        super().__init__(title="Buy Stocks")
        self.table = table
        self.table_view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if self.table.trade_locked:
            await interaction.response.send_message(
                "Trading is closed!", ephemeral=True,
            )
            return

        uid = interaction.user.id
        player = self.table.players.get(uid)
        if player is None:
            await interaction.response.send_message(
                "You're not in this game!", ephemeral=True,
            )
            return

        ticker = self.ticker_input.value.strip().upper()
        if ticker not in VALID_TICKERS:
            await interaction.response.send_message(
                f"Invalid ticker `{ticker}`. "
                f"Valid: {', '.join(sorted(VALID_TICKERS))}",
                ephemeral=True,
            )
            return

        try:
            qty = int(self.quantity_input.value)
        except ValueError:
            await interaction.response.send_message(
                "Enter a whole number for shares.", ephemeral=True,
            )
            return
        if qty < 1:
            await interaction.response.send_message(
                "Must buy at least 1 share.", ephemeral=True,
            )
            return

        price = self.table.stocks[ticker].price
        cost = qty * price
        if cost > player.cash:
            max_affordable = player.cash // price
            await interaction.response.send_message(
                f"Not enough in-game cash! "
                f"{qty} shares of {ticker} = {cost}c, you have {player.cash}c. "
                f"(Max you can buy: {max_affordable})",
                ephemeral=True,
            )
            return

        player.cash -= cost
        player.holdings[ticker] = player.holdings.get(ticker, 0) + qty

        await interaction.response.send_message(
            f"Bought **{qty}x {ticker}** at {price}c each ({cost}c total). "
            f"Remaining cash: {player.cash}c",
            ephemeral=True,
        )


class SellModal(ui.Modal):
    ticker_input = ui.TextInput(
        label="Stock ticker",
        placeholder="TECH, ENRG, FOOD, BANK, PHMA, or MEME",
        required=True,
        max_length=4,
    )
    quantity_input = ui.TextInput(
        label="Number of shares to sell",
        placeholder="e.g. 3",
        required=True,
        max_length=10,
    )

    def __init__(self, table: StockTable, view: "StockTableView") -> None:
        super().__init__(title="Sell Stocks")
        self.table = table
        self.table_view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if self.table.trade_locked:
            await interaction.response.send_message(
                "Trading is closed!", ephemeral=True,
            )
            return

        uid = interaction.user.id
        player = self.table.players.get(uid)
        if player is None:
            await interaction.response.send_message(
                "You're not in this game!", ephemeral=True,
            )
            return

        ticker = self.ticker_input.value.strip().upper()
        if ticker not in VALID_TICKERS:
            await interaction.response.send_message(
                f"Invalid ticker `{ticker}`. "
                f"Valid: {', '.join(sorted(VALID_TICKERS))}",
                ephemeral=True,
            )
            return

        try:
            qty = int(self.quantity_input.value)
        except ValueError:
            await interaction.response.send_message(
                "Enter a whole number for shares.", ephemeral=True,
            )
            return
        if qty < 1:
            await interaction.response.send_message(
                "Must sell at least 1 share.", ephemeral=True,
            )
            return

        held = player.holdings.get(ticker, 0)
        if qty > held:
            await interaction.response.send_message(
                f"You only own {held} shares of {ticker}!",
                ephemeral=True,
            )
            return

        price = self.table.stocks[ticker].price
        proceeds = qty * price
        player.cash += proceeds
        player.holdings[ticker] = held - qty
        if player.holdings[ticker] == 0:
            del player.holdings[ticker]

        await interaction.response.send_message(
            f"Sold **{qty}x {ticker}** at {price}c each ({proceeds}c total). "
            f"Cash: {player.cash}c",
            ephemeral=True,
        )


# ── View ─────────────────────────────────────────────────────────────────────


class StockTableView(ui.View):
    def __init__(
        self, table: StockTable, active_tables: dict[int, StockTable],
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
        trading_open = playing and not self.table.trade_locked

        # Row 0: Start, Join, Re-bet, Leave
        self.start_btn.disabled = (
            not betting or len(self.table.players) < MIN_PLAYERS
        )
        self.join_btn.disabled = not betting
        self.rebet_btn.disabled = not betting or not self.table.last_bets
        self.leave_btn.disabled = playing

        # Row 1: Buy, Sell, Portfolio (only active during trading)
        self.buy_btn.disabled = not trading_open
        self.sell_btn.disabled = not trading_open
        self.portfolio_btn.disabled = not playing

        # Row 2: New Game, Close Table
        self.new_game_btn.disabled = not finished
        self.close_btn.disabled = playing

    # ── Row 0 ────────────────────────────────────────────────────────────────

    @ui.button(
        label="Start", style=discord.ButtonStyle.success,
        emoji="\u25b6\ufe0f", row=0,
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
        label="Join", style=discord.ButtonStyle.primary,
        emoji="\U0001f4b5", row=0,
    )
    async def join_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Game in progress! Wait for the next one.", ephemeral=True,
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
        if bal < 1:
            await interaction.response.send_message(
                "You have no coins!", ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            JoinStockModal(self.table, self, bal),
        )

    @ui.button(
        label="Re-bet", style=discord.ButtonStyle.primary,
        emoji="\U0001f504", row=0,
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
        self.table.players[uid] = StockPlayer(
            user_id=uid, display_name=name, bet=amt,
        )
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    @ui.button(
        label="Leave", style=discord.ButtonStyle.secondary,
        emoji="\U0001f6aa", row=0,
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
            "Game is over. Wait for New Game or close.", ephemeral=True,
        )

    # ── Row 1 ────────────────────────────────────────────────────────────────

    @ui.button(
        label="Buy", style=discord.ButtonStyle.success,
        emoji="\U0001f4b0", row=1,
    )
    async def buy_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "playing" or self.table.trade_locked:
            await interaction.response.send_message(
                "Trading is closed!", ephemeral=True,
            )
            return
        uid = interaction.user.id
        if uid not in self.table.players:
            await interaction.response.send_message(
                "You're not in this game!", ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            BuyModal(self.table, self),
        )

    @ui.button(
        label="Sell", style=discord.ButtonStyle.danger,
        emoji="\U0001f4b8", row=1,
    )
    async def sell_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "playing" or self.table.trade_locked:
            await interaction.response.send_message(
                "Trading is closed!", ephemeral=True,
            )
            return
        uid = interaction.user.id
        if uid not in self.table.players:
            await interaction.response.send_message(
                "You're not in this game!", ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            SellModal(self.table, self),
        )

    @ui.button(
        label="Portfolio", style=discord.ButtonStyle.secondary,
        emoji="\U0001f4cb", row=1,
    )
    async def portfolio_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if player is None:
            await interaction.response.send_message(
                "You're not in this game!", ephemeral=True,
            )
            return

        stocks = self.table.stocks
        lines: list[str] = [f"**Cash:** {player.cash}c\n"]

        total_stock_value = 0
        if player.holdings:
            lines.append("**Holdings:**")
            for ticker, emoji, name in STOCKS:
                shares = player.holdings.get(ticker, 0)
                if shares > 0:
                    value = shares * stocks[ticker].price
                    total_stock_value += value
                    lines.append(
                        f"{emoji} {ticker} \u2014 {shares} shares "
                        f"@ {stocks[ticker].price}c = **{value}c**"
                    )
        else:
            lines.append("*No stocks held*")

        total = player.cash + total_stock_value
        lines.append(f"\n**Total Portfolio Value: {total}c**")

        await interaction.response.send_message(
            "\n".join(lines), ephemeral=True,
        )

    # ── Row 2 ────────────────────────────────────────────────────────────────

    @ui.button(
        label="New Game", style=discord.ButtonStyle.success,
        emoji="\u25b6\ufe0f", row=2,
    )
    async def new_game_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can start a new game!", ephemeral=True,
            )
            return
        if self.table.phase != "finished":
            await interaction.response.send_message(
                "Game still in progress!", ephemeral=True,
            )
            return
        self._start_new_game()
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    @ui.button(
        label="Close Table", style=discord.ButtonStyle.danger,
        emoji="\u2716\ufe0f", row=2,
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
                    log.exception("Unhandled error in stockmarket.py")
        await self._close(interaction, "Table closed by host.")

    # ── Game logic ───────────────────────────────────────────────────────────

    async def _start_game(self, interaction: discord.Interaction) -> None:
        table = self.table
        table.phase = "playing"

        # Initialize stocks
        table.stocks = _init_stocks()

        # Build and shuffle event deck, draw NUM_ROUNDS cards
        deck = list(EVENT_CARDS)
        random.shuffle(deck)
        table.event_deck = deck[:NUM_ROUNDS]

        # Reset player in-game state
        for p in table.players.values():
            p.cash = STARTING_CASH
            p.holdings = {}

        table.round_num = 0
        table.trade_locked = True

        # Kick off first round
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(table), view=self,
        )
        table.trade_task = asyncio.create_task(self._game_loop())

    async def _game_loop(self) -> None:
        """Main game loop: run NUM_ROUNDS rounds sequentially."""
        table = self.table
        try:
            for round_idx in range(NUM_ROUNDS):
                table.round_num = round_idx + 1
                await self._run_round()

            # All rounds done — finalize
            await self._finalize()

        except asyncio.CancelledError:
            pass
        except Exception:
            if table.phase == "playing":
                table.phase = "finished"
                await self._refund_all()

    async def _run_round(self) -> None:
        """Execute a single round: reveal event, trade window, lock."""
        table = self.table

        # Draw event and apply price changes
        event = table.event_deck[table.round_num - 1]
        table.current_event = event
        changes = _apply_event(table.stocks, event)

        # Open trading
        table.trade_locked = False
        self._update_buttons()

        # Show round with full timer
        if table.message:
            try:
                await table.message.edit(
                    embed=_round_embed(table, changes, TRADE_WINDOW),
                    view=self,
                )
            except discord.HTTPException:
                pass

        # Countdown: update every 10 seconds
        elapsed = 0
        while elapsed < TRADE_WINDOW:
            sleep_time = min(10, TRADE_WINDOW - elapsed)
            await asyncio.sleep(sleep_time)
            elapsed += sleep_time
            remaining = TRADE_WINDOW - elapsed

            if remaining > 0 and table.message:
                try:
                    await table.message.edit(
                        embed=_round_embed(table, changes, remaining),
                        view=self,
                    )
                except discord.HTTPException:
                    pass

        # Lock trading
        table.trade_locked = True
        self._update_buttons()

        if table.message:
            try:
                await table.message.edit(
                    embed=_round_embed(table, changes, 0),
                    view=self,
                )
            except discord.HTTPException:
                pass

        # Brief pause between rounds
        if table.round_num < NUM_ROUNDS:
            await asyncio.sleep(3)

    async def _finalize(self) -> None:
        """Compute final portfolios, determine winner, pay out."""
        table = self.table
        table.phase = "finished"

        # Compute portfolio values
        values: dict[int, int] = {}
        for uid, player in table.players.items():
            values[uid] = player.portfolio_value(table.stocks)

        # Find winner(s) — highest portfolio value
        if not values:
            await self._refund_all()
            return

        max_val = max(values.values())
        table.winners = [uid for uid, val in values.items() if val == max_val]

        # Side-pot payouts
        bets = {uid: p.bet for uid, p in table.players.items()}
        payouts = compute_side_pot_payouts(bets, table.winners)
        for uid, player in table.players.items():
            player.payout = payouts.get(uid, 0)

        # Credit payouts and log results
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
                str(uid), "stockmarket", player.bet, player.payout,
            )

        # Save last bets for re-bet
        for uid, player in table.players.items():
            table.last_bets[uid] = (player.display_name, player.bet)

        self._update_buttons()
        if table.message:
            try:
                await table.message.edit(
                    embed=_final_embed(table, balances=balances),
                    view=self,
                )
            except discord.HTTPException:
                pass

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def _start_new_game(self) -> None:
        table = self.table
        table.players.clear()
        table.phase = "betting"
        table.game_num += 1
        table.round_num = 0
        table.stocks.clear()
        table.current_event = None
        table.event_deck.clear()
        table.winners.clear()
        table.trade_task = None
        table.trade_locked = False

    async def _refund_all(self) -> None:
        for p in self.table.players.values():
            try:
                await queries.update_casino_balance(str(p.user_id), p.bet)
            except Exception:
                log.exception("Unhandled error in stockmarket.py")

    async def _close(
        self, interaction: discord.Interaction, reason: str,
    ) -> None:
        embed = discord.Embed(
            title="\U0001f4ca Stock Market Table \u2014 Closed",
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

        if table.trade_task and not table.trade_task.done():
            table.trade_task.cancel()

        if table.phase == "finished":
            self.active_tables.pop(table.channel_id, None)
            if table.message:
                try:
                    embed = discord.Embed(
                        title="\U0001f4ca Stock Market Table \u2014 Timed Out",
                        description="Table timed out between games.",
                        colour=discord.Colour.dark_grey(),
                    )
                    await table.message.edit(embed=embed, view=None)
                except Exception:
                    log.exception("Unhandled error in stockmarket.py")
            return

        # Betting or playing — refund all
        await self._refund_all()
        self.active_tables.pop(table.channel_id, None)
        if table.message:
            try:
                embed = discord.Embed(
                    title="\U0001f4ca Stock Market Table \u2014 Timed Out",
                    description="Table timed out. All buy-ins refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                log.exception("Unhandled error in stockmarket.py")


# ── Cog ──────────────────────────────────────────────────────────────────────


class StockMarketCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, StockTable] = {}

    @app_commands.command(
        name="stockmarket",
        description="Open a Stock Market table (multiplayer investment game)",
    )
    async def stockmarket(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            existing = self.active_tables[channel_id]
            _has_running = any(
                (t := getattr(existing, n, None)) is not None and not t.done()
                for n in ("game_task", "race_task", "sim_task", "round_task", "_round_task", "trade_task", "fly_task", "_shot_clock_task", "_countdown_task")
            )
            if _has_running:
                await interaction.response.send_message(
                    "There's already a Stock Market table in this channel!",
                    ephemeral=True,
                )
                return
            del self.active_tables[channel_id]

        await queries.get_or_create_casino_wallet(str(interaction.user.id))

        table = StockTable(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
        )
        self.active_tables[channel_id] = table

        view = StockTableView(table, self.active_tables)
        embed = _betting_embed(table)
        await interaction.response.send_message(embed=embed, view=view)
        table.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StockMarketCog(bot))
