"""/wall: permission + routing, embed text, option parsing, and the client's paths (mocked httpx)."""
import json
from types import SimpleNamespace

import httpx
import pytest

from bot.cogs import wall as w
from bot.wall_client import WallError, WallService, WallWeb


# ---- permission + routing ----
def test_may_use_anyone_and_roles():
    assert w.may_use("5", set(), permission="anyone", allowed=set(), owner="1")
    assert not w.may_use("5", {"9"}, permission="roles", allowed={"7"}, owner="1")
    assert w.may_use("5", {"9", "7"}, permission="roles", allowed={"7"}, owner="1")
    assert w.may_use("1", set(), permission="roles", allowed={"7"}, owner="1")   # owner always


def test_route_owner_applies_everyone_else_proposes():
    assert w.route("1", owner="1") == "apply"
    assert w.route("2", owner="1") == "propose"
    assert w.route("2", owner="") == "propose"   # no owner configured: nobody bypasses the vote


class FakeSvc:
    def __init__(self): self.applied = []
    async def apply(self, a): self.applied.append(a); return {}
    async def state(self): return {}


class FakeWeb:
    watch_url = "https://x/wall"
    def __init__(self, resp=None): self.proposed, self.resp = [], resp or {"id": "p1", "summary": "s", "needed": 2}
    async def propose(self, user, a): self.proposed.append((user, a)); return self.resp
    async def state(self): return {}


def _interaction(uid, roles=()):
    user = SimpleNamespace(id=int(uid), display_name="U", name="u", avatar=None, roles=[SimpleNamespace(id=int(r)) for r in roles])
    return SimpleNamespace(user=user, guild_id=None, channel=None)


@pytest.fixture
def cog(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "STATE_PATH", str(tmp_path / "wall_bot.json"))
    monkeypatch.setattr(w, "OWNER_ID", "1")
    monkeypatch.setattr(w, "PERMISSION", "roles")
    monkeypatch.setattr(w, "ROLE_IDS", {"7"})
    return w.WallCog(bot=SimpleNamespace(), service=FakeSvc(), web=FakeWeb())


async def test_owner_applies_at_once(cog):
    msg = await cog.act(_interaction("1"), {"op": "stop"})
    assert cog.svc.applied == [{"op": "stop"}] and not cog.web.proposed
    assert msg.startswith("Done")


async def test_member_with_role_proposes(cog):
    msg = await cog.act(_interaction("5", roles=["7"]), {"op": "sound", "i": 1})
    assert not cog.svc.applied
    (user, action), = cog.web.proposed
    assert user["id"] == "5" and action == {"op": "sound", "i": 1}
    assert msg.startswith("Proposed")


async def test_member_without_role_is_refused(cog):
    msg = await cog.act(_interaction("5", roles=["8"]), {"op": "stop"})
    assert not cog.svc.applied and not cog.web.proposed
    assert "not allowed" in msg


async def test_proposal_that_applied_immediately_says_done(cog):
    cog.web.resp = {"id": "p2", "applied": True}
    assert (await cog.act(_interaction("5", roles=["7"]), {"op": "stop"})).startswith("Done")


# ---- embed ----
def test_watching_line_truncates_and_orders_away_last():
    vs = [{"name": "Kim", "away": True}, {"name": "David"}, {"name": "Steph"}, {"name": "A"}, {"name": "B"}, {"name": "C"}]
    assert w.watching_line(vs) == "Watching: David, Steph, A, +3"
    assert w.watching_line([{"name": "David"}, {"name": "Kim", "away": True}]) == "Watching: David, Kim (away)"
    assert w.watching_line([]) == "Watching: nobody yet"


def test_build_embed_running():
    st = {"running": True, "name": "Live NFL", "urls": ["https://topstreamer.site/nfl/bills", "https://twitch.tv/x"], "sound": 1,
          "live": {"league": "nfl", "labels": ["BAL 7–10 BUF · Q2", None]}, "overlay": {"layout": "lbar", "board": "nfl", "visible": True}}
    web = {"viewers": [{"name": "David"}, {"name": "Steph"}], "proposals": [{"id": "p", "summary": "tile 2 → x", "yes": 1, "no": 0, "needed": 2, "expires": 0}]}
    e = w.build_embed(st, web, "https://s/wall")
    text = json.dumps(e.to_dict(), ensure_ascii=False)
    assert "Live NFL" in e.title and "BAL 7–10 BUF" in text and "x 🔊" in text
    assert "Watching: David, Steph" in text and "tile 2 → x" in text and "https://s/wall" in text


def test_build_embed_off_and_error():
    e = w.build_embed(None, None, "https://s/wall", "wall: unreachable")
    assert "off" in e.title and e.footer.text == "wall: unreachable"


# ---- options ----
def test_clean_url():
    assert w.clean_url("topstreamer.site/nfl/bills") == "https://topstreamer.site/nfl/bills"
    assert w.clean_url(" https://a.b/c ") == "https://a.b/c"
    for bad in ("--disable-web-security", "file:///etc/passwd", "javascript:alert(1)", "http://a b", ""):
        with pytest.raises(ValueError):
            w.clean_url(bad)


def test_parse_urls_caps_at_four():
    assert w.parse_urls(["a.com", None, "", "https://b.com"]) == ["https://a.com", "https://b.com"]
    with pytest.raises(ValueError):
        w.parse_urls(["a.com"] * 5)


def test_tile_index():
    assert w.tile_index(3, 2) == 2            # adding the third
    assert w.tile_index(1, 0) == 0
    with pytest.raises(ValueError):
        w.tile_index(4, 2)                    # can't skip ahead
    with pytest.raises(ValueError):
        w.tile_index(3, 2, adding=False)
    with pytest.raises(ValueError):
        w.tile_index(5, 4)


def test_start_action():
    assert w.start_action("Live NFL", None, None, [], None, None) == {"op": "start", "preset": "Live NFL"}
    assert w.start_action(None, "nba", 2, [], "full", "nba") == {"op": "start", "urls": [], "live": "nba", "tiles": 2, "layout": "full", "board": "nba"}
    assert w.start_action(None, None, None, [None], None, None) == {"op": "start", "mirror": True}
    assert w.describe({"op": "start", "mirror": True}) == "start the wall: same as the TV"


def test_set_actions():
    st = {"running": True, "urls": ["https://a.com", "https://b.com"]}
    assert w.set_actions(st, 3, None, "nfl/bills", None, False, None, None, None) == \
        [{"op": "tile", "i": 2, "url": "https://topstreamer.site/nfl/bills"}]
    assert w.set_actions(st, None, "c.com", None, None, False, 2, "full", "") == [
        {"op": "tile", "i": 2, "url": "https://c.com"}, {"op": "sound", "i": 1},
        {"op": "board", "layout": "full", "board": "", "visible": False}]
    assert w.set_actions(st, 2, None, None, None, True, None, None, None) == [{"op": "remove", "i": 1}]
    with pytest.raises(ValueError):
        w.set_actions(st, None, None, None, None, False, None, None, None)
    with pytest.raises(ValueError):
        w.set_actions({"running": False, "urls": []}, 1, "a.com", None, None, False, None, None, None)
    with pytest.raises(ValueError):
        w.set_actions(st, None, None, None, None, False, 3, None, None)   # sound on a tile that isn't there


def test_split_teams_fits_selects():
    parts = w.split_teams([f"t{i:02}" for i in range(32)])
    assert len(parts) == 2 and all(len(p) <= 25 for p in parts) and sum(map(len, parts)) == 32


def test_describe():
    assert w.describe({"op": "tile", "i": 0, "url": "https://topstreamer.site/nfl/bills"}) == "tile 1 → bills"
    assert w.describe({"op": "sound", "i": 2}) == "sound → tile 3"
    assert w.describe({"op": "start", "live": "nfl"}) == "start the wall: live NFL"


# ---- clients ----
def _recorder(resp=None, status=200):
    seen = []

    def handle(req: httpx.Request):
        seen.append((req.method, str(req.url), dict(req.headers), json.loads(req.content) if req.content else None))
        return httpx.Response(status, json=resp if resp is not None else {"ok": True})
    return seen, httpx.MockTransport(handle)


async def test_service_paths_and_bearer():
    seen, t = _recorder()
    s = WallService("http://h:7795/", "tok", transport=t)
    await s.presets()
    await s.live("nfl", 3)
    await s.teams()
    await s.state()
    for a in ({"op": "start", "preset": 2}, {"op": "stop"}, {"op": "tile", "i": 1, "url": "https://x"},
              {"op": "remove", "i": 1}, {"op": "board", "layout": "full"}, {"op": "sound", "i": 0}):
        await s.apply(a)
    assert [(m, u) for m, u, _, _ in seen] == [
        ("GET", "http://h:7795/presets"), ("GET", "http://h:7795/live/nfl?n=3"), ("GET", "http://h:7795/teams"),
        ("GET", "http://h:7795/i/discord"), ("POST", "http://h:7795/i/discord/start"), ("POST", "http://h:7795/i/discord/stop"),
        ("POST", "http://h:7795/i/discord/tile"), ("DELETE", "http://h:7795/i/discord/tile/1"),
        ("POST", "http://h:7795/i/discord/board"), ("POST", "http://h:7795/i/discord/sound")]
    assert all(h["authorization"] == "Bearer tok" for _, _, h, _ in seen)
    assert seen[4][3] == {"preset": 2} and seen[6][3] == {"i": 1, "url": "https://x"}   # "op" never sent to the service


async def test_service_error_message_surfaces():
    _, t = _recorder({"error": "not enough free VRAM"}, status=409)
    with pytest.raises(WallError, match="VRAM"):
        await WallService("http://h", "t", transport=t).apply({"op": "start", "preset": 0})


async def test_service_unreachable_is_wallerror():
    def boom(req): raise httpx.ConnectError("no route")
    with pytest.raises(WallError, match="unreachable"):
        await WallService("http://h", "t", transport=httpx.MockTransport(boom)).state()


async def test_web_paths_and_key():
    seen, t = _recorder({"url": "https://s/wall/login?t=abc"})
    web = WallWeb("https://s", "sec", transport=t)
    await web.state()
    await web.propose({"id": "5", "name": "Kim", "avatar": None}, {"op": "stop"})
    await web.vote("5", "p1", False)
    assert await web.link({"id": "5", "name": "Kim"}) == "https://s/wall/login?t=abc"
    assert [(m, u) for m, u, _, _ in seen] == [("GET", "https://s/api/v1/wall/state"), ("POST", "https://s/api/v1/wall/propose"),
                                               ("POST", "https://s/api/v1/wall/vote"), ("POST", "https://s/api/v1/wall/link")]
    assert all(h["x-api-key"] == "sec" for _, _, h, _ in seen)
    assert seen[1][3] == {"discord_id": "5", "name": "Kim", "avatar": None, "action": {"op": "stop"}}
    assert seen[2][3] == {"discord_id": "5", "proposal_id": "p1", "yes": False}
    assert web.watch_url == "https://s/wall.html"


async def test_link_message_falls_back_to_login_hint():
    def nope(req): return httpx.Response(403, json={"error": "not a member"})
    msg = await w.link_message(WallWeb("https://s", "x", transport=httpx.MockTransport(nope)),
                               SimpleNamespace(id=5, display_name="K", name="k", avatar=None))
    assert "not a member" in msg and "https://s/wall" in msg


async def test_link_message_suppresses_the_preview():
    """The link is single-use: an unfurl fetch by Discord would spend it before the member clicks."""
    def ok(req): return httpx.Response(200, json={"url": "https://s/api/v1/wall/login?t=abc", "ttl": 600})
    msg = await w.link_message(WallWeb("https://s", "x", transport=httpx.MockTransport(ok)),
                               SimpleNamespace(id=5, display_name="K", name="k", avatar=None))
    assert "<https://s/api/v1/wall/login?t=abc>" in msg


# ---- where a start's link goes (persisted) ----
class Chan:
    def __init__(self, cid, gid=1):
        self.id, self.guild, self.sent = cid, SimpleNamespace(id=gid), []

    async def send(self, content=None, embed=None, view=None):
        m = SimpleNamespace(id=1000 + len(self.sent), channel=self, embed=embed, view=view)
        self.sent.append(m)
        return m

    def get_partial_message(self, mid):
        async def edit(**kw): pass
        return SimpleNamespace(edit=edit)


class StartSvc(FakeSvc):
    def __init__(self):
        super().__init__()
        self.st = {"running": False}

    async def apply(self, a):
        self.applied.append(a)
        if a["op"] == "start":
            self.st = {"running": True, "started": 100.0 + len(self.applied), "name": "Mirroring the TV", "mirror": True,
                       "capture": "screen", "urls": ["https://a.tv"], "origin": a.get("origin")}
        return self.st

    async def state(self): return self.st
    async def presets(self): return [{"name": "Live NFL"}]
    async def live(self, lg, n): return []


@pytest.fixture
def chans():
    return {77: Chan(77), 88: Chan(88), 99: Chan(99, gid=2)}


@pytest.fixture
def scog(monkeypatch, tmp_path, chans):
    monkeypatch.setattr(w, "OWNER_ID", "1")
    monkeypatch.setattr(w, "PERMISSION", "anyone")
    monkeypatch.setattr(w, "STATE_PATH", str(tmp_path / "wall_bot.json"))
    monkeypatch.setattr(w, "CHANNEL_ID", "99")
    return w.WallCog(bot=SimpleNamespace(get_channel=chans.get), service=StartSvc(), web=FakeWeb())


def _in(uid, channel, guild=1):
    user = SimpleNamespace(id=int(uid), display_name="U", name="u", avatar=None, roles=[])
    return SimpleNamespace(user=user, guild_id=guild, channel_id=channel, channel=None)


def test_announce_target():
    assert w.announce_target({"origin": {"guild": "1", "channel": "5"}}, {"channel": "6"}, "7") == 5
    assert w.announce_target({"origin": None}, {"guild": "1", "channel": "6"}, "7") == 6
    assert w.announce_target({}, None, "7") == 7 and w.announce_target({}, None, "") is None


async def test_owner_bare_start_posts_the_link_where_it_was_started(scog, chans, tmp_path):
    msg = await scog.act(_in("1", 77), w.start_action(None, None, None, [None], None, None))
    assert msg.startswith("Done") and scog.svc.applied == [{"op": "start", "mirror": True, "origin": {"guild": "1", "channel": "77"}}]
    (m,) = chans[77].sent
    text = json.dumps(m.embed.to_dict(), ensure_ascii=False)
    assert "Mirroring the TV" in text and "https://x/wall" in text
    assert any(getattr(c, "custom_id", "") == "wall:link" for c in m.view.children)   # Get my link
    await scog.watch_loop()   # the same start: not posted twice
    assert len(chans[77].sent) == 1
    again = w.WallCog(bot=SimpleNamespace(get_channel=chans.get), service=scog.svc, web=FakeWeb())   # a bot restart
    assert again.embeds == {1: (77, m.id)} and again.announced == scog.announced and again.last["channel"] == "77"


async def test_a_vote_that_passed_posts_in_the_proposers_channel(scog, chans):
    msg = await scog.act(_in("5", 88), {"op": "start", "mirror": True})
    assert msg.startswith("Proposed") and scog.web.proposed[0][1]["origin"] == {"guild": "1", "channel": "88"}
    # the vote passes on the page; sharplab-web starts the wall with that origin
    await scog.svc.apply(scog.web.proposed[0][1])
    await scog.watch_loop()
    assert len(chans[88].sent) == 1 and not chans[77].sent


async def test_a_start_from_the_web_page_goes_to_the_last_channel_else_the_default(scog, chans):
    await scog.svc.apply({"op": "start", "preset": "Live NFL"})   # no origin, no channel used yet: WALL_CHANNEL_ID
    await scog.watch_loop()
    assert len(chans[99].sent) == 1
    await scog.act(_in("1", 77), {"op": "stop"})                 # /wall used in 77 since
    await scog.svc.apply({"op": "start", "preset": "Live NFL"})
    await scog.watch_loop()
    assert len(chans[77].sent) == 1


async def test_panel_view_is_persistent_and_offers_same_as_the_tv(scog):
    v = w.PanelView(scog, [], [], persistent=True)
    assert v.is_persistent()
    preset = next(c for c in v.children if getattr(c, "custom_id", "") == "wall:preset")
    assert preset.options[0].value == w.TV_CHOICE and {c.custom_id for c in v.children} >= {"wall:follow", "wall:link", "wall:game"}
    assert w.describe({"op": "follow"}) == "follow the TV again"


async def test_service_follow_path():
    seen, t = _recorder()
    await WallService("http://h", "t", transport=t).apply({"op": "follow"})
    assert seen[0][:2] == ("POST", "http://h/i/discord/follow")
