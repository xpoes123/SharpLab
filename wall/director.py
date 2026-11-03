"""Election night: TypeSafe's Jev picks what the dashboard tiles show. Every REFRESH_S (while a wall is up) the night so
far — the time, polls just closed / closing next, recent calls, the closest key races counting, exit polls, fresh turnout,
Kalshi's big movers — goes to sharplab-web's /api/v1/wall/election/direct as Jev's state, with the screens as the
choices. Its probabilities become the rotation's order (wall/rotate.py RANK); the rules there still apply (never two of a
kind, holds, votes, events). No answer for STALE_S → plain rotation order.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from wall import rotate

log = logging.getLogger("wall.director")
REFRESH_S = 30.0
OPTIONS = {
    "map?view=S": "Senate map: Senate races are being called, counted or closing; Senate control is the story",
    "map?view=H": "House map: many House districts are reporting or being called; House control is the story",
    "map?view=G": "Governors map: governor races are being called, counted or closing",
    "races": "Key races board with Kalshi odds: close key races are counting, or markets are moving",
    "turnout": "Turnout dashboard: before results, or when official turnout numbers just updated",
    "exitpoll": "Exit polls: early in the evening once exit poll results are out and few votes are counted",
}
OFF = {"S": "Senate", "H": "House", "G": "Governor"}
REASONS = {"close": "polls just closed or are about to close somewhere that matters", "calls": "races are being called",
           "counting": "close races are counting", "markets": "prediction markets are moving",
           "exit": "exit poll results are out", "turnout": "official turnout numbers updated", "quiet": "a quiet stretch: the big picture"}
WHY = {"close": "polls just closed", "calls": "races are being called", "counting": "close races are counting",
       "markets": "the markets are moving", "exit": "exit polls are out", "turnout": "new turnout numbers", "quiet": "the big picture"}
NAMES = {"map?view=S": "Senate map", "map?view=H": "House map", "map?view=G": "Governors map", "races": "Key races",
         "turnout": "Turnout", "exitpoll": "Exit polls", "swing": "vs 2024"}


def clock(t: float) -> str:
    return time.strftime("%-I:%M %p", time.localtime(t))


def summary(snap: dict, turnout: dict, exitpoll: dict, odds: dict, now: float) -> str:
    """The night in a few lines (Jev's state): ~150–300 words."""
    races = snap.get("races") or []
    out = [f"Time now: {clock(now)} Eastern."]
    closed = sorted({r["state"] for r in races if now - 1200 <= (r.get("closes") or 0) <= now})
    if closed:
        out.append("Polls closed in the last 20 minutes: " + " ".join(closed) + ".")
    nxt = sorted({r["closes"] for r in races if (r.get("closes") or 0) > now})
    if nxt:
        sts = sorted({r["state"] for r in races if r.get("closes") == nxt[0]})
        out.append(f"Next polls close {clock(nxt[0])} in " + " ".join(sts[:12]) + f" ({round((nxt[0] - now) / 60)} min away).")
    calls = [c for c in snap.get("calls") or [] if now - (c.get("t") or 0) <= 600]
    if calls:
        out.append(f"Race calls in the last 10 minutes: {len(calls)} — " + "; ".join(c.get("text") or c["id"] for c in calls[:6]) + ".")
    for o in ("S", "H", "G"):
        rs = [r for r in races if r["office"] == o]
        if rs:
            out.append(f"{OFF[o]}: {sum(1 for r in rs if r.get('called'))}/{len(rs)} called, "
                       f"{sum(1 for r in rs if (r.get('votes') or 0) > 0 and not r.get('called'))} counting.")
    close = sorted((r for r in races if r.get("watch") and (r.get("votes") or 0) > 0 and not r.get("called") and r.get("margin") is not None),
                   key=lambda r: r["margin"])[:4]
    if close:
        out.append("Closest key races counting: " + "; ".join(
            f"{r['label']} {OFF[r['office']]} {r.get('leader')}+{r['margin']} with {round(r.get('pct') or 0)}% in" for r in close) + ".")
    if exitpoll.get("questions"):
        out.append(f"Exit polls are out ({len(exitpoll['questions'])} questions, NBC/Edison).")
    else:
        out.append("Exit polls are not released yet.")
    fresh = [s for s, v in (turnout.get("states") or {}).items() if (v.get("batches") or [{}])[0].get("t", 0) >= now - 900]
    if fresh:
        out.append("Official turnout updated in the last 15 minutes for: " + " ".join(sorted(fresh)) + ".")
    movers = sorted(((k, v) for k, v in (odds or {}).items() if isinstance(v.get("chg24"), (int, float))),
                    key=lambda kv: -abs(kv[1]["chg24"]))[:3]
    if movers and abs(movers[0][1]["chg24"]) >= 5:
        out.append("Biggest Kalshi moves (24 h): " + "; ".join(f"{k} {v.get('who')} {v.get('prob')}% ({v['chg24']:+g})" for k, v in movers) + ".")
    return " ".join(out)


class Director:
    def __init__(self, http: Callable[[str, str, dict | None], tuple[int, object]] | None = None, clock=time.time) -> None:
        self.http, self.clock = http, clock
        self.asked = 0.0

    def due(self) -> bool:
        return self.http is not None and self.clock() - self.asked >= REFRESH_S

    def refresh(self, state: str) -> None:
        self.asked = self.clock()
        try:
            status, data = self.http("POST", "/api/v1/wall/election/direct", {"state": state, "options": OPTIONS, "reasons": REASONS})
        except (OSError, ValueError) as e:
            log.info("director: %s", e)
            return
        probs = data.get("probabilities") if status == 200 and isinstance(data, dict) else None
        if isinstance(probs, dict) and probs:
            rotate.set_rank(sorted((s for s in OPTIONS if s in probs), key=lambda s: -float(probs[s] or 0)), self.clock(), probs)
            rotate.RANK["why"] = WHY.get(data.get("reason") or "", "")
            log.info("director: %s", ", ".join(f"{s} {probs[s]:.2f}" for s in rotate.RANK["order"][:3]))
