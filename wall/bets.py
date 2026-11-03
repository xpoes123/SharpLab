"""Members' Kalshi positions for the wall's overlay (the per-tile badges): while a wall runs and bets are shown, the
service asks sharplab-web (POST /api/v1/wall/bets/feed, X-Api-Key = WEB_API_SECRET, over Tailscale — the same path as
the chat) for its tiles' games, each delayed by that tile's D, and keeps the answer for the overlay to poll on loopback
(GET /i/{inst}/api/overlay/bets). The opt-in and the delay are applied on the web; this side never sees a price newer
than (now − D), and never anyone who didn't opt in.

Election night: while an election board / page is up, the same call also asks for the members' midterm positions
(never delayed: results aren't a picture) and sends the results of the races the web named last time ("need"), so the
web's watch page shows the same race status as the TV. shared/kalshi_election.py maps markets to races.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from wall.chat import urllib_http

log = logging.getLogger("wall.bets")
POLL_S = 5.0


class BetsFeed:
    def __init__(self, url: str = "", key: str = "", http: Callable | None = None, clock=time.time) -> None:
        self.configured = bool(url and key)
        self.http = http or (urllib_http(url, key, timeout=6) if self.configured else None)
        self.clock = clock
        self.games: dict[str, list[dict]] = {}
        self.election: list[dict] = []   # members' midterm positions (opt-in filtered on the web), not delayed
        self.need: list[str] = []        # race ids the web wants results for
        self.at = 0.0
        self.error = ""

    def poll(self, games: list[dict], election: dict | None = None) -> None:
        """games = [{league, id, away, home, start, delay}] — the tiles' games (blocking: run it off the event loop).
        election = {"races", "offices"} while election night is on screen (else None: no election rows)."""
        if election is None:
            self.election = []
        if not games and election is None:
            self.games = {}
            return
        body = {"games": games}
        if election is not None:
            body["election"] = election
        try:
            status, data = self.http("POST", "/api/v1/wall/bets/feed", body)
        except (OSError, ValueError) as e:
            self._fail(f"web unreachable: {e}"[:120])
            return
        if status != 200 or not isinstance(data, dict) or not isinstance(data.get("games"), dict):
            self._fail(f"web answered {status}")
            return
        self.games = {k: [r for r in v if isinstance(r, dict)] for k, v in data["games"].items() if isinstance(v, list)}
        if election is not None:
            self.election = [r for r in data.get("election") or [] if isinstance(r, dict)]
            self.need = [x for x in data.get("need") or [] if isinstance(x, str)]
        self.at, self.error = self.clock(), ""

    def _fail(self, why: str) -> None:
        if why != self.error:
            log.warning("bets feed: %s", why)
        self.error = why
        if self.clock() - self.at > 60:   # stale beats nothing for a minute, then nothing (never a frozen ▲ all night)
            self.games, self.election = {}, []

    def idle(self) -> None:
        self.games, self.election = {}, []


def badge_rows(rows: list[dict], limit: int = 4) -> list[dict]:
    """What a tile's badge draws: who, side, market, entry → now, ▲/▼ %. Already delayed + opt-in-filtered on the web."""
    out = []
    for r in rows[:limit]:
        pct = r.get("pct")
        out.append({"who": str(r.get("who") or "")[:16], "side": r.get("side") or "", "label": str(r.get("label") or "")[:28],
                    "entry": r.get("entry"), "mark": r.get("mark"), "dir": r.get("dir") or "",
                    "pct": None if pct is None else round(float(pct)), "size": r.get("size") or "", "pnl": r.get("pnl")})
    return out
