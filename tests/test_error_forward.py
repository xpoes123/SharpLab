"""Tests for bot.services.error_forward."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.services.error_forward import forward_to_sentinel


def _mock_aiohttp_session(status: int = 204, body: str = ""):
    """Build a mock aiohttp.ClientSession context manager whose .post returns status/body."""
    resp = MagicMock()
    resp.status = status
    resp.text = AsyncMock(return_value=body)

    post_cm = MagicMock()
    post_cm.__aenter__ = AsyncMock(return_value=resp)
    post_cm.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.post = MagicMock(return_value=post_cm)

    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=None)
    return session_cm, session


@pytest.mark.asyncio
async def test_disabled_when_channel_unset():
    """No-op when SENTINEL_ERROR_FORWARD_CHANNEL_ID is unset/0."""
    with patch.dict("os.environ", {"SENTINEL_ERROR_FORWARD_CHANNEL_ID": "0", "DISCORD_BOT_TOKEN": "x"}, clear=False), \
         patch("bot.services.error_forward.aiohttp.ClientSession") as cs:
        await forward_to_sentinel(123, "ValueError", "msg", None, None)
    cs.assert_not_called()


@pytest.mark.asyncio
async def test_disabled_when_token_unset():
    """No-op when DISCORD_BOT_TOKEN is empty."""
    with patch.dict("os.environ", {"SENTINEL_ERROR_FORWARD_CHANNEL_ID": "12345", "DISCORD_BOT_TOKEN": ""}, clear=False), \
         patch("bot.services.error_forward.aiohttp.ClientSession") as cs:
        await forward_to_sentinel(123, "ValueError", "msg", None, None)
    cs.assert_not_called()


@pytest.mark.asyncio
async def test_posts_json_payload_with_sharplab_repo():
    """Fully configured: posts a fenced-JSON payload identifying SharpLab."""
    session_cm, session = _mock_aiohttp_session(status=200)
    with patch.dict("os.environ", {
        "SENTINEL_ERROR_FORWARD_CHANNEL_ID": "999",
        "DISCORD_BOT_TOKEN": "tok",
    }, clear=False), \
         patch("bot.services.error_forward.aiohttp.ClientSession", return_value=session_cm):
        await forward_to_sentinel(42, "TypeError", "boom", "Traceback...", "command=/place_bet")

    assert session.post.call_count == 1
    call = session.post.call_args
    url = call[0][0]
    assert url.endswith("/channels/999/messages")
    headers = call[1]["headers"]
    assert headers["Authorization"] == "Bot tok"

    body = call[1]["json"]["content"]
    assert body.startswith("```json\n")
    assert body.rstrip().endswith("```")
    inner = body[len("```json\n"):body.rfind("```")].strip()
    payload = json.loads(inner)
    assert payload["repo_owner"] == "xpoes123"
    assert payload["repo_name"] == "SharpLab"
    assert payload["error_type"] == "TypeError"
    assert payload["message"] == "boom"
    assert payload["traceback"] == "Traceback..."
    assert payload["extra_context"] == {"error_id": "42", "context": "command=/place_bet"}


@pytest.mark.asyncio
async def test_omits_context_when_none():
    session_cm, session = _mock_aiohttp_session()
    with patch.dict("os.environ", {
        "SENTINEL_ERROR_FORWARD_CHANNEL_ID": "999",
        "DISCORD_BOT_TOKEN": "tok",
    }, clear=False), \
         patch("bot.services.error_forward.aiohttp.ClientSession", return_value=session_cm):
        await forward_to_sentinel(7, "X", "y", None, None)

    body = session.post.call_args[1]["json"]["content"]
    inner = body[len("```json\n"):body.rfind("```")].strip()
    payload = json.loads(inner)
    assert payload["extra_context"] == {"error_id": "7"}


@pytest.mark.asyncio
async def test_truncates_long_traceback():
    """Bodies over Discord's 2000-char cap shrink the traceback first."""
    long_tb = "x" * 5000
    session_cm, session = _mock_aiohttp_session()
    with patch.dict("os.environ", {
        "SENTINEL_ERROR_FORWARD_CHANNEL_ID": "999",
        "DISCORD_BOT_TOKEN": "tok",
    }, clear=False), \
         patch("bot.services.error_forward.aiohttp.ClientSession", return_value=session_cm):
        await forward_to_sentinel(1, "X", "y", long_tb, None)

    body = session.post.call_args[1]["json"]["content"]
    assert len(body) <= 2000


@pytest.mark.asyncio
async def test_swallows_post_failure():
    """Network errors must not raise — error_forward is best-effort."""
    bad_session_cm = MagicMock()
    bad_session_cm.__aenter__ = AsyncMock(side_effect=RuntimeError("network down"))
    with patch.dict("os.environ", {
        "SENTINEL_ERROR_FORWARD_CHANNEL_ID": "999",
        "DISCORD_BOT_TOKEN": "tok",
    }, clear=False), \
         patch("bot.services.error_forward.aiohttp.ClientSession", return_value=bad_session_cm):
        # Should not raise
        await forward_to_sentinel(1, "X", "y", None, None)


@pytest.mark.asyncio
async def test_swallows_4xx():
    """Non-2xx response logs but doesn't raise."""
    session_cm, _ = _mock_aiohttp_session(status=403, body="Forbidden")
    with patch.dict("os.environ", {
        "SENTINEL_ERROR_FORWARD_CHANNEL_ID": "999",
        "DISCORD_BOT_TOKEN": "tok",
    }, clear=False), \
         patch("bot.services.error_forward.aiohttp.ClientSession", return_value=session_cm):
        await forward_to_sentinel(1, "X", "y", None, None)
