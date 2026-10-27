"""Video shrinker: upload a phone video, get an H.264 mp4 under 5 MB back (download link + optional post to a
Discord channel by the SharpLab bot). Encoding is discvid's (~/.local/bin/discvid): two-pass x264, HDR tone-mapped
to SDR, resolution/fps picked from the bitrate budget. Members only (Discord sign-in)."""
import asyncio
import json
import os
import secrets
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from web import auth

router = APIRouter(prefix="/api/v1/vid")

MEDIA_DIR = os.environ.get("VID_MEDIA_DIR", "/opt/share-media/v")   # Caddy serves /opt/share-media at /media
MEDIA_URL = os.environ.get("VID_MEDIA_URL", "https://sharplab.djiang.xyz/media/v")
TARGET_MB = 5.0
MAX_UPLOAD = 2_000_000_000
KEEP_DAYS = 7
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_API = "https://discord.com/api/v10"

# (short side, fps cap, min video kbps for this tier), best first; and a per-tier spend ceiling
LADDER = [(720, 60, 3500), (720, 30, 1100), (480, 30, 700), (360, 30, 350), (360, 24, 0)]
CEILING = {720: 6000, 480: 3000, 360: 1500}

_pool = ThreadPoolExecutor(max_workers=1)   # ponytail: one encode at a time on the 2-core VPS; a queue shows position
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _err(msg: str, code: int = 400) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=code)


# ── encoding (from discvid) ──────────────────────────────────────────────────

def probe(path: str) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                         capture_output=True, text=True, timeout=60)
    if out.returncode:
        raise ValueError("that file isn't a video ffmpeg can read")
    info = json.loads(out.stdout)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    if v is None:
        raise ValueError("no video stream in that file")
    rot = next((int(sd["rotation"]) for sd in v.get("side_data_list", []) if "rotation" in sd), 0) or int(v.get("tags", {}).get("rotate", 0))
    w, h = v["width"], v["height"]
    if abs(rot) % 180 == 90:   # ffmpeg autorotates, so the filters see swapped dims
        w, h = h, w
    num, den = (v.get("avg_frame_rate") or "30/1").split("/")
    fps = float(num) / float(den) if float(den) else 30.0
    dur = float(info["format"].get("duration") or v.get("duration") or 0)
    hdr = v.get("color_transfer") in ("smpte2084", "arib-std-b67") or any("dv_profile" in sd for sd in v.get("side_data_list", []))
    return {"w": w, "h": h, "fps": fps, "dur": dur, "hdr": hdr, "trc": v.get("color_transfer"),
            "audio": any(s["codec_type"] == "audio" for s in info["streams"])}


def plan(src: dict, size_mb: float) -> dict:
    budget = size_mb * 8 * 1000 * 0.96 / src["dur"]   # kbps, 4% container / rate-control margin
    ak = (128 if budget > 3000 else 96 if budget > 600 else 64 if budget > 200 else 32) if src["audio"] else 0
    vk = budget - ak
    short, fps_cap = next((s, f) for s, f, need in LADDER if vk >= need)
    short = min(short, min(src["w"], src["h"]))
    vk = int(min(vk, CEILING.get(short, 1500)))
    w, h = (-2, short) if src["w"] >= src["h"] else (short, -2)
    return {"w": w, "h": h, "short": short, "fps": min(fps_cap, round(src["fps"])), "vk": vk, "ak": ak}


def vfilter(src: dict, p: dict) -> str:
    w, h = p["w"], p["h"]
    if src["hdr"]:   # resize while 10-bit, linearize, Hable tonemap into BT.709 SDR
        tin = src["trc"] if src["trc"] in ("smpte2084", "arib-std-b67") else "arib-std-b67"
        chain = (f"scale={w}:{h}:flags=lanczos,zscale=tin={tin}:pin=bt2020:min=bt2020nc:t=linear:npl=100,"
                 "format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
    else:
        chain = f"scale={w}:{h}:flags=lanczos,format=yuv420p"
    return chain + (f",fps={p['fps']}" if round(src["fps"]) > p["fps"] else "")


def _ff(args: list[str]) -> None:
    r = subprocess.run(["nice", "-n", "15", "ffmpeg", "-hide_banner", "-y", *args], stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE, text=True, timeout=1800)
    if r.returncode:
        raise RuntimeError("ffmpeg failed: " + r.stderr[-300:])


def encode(path: str, out: str, src: dict, p: dict) -> None:
    common = ["-i", path, "-map", "0:v:0", "-vf", vfilter(src, p), "-c:v", "libx264", "-preset", "medium",
              "-profile:v", "high", "-b:v", f"{p['vk']}k", "-maxrate", f"{int(p['vk'] * 1.5)}k", "-bufsize", f"{p['vk'] * 2}k",
              "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
              "-map_metadata", "-1", "-map_chapters", "-1"]
    with tempfile.TemporaryDirectory() as td:
        log = os.path.join(td, "pass")
        _ff([*common, "-pass", "1", "-passlogfile", log, "-an", "-f", "null", os.devnull])
        audio = ["-map", "0:a:0", "-c:a", "aac", "-b:a", f"{p['ak']}k", "-ac", "2"] if p["ak"] else ["-an"]
        _ff([*common, "-pass", "2", "-passlogfile", log, *audio, "-movflags", "+faststart", out])


def shrink(path: str, out: str, size_mb: float = TARGET_MB) -> dict:
    """Encode `path` to `out` under size_mb; retries smaller if x264 overshoots. Returns the plan + final size."""
    src = probe(path)
    if src["dur"] <= 0:
        raise ValueError("can't read the video's length")
    p, limit = plan(src, size_mb), size_mb * 1_000_000
    for _ in range(3):
        encode(path, out, src, p)
        got = os.path.getsize(out)
        if got <= limit:
            break
        p["vk"] = int(p["vk"] * limit / got * 0.95)
    return {**p, "bytes": got, "dur": src["dur"], "poor": p["vk"] < 300}


def _run(jid: str, src_path: str) -> None:
    job = _jobs[jid]
    job["state"] = "working"
    try:
        r = shrink(src_path, job["out"])
        job.update(state="done", mb=round(r["bytes"] / 1e6, 2), res=f"{r['short']}p{r['fps']}", poor=r["poor"], dur=round(r["dur"]))
    except Exception as e:   # surfaced to the page; the source is dropped either way
        job.update(state="error", error=str(e)[:300])
        if os.path.exists(job["out"]):
            os.remove(job["out"])
    finally:
        os.remove(src_path)


def _sweep() -> None:
    cutoff = time.time() - KEEP_DAYS * 86400
    for f in os.listdir(MEDIA_DIR):
        fp = os.path.join(MEDIA_DIR, f)
        if os.path.getmtime(fp) < cutoff:
            os.remove(fp)
    with _lock:
        for jid in [j for j, v in _jobs.items() if v["t"] < cutoff]:
            del _jobs[jid]


# ── API ──────────────────────────────────────────────────────────────────────

@router.get("/me")
async def me(request: Request):
    sess = auth.read_session(request)
    return {"user": sess and {"id": sess["id"], "username": sess["username"]}, "can_post": bool(BOT_TOKEN)}


@router.post("")
async def upload(request: Request):
    """Raw body = the video file (the page sends it with XHR for an upload progress bar)."""
    sess = auth.read_session(request)
    if not sess:
        return _err("sign in with Discord first", 401)
    if sum(1 for j in _jobs.values() if j["uid"] == sess["id"] and j["state"] in ("queued", "working")) >= 2:
        return _err("you already have videos shrinking; wait for those", 429)
    os.makedirs(MEDIA_DIR, exist_ok=True)
    await asyncio.to_thread(_sweep)
    jid = secrets.token_urlsafe(9)
    src_path, total = os.path.join(MEDIA_DIR, f".{jid}.src"), 0
    try:
        with open(src_path, "wb") as f:
            async for chunk in request.stream():
                total += len(chunk)
                if total > MAX_UPLOAD:
                    raise ValueError
                f.write(chunk)
    except Exception:   # too big, or the phone dropped the upload
        os.remove(src_path)
        return _err("upload failed or over 2 GB", 413)
    if total < 1000:
        os.remove(src_path)
        return _err("that file is empty")
    name = (request.headers.get("X-Filename") or "video").rsplit(".", 1)[0][:60]
    _jobs[jid] = {"uid": sess["id"], "user": sess["username"], "state": "queued", "t": time.time(), "name": name,
                  "out": os.path.join(MEDIA_DIR, f"{jid}.mp4")}
    _pool.submit(_run, jid, src_path)
    return {"id": jid}


def _job(request: Request, jid: str) -> tuple[dict | None, JSONResponse | None]:
    sess = auth.read_session(request)
    job = _jobs.get(jid)
    if not sess:
        return None, _err("sign in with Discord first", 401)
    if not job or job["uid"] != sess["id"]:
        return None, _err("no such video", 404)
    return job, None


@router.get("/{jid}")
async def status(request: Request, jid: str):
    job, err = _job(request, jid)
    if err:
        return err
    out = {k: job.get(k) for k in ("state", "mb", "res", "poor", "dur", "error", "name")}
    if job["state"] == "queued":
        out["ahead"] = sum(1 for j in _jobs.values() if j["state"] in ("queued", "working") and j["t"] < job["t"])
    if job["state"] == "done":
        out["url"] = f"{MEDIA_URL}/{jid}.mp4"
    return out


@router.get("/{jid}/file")
async def download(request: Request, jid: str):
    job, err = _job(request, jid)
    if err:
        return err
    if job["state"] != "done":
        return _err("not ready yet", 409)
    return FileResponse(job["out"], media_type="video/mp4", filename=f"{job['name']}.mp4")


# ── posting through the SharpLab bot ─────────────────────────────────────────

_chan_cache: dict = {"t": 0, "list": []}


async def _channels() -> list[dict]:
    if time.time() - _chan_cache["t"] < 300:
        return _chan_cache["list"]
    out = []
    async with httpx.AsyncClient(timeout=10, headers={"Authorization": f"Bot {BOT_TOKEN}"}) as c:
        for gid in sorted(auth.GUILD_IDS):
            r = await c.get(f"{DISCORD_API}/guilds/{gid}/channels")
            if r.status_code != 200:
                continue
            chans = r.json()
            cats = {ch["id"]: ch for ch in chans if ch["type"] == 4}
            for ch in chans:
                if ch["type"] in (0, 5):   # text + announcement
                    cat = cats.get(ch.get("parent_id"))
                    out.append({"id": ch["id"], "name": ch["name"], "cat": cat["name"] if cat else "",
                                "order": (cat["position"] if cat else -1, ch["position"])})
    out.sort(key=lambda ch: ch["order"])
    _chan_cache.update(t=time.time(), list=[{k: ch[k] for k in ("id", "name", "cat")} for ch in out])
    return _chan_cache["list"]


@router.get("/discord/channels")
async def channels(request: Request):
    if not auth.read_session(request):
        return _err("sign in with Discord first", 401)
    if not BOT_TOKEN:
        return _err("the bot isn't configured here", 503)
    return {"channels": await _channels()}


@router.post("/{jid}/send")
async def send(request: Request, jid: str):
    job, err = _job(request, jid)
    if err:
        return err
    if job["state"] != "done":
        return _err("not ready yet", 409)
    body = await request.json()
    cid, note = str(body.get("channel") or ""), str(body.get("note") or "")[:500]
    if cid not in {ch["id"] for ch in await _channels()}:
        return _err("pick a channel from the list")
    content = f"📹 from **{job['user']}**" + (f": {note}" if note.strip() else "")
    payload = {"content": content, "allowed_mentions": {"parse": []}}
    with open(job["out"], "rb") as f:
        async with httpx.AsyncClient(timeout=60, headers={"Authorization": f"Bot {BOT_TOKEN}"}) as c:
            r = await c.post(f"{DISCORD_API}/channels/{cid}/messages",
                             data={"payload_json": json.dumps(payload)}, files={"files[0]": (f"{job['name']}.mp4", f, "video/mp4")})
    if r.status_code >= 300:
        return _err("Discord said no (the bot may not be allowed to post there)" if r.status_code == 403 else f"Discord error {r.status_code}", 502)
    m = r.json()
    return {"ok": True, "link": f"https://discord.com/channels/{m.get('guild_id') or next(iter(auth.GUILD_IDS), '@me')}/{cid}/{m['id']}"}
