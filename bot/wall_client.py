"""HTTP clients for /wall: the wall service on the homelab (over Tailscale, bearer token) and the
sharplab-web wall API (viewers, proposals, votes, login links; X-Api-Key like the other bot↔web calls).

Every path lives here so the contract in docs/wall-service.md is easy to follow when it moves.
"""
from __future__ import annotations

import os

import httpx

WALL_SERVICE_URL = os.environ.get("WALL_SERVICE_URL", "http://100.67.232.59:7795").rstrip("/")
WALL_API_TOKEN = os.environ.get("WALL_API_TOKEN", "")
WEB_API_BASE = os.environ.get("WEB_API_BASE", "https://sharplab.djiang.xyz").rstrip("/")
WEB_API_SECRET = os.environ.get("WEB_API_SECRET", "dev-secret")
INST = "discord"


class WallError(Exception):
    """A call failed; str() is safe to show the user."""


async def _json(resp: httpx.Response):
    try:
        data = resp.json()
    except ValueError:
        data = None
    if resp.status_code >= 400:
        msg = data.get("error") if isinstance(data, dict) else None
        raise WallError(msg or f"HTTP {resp.status_code}")
    return data


class WallService:
    """The wall engine's control API (`/presets`, `/live`, `/teams`, `/i/discord/*`)."""

    def __init__(self, base: str = WALL_SERVICE_URL, token: str = WALL_API_TOKEN, transport=None) -> None:
        self.base, self.token, self.transport = base.rstrip("/"), token, transport

    async def _call(self, method: str, path: str, body: dict | None = None):
        try:
            async with httpx.AsyncClient(timeout=15, transport=self.transport) as c:
                r = await c.request(method, f"{self.base}{path}", json=body,
                                    headers={"Authorization": f"Bearer {self.token}"})
        except httpx.HTTPError as e:
            raise WallError(f"wall service unreachable ({type(e).__name__})") from e
        return await _json(r)

    async def presets(self) -> list[dict]:
        return await self._call("GET", "/presets")

    async def live(self, league: str, n: int = 4) -> list[dict]:
        return await self._call("GET", f"/live/{league}?n={n}")

    async def teams(self) -> dict:
        return await self._call("GET", "/teams")

    async def state(self) -> dict:
        return await self._call("GET", f"/i/{INST}")

    async def apply(self, action: dict) -> dict:
        """Run one action ({"op": ..., ...}, the same shape the web API's proposals carry) → the new state."""
        op, body = action.get("op"), {k: v for k, v in action.items() if k != "op"}
        if op == "start":
            return await self._call("POST", f"/i/{INST}/start", body)
        if op == "stop":
            return await self._call("POST", f"/i/{INST}/stop", {})
        if op == "tile":
            return await self._call("POST", f"/i/{INST}/tile", body)
        if op == "remove":
            return await self._call("DELETE", f"/i/{INST}/tile/{int(body['i'])}")
        if op == "board":
            return await self._call("POST", f"/i/{INST}/board", body)
        if op == "sound":
            return await self._call("POST", f"/i/{INST}/sound", body)
        raise WallError(f"unknown action {op!r}")


class WallWeb:
    """sharplab-web's /api/v1/wall/*."""

    def __init__(self, base: str = WEB_API_BASE, secret: str = WEB_API_SECRET, transport=None) -> None:
        self.base, self.secret, self.transport = base.rstrip("/"), secret, transport

    @property
    def watch_url(self) -> str:
        return f"{self.base}/wall.html"   # Caddy serves the page statically; /wall needs deploy/caddy-wall.snippet

    async def _call(self, method: str, path: str, body: dict | None = None):
        try:
            async with httpx.AsyncClient(timeout=10, transport=self.transport) as c:
                r = await c.request(method, f"{self.base}/api/v1/wall{path}", json=body,
                                    headers={"X-Api-Key": self.secret})
        except httpx.HTTPError as e:
            raise WallError(f"web unreachable ({type(e).__name__})") from e
        return await _json(r)

    async def state(self) -> dict:
        return await self._call("GET", "/state")

    async def propose(self, user: dict, action: dict) -> dict:
        return await self._call("POST", "/propose", {"discord_id": user["id"], "name": user["name"],
                                                     "avatar": user.get("avatar"), "action": action})

    async def vote(self, discord_id: str, proposal_id: str, yes: bool) -> dict:
        return await self._call("POST", "/vote", {"discord_id": discord_id, "proposal_id": proposal_id, "yes": yes})

    async def link(self, user: dict) -> str:
        data = await self._call("POST", "/link", {"discord_id": user["id"], "username": user["name"],
                                                  "avatar": user.get("avatar")})
        return data["url"]
