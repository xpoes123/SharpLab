"""What a wall is showing, in one place. Everything that behaves differently for an election or a league asks here
instead of checking the board or the URLs itself. A new event type is one more row in MODES and one more rule in of().
Pure, so both the wall service and sharplab-web (from the state it proxies) can use it."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

ELECTION_PAGES = "/p/election/"
OPEN_HOSTS = {"www.youtube.com", "youtube.com", "m.youtube.com"}   # broadcasters' own free streams


@dataclass(frozen=True)
class Mode:
    id: str
    label: str                    # the watch page's brand
    locked_tiles: int = 0         # the first n tiles are dashboards: streams can't go there
    public_stream: bool = False   # may the plain link watch the stream? (see public_stream(): the tiles must qualify too)


MODES = {m.id: m for m in (
    Mode("none", "The Wall"),
    Mode("election", "Live Election", locked_tiles=2, public_stream=True),
    Mode("nba", "Live NBA"),
    Mode("nfl", "Live NFL"),
)}


def of(board: str = "", urls: list[str] | tuple[str, ...] = (), league: str = "") -> Mode:
    """Election wins over a league: "Election + NBA" keeps its dashboards locked (public_stream() still refuses its games)."""
    if board == "election" or any(ELECTION_PAGES in u for u in urls):
        return MODES["election"]
    return MODES.get(board if board in ("nba", "nfl") else league) or MODES["none"]


def of_state(st: dict | None) -> Mode:
    """The mode of a wall state (GET /i/{inst}); works on a mirror too, which carries the TV's tiles and board."""
    st = st or {}
    live = st.get("live")
    return of((st.get("overlay") or {}).get("board") or "", st.get("urls") or [],
              (live.get("league") if isinstance(live, dict) else live) or "")


def _open_tile(url: str) -> bool:
    return ELECTION_PAGES in url or urlparse(url).hostname in OPEN_HOSTS


def public_stream(st: dict | None) -> bool:
    """Fails closed: a running wall in a public mode, no live-games plan, and every tile on the allowlist."""
    st = st or {}
    urls = st.get("urls") or []
    return bool(st.get("running") and urls and of_state(st).public_stream and not st.get("live") and all(map(_open_tile, urls)))


def annotate(st: dict) -> dict:
    """What the watch page needs to know: the state as sharplab-web keeps and broadcasts it."""
    m = of_state(st)
    return {**st, "mode": m.id, "mode_label": m.label, "public_stream": public_stream(st)}
