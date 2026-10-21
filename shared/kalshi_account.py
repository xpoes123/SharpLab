"""Per-user Kalshi account reads (positions, PnL) with a user-supplied read-only API key.

PnL math ported from vigil-core live/pnl.py (reconciled there against 363 real settlements):
settled PnL = revenue + matched YES/NO pairs − yes cost − no cost − fees. Kalshi pays a held
YES+NO pair $1 *outside* `revenue`, so skipping the pair credit badly understates PnL.
Open positions are marked at what you could sell for now (yes bid, or 1 − yes ask for NO).

Private keys are Fernet-encrypted at rest with KALSHI_CRED_KEY (.env); GETs only.
"""
from __future__ import annotations

import base64
import os
import time
from collections import defaultdict
from urllib.parse import urlsplit

import httpx
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
SPORT = {"NBA": "NBA", "WNBA": "WNBA", "NFL": "NFL", "MLB": "MLB", "NHL": "NHL", "NCAA": "NCAA",
         "VALORANT": "Esports", "CS2": "Esports", "DOTA2": "Esports", "LOL": "Esports",
         "ATP": "Tennis", "WTA": "Tennis", "ITF": "Tennis", "BTC": "Crypto", "ETH": "Crypto"}
# ponytail: one global filter (David's ask, 2026-10-21; he's the only linked user) — per-user prefs if others link
TRACKED = {"NBA", "NFL", "MLB", "Esports"}


class KalshiAuthError(Exception):
    pass


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _fernet() -> Fernet:
    key = os.getenv("KALSHI_CRED_KEY")
    if not key:
        raise RuntimeError("KALSHI_CRED_KEY not set")
    return Fernet(key)


def encrypt(pem: str) -> bytes:
    return _fernet().encrypt(pem.encode())


def decrypt(blob: bytes) -> str:
    return _fernet().decrypt(blob).decode()


def load_key(pem: str) -> rsa.RSAPrivateKey:
    """Parse a pasted PEM. Discord can flatten newlines to spaces, so rebuild the body if needed."""
    pem = pem.strip()
    if "\n" not in pem and "-----" in pem:
        # "-----BEGIN RSA PRIVATE KEY----- MIIE... -----END RSA PRIVATE KEY-----"
        parts = pem.split("-----")
        if len(parts) >= 5:
            begin, body, end = parts[1], parts[2], parts[3]
            pem = f"-----{begin}-----\n" + "\n".join(body.split()) + f"\n-----{end}-----\n"
    key = serialization.load_pem_private_key(pem.encode(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValueError("not an RSA private key")
    return key


def sign_headers(key_id: str, key: rsa.RSAPrivateKey, path: str, ts_ms: int | None = None) -> dict:
    ts = str(ts_ms if ts_ms is not None else int(time.time() * 1000))
    msg = f"{ts}GET{urlsplit(KALSHI_BASE).path}{path.split('?')[0]}".encode()
    sig = key.sign(msg, padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                    salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
    return {"KALSHI-ACCESS-KEY": key_id, "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode()}


async def _get(client: httpx.AsyncClient, key_id: str, key, path: str) -> dict:
    r = await client.get(KALSHI_BASE + path, headers=sign_headers(key_id, key, path))
    if r.status_code in (401, 403):
        raise KalshiAuthError(f"Kalshi rejected the key ({r.status_code})")
    r.raise_for_status()
    return r.json()


async def _all(client, key_id, key, path: str, field: str) -> list[dict]:
    out, cur = [], ""
    for _ in range(100):  # ponytail: 20k-row ceiling, plenty for any human account
        r = await _get(client, key_id, key, f"{path}?limit=200" + (f"&cursor={cur}" if cur else ""))
        out += r.get(field) or []
        cur = r.get("cursor")
        if not cur:
            break
    return out


def sport(ticker: str) -> str:
    series = ticker.split("-")[0].removeprefix("KX")
    return next((v for k, v in SPORT.items() if series.startswith(k)), "Other")


def settled_pnl(st: dict) -> float:
    pair = min(_f(st.get("yes_count_fp")), _f(st.get("no_count_fp")))
    return (_f(st.get("revenue")) / 100 + pair - _f(st.get("yes_total_cost_dollars"))
            - _f(st.get("no_total_cost_dollars")) - _f(st.get("fee_cost")))


def mark_position(mp: dict, market: dict) -> dict:
    q = _f(mp.get("position_fp") or mp.get("position"))
    # position > 0 = long YES, < 0 = long NO
    if q > 0:
        mark = _f(market.get("yes_bid_dollars"))
    else:
        ask = _f(market.get("yes_ask_dollars"))
        mark = 1 - ask if ask else 0.0
    cost = _f(mp.get("market_exposure_dollars"))
    value = abs(q) * mark
    return {"ticker": mp["ticker"], "title": market.get("title") or mp["ticker"], "sport": sport(mp["ticker"]),
            "side": "YES" if q > 0 else "NO", "qty": abs(q), "cost": cost, "mark": mark,
            "value": value, "unreal": value - cost}


def summarize(settles: list[dict], open_rows: list[dict], since_iso: str | None = None) -> dict:
    by_sport = defaultdict(lambda: [0, 0.0, 0.0])
    realized = fees = 0.0
    n = won = 0
    for st in settles:
        if since_iso and (st.get("settled_time") or "") < since_iso:
            continue
        if sport(st["ticker"]) not in TRACKED:
            continue
        p = settled_pnl(st)
        cost = _f(st.get("yes_total_cost_dollars")) + _f(st.get("no_total_cost_dollars"))
        realized += p
        fees += _f(st.get("fee_cost"))
        n += 1
        won += p > 0
        s = by_sport[sport(st["ticker"])]
        s[0] += 1; s[1] += p; s[2] += cost
    return {"realized": realized, "fees": fees, "n": n, "won": won,
            "unrealized": sum(r["unreal"] for r in open_rows),
            "by_sport": sorted(([k, *v] for k, v in by_sport.items()), key=lambda r: -r[2])}


async def fetch_balance(key_id: str, pem: str) -> float:
    """Cheap auth check used at link time."""
    async with httpx.AsyncClient(timeout=20) as c:
        bal = await _get(c, key_id, load_key(pem), "/portfolio/balance")
    return _balance(bal)


def _balance(bal: dict) -> float:
    return _f(bal["balance_dollars"]) if "balance_dollars" in bal else _f(bal.get("balance")) / 100


async def fetch_account(key_id: str, pem: str, with_settlements: bool = True) -> dict:
    key = load_key(pem)
    async with httpx.AsyncClient(timeout=20) as c:
        bal = await _get(c, key_id, key, "/portfolio/balance")
        positions = await _all(c, key_id, key, "/portfolio/positions", "market_positions")
        positions = [p for p in positions
                     if _f(p.get("position_fp") or p.get("position")) and sport(p["ticker"]) in TRACKED]
        settles = await _all(c, key_id, key, "/portfolio/settlements", "settlements") if with_settlements else []
        markets = {}
        tickers = [p["ticker"] for p in positions]
        for i in range(0, len(tickers), 100):
            r = await c.get(f"{KALSHI_BASE}/markets", params={"tickers": ",".join(tickers[i:i + 100]),
                                                              "limit": 200})
            r.raise_for_status()
            markets.update({m["ticker"]: m for m in r.json().get("markets") or []})
    open_rows = sorted((mark_position(p, markets.get(p["ticker"], {})) for p in positions),
                       key=lambda r: -r["cost"])
    return {"balance": _balance(bal), "open": open_rows, "settlements": settles}


_MONTHS = {m: i for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}


def slate_date(ticker: str) -> str | None:
    """Game date from the event segment: KXNBAPTS-26OCT21MILWAS-... -> '2026-10-21'. Futures -> None."""
    parts = ticker.split("-")
    seg = parts[1] if len(parts) > 1 else ""
    if len(seg) < 7 or seg[2:5] not in _MONTHS or not (seg[:2] + seg[5:7]).isdigit():
        return None
    return f"20{seg[:2]}-{_MONTHS[seg[2:5]]:02d}-{seg[5:7]}"


def slates(settles: list[dict]) -> dict[str, dict]:
    """Settled TRACKED markets grouped by game date:
    {date: {n, won, pnl, cost, sports: {sport: [n, won, pnl]}, rows: [(ticker, pnl)]}}."""
    out: dict[str, dict] = {}
    for st in settles:
        d, sp = slate_date(st["ticker"]), sport(st["ticker"])
        if sp not in TRACKED or not d:
            continue
        p = settled_pnl(st)
        s = out.setdefault(d, {"n": 0, "won": 0, "pnl": 0.0, "cost": 0.0, "sports": {}, "rows": []})
        s["n"] += 1
        s["won"] += p > 0
        s["pnl"] += p
        s["cost"] += _f(st.get("yes_total_cost_dollars")) + _f(st.get("no_total_cost_dollars"))
        per = s["sports"].setdefault(sp, [0, 0, 0.0])
        per[0] += 1; per[1] += p > 0; per[2] += p
        s["rows"].append((st["ticker"], p))
    return out


async def market_titles(tickers: list[str]) -> dict[str, str]:
    if not tickers:
        return {}
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{KALSHI_BASE}/markets", params={"tickers": ",".join(tickers[:100]), "limit": 200})
        r.raise_for_status()
        return {m["ticker"]: m.get("title") or m["ticker"] for m in r.json().get("markets") or []}
