"""Tests for the error handler: signature hashing, severity classification, and DB round-trip."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs.error_handler import _classify_severity, _compute_signature
from db import queries, schema

# Use a temporary in-memory DB for isolation
_ORIGINAL_DB_PATH = schema.DB_PATH


@pytest.fixture(autouse=True)
async def _use_tmp_db(tmp_path):
    """Point schema + queries at a throwaway SQLite file for each test."""
    db_file = str(tmp_path / "test.db")
    schema.DB_PATH = db_file
    queries.DB_PATH = db_file
    await schema.init_db()
    yield
    schema.DB_PATH = _ORIGINAL_DB_PATH
    queries.DB_PATH = _ORIGINAL_DB_PATH


# ── _compute_signature ──────────────────────────────────────────────────────


def test_signature_stable():
    """Same inputs always produce the same signature."""
    sig1 = _compute_signature("ValueError", "odds", "Traceback...\nValueError: bad")
    sig2 = _compute_signature("ValueError", "odds", "Traceback...\nValueError: bad")
    assert sig1 == sig2
    assert len(sig1) == 16


def test_signature_differs_for_different_errors():
    sig_a = _compute_signature("ValueError", "odds", "Traceback...\nValueError: bad")
    sig_b = _compute_signature("TypeError", "odds", "Traceback...\nTypeError: bad")
    assert sig_a != sig_b


def test_signature_differs_for_different_commands():
    sig_a = _compute_signature("ValueError", "odds", "Traceback...\nValueError: bad")
    sig_b = _compute_signature("ValueError", "log", "Traceback...\nValueError: bad")
    assert sig_a != sig_b


def test_signature_none_command():
    sig = _compute_signature("ValueError", None, "Traceback...\nValueError: bad")
    assert isinstance(sig, str) and len(sig) == 16


# ── _classify_severity ──────────────────────────────────────────────────────


def test_severity_low_for_check_failure():
    assert _classify_severity(app_commands.CheckFailure(), "test") == "low"
    assert _classify_severity(commands.MissingPermissions([]), "test") == "low"
    assert _classify_severity(commands.BadArgument(), "test") == "low"
    assert _classify_severity(commands.MissingRequiredArgument(
        # MissingRequiredArgument needs a param; use a minimal mock
        type("P", (), {"name": "x", "displayed_name": "x"})()
    ), "test") == "low"


def test_severity_medium_for_discord_errors():
    assert _classify_severity(discord.NotFound(
        type("R", (), {"status": 404, "reason": "Not Found", "headers": {}})(), "not found"
    ), "test") == "medium"
    assert _classify_severity(discord.Forbidden(
        type("R", (), {"status": 403, "reason": "Forbidden", "headers": {}})(), "forbidden"
    ), "test") == "medium"


def test_severity_high_for_unhandled():
    assert _classify_severity(RuntimeError("boom"), "test") == "high"
    assert _classify_severity(ValueError("bad"), None) == "high"


# ── DB round-trip tests ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_insert_and_query_error():
    now = datetime.now(tz=timezone.utc).isoformat()
    error_id = await queries.insert_error_log(
        timestamp=now,
        error_type="RuntimeError",
        command="odds",
        user_id="123",
        guild_id="456",
        channel_id="789",
        stack_trace="Traceback...\nRuntimeError: boom",
        severity="high",
        error_signature="abcd1234abcd1234",
    )
    assert error_id is not None and error_id > 0

    errors = await queries.get_recent_errors(limit=10)
    assert len(errors) == 1
    assert errors[0]["error_type"] == "RuntimeError"
    assert errors[0]["command"] == "odds"
    assert errors[0]["severity"] == "high"
    assert errors[0]["occurrence_count"] == 1
    assert errors[0]["resolved"] == 0


@pytest.mark.asyncio
async def test_dedup_find_recent():
    now = datetime.now(tz=timezone.utc).isoformat()
    sig = "dedup_test_sig_1"
    await queries.insert_error_log(
        timestamp=now, error_type="ValueError", command="log",
        user_id=None, guild_id=None, channel_id=None,
        stack_trace="tb", severity="high", error_signature=sig,
    )
    found = await queries.find_recent_error_by_signature(sig)
    assert found is not None
    assert found["error_signature"] == sig


@pytest.mark.asyncio
async def test_increment_occurrence():
    now = datetime.now(tz=timezone.utc).isoformat()
    error_id = await queries.insert_error_log(
        timestamp=now, error_type="TypeError", command="bet",
        user_id=None, guild_id=None, channel_id=None,
        stack_trace="tb", severity="medium", error_signature="inc_test_sig",
    )
    later = datetime.now(tz=timezone.utc).isoformat()
    await queries.increment_error_occurrence(error_id, later)

    errors = await queries.get_recent_errors(limit=10)
    row = next(e for e in errors if e["id"] == error_id)
    assert row["occurrence_count"] == 2
    assert row["last_occurred"] == later


@pytest.mark.asyncio
async def test_resolve_and_reopen():
    now = datetime.now(tz=timezone.utc).isoformat()
    error_id = await queries.insert_error_log(
        timestamp=now, error_type="RuntimeError", command="crash",
        user_id=None, guild_id=None, channel_id=None,
        stack_trace="tb", severity="high", error_signature="resolve_test",
    )
    await queries.resolve_error(error_id, "sentinel", "Fixed the bug")

    errors = await queries.get_recent_errors(limit=10, resolved=True)
    assert any(e["id"] == error_id and e["resolved"] == 1 for e in errors)

    # Unresolved high severity should NOT include this
    unresolved = await queries.get_unresolved_high_severity()
    assert not any(e["id"] == error_id for e in unresolved)

    # Reopen
    await queries.reopen_error(error_id)
    unresolved = await queries.get_unresolved_high_severity()
    assert any(e["id"] == error_id and e["reopen_count"] == 1 for e in unresolved)


@pytest.mark.asyncio
async def test_filter_by_severity_and_command():
    now = datetime.now(tz=timezone.utc).isoformat()
    await queries.insert_error_log(
        timestamp=now, error_type="A", command="odds",
        user_id=None, guild_id=None, channel_id=None,
        stack_trace="tb", severity="high", error_signature="filter_a",
    )
    await queries.insert_error_log(
        timestamp=now, error_type="B", command="log",
        user_id=None, guild_id=None, channel_id=None,
        stack_trace="tb", severity="medium", error_signature="filter_b",
    )
    high_only = await queries.get_recent_errors(severity="high")
    assert all(e["severity"] == "high" for e in high_only)

    odds_only = await queries.get_recent_errors(command="odds")
    assert all(e["command"] == "odds" for e in odds_only)


@pytest.mark.asyncio
async def test_update_ticket_id():
    now = datetime.now(tz=timezone.utc).isoformat()
    error_id = await queries.insert_error_log(
        timestamp=now, error_type="X", command="test",
        user_id=None, guild_id=None, channel_id=None,
        stack_trace="tb", severity="high", error_signature="ticket_test",
    )
    await queries.update_error_ticket_id(error_id, "TICKET-42")
    errors = await queries.get_recent_errors(limit=10)
    row = next(e for e in errors if e["id"] == error_id)
    assert row["ticket_id"] == "TICKET-42"
