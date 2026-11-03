"""Election night "Spectrum": a lineup of official YouTube election streams from hard left to hard right
(web/static/spectrum.json, also served to the watch page as /spectrum.json). Verified viewers may put one on a stream tile
(3–4) only while ad-skip labels it "coverage" — never at a break or an ad. The wall service lists the catalog in each
election-mode state ("spectrum": {url: {label, live, starts}}) so wall/adwatch.py listens to it; the web refuses the
vote and the wall service refuses the tile otherwise."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "web" / "static" / "spectrum.json"


@lru_cache(maxsize=4)
def load(path: Path = PATH) -> tuple[dict, ...]:
    """Every stream, flat: ({group, name, url, note}, …). A missing / broken file = no catalog."""
    try:
        groups = json.loads(path.read_text()).get("groups") or []
    except (OSError, ValueError, AttributeError):
        return ()
    return tuple({"group": g["label"], "name": s["name"], "url": s["url"], "note": s.get("note", "")}
                 for g in groups for s in g.get("streams") or [])


def urls(path: Path = PATH) -> list[str]:
    return [s["url"] for s in load(path)]


def find(url: str, path: Path = PATH) -> dict | None:
    return next((s for s in load(path) if s["url"] == url), None)


def gate(url: str, status: dict | None, path: Path = PATH) -> str:
    """Why this catalog stream can't go on a tile now ("" = it can, or it isn't a catalog stream). status = the wall
    state's spectrum entry ({label, live, starts}): only a fresh "coverage" label lets it through."""
    s = find(url, path)
    if not s or (status or {}).get("label") == "coverage":
        return ""
    if (status or {}).get("live") is False and not (status or {}).get("label"):
        return f"{s['name']} isn't live yet."
    return f"{s['name']} is on a break — try again in a minute."
