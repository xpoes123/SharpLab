"""Chat commands to Jev on the watch page (web/wall_jev.py + Hub.jev_command): routing to a dashboard / Spectrum tile,
owner applies vs a vote, already up, the on-break gate, low confidence, timeouts, rate limits, who may. Jev is stubbed."""
import asyncio

import pytest

from tests.test_wall_web import OWNER, Clock, env  # noqa: F401 — the env fixture
from web import wall, wall_jev

OWN = wall_jev.OWN
FOX = "https://www.youtube.com/@FoxNews/live"
ABC = "https://www.youtube.com/@ABCNews/live"
FRIEND = {"id": "g-fr", "name": "Pal", "guest": True, "tier": "friend"}
MEMBER = {"id": "300", "name": "Steph"}
DAVID = {"id": OWNER, "name": "David"}


def sid(name):
    return next(k for k, s in wall_jev.streams().items() if s["name"] == name)


def elec(urls=None, sound=2, label="coverage"):
    urls = urls or [OWN + "races", OWN + "map?view=S", ABC, "https://www.youtube.com/@NBCNews/live"]
    return {"running": True, "urls": urls, "sound": sound, "overlay": {"board": "election"},
            "spectrum": {FOX: {"label": label, "live": True}, ABC: {"label": "coverage", "live": True}}}


class Jev:
    def __init__(self, choice="none", conf=0.9, delay=0.0, fail=False):
        self.choice, self.conf, self.delay, self.fail, self.calls = choice, conf, delay, fail, []

    async def __call__(self, state, questions):
        self.calls.append((state, questions))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            return None
        return {"want": {"choice": self.choice, "confidence": self.conf}}


def make(jev, state=None):
    done = []

    async def execute(a):
        done.append(a)
        return True, ""

    async def fetch_state():
        return h.wall

    h = wall.Hub(clock=Clock(), execute=execute, fetch_state=fetch_state, ask_jev=jev)
    h.wall = state or elec()
    for cid, u in ((1, DAVID), (2, MEMBER), (3, FRIEND)):
        h.presence.join(cid, u, 1000)
    return h, done


async def run(h, user, text):
    m, err = await h.post_chat(user, text)
    assert m, err
    await asyncio.gather(*list(h._jev_tasks))
    return [x["text"] for x in h.chat.log if x["kind"] == "system" and x["text"].startswith("🎧")]


def test_question_shape():
    q = wall_jev.questions()["want"]
    assert q["type"] == "choice" and q["question"] == wall_jev.QUESTION
    c = q["criteria"]
    assert {"d_mapS", "d_mapH", "d_mapG", "d_races", "d_turnout", "d_exitpoll", "d_swing", "none", "s1"} <= set(c)
    assert "2024" in c["d_swing"] and "'fox news'" in c[sid("Fox News")]
    assert wall_jev.is_command("Jev put fox on") and wall_jev.is_command("  jev, senate") and not wall_jev.is_command("jevvy")
    assert len(wall_jev.state("x" * 1000)) == len("A viewer wrote in the watch-party chat: ") + 300
    assert "'kornacki'" in c[sid("NBC News")]


@pytest.mark.anyio
async def test_owner_dashboard_applies_on_the_other_tile():
    jev = Jev("d_swing", 0.91)
    h, done = make(jev)
    out = await run(h, DAVID, "jev can you show the 2024 dashboards?")
    assert out == ["🎧 DJ Jev: putting vs 2024 on tile 2 (91%)"]
    assert done == [{"op": "tile", "i": 1, "url": OWN + "swing", "label": "vs 2024"}]
    assert "2024 dashboards" in jev.calls[0][0]


@pytest.mark.anyio
async def test_dashboard_replaces_the_tile_holding_the_same_kind():
    h, done = make(Jev("d_mapH", 0.9))
    await run(h, DAVID, "jev house map")   # tile 2 holds the Senate map: the House map replaces it (never two maps)
    assert done[0]["i"] == 1 and done[0]["url"] == OWN + "map?view=H"


@pytest.mark.anyio
async def test_dashboard_already_up():
    h, done = make(Jev("d_mapS", 0.95))
    assert await run(h, MEMBER, "jev senate map pls") == ["🎧 DJ Jev: Senate map is already on tile 2"]
    assert not done and not h.votes.props


@pytest.mark.anyio
async def test_friend_stream_opens_a_vote_on_the_silent_tile():
    h, done = make(Jev(sid("Fox News"), 0.87))
    out = await run(h, FRIEND, "Jev put fox on")
    assert out == ["🎧 DJ Jev: vote open — Fox News on tile 4 (87%)"]
    p = h.votes.open()[0]
    assert p.uid == FRIEND["id"] and p.action == {"op": "tile", "i": 3, "url": FOX, "label": "Right · Fox News"} and not done


@pytest.mark.anyio
async def test_stream_with_sound_goes_on_the_sound_tile():
    h, done = make(Jev(sid("Fox News"), 0.9))
    await run(h, DAVID, "jev put fox on with sound")
    assert done[0]["i"] == 2


@pytest.mark.anyio
async def test_stream_on_a_break_is_refused():
    h, done = make(Jev(sid("Fox News"), 0.9), elec(label="break"))
    assert await run(h, DAVID, "jev fox") == ["🎧 DJ Jev: Fox News is on a break — try again in a minute."]
    assert not done


@pytest.mark.anyio
async def test_stream_already_on_the_wall():
    h, done = make(Jev(sid("ABC News"), 0.9))
    assert await run(h, DAVID, "jev put abc on") == ["🎧 DJ Jev: ABC News is already on tile 3 with the sound"]


@pytest.mark.anyio
@pytest.mark.parametrize("jev", [Jev("none", 0.99), Jev("d_races", 0.5), Jev("bogus", 0.99)])
async def test_low_confidence_or_none(jev):
    h, done = make(jev)
    assert await run(h, MEMBER, "jev what's up") == [wall_jev.HELP]
    assert not done and not h.votes.props


@pytest.mark.anyio
async def test_unreachable_and_timeout(monkeypatch):
    h, _ = make(Jev(fail=True))
    assert await run(h, MEMBER, "jev races") == [wall_jev.UNREACHABLE]
    monkeypatch.setattr(wall_jev, "TIMEOUT_S", 0.05)
    h, done = make(Jev("d_races", 0.9, delay=1))
    assert await run(h, MEMBER, "jev races") == [wall_jev.UNREACHABLE] and not done


@pytest.mark.anyio
async def test_rate_limits():
    jev = Jev("none")
    h, _ = make(jev)
    assert h.jev_command(MEMBER, "jev a") == ""
    assert "10 s" in h.jev_command(MEMBER, "jev b")
    h.clock.t += 11
    assert h.jev_command(MEMBER, "jev c") == ""
    for k in range(4):
        assert h.jev_command({"id": f"u{k}", "name": "x"}, "jev d") == ""
    assert "busy" in h.jev_command({"id": "u9", "name": "x"}, "jev e")   # 6 a minute across everyone
    await asyncio.gather(*list(h._jev_tasks))
    assert len(jev.calls) == 6


@pytest.mark.anyio
async def test_public_and_unverified_cant_and_other_chat_is_untouched():
    jev = Jev("d_races", 0.99)
    h, done = make(jev)
    assert h.jev_command(wall.PUBLIC, "jev races") == ""
    assert h.jev_command({"id": "g-x", "name": "Nick", "guest": True}, "jev races") == ""
    assert not jev.calls and not done                                   # public / unverified: Jev never asked
    h2, done2 = make(Jev("none", 0.99))                                 # members' ordinary chat: asked, but nothing happens
    assert await run(h2, MEMBER, "hey jev races") == [] and await run(h2, MEMBER, "lol what a night") == [] and not done2


class WS:
    def __init__(self):
        self.sent = []

    async def send_json(self, m):
        self.sent.append(m)


@pytest.mark.anyio
async def test_socket_chat_posts_first_then_jev_answers():
    h, done = make(Jev("d_turnout", 0.8))
    ws = WS()
    h.socks[2] = (ws, MEMBER)
    await wall.handle(h, 2, ws, MEMBER, {"t": "chat", "text": "jev turnout"})
    assert ws.sent[0]["msg"]["text"] == "jev turnout"   # the chat line goes out before Jev is asked
    await asyncio.gather(*list(h._jev_tasks))
    assert any(m["t"] == "chat" and m["msg"]["text"] == "🎧 DJ Jev: vote open — Turnout on tile 2 (80%)" for m in ws.sent)
    h.clock.t += 2
    await wall.handle(h, 2, ws, MEMBER, {"t": "chat", "text": "jev again"})
    assert ws.sent[-1] == {"t": "error", "text": "One Jev command every 10 s.", "chat": True}


@pytest.mark.anyio
async def test_plain_lines_act_only_when_sure_and_never_reply():
    h, done = make(Jev("d_swing", 0.96))
    assert await run(h, DAVID, "can i see the 2024 dashboard?") == ["🎧 DJ Jev: putting vs 2024 on tile 2 (96%)"]
    h, done = make(Jev("d_swing", 0.6))
    assert await run(h, DAVID, "the 2024 numbers were wild") == [] and not done      # unsure: silent, nothing changes
    h, done = make(Jev("none", 0.99))
    assert await run(h, DAVID, "lol this is great") == [] and not done


@pytest.mark.anyio
async def test_no_votes_mode_jev_applies_and_panel_proposals_refused(monkeypatch):
    monkeypatch.setenv("WALL_NO_VOTES", "1")
    h, done = make(Jev("d_swing", 0.96))
    assert await run(h, MEMBER, "jev show vs 2024") == ["🎧 DJ Jev: putting vs 2024 on tile 2 (96%)"] and done
    p, err = await h.propose(MEMBER, {"op": "layout", "layout": "full"})
    assert p is None and "DJ Jev" in err


@pytest.mark.anyio
async def test_audio_request_for_a_stream_already_up_moves_the_sound():
    h, done = make(Jev(sid("NBC News"), 0.9))          # NBC is on tile 4, the sound is on tile 3 (ABC)
    out = await run(h, DAVID, "turn on kornacki's audio, i don't care about this guy")
    assert done and done[-1]["op"] == "sound" and done[-1]["i"] == 3 and out == ["🎧 DJ Jev: sound to NBC News (tile 4) (90%)"]
