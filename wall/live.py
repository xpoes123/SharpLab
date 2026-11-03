"""Live games for the wall: ESPN's public scoreboard (NFL/NBA) → which games deserve a tile, each on the home team's
topstreamer.site page. Moved from the TV OS (src/tv/livewall.py + boards.parse_scoreboard).

Order = live games, close-and-late first ("close": 4th quarter, one score), then by kickoff; finals never; pregame only
when nothing is live (soonest first). Every REFRESH_S a running live wall re-plans: a tile whose game went final (or a
pregame filler while a live game sits unshown) takes the next best game not on the wall, relaunching only that tile; a
pinned tile (or one set by hand) never moves.

topstreamer: /<league>/<slug> is a team's page and carries its next game, titled "NFL: Ravens at Bills". Usually both
teams' pages carry it, but not always, so the home page is the default and the plan (off the event loop) checks the
title and falls back to the away team's.
"""
from __future__ import annotations

import re
import json
import logging
import threading
import time
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

STREAM = "https://topstreamer.site"
TEAMS = {   # topstreamer slugs (checked against the site 2026-11: all live pages)
    "nfl": "49ers bears bengals bills broncos browns buccaneers cardinals chargers chiefs colts commanders cowboys dolphins eagles falcons "
           "giants jaguars jets lions packers panthers patriots raiders rams ravens saints seahawks steelers texans titans vikings".split(),
    "nba": "76ers bucks bulls cavaliers celtics clippers grizzlies hawks heat hornets jazz kings knicks lakers magic mavericks nets nuggets "
           "pacers pelicans pistons raptors rockets spurs suns thunder timberwolves trailblazers warriors wizards".split(),
}
LEAGUES = {"nfl": "NFL", "nba": "NBA"}
REFRESH_S = 120
UA = {"User-Agent": "Mozilla/5.0 (homelab tv)"}


def team_slug(league: str, team: dict) -> str:
    """A scoreboard team (boards._team: "name" = ESPN's short name, "Trail Blazers") → its topstreamer slug, or ""."""
    s = re.sub(r"[^a-z0-9]", "", (team.get("name") or "").lower())
    return s if s in TEAMS.get(league, ()) else ""


def rank(scores: dict | None) -> list[dict]:
    """The games worth a tile, best first: the LIVE ones only (close + late first, then kickoff) — 3 live games = 3 tiles.
    Only when nothing is live do upcoming games stand in (by kickoff). No finals."""
    games = (scores or {}).get("games") or []
    live = sorted((g for g in games if g.get("state") == "in"), key=lambda g: (not g.get("close"), g.get("start") or "", g.get("left", 1)))
    soon = sorted((g for g in games if g.get("state") == "pre"), key=lambda g: g.get("start") or "")
    return [g for g in (live or soon) if g.get("id")]


def assign(current: list[str | None], pinned: set[int], ranked: list[dict], n: int) -> list[str | None]:
    """Tile → game id. A tile keeps its game while it's still worth a tile (a pregame filler only while no live game is
    left unshown); a final / vanished one takes the best game not on the wall; pinned tiles (and None = set by hand) stay.
    Tiles are added up to n while there are games."""
    by_id = {g["id"]: g for g in ranked}
    out = list(current)
    spare = [g for g in ranked if g["id"] not in set(out)]
    for i, gid in enumerate(out):
        if i in pinned or gid is None:
            continue
        g = by_id.get(gid)
        if g is None or g["state"] == "pre" and spare and spare[0]["state"] == "in":
            if spare:
                out[i] = spare.pop(0)["id"]
            elif g is None:
                out[i] = ""   # its game ended and nothing live can take the slot: "" = remove this tile (indexes stay put)
    while len(out) < n and spare:
        out.append(spare.pop(0)["id"])
    return out


def page_title(url: str, timeout: float = 4) -> str:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        m = re.search(rb"<title>([^<]*)", r.read(300_000))
    return m.group(1).decode("utf-8", "replace").strip() if m else ""


def carries(league: str, url: str, title=page_title) -> bool | None:
    """Does this team page have a game on it ("NFL: Ravens at Bills"; a team with none is titled with its slug)? None = couldn't tell."""
    try:
        return title(url).upper().startswith(f"{league.upper()}:")
    except Exception:  # noqa: BLE001 — the site down / slow: the caller keeps the home page
        return None


def stream_urls(league: str, g: dict) -> list[str]:
    """The game's team pages, home first."""
    return [f"{STREAM}/{league}/{s}" for s in (team_slug(league, g["home"]), team_slug(league, g["away"])) if s]


def stream_url(league: str, g: dict, check=None) -> str:
    """The page to put on a tile: the first team page check(url) confirms carries a game, else the home team's ("" =
    neither team has a topstreamer page)."""
    urls = stream_urls(league, g)
    if check:
        for u in urls:
            if check(u):
                return u
    return urls[0] if urls else ""


def status(g: dict) -> str:
    if g["state"] == "post":
        return "Final"
    if g["state"] == "pre":
        return g.get("detail") or "upcoming"
    p = g.get("period") or 0
    return f"Q{p}" if p <= 4 else "OT" if p == 5 else f"{p - 4}OT"


def matchup(g: dict) -> str:
    """"BAL 7–10 BUF · Q2" (away first); pregame "LAC @ LAR · 4:05 PM"."""
    a, h = g["away"], g["home"]
    if g["state"] == "pre":
        return f"{a['abbr']} @ {h['abbr']} · {status(g)}"
    return f"{a['abbr']} {a['score'] or 0}–{h['score'] or 0} {h['abbr']} · {status(g)}"


class LiveRun:
    """One running live preset: which game each tile shows, which tiles are pinned, the page each game resolved to, and
    the plan the background check hands back to the event loop (WallApp.tick applies it)."""

    def __init__(self, league: str, n: int) -> None:
        self.league, self.n = league, max(1, min(4, n))
        self.games: list[str | None] = []   # tile → ESPN game id (None = the phone set this tile by hand)
        self.pinned: set[int] = set()
        self.urls: dict[str, str] = {}   # game id → its confirmed page
        self.data: dict | None = None   # the scoreboard the phone's labels read
        self.due = 0.0
        self.busy = False
        self.plan: list[tuple[str | None, str]] | None = None

    def game(self, gid: str | None) -> dict | None:
        return next((g for g in (self.data or {}).get("games") or [] if g.get("id") == gid), None) if gid else None

    def url(self, g: dict) -> str:
        return self.urls.get(g["id"]) or stream_url(self.league, g)

    def make_plan(self, data: dict | None, check=None) -> list[tuple[str | None, str]] | None:
        """(game id, url) per tile, or None when there's no scoreboard (keep the wall as it is). check(url) → whether a
        team page carries a game; confirmed pages are remembered for the run."""
        if not data:
            return None
        ranked = rank(data)
        by_id = {g["id"]: g for g in ranked}
        out = []
        for gid in assign(self.games, self.pinned, ranked, self.n):
            g = by_id.get(gid)
            if g is not None and gid not in self.urls and check:
                self.urls[gid] = next((u for u in stream_urls(self.league, g) if check(u) is True), "")
                if not self.urls[gid]:
                    del self.urls[gid]   # neither page confirmed (site down?): home page for now, ask again next round
            out.append((gid, self.url(g) if g is not None and gid else ""))
        return out

    # ---- the phone's live edits keep the tile → game list in step ----
    def set_by_hand(self, k: int) -> None:
        if k < len(self.games):
            self.games[k] = None
        else:
            self.games.append(None)
        self.pinned.discard(k)

    def move(self, k: int, j: int) -> None:
        if 0 <= k < len(self.games) and 0 <= j < len(self.games):
            self.games.insert(j, self.games.pop(k))
            order = list(range(len(self.games)))
            order.insert(j, order.pop(k))
            self.pinned = {order.index(i) for i in self.pinned}

    def remove(self, k: int) -> None:
        if 0 <= k < len(self.games):
            del self.games[k]
            self.pinned = {i - (i > k) for i in self.pinned if i != k}


# ---- ESPN scoreboards (from the TV's boards.py) --------------------------------------------------------------------------
ET = ZoneInfo("America/New_York")
ESPN = "https://site.api.espn.com/apis/site/v2/sports"
PATHS = {"nfl": "football/nfl", "nba": "basketball/nba"}
log = logging.getLogger("wall.live")


def get_json(url: str, timeout: float = 10):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _team(c: dict, poss: str | None) -> dict:
    t = c.get("team") or {}
    score = c.get("score")
    rec = next((r.get("summary") for r in c.get("records") or [] if r.get("type") == "total"), "")
    return {"abbr": t.get("abbreviation") or "?", "name": t.get("shortDisplayName") or t.get("name") or "", "color": "#" + (t.get("color") or "5c5c51"),
            "logo": t.get("logo") or "", "score": int(score) if str(score or "").isdigit() else None, "record": rec,
            "poss": bool(poss) and str(t.get("id")) == str(poss), "winner": bool(c.get("winner"))}


def parse_scoreboard(d: dict, league: str, now: datetime | None = None) -> dict:
    """ESPN scoreboard JSON → {"kind":"scores", "games":[…]}: live first (close late games on top), then upcoming, then final."""
    now = (now or datetime.now(ET)).astimezone(ET)
    games = []
    for e in d.get("events") or []:
        comp = (e.get("competitions") or [{}])[0]
        st = e.get("status") or comp.get("status") or {}
        state = (st.get("type") or {}).get("state") or "pre"
        sit = comp.get("situation") or {}
        cs = comp.get("competitors") or []
        away = next((c for c in cs if c.get("homeAway") == "away"), cs[0] if cs else {})
        home = next((c for c in cs if c.get("homeAway") == "home"), cs[-1] if cs else {})
        g = {"id": e.get("id"), "state": state, "away": _team(away, sit.get("possession")), "home": _team(home, sit.get("possession")),
             "detail": (st.get("type") or {}).get("shortDetail") or "", "period": int(st.get("period") or 0), "clock": st.get("displayClock") or "",
             "down": "", "spot": "", "redzone": False, "close": False, "start": e.get("date") or ""}
        if state == "pre" and g["start"]:
            try:
                t = datetime.fromisoformat(g["start"].replace("Z", "+00:00")).astimezone(ET)
                g["detail"] = t.strftime("%-I:%M %p") if t.date() == now.date() else t.strftime("%a %-I:%M %p")
            except ValueError:
                pass
        g["left"] = 1.0 if state == "pre" else 0.0 if state != "in" else game_left(league, g["period"], float(st.get("clock") or 0))
        g["last"] = ((sit.get("lastPlay") or {}).get("text") or "").strip() if state == "in" else ""   # the band's ticker
        g["headline"] = next((h.get("shortLinkText") or "" for h in comp.get("headlines") or []), "") if state == "post" else ""
        if state == "in":
            g["down"], g["spot"], g["redzone"] = sit.get("shortDownDistanceText") or "", sit.get("possessionText") or "", bool(sit.get("isRedZone"))
            a, h = g["away"]["score"] or 0, g["home"]["score"] or 0
            secs = float(st.get("clock") or 0)
            if league == "nfl":
                g["close"] = g["period"] >= 4 and abs(a - h) <= 8
            else:
                g["close"] = g["period"] >= 4 and secs <= 300 and abs(a - h) <= 6
        games.append(g)
    order = {"in": 0, "pre": 1, "post": 2}
    games.sort(key=lambda g: (order.get(g["state"], 3), not g["close"], g["start"]))
    week = (d.get("week") or {}).get("number")
    title = f"{league.upper()} · Week {week}" if league == "nfl" and week else f"{league.upper()} · {now.strftime('%a %b %-d')}"
    return {"kind": "scores", "league": league, "title": title, "games": games}


def game_left(league: str, period: int, clock_s: float) -> float:
    """Fraction of regulation still to play (an NFL game in overtime = 0: next to nothing left to project)."""
    if league == "nfl":
        return 0.0 if period > 4 else max(0.0, min(1.0, ((4 - max(period, 1)) * 900 + clock_s) / 3600))
    return 0.0 if period > 4 else max(0.0, min(1.0, ((4 - max(period, 1)) * 720 + clock_s) / 2880))



def _ts(iso: str) -> float:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return float("inf")


class Scores:
    """The latest parsed scoreboard per league, fetched on demand: get(league) returns the cached one while it's fresh
    (20 s with a game on or about to start, else 5 min); fresh=True fetches when it's over a minute old."""

    def __init__(self, fetch=get_json, base: str = ESPN, clock=time.time) -> None:
        self.fetch, self.base, self.clock = fetch, base, clock
        self.snaps: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def ttl(self, data: dict) -> float:
        soon = self.clock() + 900
        live = any(g["state"] == "in" or (g["state"] == "pre" and _ts(g["start"]) < soon) for g in data.get("games") or [])
        return 20 if live else 300

    def get(self, league: str, fresh: bool = False) -> dict | None:
        if league not in PATHS:
            return None
        with self._lock:
            at, data = self.snaps.get(league, (0.0, None))
        age = self.clock() - at
        if data is not None and age < (min(60, self.ttl(data)) if fresh else self.ttl(data)):
            return data
        try:
            data = parse_scoreboard(self.fetch(f"{self.base}/{PATHS[league]}/scoreboard"), league)
        except Exception as e:  # noqa: BLE001 — keep the last good one
            log.warning("scoreboard %s: %s", league, e)
            return data
        with self._lock:
            self.snaps[league] = (self.clock(), data)
        return data


def live_games(scores: dict | None, league: str, n: int = 4) -> list[dict]:
    """The best n games on now (or soon) with their pages ("Fill with live games")."""
    out = []
    for g in rank(scores):
        u = stream_url(league, g)
        if u:
            out.append({"id": g["id"], "label": matchup(g), "state": g["state"], "url": u})
        if len(out) >= n:
            break
    return out
