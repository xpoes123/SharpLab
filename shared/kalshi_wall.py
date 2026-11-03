"""Members' Kalshi positions on the watch-party wall: which Kalshi markets belong to which ESPN game, how a position is
drawn, and a spoiler-safe timeline of prices and positions.

Spoilers: Kalshi reacts to a play within a second, the video shows it 20–60 s later. Every price and position sample
is kept with the time it was seen, and a view is always built "as of" a past instant (now − the tile's delay): nothing
seen after that instant can reach it — not the price, not the P&L, not the ▲/▼.

Privacy: only members who turned on sharing appear (kalshi_wall_optin.share = 1); sizes and P&L are bucketed /
percent-only unless they also turned on exact numbers; balances are never read.
"""
from __future__ import annotations

import bisect
import time
from collections import deque
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")

# ESPN abbreviation → Kalshi codes it may appear as (the ESPN one is always tried too). Kalshi isn't consistent across
# series and seasons, so several candidates are fine: a match needs both teams and the date anyway.
ALIASES = {
    "nba": {"GS": ["GSW"], "NY": ["NYK"], "SA": ["SAS"], "NO": ["NOP"], "UTAH": ["UTA"], "WSH": ["WAS"], "BKN": ["BRK", "BKN"],
            "PHX": ["PHO"], "CHA": ["CHO"]},
    "nfl": {"WSH": ["WAS"], "JAX": ["JAC"], "LAR": ["LA"], "LV": ["LVR"]},
}
LEAGUE_PREFIX = {"nba": "KXNBA", "nfl": "KXNFL"}
KINDS = {"GAME": "ml", "SPREAD": "spread", "TOTAL": "total"}


def codes(league: str, abbr: str) -> list[str]:
    a = (abbr or "").upper()
    return [a, *ALIASES.get(league, {}).get(a, [])] if a else []


def et_date(iso: str) -> str:
    """ESPN start (UTC ISO) → the Kalshi event date code of that day in ET, '26NOV03'."""
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ET)
    except (ValueError, AttributeError):
        return ""
    return f"{d:%y}{_MONTHS[d.month - 1]}{d:%d}"


def _day_before(code: str) -> str:
    d = datetime(2000 + int(code[:2]), _MONTHS.index(code[2:5]) + 1, int(code[5:7])) - timedelta(days=1)
    return f"{d:%y}{_MONTHS[d.month - 1]}{d:%d}"


def event_parts(event_ticker: str) -> tuple[str, str, str]:
    """'KXNBAGAME-26NOV03MIALAL' → ('KXNBAGAME', '26NOV03', 'MIALAL'). Some series put a start time after the date
    ('26NOV031930MIALAL'): digits right after the date are dropped."""
    parts = (event_ticker or "").upper().split("-")
    if len(parts) < 2 or len(parts[1]) < 8:
        return (parts[0] if parts else "", "", "")
    key = parts[1]
    date, rest = key[:7], key[7:]
    if not (date[:2].isdigit() and date[2:5] in _MONTHS and date[5:7].isdigit()):
        return parts[0], "", ""
    return parts[0], date, rest.lstrip("0123456789")


def event_ticker_of(ticker: str) -> str:
    """A market ticker's event: the first two dash segments ('KXNBASPREAD-26NOV03MIALAL-LAL5' → '…-26NOV03MIALAL')."""
    return "-".join((ticker or "").split("-")[:2])


def match_event(event_ticker: str, game: dict) -> bool:
    """Is this Kalshi event that ESPN game? game = {league, away, home, start} (ESPN abbreviations, UTC ISO start).
    Same league series, the two teams in the key (either order, any alias) and the ET date (or the day before, for a
    game listed after midnight)."""
    league = game.get("league") or ""
    series, date, teams = event_parts(event_ticker)
    if not teams or not series.startswith(LEAGUE_PREFIX.get(league, "\0")):
        return False
    want = et_date(game.get("start") or "")
    if want and date not in (want, _day_before(want)):
        return False
    aw, hm = codes(league, game.get("away", "")), codes(league, game.get("home", ""))
    return any(teams in (a + h, h + a) for a in aw for h in hm)


def game_key(game: dict) -> str:
    return f"{game.get('league')}:{game.get('id')}"


# ---- how a market and a position are drawn ---------------------------------------------------------------------------
def _num(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def cents(m: dict, field: str) -> float | None:
    """A market price in cents: the *_dollars string ('0.4200') or the legacy cents integer."""
    v = _num(m.get(f"{field}_dollars"))
    if v is not None:
        return round(v * 100, 1)
    v = _num(m.get(field))
    return v


def kind(ticker: str) -> str:
    series = (ticker or "").split("-")[0].upper()
    for pre in LEAGUE_PREFIX.values():
        if series.startswith(pre):
            return KINDS.get(series[len(pre):], "prop")
    return "prop"


def _strike(m: dict, suffix: str) -> str:
    s = _num(m.get("floor_strike"))
    if s is None:
        digits = "".join(c for c in suffix if c.isdigit())
        s = float(digits) + 0.5 if digits else None
    return "" if s is None else (f"{s:g}")


def market_label(ticker: str, m: dict | None = None) -> str:
    """Short, TV-sized: 'LAL win', 'LAL −3.5', 'Over 224.5', or Kalshi's own sub-title for a prop."""
    m = m or {}
    suffix = (ticker or "").split("-")[-1].upper()
    k = kind(ticker)
    team = suffix.rstrip("0123456789")
    if k == "ml":
        return f"{team or suffix} win"
    if k == "spread":
        s = _strike(m, suffix)
        return f"{team} −{s}" if s else team
    if k == "total":
        s = _strike(m, suffix)
        return f"Over {s}" if s else "Over"
    sub = (m.get("yes_sub_title") or m.get("title") or suffix).strip()
    return sub if len(sub) <= 30 else sub[:29] + "…"


SIZE_BUCKETS = ((1000, "1k+"), (250, "250+"), (50, "50+"), (10, "10+"), (1, "<10"))


def size_bucket(qty: float) -> str:
    q = abs(qty)
    return next((lab for lo, lab in SIZE_BUCKETS if q >= lo), "<10")


def side_mark(side: str, bid: float | None, ask: float | None, last: float | None) -> float | None:
    """What one contract of this side sells for now, in cents (YES: the yes bid; NO: 100 − the yes ask), else the last."""
    if side == "YES":
        return bid if bid else last
    if ask:
        return round(100 - ask, 1)
    return round(100 - last, 1) if last is not None else None


# ---- the timeline ------------------------------------------------------------------------------------------------------
class Tape:
    """Time-stamped samples; at(t) = the newest sample seen at or before t, never one after."""

    def __init__(self, keep_s: float = 4 * 3600) -> None:
        self.ts: list[float] = []
        self.vals: list = []
        self.keep_s = keep_s

    def add(self, t: float, val) -> None:
        if self.vals and t >= self.ts[-1] and self.vals[-1] == val:
            return
        i = bisect.bisect_right(self.ts, t)
        self.ts.insert(i, t)
        self.vals.insert(i, val)
        cut = bisect.bisect_left(self.ts, t - self.keep_s)
        if cut:
            del self.ts[:cut], self.vals[:cut]

    def at(self, t: float):
        i = bisect.bisect_right(self.ts, t)
        return self.vals[i - 1] if i else None

    def until(self, t: float, since: float | None = None) -> list[tuple[float, object]]:
        i = bisect.bisect_right(self.ts, t)
        j = bisect.bisect_left(self.ts, since) if since is not None else 0
        return list(zip(self.ts[j:i], self.vals[j:i]))


class Book:
    """Everything the poller has seen: prices per market ticker and opted-in members' positions, each a Tape."""

    def __init__(self) -> None:
        self.prices: dict[str, Tape] = {}      # ticker → (bid, ask, last) in cents
        self.markets: dict[str, dict] = {}     # ticker → {event, label, kind} (static-ish facts, no prices)
        self.positions: dict[str, Tape] = {}   # uid → {ticker: (side, qty, cost_dollars)}
        self.members: dict[str, dict] = {}     # uid → {name, exact}

    def see_market(self, t: float, m: dict) -> None:
        tk = m.get("ticker")
        if not tk:
            return
        self.markets[tk] = {"event": m.get("event_ticker") or event_ticker_of(tk), "label": market_label(tk, m), "kind": kind(tk)}
        self.prices.setdefault(tk, Tape()).add(t, (cents(m, "yes_bid"), cents(m, "yes_ask"), cents(m, "last_price")))

    def see_positions(self, t: float, uid: str, rows: dict[str, tuple[str, float, float]]) -> None:
        self.positions.setdefault(uid, Tape(keep_s=3600)).add(t, dict(rows))

    def forget(self, uid: str) -> None:
        """An opt-out (or unlink) takes effect now, for the past too."""
        self.positions.pop(uid, None)
        self.members.pop(uid, None)


def sparkline(tape: Tape | None, side: str, as_of: float, span_s: float = 3 * 3600, points: int = 40) -> list[float]:
    """The side's mark over the session, sampled evenly up to as_of (never past it)."""
    if not tape:
        return []
    samples = tape.until(as_of, as_of - span_s)
    if not samples:
        return []
    t0 = samples[0][0]
    step = max(1.0, (as_of - t0) / max(1, points - 1))
    out, j = [], 0
    for k in range(points):
        t = t0 + k * step
        if t > as_of:
            break
        while j + 1 < len(samples) and samples[j + 1][0] <= t:
            j += 1
        v = side_mark(side, *samples[j][1])
        if v is not None:
            out.append(v)
    return out


def position_row(book: Book, uid: str, ticker: str, pos: tuple[str, float, float], as_of: float) -> dict | None:
    side, qty, cost = pos
    mkt = book.markets.get(ticker) or {}
    tape = book.prices.get(ticker)
    px = tape.at(as_of) if tape else None
    bid, ask, last = px if px else (None, None, None)
    entry = round(cost / qty * 100, 1) if qty else None   # avg cents per contract
    mark = side_mark(side, bid, ask, last)
    pct = round((mark - entry) / entry * 100, 1) if mark is not None and entry else None
    member = book.members.get(uid) or {}
    exact = bool(member.get("exact"))
    row = {"who": member.get("name") or "Member", "uid": uid, "ticker": ticker, "label": mkt.get("label") or ticker,
           "kind": mkt.get("kind") or kind(ticker), "side": side, "entry": entry, "mark": mark,
           "bid": bid, "ask": ask, "last": last, "pct": pct,
           "dir": "" if pct is None or abs(pct) < 0.5 else ("up" if pct > 0 else "down"),
           "size": f"{qty:g} ct" if exact else size_bucket(qty) + " ct",
           "spark": sparkline(tape, side, as_of)}
    if exact and mark is not None:
        row["pnl"] = round(qty * mark / 100 - cost, 2)
    return row


def game_rows(book: Book, game: dict, as_of: float, sharers: set[str]) -> list[dict]:
    """The opted-in members' positions on this game's markets, as of `as_of`. Anyone not in `sharers` never shows."""
    out = []
    for uid, tape in book.positions.items():
        if uid not in sharers:
            continue
        snap = tape.at(as_of) or {}
        for ticker, pos in snap.items():
            ev = (book.markets.get(ticker) or {}).get("event") or event_ticker_of(ticker)
            if match_event(ev, game):
                r = position_row(book, uid, ticker, pos, as_of)
                if r:
                    out.append(r)
    order = {"ml": 0, "spread": 1, "total": 2, "prop": 3}
    return sorted(out, key=lambda r: (r["who"].lower(), order.get(r["kind"], 9), r["label"]))


class RateLimit:
    """A token bucket for Kalshi calls: `rate` per second, bursts of `burst`. take() → seconds to wait first."""

    def __init__(self, rate: float = 5, burst: int = 10, clock=time.monotonic) -> None:
        self.rate, self.burst, self.clock = rate, burst, clock
        self.tokens, self.at = float(burst), clock()

    def take(self) -> float:
        now = self.clock()
        self.tokens = min(self.burst, self.tokens + (now - self.at) * self.rate)
        self.at = now
        self.tokens -= 1
        return 0.0 if self.tokens >= 0 else -self.tokens / self.rate
