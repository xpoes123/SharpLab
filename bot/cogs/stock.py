"""Stock price lookup + personal portfolio tracking.

Trades are the source of truth — every buy/sell is appended to a trade log
and the current holding (shares + DCA) is derived from that log using
average-cost-basis accounting.

- /stock [ticker]               — quote a single ticker, or your full portfolio if omitted.
- /stock_portfolio buy          — record a buy (shares + price + optional date).
- /stock_portfolio sell         — record a sell (shares + price + optional date).
- /stock_portfolio trades       — show your recent trade history (optionally filtered to one ticker).
- /stock_portfolio remove       — wipe all trades for a ticker.
- /stock_portfolio list         — show your holdings (alias of bare /stock).
"""
import asyncio
import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from db import queries

log = logging.getLogger(__name__)


def _yahoo_url(ticker: str) -> str:
    return f"https://finance.yahoo.com/quote/{ticker}"


def _fmt_money(amount: float, currency: str = "USD") -> str:
    return f"{amount:,.2f} {currency}"


def _fmt_change(change: float, pct: float) -> str:
    sign = "+" if change >= 0 else ""
    return f"{sign}{change:,.2f} ({sign}{pct:.2f}%)"


def _parse_executed_at(value: str | None) -> str | None:
    """Accept 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM' (UTC); return ISO 8601.
    None passes through (caller will default to now)."""
    if value is None:
        return None
    s = value.strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            continue
    raise ValueError(f"Could not parse date {value!r}. Use YYYY-MM-DD or YYYY-MM-DD HH:MM.")


async def fetch_quote(ticker: str) -> dict:
    """Fetch latest price + previous close for a single ticker via yfinance.

    Returns {price, prev_close, currency, name}. Raises on missing data.
    """
    import yfinance as yf

    def _fetch() -> dict:
        tk = yf.Ticker(ticker)
        info = tk.fast_info
        get = info.get if hasattr(info, "get") else lambda k, d=None: getattr(info, k, d)
        price = get("last_price")
        prev = get("previous_close")
        currency = get("currency") or "USD"

        if price is None or prev is None:
            hist = tk.history(period="2d")
            if hist.empty:
                raise ValueError(f"No price data for {ticker}")
            price = float(hist["Close"].iloc[-1])
            prev = float(hist["Close"].iloc[0]) if len(hist) >= 2 else price

        name = ticker
        try:
            long_name = tk.info.get("longName") or tk.info.get("shortName")
            if long_name:
                name = long_name
        except Exception:
            pass

        return {
            "price": float(price),
            "prev_close": float(prev),
            "currency": currency,
            "name": name,
        }

    return await asyncio.get_running_loop().run_in_executor(None, _fetch)


async def fetch_quotes(tickers: list[str]) -> dict[str, dict]:
    """Fetch quotes for many tickers in one executor call. Skips ones that fail."""
    import yfinance as yf

    def _fetch() -> dict[str, dict]:
        out: dict[str, dict] = {}
        for sym in tickers:
            try:
                tk = yf.Ticker(sym)
                info = tk.fast_info
                get = info.get if hasattr(info, "get") else lambda k, d=None: getattr(info, k, d)
                price = get("last_price")
                prev = get("previous_close")
                currency = get("currency") or "USD"
                if price is None or prev is None:
                    hist = tk.history(period="2d")
                    if hist.empty:
                        continue
                    price = float(hist["Close"].iloc[-1])
                    prev = float(hist["Close"].iloc[0]) if len(hist) >= 2 else price
                out[sym] = {
                    "price": float(price),
                    "prev_close": float(prev),
                    "currency": currency,
                }
            except Exception as e:
                log.warning(f"fetch_quotes: {sym} failed: {e}")
        return out

    return await asyncio.get_running_loop().run_in_executor(None, _fetch)


# ── Portfolio embed builder ─────────────────────────────────────────────────


async def _build_portfolio_embed(user: discord.abc.User) -> discord.Embed | None:
    holdings = await queries.get_stock_holdings(str(user.id))
    if not holdings:
        return None

    quotes = await fetch_quotes([h["ticker"] for h in holdings])

    total_value = 0.0
    total_cost = 0.0
    total_day_change = 0.0
    lines: list[str] = []

    for h in holdings:
        sym = h["ticker"]
        shares = h["shares"]
        dca = h["dca_price"]
        cost = shares * dca

        q = quotes.get(sym)
        if not q:
            lines.append(f"`{sym}` — {shares:g} sh @ {dca:,.2f} DCA · *price unavailable*")
            total_cost += cost
            continue

        price = q["price"]
        prev = q["prev_close"]
        value = shares * price
        pl = value - cost
        pl_pct = (pl / cost * 100) if cost else 0.0
        day_change = shares * (price - prev)

        total_value += value
        total_cost += cost
        total_day_change += day_change

        pl_sign = "+" if pl >= 0 else ""
        emoji = "🟢" if pl >= 0 else "🔴"
        lines.append(
            f"{emoji} [`{sym}`]({_yahoo_url(sym)}) · {shares:g} sh @ {dca:,.2f} → "
            f"**{price:,.2f}** · P/L `{pl_sign}{pl:,.2f} ({pl_sign}{pl_pct:.2f}%)`"
        )

    total_pl = total_value - total_cost
    total_pl_pct = (total_pl / total_cost * 100) if total_cost else 0.0
    day_pct = (total_day_change / (total_value - total_day_change) * 100) if (total_value - total_day_change) else 0.0
    color = 0x57F287 if total_pl >= 0 else 0xED4245

    embed = discord.Embed(
        title=f"{user.display_name}'s Portfolio",
        description="\n".join(lines),
        color=color,
    )
    embed.add_field(name="Market Value", value=f"`{_fmt_money(total_value)}`", inline=True)
    embed.add_field(name="Cost Basis", value=f"`{_fmt_money(total_cost)}`", inline=True)
    embed.add_field(
        name="Total P/L",
        value=f"`{_fmt_change(total_pl, total_pl_pct)}`",
        inline=True,
    )
    embed.add_field(
        name="Today",
        value=f"`{_fmt_change(total_day_change, day_pct)}`",
        inline=True,
    )
    return embed


# ── Cog ─────────────────────────────────────────────────────────────────────


class StockCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    portfolio = app_commands.Group(name="stock_portfolio", description="Manage your tracked stock holdings")

    # ── /stock ────────────────────────────────────────────────────────────────

    @app_commands.command(
        name="stock",
        description="Look up a stock's price (or your full portfolio if no ticker is given)",
    )
    @app_commands.describe(ticker="Ticker symbol (e.g. AAPL). Leave blank to show your portfolio.")
    async def stock(self, interaction: discord.Interaction, ticker: str | None = None) -> None:
        await interaction.response.defer()

        if ticker is None or not ticker.strip():
            embed = await _build_portfolio_embed(interaction.user)
            if embed is None:
                await interaction.followup.send(
                    "You don't have any holdings yet. Add one with `/stock_portfolio buy`.",
                    ephemeral=True,
                )
                return
            await interaction.followup.send(embed=embed)
            return

        symbol = ticker.strip().upper()
        try:
            quote = await fetch_quote(symbol)
        except Exception as e:
            log.warning(f"/stock failed for {symbol}: {e}")
            await interaction.followup.send(f"Couldn't fetch a quote for `{symbol}`.", ephemeral=True)
            return

        price = quote["price"]
        prev = quote["prev_close"]
        change = price - prev
        pct = (change / prev * 100) if prev else 0.0
        color = 0x57F287 if change >= 0 else 0xED4245
        url = _yahoo_url(symbol)

        embed = discord.Embed(title=f"{quote['name']} ({symbol})", url=url, color=color)
        embed.add_field(name="Price", value=f"`{_fmt_money(price, quote['currency'])}`", inline=True)
        embed.add_field(name="Today", value=f"`{_fmt_change(change, pct)}`", inline=True)

        # If the user holds this ticker, show their P/L too.
        holding = await queries.get_stock_holding(str(interaction.user.id), symbol)
        if holding:
            shares = holding["shares"]
            dca = holding["dca_price"]
            cost = shares * dca
            value = shares * price
            pl = value - cost
            pl_pct = (pl / cost * 100) if cost else 0.0
            embed.add_field(
                name="Your Position",
                value=(
                    f"`{shares:g}` sh @ DCA `{dca:,.2f}`\n"
                    f"Value: `{_fmt_money(value, quote['currency'])}`\n"
                    f"P/L: `{_fmt_change(pl, pl_pct)}`"
                ),
                inline=False,
            )

        embed.add_field(name="Yahoo Finance", value=f"[Open ↗]({url})", inline=False)
        await interaction.followup.send(embed=embed)

    # ── /stock_portfolio buy ─────────────────────────────────────────────────

    @portfolio.command(name="buy", description="Record a stock purchase")
    @app_commands.describe(
        ticker="Ticker symbol (e.g. AAPL)",
        shares="Number of shares bought (fractional ok)",
        price="Execution price per share",
        date="Optional trade date (YYYY-MM-DD or YYYY-MM-DD HH:MM, UTC). Defaults to now.",
        notes="Optional note",
    )
    async def portfolio_buy(
        self,
        interaction: discord.Interaction,
        ticker: str,
        shares: float,
        price: float,
        date: str | None = None,
        notes: str | None = None,
    ) -> None:
        symbol = ticker.strip().upper()
        if not symbol:
            await interaction.response.send_message("Please provide a ticker.", ephemeral=True)
            return
        if shares <= 0 or price <= 0:
            await interaction.response.send_message("Shares and price must be > 0.", ephemeral=True)
            return
        try:
            executed_at = _parse_executed_at(date)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        await queries.add_stock_trade(
            str(interaction.user.id), symbol, "buy", shares, price, executed_at, notes
        )
        holding = await queries.get_stock_holding(str(interaction.user.id), symbol)
        position_line = (
            f"Position: **{holding['shares']:g}** sh @ DCA **{holding['dca_price']:,.2f}**"
            if holding else "Position closed."
        )
        await interaction.response.send_message(
            f"Recorded **BUY** `{symbol}` — {shares:g} sh @ `{price:,.2f}`.\n{position_line}",
            ephemeral=True,
        )

    # ── /stock_portfolio sell ────────────────────────────────────────────────

    @portfolio.command(name="sell", description="Record a stock sale")
    @app_commands.describe(
        ticker="Ticker symbol (e.g. AAPL)",
        shares="Number of shares sold (fractional ok)",
        price="Execution price per share",
        date="Optional trade date (YYYY-MM-DD or YYYY-MM-DD HH:MM, UTC). Defaults to now.",
        notes="Optional note",
    )
    async def portfolio_sell(
        self,
        interaction: discord.Interaction,
        ticker: str,
        shares: float,
        price: float,
        date: str | None = None,
        notes: str | None = None,
    ) -> None:
        symbol = ticker.strip().upper()
        if not symbol:
            await interaction.response.send_message("Please provide a ticker.", ephemeral=True)
            return
        if shares <= 0 or price <= 0:
            await interaction.response.send_message("Shares and price must be > 0.", ephemeral=True)
            return
        try:
            executed_at = _parse_executed_at(date)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        current = await queries.get_stock_holding(str(interaction.user.id), symbol)
        held = current["shares"] if current else 0.0
        if shares > held + 1e-9:
            await interaction.response.send_message(
                f"You only hold {held:g} sh of `{symbol}` — can't sell {shares:g}.",
                ephemeral=True,
            )
            return

        await queries.add_stock_trade(
            str(interaction.user.id), symbol, "sell", shares, price, executed_at, notes
        )
        holding = await queries.get_stock_holding(str(interaction.user.id), symbol)
        position_line = (
            f"Position: **{holding['shares']:g}** sh @ DCA **{holding['dca_price']:,.2f}**"
            if holding else "Position closed."
        )
        await interaction.response.send_message(
            f"Recorded **SELL** `{symbol}` — {shares:g} sh @ `{price:,.2f}`.\n{position_line}",
            ephemeral=True,
        )

    # ── /stock_portfolio trades ──────────────────────────────────────────────

    @portfolio.command(name="trades", description="Show your trade history")
    @app_commands.describe(ticker="Optional ticker to filter by")
    async def portfolio_trades(
        self, interaction: discord.Interaction, ticker: str | None = None
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        symbol = ticker.strip().upper() if ticker else None
        trades = await queries.get_stock_trades(str(interaction.user.id), symbol)
        if not trades:
            scope = f" for `{symbol}`" if symbol else ""
            await interaction.followup.send(f"No trades recorded{scope}.", ephemeral=True)
            return

        # Show most recent 25 (chronological from queries; reverse for display).
        recent = list(reversed(trades))[:25]
        lines: list[str] = []
        for t in recent:
            side_emoji = "🟢" if t["side"] == "buy" else "🔴"
            # executed_at is ISO; show just the date for compactness.
            date_str = t["executed_at"][:10]
            note = f" — _{t['notes']}_" if t["notes"] else ""
            lines.append(
                f"{side_emoji} `#{t['trade_id']:>4}` · {date_str} · "
                f"**{t['side'].upper()}** `{t['ticker']}` "
                f"{t['shares']:g} @ {t['price']:,.2f}{note}"
            )

        title = f"Trades for {symbol}" if symbol else "Recent trades"
        embed = discord.Embed(
            title=title,
            description="\n".join(lines),
            color=0x5865F2,
        )
        if len(trades) > 25:
            embed.set_footer(text=f"Showing 25 of {len(trades)} total trades.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /stock_portfolio remove ──────────────────────────────────────────────

    @portfolio.command(
        name="remove",
        description="Wipe all trades for a ticker (irreversible)",
    )
    @app_commands.describe(ticker="Ticker symbol to remove")
    async def portfolio_remove(self, interaction: discord.Interaction, ticker: str) -> None:
        symbol = ticker.strip().upper()
        n = await queries.delete_stock_trades_for_ticker(str(interaction.user.id), symbol)
        if n == 0:
            await interaction.response.send_message(
                f"You don't have any trades for `{symbol}`.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"Removed `{symbol}` — wiped {n} trade(s) from your log.",
            ephemeral=True,
        )

    # ── /stock_portfolio list ────────────────────────────────────────────────

    @portfolio.command(name="list", description="Show your portfolio with current P/L")
    async def portfolio_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        embed = await _build_portfolio_embed(interaction.user)
        if embed is None:
            await interaction.followup.send(
                "You don't have any holdings yet. Add one with `/stock_portfolio buy`.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StockCog(bot))
