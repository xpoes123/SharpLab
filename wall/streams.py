"""Is a YouTube tile's stream on yet? From the stream's public watch page: live now / scheduled (with its start) /
neither. Checked in the background (refresh(), every CHECK_S per URL); peek() never blocks, for the instances' state."""
from __future__ import annotations

import re
import time
import urllib.request

from wall.engine import YT_LIVE, resolve_live

CHECK_S = 60.0
WATCH = re.compile(r"^https://(www\.|m\.)?youtube\.com/watch\?v=[\w-]{11}")
_cache: dict[str, tuple[float, dict]] = {}


def is_stream(url: str) -> bool:
    return bool(YT_LIVE.match(url) or WATCH.match(url))


def parse(body: bytes) -> dict:
    """{"live": True | False, "starts": epoch | None}: isLiveNow, else a scheduled start (isUpcoming)."""
    if b'"isLiveNow":true' in body:
        return {"live": True, "starts": None}
    m = re.search(rb'"scheduledStartTime":"(\d+)"', body)
    return {"live": False, "starts": int(m.group(1)) if m and b'"isUpcoming":true' in body else None}


def check(url: str) -> dict:
    target = resolve_live(url)
    if not WATCH.match(target):   # a channel with no stream on: nothing live
        return {"live": False, "starts": None}
    req = urllib.request.Request(target, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/140.0 Safari/537.36",
                                                  "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=6) as r:
        return parse(r.read(2_000_000))


def refresh(urls: list[str]) -> None:
    now = time.monotonic()
    for u in {u for u in urls if is_stream(u)}:
        hit = _cache.get(u)
        if hit and now - hit[0] < CHECK_S:
            continue
        try:
            _cache[u] = (now, check(u))
        except Exception:  # noqa: BLE001 — unknown this round
            _cache[u] = (now, {"live": None, "starts": None})


def peek(url: str) -> dict | None:
    hit = _cache.get(url)
    return hit[1] if hit and is_stream(url) else None
