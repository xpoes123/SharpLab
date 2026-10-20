"""shared.llm: local-first Ollama with Haiku fallback; nothing ever raises."""
from __future__ import annotations

import json

import httpx
import pytest

import shared.llm as llm

PROMPT = "Respond with ONLY JSON. Message: took Lakers -4.5 2u"


@pytest.fixture
def ollama(monkeypatch):
    """Point OLLAMA_URL at a mock transport; returns the list of captured request bodies.
    Set ``state["handler"]`` to change the response."""
    monkeypatch.setenv("OLLAMA_URL", "http://ollama.test:11434")
    for k in ("OLLAMA_MODEL", "OLLAMA_TIMEOUT", "OLLAMA_NUM_CTX"):
        monkeypatch.delenv(k, raising=False)
    state = {"bodies": [], "handler": lambda req: httpx.Response(
        200, json={"message": {"content": '{"is_bet": true, "team": "Lakers"}'}})}

    def handle(req: httpx.Request) -> httpx.Response:
        state["bodies"].append(json.loads(req.content))
        return state["handler"](req)

    real = httpx.AsyncClient
    monkeypatch.setattr(llm.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handle), **kw))
    return state


@pytest.fixture
def cloud(monkeypatch):
    calls = []

    async def fake(prompt, *, api_key, max_tokens):
        calls.append(prompt)
        return 'sure: {"is_bet": false, "src": "cloud"}'

    monkeypatch.setattr(llm, "_cloud_text", fake)
    return calls


async def test_local_success_skips_cloud(ollama, cloud):
    out = await llm.haiku_json(PROMPT, api_key="k", max_tokens=300, local=True)
    assert out == {"is_bet": True, "team": "Lakers"}
    assert cloud == []
    body = ollama["bodies"][0]
    assert body["model"] == "gemma4:12b"
    assert body["stream"] is False and body["think"] is False
    assert body["keep_alive"] == "24h" and body["format"] == "json"
    assert body["options"] == {"temperature": 0, "num_ctx": 16384, "num_predict": 300}
    assert body["messages"] == [{"role": "user", "content": PROMPT}]


async def test_fallback_on_timeout(ollama, cloud):
    def boom(req):
        raise httpx.ReadTimeout("slow", request=req)
    ollama["handler"] = boom
    out = await llm.haiku_json(PROMPT, api_key="k", local=True)
    assert out == {"is_bet": False, "src": "cloud"}
    assert len(cloud) == 1


async def test_fallback_on_connect_error(ollama, cloud):
    def boom(req):
        raise httpx.ConnectError("refused", request=req)
    ollama["handler"] = boom
    assert (await llm.haiku_json(PROMPT, api_key="k", local=True))["src"] == "cloud"


async def test_fallback_on_http_error(ollama, cloud):
    ollama["handler"] = lambda req: httpx.Response(500, text="model not found")
    assert (await llm.haiku_json(PROMPT, api_key="k", local=True))["src"] == "cloud"


@pytest.mark.parametrize("content", ["not json at all", "", "[1, 2]", '{"broken": '])
async def test_fallback_on_bad_json(ollama, cloud, content):
    ollama["handler"] = lambda req: httpx.Response(200, json={"message": {"content": content}})
    assert (await llm.haiku_json(PROMPT, api_key="k", local=True))["src"] == "cloud"
    assert len(cloud) == 1


async def test_disabled_when_url_empty(ollama, cloud, monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", "")
    assert (await llm.haiku_json(PROMPT, api_key="k", local=True))["src"] == "cloud"
    assert ollama["bodies"] == []


async def test_local_false_never_calls_ollama(ollama, cloud):
    # rohan_nlp stays cloud-only.
    assert (await llm.haiku_json(PROMPT, api_key="k"))["src"] == "cloud"
    assert ollama["bodies"] == []


async def test_oversized_prompt_skips_local(ollama, cloud):
    big = "x" * (16384 * 3)
    assert (await llm.haiku_json(big, api_key="k", local=True))["src"] == "cloud"
    assert ollama["bodies"] == []


async def test_env_overrides(ollama, cloud, monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "other:7b")
    monkeypatch.setenv("OLLAMA_NUM_CTX", "8192")
    await llm.haiku_json(PROMPT, api_key="k", local=True)
    assert ollama["bodies"][0]["model"] == "other:7b"
    assert ollama["bodies"][0]["options"]["num_ctx"] == 8192


async def test_haiku_text_local_has_no_format(ollama, cloud):
    ollama["handler"] = lambda req: httpx.Response(200, json={"message": {"content": "hello"}})
    assert await llm.haiku_text(PROMPT, api_key="k", local=True) == "hello"
    assert "format" not in ollama["bodies"][0]


@pytest.mark.parametrize("exc", [httpx.ConnectError("x"), TimeoutError(), ValueError("sdk"),
                                 RuntimeError("anything")])
async def test_cloud_non_api_errors_return_none(monkeypatch, exc):
    class Boom:
        def __init__(self, **kw):
            self.messages = self

        async def create(self, **kw):
            raise exc

    monkeypatch.setattr(llm.anthropic, "AsyncAnthropic", Boom)
    monkeypatch.setenv("OLLAMA_URL", "")
    assert await llm.haiku_text(PROMPT, api_key="k") is None
    assert await llm.haiku_json(PROMPT, api_key="k", local=True) is None


async def test_cloud_without_key_returns_none(monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", "")
    assert await llm.haiku_json(PROMPT, api_key="") is None
