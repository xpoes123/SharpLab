"""/stock — quotes, personal portfolio tracking, leaderboard, and movers.

All commands live under a single /stock group:

- /stock lookup <ticker>            — quote a single ticker (+ your position if any).
- /stock profile [user]             — portfolio (Overview / Today / Graph / Allocation / Risk).
- /stock graph [user]               — equity curve vs S&P 500 (are you beating the market?).
- /stock server                     — server-wide portfolio (Overview / Today / Graph).
- /stock cash <amount> [action]     — set/deposit/withdraw uninvested cash.
- /stock buy <ticker> <sh> <px>     — record a buy (stocks, ETFs, or crypto: BTC, ETH, …).
- /stock sell <ticker> <sh> <px>    — record a sell (realized P/L computed).
- /stock short <ticker> <sh> <px>   — open/add a short (alias for an opening sell).
- /stock cover <ticker> <sh> <px>   — buy to cover an open short (alias for a closing buy).
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
import json
import logging
import time
from collections import deque
from datetime import date, datetime, timedelta, timezone

import httpx
import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.cogs._movers_helpers import build_movers_embed
from db import queries

log = logging.getLogger(__name__)

# ── Price-monitor config ─────────────────────────────────────────────────────

DEFAULT_STOCK_ALERTS_CHANNEL_ID = 1510100250411536495  # auto-swing alerts land here
MONITOR_POLL_MINUTES = 5
SWING_THRESHOLD_PCT = 10.0  # auto-alert when a held ticker moves this % on the day
SWING_MIN_PRICE = 1.0  # skip swing alerts for sub-$1 tickers — penny stocks trip the % on meaningless ticks
DIVIDEND_LOOKBACK_DAYS = 10  # only pay dividends whose ex-date is within this many days (no back-paying history)
_ALERTS_CHANNEL_SETTING = "stock_alerts_channel"
_DIGEST_SETTING = "last_stock_digest"  # ET date of the last daily portfolio report
_EARNINGS_SETTING = "last_earnings_digest"  # ET date of the last earnings-on-deck digest
_SWING_STATE_SETTING = "swing_alert_state"  # persisted {uid:ticker -> {date, band}} so restarts don't re-alert

# Rohan's picks: ping the "rohan stock picks" role whenever he trades.
ROHAN_USER_ID = "688444350325456939"
_ROHAN_PICKS_CHANNEL_SETTING = "rohan_picks_channel"


def _manual_triggered(direction: str, target: float, price: float) -> bool:
    """True when an above/below monitor's condition is met."""
    if direction == "above":
        return price >= target
    return price <= target


def _swing_pct(price: float, prev_close: float | None) -> float:
    """Daily % change vs previous close (0 when prev_close is missing)."""
    if not prev_close:
        return 0.0
    return (price - prev_close) / prev_close * 100.0


def _period_return_pct(base: float | None, change: float) -> float | None:
    """Percent return over a period, or None when the base is non-positive. A
    negative/zero starting equity (overdrafted cash, net-short book) makes a
    percentage meaningless and — worse — flips its sign, so a real gain reads as a
    huge negative %. `if base` isn't enough: a negative base is truthy."""
    return (change / base * 100) if base and base > 0 else None


def _market_today() -> str:
    """Today's date in US/Eastern (the market's calendar day). Used for the daily
    swing-alert dedup so the reset lands at ET midnight — NOT at UTC midnight, which
    is 8pm ET and would re-fire every already-alerted swing right after the close."""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def _market_day_start_utc() -> str:
    """UTC ISO timestamp (…+00:00) of the start of the current US/Eastern calendar
    day — the boundary for 'realized today', aligned with the ET market day."""
    from zoneinfo import ZoneInfo
    start_et = datetime.now(ZoneInfo("America/New_York")).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return start_et.astimezone(timezone.utc).isoformat()


def _display_ticker(symbol: str) -> str:
    """Strip Yahoo's -USD suffix for crypto display (BTC-USD -> BTC)."""
    return symbol[:-4] if symbol.endswith("-USD") else symbol


def _yahoo_url(ticker: str) -> str:
    return f"https://finance.yahoo.com/quote/{ticker}"


async def _check_achievements(interaction: discord.Interaction) -> None:
    """Evaluate achievements after a trade and announce any unlocks in-channel
    (best-effort)."""
    try:
        from bot.cogs.progression import evaluate_user_achievements, announce_achievements
        uid = str(interaction.user.id)
        newly = await evaluate_user_achievements(uid)
        if newly:
            await announce_achievements(interaction.client, uid, newly, interaction.channel)
    except Exception:
        log.debug("achievement check failed for %s", interaction.user.id, exc_info=True)


async def _award_trade_xp(interaction: discord.Interaction) -> None:
    """Grant XP + capped casino coins for logging a stock/option/crypto trade (best-effort)."""
    try:
        from bot.cogs.progression import award_xp, XP_STOCK
        await award_xp(interaction.client, str(interaction.user.id), XP_STOCK, interaction.channel)
    except Exception:
        log.debug("trade xp award failed for %s", interaction.user.id, exc_info=True)
    try:
        from datetime import datetime, timezone
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        await queries.grant_activity_reward(str(interaction.user.id), "trade_log", day)
    except Exception:
        log.debug("trade coin reward failed for %s", interaction.user.id, exc_info=True)


# Bare crypto tickers — typed without a suffix (BTC) they map to Yahoo's BTC-USD
# form. This list is deliberately limited to coins that do NOT collide with a real
# US-listed company/ETF, because this is a stock-first brokerage: a bare ticker
# that's a real security must resolve to that security, not silently to a coin.
# (e.g. LTC=LTC Properties, SUI=Sun Communities, VET=Vermilion, AXS=Axis Capital,
# BCH=Banco de Chile, ATOM=Atomera, LINK=Interlink, APT=Alpha Pro Tech, NEAR/ARB=
# real ETFs — all live as stocks; use the explicit `LTC-USD` form for the coin.)
# BTC/ETH/XRP/MANA are kept because their only equity match is a crypto fund, so a
# bare BTC clearly means Bitcoin. Don't re-add a ticker here without checking it
# isn't a tradeable equity — doing so traps any existing position under that symbol
# (the sell path normalizes to -USD and then can't find the bare-ticker holding).
CRYPTO_SYMBOLS = {
    "BTC", "ETH", "SOL", "DOGE", "XRP", "ADA", "AVAX", "DOT", "MATIC", "SHIB",
    "UNI", "XLM", "OP", "PEPE", "BNB", "TON", "INJ", "FIL", "ICP", "HBAR",
    "ALGO", "AAVE", "MKR", "GRT", "SAND", "MANA", "FTM", "RNDR", "TIA",
}


def _normalize_symbol(raw: str) -> str:
    """Canonicalise a user-entered symbol. Bare crypto tickers (BTC, ETH, …) and
    anything already ending in -USD become Yahoo's `BTC-USD` form; everything
    else — including tickers that are real companies sharing a coin's name — is
    treated as a normal stock/ETF ticker."""
    # Discord autocomplete doesn't constrain input: the field can hold a choice's
    # label ("AMZU — 226 sh") and submit it verbatim. A real ticker has no spaces,
    # so take the first token — kills the whole label-leak class.
    s = (raw.strip().upper().split() or [""])[0]
    if not s or s.endswith("-USD"):
        return s
    if s in CRYPTO_SYMBOLS:
        return f"{s}-USD"
    return s


def _fmt_money(amount: float, currency: str = "USD") -> str:
    return f"{amount:,.2f} {currency}"


def _fmt_price(price: float, currency: str = "") -> str:
    """Per-share price with adaptive precision so sub-dollar prices don't
    collapse to 0.00. >=$1 (or zero): 2dp; >=$0.01: 4dp; otherwise enough
    decimals to show the value (trailing zeros trimmed). e.g. 510.69, 0.0115,
    0.0003. Optional currency suffix."""
    a = abs(price)
    if a >= 1 or a == 0:
        s = f"{price:,.2f}"
    elif a >= 0.01:
        s = f"{price:.4f}"
    else:
        s = f"{price:.8f}".rstrip("0").rstrip(".")
    return f"{s} {currency}".rstrip()


def _fmt_change(change: float, pct: float) -> str:
    sign = "+" if change >= 0 else ""
    return f"{sign}{change:,.2f} ({sign}{pct:.2f}%)"


def _fmt_pnl(amount: float) -> str:
    sign = "+" if amount >= 0 else ""
    return f"{sign}{amount:,.2f}"


def _fmt_position(holding: dict | None) -> str:
    """One-line position summary for /stock buy|sell confirmations. Handles
    shorts (negative shares) by labelling them and quoting the average entry."""
    if not holding:
        return "Position closed."
    sh = holding["shares"]
    if sh < 0:
        return f"Position: **short {abs(sh):g}** sh @ avg **{holding['dca_price']:,.2f}**"
    return f"Position: **{sh:g}** sh @ DCA **{holding['dca_price']:,.2f}**"


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


# ── Quote cache ──────────────────────────────────────────────────────────────
# yfinance calls are the slow part of every /stock command, and the same tickers
# get re-fetched constantly when people spam /profile, /leaderboard, /server, …
# A short in-memory TTL cache makes repeat calls near-instant. Prices going a
# few seconds stale is fine for a portfolio tracker. The cache is only ever
# touched from the event loop (the executor just does the network fetch), so
# the plain dicts need no locking.
_QUOTE_TTL = 45.0          # seconds — single + batch quotes
_OPTION_PRICE_TTL = 120.0  # seconds — option-chain premiums (slower to fetch)
_quote_cache: dict[str, tuple[float, dict]] = {}
_quote1_cache: dict[str, tuple[float, dict]] = {}
_quote_ext_cache: dict[str, tuple[float, dict]] = {}  # batch quotes WITH extended-hours
_option_price_cache: dict[tuple, tuple[float, dict]] = {}


def _cache_get(cache: dict, key, ttl: float):
    ent = cache.get(key)
    if ent is not None and (time.monotonic() - ent[0]) < ttl:
        return ent[1]
    return None


async def fetch_quote(ticker: str) -> dict:
    """Fetch latest price + previous close for a single ticker via yfinance.

    Returns {price, prev_close, currency, name, extended}. `extended` is None
    or a dict with {session: 'post'|'pre', price, change, pct} when the market
    is currently outside regular hours and Yahoo has an extended-hours print.
    Raises on missing data. Cached for _QUOTE_TTL seconds.
    """
    import yfinance as yf

    cached = _cache_get(_quote1_cache, ticker, _QUOTE_TTL)
    if cached is not None:
        return cached

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

    quote = await asyncio.get_running_loop().run_in_executor(None, _fetch)
    _quote1_cache[ticker] = (time.monotonic(), quote)
    return quote


async def fetch_quotes(tickers: list[str], *, extended: bool = False) -> dict[str, dict]:
    """Fetch quotes for many tickers, skipping ones that fail. Per-ticker results
    are cached for _QUOTE_TTL seconds, so only cache-misses hit yfinance.

    extended=True also reads the pre/post-market print (an "extended" field with
    {session, price, pct}) via the slower full .info call — only use it for a single
    user's holdings, never the whole-server leaderboard. Cached separately."""
    import yfinance as yf

    cache = _quote_ext_cache if extended else _quote_cache
    out: dict[str, dict] = {}
    misses: list[str] = []
    for sym in tickers:
        hit = _cache_get(cache, sym, _QUOTE_TTL)
        if hit is not None:
            out[sym] = hit
        else:
            misses.append(sym)
    if not misses:
        return out

    def _fetch() -> dict[str, dict]:
        fetched: dict[str, dict] = {}
        for sym in misses:
            try:
                tk = yf.Ticker(sym)
                info = tk.fast_info
                get = info.get if hasattr(info, "get") else lambda k, d=None: getattr(info, k, d)
                price = get("last_price")
                prev = get("previous_close")
                currency = get("currency") or "USD"
                if price is None or prev is None:
                    # fast_info is empty for crypto (BTC-USD etc.); fall back to
                    # recent daily closes for both the price and a real prior close.
                    hist = tk.history(period="5d")
                    if hist.empty:
                        continue
                    price = float(hist["Close"].iloc[-1])
                    prev = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else price
                q = {"price": float(price), "prev_close": float(prev), "currency": currency}
                if extended:
                    try:
                        full = tk.info
                        rmp = full.get("regularMarketPrice")
                        if rmp is not None:
                            q["price"] = float(rmp)   # keep "price" = regular session
                        ms = full.get("marketState")
                        pre, post = full.get("preMarketPrice"), full.get("postMarketPrice")
                        ext_price, session = None, None
                        if ms in ("PRE", "PREPRE") and pre is not None:
                            ext_price, session = float(pre), "pre"
                        elif ms not in (None, "REGULAR") and post is not None:
                            ext_price, session = float(post), "post"
                        if ext_price and q["price"]:
                            q["extended"] = {"session": session, "price": ext_price,
                                             "pct": (ext_price - q["price"]) / q["price"] * 100}
                    except Exception:
                        pass
                fetched[sym] = q
            except Exception as e:
                log.warning(f"fetch_quotes: {sym} failed: {e}")
        return fetched

    fetched = await asyncio.get_running_loop().run_in_executor(None, _fetch)
    now = time.monotonic()
    for sym, q in fetched.items():
        cache[sym] = (now, q)
        out[sym] = q
    return out


PRICE_TOLERANCE = 0.15   # a "now" trade's price must be within 15% of the live quote


def price_within_tolerance(price: float, live: float, tol: float = PRICE_TOLERANCE) -> bool:
    """True if `price` is within `tol` of `live` (pure — easy to test)."""
    return live > 0 and abs(price - live) / live <= tol


async def live_price_check(symbol: str, price: float, *, tol: float = PRICE_TOLERANCE) -> tuple[bool, float | None]:
    """Confirm a 'now' trade's entered price is near the live market price — catches
    stale/fat-fingered prices that would inflate gains (e.g. logging a buy at a months-old
    price). Returns (ok, live_price); ok=True when no quote is available (can't validate)."""
    q = (await fetch_quotes([symbol])).get(symbol)
    live = q.get("price") if q else None
    if not live:
        return True, None
    return price_within_tolerance(price, live, tol), live


async def live_option_check(underlying: str, opt_type: str, strike: float, expiry: str,
                            premium: float, *, tol: float = PRICE_TOLERANCE) -> tuple[bool, str | None]:
    """Sanity-check a 'now' option trade. yfinance's strike list is INCOMPLETE (it caps
    far-OTM strikes — e.g. it lacks HPE's real $65 call), so we do NOT reject a strike
    just because yfinance is missing it. Instead enforce a model-free arbitrage bound on
    the premium: a call can never cost more than the stock, a put more than the strike —
    which catches fat-fingers like a $100 premium on a $65 call. Plus a valid, future,
    ISO expiry. Returns (ok, reason); (True, None) when we can't fetch a spot."""
    from datetime import date as _date
    try:
        exp = _date.fromisoformat(expiry)
    except ValueError:
        return False, f"expiry `{expiry}` must be YYYY-MM-DD"
    if exp < datetime.now(timezone.utc).date():
        return False, f"expiry {expiry} is in the past"
    q = (await fetch_quotes([underlying])).get(underlying)
    spot = q.get("price") if q else None
    if not spot:
        return True, None
    cap = spot if opt_type == "call" else strike
    if premium > cap * 1.10:
        what = "the stock price" if opt_type == "call" else "the strike"
        return False, f"premium ${premium:g} can't exceed {what} (~${cap:,.2f}) — check the premium"
    return True, None


# ── Per-ticker period changes (1D / 1W / 1M / YTD / 1Y) ───────────────────────
_HIST_TTL = 900.0  # 15 min — daily closes move slowly
_hist_cache: dict[str, tuple[float, list]] = {}


def _fetch_history_blocking(sym: str) -> list:
    """1y of daily closes as [(date_iso, close)] ascending. Blocking — run in executor."""
    import yfinance as yf
    try:
        h = yf.Ticker(sym).history(period="1y", interval="1d")
        return [(idx.date().isoformat(), float(c)) for idx, c in h["Close"].items() if c == c]
    except Exception:
        return []


async def _ticker_history(sym: str) -> list:
    hit = _hist_cache.get(sym)
    if hit is not None and (time.monotonic() - hit[0]) < _HIST_TTL:
        return hit[1]
    series = await asyncio.get_running_loop().run_in_executor(None, _fetch_history_blocking, sym)
    _hist_cache[sym] = (time.monotonic(), series)
    return series


def _close_on_or_before(series: list, cutoff_iso: str) -> float | None:
    prev = None
    for d, c in series:
        if d <= cutoff_iso:
            prev = c
        else:
            break
    return prev


# ── Ticker metadata (sector / quote type / beta), cached ─────────────────────

_TICKER_META_TTL = timedelta(days=14)


def _fetch_ticker_info(tickers: list[str]) -> dict[str, dict]:
    """Blocking yfinance .info pull for several tickers. Run in an executor."""
    import yfinance as yf

    out: dict[str, dict] = {}
    for sym in tickers:
        try:
            info = yf.Ticker(sym).info
            out[sym] = {
                "quote_type": info.get("quoteType"),
                "sector": info.get("sector"),
                "category": info.get("category"),
                "beta": info.get("beta"),
                "name": info.get("longName") or info.get("shortName"),
            }
        except Exception as e:
            log.debug(f"ticker info fetch failed for {sym}: {e}")
    return out


async def get_ticker_metadata(tickers: list[str]) -> dict[str, dict]:
    """{ticker: {quote_type, sector, category, beta, name}} for tickers, served
    from the ticker_meta cache and refreshed from yfinance when missing or older
    than _TICKER_META_TTL (.info is slow, so we cache aggressively)."""
    wanted = sorted({t.upper() for t in tickers})
    if not wanted:
        return {}
    cached = await queries.get_ticker_meta_bulk(wanted)
    now = datetime.now(timezone.utc)
    stale: list[str] = []
    for t in wanted:
        row = cached.get(t)
        upd = _parse_utc(row["updated_at"]) if row else None
        if row is None or upd is None or (now - upd) > _TICKER_META_TTL:
            stale.append(t)

    if stale:
        fetched = await asyncio.get_running_loop().run_in_executor(
            None, _fetch_ticker_info, stale
        )
        for t, info in fetched.items():
            # Only persist a real hit (quote_type present); leave failures
            # uncached so a transient error isn't sticky for two weeks.
            if info.get("quote_type"):
                await queries.upsert_ticker_meta(
                    t, info["quote_type"], info["sector"], info["category"],
                    info["beta"], info["name"],
                )
            cached[t] = {"ticker": t, **info}

    out: dict[str, dict] = {}
    for t in wanted:
        row = cached.get(t)
        if row is None:
            continue
        out[t] = {
            "quote_type": row.get("quote_type"),
            "sector": row.get("sector"),
            "category": row.get("category"),
            "beta": row.get("beta"),
            "name": row.get("name"),
        }
    return out


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


async def _holdings_autocomplete(interaction: "discord.Interaction", current: str):
    """Suggest the user's own open stock positions — turns /stock sell's ticker into
    a dropdown of what you actually hold (with share counts)."""
    try:
        positions = await queries.get_stock_positions_full(str(interaction.user.id))
    except Exception:
        return []
    cur = (current or "").upper().strip()
    out = []
    for p in positions:
        sh = p.get("shares") or 0
        if p.get("closed") or abs(sh) <= 1e-9:
            continue
        t = p["ticker"]
        if cur and cur not in t:
            continue
        label = f"{t} — short {abs(sh):g} sh" if sh < 0 else f"{t} — {sh:g} sh"
        out.append(app_commands.Choice(name=label, value=t))
    return out[:25]


def _bs_price(opt_type: str, S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes price per share for a European call/put — used to ESTIMATE
    contracts the data provider doesn't list (yfinance caps far-OTM strikes)."""
    import math
    from statistics import NormalDist
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return round(max(0.0, (S - K) if opt_type == "call" else (K - S)), 2)
    N = NormalDist().cdf
    d1 = (math.log(S / K) + (r + sigma * sigma / 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    val = (S * N(d1) - K * math.exp(-r * T) * N(d2)) if opt_type == "call" \
        else (K * math.exp(-r * T) * N(-d2) - S * N(-d1))
    return round(max(0.0, val), 2)


def _bs_greeks(opt_type: str, S: float, K: float, T: float, r: float, sigma: float) -> dict | None:
    """Per-share Black-Scholes Greeks for a European call/put. Returns
    {delta, gamma, theta, vega} or None when inputs are degenerate (expired /
    no vol). theta is per CALENDAR DAY, vega is per 1 vol point (1%) — the units a
    broker shows. delta is per share (calls 0..1, puts -1..0)."""
    import math
    from statistics import NormalDist
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return None
    nd = NormalDist()
    N, pdf = nd.cdf, nd.pdf
    srt = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r + sigma * sigma / 2) * T) / srt
    d2 = d1 - srt
    disc = math.exp(-r * T)
    if opt_type == "call":
        delta = N(d1)
        theta = (-(S * pdf(d1) * sigma) / (2 * math.sqrt(T)) - r * K * disc * N(d2))
    else:
        delta = N(d1) - 1
        theta = (-(S * pdf(d1) * sigma) / (2 * math.sqrt(T)) + r * K * disc * N(-d2))
    gamma = pdf(d1) / (S * srt)
    vega = S * pdf(d1) * math.sqrt(T)
    return {"delta": delta, "gamma": gamma, "theta": theta / 365.0, "vega": vega / 100.0}


def _implied_vol(opt_type: str, premium: float, S: float, K: float, T: float, r: float) -> float | None:
    """Back out implied vol from a market premium by bisection on _bs_price (price is
    monotonic in sigma). Used when the data provider's chain IV is missing/garbage
    (common for deep-ITM LEAPS). None if the premium is below intrinsic / unsolvable."""
    if premium <= 0 or T <= 0 or S <= 0 or K <= 0:
        return None
    lo, hi = 1e-4, 5.0
    if _bs_price(opt_type, S, K, T, r, hi) < premium:   # premium richer than 500% vol → give up
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if _bs_price(opt_type, S, K, T, r, mid) < premium:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _fmt_market_cap(mc: float) -> str:
    if mc >= 1e12:
        return f"${mc / 1e12:.1f}T"
    if mc >= 1e9:
        return f"${mc / 1e9:.0f}B"
    if mc >= 1e6:
        return f"${mc / 1e6:.0f}M"
    return ""


async def fetch_earnings_calendar(date_iso: str) -> list[dict]:
    """Every company reporting on `date_iso` (YYYY-MM-DD), from Nasdaq's public
    calendar. Returns [{symbol, name, time, mc}] — `time` is 'time-after-hours'
    (AMC), 'time-pre-market' (BMO), or 'time-not-supplied'. [] on failure."""
    url = f"https://api.nasdaq.com/api/calendar/earnings?date={date_iso}"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0",
                                               "Accept": "application/json"})
        if r.status_code != 200:
            return []
        rows = ((r.json().get("data") or {}).get("rows")) or []
    except Exception:
        log.debug("nasdaq earnings fetch failed for %s", date_iso, exc_info=True)
        return []
    out = []
    for row in rows:
        sym = (row.get("symbol") or "").strip().upper()
        if not sym:
            continue
        try:
            mc = float((row.get("marketCap") or "0").replace("$", "").replace(",", ""))
        except (ValueError, AttributeError):
            mc = 0.0
        out.append({"symbol": sym, "name": (row.get("name") or "").strip(),
                    "time": row.get("time") or "", "mc": mc})
    return out


async def fetch_earnings_result(sym: str, report_date: str) -> dict | None:
    """If `sym` reported earnings on `report_date` (ET, YYYY-MM-DD) and the actual EPS
    is out, return {eps_actual, eps_estimate, surprise_pct, when}; else None (not
    reported / numbers not posted yet / no data). yfinance's earnings_dates is indexed
    by ET-localized report timestamps."""
    import math

    import yfinance as yf

    def _fetch() -> dict | None:
        try:
            df = yf.Ticker(sym).get_earnings_dates(limit=8)
        except Exception:
            return None
        if df is None or df.empty:
            return None
        for ts, row in df.iterrows():
            try:
                if ts.date().isoformat() != report_date:
                    continue
            except Exception:
                continue
            rep = row.get("Reported EPS")
            if rep is None or (isinstance(rep, float) and math.isnan(rep)):
                return None  # reported today but the actual isn't published yet

            def _num(v):
                return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)

            hour = ts.hour if hasattr(ts, "hour") else 16
            return {
                "eps_actual": float(rep),
                "eps_estimate": _num(row.get("EPS Estimate")),
                "surprise_pct": _num(row.get("Surprise(%)")),
                "when": "after close" if hour >= 12 else "before open",
            }
        return None

    return await asyncio.to_thread(_fetch)


# ── Dividends ────────────────────────────────────────────────────────────────
_DIVIDEND_TTL = 6 * 3600.0  # seconds — the payout loop runs every 6h; cache a full cycle
_dividend_cache: dict[str, tuple[float, list]] = {}  # sym -> (monotonic, [(ex_date_iso, per_share)])


async def fetch_dividends(sym: str) -> list[tuple[str, float]]:
    """Recent cash dividends for `sym` via yfinance's `.dividends` Series (indexed by
    tz-aware ex-dates → $/share). Returns [(ex_date_iso, per_share), ...] for ex-dates
    within the last ~30 days, newest last. Cached per-ticker for _DIVIDEND_TTL so a
    ticker held by many users is fetched once per cycle. Never raises — [] on any error
    or for tickers with no dividends (crypto/ETF/growth names)."""
    import math

    import yfinance as yf

    cached = _cache_get(_dividend_cache, sym, _DIVIDEND_TTL)
    if cached is not None:
        return cached

    def _fetch() -> list[tuple[str, float]]:
        try:
            ser = yf.Ticker(sym).dividends
        except Exception:
            return []
        if ser is None or len(ser) == 0:
            return []
        # Only look back a month — the payout loop only pays the last 10 days, and
        # yfinance can return decades of history we never want to walk.
        cutoff = date.today() - timedelta(days=30)
        out: list[tuple[str, float]] = []
        for ts, amt in ser.items():
            try:
                ex = ts.date()
            except Exception:
                continue
            if ex < cutoff:
                continue
            try:
                a = float(amt)
            except (TypeError, ValueError):
                continue
            if math.isnan(a) or a <= 0:
                continue
            out.append((ex.isoformat(), a))
        out.sort(key=lambda x: x[0])
        return out

    result = await asyncio.to_thread(_fetch)
    _dividend_cache[sym] = (time.monotonic(), result)
    return result


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
    Per-contract results are cached for _OPTION_PRICE_TTL seconds.
    """
    import yfinance as yf

    today = datetime.now(timezone.utc).date()

    out: dict[tuple, dict] = {}
    misses: list[dict] = []
    for s in specs:
        key = (s["underlying"], s["opt_type"], s["strike"], s["expiry"])
        hit = _cache_get(_option_price_cache, key, _OPTION_PRICE_TTL)
        if hit is not None:
            out[key] = hit
        else:
            misses.append(s)
    if not misses:
        return out

    def _fetch() -> dict[tuple, dict]:
        groups: dict[tuple, list[dict]] = {}
        for s in misses:
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
                iv = None
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
                        ivv = float(sel["impliedVolatility"].iloc[0])
                        if 0.05 <= ivv <= 5.0:
                            iv = ivv
                estimated = False
                if premium is None and not expired:
                    spot = spots.get(s["underlying"])
                    if spot:
                        # strike not listed by yfinance — estimate via Black-Scholes,
                        # using the nearest listed strike's implied vol when we have it.
                        iv = 0.5
                        if chains is not None:
                            cdf = chains[s["opt_type"]]
                            if not cdf.empty:
                                near = cdf.iloc[(cdf["strike"] - s["strike"]).abs().argsort()[:1]]
                                ivv = float(near["impliedVolatility"].iloc[0]) if not near.empty else 0.0
                                if 0.05 <= ivv <= 3.0:
                                    iv = ivv
                        try:
                            T = max(0, (date.fromisoformat(s["expiry"]) - today).days) / 365.0
                        except ValueError:
                            T = 0.0
                        premium = _bs_price(s["opt_type"], spot, s["strike"], T, 0.045, iv)
                        estimated = True
                if premium is None and expired:
                    spot = spots.get(s["underlying"])
                    if spot is not None:
                        premium = (
                            max(0.0, spot - s["strike"])
                            if s["opt_type"] == "call"
                            else max(0.0, s["strike"] - spot)
                        )
                out[key] = {"premium": premium, "expired": expired, "estimated": estimated,
                            "iv": iv, "spot": spots.get(s["underlying"])}
        return out

    fetched = await asyncio.get_running_loop().run_in_executor(None, _fetch)
    now = time.monotonic()
    for key, val in fetched.items():
        _option_price_cache[key] = (now, val)
        out[key] = val
    return out


def effective_price(q: dict) -> float:
    """The price to value a position at: the pre/post-market print when one is
    live (e.g. an earnings move after the close), else the regular-session price.
    yfinance's regular `lastPrice` lags the extended session — valuing an option
    off the stale spot makes a contract look dead even when the underlying ripped."""
    ext = q.get("extended")
    if ext and ext.get("price"):
        return ext["price"]
    return q["price"]


async def _price_option_positions(open_options: list[dict]) -> dict[tuple, dict]:
    """Price a list of open option positions, fetching underlying spots only
    for the ones that have expired (needed for intrinsic value)."""
    if not open_options:
        return {}
    # Spots for every underlying — needed for expired intrinsic value AND for the
    # Black-Scholes estimate of contracts yfinance doesn't list. extended=True so a
    # post-earnings move feeds the BS spot (a deep-OTM strike is very spot-sensitive).
    underlyings = sorted({p["underlying"] for p in open_options})
    quotes = await fetch_quotes(underlyings, extended=True)
    spots = {sym: effective_price(q) for sym, q in quotes.items()}
    return await fetch_option_prices(open_options, spots)


def _is_expired(expiry: str) -> bool:
    try:
        return date.fromisoformat(expiry) < datetime.now(timezone.utc).date()
    except ValueError:
        return False


def option_greeks(pos: dict, info: dict) -> dict | None:
    """Greeks for an open option position from a fetch_option_prices() info row.
    Returns {iv, delta, theta, dte} for display (delta per share; theta = $/day for
    ONE contract), or None when the contract can't be priced (expired / no IV)."""
    spot, iv = info.get("spot"), info.get("iv")
    if not spot:
        return None
    try:
        dte = max(0, (date.fromisoformat(pos["expiry"]) - datetime.now(timezone.utc).date()).days)
    except ValueError:
        return None
    # Chain IV is often missing/garbage for deep-ITM LEAPS — back it out of the premium.
    if not iv and info.get("premium"):
        iv = _implied_vol(pos["opt_type"], info["premium"], spot, pos["strike"], dte / 365.0, 0.045)
        # Illiquid deep-ITM LEAPS quote a stale lastPrice that inverts to absurd vol;
        # don't show 300%+ "IV" — drop the Greeks rather than mislead.
        if iv and iv > 2.5:
            iv = None
    if not iv:
        return None
    g = _bs_greeks(pos["opt_type"], spot, pos["strike"], dte / 365.0, 0.045, iv)
    if not g:
        return None
    return {"iv": iv, "delta": g["delta"], "theta": g["theta"] * OPTION_MULTIPLIER, "dte": dte}


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

    # extended=True so after-hours / pre-market prints value the portfolio when the
    # regular session is closed (e.g. a stock that ripped on post-close earnings).
    quotes = await fetch_quotes([p["ticker"] for p in open_positions], extended=True) if open_positions else {}

    total_value = 0.0
    total_cost = 0.0          # signed (shares·dca) — feeds the unrealized identity
    total_gross_cost = 0.0    # |shares|·dca — % denominator (shorts have negative cost)
    total_day_change = 0.0
    stocks: list[dict] = []

    for h in open_positions:
        sym = h["ticker"]
        shares = h["shares"]
        dca = h["dca_price"]
        cost = shares * dca
        q = quotes.get(sym)
        if not q:
            stocks.append({"sym": sym, "shares": shares, "dca": dca, "cost": cost,
                           "available": False})
            continue
        price = effective_price(q)
        prev = q["prev_close"]
        value = shares * price
        pl = value - cost   # = shares·(price−dca): a short profits when price falls
        day_change = shares * (price - prev)
        total_value += value
        total_cost += cost
        total_gross_cost += abs(cost)
        total_day_change += day_change
        stocks.append({
            "sym": sym, "shares": shares, "dca": dca, "cost": cost, "available": True,
            "price": price, "prev": prev, "value": value,
            "pl": pl, "pl_pct": (pl / abs(cost) * 100) if cost else 0.0,
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
    realized_total += await queries.get_realized_adjustment_total(uid)
    dividend_income = await queries.get_dividend_income(uid)
    day_realized = await queries.get_day_realized_pnl(uid, _market_day_start_utc())
    unrealized = (total_value - total_cost) + options_unrealized
    combined_cost = total_gross_cost + options_cost
    total_pnl = unrealized + realized_total
    base = total_value - total_day_change
    return {
        "stocks": stocks,
        "options": options,
        "has_options": bool(option_positions),
        "stock_value": total_value,
        "stock_cost": total_gross_cost,
        "day_change": total_day_change,
        "day_pct": (total_day_change / base * 100) if base else 0.0,
        "day_realized": day_realized,
        "day_total": total_day_change + day_realized,
        "day_total_pct": ((total_day_change + day_realized) / base * 100) if base else 0.0,
        "options_value": options_value,
        "options_unrealized": options_unrealized,
        "options_cost": options_cost,
        "cash": cash,
        "account_value": total_value + options_value + cash,
        "realized_total": realized_total,
        "unrealized": unrealized,
        "unrealized_pct": (unrealized / combined_cost * 100) if combined_cost else 0.0,
        "total_pnl": total_pnl,
        "dividend_income": dividend_income,
    }


def _join_capped(lines: list[str], limit: int, noun: str = "position") -> str:
    """Join lines into an embed description, dropping any that would overflow
    `limit` chars and appending a '…and N more' marker. Discord rejects embeds
    whose description exceeds 4096 chars, which is what breaks /stock profile for
    users holding a lot of positions."""
    kept: list[str] = []
    used = 0
    for idx, ln in enumerate(lines):
        cost = len(ln) + (1 if kept else 0)
        if used + cost > limit:
            dropped = len(lines) - idx
            kept.append(f"_…and {dropped} more {noun}{'' if dropped == 1 else 's'}_")
            break
        kept.append(ln)
        used += cost
    return "\n".join(kept)


def _build_overview_embed(target: discord.abc.User, d: dict) -> discord.Embed:
    """The default portfolio view: holdings list + headline totals."""
    lines: list[str] = []
    for h in d["stocks"]:
        sym = h["sym"]
        qty = f"short {abs(h['shares']):g}" if h["shares"] < 0 else f"{h['shares']:g}"
        if not h["available"]:
            lines.append(f"`{sym}` — {qty} sh @ {_fmt_price(h['dca'])} DCA · *price unavailable*")
            continue
        sign = "+" if h["pl"] >= 0 else ""
        emoji = "🟢" if h["pl"] >= 0 else "🔴"
        lines.append(
            f"{emoji} [`{sym}`]({_yahoo_url(sym)}) · {qty} sh @ {_fmt_price(h['dca'])} → "
            f"**{_fmt_price(h['price'])}** · P/L `{sign}{h['pl']:,.2f} ({sign}{h['pl_pct']:.2f}%)`"
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

    # Discord caps embed descriptions at 4096 chars; cap each section so a big
    # book (many stocks/options) can't overflow and break the whole embed.
    sections: list[str] = []
    opt_text = ""
    if option_lines:
        opt_text = "**Options**\n" + _join_capped(option_lines, limit=1000, noun="option")
    if lines:
        stock_budget = 4000 - (len(opt_text) + 2 if opt_text else 0)
        sections.append(_join_capped(lines, limit=stock_budget, noun="position"))
    if opt_text:
        sections.append(opt_text)
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
    embed.add_field(name="Today",
                    value=f"`{_fmt_change(d['day_total'], d['day_total_pct'])}`", inline=True)
    if d.get("dividend_income"):
        embed.add_field(name="Dividends received",
                        value=f"`{_fmt_money(d['dividend_income'])}`", inline=True)
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
            f"{emoji} [`{h['sym']}`]({_yahoo_url(h['sym'])}) · {_fmt_price(h['prev'])} → **{_fmt_price(h['price'])}** "
            f"`{psign}{h['day_pct']:.2f}%` · {sign}{_fmt_money(h['day_change'])}"
        )
    unpriced = [h for h in d["stocks"] if not h.get("available")]
    for h in unpriced:
        lines.append(f"`{h['sym']}` · *price unavailable*")

    description = _join_capped(lines, limit=4000, noun="position") if lines \
        else "_No priced stock positions today._"
    color = 0x57F287 if d["day_total"] >= 0 else 0xED4245
    embed = discord.Embed(
        title=f"{target.display_name}'s Portfolio — Today",
        description=description,
        color=color,
    )
    embed.add_field(name="Day P/L",
                    value=f"`{_fmt_change(d['day_total'], d['day_total_pct'])}`", inline=True)
    embed.add_field(name="• market move", value=f"`{_fmt_pnl(d['day_change'])}`", inline=True)
    embed.add_field(name="• realized today", value=f"`{_fmt_pnl(d['day_realized'])}`", inline=True)
    if movers:
        top = movers[0]
        embed.add_field(name="Top gainer",
                        value=f"`{top['sym']}` {('+' if top['day_pct']>=0 else '')}{top['day_pct']:.2f}%",
                        inline=True)
        bot_ = movers[-1]
        embed.add_field(name="Top loser",
                        value=f"`{bot_['sym']}` {('+' if bot_['day_pct']>=0 else '')}{bot_['day_pct']:.2f}%",
                        inline=True)
    embed.set_footer(text="Day P/L = today's market move on held stocks + realized gains booked today "
                          "(options market move excluded).")
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
    """Net shares held after applying all of a ticker's trades up to `cutoff`.
    Signed: a position oversold past flat goes negative (a short)."""
    shares = 0.0
    for t in trades:
        dt = _parse_utc(t["executed_at"])
        if dt is None or dt > cutoff:
            continue
        shares += t["shares"] if t["side"] == "buy" else -t["shares"]
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
            if abs(shares) <= 1e-9:
                continue
            px = ff.get(sym, {}).get(d)
            if px is not None and not pd.isna(px):
                total += shares * float(px)   # shorts subtract (negative shares)
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


def _render_equity_curve_png(
    name: str, points: list[tuple], title: str | None = None,
    benchmark: list[tuple] | None = None,
) -> bytes:
    """Render an equity curve to a PNG (Tokyo-Night styled). Synchronous — run in executor.
    `points` is [(datetime, account_value), ...] sorted ascending. `benchmark`,
    if given, is a same-scale [(datetime, value), ...] series (e.g. SPY started
    at the same dollars) drawn as a reference line."""
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
    ax.plot(xs, ys, color=line, linewidth=2.0, label="Portfolio")
    ax.fill_between(xs, ys, min(ys), color=line, alpha=0.12)

    if benchmark:
        bxs = [p[0] for p in benchmark]
        bys = [p[1] for p in benchmark]
        ax.plot(bxs, bys, color="#7aa2f7", linewidth=1.6, linestyle="--",
                label="S&P 500 (same $)")
        ax.legend(loc="upper left", frameon=False, labelcolor="#a9b1d6", fontsize=9)

    ax.set_title(title or f"{name}'s Portfolio Value", color="#c0caf5", fontsize=14, pad=12)
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


async def _intraday_png(symbol: str, prev_close: float | None) -> bytes | None:
    """Fetch today's intraday prices and render a Tokyo-Night line chart PNG.
    Returns None if there's not enough data to plot. Sync work runs in executor."""
    import yfinance as yf

    def _work() -> bytes | None:
        df = yf.download(
            tickers=symbol, period="1d", interval="5m",
            auto_adjust=False, progress=False, threads=False,
        )
        if df is None or df.empty:
            return None
        close = df["Close"]
        if hasattr(close, "columns"):  # multi-index when yfinance wraps single ticker
            close = close.iloc[:, 0]
        close = close.dropna()
        if len(close) < 2:
            return None
        xs = list(close.index.to_pydatetime())
        ys = [float(v) for v in close.values]

        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates

        base = prev_close if prev_close else ys[0]
        up = ys[-1] >= base
        line = "#9ece6a" if up else "#f7768e"

        fig, ax = plt.subplots(figsize=(9, 4.0), dpi=110)
        fig.patch.set_facecolor("#1a1b26")
        ax.set_facecolor("#1a1b26")
        ax.plot(xs, ys, color=line, linewidth=2.0)
        ax.fill_between(xs, ys, min(ys), color=line, alpha=0.12)
        if prev_close:
            ax.axhline(prev_close, color="#565f89", linewidth=1.0, linestyle="--")

        ax.set_title(f"{symbol} — Today", color="#c0caf5", fontsize=14, pad=12)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        for spine in ("left", "bottom"):
            ax.spines[spine].set_color("#414868")
        ax.tick_params(colors="#a9b1d6", labelsize=9)
        ax.grid(True, color="#292e42", linewidth=0.6)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:,.2f}"))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        fig.autofmt_xdate(rotation=0, ha="center")

        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close(fig)
        return buf.getvalue()

    try:
        return await asyncio.get_running_loop().run_in_executor(None, _work)
    except Exception as e:
        log.warning(f"intraday chart failed for {symbol}: {e}")
        return None


# ── Benchmark (S&P 500) ──────────────────────────────────────────────────────

_spy_cache: dict = {"at": None, "series": None}
_SPY_TTL = timedelta(hours=12)
_SPY_LOOKBACK_DAYS = 1100  # ~3 years; covers any realistic portfolio history


def _fetch_spy_history_sync(start_date: date, end_date: date) -> dict:
    """{date: close} for SPY over a range. Synchronous — run in executor."""
    import yfinance as yf
    try:
        h = yf.Ticker("SPY").history(
            start=start_date.isoformat(), end=(end_date + timedelta(days=1)).isoformat(),
            interval="1d", auto_adjust=True,
        )
    except Exception:
        return {}
    if h is None or h.empty or "Close" not in h:
        return {}
    return {ts.date(): float(c) for ts, c in zip(h.index, h["Close"].values)}


async def _get_spy_series() -> dict:
    """SPY daily closes (cached 12h)."""
    now = datetime.now(timezone.utc)
    if _spy_cache["series"] and _spy_cache["at"] and (now - _spy_cache["at"]) < _SPY_TTL:
        return _spy_cache["series"]
    start = (now - timedelta(days=_SPY_LOOKBACK_DAYS)).date()
    series = await asyncio.get_running_loop().run_in_executor(
        None, _fetch_spy_history_sync, start, now.date()
    )
    if series:
        _spy_cache.update({"at": now, "series": series})
    return _spy_cache["series"] or {}


def _benchmark_points(points: list[tuple], spy: dict) -> list[tuple] | None:
    """Scale SPY so it starts at the portfolio's first dollar value — 'what the
    same money in SPY would be worth.' Returns same-axis [(dt, value), ...]."""
    import bisect
    if not spy or not points:
        return None
    dates = sorted(spy)
    closes = [spy[d] for d in dates]

    def spy_on(day: date):
        i = bisect.bisect_right(dates, day) - 1
        return closes[i] if i >= 0 else None

    base_port = points[0][1]
    base_spy = spy_on(points[0][0].date())
    if not base_spy or not base_port:
        return None
    out = [(dt, base_port * c / base_spy) for dt, _ in points
           if (c := spy_on(dt.date())) is not None]
    return out if len(out) >= 2 else None


def _sharpe_ratio(values: list[float]) -> float | None:
    """Annualised Sharpe from a daily value series (risk-free ~0)."""
    rets = [values[i] / values[i - 1] - 1 for i in range(1, len(values))
            if values[i - 1]]
    if len(rets) < 20:  # need ~a month of daily points before this means anything
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    sd = var ** 0.5
    if sd == 0:
        return None
    return (mean / sd) * (252 ** 0.5)


def _benchmark_stats(points: list[tuple], bench: list[tuple] | None) -> dict:
    """Alpha vs SPY and Sharpe over the window covered by `points`."""
    out: dict = {"alpha": None, "sharpe": _sharpe_ratio([v for _, v in points])}
    # Both bases must be positive — a negative starting equity flips the return's
    # sign and yields a nonsense alpha (see _period_return_pct).
    if bench and len(bench) >= 2 and points[0][1] > 0 and bench[0][1] > 0:
        port_ret = points[-1][1] / points[0][1] - 1
        spy_ret = bench[-1][1] / bench[0][1] - 1
        out["alpha"] = (port_ret - spy_ret) * 100
    return out


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

    bench = _benchmark_points(points, await _get_spy_series())
    stats = _benchmark_stats(points, bench)
    png = await asyncio.get_running_loop().run_in_executor(
        None, _render_equity_curve_png, target.display_name, points, None, bench
    )
    file = discord.File(io.BytesIO(png), filename="portfolio.png")

    first_v, last_v = points[0][1], points[-1][1]
    change = last_v - first_v
    pct = _period_return_pct(first_v, change)
    color = 0x57F287 if change >= 0 else 0xED4245
    embed = discord.Embed(title=f"{target.display_name}'s Portfolio Value", color=color)
    embed.set_image(url="attachment://portfolio.png")
    span_days = (points[-1][0] - points[0][0]).days or 1
    embed.add_field(name="Now", value=f"`{_fmt_money(last_v)}`", inline=True)
    # No percent when the window started at a non-positive value — show $ only.
    change_str = _fmt_change(change, pct) if pct is not None else _fmt_pnl(change)
    embed.add_field(name=f"Change ({span_days}d)",
                    value=f"`{change_str}`", inline=True)
    if stats["alpha"] is not None:
        verb = "beating" if stats["alpha"] >= 0 else "trailing"
        embed.add_field(name="vs S&P 500",
                        value=f"`{_pct_str(stats['alpha'])}` ({verb})", inline=True)
    if stats["sharpe"] is not None:
        embed.add_field(name="Sharpe", value=f"`{stats['sharpe']:.2f}`", inline=True)
    embed.set_footer(text="Dashed = the same money in SPY. Points before today are reconstructed "
                          "from stock trades (cash/options excluded); live points include everything.")
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

    @discord.ui.button(label="Allocation", style=discord.ButtonStyle.secondary, custom_id="allocation")
    async def allocation(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self._set_active("allocation")
        await interaction.response.defer()
        holdings = [(h["sym"], h["value"]) for h in self.data["stocks"] if h.get("available")]
        file, embed = await _build_allocation(
            self.target.display_name, holdings, self.data["options_value"], self.data["cash"]
        )
        attachments = [file] if file is not None else []
        await interaction.edit_original_response(embed=embed, attachments=attachments, view=self)

    @discord.ui.button(label="Risk", style=discord.ButtonStyle.secondary, custom_id="risk")
    async def risk(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self._set_active("risk")
        await interaction.response.defer()
        holdings = [(h["sym"], h["value"]) for h in self.data["stocks"] if h.get("available")]
        snaps = await queries.get_portfolio_snapshots(str(self.target.id))
        series = [s["stock_value"] for s in snaps]
        trades = await queries.get_stock_trades(str(self.target.id))
        embed = await _build_risk(
            self.target.display_name, holdings, self.data["options_value"],
            self.data["cash"], self.data["account_value"], series, trades,
        )
        await interaction.edit_original_response(embed=embed, attachments=[], view=self)


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
    # Bulk-load every trader's positions/options/cash in one query each, rather
    # than a connection-per-user round-trip inside the loop.
    bulk_positions = await queries.get_all_stock_positions()
    bulk_options = await queries.get_all_option_positions()
    bulk_cash = await queries.get_all_stock_cash()
    all_positions: dict[str, list[dict]] = {}
    all_options: dict[str, list[dict]] = {}
    ticker_set: set[str] = set()
    open_option_specs: list[dict] = []
    for uid in users:
        positions = bulk_positions.get(uid, [])
        options = bulk_options.get(uid, [])
        all_positions[uid] = positions
        all_options[uid] = options
        for p in positions:
            if not p["closed"]:
                ticker_set.add(p["ticker"])
        for p in options:
            if not p["closed"]:
                open_option_specs.append(p)

    quotes = await fetch_quotes(sorted(ticker_set), extended=True) if ticker_set else {}
    option_prices = await _price_option_positions(open_option_specs)
    adjustments = await queries.get_all_realized_adjustments()

    now = datetime.now(timezone.utc)
    week_cut = (now - timedelta(days=7)).isoformat()
    ytd_cut = datetime(now.year, 1, 1, tzinfo=timezone.utc).isoformat()

    week_snaps, ytd_snaps = await asyncio.gather(
        queries.get_all_snapshot_values_asof(week_cut),
        queries.get_all_snapshot_values_asof(ytd_cut),
    )

    rows: list[dict] = []
    for uid in users:
        positions = all_positions[uid]
        unrealized = 0.0
        realized = sum(p["realized_pnl"] for p in positions) + adjustments.get(uid, 0.0)
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
            price = effective_price(q)
            unrealized += p["shares"] * (price - p["dca_price"])
            stock_value += p["shares"] * price
            stock_value_prev += p["shares"] * q["prev_close"]
            day_gain += p["shares"] * (price - q["prev_close"])

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

        cash = bulk_cash.get(uid, 0.0)
        total = unrealized + realized
        rows.append({
            "user_id": uid,
            "total": total,
            "invested": invested,
            "pct": (total / invested * 100) if invested > 0 else 0.0,
            "account_value": stock_value + options_value + cash,
            "day_gain": day_gain,
            "day_base": stock_value_prev,
            "week_base": week_snaps.get(uid),
            "ytd_base": ytd_snaps.get(uid),
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
            pct = _period_return_pct(r["day_base"], r["day_gain"])
            pct_txt = _pct_str(pct) if pct is not None else "—"
            lines.append(f"{medal} **{name}** · `{_fmt_pnl(r['day_gain'])}` · `{pct_txt}`")
        else:
            base = r["week_base"] if period == "weekly" else r["ytd_base"]
            gain = r["account_value"] - base
            pct = _period_return_pct(base, gain)
            pct_txt = _pct_str(pct) if pct is not None else "—"
            lines.append(f"{medal} **{name}** · `{_fmt_pnl(gain)}` · `{pct_txt}`")

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


# ── Server-wide portfolio ────────────────────────────────────────────────────


async def _compute_server_portfolio() -> dict | None:
    """Aggregate every trader's portfolio into one server book. Prices all open
    positions in a single batched pass. Returns None if nobody trades."""
    users = await queries.get_all_portfolio_users()
    if not users:
        return None

    # Bulk-load every trader's positions/options/cash in one query each, rather
    # than a connection-per-user round-trip inside the loop.
    bulk_positions = await queries.get_all_stock_positions()
    bulk_options = await queries.get_all_option_positions()
    bulk_cash = await queries.get_all_stock_cash()
    all_positions: dict[str, list[dict]] = {}
    all_options: dict[str, list[dict]] = {}
    ticker_set: set[str] = set()
    open_option_specs: list[dict] = []
    for uid in users:
        positions = bulk_positions.get(uid, [])
        options = bulk_options.get(uid, [])
        all_positions[uid] = positions
        all_options[uid] = options
        for p in positions:
            if not p["closed"]:
                ticker_set.add(p["ticker"])
        for p in options:
            if not p["closed"]:
                open_option_specs.append(p)

    quotes = await fetch_quotes(sorted(ticker_set), extended=True) if ticker_set else {}
    option_prices = await _price_option_positions(open_option_specs)
    adjustments = await queries.get_all_realized_adjustments()

    per_ticker: dict[str, dict] = {}
    per_option: dict[tuple, dict] = {}
    stock_value = stock_cost = day_change = 0.0
    realized_total = sum(adjustments.get(uid, 0.0) for uid in users)
    options_value = options_unrealized = options_cost = 0.0
    holders: set[str] = set()

    for uid in users:
        held = False
        for p in all_positions[uid]:
            realized_total += p["realized_pnl"]
            if p["closed"]:
                continue
            q = quotes.get(p["ticker"])
            if not q:
                continue
            held = True
            sh, price, prev, dca = p["shares"], effective_price(q), q["prev_close"], p["dca_price"]
            val, cost, dch = sh * price, sh * dca, sh * (price - prev)
            stock_value += val
            stock_cost += cost
            day_change += dch
            t = per_ticker.setdefault(p["ticker"], {
                "shares": 0.0, "value": 0.0, "cost": 0.0,
                "day_change": 0.0, "prev_value": 0.0, "price": price, "holders": 0,
            })
            t["shares"] += sh
            t["value"] += val
            t["cost"] += cost
            t["day_change"] += dch
            t["prev_value"] += sh * prev
            t["holders"] += 1
        for p in all_options[uid]:
            realized_total += p["realized_pnl"]
            if p["closed"]:
                continue
            held = True
            key = (p["underlying"], p["opt_type"], p["strike"], p["expiry"])
            premium = option_prices.get(key, {}).get("premium")
            pl = _option_position_pnl(p, premium)
            options_value += pl["value"]
            options_unrealized += pl["unrealized"]
            options_cost += pl["cost"]
            o = per_option.setdefault(key, {
                "underlying": p["underlying"], "opt_type": p["opt_type"],
                "strike": p["strike"], "expiry": p["expiry"],
                "net_contracts": 0.0, "value": 0.0, "unrealized": 0.0, "holders": 0,
            })
            o["net_contracts"] += p["net_contracts"]
            o["value"] += pl["value"]
            o["unrealized"] += pl["unrealized"]
            o["holders"] += 1
        if held:
            holders.add(uid)

    cash_total = sum(bulk_cash.get(uid, 0.0) for uid in users)

    unrealized = (stock_value - stock_cost) + options_unrealized
    base = stock_value - day_change
    return {
        "per_ticker": per_ticker,
        "per_option": per_option,
        "num_traders": len(users),
        "num_holders": len(holders),
        "stock_value": stock_value,
        "stock_cost": stock_cost,
        "day_change": day_change,
        "day_pct": (day_change / base * 100) if base else 0.0,
        "options_value": options_value,
        "options_cost": options_cost,
        "cash": cash_total,
        "account_value": stock_value + options_value + cash_total,
        "realized_total": realized_total,
        "unrealized": unrealized,
        "unrealized_pct": (unrealized / (stock_cost + options_cost) * 100)
        if (stock_cost + options_cost) else 0.0,
        "total_pnl": unrealized + realized_total,
        "has_options": options_value != 0 or options_cost != 0,
    }


def _build_server_overview_embed(name: str, d: dict) -> discord.Embed:
    """Server totals + the most valuable holdings across everyone."""
    lines: list[str] = []
    for sym, t in sorted(d["per_ticker"].items(), key=lambda kv: kv[1]["value"], reverse=True)[:12]:
        dpct = (t["day_change"] / t["prev_value"] * 100) if t["prev_value"] else 0.0
        emoji = "🟢" if t["day_change"] >= 0 else "🔴"
        lines.append(
            f"{emoji} [`{sym}`]({_yahoo_url(sym)}) · {t['shares']:g} sh · "
            f"**{_fmt_money(t['value'])}** · today `{_pct_str(dpct)}` "
            f"· {t['holders']} holder{'s' if t['holders'] != 1 else ''}"
        )
    description = "\n".join(lines) if lines else "_No open positions across the server._"

    color = 0x57F287 if d["total_pnl"] >= 0 else 0xED4245
    embed = discord.Embed(
        title=f"🏦 {name} — Server Portfolio",
        description=description,
        color=color,
    )
    embed.add_field(name="Account Value", value=f"`{_fmt_money(d['account_value'])}`", inline=True)
    embed.add_field(name="Stock Value", value=f"`{_fmt_money(d['stock_value'])}`", inline=True)
    if d["has_options"]:
        embed.add_field(name="Options Value", value=f"`{_fmt_money(d['options_value'])}`", inline=True)
    embed.add_field(name="Cash", value=f"`{_fmt_money(d['cash'])}`", inline=True)
    embed.add_field(name="Unrealized P/L",
                    value=f"`{_fmt_change(d['unrealized'], d['unrealized_pct'])}`", inline=True)
    embed.add_field(name="Realized P/L", value=f"`{_fmt_pnl(d['realized_total'])}`", inline=True)
    embed.add_field(name="Total P/L", value=f"`{_fmt_pnl(d['total_pnl'])}`", inline=True)
    embed.add_field(name="Today (stocks)",
                    value=f"`{_fmt_change(d['day_change'], d['day_pct'])}`", inline=True)

    # Open option positions across the server (most valuable first).
    per_option = d.get("per_option") or {}
    if per_option:
        olines: list[str] = []
        for _key, o in sorted(per_option.items(), key=lambda kv: abs(kv[1]["value"]), reverse=True)[:8]:
            emoji = "🟢" if o["unrealized"] >= 0 else "🔴"
            n = o["net_contracts"]
            qty = f"{abs(n):g}x {'long' if n >= 0 else 'short'}"
            olines.append(
                f"{emoji} `{_option_label(o)}` · {qty} · **{_fmt_money(o['value'])}** "
                f"· {o['holders']} holder{'s' if o['holders'] != 1 else ''}"
            )
        embed.add_field(name="📄 Open Options", value="\n".join(olines), inline=False)

    foot = f"{d['num_holders']} active holder(s) · {len(d['per_ticker'])} ticker(s)"
    if per_option:
        foot += f" · {len(per_option)} option position(s)"
    embed.set_footer(text=foot)
    return embed


def _build_server_today_embed(name: str, d: dict) -> discord.Embed:
    """The server's combined book ranked by today's dollar move."""
    movers = sorted(d["per_ticker"].values(), key=lambda t: t["day_change"], reverse=True)
    items = sorted(d["per_ticker"].items(), key=lambda kv: kv[1]["day_change"], reverse=True)
    lines: list[str] = []
    for sym, t in items[:15]:
        dpct = (t["day_change"] / t["prev_value"] * 100) if t["prev_value"] else 0.0
        emoji = "🟢" if t["day_change"] > 0 else ("🔴" if t["day_change"] < 0 else "⚪")
        sign = "+" if t["day_change"] >= 0 else ""
        lines.append(
            f"{emoji} [`{sym}`]({_yahoo_url(sym)}) · `{_pct_str(dpct)}` · "
            f"{sign}{_fmt_money(t['day_change'])} ({t['shares']:g} sh)"
        )
    description = "\n".join(lines) if lines else "_No priced positions today._"
    color = 0x57F287 if d["day_change"] >= 0 else 0xED4245
    embed = discord.Embed(
        title=f"🏦 {name} — Server Today",
        description=description,
        color=color,
    )
    embed.add_field(name="Day P/L (stocks)",
                    value=f"`{_fmt_change(d['day_change'], d['day_pct'])}`", inline=True)
    if movers:
        top, bot_ = movers[0], movers[-1]
        tp = (top["day_change"] / top["prev_value"] * 100) if top["prev_value"] else 0.0
        bp = (bot_["day_change"] / bot_["prev_value"] * 100) if bot_["prev_value"] else 0.0
        # Recover symbols for the top/bottom movers.
        tsym = next(k for k, v in d["per_ticker"].items() if v is top)
        bsym = next(k for k, v in d["per_ticker"].items() if v is bot_)
        embed.add_field(name="Top gainer", value=f"`{tsym}` {_pct_str(tp)}", inline=True)
        embed.add_field(name="Top loser", value=f"`{bsym}` {_pct_str(bp)}", inline=True)
    embed.set_footer(text="Day change reflects stock positions only (options excluded).")
    return embed


def _server_equity_points(rows: list[dict]) -> list[tuple]:
    """Sum all users' equity curves onto a shared daily axis. Each user's value
    is forward-filled (last known snapshot) so gaps don't drop them to zero.
    Returns [(datetime, server_account_value), ...]."""
    by_user: dict[str, list[tuple]] = {}
    for r in rows:
        dt = _parse_utc(r["captured_at"])
        if dt is not None:
            by_user.setdefault(r["discord_user"], []).append((dt, r["account_value"]))
    if not by_user:
        return []
    for series in by_user.values():
        series.sort(key=lambda x: x[0])

    first_day = min(s[0][0] for s in by_user.values()).date()
    last_day = max(s[-1][0] for s in by_user.values()).date()
    days: list = []
    d = first_day
    while d <= last_day:
        days.append(d)
        d += timedelta(days=1)

    totals = [0.0] * len(days)
    for series in by_user.values():
        idx = 0
        last = None
        for i, day in enumerate(days):
            end = datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=timezone.utc)
            while idx < len(series) and series[idx][0] <= end:
                last = series[idx][1]
                idx += 1
            if last is not None:
                totals[i] += last

    return [
        (datetime(day.year, day.month, day.day, 21, 0, 0, tzinfo=timezone.utc), totals[i])
        for i, day in enumerate(days)
    ]


async def _build_server_graph(name: str, snap_rows: list[dict], current_value: float | None):
    """Build (discord.File, discord.Embed) for the server equity curve."""
    points = _server_equity_points(snap_rows)
    now = datetime.now(timezone.utc)
    if current_value is not None and (not points or (now - points[-1][0]) > timedelta(hours=1)):
        points.append((now, current_value))

    if len(points) < 2:
        err = discord.Embed(
            title=f"🏦 {name} — Server Portfolio Value",
            description="Not enough history to graph yet — the server curve fills in as "
                        "members' portfolios are snapshotted hourly.",
            color=0xE0AF68,
        )
        return None, err

    bench = _benchmark_points(points, await _get_spy_series())
    stats = _benchmark_stats(points, bench)
    png = await asyncio.get_running_loop().run_in_executor(
        None, _render_equity_curve_png, name, points, f"{name} — Server Portfolio Value", bench
    )
    file = discord.File(io.BytesIO(png), filename="server_portfolio.png")

    first_v, last_v = points[0][1], points[-1][1]
    change = last_v - first_v
    pct = (change / first_v * 100) if first_v else 0.0
    color = 0x57F287 if change >= 0 else 0xED4245
    embed = discord.Embed(title=f"🏦 {name} — Server Portfolio Value", color=color)
    embed.set_image(url="attachment://server_portfolio.png")
    span_days = (points[-1][0] - points[0][0]).days or 1
    embed.add_field(name="Now", value=f"`{_fmt_money(last_v)}`", inline=True)
    embed.add_field(name=f"Change ({span_days}d)", value=f"`{_fmt_change(change, pct)}`", inline=True)
    if stats["alpha"] is not None:
        verb = "beating" if stats["alpha"] >= 0 else "trailing"
        embed.add_field(name="vs S&P 500",
                        value=f"`{_pct_str(stats['alpha'])}` ({verb})", inline=True)
    embed.set_footer(text="Combined account value of all members vs the same money in SPY (dashed). "
                          "Points before today are reconstructed from stock trades.")
    return file, embed


class ServerPortfolioView(discord.ui.View):
    """Overview / Today / Graph switcher for the server-wide portfolio."""

    def __init__(self, name: str, data: dict, snap_rows: list[dict], invoker_id: int) -> None:
        super().__init__(timeout=180)
        self.name = name
        self.data = data
        self.snap_rows = snap_rows
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
                "This isn't your menu — run `/stock server` yourself.", ephemeral=True
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
            embed=_build_server_overview_embed(self.name, self.data), attachments=[], view=self
        )

    @discord.ui.button(label="Today", style=discord.ButtonStyle.secondary, custom_id="today")
    async def today(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self._set_active("today")
        await interaction.response.edit_message(
            embed=_build_server_today_embed(self.name, self.data), attachments=[], view=self
        )

    @discord.ui.button(label="Graph", style=discord.ButtonStyle.secondary, custom_id="graph")
    async def graph(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self._set_active("graph")
        await interaction.response.defer()
        file, embed = await _build_server_graph(self.name, self.snap_rows, self.data["account_value"])
        attachments = [file] if file is not None else []
        await interaction.edit_original_response(embed=embed, attachments=attachments, view=self)

    @discord.ui.button(label="Allocation", style=discord.ButtonStyle.secondary, custom_id="allocation")
    async def allocation(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self._set_active("allocation")
        await interaction.response.defer()
        holdings = [(sym, t["value"]) for sym, t in self.data["per_ticker"].items()]
        file, embed = await _build_allocation(
            self.name, holdings, self.data["options_value"], self.data["cash"]
        )
        attachments = [file] if file is not None else []
        await interaction.edit_original_response(embed=embed, attachments=attachments, view=self)

    @discord.ui.button(label="Risk", style=discord.ButtonStyle.secondary, custom_id="risk")
    async def risk(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self._set_active("risk")
        await interaction.response.defer()
        holdings = [(sym, t["value"]) for sym, t in self.data["per_ticker"].items()]
        series = [v for _, v in _server_equity_points(self.snap_rows)]
        embed = await _build_risk(
            self.name, holdings, self.data["options_value"], self.data["cash"],
            self.data["account_value"], series, None,
        )
        await interaction.edit_original_response(embed=embed, attachments=[], view=self)


# ── Composition / allocation (stocks vs ETFs, sectors, persona) ──────────────

_TYPE_LABELS = {"EQUITY": "Stocks", "ETF": "ETFs", "MUTUALFUND": "Funds", "CRYPTOCURRENCY": "Crypto"}


def _compute_composition(
    holdings: list[tuple], options_value: float, cash: float, meta: dict[str, dict]
) -> dict:
    """Bucket open stock holdings into Stocks/ETFs/Funds/Other (by quote type)
    and by sector. `holdings` is [(ticker, market_value), ...]. Returns the type
    split, sector split, full-account donut slices, and the largest position."""
    by_type: dict[str, float] = {}
    by_sector: dict[str, float] = {}
    top_value = 0.0
    top_sym = None
    for sym, value in holdings:
        m = meta.get(sym.upper(), {})
        qt = (m.get("quote_type") or "").upper()
        label = _TYPE_LABELS.get(qt, "Other")
        by_type[label] = by_type.get(label, 0.0) + value
        if qt == "EQUITY":
            sec = m.get("sector") or "Unknown"
        elif qt in ("ETF", "MUTUALFUND"):
            sec = "ETF / Fund"
        elif qt == "CRYPTOCURRENCY":
            sec = "Crypto"
        else:
            sec = m.get("sector") or "Other"
        by_sector[sec] = by_sector.get(sec, 0.0) + value
        if value > top_value:
            top_value, top_sym = value, sym

    invested_total = sum(by_type.values())
    slices = dict(by_type)
    if options_value and options_value > 0:
        slices["Options"] = options_value
    if cash and cash > 0:
        slices["Cash"] = cash
    return {
        "by_type": by_type,
        "by_sector": by_sector,
        "invested_total": invested_total,
        "slices": slices,
        "account_total": sum(slices.values()),
        "options_value": options_value,
        "cash": cash,
        "max_weight": (top_value / invested_total * 100) if invested_total else 0.0,
        "top_sym": top_sym,
    }


def _portfolio_persona(comp: dict) -> str:
    """A lighthearted archetype label derived from the composition."""
    inv = comp["invested_total"] or 0.0
    bt = comp["by_type"]
    etf_pct = (bt.get("ETFs", 0.0) + bt.get("Funds", 0.0)) / inv * 100 if inv else 0.0
    crypto_pct = bt.get("Crypto", 0.0) / inv * 100 if inv else 0.0
    acct = comp["account_total"] or 0.0
    cash_pct = (comp["cash"] / acct * 100) if acct else 0.0
    sectors = [s for s in comp["by_sector"] if s not in ("ETF / Fund", "Crypto")]

    if crypto_pct >= 50:
        return "🪙 Crypto Degen"
    if comp["options_value"]:
        return "🎰 Theta Gang"
    if etf_pct >= 60:
        return "🧘 Index Zen Master"
    if comp["max_weight"] >= 60 and comp["top_sym"]:
        return f"🎯 All-In {comp['top_sym']}"
    if cash_pct >= 50:
        return "💰 Cash Hoarder"
    if len(sectors) >= 5:
        return "🌐 Diversified"
    return "⚖️ Balanced Trader"


def _render_allocation_donut(title: str, slices: dict) -> bytes:
    """Render an allocation donut chart to PNG. Synchronous — run in executor."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    items = sorted(slices.items(), key=lambda kv: kv[1], reverse=True)
    labels = [k for k, _ in items]
    vals = [v for _, v in items]
    palette = ["#7aa2f7", "#9ece6a", "#e0af68", "#bb9af7", "#f7768e",
               "#7dcfff", "#ff9e64", "#73daca", "#c0caf5", "#565f89"]
    colors = [palette[i % len(palette)] for i in range(len(items))]

    fig, ax = plt.subplots(figsize=(6.5, 4.8), dpi=110)
    fig.patch.set_facecolor("#1a1b26")
    wedges, _ = ax.pie(
        vals, colors=colors, startangle=90,
        wedgeprops=dict(width=0.42, edgecolor="#1a1b26", linewidth=2),
    )
    ax.set_title(title, color="#c0caf5", fontsize=13, pad=10)
    total = sum(vals) or 1.0
    legend = [f"{l}  {v / total * 100:.0f}%" for l, v in items]
    ax.legend(wedges, legend, loc="center left", bbox_to_anchor=(0.98, 0.5),
              frameon=False, labelcolor="#a9b1d6", fontsize=10)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def _build_allocation_embed(name: str, comp: dict, persona: str) -> discord.Embed:
    inv = comp["invested_total"]
    lines: list[str] = []
    for label in ("Stocks", "ETFs", "Funds", "Crypto", "Other"):
        v = comp["by_type"].get(label)
        if not v:
            continue
        pct = (v / inv * 100) if inv else 0.0
        lines.append(f"**{label}** · {_fmt_money(v)} · `{pct:.1f}%`")
    type_block = "\n".join(lines) if lines else "_No stock/ETF holdings._"

    sec_items = sorted(comp["by_sector"].items(), key=lambda kv: kv[1], reverse=True)[:6]
    sec_block = "\n".join(
        f"{s} · `{(v / inv * 100 if inv else 0):.0f}%`" for s, v in sec_items
    ) if sec_items else "—"

    embed = discord.Embed(
        title=f"{name} — Allocation",
        description=f"**By type** (share of stock holdings)\n{type_block}",
        color=0x7AA2F7,
    )
    embed.add_field(name="By sector", value=sec_block, inline=False)
    embed.set_image(url="attachment://allocation.png")
    embed.set_footer(text=f"Persona: {persona} · donut shows full account incl. cash/options")
    return embed


async def _build_allocation(name: str, holdings: list[tuple], options_value: float, cash: float):
    """Build (discord.File donut, discord.Embed) for an allocation breakdown."""
    meta = await get_ticker_metadata([s for s, _ in holdings]) if holdings else {}
    comp = _compute_composition(holdings, options_value, cash, meta)
    if not comp["slices"]:
        embed = discord.Embed(
            title=f"{name} — Allocation",
            description="_No open positions to break down yet._",
            color=0x7AA2F7,
        )
        return None, embed
    persona = _portfolio_persona(comp)
    png = await asyncio.get_running_loop().run_in_executor(
        None, _render_allocation_donut, f"{name} — Allocation", comp["slices"]
    )
    file = discord.File(io.BytesIO(png), filename="allocation.png")
    return file, _build_allocation_embed(name, comp, persona)


# ── Risk profile + trade quality ─────────────────────────────────────────────

_MARKET_DAILY_VOL = 0.01  # ~S&P 500 daily stdev, for a bottom-up VaR estimate
_VAR_Z = 1.645            # one-sided 95% z-score


def _max_drawdown(values: list[float]) -> float | None:
    """Largest peak-to-trough decline (as a negative %) over a value series."""
    clean = [v for v in values if v is not None]
    if len(clean) < 2:
        return None
    peak = clean[0]
    mdd = 0.0
    for v in clean:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (v - peak) / peak
            if dd < mdd:
                mdd = dd
    return mdd * 100


def _risk_label(score: float) -> str:
    if score < 20:
        return "🟢 Conservative"
    if score < 40:
        return "🔵 Balanced"
    if score < 60:
        return "🟡 Moderately Aggressive"
    if score < 80:
        return "🟠 Aggressive"
    return "🔴 Degenerate"


def _compute_risk(
    holdings: list[tuple], options_value: float, cash: float,
    account_value: float, meta: dict[str, dict], value_series: list[float],
) -> dict:
    """Heuristic risk profile. Beta/concentration/VaR are bottom-up from current
    holdings (instant); max drawdown comes from the value series if available."""
    invested = sum(v for _, v in holdings)
    # Position-weighted beta. ETFs/unknowns default to ~market (1.0); crypto has
    # no published beta but is far swingier, so it defaults high (~3.0).
    bsum = wsum = 0.0
    for sym, v in holdings:
        m = meta.get(sym.upper(), {})
        b = m.get("beta")
        if b is None:
            b = 3.0 if (m.get("quote_type") or "").upper() == "CRYPTOCURRENCY" else 1.0
        bsum += v * b
        wsum += v
    beta = (bsum / wsum) if wsum else 0.0

    values = [v for _, v in holdings]
    largest = max(values, default=0.0)
    largest_pct = (largest / invested * 100) if invested else 0.0
    top_sym = max(holdings, key=lambda kv: kv[1])[0] if holdings else None
    hhi = sum((v / invested) ** 2 for v in values) if invested else 0.0

    at_risk = invested + abs(options_value)
    exposure = (at_risk / account_value) if account_value else 0.0
    var_1d = at_risk * beta * _MARKET_DAILY_VOL * _VAR_Z
    var_pct = (var_1d / account_value * 100) if account_value else 0.0
    mdd = _max_drawdown(value_series)

    beta_score = min(beta / 2.0, 1.0) * 100
    raw = 0.55 * beta_score + 0.45 * largest_pct
    score = max(0.0, min(100.0, raw * exposure + (12 if options_value else 0)))
    return {
        "score": score, "label": _risk_label(score),
        "beta": beta, "largest_pct": largest_pct, "top_sym": top_sym,
        "n_positions": len(holdings), "hhi": hhi,
        "var_1d": var_1d, "var_pct": var_pct,
        "max_drawdown": mdd, "exposure": exposure * 100,
        "has_options": bool(options_value),
    }


def _trade_quality(trades: list[dict]) -> dict | None:
    """FIFO-match the stock trade log into closed lots and summarise: win rate,
    profit factor, avg win/loss, best/worst, and share-weighted holding period."""
    by_ticker: dict[str, list[dict]] = {}
    for t in trades:
        by_ticker.setdefault(t["ticker"], []).append(t)

    closed: list[dict] = []
    for ts in by_ticker.values():
        lots: deque = deque()  # [shares, price, dt]
        for t in ts:
            dt = _parse_utc(t["executed_at"])
            if t["side"] == "buy":
                lots.append([t["shares"], t["price"], dt])
            else:
                qty, sell_price, sell_dt = t["shares"], t["price"], dt
                while qty > 1e-9 and lots:
                    lot = lots[0]
                    take = min(qty, lot[0])
                    hold = (sell_dt - lot[2]).days if (sell_dt and lot[2]) else None
                    closed.append({"pnl": take * (sell_price - lot[1]), "hold": hold, "shares": take})
                    lot[0] -= take
                    qty -= take
                    if lot[0] <= 1e-9:
                        lots.popleft()
    if not closed:
        return None

    wins = [c for c in closed if c["pnl"] > 0]
    losses = [c for c in closed if c["pnl"] < 0]
    gross_win = sum(c["pnl"] for c in wins)
    gross_loss = -sum(c["pnl"] for c in losses)
    held = [(c["hold"], c["shares"]) for c in closed if c["hold"] is not None]
    avg_hold = (sum(h * s for h, s in held) / sum(s for _, s in held)) if held else None
    n = len(closed)
    return {
        "n": n,
        "win_rate": len(wins) / n * 100,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
        "avg_win": (gross_win / len(wins)) if wins else 0.0,
        "avg_loss": (gross_loss / len(losses)) if losses else 0.0,
        "best": max((c["pnl"] for c in closed), default=0.0),
        "worst": min((c["pnl"] for c in closed), default=0.0),
        "avg_hold": avg_hold,
    }


def _score_bar(score: float) -> str:
    filled = round(score / 10)
    return "█" * filled + "░" * (10 - filled)


def _build_risk_embed(name: str, risk: dict, tq: dict | None) -> discord.Embed:
    score = risk["score"]
    color = 0x9ECE6A if score < 40 else (0xE0AF68 if score < 70 else 0xF7768E)
    embed = discord.Embed(
        title=f"{name} — Risk Profile",
        description=f"**Risk Score: {score:.0f} / 100** — {risk['label']}\n`{_score_bar(score)}`",
        color=color,
    )
    embed.add_field(name="Beta", value=f"`{risk['beta']:.2f}`", inline=True)
    conc = f"`{risk['largest_pct']:.0f}%`"
    if risk["top_sym"]:
        conc += f" {risk['top_sym']}"
    embed.add_field(name="Largest position", value=conc, inline=True)
    embed.add_field(name="Positions", value=f"`{risk['n_positions']}`", inline=True)
    embed.add_field(
        name="1-day VaR (95%)",
        value=f"`-{_fmt_money(risk['var_1d'])}` (`-{risk['var_pct']:.1f}%`)",
        inline=True,
    )
    if risk["max_drawdown"] is not None:
        embed.add_field(name="Max drawdown", value=f"`{risk['max_drawdown']:.1f}%`", inline=True)
    embed.add_field(name="Invested", value=f"`{risk['exposure']:.0f}%` of account", inline=True)

    if tq:
        pf = f"{tq['profit_factor']:.2f}" if tq["profit_factor"] is not None else "∞"
        hold = f"{tq['avg_hold']:.0f}d" if tq["avg_hold"] is not None else "—"
        embed.add_field(
            name="📒 Trade record (closed lots, FIFO)",
            value=(
                f"Win rate `{tq['win_rate']:.0f}%` ({tq['n']} lots) · Profit factor `{pf}`\n"
                f"Avg win `+{_fmt_money(tq['avg_win'])}` · Avg loss `-{_fmt_money(tq['avg_loss'])}` · "
                f"Avg hold `{hold}`\n"
                f"Best `{_fmt_pnl(tq['best'])}` · Worst `{_fmt_pnl(tq['worst'])}`"
            ),
            inline=False,
        )
    embed.set_footer(
        text="Heuristic score from beta, concentration & exposure. "
             "VaR assumes ~1%/day market vol; drawdown from your value history."
    )
    return embed


async def _build_risk(
    name: str, holdings: list[tuple], options_value: float, cash: float,
    account_value: float, value_series: list[float], trades: list[dict] | None,
):
    """Build the risk-profile embed (no image)."""
    meta = await get_ticker_metadata([s for s, _ in holdings]) if holdings else {}
    risk = _compute_risk(holdings, options_value, cash, account_value, meta, value_series)
    tq = _trade_quality(trades) if trades else None
    return _build_risk_embed(name, risk, tq)


# ── Trade edit: dropdown → modal ──────────────────────────────────────────────


class _TradeEditModal(discord.ui.Modal):
    def __init__(self, trade: dict) -> None:
        super().__init__(title=f"Edit #{trade['trade_id']} · {trade['ticker']}")
        self.trade = trade
        self.shares = discord.ui.TextInput(
            label="Shares", default=f"{trade['shares']:g}", max_length=20)
        self.price = discord.ui.TextInput(
            label="Price", default=f"{trade['price']:g}", max_length=20)
        self.date = discord.ui.TextInput(
            label="Date (YYYY-MM-DD)", default=trade["executed_at"][:10], required=False, max_length=20)
        for f in (self.shares, self.price, self.date):
            self.add_item(f)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            new_shares, new_price = float(self.shares.value), float(self.price.value)
        except ValueError:
            await interaction.response.send_message("Shares and price must be numbers.", ephemeral=True)
            return
        if new_shares <= 0 or new_price <= 0:
            await interaction.response.send_message("Shares and price must be > 0.", ephemeral=True)
            return
        try:
            executed_at = _parse_executed_at(self.date.value.strip()) if self.date.value.strip() else None
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        ok = await queries.update_stock_trade(
            str(interaction.user.id), self.trade["trade_id"], new_shares, new_price, executed_at)
        if not ok:
            await interaction.response.send_message("Couldn't update that trade.", ephemeral=True)
            return
        t = self.trade
        date_note = f" · {executed_at[:10]}" if executed_at else ""
        await interaction.response.send_message(
            f"✏️ Updated **#{t['trade_id']}** `{t['ticker']}` — "
            f"~~{t['shares']:g} @ {t['price']:,.2f}~~ → **{new_shares:g} @ {new_price:,.2f}**{date_note}",
            ephemeral=True)


class _TradeEditSelect(discord.ui.Select):
    def __init__(self, trades: list[dict]) -> None:
        self._by_id = {str(t["trade_id"]): t for t in trades}
        options = [
            discord.SelectOption(
                label=f"#{t['trade_id']} {t['side'].upper()} {t['shares']:g} {t['ticker']} @ {t['price']:,.2f}"[:100],
                description=t["executed_at"][:10] + (f" · {t['notes']}" if t.get("notes") else ""),
                value=str(t["trade_id"]))
            for t in trades]
        super().__init__(placeholder="Pick a trade to fix…", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(_TradeEditModal(self._by_id[self.values[0]]))


class _TradeEditView(discord.ui.View):
    def __init__(self, trades: list[dict]) -> None:
        super().__init__(timeout=300)
        self.add_item(_TradeEditSelect(trades))


# ── Cog ─────────────────────────────────────────────────────────────────────


class StockCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # "uid:ticker" -> {"date": iso, "band": int} — highest 10% band alerted today.
        # Persisted to a bot setting so a restart can't re-fire the same alert.
        self._swing_alerted: dict[str, dict] = {}
        self.snapshot_loop.start()
        self.monitor_loop.start()
        self.daily_digest.start()
        self.earnings_digest.start()
        self.earnings_results.start()
        self.dividend_loop.start()

    async def cog_load(self) -> None:
        raw = await queries.get_bot_setting(_SWING_STATE_SETTING)
        if raw:
            try:
                self._swing_alerted = json.loads(raw)
            except Exception:
                self._swing_alerted = {}

    def cog_unload(self) -> None:
        self.snapshot_loop.cancel()
        self.monitor_loop.cancel()
        self.daily_digest.cancel()
        self.earnings_digest.cancel()
        self.earnings_results.cancel()
        self.dividend_loop.cancel()

    @tasks.loop(hours=1)
    async def daily_digest(self) -> None:
        """Once a day after the US close, post every trader's day move % (sorted,
        no pings) to the stocks channel."""
        try:
            from zoneinfo import ZoneInfo
            et = datetime.now(ZoneInfo("America/New_York"))
            if et.weekday() >= 5:  # Sat/Sun — market closed, no close to report
                return
            if et.hour < 16:  # before market close — wait
                return
            today = et.date().isoformat()
            if await queries.get_bot_setting(_DIGEST_SETTING) == today:
                return
            users = list(set(await queries.get_users_with_trades())
                         | set(await queries.get_users_with_option_trades()))
            rows = await _leaderboard_rows(users) if users else []
            scored = [{**r, "day_pct": r["day_gain"] / r["day_base"] * 100}
                      for r in rows if r.get("day_base") and r["day_base"] > 0]
            channel = self.bot.get_channel(await self._alerts_channel_id())
            if not scored or channel is None:
                await queries.set_bot_setting(_DIGEST_SETTING, today)  # nothing to post today
                return
            scored.sort(key=lambda r: r["day_pct"], reverse=True)
            names = await self._resolve_names([r["user_id"] for r in scored])
            lines = []
            for i, r in enumerate(scored[:25], 1):
                medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"`#{i:>2}`")
                nm = names.get(r["user_id"], f"Player {r['user_id'][:6]}")
                emoji = "🟢" if r["day_pct"] >= 0 else "🔴"
                lines.append(f"{medal} **{nm}** {emoji} {r['day_pct']:+.2f}% · {_fmt_money(r['day_gain'])}")
            embed = discord.Embed(title="📊 Daily Portfolio Report",
                                  description="\n".join(lines), colour=0x57F287)
            embed.set_footer(text=f"{et.strftime('%b %d')} close · stocks only · sorted by % move")
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            await queries.set_bot_setting(_DIGEST_SETTING, today)
        except Exception:
            log.exception("daily stock digest failed")

    @daily_digest.before_loop
    async def _before_daily_digest(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(hours=1)
    async def earnings_digest(self) -> None:
        """Each morning, post the notable companies reporting tonight (after close)
        or tomorrow before the open — market-wide, sorted by market cap. Tickers the
        server holds are flagged 📍."""
        try:
            from zoneinfo import ZoneInfo
            et = datetime.now(ZoneInfo("America/New_York"))
            if not (7 <= et.hour < 12):   # morning window only
                return
            today_iso = et.date().isoformat()
            if await queries.get_bot_setting(_EARNINGS_SETTING) == today_iso:
                return

            tomorrow_iso = (et.date() + timedelta(days=1)).isoformat()
            tonight = [e for e in await fetch_earnings_calendar(today_iso)
                       if e["time"] == "time-after-hours"]
            morning = [e for e in await fetch_earnings_calendar(tomorrow_iso)
                       if e["time"] == "time-pre-market"]
            channel = self.bot.get_channel(await self._alerts_channel_id())
            if (not tonight and not morning) or channel is None:
                await queries.set_bot_setting(_EARNINGS_SETTING, today_iso)
                return

            # 📍 flag = held by anyone; pings go ONLY to opted-in holders (/stock
            # alerts on) — earnings pings are opt-in, same toggle as swing alerts.
            optin = await queries.get_stock_alert_optin_users()
            held: set[str] = set()
            held_by: dict[str, list[str]] = {}
            for uid, plist in (await queries.get_all_stock_positions()).items():
                for p in plist:
                    if not p.get("closed") and p.get("shares"):
                        held.add(p["ticker"])
                        if uid in optin:
                            held_by.setdefault(p["ticker"], []).append(uid)
            MC_FLOOR, CAP = 2e9, 18   # notable large-caps only (≥$2B); keep it readable

            def group_lines(items: list[dict]) -> list[str]:
                notable = sorted((e for e in items if e["mc"] >= MC_FLOOR),
                                 key=lambda e: e["mc"], reverse=True)
                lines = []
                for e in notable[:CAP]:
                    flag = " 📍" if e["symbol"] in held else ""
                    mc = f" · {_fmt_market_cap(e['mc'])}" if e["mc"] else ""
                    lines.append(f"**{e['symbol']}** {e['name'][:34]}{mc}{flag}")
                if len(notable) > CAP:
                    lines.append(f"…and {len(notable) - CAP} more")
                return lines

            sections = []
            tl, ml = group_lines(tonight), group_lines(morning)
            if tl:
                sections.append("🌙 **Tonight · after close**\n" + "\n".join(tl))
            if ml:
                sections.append("🌅 **Tomorrow · before open**\n" + "\n".join(ml))

            # Holder pings: every held ticker reporting (any cap), @-mentioning owners.
            # Must live in message CONTENT — mentions inside an embed don't notify.
            def holder_lines(items: list[dict], when: str) -> list[str]:
                out = []
                for e in items:
                    us = held_by.get(e["symbol"])
                    if us:
                        out.append(f"📍 **{e['symbol']}** reports {when} — "
                                   + " ".join(f"<@{u}>" for u in us))
                return out
            pings = (holder_lines(tonight, "tonight after close")
                     + holder_lines(morning, "tomorrow before open"))
            holder_content = ("**Earnings for your holdings:**\n" + "\n".join(pings))[:1900] if pings else None

            if not sections and not holder_content:
                await queries.set_bot_setting(_EARNINGS_SETTING, today_iso)
                return
            embed = None
            if sections:
                embed = discord.Embed(title="📅 Earnings on deck",
                                      description="\n\n".join(sections), colour=0xE0AF68)
                embed.set_footer(text="📍 = held in the server · large-caps only · via Nasdaq")
            await channel.send(content=holder_content, embed=embed,
                               allowed_mentions=discord.AllowedMentions(users=True))
            await queries.set_bot_setting(_EARNINGS_SETTING, today_iso)
        except Exception:
            log.exception("earnings digest failed")

    @earnings_digest.before_loop
    async def _before_earnings_digest(self) -> None:
        await self.bot.wait_until_ready()

    def _earnings_result_embed(self, sym: str, name: str, res: dict, q: dict | None) -> discord.Embed:
        est, act, surp = res["eps_estimate"], res["eps_actual"], res["surprise_pct"]
        beat = (surp is not None and surp >= 0) if surp is not None else (
            act >= est if est is not None else None)
        if est is not None:
            verdict = "🟢 Beat" if act >= est else "🔴 Missed"
            eps_line = f"**EPS:** ${act:.2f} vs ${est:.2f} est — {verdict}"
            if surp is not None:
                eps_line += f" ({surp:+.1f}%)"
        else:
            eps_line = f"**EPS:** ${act:.2f} reported"
        lines = [eps_line]
        if q:
            price, prev = effective_price(q), q.get("prev_close")
            pctv = _swing_pct(price, prev)
            arrow = "🟢" if pctv >= 0 else "🔴"
            label = "after hours" if res["when"] == "after close" else "today"
            lines.append(f"**Reaction:** {arrow} {pctv:+.1f}% {label} (${price:,.2f})")
        color = 0x9ECE6A if beat else (0xF7768E if beat is False else 0xE0AF68)
        title = f"📊 {sym}{f' — {name[:40]}' if name else ''} · reported {res['when']}"
        return discord.Embed(title=title, description="\n".join(lines), colour=color)

    @tasks.loop(minutes=30)
    async def earnings_results(self) -> None:
        """After a company the server holds reports, post its results (EPS beat/miss +
        surprise + price reaction) and @-ping its opted-in holders. Self-gating: fires
        once the actual EPS is published (shortly after AMC close / BMO open), deduped
        per (ticker, report date)."""
        try:
            from zoneinfo import ZoneInfo
            et = datetime.now(ZoneInfo("America/New_York"))
            today_iso = et.date().isoformat()
            cal = await fetch_earnings_calendar(today_iso)
            names = {e["symbol"]: e["name"] for e in cal}
            reporting_today = set(names)
            if not reporting_today:
                return
            optin = await queries.get_stock_alert_optin_users()
            held_by: dict[str, list[str]] = {}
            for uid, plist in (await queries.get_all_stock_positions()).items():
                if uid not in optin:
                    continue
                for p in plist:
                    if not p.get("closed") and p.get("shares"):
                        held_by.setdefault(p["ticker"], []).append(uid)
            targets = [s for s in held_by if s in reporting_today]
            if not targets:
                return
            channel = self.bot.get_channel(await self._alerts_channel_id())
            if channel is None:
                return
            for sym in targets:
                if await queries.earnings_result_posted(sym, today_iso):
                    continue
                res = await fetch_earnings_result(sym, today_iso)
                if res is None:
                    continue  # reported but numbers not out yet — a later cycle will catch it
                q = (await fetch_quotes([sym], extended=True)).get(sym)
                embed = self._earnings_result_embed(sym, names.get(sym, ""), res, q)
                holders = held_by[sym]
                content = (f"📊 **{sym}** just reported — "
                           + " ".join(f"<@{u}>" for u in holders))[:1900]
                await channel.send(content=content, embed=embed,
                                   allowed_mentions=discord.AllowedMentions(users=True))
                await queries.mark_earnings_result_posted(sym, today_iso)
        except Exception:
            log.exception("earnings results loop failed")

    @earnings_results.before_loop
    async def _before_earnings_results(self) -> None:
        await self.bot.wait_until_ready()

    # ── Dividends ────────────────────────────────────────────────────────────
    @tasks.loop(hours=6)
    async def dividend_loop(self) -> None:
        """A few times a day: pay cash dividends to holders when a stock they own
        goes ex-dividend. Only ex-dates within the last DIVIDEND_LOOKBACK_DAYS are
        paid (so a first run can't back-pay years of history); each (holder, ticker,
        ex-date) is credited exactly once (deduped via dividends_paid). Credited cash
        goes to brokerage cash, then a consolidated digest pings opted-in holders."""
        try:
            from zoneinfo import ZoneInfo
            today_et = datetime.now(ZoneInfo("America/New_York")).date()
            cutoff = today_et - timedelta(days=DIVIDEND_LOOKBACK_DAYS)

            holdings = await queries.get_all_stock_holdings()
            if not holdings:
                return

            # Batch: one yfinance fetch per distinct ticker, shared across holders.
            tickers = sorted({h["ticker"] for h in holdings})
            divs: dict[str, list[tuple[str, float]]] = {}
            for sym in tickers:
                try:
                    divs[sym] = await fetch_dividends(sym)
                except Exception:
                    log.exception("dividend_loop: fetch failed for %s", sym)
                    divs[sym] = []
                await asyncio.sleep(0)  # cooperatively yield between tickers

            # ticker -> {ex_date -> per_share} restricted to the recent window.
            recent: dict[str, dict[str, float]] = {}
            for sym, rows in divs.items():
                keep = {ex: ps for ex, ps in rows if ex >= cutoff.isoformat()}
                if keep:
                    recent[sym] = keep
            if not recent:
                return

            # (sym, ex_date, per_share) -> [(uid, amount, shares)] credited this cycle.
            paid: dict[tuple[str, str, float], list[tuple[str, float, float]]] = {}
            for h in holdings:
                sym, uid, shares = h["ticker"], h["discord_user"], h["shares"]
                if sym not in recent or abs(shares) <= 1e-9:
                    continue
                for ex_date, per_share in recent[sym].items():
                    if await queries.dividend_paid(uid, sym, ex_date):
                        continue
                    amount = round(shares * per_share, 2)
                    if amount <= 0:  # shorts owe dividends; skip rather than debit here
                        await queries.record_dividend(uid, sym, ex_date, 0.0)
                        continue
                    await queries.adjust_stock_cash(uid, amount)
                    await queries.record_dividend(uid, sym, ex_date, amount)
                    paid.setdefault((sym, ex_date, per_share), []).append((uid, amount, shares))

            if not paid:
                return
            await self._post_dividend_digest(paid)
        except Exception:
            log.exception("dividend loop failed")

    @dividend_loop.before_loop
    async def _before_dividend_loop(self) -> None:
        await self.bot.wait_until_ready()

    async def _post_dividend_digest(
        self, paid: dict[tuple[str, str, float], list[tuple[str, float, float]]]
    ) -> None:
        """Post one consolidated dividend digest to the alerts channel. Every holder is
        already credited; only opted-in holders get @-pinged (mentions must live in the
        message CONTENT to notify — same rule as earnings)."""
        channel = self.bot.get_channel(await self._alerts_channel_id())
        if channel is None:
            return
        optin = await queries.get_stock_alert_optin_users()
        lines: list[str] = []
        for (sym, _ex, per_share), credits in sorted(paid.items()):
            for uid, amount, shares in sorted(credits, key=lambda c: c[1], reverse=True):
                who = f"<@{uid}>" if uid in optin else "a holder"
                lines.append(
                    f"💵 **{sym}** paid ${per_share:.2f}/sh — {who} received "
                    f"{_fmt_money(amount)} ({shares:g} sh)"
                )
        if not lines:
            return
        # Mentions must live in message CONTENT to notify (an embed body doesn't ping).
        # Cap to Discord's 2000-char content limit.
        out = ["**💵 Dividends paid**"]
        used = len(out[0])
        for i, ln in enumerate(lines):
            if used + len(ln) + 1 > 1990:
                out.append(f"_…and {len(lines) - i} more_")
                break
            out.append(ln)
            used += len(ln) + 1
        content = "\n".join(out)
        await channel.send(content=content,
                           allowed_mentions=discord.AllowedMentions(users=True))

    stock = app_commands.Group(name="stock", description="Stock quotes, portfolio, and market data")
    option = app_commands.Group(
        name="option", description="Track option contracts (calls/puts)", parent=stock
    )
    monitor = app_commands.Group(
        name="monitor", description="Price alerts for a ticker", parent=stock
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

    # ── Price monitors ───────────────────────────────────────────────────────

    @monitor.command(name="add", description="Alert me when a ticker crosses a price")
    @app_commands.describe(
        ticker="Ticker — stock/ETF (AAPL) or crypto (BTC, ETH)",
        direction="Fire when the price goes above or below your target",
        price="Target price",
    )
    @app_commands.choices(direction=[
        app_commands.Choice(name="above", value="above"),
        app_commands.Choice(name="below", value="below"),
    ])
    async def monitor_add(
        self, interaction: discord.Interaction,
        ticker: str, direction: app_commands.Choice[str], price: float,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        symbol = _normalize_symbol(ticker)
        if price <= 0:
            await interaction.followup.send("Target price must be positive.", ephemeral=True)
            return
        try:
            quote = await fetch_quote(symbol)
        except Exception:
            await interaction.followup.send(
                f"Couldn't find a price for `{_display_ticker(symbol)}`.", ephemeral=True,
            )
            return
        cur = quote["price"]
        # Reject if the condition is already true — it would fire instantly.
        if _manual_triggered(direction.value, price, cur):
            await interaction.followup.send(
                f"`{_display_ticker(symbol)}` is already {direction.value} "
                f"${price:,.2f} (now ${cur:,.2f}). Pick a target on the other side.",
                ephemeral=True,
            )
            return
        mid = await queries.add_stock_monitor(
            str(interaction.user.id), str(interaction.channel_id),
            symbol, direction.value, price,
        )
        await interaction.followup.send(
            f"✅ Monitor **#{mid}**: I'll ping you here when "
            f"**{_display_ticker(symbol)}** goes **{direction.value} ${price:,.2f}** "
            f"(now ${cur:,.2f}).",
            ephemeral=True,
        )

    @monitor.command(name="list", description="Your active price monitors")
    async def monitor_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        mons = await queries.get_stock_monitors_for_user(str(interaction.user.id))
        if not mons:
            await interaction.followup.send(
                "You have no active monitors. Add one with `/stock monitor add`.",
                ephemeral=True,
            )
            return
        lines = [
            f"**#{m['monitor_id']}** — {_display_ticker(m['ticker'])} "
            f"{m['direction']} ${m['target_price']:,.2f}"
            for m in mons
        ]
        embed = discord.Embed(
            title="📡 Your Price Monitors", description="\n".join(lines), colour=0x3498DB,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @monitor.command(name="remove", description="Cancel a price monitor")
    @app_commands.describe(monitor_id="ID from /stock monitor list")
    async def monitor_remove(
        self, interaction: discord.Interaction, monitor_id: int,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        ok = await queries.delete_stock_monitor(monitor_id, str(interaction.user.id))
        if ok:
            await interaction.followup.send(f"🗑️ Removed monitor **#{monitor_id}**.", ephemeral=True)
        else:
            await interaction.followup.send(
                f"No monitor **#{monitor_id}** of yours found.", ephemeral=True,
            )

    @monitor.command(name="channel", description="Set the channel for auto portfolio-swing alerts")
    @app_commands.describe(channel="Channel where ≥10% daily swing alerts are posted")
    async def monitor_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel,
    ) -> None:
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "You need **Manage Server** to set the alerts channel.", ephemeral=True,
            )
            return
        await queries.set_bot_setting(_ALERTS_CHANNEL_SETTING, str(channel.id))
        await interaction.response.send_message(
            f"✅ Portfolio-swing alerts will post in {channel.mention}.", ephemeral=True,
        )

    async def _alerts_channel_id(self) -> int:
        raw = await queries.get_bot_setting(_ALERTS_CHANNEL_SETTING)
        try:
            return int(raw) if raw else DEFAULT_STOCK_ALERTS_CHANNEL_ID
        except ValueError:
            return DEFAULT_STOCK_ALERTS_CHANNEL_ID

    @stock.command(name="alerts", description="Opt in/out of swing + earnings alerts on your holdings")
    @app_commands.describe(enabled="On to get pinged on ≥10% daily swings and when a holding reports earnings; off to stop (default: off)")
    @app_commands.choices(enabled=[
        app_commands.Choice(name="on", value="on"),
        app_commands.Choice(name="off", value="off"),
    ])
    async def stock_alerts(self, interaction: discord.Interaction, enabled: app_commands.Choice[str]) -> None:
        on = enabled.value == "on"
        await queries.set_stock_alerts_enabled(str(interaction.user.id), on)
        msg = ("✅ You'll get pinged on ≥10% daily swings and when a holding reports earnings."
               if on else "✅ Swing + earnings alerts are off for you.")
        await interaction.response.send_message(msg, ephemeral=True)

    # ── Rohan's picks ─────────────────────────────────────────────────────────
    @stock.command(name="picks-channel", description="Set the channel for Rohan's stock-pick alerts")
    @app_commands.describe(channel="Where to ping the 'rohan stock picks' role on each of his trades")
    async def picks_channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("You need **Manage Server**.", ephemeral=True)
            return
        await queries.set_bot_setting(_ROHAN_PICKS_CHANNEL_SETTING, str(channel.id))
        await interaction.response.send_message(
            f"✅ Rohan's pick alerts will post in {channel.mention}.", ephemeral=True)

    async def _maybe_post_rohan_pick(self, interaction: discord.Interaction, action: str, detail: str) -> None:
        """Ping the 'rohan stock picks' role when Rohan records a trade."""
        if str(interaction.user.id) != ROHAN_USER_ID:
            return
        try:
            raw = await queries.get_bot_setting(_ROHAN_PICKS_CHANNEL_SETTING)
            cid = int(raw) if raw else DEFAULT_STOCK_ALERTS_CHANNEL_ID
            ch = self.bot.get_channel(cid) or await self.bot.fetch_channel(cid)
            guild = getattr(ch, "guild", None) or interaction.guild
            role = discord.utils.find(
                lambda r: "rohan" in r.name.lower() and "pick" in r.name.lower(), guild.roles) if guild else None
            mention = role.mention if role else "**rohan stock picks**"
            color = 0x9ece6a if action == "BOUGHT" else 0xf7768e if action == "SOLD" else 0x7aa2f7
            embed = discord.Embed(title="📈 New Rohan pick", description=f"Rohan just **{action}** {detail}", color=color)
            embed.set_footer(text="You're getting this because you have the rohan stock picks role.")
            await ch.send(content=f"{mention}", embed=embed,
                          allowed_mentions=discord.AllowedMentions(roles=True))
        except Exception:
            log.exception("failed to post Rohan pick alert")

    # ── Monitor polling loop ─────────────────────────────────────────────────

    @tasks.loop(minutes=MONITOR_POLL_MINUTES)
    async def monitor_loop(self) -> None:
        """Poll watched tickers and fire manual + auto-swing alerts."""
        try:
            monitors = await queries.get_active_stock_monitors()
            holdings = await queries.get_all_stock_holdings()
        except Exception:
            log.exception("monitor_loop: failed to load monitors/holdings")
            return
        tickers = {m["ticker"] for m in monitors} | {h["ticker"] for h in holdings}
        if not tickers:
            return
        try:
            quotes = await fetch_quotes(list(tickers))
        except Exception:
            log.exception("monitor_loop: quote fetch failed")
            return
        await self._fire_manual_alerts(monitors, quotes)
        await self._fire_swing_alerts(holdings, quotes)

    async def _fire_manual_alerts(
        self, monitors: list[dict], quotes: dict[str, dict],
    ) -> None:
        for m in monitors:
            q = quotes.get(m["ticker"])
            if not q:
                continue
            if not _manual_triggered(m["direction"], m["target_price"], q["price"]):
                continue
            await queries.deactivate_stock_monitor(m["monitor_id"])
            arrow = "📈" if m["direction"] == "above" else "📉"
            alert_text = (
                f"{arrow} <@{m['discord_user']}> **{_display_ticker(m['ticker'])}** is "
                f"now **${q['price']:,.2f}** — {m['direction']} your "
                f"${m['target_price']:,.2f} target."
            )
            try:
                channel = self.bot.get_channel(int(m["channel_id"])) or await self.bot.fetch_channel(int(m["channel_id"]))
                await channel.send(alert_text)
            except (discord.NotFound, discord.Forbidden):
                try:
                    user = await self.bot.fetch_user(int(m["discord_user"]))
                    await user.send(alert_text)
                except Exception:
                    pass
            except discord.HTTPException:
                log.exception("monitor_loop: failed to post manual alert")

    async def _fire_swing_alerts(
        self, holdings: list[dict], quotes: dict[str, dict],
    ) -> None:
        # Dedup per ET market day, not UTC — UTC midnight is 8pm ET, which would
        # reset the bands mid-evening and re-fire every swing already alerted today.
        today = _market_today()
        # Opt-in only: never ping a holder who hasn't enabled portfolio swing alerts.
        try:
            opted_in = await queries.get_stock_alert_optin_users()
        except Exception:
            log.exception("monitor_loop: failed to load stock-alert opt-ins")
            return
        # Group holders by ticker (opted-in users only)
        holders: dict[str, list[str]] = {}
        for h in holdings:
            if h["discord_user"] in opted_in:
                holders.setdefault(h["ticker"], []).append(h["discord_user"])
        channel = self.bot.get_channel(await self._alerts_channel_id())
        changed = False
        for ticker, uids in holders.items():
            q = quotes.get(ticker)
            if not q:
                continue
            if q["price"] < SWING_MIN_PRICE:
                continue  # penny stocks swing wildly on noise; don't ping
            pct = _swing_pct(q["price"], q.get("prev_close"))
            band = int(abs(pct) // 10)  # 1 = 10–19%, 2 = 20–29%, … (0 = below threshold)
            if band < 1:
                continue
            # One alert per TICKER per 10% band per day — so 10→12% doesn't re-fire
            # (but 10→20% does), and it pings EVERYONE holding it in a single message
            # (a late buyer doesn't trigger a duplicate). Persisted across restarts.
            st = self._swing_alerted.get(ticker)
            last_band = st["band"] if (st and st.get("date") == today) else 0
            if band <= last_band or channel is None:
                continue
            self._swing_alerted[ticker] = {"date": today, "band": band}
            changed = True
            sign = "🟢 +" if pct >= 0 else "🔴 "
            mentions = " ".join(f"<@{uid}>" for uid in uids)
            prev = q.get("prev_close") or q["price"]
            try:
                await channel.send(
                    f"⚠️ **{_display_ticker(ticker)}** {sign}{pct:.1f}% today "
                    f"(${_fmt_price(prev)} → ${_fmt_price(q['price'])}) — {mentions}"
                )
            except discord.HTTPException:
                log.exception("monitor_loop: failed to post swing alert")
        if changed:  # persist (today only) so restarts don't re-alert
            self._swing_alerted = {k: v for k, v in self._swing_alerted.items() if v.get("date") == today}
            await queries.set_bot_setting(_SWING_STATE_SETTING, json.dumps(self._swing_alerted))

    @monitor_loop.before_loop
    async def _before_monitor_loop(self) -> None:
        await self.bot.wait_until_ready()

    # ── /stock lookup ────────────────────────────────────────────────────────

    @stock.command(name="lookup", description="Look up a stock's current price")
    @app_commands.describe(ticker="Ticker — stock/ETF (AAPL) or crypto (BTC, ETH)")
    async def lookup(self, interaction: discord.Interaction, ticker: str) -> None:
        await interaction.response.defer()
        symbol = _normalize_symbol(ticker)
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
        embed.add_field(name="Price", value=f"`{_fmt_price(price, quote['currency'])}`", inline=True)
        embed.add_field(name="Today", value=f"`{_fmt_change(change, pct)}`", inline=True)

        ext = quote.get("extended")
        if ext:
            label = "After Hours" if ext["session"] == "post" else "Pre-Market"
            embed.add_field(
                name=label,
                value=(
                    f"`{_fmt_price(ext['price'], quote['currency'])}` · "
                    f"`{_fmt_change(ext['change'], ext['pct'])}`"
                ),
                inline=True,
            )

        pos = await queries.get_ticker_position(str(interaction.user.id), symbol)
        if pos:
            shares = pos["shares"]
            realized = pos["realized_pnl"]
            lines: list[str] = []
            if shares != 0:   # still holding — show the open leg + unrealized
                dca = pos["dca_price"]
                cost = shares * dca
                value = shares * price
                pl = value - cost
                pl_pct = (pl / abs(cost) * 100) if cost else 0.0
                qty = f"short `{abs(shares):g}`" if shares < 0 else f"`{shares:g}`"
                lines.append(f"{qty} sh @ DCA `{dca:,.2f}`")
                lines.append(f"Value: `{_fmt_money(value, quote['currency'])}`")
                lines.append(f"Unrealized P/L: `{_fmt_change(pl, pl_pct)}`")
            if abs(realized) > 1e-9:   # realized from any shares sold (incl. sold-out)
                lines.append(f"Realized P/L: `{_fmt_pnl(realized)}`")
            if lines:
                title = "Your Position" if shares != 0 else "Your History"
                embed.add_field(name=title, value="\n".join(lines), inline=False)

        embed.add_field(name="Yahoo Finance", value=f"[Open ↗]({url})", inline=False)

        png = await _intraday_png(symbol, prev)
        if png:
            embed.set_image(url="attachment://intraday.png")
            await interaction.followup.send(
                embed=embed, file=discord.File(io.BytesIO(png), filename="intraday.png")
            )
        else:
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

    # ── /stock server ────────────────────────────────────────────────────────

    @stock.command(name="server", description="Server-wide portfolio: holdings, P/L, and value over time")
    async def server(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        data = await _compute_server_portfolio()
        if data is None or data["num_traders"] == 0:
            await interaction.followup.send(
                "Nobody on the server has logged any stock trades yet.", ephemeral=True
            )
            return
        snap_rows = await queries.get_all_portfolio_snapshots()
        name = interaction.guild.name if interaction.guild else "Server"
        embed = _build_server_overview_embed(name, data)
        view = ServerPortfolioView(name, data, snap_rows, interaction.user.id)
        msg = await interaction.followup.send(embed=embed, view=view)
        view.message = msg

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

    # ── /stock gains ─────────────────────────────────────────────────────────

    @stock.command(name="gains", description="Manually add realized gains (credits cash + logs to realized P/L)")
    @app_commands.describe(
        amount="Realized gain to add (use a negative number for a realized loss)",
        note="Optional note (e.g. 'pre-2026 Robinhood gains')",
    )
    async def gains(
        self,
        interaction: discord.Interaction,
        amount: float,
        note: str | None = None,
    ) -> None:
        if amount == 0:
            await interaction.response.send_message("Amount can't be 0.", ephemeral=True)
            return
        uid = str(interaction.user.id)
        await queries.add_realized_adjustment(uid, amount, note)
        # Cash floored at 0 (a realized loss still books to P/L via the adjustment
        # above, but never drives the cash balance negative).
        new_cash = await queries.adjust_stock_cash(uid, amount)
        cash_line = f"\nCash: **${new_cash:,.2f}**"
        await interaction.response.send_message(
            f"Logged realized {'gain' if amount > 0 else 'loss'} of "
            f"`{_fmt_pnl(amount)}` to your P/L and cash.{cash_line}",
            ephemeral=True,
        )

    # ── /stock buy ───────────────────────────────────────────────────────────

    @stock.command(name="buy", description="Record a stock purchase")
    @app_commands.describe(
        ticker="Ticker — stock/ETF (AAPL) or crypto (BTC, ETH)",
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
        await self._record_buy(interaction, ticker, shares, price, date, notes)

    async def _record_buy(self, interaction, ticker, shares, price, date, notes, *, verb="BUY") -> None:
        """Shared body for /stock buy and /stock cover. `verb` only changes the wording —
        a buy and a cover are the same trade (a buy that closes an open short first)."""
        symbol = _normalize_symbol(ticker)
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

        await interaction.response.defer(ephemeral=True)
        warn = ""
        if date is None:   # near the live price — warn (don't block; people may log old data)
            ok, live = await live_price_check(symbol, price)
            if not ok and live:
                warn = (f"\n⚠️ heads up: `{price:,.2f}` is {abs(price - live) / live * 100:.0f}% off the live "
                        f"**{symbol}** ${live:,.2f} — recorded anyway (pass `date:` for a real past fill).")

        # A buy covers any open short first; realize P/L on the covered portion
        # (avg short entry − cover price) before the trade is written.
        current = await queries.get_stock_holding(str(interaction.user.id), symbol)
        net = current["shares"] if current else 0.0
        avg = current["dca_price"] if current else 0.0
        covered = min(shares, max(-net, 0.0))
        realized = covered * (avg - price)
        if verb == "COVER" and covered <= 1e-9:
            await interaction.followup.send(
                f"You have no open **{symbol}** short to cover — use `/stock buy` to go long.",
                ephemeral=True)
            return

        await queries.add_stock_trade(
            str(interaction.user.id), symbol, "buy", shares, price, executed_at, notes
        )
        # Floored at 0: a buy spends available cash, and any excess is funded by
        # money the user already had (most people never /stock cash deposit). Cash
        # only becomes meaningful once selling generates proceeds — so a buy never
        # drives the balance negative (which used to make account value go negative).
        new_cash = await queries.adjust_stock_cash(
            str(interaction.user.id), -(shares * price))
        await _check_achievements(interaction)
        holding = await queries.get_stock_holding(str(interaction.user.id), symbol)
        position_line = _fmt_position(holding)
        realized_line = f" · realized P/L `{_fmt_pnl(realized)}`" if covered > 1e-9 else ""
        cash_line = f"\nCash: **${new_cash:,.2f}**"
        await interaction.followup.send(
            f"Recorded **{verb}** `{symbol}` — {shares:g} sh @ `{price:,.2f}` "
            f"(−${shares * price:,.2f}){realized_line}.\n{position_line}{cash_line}{warn}",
            ephemeral=True,
        )
        await self._maybe_post_rohan_pick(interaction, "BOUGHT", f"**{shares:g}** {symbol} @ ${price:,.2f}")
        await _award_trade_xp(interaction)

    # ── /stock sell ──────────────────────────────────────────────────────────

    @stock.command(name="sell", description="Record a stock sale (realized P/L is computed)")
    @app_commands.describe(
        ticker="Pick one of your holdings (or type a ticker)",
        shares="Number of shares sold (fractional ok)",
        price="Execution price per share",
        date="Optional trade date (YYYY-MM-DD or YYYY-MM-DD HH:MM, UTC). Defaults to now.",
        notes="Optional note",
    )
    @app_commands.autocomplete(ticker=_holdings_autocomplete)
    async def sell(
        self,
        interaction: discord.Interaction,
        ticker: str,
        shares: float,
        price: float,
        date: str | None = None,
        notes: str | None = None,
    ) -> None:
        await self._record_sell(interaction, ticker, shares, price, date, notes)

    async def _record_sell(self, interaction, ticker, shares, price, date, notes, *, verb="SELL") -> None:
        """Shared body for /stock sell and /stock short. `verb` only changes the wording —
        a sell and a short are the same trade (a sell past flat opens a short)."""
        symbol = _normalize_symbol(ticker)
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

        await interaction.response.defer(ephemeral=True)
        sell_warn = ""
        if date is None:   # near the live price — warn (don't block)
            ok, live = await live_price_check(symbol, price)
            if not ok and live:
                sell_warn = (f"\n⚠️ heads up: `{price:,.2f}` is {abs(price - live) / live * 100:.0f}% off the live "
                             f"**{symbol}** ${live:,.2f} — recorded anyway (pass `date:` for a real past fill).")

        # Selling beyond a held long opens/extends a SHORT (no guard). Realize
        # P/L only on the long portion this sell closes (price − avg cost); the
        # short-opening remainder realizes nothing until it's covered.
        current = await queries.get_stock_holding(str(interaction.user.id), symbol)
        net = current["shares"] if current else 0.0
        avg_cost = current["dca_price"] if current else 0.0
        closed_qty = min(shares, max(net, 0.0))
        realized = closed_qty * (price - avg_cost)

        await queries.add_stock_trade(
            str(interaction.user.id), symbol, "sell", shares, price, executed_at, notes
        )
        new_cash = await queries.adjust_stock_cash(
            str(interaction.user.id), shares * price, allow_negative=True)
        await _check_achievements(interaction)
        holding = await queries.get_stock_holding(str(interaction.user.id), symbol)
        position_line = _fmt_position(holding)
        realized_line = f" · realized P/L `{_fmt_pnl(realized)}`" if closed_qty > 1e-9 else ""
        short_note = ""
        if holding and holding["shares"] < 0:
            short_note = (f"\n📉 You're now **short {abs(holding['shares']):g}** sh — "
                          f"buy/cover to close.")
        elif verb == "SHORT":   # sold into an existing long — reduced it, didn't go short
            short_note = "\nℹ️ This reduced your long — you're not short yet."
        cash_line = f"\nCash: **${new_cash:,.2f}**" + (
            " ⚠️ you're in the red" if new_cash < 0 else "")
        await interaction.followup.send(
            f"Recorded **{verb}** `{symbol}` — {shares:g} sh @ `{price:,.2f}` "
            f"(+${shares * price:,.2f}){realized_line}."
            f"\n{position_line}{short_note}{cash_line}{sell_warn}",
            ephemeral=True,
        )
        await self._maybe_post_rohan_pick(interaction, "SOLD", f"**{shares:g}** {symbol} @ ${price:,.2f}")
        await _award_trade_xp(interaction)

    # ── /stock short + /stock cover ───────────────────────────────────────────

    @stock.command(name="short", description="Open or add to a short position (sell shares you don't own)")
    @app_commands.describe(
        ticker="Ticker to short (stock/ETF or crypto)",
        shares="Number of shares to short (fractional ok)",
        price="Execution price per share",
        date="Optional trade date (YYYY-MM-DD or YYYY-MM-DD HH:MM, UTC). Defaults to now.",
        notes="Optional note",
    )
    async def short(
        self,
        interaction: discord.Interaction,
        ticker: str,
        shares: float,
        price: float,
        date: str | None = None,
        notes: str | None = None,
    ) -> None:
        await self._record_sell(interaction, ticker, shares, price, date, notes, verb="SHORT")

    @stock.command(name="cover", description="Buy to cover (close or reduce) an open short")
    @app_commands.describe(
        ticker="Pick the short you're covering (or type a ticker)",
        shares="Number of shares to buy back (fractional ok)",
        price="Execution price per share",
        date="Optional trade date (YYYY-MM-DD or YYYY-MM-DD HH:MM, UTC). Defaults to now.",
        notes="Optional note",
    )
    @app_commands.autocomplete(ticker=_holdings_autocomplete)
    async def cover(
        self,
        interaction: discord.Interaction,
        ticker: str,
        shares: float,
        price: float,
        date: str | None = None,
        notes: str | None = None,
    ) -> None:
        await self._record_buy(interaction, ticker, shares, price, date, notes, verb="COVER")

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
        symbol = _normalize_symbol(ticker) if ticker else None
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

    # ── /stock edit ──────────────────────────────────────────────────────────

    @stock.command(name="edit", description="Fix one of your trades — pick it from a dropdown")
    @app_commands.describe(ticker="Optional: only show trades for this ticker")
    async def edit_trade(
        self, interaction: discord.Interaction, ticker: str | None = None,
    ) -> None:
        uid = str(interaction.user.id)
        symbol = _normalize_symbol(ticker) if ticker else None
        trades = await queries.get_stock_trades(uid, symbol)
        if not trades:
            await interaction.response.send_message(
                "You have no trades to edit." + (f" (none for `{symbol}`)" if symbol else ""),
                ephemeral=True)
            return
        recent = list(reversed(trades))[:25]
        await interaction.response.send_message(
            "Pick a trade to fix — you'll get a form to correct the shares / price / date:",
            view=_TradeEditView(recent), ephemeral=True)

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

        await interaction.response.defer(ephemeral=True)
        opt_warn = ""
        if date is None:   # warn (don't block) if the premium looks off for a "now" trade
            ok, reason = await live_option_check(sym, otype, strike, expiry_iso, premium)
            if not ok:
                opt_warn = f"\n⚠️ heads up: {reason} — recorded anyway (pass `date:` for a real past fill)."

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
        # Premium flows through cash: buying debits, selling/writing credits.
        # Floored at 0 (like /stock buy) — a debit never drives cash negative.
        cash_flow = (-1 if side == "buy" else 1) * contracts * premium * OPTION_MULTIPLIER
        new_cash = await queries.adjust_stock_cash(uid, cash_flow)

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
        cash_line = f"\nCash: **${new_cash:,.2f}**" + (
            " ⚠️ you're in the red" if new_cash < 0 else "")
        await interaction.followup.send(
            f"Recorded **{side.upper()}** {contracts} × `{spec}` @ `{premium:,.2f}` "
            f"({'−' if cash_flow < 0 else '+'}${abs(cash_flow):,.2f})"
            f"{realized_line}.\n{position_line}{cash_line}{opt_warn}",
            ephemeral=True,
        )
        await self._maybe_post_rohan_pick(
            interaction, "BOUGHT" if side == "buy" else "SOLD",
            f"{contracts} × `{spec}` option @ ${premium:,.2f}")
        await _award_trade_xp(interaction)

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
        hidden_expired = 0
        for p in open_positions:
            key = (p["underlying"], p["opt_type"], p["strike"], p["expiry"])
            info = prices.get(key, {})
            premium = info.get("premium")
            expired = info.get("expired", _is_expired(p["expiry"]))
            pl = _option_position_pnl(p, premium)
            # Skip expired worthless positions — they clutter the list but have no value
            if expired and (premium is None or premium <= 0) and pl["value"] <= 0:
                hidden_expired += 1
                continue
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
            gk = option_greeks(p, info)
            greeks = (f"\n   ┕ IV `{gk['iv'] * 100:.0f}%` · Δ `{gk['delta']:+.2f}` · "
                      f"Θ `{gk['theta']:+.2f}/d` · `{gk['dte']}d` left") if gk else ""
            lines.append(
                f"{emoji} {label}{flag} · {side_word} {abs(net)} @ {p['avg_premium']:,.2f} → "
                f"**{premium:,.2f}** · P/L `{sign}{u:,.2f} ({sign}{upct:.2f}%)`{greeks}"
            )

        if hidden_expired:
            lines.append(f"_({hidden_expired} expired worthless position{'s' if hidden_expired != 1 else ''} hidden)_")
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
