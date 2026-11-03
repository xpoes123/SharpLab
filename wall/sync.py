"""Spoiler sync: how far each tile's video runs behind real life (D), so nothing on the wall — Kalshi prices, P&L, the
scoreboard, the ticker — is ever ahead of the picture.

* D per tile page (keyed by URL: a tile that moves keeps its delay, both walls share it), persisted in
  $WALL_STATE_DIR/sync.json:
    manual (−/+ 5 s, or "tap when the score changes")  >  auto (scorebug OCR)  >  default (45 s).
* Auto: every POLL_S, for each tile with a live game, the tile's frame (CDP Page.captureScreenshot at the video's own
  resolution — only the scorebug's area once it's found) → wall/scorebug.py reads the two scores. When they change to a
  score ESPN's play-by-play has, lag = when we saw it − that play's wall clock. D = median of the last 5, 0–120 s.
  Only while bets or a board are shown; at most one OCR per tile per POLL_S.
* Tap: "the score on screen just changed" at time T → the ESPN scoring play whose wall clock is closest to T − D (within
  2.5 min) → D = T − its wall clock, as a manual setting.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import statistics
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from wall import scorebug
from wall.live import LEAGUES, stream_urls

log = logging.getLogger("wall.sync")
DEFAULT_S = 45.0
MAX_S = 120.0
STEP_S = 5.0
KEEP = 5
POLL_S = 2.0
PBP_S = 15.0           # play-by-play refetch while a tile is tracked (and on a seen change)
SEARCH_S = 20.0        # look for a lost scorebug at most this often (a search is ~2 s of CPU)
LOST_AFTER = 15        # unreadable reads in a row (≈ 30 s: replays, ads) → search again
SUMMARY = {"nba": "basketball/nba", "nfl": "football/nfl"}


def clamp(d: float) -> float:
    return max(0.0, min(MAX_S, float(d)))


class SyncStore:
    def __init__(self, path: Path | str | None = None, default: float = DEFAULT_S) -> None:
        self.path = Path(path) if path else None
        self.show = True
        self.ticker = False   # a bets line in the L-bar's bottom ticker too (the sidebar's Bets section is always there)
        self.default = default
        self.manual: dict[str, float] = {}
        self.auto: dict[str, list[float]] = {}
        self.rev = 0
        self.lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            d = json.loads(self.path.read_text())
            self.show = bool(d.get("show", True))
            self.ticker = bool(d.get("ticker", False))
            self.default = clamp(d.get("default", self.default))
            self.manual = {str(k): clamp(v) for k, v in (d.get("manual") or {}).items()}
            self.auto = {str(k): [clamp(x) for x in v][-KEEP:] for k, v in (d.get("auto") or {}).items() if isinstance(v, list)}
        except (OSError, ValueError, TypeError, AttributeError) as e:
            log.warning("sync settings unreadable, starting fresh: %s", e)

    def _save(self) -> None:
        self.rev += 1
        if not self.path:
            return
        tmp = self.path.with_suffix(".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps({"show": self.show, "ticker": self.ticker, "default": self.default, "manual": self.manual, "auto": self.auto}))
            os.replace(tmp, self.path)
        except OSError as e:
            log.warning("sync settings not saved: %s", e)

    def delay(self, url: str) -> tuple[float, str]:
        with self.lock:
            if url in self.manual:
                return self.manual[url], "manual"
            if self.auto.get(url):
                return round(statistics.median(self.auto[url]), 1), "auto"
            return self.default, "default"

    def set_show(self, show: bool) -> None:
        with self.lock:
            self.show = bool(show)
            self._save()

    def set_ticker(self, on: bool) -> None:
        with self.lock:
            self.ticker = bool(on)
            self._save()

    def set_default(self, d: float) -> None:
        with self.lock:
            self.default = clamp(d)
            self._save()

    def nudge(self, url: str, step: float) -> float:
        d = clamp(self.delay(url)[0] + step)
        with self.lock:
            self.manual[url] = d
            self._save()
        return d

    def set_manual(self, url: str, d: float) -> float:
        with self.lock:
            self.manual[url] = clamp(d)
            self._save()
            return self.manual[url]

    def use_auto(self, url: str) -> None:
        with self.lock:
            self.manual.pop(url, None)
            self._save()

    def add_sample(self, url: str, lag: float) -> bool:
        """An OCR'd score change: lag in seconds. Outside 0–120 s = a misread or a wrong play: dropped."""
        if not 0 <= lag <= MAX_S:
            return False
        with self.lock:
            self.auto[url] = (self.auto.get(url, []) + [round(lag, 1)])[-KEEP:]
            self._save()
        return True

    def tiles(self, urls: list[str]) -> list[dict]:
        return [{"url": u, "delay": d, "src": src, "samples": len(self.auto.get(u, []))} for u in urls for d, src in [self.delay(u)]]


# ---- ESPN play-by-play: when each score happened -------------------------------------------------------------------------
def _ts(iso: str) -> float | None:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def score_plays(summary: dict) -> list[tuple[float, int, int]]:
    """Every (wall clock, away, home) a play left the score at, in time order, first time each score appears (NBA:
    summary.plays; NFL: drives.*.plays — any object with wallclock + awayScore + homeScore)."""
    found: list[tuple[float, int, int]] = []

    def walk(x) -> None:
        if isinstance(x, dict):
            if "wallclock" in x and "awayScore" in x and "homeScore" in x:
                t = _ts(x.get("wallclock") or "")
                try:
                    a, h = int(x["awayScore"]), int(x["homeScore"])
                except (TypeError, ValueError):
                    a = h = None
                if t is not None and a is not None:
                    found.append((t, a, h))
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(summary)
    out, seen = [], set()
    for t, a, h in sorted(found):
        if (a, h) not in seen:
            seen.add((a, h))
            out.append((t, a, h))
    return out


def when(plays: list[tuple[float, int, int]], pair: tuple[int, int]) -> float | None:
    return next((t for t, a, h in plays if (a, h) == pair), None)


def recent_pairs(plays: list[tuple[float, int, int]], now: float, span: float = 600) -> list[tuple[int, int]]:
    """Scores the screen may show now (newest first): the last 10 min of plays, at least the last 8."""
    tail = [p for p in plays if p[0] >= now - span] or []
    tail = plays[-8:] if len(tail) < 8 else tail
    return [(a, h) for _, a, h in reversed(tail)]


def tap_delay(plays: list[tuple[float, int, int]], at: float, current: float) -> float | None:
    """The viewer saw a score change at `at`: the scoring play it most likely was (wall clock within 2.5 min before,
    closest to the current delay's guess) → the new delay."""
    cand = [t for t, _, _ in plays if 2 <= at - t <= 150]
    if not cand:
        return None
    t = min(cand, key=lambda w: abs((at - w) - current))
    return clamp(at - t)


# ---- one tile's tracker ------------------------------------------------------------------------------------------------
@dataclass
class Tracker:
    bug: scorebug.Bug | None = None
    last: tuple[int, int] | None = None
    pending: tuple[tuple[int, int], float] | None = None
    misses: int = 0
    search_at: float = 0.0
    searches: int = 0

    def step(self, frame, now: float, plays: list[tuple[float, int, int]], grab_bug=None, ocr=scorebug.tesseract_tsv) -> float | None:
        """One OCR round. frame() → the whole frame (only called to search); grab_bug(bug) → just the bug's area. A lag
        (s) when a score change was confirmed (seen twice running, timed at its first sighting), else None."""
        if not plays:
            return None
        history = {(a, h): i for i, (_, a, h) in enumerate(plays)}
        if self.bug is None:
            if now < self.search_at:
                return None
            self.search_at = now + SEARCH_S
            self.searches += 1
            img = frame()
            width = scorebug.TARGET_W * (1 if self.searches % 2 else 3) // (1 if self.searches % 2 else 2)
            self.bug = scorebug.find(img, recent_pairs(plays, now), ocr, width=width) if img is not None else None
            if self.bug is None:
                return None
            r = scorebug.read(img, self.bug, ocr=ocr)
            self.last, self.pending, self.misses = (r if r in history else None), None, 0
            return None
        if grab_bug:
            img, part = grab_bug(self.bug), self.bug.box
        else:
            img, part = frame(), (0.0, 0.0, 1.0, 1.0)
        r = scorebug.read(img, self.bug, ocr=ocr, part=part) if img is not None else None
        if r not in history:
            self.misses += 1
            if self.misses >= LOST_AFTER:
                self.bug, self.last, self.pending = None, None, None
            return None
        self.misses = 0
        if self.last is None:
            self.last, self.pending = r, None
            return None
        if r == self.last or history[r] < history[self.last]:   # unchanged, or an older score (a replay's graphic)
            self.pending = None
            return None
        if self.pending and self.pending[0] == r:
            seen_at = self.pending[1]
            self.last, self.pending = r, None
            t = when(plays, r)
            return None if t is None else seen_at - t
        self.pending = (r, now)
        return None


# ---- the runner: tiles → frames → lags ----------------------------------------------------------------------------------
def tile_games(inst, scores) -> list[dict | None]:
    """Each tile's ESPN game {league, id, away, home, start, state, label} or None: the live plan's game, else the game
    whose team page is on the tile."""
    with inst.lock:
        urls = list(inst.urls)
        lv = inst.live
        ids = list(lv.games) if lv else []
    out: list[dict | None] = []
    boards = {lg: ((scores.peek(lg) if hasattr(scores, "peek") else scores.get(lg)) or {}).get("games") or [] for lg in LEAGUES}
    for k, u in enumerate(urls):
        g, league = None, ""
        if lv and k < len(ids) and ids[k]:
            league, g = lv.league, next((x for x in boards.get(lv.league, []) if x.get("id") == ids[k]), None) or lv.game(ids[k])
        if g is None:
            for lg, games in boards.items():
                g = next((x for x in games if x.get("state") in ("in", "pre") and u in stream_urls(lg, x)), None)
                if g:
                    league = lg
                    break
        if not g:
            out.append(None)
            continue
        out.append({"league": league, "id": str(g.get("id")), "away": g["away"]["abbr"], "home": g["home"]["abbr"],
                    "start": g.get("start") or "", "state": g.get("state") or "",
                    "label": f"{g['away']['abbr']} @ {g['home']['abbr']}"})
    return out


def grab(port: int, part: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0), width: int = 1920):
    """A tile's frame (or the part of it, fractions) at ~`width` px across the whole frame: CDP clip scale renders the
    video at its own resolution instead of the tile's on-screen size. Read-only; JPEG (cheaper than PNG)."""
    from wall.engine import cdp_page
    with cdp_page(port) as call:
        vp = call("Page.getLayoutMetrics").get("cssVisualViewport") or {}
        w, h = float(vp.get("clientWidth") or 0), float(vp.get("clientHeight") or 0)
        if w <= 0 or h <= 0:
            return None
        scale = min(3.0, width / w)
        clip = {"x": part[0] * w, "y": part[1] * h, "width": (part[2] - part[0]) * w, "height": (part[3] - part[1]) * h, "scale": scale}
        data = call("Page.captureScreenshot", {"format": "jpeg", "quality": 92, "clip": clip}).get("data")
    return scorebug.load(base64.b64decode(data)) if data else None


class Runner:
    """The calibration loop (a daemon thread): every POLL_S, the tiles of the walls that run, while bets or a board show."""

    def __init__(self, store: SyncStore, instances: dict, scores, fetch_json=None, grabber=grab, clock=time.time) -> None:
        from wall.live import get_json
        self.store, self.instances, self.scores = store, instances, scores
        self.fetch = fetch_json or get_json
        self.grab, self.clock = grabber, clock
        self.trackers: dict[str, Tracker] = {}
        self.pbp: dict[str, tuple[float, list]] = {}
        self.status: dict[str, str] = {}       # url → what the auto sync is doing ("scorebug found", "no scorebug", …)
        self.enabled = scorebug.available()

    def plays(self, game: dict, fresh: bool = False) -> list[tuple[float, int, int]]:
        key = f"{game['league']}:{game['id']}"
        at, plays = self.pbp.get(key, (0.0, []))
        if self.clock() - at < (3 if fresh else PBP_S):
            return plays
        try:
            d = self.fetch(f"https://site.api.espn.com/apis/site/v2/sports/{SUMMARY[game['league']]}/summary?event={game['id']}")
            plays = score_plays(d)
        except Exception as e:  # noqa: BLE001 — keep the last ones
            log.debug("pbp %s: %s", key, e)
        self.pbp[key] = (self.clock(), plays)
        return plays

    def active(self, inst) -> bool:
        return bool(inst.urls) and (self.store.show or bool(inst.visible) or inst.view in ("lbar", "full"))

    def round(self) -> None:
        if not self.enabled:
            return
        done: set[str] = set()
        for inst in self.instances.values():
            if not self.active(inst) or (inst.headless and inst.screen):   # a screen mirror has no tiles of its own
                continue
            games = tile_games(inst, self.scores)
            tiles = list(inst.driver.tiles)
            for k, g in enumerate(games):
                url = inst.urls[k] if k < len(inst.urls) else ""
                if not g or g["state"] != "in" or url in done or k >= len(tiles):
                    continue
                done.add(url)
                port = inst.session.cdp_base + tiles[k][1]
                tr = self.trackers.setdefault(url, Tracker())
                plays = self.plays(g, fresh=tr.pending is not None)
                try:
                    lag = tr.step(lambda: self.grab(port), self.clock(), plays, grab_bug=lambda b: self.grab(port, b.box))
                except Exception as e:  # noqa: BLE001 — a tile loading, an ad; next round
                    log.debug("sync %s: %s", url, e)
                    continue
                self.status[url] = "reading the scorebug" if tr.bug else "looking for the scorebug"
                if lag is not None:
                    ok = self.store.add_sample(url, lag)
                    log.info("sync %s: score change seen %.1f s after ESPN's play%s", url, lag, "" if ok else " (ignored)")
        for u in [u for u in self.trackers if u not in done]:
            self.trackers.pop(u, None)

    def game_of(self, url: str) -> dict | None:
        for inst in self.instances.values():
            for u, g in zip(list(inst.urls), tile_games(inst, self.scores)):
                if u == url and g:
                    return g
        return None

    def tap(self, url: str, at: float) -> float | None:
        """"The score on screen just changed" (at = when, box clock) → that tile's manual delay. None = no scoring play
        of its game fits."""
        g = self.game_of(url)
        if not g:
            return None
        d = tap_delay(self.plays(g, fresh=True), at, self.store.delay(url)[0])
        if d is not None:
            self.store.set_manual(url, d)
        return d

    def run_forever(self) -> None:
        while True:
            t0 = time.monotonic()
            try:
                self.round()
            except Exception as e:  # noqa: BLE001
                log.warning("sync round: %s", e)
            time.sleep(max(0.2, POLL_S - (time.monotonic() - t0)))

    def start(self) -> None:
        if self.enabled:
            threading.Thread(target=self.run_forever, name="wall-sync", daemon=True).start()
        else:
            log.warning("tesseract isn't installed: no automatic bets/board delay (manual + default only)")
