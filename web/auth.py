"""Discord OAuth2 login + signed session cookies for SharpLab HQ.

No DB sessions — the cookie is a signed, time-limited token (itsdangerous)
carrying the Discord id/name/avatar. Guild-gated: only members of
DISCORD_GUILD_ID get a session.
"""

from __future__ import annotations

import logging
import os
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

log = logging.getLogger(__name__)

CLIENT_ID = os.environ.get("DISCORD_OAUTH_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("DISCORD_OAUTH_CLIENT_SECRET", "")
WEB_BASE_URL = os.environ.get("WEB_BASE_URL", "https://sharplab.djiang.xyz")
REDIRECT_URI = os.environ.get(
    "DISCORD_OAUTH_REDIRECT_URI", f"{WEB_BASE_URL}/api/v1/auth/discord/callback",
)
# Reuse the bot↔web shared secret to sign cookies unless given a dedicated one.
SESSION_SECRET = os.environ.get("SESSION_SECRET") or os.environ.get("WEB_API_SECRET", "dev-secret")
if SESSION_SECRET == "dev-secret":
    log.warning("session cookies are signed with the insecure default secret — set "
                "SESSION_SECRET (or WEB_API_SECRET); sessions are forgeable until you do.")
GUILD_IDS = {g for g in os.environ.get("DISCORD_GUILD_ID", "").split(",") if g}

COOKIE_NAME = "sharplab_session"
SESSION_TTL = 7 * 24 * 3600  # 1 week
_STATE_TTL = 600

DISCORD_API = "https://discord.com/api"

_session_signer = URLSafeTimedSerializer(SESSION_SECRET, salt="sharplab-hq-session")
_state_signer = URLSafeTimedSerializer(SESSION_SECRET, salt="sharplab-hq-oauth-state")


def oauth_configured() -> bool:
    return bool(CLIENT_ID and CLIENT_SECRET)


def make_state() -> str:
    return _state_signer.dumps(secrets.token_urlsafe(16))


def check_state(state: str) -> bool:
    try:
        _state_signer.loads(state, max_age=_STATE_TTL)
        return True
    except (BadSignature, SignatureExpired):
        return False


def login_url(state: str) -> str:
    q = urlencode({
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "identify guilds",
        "state": state,
        "prompt": "none",
    })
    return f"{DISCORD_API}/oauth2/authorize?{q}"


async def exchange_code(code: str) -> dict:
    """Exchange an auth code for the user's profile + guild memberships."""
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        tok_resp = await client.post(
            f"{DISCORD_API}/oauth2/token", data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        tok_resp.raise_for_status()
        access = tok_resp.json()["access_token"]
        auth_hdr = {"Authorization": f"Bearer {access}"}
        user = (await client.get(f"{DISCORD_API}/users/@me", headers=auth_hdr)).json()
        guilds: list[str] = []
        if GUILD_IDS:
            gr = await client.get(f"{DISCORD_API}/users/@me/guilds", headers=auth_hdr)
            if gr.status_code == 200:
                guilds = [g["id"] for g in gr.json()]
    return {"user": user, "guilds": guilds}


def is_member(guilds: list[str]) -> bool:
    """True if the user is in a configured guild (or no gating is configured)."""
    return not GUILD_IDS or any(g in GUILD_IDS for g in guilds)


def make_session_cookie(user: dict) -> str:
    return _session_signer.dumps({
        "id": user["id"],
        "username": user.get("global_name") or user.get("username") or "Player",
        "avatar": user.get("avatar"),
    })


def read_session(request: Request) -> dict | None:
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        return None
    try:
        return _session_signer.loads(raw, max_age=SESSION_TTL)
    except (BadSignature, SignatureExpired):
        return None


def avatar_url(uid: str, avatar_hash: str | None) -> str | None:
    if not avatar_hash:
        return None
    ext = "gif" if avatar_hash.startswith("a_") else "png"
    return f"https://cdn.discordapp.com/avatars/{uid}/{avatar_hash}.{ext}"
