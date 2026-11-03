"""The watch party's chat, for the living-room TV: while the discord wall runs, the service polls sharplab-web's bot API
(GET /api/v1/wall/chat, X-Api-Key = WEB_API_SECRET, over Tailscale) and keeps the last few messages + who's watching. The
TV router reads them from loopback (GET /chat) for the wall overlay's sidebar and its phones, and its phones post through
POST /chat, which goes out as the owner (WALL_OWNER_ID). The key stays in this service's env: never in a browser, never
in the TV router.

Moderation is the web's: a deleted message drops out of the poll's `ids` (so it leaves here too) and a muted user's
messages aren't sent while the mute lasts; a muted owner gets the web's 403.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Callable

log = logging.getLogger("wall.chat")
KEEP = 20          # messages kept here (the TV shows the last ~6, its phones ~8)
POLL_S = 1.5
OLD_S = 120        # a message from before this wall started (minus this) is an earlier party's: not shown


def urllib_http(base: str, key: str, timeout: float = 4.0) -> Callable[[str, str, dict | None], tuple[int, object]]:
    def call(method: str, path: str, body: dict | None) -> tuple[int, object]:
        data = None if body is None else json.dumps(body).encode()
        headers = {"X-Api-Key": key, **({"Content-Type": "application/json"} if data is not None else {})}
        req = urllib.request.Request(base.rstrip("/") + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"null")
            except ValueError:
                return e.code, {"error": str(e.reason)}
    return call


class ChatFeed:
    def __init__(self, url: str = "", key: str = "", owner_id: str = "", owner_name: str = "David",
                 http: Callable[[str, str, dict | None], tuple[int, object]] | None = None, clock=time.time) -> None:
        self.configured = bool(url and key and owner_id.isdigit())
        self.http = http or (urllib_http(url, key) if self.configured else None)
        self.owner_id, self.owner_name, self.clock = owner_id, owner_name or "David", clock
        self.rev = 0
        self.since = None
        self.reset()

    def reset(self) -> None:
        self.messages: list[dict] = []
        self.viewers: list[dict] = []
        self.watching = self.active = 0
        self.last = 0
        self.error = ""

    def _bump(self) -> None:
        self.rev += 1

    def idle(self) -> None:
        """The discord wall isn't running: nothing to show."""
        if self.since is not None or self.messages:
            self.since = None
            self.reset()
            self._bump()

    def poll(self, since: float) -> bool:
        """One round against the web (blocking: run it off the event loop). since = when this wall started. True = changed."""
        if since != self.since:
            self.since = since
            self.reset()
            self._bump()
        try:
            status, data = self.http("GET", f"/api/v1/wall/chat?after={self.last}&limit=50", None)
        except (OSError, ValueError) as e:   # URLError / timeouts are OSErrors
            return self._fail(f"web unreachable: {e}"[:120])
        if status != 200 or not isinstance(data, dict):
            return self._fail(f"web answered {status}")
        ids = {i for i in data.get("ids") or [] if isinstance(i, int)}
        new = [self._clean(m) for m in data.get("messages") or [] if isinstance(m, dict) and isinstance(m.get("id"), int)]
        kept = [m for m in self.messages if m["id"] in ids]
        have = {m["id"] for m in kept}
        msgs = (kept + [m for m in new if m["id"] not in have and m["id"] in ids and m["ts"] >= since - OLD_S])[-KEEP:]
        viewers = [{"name": str(v.get("name") or "?")[:40], "guest": bool(v.get("guest")), "away": bool(v.get("away"))}
                   for v in data.get("viewers") or [] if isinstance(v, dict)]
        watching, active = int(data.get("watching") or 0), int(data.get("active") or 0)
        last = max(self.last, int(data.get("last") or 0))
        before = (self.messages, self.viewers, self.watching, self.active, self.error)
        self.messages, self.viewers, self.watching, self.active, self.last, self.error = msgs, viewers, watching, active, last, ""
        if before != (msgs, viewers, watching, active, ""):
            self._bump()
            return True
        return False

    def _fail(self, why: str) -> bool:
        if why != self.error:
            log.warning("chat poll: %s", why)
            self.error = why
            self._bump()
            return True
        return False

    @staticmethod
    def _clean(m: dict) -> dict:
        return {"id": m["id"], "ts": float(m.get("ts") or 0), "kind": "system" if m.get("kind") == "system" else "chat",
                "name": str(m.get("name") or "")[:40], "guest": bool(m.get("guest")), "text": str(m.get("text") or "")[:400]}

    def view(self, running: bool) -> dict:
        """GET /chat: on = the discord wall runs and the chat is set up."""
        on = self.configured and running
        return {"on": on, "rev": self.rev, "messages": self.messages if on else [], "viewers": self.viewers if on else [],
                "watching": self.watching if on else 0, "active": self.active if on else 0, "error": self.error if on else "",
                "owner": self.owner_name, "configured": self.configured}

    def post(self, text: str) -> tuple[int, dict]:
        """POST /chat: as the owner. (status, body) — the web's own answer (429 too fast, 403 muted, 422 empty)."""
        try:
            status, data = self.http("POST", "/api/v1/wall/chat", {"discord_id": self.owner_id, "text": text})
        except (OSError, ValueError) as e:
            return 502, {"error": f"couldn't reach the watch page: {e}"[:160]}
        data = data if isinstance(data, dict) else {}
        if status == 200 and isinstance(data.get("msg"), dict):
            m = self._clean(data["msg"])
            if m["id"] not in {x["id"] for x in self.messages}:   # shows now, not on the next poll
                self.messages = (self.messages + [m])[-KEEP:]
                self._bump()
            return 200, {"msg": m}
        if status == 200:
            return 502, {"error": "the watch page sent a bad answer"}
        return status, {"error": str(data.get("error") or f"the watch page answered {status}")[:160]}
