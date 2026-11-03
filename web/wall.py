"""The Discord wall's watch page backend: a SharpLab-session-gated HLS proxy, a socket for chat + who's watching +
votes, and a small bot API. docs/wall-service.md has the whole picture.

The wall itself (Chromium tiles in a headless KWin, captured to HLS) runs on the homelab as sharplab-wall; this app
talks to it over Tailscale with WALL_API_TOKEN and is the only thing that does. Viewers never reach the homelab:
segments come through /api/v1/wall/hls/*, cached here so N viewers cost one upstream fetch.

Pure state (Presence, Chat, Votes) takes `now` explicitly so the rules are testable without sockets; Hub glues them to
the sockets and to the wall service.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import os
import re
import secrets
import time
from collections import deque
from dataclasses import dataclass, field

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from web import auth
from web._apisec import check_api_key

log = logging.getLogger(__name__)

PAGE = "/wall.html"
AWAY_S = 60            # no heartbeat for this long (or a hidden tab) = away
CHAT_MAX = 300
SCROLLBACK = 200
CHAT_GAP_S = 1.5       # one message per 1.5 s …
CHAT_BURST = (8, 30)   # … and at most 8 per 30 s
PROPOSE_GAP_S = 20
LINK_TTL = 600
LAYOUTS = ("lbar", "full")
BOARDS = ("", "nfl", "nba")
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏‪-‮⁦-⁩]")
_HLS_NAME = re.compile(r"^(index\.m3u8|seg\d{6}\.ts)$")   # exactly what the wall service serves


def cfg() -> dict:
    """Read on use, so tests (and a restart with a new .env) see the current values."""
    e = os.environ.get
    return {
        "service": e("WALL_SERVICE_URL", "http://100.67.232.59:7795").rstrip("/"),
        "token": e("WALL_API_TOKEN", ""),
        "owner": e("WALL_OWNER_ID", ""),
        "admins": {x.strip() for x in e("WALL_ADMIN_IDS", "").split(",") if x.strip()},
        "delay": float(e("WALL_SYNC_DELAY_S", "6")),
        "sound_approvals": max(1, int(e("WALL_SOUND_APPROVALS", "2"))),
        "vote_s": float(e("WALL_VOTE_S", "60")),
        "notices": e("WALL_JOIN_NOTICES", "0") == "1",
        # demo mode: anyone with the keyed link (/wall.html?k=WALL_DEMO_KEY) may watch under a nickname
        "demo_key": e("WALL_DEMO_KEY", "") if e("WALL_DEMO", "0") == "1" and len(e("WALL_DEMO_KEY", "")) >= 16 else "",
    }


def initials(name: str) -> str:
    parts = [p for p in re.split(r"[\s_.-]+", name or "") if p]
    return ("".join(p[0] for p in parts[:2]) or "?").upper()


def clean_text(text: object, limit: int = CHAT_MAX) -> str:
    """One line of plain text: control / bidi characters gone, whitespace squeezed, capped. (The page renders with
    textContent, so markup is shown, never run.)"""
    if not isinstance(text, str):
        return ""
    t = _CTRL.sub("", text.replace("\r", " ").replace("\n", " ").replace("\t", " "))
    return re.sub(r" {2,}", " ", t).strip()[:limit]


# ---- who's watching -------------------------------------------------------------------------------------------------
@dataclass
class Conn:
    uid: str
    hidden: bool = False
    last: float = 0.0


class Presence:
    """Viewers by connection (a user with two tabs is one viewer, active if either tab is)."""

    def __init__(self) -> None:
        self.conns: dict[int, Conn] = {}
        self.users: dict[str, dict] = {}   # uid → {id, name, avatar}

    def join(self, cid: int, user: dict, now: float) -> bool:
        """True when this user wasn't watching before."""
        new = not any(c.uid == user["id"] for c in self.conns.values())
        self.users[user["id"]] = {"id": user["id"], "name": user.get("name") or "Player", "avatar": user.get("avatar"),
                                  "guest": bool(user.get("guest"))}
        self.conns[cid] = Conn(user["id"], False, now)
        return new

    def leave(self, cid: int) -> str | None:
        """The uid when that was the user's last tab."""
        c = self.conns.pop(cid, None)
        if c and not any(x.uid == c.uid for x in self.conns.values()):
            return c.uid
        return None

    def beat(self, cid: int, hidden: bool, now: float) -> None:
        c = self.conns.get(cid)
        if c:
            c.hidden, c.last = bool(hidden), now

    def is_active(self, uid: str, now: float) -> bool:
        return any(c.uid == uid and not c.hidden and now - c.last <= AWAY_S for c in self.conns.values())

    def active(self, now: float) -> set[str]:
        return {c.uid for c in self.conns.values() if not c.hidden and now - c.last <= AWAY_S}

    def watching(self) -> set[str]:
        return {c.uid for c in self.conns.values()}

    def listing(self, now: float) -> list[dict]:
        act = self.active(now)
        out = [{**self.users[u], "initials": initials(self.users[u]["name"]), "away": u not in act} for u in self.watching()]
        return sorted(out, key=lambda v: (v["away"], v["name"].lower()))


# ---- chat ------------------------------------------------------------------------------------------------------------
class Chat:
    def __init__(self) -> None:
        self.log: deque[dict] = deque(maxlen=SCROLLBACK)
        self.ids = itertools.count(1)
        self.sent: dict[str, deque] = {}
        self.muted: dict[str, float] = {}   # uid → until

    def is_muted(self, uid: str, now: float) -> bool:
        return self.muted.get(uid, 0) > now

    def post(self, user: dict, text: object, now: float) -> tuple[dict | None, str]:
        """(message, "") or (None, why not)."""
        if self.is_muted(user["id"], now):
            return None, "You're muted for now."
        t = clean_text(text)
        if not t:
            return None, "Empty message."
        q = self.sent.setdefault(user["id"], deque())
        while q and now - q[0] > CHAT_BURST[1]:
            q.popleft()
        if q and now - q[-1] < CHAT_GAP_S or len(q) >= CHAT_BURST[0]:
            return None, "Slow down a little."
        q.append(now)
        return self.add({"kind": "chat", "uid": user["id"], "name": user.get("name") or "Player",
                         "avatar": user.get("avatar"), "guest": bool(user.get("guest")), "text": t}, now), ""

    def add(self, msg: dict, now: float) -> dict:
        m = {"id": next(self.ids), "ts": now, **msg}
        self.log.append(m)
        return m

    def system(self, text: str, now: float) -> dict:
        return self.add({"kind": "system", "uid": "", "name": "", "avatar": None, "text": clean_text(text, 400)}, now)

    def delete(self, mid: int) -> bool:
        for m in self.log:
            if m["id"] == mid:
                self.log.remove(m)
                return True
        return False

    def mute(self, uid: str, minutes: float, now: float) -> None:
        if minutes <= 0:
            self.muted.pop(uid, None)
        else:
            self.muted[uid] = now + min(minutes, 24 * 60) * 60


# ---- votes -----------------------------------------------------------------------------------------------------------
def _url(u: object) -> str:
    if not (isinstance(u, str) and u.startswith(("http://", "https://")) and len(u) <= 500 and not any(c.isspace() for c in u)):
        raise ValueError("not an http(s) URL")
    return u


def _tile(i: object, top: int = 3) -> int:
    if not isinstance(i, int) or isinstance(i, bool) or not 0 <= i <= top:
        raise ValueError("tile is 1–4")
    return i


def _origin(o: object) -> dict:
    """{"origin": {"guild", "channel"}} when a start came from a Discord channel (the bot posts the link there)."""
    if not isinstance(o, dict):
        return {}
    out = {k: str(o[k]) for k in ("guild", "channel") if isinstance(o.get(k), (str, int)) and not isinstance(o.get(k), bool)
           and str(o[k]).isdigit() and len(str(o[k])) <= 20}
    return {"origin": out} if "channel" in out else {}


def noop(a: dict, wall: dict | None) -> str:
    """Why this action would change nothing on the wall as it is now ("" = it would change something)."""
    w = wall or {}
    on, urls, ov = bool(w.get("running")), w.get("urls") or [], w.get("overlay") or {}
    op = a["op"]
    if op == "start":
        return "It's already mirroring the TV." if on and a.get("mirror") and w.get("mirror") else ""
    if not on:
        return "The wall is off."
    if op == "stop":
        return ""
    if op == "follow":
        return "It's already following the TV." if w.get("mirror") else ""
    if op == "sound":
        return "That tile already has the sound." if a["i"] == w.get("sound") else "There's no such tile." if a["i"] >= len(urls) else ""
    if op == "remove":
        return "There's no such tile." if a["i"] >= len(urls) else ""
    if op == "promote":
        return "That's already the main tile." if a["i"] == 0 else "There's no such tile." if a["i"] >= len(urls) else ""
    if op == "tile":
        if a["i"] < len(urls) and urls[a["i"]] == a["url"]:
            return "That's already on that tile."
        return "That's already on the wall." if a["url"] in urls else ""
    if op == "layout":
        return "That's already the layout." if ov.get("layout") == a["layout"] else ""
    if op == "board":
        same_board = a.get("board", ov.get("board")) == ov.get("board")
        same_vis = a.get("visible", ov.get("visible")) == ov.get("visible")
        return "That's already showing." if same_board and same_vis else ""
    return ""


def check_action(a: object) -> dict:
    """A proposal's action, validated (ValueError otherwise): {"op": start|stop|tile|remove|board|sound|layout|follow, …the
    wall service's body for that call}. Only what the service's control API can do. {"op": "start", "mirror": true} =
    stream the TV's wall and follow it; "follow" = follow the TV again."""
    if not isinstance(a, dict):
        raise ValueError("bad action")
    op = a.get("op")
    label = {"label": clean_text(a["label"], 80)} if isinstance(a.get("label"), str) and a["label"].strip() else {}
    if op == "stop":
        return {"op": "stop"}
    if op == "follow":
        return {"op": "follow"}
    if op == "start":
        origin = _origin(a.get("origin"))
        if a.get("mirror") is True:
            return {"op": "start", "mirror": True, **origin}
        p = a.get("preset")
        if p is not None:
            if isinstance(p, bool) or not (isinstance(p, int) and 0 <= p < 100 or isinstance(p, str) and 0 < len(p) <= 40):
                raise ValueError("start needs a preset")
            return {"op": "start", "preset": p, **label, **origin}
        urls = a.get("urls") or []
        live = a.get("live") or ""
        if not isinstance(urls, list) or len(urls) > 4 or live not in ("", "nfl", "nba") or not (urls or live):
            raise ValueError("start needs a preset, 1–4 URLs, live games, or mirror (same as the TV)")
        out = {"op": "start", "urls": [_url(u) for u in urls]}
        if live:
            out["live"] = live
        if isinstance(a.get("tiles"), int) and not isinstance(a["tiles"], bool):
            out["tiles"] = max(1, min(4, a["tiles"]))
        if a.get("board") in BOARDS:
            out["board"] = a["board"]
        if a.get("layout") in LAYOUTS:
            out["layout"] = a["layout"]
        if isinstance(a.get("sound"), int) and not isinstance(a["sound"], bool):
            out["sound"] = _tile(a["sound"])
        return {**out, **label, **origin}
    if op == "sound":
        return {"op": "sound", "i": _tile(a.get("i")), **label}
    if op == "remove":
        return {"op": "remove", "i": _tile(a.get("i")), **label}
    if op == "promote":   # the big tile is tile 1: move this one there
        return {"op": "promote", "i": _tile(a.get("i")), **label}
    if op == "tile":
        return {"op": "tile", "i": _tile(a.get("i"), 4), "url": _url(a.get("url")), **label}
    if op == "layout":
        if a.get("layout") not in LAYOUTS:
            raise ValueError("layout is lbar or full")
        return {"op": "layout", "layout": a["layout"]}
    if op == "board":
        out = {"op": "board"}
        if "board" in a:
            if a["board"] not in BOARDS:
                raise ValueError("board is nfl, nba or none")
            out["board"] = a["board"]
        if "visible" in a:
            if not isinstance(a["visible"], bool):
                raise ValueError("visible is true/false")
            out["visible"] = a["visible"]
        if "layout" in a:
            if a["layout"] not in LAYOUTS:
                raise ValueError("layout is lbar or full")
            out["layout"] = a["layout"]
        if len(out) == 1:
            raise ValueError("board needs board, visible or layout")
        return out
    raise ValueError("unknown action")


def describe(a: dict) -> str:
    op = a["op"]
    if op == "start":
        if a.get("mirror"):
            return "start the wall: same as the TV"
        if "preset" in a:
            return f"start {a.get('label') or (a['preset'] if isinstance(a['preset'], str) else 'preset #' + str(a['preset'] + 1))}"
        what = f"live {a['live'].upper()} games" if a.get("live") else f"{len(a['urls'])} tiles"
        return f"start {a.get('label') or what}"
    if op == "stop":
        return "stop the wall"
    if op == "follow":
        return "follow the TV again"
    if op == "tile":
        return f"put {a.get('label') or a['url']} on tile {a['i'] + 1}"
    if op == "remove":
        return f"remove tile {a['i'] + 1}"
    if op == "promote":
        return f"make {a.get('label') or 'tile ' + str(a['i'] + 1)} the main tile"
    if op == "sound":
        return f"move the sound to tile {a['i'] + 1}"
    if op == "layout":
        return f"switch the overlay to {'L-bar' if a['layout'] == 'lbar' else 'full'}"
    parts = []
    if "board" in a:
        parts.append(f"show the {a['board'].upper()} board" if a["board"] else "hide the board")
    if a.get("visible") is False and "board" not in a:
        parts.append("hide the board")
    elif a.get("visible") is True and "board" not in a:
        parts.append("show the board")
    if "layout" in a:
        parts.append(f"use the {'L-bar' if a['layout'] == 'lbar' else 'full'} layout")
    return " and ".join(parts)


@dataclass
class Proposal:
    id: int
    uid: str
    name: str
    action: dict
    kind: str              # "screen" (majority of active viewers) | "sound" (N approvals)
    created: float
    expires: float
    yes: set = field(default_factory=set)
    no: set = field(default_factory=set)
    status: str = "open"   # open | passed | failed | expired | override | error

    def public(self, now: float, active: set[str], sound_need: int) -> dict:
        return {"id": self.id, "uid": self.uid, "name": self.name, "action": self.action, "text": describe(self.action),
                "kind": self.kind, "yes": sorted(self.yes), "no": sorted(self.no), "status": self.status,
                "left": max(0.0, round(self.expires - now, 1)), "need": need(self, active, sound_need)}

    def for_bot(self, now: float, active: set[str], sound_need: int) -> dict:
        """The bot's shape: counts, not voter ids; expires as epoch seconds."""
        return {"id": self.id, "kind": self.kind, "summary": describe(self.action), "by": self.name, "action": self.action,
                "yes": len(self.yes), "no": len(self.no), "needed": need(self, active, sound_need), "expires": self.expires,
                "status": self.status, "passed": self.status in ("passed", "override"), "applied": self.status in ("passed", "override")}


def electorate(p: Proposal, active: set[str]) -> set[str]:
    """Who decides: the active viewers, plus the proposer and anyone who voted (a Discord vote counts, away viewers
    who didn't vote don't)."""
    return set(active) | {p.uid} | p.yes | p.no


def need(p: Proposal, active: set[str], sound_need: int) -> int:
    """Yes votes needed to pass."""
    if p.kind == "sound":
        return 1 if len(set(active) | {p.uid}) <= 1 else sound_need
    return len(electorate(p, active)) // 2 + 1   # strict majority: a tie fails


def verdict(p: Proposal, active: set[str], now: float, sound_need: int) -> str:
    """"passed" | "failed" | "expired" | "open"."""
    if p.status != "open":
        return p.status
    n = need(p, active, sound_need)
    if len(p.yes) >= n:
        return "passed"
    voters = electorate(p, active)
    if len(p.yes) + (len(voters) - len(p.yes) - len(p.no)) < n:   # even every undecided yes can't get there
        return "failed"
    return "expired" if now >= p.expires else "open"


class Votes:
    def __init__(self) -> None:
        self.props: dict[int, Proposal] = {}
        self.ids = itertools.count(1)
        self.last: dict[str, float] = {}

    def open(self) -> list[Proposal]:
        return [p for p in self.props.values() if p.status == "open"]

    def propose(self, user: dict, action: dict, now: float, vote_s: float, owner: bool = False) -> tuple[Proposal | None, str]:
        if not owner:
            if any(p.uid == user["id"] for p in self.open()):
                return None, "You already have a proposal open."
            if now - self.last.get(user["id"], -1e9) < PROPOSE_GAP_S:
                return None, f"One proposal every {PROPOSE_GAP_S} s."
        self.last[user["id"]] = now
        p = Proposal(next(self.ids), user["id"], user.get("name") or "Player", action,
                     "sound" if action["op"] == "sound" else "screen", now, now + vote_s, yes={user["id"]})
        if owner:
            p.status = "override"
        self.props[p.id] = p
        while len(self.props) > 50:   # keep a short history
            self.props.pop(next(iter(self.props)))
        return p, ""

    def vote(self, pid: int, uid: str, yes: bool) -> tuple[Proposal | None, str]:
        p = self.props.get(pid)
        if not p or p.status != "open":
            return None, "That vote is over."
        (p.yes if yes else p.no).add(uid)
        (p.no if yes else p.yes).discard(uid)
        return p, ""


# ---- login links (the bot's "Get my link") ---------------------------------------------------------------------------
_link_signer = URLSafeTimedSerializer(auth.SESSION_SECRET, salt="sharplab-wall-link")
_used_links: dict[str, float] = {}
_seq = [0]   # the playlist's last media sequence (a drop = the wall restarted)


def mint_link(discord_id: str, username: str, avatar: str | None) -> str:
    t = _link_signer.dumps({"id": str(discord_id), "username": username or "Player", "avatar": avatar, "n": secrets.token_urlsafe(16)})
    return f"{auth.WEB_BASE_URL}/api/v1/wall/login?t={t}"


def redeem_link(token: str, now: float | None = None) -> dict | None:
    """The member a link was minted for, once (a second use of the same link is refused). Spent links are keyed by their
    signed nonce, not the token text: base64's spare bits give each signature several spellings that verify alike."""
    now = time.time() if now is None else now
    for k, exp in list(_used_links.items()):
        if exp < now:
            del _used_links[k]
    try:
        data = _link_signer.loads(token, max_age=LINK_TTL)
    except (BadSignature, SignatureExpired):
        return None
    n = data.get("n") if isinstance(data, dict) else None
    if not n or n in _used_links:
        return None
    _used_links[n] = now + LINK_TTL + 60
    return data


# ---- demo mode: guests with the keyed link pick a nickname ------------------------------------------------------------
GUEST_COOKIE = "sharplab_wall_guest"
GUEST_TTL = 24 * 3600
GUEST_OPS = {"start", "stop", "tile"}   # what a guest may not propose: no starting/stopping, no arbitrary pages on stream
_guest_signer = URLSafeTimedSerializer(auth.SESSION_SECRET, salt="sharplab-wall-guest")
_NICK = re.compile(r"^[\w .'-]{2,24}$")


def key_ok(k: object) -> bool:
    dk = cfg()["demo_key"]
    return bool(dk) and isinstance(k, str) and secrets.compare_digest(k.encode(), dk.encode())


def _key_tag() -> str:
    """Guest cookies are bound to the current key: rotating WALL_DEMO_KEY (or demo off) ends every guest session."""
    import hashlib
    return hashlib.sha256(cfg()["demo_key"].encode()).hexdigest()[:16]


def clean_nick(name: object) -> str:
    n = clean_text(name, 40)
    if not _NICK.match(n) or not n.strip(" .'-"):
        raise ValueError("A nickname is 2–24 letters, digits, spaces, . ' - or _.")
    return n


def unique_name(name: str, taken: set[str]) -> str:
    """name, or name (guest), or name (guest 2)… — whatever nobody watching is called (case-insensitive)."""
    low = {t.lower() for t in taken}
    if name.lower() not in low:
        return name
    for k in range(1, 100):
        cand = f"{name} (guest{'' if k == 1 else f' {k}'})"
        if cand.lower() not in low:
            return cand
    return f"{name} ({secrets.token_hex(2)})"


def make_guest_cookie(name: str) -> tuple[str, dict]:
    g = {"id": "g-" + secrets.token_hex(6), "name": name, "k": _key_tag()}
    return _guest_signer.dumps(g), g


def read_guest(conn) -> dict | None:
    if not cfg()["demo_key"]:
        return None
    raw = conn.cookies.get(GUEST_COOKIE)
    if not raw:
        return None
    try:
        g = _guest_signer.loads(raw, max_age=GUEST_TTL)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(g, dict) or g.get("k") != _key_tag() or not str(g.get("id", "")).startswith("g-"):
        return None
    return {"id": g["id"], "name": g.get("name") or "Guest", "avatar": None, "guest": True}


def viewer(conn) -> dict | None:
    """A logged-in member (SharpLab session), else in demo mode a guest with a nickname cookie."""
    return session_user(auth.read_session(conn)) or read_guest(conn)


def client_ip(conn) -> str:
    """Behind Caddy (it sets X-Forwarded-For), like the other endpoints."""
    fwd = conn.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (conn.client.host if conn.client else "")


class RateLimit:
    """At most n events per window seconds per key."""

    def __init__(self, n: int, window: float) -> None:
        self.n, self.window, self.hits = n, window, {}

    def allow(self, key: str, now: float) -> bool:
        q = [t for t in self.hits.get(key, []) if now - t < self.window]
        if len(q) >= self.n:
            self.hits[key] = q
            return False
        q.append(now)
        self.hits[key] = q
        if len(self.hits) > 5000:   # bounded: drop the stalest keys
            for k in sorted(self.hits, key=lambda k: self.hits[k][-1] if self.hits[k] else 0)[:1000]:
                self.hits.pop(k, None)
        return True


guest_joins = RateLimit(10, 60)    # guest socket connects per IP
guest_names = RateLimit(5, 300)    # nicknames picked per IP


def watch_url() -> str:
    k = cfg()["demo_key"]
    return f"{auth.WEB_BASE_URL}{PAGE}" + (f"?k={k}" if k else "")


# ---- the wall service (homelab, over Tailscale) ----------------------------------------------------------------------
def _route(a: dict) -> tuple[str, str, dict]:
    """(method, path, body) on the wall service."""
    op = a["op"]
    body = {k: v for k, v in a.items() if k not in ("op", "label")}
    if op == "start":
        return "POST", "/i/discord/start", body
    if op == "stop":
        return "POST", "/i/discord/stop", {}
    if op == "follow":
        return "POST", "/i/discord/follow", {}
    if op == "tile":
        return "POST", "/i/discord/tile", body
    if op == "remove":
        return "DELETE", f"/i/discord/tile/{a['i']}", {}
    if op == "promote":
        return "POST", "/i/discord/move", {"i": a["i"], "j": 0}
    if op == "sound":
        return "POST", "/i/discord/sound", body
    if op == "layout":
        return "POST", "/i/discord/board", {"layout": a["layout"], "visible": True}
    if body.get("board") == "":   # "no board" = hide it
        body = {k: v for k, v in body.items() if k != "board"} | {"visible": False}
    elif "board" in body and "visible" not in body:
        body["visible"] = True
    return "POST", "/i/discord/board", body


async def service_execute(action: dict) -> tuple[bool, str]:
    c = cfg()
    method, path, body = _route(action)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.request(method, c["service"] + path, json=body or None, headers={"Authorization": f"Bearer {c['token']}"})
        if r.status_code >= 400:
            try:
                err = r.json().get("error") or ""
            except ValueError:
                err = r.text
            return False, (err or f"HTTP {r.status_code}")[:200]
        return True, ""
    except httpx.HTTPError as e:
        return False, f"wall service unreachable ({type(e).__name__})"


async def service_state() -> dict | None:
    c = cfg()
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            h = {"Authorization": f"Bearer {c['token']}"}
            st = (await client.get(c["service"] + "/i/discord", headers=h)).json()
            presets = (await client.get(c["service"] + "/presets", headers=h)).json()
        return {**st, "presets": [p.get("name") for p in presets if isinstance(p, dict)]}
    except (httpx.HTTPError, ValueError):
        return None


# ---- the stream's clock -----------------------------------------------------------------------------------------------
def playlist_edge(text: str) -> float | None:
    """The live edge of an HLS playlist in epoch ms: the last segment's EXT-X-PROGRAM-DATE-TIME + its EXTINF."""
    from datetime import datetime
    pdt = dur = None
    edge = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF:"):
            try:
                dur = float(line[8:].split(",")[0])
            except ValueError:
                dur = None
        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            try:
                pdt = datetime.fromisoformat(line[25:].replace("Z", "+00:00")).timestamp() * 1000
            except ValueError:
                pdt = None
        elif line and not line.startswith("#"):
            if pdt is not None and dur is not None:
                edge = pdt + dur * 1000
                pdt += dur * 1000   # a segment without its own tag follows on
            dur = None
    return edge


class EdgeClock:
    """Maps server time to the stream's program-date-time: pdt_at(now) = now + off. The encoder's PDT may be a fixed
    few seconds off the box clock, so every viewer targets the same edge-relative instant instead of "server now − D".
    Each playlist we proxy gives one sample off = edge − seen_at, which is the true offset minus however stale that
    playlist was; the best (largest) sample in the last minute is the estimate. A jump of more than 5 s (the encoder
    restarted) starts over."""

    WINDOW_S = 60

    def __init__(self) -> None:
        self.samples: deque[tuple[float, float]] = deque()
        self.off: float | None = None

    def observe(self, edge_ms: float, now_ms: float) -> bool:
        """True when the estimate moved by more than 100 ms (worth telling the viewers)."""
        val = edge_ms - now_ms
        if self.off is not None and abs(val - self.off) > 5000:
            self.samples.clear()
        self.samples.append((now_ms, val))
        while self.samples and now_ms - self.samples[0][0] > self.WINDOW_S * 1000:
            self.samples.popleft()
        new = max(v for _, v in self.samples)
        moved = self.off is None or abs(new - self.off) > 100
        if moved:
            self.off = new
        return moved


# ---- the hub: sockets + state ----------------------------------------------------------------------------------------
class Hub:
    def __init__(self, clock=time.time, execute=None, fetch_state=None) -> None:
        self.clock = clock
        self.execute = execute or service_execute
        self.fetch_state = fetch_state or service_state
        self.presence = Presence()
        self.chat = Chat()
        self.votes = Votes()
        self.socks: dict[int, tuple[WebSocket, dict]] = {}
        self.cids = itertools.count(1)
        self.wall: dict | None = None
        self._viewers_sig: tuple = ()
        self._seen_notice = False
        self.edge = EdgeClock()
        self._ticker: asyncio.Task | None = None

    # -- roles
    @staticmethod
    def is_owner(uid: str) -> bool:
        return bool(cfg()["owner"]) and uid == cfg()["owner"]

    def is_admin(self, uid: str) -> bool:
        return self.is_owner(uid) or uid in cfg()["admins"]

    # -- broadcast
    async def send(self, ws: WebSocket, msg: dict) -> None:
        try:
            await ws.send_json(msg)
        except Exception:  # noqa: BLE001 — a closing socket; its own loop cleans up
            pass

    async def broadcast(self, msg: dict) -> None:
        for ws, _ in list(self.socks.values()):
            await self.send(ws, msg)

    def viewers(self) -> list[dict]:
        return self.presence.listing(self.clock())

    async def push_viewers(self, force: bool = False) -> None:
        v = self.viewers()
        sig = tuple((x["id"], x["away"]) for x in v)
        if force or sig != self._viewers_sig:
            self._viewers_sig = sig
            await self.broadcast({"t": "viewers", "list": v})

    def pub(self, p: Proposal) -> dict:
        c = cfg()
        return p.public(self.clock(), self.presence.active(self.clock()), c["sound_approvals"])

    def bot_pub(self, p: Proposal) -> dict:
        return p.for_bot(self.clock(), self.presence.active(self.clock()), cfg()["sound_approvals"])

    async def observe_playlist(self, text: str) -> None:
        edge = playlist_edge(text)
        if edge is not None and self.edge.observe(edge, self.clock() * 1000):
            await self.broadcast({"t": "clock", "pdt_off": self.edge.off})

    # -- votes
    async def propose(self, user: dict, action: object) -> tuple[Proposal | None, str]:
        try:
            a = check_action(action)
        except ValueError as e:
            return None, str(e)
        if self.wall and "running" in self.wall and (why := noop(a, self.wall)):   # no votes on changes that change nothing (state unknown: let it through)
            return None, why
        now = self.clock()
        if self.chat.is_muted(user["id"], now):
            return None, "You're muted for now."
        live_tile = a["op"] == "tile" and a["url"] in {g["url"] for g in _live["games"]}
        if user.get("guest") and a["op"] in GUEST_OPS and not live_tile:
            return None, "Guests can vote, chat and change the sound / layout / board — starting, stopping and new pages need a SharpLab login."
        owner = self.is_owner(user["id"])
        p, err = self.votes.propose(user, a, now, cfg()["vote_s"], owner=owner)
        if not p:
            return None, err
        if owner:
            await self.apply(p, f"{p.name} (owner) changed it: {describe(a)}.")
        else:
            await self.say(f"{p.name} proposes to {describe(a)} — vote ✓/✗ on the page ({int(cfg()['vote_s'])} s).", proposal=p.id)
            await self.settle(p)
        return p, ""

    async def vote(self, user: dict, pid: object, yes: object) -> tuple[Proposal | None, str]:
        if not isinstance(pid, int) or not isinstance(yes, bool):
            return None, "bad vote"
        p, err = self.votes.vote(pid, user["id"], yes)
        if p:
            await self.settle(p)
        return p, err

    async def settle(self, p: Proposal) -> None:
        v = verdict(p, self.presence.active(self.clock()), self.clock(), cfg()["sound_approvals"])
        if v == "passed":
            p.status = "passed"
            await self.apply(p, f"Passed ({len(p.yes)} yes): {describe(p.action)}.")
        elif v in ("failed", "expired"):
            p.status = v
            await self.say(f"{'Failed' if v == 'failed' else 'Expired'}: {describe(p.action)} ({len(p.yes)} yes, {len(p.no)} no).")
        await self.broadcast({"t": "proposal", "p": self.pub(p)})

    async def apply(self, p: Proposal, text: str) -> None:
        ok, err = await self.execute(p.action)
        if not ok:
            p.status = "error"
            text = f"Couldn't {describe(p.action)}: {err}"
        await self.say(text)
        await self.broadcast({"t": "proposal", "p": self.pub(p)})
        await self.refresh_wall()

    async def say(self, text: str, **extra) -> None:
        m = self.chat.system(text, self.clock())
        m.update(extra)
        await self.broadcast({"t": "chat", "msg": m})

    async def refresh_wall(self) -> None:
        st = await self.fetch_state()
        if st is not None and st != self.wall:
            old = (self.wall or {}).get("notice") or {}
            note = st.get("notice") or {}
            self.wall = st
            await self.broadcast({"t": "wall", "state": st})
            if self._seen_notice and note.get("text") and (note.get("id"), note.get("at")) != (old.get("id"), old.get("at")):
                await self.say(note["text"])   # "No longer mirroring the TV", "Skipped on the stream: …"
            self._seen_notice = True   # the first state we see: its old notice isn't news

    # -- periodic: expiry, away, wall state
    async def tick(self) -> None:
        for p in self.votes.open():
            await self.settle(p)
        await self.push_viewers()

    def ensure_ticker(self) -> None:
        if self._ticker is None or self._ticker.done():
            self._ticker = asyncio.create_task(self._run())

    async def _run(self) -> None:
        n = 0
        while self.socks:
            await asyncio.sleep(1)
            try:
                await self.tick()
                n += 1
                if n % 5 == 0:
                    await self.refresh_wall()
            except Exception as e:  # noqa: BLE001 — never let the ticker die
                log.warning("wall tick: %s", e)

    # -- chat for the TV wall (bot API): the TV overlay + the TV's phones, through the homelab wall service
    def chat_feed(self, after: int = 0, limit: int = 30) -> dict:
        """The recent chat as a poller sees it: messages newer than `after`, and `ids` = every message still in the window
        (a moderator's delete drops out of it, so the poller drops it too). A muted user's messages are left out while the
        mute lasts. Plus who's watching."""
        now = self.clock()
        window = [m for m in list(self.chat.log)[-max(1, min(limit, 100)):] if not self.chat.is_muted(m.get("uid") or "", now)]
        out = lambda m: {k: m.get(k) for k in ("id", "ts", "kind", "name", "guest", "text")} | {"guest": bool(m.get("guest"))}
        return {"messages": [out(m) for m in window if m["id"] > after], "ids": [m["id"] for m in window],
                "last": self.chat.log[-1]["id"] if self.chat.log else 0, "now": now,
                "viewers": [{"name": v["name"], "guest": bool(v.get("guest")), "away": v["away"]} for v in self.viewers()],
                "watching": len(self.presence.watching()), "active": len(self.presence.active(now))}

    async def post_chat(self, user: dict, text: object) -> tuple[dict | None, str]:
        """A message from outside the page (the TV's phones post as the owner), same limits and mute as the socket."""
        m, err = self.chat.post(user, text, self.clock())
        if m:
            await self.broadcast({"t": "chat", "msg": m})
        return m, err

    def state(self) -> dict:
        now = self.clock()
        active = self.presence.active(now)
        return {"viewers": self.viewers(), "active": len(active), "watching": len(self.presence.watching()),
                "proposals": [self.bot_pub(p) for p in self.votes.open()], "wall": self.wall,
                "watch_url": watch_url()}


hub = Hub()


def reset_hub(**kw) -> Hub:
    global hub
    hub = Hub(**kw)
    return hub


def session_user(sess: dict | None) -> dict | None:
    if not sess or not sess.get("id"):
        return None
    uid = str(sess["id"])
    return {"id": uid, "name": sess.get("username") or "Player", "avatar": auth.avatar_url(uid, sess.get("avatar"))}


# ---- socket ----------------------------------------------------------------------------------------------------------
def origin_ok(origin: str | None) -> bool:
    """The socket rides the session cookie, which every *.djiang.xyz page would send too (same-site): only our own page
    may open it. No Origin = not a browser."""
    return origin is None or origin.rstrip("/") == auth.WEB_BASE_URL.rstrip("/")


async def wall_websocket(ws: WebSocket) -> None:
    if not origin_ok(ws.headers.get("origin")):
        await ws.close(code=4403, reason="bad origin")
        return
    user = viewer(ws)
    if not user:
        await ws.close(code=4401, reason="Log in to SharpLab first")
        return
    h = hub
    if user.get("guest"):
        if not guest_joins.allow(client_ip(ws), h.clock()):
            await ws.close(code=4429, reason="Too many connects — wait a minute")
            return
        others = {u["name"] for uid, u in h.presence.users.items() if uid != user["id"] and uid in h.presence.watching()}
        user = {**user, "name": unique_name(user["name"], others)}
    await ws.accept()
    cid = next(h.cids)
    now = h.clock()
    if h.wall is None:
        await h.refresh_wall()   # before this socket joins the broadcast list: hello carries it
    new = h.presence.join(cid, user, now)
    h.socks[cid] = (ws, user)
    c = cfg()
    await h.send(ws, {"t": "hello", "me": {**user, "initials": initials(user["name"]), "admin": h.is_admin(user["id"]),
                                            "guest": bool(user.get("guest")),
                                            "owner": h.is_owner(user["id"])},
                      "now": now * 1000, "pdt_off": h.edge.off, "history": list(h.chat.log), "viewers": h.viewers(),
                      "proposals": [h.pub(p) for p in h.votes.open()], "wall": h.wall,
                      "cfg": {"delay": c["delay"], "vote_s": c["vote_s"], "sound_approvals": c["sound_approvals"],
                              "chat_max": CHAT_MAX}})
    if new and c["notices"]:
        await h.say(f"{user['name']} joined.")
    await h.push_viewers(force=True)
    h.ensure_ticker()
    try:
        while True:
            msg = await ws.receive_json()
            if not isinstance(msg, dict):
                continue
            await handle(h, cid, ws, user, msg)
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        h.socks.pop(cid, None)
        gone = h.presence.leave(cid)
        if gone and cfg()["notices"]:
            await h.say(f"{user['name']} left.")
        await h.push_viewers()


async def handle(h: Hub, cid: int, ws: WebSocket, user: dict, msg: dict) -> None:
    t, now = msg.get("t"), h.clock()
    if t == "ping":
        await h.send(ws, {"t": "pong", "c": msg.get("c"), "s": now * 1000, "pdt_off": h.edge.off})
        return
    if t == "hb":
        h.presence.beat(cid, bool(msg.get("hidden")), now)
        await h.push_viewers()
        return
    h.presence.beat(cid, False, now)   # anything else they do means they're here
    if t == "chat":
        m, err = h.chat.post(user, msg.get("text"), now)
        if m:
            await h.broadcast({"t": "chat", "msg": m})
        else:
            await h.send(ws, {"t": "error", "text": err})
    elif t == "propose":
        _, err = await h.propose(user, msg.get("action"))
        if err:
            await h.send(ws, {"t": "error", "text": err})
    elif t == "vote":
        _, err = await h.vote(user, msg.get("id"), msg.get("yes"))
        if err:
            await h.send(ws, {"t": "error", "text": err})
    elif t in ("del", "mute"):
        if not h.is_admin(user["id"]):
            await h.send(ws, {"t": "error", "text": "Only David and admins can moderate."})
            return
        if t == "del" and isinstance(msg.get("id"), int) and h.chat.delete(msg["id"]):
            await h.broadcast({"t": "del", "id": msg["id"]})
        elif t == "mute" and isinstance(msg.get("uid"), str):
            mins = msg.get("minutes", 10)
            h.chat.mute(msg["uid"], float(mins) if isinstance(mins, (int, float)) else 10, now)
            who = h.presence.users.get(msg["uid"], {}).get("name", "someone")
            await h.say(f"{who} was muted for {int(mins)} min." if mins and mins > 0 else f"{who} was unmuted.")


# ---- HTTP ------------------------------------------------------------------------------------------------------------
router = APIRouter(prefix="/api/v1/wall", tags=["wall"])
_cache: dict[str, tuple[float, bytes, str]] = {}
_locks: dict[str, asyncio.Lock] = {}


def _need_key(x_api_key: str) -> None:
    if not check_api_key(x_api_key):
        raise HTTPException(401, "Invalid API key")


_live: dict = {"at": 0.0, "games": []}


async def live_games() -> list[dict]:
    """Live NBA + NFL games with their stream pages (the wall service's /live/*), cached 20 s."""
    if time.monotonic() - _live["at"] < 20:
        return _live["games"]
    c = cfg()
    out = []
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            for lg in ("nfl", "nba"):
                r = await client.get(f"{c['service']}/live/{lg}", headers={"Authorization": f"Bearer {c['token']}"})
                if r.status_code == 200:
                    out += [{"league": lg, "label": str(g.get("label", ""))[:60], "url": g["url"], "state": g.get("state", "")}
                            for g in r.json() if isinstance(g, dict) and isinstance(g.get("url"), str) and g["url"].startswith("https://")]
    except httpx.HTTPError:
        return _live["games"]
    _live.update(at=time.monotonic(), games=out)
    return out


@router.get("/games")
async def games(request: Request):
    if not viewer(request):
        return JSONResponse({"error": "log in"}, status_code=401)
    return {"games": await live_games()}


@router.get("/me")
async def me(request: Request, k: str = ""):
    user = viewer(request)
    if not user:
        return JSONResponse({"authenticated": False, "login": f"/api/v1/auth/discord/login?next={PAGE}",
                             "guest_ok": key_ok(k)}, status_code=401)
    return {"authenticated": True, **user}


@router.post("/guest")
async def guest(request: Request):
    """Demo mode: someone with the keyed link picks a nickname → a signed guest cookie (24 h)."""
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or not key_ok(body.get("k")):
        return JSONResponse({"error": "This link isn't valid — log in with Discord instead."}, status_code=403)
    if not guest_names.allow(client_ip(request), time.time()):
        return JSONResponse({"error": "Too many nicknames from here — wait a few minutes."}, status_code=429)
    try:
        name = clean_nick(body.get("name"))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    taken = {hub.presence.users[u]["name"] for u in hub.presence.watching() if u in hub.presence.users}
    name = unique_name(name, taken)
    raw, g = make_guest_cookie(name)
    resp = JSONResponse({"id": g["id"], "name": name, "guest": True})
    resp.set_cookie(GUEST_COOKIE, raw, max_age=GUEST_TTL, httponly=True, secure=True, samesite="lax")
    return resp


@router.get("/login")
async def login(t: str = ""):
    """A bot-minted link: sets the normal SharpLab session cookie for that member, then the watch page."""
    data = redeem_link(t)
    if not data:
        return RedirectResponse(f"{auth.WEB_BASE_URL}{PAGE}?error=link_expired")
    resp = RedirectResponse(f"{auth.WEB_BASE_URL}{PAGE}")
    resp.set_cookie(auth.COOKIE_NAME, auth.make_session_cookie(data), max_age=auth.SESSION_TTL,
                    httponly=True, secure=True, samesite="lax")
    return resp


async def fetch_upstream(name: str) -> tuple[int, bytes, str]:
    c = cfg()
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{c['service']}/hls/discord/{name}", headers={"Authorization": f"Bearer {c['token']}"})
    return r.status_code, r.content, r.headers.get("content-type", "")


@router.get("/hls/{name}")
async def hls(name: str, request: Request):
    if not viewer(request):
        return JSONResponse({"error": "log in"}, status_code=401)
    if not _HLS_NAME.match(name):
        return JSONResponse({"error": "no such file"}, status_code=404)
    playlist = name.endswith(".m3u8")
    ctype = "application/vnd.apple.mpegurl" if playlist else "video/mp2t"
    now = time.monotonic()
    hit = _cache.get(name)
    if not hit or hit[0] < now:
        async with _locks.setdefault(name, asyncio.Lock()):
            hit = _cache.get(name)
            if not hit or hit[0] < time.monotonic():
                try:
                    code, body, _ = await fetch_upstream(name)
                except httpx.HTTPError:
                    return JSONResponse({"error": "the wall isn't streaming"}, status_code=502)
                if code != 200:
                    return JSONResponse({"error": "the wall isn't streaming"}, status_code=404 if code == 404 else 502)
                hit = (time.monotonic() + (0.5 if playlist else 60), body, ctype)
                if playlist:
                    text = body.decode("utf-8", "replace")
                    seq = re.search(r"#EXT-X-MEDIA-SEQUENCE:(\d+)", text)
                    if seq and int(seq.group(1)) < _seq[0]:   # the wall restarted: same segment names, new video
                        for k in [k for k in _cache if k != name]:
                            _cache.pop(k, None)
                    _seq[0] = int(seq.group(1)) if seq else 0
                    await hub.observe_playlist(text)
                _cache[name] = hit
                for k, v in list(_cache.items()):   # expired segments go now: ~0.5 MB each, on a 2 GB VPS
                    if v[0] < time.monotonic():
                        _cache.pop(k, None)
                        _locks.pop(k, None)
    return Response(hit[1], media_type=ctype, headers={"Cache-Control": "no-store"})   # names repeat across wall restarts


# ---- bot API (X-Api-Key = WEB_API_SECRET) ----------------------------------------------------------------------------
@router.get("/state")
async def bot_state(x_api_key: str = Header("")):
    _need_key(x_api_key)
    await hub.tick()   # settle proposals that expired while nobody had the page open
    if hub.wall is None:
        await hub.refresh_wall()
    return hub.state()


def _bot_user(body: dict) -> dict:
    uid = str(body.get("discord_id") or "")
    if not uid.isdigit():
        raise HTTPException(422, "discord_id is required")
    name = body.get("name") or body.get("display_name") or body.get("username") or "Player"
    avatar = body.get("avatar_url") or body.get("avatar")
    return {"id": uid, "name": clean_text(name, 40) or "Player",
            "avatar": avatar if isinstance(avatar, str) and avatar.startswith("https://") else None}


@router.post("/propose")
async def bot_propose(request: Request, x_api_key: str = Header("")):
    _need_key(x_api_key)
    body = await request.json()
    p, err = await hub.propose(_bot_user(body), body.get("action"))
    if not p:
        return JSONResponse({"error": err}, status_code=409)
    return hub.bot_pub(p)


@router.post("/vote")
async def bot_vote(request: Request, x_api_key: str = Header("")):
    _need_key(x_api_key)
    body = await request.json()
    p, err = await hub.vote(_bot_user(body), body.get("proposal_id"), body.get("yes"))
    if not p:
        return JSONResponse({"error": err}, status_code=409)
    return hub.bot_pub(p)


@router.post("/link")
async def bot_link(request: Request, x_api_key: str = Header("")):
    _need_key(x_api_key)
    body = await request.json()
    uid = str(body.get("discord_id") or "")
    if not uid.isdigit():
        return JSONResponse({"error": "discord_id is required"}, status_code=422)
    name = body.get("username") or body.get("name") or body.get("display_name") or "Player"
    avatar = body.get("avatar")   # the Discord avatar hash, as the OAuth session stores it
    return {"url": mint_link(uid, clean_text(name, 40) or "Player", avatar if isinstance(avatar, str) and len(avatar) < 80 else None),
            "ttl": LINK_TTL}


@router.get("/chat")
async def bot_chat(after: int = 0, limit: int = 30, x_api_key: str = Header("")):
    """The TV wall's chat poll (the homelab wall service, every ~1.5 s while the discord wall runs)."""
    _need_key(x_api_key)
    return hub.chat_feed(after, limit)


@router.post("/chat")
async def bot_chat_post(request: Request, x_api_key: str = Header("")):
    """Chat from the TV's phone remote: {discord_id (the owner's), name, text}."""
    _need_key(x_api_key)
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=422)
    user = _bot_user(body)
    known = hub.presence.users.get(user["id"])
    if known:   # watching on the page too: the same name + avatar as there
        user = {**user, "name": known["name"], "avatar": known.get("avatar")}
    m, err = await hub.post_chat(user, body.get("text"))
    if not m:
        return JSONResponse({"error": err}, status_code=403 if "muted" in err else 429 if "Slow" in err else 422)
    return {"msg": {k: m.get(k) for k in ("id", "ts", "kind", "name", "guest", "text")}}
