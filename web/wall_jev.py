"""Chat commands to Jev on the watch page: a verified viewer's chat line that starts with "jev" ("jev senate map pls",
"Jev put fox on") becomes ONE Jev choice question — which dashboard or Spectrum stream do they want — and, when Jev is
sure, the same tile action the panel would send, proposed as that viewer (web/wall.py Hub.jev_command). Pure: the Hub
does the call, the limits and the replies. The chat text is untrusted — it only ever goes to Jev as plain-text state."""
from __future__ import annotations

import re

from wall import spectrum

OWN = "http://127.0.0.1:7795/p/election/"
TRIGGER = re.compile(r"^\s*jev\b", re.I)
SOUND = re.compile(r"\b(sound|audio|listen|hear|volume|unmute|loud)\b", re.I)
MIN_CONF = 0.55   # live Jev: right picks land 0.59–1.0; unclear ones ≤ 0.3
TIMEOUT_S = 3.0
STATE_MAX = 300
QID = "want"
QUESTION = "What does this viewer want the election wall to show?"
HELP = "🎧 DJ Jev: didn't catch that — try 'jev show the senate map' or 'jev put abc on'"
UNREACHABLE = "🎧 DJ Jev: couldn't reach Jev"

# id → (screen, label, description for Jev); labels match the panel's (web/static/wall.js ELEC_SCREENS)
DASHBOARDS = {
    "d_mapS": ("map?view=S", "Senate map", "the Senate map: Senate races and results by state, e.g. 'senate', 'senate map', 'who wins the senate'"),
    "d_mapH": ("map?view=H", "House map", "the House map: House races and districts, e.g. 'house', 'house map', 'congress', 'districts'"),
    "d_mapG": ("map?view=G", "Governor map", "the Governor map: governor races by state, e.g. 'governors', 'governor map', 'gubernatorial'"),
    "d_races": ("races", "Key races", "the key races board: the closest / most important races with live odds and prices, "
                "e.g. 'key races', 'close races', 'races to watch', 'odds', 'kalshi', 'markets'"),
    "d_turnout": ("turnout", "Turnout", "the turnout dashboard: how many people voted, early vote, e.g. 'turnout', 'votes cast', 'early vote'"),
    "d_exitpoll": ("exitpoll", "Exit polls", "the exit polls dashboard: who voted for whom by group, e.g. 'exit polls', 'demographics', 'polls'"),
    "d_swing": ("swing", "vs 2024", "the comparison with 2024: how results swing against the 2024 election, e.g. '2024', "
                "'2024 dashboards', 'swing', 'vs 2024', 'comparison', 'compared to last time', 'shift'"),
}


IMPLICIT_CONF = 0.85   # a chat line NOT addressed to Jev ("can i see the 2024 dashboard?"): act only when this sure, never reply


def is_implicit(text: object) -> bool:
    """Any other line worth a look: a short request-sized message (Jev decides whether it's a wall request at all)."""
    return isinstance(text, str) and 6 <= len(text.strip()) <= 200 and not is_command(text)


def is_command(text: object) -> bool:
    return isinstance(text, str) and bool(TRIGGER.match(text))


def streams() -> dict[str, dict]:
    """s1…sN → the Spectrum lineup's streams ({group, name, url, note}), in the catalog's order."""
    return {f"s{k}": s for k, s in enumerate(spectrum.load(), 1)}


def criteria() -> dict[str, str]:
    out = {k: v[2] for k, v in DASHBOARDS.items()}
    for k, s in streams().items():
        # anchors as example phrases ("kornacki"): Jev resolves those, not a hosts list (0.48 → 0.94 live)
        ex = [s["name"].lower()] + [h.split()[-1].lower() for h in s.get("hosts", "").split(", ") if h]
        out[k] = f"the {s['name']} live stream ({s['group']}), e.g. " + ", ".join(f"'{e}'" for e in ex)
    out["none"] = "not a request to change what the wall shows, or unclear which screen or stream they mean"
    return out


def state(text: str) -> str:
    # no "on the wall now" context: tried live, it pulled Jev toward the tile with the sound (0.94 → 0.5)
    return "A viewer wrote in the watch-party chat: " + " ".join(str(text).split())[:STATE_MAX]


def questions() -> dict:
    return {QID: {"type": "choice", "question": QUESTION, "criteria": criteria()}}


def answer(a: object) -> tuple[str, float]:
    """(choice, confidence) from Jev's answers; ("none", 0) for anything off."""
    got = a.get(QID) if isinstance(a, dict) else None
    if not isinstance(got, dict):
        return "none", 0.0
    choice, conf = got.get("choice"), got.get("confidence")
    if not isinstance(choice, str) or choice not in criteria() or not isinstance(conf, (int, float)) or isinstance(conf, bool):
        return "none", 0.0
    return choice, float(conf)


def _kind(url: str) -> str:
    return url[len(OWN):].split("?")[0] if url.startswith(OWN) else ""


def plan(choice: str, text: str, wall: dict | None) -> tuple[dict | None, str, str]:
    """(action, name, "") to propose, or (None, name, reply) when there's nothing to do. tile i is 0-based."""
    w = wall or {}
    urls = list(w.get("urls") or [])
    if choice in DASHBOARDS:
        scr, name, _ = DASHBOARDS[choice]
        url = OWN + scr
        if url in urls:
            return None, name, f"🎧 DJ Jev: {name} is already on tile {urls.index(url) + 1}"
        same = {i for i in (0, 1) if i < len(urls) and _kind(urls[i]) == _kind(url)}
        i = next(iter(sorted(same)), 1)   # a map replaces the map: the dashboards never show two of a kind
        return {"op": "tile", "i": i, "url": url, "label": name}, name, ""
    s = streams().get(choice)
    if not s:
        return None, "", HELP
    name = s["name"]
    sound, want = w.get("sound"), bool(SOUND.search(text))
    if s["url"] in urls:   # already up: "turn on kornacki's audio" = the sound to that tile
        k = urls.index(s["url"])
        if want and k != sound:
            return {"op": "sound", "i": k, "label": name}, name, ""
        return None, name, f"🎧 DJ Jev: {name} is already on tile {k + 1}" + (" with the sound" if k == sound else "")
    ok = [i for i in (3, 2) if i <= max(len(urls), 2)]
    i = next((i for i in ok if (i == sound) == want), ok[0])
    return {"op": "tile", "i": i, "url": s["url"], "label": f"{s['group']} · {name}"}, name, ""


def reply(p, err: str, action: dict, name: str, conf: float) -> str:
    """The system line after hub.propose(…) returned (p, err)."""
    if err or not p:
        return f"🎧 DJ Jev: {err or 'that didn’t work'}"
    where, pct = f"{name} on tile {action['i'] + 1}", f"({round(conf * 100)}%)"
    if p.status in ("override", "passed") and action.get("op") == "sound":
        return f"🎧 DJ Jev: sound to {name} (tile {action['i'] + 1}) {pct}"
    if p.status in ("override", "passed"):
        return f"🎧 DJ Jev: putting {where} {pct}"
    if p.status == "open":
        return f"🎧 DJ Jev: vote open — {where} {pct}"
    if p.status == "error":
        return f"🎧 DJ Jev: couldn't put {where}"
    return f"🎧 DJ Jev: {where} didn't pass"
