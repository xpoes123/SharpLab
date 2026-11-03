"""Abuse guard for the watch page (web/wall.py): per-identity token buckets, a short debounce, and a watcher that puts an
identity that keeps hitting limits in a 10-min timeout (writes and socket messages refused; reads still work so it can
keep watching). In memory, one process (--workers 1). Pure: every call takes `now`.

An identity is "u:<user or guest id>" when the request carries one, else "ip:<client ip>". IP keys get looser buckets:
guests share IPs (one apartment wifi), so per IP only a flood is stopped.

Limits against the page's real cadence (web/static/wall*.js), per viewer: hls.js polls the playlist + a segment every
~1–2 s, the bets panel every 5 s, the election feed 15 s, an open state sheet 2 calls / 30 s + charts / 60 s + up to 8
race details / 10 s, the map 30 s, the socket a ping + heartbeat / 15 s. Opening a state sheet bursts ~20 GETs. So reads
are 10/s with a burst of 40 (a few tabs never notice) and writes 1/s with a burst of 8 (nobody taps that fast).
"""
from __future__ import annotations

import logging
from collections import deque

log = logging.getLogger(__name__)

READ = (10.0, 40)        # (refill per s, burst) per user / guest
WRITE = (1.0, 8)
READ_IP = (40.0, 240)    # per IP when there's no identity yet (join screen, many friends on one wifi)
WRITE_IP = (2.0, 120)
CHAT = (0.5, 6)          # chat: 6 in a burst, then one per 2 s
CHAT_DUP_S = 10          # the same text again within 10 s is dropped
DEBOUNCE_S = 1.5         # the same action twice within 1.5 s counts once
STRIKES = (20, 60)       # more than 20 refusals in 60 s …
TIMEOUT_S = 600          # … = a 10-min timeout
MAX_KEYS = 5000


class Buckets:
    """A token bucket per key: `take` → 0 when allowed, else seconds until the next token (for Retry-After)."""

    def __init__(self, rate: float, burst: int) -> None:
        self.rate, self.burst, self.b = rate, burst, {}

    def take(self, key: str, now: float) -> float:
        tokens, last = self.b.get(key, (self.burst, now))
        tokens = min(self.burst, tokens + max(0.0, now - last) * self.rate)
        if tokens >= 1:
            self.b[key] = (tokens - 1, now)
            if len(self.b) > MAX_KEYS:   # bounded: drop the stalest keys
                for k in sorted(self.b, key=lambda k: self.b[k][1])[:MAX_KEYS // 5]:
                    self.b.pop(k, None)
            return 0.0
        self.b[key] = (tokens, now)
        return (1 - tokens) / self.rate


class Guard:
    def __init__(self) -> None:
        self.read, self.write = Buckets(*READ), Buckets(*WRITE)
        self.read_ip, self.write_ip = Buckets(*READ_IP), Buckets(*WRITE_IP)
        self.chat = Buckets(*CHAT)
        self.last_chat: dict[str, tuple[str, float]] = {}
        self.recent: dict[tuple, tuple[float, object]] = {}
        self.strikes: dict[str, deque] = {}
        self.timeouts: dict[str, dict] = {}   # identity → {until, reason, ip, name}
        self.told: dict[object, float] = {}

    # -- limits
    def limit(self, ident: str, write: bool, now: float) -> float:
        """0 = go ahead, else seconds to wait. Doesn't strike: the caller does (it knows the reason)."""
        ip = ident.startswith("ip:")
        b = (self.write_ip if ip else self.write) if write else (self.read_ip if ip else self.read)
        return b.take(ident, now)

    def chat_ok(self, ident: str, text: str, now: float) -> str:
        """"" = send it, else why not."""
        prev = self.last_chat.get(ident)
        if prev and prev[0] == text and now - prev[1] < CHAT_DUP_S:
            return "You just said that."
        if self.chat.take(ident, now):
            return "Slow down a little."
        self.last_chat[ident] = (text, now)
        if len(self.last_chat) > MAX_KEYS:
            self.last_chat = {k: v for k, v in self.last_chat.items() if now - v[1] < CHAT_DUP_S}
        return ""

    # -- debounce: the same identity, the same action
    def seen(self, key: tuple, now: float):
        """The result stored for this key within DEBOUNCE_S, else None."""
        hit = self.recent.get(key)
        return hit[1] if hit and now - hit[0] < DEBOUNCE_S else None

    def remember(self, key: tuple, result, now: float) -> None:
        if len(self.recent) > 1000:
            self.recent = {k: v for k, v in self.recent.items() if now - v[0] < DEBOUNCE_S}
        self.recent[key] = (now, result)

    # -- the watcher
    def timed_out(self, ident: str, now: float) -> float:
        """Seconds left in this identity's timeout (0 = none)."""
        t = self.timeouts.get(ident)
        if t and t["until"] <= now:
            self.timeouts.pop(ident, None)
            return 0.0
        return t["until"] - now if t else 0.0

    def strike(self, ident: str, now: float, reason: str, ip: str = "", name: str = "", exempt: bool = False) -> dict | None:
        """Count one refusal. Returns the new timeout when this one tipped it over (exempt identities never time out)."""
        if exempt or self.timed_out(ident, now):
            return None
        q = self.strikes.setdefault(ident, deque())
        q.append(now)
        while q and now - q[0] > STRIKES[1]:
            q.popleft()
        if len(self.strikes) > MAX_KEYS:
            self.strikes = {k: v for k, v in self.strikes.items() if v and now - v[-1] <= STRIKES[1]}
            self.strikes.setdefault(ident, q)
        if len(q) <= STRIKES[0]:
            return None
        q.clear()
        t = {"ident": ident, "until": now + TIMEOUT_S, "reason": reason, "ip": ip, "name": name}
        self.timeouts[ident] = t
        log.warning("wall timeout: %s (%s) ip=%s reason=%s for %d min", ident, name or "?", ip or "?", reason, TIMEOUT_S // 60)
        return t

    def lift(self, who: str) -> list[str]:
        """Lift timeouts matching an identity ("u:123", "123", "ip:1.2.3.4") or a name (case-insensitive)."""
        w = who.strip().lower()
        hits = [k for k, t in self.timeouts.items() if w in (k.lower(), k.split(":", 1)[1].lower(), (t.get("name") or "").lower())]
        for k in hits:
            self.timeouts.pop(k, None)
            self.strikes.pop(k, None)
        return hits

    def tell(self, key: object, now: float, gap: float = 2.0) -> bool:
        """Rate-limit our own error replies (a flood mustn't get one frame back per message)."""
        if now - self.told.get(key, -1e9) < gap:
            return False
        if len(self.told) > MAX_KEYS:
            self.told.clear()
        self.told[key] = now
        return True
