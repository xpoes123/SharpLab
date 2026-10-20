"""News loops: when the LLM judge fails, post NOTHING and leave ids unseen so the
next cycle retries (regression: sportsnews used to post every HeadlineNews item)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from email.utils import format_datetime

import httpx
import pytest

import bot.cogs.chessnews as chess
import bot.cogs.sportsnews as sports


class FakeChannel:
    def __init__(self):
        self.sent = []

    async def send(self, **kw):
        self.sent.append(kw)


class FakeBot:
    def __init__(self, channel):
        self.channel = channel

    def get_channel(self, _id):
        return self.channel


@pytest.fixture
def settings(monkeypatch):
    store: dict[str, str] = {}

    async def get(key):
        return store.get(key)

    async def set_(key, val):
        store[key] = val

    for mod in (sports, chess):
        monkeypatch.setattr(mod.queries, "get_bot_setting", get)
        monkeypatch.setattr(mod.queries, "set_bot_setting", set_)
    return store


def _judge_returns(monkeypatch, mod, result):
    calls = []

    async def fake(prompt, *, api_key, max_tokens=300, local=False):
        calls.append(local)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(mod, "haiku_json", fake)
    return calls


# ── sportsnews ───────────────────────────────────────────────────────────────

def _sports_cog(monkeypatch, settings):
    now = datetime.now(timezone.utc).isoformat()
    arts = [{"id": str(i), "league": "nba", "headline": f"h{i}", "description": "",
             "type": "HeadlineNews", "published": now, "url": ""} for i in (1, 2, 3)]

    async def fetch(client, key):
        return [dict(a) for a in arts] if key == "nba" else []

    monkeypatch.setattr(sports, "_fetch_league", fetch)
    settings[sports._SEEN_KEY] = json.dumps(["old"])  # past cold start
    ch = FakeChannel()
    cog = sports.SportsNewsCog.__new__(sports.SportsNewsCog)
    cog.bot, cog.api_key = FakeBot(ch), "k"
    return cog, ch


@pytest.mark.parametrize("result", [None, RuntimeError("boom"), {"major": "not-a-list"}])
async def test_sportsnews_judge_failure_posts_nothing(monkeypatch, settings, result):
    cog, ch = _sports_cog(monkeypatch, settings)
    _judge_returns(monkeypatch, sports, result)
    await cog.news_check.coro(cog)
    assert ch.sent == []
    assert json.loads(settings[sports._SEEN_KEY]) == ["old"]  # unseen → retried next cycle


async def test_sportsnews_retries_then_posts_judged(monkeypatch, settings):
    cog, ch = _sports_cog(monkeypatch, settings)
    _judge_returns(monkeypatch, sports, None)
    await cog.news_check.coro(cog)
    calls = _judge_returns(monkeypatch, sports, {"major": ["2"]})
    await cog.news_check.coro(cog)
    assert calls == [True]  # local-first
    assert [s["embed"].title for s in ch.sent] == ["h2"]
    assert set(json.loads(settings[sports._SEEN_KEY])) == {"old", "1", "2", "3"}


async def test_sportsnews_no_key_no_local_posts_nothing(monkeypatch, settings):
    cog, ch = _sports_cog(monkeypatch, settings)
    cog.api_key = ""
    monkeypatch.setenv("OLLAMA_URL", "")
    await cog.news_check.coro(cog)  # real haiku_json → None
    assert ch.sent == []


# ── chessnews ────────────────────────────────────────────────────────────────

def _chess_cog(monkeypatch, settings):
    pub = format_datetime(datetime.now(timezone.utc))
    items = "".join(f"<item><title>c{i}</title><description>d</description>"
                    f"<link>https://chess.test/{i}</link><pubDate>{pub}</pubDate></item>"
                    for i in (1, 2))
    rss = f"<rss><channel>{items}</channel></rss>"
    real = httpx.AsyncClient
    monkeypatch.setattr(chess.httpx, "AsyncClient", lambda **kw: real(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text=rss)), **kw))
    settings[chess._SEEN_KEY] = json.dumps(["old"])
    ch = FakeChannel()
    cog = chess.ChessNewsCog.__new__(chess.ChessNewsCog)
    cog.bot, cog.api_key = FakeBot(ch), "k"
    return cog, ch


@pytest.mark.parametrize("result", [None, RuntimeError("boom")])
async def test_chessnews_judge_failure_posts_nothing(monkeypatch, settings, result):
    cog, ch = _chess_cog(monkeypatch, settings)
    _judge_returns(monkeypatch, chess, result)
    await cog.news_check.coro(cog)
    assert ch.sent == []
    assert json.loads(settings[chess._SEEN_KEY]) == ["old"]


async def test_chessnews_posts_judged(monkeypatch, settings):
    cog, ch = _chess_cog(monkeypatch, settings)
    calls = _judge_returns(monkeypatch, chess, {"major": ["https://chess.test/2"]})
    await cog.news_check.coro(cog)
    assert calls == [True]
    assert [s["embed"].title for s in ch.sent] == ["c2"]
