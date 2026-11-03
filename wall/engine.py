"""The wall's box side (moved from the TV OS's src/tv/wall.py Driver): each tile is its own Chromium (own profile, so
its own pid, window and audio stream) launched with --profile-directory=Wall, which makes the window class end in
"-Wall". A KWin rule (deploy/kwin-wall-rule.sh, once per box; the nested session reads the same kwinrulesrc) forces
those windows borderless and never-fullscreen, so a site's fullscreen request fills just its tile. A KWin script tiles
them (qml/lbar.js has the geometry, shared with the overlay), CDP (one debugging port per tile) fullscreens each page's
video, and pactl mutes every tile but the one with the sound.

A Session says where a Driver draws: the TV's real KWin session, or a nested headless one (headless.py). Everything
that talks to the compositor (chromium, qdbus6, qml6) gets the session's env.
"""
from __future__ import annotations

import itertools
import json
import logging
import os
import re
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("wall.engine")
QML_DIR = Path(__file__).parent / "qml"
QML = QML_DIR / "Overlay.qml"
LBAR_JS = (QML_DIR / "lbar.js").read_text().replace(".pragma library", "")
TILES = json.loads(re.search(r"^var TILES = (\{.*\});$", LBAR_JS, re.M).group(1))
LAYOUTS = ("lbar", "full", "matchup")

# `env` execs chromium in place, so the pid (and window matching) is unchanged
GPU_ENV = ["env", "LIBVA_DRIVER_NAME=nvidia", "NVD_BACKEND=direct"]
# no KWallet / Secret Service: a headless session has none, and each try crash-looped ksecretd
QUIET_FLAGS = ["--password-store=basic"]
# VaapiOnNvidiaGPUs + VaapiVideoDecoder: without them Chromium 154 never uses nvidia-vaapi-driver and decodes in software
# (CDP Media: FFmpegVideoDecoder; a 1080p30 h264 tile's renderer 31 % CPU → 1.7 % with VaapiVideoDecoder, measured 2026-11-02)
GPU_FLAGS = ["--enable-features=AcceleratedVideoDecodeLinuxGL,AcceleratedVideoDecodeLinuxZeroCopyGL,VaapiIgnoreDriverChecks,"
             "VaapiOnNvidiaGPUs,VaapiVideoDecoder",
             "--ignore-gpu-blocklist"]

FULLSCREEN_JS = """(async () => {
  // the biggest <video>: fullscreen it inside the tile (KWin keeps tile windows from going truly fullscreen)
  const v = [...document.querySelectorAll('video')].sort((a, b) => b.offsetWidth * b.offsetHeight - a.offsetWidth * a.offsetHeight)[0];
  if (!v) return false;
  v.play().catch(() => {});
  if (v.hasAttribute('data-tvfs')) return true;          // already pinned by the CSS fallback
  if (!v.offsetWidth && document.fullscreenElement !== v) return false;
  const r = v.getBoundingClientRect(), fits = Math.abs(r.width - innerWidth) <= 4 || Math.abs(r.height - innerHeight) <= 4;
  if (document.fullscreenElement === v && fits) return true;
  if (document.fullscreenElement) await Promise.race([document.exitFullscreen(), new Promise((ok) => setTimeout(ok, 800))]);
  // some pages never resolve requestFullscreen (an ad overlay, a stuck request): give it 1.5 s, then pin the video with CSS
  const done = await Promise.race([v.requestFullscreen().then(() => true, () => false), new Promise((ok) => setTimeout(() => ok(false), 1500))]);
  if (done && document.fullscreenElement === v) return true;
  if (!document.getElementById('__tvfs')) {
    const st = document.createElement('style'); st.id = '__tvfs';
    st.textContent = 'video[data-tvfs]{position:fixed!important;inset:0!important;width:100vw!important;height:100vh!important;max-width:none!important;max-height:none!important;z-index:2147483647!important;background:#000!important;object-fit:contain!important;transform:none!important;margin:0!important}html,body{overflow:hidden!important}';
    document.documentElement.appendChild(st);
  }
  v.setAttribute('data-tvfs', '1');
  return true;
})()"""


# what the stall watchdog needs from a tile's biggest <video>
PROBE_JS = """(() => {
  const v = [...document.querySelectorAll('video')].sort((a, b) => b.offsetWidth * b.offsetHeight - a.offsetWidth * a.offsetHeight)[0];
  if (!v) return null;
  const r = (x) => Array.from({length: x.length}, (_, i) => [x.start(i), x.end(i)]);
  const sk = r(v.seekable);
  return {t: v.currentTime, paused: v.paused, ended: v.ended, ready: v.readyState, buffered: r(v.buffered),
          seekable_end: sk.length ? sk[sk.length - 1][1] : 0};
})()"""


@dataclass
class Stall:
    """One tile's watchdog state: the last currentTime seen, since when it hasn't moved, whether we went to the edge."""
    t: float = -1.0
    since: float = 0.0
    edge: bool = False


def stall_action(st: Stall, probe: dict | None, now: float) -> tuple[str, float] | None:
    """A live tile whose video stopped advancing (the site's player doesn't jump HLS gaps): ("seek", t) / ("reload", 0)
    or None. After 3 s stuck: jump to the next buffered range (+0.3 s); after 10 s with nothing buffered ahead: the live
    edge − 3 s (once); after 30 s: reload the tile."""
    if not probe or probe.get("paused") or probe.get("ended"):
        st.t, st.since, st.edge = -1.0, now, False
        return None
    t = float(probe.get("t") or 0)
    if t != st.t:
        st.t, st.since, st.edge = t, now, False
        return None
    stuck = now - st.since
    if stuck >= 30:
        st.since, st.edge = now, False
        return ("reload", 0.0)
    if stuck >= 3:
        later = [b[0] for b in probe.get("buffered") or [] if b[0] > t + 0.1]
        if later:
            return ("seek", min(later) + 0.3)
        end = float(probe.get("seekable_end") or 0)
        if stuck >= 10 and not st.edge and end - 3 > t:
            st.edge = True
            return ("seek", end - 3)
    return None


@dataclass
class Session:
    """Where a wall draws. env: what its children get on top of ours (WAYLAND_DISPLAY, DBUS_SESSION_BUS_ADDRESS,
    PULSE_SINK…). cdp_base: tile slot s debugs on cdp_base + s. profiles: dir for chromium-wall-<prefix><slot>.
    overlay_api: the --api the overlay process polls. sink: only touch sink inputs on this sink ("" = any)."""
    name: str
    env: dict = field(default_factory=dict)
    cdp_base: int = 9231
    profiles: str = os.path.expanduser("~/.local/state/tv")
    prefix: str = ""
    overlay_api: str = "http://127.0.0.1:7790"
    unmute_on_stop: bool = False   # the TV: tile mutes are remembered per app name ("Chromium" = the kiosk too)
    extra_flags: list = field(default_factory=list)

    def child_env(self) -> dict:
        return {**os.environ, **self.env}


def tile_map(n: int) -> tuple[int, int, list[tuple[float, float, float, float]]]:
    """lbar.js's arrangement of n tiles as fractions of its bounding box: (aspect w, aspect h, [(x, y, w, h)])."""
    t = TILES[str(max(1, min(4, n)))]
    cols, rows = max(c + k for c, _, k in t), max(r + k for _, r, k in t)
    return cols * 16, rows * 9, [(c / cols, r / rows, k / cols, k / rows) for c, r, k in t]


def tile_js(pids: list[int], lbar: bool, sel: int) -> str:
    """KWin script: len(pids) tiles (lbar.js's wallTiles: exact 16:9, in the corner the L-bar leaves when there is one,
    else the whole screen), keys to tile `sel`."""
    return f"""{LBAR_JS}
const pids = {json.dumps(pids)}, sel = {int(sel)}, useL = {"true" if lbar else "false"};
const g = workspace.clientArea(KWin.FullScreenArea, workspace.activeScreen, workspace.currentDesktop);
const T = wallTiles(pids.length, wallArea(g.width, g.height, useL));
for (const w of workspace.windowList()) {{
  const i = pids.indexOf(w.pid); if (i < 0 || i >= T.length) continue;
  w.noBorder = true; w.keepAbove = true;
  w.frameGeometry = {{x: g.x + T[i].x, y: g.y + T[i].y, width: T[i].w, height: T[i].h}};
  if (i == sel) workspace.activeWindow = w;
}}"""


def overlay_cmd(api: str) -> list[str]:
    return ["qml6", str(QML), "--", "--api", api]


def overlay_env(env: dict) -> dict:
    """Forced onto Wayland + layer-shell: DISPLAY=:0 may be set too, and an xcb fallback would be a plain window over nothing."""
    return {**env, "QT_QPA_PLATFORM": "wayland", "QT_WAYLAND_SHELL_INTEGRATION": "layer-shell"}


def tile_of(pid: int, roots: list[int], ppid) -> int | None:
    """Which tile's chromium (roots[i]) is pid, or an ancestor of it? ppid(pid) → parent pid or 0."""
    for _ in range(32):
        if pid in roots:
            return roots.index(pid)
        if pid <= 1:
            return None
        pid = ppid(pid)
    return None


def _ppid(pid: int) -> int:
    try:
        return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        return 0


def kwin(js: str, name: str, env: dict | None = None) -> None:
    """Load, run, unload a KWin script on the session's D-Bus."""
    env = env or dict(os.environ)
    path = Path(env.get("XDG_RUNTIME_DIR", "/tmp")) / f"wall-{name}-{os.getpid()}.js"
    path.write_text(js)
    q = lambda *a: subprocess.run(["qdbus6", "org.kde.KWin", *a], capture_output=True, text=True, timeout=5, env=env)
    try:
        sid = q("/Scripting", "org.kde.kwin.Scripting.loadScript", str(path), name).stdout.strip()
        q(f"/Scripting/Script{sid}", "org.kde.kwin.Script.run")
        q("/Scripting", "org.kde.kwin.Scripting.unloadScript", name)
    except Exception as e:  # noqa: BLE001 — no KWin (dev box) is not an error worth more than a log line
        log.warning("kwin %s failed: %s", name, e)


def _end(p: subprocess.Popen | None) -> None:
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            p.kill()


SEEK_JS = """(() => { const v = [...document.querySelectorAll('video')].sort((a, b) => b.offsetWidth * b.offsetHeight
  - a.offsetWidth * a.offsetHeight)[0]; if (v) { v.currentTime = %.3f; v.play().catch(() => {}); } })()"""


@contextmanager
def cdp_page(port: int):
    """call(method, params) → result, on the tile's first page target."""
    from websockets.sync.client import connect
    import urllib.request
    targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2))
    page = next(t for t in targets if t.get("type") == "page")
    with connect(page["webSocketDebuggerUrl"], open_timeout=2, max_size=None) as ws:
        ids = itertools.count(1)

        def call(method: str, params: dict | None = None) -> dict:
            n = next(ids)
            ws.send(json.dumps({"id": n, "method": method, "params": params or {}}))
            while True:
                m = json.loads(ws.recv(timeout=5))
                if m.get("id") == n:
                    return m.get("result") or {}
        yield call


def cdp_close(port: int) -> bool:
    """Ask a tile's browser to quit by itself (CDP Browser.close): its renderers then exit cleanly instead of dying with
    SIGILL when the browser process is signalled, and every such crash costs a systemd-coredump run."""
    from websockets.sync.client import connect
    import urllib.request
    try:
        ws_url = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1))["webSocketDebuggerUrl"]
        with connect(ws_url, open_timeout=1, close_timeout=1) as ws:
            ws.send(json.dumps({"id": 1, "method": "Browser.close"}))
        return True
    except Exception:  # noqa: BLE001 — not answering: the signals below do it
        return False


def close_tiles(tiles: list[tuple[subprocess.Popen | None, int]], cdp_base: int, grace: float = 4) -> None:
    """Browser.close each, wait up to `grace` s for all, then SIGTERM (3 s), then SIGKILL."""
    live = [(p, slot) for p, slot in tiles if p and p.poll() is None]
    for p, slot in live:
        cdp_close(cdp_base + slot)
    end = time.monotonic() + grace
    for p, _ in live:
        try:
            p.wait(timeout=max(0.05, end - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    for p, _ in live:
        if p.poll() is None:
            p.terminate()
    for p, _ in live:
        _end(p)


def no_core() -> None:
    """preexec_fn for the wall's children: no core dumps (a crashing Chromium would otherwise keep systemd-coredump
    compressing 15 MB dumps at full CPU)."""
    import resource
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def sink_inputs(env: dict) -> list[dict]:
    try:
        return json.loads(subprocess.run(["pactl", "-f", "json", "list", "sink-inputs"], capture_output=True, text=True,
                                         timeout=3, env=env).stdout or "[]")
    except Exception as e:  # noqa: BLE001
        log.debug("pactl: %s", e)
        return []


def unmute_all(env: dict | None = None) -> None:
    """Unmute every sink input; WirePlumber then stores mute=false for the app name, so new kiosk streams start audible."""
    env = env or dict(os.environ)
    for s in sink_inputs(env):
        if s.get("mute"):
            subprocess.run(["pactl", "set-sink-input-mute", str(s["index"]), "0"], capture_output=True, timeout=3, env=env)


class Driver:
    """Runs one wall's tiles. One worker thread: tiles windows (first 15 s, and again after a tile comes, goes or moves),
    keeps every tile's video fullscreen, keeps the mutes right.

    tiles[i] = (chromium or None if it failed to start, slot): the slot (0..3) is its profile dir + CDP port, and stays
    with the tile when it moves, so a live swap / add / remove / reorder relaunches at most that one tile."""

    def __init__(self, session: Session) -> None:
        self.s = session
        self.tiles: list[tuple[subprocess.Popen | None, int]] = []
        self.ov: subprocess.Popen | None = None
        self.layout = ""
        self.lbar = False
        self.sound = 0
        self._layout_until = 0.0
        self._stop = threading.Event()
        self.on_change = lambda: None   # tile died / relaunched → the owner bumps its rev

        self._stalls: dict[int, Stall] = {}   # slot → stall watchdog

    @property
    def procs(self) -> list[subprocess.Popen]:
        return [p for p, _ in self.tiles if p]

    def _launch(self, url: str, slot: int) -> subprocess.Popen | None:
        try:
            return subprocess.Popen(
                [*GPU_ENV, "chromium", f"--user-data-dir={Path(self.s.profiles) / f'chromium-wall-{self.s.prefix}{slot}'}",
                 "--profile-directory=Wall", "--no-first-run", *GPU_FLAGS, *self.s.extra_flags, *QUIET_FLAGS, "--noerrdialogs",
                 "--disable-session-crashed-bubble", "--autoplay-policy=no-user-gesture-required",
                 f"--remote-debugging-port={self.s.cdp_base + slot}", f"--app={url}"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=self.s.child_env(),
                preexec_fn=no_core)
        except OSError as e:
            log.warning("%s tile (slot %d) failed to start: %s", self.s.name, slot, e)
            return None

    def start(self, urls: list[str]) -> None:
        self.stop()
        self._stop = threading.Event()
        self.sound = 0
        self._stalls = {}
        self.tiles = [(self._launch(u, i), i) for i, u in enumerate(urls[:4])]
        self._layout_until = time.monotonic() + 15
        if self.procs:
            self._ensure_overlay()
        threading.Thread(target=self._run, args=(self._stop,), name=f"wall-{self.s.name}", daemon=True).start()

    def set_tile(self, i: int, url: str) -> None:
        if 0 <= i < len(self.tiles):
            p, slot = self.tiles[i]
            close_tiles([(p, slot)], self.s.cdp_base)
            self._stalls.pop(slot, None)
            tiles = list(self.tiles)
            tiles[i] = (self._launch(url, slot), slot)
            self.tiles = tiles
            self._layout_until = time.monotonic() + 15

    def add_tile(self, url: str) -> None:
        free = sorted(set(range(4)) - {s for _, s in self.tiles})
        if free and self.tiles:
            self.tiles = [*self.tiles, (self._launch(url, free[0]), free[0])]
            self._layout_until = time.monotonic() + 15

    def remove_tile(self, i: int) -> None:
        if 0 <= i < len(self.tiles) and len(self.tiles) > 1:
            close_tiles([self.tiles[i]], self.s.cdp_base)
            self.tiles = self.tiles[:i] + self.tiles[i + 1:]
            self.sound = min(self.sound - (self.sound > i), len(self.tiles) - 1)
            self._layout_until = time.monotonic() + 10

    def move_tile(self, i: int, j: int) -> None:
        if 0 <= i < len(self.tiles) and 0 <= j < len(self.tiles) and i != j:
            tiles = list(self.tiles)
            tiles.insert(j, tiles.pop(i))
            self.tiles = tiles
            self._layout_until = time.monotonic() + 5

    def overlay(self, layout: str, lbar: bool = False) -> None:
        """What the overlay shows ("" = hidden; the process reads the view itself and stays up while the wall runs — this
        only re-tiles, and restarts the process if it died). lbar = tile into the L-bar's corner."""
        self.layout = layout if layout in LAYOUTS and self.procs else ""
        lbar = lbar and bool(self.procs)
        if lbar != self.lbar:
            self._layout_until = time.monotonic() + 20
        self.lbar = lbar
        if self.procs:
            self._ensure_overlay()

    def _ensure_overlay(self) -> None:
        if self.ov is None or self.ov.poll() is not None:
            try:
                self.ov = subprocess.Popen(overlay_cmd(self.s.overlay_api), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL, env=overlay_env(self.s.child_env()))
            except OSError as e:
                log.warning("%s overlay failed to start: %s", self.s.name, e)

    def stop(self) -> None:
        had = bool(self.tiles)
        self._stop.set()
        close_tiles(self.tiles, self.s.cdp_base)
        self.tiles = []
        _end(self.ov)
        self.ov = None
        self.layout, self.lbar = "", False
        if had and self.s.unmute_on_stop:
            unmute_all(self.s.child_env())

    def focus(self, i: int) -> None:
        """Keys + sound to tile i (the sound follows on the worker's next round, within 2 s; this does it now)."""
        if 0 <= i < len(self.tiles):
            self.sound = i
            if self.tiles[i][0]:
                kwin(f"for (const w of workspace.windowList()) if (w.pid == {self.tiles[i][0].pid}) workspace.activeWindow = w;",
                     f"{self.s.name}-focus", self.s.child_env())
            self._mute([p.pid if p else -1 for p, _ in self.tiles])

    def pids(self) -> list[int]:
        return [p.pid if p else -1 for p, _ in self.tiles]

    # ---- worker ----
    def _run(self, stop: threading.Event) -> None:
        while not stop.wait(2):
            tiles = self.tiles
            pids = [p.pid if p else -1 for p, _ in tiles]
            if time.monotonic() < self._layout_until:
                kwin(tile_js(pids, self.lbar, self.sound), f"{self.s.name}-tile", self.s.child_env())
            for _, slot in tiles:
                self._tend(slot)
            self._mute(pids)

    def _tend(self, slot: int) -> None:
        """One round for one tile over CDP: keep its video fullscreen, and unstick it when it stalls."""
        port = self.s.cdp_base + slot
        try:
            with cdp_page(port) as call:
                call("Runtime.evaluate", {"expression": FULLSCREEN_JS, "userGesture": True, "awaitPromise": True})
                probe = call("Runtime.evaluate", {"expression": PROBE_JS, "returnByValue": True}).get("result", {}).get("value")
                act = stall_action(self._stalls.setdefault(slot, Stall()), probe, time.monotonic())
                if act and act[0] == "seek":
                    log.info("%s tile slot %d stalled at %.1f s: seeking to %.1f", self.s.name, slot, probe["t"], act[1])
                    call("Runtime.evaluate", {"expression": SEEK_JS % act[1]})
                elif act:
                    log.info("%s tile slot %d stalled at %.1f s for 30 s: reloading", self.s.name, slot, probe["t"])
                    call("Page.reload", {"ignoreCache": True})
        except Exception as e:  # noqa: BLE001 — a tile still loading, or an ad holding the request; try again next round
            log.debug("cdp %d: %s", port, e)

    def _mute(self, pids: list[int]) -> None:
        env = self.s.child_env()
        for s in sink_inputs(env):
            i = tile_of(int(s.get("properties", {}).get("application.process.id") or 0), pids, _ppid)
            if i is not None:
                want = i != self.sound
                if bool(s.get("mute")) != want:
                    subprocess.run(["pactl", "set-sink-input-mute", str(s["index"]), "1" if want else "0"], capture_output=True, timeout=3, env=env)
