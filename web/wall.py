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
_HLS_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}\.(m3u8|ts)$")


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
        self.users[user["id"]] = {"id": user["id"], "name": user.get("name") or "Player", "avatar": user.get("avatar")}
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
                         "avatar": user.get("avatar"), "text": t}, now), ""

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
def check_action(a: object) -> dict:
    """A proposal's action, validated (ValueError otherwise). Only what the wall service's control API can do."""
    if not isinstance(a, dict):
        raise ValueError("bad action")
    op = a.get("op")
    if op == "stop":
        return {"op": "stop"}
    if op == "start":
        p = a.get("preset")
        if isinstance(p, bool) or not (isinstance(p, int) and 0 <= p < 100 or isinstance(p, str) and 0 < len(p) <= 40):
            raise ValueError("start needs a preset")
        return {"op": "start", "preset": p}
    if op in ("tile", "sound"):
        i = a.get("i")
        if not isinstance(i, int) or isinstance(i, bool) or not 0 <= i <= 3:
            raise ValueError("tile is 1–4")
        if op == "sound":
            return {"op": "sound", "i": i}
        u = a.get("url")
        if not (isinstance(u, str) and u.startswith(("http://", "https://")) and len(u) <= 500 and not any(c.isspace() for c in u)):
            raise ValueError("not an http(s) URL")
        return {"op": "tile", "i": i, "url": u, **({"label": clean_text(a["label"], 60)} if a.get("label") else {})}
    if op == "layout":
        if a.get("layout") not in LAYOUTS:
            raise ValueError("layout is lbar or full")
        return {"op": "layout", "layout": a["layout"]}
    if op == "board":
        if a.get("board") not in BOARDS:
            raise ValueError("board is nfl, nba or none")
        return {"op": "board", "board": a["board"]}
    raise ValueError("unknown action")


def describe(a: dict) -> str:
    op = a["op"]
    if op == "start":
        return f"start preset {a['preset'] if isinstance(a['preset'], str) else '#' + str(a['preset'] + 1)}"
    if op == "stop":
        return "stop the wall"
    if op == "tile":
        return f"put {a.get('label') or a['url']} on tile {a['i'] + 1}"
    if op == "sound":
        return f"move the sound to tile {a['i'] + 1}"
    if op == "layout":
        return f"switch the overlay to {'L-bar' if a['layout'] == 'lbar' else 'full'}"
    return f"show the {a['board'].upper()} board" if a["board"] else "hide the board"


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


def mint_link(discord_id: str, username: str, avatar: str | None) -> str:
    t = _link_signer.dumps({"id": str(discord_id), "username": username or "Player", "avatar": avatar, "n": secrets.token_hex(4)})
    return f"{auth.WEB_BASE_URL}/api/v1/wall/login?t={t}"


def redeem_link(token: str, now: float | None = None) -> dict | None:
    """The member a link was minted for, once (a second use of the same link is refused)."""
    now = time.time() if now is None else now
    for k, exp in list(_used_links.items()):
        if exp < now:
            del _used_links[k]
    if token in _used_links:
        return None
    try:
        data = _link_signer.loads(token, max_age=LINK_TTL)
    except (BadSignature, SignatureExpired):
        return None
    _used_links[token] = now + LINK_TTL
    return data


# ---- the wall service (homelab, over Tailscale) ----------------------------------------------------------------------
def _route(a: dict) -> tuple[str, dict]:
    op = a["op"]
    if op == "start":
        return "/i/discord/start", {"preset": a["preset"]}
    if op == "stop":
        return "/i/discord/stop", {}
    if op == "tile":
        return "/i/discord/tile", {"i": a["i"], "url": a["url"]}
    if op == "sound":
        return "/i/discord/sound", {"i": a["i"]}
    if op == "layout":
        return "/i/discord/board", {"layout": a["layout"], "visible": True}
    return "/i/discord/board", ({"board": a["board"], "visible": True} if a["board"] else {"visible": False})


async def service_execute(action: dict) -> tuple[bool, str]:
    c = cfg()
    path, body = _route(action)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(c["service"] + path, json=body, headers={"Authorization": f"Bearer {c['token']}"})
        if r.status_code >= 400:
            return False, (r.json().get("error") if r.headers.get("content-type", "").startswith("application/json") else r.text)[:200] or str(r.status_code)
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

    # -- votes
    async def propose(self, user: dict, action: object) -> tuple[Proposal | None, str]:
        try:
            a = check_action(action)
        except ValueError as e:
            return None, str(e)
        now = self.clock()
        if self.chat.is_muted(user["id"], now):
            return None, "You're muted for now."
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
            self.wall = st
            await self.broadcast({"t": "wall", "state": st})

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

    def state(self) -> dict:
        now = self.clock()
        active = self.presence.active(now)
        return {"viewers": self.viewers(), "active": len(active), "watching": len(self.presence.watching()),
                "proposals": [self.pub(p) for p in self.votes.open()], "wall": self.wall,
                "watch_url": f"{auth.WEB_BASE_URL}{PAGE}"}


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
async def wall_websocket(ws: WebSocket) -> None:
    user = session_user(auth.read_session(ws))
    if not user:
        await ws.close(code=4401, reason="Log in to SharpLab first")
        return
    h = hub
    await ws.accept()
    cid = next(h.cids)
    now = h.clock()
    new = h.presence.join(cid, user, now)
    h.socks[cid] = (ws, user)
    c = cfg()
    if h.wall is None:
        await h.refresh_wall()
    await h.send(ws, {"t": "hello", "me": {**user, "initials": initials(user["name"]), "admin": h.is_admin(user["id"]),
                                            "owner": h.is_owner(user["id"])},
                      "now": now * 1000, "history": list(h.chat.log), "viewers": h.viewers(),
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
        await h.send(ws, {"t": "pong", "c": msg.get("c"), "s": now * 1000})
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


@router.get("/me")
async def me(request: Request):
    user = session_user(auth.read_session(request))
    if not user:
        return JSONResponse({"authenticated": False, "login": f"/api/v1/auth/discord/login?next={PAGE}"}, status_code=401)
    return {"authenticated": True, **user}


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
    if not session_user(auth.read_session(request)):
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
                _cache[name] = hit
                for k, v in list(_cache.items()):
                    if v[0] < time.monotonic() - 120:
                        _cache.pop(k, None)
    return Response(hit[1], media_type=ctype, headers={"Cache-Control": "no-cache" if playlist else "private, max-age=60"})


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
    return {"id": uid, "name": clean_text(body.get("name") or "Player", 40), "avatar": body.get("avatar_url")}


@router.post("/propose")
async def bot_propose(request: Request, x_api_key: str = Header("")):
    _need_key(x_api_key)
    body = await request.json()
    p, err = await hub.propose(_bot_user(body), body.get("action"))
    if not p:
        return JSONResponse({"error": err}, status_code=409)
    return hub.pub(p)


@router.post("/vote")
async def bot_vote(request: Request, x_api_key: str = Header("")):
    _need_key(x_api_key)
    body = await request.json()
    p, err = await hub.vote(_bot_user(body), body.get("proposal_id"), body.get("yes"))
    if not p:
        return JSONResponse({"error": err}, status_code=409)
    return hub.pub(p)


@router.post("/link")
async def bot_link(request: Request, x_api_key: str = Header("")):
    _need_key(x_api_key)
    body = await request.json()
    uid = str(body.get("discord_id") or "")
    if not uid.isdigit():
        return JSONResponse({"error": "discord_id is required"}, status_code=422)
    return {"url": mint_link(uid, clean_text(body.get("username") or "Player", 40), body.get("avatar")), "ttl": LINK_TTL}
