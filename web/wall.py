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
import json
import logging
import math
import os
from urllib.parse import quote
from pathlib import Path
import re
import secrets
import time
from collections import deque
from dataclasses import dataclass, field

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from web import auth
from wall import spectrum
from web._apisec import check_api_key
from web import wall_jev
from web.wall_guard import Guard

log = logging.getLogger(__name__)

PAGE = "/wall.html"
AWAY_S = 60            # no heartbeat for this long (or a hidden tab) = away
CHAT_MAX = 300
SCROLLBACK = 200
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
        "no_votes": e("WALL_NO_VOTES", "0") == "1",   # election night: the owner drives, others ask DJ Jev
        # demo mode: anyone with the keyed link (/wall.html?k=WALL_DEMO_KEY) joins under a nickname as a "friend" (a
        # member's powers on this page). Public mode: the plain page is data-only for anyone (map, results, the live feed,
        # chat read-only) — no stream, no votes, no Kalshi. WALL_GUEST_OPEN=1 is its old name and still turns it on.
        "public": e("WALL_PUBLIC", "0") == "1" or e("WALL_GUEST_OPEN", "0") == "1",
        "demo_key": e("WALL_DEMO_KEY", "") if e("WALL_DEMO", "0") == "1" and len(e("WALL_DEMO_KEY", "")) >= 4 else "",
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
    """The watch-party chat. With a `path` (WALL_CHAT_LOG) every message and delete is appended there as JSON lines — the
    whole night's log on disk — and a restart reloads the last SCROLLBACK, so the chat never starts over."""

    def __init__(self, guard: Guard | None = None, path: str | None = None) -> None:
        self.log: deque[dict] = deque(maxlen=SCROLLBACK)
        self.path = Path(path) if path else None
        self.ids = itertools.count(self._load() + 1)
        self.guard = guard or Guard()   # the per-sender token bucket + duplicate drop (web/wall_guard.py)
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
        if why := self.guard.chat_ok("u:" + user["id"], t, now):
            return None, why
        return self.add({"kind": "chat", "uid": user["id"], "name": user.get("name") or "Player",
                         "avatar": user.get("avatar"), "guest": bool(user.get("guest")), "text": t}, now), ""

    def _load(self) -> int:
        """Last SCROLLBACK messages (deletes applied) from the log; the highest id seen (ids keep counting up)."""
        if not self.path or not self.path.exists():
            return 0
        msgs: dict[int, dict] = {}
        top = 0
        try:
            with self.path.open() as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if "del" in r:
                        msgs.pop(r["del"], None)
                    elif isinstance(r.get("id"), int):
                        msgs[r["id"]] = r
                        top = max(top, r["id"])
                        if len(msgs) > 4 * SCROLLBACK:   # bounded memory on a long log
                            for k in sorted(msgs)[:-2 * SCROLLBACK]:
                                del msgs[k]
        except OSError as e:
            log.warning("chat log: %s", e)
        self.log.extend(msgs[k] for k in sorted(msgs)[-SCROLLBACK:])
        return top

    def _write(self, rec: dict) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps(rec, separators=(",", ":")) + "\n")
        except OSError as e:
            log.warning("chat log: %s", e)

    def add(self, msg: dict, now: float) -> dict:
        m = {"id": next(self.ids), "ts": now, **msg}
        self.log.append(m)
        if not m.get("private"):
            self._write(m)
        return m

    def system(self, text: str, now: float) -> dict:
        return self.add({"kind": "system", "uid": "", "name": "", "avatar": None, "text": clean_text(text, 400)}, now)

    def delete(self, mid: int) -> bool:
        for m in self.log:
            if m["id"] == mid:
                self.log.remove(m)
                self._write({"del": mid})
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
    if op == "tile" and a["i"] < 2 and "/p/election/" not in a["url"] and \
            ((ov.get("board") == "election") or any("/p/election/" in u for u in urls)):
        return "Tiles 1–2 are the election dashboards — put streams on tiles 3–4."
    if op == "tile":
        if a["i"] < len(urls) and urls[a["i"]] == a["url"]:
            return "That's already on that tile."
        if a["url"] in urls:
            return "That's already on the wall."
        # a Spectrum stream only while it's on air (election mode: the state lists the lineup with its labels)
        return spectrum.gate(a["url"], w["spectrum"].get(a["url"])) if isinstance(w.get("spectrum"), dict) else ""
    if op == "rotate":
        return "" if any(r.get("i") == a["i"] for r in w.get("rotate") or []) else "That tile doesn't rotate."
    if op == "adwatch":
        return f"Auto ad-skip is already {'on' if a['on'] else 'off'}." if (w.get("adwatch") or {}).get("on") == a["on"] else ""
    if op == "jevswap":
        return "" if a["i"] < len(urls) and "/p/" not in urls[a["i"]] else "That isn't a stream tile."
    if op == "director":
        return f"The auto-director is already {'on' if a['on'] else 'off'}." \
            if (w.get("stream_director") or {}).get("on") == a["on"] else ""
    if op == "tvchat":
        return f"The TV chat is already {'on' if a['on'] else 'off'}." if (w.get("tvchat") or {}).get("on") == a["on"] else ""
    if op == "favorite":
        starred = a["url"] in (w.get("favorites") or [])
        return ("That's already a favorite." if a["on"] else "That isn't a favorite.") if starred == a["on"] else ""
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
    if op == "default":   # election night: the default screens back on every tile
        return {"op": "default"}
    if op == "adwatch":   # election night: auto ad-skip (sound off commercials, break tiles to on-air streams) on / off
        if not isinstance(a.get("on"), bool):
            raise ValueError("on is true/false")
        return {"op": "adwatch", "on": a["on"]}
    if op == "jevswap":   # "DJ Jev, put something else on this tile" (wall/server.py /jevswap)
        return {"op": "jevswap", "i": _tile(a.get("i"))}
    if op == "director":   # election night: Jev runs the stream tiles 3–4 + the sound (wall/streamdir.py) on / off
        if not isinstance(a.get("on"), bool):
            raise ValueError("on is true/false")
        return {"op": "director", "on": a["on"]}
    if op == "tvchat":   # the chat floating over the TV's tiles (wall/qml/ChatFloat.qml; the stream shows it too) on / off
        if not isinstance(a.get("on"), bool):
            raise ValueError("on is true/false")
        return {"op": "tvchat", "on": a["on"]}
    if op == "favorite":   # star a stream (two at most): its tile goes back to it after a break; not up yet → tile 3 or 4
        if not isinstance(a.get("on"), bool):
            raise ValueError("on is true/false")
        return {"op": "favorite", "url": _url(a.get("url")), "on": a["on"], **label}
    if op == "rotate":   # election data tiles: hold this screen / the next one now (either holds 5 min, then rotates again)
        if a.get("act") not in ("hold", "next"):
            raise ValueError("act is hold or next")
        return {"op": "rotate", "i": _tile(a.get("i")), "act": a["act"]}
    if op == "layout":
        if a.get("layout") not in LAYOUTS:
            raise ValueError("layout is lbar or full")
        return {"op": "layout", "layout": a["layout"]}
    if op == "spotlight":   # election night: a state's races + Kalshi prices on the TV's sidebar for ~30 s ("" = dismiss)
        st, race = a.get("state"), a.get("race", "")
        if not isinstance(st, str) or not (st == "" or re.fullmatch(r"[A-Z]{2}", st)):
            raise ValueError("state is a two-letter code")
        if race and not (isinstance(race, str) and re.fullmatch(rf"[SGH]-{st}(-\d{{1,2}})?(-special)?", race)):
            raise ValueError("race is one of that state's race ids (S-GA, H-WI-3)")
        return {"op": "spotlight", "state": st, **({"race": race, **label} if race else {})}
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
    if op == "default":
        return "put the default election screens back"
    if op == "adwatch":
        return f"turn auto ad-skip {'on' if a['on'] else 'off'}"
    if op == "jevswap":
        return f"ask DJ Jev to swap tile {a['i'] + 1}"
    if op == "director":
        return f"turn the auto-director {'on' if a['on'] else 'off'}"
    if op == "tvchat":
        return f"turn the TV chat {'on' if a['on'] else 'off'}"
    if op == "favorite":
        return f"{'star' if a['on'] else 'unstar'} {a.get('label') or a['url']}"
    if op == "rotate":
        return f"hold tile {a['i'] + 1} on this screen for 5 min" if a["act"] == "hold" else f"switch tile {a['i'] + 1} to its next screen"
    if op == "spotlight":
        if a.get("race"):
            return f"spotlight {a.get('label') or a['race']} on the TV"
        return f"spotlight {a['state']} on the TV" if a["state"] else "close the TV's spotlight"
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
_guest_signer = URLSafeTimedSerializer(auth.SESSION_SECRET, salt="sharplab-wall-guest")
_NICK = re.compile(r"^[\w .'-]{2,24}$")


def key_ok(k: object) -> bool:
    """Friends may join: only with the keyed link (the plain page is the public, data-only view)."""
    dk = cfg()["demo_key"]
    return bool(dk) and isinstance(k, str) and secrets.compare_digest(k.encode(), dk.encode())


def public_mode() -> bool:
    return cfg()["public"]


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
    g = {"id": "g-" + secrets.token_hex(6), "name": name, "k": _key_tag(), "t": "friend"}
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
    # only friend cookies (the keyed link): one from before tiers, or from the old open plain page, is a public viewer
    if not isinstance(g, dict) or g.get("k") != _key_tag() or not str(g.get("id", "")).startswith("g-") or g.get("t") != "friend":
        return None
    return {"id": g["id"], "name": g.get("name") or "Guest", "avatar": None, "guest": True, "tier": "friend"}


def verified(user: dict) -> bool:
    """A Discord member, or a friend-tier guest (a link the owner handed out) — never a public / nickname-only viewer."""
    return not user.get("guest") or user.get("tier") == "friend"


def viewer(conn) -> dict | None:
    """Who may use the whole page: a logged-in member (SharpLab session), else in demo mode a friend (the keyed link's
    nickname cookie) — a member's powers on this page. None = a public viewer (data only, when public_mode())."""
    return session_user(auth.read_session(conn)) or read_guest(conn)


PUBLIC = {"id": "", "name": "", "avatar": None, "public": True}   # a public viewer, where a read needs a "who"


def tier(user: dict | None) -> str:
    return "public" if not user else "friend" if user.get("guest") else "member"


def client_ip(conn) -> str:
    """Behind Caddy (it sets X-Forwarded-For), like the other endpoints."""
    fwd = conn.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (conn.client.host if conn.client else "")


class RateLimit:
    """At most n events per window seconds per key."""

    def __init__(self, n: int, window: float) -> None:
        self.n, self.window, self.hits = n, window, {}

    def wait(self, key: str, now: float) -> int:
        """Seconds until key may go again (0 = now): for Retry-After."""
        q = [t for t in self.hits.get(key, []) if now - t < self.window]
        return 0 if len(q) < self.n else max(1, int(q[0] + self.window - now + 0.999))

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


# Guests share IPs (one apartment wifi, a dorm, carrier NAT): the per-IP limits are loose and only stop a flood; the tight
# ones are per browser (the guest cookie's id for sockets, a browser-token cookie for nicknames).
guest_joins = RateLimit(20, 60)     # guest socket connects per guest
guest_joins_ip = RateLimit(240, 60) # … and per IP
guest_names = RateLimit(8, 300)     # new nicknames per browser
guest_names_ip = RateLimit(60, 300) # … and per IP
GUEST_BROWSER = "sharplab_wall_gb"


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
    if op == "default":
        return "POST", "/i/discord/default", {}
    if op == "adwatch":
        return "POST", "/i/discord/adwatch", {"on": a["on"]}
    if op == "jevswap":
        return "POST", "/i/discord/jevswap", {"i": a["i"]}
    if op == "director":
        return "POST", "/i/discord/director", {"on": a["on"]}
    if op == "tvchat":
        return "POST", "/i/discord/tvchat", {"on": a["on"]}
    if op == "favorite":
        return "POST", "/i/discord/favorite", {"url": a["url"], "on": a["on"]}
    if op == "rotate":
        return "POST", "/i/discord/rotate", body
    if op == "spotlight":
        return "POST", "/i/discord/spotlight", {"state": a["state"], **({"race": a["race"]} if a.get("race") else {})}
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


async def _ask_jev(state: str, questions: dict) -> dict | None:
    from web import wall_election   # it imports this module
    return await wall_election._jev(state, questions, timeout=wall_jev.TIMEOUT_S)


# ---- the hub: sockets + state ----------------------------------------------------------------------------------------
class Hub:
    def __init__(self, clock=time.time, execute=None, fetch_state=None, ask_jev=None) -> None:
        self.clock = clock
        self.ask_jev = ask_jev or _ask_jev   # chat commands to Jev ("jev show the senate map"); tests stub it
        self.jev_user, self.jev_all = RateLimit(1, 10), RateLimit(6, 60)
        self._jev_tasks: set[asyncio.Task] = set()
        self.execute = execute or service_execute
        self.fetch_state = fetch_state or service_state
        self.presence = Presence()
        self.guard = Guard()
        self.chat = Chat(self.guard, os.environ.get("WALL_CHAT_LOG") or None)   # the full log on disk; reloaded on restart
        self._alert_ids = itertools.count(-1, -1)
        self.votes = Votes()
        self.socks: dict[int, tuple[WebSocket, dict]] = {}
        self.kicked: dict[str, float] = {}   # uid or "ip:<addr>" → until (the owner's kick)
        self.pubs: dict[int, WebSocket] = {}   # public viewers: chat read-only + a head count, nothing else
        self._count_sig: tuple = ()
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

    # -- the abuse watcher: a refusal counts; enough of them = a timeout, and the owner + admins hear about it
    async def strike(self, ident: str, reason: str, ip: str = "", name: str = "", uid: str = "") -> None:
        t = self.guard.strike(ident, self.clock(), reason, ip, name, exempt=bool(uid) and self.is_admin(uid))
        if t:
            await self.alert(f"⏱ {name or ident} is in a {round((t['until'] - self.clock()) / 60)}-min timeout ({reason}; "
                             f"{ident}{', IP ' + ip if ip else ''}). Lift it: /lift {ident}")

    async def alert(self, text: str) -> None:
        """A system line only the owner and admins see (not in the scrollback, not on the TV)."""
        m = {"id": next(self._alert_ids), "ts": self.clock(), "kind": "system", "uid": "", "name": "", "avatar": None,
             "text": clean_text(text, 400), "private": True}
        for ws, u in list(self.socks.values()):
            if self.is_admin(u["id"]):
                await self.send(ws, {"t": "chat", "msg": m})

    def timeout_lines(self) -> list[str]:
        now = self.clock()
        return [f"⏱ {t['name'] or k}: {math.ceil(left / 60)} min left ({t['reason']}). /lift {k}"
                for k, t in list(self.guard.timeouts.items()) if (left := self.guard.timed_out(k, now))]

    # -- broadcast
    async def send(self, ws: WebSocket, msg: dict) -> None:
        try:
            await ws.send_json(msg)
        except Exception:  # noqa: BLE001 — a closing socket; its own loop cleans up
            pass

    async def broadcast(self, msg: dict) -> None:
        for ws, _ in list(self.socks.values()):
            await self.send(ws, msg)
        if self.pubs and (pm := public_msg(msg)) is not None:
            for ws in list(self.pubs.values()):
                await self.send(ws, pm)

    def counts(self) -> dict:
        return {"t": "count", "watching": len(self.presence.watching()), "active": len(self.presence.active(self.clock())),
                "public": len(self.pubs)}

    async def push_counts(self, force: bool = False) -> None:
        c = self.counts()
        sig = (c["watching"], c["active"], c["public"])
        if force or sig != self._count_sig:
            self._count_sig = sig
            for ws in list(self.pubs.values()):
                await self.send(ws, c)
            for ws, _ in list(self.socks.values()):   # the party sees how many follow along on the public link
                await self.send(ws, c)

    def viewers(self) -> list[dict]:
        return self.presence.listing(self.clock())

    async def push_viewers(self, force: bool = False) -> None:
        v = self.viewers()
        sig = tuple((x["id"], x["away"]) for x in v)
        if force or sig != self._viewers_sig:
            self._viewers_sig = sig
            for ws, _ in list(self.socks.values()):   # members + friends only: public viewers get a count (push_counts)
                await self.send(ws, {"t": "viewers", "list": v})
        await self.push_counts()

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
    async def propose(self, user: dict, action: object, via_jev: bool = False) -> tuple[Proposal | None, str]:
        try:
            a = check_action(action)
        except ValueError as e:
            return None, str(e)
        key = (user["id"], "propose", json.dumps(a, sort_keys=True))
        if (same := self.guard.seen(key, self.clock())) is not None:   # a double tap / resend: the first one stands
            return same
        res = await self._propose(user, a, via_jev)
        self.guard.remember(key, res, self.clock())
        return res

    async def _propose(self, user: dict, a: dict, via_jev: bool = False) -> tuple[Proposal | None, str]:
        # WALL_NO_VOTES=1: no votes — the owner (and admins) change the wall directly; everyone else through DJ Jev in chat
        nv = cfg()["no_votes"]
        if nv and not via_jev and not self.is_admin(user["id"]):
            return None, "Ask DJ Jev in chat to change the wall — e.g. “jev put abc on” or “jev show the senate map”."
        if a["op"] == "tile" and spectrum.find(a["url"]) and not verified(user):
            return None, "The Spectrum streams are for verified viewers — log in with Discord."
        if self.wall and "running" in self.wall and (why := noop(a, self.wall)):   # no votes on changes that change nothing (state unknown: let it through)
            return None, why
        now = self.clock()
        if self.chat.is_muted(user["id"], now):
            return None, "You're muted for now."
        if a["op"] == "adwatch" and not self.is_admin(user["id"]):
            return None, "Only the owner and admins can switch auto ad-skip."
        if a["op"] == "director" and not self.is_admin(user["id"]):
            return None, "Only the owner and admins can switch the auto-director."
        if a["op"] == "tvchat" and not self.is_admin(user["id"]):
            return None, "Only the owner and admins can switch the TV chat."
        owner = self.is_owner(user["id"]) or (nv and (self.is_admin(user["id"]) or via_jev))   # Jev's picks apply directly too
        p, err = self.votes.propose(user, a, now, cfg()["vote_s"], owner=owner)
        if not p:
            return None, err
        if owner:
            await self.apply(p, f"{p.name} via DJ Jev: {describe(a)}." if via_jev and not self.is_admin(user["id"])
                             else f"{p.name} (owner) changed it: {describe(a)}.")
        else:
            await self.say(f"{p.name} proposes to {describe(a)} — vote ✓/✗ on the page ({int(cfg()['vote_s'])} s).", proposal=p.id)
            await self.settle(p)
        return p, ""

    async def vote(self, user: dict, pid: object, yes: object) -> tuple[Proposal | None, str]:
        if not isinstance(pid, int) or not isinstance(yes, bool):
            return None, "bad vote"
        key = (user["id"], "vote", pid, yes)
        if (same := self.guard.seen(key, self.clock())) is not None:
            return same
        p, err = self.votes.vote(pid, user["id"], yes)
        if p:
            await self.settle(p)
        self.guard.remember(key, (p, err), self.clock())
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

    # -- chat commands to Jev: "jev put fox on" → one Jev choice → the panel's own tile action, proposed as that viewer
    def jev_command(self, user: dict, text: object) -> str:
        """Fire a "jev …" chat line in the background (after the line itself is posted; never delays chat). "" = fired, or
        not a Jev command; else why not (for the sender only)."""
        explicit = wall_jev.is_command(text)
        if not (explicit or wall_jev.is_implicit(text)) or user.get("public") or not user.get("id") or not verified(user):
            return ""
        now = self.clock()
        if not self.jev_user.allow(user["id"], now):
            return "One Jev command every 10 s." if explicit else ""
        if not self.jev_all.allow("*", now):
            return "Jev is busy — try again in a minute." if explicit else ""
        t = asyncio.create_task(self._jev_command(user, str(text), explicit))
        self._jev_tasks.add(t)
        t.add_done_callback(self._jev_tasks.discard)
        return ""

    async def _jev_command(self, user: dict, text: str, explicit: bool = True) -> None:
        """explicit ("jev …"): act at MIN_CONF and always answer; any other line: act only at IMPLICIT_CONF, silent otherwise."""
        try:
            try:
                a = await asyncio.wait_for(self.ask_jev(wall_jev.state(text), wall_jev.questions()), wall_jev.TIMEOUT_S)
            except Exception as e:  # noqa: BLE001 — a timeout or a failure: Jev wasn't reached
                log.warning("jev command: %s", type(e).__name__)
                a = None
            if a is None:
                if explicit:
                    await self.say(wall_jev.UNREACHABLE)
                return
            choice, conf = wall_jev.answer(a)
            if choice == "none" or conf < (wall_jev.MIN_CONF if explicit else wall_jev.IMPLICIT_CONF):
                if explicit:
                    await self.say(wall_jev.HELP)
                return
            if choice == "q_ask":   # a question about the night: Haiku answers from our results + odds
                await self.say(await self._jev_answer(text))
                return
            action, name, msg = wall_jev.plan(choice, text, self.wall)
            if not action:
                if explicit:
                    await self.say(msg)
                return
            p, err = await self.propose(user, action, via_jev=True)
            await self.say(wall_jev.reply(p, err, action, name, conf))
        except Exception as e:  # noqa: BLE001 — a background task: log, never raise into the loop
            log.warning("jev command failed: %s", e)

    async def _jev_answer(self, text: str) -> str:
        from web import wall_ask, wall_election   # they import this module
        snap = await wall_election.snapshot()
        try:
            odds = await wall_election.odds_payload(snap)
        except Exception:  # noqa: BLE001 — answer from the results alone
            odds = {}
        control = []
        for office in ("S", "H"):
            try:
                control += (await wall_election.control_payload(office, snap, None)).get("markets") or []
            except Exception:  # noqa: BLE001
                pass
        got = await wall_ask.answer(wall_jev.TRIGGER.sub("", text).strip(" ,:"), snap, odds, control)
        return f"🎧 DJ Jev: {got}" if got else "🎧 DJ Jev: couldn't look that up right now"

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
                text = note["text"]
                if text.startswith("📊"):   # the dashboard director's reason pick isn't reliable ("polls just closed" at 5:36): just the numbers
                    text = text.split(" — ", 1)[0]
                await self.say(text)   # "No longer mirroring the TV", "Skipped on the stream: …"
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
        while self.socks or self.pubs:
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
            self.jev_command(user, m["text"])
        return m, err

    def state(self) -> dict:
        now = self.clock()
        active = self.presence.active(now)
        return {"viewers": self.viewers(), "active": len(active), "watching": len(self.presence.watching()),
                "proposals": [self.bot_pub(p) for p in self.votes.open()], "wall": self.wall,
                "watch_url": watch_url()}


def public_chat(m: dict) -> dict:
    """A chat line as a public viewer sees it: no Discord ids or avatars."""
    return {k: m.get(k) for k in ("id", "ts", "kind", "name", "text")} | {"guest": bool(m.get("guest"))}


def public_msg(msg: dict) -> dict | None:
    """What of a broadcast reaches public viewers: chat lines (never the owner's private alerts) and deletes."""
    if msg.get("t") == "chat" and isinstance(msg.get("msg"), dict) and not msg["msg"].get("private"):
        return {"t": "chat", "msg": public_chat(msg["msg"])}
    if msg.get("t") == "del":
        return {"t": "del", "id": msg.get("id")}
    if msg.get("t") == "clock":   # the stream's clock offset: without it a public player sat ~4 s "off" and kept seeking
        return msg
    if msg.get("t") == "wall" and isinstance(msg.get("state"), dict):   # public viewers watch the stream too (no origin ids)
        return {"t": "wall", "state": public_wall(msg["state"])}
    return None


def public_wall(st: dict | None) -> dict | None:
    return {k: v for k, v in st.items() if k != "origin"} if isinstance(st, dict) else st


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
    if origin is None or origin.rstrip("/") == auth.WEB_BASE_URL.rstrip("/"):
        return True
    return origin.rstrip("/") in {f"https://{h}" for h in ALT_HOSTS}   # live.djiang.xyz: the same app on our other host


async def wall_websocket(ws: WebSocket) -> None:
    if not origin_ok(ws.headers.get("origin")):
        await ws.close(code=4403, reason="bad origin")
        return
    user = viewer(ws)
    if not user and public_mode():
        await public_websocket(ws)
        return
    if not user:
        await ws.close(code=4401, reason="Log in to SharpLab first")
        return
    h = hub
    if user.get("guest"):
        if not (guest_joins.allow(user["id"], h.clock()) and guest_joins_ip.allow(client_ip(ws), h.clock())):
            await h.strike("u:" + user["id"], "reconnect flood", client_ip(ws), user["name"])
            await ws.close(code=4429, reason="Too many connects — wait a minute")
            return
        others = {u["name"] for uid, u in h.presence.users.items() if uid != user["id"] and uid in h.presence.watching()}
        user = {**user, "name": unique_name(user["name"], others)}
    if h.kicked.get(user["id"], 0) > h.clock() or h.kicked.get("ip:" + client_ip(ws), 0) > h.clock():
        await ws.close(code=4403, reason="Removed from the watch party")
        return
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
                      "cfg": {"delay": c["delay"], "vote_s": c["vote_s"], "sound_approvals": c["sound_approvals"], "no_votes": c["no_votes"],
                              "chat_max": CHAT_MAX}})
    if h.is_admin(user["id"]):
        for line in h.timeout_lines():
            await h.alert(line)
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


async def public_websocket(ws: WebSocket) -> None:
    """A public viewer (no login, no friend cookie): the chat read-only and a head count. Anything it sends but a ping is
    refused — it can't chat, vote or propose."""
    h, ip = hub, client_ip(ws)
    if not guest_joins_ip.allow(ip, h.clock()):
        await ws.close(code=4429, reason="Too many connects — wait a minute")
        return
    await ws.accept()
    cid = next(h.cids)
    h.pubs[cid] = ws
    await h.send(ws, {"t": "hello", "me": {"public": True, "id": "", "name": "", "guest": False, "admin": False, "owner": False},
                      "now": h.clock() * 1000, "history": [public_chat(m) for m in h.chat.log], "viewers": [],
                      "proposals": [], "wall": public_wall(h.wall), "count": h.counts(), "pdt_off": h.edge.off,
                      "cfg": {"chat_max": CHAT_MAX, "delay": cfg()["delay"]}})   # the same shared point as the party
    await h.push_counts(force=True)
    h.ensure_ticker()
    ident = "ip:" + ip
    try:
        while True:
            msg = await ws.receive_json()
            t, now = msg.get("t") if isinstance(msg, dict) else None, h.clock()
            if h.guard.limit(ident, t != "ping", now):
                continue
            if t == "ping":
                await h.send(ws, {"t": "pong", "c": msg.get("c"), "s": now * 1000, "pdt_off": h.edge.off})
            elif t != "hb" and h.guard.tell(("pub", cid), now):
                await h.send(ws, {"t": "error", "text": "The chat is read-only here — the watch party is invite-only.", "chat": t == "chat"})
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        h.pubs.pop(cid, None)
        await h.push_counts()


async def handle(h: Hub, cid: int, ws: WebSocket, user: dict, msg: dict) -> None:
    t, now = msg.get("t"), h.clock()
    ident, admin = "u:" + user["id"], h.is_admin(user["id"])
    write = t not in ("ping", "hb")
    if not admin:   # the owner and admins are never limited or timed out
        if write and (left := h.guard.timed_out(ident, now)):
            if h.guard.tell(cid, now):
                await h.send(ws, {"t": "error", "text": f"You're in a short timeout ({math.ceil(left / 60)} min) — you can keep watching."})
            return
        if t != "chat" and (wait := h.guard.limit(ident, write, now)):   # chat has its own bucket (Chat.post)
            await h.strike(ident, f"socket flood ({str(t)[:12]})", client_ip(ws), user.get("name", ""), user["id"])
            if h.guard.tell(cid, now):
                await h.send(ws, {"t": "error", "text": "Slow down a little.", "retry_after": math.ceil(wait)})
            return
    if t == "ping":
        await h.send(ws, {"t": "pong", "c": msg.get("c"), "s": now * 1000, "pdt_off": h.edge.off})
        return
    if t == "hb":
        h.presence.beat(cid, bool(msg.get("hidden")), now)
        await h.push_viewers()
        return
    h.presence.beat(cid, False, now)   # anything else they do means they're here
    if t == "chat":
        text = msg.get("text")
        if admin and isinstance(text, str) and text.strip().split(" ")[0] in ("/timeouts", "/lift"):
            cmd, _, arg = text.strip().partition(" ")
            if cmd == "/timeouts":
                for line in h.timeout_lines() or ["Nobody's in a timeout."]:
                    await h.alert(line)
            else:
                lifted = h.guard.lift(arg) if arg.strip() else []
                await h.alert(f"Timeout lifted: {', '.join(lifted)}." if lifted else f"No timeout matches “{arg.strip()}”.")
            return
        m, err = h.chat.post(user, text, now)
        if m:
            await h.broadcast({"t": "chat", "msg": m})
            if why := h.jev_command(user, m["text"]):
                await h.send(ws, {"t": "error", "text": why, "chat": True})
        else:
            if err in ("Slow down a little.", "You just said that."):
                await h.strike(ident, "chat flood", client_ip(ws), user.get("name", ""), user["id"])
            await h.send(ws, {"t": "error", "text": err, "chat": True})
    elif t == "propose":
        _, err = await h.propose(user, msg.get("action"))
        if err:
            await h.send(ws, {"t": "error", "text": err})
    elif t == "vote":
        _, err = await h.vote(user, msg.get("id"), msg.get("yes"))
        if err:
            await h.send(ws, {"t": "error", "text": err})
    elif t == "kick" and isinstance(msg.get("uid"), str):   # the owner / admins: out of the party for 30 min
        if not h.is_admin(user["id"]) or h.is_admin(msg["uid"]):
            await h.send(ws, {"t": "error", "text": "Only David and admins can kick (and never each other)."})
            return
        who = h.presence.users.get(msg["uid"], {}).get("name", "someone")
        until = now + 1800
        h.kicked[msg["uid"]] = until
        for c2, (w2, u2) in list(h.socks.items()):
            if u2["id"] == msg["uid"]:
                if u2.get("guest"):
                    h.kicked["ip:" + client_ip(w2)] = until   # a guest can't just rejoin under a new nickname
                try:
                    await w2.close(code=4403, reason="Removed from the watch party")
                except RuntimeError:
                    pass
        await h.say(f"{who} was removed from the watch party.")
    elif t in ("del", "mute"):
        if not h.is_admin(user["id"]):
            await h.send(ws, {"t": "error", "text": "Only David and admins can moderate."})
            return
        if t == "del" and isinstance(msg.get("id"), int) and h.chat.delete(msg["id"]):
            await h.broadcast({"t": "del", "id": msg["id"]})
        elif t == "mute" and isinstance(msg.get("uid"), str):
            mins = msg.get("minutes", 10)
            h.chat.mute(msg["uid"], float(mins) if isinstance(mins, (int, float)) else 10, now)
            if isinstance(mins, (int, float)) and mins <= 0:   # unmute also lifts an abuse timeout
                h.guard.lift("u:" + msg["uid"])
            who = h.presence.users.get(msg["uid"], {}).get("name", "someone")
            await h.say(f"{who} was muted for {int(mins)} min." if mins and mins > 0 else f"{who} was unmuted.")


# ---- HTTP ------------------------------------------------------------------------------------------------------------
async def http_guard(request: Request) -> None:
    """Every /api/v1/wall* route (a router dependency): per-identity token buckets, reads loose, writes tight; a timed-out
    identity may still read. The bot / wall service (X-API-Key or the wall token), the owner and admins are exempt."""
    if check_api_key(request.headers.get("x-api-key") or ""):
        return
    tok = cfg()["token"]
    if tok and secrets.compare_digest(request.headers.get("authorization", "").encode(), f"Bearer {tok}".encode()):
        return
    user = viewer(request)
    if user and hub.is_admin(user["id"]):
        return
    ip, now = client_ip(request), hub.clock()
    ident = f"u:{user['id']}" if user else f"ip:{ip}"
    # /guest has its own limits (guest_names / guest_names_ip, and a rejoin under the held nickname is free): a read here
    write = request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path != "/api/v1/wall/guest"
    if write and (left := hub.guard.timed_out(ident, now)):
        raise HTTPException(429, "You're in a short timeout — you can keep watching.", headers={"Retry-After": str(math.ceil(left))})
    if wait := hub.guard.limit(ident, write, now):
        await hub.strike(ident, f"{'write' if write else 'read'} limit on {request.url.path[:60]}", ip,
                         (user or {}).get("name", ""), (user or {}).get("id", ""))
        raise HTTPException(429, "Slow down a little.", headers={"Retry-After": str(max(1, math.ceil(wait)))})


guarded = [Depends(http_guard)]


router = APIRouter(prefix="/api/v1/wall", tags=["wall"], dependencies=guarded)
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


def peek() -> dict:
    """What the join screen may show before anyone logs in: the wall's name while it runs, and a head count. Never names or media."""
    w = hub.wall or {}
    return {"name": (w.get("name") or None) if w.get("running") else None, "watching": len(hub.presence.watching())}


@router.get("/me")
async def me(request: Request, k: str = ""):
    user = viewer(request)
    login = f"/api/v1/auth/discord/login?next={PAGE}"
    if not user and not key_ok(k) and public_mode():   # the plain page: data only, no join
        return {"authenticated": False, "public": True, "tier": "public", "login": login, "peek": peek()}
    if not user:
        return JSONResponse({"authenticated": False, "login": login, "guest_ok": key_ok(k), "peek": peek()}, status_code=401)
    return {"authenticated": True, **user, "tier": tier(user)}


@router.post("/guest")
async def guest(request: Request):
    """Demo mode: someone with the keyed link picks a nickname → a signed friend cookie (24 h)."""
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or not key_ok(body.get("k")):
        return JSONResponse({"error": "This link isn't valid — log in with Discord instead."}, status_code=403)
    try:
        name = clean_nick(body.get("name"))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    held = read_guest(request)
    if held and held["name"].lower() in (name.lower(), unique_name(name, {name}).lower()) or \
            held and held["name"].lower().startswith(name.lower() + " (guest"):   # this browser already has that nickname: rejoining is free
        return {"id": held["id"], "name": held["name"], "guest": True}
    now, ip = time.time(), client_ip(request)
    gb = request.cookies.get(GUEST_BROWSER) or ""
    if not re.fullmatch(r"[0-9a-f]{32}", gb):
        gb = secrets.token_hex(16)
    if left := max(hub.guard.timed_out(f"gb:{gb}", hub.clock()), hub.guard.timed_out(f"ip:{ip}", hub.clock())):
        return JSONResponse({"error": "Too many nicknames from this browser — try again later.", "retry_after": math.ceil(left)},
                            status_code=429, headers={"Retry-After": str(math.ceil(left))})
    w_gb, w_ip = guest_names.wait(gb, now), guest_names_ip.wait(ip, now)
    wait = max(w_gb, w_ip)
    if wait:
        if w_gb:   # one browser cycling nicknames
            await hub.strike(f"gb:{gb}", "too many guest nicknames", ip)
        else:      # a whole IP: only its unauthenticated writes (joining) are affected
            await hub.strike(f"ip:{ip}", "too many guest nicknames from one IP", ip)
        return JSONResponse({"error": "Lots of people just joined from this network. Use Continue with Discord above, or wait a moment.",
                             "retry_after": wait}, status_code=429, headers={"Retry-After": str(wait)})
    guest_names.allow(gb, now)
    guest_names_ip.allow(ip, now)
    taken = {hub.presence.users[u]["name"] for u in hub.presence.watching() if u in hub.presence.users}
    name = unique_name(name, taken)
    raw, g = make_guest_cookie(name)
    resp = JSONResponse({"id": g["id"], "name": name, "guest": True})
    resp.set_cookie(GUEST_COOKIE, raw, max_age=GUEST_TTL, httponly=True, secure=True, samesite="lax")
    resp.set_cookie(GUEST_BROWSER, gb, max_age=30 * 86400, httponly=True, secure=True, samesite="lax")
    return resp


ALT_HOSTS = {h.strip() for h in os.environ.get("WALL_ALT_HOSTS", "live.djiang.xyz").split(",") if h.strip()}


@router.get("/handoff")
async def handoff(request: Request, to: str = "", back: str = "/wall.html", login: int = 0):
    """The watch page on another of our hosts (live.djiang.xyz): a SharpLab member signed in here goes there signed in
    (a one-time login link minted for that host); anyone else goes straight back as they were — or, with login=1 (the
    page's "Log in"), through Discord here first. Only our hosts, only a path back."""
    if to not in ALT_HOSTS or not back.startswith("/") or back.startswith("//"):
        return JSONResponse({"error": "bad handoff"}, status_code=400)
    user = session_user(auth.read_session(request))
    if user:
        sess = auth.read_session(request) or {}
        link = mint_link(user["id"], sess.get("username") or user["name"], sess.get("avatar"))
        return RedirectResponse(f"https://{to}/api/v1/wall/login?t={link.split('?t=', 1)[1]}&back={quote(back, safe='')}")
    if login:
        nxt = f"/api/v1/wall/handoff?to={quote(to)}&back={quote(back, safe='')}"
        return RedirectResponse(f"/api/v1/auth/discord/login?next={quote(nxt, safe='')}")
    sep = "&" if "?" in back else "?"
    return RedirectResponse(f"https://{to}{back}{sep}nohandoff=1")


@router.get("/login")
async def login(t: str = "", back: str = ""):
    """A bot-minted link: sets the normal SharpLab session cookie for that member, then the watch page (on whichever of our
    hosts it was opened — sharplab.djiang.xyz or live.djiang.xyz)."""
    data = redeem_link(t)
    dest = back if back.startswith("/") and not back.startswith("//") else PAGE
    if not data:
        return RedirectResponse(f"{PAGE}?error=link_expired")
    resp = RedirectResponse(dest)
    resp.set_cookie(auth.COOKIE_NAME, auth.make_session_cookie(data), max_age=auth.SESSION_TTL,
                    httponly=True, secure=True, samesite="lax")
    return resp


_upstream: httpx.AsyncClient | None = None


async def fetch_upstream(name: str) -> tuple[int, bytes, str]:
    """One kept-alive connection to the wall service: a new one per 1 s segment never got out of TCP slow start, so a
    0.6 MB segment took ~1.5 s over the homelab's ~8 Mbps uplink and every browser stalled."""
    global _upstream
    c = cfg()
    if _upstream is None or _upstream.is_closed:
        _upstream = httpx.AsyncClient(timeout=10, limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=60))
    r = await _upstream.get(f"{c['service']}/hls/discord/{name}", headers={"Authorization": f"Bearer {c['token']}"})
    return r.status_code, r.content, r.headers.get("content-type", "")


async def hls_get(name: str) -> tuple | None:
    """(expires, body, content type) from the cache, else one upstream fetch shared by everyone asking; None = down."""
    playlist = name.endswith(".m3u8")
    ctype = "application/vnd.apple.mpegurl" if playlist else "video/mp2t"
    hit = _cache.get(name)
    if hit and hit[0] >= time.monotonic():
        return hit
    async with _locks.setdefault(name, asyncio.Lock()):
        hit = _cache.get(name)
        if hit and hit[0] >= time.monotonic():
            return hit
        try:
            code, body, _ = await fetch_upstream(name)
        except httpx.HTTPError:
            return None
        if code != 200:
            return (0.0, code, ctype)
        hit = (time.monotonic() + (0.5 if playlist else 60), body, ctype)
        if playlist:
            text = body.decode("utf-8", "replace")
            seq = re.search(r"#EXT-X-MEDIA-SEQUENCE:(\d+)", text)
            if seq and int(seq.group(1)) < _seq[0]:   # the wall restarted: same segment names, new video
                for k in [k for k in _cache if k != name]:
                    _cache.pop(k, None)
            _seq[0] = int(seq.group(1)) if seq else 0
            await hub.observe_playlist(text)
            # prefetch: the newest segments start coming over now, before the first player asks for them
            for seg in re.findall(r"^(seg\d+\.ts)$", text, re.M)[-3:]:
                if seg not in _cache and not (_locks.get(seg) and _locks[seg].locked()):
                    asyncio.create_task(hls_get(seg))
        _cache[name] = hit
        for k, v in list(_cache.items()):   # expired segments go now: ~0.5 MB each, on a 2 GB VPS
            if v[0] < time.monotonic():
                _cache.pop(k, None)
                _locks.pop(k, None)
        return hit


@router.get("/hls/{name}")
async def hls(name: str, request: Request):
    if not viewer(request) and not public_mode():   # public mode: the plain link watches the stream too (David, 2026-11-03)
        return JSONResponse({"error": "log in"}, status_code=401)
    if not _HLS_NAME.match(name):
        return JSONResponse({"error": "no such file"}, status_code=404)
    hit = await hls_get(name)
    if hit is None or not isinstance(hit[1], bytes):
        code = hit[1] if hit else 502
        return JSONResponse({"error": "the wall isn't streaming"}, status_code=404 if code == 404 else 502)
    return Response(hit[1], media_type=hit[2], headers={"Cache-Control": "no-store"})   # names repeat across wall restarts


# ---- pop-out: the wall's election dashboards (wall/pages/election/*), read-only, for every tier that sees the page ----
# Mounted at …/p/election/<file> + …/p/us-states.json so the pages' relative URLs (data.json, election.css,
# ../us-states.json) resolve back through here. Exactly these files; the wall service's REMOTE_OK allows the same set.
POP_FILE = re.compile(r"^(map|races|turnout|exitpoll|swing|election\.css|election\.js|(data|odds|turnout|exitpoll|swing)\.json)$")
POP_TYPES = {"": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
             ".json": "application/json"}
POP_JSON_S, POP_STATIC_S = 3.0, 60.0   # data: shared by every viewer for a few s; the page files change only on a deploy
_pop: dict[str, tuple[float, bytes | int]] = {}
_pop_locks: dict[str, asyncio.Lock] = {}


async def fetch_pop(path: str) -> tuple[int, bytes]:
    """GET a wall-service path over the HLS proxy's kept-alive client."""
    global _upstream
    c = cfg()
    if _upstream is None or _upstream.is_closed:
        _upstream = httpx.AsyncClient(timeout=10, limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=60))
    r = await _upstream.get(f"{c['service']}{path}", headers={"Authorization": f"Bearer {c['token']}"})
    return r.status_code, r.content


async def pop_get(path: str, ttl: float) -> bytes | int:
    """The body (cached; one upstream fetch per expiry, shared by every viewer), or an error status (502 = unreachable)."""
    hit = _pop.get(path)
    if hit and hit[0] >= time.monotonic():
        return hit[1]
    async with _pop_locks.setdefault(path, asyncio.Lock()):
        hit = _pop.get(path)
        if hit and hit[0] >= time.monotonic():
            return hit[1]
        try:
            code, body = await fetch_pop(path)
        except httpx.HTTPError:
            code, body = 502, b""
        val: bytes | int = body if code == 200 else (404 if code == 404 else 502)
        _pop[path] = (time.monotonic() + (ttl if code == 200 else 2.0), val)   # an outage is retried in 2 s, not per viewer
        return val


async def pop_serve(request: Request, path: str, ctype: str, ttl: float):
    user = viewer(request)
    if not user and not public_mode():
        return JSONResponse({"error": "log in"}, status_code=401)
    got = await pop_get(path, ttl)
    if isinstance(got, int):
        return JSONResponse({"error": "the election dashboards aren't reachable right now"}, status_code=got)
    if path == "/p/election/data.json" and not user:   # the members' position chips (names + sides) aren't for the public
        try:
            snap = json.loads(got)
            snap["holders"] = {}
            got = json.dumps(snap, separators=(",", ":")).encode()
        except (ValueError, TypeError):
            return JSONResponse({"error": "bad upstream data"}, status_code=502)
    return Response(got, media_type=ctype, headers={"Cache-Control": f"private, max-age={int(ttl)}", "X-Content-Type-Options": "nosniff"})


@router.get("/p/election/{name}")
async def pop_file(name: str, request: Request):
    if not POP_FILE.match(name):
        return JSONResponse({"error": "no such file"}, status_code=404)
    ext = name[name.rfind("."):] if "." in name else ""
    return await pop_serve(request, f"/p/election/{name}", POP_TYPES[ext], POP_JSON_S if ext == ".json" else POP_STATIC_S)


@router.get("/p/us-states.json")
async def pop_shapes(request: Request):
    return await pop_serve(request, "/p/us-states.json", POP_TYPES[".json"], POP_STATIC_S)


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


@router.get("/abuse")
async def bot_abuse(x_api_key: str = Header("")):
    """Who's in an abuse timeout now (also /timeouts in the page's chat, for the owner / admins)."""
    _need_key(x_api_key)
    now = hub.clock()
    return {"timeouts": [{**t, "left": round(left)} for k, t in list(hub.guard.timeouts.items())
                         if (left := hub.guard.timed_out(k, now))]}


@router.post("/abuse/lift")
async def bot_abuse_lift(request: Request, x_api_key: str = Header("")):
    """{"who": "u:123" | "123" | a name}: lift that timeout now (also /lift in the page's chat)."""
    _need_key(x_api_key)
    body = await request.json()
    return {"lifted": hub.guard.lift(str(body.get("who") or "")) if isinstance(body, dict) else []}


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
