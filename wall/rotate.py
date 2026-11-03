"""Election night: the wall's own data pages (/p/election/map, races, turnout, exitpoll) rotate on their tiles so the data tiles
don't sit on one screen all night. Each rotating tile moves to the next screen not already on the wall every EVERY_S, the
tiles staggered by half that so two never change together. A vote holds a tile ("hold") or moves it on now ("next");
either way it stays put HOLD_S and then rotates again by itself, so nobody has to remember to unlock it.
"""
from __future__ import annotations

SCREENS = ("map?view=S", "map?view=H", "map?view=G", "races", "turnout", "exitpoll")
EVERY_S, HOLD_S = 60.0, 300.0
MARK = "/p/election/"


def screen(url: str) -> str | None:
    """The screen a tile shows ("map?view=H"), or None when it isn't one of the rotating data pages."""
    if MARK not in url:
        return None
    rest = url.split(MARK, 1)[1]
    return rest if rest.split("?")[0] in ("map", "races", "turnout", "exitpoll") else None


def kind(s: str | None) -> str | None:
    """"map?view=S" → "map": the dashboards never show two of one kind at once (two maps side by side)."""
    return s.split("?")[0] if s else None


def next_url(urls: list[str], i: int) -> str | None:
    """Tile i's next screen: not one already on the wall, nor the same kind as another dashboard (None: nothing else)."""
    cur, taken = screen(urls[i]), {screen(u) for u in urls}
    others = {kind(screen(u)) for j, u in enumerate(urls) if j != i and screen(u)}
    start = SCREENS.index(cur) if cur in SCREENS else -1
    for k in range(1, len(SCREENS) + 1):
        s = SCREENS[(start + k) % len(SCREENS)]
        if s not in taken and kind(s) not in others:
            return urls[i].split(MARK, 1)[0] + MARK + s
    return None


class Rotation:
    def __init__(self) -> None:
        self.at: dict[int, float] = {}     # tile → when it changes next
        self.held: dict[int, float] = {}   # tile → held (a vote) until

    def reset(self) -> None:   # ponytail: keyed by tile index, so a move / remove / restart just starts the clocks over
        self.at.clear()
        self.held.clear()

    def hold(self, i: int, now: float) -> None:
        self.at[i] = self.held[i] = now + HOLD_S

    def due(self, urls: list[str], now: float) -> list[tuple[int, str]]:
        """(tile, its next screen's URL) for the rotating tiles whose turn it is; the caller swaps them in."""
        out, urls = [], list(urls)   # next_url keeps the kinds apart; a kind clash already on the wall heals on its next turn
        for i, u in enumerate(urls):
            if screen(u) is None:
                self.at.pop(i, None)
                continue
            if now < self.at.setdefault(i, now + EVERY_S * (1 + i / 2)):
                continue
            self.at[i] = now + EVERY_S
            nxt = next_url(urls, i)
            if nxt:
                urls[i] = nxt
                out.append((i, nxt))
        return out

    def view(self, urls: list[str], now: float) -> list[dict]:
        """The rotating tiles for the watch page: seconds to the next change and whether a vote is holding it."""
        return [{"i": i, "in": max(0, round(self.at.get(i, now + EVERY_S) - now)), "held": self.held.get(i, 0.0) > now}
                for i, u in enumerate(urls) if screen(u)]
