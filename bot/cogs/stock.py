"""/stock — quotes, personal portfolio tracking, leaderboard, and movers.

All commands live under a single /stock group:

- /stock lookup <ticker>            — quote a single ticker (+ your position if any).
- /stock profile [user]             — show your portfolio (Overview / Today / Graph buttons).
- /stock graph [user]               — equity curve of portfolio value over time.
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
import io
import logging
from datetime import date, datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

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


# ── Portfolio computation + views ───────────────────────────────────────────


async def _compute_portfolio(uid: str) -> dict | None:
    """Price a user's whole portfolio once and return a structured breakdown the
    Overview / Today / Graph views all share. Returns None if the user has
    nothing (no positions, no options, no cash)."""
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
    stocks: list[dict] = []

    for h in open_positions:
        sym = h["ticker"]
        shares = h["shares"]
        dca = h["dca_price"]
        cost = shares * dca
        q = quotes.get(sym)
        if not q:
            total_cost += cost
            stocks.append({"sym": sym, "shares": shares, "dca": dca, "cost": cost,
                           "available": False})
            continue
        price = q["price"]
        prev = q["prev_close"]
        value = shares * price
        pl = value - cost
        day_change = shares * (price - prev)
        total_value += value
        total_cost += cost
        total_day_change += day_change
        stocks.append({
            "sym": sym, "shares": shares, "dca": dca, "cost": cost, "available": True,
            "price": price, "prev": prev, "value": value,
            "pl": pl, "pl_pct": (pl / cost * 100) if cost else 0.0,
            "day_change": day_change,
            "day_pct": ((price - prev) / prev * 100) if prev else 0.0,
        })

    # ── Options ──
    open_options = [p for p in option_positions if not p["closed"]]
    option_realized = sum(p["realized_pnl"] for p in option_positions)
    option_prices = await _price_option_positions(open_options)

    options_value = 0.0
    options_unrealized = 0.0
    options_cost = 0.0
    options: list[dict] = []

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
        u = pl["unrealized"]
        options.append({
            "label": _option_label(p),
            "side_word": "long" if net > 0 else "short",
            "qty": abs(net), "avg": p["avg_premium"], "premium": premium,
            "priced": pl["priced"], "expired": expired,
            "unrealized": u, "cost": pl["cost"],
            "upct": (u / pl["cost"] * 100) if pl["cost"] else 0.0,
        })

    realized_total += option_realized
    unrealized = (total_value - total_cost) + options_unrealized
    combined_cost = total_cost + options_cost
    total_pnl = unrealized + realized_total
    base = total_value - total_day_change
    return {
        "stocks": stocks,
        "options": options,
        "has_options": bool(option_positions),
        "stock_value": total_value,
        "stock_cost": total_cost,
        "day_change": total_day_change,
        "day_pct": (total_day_change / base * 100) if base else 0.0,
        "options_value": options_value,
        "options_unrealized": options_unrealized,
        "options_cost": options_cost,
        "cash": cash,
        "account_value": total_value + options_value + cash,
        "realized_total": realized_total,
        "unrealized": unrealized,
        "unrealized_pct": (unrealized / combined_cost * 100) if combined_cost else 0.0,
        "total_pnl": total_pnl,
    }


def _build_overview_embed(target: discord.abc.User, d: dict) -> discord.Embed:
    """The default portfolio view: holdings list + headline totals."""
    lines: list[str] = []
    for h in d["stocks"]:
        sym = h["sym"]
        if not h["available"]:
            lines.append(f"`{sym}` — {h['shares']:g} sh @ {h['dca']:,.2f} DCA · *price unavailable*")
            continue
        sign = "+" if h["pl"] >= 0 else ""
        emoji = "🟢" if h["pl"] >= 0 else "🔴"
        lines.append(
            f"{emoji} [`{sym}`]({_yahoo_url(sym)}) · {h['shares']:g} sh @ {h['dca']:,.2f} → "
            f"**{h['price']:,.2f}** · P/L `{sign}{h['pl']:,.2f} ({sign}{h['pl_pct']:.2f}%)`"
        )

    option_lines: list[str] = []
    for o in d["options"]:
        if not o["priced"]:
            flag = " ⚠️ expired" if o["expired"] else ""
            option_lines.append(
                f"• {o['label']} · {o['side_word']} {o['qty']} @ {o['avg']:,.2f}{flag} · *price unavailable*"
            )
            continue
        u = o["unrealized"]
        sign = "+" if u >= 0 else ""
        emoji = "🟢" if u >= 0 else "🔴"
        flag = " ⚠️" if o["expired"] else ""
        option_lines.append(
            f"{emoji} {o['label']}{flag} · {o['side_word']} {o['qty']} @ {o['avg']:,.2f} → "
            f"**{o['premium']:,.2f}** · P/L `{sign}{u:,.2f} ({sign}{o['upct']:.2f}%)`"
        )

    sections: list[str] = []
    if lines:
        sections.append("\n".join(lines))
    if option_lines:
        sections.append("**Options**\n" + "\n".join(option_lines))
    description = "\n\n".join(sections) if sections else "_No open positions._"

    color = 0x57F287 if d["total_pnl"] >= 0 else 0xED4245
    embed = discord.Embed(
        title=f"{target.display_name}'s Portfolio",
        description=description,
        color=color,
    )
    embed.add_field(name="Stock Value", value=f"`{_fmt_money(d['stock_value'])}`", inline=True)
    if d["has_options"]:
        embed.add_field(name="Options Value", value=f"`{_fmt_money(d['options_value'])}`", inline=True)
    embed.add_field(name="Cash", value=f"`{_fmt_money(d['cash'])}`", inline=True)
    embed.add_field(name="Account Value", value=f"`{_fmt_money(d['account_value'])}`", inline=True)
    if not d["has_options"]:
        embed.add_field(name="Cost Basis", value=f"`{_fmt_money(d['stock_cost'])}`", inline=True)
    embed.add_field(name="Unrealized P/L",
                    value=f"`{_fmt_change(d['unrealized'], d['unrealized_pct'])}`", inline=True)
    embed.add_field(name="Realized P/L", value=f"`{_fmt_pnl(d['realized_total'])}`", inline=True)
    embed.add_field(name="Total P/L", value=f"`{_fmt_pnl(d['total_pnl'])}`", inline=True)
    embed.add_field(name="Today (stocks)",
                    value=f"`{_fmt_change(d['day_change'], d['day_pct'])}`", inline=True)
    return embed


def _build_today_embed(target: discord.abc.User, d: dict) -> discord.Embed:
    """Granular day view: each stock's move today, biggest movers first."""
    priced = [h for h in d["stocks"] if h.get("available")]
    movers = sorted(priced, key=lambda h: h["day_change"], reverse=True)

    lines: list[str] = []
    for h in movers:
        sign = "+" if h["day_change"] >= 0 else ""
        psign = "+" if h["day_pct"] >= 0 else ""
        emoji = "🟢" if h["day_change"] >= 0 else ("🔴" if h["day_change"] < 0 else "⚪")
        lines.append(
            f"{emoji} [`{h['sym']}`]({_yahoo_url(h['sym'])}) · {h['prev']:,.2f} → **{h['price']:,.2f}** "
            f"`{psign}{h['day_pct']:.2f}%` · {sign}{_fmt_money(h['day_change'])}"
        )
    unpriced = [h for h in d["stocks"] if not h.get("available")]
    for h in unpriced:
        lines.append(f"`{h['sym']}` · *price unavailable*")

    description = "\n".join(lines) if lines else "_No priced stock positions today._"
    color = 0x57F287 if d["day_change"] >= 0 else 0xED4245
    embed = discord.Embed(
        title=f"{target.display_name}'s Portfolio — Today",
        description=description,
        color=color,
    )
    base = d["stock_value"] - d["day_change"]
    embed.add_field(name="Day P/L (stocks)",
                    value=f"`{_fmt_change(d['day_change'], d['day_pct'])}`", inline=True)
    if movers:
        top = movers[0]
        embed.add_field(name="Top gainer",
                        value=f"`{top['sym']}` {('+' if top['day_pct']>=0 else '')}{top['day_pct']:.2f}%",
                        inline=True)
        bot_ = movers[-1]
        embed.add_field(name="Top loser",
                        value=f"`{bot_['sym']}` {('+' if bot_['day_pct']>=0 else '')}{bot_['day_pct']:.2f}%",
                        inline=True)
    embed.set_footer(text="Day change reflects stock positions only (options excluded).")
    return embed


async def _build_profile_embed(target: discord.abc.User) -> discord.Embed | None:
    """Back-compat thin wrapper: compute + render the overview embed."""
    data = await _compute_portfolio(str(target.id))
    if data is None:
        return None
    return _build_overview_embed(target, data)


# ── Equity curve: snapshots, backfill, rendering ─────────────────────────────


def _parse_utc(iso: str) -> datetime | None:
    """Parse an ISO timestamp to an aware UTC datetime (None on failure)."""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _shares_held_asof(trades: list[dict], cutoff: datetime) -> float:
    """Net shares held after applying all of a ticker's trades up to `cutoff`."""
    shares = 0.0
    for t in trades:
        dt = _parse_utc(t["executed_at"])
        if dt is None or dt > cutoff:
            continue
        if t["side"] == "buy":
            shares += t["shares"]
        else:
            shares -= min(t["shares"], shares)
    return shares


def _reconstruct_stock_history_sync(trades: list[dict], end_date: date) -> list[tuple]:
    """Reconstruct daily market value of stock HOLDINGS from the trade log using
    yfinance historical closes. Returns [(date, stock_value), ...]. Synchronous
    (pandas/yfinance) — run in an executor. Options and cash are not modelled."""
    import yfinance as yf
    import pandas as pd

    parsed = [(_parse_utc(t["executed_at"]), t) for t in trades]
    parsed = [(dt, t) for dt, t in parsed if dt is not None]
    if not parsed:
        return []
    first = min(dt for dt, _ in parsed).date()
    if first > end_date:
        return []

    tickers = sorted({t["ticker"] for _, t in parsed})
    closes: dict[str, dict] = {}
    for sym in tickers:
        try:
            h = yf.Ticker(sym).history(
                start=first.isoformat(),
                end=(end_date + timedelta(days=1)).isoformat(),
                interval="1d",
                auto_adjust=True,
            )
        except Exception:
            continue
        if h is None or h.empty or "Close" not in h:
            continue
        s = h["Close"]
        s.index = [ts.date() for ts in s.index]
        s = s[~s.index.duplicated(keep="last")]
        closes[sym] = s

    if not closes:
        return []

    all_dates = sorted({d for s in closes.values() for d in s.index if first <= d <= end_date})
    if not all_dates:
        return []

    # Forward-fill each ticker's close across the shared trading-day axis.
    ff: dict[str, dict] = {}
    for sym, s in closes.items():
        ff[sym] = s.reindex(all_dates).ffill().to_dict()

    by_ticker: dict[str, list[dict]] = {}
    for _, t in parsed:
        by_ticker.setdefault(t["ticker"], []).append(t)

    out: list[tuple] = []
    for d in all_dates:
        cutoff = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=timezone.utc)
        total = 0.0
        for sym in tickers:
            shares = _shares_held_asof(by_ticker.get(sym, []), cutoff)
            if shares <= 0:
                continue
            px = ff.get(sym, {}).get(d)
            if px is not None and not pd.isna(px):
                total += shares * float(px)
        out.append((d, total))
    return out


async def _ensure_backfill(uid: str, trades: list[dict]) -> None:
    """Reconstruct and store this user's historical stock-value curve once."""
    end = datetime.now(timezone.utc).date() - timedelta(days=1)  # leave today to live snapshots
    loop = asyncio.get_running_loop()
    hist = await loop.run_in_executor(None, _reconstruct_stock_history_sync, trades, end)
    rows = [
        {
            "discord_user": uid,
            "captured_at": datetime(d.year, d.month, d.day, 21, 0, 0, tzinfo=timezone.utc).isoformat(),
            "account_value": v,
            "stock_value": v,
            "options_value": 0.0,
            "cash": 0.0,
            "kind": "backfill",
        }
        for d, v in hist
    ]
    if rows:
        await queries.insert_portfolio_snapshots_bulk(rows)


async def _take_live_snapshot(uid: str, data: dict | None = None) -> None:
    """Store one live equity-curve point for a user (computes the portfolio if not given)."""
    if data is None:
        data = await _compute_portfolio(uid)
    if data is None:
        return
    await queries.insert_portfolio_snapshot(
        uid, data["account_value"], data["stock_value"], data["options_value"], data["cash"]
    )


def _render_equity_curve_png(name: str, points: list[tuple]) -> bytes:
    """Render an equity curve to a PNG (Tokyo-Night styled). Synchronous — run in executor.
    `points` is [(datetime, account_value), ...] sorted ascending."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    up = ys[-1] >= ys[0]
    line = "#9ece6a" if up else "#f7768e"

    fig, ax = plt.subplots(figsize=(9, 4.5), dpi=110)
    fig.patch.set_facecolor("#1a1b26")
    ax.set_facecolor("#1a1b26")
    ax.plot(xs, ys, color=line, linewidth=2.0)
    ax.fill_between(xs, ys, min(ys), color=line, alpha=0.12)

    ax.set_title(f"{name}'s Portfolio Value", color="#c0caf5", fontsize=14, pad=12)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#414868")
    ax.tick_params(colors="#a9b1d6", labelsize=9)
    ax.grid(True, color="#292e42", linewidth=0.6)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    fig.autofmt_xdate(rotation=0, ha="center")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


async def _build_graph(target: discord.abc.User, data: dict | None = None):
    """Build (discord.File, discord.Embed) for a user's equity curve, or (None, error_embed)."""
    uid = str(target.id)
    trades = await queries.get_stock_trades(uid)
    if trades and not await queries.has_backfill_snapshots(uid):
        try:
            await _ensure_backfill(uid, trades)
        except Exception:
            log.exception("backfill failed for %s", uid)

    snaps = await queries.get_portfolio_snapshots(uid)
    points: list[tuple] = []
    for s in snaps:
        dt = _parse_utc(s["captured_at"])
        if dt is not None:
            points.append((dt, s["account_value"]))

    # Always anchor the curve to the user's current account value.
    if data is None:
        data = await _compute_portfolio(uid)
    if data is not None:
        now = datetime.now(timezone.utc)
        if not points or (now - points[-1][0]) > timedelta(minutes=30):
            points.append((now, data["account_value"]))

    if len(points) < 2:
        err = discord.Embed(
            title=f"{target.display_name}'s Portfolio Value",
            description=(
                "Not enough history to graph yet — I need at least two data points.\n"
                "The bot snapshots portfolios hourly, so check back later, or this fills in "
                "automatically once you've held positions across more than one day."
            ),
            color=0xE0AF68,
        )
        return None, err

    png = await asyncio.get_running_loop().run_in_executor(
        None, _render_equity_curve_png, target.display_name, points
    )
    file = discord.File(io.BytesIO(png), filename="portfolio.png")

    first_v, last_v = points[0][1], points[-1][1]
    change = last_v - first_v
    pct = (change / first_v * 100) if first_v else 0.0
    color = 0x57F287 if change >= 0 else 0xED4245
    embed = discord.Embed(title=f"{target.display_name}'s Portfolio Value", color=color)
    embed.set_image(url="attachment://portfolio.png")
    span_days = (points[-1][0] - points[0][0]).days or 1
    embed.add_field(name="Now", value=f"`{_fmt_money(last_v)}`", inline=True)
    embed.add_field(name=f"Change ({span_days}d)",
                    value=f"`{_fmt_change(change, pct)}`", inline=True)
    embed.set_footer(text="Points before today are reconstructed from your stock trades "
                          "(cash/options excluded); live points include everything.")
    return file, embed


# ── Interactive profile view ─────────────────────────────────────────────────


class PortfolioView(discord.ui.View):
    """Buttons under /stock profile to switch between Overview, Today, and Graph."""

    def __init__(self, target: discord.abc.User, data: dict, invoker_id: int) -> None:
        super().__init__(timeout=180)
        self.target = target
        self.data = data
        self.invoker_id = invoker_id
        self.message: discord.Message | None = None
        self._set_active("overview")

    def _set_active(self, mode: str) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = child.custom_id == mode

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                "This isn't your portfolio menu — run `/stock profile` yourself.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Overview", style=discord.ButtonStyle.primary, custom_id="overview")
    async def overview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self._set_active("overview")
        await interaction.response.edit_message(
            embed=_build_overview_embed(self.target, self.data), attachments=[], view=self
        )

    @discord.ui.button(label="Today", style=discord.ButtonStyle.secondary, custom_id="today")
    async def today(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self._set_active("today")
        await interaction.response.edit_message(
            embed=_build_today_embed(self.target, self.data), attachments=[], view=self
        )

    @discord.ui.button(label="Graph", style=discord.ButtonStyle.secondary, custom_id="graph")
    async def graph(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self._set_active("graph")
        await interaction.response.defer()
        file, embed = await _build_graph(self.target, self.data)
        attachments = [file] if file is not None else []
        await interaction.edit_original_response(embed=embed, attachments=attachments, view=self)


# ── Leaderboard (period-rankable) ────────────────────────────────────────────

_PERIOD_LABELS = {
    "all": "All-Time",
    "ytd": "Year to Date",
    "weekly": "This Week",
    "daily": "Today",
}


async def _leaderboard_rows(users: list[str]) -> list[dict]:
    """Price every trader once and return per-user metrics for all periods:
    all-time P/L, today's stock move, current account value, and the snapshot
    baselines used for weekly / YTD gains."""
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

    now = datetime.now(timezone.utc)
    week_cut = (now - timedelta(days=7)).isoformat()
    ytd_cut = datetime(now.year, 1, 1, tzinfo=timezone.utc).isoformat()

    rows: list[dict] = []
    for uid in users:
        positions = all_positions[uid]
        unrealized = 0.0
        realized = sum(p["realized_pnl"] for p in positions)
        invested = sum(p["invested"] for p in positions)
        stock_value = 0.0
        stock_value_prev = 0.0
        day_gain = 0.0
        for p in positions:
            if p["closed"]:
                continue
            q = quotes.get(p["ticker"])
            if not q:
                continue
            unrealized += p["shares"] * (q["price"] - p["dca_price"])
            stock_value += p["shares"] * q["price"]
            stock_value_prev += p["shares"] * q["prev_close"]
            day_gain += p["shares"] * (q["price"] - q["prev_close"])

        options_value = 0.0
        for p in all_options[uid]:
            realized += p["realized_pnl"]
            invested += p["invested"]
            if p["closed"]:
                continue
            key = (p["underlying"], p["opt_type"], p["strike"], p["expiry"])
            premium = option_prices.get(key, {}).get("premium")
            pl = _option_position_pnl(p, premium)
            unrealized += pl["unrealized"]
            options_value += pl["value"]

        cash = await queries.get_stock_cash(uid)
        total = unrealized + realized
        rows.append({
            "user_id": uid,
            "total": total,
            "invested": invested,
            "pct": (total / invested * 100) if invested > 0 else 0.0,
            "account_value": stock_value + options_value + cash,
            "day_gain": day_gain,
            "day_base": stock_value_prev,
            "week_base": await queries.get_snapshot_value_asof(uid, week_cut),
            "ytd_base": await queries.get_snapshot_value_asof(uid, ytd_cut),
        })
    return rows


def _pct_str(pct: float) -> str:
    return f"{'+' if pct >= 0 else ''}{pct:.2f}%"


def _render_leaderboard_embed(rows: list[dict], period: str, names: dict[str, str]) -> discord.Embed:
    """Rank `rows` for one period and render the leaderboard embed."""
    hidden = 0
    if period == "all":
        ranked = sorted(rows, key=lambda r: r["total"], reverse=True)
    elif period == "daily":
        ranked = sorted(rows, key=lambda r: r["day_gain"], reverse=True)
    else:
        base_key = "week_base" if period == "weekly" else "ytd_base"
        eligible = [r for r in rows if r[base_key] is not None]
        hidden = len(rows) - len(eligible)
        ranked = sorted(
            eligible, key=lambda r: r["account_value"] - r[base_key], reverse=True
        )

    lines: list[str] = []
    for rank, r in enumerate(ranked[:10], start=1):
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"`#{rank:>2}`")
        name = names.get(r["user_id"], f"<@{r['user_id']}>")
        if period == "all":
            lines.append(
                f"{medal} **{name}** · P/L `{_fmt_pnl(r['total'])}` · "
                f"`{_pct_str(r['pct'])}` on `{_fmt_money(r['invested'])}`"
            )
        elif period == "daily":
            base = r["day_base"]
            pct = (r["day_gain"] / base * 100) if base else 0.0
            lines.append(f"{medal} **{name}** · `{_fmt_pnl(r['day_gain'])}` · `{_pct_str(pct)}`")
        else:
            base = r["week_base"] if period == "weekly" else r["ytd_base"]
            gain = r["account_value"] - base
            pct = (gain / base * 100) if base else 0.0
            lines.append(f"{medal} **{name}** · `{_fmt_pnl(gain)}` · `{_pct_str(pct)}`")

    color = 0x5865F2
    embed = discord.Embed(
        title=f"📊 Stock Leaderboard — {_PERIOD_LABELS[period]}",
        description="\n".join(lines) if lines else "_No data for this period yet._",
        color=color,
    )
    if period == "all":
        embed.set_footer(text=f"{len(rows)} trader(s) · all-time P/L (realized + unrealized)")
    elif period == "daily":
        embed.set_footer(text=f"{len(rows)} trader(s) · today's stock move (options/cash excluded)")
    else:
        note = f" · {hidden} hidden (no history this far back)" if hidden else ""
        embed.set_footer(text=f"{len(rows) - hidden} ranked · account-value change{note}")
    return embed


class LeaderboardView(discord.ui.View):
    """Period switcher for /stock leaderboard. Metrics are precomputed, so the
    buttons just re-rank and re-render — no refetch."""

    def __init__(self, rows: list[dict], names: dict[str, str], period: str,
                 invoker_id: int) -> None:
        super().__init__(timeout=180)
        self.rows = rows
        self.names = names
        self.invoker_id = invoker_id
        self.message: discord.Message | None = None
        self._set_active(period)

    def _set_active(self, period: str) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = child.custom_id == period

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                "This isn't your leaderboard — run `/stock leaderboard` yourself.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    async def _switch(self, interaction: discord.Interaction, period: str) -> None:
        self._set_active(period)
        await interaction.response.edit_message(
            embed=_render_leaderboard_embed(self.rows, period, self.names), view=self
        )

    @discord.ui.button(label="Today", style=discord.ButtonStyle.secondary, custom_id="daily")
    async def daily(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._switch(interaction, "daily")

    @discord.ui.button(label="Week", style=discord.ButtonStyle.secondary, custom_id="weekly")
    async def weekly(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._switch(interaction, "weekly")

    @discord.ui.button(label="YTD", style=discord.ButtonStyle.secondary, custom_id="ytd")
    async def ytd(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._switch(interaction, "ytd")

    @discord.ui.button(label="All-Time", style=discord.ButtonStyle.primary, custom_id="all")
    async def all_time(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._switch(interaction, "all")


# ── Cog ─────────────────────────────────────────────────────────────────────


class StockCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.snapshot_loop.start()

    def cog_unload(self) -> None:
        self.snapshot_loop.cancel()

    stock = app_commands.Group(name="stock", description="Stock quotes, portfolio, and market data")
    option = app_commands.Group(
        name="option", description="Track option contracts (calls/puts)", parent=stock
    )

    # ── Hourly equity-curve snapshot ─────────────────────────────────────────

    @tasks.loop(hours=1)
    async def snapshot_loop(self) -> None:
        """Record one account-value point per portfolio user, hourly, for /stock graph."""
        try:
            users = await queries.get_all_portfolio_users()
        except Exception:
            log.exception("snapshot_loop: failed to list portfolio users")
            return
        for uid in users:
            try:
                await _take_live_snapshot(uid)
            except Exception:
                log.exception("snapshot_loop: snapshot failed for %s", uid)
            await asyncio.sleep(0)  # cooperatively yield between users

    @snapshot_loop.before_loop
    async def _before_snapshot_loop(self) -> None:
        await self.bot.wait_until_ready()

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
        data = await _compute_portfolio(str(target.id))
        if data is None:
            if target.id == interaction.user.id:
                msg = "You don't have any trades yet. Add one with `/stock buy`."
            else:
                msg = f"{target.display_name} hasn't logged any trades yet."
            await interaction.followup.send(msg, ephemeral=True)
            return
        embed = _build_overview_embed(target, data)
        view = PortfolioView(target, data, interaction.user.id)
        msg = await interaction.followup.send(embed=embed, view=view)
        view.message = msg

    # ── /stock graph ─────────────────────────────────────────────────────────

    @stock.command(name="graph", description="Graph your portfolio value over time")
    @app_commands.describe(user="Whose portfolio to graph (defaults to you)")
    async def graph(
        self, interaction: discord.Interaction, user: discord.User | None = None
    ) -> None:
        await interaction.response.defer()
        target = user or interaction.user
        data = await _compute_portfolio(str(target.id))
        if data is None:
            if target.id == interaction.user.id:
                msg = "You don't have any trades yet. Add one with `/stock buy`."
            else:
                msg = f"{target.display_name} hasn't logged any trades yet."
            await interaction.followup.send(msg, ephemeral=True)
            return
        file, embed = await _build_graph(target, data)
        if file is None:
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        await interaction.followup.send(embed=embed, file=file)

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

    async def _resolve_names(self, uids: list[str]) -> dict[str, str]:
        """Resolve discord user IDs to display names (cached + fetch fallback)."""
        names: dict[str, str] = {}
        for uid in uids:
            user = self.bot.get_user(int(uid)) if uid.isdigit() else None
            if user is None and uid.isdigit():
                try:
                    user = await self.bot.fetch_user(int(uid))
                except Exception:
                    user = None
            names[uid] = user.display_name if user else f"<@{uid}>"
        return names

    @stock.command(name="leaderboard", description="Server-wide portfolio leaderboard")
    @app_commands.describe(period="Ranking window — switch any time with the buttons")
    @app_commands.choices(
        period=[
            app_commands.Choice(name="all-time", value="all"),
            app_commands.Choice(name="ytd", value="ytd"),
            app_commands.Choice(name="weekly", value="weekly"),
            app_commands.Choice(name="daily", value="daily"),
        ]
    )
    async def leaderboard(
        self, interaction: discord.Interaction, period: app_commands.Choice[str] | None = None
    ) -> None:
        await interaction.response.defer()
        sel = period.value if period else "all"

        stock_users = await queries.get_users_with_trades()
        option_users = await queries.get_users_with_option_trades()
        users = sorted(set(stock_users) | set(option_users))
        if not users:
            await interaction.followup.send("Nobody has logged any trades yet.", ephemeral=True)
            return

        rows = await _leaderboard_rows(users)
        names = await self._resolve_names([r["user_id"] for r in rows])
        view = LeaderboardView(rows, names, sel, interaction.user.id)
        embed = _render_leaderboard_embed(rows, sel, names)
        msg = await interaction.followup.send(embed=embed, view=view)
        view.message = msg

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
