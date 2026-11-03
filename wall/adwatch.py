"""Election night ad-skip, the listening half (docs/wall-service.md "Ad-skip"): a separate process on the homelab that
follows a pool of national news streams, the Spectrum lineup (wall/spectrum.py) and the walls' streams, transcribes them, labels each one
coverage / ad / break / offtopic every CLASSIFY_S, and posts the labels to the wall service (POST /adwatch), whose
wall/onair.py moves the sound and swaps tiles.

    uv run python -m wall.adwatch                 (user unit deploy/sharplab-adwatch.service)
    uv run python -m wall.adwatch --once <url>    (one 15 s chunk: resolve, transcribe, cue counts — a sanity check)

Per stream: `yt-dlp -g` gives the HLS URL (refreshed every HLS_TTL or on failure). Text comes from (a) CEA-608/708
captions in the video track when ffprobe sees them (ffmpeg's lavfi `movie=…[out0+subcc]`; YouTube's live HLS normally
has none), else (b) speech-to-text over CHUNK_S audio chunks (`ffmpeg … -f s16le`): faster-whisper on the GPU (one small
model shared by every stream; `uv sync --extra adwatch`), or the homelab's CPU whisper service over HTTP
(WALL_ADWATCH_WHISPER_URL; serialized, enough for one or two streams). Labels: every window goes to sharplab-web's
POST /api/v1/wall/election/classify (TypeSafe Jev, Claude Haiku when Jev is unsure; WALL_WEB_URL + WALL_WEB_KEY) — no
keyword ever decides a label (no false "ad" on coverage); no answer or silence = "unknown", which the policy ignores.
Nothing runs while no wall is up.

Env: WALL_ADWATCH_POOL (comma-separated URLs), WALL_ADWATCH_MAX (streams at once, 16), WALL_ADWATCH_STT (gpu | http),
WALL_ADWATCH_MODEL (small.en), WALL_ADWATCH_WHISPER_URL, WALL_URL (http://127.0.0.1:7795), WALL_WEB_URL, WALL_WEB_KEY.
"""
from __future__ import annotations

import io
import json
import logging
import os
import queue
import re
import struct
import subprocess
import sys
import threading
import time
import urllib.request
import wave
from collections import deque

from wall import streams as yt

log = logging.getLogger("wall.adwatch")
POOL = ["https://www.youtube.com/@NBCNews/live", "https://www.youtube.com/@PBSNewsHour/live",
        "https://www.youtube.com/@ABCNews/live", "https://www.youtube.com/@CBSNews/live",
        "https://www.youtube.com/@LiveNOWFOX/live"]
# streams at once: the 14-stream Spectrum lineup (wall/spectrum.py) + a couple of others on the walls. small.en int8 on the
# GPU transcribes a 15 s chunk in ~0.3 s, so 16 streams keep it busy ~1/3 of the time (~1 GB VRAM); each stream is an
# ffmpeg + ~130 kb/s of audio and one classify call per 20 s (Jev, Haiku only when Jev is unsure)
MAX = 16
RATE = 16000
CHUNK_S, WINDOW_S, CLASSIFY_S, POST_S, WATCH_S = 15, 45, 20, 5, 15
HLS_TTL, POOL_RECHECK_S, MAX_LAG_S = 20 * 60, 10 * 60, 40
RECENT_S = 20   # "the last ~20 s": what the web asks Jev "is a break coming?" about (wall/onair.py preempts on it)
QUIET_RMS = 120   # s16 RMS under this = silence (a bumper's dead air, a cut to black)

AD_CUES = [r"\bcall now\b", r"\bask your doctor\b", r"\bside effects?\b", r"\bterms (and conditions )?apply\b",
           r"\bvisit \w+\.(com|org)\b", r"\bdot com\b", r"\bapr\b", r"%\s*apr", r"\bbrought to you by\b",
           r"\bsponsored by\b", r"\bfree (quote|shipping|trial)\b", r"\blimited time\b", r"\bact now\b", r"\bpaid for by\b",
           r"\bi approve(d)? this message\b", r"\bnot available in all states\b", r"\bindividual results may vary\b",
           r"\bsave up to\b", r"\border (now|today)\b", r"\b1-?800\b", r"\bmoney[- ]back\b", r"\bprescription\b",
           r"\binsurance\b", r"\bfinancing\b", r"\bper month\b", r"\bdownload the app\b", r"\bmember fdic\b"]
BREAK_CUES = [r"\bwe'?ll be right back\b", r"\bright back\b", r"\bafter (the|this) break\b", r"\bdon'?t go anywhere\b",
              r"\bstay with us\b", r"\bwhen we come back\b", r"\bcoming up (next|after)\b", r"\bafter these messages\b"]
COVERAGE_CUES = [r"\belection\b", r"\bpolls?\b", r"\bvot(e|es|ers|ing)\b", r"\bballots?\b", r"\brace\b", r"\bsenate\b",
                 r"\bgovernor\b", r"\bdistrict\b", r"\bprojects?\b|\bprojected\b", r"\bcandidates?\b", r"\bcongress\b",
                 r"\bhouse\b", r"\brepublicans?\b", r"\bdemocrats?\b", r"\bgop\b", r"\bprecincts?\b", r"\bturnout\b",
                 r"\bexit polls?\b", r"\bdecision desk\b", r"\bmidterms?\b", r"\bcampaign\b", r"\bcounty\b",
                 r"\bi'?m \w+ \w+ (in|at|here)\b", r"\bjoining us\b", r"\bour correspondent\b", r"\bwelcome back\b",
                 r"\blive (in|from)\b", r"\breporting\b", r"\bpresident\b", r"\badministration\b", r"\bofficials?\b",
                 r"\baccording to\b", r"\bbreaking news\b"]
_AD = [re.compile(c, re.I) for c in AD_CUES]
_BREAK = [re.compile(c, re.I) for c in BREAK_CUES]
_COV = [re.compile(c, re.I) for c in COVERAGE_CUES]


def cues(text: str) -> tuple[int, int, int]:
    """(ad, break, coverage): how many distinct cues of each kind the text has."""
    return sum(bool(r.search(text)) for r in _AD), sum(bool(r.search(text)) for r in _BREAK), \
        sum(bool(r.search(text)) for r in _COV)


def recent_text(text: deque) -> str:
    """The words of the last ~RECENT_S of a stream's window."""
    if not text:
        return ""
    last = text[-1][0]
    return " ".join(t for at, t, _ in text if t and last - at <= RECENT_S)


# ---- I/O helpers (separate so the logic above tests without network) --------------------------------------------------
def hls_url(url: str, fmt: str = "bestaudio/worst") -> str:
    out = subprocess.run(["yt-dlp", "-q", "--no-warnings", "-g", "-f", fmt, url], capture_output=True, text=True, timeout=60)
    lines = [x for x in out.stdout.splitlines() if x.startswith("http")]
    if not lines:
        raise RuntimeError(f"yt-dlp: {out.stderr.strip()[:160] or 'no URL'}")
    return lines[0]


def has_captions(video_hls: str) -> bool:
    """ffprobe marks a video stream with embedded CEA-608/708 as "Closed Captions"."""
    try:
        p = subprocess.run(["ffprobe", "-hide_banner", "-i", video_hls], capture_output=True, text=True, timeout=25)
    except subprocess.TimeoutExpired:
        return False
    return "Closed Captions" in p.stderr


def movie_arg(url: str) -> str:
    """A URL as lavfi movie's filename: option-level escaping (\\ and :) inside filtergraph single quotes."""
    s = url.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    return "'" + s.replace("'", "'\\''") + "'"


def rms(pcm: bytes) -> float:
    n = len(pcm) // 2
    if not n:
        return 0.0
    step = max(1, n // 4000)   # a sample of the chunk is plenty
    vals = struct.unpack(f"<{n}h", pcm[:n * 2])[::step]
    return (sum(v * v for v in vals) / len(vals)) ** 0.5


def wav(pcm: bytes) -> bytes:
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return b.getvalue()


def post_json(url: str, body: dict, headers: dict | None = None, timeout: float = 10) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def get_json(url: str, timeout: float = 5) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


# ---- speech-to-text ------------------------------------------------------------------------------------------------------

def cuda12_preload() -> None:
    """ctranslate2 wants CUDA 12's cuBLAS / cuDNN; the box runs CUDA 13. The `adwatch` extra ships them as NVIDIA's pip
    wheels: load them globally first so ctranslate2's dlopen finds them (no LD_LIBRARY_PATH in the unit)."""
    import ctypes
    import glob
    import importlib.util
    import os
    spec = importlib.util.find_spec("nvidia")   # a namespace package: its wheels' lib dirs under each search location
    for root in (spec.submodule_search_locations if spec else []):
        for name in ("cublas", "cudnn"):
            for f in sorted(glob.glob(os.path.join(root, name, "lib", "lib*.so*"))):
                try:
                    ctypes.CDLL(f, mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    pass

NO_SPEECH = ("[No speech at all for the last ~45 seconds: only music, a jingle or ambient sound is playing — on a live news "
             "channel this is usually a break slate such as \"back in 3 minutes\".]")

class GpuSTT:
    """faster-whisper on the GPU: one model for every stream, chunks one at a time."""

    def __init__(self, model: str) -> None:
        cuda12_preload()
        from faster_whisper import WhisperModel   # the `adwatch` extra
        self.model = WhisperModel(model, device="cuda", compute_type="int8_float16")
        self(b"\0\0" * RATE)   # a missing CUDA / cuDNN library shows up on the first transcribe: here, not mid-night

    def __call__(self, pcm: bytes) -> str:
        import numpy as np
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segs, _ = self.model.transcribe(audio, language="en", beam_size=1, vad_filter=True,
                                        condition_on_previous_text=False, without_timestamps=True)
        return " ".join(s.text.strip() for s in segs)


class HttpSTT:
    """The homelab's CPU whisper service (POST /transcribe, a WAV body → {"text"}): serialized, ~realtime."""

    def __init__(self, url: str) -> None:
        self.url = url

    def __call__(self, pcm: bytes) -> str:
        req = urllib.request.Request(self.url, data=wav(pcm), method="POST", headers={"Content-Type": "audio/wav"})
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.load(r).get("text") or ""


def make_stt(env=os.environ):
    kind = env.get("WALL_ADWATCH_STT", "gpu")
    if kind == "gpu":
        try:
            return GpuSTT(env.get("WALL_ADWATCH_MODEL", "small.en"))
        except Exception as e:  # noqa: BLE001 — no extra installed / no CUDA: the CPU service instead
            log.warning("GPU whisper unavailable (%s: %s); using the CPU service", type(e).__name__, e)
    return HttpSTT(env.get("WALL_ADWATCH_WHISPER_URL", "http://100.67.232.59:9000/transcribe"))


# ---- one stream ------------------------------------------------------------------------------------------------------------
class Stream:
    def __init__(self, url: str, chunks: queue.Queue) -> None:
        self.url, self.chunks = url, chunks
        self.text: deque[tuple[float, str, bool]] = deque()   # (t, text, quiet)
        self.status = {"label": "unknown", "confidence": 0.0, "since": time.time(), "text_tail": ""}
        self.last_classify = 0.0
        self.stop = threading.Event()
        self.proc: subprocess.Popen | None = None
        self.mode = ""
        self.thread = threading.Thread(target=self.run, name=f"adwatch-{url[-30:]}", daemon=True)
        self.thread.start()

    def add(self, text: str, quiet: bool, at: float | None = None) -> None:
        at = time.time() if at is None else at
        self.text.append((at, text.strip(), quiet))
        while self.text and at - self.text[0][0] > WINDOW_S:
            self.text.popleft()

    def window(self) -> tuple[str, float]:
        if not self.text:
            return "", 0.0
        return " ".join(t for _, t, _ in self.text if t), sum(q for *_, q in self.text) / len(self.text)

    def close(self) -> None:
        self.stop.set()
        p = self.proc
        if p and p.poll() is None:
            p.terminate()

    def run(self) -> None:
        backoff = 5
        while not self.stop.is_set():
            t0 = time.time()
            try:
                self._session()
                backoff = 5
            except Exception as e:  # noqa: BLE001 — a dead stream / expired URL: resolve again
                log.info("%s: %s", self.url, e)
            if self.stop.is_set():
                return
            if time.time() - t0 < 30:
                self.stop.wait(backoff)
                backoff = min(backoff * 2, 120)

    def _session(self) -> None:
        """One HLS URL's life: captions when the video carries them, else audio chunks for whisper; ends at HLS_TTL."""
        until = time.time() + HLS_TTL
        if self.mode != "audio":   # captions are checked once per stream; YouTube live HLS normally has none
            try:
                v = hls_url(self.url, "worstvideo/worst")
                if has_captions(v):
                    self.mode = "cc"
                    return self._captions(v, until)
            except Exception as e:  # noqa: BLE001
                log.info("%s: no caption probe (%s)", self.url, e)
            self.mode = "audio"
        self._audio(hls_url(self.url), until)

    def _spawn(self, cmd: list[str]) -> subprocess.Popen:
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        return self.proc

    def _audio(self, hls: str, until: float) -> None:
        p = self._spawn(["ffmpeg", "-hide_banner", "-loglevel", "error", "-reconnect", "1", "-reconnect_streamed", "1",
                         "-i", hls, "-vn", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"])
        size = CHUNK_S * RATE * 2
        try:
            while not self.stop.is_set() and time.time() < until:
                pcm = p.stdout.read(size)
                if len(pcm) < size // 3:
                    raise RuntimeError("audio ended")
                try:
                    self.chunks.put_nowait((self, time.time(), pcm))
                except queue.Full:
                    pass   # the transcriber is behind: this chunk is skipped (the window keeps the rest)
        finally:
            p.terminate()

    def _captions(self, hls: str, until: float) -> None:
        p = self._spawn(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"movie={movie_arg(hls)}[out0+subcc]",
                         "-map", "0:s", "-f", "srt", "-flush_packets", "1", "-"])
        try:
            got = time.time()
            for raw in p.stdout:
                if self.stop.is_set() or time.time() > until:
                    break
                line = raw.decode(errors="replace").strip()
                if line and not line.isdigit() and "-->" not in line:
                    self.add(re.sub(r"<[^>]+>", "", line), False)
                    got = time.time()
                elif time.time() - got > 90:   # captions dried up: whisper from now on
                    self.mode = "audio"
                    break
        finally:
            p.terminate()


# ---- the loop ----------------------------------------------------------------------------------------------------------------
class Watcher:
    def __init__(self, env=os.environ, stt=None, classify=None, post=None, walls=None, live=None, clock=time.time) -> None:
        self.wall = env.get("WALL_URL", "http://127.0.0.1:7795").rstrip("/")
        self.web, self.key = env.get("WALL_WEB_URL", "").rstrip("/"), env.get("WALL_WEB_KEY", "")
        pool = env.get("WALL_ADWATCH_POOL", "")
        self.pool = [u.strip() for u in pool.split(",") if u.strip()] if pool else list(POOL)
        self.max = int(env.get("WALL_ADWATCH_MAX", str(MAX)))
        self.stt = stt
        self.env = env
        self.classify = classify or self._classify_web
        self.post = post or (lambda body: post_json(self.wall + "/adwatch", body))
        self.walls = walls or (lambda: get_json(self.wall + "/i"))
        self.live = live or (lambda u: yt.check(u).get("live"))
        self.clock = clock
        self.chunks: queue.Queue = queue.Queue(maxsize=self.max * 2)
        self.streams: dict[str, Stream] = {}
        self.pool_live: dict[str, tuple[float, bool]] = {}

    def _classify_web(self, url: str, text: str) -> tuple[str, float, float | None, float | None]:
        """(label, confidence, soon, important): soon = P(a break within the next minute) from the last ~20 s, important =
        P(something viewers shouldn't miss is on now) — both from the same call, None if unanswered."""
        if not (self.web and self.key):
            return "unknown", 0.0, None, None
        s = self.streams.get(url)
        try:
            r = post_json(self.web + "/api/v1/wall/election/classify",
                          {"stream": url, "text": text, "recent": recent_text(s.text) if s else ""},
                          {"X-Api-Key": self.key}, timeout=12)
            num = lambda v: float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None  # noqa: E731
            return r.get("label", "unknown"), float(r.get("confidence") or 0), num(r.get("soon")), num(r.get("important"))
        except Exception as e:  # noqa: BLE001
            log.info("classify: %s", e)
            return "unknown", 0.0, None, None

    def wanted(self) -> list[str]:
        """Streams on the walls first (and the originals the policy swapped away, and the favorites), then the live
        Spectrum streams, then the live pool."""
        try:
            states = self.walls()
        except Exception as e:  # noqa: BLE001
            log.info("wall service: %s", e)
            return []
        if not any(s.get("running") for s in states.values()):
            return []   # no wall up: listen to nothing
        out: list[str] = []
        spec: list[str] = []   # the Spectrum lineup (wall/spectrum.py): its labels gate who may go on a tile
        for s in states.values():
            if not s.get("running"):
                continue
            out += [u for u in s.get("urls") or [] if yt.is_stream(u)]
            out += list(((s.get("adwatch") or {}).get("orig") or {}).values())
            out += s.get("favorites") or []
            for u, st in (s.get("spectrum") or {}).items():
                if (st or {}).get("live") is True:   # the wall service checks it every minute
                    out.append(u)
                elif (st or {}).get("live") is None:   # not checked there yet: the pool's own check below
                    spec.append(u)
        now = self.clock()
        for u in spec + self.pool:
            at, ok = self.pool_live.get(u, (-1e9, False))
            if now - at > POOL_RECHECK_S:
                try:
                    ok = bool(self.live(u))
                except Exception:  # noqa: BLE001
                    ok = False
                self.pool_live[u] = (now, ok)
                log.info("pool %s: %s", u, "live" if ok else "not live")
            if ok:
                out.append(u)
        seen: list[str] = []
        for u in out:
            if isinstance(u, str) and u.startswith("https://") and u not in seen:
                seen.append(u)
        return seen[:self.max]

    def sync_streams(self, want: list[str]) -> None:
        for u in list(self.streams):
            if u not in want:
                self.streams.pop(u).close()
        for u in want:
            if u not in self.streams:
                self.streams[u] = Stream(u, self.chunks)

    def label(self, s: Stream, now: float) -> None:
        text, quiet = s.window()
        # every label is the model's (Jev, Haiku when unsure): no keyword call can mislabel coverage as an ad; no answer
        # or no words (silence) = "unknown", which the policy never acts on
        if not text.strip() and len(s.text) >= 2 and quiet < 0.9:
            # sound but no words for the whole window (a "back in 3 minutes" slate over music, a jingle): say so and let
            # the model judge — still never a rule of ours
            text = NO_SPEECH
        got = self.classify(s.url, text[-2000:]) if text.strip() else ("unknown", 0.0)
        lab, conf = got[0], got[1]
        soon = got[2] if len(got) > 2 else None   # passed through: the wall's policy may move the sound before the break
        imp = got[3] if len(got) > 3 else None    # and the stream director (wall/streamdir.py) may jump to a can't-miss moment
        if lab not in ("coverage", "ad", "break", "offtopic"):
            lab, conf, soon, imp = "unknown", 0.0, None, None
        if lab != s.status["label"]:
            s.status["since"] = now
        s.status.update(label=lab, confidence=round(conf, 2), text_tail=text[-160:], soon=soon, important=imp,
                        recent=recent_text(s.text)[-1200:])
        s.last_classify = now

    def transcribe_loop(self) -> None:
        while True:
            s, at, pcm = self.chunks.get()
            if s.stop.is_set() or time.time() - at > MAX_LAG_S:
                continue
            quiet = rms(pcm) < QUIET_RMS
            try:
                text = "" if quiet else self.stt(pcm)
            except Exception as e:  # noqa: BLE001
                log.info("stt: %s", e)
                continue
            s.add(text, quiet, at)

    def cycle(self, now: float) -> dict:
        for s in list(self.streams.values()):
            if s.text and now - s.last_classify >= CLASSIFY_S:
                self.label(s, now)
        body = {"streams": {u: dict(s.status) for u, s in self.streams.items() if s.last_classify}}
        if body["streams"]:
            try:
                self.post(body)
            except Exception as e:  # noqa: BLE001
                log.info("post: %s", e)
        return body

    def run(self) -> None:
        self.stt = self.stt or make_stt(self.env)
        threading.Thread(target=self.transcribe_loop, name="adwatch-stt", daemon=True).start()
        last_watch = 0.0
        while True:
            now = self.clock()
            if now - last_watch >= WATCH_S:
                last_watch = now
                self.sync_streams(self.wanted())
            self.cycle(now)
            time.sleep(POST_S)


def once(url: str) -> None:
    """A sanity check from any box: resolve, 15 s of audio, transcribe, cue counts (informational only)."""
    t = time.time()
    hls = hls_url(url)
    print(f"hls in {time.time() - t:.1f}s")
    pcm = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", hls, "-t", str(CHUNK_S), "-vn", "-ac", "1",
                          "-ar", str(RATE), "-f", "s16le", "-"], capture_output=True, timeout=90).stdout
    print(f"audio {len(pcm) / RATE / 2:.1f}s rms {rms(pcm):.0f}")
    t = time.time()
    text = make_stt()(pcm)
    print(f"stt in {time.time() - t:.1f}s: {text}")
    print("cues (ad, break, coverage):", cues(text))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if len(sys.argv) == 3 and sys.argv[1] == "--once":
        return once(sys.argv[2])
    w = Watcher()
    try:
        w.run()
    finally:
        for s in w.streams.values():
            s.close()


if __name__ == "__main__":
    main()
