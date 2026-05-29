"""/stock — quotes, personal portfolio tracking, leaderboard, and movers.

All commands live under a single /stock group:

- /stock lookup <ticker>            — quote a single ticker (+ your position if any).
- /stock profile [user]             — show your portfolio, or a tagged user's.
- /stock cash <amount> [action]     — set/deposit/withdraw uninvested cash.
- /stock buy <ticker> <sh> <px>     — record a buy.
- /stock sell <ticker> <sh> <px>    — record a sell (realized P/L computed).
- /stock trades [ticker] [user]     — show recent trade history.
- /stock leaderboard                — server-wide ranking by total P/L (unrealized + realized).
- /stock movers                     — S&P 100 gainers and losers today.
- /stock option buy|sell ...        — record an option contract trade (long or short).
- /stock option positions [user]    — open option positions with live P/L.

Stock trades and option trades are each their own source-of-truth log;
holdings/positions and realized P/L are derived by walking the log with
average-cost-basis accounting (signed for options, so shorts work too).
Options use a 100x multiplier and are priced from yfinance option chains,
falling back to intrinsic value once expired.
"""
import asyncio
import logging
from datetime import date, datetime, timezone

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


# ── Options ──────────────────────────────────────────────────────────────────

OPTION_MULTIPLIER = queries.OPTION_MULTIPLIER  # 100 shares per contract


def _parse_expiry(value: str) -> str:
    """Validate an expiry date string and return canonical 'YYYY-MM-DD'."""
    s = value.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"Could not parse expiry {value!r}. Use YYYY-MM-DD.")


def _fmt_expiry(expiry: str) -> str:
    """'2026-06-20' -> '6/20/26' for compact display."""
    try:
        d = date.fromisoformat(expiry)
        return f"{d.month}/{d.day}/{d.strftime('%y')}"
    except ValueError:
        return expiry


def _option_label(pos: dict) -> str:
    """e.g. 'AAPL $200C 6/20/26'."""
    tag = "C" if pos["opt_type"] == "call" else "P"
    strike = pos["strike"]
    strike_str = f"{strike:g}"
    return f"{pos['underlying']} ${strike_str}{tag} {_fmt_expiry(pos['expiry'])}"


async def fetch_option_prices(
    specs: list[dict], spots: dict[str, float]
) -> dict[tuple, dict]:
    """Price option contracts via yfinance option chains, batched by
    (underlying, expiry) so each pair costs one chain fetch.

    `specs` items need underlying/opt_type/strike/expiry. `spots` maps
    underlying -> current share price, used to value EXPIRED contracts at
    intrinsic (calls: max(0, spot-strike); puts: max(0, strike-spot)).

    Returns {(underlying, opt_type, strike, expiry): {premium: float|None,
    expired: bool}}. premium is None when the contract couldn't be priced.
    """
    import yfinance as yf

    today = datetime.now(timezone.utc).date()

    def _fetch() -> dict[tuple, dict]:
        groups: dict[tuple, list[dict]] = {}
        for s in specs:
            groups.setdefault((s["underlying"], s["expiry"]), []).append(s)

        out: dict[tuple, dict] = {}
        for (underlying, expiry), items in groups.items():
            try:
                expired = date.fromisoformat(expiry) < today
            except ValueError:
                expired = False

            chains: dict[str, object] | None = None
            if not expired:
                try:
                    tk = yf.Ticker(underlying)
                    if expiry in tk.options:
                        oc = tk.option_chain(expiry)
                        chains = {"call": oc.calls, "put": oc.puts}
                except Exception as e:
                    log.debug(f"option chain fetch failed for {underlying} {expiry}: {e}")

            for s in items:
                key = (s["underlying"], s["opt_type"], s["strike"], s["expiry"])
                premium = None
                if chains is not None:
                    df = chains[s["opt_type"]]
                    sel = df[(df["strike"] - s["strike"]).abs() < 1e-6]
                    if not sel.empty:
                        last = float(sel["lastPrice"].iloc[0])
                        bid = float(sel["bid"].iloc[0])
                        ask = float(sel["ask"].iloc[0])
                        if last > 0:
                            premium = last
                        elif bid > 0 and ask > 0:
                            premium = (bid + ask) / 2
                if premium is None and expired:
                    spot = spots.get(s["underlying"])
                    if spot is not None:
                        premium = (
                            max(0.0, spot - s["strike"])
                            if s["opt_type"] == "call"
                            else max(0.0, s["strike"] - spot)
                        )
                out[key] = {"premium": premium, "expired": expired}
        return out

    return await asyncio.get_running_loop().run_in_executor(None, _fetch)


async def _price_option_positions(open_options: list[dict]) -> dict[tuple, dict]:
    """Price a list of open option positions, fetching underlying spots only
    for the ones that have expired (needed for intrinsic value)."""
    if not open_options:
        return {}
    expired_underlyings = sorted({
        p["underlying"]
        for p in open_options
        if _is_expired(p["expiry"])
    })
    spots: dict[str, float] = {}
    if expired_underlyings:
        quotes = await fetch_quotes(expired_underlyings)
        spots = {sym: q["price"] for sym, q in quotes.items()}
    return await fetch_option_prices(open_options, spots)


def _is_expired(expiry: str) -> bool:
    try:
        return date.fromisoformat(expiry) < datetime.now(timezone.utc).date()
    except ValueError:
        return False


def _option_position_pnl(pos: dict, premium: float | None) -> dict:
    """Compute value/cost/unrealized for one open option position.

    Works for long (net>0) and short (net<0): unrealized = net*(cur-avg)*100,
    market value contribution = net*cur*100 (a short is a negative liability).
    Returns {value, cost, unrealized, priced}. When premium is None, value and
    unrealized are 0 and priced=False (cost still reflects the basis).
    """
    net = pos["net_contracts"]
    avg = pos["avg_premium"]
    cost = abs(net) * avg * OPTION_MULTIPLIER  # premium outlay (long) / credit (short)
    if premium is None:
        return {"value": 0.0, "cost": cost, "unrealized": 0.0, "priced": False}
    value = net * premium * OPTION_MULTIPLIER
    unrealized = net * (premium - avg) * OPTION_MULTIPLIER
    return {"value": value, "cost": cost, "unrealized": unrealized, "priced": True}


# ── Portfolio embed builder ─────────────────────────────────────────────────


async def _build_profile_embed(target: discord.abc.User) -> discord.Embed | None:
    """Render `target`'s portfolio: stock holdings, option positions, and cash.
    Unrealized P/L on open positions plus cumulative realized P/L from past
    sells (including fully-closed positions) across both stocks and options."""
    uid = str(target.id)
    positions = await queries.get_stock_positions_full(uid)
    option_positions = await queries.get_option_positions_full(uid)
    cash = await queries.get_stock_cash(uid)
    if not positions and not option_positions and cash <= 0:
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

    # ── Options ──
    open_options = [p for p in option_positions if not p["closed"]]
    option_realized = sum(p["realized_pnl"] for p in option_positions)
    option_prices = await _price_option_positions(open_options)

    options_value = 0.0
    options_unrealized = 0.0
    options_cost = 0.0
    option_lines: list[str] = []

    for p in open_options:
        key = (p["underlying"], p["opt_type"], p["strike"], p["expiry"])
        info = option_prices.get(key, {})
        premium = info.get("premium")
        expired = info.get("expired", _is_expired(p["expiry"]))
        pl = _option_position_pnl(p, premium)
        options_value += pl["value"]
        options_unrealized += pl["unrealized"]
        options_cost += pl["cost"]

        net = p["net_contracts"]
        side_word = "long" if net > 0 else "short"
        qty = abs(net)
        avg = p["avg_premium"]
        label = _option_label(p)
        if not pl["priced"]:
            flag = " ⚠️ expired" if expired else ""
            option_lines.append(
                f"• {label} · {side_word} {qty} @ {avg:,.2f}{flag} · *price unavailable*"
            )
            continue
        u = pl["unrealized"]
        upct = (u / pl["cost"] * 100) if pl["cost"] else 0.0
        sign = "+" if u >= 0 else ""
        emoji = "🟢" if u >= 0 else "🔴"
        flag = " ⚠️" if expired else ""
        option_lines.append(
            f"{emoji} {label}{flag} · {side_word} {qty} @ {avg:,.2f} → "
            f"**{premium:,.2f}** · P/L `{sign}{u:,.2f} ({sign}{upct:.2f}%)`"
        )

    realized_total += option_realized
    unrealized = (total_value - total_cost) + options_unrealized
    combined_cost = total_cost + options_cost
    total_pnl = unrealized + realized_total
    unrealized_pct = (unrealized / combined_cost * 100) if combined_cost else 0.0
    day_pct = (total_day_change / (total_value - total_day_change) * 100) if (total_value - total_day_change) else 0.0
    account_value = total_value + options_value + cash
    color = 0x57F287 if total_pnl >= 0 else 0xED4245

    has_options = bool(option_positions)
    sections: list[str] = []
    if lines:
        sections.append("\n".join(lines))
    if option_lines:
        sections.append("**Options**\n" + "\n".join(option_lines))
    description = "\n\n".join(sections) if sections else "_No open positions._"

    embed = discord.Embed(
        title=f"{target.display_name}'s Portfolio",
        description=description,
        color=color,
    )
    embed.add_field(name="Stock Value", value=f"`{_fmt_money(total_value)}`", inline=True)
    if has_options:
        embed.add_field(name="Options Value", value=f"`{_fmt_money(options_value)}`", inline=True)
    embed.add_field(name="Cash", value=f"`{_fmt_money(cash)}`", inline=True)
    embed.add_field(name="Account Value", value=f"`{_fmt_money(account_value)}`", inline=True)
    if not has_options:
        embed.add_field(name="Cost Basis", value=f"`{_fmt_money(total_cost)}`", inline=True)
    embed.add_field(
        name="Unrealized P/L",
        value=f"`{_fmt_change(unrealized, unrealized_pct)}`",
        inline=True,
    )
    embed.add_field(name="Realized P/L", value=f"`{_fmt_pnl(realized_total)}`", inline=True)
    embed.add_field(name="Total P/L", value=f"`{_fmt_pnl(total_pnl)}`", inline=True)
    embed.add_field(
        name="Today (stocks)",
        value=f"`{_fmt_change(total_day_change, day_pct)}`",
        inline=True,
    )
    return embed


# ── Cog ─────────────────────────────────────────────────────────────────────


class StockCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    stock = app_commands.Group(name="stock", description="Stock quotes, portfolio, and market data")
    option = app_commands.Group(
        name="option", description="Track option contracts (calls/puts)", parent=stock
    )

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

    # ── /stock cash ──────────────────────────────────────────────────────────

    @stock.command(name="cash", description="Set, deposit, or withdraw your portfolio cash")
    @app_commands.describe(
        amount="Dollar amount",
        action="set (replace), deposit (add), or withdraw (subtract). Defaults to set.",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="set", value="set"),
            app_commands.Choice(name="deposit", value="deposit"),
            app_commands.Choice(name="withdraw", value="withdraw"),
        ]
    )
    async def cash(
        self,
        interaction: discord.Interaction,
        amount: float,
        action: app_commands.Choice[str] | None = None,
    ) -> None:
        mode = action.value if action else "set"
        if amount < 0:
            await interaction.response.send_message("Amount can't be negative.", ephemeral=True)
            return
        if mode in ("deposit", "withdraw") and amount == 0:
            await interaction.response.send_message(
                f"Nothing to {mode} — amount is 0.", ephemeral=True
            )
            return

        uid = str(interaction.user.id)
        if mode == "set":
            new_balance = await queries.set_stock_cash(uid, amount)
            verb = "Set cash to"
            delta_line = ""
        else:
            delta = amount if mode == "deposit" else -amount
            new_balance = await queries.adjust_stock_cash(uid, delta)
            verb = "Deposited" if mode == "deposit" else "Withdrew"
            delta_line = f"{verb} `{_fmt_money(amount)}` · "

        await interaction.response.send_message(
            f"{delta_line}cash balance: **{_fmt_money(new_balance)}**.",
            ephemeral=True,
        )

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
    @app_commands.describe(sort="Rank by % return on capital (default) or total P/L in dollars")
    @app_commands.choices(
        sort=[
            app_commands.Choice(name="percent", value="percent"),
            app_commands.Choice(name="total", value="total"),
        ]
    )
    async def leaderboard(
        self, interaction: discord.Interaction, sort: app_commands.Choice[str] | None = None
    ) -> None:
        await interaction.response.defer()
        sort_by = sort.value if sort else "percent"

        stock_users = await queries.get_users_with_trades()
        option_users = await queries.get_users_with_option_trades()
        users = sorted(set(stock_users) | set(option_users))
        if not users:
            await interaction.followup.send("Nobody has logged any trades yet.", ephemeral=True)
            return

        # Per-user positions in one pass, then batch-fetch stock prices for the
        # union of open tickers and option prices for the union of open contracts.
        all_positions: dict[str, list[dict]] = {}
        all_options: dict[str, list[dict]] = {}
        ticker_set: set[str] = set()
        open_option_specs: list[dict] = []
        for uid in users:
            positions = await queries.get_stock_positions_full(uid)
            options = await queries.get_option_positions_full(uid)
            all_positions[uid] = positions
            all_options[uid] = options
            for p in positions:
                if not p["closed"]:
                    ticker_set.add(p["ticker"])
            for p in options:
                if not p["closed"]:
                    open_option_specs.append(p)

        quotes = await fetch_quotes(sorted(ticker_set)) if ticker_set else {}
        option_prices = await _price_option_positions(open_option_specs)

        rows: list[dict] = []
        for uid in users:
            positions = all_positions[uid]
            unrealized = 0.0
            realized = sum(p["realized_pnl"] for p in positions)
            invested = sum(p["invested"] for p in positions)
            for p in positions:
                if p["closed"]:
                    continue
                q = quotes.get(p["ticker"])
                if not q:
                    continue
                unrealized += p["shares"] * (q["price"] - p["dca_price"])
            for p in all_options[uid]:
                realized += p["realized_pnl"]
                invested += p["invested"]
                if p["closed"]:
                    continue
                key = (p["underlying"], p["opt_type"], p["strike"], p["expiry"])
                premium = option_prices.get(key, {}).get("premium")
                unrealized += _option_position_pnl(p, premium)["unrealized"]
            total = unrealized + realized
            rows.append({
                "user_id": uid,
                "unrealized": unrealized,
                "realized": realized,
                "total": total,
                "invested": invested,
                "pct": (total / invested * 100) if invested > 0 else 0.0,
            })

        sort_key = "pct" if sort_by == "percent" else "total"
        rows.sort(key=lambda r: r[sort_key], reverse=True)
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
            sign = "+" if r["pct"] >= 0 else ""
            if sort_by == "percent":
                lines.append(
                    f"{medal} **{name}** · `{sign}{r['pct']:.2f}%` · "
                    f"P/L `{_fmt_pnl(r['total'])}` on `{_fmt_money(r['invested'])}`"
                )
            else:
                lines.append(
                    f"{medal} **{name}** · P/L `{_fmt_pnl(r['total'])}` · "
                    f"`{sign}{r['pct']:.2f}%` on `{_fmt_money(r['invested'])}`"
                )

        label = "% return on capital" if sort_by == "percent" else "total P/L"
        embed = discord.Embed(
            title="📊 Stock Portfolio Leaderboard",
            description="\n".join(lines),
            color=0x5865F2,
        )
        embed.set_footer(text=f"{len(rows)} trader(s) · sorted by {label}")
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

    # ── /stock option buy|sell ────────────────────────────────────────────────

    async def _record_option(
        self,
        interaction: discord.Interaction,
        side: str,
        underlying: str,
        opt_type: app_commands.Choice[str],
        strike: float,
        expiry: str,
        contracts: int,
        premium: float,
        date: str | None,
        notes: str | None,
    ) -> None:
        sym = underlying.strip().upper()
        otype = opt_type.value
        if not sym:
            await interaction.response.send_message("Please provide an underlying ticker.", ephemeral=True)
            return
        if contracts <= 0 or premium <= 0 or strike <= 0:
            await interaction.response.send_message(
                "Contracts, premium, and strike must be > 0.", ephemeral=True
            )
            return
        try:
            expiry_iso = _parse_expiry(expiry)
            executed_at = _parse_executed_at(date)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        uid = str(interaction.user.id)

        def _match(t: dict) -> bool:
            return (
                t["opt_type"] == otype
                and abs(t["strike"] - strike) < 1e-6
                and t["expiry"] == expiry_iso
            )

        before = await queries.get_option_trades(uid, sym)
        realized_before = queries._aggregate_option_trades(
            [t for t in before if _match(t)]
        )["realized_pnl"]

        await queries.add_option_trade(
            uid, sym, otype, strike, expiry_iso, side, contracts, premium,
            executed_at, notes,
        )

        after = await queries.get_option_trades(uid, sym)
        agg = queries._aggregate_option_trades([t for t in after if _match(t)])
        realized_delta = agg["realized_pnl"] - realized_before

        net = agg["net_contracts"]
        if agg["closed"]:
            position_line = "Position closed."
        else:
            side_word = "long" if net > 0 else "short"
            position_line = (
                f"Position: **{side_word} {abs(net)}** ct @ avg **{agg['avg_premium']:,.2f}**"
            )
        realized_line = (
            f" · realized P/L `{_fmt_pnl(realized_delta)}`" if abs(realized_delta) > 1e-9 else ""
        )
        spec = _option_label({
            "underlying": sym, "opt_type": otype, "strike": strike, "expiry": expiry_iso,
        })
        await interaction.response.send_message(
            f"Recorded **{side.upper()}** {contracts} × `{spec}` @ `{premium:,.2f}`"
            f"{realized_line}.\n{position_line}",
            ephemeral=True,
        )

    _OPT_TYPE = [
        app_commands.Choice(name="call", value="call"),
        app_commands.Choice(name="put", value="put"),
    ]

    @option.command(name="buy", description="Record an option buy (long, or buy-to-close a short)")
    @app_commands.describe(
        underlying="Underlying ticker (e.g. AAPL)",
        opt_type="Call or put",
        strike="Strike price",
        expiry="Expiration date (YYYY-MM-DD)",
        contracts="Number of contracts (1 = 100 shares)",
        premium="Premium paid per share",
        date="Optional trade date (YYYY-MM-DD or YYYY-MM-DD HH:MM, UTC). Defaults to now.",
        notes="Optional note",
    )
    @app_commands.choices(opt_type=_OPT_TYPE)
    async def option_buy(
        self,
        interaction: discord.Interaction,
        underlying: str,
        opt_type: app_commands.Choice[str],
        strike: float,
        expiry: str,
        contracts: int,
        premium: float,
        date: str | None = None,
        notes: str | None = None,
    ) -> None:
        await self._record_option(
            interaction, "buy", underlying, opt_type, strike, expiry, contracts, premium, date, notes
        )

    @option.command(name="sell", description="Record an option sell (write to open, or sell-to-close)")
    @app_commands.describe(
        underlying="Underlying ticker (e.g. AAPL)",
        opt_type="Call or put",
        strike="Strike price",
        expiry="Expiration date (YYYY-MM-DD)",
        contracts="Number of contracts (1 = 100 shares)",
        premium="Premium received per share",
        date="Optional trade date (YYYY-MM-DD or YYYY-MM-DD HH:MM, UTC). Defaults to now.",
        notes="Optional note",
    )
    @app_commands.choices(opt_type=_OPT_TYPE)
    async def option_sell(
        self,
        interaction: discord.Interaction,
        underlying: str,
        opt_type: app_commands.Choice[str],
        strike: float,
        expiry: str,
        contracts: int,
        premium: float,
        date: str | None = None,
        notes: str | None = None,
    ) -> None:
        await self._record_option(
            interaction, "sell", underlying, opt_type, strike, expiry, contracts, premium, date, notes
        )

    # ── /stock option positions ───────────────────────────────────────────────

    @option.command(name="positions", description="Show open option positions (yours or a tagged user's)")
    @app_commands.describe(user="Whose options to show (defaults to you)")
    async def option_positions(
        self, interaction: discord.Interaction, user: discord.User | None = None
    ) -> None:
        await interaction.response.defer()
        target = user or interaction.user
        positions = await queries.get_option_positions_full(str(target.id))
        open_positions = [p for p in positions if not p["closed"]]
        realized_total = sum(p["realized_pnl"] for p in positions)

        if not positions:
            owner = "You have" if target.id == interaction.user.id else f"{target.display_name} has"
            await interaction.followup.send(
                f"{owner} no option positions. Open one with `/stock option buy`.",
                ephemeral=True,
            )
            return

        prices = await _price_option_positions(open_positions)
        value_total = 0.0
        unrealized_total = 0.0
        lines: list[str] = []
        for p in open_positions:
            key = (p["underlying"], p["opt_type"], p["strike"], p["expiry"])
            info = prices.get(key, {})
            premium = info.get("premium")
            expired = info.get("expired", _is_expired(p["expiry"]))
            pl = _option_position_pnl(p, premium)
            value_total += pl["value"]
            unrealized_total += pl["unrealized"]

            net = p["net_contracts"]
            side_word = "long" if net > 0 else "short"
            label = _option_label(p)
            if not pl["priced"]:
                flag = " ⚠️ expired" if expired else ""
                lines.append(
                    f"• {label} · {side_word} {abs(net)} @ {p['avg_premium']:,.2f}{flag} · *price unavailable*"
                )
                continue
            u = pl["unrealized"]
            upct = (u / pl["cost"] * 100) if pl["cost"] else 0.0
            sign = "+" if u >= 0 else ""
            emoji = "🟢" if u >= 0 else "🔴"
            flag = " ⚠️" if expired else ""
            lines.append(
                f"{emoji} {label}{flag} · {side_word} {abs(net)} @ {p['avg_premium']:,.2f} → "
                f"**{premium:,.2f}** · P/L `{sign}{u:,.2f} ({sign}{upct:.2f}%)`"
            )

        total_pnl = unrealized_total + realized_total
        color = 0x57F287 if total_pnl >= 0 else 0xED4245
        embed = discord.Embed(
            title=f"{target.display_name}'s Options",
            description="\n".join(lines) if lines else "_No open option positions._",
            color=color,
        )
        embed.add_field(name="Options Value", value=f"`{_fmt_money(value_total)}`", inline=True)
        embed.add_field(name="Unrealized P/L", value=f"`{_fmt_pnl(unrealized_total)}`", inline=True)
        embed.add_field(name="Realized P/L", value=f"`{_fmt_pnl(realized_total)}`", inline=True)
        embed.set_footer(text="⚠️ = expired, valued at intrinsic until you close it")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StockCog(bot))
