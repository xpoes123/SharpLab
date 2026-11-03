"""sharplab-wall: the wall engine's HTTP API (docs/wall-service.md). Runs on the homelab as a user unit:

    uv run python -m wall.server          (WALL_HOST=0.0.0.0, WALL_PORT=7795)

Who may call: loopback (the TV OS router) → everything. Tailscale (100.64.0.0/10, the VPS bot / web) → only with
`Authorization: Bearer $WALL_API_TOKEN`, and only the discord instance, its HLS, and read-only preset / live lookups.
Anyone else → 403. Nothing here is meant to be public; sharplab-web gates viewers with SharpLab sessions.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import secrets
import socket
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import ValidationError

from wall.engine import Session
from wall.headless import Headless
from wall.instance import BOARDS, Instance, WallError
from wall.llm import LLMGuard
from wall.live import LEAGUES, STREAM, TEAMS, Scores, live_games
from wall.presets import PresetStore, check_url, errors

log = logging.getLogger("wall.server")
TAILNET = ipaddress.ip_network("100.64.0.0/10")
REMOTE_OK = re.compile(r"^/(healthz|teams|live/[a-z]+|presets|i/discord(/.*)?|hls/discord/[A-Za-z0-9_.]+)$")
HLS_FILE = re.compile(r"^(index\.m3u8|seg\d{6}\.ts)$")
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def resolve(host: str) -> list:
    return socket.getaddrinfo(host, None)


def tv_active(api: str) -> str | None:
    """What the living-room TV is running (TV OS /healthz "active"), or None when it's idle. Unreachable = idle."""
    import json, urllib.request
    try:
        with urllib.request.urlopen(api.rstrip("/") + "/healthz", timeout=2) as r:
            return json.load(r).get("active")
    except (OSError, ValueError):
        return None


def tv_board(api: str) -> str:
    """Which board the TV router's wall overlay has (its overlay state lives there, not here)."""
    import json, urllib.request
    try:
        with urllib.request.urlopen(api.rstrip("/") + "/api/overlay", timeout=3) as r:
            return json.load(r).get("board") or ""
    except (OSError, ValueError):
        return ""


def clean_origin(o: object) -> dict | None:
    """Where a start came from ({"guild": id, "channel": id} — Discord snowflakes), for the bot to post the link there."""
    if not isinstance(o, dict):
        return None
    out = {k: str(o[k]) for k in ("guild", "channel") if isinstance(o.get(k), (str, int)) and not isinstance(o.get(k), bool)
           and str(o[k]).isdigit() and len(str(o[k])) <= 20}
    return out if "channel" in out else None


def browserish(request: Request) -> bool:
    """A request a web page made: the tiles are arbitrary sites in Chromium on this box, so a page's no-CORS POST (or a
    DNS-rebound one) would arrive from loopback. Real callers (the TV router's urllib, the qml6 overlay's XHR) send no
    Origin / Sec-Fetch-Site and address us as 127.0.0.1 / localhost."""
    host = (urlsplit("//" + request.headers.get("host", "")).hostname or "").lower()
    if host not in LOOPBACK_HOSTS:
        return True
    if request.method not in ("GET", "HEAD") and "origin" in request.headers:
        return True
    return request.headers.get("sec-fetch-site", "none") != "none"


def public_url(u: str) -> str:
    """The discord wall streams its tiles to every viewer: no loopback / LAN / tailnet pages (the TV's own UI, the router,
    CDP, Tailscale-only services). Resolves the host; a name that won't resolve is refused too."""
    host = urlsplit(check_url(u)).hostname or ""
    try:
        addrs = {a[4][0] for a in resolve(host)}
    except (OSError, UnicodeError):
        raise ValueError(f"can't resolve {host[:60]!r}") from None
    for a in addrs:
        ip = ipaddress.ip_address(a.split("%")[0])
        ip = ip.ipv4_mapped or ip if isinstance(ip, ipaddress.IPv6Address) else ip
        if not ip.is_global or ip in TAILNET:
            raise ValueError(f"not a public address: {host[:60]!r}")
    return u


class Cfg:
    def __init__(self, env=os.environ) -> None:
        self.state = Path(env.get("WALL_STATE_DIR") or os.path.expanduser("~/.local/state/sharplab-wall"))
        self.token = env.get("WALL_API_TOKEN", "")
        self.import_from = env.get("WALL_IMPORT_FROM", os.path.expanduser("~/.local/state/tv/wall.json"))
        self.tv_profiles = env.get("WALL_TV_PROFILES", os.path.expanduser("~/.local/state/tv"))
        self.tv_overlay_api = env.get("WALL_TV_OVERLAY_API", "http://127.0.0.1:7790")
        self.port = int(env.get("WALL_PORT", "7795"))
        self.host = env.get("WALL_HOST", "0.0.0.0")
        self.idle_s = float(env.get("WALL_IDLE_S", "600"))
        self.first_viewer_s = float(env.get("WALL_FIRST_VIEWER_S", "900"))
        self.max_hours = float(env.get("WALL_MAX_HOURS", "6"))
        self.min_free_mb = int(env.get("WALL_MIN_FREE_MB", "2500"))
        self.discord = env.get("WALL_DISCORD", "1") not in ("0", "false", "")
        self.tv = env.get("WALL_TV", "1") not in ("0", "false", "")
        self.tv_output = env.get("WALL_TV_OUTPUT", "HDMI-A-1")   # mirror mode streams this output of the TV ("" = copy with tiles)
        self.tv_sink = env.get("WALL_TV_SINK", "")                # and this sink's sound ("" = the default sink)
        self.ollama = env.get("WALL_OLLAMA_URL", "http://127.0.0.1:11434")
        self.llm_guard = env.get("WALL_LLM_GUARD", "unload")   # unload | stop | off (wall/llm.py)
        self.max_load = float(env.get("WALL_MAX_LOAD", "16"))
        self.min_avail_mb = int(env.get("WALL_MIN_AVAIL_MB", "3072"))


def build(cfg: Cfg | None = None, instances: dict[str, Instance] | None = None, scores: Scores | None = None,
          store: PresetStore | None = None, guard: LLMGuard | None = None) -> FastAPI:
    cfg = cfg or Cfg()
    guard = guard or LLMGuard(cfg.ollama, cfg.llm_guard)
    scores = scores or Scores()
    store = store or PresetStore(str(cfg.state), cfg.import_from)
    if instances is None:
        instances = {}
        if cfg.tv:
            instances["tv"] = Instance("tv", Session("tv", cdp_base=9231, profiles=cfg.tv_profiles, overlay_api=cfg.tv_overlay_api,
                                                     unmute_on_stop=True), store, scores)
        if cfg.discord:
            instances["discord"] = Instance(
                "discord", Session("discord", cdp_base=9241, profiles=str(cfg.state), prefix="discord-",
                                   overlay_api=f"http://127.0.0.1:{cfg.port}/i/discord", extra_flags=["--ozone-platform=wayland"]),
                store, scores, headless=Headless("discord", cfg.state), idle_s=cfg.idle_s, first_viewer_s=cfg.first_viewer_s,
                max_hours=cfg.max_hours, min_free_mb=cfg.min_free_mb, max_load=cfg.max_load, min_avail_mb=cfg.min_avail_mb)
    disc, tv = instances.get("discord"), instances.get("tv")
    if disc is not None and disc.headless and tv is not None and disc.source is None:   # the discord wall can mirror the TV's
        disc.source, disc.vet = tv, public_url
        disc.source_board = lambda: tv_board(cfg.tv_overlay_api)
        disc.tv_on_wall = lambda: tv_active(cfg.tv_overlay_api) == "wall"
        disc.capture_output, disc.screen_sink = cfg.tv_output, cfg.tv_sink
    for i in instances.values():
        if i.headless:
            i.before_start = guard.park   # the LLM's VRAM is free before the budget check

    @asynccontextmanager
    async def lifespan(app):
        async def ticker():
            n = 0
            while True:
                await asyncio.sleep(1)
                n += 1
                if n % 10 == 0:   # any wall up: the local LLM stays off the GPU
                    try:
                        await asyncio.to_thread(guard.tick, any(i.urls for i in instances.values()))
                    except Exception as e:  # noqa: BLE001
                        log.warning("llm guard: %s", e)
                for inst in instances.values():
                    try:
                        await asyncio.to_thread(inst.tick)
                    except Exception as e:  # noqa: BLE001
                        log.warning("tick %s: %s", inst.name, e)
        task = asyncio.create_task(ticker())
        yield
        task.cancel()
        for inst in instances.values():
            await asyncio.to_thread(inst.stop, "service stopped")

    app = FastAPI(title="sharplab-wall", lifespan=lifespan)
    app.state.instances, app.state.store, app.state.scores, app.state.guard = instances, store, scores, guard

    @app.middleware("http")
    async def gate(request: Request, call_next):
        host = request.client.host if request.client else ""
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None
        if ip is not None and ip.is_loopback:
            if browserish(request):
                return JSONResponse({"error": "forbidden"}, status_code=403)
            return await call_next(request)
        if ip is None or ip not in TAILNET:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        auth = request.headers.get("authorization", "")
        if not cfg.token or not secrets.compare_digest(auth, f"Bearer {cfg.token}"):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if not REMOTE_OK.match(request.url.path) or (request.url.path == "/presets" and request.method != "GET"):
            return JSONResponse({"error": "not for remote callers"}, status_code=403)
        return await call_next(request)

    def inst(name: str) -> Instance:
        if name not in instances:
            raise WallError(f"no wall instance {name!r}")
        return instances[name]

    @app.exception_handler(WallError)
    async def wall_error(request: Request, e: WallError):
        return JSONResponse({"error": str(e)}, status_code=404 if str(e).startswith("no wall instance") else 409)

    @app.exception_handler(ValueError)
    async def value_error(request: Request, e: ValueError):
        return JSONResponse({"error": str(e)[:300]}, status_code=422)

    async def body(request: Request) -> dict:
        try:
            data = await request.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise ValueError("body must be a JSON object")
        return data

    def num(d: dict, k: str) -> int:
        v = d.get(k)
        if not isinstance(v, int) or isinstance(v, bool):
            raise ValueError(f"{k} must be an integer")
        return v

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True, "instances": {k: v.state()["running"] for k, v in instances.items()}}

    # ---- presets / lookups ----
    @app.get("/presets")
    def get_presets() -> list[dict]:
        return store.presets

    @app.put("/presets")
    async def put_presets(request: Request) -> Response:
        try:
            data = await request.json()
        except ValueError:
            return JSONResponse({"error": "bad json"}, status_code=400)
        try:
            return JSONResponse(store.put(data))
        except ValidationError as e:
            return JSONResponse({"error": errors(e)}, status_code=422)

    @app.get("/live/{league}")
    def get_live(league: str, n: int = 4) -> Response:
        if league not in LEAGUES:
            return JSONResponse({"error": "league is nfl or nba"}, status_code=404)
        return JSONResponse(live_games(scores.get(league, True), league, max(1, min(4, n))))

    @app.get("/teams")
    def get_teams() -> dict:
        return {"stream": STREAM, **TEAMS}

    # ---- instances ----
    @app.get("/i")
    def all_states() -> dict:
        return {k: v.state() for k, v in instances.items()}

    @app.get("/i/{name}")
    def get_state(name: str) -> dict:
        return inst(name).state()

    @app.post("/i/{name}/start")
    async def start(name: str, request: Request) -> dict:
        d = await body(request)
        i = inst(name)
        preset = d.get("preset")
        if preset is not None and not isinstance(preset, (int, str)):
            raise ValueError("preset is an index or a name")
        urls = d.get("urls")
        if urls is not None and not (isinstance(urls, list) and all(isinstance(u, str) for u in urls)):
            raise ValueError("urls is a list of strings")
        board, layout = d.get("board"), d.get("layout")
        tiles = d.get("tiles", 4)
        if not isinstance(tiles, int) or not 1 <= tiles <= 4:
            raise ValueError("tiles is 1–4")
        mirror, origin = d.get("mirror") is True, clean_origin(d.get("origin"))
        if i.headless and i.urls and mirror:   # already on: "same as the TV" = follow it again
            return await asyncio.to_thread(i.follow_source)
        if i.headless and not i.urls:   # anyone may start the discord wall, but only while nobody is using the TV
            busy = await asyncio.to_thread(tv_active, cfg.tv_overlay_api)
            tv = instances.get("tv")
            if busy == "wall" and tv and tv.urls and i.source is not None:   # the TV is on the wall: stream it, and follow it
                return await asyncio.to_thread(i.start_mirror, origin)
            elif busy:
                raise WallError(f"the TV is in use ({busy}) — try again when it's free")
            elif mirror or (preset is None and not urls and not d.get("live")):
                raise WallError("the TV isn't on the wall — open the wall on the TV first, or pick a preset, live games or URLs")
        if i.headless and urls:
            await asyncio.to_thread(lambda: [public_url(u) for u in urls])
        return await asyncio.to_thread(i.start, preset=preset, urls=urls, live=d.get("live") or "", tiles=tiles,
                                       board=board, layout=layout, sound=d.get("sound", 0) if isinstance(d.get("sound", 0), int) else 0,
                                       origin=origin)

    def target(name: str) -> Instance:
        """Where a change made on `name` lands. The stream is always the TV while it mirrors: a change made on the stream
        (a passed vote, /wall) goes to the TV's wall, and the stream follows it."""
        i = inst(name)
        tv = instances.get("tv")
        if i.headless and i.follow and tv is not None and tv.urls:
            return tv
        return i

    @app.post("/i/{name}/follow")
    async def follow(name: str) -> dict:
        """Mirror the TV again after a change on this wall stopped the following."""
        i = inst(name)
        if not i.headless:
            raise WallError("only the discord wall follows the TV")
        return await asyncio.to_thread(i.follow_source)

    @app.post("/i/{name}/stop")
    async def stop(name: str) -> dict:
        return await asyncio.to_thread(inst(name).stop)

    @app.post("/i/{name}/tile")
    async def tile(name: str, request: Request) -> dict:
        d = await body(request)
        if not isinstance(d.get("url"), str):
            raise ValueError("url is a string")
        i = inst(name)
        if i.headless:   # streamed either way (its own tile or the TV it mirrors)
            await asyncio.to_thread(public_url, d["url"])
        await asyncio.to_thread(target(name).set_tile, num(d, "i"), d["url"])
        return i.state()

    @app.delete("/i/{name}/tile/{i}")
    async def rm_tile(name: str, i: int) -> dict:
        await asyncio.to_thread(target(name).remove_tile, i)
        return inst(name).state()

    @app.post("/i/{name}/move")
    async def move(name: str, request: Request) -> dict:
        d = await body(request)
        await asyncio.to_thread(target(name).move, num(d, "i"), num(d, "j"))
        return inst(name).state()

    @app.post("/i/{name}/sound")
    async def sound(name: str, request: Request) -> dict:
        d = await body(request)
        await asyncio.to_thread(target(name).sound, num(d, "i"))
        return inst(name).state()

    @app.post("/i/{name}/pin")
    async def pin(name: str, request: Request) -> dict:
        d = await body(request)
        await asyncio.to_thread(target(name).pin, num(d, "i"))
        return inst(name).state()

    @app.post("/i/{name}/board")
    async def board(name: str, request: Request) -> dict:
        d = await body(request)
        if d.get("visible") is not None and not isinstance(d.get("visible"), bool):
            raise ValueError("visible is a boolean")
        await asyncio.to_thread(target(name).set_board, d.get("board"), d.get("visible"), d.get("layout"))
        return inst(name).state()

    @app.post("/i/{name}/overlay")
    async def overlay(name: str, request: Request) -> dict:
        d = await body(request)
        return await asyncio.to_thread(inst(name).overlay, d.get("view") or "", bool(d.get("lbar")))

    # ---- the overlay process of a service-owned overlay polls these (under its --api) ----
    @app.get("/i/{name}/api/overlay/view")
    def ov_view(name: str) -> dict:
        return inst(name).overlay_view()

    @app.get("/i/{name}/api/overlay")
    async def ov_data(name: str, board: str = "") -> dict:
        return await asyncio.to_thread(inst(name).overlay_data, board)

    # ---- HLS ----
    @app.get("/hls/{name}/{file}")
    def hls(name: str, file: str) -> Response:
        i = inst(name)
        if not i.headless or not HLS_FILE.match(file) or not i.urls or not i.headless.up:   # a stopped wall's stale playlist isn't live
            return JSONResponse({"error": "not found"}, status_code=404)
        path = i.headless.hls / file
        if not path.exists():
            return JSONResponse({"error": "not found"}, status_code=404)
        if file.endswith(".m3u8"):
            i.seen()
            return FileResponse(path, media_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "no-cache"})
        return FileResponse(path, media_type="video/mp2t", headers={"Cache-Control": "max-age=60"})

    app.state.boards = BOARDS
    return app


def main() -> None:
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    cfg = Cfg()
    if not cfg.token:
        log.warning("WALL_API_TOKEN is not set: only loopback callers will get in")
    # proxy_headers off: the gate trusts the socket's peer address only, never X-Forwarded-For
    uvicorn.run(build(cfg), host=cfg.host, port=cfg.port, log_level="info", access_log=False, proxy_headers=False)


if __name__ == "__main__":
    main()
