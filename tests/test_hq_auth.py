"""Tests for HQ Discord-OAuth session/state signing + guild gating."""
from __future__ import annotations

from web import auth


class _Req:
    """Minimal stand-in for fastapi.Request (read_session only reads .cookies)."""
    def __init__(self, cookies):
        self.cookies = cookies


def test_state_roundtrip_and_tamper():
    state = auth.make_state()
    assert auth.check_state(state)
    assert not auth.check_state(state + "x")     # tampered
    assert not auth.check_state("not-a-state")


def test_session_cookie_roundtrip():
    user = {"id": "42", "username": "tester", "avatar": "abc"}
    token = auth.make_session_cookie(user)
    sess = auth.read_session(_Req({auth.COOKIE_NAME: token}))
    assert sess["id"] == "42"
    assert sess["username"] == "tester"


def test_session_uses_global_name_then_username():
    assert auth.make_session_cookie({"id": "1", "global_name": "Glob", "username": "u"})
    sess = auth.read_session(_Req({auth.COOKIE_NAME: auth.make_session_cookie(
        {"id": "1", "global_name": "Glob", "username": "u"})}))
    assert sess["username"] == "Glob"


def test_read_session_missing_or_bad():
    assert auth.read_session(_Req({})) is None
    assert auth.read_session(_Req({auth.COOKIE_NAME: "garbage.token.here"})) is None


def test_guild_gating(monkeypatch):
    # No guilds configured → everyone allowed
    monkeypatch.setattr(auth, "GUILD_IDS", set())
    assert auth.is_member([])
    # Gated → only members of the configured guild
    monkeypatch.setattr(auth, "GUILD_IDS", {"123"})
    assert auth.is_member(["999", "123"])
    assert not auth.is_member(["999"])


def test_avatar_url():
    assert auth.avatar_url("42", None) is None
    assert auth.avatar_url("42", "abc") == "https://cdn.discordapp.com/avatars/42/abc.png"
    assert auth.avatar_url("42", "a_xyz").endswith(".gif")  # animated
