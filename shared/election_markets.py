"""Click a state on the wall → its races, their Kalshi markets and price charts (the watch page's Election panel and
the TV's state spotlight). Pure: no network, no clock of its own — web/wall_election.py does the fetching and caching.

Which markets a state has: Kalshi's Elections series (GET /series?category=Elections) whose ticker names the state
(SENATEGA, GOVPARTYPA, HOUSEPA7, KXGOVCA, KXAKSENATE… — shared/kalshi_election._RACE) plus KXHOUSERACE, one series
for every House district. Each series' markets come from one public GET /markets?series_ticker=…; a market belongs to
a race through shared/kalshi_election.resolve (party-only markets: this year's only; named ones: on tonight's ballot).

Charts are implied probability over time from Kalshi's public candlesticks (GET /series/{s}/markets/{t}/candlesticks,
no auth; checked 2026-11-03): hourly candles for the last few days + minute candles for today's session, drawn on a
split axis so the evening isn't a sliver. A candle's probability = the mid of its closing yes bid / ask when the spread
is tight, else its last trade.
"""
from __future__ import annotations

from shared import kalshi_election as ke
from shared import kalshi_wall as kw

HOUSERACE = "KXHOUSERACE"
SESSION_S = 12 * 3600       # "today's session": minute candles over the last 12 h
DAYS_S = 4 * 86400          # before that: hourly candles over the last 4 days
MAX_POINTS = 240            # per line, per window (minute candles are thinned to this)
TIGHT = 15.0                # a bid / ask spread (cents) tight enough to trust its mid


# ---- which series a state has ------------------------------------------------------------------------------------------
def state_series(series: list[dict]) -> dict[str, list[str]]:
    """Kalshi's Elections series list → {state: [series tickers that are one of its races]} (KXHOUSERACE not included:
    it covers every state)."""
    out: dict[str, list[str]] = {}
    for s in series:
        t = (s.get("ticker") or "").upper()
        for rx, _office in ke._RACE:
            m = rx.match(t)
            if m and m["st"] in ke.STATES:
                out.setdefault(m["st"], []).append(t)
                break
    return {k: sorted(set(v)) for k, v in out.items()}


def series_of(ticker: str) -> str:
    return (ticker or "").split("-")[0].upper()


def chartable(ticker: str) -> bool:
    """Only midterm race / control markets may be charted (the chart endpoint never proxies anything else)."""
    c = ke.classify(ke.event_of(ticker))
    return bool(c) and c["type"] in ("race", "control")


# ---- a market, priced --------------------------------------------------------------------------------------------------
def prob(bid: float | None, ask: float | None, last: float | None) -> float | None:
    """Implied probability in % (0–100): the mid when the book is two-sided and tight, else the last trade."""
    if bid and ask and 0 < ask - bid <= TIGHT:
        return round((bid + ask) / 2, 1)
    if last:
        return round(last, 1)
    if bid and ask:
        return round((bid + ask) / 2, 1)
    return None


def market_row(m: dict) -> dict:
    """One market as the sheet draws it: prices in cents, implied %, the 24 h change (Kalshi's own day-ago book)."""
    bid, ask, last = kw.cents(m, "yes_bid"), kw.cents(m, "yes_ask"), kw.cents(m, "last_price")
    now = prob(bid, ask, last)
    before = prob(kw.cents(m, "previous_yes_bid"), kw.cents(m, "previous_yes_ask"), kw.cents(m, "previous_price"))
    info = ke.market_info(m) or {}
    p = info.get("pick") or ke.pick(m)
    return {"ticker": m.get("ticker") or "", "event": m.get("event_ticker") or ke.event_of(m.get("ticker") or ""),
            "label": info.get("label") or (m.get("yes_sub_title") or m.get("ticker") or ""),
            "who": p.get("name") or ke.PARTY_NAME.get(p.get("party") or "", "") or (m.get("yes_sub_title") or ""),
            "party": p.get("party") or "", "bid": bid, "ask": ask, "last": last, "prob": now,
            "chg24": round(now - before, 1) if now is not None and before is not None else None,
            "vol24": kw._num(m.get("volume_24h_fp")) or 0.0, "status": m.get("status") or "", "result": m.get("result") or ""}


def _live(m: dict) -> bool:
    return m.get("status") not in ("unopened", "initialized")   # closed / settled stay: a called race keeps its markets


def race_markets(markets: list[dict], races: dict[str, dict], state: str) -> dict[str, list[dict]]:
    """Every market of the state's series (+ KXHOUSERACE) → {race id: [market rows]}, a race's own markets grouped by
    Kalshi event, the busiest event first and, within one, the likeliest pick first. Markets of other states / years /
    offices drop out."""
    by: dict[str, dict[str, list[dict]]] = {}
    seen = set()
    for m in markets:
        tk = m.get("ticker") or ""
        if not tk or tk in seen or not _live(m):
            continue
        c = ke.classify(m.get("event_ticker") or ke.event_of(tk))
        if not c or c["type"] != "race" or c["state"] != state or "state senate" in (m.get("title") or "").lower():
            continue
        r = ke.resolve(c, ke.pick(m), races)
        if not r or r["state"] != state:
            continue
        seen.add(tk)
        by.setdefault(r["id"], {}).setdefault(m.get("event_ticker") or ke.event_of(tk), []).append(market_row(m))
    out = {}
    for rid, events in by.items():
        evs = sorted(events.values(), key=lambda rows: -sum(x["vol24"] for x in rows))
        out[rid] = [x for rows in evs for x in sorted(rows, key=lambda x: -(x["prob"] or 0))]
    return out


def control_markets(markets: list[dict], office: str) -> list[dict]:
    """CONTROLS-2026 / CONTROLH-2026: the chamber-control markets of this election (D / R), likeliest first."""
    rows = []
    for m in markets:
        c = ke.classify(m.get("event_ticker") or ke.event_of(m.get("ticker") or ""))
        if c and c["type"] == "control" and c["office"] == office and _live(m):
            r = market_row(m)
            if not r["party"]:
                r["party"] = (m.get("ticker") or "").rsplit("-", 1)[-1][:1].upper()
            r["who"] = ke.PARTY_NAME.get(r["party"], r["who"])
            rows.append(r)
    return sorted(rows, key=lambda x: -(x["prob"] or 0))


def chart_tickers(rows: list[dict], n: int = 4) -> list[str]:
    """The lines a race's chart draws: its busiest event's picks (the first event in race_markets order), up to n."""
    if not rows:
        return []
    ev = rows[0]["event"]
    return [r["ticker"] for r in rows if r["event"] == ev][:n]


# ---- the state sheet ---------------------------------------------------------------------------------------------------
RACE_KEYS = ("id", "office", "state", "district", "special", "label", "pct", "votes", "leader", "margin", "called", "caller",
             "flip", "watch", "closes", "uncontested")


def call_times(calls: list[dict]) -> dict[str, float]:
    """The results' calls log → {race id: when we saw it called} (0 = called before we started watching)."""
    return {c["id"]: c.get("t") or 0 for c in calls or [] if c.get("id")}


def race_view(r: dict, rows: list[dict], called_at: float | None = None) -> dict:
    out = {k: r.get(k) for k in RACE_KEYS}
    out["cands"] = [{k: c.get(k) for k in ("name", "full", "party", "pct", "votes", "winner", "inc")} for c in r.get("cands") or []][:4]
    out["called_at"] = called_at or None
    out["markets"] = rows
    out["chart"] = chart_tickers(rows)
    out["status"] = status_line(r)
    return out


def status_line(r: dict) -> str:
    if r.get("called"):
        w = next((c for c in r.get("cands") or [] if c.get("winner")), None)
        return f"✓ {(w or {}).get('name') or r['called']} ({r['called']}) · {r.get('caller') or ''}".rstrip(" ·")
    if r.get("votes"):
        lead = (r.get("cands") or [{}])[0]
        return f"{round(r.get('pct') or 0)}% in · {lead.get('name', '')} +{r.get('margin') or 0:g}"
    if r.get("uncontested"):
        return "uncontested"
    return "no votes yet"


OFFICE_ORDER = {"S": 0, "G": 1, "H": 2}


def state_view(state: str, races: list[dict], markets: dict[str, list[dict]], calls: list[dict]) -> dict:
    """{state, name, races: [race_view…]} — Senate, then Governor, then House by district."""
    when = call_times(calls)
    rs = sorted((r for r in races if r.get("state") == state),
                key=lambda r: (OFFICE_ORDER.get(r["office"], 9), r.get("special", False), r.get("district") or 0))
    return {"state": state, "races": [race_view(r, markets.get(r["id"], []), when.get(r["id"])) for r in rs]}


def map_races(races: list[dict]) -> list[dict]:
    """What the map colours from, per race (the watch page computes fills exactly like the TV's map)."""
    keep = ("id", "office", "state", "district", "special", "called", "leader", "pct", "votes", "margin", "flip", "watch")
    return [{k: r.get(k) for k in keep} for r in races]


def state_positions(rows: list[dict], state: str) -> list[dict]:
    """Members' election rows (kalshi_election.enrich'ed) on this state's races. `uid` never leaves the server."""
    out = []
    for r in rows:
        rid = r.get("race") or ""
        parts = rid.split("-")
        if len(parts) >= 2 and parts[0] in ("S", "G", "H") and parts[1] == state:
            out.append({k: v for k, v in r.items() if k not in ("uid", "election", "spark")})
    return out


def control_positions(rows: list[dict], office: str) -> list[dict]:
    return [{k: v for k, v in r.items() if k not in ("uid", "election", "spark")} for r in rows if r.get("race") == f"CTRL-{office}"]


# ---- charts ------------------------------------------------------------------------------------------------------------
def _c(d: dict | None, k: str = "close_dollars") -> float | None:
    v = kw._num((d or {}).get(k))
    return None if v is None else round(v * 100, 2)


def candle_prob(c: dict) -> float | None:
    bid, ask = _c(c.get("yes_bid")), _c(c.get("yes_ask"))
    last = _c(c.get("price")) or _c(c.get("price"), "previous_dollars")
    if bid is not None and ask is not None and 0 < ask - bid <= TIGHT:
        return round((bid + ask) / 2, 1)
    if last:
        return round(last, 1)
    if bid is not None and ask is not None and ask > 0:
        return round((bid + ask) / 2, 1)
    return None


def points(candles: list[dict], start: float | None = None, end: float | None = None, max_points: int = MAX_POINTS) -> list[list[float]]:
    """Candlesticks → [[t (epoch s), %], …] in time order, within [start, end), thinned evenly to max_points (the last
    point always kept)."""
    pts = []
    for c in candles or []:
        t = c.get("end_period_ts")
        p = candle_prob(c)
        if t is None or p is None or (start is not None and t < start) or (end is not None and t >= end):
            continue
        pts.append([float(t), p])
    pts.sort()
    if len(pts) > max_points:
        step = len(pts) / max_points
        thin = [pts[int(i * step)] for i in range(max_points - 1)]
        pts = thin + [pts[-1]]
    return pts


def windows(now: float) -> dict:
    """The two fetch windows, aligned so the cache keys stay put: older = hourly, [now − 4 d, session start) aligned to
    the hour; today = minute, [session start, now)."""
    hour = int(now // 3600) * 3600
    split = hour - SESSION_S + 3600
    return {"older": (hour - DAYS_S, split), "today": (split, int(now)), "split": split}


def change_24h(older: list[list[float]], today: list[list[float]], now: float) -> float | None:
    """Now − 24 h ago, from the chart's own points."""
    allp = (older or []) + (today or [])
    if len(allp) < 2:
        return None
    cut = now - 86400
    before = next((p for t, p in reversed(allp) if t <= cut), None)
    return None if before is None else round(allp[-1][1] - before, 1)


def chart_line(row: dict, older: list[dict], today: list[dict], win: dict, now: float) -> dict:
    o = points(older, win["older"][0], win["split"])
    t = points(today, win["split"], None)
    return {"ticker": row["ticker"], "who": row.get("who") or row.get("label") or row["ticker"], "party": row.get("party") or "",
            "older": o, "today": t, "chg24": change_24h(o, t, now), "now": (t or o or [[0, None]])[-1][1]}


def marks(race: dict | None, called_at: float | None) -> list[dict]:
    """Vertical marks on a race's chart: when its polls close, and when it was called (if we saw it)."""
    out = []
    if race and race.get("closes"):
        out.append({"t": race["closes"], "label": "polls close"})
    if called_at:
        out.append({"t": called_at, "label": f"called · {race.get('called', '')}" if race else "called"})
    return out


def sparkline(line: dict, n: int = 40) -> list[float]:
    """The TV spotlight's small chart: today's points (or the older ones) thinned to n values."""
    pts = line.get("today") or line.get("older") or []
    if len(pts) <= n:
        return [p for _, p in pts]
    step = (len(pts) - 1) / (n - 1)
    return [pts[round(i * step)][1] for i in range(n)]
