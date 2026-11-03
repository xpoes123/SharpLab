"""One wall instance ("tv" on the real display, "discord" headless): which tiles are up, which has the sound, the live
run (if a live preset is playing), the service-owned overlay state, and for headless instances the capture + idle
auto-stop. Every change bumps `rev`, so clients poll GET /i/<name> cheaply.

Mirroring: a headless instance started from the TV's wall follows it (`follow`): every TV change (tiles, sound, layout,
board) is copied on the next tick. Any other edit stops the following; follow_source() brings it back. A TV tile the
discord wall may not stream (`vet` refuses it) is left out, with a notice."""
from __future__ import annotations

import logging
import threading
import time

from wall.engine import Driver, Session, sound_tile
from wall.headless import Headless, free_vram_mb
from wall.live import LEAGUES, REFRESH_S, LiveRun, Scores, carries, matchup
from wall.presets import PresetStore, check_url
from wall.rotate import EVERY_S, MARK, Rotation, kind, next_url, screen
from wall import streams

log = logging.getLogger("wall.instance")
APP_POLL_S = 2.0   # how often a TV wall asks the TV OS what it shows
AWAY_S = 8.0       # in another app this long → a wall the TV OS didn't start stops
BOARDS = {"nfl": "NFL", "nba": "NBA", "election": "Election"}   # the boards a service-owned overlay can show (ESPN scoreboards;
# election = wall/election.py's results, set by the server as feeds["election"])


class WallError(Exception):
    pass


class Instance:
    def __init__(self, name: str, session: Session, store: PresetStore, scores: Scores, *, headless: Headless | None = None,
                 driver: Driver | None = None, check=carries, run=None, clock=time.time, idle_s: float = 600,
                 first_viewer_s: float = 900, max_hours: float = 6, min_free_mb: int = 0, vram=free_vram_mb,
                 max_load: float = 16, min_avail_mb: int = 3072, sysload=None) -> None:
        self.name, self.session, self.store, self.scores = name, session, store, scores
        self.headless = headless
        self.driver = driver or Driver(session)
        self.check = check
        self.run = run or (lambda f: threading.Thread(target=f, name=f"wall-{name}-live", daemon=True).start())
        self.clock = clock
        self.idle_s, self.first_viewer_s, self.max_s = idle_s, first_viewer_s, max_hours * 3600
        self.min_free_mb, self.vram = min_free_mb, vram
        self.max_load, self.min_avail_mb, self.sysload = max_load, min_avail_mb, sysload or system_load
        self._over_since = 0.0
        self.before_start = lambda: None   # e.g. the LLM guard parking Ollama's models
        self.lock = threading.RLock()
        self.urls: list[str] = []
        self.live: LiveRun | None = None
        self.rot = Rotation()                   # election data tiles rotating (wall/rotate.py)
        self.preset_name, self.preset = "", -1
        self.board, self.visible, self.layout, self.matchup, self.view = "", False, "lbar", "", ""
        self.ov_rev = 0
        self.rev = 0
        self.started = 0.0
        self.last_fetch = 0.0
        self.error = ""
        self.stopped_why = ""
        # mirroring (headless): the instance followed, its last copied rev, a page vetter, the TV router's board
        self.source: Instance | None = None
        self.vet = None                         # url → url, ValueError when it may not be streamed
        self.source_board = lambda: ""          # the source's board id when its overlay lives outside this service
        self.follow = False
        self.src_rev = -1
        self._src_ov: tuple[int, str] = (-1, "")
        self._vetted: dict[str, str] = {}       # url → "" (ok) or why not
        self._refused: tuple[str, ...] = ()
        self.notice: dict | None = None         # {"id", "text", "at"}: something viewers should be told
        self.origin: dict | None = None         # where the start came from ({"guild", "channel"}), for the bot
        self.capture_output = ""                # the TV's output to stream in mirror mode ("" = copy with tiles)
        self.screen_sink = ""                   # whose sound goes with it ("" = the TV's default sink)
        self.tv_on_wall = lambda: True          # is the TV showing its wall right now?
        self._tv_wall, self._wall_at, self._screen_retry = False, 0.0, 0.0
        self.feeds: dict = {}                   # board id → () → its data, for boards that aren't ESPN scoreboards
        self.sync = None                        # wall/sync.SyncStore: the spoiler delay per tile page (set by the server)
        # the TV OS this wall's screen belongs to (tv only): host_app() → its active app ("wall", "dreamscape", … or
        # None = home / unreachable). The overlay draws only while it is "wall"; a wall the TV OS didn't start (the
        # service API, a mirror's change) stops once the TV OS has been in another app for AWAY_S.
        self.host_app = None
        self.app = None                          # the last answer ("" = never asked)
        self._app_at, self._away_since = 0.0, 0.0
        self.tv_owned = False                    # the TV OS was on its wall app when this wall started
        # the L-bar asked for (lbar_req) vs brought up for the bets (auto_lbar: 1 or 4 tiles, no L-bar, bets shown —
        # the bets live in the L-bar, never over the video; 2 and 3 tiles have the bands instead)
        self.lbar_req, self.auto_lbar, self._bets_seen = False, False, 0.0
        self.extra = None                        # inst → dict merged into state() (the server's ad-skip: onair, favorites)

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
                   "urls": list(self.urls), "sound": self.driver.sound, "volume": self.driver.volume, "rev": self.rev, "live": live,
                   "overlay": {"board": self.board, "visible": self.visible, "layout": self.layout, "view": self.view},
                   "started": self.started, "error": self.error, "stopped": self.stopped_why,
                   "mirror": self.follow, "notice": self.notice, "origin": self.origin,
                   "tv_app": self.app if self.host_app is not None else None, "overlay_on": self.overlay_on()}
            rot = self.source.rot if self.follow and self.source is not None else self.rot   # mirroring: the TV's clocks
            out["rotate"] = rot.view(self.urls, self.clock())
            out["streams"] = [streams.peek(u) for u in self.urls]   # YouTube tiles: live now / starts at (wall/streams.py)
            if self.sync is not None:
                out["sync"] = self.sync_view()
            if self.headless:
                out["capture"] = "screen" if self.screen else "tiles" if self.urls else ""
            if self.extra is not None:
                out.update(self.extra(self))
            if self.headless:
                out["stream"] = {"hls": f"/hls/{self.name}/index.m3u8", "up": self.headless.up, "last_fetch": self.last_fetch}
            return out

    # ---- start / stop --------------------------------------------------------------------------------------------------
    def start(self, preset: int | str | None = None, urls: list[str] | None = None, live: str = "", tiles: int = 4,
              board: str | None = None, layout: str | None = None, sound: int = 0, follow: bool = False,
              origin: dict | None = None, fixed: bool = False, app: str | None = None) -> dict:
        p = self.store.find(preset) if preset is not None else None
        if preset is not None and p is None:
            raise WallError(f"no preset {preset!r}")
        if p:
            urls, live, tiles, fixed = list(p["urls"]), p.get("live", ""), p.get("tiles", 4), bool(p.get("fixed"))
            board = board if board is not None else p.get("overlay") or p.get("live") or ""
        urls = [check_url(u) for u in (urls or [])][:4]
        fixed = bool(fixed and live and urls)
        if live and live not in LEAGUES:
            raise WallError("live is nfl or nba")
        run = None
        if fixed:   # the preset's pages stay as tiles set by hand (never swapped); games fill the rest, now and later
            urls = urls[:4]
            run = LiveRun(live, max(tiles, len(urls)))
            run.games = [None] * len(urls)
            run.data = self.scores.get(live, True)
            plan = [(g, u) for g, u in (run.make_plan(run.data) or [])[len(urls):] if g and u]
            run.games += [g for g, _ in plan]
            urls = urls + [u for _, u in plan]
            run.due = self.clock() + 5
        elif live:
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
        # the TV OS on its wall app now = it started this wall. The TV OS says so itself (app="wall") when it starts us: it
        # calls synchronously, so asking its /healthz back would wait out our 2 s timeout — and its own 2 s, failing the start
        app = app if app is not None else self._ask_app()   # an HTTP call: outside the lock
        with self.lock:
            self._stop_locked("")
            if self.headless:
                self._guard()
                self.before_start()
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
            self.rot.reset()
            self.preset_name = p["name"] if p else ("Live " + LEAGUES[live] if live else "Custom")
            self.preset = self.store.presets.index(p) if p else -1
            self.started, self.last_fetch, self.error, self.stopped_why = self.clock(), 0.0, "", ""
            self.follow, self.origin = follow, origin
            self._app_at, self._away_since, self.app = self.clock(), 0.0, app
            self.tv_owned = app == "wall"
            self.driver.start(urls)
            self.driver.sound = sound_tile(urls, max(0, min(sound, len(urls) - 1)))
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
        self.rot.reset()
        self.auto_lbar = False
        self.preset_name, self.preset = "", -1
        self.board, self.visible, self.matchup, self.view = "", False, "", ""
        self.follow, self.src_rev, self._refused = False, -1, ()
        self.ov_rev += 1
        if was:
            self.stopped_why = why
            self._bump()

    # ---- tile edits ------------------------------------------------------------------------------------------------------
    def _need(self) -> None:
        if not self.urls:
            raise WallError("the wall isn't running")

    def _unfollow(self) -> None:
        """A change made on this wall itself: it stops copying the TV (until follow_source)."""
        if self.follow:
            self.follow = False
            self._say("No longer mirroring the TV (this wall was changed) — “Follow the TV” brings it back.")

    def _say(self, text: str) -> None:
        n = (self.notice or {}).get("id", 0) + 1
        self.notice = {"id": n, "text": text, "at": self.clock()}

    def set_tile(self, i: int, url: str) -> dict:
        url = check_url(url)
        with self.lock:
            self._need()
            self._own()
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
            self.rot.hold(i, self.clock())
            self._bump()
            return self.state()

    def remove_tile(self, i: int) -> dict:
        with self.lock:
            self._need()
            self._own()
            if not 0 <= i < len(self.urls) or len(self.urls) < 2:
                raise WallError("can't remove that tile")
            del self.urls[i]
            self.driver.remove_tile(i)
            if self.live:
                self.live.remove(i)
            self.rot.reset()
            self._bump()
            return self.state()

    def move(self, i: int, j: int) -> dict:
        with self.lock:
            self._need()
            self._own()
            if not (0 <= i < len(self.urls) and 0 <= j < len(self.urls)):
                raise WallError("tile index out of range")
            if i != j:
                self.urls.insert(j, self.urls.pop(i))
                s = self.driver.sound
                self.driver.move_tile(i, j)
                self.driver.sound = j if s == i else (s - 1 if i < s <= j else s + 1 if j <= s < i else s)
                if self.live:
                    self.live.move(i, j)
                self.rot.reset()
                self._bump()
            return self.state()

    def sound(self, i: int) -> dict:
        with self.lock:
            self._need()
            self._own()
            if not 0 <= i < len(self.urls):
                raise WallError("tile index out of range")
            self.driver.focus(i)
            self._bump()
            return self.state()

    def volume(self, volume: int | None = None, delta: int = 0) -> dict:
        """The sound tile's stream volume (%): set it, or step it (the phone remote's up/down). Kept across presets."""
        with self.lock:
            self.driver.set_volume((self.driver.volume if volume is None else volume) + delta)
            self._bump()
            return self.state()

    def pin(self, i: int) -> dict:
        with self.lock:
            self._need()
            self._own()
            if not (self.live and i < len(self.live.games) and self.live.games[i] is not None):
                raise WallError("only a live game's tile can be pinned")
            self.live.pinned ^= {i}
            self._bump()
            return self.state()

    def reset(self, urls: list[str]) -> dict:
        """Back to the default screens: each tile that differs swaps in place, extra tiles go, missing ones come; the
        rotation starts over (no holds)."""
        with self.lock:
            self._need()
            self._own()
            urls = [check_url(u) for u in urls][:4]
            for i, u in enumerate(urls):
                if i < len(self.urls):
                    if self.urls[i] != u:
                        self.urls[i] = u
                        self.driver.set_tile(i, u)
                else:
                    self.urls.append(u)
                    self.driver.add_tile(u)
            while len(self.urls) > max(1, len(urls)):
                self.driver.remove_tile(len(self.urls) - 1)
                self.urls.pop()
            if self.live:
                self.live = None
            self.rot.reset()
            self._bump()
            return self.state()

    def show_screen(self, key: str, for_s: float = 2 * EVERY_S) -> bool:
        """An event (a race call…): put that data screen ("map?view=S") on a rotating tile nobody is holding, for a while.
        Nothing when it's already up, every rotating tile is held, or this wall mirrors another."""
        with self.lock:
            if not self.urls or self.follow or any(screen(u) == key for u in self.urls):
                return False
            now = self.clock()
            free = [i for i, u in enumerate(self.urls) if screen(u) and self.rot.held.get(i, 0.0) <= now]
            if not free:
                return False
            same = [i for i in free if kind(screen(self.urls[i])) == kind(key)]   # a map replaces the map: never two maps
            i = (same or free)[0]
            if any(kind(screen(u)) == kind(key) for j, u in enumerate(self.urls) if j != i):
                return False   # that kind is up on a held tile: leave it
            self.urls[i] = self.urls[i].split(MARK, 1)[0] + MARK + key
            self.driver.set_tile(i, self.urls[i])
            self.rot.at[i] = now + for_s
            self._bump()
            return True

    def rotate(self, i: int, act: str) -> dict:
        """A rotating data tile: "hold" keeps its screen, "next" moves it on now; either way it then stays put HOLD_S."""
        with self.lock:
            self._need()
            self._own()
            if not (0 <= i < len(self.urls) and screen(self.urls[i])):
                raise WallError("that tile doesn't rotate")
            if act == "next":
                url = next_url(self.urls, i, force=True)
                if not url:
                    raise WallError("no other screen to show")
                self.urls[i] = url
                self.driver.set_tile(i, url)
            elif act != "hold":
                raise WallError("act is hold or next")
            self.rot.hold(i, self.clock())
            self._bump()
            return self.state()

    # ---- overlay -----------------------------------------------------------------------------------------------------------
    def overlay(self, view: str, lbar: bool) -> dict:
        """Low level (tv): the overlay's state lives in the client; just run the process and tile for the L-bar."""
        with self.lock:
            self.view = view if view in ("lbar", "full", "matchup") else ""
            self.ov_rev += 1   # a mirror re-reads the board on this
            self.lbar_req = lbar
            self.driver.overlay(self.view, lbar=lbar or self.auto_lbar)
            self._bump()
            return self.state()

    def set_board(self, board: str | None = None, visible: bool | None = None, layout: str | None = None) -> dict:
        with self.lock:
            if self.urls:
                self._own()
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
            self.lbar_req = self.view == "lbar"
            self.driver.overlay(self.view, lbar=self.lbar_req or self.auto_lbar)

    def sync_view(self) -> dict:
        """The spoiler delay of each tile (+ its game), for the web's Bets panel and the overlay."""
        from wall.sync import tile_games
        with self.lock:
            urls = list(self.urls)
        games = tile_games(self, self.scores)
        return {"show": self.sync.show, "default": self.sync.default,
                "tiles": [{**t, "game": g} for t, g in zip(self.sync.tiles(urls), games)]}

    def overlay_view(self) -> dict:
        return {"view": self.view if self.overlay_on() else "", "rev": self.ov_rev}

    def overlay_data(self, board: str = "") -> dict:
        b = board or self.board or "nfl"
        if b not in BOARDS:
            raise WallError("no such board")
        data = self.feeds[b]() if b in self.feeds else self.scores.get(b)
        return {"board": self.board, "visible": self.visible, "layout": self.layout, "matchup": "", "view": self.view,
                "rev": self.ov_rev, "boards": [{"id": k, "name": v} for k, v in BOARDS.items()], "shown": b,
                "data": data, "updated": self.clock() if data else 0, "error": "" if data else "no scoreboard yet"}

    # ---- mirroring the TV (headless) -------------------------------------------------------------------------------------------
    def _snapshot(self, src: "Instance") -> dict:
        """What the source shows now: tiles, sound and its overlay as (board, visible, layout)."""
        with src.lock:
            snap = {"rev": src.rev, "urls": list(src.urls), "sound": src.driver.sound, "ov_rev": src.ov_rev, "view": src.view,
                    "board": src.board, "visible": src.visible, "layout": src.layout}
        if snap["board"]:   # a service-owned overlay (started with a board here)
            snap["overlay"] = (snap["board"], snap["visible"], snap["layout"])
        elif snap["view"] in ("lbar", "full"):   # the TV router owns it: ask it which board, once per overlay change
            if self._src_ov[0] != snap["ov_rev"]:
                try:
                    b = self.source_board() or ""
                except Exception as e:  # noqa: BLE001 — no board beats no mirror
                    log.warning("%s: the TV's board: %s", self.name, e)
                    b = ""
                self._src_ov = (snap["ov_rev"], b)
            b = self._src_ov[1]
            snap["overlay"] = (b, True, snap["view"]) if b in BOARDS else ("", False, snap["view"])
        else:
            snap["overlay"] = ("", False, snap["layout"] or "lbar")
        return snap

    def _screen(self, urls: list[str], sound: int) -> tuple[list[str], int, list[str]]:
        """The source's tiles this wall may stream, the sound's index among them, and the refused ones (with why)."""
        keep, refused = [], []
        for u in urls:
            if self.vet is not None and u not in self._vetted:
                try:
                    self.vet(u)
                    self._vetted[u] = ""
                except ValueError as e:
                    self._vetted[u] = str(e) or "refused"
            why = self._vetted.get(u, "")
            (refused.append(f"{u[:80]} ({why})") if why else keep.append(u))
        idx = [k for k, u in enumerate(urls) if not self._vetted.get(u)]
        return keep, idx.index(sound) if sound in idx else 0, refused

    @property
    def screen(self) -> bool:
        """Streaming the TV's real output (no tiles of our own)."""
        return bool(self.headless and getattr(self.headless, "mode", "") == "screen")

    def _on_wall(self) -> bool:
        """Is the TV showing its wall (checked at most every 3 s; the TV router's /healthz)? Never stream another TV app."""
        now = self.clock()
        if now >= self._wall_at:
            self._wall_at = now + 3
            try:
                self._tv_wall = bool(self.tv_on_wall())
            except Exception:  # noqa: BLE001
                self._tv_wall = False
        return self._tv_wall

    def mirror_plan(self) -> dict:
        """What copying the source now means: its tiles this wall may stream (refused ones left out), sound, overlay."""
        src = self.source
        if src is None or not src.urls:
            raise WallError("the TV's wall isn't on")
        snap = self._snapshot(src)
        keep, sound, refused = self._screen(snap["urls"], snap["sound"])
        return {"rev": snap["rev"], "urls": keep, "sound": sound, "overlay": snap["overlay"], "refused": refused}

    def start_mirror(self, origin: dict | None = None) -> dict:
        """Stream the TV's wall and follow it: its real screen when every tile may be streamed (and a capture output is
        set), else tiles of our own copying it, minus the refused pages (with a notice)."""
        plan = self.mirror_plan()
        self._wall_at = 0.0
        on_wall = self._on_wall() if self.capture_output else False
        with self.lock:
            if not plan["urls"]:
                raise WallError("none of the TV's tiles can be streamed: " + "; ".join(plan["refused"])[:200])
            if on_wall and not plan["refused"]:
                self._start_screen(plan["urls"], plan["sound"], plan["overlay"], origin)
            else:
                self._start_tiles(plan["urls"], plan["sound"], plan["overlay"], origin)
            self.src_rev = plan["rev"]
            self._note_refused(plan["refused"])
            return self.state()

    def _start_tiles(self, urls, sound, overlay, origin) -> None:
        board, visible, layout = overlay
        keep = (self.started, self.last_fetch) if self.urls else None   # a switch, not a new start: same session
        self.start(urls=urls, sound=sound, board=board if visible else "", layout=layout, follow=True, origin=origin)
        self.preset_name = "Mirroring the TV"
        if keep:
            self.started, self.last_fetch = keep

    def _start_screen(self, urls, sound, overlay, origin) -> None:
        """Capture the TV's output instead of running tiles. Falls back to tiles (and retries a minute later)."""
        with self.lock:
            keep = (self.started, self.last_fetch) if self.urls else None   # a switch, not a new start: same session
            self._stop_locked("")
            self._guard()
            self.before_start()
            try:
                self.headless.capture_screen(self.capture_output, self.screen_sink)
            except Exception as e:  # noqa: BLE001
                log.warning("%s: screen capture failed (%s), running tiles instead", self.name, e)
                self._screen_retry = self.clock() + 60
                self._start_tiles(urls, sound, overlay, origin)
                if keep:
                    self.started, self.last_fetch = keep
                self.error = f"screen capture: {e}"
                return
            self.urls, self.live = list(urls), None
            self.preset_name, self.preset = "Mirroring the TV", -1
            (self.started, self.last_fetch), self.error, self.stopped_why = keep or (self.clock(), 0.0), "", ""
            self.follow, self.origin = True, origin
            self._copy_state(urls, sound, *overlay)
            self._bump()

    def _leave_screen(self, why: str) -> None:
        """The TV left its wall: keep the last tiles up as our own while someone watches, else stop."""
        with self.lock:
            if not self.screen:
                return
            if self.last_fetch and self.clock() - self.last_fetch < 90:
                board = self.board if self.visible else ""
                self._start_tiles(list(self.urls), self.driver.sound, (board, self.visible, self.layout), self.origin)
                self._say(f"{why} — the stream keeps its last tiles.")
            else:
                self._stop_locked(why)

    def _own(self) -> None:
        """Before a change made on this wall itself: stop following, and if it was the TV's screen, run our own tiles."""
        self._unfollow()
        if self.screen:
            board = self.board if self.visible else ""
            started, fetch = self.started, self.last_fetch
            self.start(urls=list(self.urls), sound=self.driver.sound, board=board, layout=self.layout, origin=self.origin)
            self.started, self.last_fetch = started, fetch

    def follow_source(self) -> dict:
        """Mirror the TV again (after a change here stopped it): copies it now and keeps following."""
        with self.lock:
            self._need()
            if self.source is None:
                raise WallError("there's no TV wall to follow")
            self.follow, self.src_rev, self._wall_at, self._screen_retry = True, -1, 0.0, 0.0
            self._say("Mirroring the TV again.")
        self.mirror_tick()
        with self.lock:
            self._bump()
            return self.state()

    def _note_refused(self, refused: list[str]) -> None:
        if tuple(refused) != self._refused:
            self._refused = tuple(refused)
            if refused:
                self._say("Skipped on the stream (can't be shown to viewers): " + "; ".join(refused))

    def mirror_tick(self) -> None:
        """Follow the source: copy its latest change; switch between its real screen and our own tiles as needed (the TV
        leaving its wall or stopping leaves the last tiles up)."""
        src = self.source
        if not (self.follow and src and self.urls):
            return
        on_wall = self._on_wall() if self.capture_output else False
        if self.screen and (not on_wall or not src.urls):
            self._leave_screen("The TV left the wall" if not on_wall else "The TV's wall stopped")
            return
        want_screen = bool(self.capture_output and on_wall and src.urls and not self.screen and not self._refused
                           and self.clock() >= self._screen_retry)
        if src.rev == self.src_rev and not want_screen:
            return
        snap = self._snapshot(src)
        if not snap["urls"]:
            self.src_rev = snap["rev"]
            return
        keep, sound, refused = self._screen(snap["urls"], snap["sound"])
        with self.lock:
            if not (self.follow and self.urls):
                return
            self.src_rev = snap["rev"]
            self._note_refused(refused)
            can_screen = bool(self.capture_output and on_wall and not refused)
            if self.screen and not can_screen:   # a page we may not stream came up on the TV: our own tiles, without it
                if keep:
                    self._start_tiles(keep, sound, snap["overlay"], self.origin)
                else:
                    self._stop_locked("nothing on the TV can be streamed")
                return
            if can_screen and not self.screen and self.clock() >= self._screen_retry:
                self._start_screen(keep, sound, snap["overlay"], self.origin)
                return
            if not keep:
                return
            changed = self._copy_state(keep, sound, *snap["overlay"]) if self.screen else self._copy(keep, sound, *snap["overlay"])
            if changed or refused:
                self._bump()

    def _copy_state(self, urls: list[str], sound: int, board: str, visible: bool, layout: str) -> bool:
        """Screen mode: the picture is the TV's own; only keep the state (what's on, sound, overlay) in step."""
        before = (list(self.urls), self.driver.sound, self.board, self.visible, self.layout)
        self.urls, self.driver.sound = list(urls), max(0, min(sound, len(urls) - 1))
        self.board, self.visible, self.layout = (board if visible else ""), visible, layout
        self.view = layout if visible else ""
        return before != (list(self.urls), self.driver.sound, self.board, self.visible, self.layout)

    # ---- resource guard (headless) ------------------------------------------------------------------------------------------
    def _guard(self) -> None:
        if not self.headless:
            return
        load, avail = self.sysload()
        if load is not None and load > self.max_load:
            log.warning("%s refused: load %.1f > %.1f", self.name, load, self.max_load)
            raise WallError(f"the box is busy (load {load:.1f}) — try again in a minute")
        if avail is not None and avail < self.min_avail_mb:
            log.warning("%s refused: %d MB available < %d", self.name, avail, self.min_avail_mb)
            raise WallError(f"the box is low on memory ({avail} MB free) — try again later")

    def _overloaded(self, now: float) -> str:
        """Why to auto-stop: load or memory past the limits for 30 s straight ("" = fine)."""
        load, avail = self.sysload()
        bad = (f"load {load:.1f}" if load is not None and load > self.max_load else
               f"{avail} MB memory available" if avail is not None and avail < self.min_avail_mb else "")
        if not bad:
            self._over_since = 0.0
            return ""
        self._over_since = self._over_since or now
        return bad if now - self._over_since >= 30 else ""

    def _copy(self, urls: list[str], sound: int, board: str, visible: bool, layout: str) -> bool:
        """Make this wall's tiles / sound / overlay equal the given ones with the fewest relaunches."""
        changed = False
        if urls != self.urls and len(urls) == len(self.urls) and sorted(urls) == sorted(self.urls):   # a reorder: move, don't reload
            for j, u in enumerate(urls):
                i = self.urls.index(u, j)
                if i != j:
                    self.urls.insert(j, self.urls.pop(i))
                    self.driver.move_tile(i, j)
            changed = True
        for k, u in enumerate(urls):
            if k < len(self.urls):
                if self.urls[k] != u:
                    self.urls[k] = u
                    self.driver.set_tile(k, u)
                    changed = True
            else:
                self.urls.append(u)
                self.driver.add_tile(u)
                changed = True
        while len(self.urls) > len(urls):
            self.driver.remove_tile(len(self.urls) - 1)
            self.urls.pop()
            changed = True
        if self.driver.sound != sound or changed:
            changed = changed or self.driver.sound != sound
            self.driver.focus(max(0, min(sound, len(self.urls) - 1)))
        if (self.board if self.visible else "", self.visible, self.layout) != (board if visible else "", visible, layout):
            self._set_board(board if visible else "", visible, layout)
            changed = True
        return changed

    # ---- the background tick (1 Hz from the server) -------------------------------------------------------------------------
    def seen(self) -> None:
        """A playlist fetch = someone is watching."""
        self.last_fetch = self.clock()

    def bets_lbar(self, has_bets: bool, now: float) -> None:
        """Bring the L-bar up for the bets on a 1- or 4-tile wall with no L-bar (and let it go 30 s after they're gone):
        the tiles re-tile into the L-bar's corner, the overlay draws the L-bar (bets payload "auto_lbar")."""
        with self.lock:
            want = has_bets and len(self.urls) in (1, 4) and self.view == "" and self.overlay_on()
            if want:
                self._bets_seen = now
            on = want or (self.auto_lbar and len(self.urls) in (1, 4) and self.view == "" and self.overlay_on()
                          and now - self._bets_seen < 30)
            if on != self.auto_lbar:
                self.auto_lbar = on
                if self.urls:
                    self.driver.overlay(self.view, lbar=self.lbar_req or on)
                self._bump()

    # ---- the TV OS's active app (tv) ----------------------------------------------------------------------------------
    def _ask_app(self) -> str | None:
        if self.host_app is None:
            return None
        try:
            return self.host_app() or None
        except Exception:  # noqa: BLE001 — unknown = not on the wall: the overlay hides, nothing stops
            return None

    def overlay_on(self) -> bool:
        """May this wall's overlay draw (bets, bands, boards)? Always on a wall without a TV OS; on the TV only while
        the TV OS shows its wall app — never over Dreamscape, YouTube, News…"""
        return self.host_app is None or self.app == "wall"

    def app_tick(self, now: float) -> bool:
        """Every APP_POLL_S while the wall runs: ask the TV OS what it shows; True when the wall should stop (it moved to
        another app for AWAY_S and didn't start this wall itself). Called without the lock (an HTTP call)."""
        if self.host_app is None or now - self._app_at < APP_POLL_S:
            return False
        self._app_at = now
        app = self._ask_app()
        if app != self.app:
            self.app = app
            self._bump()
        if app in (None, "wall"):   # home / unreachable: hidden overlay, but nobody left for another app
            self._away_since = 0.0
            return False
        self._away_since = self._away_since or now
        return not self.tv_owned and now - self._away_since >= AWAY_S

    def tick(self) -> None:
        try:
            self.mirror_tick()
        except Exception as e:  # noqa: BLE001 — a bad copy leaves the wall as it is
            log.warning("%s mirror: %s", self.name, e)
        if self.urls and self.app_tick(self.clock()):
            with self.lock:
                if self.urls:
                    log.info("%s auto-stop: the TV moved to %s", self.name, self.app)
                    self._stop_locked(f"auto-stopped: the TV moved to {self.app}")
                    return
        with self.lock:
            if not self.urls:
                return
            now = self.clock()
            if self.headless:
                idle_since = self.last_fetch or self.started
                limit = self.idle_s if self.last_fetch else self.first_viewer_s
                why = "nobody watching" if now - idle_since > limit else f"ran {self.max_s / 3600:g} h" if now - self.started > self.max_s else ""
                over = self._overloaded(now)
                why = why or (f"the box is overloaded ({over})" if over else "")
                if why:
                    log.info("%s auto-stop: %s", self.name, why)
                    self._stop_locked(f"auto-stopped: {why}")
                    return
            if not self.follow:   # a mirror copies the TV's rotation instead
                swaps = self.rot.due(self.urls, now)
                for i, url in swaps:
                    self.urls[i] = url
                    self.driver.set_tile(i, url)
                if swaps:
                    self._bump()
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


def system_load() -> tuple[float | None, int | None]:
    """(1-min load average, MemAvailable in MB); None where unknown."""
    import os
    try:
        load = os.getloadavg()[0]
    except OSError:
        load = None
    try:
        with open("/proc/meminfo") as f:
            avail = next(int(line.split()[1]) // 1024 for line in f if line.startswith("MemAvailable:"))
    except (OSError, StopIteration, ValueError):
        avail = None
    return load, avail


def summary(st: dict) -> str:
    """One line for logs / the bot: "Live NFL · 3 tiles · sound 1"."""
    if not st.get("running"):
        return "off"
    return f"{st['name']} · {len(st['urls'])} tile{'s' * (len(st['urls']) != 1)} · sound {st['sound'] + 1}"

