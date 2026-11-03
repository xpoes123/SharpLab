"""One wall instance ("tv" on the real display, "discord" headless): which tiles are up, which has the sound, the live
run (if a live preset is playing), the service-owned overlay state, and for headless instances the capture + idle
auto-stop. Every change bumps `rev`, so clients poll GET /i/<name> cheaply."""
from __future__ import annotations

import logging
import threading
import time

from wall.engine import Driver, Session
from wall.headless import Headless, free_vram_mb
from wall.live import LEAGUES, REFRESH_S, LiveRun, Scores, carries, matchup
from wall.presets import PresetStore, check_url

log = logging.getLogger("wall.instance")
BOARDS = {"nfl": "NFL", "nba": "NBA"}   # the boards a service-owned overlay can show (ESPN scoreboards)


class WallError(Exception):
    pass


class Instance:
    def __init__(self, name: str, session: Session, store: PresetStore, scores: Scores, *, headless: Headless | None = None,
                 driver: Driver | None = None, check=carries, run=None, clock=time.time, idle_s: float = 600,
                 first_viewer_s: float = 900, max_hours: float = 6, min_free_mb: int = 0, vram=free_vram_mb) -> None:
        self.name, self.session, self.store, self.scores = name, session, store, scores
        self.headless = headless
        self.driver = driver or Driver(session)
        self.check = check
        self.run = run or (lambda f: threading.Thread(target=f, name=f"wall-{name}-live", daemon=True).start())
        self.clock = clock
        self.idle_s, self.first_viewer_s, self.max_s = idle_s, first_viewer_s, max_hours * 3600
        self.min_free_mb, self.vram = min_free_mb, vram
        self.lock = threading.RLock()
        self.urls: list[str] = []
        self.live: LiveRun | None = None
        self.preset_name, self.preset = "", -1
        self.board, self.visible, self.layout, self.matchup, self.view = "", False, "lbar", "", ""
        self.ov_rev = 0
        self.rev = 0
        self.started = 0.0
        self.last_fetch = 0.0
        self.error = ""
        self.stopped_why = ""

    # ---- state -------------------------------------------------------------------------------------------------------
    def _bump(self) -> None:
        self.rev += 1

    def state(self) -> dict:
        with self.lock:
            lv = self.live
            live = None
            if lv:
                labels = []
                for k, u in enumerate(self.urls):
                    gid = lv.games[k] if k < len(lv.games) else None
                    g = lv.game(gid)
                    labels.append(matchup(g) if g else "")
                live = {"league": lv.league, "games": list(lv.games), "pinned": sorted(lv.pinned), "labels": labels,
                        "clock": [(lv.game(g) or {}).get("clock", "") if g else "" for g in lv.games],
                        "states": [(lv.game(g) or {}).get("state", "") if g else "" for g in lv.games]}
            out = {"inst": self.name, "running": bool(self.urls), "name": self.preset_name, "preset": self.preset,
                   "urls": list(self.urls), "sound": self.driver.sound, "rev": self.rev, "live": live,
                   "overlay": {"board": self.board, "visible": self.visible, "layout": self.layout, "view": self.view},
                   "started": self.started, "error": self.error, "stopped": self.stopped_why}
            if self.headless:
                out["stream"] = {"hls": f"/hls/{self.name}/index.m3u8", "up": self.headless.up, "last_fetch": self.last_fetch}
            return out

    # ---- start / stop --------------------------------------------------------------------------------------------------
    def start(self, preset: int | str | None = None, urls: list[str] | None = None, live: str = "", tiles: int = 4,
              board: str | None = None, layout: str | None = None, sound: int = 0) -> dict:
        p = self.store.find(preset) if preset is not None else None
        if preset is not None and p is None:
            raise WallError(f"no preset {preset!r}")
        if p:
            urls, live, tiles = list(p["urls"]), p.get("live", ""), p.get("tiles", 4)
            board = board if board is not None else p.get("overlay") or p.get("live") or ""
        urls = [check_url(u) for u in (urls or [])][:4]
        if live and live not in LEAGUES:
            raise WallError("live is nfl or nba")
        run = None
        if live:
            run = LiveRun(live, tiles)
            run.data = self.scores.get(live, True)
            plan = [(g, u) for g, u in run.make_plan(run.data) or [] if u]
            if plan:
                run.games, urls = [g for g, _ in plan], [u for _, u in plan]
                run.due = self.clock() + 5   # the first background round (page checks) right away
            else:
                run = None
                if not urls:
                    raise WallError(f"no {LEAGUES[live]} games on now or later today")
        if not urls:
            raise WallError("needs 1–4 URLs, a preset or live games")
        with self.lock:
            self._stop_locked("")
            if self.headless:
                free = self.vram()
                if free is not None and free < self.min_free_mb:
                    raise WallError(f"GPU busy: {free} MB free, the headless wall wants {self.min_free_mb}")
                try:
                    self.session.env = self.headless.start()
                except Exception as e:  # noqa: BLE001
                    self.error = str(e)
                    self._bump()
                    raise WallError(f"headless session failed: {e}") from e
            self.urls, self.live = urls, run
            self.preset_name = p["name"] if p else ("Live " + LEAGUES[live] if live else "Custom")
            self.preset = self.store.presets.index(p) if p else -1
            self.started, self.last_fetch, self.error, self.stopped_why = self.clock(), 0.0, "", ""
            self.driver.start(urls)
            self.driver.sound = max(0, min(sound, len(urls) - 1))
            if self.headless:
                try:
                    self.headless.begin_capture()
                except Exception as e:  # noqa: BLE001 — the tiles stay up; the status says why there's no stream
                    self.error = f"capture: {e}"
                    log.warning("%s capture failed: %s", self.name, e)
            if board is not None or layout is not None:
                self._set_board(board if board is not None else self.board, bool(board) if board is not None else self.visible, layout)
            self._bump()
            return self.state()

    def stop(self, why: str = "stopped") -> dict:
        with self.lock:
            self._stop_locked(why)
            return self.state()

    def _stop_locked(self, why: str) -> None:
        was = bool(self.urls)
        self.driver.stop()
        if self.headless:
            self.headless.stop()
            self.session.env = {}
        self.urls, self.live = [], None
        self.preset_name, self.preset = "", -1
        self.board, self.visible, self.matchup, self.view = "", False, "", ""
        self.ov_rev += 1
        if was:
            self.stopped_why = why
            self._bump()

    # ---- tile edits ------------------------------------------------------------------------------------------------------
    def _need(self) -> None:
        if not self.urls:
            raise WallError("the wall isn't running")

    def set_tile(self, i: int, url: str) -> dict:
        url = check_url(url)
        with self.lock:
            self._need()
            if 0 <= i < len(self.urls):
                self.urls[i] = url
                self.driver.set_tile(i, url)
            elif i == len(self.urls) < 4:
                self.urls.append(url)
                self.driver.add_tile(url)
            else:
                raise WallError("tile index out of range (at most 4 tiles)")
            if self.live:
                self.live.set_by_hand(i)
            self._bump()
            return self.state()

    def remove_tile(self, i: int) -> dict:
        with self.lock:
            self._need()
            if not 0 <= i < len(self.urls) or len(self.urls) < 2:
                raise WallError("can't remove that tile")
            del self.urls[i]
            self.driver.remove_tile(i)
            if self.live:
                self.live.remove(i)
            self._bump()
            return self.state()

    def move(self, i: int, j: int) -> dict:
        with self.lock:
            self._need()
            if not (0 <= i < len(self.urls) and 0 <= j < len(self.urls)):
                raise WallError("tile index out of range")
            if i != j:
                self.urls.insert(j, self.urls.pop(i))
                s = self.driver.sound
                self.driver.move_tile(i, j)
                self.driver.sound = j if s == i else (s - 1 if i < s <= j else s + 1 if j <= s < i else s)
                if self.live:
                    self.live.move(i, j)
                self._bump()
            return self.state()

    def sound(self, i: int) -> dict:
        with self.lock:
            self._need()
            if not 0 <= i < len(self.urls):
                raise WallError("tile index out of range")
            self.driver.focus(i)
            self._bump()
            return self.state()

    def pin(self, i: int) -> dict:
        with self.lock:
            self._need()
            if not (self.live and i < len(self.live.games) and self.live.games[i] is not None):
                raise WallError("only a live game's tile can be pinned")
            self.live.pinned ^= {i}
            self._bump()
            return self.state()

    # ---- overlay -----------------------------------------------------------------------------------------------------------
    def overlay(self, view: str, lbar: bool) -> dict:
        """Low level (tv): the overlay's state lives in the client; just run the process and tile for the L-bar."""
        with self.lock:
            self.view = view if view in ("lbar", "full", "matchup") else ""
            self.driver.overlay(self.view, lbar=lbar)
            self._bump()
            return self.state()

    def set_board(self, board: str | None = None, visible: bool | None = None, layout: str | None = None) -> dict:
        with self.lock:
            self._set_board(board, visible, layout)
            self._bump()
            return self.state()

    def _set_board(self, board: str | None, visible: bool | None, layout: str | None) -> None:
        if board is not None and board not in ("", *BOARDS):
            raise WallError(f"board is one of {', '.join(BOARDS)} (or empty)")
        if layout is not None and layout not in ("lbar", "full"):
            raise WallError("layout is lbar or full")
        if board:
            self.board = board
        if visible is not None:
            self.visible = bool(visible) and bool(self.board)
        if board == "":
            self.visible = False
        if layout:
            self.layout = layout
        self.view = self.layout if self.visible and self.urls else ""
        self.ov_rev += 1
        if self.urls:
            self.driver.overlay(self.view, lbar=self.view == "lbar")

    def overlay_view(self) -> dict:
        return {"view": self.view, "rev": self.ov_rev}

    def overlay_data(self, board: str = "") -> dict:
        b = board or self.board or "nfl"
        if b not in BOARDS:
            raise WallError("no such board")
        data = self.scores.get(b)
        return {"board": self.board, "visible": self.visible, "layout": self.layout, "matchup": "", "view": self.view,
                "rev": self.ov_rev, "boards": [{"id": k, "name": v} for k, v in BOARDS.items()], "shown": b,
                "data": data, "updated": self.clock() if data else 0, "error": "" if data else "no scoreboard yet"}

    # ---- the background tick (1 Hz from the server) -------------------------------------------------------------------------
    def seen(self) -> None:
        """A playlist fetch = someone is watching."""
        self.last_fetch = self.clock()

    def tick(self) -> None:
        with self.lock:
            if not self.urls:
                return
            now = self.clock()
            if self.headless:
                idle_since = self.last_fetch or self.started
                limit = self.idle_s if self.last_fetch else self.first_viewer_s
                why = "nobody watching" if now - idle_since > limit else f"ran {self.max_s / 3600:g} h" if now - self.started > self.max_s else ""
                if why:
                    log.info("%s auto-stop: %s", self.name, why)
                    self._stop_locked(f"auto-stopped: {why}")
                    return
            lv = self.live
            if not lv:
                return
            if lv.plan is not None:
                plan, lv.plan = lv.plan, None
                if self._apply_plan(lv, plan):
                    self._bump()
            if not lv.busy and now >= lv.due:
                lv.busy, lv.due = True, now + REFRESH_S
                self.run(lambda: self._plan_round(lv))

    def _plan_round(self, lv: LiveRun) -> None:
        try:
            lv.data = self.scores.get(lv.league, True) or lv.data
            lv.plan = lv.make_plan(lv.data, lambda u: self.check(lv.league, u))
        except Exception as e:  # noqa: BLE001 — a bad round leaves the wall as it is
            log.warning("%s live plan: %s", self.name, e)
        finally:
            lv.busy = False

    def _apply_plan(self, lv: LiveRun, plan: list[tuple[str | None, str]]) -> bool:
        """Swap the tiles whose game (or page) changed; pinned and by-hand tiles never move."""
        changed = False
        gone = [i for i, (gid, _) in enumerate(plan) if gid == "" and i < len(self.urls) and i not in lv.pinned
                and i < len(lv.games) and lv.games[i] is not None]
        for i, (gid, url) in enumerate(plan):
            if gid is None or not url:
                continue
            if i < len(self.urls):
                if i in lv.pinned or i >= len(lv.games) or lv.games[i] is None or (lv.games[i], self.urls[i]) == (gid, url):
                    continue
                lv.games[i], self.urls[i] = gid, url
                self.driver.set_tile(i, url)
                changed = True
            elif i == len(self.urls) < 4:
                lv.games.append(gid)
                self.urls.append(url)
                self.driver.add_tile(url)
                changed = True
        for i in reversed(gone):
            if len(self.urls) > 1:
                self.driver.remove_tile(i)
                del self.urls[i]
                lv.remove(i)
                changed = True
        return changed


def summary(st: dict) -> str:
    """One line for logs / the bot: "Live NFL · 3 tiles · sound 1"."""
    if not st.get("running"):
        return "off"
    return f"{st['name']} · {len(st['urls'])} tile{'s' * (len(st['urls']) != 1)} · sound {st['sound'] + 1}"

