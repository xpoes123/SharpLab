"""/stock — quotes, personal portfolio tracking, leaderboard, and movers.

All commands live under a single /stock group:

- /stock lookup <ticker>            — quote a single ticker (+ your position if any).
- /stock profile [user]             — show your portfolio, or a tagged user's.
- /stock buy <ticker> <sh> <px>     — record a buy.
- /stock sell <ticker> <sh> <px>    — record a sell (realized P/L computed).
- /stock trades [ticker] [user]     — show recent trade history.
- /stock leaderboard                — server-wide ranking by total P/L (unrealized + realized).
- /stock movers                     — S&P 100 gainers and losers today.

Trades are the source of truth; holdings and realized P/L are derived by
walking the trade log with average-cost-basis accounting.
"""
import asyncio
import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs._movers_helpers import build_movers_embed
from db import queries

log = logging.getLogger(__name__)


def _yahoo_url(ticker: str) -> str:
    return f"https://finance.yahoo.com/quote/{ticker}"


def _fmt_money(amount: float, currency: str = "USD") -> str:
    return f"{amount:,.2f} {currency}"


def _fmt_change(change: float, pct: float) -> str:
    sign = "+" if change >= 0 else ""
    return f"{sign}{change:,.2f} ({sign}{pct:.2f}%)"


def _fmt_pnl(amount: float) -> str:
    sign = "+" if amount >= 0 else ""
    return f"{sign}{amount:,.2f}"


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

    Returns {price, prev_close, currency, name, extended}. `extended` is None
    or a dict with {session: 'post'|'pre', price, change, pct} when the market
    is currently outside regular hours and Yahoo has an extended-hours print.
    Raises on missing data.
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
        extended: dict | None = None
        # tk.info is slow and occasionally flaky, but it's the only path that
        # exposes regular-session vs extended-hours prices explicitly. We've
        # already been paying for it to get longName, so reuse the same call.
        try:
            full = tk.info
            long_name = full.get("longName") or full.get("shortName")
            if long_name:
                name = long_name
            # Prefer explicit regular-session values when present; fast_info's
            # `last_price` will be the AH/pre print during extended hours,
            # which would make "Today" compare two different sessions.
            rmp = full.get("regularMarketPrice")
            if rmp is not None:
                price = float(rmp)
            rpc = full.get("regularMarketPreviousClose") or full.get("previousClose")
            if rpc is not None:
                prev = float(rpc)
            market_state = full.get("marketState")
            pre_price = full.get("preMarketPrice")
            post_price = full.get("postMarketPrice")
            ext_price = None
            session = None
            # Pre-market takes priority when actively in pre-market hours.
            # Otherwise the post-market print is the most recent extended-hours
            # info (during AH itself or in the overnight gap when Yahoo's
            # marketState is PREPRE but the pre session hasn't started yet).
            if market_state in ("PRE", "PREPRE") and pre_price is not None:
                ext_price, session = float(pre_price), "pre"
            elif market_state != "REGULAR" and post_price is not None:
                ext_price, session = float(post_price), "post"
            if ext_price is not None and price:
                ext_price = float(ext_price)
                change = ext_price - price
                pct = (change / price * 100) if price else 0.0
                extended = {
                    "session": session,
                    "price": ext_price,
                    "change": change,
                    "pct": pct,
                }
        except Exception as e:
            log.debug(f"fetch_quote: info read failed for {ticker}: {e}")

        return {
            "price": float(price),
            "prev_close": float(prev),
            "currency": currency,
            "name": name,
            "extended": extended,
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


async def _build_profile_embed(target: discord.abc.User) -> discord.Embed | None:
    """Render `target`'s portfolio. Includes both unrealized P/L on open
    positions and cumulative realized P/L from past sells (including
    fully-closed positions)."""
    positions = await queries.get_stock_positions_full(str(target.id))
    if not positions:
        return None

    open_positions = [p for p in positions if not p["closed"]]
    realized_total = sum(p["realized_pnl"] for p in positions)

    quotes = await fetch_quotes([p["ticker"] for p in open_positions]) if open_positions else {}

    total_value = 0.0
    total_cost = 0.0
    total_day_change = 0.0
    lines: list[str] = []

    for h in open_positions:
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

    unrealized = total_value - total_cost
    total_pnl = unrealized + realized_total
    unrealized_pct = (unrealized / total_cost * 100) if total_cost else 0.0
    day_pct = (total_day_change / (total_value - total_day_change) * 100) if (total_value - total_day_change) else 0.0
    color = 0x57F287 if total_pnl >= 0 else 0xED4245

    description = "\n".join(lines) if lines else "_No open positions._"
    embed = discord.Embed(
        title=f"{target.display_name}'s Portfolio",
        description=description,
        color=color,
    )
    embed.add_field(name="Market Value", value=f"`{_fmt_money(total_value)}`", inline=True)
    embed.add_field(name="Cost Basis", value=f"`{_fmt_money(total_cost)}`", inline=True)
    embed.add_field(
        name="Unrealized P/L",
        value=f"`{_fmt_change(unrealized, unrealized_pct)}`",
        inline=True,
    )
    embed.add_field(name="Realized P/L", value=f"`{_fmt_pnl(realized_total)}`", inline=True)
    embed.add_field(name="Total P/L", value=f"`{_fmt_pnl(total_pnl)}`", inline=True)
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

    stock = app_commands.Group(name="stock", description="Stock quotes, portfolio, and market data")

    # ── /stock lookup ────────────────────────────────────────────────────────

    @stock.command(name="lookup", description="Look up a stock's current price")
    @app_commands.describe(ticker="Ticker symbol (e.g. AAPL)")
    async def lookup(self, interaction: discord.Interaction, ticker: str) -> None:
        await interaction.response.defer()
        symbol = ticker.strip().upper()
        if not symbol:
            await interaction.followup.send("Please provide a ticker.", ephemeral=True)
            return
        try:
            quote = await fetch_quote(symbol)
        except Exception as e:
            log.warning(f"/stock lookup failed for {symbol}: {e}")
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

        ext = quote.get("extended")
        if ext:
            label = "After Hours" if ext["session"] == "post" else "Pre-Market"
            embed.add_field(
                name=label,
                value=(
                    f"`{_fmt_money(ext['price'], quote['currency'])}` · "
                    f"`{_fmt_change(ext['change'], ext['pct'])}`"
                ),
                inline=True,
            )

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
                    f"Unrealized P/L: `{_fmt_change(pl, pl_pct)}`"
                ),
                inline=False,
            )

        embed.add_field(name="Yahoo Finance", value=f"[Open ↗]({url})", inline=False)
        await interaction.followup.send(embed=embed)

    # ── /stock profile ───────────────────────────────────────────────────────

    @stock.command(name="profile", description="Show your portfolio (or a tagged user's)")
    @app_commands.describe(user="Whose portfolio to show (defaults to you)")
    async def profile(
        self, interaction: discord.Interaction, user: discord.User | None = None
    ) -> None:
        await interaction.response.defer()
        target = user or interaction.user
        embed = await _build_profile_embed(target)
        if embed is None:
            if target.id == interaction.user.id:
                msg = "You don't have any trades yet. Add one with `/stock buy`."
            else:
                msg = f"{target.display_name} hasn't logged any trades yet."
            await interaction.followup.send(msg, ephemeral=True)
            return
        await interaction.followup.send(embed=embed)

    # ── /stock buy ───────────────────────────────────────────────────────────

    @stock.command(name="buy", description="Record a stock purchase")
    @app_commands.describe(
        ticker="Ticker symbol (e.g. AAPL)",
        shares="Number of shares bought (fractional ok)",
        price="Execution price per share",
        date="Optional trade date (YYYY-MM-DD or YYYY-MM-DD HH:MM, UTC). Defaults to now.",
        notes="Optional note",
    )
    async def buy(
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

    # ── /stock sell ──────────────────────────────────────────────────────────

    @stock.command(name="sell", description="Record a stock sale (realized P/L is computed)")
    @app_commands.describe(
        ticker="Ticker symbol (e.g. AAPL)",
        shares="Number of shares sold (fractional ok)",
        price="Execution price per share",
        date="Optional trade date (YYYY-MM-DD or YYYY-MM-DD HH:MM, UTC). Defaults to now.",
        notes="Optional note",
    )
    async def sell(
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

        # Compute realized P/L for this individual sell, using the average
        # cost basis at the moment of execution. This is the same number the
        # aggregator will record cumulatively.
        avg_cost = current["dca_price"]
        realized = shares * (price - avg_cost)

        await queries.add_stock_trade(
            str(interaction.user.id), symbol, "sell", shares, price, executed_at, notes
        )
        holding = await queries.get_stock_holding(str(interaction.user.id), symbol)
        position_line = (
            f"Position: **{holding['shares']:g}** sh @ DCA **{holding['dca_price']:,.2f}**"
            if holding else "Position closed."
        )
        await interaction.response.send_message(
            f"Recorded **SELL** `{symbol}` — {shares:g} sh @ `{price:,.2f}` "
            f"· realized P/L `{_fmt_pnl(realized)}`.\n{position_line}",
            ephemeral=True,
        )

    # ── /stock trades ────────────────────────────────────────────────────────

    @stock.command(name="trades", description="Show recent trade history")
    @app_commands.describe(
        ticker="Optional ticker to filter by",
        user="Whose trades to show (defaults to you)",
    )
    async def trades(
        self,
        interaction: discord.Interaction,
        ticker: str | None = None,
        user: discord.User | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        target = user or interaction.user
        symbol = ticker.strip().upper() if ticker else None
        trades = await queries.get_stock_trades(str(target.id), symbol)
        if not trades:
            scope = f" for `{symbol}`" if symbol else ""
            owner = "you" if target.id == interaction.user.id else target.display_name
            await interaction.followup.send(
                f"No trades recorded{scope} for {owner}.", ephemeral=True
            )
            return

        recent = list(reversed(trades))[:25]
        lines: list[str] = []
        for t in recent:
            side_emoji = "🟢" if t["side"] == "buy" else "🔴"
            date_str = t["executed_at"][:10]
            note = f" — _{t['notes']}_" if t["notes"] else ""
            lines.append(
                f"{side_emoji} `#{t['trade_id']:>4}` · {date_str} · "
                f"**{t['side'].upper()}** `{t['ticker']}` "
                f"{t['shares']:g} @ {t['price']:,.2f}{note}"
            )

        title = f"Trades — {target.display_name}"
        if symbol:
            title += f" · {symbol}"
        embed = discord.Embed(title=title, description="\n".join(lines), color=0x5865F2)
        if len(trades) > 25:
            embed.set_footer(text=f"Showing 25 of {len(trades)} total trades.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /stock leaderboard ───────────────────────────────────────────────────

    @stock.command(name="leaderboard", description="Server-wide P/L leaderboard")
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        users = await queries.get_users_with_trades()
        if not users:
            await interaction.followup.send("Nobody has logged any trades yet.", ephemeral=True)
            return

        # Per-user positions in one pass, then batch-fetch prices for the union
        # of open tickers (one yfinance call, not one per user).
        all_positions: dict[str, list[dict]] = {}
        ticker_set: set[str] = set()
        for uid in users:
            positions = await queries.get_stock_positions_full(uid)
            all_positions[uid] = positions
            for p in positions:
                if not p["closed"]:
                    ticker_set.add(p["ticker"])

        quotes = await fetch_quotes(sorted(ticker_set)) if ticker_set else {}

        rows: list[dict] = []
        for uid, positions in all_positions.items():
            unrealized = 0.0
            realized = sum(p["realized_pnl"] for p in positions)
            for p in positions:
                if p["closed"]:
                    continue
                q = quotes.get(p["ticker"])
                if not q:
                    continue
                unrealized += p["shares"] * (q["price"] - p["dca_price"])
            rows.append({
                "user_id": uid,
                "unrealized": unrealized,
                "realized": realized,
                "total": unrealized + realized,
            })

        rows.sort(key=lambda r: r["total"], reverse=True)
        top = rows[:10]

        async def _name(uid: str) -> str:
            user = self.bot.get_user(int(uid)) if uid.isdigit() else None
            if user is None and uid.isdigit():
                try:
                    user = await self.bot.fetch_user(int(uid))
                except Exception:
                    user = None
            return user.display_name if user else f"<@{uid}>"

        lines: list[str] = []
        for rank, r in enumerate(top, start=1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"`#{rank:>2}`")
            name = await _name(r["user_id"])
            lines.append(
                f"{medal} **{name}** · Total `{_fmt_pnl(r['total'])}` "
                f"(Unreal `{_fmt_pnl(r['unrealized'])}` · Real `{_fmt_pnl(r['realized'])}`)"
            )

        embed = discord.Embed(
            title="📊 Stock Portfolio Leaderboard",
            description="\n".join(lines),
            color=0x5865F2,
        )
        embed.set_footer(text=f"{len(rows)} trader(s) · sorted by Total P/L")
        await interaction.followup.send(embed=embed)

    # ── /stock movers ────────────────────────────────────────────────────────

    @stock.command(name="movers", description="Top S&P 100 gainers and losers today")
    async def movers(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        embed, status = await build_movers_embed()
        if status == "error":
            await interaction.followup.send("Couldn't pull market data right now.", ephemeral=True)
            return
        if status == "empty":
            await interaction.followup.send(
                "No price data available — markets may not have opened yet.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StockCog(bot))
