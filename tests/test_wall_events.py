"""wall/events.py: the first look is a baseline; new calls and a key race's first votes become events, calls first."""
from wall.events import Events


def race(i, st, o, votes=0, watch=True):
    return {"id": i, "state": st, "office": o, "votes": votes, "watch": watch}


def test_baseline_then_calls_and_first_votes():
    ev = Events()
    snap = {"races": [race("S-GA", "GA", "S"), race("G-GA", "GA", "G", 5), race("H-WI-3", "WI", "H")], "calls": [{"id": "G-GA"}]}
    assert ev.check(snap) == []                                   # baseline: G-GA's call and votes aren't news
    snap["races"][0]["votes"] = 10                                 # GA Senate starts counting
    snap["races"][2]["votes"] = 3                                  # a House race that isn't a key race: no event…
    snap["races"][2]["watch"] = False
    snap["calls"].append({"id": "H-WI-3"})                         # … but its call is
    assert ev.check(snap) == [{"race": "H-WI-3", "by": "race call", "state": "WI", "office": "H"},
                              {"race": "S-GA", "by": "first votes", "state": "GA", "office": "S"}]
    assert ev.check(snap) == []                                   # nothing new


def test_kalshi_move_once_then_quiet():
    ev, races = Events(), {"S-GA": race("S-GA", "GA", "S")}
    assert ev.check_odds({"S-GA": {"who": "Ossoff", "prob": 60}}, races, 0) == []
    assert ev.check_odds({"S-GA": {"who": "Ossoff", "prob": 66}}, races, 300) == []
    got = ev.check_odds({"S-GA": {"who": "Ossoff", "prob": 72}}, races, 600)
    assert got == [{"race": "S-GA", "by": "Kalshi move 12 pts", "state": "GA", "office": "S"}]
    assert ev.check_odds({"S-GA": {"who": "Ossoff", "prob": 90}}, races, 700) == []          # quiet for 20 min
    assert ev.check_odds({"S-GA": {"who": "Collins", "prob": 51}}, races, 2000) == []        # a new favourite starts over
