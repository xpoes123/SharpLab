"""Election night: which news stream is on air and which is at a commercial (wall/adwatch.py listens and posts here), and
what the wall does about it (docs/wall-service.md "Ad-skip").

* Board: the latest label per stream URL ({label: coverage|ad|break|offtopic|unknown, confidence, since, tail}), as posted
  to POST /adwatch. An entry not refreshed in STALE_S counts as unknown.
* Favorites: up to two starred streams ("home" streams for tiles 3–4), kept in $WALL_STATE_DIR/favorites.json.
* Policy (per wall, only while on — WALL_ADWATCH=1 or the owner's toggle):
  - sound: the sound tile's stream at ad/break SOUND_AD_S → the sound goes to an on-air tile (a favorite first, then the
    last manual pick, then the surest); after an automatic move it returns to a favorite on air / the manual pick once
    that has been coverage SOUND_BACK_S;
  - tiles: a stream tile (never one of our /p/ pages, never tiles 1–2 in election mode) at ad/break/offtopic SWAP_AFTER_S
    swaps in place to the best on-air pool stream not already up; it goes home — its favorite (coverage FAV_BACK_S) or
    else the original (coverage BACK_S) — later. At most one swap per tile per SWAP_GAP_S; a change by people (votes, the
    owner, the TV's phones) pauses the auto-swaps on that tile for HAND_PAUSE_S and drops what it remembered.
  Changes by people are noticed by comparing the tiles with what the policy last saw, so every path (votes, reset, the
  TV router, rotation) counts without hooks.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from wall.engine import own_page

log = logging.getLogger("wall.onair")
LABELS = ("coverage", "ad", "break", "offtopic", "unknown")
AWAY = {"ad", "break"}            # the sound leaves these
SWAP_AWAY = AWAY | {"offtopic"}   # a tile leaves these
STALE_S = 90.0
SOUND_AD_S, SOUND_BACK_S = 10.0, 10.0
SWAP_AFTER_S, BACK_S, FAV_BACK_S, FRESH_S = 60.0, 30.0, 20.0, 10.0
SWAP_SOUND_S = 15.0   # the tile with the sound on a break and no other tile on air to take the sound: swap it this soon
SWAP_GAP_S, HAND_PAUSE_S = 120.0, 600.0
MAX_FAVS = 2


class Board:
    def __init__(self, clock=time.time) -> None:
        self.clock = clock
        self.streams: dict[str, dict] = {}

    def put(self, streams: object) -> int:
        if not isinstance(streams, dict):
            raise ValueError("streams is {url: {label, confidence, since}}")
        now = self.clock()
        n = 0
        for url, s in list(streams.items())[:40]:
            if not (isinstance(url, str) and url.startswith("https://") and isinstance(s, dict) and s.get("label") in LABELS):
                continue
            conf, since = s.get("confidence", 0), s.get("since", now)
            conf = float(conf) if isinstance(conf, (int, float)) and not isinstance(conf, bool) else 0.0
            since = float(since) if isinstance(since, (int, float)) and not isinstance(since, bool) else now
            tail = s.get("text_tail") if isinstance(s.get("text_tail"), str) else ""
            self.streams[url[:500]] = {"label": s["label"], "confidence": round(max(0.0, min(1.0, conf)), 2),
                                       "since": min(since, now), "tail": tail[-160:], "at": now}
            n += 1
        return n

    def get(self, url: str) -> dict | None:
        s = self.streams.get(url)
        if not s or self.clock() - s["at"] > STALE_S or s["label"] == "unknown":
            return None
        return s

    def view(self, url: str) -> dict | None:
        s = self.get(url)
        return {k: s[k] for k in ("label", "confidence", "since", "tail")} if s else None

    def is_(self, url: str, labels: set[str] | str, for_s: float, now: float) -> bool:
        s = self.get(url)
        labels = {labels} if isinstance(labels, str) else labels
        return bool(s) and s["label"] in labels and now - s["since"] >= for_s

    def fresh(self) -> list[str]:
        return [u for u in self.streams if self.get(u)]


class Favorites:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.urls: list[str] = []
        try:
            data = json.loads(path.read_text()) if path and path.exists() else []
            self.urls = [u for u in data if isinstance(u, str)][:MAX_FAVS]
        except (OSError, ValueError):
            log.warning("favorites: unreadable %s", path)

    def set(self, url: str, on: bool) -> list[str]:
        if on and url not in self.urls:
            self.urls = (self.urls + [url])[-MAX_FAVS:]   # a third star replaces the oldest
        elif not on and url in self.urls:
            self.urls.remove(url)
        if self.path:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(self.urls))
            except OSError as e:
                log.warning("favorites: can't save (%s)", e)
        return list(self.urls)


def election_mode(inst) -> bool:
    return inst.board == "election" or any(own_page(u) for u in inst.urls)


def stream_tiles(urls: list[str], elec: bool) -> list[int]:
    """The tiles the policy may swap: video tiles, and in election mode only 3–4 (1–2 are our dashboards)."""
    return [i for i, u in enumerate(urls) if not own_page(u) and (not elec or i >= 2)]


def fav_tile(urls: list[str], favs: list[str], url: str, elec: bool) -> int:
    """Where a newly starred stream goes: tile 3 or 4, whichever doesn't hold the other favorite (index == len = add)."""
    n = len(urls)
    for i in (2, 3):
        if i < n and urls[i] not in favs:
            return i
    if n < 4 and (n >= 2 or not elec):
        return n
    return 3 if n > 3 else n - 1


class Policy:
    def __init__(self, on: bool = False) -> None:
        self.on = on
        self.reset()

    def reset(self) -> None:
        self.seen: list[str] | None = None
        self.seen_sound = -1
        self.started = None
        self.pref: str | None = None          # the last stream someone gave the sound to
        self.auto_sound = False               # the sound sits where the policy moved it
        self.orig: dict[int, str] = {}        # tile → the stream it showed before an auto-swap
        self.last_swap: dict[int, float] = {}
        self.paused: dict[int, float] = {}

    def set_on(self, on: bool) -> None:
        self.on = on
        if not on:
            self.orig.clear()
            self.auto_sound = False

    def view(self) -> dict:
        return {"on": self.on, "orig": {str(k): v for k, v in sorted(self.orig.items())}}

    def placed(self, inst, i: int, url: str) -> None:
        """A tile set on the policy's behalf (a favorite starred onto the wall): no hand pause."""
        if self.seen is not None:
            self.seen = list(inst.urls)
            self.seen_sound = inst.driver.sound
        self.orig.pop(i, None)

    def _observe(self, inst, now: float) -> None:
        urls, s = list(inst.urls), inst.driver.sound
        if self.seen is None or inst.started != self.started:
            self.reset()
            self.started = inst.started
            self.pref = urls[s] if 0 <= s < len(urls) else None
        else:
            for i, u in enumerate(urls):
                if i >= len(self.seen) or self.seen[i] != u:   # someone changed this tile
                    self.paused[i] = now + HAND_PAUSE_S
                    self.orig.pop(i, None)
            for i in range(len(urls), len(self.seen)):
                self.orig.pop(i, None)
            if s != self.seen_sound and 0 <= s < len(urls):   # someone moved the sound
                self.pref, self.auto_sound = urls[s], False
        self.seen, self.seen_sound = urls, s

    def tick(self, inst, board: Board, favs: list[str], now: float) -> list[str]:
        """One pass; returns what it did (for the log)."""
        with inst.lock:
            if not inst.urls or inst.follow:   # a mirror copies the TV, whose own policy decides
                if not inst.urls:
                    self.reset()
                return []
            self._observe(inst, now)
            if not self.on:
                return []
            acts = self._sound(inst, board, favs, now)
            acts += self._tiles(inst, board, favs, now)
            return acts

    def _sound(self, inst, board: Board, favs: list[str], now: float) -> list[str]:
        urls, s = list(inst.urls), inst.driver.sound
        if not 0 <= s < len(urls):
            return []
        video = [k for k, u in enumerate(urls) if not own_page(u)]
        target = None
        if board.is_(urls[s], AWAY, SOUND_AD_S, now):
            on_air = [k for k in video if k != s and board.is_(urls[k], "coverage", 0, now)]
            target = next((k for k in on_air if urls[k] in favs), None)
            if target is None:
                target = next((k for k in on_air if urls[k] == self.pref), None)
            if target is None and on_air:
                target = max(on_air, key=lambda k: board.get(urls[k])["confidence"])
        elif self.auto_sound:
            back = [k for k in video if board.is_(urls[k], "coverage", SOUND_BACK_S, now)]
            fav = [k for k in back if urls[k] in favs]
            target = next((k for k in fav if urls[k] == self.pref), fav[0] if fav else None)
            if target is None:
                target = next((k for k in back if urls[k] == self.pref), None)
        if target is None or target == s:
            return []
        inst.sound(target)
        self.seen_sound = target
        self.auto_sound = urls[target] != self.pref   # at the manual pick: nothing left to undo
        return [f"sound {s + 1}→{target + 1}"]

    def _home(self, i: int, favs: list[str], on_wall: set[str]) -> str | None:
        orig = self.orig.get(i)
        if orig in favs:
            return orig
        claimed = {u for k, u in self.orig.items() if k != i}
        free = [f for f in favs if f not in on_wall and f not in claimed]
        return free[0] if free else orig

    def _tiles(self, inst, board: Board, favs: list[str], now: float) -> list[str]:
        acts = []
        for i in stream_tiles(list(inst.urls), election_mode(inst)):
            urls = list(inst.urls)
            if self.paused.get(i, 0.0) > now or now - self.last_swap.get(i, -1e9) < SWAP_GAP_S:
                continue
            u, on_wall = urls[i], set(urls)
            if i in self.orig:
                home = self._home(i, favs, on_wall)
                if home and home != u and home not in on_wall and \
                        board.is_(home, "coverage", FAV_BACK_S if home in favs else BACK_S, now):
                    self._swap(inst, i, home, now)
                    self.orig.pop(i, None)
                    acts.append(f"tile {i + 1} home → {home}")
                    continue
            stuck = i == inst.driver.sound and not any(board.is_(urls[k], "coverage", 0, now) for k in range(len(urls))
                                                         if k != i and not own_page(urls[k]))
            if board.is_(u, SWAP_AWAY, SWAP_SOUND_S if stuck else SWAP_AFTER_S, now):
                cands = [c for c in board.fresh() if c not in on_wall and board.is_(c, "coverage", FRESH_S, now)]
                if not cands:
                    continue
                best = max(cands, key=lambda c: (c in favs, board.get(c)["confidence"], -board.get(c)["since"]))
                self.orig.setdefault(i, u)
                self._swap(inst, i, best, now)
                acts.append(f"tile {i + 1} {board.get(u)['label']} → {best}")
        return acts

    def _swap(self, inst, i: int, url: str, now: float) -> None:
        inst.set_tile(i, url)
        self.seen = list(inst.urls)
        self.seen_sound = inst.driver.sound
        self.last_swap[i] = now
