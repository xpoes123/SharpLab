"""All DB access lives here. No raw SQL anywhere else."""
from __future__ import annotations
import json
import logging
import math
from datetime import datetime, timezone
import aiosqlite

log = logging.getLogger(__name__)
from shared.models import Bet, Game, InjuryAlert, OddsSnapshot
from db.schema import DB_PATH


def _row_to_game(row: aiosqlite.Row) -> Game:
    return Game(
        game_id=row["game_id"],
        home_team=row["home_team"],
        away_team=row["away_team"],
        start_time_utc_iso=row["start_time"],
        sport=row["sport"] if "sport" in row.keys() else "nba",
    )


async def upsert_game(game: Game) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO games (game_id, home_team, away_team, start_time, sport)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(game_id) DO UPDATE SET
                home_team  = excluded.home_team,
                away_team  = excluded.away_team,
                start_time = excluded.start_time
            """,
            (game.game_id, game.home_team, game.away_team, game.start_time_utc_iso, game.sport),
        )
        await db.commit()


async def upsert_odds_snapshot(snapshot: OddsSnapshot) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO odds_snapshots (snapshot_id, game_id, kind, source, captured_at, payload)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_id) DO UPDATE SET
                payload     = excluded.payload,
                captured_at = excluded.captured_at
            """,
            (
                snapshot.snapshot_id,
                snapshot.game_id,
                snapshot.kind,
                snapshot.source,
                snapshot.captured_at_utc_iso,
                json.dumps(snapshot.payload),
            ),
        )
        await db.commit()


async def get_latest_snapshots_for_game(game_id: str) -> list[OddsSnapshot]:
    """Returns the most recent poll snapshot per source for a game."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT s.*
            FROM odds_snapshots s
            INNER JOIN (
                SELECT source, MAX(captured_at) AS max_captured
                FROM odds_snapshots
                WHERE game_id = ? AND kind = 'poll'
                GROUP BY source
            ) latest ON s.source = latest.source AND s.captured_at = latest.max_captured
            WHERE s.game_id = ? AND s.kind = 'poll'
            """,
            (game_id, game_id),
        )
        rows = await cursor.fetchall()
    return [
        OddsSnapshot(
            snapshot_id=row["snapshot_id"],
            game_id=row["game_id"],
            kind=row["kind"],
            source=row["source"],
            captured_at_utc_iso=row["captured_at"],
            payload=json.loads(row["payload"]),
        )
        for row in rows
    ]


async def get_snapshots_for_game_since(game_id: str, since_utc_iso: str) -> list[OddsSnapshot]:
    """Returns all poll snapshots for a game after a given timestamp (for line-move history)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM odds_snapshots
            WHERE game_id = ? AND kind = 'poll' AND captured_at >= ?
            ORDER BY captured_at ASC
            """,
            (game_id, since_utc_iso),
        )
        rows = await cursor.fetchall()
    return [
        OddsSnapshot(
            snapshot_id=row["snapshot_id"],
            game_id=row["game_id"],
            kind=row["kind"],
            source=row["source"],
            captured_at_utc_iso=row["captured_at"],
            payload=json.loads(row["payload"]),
        )
        for row in rows
    ]


async def find_games_by_team(team_name: str, sport: str = "nba") -> list[Game]:
    """Find games where either team name contains the query (case-insensitive)."""
    pattern = f"%{team_name}%"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM games
            WHERE sport = ?
              AND (home_team LIKE ? OR away_team LIKE ?)
            ORDER BY start_time ASC
            """,
            (sport, pattern, pattern),
        )
        rows = await cursor.fetchall()
    return [_row_to_game(row) for row in rows]


async def get_games_in_window(start_utc_iso: str, end_utc_iso: str, sport: str = "nba") -> list[Game]:
    """Return all games with start_time in [start, end] (UTC ISO strings)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM games
            WHERE sport = ? AND start_time >= ? AND start_time <= ?
            ORDER BY start_time ASC
            """,
            (sport, start_utc_iso, end_utc_iso),
        )
        rows = await cursor.fetchall()
    return [_row_to_game(row) for row in rows]


async def get_upcoming_games(filter_str: str = "", sport: str = "nba") -> list[Game]:
    """Return upcoming games (start_time >= now), optionally filtered by team name."""
    now = datetime.now(timezone.utc).isoformat()
    pattern = f"%{filter_str}%"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM games
            WHERE sport = ?
              AND start_time >= ?
              AND (home_team LIKE ? OR away_team LIKE ?)
            ORDER BY start_time ASC
            LIMIT 25
            """,
            (sport, now, pattern, pattern),
        )
        rows = await cursor.fetchall()
    return [_row_to_game(row) for row in rows]


async def get_game_by_id(game_id: str) -> Game | None:
    """Return a single game by its ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM games WHERE game_id = ?",
            (game_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_game(row)


async def get_close_snapshot(game_id: str, source: str) -> OddsSnapshot | None:
    """Returns the close snapshot for a game/source pair."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM odds_snapshots
            WHERE game_id = ? AND source = ? AND kind = 'close'
            ORDER BY captured_at DESC LIMIT 1
            """,
            (game_id, source),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    return OddsSnapshot(
        snapshot_id=row["snapshot_id"],
        game_id=row["game_id"],
        kind=row["kind"],
        source=row["source"],
        captured_at_utc_iso=row["captured_at"],
        payload=json.loads(row["payload"]),
    )


# ── Bets ───────────────────────────────────────────────────────────────────────

def _row_to_bet(row: aiosqlite.Row) -> Bet:
    return Bet(
        bet_id=row["bet_id"],
        game_id=row["game_id"],
        placed_at=row["placed_at"],
        discord_user=row["discord_user"],
        book=row["book"],
        market=row["market"],
        side=row["side"],
        odds=row["odds"],
        units=row["units"],
        line=row["line"],
        status=row["status"],
        clv=row["clv"],
        notes=row["notes"],
    )


async def insert_bet(bet: Bet) -> int:
    """Insert a new bet and return the assigned bet_id."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO bets
                (game_id, placed_at, discord_user, book, market, side, line, odds, units, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (bet.game_id, bet.placed_at, bet.discord_user, bet.book,
             bet.market, bet.side, bet.line, bet.odds, bet.units, bet.notes),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore[return-value]


async def get_bets_for_user(discord_user: str) -> list[Bet]:
    """Return all bets for a user, newest first."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM bets WHERE discord_user = ? ORDER BY placed_at DESC",
            (discord_user,),
        )
        rows = await cursor.fetchall()
    return [_row_to_bet(r) for r in rows]


async def get_graded_bets_for_user(discord_user: str) -> list[Bet]:
    """Return all bets with CLV computed (graded/won/lost/push/void), newest first."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM bets WHERE discord_user = ? AND clv IS NOT NULL ORDER BY placed_at DESC",
            (discord_user,),
        )
        rows = await cursor.fetchall()
    return [_row_to_bet(r) for r in rows]


async def get_open_bets_for_user(discord_user: str) -> list[Bet]:
    """Return open and graded bets for a user, newest first."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM bets WHERE discord_user = ? AND status IN ('open', 'graded') ORDER BY placed_at DESC",
            (discord_user,),
        )
        rows = await cursor.fetchall()
    return [_row_to_bet(r) for r in rows]


async def get_games_with_close_and_open_bets() -> list[str]:
    """Return game_ids that have a close snapshot and at least one open bet."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT DISTINCT b.game_id
            FROM bets b
            INNER JOIN odds_snapshots os
                ON b.game_id = os.game_id AND os.kind = 'close'
            WHERE b.status = 'open'
            """
        )
        rows = await cursor.fetchall()
    return [row[0] for row in rows]


async def get_games_with_close_not_posted() -> list[str]:
    """Return game_ids that have a close snapshot but haven't had CLV posted yet."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT DISTINCT g.game_id
            FROM games g
            INNER JOIN odds_snapshots os
                ON g.game_id = os.game_id AND os.kind = 'close'
            WHERE g.clv_posted = 0
            """
        )
        rows = await cursor.fetchall()
    return [row[0] for row in rows]


async def mark_game_clv_posted(game_id: str) -> None:
    """Mark a game's closing lines as posted so we don't re-post."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE games SET clv_posted = 1 WHERE game_id = ?",
            (game_id,),
        )
        await db.commit()


async def get_any_close_snapshot(game_id: str) -> OddsSnapshot | None:
    """Return the close snapshot for a game (Kalshi preferred, then DraftKings)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM odds_snapshots
            WHERE game_id = ? AND kind = 'close'
            ORDER BY
                CASE source
                    WHEN 'kalshi'     THEN 0
                    WHEN 'draftkings' THEN 1
                    ELSE 2
                END,
                captured_at DESC
            LIMIT 1
            """,
            (game_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    return OddsSnapshot(
        snapshot_id=row["snapshot_id"],
        game_id=row["game_id"],
        kind=row["kind"],
        source=row["source"],
        captured_at_utc_iso=row["captured_at"],
        payload=json.loads(row["payload"]),
    )


async def update_bet_clv(bet_id: int, clv: float | None) -> None:
    """Set CLV on a bet and mark it as graded (awaiting final result)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE bets SET clv = ?, status = 'graded' WHERE bet_id = ?",
            (clv, bet_id),
        )
        await db.commit()


async def get_open_bets_for_game(game_id: str) -> list[Bet]:
    """Return all open bets for a game (used by CLV auto-post)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM bets WHERE game_id = ? AND status = 'open'",
            (game_id,),
        )
        rows = await cursor.fetchall()
    return [_row_to_bet(r) for r in rows]


async def get_bet_by_id(bet_id: int) -> Bet | None:
    """Return a single bet by its primary key."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM bets WHERE bet_id = ?",
            (bet_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_bet(row)


async def get_resolvable_bets_for_game(game_id: str) -> list[Bet]:
    """Return open/graded bets for a game that haven't received a final result yet."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM bets WHERE game_id = ? AND status IN ('open', 'graded')",
            (game_id,),
        )
        rows = await cursor.fetchall()
    return [_row_to_bet(r) for r in rows]


async def get_game_by_team_suffixes(
    home_last: str, away_last: str, after_utc_iso: str
) -> Game | None:
    """Find the most recent non-final game matching both team name suffixes (case-insensitive)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM games
            WHERE lower(home_team) LIKE ?
              AND lower(away_team) LIKE ?
              AND start_time >= ?
              AND status != 'final'
            ORDER BY start_time DESC
            LIMIT 1
            """,
            (f"%{home_last.lower()}", f"%{away_last.lower()}", after_utc_iso),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_game(row)


async def update_bet_result(bet_id: int, status: str) -> None:
    """Set the final result (won/lost/push/void) on a resolved bet."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE bets SET status = ? WHERE bet_id = ?",
            (status, bet_id),
        )
        await db.commit()


async def update_game_status(game_id: str, status: str) -> None:
    """Update the status column of a game (e.g. 'final')."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE games SET status = ? WHERE game_id = ?",
            (status, game_id),
        )
        await db.commit()


# ── Injuries ───────────────────────────────────────────────────────────────────

# Statuses that indicate a player is already compromised (known injury)
_PREVIOUSLY_INJURED = {"Out", "Doubtful", "Questionable", "Day-To-Day"}


async def upsert_injury_status(
    record_id: str,
    player_name: str,
    team: str,
    status: str,
    detail: str | None,
    now_iso: str,
) -> str | None:
    """
    Upsert a player's injury status.
    Returns None if no actionable change, "" if new Out listing,
    or the previous status string if status changed to Out.

    Notification rules:
    - Only Discord-notify (notified=0) for Out status.
    - Only notify if the player was previously healthy (not already in _PREVIOUSLY_INJURED).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT status FROM injuries WHERE record_id = ?",
            (record_id,),
        )
        row = await cursor.fetchone()

        if row is None:
            # New player — notify only if Out (surprise listing)
            notified = 0 if status == "Out" else 1
            await db.execute(
                """
                INSERT INTO injuries
                    (record_id, player_name, team, status, prev_status, detail, updated_at, notified)
                VALUES (?, ?, ?, ?, NULL, ?, ?, ?)
                """,
                (record_id, player_name, team, status, detail, now_iso, notified),
            )
            await db.commit()
            return "" if status == "Out" else None

        current_status = row["status"]
        if current_status == status:
            return None

        # Status changed — only act on transitions TO Out
        going_out = status == "Out"
        # Notify only if player was healthy before (not already on injury report)
        was_healthy = current_status not in _PREVIOUSLY_INJURED
        notified = 0 if (going_out and was_healthy) else 1
        await db.execute(
            """
            UPDATE injuries
            SET player_name=?, team=?, status=?, prev_status=?, detail=?, updated_at=?, notified=?
            WHERE record_id=?
            """,
            (player_name, team, status, current_status, detail, now_iso, notified, record_id),
        )
        await db.commit()
        # Only return non-None (triggering odds re-fetch) when going Out
        return current_status if going_out else None


async def get_unnotified_injuries() -> list[InjuryAlert]:
    """Return injury alerts that haven't been posted to Discord yet."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM injuries WHERE notified = 0 ORDER BY updated_at ASC"
        )
        rows = await cursor.fetchall()
    return [
        InjuryAlert(
            record_id=row["record_id"],
            player_name=row["player_name"],
            team=row["team"],
            status=row["status"],
            prev_status=row["prev_status"],
            detail=row["detail"],
            updated_at_utc_iso=row["updated_at"],
        )
        for row in rows
    ]


async def get_injuries_for_team(team: str) -> list[InjuryAlert]:
    """Return all injury report entries for a team, ordered by status severity then name."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM injuries
            WHERE team = ?
            ORDER BY
                CASE status
                    WHEN 'Out'          THEN 0
                    WHEN 'Doubtful'     THEN 1
                    WHEN 'Questionable' THEN 2
                    WHEN 'Day-To-Day'   THEN 3
                    WHEN 'Probable'     THEN 4
                    ELSE 5
                END,
                player_name ASC
            """,
            (team,),
        )
        rows = await cursor.fetchall()
    return [
        InjuryAlert(
            record_id=row["record_id"],
            player_name=row["player_name"],
            team=row["team"],
            status=row["status"],
            prev_status=row["prev_status"],
            detail=row["detail"],
            updated_at_utc_iso=row["updated_at"],
        )
        for row in rows
    ]


async def mark_injury_notified(record_id: str) -> None:
    """Mark an injury alert as posted."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE injuries SET notified = 1 WHERE record_id = ?",
            (record_id,),
        )
        await db.commit()


async def get_past_game_count(sport: str = "nba") -> int:
    """Count of games with start_time in the past."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM games WHERE sport = ? AND start_time <= ?", (sport, now)
        )
        row = await cursor.fetchone()
    return row[0] if row else 0


async def get_history_page(offset: int, limit: int, sport: str = "nba") -> list[dict]:
    """
    Returns past games ordered newest first with close snapshot spread if available.
    Each dict: game_id, home_team, away_team, start_time, status, spread, spread_odds.
    """
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT
                g.game_id, g.home_team, g.away_team, g.start_time, g.status,
                (
                    SELECT payload FROM odds_snapshots
                    WHERE game_id = g.game_id AND kind = 'close'
                    ORDER BY
                        CASE source WHEN 'draftkings' THEN 0 WHEN 'kalshi' THEN 1 ELSE 2 END,
                        captured_at DESC
                    LIMIT 1
                ) AS close_payload
            FROM games g
            WHERE g.sport = ? AND g.start_time <= ?
            ORDER BY g.start_time DESC
            LIMIT ? OFFSET ?
            """,
            (sport, now, limit, offset),
        )
        rows = await cursor.fetchall()

    result = []
    for row in rows:
        spread = spread_odds = None
        if row["close_payload"]:
            p = json.loads(row["close_payload"])
            spread = p.get("spread")
            spread_odds = p.get("spread_odds")
        result.append({
            "game_id": row["game_id"],
            "home_team": row["home_team"],
            "away_team": row["away_team"],
            "start_time": row["start_time"],
            "status": row["status"],
            "spread": spread,
            "spread_odds": spread_odds,
        })
    return result


async def get_first_poll_snapshot(game_id: str, source: str) -> OddsSnapshot | None:
    """Return the earliest poll snapshot for a game/source (opening line)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM odds_snapshots
            WHERE game_id = ? AND source = ? AND kind = 'poll'
            ORDER BY captured_at ASC LIMIT 1
            """,
            (game_id, source),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    return OddsSnapshot(
        snapshot_id=row["snapshot_id"],
        game_id=row["game_id"],
        kind=row["kind"],
        source=row["source"],
        captured_at_utc_iso=row["captured_at"],
        payload=json.loads(row["payload"]),
    )


async def get_recent_games(filter_str: str = "", sport: str = "nba") -> list[Game]:
    """Return games from yesterday through end of tomorrow (for /line-move autocomplete)."""
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat()
    future_cutoff = (now + timedelta(days=2)).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat()
    pattern = f"%{filter_str}%"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM games
            WHERE sport = ?
              AND start_time >= ?
              AND start_time < ?
              AND (home_team LIKE ? OR away_team LIKE ?)
            ORDER BY start_time DESC
            LIMIT 25
            """,
            (sport, cutoff, future_cutoff, pattern, pattern),
        )
        rows = await cursor.fetchall()
    return [_row_to_game(row) for row in rows]


async def get_games_by_id_prefix(prefix: str, limit: int = 25) -> list[Game]:
    """Search games by game_id prefix (for historical autocomplete)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM games
            WHERE game_id LIKE ?
            ORDER BY start_time DESC
            LIMIT ?
            """,
            (f"{prefix}%", limit),
        )
        rows = await cursor.fetchall()
    return [_row_to_game(row) for row in rows]


async def get_all_games_filtered(filter_str: str = "", limit: int = 25, sport: str = "nba") -> list[Game]:
    """Search all games (past and future) by team name, newest first."""
    pattern = f"%{filter_str}%"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM games
            WHERE sport = ?
              AND (home_team LIKE ? OR away_team LIKE ?)
            ORDER BY start_time DESC
            LIMIT ?
            """,
            (sport, pattern, pattern, limit),
        )
        rows = await cursor.fetchall()
    return [_row_to_game(row) for row in rows]


# ── Wallets ────────────────────────────────────────────────────────────────────

DAILY_AMOUNT = 100
SAFETY_NET = 25  # free coins when balance hits 0
_DAILY_SECONDS = 28800  # 8 hours


async def get_or_create_wallet(discord_user: str) -> tuple[int, bool]:
    """
    Return (balance, daily_credited).
    Creates wallet with bonus coins if new; credits 100 coins if >8h since last.
    """
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT balance, last_daily FROM wallets WHERE discord_user = ?",
            (discord_user,),
        )
        row = await cursor.fetchone()

        if row is None:
            await db.execute(
                "INSERT INTO wallets (discord_user, balance, last_daily) VALUES (?, ?, ?)",
                (discord_user, DAILY_AMOUNT, now_iso),
            )
            await db.commit()
            return (DAILY_AMOUNT, True)

        balance = row["balance"]
        last_daily = row["last_daily"]
        credited = False

        if last_daily is None:
            eligible = True
        else:
            last_dt = datetime.fromisoformat(last_daily)
            eligible = (now - last_dt).total_seconds() >= _DAILY_SECONDS

        if eligible:
            balance += DAILY_AMOUNT
            await db.execute(
                "UPDATE wallets SET balance = ?, last_daily = ? WHERE discord_user = ?",
                (balance, now_iso, discord_user),
            )
            await db.commit()
            credited = True

        # Safety net: if broke, give 25 coins so they can always play
        if balance == 0:
            balance = SAFETY_NET
            await db.execute(
                "UPDATE wallets SET balance = ? WHERE discord_user = ?",
                (balance, discord_user),
            )
            await db.commit()

        return (balance, credited)


async def update_balance(discord_user: str, delta: int) -> int:
    """Add/subtract coins atomically. Returns new balance. Raises ValueError if insufficient."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if delta < 0:
            # Atomic check-and-subtract: WHERE guards against going negative
            cursor = await db.execute(
                "UPDATE wallets SET balance = balance + ? "
                "WHERE discord_user = ? AND balance + ? >= 0",
                (delta, discord_user, delta),
            )
            if cursor.rowcount == 0:
                # Either wallet doesn't exist or insufficient balance
                check = await db.execute(
                    "SELECT balance FROM wallets WHERE discord_user = ?",
                    (discord_user,),
                )
                row = await check.fetchone()
                if row is None:
                    raise ValueError("No wallet found")
                raise ValueError(f"Insufficient coins (have {row['balance']}, need {-delta})")
        else:
            cursor = await db.execute(
                "UPDATE wallets SET balance = balance + ? WHERE discord_user = ?",
                (delta, discord_user),
            )
            if cursor.rowcount == 0:
                raise ValueError("No wallet found")
        await db.commit()
        # Read back the new balance
        cursor = await db.execute(
            "SELECT balance FROM wallets WHERE discord_user = ?",
            (discord_user,),
        )
        row = await cursor.fetchone()
        return row["balance"]


async def get_balance(discord_user: str) -> int | None:
    """Return balance or None if no wallet exists."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT balance FROM wallets WHERE discord_user = ?",
            (discord_user,),
        )
        row = await cursor.fetchone()
    return row["balance"] if row else None


# ── Casino Wallets (separate from paper-trading wallets) ──────────────────────

CASINO_STARTING_COINS = 1000
CASINO_MIN_BALANCE = 1000


async def get_or_create_casino_wallet(discord_user: str) -> int:
    """Return casino balance. Creates wallet with starting coins if new."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT balance FROM casino_wallets WHERE discord_user = ?",
            (discord_user,),
        )
        row = await cursor.fetchone()
        if row is not None:
            bal = row["balance"]
            if bal < CASINO_MIN_BALANCE:
                bal = CASINO_MIN_BALANCE
                await db.execute(
                    "UPDATE casino_wallets SET balance = ? WHERE discord_user = ?",
                    (bal, discord_user),
                )
                await db.commit()
            return bal
        await db.execute(
            "INSERT INTO casino_wallets (discord_user, balance) VALUES (?, ?)",
            (discord_user, CASINO_STARTING_COINS),
        )
        await db.commit()
        return CASINO_STARTING_COINS


async def update_casino_balance(discord_user: str, delta: int) -> int:
    """Add/subtract casino coins atomically. Returns new balance. Raises ValueError if insufficient."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if delta < 0:
            # Atomic check-and-subtract: WHERE guards against going negative
            cursor = await db.execute(
                "UPDATE casino_wallets SET balance = MAX(balance + ?, ?) "
                "WHERE discord_user = ? AND balance + ? >= 0",
                (delta, CASINO_MIN_BALANCE, discord_user, delta),
            )
            if cursor.rowcount == 0:
                check = await db.execute(
                    "SELECT balance FROM casino_wallets WHERE discord_user = ?",
                    (discord_user,),
                )
                row = await check.fetchone()
                if row is None:
                    raise ValueError("No casino wallet found")
                raise ValueError(f"Insufficient casino coins (have {row['balance']}, need {-delta})")
        else:
            await db.execute(
                "UPDATE casino_wallets SET balance = balance + ? WHERE discord_user = ?",
                (delta, discord_user),
            )
        await db.commit()
        # Read back the new balance
        cursor = await db.execute(
            "SELECT balance FROM casino_wallets WHERE discord_user = ?",
            (discord_user,),
        )
        row = await cursor.fetchone()
        return row["balance"]


async def get_casino_balance(discord_user: str) -> int | None:
    """Return casino balance or None if no wallet."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT balance FROM casino_wallets WHERE discord_user = ?",
            (discord_user,),
        )
        row = await cursor.fetchone()
    return row["balance"] if row else None


async def give_casino_coins(discord_user: str, amount: int) -> int:
    """Give casino coins to a user (admin). Creates wallet if needed. Returns new balance."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT balance FROM casino_wallets WHERE discord_user = ?",
            (discord_user,),
        )
        row = await cursor.fetchone()
        if row is None:
            new_balance = amount
            await db.execute(
                "INSERT INTO casino_wallets (discord_user, balance) VALUES (?, ?)",
                (discord_user, new_balance),
            )
        else:
            new_balance = row["balance"] + amount
            await db.execute(
                "UPDATE casino_wallets SET balance = ? WHERE discord_user = ?",
                (new_balance, discord_user),
            )
        await db.commit()
        return new_balance


async def tip_casino_coins(from_user: str, to_user: str, amount: int) -> tuple[int, int]:
    """Transfer casino coins from one user to another atomically.

    Returns (sender_new_balance, recipient_new_balance).
    Raises ValueError if sender has insufficient funds (must keep >= CASINO_MIN_BALANCE after tip).

    The wallet creation, deduction, credit, and history inserts all happen in a
    single connection and are committed together — a crash at any point leaves
    both balances unchanged.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Ensure recipient wallet exists within the same transaction
        await db.execute(
            "INSERT OR IGNORE INTO casino_wallets (discord_user, balance) VALUES (?, ?)",
            (to_user, CASINO_MIN_BALANCE),
        )
        # Deduct from sender; the WHERE clause enforces the min-balance floor atomically
        cursor = await db.execute(
            "UPDATE casino_wallets SET balance = balance - ? "
            "WHERE discord_user = ? AND balance - ? >= ?",
            (amount, from_user, amount, CASINO_MIN_BALANCE),
        )
        if cursor.rowcount == 0:
            check = await db.execute(
                "SELECT balance FROM casino_wallets WHERE discord_user = ?",
                (from_user,),
            )
            row = await check.fetchone()
            if row is None:
                raise ValueError("No casino wallet found")
            raise ValueError(
                f"Insufficient casino coins (have {row['balance']}, need {amount} + keep {CASINO_MIN_BALANCE} minimum)"
            )
        # Credit recipient
        await db.execute(
            "UPDATE casino_wallets SET balance = balance + ? WHERE discord_user = ?",
            (amount, to_user),
        )
        # Fetch both new balances before committing
        sender_row = await (await db.execute(
            "SELECT balance FROM casino_wallets WHERE discord_user = ?", (from_user,)
        )).fetchone()
        recipient_row = await (await db.execute(
            "SELECT balance FROM casino_wallets WHERE discord_user = ?", (to_user,)
        )).fetchone()
        sender_new_balance = sender_row["balance"]
        recipient_new_balance = recipient_row["balance"]
        # Log history for both within the same transaction so records are never
        # written without the corresponding balance changes
        await db.execute(
            "INSERT INTO casino_history (discord_user, game, wagered, payout, played_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (from_user, "tip", amount, 0, now_iso),
        )
        await db.execute(
            "INSERT INTO casino_history (discord_user, game, wagered, payout, played_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (to_user, "tip", 0, amount, now_iso),
        )
        await db.commit()
    return sender_new_balance, recipient_new_balance


# ── Casino History ────────────────────────────────────────────────────────────


async def log_casino_result(
    discord_user: str, game: str, wagered: int, payout: int,
) -> None:
    """Record a completed casino round for PnL tracking. Also awards XP."""
    now_iso = datetime.now(timezone.utc).isoformat()
    xp = 10 + (15 if payout > wagered else 0)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO casino_history (discord_user, game, wagered, payout, played_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (discord_user, game, wagered, payout, now_iso),
        )
        await db.execute(
            """INSERT INTO user_xp (discord_user, total_xp, level)
               VALUES (?, ?, 1)
               ON CONFLICT(discord_user) DO UPDATE SET total_xp = total_xp + ?""",
            (discord_user, xp, xp),
        )
        cursor = await db.execute(
            "SELECT total_xp FROM user_xp WHERE discord_user = ?",
            (discord_user,),
        )
        row = await cursor.fetchone()
        if row:
            new_level = compute_level(row[0])
            await db.execute(
                "UPDATE user_xp SET level = ? WHERE discord_user = ?",
                (new_level, discord_user),
            )
        await db.commit()


async def get_casino_stats(discord_user: str) -> dict:
    """Overall casino stats for a user."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT
                COALESCE(SUM(wagered), 0) AS total_wagered,
                COALESCE(SUM(payout), 0)  AS total_payout,
                COALESCE(SUM(payout) - SUM(wagered), 0) AS net_profit,
                COUNT(*) AS rounds,
                CASE WHEN SUM(wagered) > 0
                    THEN (SUM(payout) - SUM(wagered)) * 100.0 / SUM(wagered)
                    ELSE 0
                END AS roi
            FROM casino_history WHERE discord_user = ?
            """,
            (discord_user,),
        )
        row = await cursor.fetchone()
    if row:
        return dict(row)
    return {"total_wagered": 0, "total_payout": 0, "net_profit": 0, "rounds": 0, "roi": 0.0}


async def get_casino_stats_by_game(discord_user: str) -> list[dict]:
    """Per-game casino breakdown, sorted by rounds played descending."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT
                game,
                COALESCE(SUM(wagered), 0) AS total_wagered,
                COALESCE(SUM(payout), 0)  AS total_payout,
                COALESCE(SUM(payout) - SUM(wagered), 0) AS net_profit,
                COUNT(*) AS rounds
            FROM casino_history WHERE discord_user = ?
            GROUP BY game ORDER BY rounds DESC
            """,
            (discord_user,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_casino_leaderboard(limit: int = 10) -> list[dict]:
    """Top casino users by current balance, with net profit from history."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT
                w.discord_user,
                w.balance,
                COALESCE(SUM(h.wagered), 0) AS total_wagered,
                COALESCE(SUM(h.payout), 0)  AS total_payout,
                COALESCE(SUM(h.payout) - SUM(h.wagered), 0) AS net_profit,
                COUNT(h.id) AS rounds
            FROM casino_wallets w
            LEFT JOIN casino_history h ON w.discord_user = h.discord_user
            GROUP BY w.discord_user
            ORDER BY w.balance DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_casino_game_leaderboard(game: str, limit: int = 10) -> list[dict]:
    """Top users for a specific casino game by net profit."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT
                discord_user,
                COALESCE(SUM(wagered), 0) AS total_wagered,
                COALESCE(SUM(payout), 0)  AS total_payout,
                COALESCE(SUM(payout) - SUM(wagered), 0) AS net_profit,
                COUNT(*) AS rounds
            FROM casino_history
            WHERE game = ?
            GROUP BY discord_user
            ORDER BY net_profit DESC
            LIMIT ?
            """,
            (game, limit),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_todays_game_for_team(team: str) -> Game | None:
    """Return the next unfinished game today for a given team (exact name match)."""
    today_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM games
            WHERE (home_team = ? OR away_team = ?)
              AND start_time LIKE ?
              AND status != 'final'
            ORDER BY start_time ASC
            LIMIT 1
            """,
            (team, team, f"{today_prefix}%"),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_game(row)


# ── Game scores ───────────────────────────────────────────────────────────────


async def update_game_scores(game_id: str, home_score: int, away_score: int) -> None:
    """Write final scores to the games table."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE games SET home_score = ?, away_score = ? WHERE game_id = ?",
            (home_score, away_score, game_id),
        )
        await db.commit()


async def get_game_scores(game_id: str) -> tuple[int, int] | None:
    """Return (home_score, away_score) if the game is final and scores are stored."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT home_score, away_score FROM games WHERE game_id = ? AND status = 'final'",
            (game_id,),
        )
        row = await cursor.fetchone()
    if row is None or row["home_score"] is None:
        return None
    return (row["home_score"], row["away_score"])


# ── Paper bets ────────────────────────────────────────────────────────────────


async def insert_paper_bet(
    game_id: str,
    discord_user: str,
    placed_at: str,
    market: str,
    side: str,
    line: float | None,
    odds: int,
    wager: int,
    potential_payout: int,
) -> int:
    """Insert a paper bet and return the auto-generated paper_bet_id."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO paper_bets
                (game_id, discord_user, placed_at, market, side, line, odds, wager, potential_payout)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (game_id, discord_user, placed_at, market, side, line, odds, wager, potential_payout),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore[return-value]


async def get_open_paper_bets_for_user(discord_user: str) -> list[dict]:
    """All open paper bets for a user, newest first."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM paper_bets
            WHERE discord_user = ? AND status = 'open'
            ORDER BY placed_at DESC
            """,
            (discord_user,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_open_paper_bets_for_game(game_id: str) -> list[dict]:
    """All open paper bets on a specific game."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM paper_bets WHERE game_id = ? AND status = 'open'",
            (game_id,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_games_with_open_paper_bets() -> list[str]:
    """Distinct game_ids that still have unresolved paper bets."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT DISTINCT game_id FROM paper_bets WHERE status = 'open'"
        )
        rows = await cursor.fetchall()
    return [r[0] for r in rows]


async def resolve_paper_bet(
    paper_bet_id: int, status: str, payout: int, clv: float | None = None,
) -> None:
    """Mark a paper bet as resolved with final status, payout, and optional CLV."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE paper_bets
            SET status = ?, payout = ?, resolved_at = ?, clv = ?
            WHERE paper_bet_id = ?
            """,
            (status, payout, now, clv, paper_bet_id),
        )
        await db.commit()


async def get_paper_bet_stats(discord_user: str) -> dict:
    """Aggregate stats for a user's resolved paper bets."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT
                COALESCE(SUM(wager), 0) AS total_wagered,
                COALESCE(SUM(payout), 0) AS total_payout,
                COALESCE(SUM(payout) - SUM(wager), 0) AS net_profit,
                SUM(CASE WHEN status = 'won' THEN 1 ELSE 0 END) AS num_won,
                SUM(CASE WHEN status = 'lost' THEN 1 ELSE 0 END) AS num_lost,
                SUM(CASE WHEN status = 'push' THEN 1 ELSE 0 END) AS num_push,
                COUNT(*) AS num_bets,
                AVG(clv) AS avg_clv,
                SUM(CASE WHEN clv IS NOT NULL THEN 1 ELSE 0 END) AS clv_count
            FROM paper_bets
            WHERE discord_user = ? AND status IN ('won', 'lost', 'push')
            """,
            (discord_user,),
        )
        row = await cursor.fetchone()
    return dict(row) if row else {
        "total_wagered": 0, "total_payout": 0, "net_profit": 0,
        "num_won": 0, "num_lost": 0, "num_push": 0, "num_bets": 0,
        "avg_clv": None, "clv_count": 0,
    }


async def get_paper_leaderboard(limit: int = 10) -> list[dict]:
    """Top users by net profit from resolved paper bets."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT
                discord_user,
                SUM(payout) - SUM(wager) AS net_profit,
                SUM(wager) AS total_wagered,
                COUNT(*) AS num_bets,
                SUM(CASE WHEN status = 'won' THEN 1 ELSE 0 END) AS num_won,
                SUM(CASE WHEN status = 'lost' THEN 1 ELSE 0 END) AS num_lost,
                CASE WHEN SUM(wager) > 0
                    THEN (SUM(payout) - SUM(wager)) * 100.0 / SUM(wager)
                    ELSE 0
                END AS roi,
                AVG(clv) AS avg_clv
            FROM paper_bets
            WHERE status IN ('won', 'lost', 'push')
            GROUP BY discord_user
            ORDER BY net_profit DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_paper_bet_by_id(paper_bet_id: int) -> dict | None:
    """Return a single paper bet row as a dict."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM paper_bets WHERE paper_bet_id = ?",
            (paper_bet_id,),
        )
        row = await cursor.fetchone()
    return dict(row) if row else None


async def get_paper_stats_by_market(discord_user: str) -> list[dict]:
    """Per-market breakdown (moneyline, spread, total) for resolved paper bets."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT
                market,
                COUNT(*) AS num_bets,
                SUM(CASE WHEN status = 'won' THEN 1 ELSE 0 END) AS num_won,
                SUM(CASE WHEN status = 'lost' THEN 1 ELSE 0 END) AS num_lost,
                SUM(CASE WHEN status = 'push' THEN 1 ELSE 0 END) AS num_push,
                COALESCE(SUM(payout) - SUM(wager), 0) AS net_profit
            FROM paper_bets
            WHERE discord_user = ? AND status IN ('won', 'lost', 'push')
            GROUP BY market
            ORDER BY num_bets DESC
            """,
            (discord_user,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_recent_paper_bets(discord_user: str, limit: int = 10) -> list[dict]:
    """Most recent resolved paper bets for a user."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM paper_bets
            WHERE discord_user = ? AND status IN ('won', 'lost', 'push', 'void')
            ORDER BY resolved_at DESC
            LIMIT ?
            """,
            (discord_user, limit),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_paper_streak(discord_user: str) -> tuple[str, int]:
    """Current win/loss streak. Returns ('won'|'lost'|'none', count)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT status FROM paper_bets
            WHERE discord_user = ? AND status IN ('won', 'lost')
            ORDER BY resolved_at DESC
            """,
            (discord_user,),
        )
        rows = await cursor.fetchall()
    if not rows:
        return ("none", 0)
    streak_status = rows[0]["status"]
    count = 0
    for r in rows:
        if r["status"] == streak_status:
            count += 1
        else:
            break
    return (streak_status, count)


async def void_paper_bet(paper_bet_id: int) -> None:
    """Void a paper bet and record resolution time."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE paper_bets
            SET status = 'void', payout = 0, resolved_at = ?
            WHERE paper_bet_id = ?
            """,
            (now, paper_bet_id),
        )
        await db.commit()


# ── XP & Leveling ────────────────────────────────────────────────────────────


def compute_level(xp: int) -> int:
    """Level from XP. Level N requires 50*(N-1)^2 XP."""
    return int(math.isqrt(xp // 50)) + 1 if xp >= 0 else 1


def xp_for_level(level: int) -> int:
    """XP threshold to reach a given level."""
    return 50 * (level - 1) ** 2


async def get_or_create_xp(discord_user: str) -> dict:
    """Return {total_xp, level} for a user, creating row if needed."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT total_xp, level FROM user_xp WHERE discord_user = ?",
            (discord_user,),
        )
        row = await cursor.fetchone()
        if row:
            return dict(row)
        await db.execute(
            "INSERT INTO user_xp (discord_user, total_xp, level) VALUES (?, 0, 1)",
            (discord_user,),
        )
        await db.commit()
    return {"total_xp": 0, "level": 1}


async def add_xp(discord_user: str, amount: int) -> dict:
    """Add XP, recalculate level. Returns {total_xp, level, leveled_up, old_level}."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT total_xp, level FROM user_xp WHERE discord_user = ?",
            (discord_user,),
        )
        row = await cursor.fetchone()
        if row is None:
            old_xp, old_level = 0, 1
            await db.execute(
                "INSERT INTO user_xp (discord_user, total_xp, level) VALUES (?, ?, 1)",
                (discord_user, amount),
            )
        else:
            old_xp, old_level = row["total_xp"], row["level"]
        new_xp = old_xp + amount
        new_level = compute_level(new_xp)
        await db.execute(
            "UPDATE user_xp SET total_xp = ?, level = ? WHERE discord_user = ?",
            (new_xp, new_level, discord_user),
        )
        await db.commit()
    return {
        "total_xp": new_xp,
        "level": new_level,
        "leveled_up": new_level > old_level,
        "old_level": old_level,
    }


# ── Achievements ─────────────────────────────────────────────────────────────


async def unlock_achievement(discord_user: str, achievement_id: str) -> bool:
    """Unlock an achievement. Returns True if newly unlocked."""
    now_iso = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO user_achievements (discord_user, achievement_id, unlocked_at) "
                "VALUES (?, ?, ?)",
                (discord_user, achievement_id, now_iso),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def get_user_achievements(discord_user: str) -> list[dict]:
    """All achievements a user has unlocked."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT achievement_id, unlocked_at FROM user_achievements "
            "WHERE discord_user = ? ORDER BY unlocked_at ASC",
            (discord_user,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def has_achievement(discord_user: str, achievement_id: str) -> bool:
    """Check if user has a specific achievement."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT 1 FROM user_achievements WHERE discord_user = ? AND achievement_id = ?",
            (discord_user, achievement_id),
        )
        return await cursor.fetchone() is not None


# ── Daily Challenges ─────────────────────────────────────────────────────────


async def get_daily_challenge_slots(
    discord_user: str, date: str, challenge_ids: list[str],
) -> list[dict]:
    """Return the 3 slots for a user/date, creating them if needed."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM daily_challenges WHERE discord_user = ? AND challenge_date = ? "
            "ORDER BY slot ASC",
            (discord_user, date),
        )
        rows = await cursor.fetchall()
        if rows:
            return [dict(r) for r in rows]
        # Create the 3 slots
        for i, cid in enumerate(challenge_ids):
            await db.execute(
                "INSERT OR IGNORE INTO daily_challenges "
                "(discord_user, challenge_date, slot, challenge_id) VALUES (?, ?, ?, ?)",
                (discord_user, date, i, cid),
            )
        await db.commit()
        cursor = await db.execute(
            "SELECT * FROM daily_challenges WHERE discord_user = ? AND challenge_date = ? "
            "ORDER BY slot ASC",
            (discord_user, date),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def complete_daily_challenge(
    discord_user: str, date: str, slot: int, coins: int,
) -> None:
    """Mark a challenge slot as completed and award coins."""
    now_iso = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE daily_challenges SET completed = 1, completed_at = ? "
            "WHERE discord_user = ? AND challenge_date = ? AND slot = ?",
            (now_iso, discord_user, date, slot),
        )
        await db.execute(
            "UPDATE casino_wallets SET balance = balance + ? WHERE discord_user = ?",
            (coins, discord_user),
        )
        await db.commit()


async def is_daily_bonus_claimed(discord_user: str, date: str) -> bool:
    """Check if user already claimed the all-3 bonus."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT 1 FROM daily_bonus_claimed WHERE discord_user = ? AND challenge_date = ?",
            (discord_user, date),
        )
        return await cursor.fetchone() is not None


async def claim_daily_bonus(discord_user: str, date: str, coins: int) -> None:
    """Record all-3 bonus claim and award coins."""
    now_iso = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO daily_bonus_claimed (discord_user, challenge_date, claimed_at) "
            "VALUES (?, ?, ?)",
            (discord_user, date, now_iso),
        )
        await db.execute(
            "UPDATE casino_wallets SET balance = balance + ? WHERE discord_user = ?",
            (coins, discord_user),
        )
        await db.commit()


async def get_todays_casino_history(discord_user: str, date: str) -> list[dict]:
    """All casino history entries for a user on a date (YYYY-MM-DD prefix match)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM casino_history WHERE discord_user = ? AND played_at LIKE ? "
            "ORDER BY id ASC",
            (discord_user, f"{date}%"),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_casino_history_since(last_id: int) -> list[dict]:
    """Return casino_history entries with id > last_id."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM casino_history WHERE id > ? ORDER BY id ASC",
            (last_id,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_consecutive_daily_completions(discord_user: str, before_date: str) -> int:
    """Count consecutive days before `before_date` where all 3 challenges were completed."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT challenge_date,
                      SUM(completed) AS done, COUNT(*) AS total
               FROM daily_challenges
               WHERE discord_user = ? AND challenge_date < ?
               GROUP BY challenge_date
               ORDER BY challenge_date DESC""",
            (discord_user, before_date),
        )
        rows = await cursor.fetchall()
    streak = 0
    from datetime import timedelta
    expected = datetime.strptime(before_date, "%Y-%m-%d") - timedelta(days=1)
    for row in rows:
        row_date = datetime.strptime(row["challenge_date"], "%Y-%m-%d")
        if row_date.date() != expected.date():
            break
        if row["done"] < 3:
            break
        streak += 1
        expected -= timedelta(days=1)
    return streak


# ── Duels ────────────────────────────────────────────────────────────────────


async def create_duel(
    channel_id: str, challenger_id: str, opponent_id: str, wager: int,
) -> int:
    """Create a pending duel. Returns duel_id."""
    now_iso = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO duels
               (channel_id, challenger_id, opponent_id, wager, status,
                score_challenger, score_opponent, started_at)
               VALUES (?, ?, ?, ?, 'pending', 1000, 1000, ?)""",
            (channel_id, challenger_id, opponent_id, wager, now_iso),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore[return-value]


async def get_duel(duel_id: int) -> dict | None:
    """Return duel row as dict."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM duels WHERE duel_id = ?", (duel_id,),
        )
        row = await cursor.fetchone()
    return dict(row) if row else None


async def get_active_duel_in_channel(channel_id: str) -> dict | None:
    """Return pending or active duel in channel."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM duels WHERE channel_id = ? AND status IN ('pending', 'active') "
            "ORDER BY duel_id DESC LIMIT 1",
            (channel_id,),
        )
        row = await cursor.fetchone()
    return dict(row) if row else None


async def update_duel(duel_id: int, **kwargs: object) -> None:
    """Update duel fields dynamically."""
    if not kwargs:
        return
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [duel_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE duels SET {sets} WHERE duel_id = ?", vals)
        await db.commit()


async def get_duel_stats(discord_user: str) -> dict:
    """W-L record for finished duels."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT
                   SUM(CASE WHEN winner_id = ? THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN winner_id IS NOT NULL AND winner_id != ? THEN 1 ELSE 0 END) AS losses,
                   SUM(CASE WHEN winner_id IS NULL THEN 1 ELSE 0 END) AS draws
               FROM duels
               WHERE status = 'finished'
                 AND (challenger_id = ? OR opponent_id = ?)""",
            (discord_user, discord_user, discord_user, discord_user),
        )
        row = await cursor.fetchone()
    if row and row["wins"] is not None:
        return dict(row)
    return {"wins": 0, "losses": 0, "draws": 0}


# ── Tournaments ──────────────────────────────────────────────────────────────


async def create_tournament(
    channel_id: str, host_id: str, size: int, buy_in: int,
) -> int:
    """Create a tournament in registration phase. Returns tournament_id."""
    now_iso = datetime.now(timezone.utc).isoformat()
    prize_pool = size * buy_in
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO tournaments
               (game, size, buy_in, prize_pool, status, host_id, channel_id, created_at)
               VALUES ('minigames', ?, ?, ?, 'registration', ?, ?, ?)""",
            (size, buy_in, prize_pool, host_id, channel_id, now_iso),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore[return-value]


async def join_tournament(tournament_id: int, discord_user: str) -> None:
    """Add a player to tournament entries."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO tournament_entries (tournament_id, discord_user) "
            "VALUES (?, ?)",
            (tournament_id, discord_user),
        )
        await db.commit()


async def get_tournament(tournament_id: int) -> dict | None:
    """Return tournament row as dict."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM tournaments WHERE tournament_id = ?",
            (tournament_id,),
        )
        row = await cursor.fetchone()
    return dict(row) if row else None


async def get_tournament_in_channel(channel_id: str) -> dict | None:
    """Return active/registration tournament in channel."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM tournaments WHERE channel_id = ? "
            "AND status IN ('registration', 'active') "
            "ORDER BY tournament_id DESC LIMIT 1",
            (channel_id,),
        )
        row = await cursor.fetchone()
    return dict(row) if row else None


async def get_tournament_entries(tournament_id: int) -> list[dict]:
    """All entries for a tournament."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM tournament_entries WHERE tournament_id = ? ORDER BY seed ASC",
            (tournament_id,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def update_tournament(tournament_id: int, **kwargs: object) -> None:
    """Update tournament fields dynamically."""
    if not kwargs:
        return
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [tournament_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE tournaments SET {sets} WHERE tournament_id = ?", vals)
        await db.commit()


async def update_tournament_entry(
    tournament_id: int, discord_user: str, **kwargs: object,
) -> None:
    """Update a tournament entry."""
    if not kwargs:
        return
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [tournament_id, discord_user]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE tournament_entries SET {sets} "
            "WHERE tournament_id = ? AND discord_user = ?",
            vals,
        )
        await db.commit()


async def get_tournament_stats(discord_user: str) -> dict:
    """Tournament stats: wins, entries, total_payout."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT
                   COUNT(*) AS entries,
                   SUM(CASE WHEN final_place = 1 THEN 1 ELSE 0 END) AS wins,
                   COALESCE(SUM(payout), 0) AS total_payout
               FROM tournament_entries
               WHERE discord_user = ?""",
            (discord_user,),
        )
        row = await cursor.fetchone()
    if row and row["entries"] is not None:
        return dict(row)
    return {"entries": 0, "wins": 0, "total_payout": 0}


async def get_casino_win_streak(discord_user: str) -> int:
    """Current consecutive win streak from casino_history (most recent first)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT wagered, payout FROM casino_history "
            "WHERE discord_user = ? ORDER BY id DESC",
            (discord_user,),
        )
        rows = await cursor.fetchall()
    streak = 0
    for r in rows:
        if r["payout"] > r["wagered"]:
            streak += 1
        else:
            break
    return streak


async def get_distinct_games_played(discord_user: str) -> int:
    """Count of distinct casino games a user has played."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(DISTINCT game) FROM casino_history WHERE discord_user = ?",
            (discord_user,),
        )
        row = await cursor.fetchone()
    return row[0] if row else 0


async def get_max_single_profit(discord_user: str) -> int:
    """Largest single-round profit from casino_history."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT MAX(payout - wagered) FROM casino_history WHERE discord_user = ?",
            (discord_user,),
        )
        row = await cursor.fetchone()
    return row[0] if row and row[0] else 0


# ── User Settings ─────────────────────────────────────────────────────────────


async def get_craps_default_bet(discord_user: str) -> int | None:
    """Return the user's saved default craps bet, or None if not set."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT craps_default_bet FROM user_settings WHERE discord_user = ?",
            (discord_user,),
        )
        row = await cursor.fetchone()
    return row["craps_default_bet"] if row else None


async def set_craps_default_bet(discord_user: str, amount: int) -> None:
    """Save the user's default craps bet amount (upsert)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO user_settings (discord_user, craps_default_bet)
            VALUES (?, ?)
            ON CONFLICT(discord_user) DO UPDATE SET craps_default_bet = excluded.craps_default_bet
            """,
            (discord_user, amount),
        )
        await db.commit()


async def get_crapless_default_bet(discord_user: str) -> int | None:
    """Return the user's saved default crapless craps bet, or None if not set."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT crapless_default_bet FROM user_settings WHERE discord_user = ?",
            (discord_user,),
        )
        row = await cursor.fetchone()
    return row["crapless_default_bet"] if row else None


async def set_crapless_default_bet(discord_user: str, amount: int) -> None:
    """Save the user's default crapless craps bet amount (upsert)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO user_settings (discord_user, crapless_default_bet)
            VALUES (?, ?)
            ON CONFLICT(discord_user) DO UPDATE SET crapless_default_bet = excluded.crapless_default_bet
            """,
            (discord_user, amount),
        )
        await db.commit()


# ── Discord User Cache (for web leaderboard) ────────────────────────────────


async def upsert_discord_user(
    discord_user: str, username: str, avatar_url: str | None,
) -> None:
    """Cache a Discord user's display name and avatar for the web leaderboard."""
    now_iso = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO discord_users (discord_user, username, avatar_url, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(discord_user) DO UPDATE SET
                   username = excluded.username,
                   avatar_url = excluded.avatar_url,
                   updated_at = excluded.updated_at""",
            (discord_user, username, avatar_url, now_iso),
        )
        await db.commit()


# ── ELO Ratings ──────────────────────────────────────────────────────────────


async def get_elo_rating(discord_user: str, game: str) -> dict:
    """Return ELO row for a user+game, creating a default 1000 row if none."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM elo_ratings WHERE discord_user = ? AND game = ?",
            (discord_user, game),
        )
        row = await cursor.fetchone()
        if row:
            return dict(row)
        await db.execute(
            "INSERT INTO elo_ratings (discord_user, game) VALUES (?, ?)",
            (discord_user, game),
        )
        await db.commit()
    return {
        "discord_user": discord_user, "game": game,
        "rating": 1000.0, "games_played": 0,
        "wins": 0, "losses": 0, "draws": 0,
        "peak_rating": 1000.0, "last_played": None,
    }


async def update_elo_rating(
    discord_user: str, game: str, new_rating: float,
    result: str, peak: float,
) -> None:
    """Update rating, increment counters, update peak and last_played."""
    now_iso = datetime.now(timezone.utc).isoformat()
    col = {"win": "wins", "loss": "losses", "draw": "draws"}.get(result, "draws")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"""UPDATE elo_ratings
                SET rating = ?, games_played = games_played + 1,
                    {col} = {col} + 1,
                    peak_rating = ?, last_played = ?
                WHERE discord_user = ? AND game = ?""",
            (new_rating, peak, now_iso, discord_user, game),
        )
        await db.commit()


async def log_elo_match(
    discord_user: str, opponent_user: str | None,
    game: str, result: float,
    rating_before: float, rating_after: float,
    context: str,
) -> None:
    """Insert a row into elo_match_history."""
    now_iso = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO elo_match_history
               (discord_user, opponent_user, game, result,
                rating_before, rating_after, rating_change, context, played_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (discord_user, opponent_user, game, result,
             rating_before, rating_after, rating_after - rating_before,
             context, now_iso),
        )
        await db.commit()


async def get_elo_ratings_for_user(discord_user: str) -> list[dict]:
    """All rated games for a user, ordered by rating descending."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM elo_ratings WHERE discord_user = ? ORDER BY rating DESC",
            (discord_user,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_elo_leaderboard(
    game: str, min_games: int = 5, limit: int = 25,
) -> list[dict]:
    """Top players for a game, filtered by min games played."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT * FROM elo_ratings
               WHERE game = ? AND games_played >= ?
               ORDER BY rating DESC
               LIMIT ?""",
            (game, min_games, limit),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_all_elo_leaderboards(
    min_games: int = 5,
) -> dict[str, list[dict]]:
    """All games' leaderboards keyed by game name. For F1 points calc."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT * FROM elo_ratings
               WHERE games_played >= ?
               ORDER BY game, rating DESC""",
            (min_games,),
        )
        rows = await cursor.fetchall()
    result: dict[str, list[dict]] = {}
    for r in rows:
        d = dict(r)
        result.setdefault(d["game"], []).append(d)
    return result


async def get_elo_history(
    discord_user: str, game: str, limit: int = 10,
) -> list[dict]:
    """Recent match history for a user in a game."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT * FROM elo_match_history
               WHERE discord_user = ? AND game = ?
               ORDER BY played_at DESC
               LIMIT ?""",
            (discord_user, game, limit),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


# ── Web Game Sessions ────────────────────────────────────────────────────────


async def create_game_session(
    room_id: str, game_type: str, host_discord_id: str, channel_id: str,
) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO game_sessions
               (room_id, game_type, host_discord_id, channel_id, status, created_at)
               VALUES (?, ?, ?, ?, 'waiting', ?)""",
            (room_id, game_type, host_discord_id, channel_id, now_iso),
        )
        await db.commit()


async def create_game_token(
    token: str, room_id: str, discord_user: str, display_name: str, wager: int,
) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO game_tokens
               (token, room_id, discord_user, display_name, wager, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (token, room_id, discord_user, display_name, wager, now_iso),
        )
        await db.commit()


async def get_game_token(token: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM game_tokens WHERE token = ?", (token,),
        )
        row = await cursor.fetchone()
    return dict(row) if row else None


async def finish_game_session(
    room_id: str, result_json: str, prize_pool: int,
) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE game_sessions SET status = 'finished', result_json = ?,
               prize_pool = ?, finished_at = ? WHERE room_id = ?""",
            (result_json, prize_pool, now_iso, room_id),
        )
        await db.commit()


async def get_game_session(room_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM game_sessions WHERE room_id = ?", (room_id,),
        )
        row = await cursor.fetchone()
    return dict(row) if row else None


# ── Geo Accuracy Stats ─────────────────────────────────────────────────


async def record_geo_attempt(
    discord_user: str, country: str, region: str, category: str, correct: bool,
) -> None:
    """Upsert a geography attempt into geo_accuracy, incrementing totals."""
    correct_inc = 1 if correct else 0
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO geo_accuracy (discord_user, country, region, category, correct, total)
            VALUES (?, ?, ?, ?, ?, 1)
            ON CONFLICT(discord_user, country, category) DO UPDATE SET
                correct = correct + ?,
                total = total + 1
            """,
            (discord_user, country, region, category, correct_inc, correct_inc),
        )
        await db.commit()


async def get_geo_stats(discord_user: str) -> list[dict]:
    """Return all geo_accuracy rows for a user."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT country, region, category, correct, total "
            "FROM geo_accuracy WHERE discord_user = ? ORDER BY country, category",
            (discord_user,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_geo_stats_by_region(discord_user: str) -> list[dict]:
    """Return aggregated geo accuracy stats grouped by region."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT region, SUM(correct) AS correct, SUM(total) AS total
            FROM geo_accuracy
            WHERE discord_user = ?
            GROUP BY region
            ORDER BY region
            """,
            (discord_user,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


# ── Startup cleanup ─────────────────────────────────────────────────


async def cleanup_stale_duels() -> int:
    """Expire pending/active duels left over from a previous bot session.

    Refunds wagers for duels that were *active* (coins already deducted).
    Pending duels never had coins taken, so they just get marked expired.
    Returns the number of duels cleaned up.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    cleaned = 0
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Active duels need refunds
        cursor = await db.execute(
            "SELECT duel_id, challenger_id, opponent_id, wager "
            "FROM duels WHERE status = 'active'",
        )
        active = [dict(r) for r in await cursor.fetchall()]
        for d in active:
            if d["wager"] > 0:
                for uid in (d["challenger_id"], d["opponent_id"]):
                    try:
                        await db.execute(
                            "UPDATE casino_wallets SET balance = balance + ? "
                            "WHERE discord_user = ?",
                            (d["wager"], uid),
                        )
                    except Exception:
                        log.warning("Failed to refund duel wager for user %s (duel %s)", uid, d["duel_id"], exc_info=True)
            await db.execute(
                "UPDATE duels SET status = 'expired', finished_at = ? "
                "WHERE duel_id = ?",
                (now_iso, d["duel_id"]),
            )
            cleaned += 1
        # Pending duels — no coins taken, just expire
        cursor = await db.execute(
            "UPDATE duels SET status = 'expired', finished_at = ? "
            "WHERE status = 'pending'",
            (now_iso,),
        )
        cleaned += cursor.rowcount
        await db.commit()
    return cleaned


async def cleanup_stale_tournaments() -> int:
    """Cancel registration/active tournaments from a previous session.

    Refunds buy-ins to all entrants. Returns the number of tournaments cleaned up.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    cleaned = 0
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT tournament_id, buy_in FROM tournaments "
            "WHERE status IN ('registration', 'active')",
        )
        stale = [dict(r) for r in await cursor.fetchall()]
        for t in stale:
            if t["buy_in"] > 0:
                ecursor = await db.execute(
                    "SELECT discord_user FROM tournament_entries "
                    "WHERE tournament_id = ?",
                    (t["tournament_id"],),
                )
                for entry in await ecursor.fetchall():
                    try:
                        await db.execute(
                            "UPDATE casino_wallets SET balance = balance + ? "
                            "WHERE discord_user = ?",
                            (t["buy_in"], entry["discord_user"]),
                        )
                    except Exception:
                        log.warning("Failed to refund tournament buy-in for user %s (tournament %s)", entry["discord_user"], t["tournament_id"], exc_info=True)
            await db.execute(
                "UPDATE tournaments SET status = 'cancelled', finished_at = ? "
                "WHERE tournament_id = ?",
                (now_iso, t["tournament_id"]),
            )
            cleaned += 1
        await db.commit()
    return cleaned


async def cleanup_stale_game_sessions() -> int:
    """Cancel web game sessions stuck in 'waiting' from a previous session.

    Refunds each player's wager via game_tokens. Returns sessions cleaned.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    cleaned = 0
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT room_id FROM game_sessions WHERE status = 'waiting'",
        )
        stale = [dict(r) for r in await cursor.fetchall()]
        for s in stale:
            tcursor = await db.execute(
                "SELECT discord_user, wager FROM game_tokens "
                "WHERE room_id = ?",
                (s["room_id"],),
            )
            for tok in await tcursor.fetchall():
                if tok["wager"] > 0:
                    try:
                        await db.execute(
                            "UPDATE casino_wallets SET balance = balance + ? "
                            "WHERE discord_user = ?",
                            (tok["wager"], tok["discord_user"]),
                        )
                    except Exception:
                        log.warning("Failed to refund game token wager for user %s (room %s)", tok["discord_user"], s["room_id"], exc_info=True)
            await db.execute(
                "UPDATE game_sessions SET status = 'finished', finished_at = ? "
                "WHERE room_id = ?",
                (now_iso, s["room_id"]),
            )
            cleaned += 1
        await db.commit()
    return cleaned


# ── Active Discord table tracking (cross-session cleanup) ────────────────────


async def register_discord_table(channel_id: int, message_id: int | None, game_type: str) -> None:
    """Record that a Discord game table is open in channel_id.

    Called when a table is created. Persists across bot restarts so that
    on_ready can close orphaned tables from the previous session.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO active_discord_tables "
            "(channel_id, message_id, game_type, created_at) VALUES (?, ?, ?, ?)",
            (channel_id, message_id, game_type, now_iso),
        )
        await db.commit()


async def unregister_discord_table(channel_id: int) -> None:
    """Remove the table record when a game closes normally."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM active_discord_tables WHERE channel_id = ?",
            (channel_id,),
        )
        await db.commit()


async def get_stale_discord_tables(game_type: str | None = None) -> list[dict]:
    """Return records for tables still in the DB (orphaned from a previous session).

    Pass game_type to filter by game, or None to return all.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if game_type is not None:
            cursor = await db.execute(
                "SELECT channel_id, message_id, game_type FROM active_discord_tables "
                "WHERE game_type = ?",
                (game_type,),
            )
        else:
            cursor = await db.execute(
                "SELECT channel_id, message_id, game_type FROM active_discord_tables",
            )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]
