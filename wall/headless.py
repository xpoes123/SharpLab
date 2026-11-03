"""The headless wall: a nested `kwin_wayland --virtual` 1920×1080 with its own D-Bus, captured to HLS.

    dbus-run-session -- kwin_wayland --virtual … --exit-with-session=hold.sh      (process group = ours to end)
    pactl load-module module-null-sink sink_name=wall_<name>                     (the tiles' audio, never the TV's speakers)
    qml6 Cast.qml -- Virtual-0                                                    (KWin makes a PipeWire node of the output)
    gst-launch-1.0 pipewiresrc path=<node> → 30 fps → NV12 → nvh264enc (GOP 30)  ┐
                   pipewiresrc target-object=wall_<name> (monitor) → avenc_aac   ┴→ mpegtsmux → stdout
    ffmpeg -i pipe: -c copy -f hls -hls_time 1 (+ PROGRAM-DATE-TIME)              → <state>/hls/<name>/index.m3u8

ffmpeg has no PipeWire input and gst-plugins-good (mp4mux, pulsesrc, hlssink with PDT) isn't installed, hence the split.
Everything here is already on the box (verified 2026-11-02); nothing new to install.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from wall.engine import QML_DIR, _ppid, no_core

log = logging.getLogger("wall.headless")
HOLD = Path(__file__).parent / "hold.sh"
BUS_CONF = Path(__file__).parent / "nested-bus.conf"
# the nested session's clients: no portal, no keyring (the private bus activates nothing anyway, see nested-bus.conf)
QUIET_ENV = {"GTK_USE_PORTAL": "0", "QT_NO_XDG_DESKTOP_PORTAL": "1", "NO_AT_BRIDGE": "1", "KDE_FORK_SLAVES": "1"}
OUTPUT = "Virtual-0"


def free_vram_mb() -> int | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.total,memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5).stdout
        total, used = (int(x) for x in out.splitlines()[0].split(","))
        return total - used
    except Exception:  # noqa: BLE001 — no nvidia-smi: no budget check
        return None


# a 3 s queue in front of the mux on each branch: one branch's hiccup never starves the other (mpegtsmux waits for both)
QUEUE = ["queue", "max-size-buffers=0", "max-size-bytes=0", "max-size-time=3000000000"]


def gst_cmd(node: int, sink: str, kbps: int = 4500, fps: int = 30, width: int = 1920, height: int = 1080) -> list[str]:
    """One clock for picture and sound: both pipewiresrc's into one mpegtsmux on stdout.

    Picture: system-memory frames → videorate (30 fps) → cudaupload → cudaconvertscale (to width×height NV12 on the GPU,
    so a 1440p TV costs no CPU scaling or colour conversion) → nvh264enc.
    Sound: asked from PipeWire as 48 kHz stereo float (no resampling on our side), audiorate keeps the stream gap-free
    (gaps / overlaps from capture jitter are what clicked), AAC 160 kb/s."""
    return ["gst-launch-1.0", "-q", "-e",
            "pipewiresrc", f"path={node}", "do-timestamp=true", "keepalive-time=100", "!", "video/x-raw", "!",
            "videorate", "!", f"video/x-raw,framerate={fps}/1", "!", "cudaupload", "!", "cudaconvertscale", "!",
            f"video/x-raw(memory:CUDAMemory),format=NV12,width={width},height={height},pixel-aspect-ratio=1/1", "!",
            "nvh264enc", f"gop-size={fps}", f"bitrate={kbps}", "rc-mode=cbr", "preset=low-latency-hq", "zerolatency=true", "!",
            "h264parse", "config-interval=-1", "!", *QUEUE, "!", "mux.",
            "pipewiresrc", *([f"target-object={sink}"] if sink else []), "stream-properties=props,stream.capture.sink=true",
            "do-timestamp=true", "!", "audio/x-raw,format=F32LE,rate=48000,channels=2", "!",
            "audiorate", "tolerance=40000000", "skip-to-first=true", "!", "audioconvert", "!",
            "avenc_aac", "bitrate=160000", "!", *QUEUE, "!", "mux.",
            "mpegtsmux", "name=mux", "!", "fdsink", "fd=1"]


def ffmpeg_cmd(out_dir: Path, window: int = 30) -> list[str]:
    return ["ffmpeg", "-v", "warning", "-fflags", "+genpts", "-i", "pipe:0", "-c", "copy", "-f", "hls", "-hls_time", "1",
            "-hls_list_size", str(window), "-hls_flags", "delete_segments+program_date_time+independent_segments+temp_file",
            "-hls_segment_filename", str(out_dir / "seg%06d.ts"), str(out_dir / "index.m3u8")]


def pw_dump() -> list:
    try:
        return json.loads(subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=5).stdout)
    except Exception:  # noqa: BLE001
        return []


def cast_nodes(output: str, objs: list | None = None) -> set[int]:
    """Every PipeWire node KWin made for a screencast of `output`."""
    objs = pw_dump() if objs is None else objs
    return {o["id"] for o in objs if o.get("type", "").endswith("Node")
            and ((o.get("info") or {}).get("props") or {}).get("media.name") == f"kwin-screencast-{output}"}


def find_cast_node(root_pid: int, dump: str | None = None, ppid=_ppid) -> int | None:
    """The PipeWire node KWin made for OUTPUT, owned by a kwin_wayland under root_pid (our dbus-run-session)."""
    try:
        objs = json.loads(dump if dump is not None else subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=5).stdout)
    except Exception:  # noqa: BLE001
        return None
    clients = {o["id"]: ((o.get("info") or {}).get("props") or {}) for o in objs if o.get("type", "").endswith("Client")}
    for o in objs:
        p = (o.get("info") or {}).get("props") or {}
        if o.get("type", "").endswith("Node") and p.get("media.name") == f"kwin-screencast-{OUTPUT}":
            pid = int(clients.get(p.get("client.id"), {}).get("application.process.id") or 0)
            for _ in range(16):
                if pid == root_pid:
                    return o["id"]
                if pid <= 1:
                    break
                pid = ppid(pid)
    return None


def _spawn(cmd: list[str], log_path: Path, both: bool = False, **kw) -> subprocess.Popen:
    """Popen with stderr (both: stdout too) into log_path. Our copy of the log fd closes once the child has its own, so
    every start/stop doesn't leak four fds into the long-lived service."""
    with open(log_path, "wb") as f:
        if both:
            kw.update(stdout=f, stderr=subprocess.STDOUT)
        else:
            kw["stderr"] = f
        return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, **kw)


class Headless:
    """start() → the env the wall's children need (WAYLAND_DISPLAY, the private D-Bus, PULSE_SINK); begin_capture() once
    the tiles are up; stop() ends every process it started, by pid / its own process group, and unloads the sink."""

    def __init__(self, name: str, state_dir: Path, width: int = 1920, height: int = 1080) -> None:
        self.name, self.width, self.height = name, width, height
        self.dir = state_dir / "headless" / name
        self.hls = state_dir / "hls" / name
        self.sink = f"wall_{name}"
        self.kwin: subprocess.Popen | None = None
        self.cast: subprocess.Popen | None = None
        self.gst: subprocess.Popen | None = None
        self.ff: subprocess.Popen | None = None
        self.module = ""
        self.env: dict = {}
        self.error = ""
        self.mode = ""   # "tiles" (our nested kwin) | "screen" (the TV's real output) | "" (off)

    @property
    def up(self) -> bool:
        return bool(self.ff and self.ff.poll() is None and self.gst and self.gst.poll() is None)

    def _base_env(self) -> dict:
        env = {k: v for k, v in os.environ.items() if k not in ("WAYLAND_DISPLAY", "DISPLAY", "DBUS_SESSION_BUS_ADDRESS")}
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        return env

    def start(self, timeout: float = 20) -> dict:
        self.stop()
        self.mode = "tiles"
        self.dir.mkdir(parents=True, exist_ok=True)
        envf = self.dir / "env"
        envf.unlink(missing_ok=True)
        base = self._base_env()
        r = subprocess.run(["pactl", "load-module", "module-null-sink", f"sink_name={self.sink}",
                            f"sink_properties=device.description=Wall-{self.name},node.passive=true,priority.session=0,priority.driver=0"],
                           capture_output=True, text=True, timeout=5, env=base)
        self.module = r.stdout.strip()
        socket = f"wall-{self.name}"
        self.kwin = _spawn(
            ["dbus-run-session", f"--config-file={BUS_CONF}", "--", "kwin_wayland", "--virtual", "--width", str(self.width), "--height", str(self.height),
             "--socket", socket, "--no-lockscreen", "--no-global-shortcuts", f"--exit-with-session={HOLD}"],
            self.dir / "kwin.log", both=True,
            env={**base, **QUIET_ENV, "KWIN_WAYLAND_NO_PERMISSION_CHECKS": "1", "WALL_ENV_OUT": str(envf)}, start_new_session=True,
            preexec_fn=no_core)
        end = time.monotonic() + timeout
        while not envf.exists():
            if time.monotonic() > end or self.kwin.poll() is not None:
                self.error = "nested kwin did not come up (see kwin.log)"
                self.stop()
                raise RuntimeError(self.error)
            time.sleep(0.2)
        got = dict(line.split("=", 1) for line in envf.read_text().splitlines() if "=" in line)
        self.env = {**got, **QUIET_ENV, "XDG_RUNTIME_DIR": base["XDG_RUNTIME_DIR"], "PULSE_SINK": self.sink, "DISPLAY": ""}
        return self.env

    def begin_capture(self, timeout: float = 10) -> None:
        if shutil.which("gst-launch-1.0") is None:
            raise RuntimeError("gst-launch-1.0 missing")
        env = {**self._base_env(), **self.env, "QT_QPA_PLATFORM": "wayland"}
        self.cast = _spawn(["qml6", str(QML_DIR / "Cast.qml"), "--", OUTPUT], self.dir / "cast.log", env=env, stdout=subprocess.DEVNULL,
                           preexec_fn=no_core)
        node, end = None, time.monotonic() + timeout
        while node is None and time.monotonic() < end:
            time.sleep(0.3)
            node = find_cast_node(self.kwin.pid) if self.kwin else None
        if node is None:
            self.error = "no screencast node from the nested kwin"
            raise RuntimeError(self.error)
        self._encode(node, self.sink)

    def _encode(self, node: int, sink: str) -> None:
        if self.hls.exists():
            shutil.rmtree(self.hls)
        self.hls.mkdir(parents=True)
        self.gst = _spawn(gst_cmd(node, sink), self.dir / "gst.log", env=self._base_env(), stdout=subprocess.PIPE,
                          preexec_fn=no_core)
        with open(self.dir / "ffmpeg.log", "wb") as f:
            self.ff = subprocess.Popen(ffmpeg_cmd(self.hls), stdin=self.gst.stdout, stdout=subprocess.DEVNULL, stderr=f,
                                       preexec_fn=no_core)
        self.gst.stdout.close()   # ffmpeg owns the read end now
        self.error = ""

    def capture_screen(self, output: str, sink: str = "", timeout: float = 10) -> None:
        """Mirror mode: stream the TV's real output (tiles + overlay exactly as shown) and the sound of `sink` (the TV's
        default sink when empty) — no tiles of our own. A KWin screencast of the user's session (zkde_screencast via
        Cast.qml, no portal, unattended), scaled + encoded on the GPU."""
        self.stop()
        self.mode = "screen"
        self.dir.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, **QUIET_ENV, "QT_QPA_PLATFORM": "wayland"}   # the service's own env = the TV's session
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        before = cast_nodes(output)
        self.cast = _spawn(["qml6", str(QML_DIR / "Cast.qml"), "--", output], self.dir / "cast.log", env=env,
                           stdout=subprocess.DEVNULL, preexec_fn=no_core)
        new, end = set(), time.monotonic() + timeout
        while not new and time.monotonic() < end and self.cast.poll() is None:
            time.sleep(0.3)
            new = cast_nodes(output) - before
        if not new:
            self.error = f"no screencast of {output} (see cast.log)"
            self.stop()
            raise RuntimeError(self.error)
        self._encode(max(new), sink)

    def stop(self) -> None:
        for p in (self.gst, self.ff, self.cast):
            _end(p)
        self.gst = self.ff = self.cast = None
        self.mode = ""
        if self.kwin is not None:
            try:
                os.killpg(self.kwin.pid, signal.SIGTERM)   # the group we created: dbus-run-session, its dbus-daemon, kwin, hold
            except ProcessLookupError:
                pass
            try:
                self.kwin.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.kwin.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            self.kwin = None
        if self.module.isdigit():
            subprocess.run(["pactl", "unload-module", self.module], capture_output=True, timeout=5, env=self._base_env())
        self.module = ""
        self.env = {}


def _end(p: subprocess.Popen | None) -> None:
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
