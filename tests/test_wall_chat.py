"""The watch party's chat on the TV (wall/chat.py + the service's loopback /chat): polling the web's bot API, deletes /
mutes, posting as the owner, and who may call it."""
import pytest
from starlette.testclient import TestClient

from wall.chat import KEEP, ChatFeed
from wall.instance import Instance
from wall.engine import Session
from wall.llm import LLMGuard
from wall.presets import PresetStore
from wall.server import Cfg, build

from tests.test_wall_service import FakeDriver, FakeHeadless, FakeScores

OWNER = "100"


def msg(i, text="hi", name="Kim", guest=False, ts=1000.0, kind="chat"):
    return {"id": i, "ts": ts, "kind": kind, "name": name, "guest": guest, "text": text}


class FakeWeb:
    """sharplab-web's /api/v1/wall/chat, as far as the feed sees it."""
    def __init__(self):
        self.log, self.viewers, self.calls, self.down, self.post_status = [], [], [], False, 200

    def __call__(self, method, path, body):
        self.calls.append((method, path, body))
        if self.down:
            raise OSError("connection refused")
        if method == "GET":
            after = int(path.split("after=")[1].split("&")[0])
            return 200, {"messages": [m for m in self.log if m["id"] > after], "ids": [m["id"] for m in self.log],
                         "last": self.log[-1]["id"] if self.log else 0, "viewers": self.viewers,
                         "watching": len(self.viewers), "active": len(self.viewers)}
        if self.post_status != 200:
            return self.post_status, {"error": "Slow down a little."}
        m = msg(len(self.log) + 1, body["text"], name="David")
        self.log.append(m)
        return 200, {"msg": m}


def feed(web=None):
    web = web or FakeWeb()
    return ChatFeed("http://web", "k", OWNER, http=web), web


def test_feed_polls_after_last_and_keeps_viewers():
    f, web = feed()
    web.log = [msg(1, "one"), msg(2, "two", name="Kim (guest)", guest=True)]
    web.viewers = [{"name": "David", "guest": False, "away": False}, {"name": "Kim (guest)", "guest": True, "away": False}]
    assert f.poll(1000.0)
    assert [m["text"] for m in f.messages] == ["one", "two"] and f.messages[1]["guest"] and f.watching == 2
    rev = f.rev
    assert not f.poll(1000.0) and f.rev == rev            # nothing new: no change
    assert web.calls[-1][1].startswith("/api/v1/wall/chat?after=2")
    web.log.append(msg(3, "three"))
    assert f.poll(1000.0) and [m["id"] for m in f.messages] == [1, 2, 3]


def test_feed_drops_deleted_and_muted_messages():
    f, web = feed()
    web.log = [msg(1, "keep"), msg(2, "rude"), msg(3, "muted one")]
    f.poll(1000.0)
    web.log = [m for m in web.log if m["id"] == 1]   # a moderator deleted 2; 3's author got muted (the web hides them)
    assert f.poll(1000.0) and [m["text"] for m in f.messages] == ["keep"]


def test_feed_skips_an_earlier_partys_messages_and_resets_on_a_new_wall():
    f, web = feed()
    web.log = [msg(1, "last week", ts=100.0), msg(2, "now", ts=5000.0)]
    f.poll(5000.0)
    assert [m["text"] for m in f.messages] == ["now"]
    web.log.append(msg(3, "later", ts=9000.0))
    f.poll(9000.0)   # a new discord wall (another start time): starts over
    assert [m["text"] for m in f.messages] == ["later"]


def test_feed_keeps_the_last_few_and_goes_idle():
    f, web = feed()
    web.log = [msg(i, str(i)) for i in range(1, 40)]
    f.poll(1000.0)
    assert len(f.messages) == KEEP and f.messages[-1]["id"] == 39
    f.idle()
    assert f.messages == [] and f.view(False)["on"] is False


def test_feed_error_is_shown_once_and_recovers():
    f, web = feed()
    web.down = True
    assert f.poll(1000.0) and "unreachable" in f.error
    assert not f.poll(1000.0)
    web.down = False
    assert f.poll(1000.0) is True and f.error == ""


def test_post_goes_as_the_owner_and_shows_at_once():
    f, web = feed()
    f.poll(1000.0)
    status, out = f.post("hello from the couch")
    assert status == 200 and web.calls[-1] == ("POST", "/api/v1/wall/chat", {"discord_id": OWNER, "text": "hello from the couch"})
    assert [m["text"] for m in f.messages] == ["hello from the couch"]
    f.poll(1000.0)
    assert len(f.messages) == 1   # the next poll doesn't add it twice
    web.post_status = 429
    assert f.post("again") == (429, {"error": "Slow down a little."})
    web.down = True
    assert f.post("x")[0] == 502


def test_unconfigured_feed_is_off():
    f = ChatFeed("", "", "")
    assert not f.configured and f.view(True)["on"] is False
    assert not ChatFeed("http://web", "k", "not-a-snowflake").configured


# ---- the service's /chat ----------------------------------------------------------------------------------------------------
@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr("wall.server.resolve", lambda h: [(2, 1, 6, "", ("93.184.216.34", 0))])
    monkeypatch.setattr("wall.server.tv_active", lambda api: None)
    cfg = Cfg({"WALL_STATE_DIR": str(tmp_path), "WALL_API_TOKEN": "s3cret", "WALL_IMPORT_FROM": "", "WALL_TV_OUTPUT": ""})
    store = PresetStore(str(tmp_path))
    (tmp_path / "hls").mkdir()
    tv = Instance("tv", Session("tv"), store, FakeScores({}), driver=FakeDriver(), run=lambda f: None)
    disc = Instance("discord", Session("discord"), store, FakeScores({}), driver=FakeDriver(), run=lambda f: None,
                    headless=FakeHeadless(tmp_path / "hls"), vram=lambda: None, sysload=lambda: (1.0, 64000))
    f, web = feed()
    app = build(cfg, {"tv": tv, "discord": disc}, FakeScores({}), store, guard=LLMGuard(mode="off"), chat=f)
    return app, disc, f, web


def local(app):
    return TestClient(app, base_url="http://127.0.0.1:7795", client=("127.0.0.1", 5555))


def test_service_chat_is_off_until_the_discord_wall_runs(svc):
    app, disc, f, web = svc
    c = local(app)
    assert c.get("/chat").json()["on"] is False
    r = c.post("/chat", json={"text": "hi"})
    assert r.status_code == 409 and "watch party" in r.json()["error"] and not [x for x in web.calls if x[0] == "POST"]
    assert c.post("/i/discord/start", json={"urls": ["https://a.tv"]}).json()["running"]
    web.log = [msg(1, "hey TV", ts=disc.started)]
    f.poll(disc.started)
    v = c.get("/chat").json()
    assert v["on"] and [m["text"] for m in v["messages"]] == ["hey TV"]
    r = c.post("/chat", json={"text": "hi all"})
    assert r.status_code == 200 and r.json()["msg"]["name"] == "David" and web.calls[-1][2]["discord_id"] == OWNER
    assert c.post("/chat", json={"text": "  "}).status_code == 422


def test_service_chat_is_loopback_only_and_refuses_pages(svc):
    app, disc, f, web = svc
    remote = TestClient(app, base_url="http://127.0.0.1:7795", client=("100.81.31.34", 5555), headers={"Authorization": "Bearer s3cret"})
    assert remote.get("/chat").status_code == 403 and remote.post("/chat", json={"text": "x"}).status_code == 403
    c = local(app)
    assert c.post("/chat", json={"text": "x"}, headers={"Origin": "https://evil.example"}).status_code == 403   # a tile's page
    assert c.get("/chat", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert set(c.get("/chat").json()) == {"on", "rev", "messages", "viewers", "watching", "active", "error", "owner", "configured"}
