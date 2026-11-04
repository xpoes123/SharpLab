import asyncio

import pytest

from web import wall, wall_commands as wc

KNICKS = "https://topstreamer.site/nba/knicks"
BULLS = "https://topstreamer.site/nba/bulls"
CELTICS = "https://topstreamer.site/nba/celtics"
BUCKS = "https://topstreamer.site/nba/bucks"
DAVID = {"id": "100", "name": "David"}


def team(abbr, name):
    return {"abbr": abbr, "name": name, "score": 99}


GAMES = [
    {"league": "nba", "away": team("CHI", "Bulls"), "home": team("NY", "Knicks"), "urls": [KNICKS, BULLS], "live": True},
    {"league": "nba", "away": team("MIL", "Bucks"), "home": team("BOS", "Celtics"), "urls": [CELTICS, BUCKS], "live": False},
]


def state(urls, sound=0):
    return {"running": True, "urls": urls, "sound": sound, "overlay": {"board": "nba"}}


def action(text, st):
    return wc.run(text, st, GAMES).action


def test_put_uses_empty_tile_then_avoids_the_sound():
    assert action("/put knicks", state([CELTICS])) == {"op": "tile", "i": 1, "url": KNICKS, "label": "CHI @ NY"}
    full = state(["https://a.example", "https://b.example", "https://c.example", "https://d.example"], sound=3)
    assert action("/put bulls", full)["i"] == 2


def test_put_takes_a_tile_and_refuses_a_game_already_up():
    assert action("/put knicks 1", state([CELTICS]))["i"] == 0
    assert wc.run("/put bulls", state([KNICKS]), GAMES).reply == "CHI @ NY is already on tile 1."


def test_team_lookup():
    assert action("/put BOS", state([]))["url"] == CELTICS
    assert "No game" in wc.run("/put lakers", state([]), GAMES).reply
    two = [{"league": "nfl", "away": team("A", "Bills"), "home": team("X", "New York Giants"), "urls": [], "live": True},
           {"league": "nfl", "away": team("B", "Bears"), "home": team("Y", "New York Jets"), "urls": [], "live": True}]
    assert wc.run("/put york", state([]), two).reply.startswith("Which one?")


def test_sound_by_tile_or_team():
    st = state([CELTICS, KNICKS])
    assert action("/sound 2", st)["i"] == 1
    assert action("/sound bulls", st)["i"] == 1


def test_sound_for_a_game_not_up():
    assert "isn't on the wall" in wc.run("/sound celtics", state([KNICKS]), GAMES).reply


def test_games_and_help_never_show_scores():
    out = wc.run("/games", state([KNICKS]), GAMES).reply
    assert out == "CHI @ NY (tile 1) · MIL @ BOS" and "99" not in out
    assert "/put <team> [tile]" in wc.run("/help", {}, GAMES).reply


def test_bad_input():
    assert "Unknown" in wc.run("/rm -rf", state([]), GAMES).reply
    assert "Usage" in wc.run("/put", state([]), GAMES).reply
    assert wc.run("/put knicks", {"running": False}, GAMES).reply == "The wall isn't running."
    assert "Unknown" in wc.run("/", state([]), GAMES).reply


def test_election_keeps_its_dashboard_tiles():
    st = {"running": True, "urls": ["http://127.0.0.1:7795/p/election/map"], "sound": 0, "overlay": {"board": "election"}}
    assert action("/put knicks", st)["i"] == 2


@pytest.mark.parametrize("reply,want", [
    ("/put knicks", "/put knicks"),
    ("  /sound 2\n", "/sound 2"),
    ("none", None),
    (None, None),
    ("/help", None),                 # agents can't run this
    ("/lift someone", None),         # not in the table
    ("/put knicks\n/put bulls", None),
    ("Sure! /put knicks", None),
])
def test_parse_agent(reply, want):
    assert wc.parse_agent(reply) == want


def test_agent_prompt_is_generated_from_the_table():
    s = wc.agent_system(GAMES)
    assert "/put <team> [tile]" in s and "/help" not in s and "CHI @ NY (Bulls at Knicks)" in s and "99" not in s


# ---- through the hub ---------------------------------------------------------------------------------------------------
def hub(monkeypatch, st):
    done = []

    async def execute(a):
        done.append(a)
        return True, ""

    async def fetch_state():
        return h.wall

    async def games(league=""):
        return GAMES

    monkeypatch.setattr(wc, "games", games)
    monkeypatch.setenv("WALL_OWNER_ID", DAVID["id"])
    h = wall.Hub(execute=execute, fetch_state=fetch_state, ask_jev=None)
    h.wall = st
    return h, done


def test_slash_command_applies_for_the_owner(monkeypatch):
    h, done = hub(monkeypatch, state([CELTICS]))
    assert asyncio.run(h.command(DAVID, "/put knicks")) == ""
    assert done[0]["url"] == KNICKS


@pytest.mark.asyncio
async def test_jev_agent_runs_the_command(monkeypatch):
    h, done = hub(monkeypatch, state([CELTICS]))

    async def translate(text, gs):
        return "/put knicks"
    monkeypatch.setattr(wc, "translate", translate)
    assert h.jev_command(DAVID, "jev put the knicks game up") == ""
    await asyncio.gather(*list(h._jev_tasks))
    assert done[0]["url"] == KNICKS


@pytest.mark.asyncio
async def test_plain_chat_on_a_sports_wall_never_reaches_an_agent(monkeypatch):
    h, done = hub(monkeypatch, state([CELTICS]))

    async def translate(text, gs):
        raise AssertionError("called")
    monkeypatch.setattr(wc, "translate", translate)
    assert h.jev_command(DAVID, "the knicks are trash tonight") == ""
    assert not h._jev_tasks and not done


@pytest.mark.asyncio
async def test_agent_with_no_answer_points_to_the_commands(monkeypatch):
    h, done = hub(monkeypatch, state([CELTICS]))

    async def translate(text, gs):
        return None
    monkeypatch.setattr(wc, "translate", translate)
    h.jev_command(DAVID, "jev do a backflip")
    await asyncio.gather(*list(h._jev_tasks))
    assert not done and any("/help" in m["text"] for m in h.chat.log)
