"""Election night exit polls: NBC News' public exit poll page (Edison / NEP), national.

The page is server-rendered Next.js: its <script id="__NEXT_DATA__"> JSON carries props.pageProps.nationalExitPollData,
a list of voter groups ("All voters", "White", "Women", …), each the full question set for that group:
    {title, polls: {lastUpdated: "9 AM ET", candidates: [{party: "dem"}, {party: "gop"}],
                    polls: [{question, answers: [{label, percent: "47%", values: [{value: 43}, {value: 55}]}]}]}}
(values follow candidates' order). Empty list until NBC releases the first wave. Only that HTML page is fetched.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.request
from typing import Callable

log = logging.getLogger("wall.exitpoll")
URL = "https://www.nbcnews.com/politics/2026-elections/exit-polls"
SOURCE = "NBC News exit poll (Edison)"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
      "Accept": "text/html"}
POLL_S, IDLE_S = 90.0, 900.0
_NEXT = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def get(url: str = URL) -> str:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
        return r.read().decode("utf-8", "replace")


def _pct(s) -> float | None:
    try:
        return float(str(s).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None


def _questions(polls: dict) -> list[dict]:
    order = [str(c.get("party") or "").lower() for c in polls.get("candidates") or []]
    di = order.index("dem") if "dem" in order else 0
    ri = order.index("gop") if "gop" in order else 1
    out = []
    for p in polls.get("polls") or []:
        answers = []
        for a in p.get("answers") or []:
            vals = [_pct(v.get("value")) if isinstance(v, dict) else None for v in a.get("values") or []]
            answers.append({"label": str(a.get("label") or ""), "share": _pct(a.get("percent")),
                            "d": vals[di] if di < len(vals) else None, "r": vals[ri] if ri < len(vals) else None})
        if p.get("question") and answers:
            out.append({"q": str(p["question"]), "answers": answers})
    return out


def parse(page: str) -> dict:
    """The page's HTML → {phase, as_of, questions (All voters), groups: [{title, questions}]}. Raises if it isn't there."""
    m = _NEXT.search(page)
    if not m:
        raise ValueError("no __NEXT_DATA__ on the page")
    props = json.loads(m.group(1))["props"]["pageProps"]
    groups = [{"title": str(g.get("title") or ""), "as_of": str((g.get("polls") or {}).get("lastUpdated") or ""),
               "questions": _questions(g.get("polls") or {})} for g in props.get("nationalExitPollData") or []]
    groups = [g for g in groups if g["questions"]]
    allv = next((g for g in groups if g["title"].lower() == "all voters"), groups[0] if groups else None)
    return {"phase": str((props.get("nationalExitPollCMSData") or {}).get("currentPhase") or ""),
            "as_of": allv["as_of"] if allv else "", "questions": allv["questions"] if allv else [],
            "groups": [{"title": g["title"], "questions": g["questions"]} for g in groups if g is not allv]}


class ExitPoll:
    def __init__(self, fetch: Callable[[], str] = get, clock=time.time, poll_s: float = POLL_S) -> None:
        self.fetch, self.clock, self.poll_s = fetch, clock, poll_s
        self.data: dict = {"phase": "", "as_of": "", "questions": [], "groups": []}
        self.updated = 0.0
        self.error = ""
        self.seen = 0.0
        self.lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def touch(self) -> None:
        self.seen = self.clock()
        with self.lock:
            if not (self._thread and self._thread.is_alive()):
                self._thread = threading.Thread(target=self._run, name="exitpoll", daemon=True)
                self._thread.start()

    def _run(self) -> None:
        while self.clock() - self.seen < IDLE_S:
            self.poll()
            time.sleep(self.poll_s)

    def poll(self) -> None:
        try:
            got = parse(self.fetch())
        except Exception as e:  # noqa: BLE001 — keep the last good poll, say why
            log.warning("exitpoll: %s", e)
            with self.lock:
                self.error = str(e)[:120]
            return
        with self.lock:
            if got["questions"] or not self.data["questions"]:   # an empty page after a full one = a blip: keep the numbers
                self.data = got
            else:
                self.data["phase"] = got["phase"]
            self.updated, self.error = self.clock(), ""

    def snapshot(self, groups: bool = False) -> dict:
        with self.lock:
            d = self.data
            return {"phase": d["phase"], "as_of": d["as_of"], "updated": self.updated, "now": self.clock(), "source": SOURCE,
                    "url": URL, "questions": d["questions"], "error": self.error,
                    **({"groups": d["groups"]} if groups else {"group_titles": [g["title"] for g in d["groups"]]})}
