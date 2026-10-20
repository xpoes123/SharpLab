"""nlp_labels: human confirm/cancel/edit/timeout clicks on bet_nlp + rohan_nlp cards
are logged for later decision-model calibration, without affecting the flow."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

import db.queries as _queries
import db.schema as _schema
import shared.llm as llm
from bot.cogs import bet_nlp, rohan_nlp


@pytest.fixture()
async def tmp_db(tmp_path):
    p = str(tmp_path / "t.db")
    a, b = _schema.DB_PATH, _queries.DB_PATH
    _schema.DB_PATH = _queries.DB_PATH = p
    await _schema.init_db()
    yield p
    _schema.DB_PATH, _queries.DB_PATH = a, b


async def _rows(path):
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM nlp_labels ORDER BY id")
        return [dict(r) for r in await cur.fetchall()]


def _interaction():
    return SimpleNamespace(user=SimpleNamespace(id=1), response=SimpleNamespace(
        edit_message=AsyncMock(), send_message=AsyncMock(), send_modal=AsyncMock()))


async def test_log_nlp_label_roundtrip(tmp_db):
    await _queries.log_nlp_label("bet_nlp", "confirm", discord_user="7", message_text="took Lakers -4.5",
                                 extracted={"is_bet": True}, final={"odds": -110}, model="claude-haiku-4-5")
    [r] = await _rows(tmp_db)
    assert (r["source"], r["action"], r["discord_user"], r["model"]) == ("bet_nlp", "confirm", "7", "claude-haiku-4-5")
    assert r["message_text"] == "took Lakers -4.5"
    assert json.loads(r["extracted"]) == {"is_bet": True}
    assert json.loads(r["final"]) == {"odds": -110}
    assert r["ts"]


async def test_log_nlp_label_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(_queries, "DB_PATH", str(tmp_path / "missing_dir" / "x.db"))
    await _queries.log_nlp_label("bet_nlp", "cancel", discord_user="1", message_text="x", extracted=None)


async def test_haiku_json_reports_model(monkeypatch):
    async def cloud(prompt, *, api_key, max_tokens):
        return '{"is_bet": true}'

    async def local(prompt, *, max_tokens, fmt=None):
        return '{"is_bet": false}'

    monkeypatch.setattr(llm, "_cloud_text", cloud)
    meta: dict = {}
    assert await llm.haiku_json("p", api_key="k", meta=meta) == {"is_bet": True}
    assert meta["model"] == llm.HAIKU

    monkeypatch.setattr(llm, "local_text", local)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    meta = {}
    assert await llm.haiku_json("p", api_key="k", local=True, meta=meta) == {"is_bet": False}
    assert meta["model"] == "ollama:gemma4:12b"


def _bet():
    return {"game_id": "g1", "game_str": "BOS @ LAL", "sport": "nba", "market": "spread",
            "side": "Los Angeles Lakers", "line": -4.5, "odds": -110, "units": 1.0, "odds_defaulted": True}


LABEL = {"text": "took Lakers -4.5", "extracted": {"is_bet": True, "team": "Lakers"}, "model": "claude-haiku-4-5"}


async def test_bet_confirm_and_cancel_logged(tmp_db):
    cog = MagicMock()
    cog.record = AsyncMock(return_value=42)
    view = bet_nlp.ConfirmBetView(cog, _bet(), 1, dict(LABEL))
    await view.confirm.callback(_interaction())
    await view.on_timeout()  # already acted → no extra row
    view2 = bet_nlp.ConfirmBetView(cog, _bet(), 1, dict(LABEL))
    await view2.reject.callback(_interaction())
    rows = await _rows(tmp_db)
    assert [r["action"] for r in rows] == ["confirm", "cancel"]
    assert rows[0]["source"] == "bet_nlp" and rows[0]["model"] == "claude-haiku-4-5"
    assert json.loads(rows[0]["extracted"])["team"] == "Lakers"
    assert json.loads(rows[0]["final"])["game_id"] == "g1"
    cog.record.assert_awaited_once()


async def test_bet_edit_and_timeout_logged(tmp_db):
    view = bet_nlp.ConfirmBetView(MagicMock(), _bet(), 1, dict(LABEL))
    modal = bet_nlp._BetEditModal(view, MagicMock())
    modal.odds._value, modal.units._value, modal.line._value = "+150", "2", ""
    await modal.on_submit(_interaction())
    await view.on_timeout()
    rows = await _rows(tmp_db)
    assert [r["action"] for r in rows] == ["edit", "timeout"]
    final = json.loads(rows[0]["final"])
    assert (final["odds"], final["units"]) == (150, 2.0)


async def test_rohan_confirm_cancel_logged(tmp_db, monkeypatch):
    trade = {"is_trade": True, "side": "buy", "kind": "stock", "ticker": "AAPL", "quantity": 10, "price": 200}
    cog = MagicMock()
    cog._review = AsyncMock(return_value={"ok": True, "missing": [], "blocking": []})
    cog.record = AsyncMock()
    cog.fire_pick_alert = AsyncMock()
    label = {"text": "bought 10 AAPL at 200", "extracted": dict(trade), "model": "claude-haiku-4-5"}
    view = rohan_nlp.TradeReviewView(cog, dict(trade), label)
    await view.confirm.callback(_interaction())
    view2 = rohan_nlp.TradeReviewView(cog, dict(trade), label)
    await view2.reject.callback(_interaction())
    await view2.on_timeout()
    rows = await _rows(tmp_db)
    assert [(r["source"], r["action"]) for r in rows] == [("rohan_nlp", "confirm"), ("rohan_nlp", "cancel")]
    assert rows[0]["message_text"] == "bought 10 AAPL at 200"


async def test_rohan_modal_logs_edit_then_confirm(tmp_db):
    trade = {"is_trade": True, "side": "buy", "kind": "stock", "ticker": "AAPL", "quantity": None, "price": 200}
    cog = MagicMock()
    cog._review = AsyncMock(return_value={"ok": True, "missing": [], "blocking": []})
    cog.record = AsyncMock()
    cog.fire_pick_alert = AsyncMock()
    view = rohan_nlp.TradeReviewView(cog, dict(trade), {"text": "bought AAPL at 200", "extracted": dict(trade)})
    modal = SimpleNamespace(view=view, message=SimpleNamespace(edit=AsyncMock()))
    await rohan_nlp._finish_modal(modal, _interaction(), dict(trade, quantity=5.0))
    await view.on_timeout()
    rows = await _rows(tmp_db)
    assert [r["action"] for r in rows] == ["edit", "confirm"]
    assert json.loads(rows[1]["final"])["quantity"] == 5.0
    assert json.loads(rows[1]["extracted"])["quantity"] is None
