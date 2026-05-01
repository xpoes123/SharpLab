"""
Tests for injury idempotency fix.

fetch_injuries is now read-only.  record_injury_changes / record_injury_alert
do the DB writes and are safe to retry.  These tests verify:

1. get_injury_status returns None for unknown players, correct status otherwise.
2. record_injury_alert inserts a new player with notified=0.
3. record_injury_alert with the same status a second time preserves notified state
   (idempotency — the key safety property we rely on for retry correctness).
4. record_injury_alert with a different status resets notified=0.
5. The change-detection logic (the _PREVIOUSLY_INJURED threshold) is correct:
   - New player + Out → alert
   - New player + non-Out → no alert
   - Previously healthy + Out → alert
   - Already injured + Out → no alert (already compromised)
   - Out → Out (stable) → no alert
"""
import aiosqlite
import pytest

from shared.models import InjuryAlert


# ── Minimal in-memory DB fixture ─────────────────────────────────────────────

INJURIES_DDL = """
CREATE TABLE IF NOT EXISTS injuries (
    record_id    TEXT PRIMARY KEY,
    player_name  TEXT NOT NULL,
    team         TEXT NOT NULL,
    status       TEXT NOT NULL,
    prev_status  TEXT,
    detail       TEXT,
    updated_at   TEXT NOT NULL,
    notified     INTEGER DEFAULT 0
);
"""


async def _make_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    async with aiosqlite.connect(db_path) as db:
        await db.execute(INJURIES_DDL)
        await db.commit()
    return db_path


# ── Helpers that mirror db/queries.py using a custom DB path ─────────────────

async def _get_status(db_path: str, record_id: str):
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "SELECT status, notified FROM injuries WHERE record_id = ?", (record_id,)
        )
        return await cursor.fetchone()


async def _record_alert(db_path: str, alert: InjuryAlert) -> None:
    """Mirrors db.queries.record_injury_alert for isolated testing."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT status FROM injuries WHERE record_id = ?", (alert.record_id,)
        )
        row = await cursor.fetchone()

        if row is None:
            await db.execute(
                """
                INSERT INTO injuries
                    (record_id, player_name, team, status, prev_status, detail, updated_at, notified)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    alert.record_id, alert.player_name, alert.team, alert.status,
                    alert.prev_status, alert.detail, alert.updated_at_utc_iso,
                ),
            )
        elif row["status"] != alert.status:
            await db.execute(
                """
                UPDATE injuries
                SET player_name=?, team=?, status=?, prev_status=?, detail=?, updated_at=?, notified=0
                WHERE record_id=?
                """,
                (
                    alert.player_name, alert.team, alert.status, alert.prev_status,
                    alert.detail, alert.updated_at_utc_iso, alert.record_id,
                ),
            )
        await db.commit()


_ALERT = InjuryAlert(
    record_id="123",
    player_name="LeBron James",
    team="Los Angeles Lakers",
    status="Out",
    prev_status=None,
    detail="Ankle",
    updated_at_utc_iso="2026-01-01T00:00:00+00:00",
)


# ── get_injury_status ────────────────────────────────────────────────────────

async def test_get_status_unknown_player(tmp_path):
    db_path = await _make_db(tmp_path)
    row = await _get_status(db_path, "nonexistent-id")
    assert row is None


async def test_get_status_known_player(tmp_path):
    db_path = await _make_db(tmp_path)
    await _record_alert(db_path, _ALERT)
    row = await _get_status(db_path, "123")
    assert row is not None
    status, _ = row
    assert status == "Out"


# ── record_injury_alert idempotency ──────────────────────────────────────────

async def test_record_alert_inserts_with_notified_zero(tmp_path):
    db_path = await _make_db(tmp_path)
    await _record_alert(db_path, _ALERT)
    row = await _get_status(db_path, "123")
    assert row is not None
    _, notified = row
    assert notified == 0


async def test_record_alert_idempotent_preserves_notified(tmp_path):
    """
    Critical idempotency test: if the InjuryCog has already processed the
    alert (notified=1) and we retry record_injury_alert with the same status,
    the notified flag must NOT be reset to 0.
    """
    db_path = await _make_db(tmp_path)
    await _record_alert(db_path, _ALERT)

    # Simulate InjuryCog marking it as notified
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE injuries SET notified=1 WHERE record_id=?", ("123",))
        await db.commit()

    # Retry the same alert (same status — simulates activity retry)
    await _record_alert(db_path, _ALERT)

    row = await _get_status(db_path, "123")
    _, notified = row
    assert notified == 1, "retry must not reset notified=1 when status is unchanged"


async def test_record_alert_status_change_resets_notified(tmp_path):
    """A genuine status transition should re-arm the notification."""
    db_path = await _make_db(tmp_path)
    await _record_alert(db_path, _ALERT)

    # Mark as notified
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE injuries SET notified=1 WHERE record_id=?", ("123",))
        await db.commit()

    # Player status changes (e.g. clears to Probable)
    updated = InjuryAlert(
        record_id="123",
        player_name="LeBron James",
        team="Los Angeles Lakers",
        status="Probable",
        prev_status="Out",
        detail="Ankle",
        updated_at_utc_iso="2026-01-02T00:00:00+00:00",
    )
    await _record_alert(db_path, updated)

    row = await _get_status(db_path, "123")
    status, notified = row
    assert status == "Probable"
    assert notified == 0


# ── Change-detection logic (_PREVIOUSLY_INJURED threshold) ──────────────────
# These are pure-logic tests that mirror what fetch_injuries now computes.

_PREVIOUSLY_INJURED = {"Out", "Doubtful", "Questionable", "Day-To-Day"}


def _should_alert(current: str | None, new_status: str) -> bool:
    """Mirror the change-detection logic in fetch_injuries."""
    if current is None:
        return new_status == "Out"
    return new_status == "Out" and current != new_status and current not in _PREVIOUSLY_INJURED


def test_new_player_out_triggers_alert():
    assert _should_alert(None, "Out") is True


def test_new_player_questionable_no_alert():
    assert _should_alert(None, "Questionable") is False


def test_healthy_to_out_triggers_alert():
    assert _should_alert("Active", "Out") is True


def test_already_injured_to_out_no_alert():
    """Player already on injury report — no surprise, no alert."""
    for prev in ("Doubtful", "Questionable", "Day-To-Day"):
        assert _should_alert(prev, "Out") is False, f"expected no alert from {prev}→Out"


def test_out_to_out_stable_no_alert():
    assert _should_alert("Out", "Out") is False


def test_out_to_active_no_alert():
    """Recovery is not an alert condition."""
    assert _should_alert("Out", "Active") is False
