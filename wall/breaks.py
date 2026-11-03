"""Election night: when each news stream goes to commercial, learned from what it has done tonight (wall/onair.py).

* BreakLog: every break the Board saw per stream — a label going coverage/offtopic → ad/break and back — as JSONL rows
  {stream, start, end, label} in $WALL_STATE_DIR/breaks.jsonl (appended on the way back, reloaded at start).
* predict(): from that log only, per stream, how likely a break starts in the next ~60–90 s given the minute of the hour
  (networks run breaks on a clock: :18, :48 …). A histogram of break-start minutes with a ±WINDOW_MIN window over the hours
  we watched that stream; under MIN_BREAKS breaks it says nothing. It never moves anything by itself (wall/onair.py Policy
  needs the transcript model's "soon" too).
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from statistics import median

log = logging.getLogger("wall.breaks")
AWAY = {"ad", "break"}
BACK = {"coverage", "offtopic"}   # what ends a break ("unknown" doesn't: no answer is no evidence)
MIN_BREAKS, WINDOW_MIN = 3, 2
AHEAD_S = (60.0, 90.0)            # "about to": a start in this far ahead
MAX_BREAK_S = 15 * 60             # longer = we lost the stream, not a break
MAX_ROWS = 5000


def _min_of_hour(t: float) -> float:
    return (t % 3600) / 60   # whole-hour time zones: the minute of the hour is the same everywhere


def _circ(a: float, b: float) -> float:
    d = abs(a - b) % 60
    return min(d, 60 - d)


class BreakLog:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.rows: list[dict] = []
        self.open: dict[str, tuple[float, str]] = {}   # stream → (start, label) of the break it is in now
        if path and path.exists():
            try:
                for line in path.read_text().splitlines()[-MAX_ROWS:]:
                    r = json.loads(line)
                    if isinstance(r, dict) and isinstance(r.get("stream"), str) and \
                            all(isinstance(r.get(k), (int, float)) for k in ("start", "end")):
                        self.rows.append(r)
            except (OSError, ValueError) as e:
                log.warning("breaks: unreadable %s (%s)", path, e)

    def note(self, stream: str, prev: str | None, label: str, at: float) -> dict | None:
        """A label change on a stream (at = when the new label began). Returns the finished break row, if any."""
        if label in AWAY and stream not in self.open and prev not in AWAY:
            self.open[stream] = (at, label)
        elif label in BACK and stream in self.open:
            start, lab = self.open.pop(stream)
            if 0 < at - start <= MAX_BREAK_S:
                row = {"stream": stream, "start": round(start, 1), "end": round(at, 1), "label": lab}
                self.rows.append(row)
                del self.rows[:-MAX_ROWS]
                if self.path:
                    try:
                        self.path.parent.mkdir(parents=True, exist_ok=True)
                        with self.path.open("a") as f:
                            f.write(json.dumps(row) + "\n")
                    except OSError as e:
                        log.warning("breaks: can't save (%s)", e)
                return row
        return None

    def of(self, stream: str) -> list[dict]:
        return [r for r in self.rows if r["stream"] == stream]

    def predict(self, stream: str, now: float | None = None) -> dict:
        return predict(self.of(stream), time.time() if now is None else now)


def predict(rows: list[dict], now: float) -> dict:
    """{next_likely: "~:48" | None, p_soon: 0..1, n_breaks, len_s}: p_soon = the share of the hours we watched in which a
    break started within ±WINDOW_MIN of now + 60–90 s."""
    n = len(rows)
    out = {"next_likely": None, "p_soon": 0.0, "n_breaks": n, "len_s": None}
    if n < MIN_BREAKS:
        return out
    starts = [_min_of_hour(r["start"]) for r in rows]
    out["len_s"] = round(median(r["end"] - r["start"] for r in rows))
    hours = {int(t // 3600) for r in rows for t in (r["start"], r["end"])}
    ahead = _min_of_hour(now + sum(AHEAD_S) / 2)
    reach = WINDOW_MIN + (AHEAD_S[1] - AHEAD_S[0]) / 120   # ±2 min around the middle of the 60–90 s window
    hit = {int(r["start"] // 3600) for r, m in zip(rows, starts) if _circ(m, ahead) <= reach}
    out["p_soon"] = round(min(1.0, len(hit) / max(1, len(hours))), 2)
    near = [sum(_circ(m, k) <= WINDOW_MIN for m in starts) for k in range(60)]
    peak = [sum(max(0.0, WINDOW_MIN + 1 - _circ(m, k)) for m in starts) for k in range(60)]   # triangular: peaks on the minute
    cur = int(_min_of_hour(now))
    for d in range(1, 61):   # the next minute of the hour with ≥2 break starts around it, at its peak
        k = (cur + d) % 60
        if near[k] >= 2 and peak[k] >= peak[(k + 1) % 60] and peak[k] >= peak[(k - 1) % 60]:
            out["next_likely"] = f"~:{k:02d}"
            break
    return out
