"""Async Kalshi trade-API client with RSA-PSS request signing.

SharpLab's existing Kalshi reads (activities.fetch_kalshi_odds_batch) hit the PUBLIC /markets
endpoint with a Bearer token. The bet-logger needs the AUTHENTICATED /portfolio/fills endpoint,
which requires Kalshi's key-id + private-key signing scheme (djtoolkit.kalshi). Public
market reads (for price snapshots) are signed here too — harmless, keeps one code path.
"""
from __future__ import annotations

from djtoolkit.kalshi import KALSHI_BASE, AsyncKalshiClient, load_key_file


class KalshiClient(AsyncKalshiClient):
    def __init__(self, key_id: str, private_key_path: str, base: str = KALSHI_BASE) -> None:
        super().__init__(key_id, load_key_file(private_key_path), base, timeout=10.0)

    async def _req(self, method: str, path: str) -> dict:
        return await self.request(method, path)

    async def fills(self, min_ts: int | None = None, cursor: str | None = None,
                    limit: int = 200) -> tuple[list[dict], str | None]:
        """Recent fills for the account, newest first. min_ts = unix seconds lower bound.
        Returns (fills, next_cursor). Each fill: trade_id, ticker, order_id, side (yes|no),
        action (buy|sell), count, yes_price, no_price, created_time."""
        q = [f"limit={limit}"]
        if min_ts is not None:
            q.append(f"min_ts={int(min_ts)}")
        if cursor:
            q.append(f"cursor={cursor}")
        r = await self._req("GET", "/portfolio/fills?" + "&".join(q))
        return (r.get("fills") or []), (r.get("cursor") or None)

    async def market(self, ticker: str) -> dict:
        """Single market: normalized price + settlement state. Returns dict with
        yes_bid_c / yes_ask_c / mid_c (cents, None if no book), status, result, close_time."""
        m = (await self._req("GET", f"/markets/{ticker}")).get("market") or {}
        bid, ask = m.get("yes_bid"), m.get("yes_ask")
        last = m.get("last_price")
        if bid is not None and ask is not None:
            mid = (bid + ask) / 2
        elif last is not None:
            mid = float(last)
        else:
            mid = None
        return {
            "ticker": ticker, "yes_bid_c": bid, "yes_ask_c": ask, "mid_c": mid,
            "status": m.get("status"), "result": m.get("result") or "",
            "close_time": m.get("close_time"),
        }
