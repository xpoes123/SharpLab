"""wall/rotate.py: election data tiles rotate, staggered, skipping screens already up; a hold/next sticks HOLD_S."""
from wall.rotate import EVERY_S, HOLD_S, Rotation, next_url, screen

B = "http://127.0.0.1:7795/p/election/"
URLS = [B + "map", B + "races", "https://www.youtube.com/@NBCNews/live"]


def test_screen_and_next():
    assert screen(B + "map?view=H") == "map?view=H" and screen(URLS[2]) is None and screen(B + "data.json") is None
    assert next_url(URLS, 0) == B + "map?view=S"                    # the unpinned map starts the cycle
    assert next_url([B + "map?view=G", B + "races"], 0) == B + "turnout"         # races is taken: skips it
    assert next_url([B + "turnout", B + "map?view=S"], 0) == B + "exitpoll"
    assert next_url([B + "exitpoll", B + "map?view=S"], 0) == B + "map?view=H"  # wraps, skipping the map tile 2 has


def test_due_staggers_and_holds():
    r = Rotation()
    assert r.due(URLS, 0) == []
    assert [i for i, _ in r.due(URLS, EVERY_S)] == [0]                 # tile 1 first …
    assert [i for i, _ in r.due(URLS, EVERY_S * 1.5)] == [1]           # … tile 2 half a period later; never the stream
    r.hold(0, 100)
    assert r.due(URLS, 100 + HOLD_S - 1) == [] or all(i != 0 for i, _ in r.due(URLS, 100 + HOLD_S - 1))
    assert {v["i"]: v["held"] for v in r.view(URLS, 101)} == {0: True, 1: False}
    assert 0 in [i for i, _ in r.due(URLS, 100 + HOLD_S)]            # the hold runs out: rotates again by itself
