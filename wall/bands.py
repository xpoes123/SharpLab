"""What the overlay draws in the bands the tiles leave (qml/Bands.qml): a header over each tile (its game, delayed),
a calm backdrop tinted by the teams on screen, and, without the L-bar, a slim strip of the other games + the watch
party's chat. The overlay polls GET /i/<inst>/bands (loopback) every 2 s.

Spoilers: a tile's video runs D seconds behind real life, so its header shows the scoreboard as it was D seconds ago
(ScoreTape keeps every scoreboard with the time we first saw it; nothing seen after now − D can reach the header). D comes
from `delay(url) -> (seconds, src)`: the same per-tile setting the bets use (wall/sync.py's SyncStore.delay on the
wall-kalshi branch); until that lands, default_delay = 45 s for every tile. The strip's other games use the default.
Right after a wall starts there is no snapshot that old yet: the header shows the teams and "syncing", never a score.
"""
from __future__ import annotations

import time
from collections import deque
from urllib.parse import urlsplit

from wall.live import LEAGUES, stream_urls

DEFAULT_DELAY_S = 45.0
KEEP_S = 600.0          # the tape's memory (delays are clamped to 120 s)
TICKER_MAX = 12
CHAT_LINES = 4
CHAT_FRESH_S = 20.0


def default_delay(url: str) -> tuple[float, str]:
    """The spoiler delay hook: (seconds, src). Swap for the shared per-tile setting (SyncStore.delay) when it lands."""
    return DEFAULT_DELAY_S, "default"


class ScoreTape:
    """Every scoreboard per league with the time it was first seen; at(t) = the newest one seen at or before t."""

    def __init__(self, keep_s: float = KEEP_S) -> None:
        self.keep_s = keep_s
        self.snaps: dict[str, deque[tuple[float, dict]]] = {}

    def see(self, league: str, t: float, data: dict | None) -> None:
        if not data:
            return
        q = self.snaps.setdefault(league, deque())
        if q and (q[-1][1] is data or q[-1][0] >= t):
            return
        if q and t - q[-1][0] > self.keep_s:   # nobody asked for a while (no wall up): that history is stale, not delayed
            q.clear()
        q.append((t, data))
        while len(q) > 1 and q[1][0] <= t - self.keep_s:   # keep the newest one older than the window, too
            q.popleft()

    def at(self, league: str, t: float) -> dict | None:
        best = None
        for ts, data in self.snaps.get(league, ()):
            if ts > t:
                break
            best = data
        return best

    def latest(self, league: str) -> dict | None:
        q = self.snaps.get(league)
        return q[-1][1] if q else None


def _find(board: dict | None, gid: str) -> dict | None:
    return next((g for g in (board or {}).get("games") or [] if str(g.get("id")) == gid), None)


def status(g: dict) -> str:
    """"Q3 4:12", "Halftime", "Final", "7:30 PM"."""
    st = g.get("state")
    if st == "post":
        return g.get("detail") or "Final"
    if st == "pre":
        return g.get("detail") or "Soon"
    p, clock, detail = int(g.get("period") or 0), g.get("clock") or "", g.get("detail") or ""
    if detail and clock and clock not in detail:   # "Halftime", "End of 3rd"
        return detail
    per = f"Q{p}" if p <= 4 else "OT" if p == 5 else f"{p - 4}OT"
    return f"{per} {clock}".strip()


def _side(t: dict, score: bool) -> dict:
    return {"abbr": t.get("abbr") or "?", "name": t.get("name") or "", "color": t.get("color") or "#5c5c51",
            "score": t.get("score") if score else None, "poss": bool(t.get("poss")) and score}


def compact(league: str, g: dict, score: bool = True) -> dict:
    return {"league": league, "id": str(g.get("id")), "state": g.get("state") or "", "status": status(g),
            "close": bool(g.get("close")) and score, "away": _side(g["away"], score), "home": _side(g["home"], score)}


def tile_games(urls: list[str], live_league: str, live_ids: list, boards: dict[str, dict | None]) -> list[tuple[str, str] | None]:
    """Each tile's (league, ESPN id): the live plan's game, else the game whose team page is on the tile (live first)."""
    rank = {"in": 0, "pre": 1, "post": 2}
    out: list[tuple[str, str] | None] = []
    for k, u in enumerate(urls):
        gid = live_ids[k] if k < len(live_ids) else None
        if live_league and gid:
            out.append((live_league, str(gid)))
            continue
        hits = [(rank.get(g.get("state"), 3), lg, str(g.get("id"))) for lg, b in boards.items()
                for g in (b or {}).get("games") or [] if g.get("id") and u.rstrip("/") in stream_urls(lg, g)]
        out.append(min(hits)[1:] if hits else None)
    return out


def host(url: str) -> str:
    h = (urlsplit(url).hostname or "").removeprefix("www.")
    return h[:40]


def chat_lines(view: dict | None, now: float) -> list[dict]:
    if not view or not view.get("on"):
        return []
    return [{"id": m["id"], "name": m.get("name") or "", "guest": bool(m.get("guest")), "text": m.get("text") or "",
             "sys": m.get("kind") == "system", "fresh": now - float(m.get("ts") or 0) < CHAT_FRESH_S}
            for m in (view.get("messages") or [])[-CHAT_LINES:]]


def build_view(*, urls: list[str], sound: int, lbar: bool, view: str, title: str, live_league: str, live_ids: list,
               scores, tape: ScoreTape, delay=default_delay, chat: dict | None = None, now: float | None = None) -> dict:
    """GET /i/<inst>/bands. scores.get(league) → the cached parsed scoreboard (fetches when stale)."""
    now = time.time() if now is None else now
    leagues = list(LEAGUES)
    for lg in leagues:
        tape.see(lg, now, scores.get(lg))
    latest = {lg: tape.latest(lg) for lg in leagues}
    picks = tile_games(urls, live_league, live_ids, latest)
    tiles, on_tiles, seen_lg = [], set(), []
    for u, pick in zip(urls, picks):
        d, src = delay(u)
        t = {"url": u, "host": host(u), "delay": round(float(d), 1), "src": src, "game": None, "synced": False}
        if pick:
            lg, gid = pick
            on_tiles.add(pick)
            seen_lg.append(lg)
            then, last = _find(tape.at(lg, now - d), gid), _find(latest[lg], gid)
            if then is not None:
                t["game"], t["synced"] = compact(lg, then), True
            elif last is not None:   # nothing that old yet: the teams, no score (a game not started yet is no spoiler)
                pre = last.get("state") == "pre"
                t["game"] = compact(lg, last, score=pre)
                if not pre:
                    t["game"]["status"] = "syncing"
                t["synced"] = pre
        tiles.append(t)
    d0 = delay("")[0]   # no tile: the default
    ticker = []
    for lg in dict.fromkeys(seen_lg + ([live_league] if live_league else []) or leagues):
        board = tape.at(lg, now - d0)
        for g in (board or {}).get("games") or []:
            if (lg, str(g.get("id"))) not in on_tiles:
                ticker.append(compact(lg, g))
    order = {"in": 0, "post": 1, "pre": 2}
    ticker.sort(key=lambda g: (order.get(g["state"], 3), not g["close"]))
    main = max(set(seen_lg), key=seen_lg.count) if seen_lg else live_league
    return {"n": len(urls), "sound": sound, "lbar": lbar, "view": view, "title": title, "league": main or "",
            "tiles": tiles, "ticker": ticker[:TICKER_MAX], "delay": d0, "chat": chat_lines(chat, now)}
