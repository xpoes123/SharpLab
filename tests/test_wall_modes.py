from wall import modes, presets, spectrum

MAP = "http://127.0.0.1:7795/p/election/map"
YT = "https://www.youtube.com/@NBCNews/live"
GAME = "https://topstreamer.site/nba/knicks"


def state(urls, board="", live=None, running=True):
    return {"running": running, "urls": urls, "overlay": {"board": board}, "live": live}


def test_mode_detection():
    assert modes.of().id == "none"
    assert modes.of("nba").id == "nba"
    assert modes.of(league="nfl").id == "nfl"
    assert modes.of("election").locked_tiles == 2
    assert modes.of(urls=[MAP]).id == "election"
    assert modes.of("nba", [MAP]).id == "election"   # Election + NBA keeps its dashboards locked
    assert modes.of_state(state([GAME], live={"league": "nba"})).id == "nba"


def test_election_night_stays_public():
    assert modes.public_stream(state([MAP, YT], "election"))
    for p in presets.ELECTION_SEED:
        if not p.get("live"):
            assert modes.public_stream(state(p["urls"], p["overlay"]))
    assert all(modes.public_stream(state([MAP, s["url"]], "election")) for s in spectrum.load())


def test_sports_never_public():
    assert not modes.public_stream(state([GAME], "nba", {"league": "nba"}))
    assert not modes.public_stream(state([YT], "nba"))                       # a league wall, even on YouTube
    assert not modes.public_stream(state([MAP, GAME], "election"))           # a game on an election wall
    assert not modes.public_stream(state([MAP, YT], "election", {"league": "nba"}))
    assert not modes.public_stream(state([MAP, "https://youtube.com.evil.example/x"], "election"))


def test_fails_closed():
    assert not modes.public_stream(None)
    assert not modes.public_stream({})
    assert not modes.public_stream(state([], "election"))
    assert not modes.public_stream(state([MAP], "election", running=False))


def test_annotate():
    a = modes.annotate(state([GAME], "nfl"))
    assert (a["mode"], a["mode_label"], a["public_stream"]) == ("nfl", "Live NFL", False)
