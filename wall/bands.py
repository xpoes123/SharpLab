"""What the overlay draws in the bands the tiles leave (qml/Bands.qml): a header over each tile (its game, delayed),
a calm backdrop tinted by the teams on screen, and, without the L-bar, a slim strip of the other games + the watch
party's chat. The overlay polls GET /i/<inst>/bands (loopback) every 2 s.

Spoilers: a tile's video runs D seconds behind real life, so its header shows the scoreboard as it was D seconds ago
(ScoreTape keeps every scoreboard with the time we first saw it; nothing seen after now − D can reach the header). D comes
from `delay(url) -> (seconds, src)`: the same per-tile setting the bets and the L-bar's boards use (wall/sync.py
SyncStore.delay; default_delay = 45 s everywhere is only the fallback for tests / previews). The strip uses the default.
The overlay prefers its own delayed board (Board.qml, same delays) for a game it holds, so a header and the sidebar
never disagree (qml/bands.js headerGame).
Right after a wall starts there is no snapshot that old yet: the header shows the teams and "syncing", never a score.
"""
from __future__ import annotations

import json
import re
import time
from collections import deque
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from wall import spectrum
from wall.engine import own_page, tile_map
from wall.live import LEAGUES, TEAMS, stream_urls, team_slug

DEFAULT_DELAY_S = 45.0
KEEP_S = 600.0          # the tape's memory (delays are clamped to 120 s)
TICKER_MAX = 12
CHAT_LINES = 4
CHAT_FRESH_S = 20.0
TVCHAT_LINES = 6   # the chat floating over the tiles (qml/ChatFloat.qml)


LOCAL_NEWS = Path(__file__).resolve().parent.parent / "web" / "static" / "local-news.json"
# the ad-skip pool's networks (wall/adwatch.py POOL), by YouTube handle (lower case)
POOL_NAMES = {"@nbcnews": "NBC News", "@pbsnewshour": "PBS NewsHour", "@abcnews": "ABC News", "@cbsnews": "CBS News",
              "@livenowfox": "LiveNOW from FOX"}
YT_HANDLE = re.compile(r"youtube\.com/(@[^/?#]+)", re.I)


@lru_cache(maxsize=2)
def _local_news(path: Path = LOCAL_NEWS) -> dict[str, str]:
    """{handle (lower case): station name} from the watch page's per-state local news list. Missing / broken = none."""
    try:
        states = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    out = {}
    for lst in states.values() if isinstance(states, dict) else []:
        for s in lst if isinstance(lst, list) else []:
            m = YT_HANDLE.search(str(s.get("url") or "")) if isinstance(s, dict) else None
            if m and s.get("name"):
                out.setdefault(m.group(1).lower(), str(s["name"]))
    return out


def stream_name(url: str) -> str | None:
    """The outlet's name for a stream tile's chip (qml/SpeakerBadge.qml): the Spectrum catalog, else the local news
    list, else the ad-skip pool's networks, else the YouTube @handle. None = no name chip (our /p/ pages, or a page that
    isn't a YouTube stream — the sports tiles have their game header in the bands)."""
    if not url or own_page(url):
        return None
    s = spectrum.find(url)
    if s:
        return s["name"]
    m = YT_HANDLE.search(url)
    if not m:
        return None
    h = m.group(1)
    return _local_news().get(h.lower()) or POOL_NAMES.get(h.lower()) or h


def default_delay(url: str) -> tuple[float, str]:
    """(seconds, src) without a SyncStore (tests, previews); the service passes SyncStore.delay."""
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


def host(url: str) -> str:
    h = (urlsplit(url).hostname or "").removeprefix("www.")
    return h[:40]


def page_team(url: str) -> tuple[str, str] | None:
    """A topstreamer team page → (league, slug): https://topstreamer.site/nba/clippers → ("nba", "clippers")."""
    parts = [p for p in urlsplit(url).path.split("/") if p]
    if len(parts) == 2 and parts[0] in TEAMS and parts[1] in TEAMS[parts[0]]:
        return parts[0], parts[1]
    return None


def ended_game(url: str, boards: dict[str, dict | None]) -> tuple[str, str] | None:
    """The tile's game when sync.tile_games has none because it's over (it only maps live / upcoming games)."""
    for lg, b in boards.items():
        for g in (b or {}).get("games") or []:
            if g.get("id") and g.get("state") == "post" and url.rstrip("/") in stream_urls(lg, g):
                return lg, str(g["id"])
    return None


def team_label(url: str, boards: dict[str, dict | None]) -> str:
    """No game at all: the team the page is for, "CLIPPERS · LAC" (the abbreviation when a scoreboard knows it)."""
    pt = page_team(url)
    if not pt:
        return ""
    lg, slug = pt
    for g in (boards.get(lg) or {}).get("games") or []:
        for side in ("away", "home"):
            if team_slug(lg, g[side]) == slug:
                return f"{g[side].get('name') or slug} · {g[side]['abbr']}"
    return slug


def chat_lines(view: dict | None, now: float, n: int = CHAT_LINES) -> list[dict]:
    if not view or not view.get("on"):
        return []
    return [{"id": m["id"], "name": m.get("name") or "", "guest": bool(m.get("guest")), "text": m.get("text") or "",
             "sys": m.get("kind") == "system", "fresh": now - float(m.get("ts") or 0) < CHAT_FRESH_S}
            for m in (view.get("messages") or [])[-n:]]


def speaker(urls: list[str], sound: int) -> dict | None:
    """The overlay's speaker badge (qml/Speaker.qml): which tile has the sound + its rect as fractions of the tiles' block
    (engine.tile_map; the overlay places it with lbar.js wallTiles, the same arrangement). None = no badge: fewer than two
    tiles with video (nothing to tell apart), or the sound index is on one of our /p/ data pages (they have no sound)."""
    video = [k for k, u in enumerate(urls) if not own_page(u)]
    if len(video) < 2 or sound not in video:
        return None
    return {"tile": sound, "rect": [round(v, 4) for v in tile_map(len(urls))[2][sound]]}


def build_view(*, urls: list[str], games: list[dict | None], sound: int, lbar: bool, view: str, title: str, live_league: str,
               scores, tape: ScoreTape, delay=default_delay, chat: dict | None = None, now: float | None = None) -> dict:
    """GET /i/<inst>/bands. games = wall/sync.py tile_games (each tile's {league, id, …} or None, the same mapping the
    bets and the boards' delays use); scores.get(league) → the cached parsed scoreboard (fetches when stale)."""
    now = time.time() if now is None else now
    leagues = list(LEAGUES)
    for lg in leagues:
        tape.see(lg, now, scores.get(lg))
    latest = {lg: tape.latest(lg) for lg in leagues}
    picks = [(g["league"], str(g["id"])) if g else None for g in games] + [None] * (len(urls) - len(games))
    tiles, on_tiles, seen_lg = [], set(), []
    for u, pick in zip(urls, picks):
        d, src = delay(u)
        pick = pick or ended_game(u, latest)
        t = {"url": u, "host": host(u), "label": "" if pick else team_label(u, latest), "delay": round(float(d), 1),
             "src": src, "game": None, "synced": False, "name": stream_name(u)}
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
            "tiles": tiles, "speaker": speaker(urls, sound), "ticker": ticker[:TICKER_MAX], "delay": d0, "chat": chat_lines(chat, now)}
