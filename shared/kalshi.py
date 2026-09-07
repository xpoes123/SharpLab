"""Async Kalshi trade-API client with RSA-PSS request signing.

SharpLab's existing Kalshi reads (activities.fetch_kalshi_odds_batch) hit the PUBLIC /markets
endpoint with a Bearer token. The bet-logger needs the AUTHENTICATED /portfolio/fills endpoint,
which requires Kalshi's key-id + private-key signing scheme (ported from live-trader). Public
market reads (for price snapshots) are signed here too — harmless, keeps one code path.
"""
from __future__ import annotations

import base64
import time
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


class KalshiClient:
    def __init__(self, key_id: str, private_key_path: str,
                 base: str = "https://api.elections.kalshi.com/trade-api/v2") -> None:
        self.key_id = key_id
        self.base = base.rstrip("/")
        with open(private_key_path, "rb") as f:
            self.pk = serialization.load_pem_private_key(f.read(), password=None)
        self.http = httpx.AsyncClient(timeout=10.0)

    def _headers(self, method: str, path_no_query: str) -> dict:
        ts = str(int(time.time() * 1000))
        sig = self.pk.sign(
            f"{ts}{method}{path_no_query}".encode(),
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
            "Content-Type": "application/json",
        }

    async def _req(self, method: str, path: str) -> dict:
        # Sign the path AFTER the host, INCLUDING /trade-api/v2, WITHOUT the query string.
        sign_path = urlsplit(self.base).path + path.split("?", 1)[0]
        r = await self.http.request(method, self.base + path, headers=self._headers(method, sign_path))
        r.raise_for_status()
        return r.json()

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

    async def aclose(self) -> None:
        await self.http.aclose()
