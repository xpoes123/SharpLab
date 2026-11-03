"""wall/rotate.py: election data tiles rotate, staggered, skipping screens already up; a hold/next sticks HOLD_S."""
from wall.rotate import EVERY_S, HOLD_S, Rotation, next_url, screen

B = "http://127.0.0.1:7795/p/election/"
URLS = [B + "map", B + "races", "https://www.youtube.com/@NBCNews/live"]


def test_screen_and_next():
    assert screen(B + "map?view=H") == "map?view=H" and screen(URLS[2]) is None and screen(B + "data.json") is None
    assert next_url(URLS, 0) == B + "map?view=S"                    # the unpinned map starts the cycle
    assert next_url([B + "map?view=G", B + "races"], 0) == B + "turnout"         # races is taken: skips it
    assert next_url([B + "turnout", B + "map?view=S"], 0) == B + "exitpoll"
    assert next_url([B + "exitpoll", B + "map?view=S"], 0) == B + "races"     # wraps past every map: tile 2 has one


def test_due_staggers_and_holds():
    r = Rotation()
    assert r.due(URLS, 0) == []
    assert [i for i, _ in r.due(URLS, EVERY_S)] == [0]                 # tile 1 first …
    assert [i for i, _ in r.due(URLS, EVERY_S * 1.5)] == [1]           # … tile 2 half a period later; never the stream
    r.hold(0, 100)
    assert r.due(URLS, 100 + HOLD_S - 1) == [] or all(i != 0 for i, _ in r.due(URLS, 100 + HOLD_S - 1))
    assert {v["i"]: v["held"] for v in r.view(URLS, 101)} == {0: True, 1: False}
    assert 0 in [i for i, _ in r.due(URLS, 100 + HOLD_S)]            # the hold runs out: rotates again by itself


def test_show_screen_skips_held_tiles_and_screens_already_up():
    from types import SimpleNamespace
    from wall.instance import Instance
    t = [100.0]
    got = []
    i = Instance.__new__(Instance)
    import threading
    i.lock, i.follow, i.clock, i.rot = threading.RLock(), False, lambda: t[0], Rotation()
    i.urls = [B + "races", B + "map?view=S", URLS[2]]
    i.driver = SimpleNamespace(set_tile=lambda k, u: got.append((k, u)))
    i._bump = lambda: None
    assert not i.show_screen("map?view=S")                # already up
    i.rot.hold(0, t[0])                                   # a vote holds tile 1
    assert i.show_screen("map?view=G") and got == [(1, B + "map?view=G")]
    assert i.rot.at[1] == t[0] + 2 * EVERY_S


def test_dashboards_never_two_of_a_kind():
    from wall.rotate import next_url
    assert next_url([B + "map?view=S", B + "races"], 1) == B + "turnout"          # not a second map next to the map
    assert next_url([B + "map?view=S", B + "races"], 0) == B + "map?view=H"      # a map may follow a map on the same tile
    from wall.headless import gain_stages
    assert gain_stages(20) == ["volume", "volume=10", "!", "volume", "volume=2", "!"] and gain_stages(2) == ["volume", "volume=2", "!"]
