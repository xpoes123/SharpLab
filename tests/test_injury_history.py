"""injury_history keeps every status change; injuries keeps only the latest."""
import aiosqlite
import pytest

import db.queries as q
from db.schema import _SCHEMA
from shared.models import InjuryAlert


@pytest.fixture
async def dbpath(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    async with aiosqlite.connect(path) as db:
        await db.executescript(_SCHEMA)
    monkeypatch.setattr(q, "DB_PATH", path)
    return path


async def _history(path):
    async with aiosqlite.connect(path) as db:
        cur = await db.execute("SELECT status, prev_status FROM injury_history ORDER BY rowid")
        return await cur.fetchall()


@pytest.mark.asyncio
async def test_upsert_logs_each_change_once(dbpath):
    await q.upsert_injury_status("1", "A B", "Team", "Day-To-Day", "Ankle", "2026-11-10T00:00:00+00:00")
    await q.upsert_injury_status("1", "A B", "Team", "Day-To-Day", "Ankle", "2026-11-10T00:10:00+00:00")  # no change
    await q.upsert_injury_status("1", "A B", "Team", "Out", "Ankle", "2026-11-10T00:20:00+00:00")
    assert await _history(dbpath) == [("Day-To-Day", None), ("Out", "Day-To-Day")]


@pytest.mark.asyncio
async def test_record_alert_logs_new_and_changed_not_repeats(dbpath):
    a = InjuryAlert("2", "C D", "Team", "Out", None, "Knee", "2026-11-10T01:00:00+00:00")
    await q.record_injury_alert(a)
    await q.record_injury_alert(a)  # idempotent retry: no new history row
    b = InjuryAlert("2", "C D", "Team", "Questionable", "Out", "Knee", "2026-11-10T02:00:00+00:00")
    await q.record_injury_alert(b)
    assert await _history(dbpath) == [("Out", None), ("Questionable", "Out")]
