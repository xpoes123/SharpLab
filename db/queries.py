"""All DB access lives here. No raw SQL anywhere else."""
from __future__ import annotations
import json
from datetime import datetime, timezone
import aiosqlite
from shared.models import Bet, Game, OddsSnapshot
from db.schema import DB_PATH


async def upsert_game(game: Game) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO games (game_id, home_team, away_team, start_time)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(game_id) DO UPDATE SET
                home_team  = excluded.home_team,
                away_team  = excluded.away_team,
                start_time = excluded.start_time
            """,
            (game.game_id, game.home_team, game.away_team, game.start_time_utc_iso),
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


async def find_games_by_team(team_name: str) -> list[Game]:
    """Find games where either team name contains the query (case-insensitive)."""
    pattern = f"%{team_name}%"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM games
            WHERE home_team LIKE ? OR away_team LIKE ?
            ORDER BY start_time ASC
            """,
            (pattern, pattern),
        )
        rows = await cursor.fetchall()
    return [
        Game(
            game_id=row["game_id"],
            home_team=row["home_team"],
            away_team=row["away_team"],
            start_time_utc_iso=row["start_time"],
        )
        for row in rows
    ]


async def get_upcoming_games(filter_str: str = "") -> list[Game]:
    """Return upcoming games (start_time >= now), optionally filtered by team name."""
    now = datetime.now(timezone.utc).isoformat()
    pattern = f"%{filter_str}%"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM games
            WHERE start_time >= ?
              AND (home_team LIKE ? OR away_team LIKE ?)
            ORDER BY start_time ASC
            LIMIT 25
            """,
            (now, pattern, pattern),
        )
        rows = await cursor.fetchall()
    return [
        Game(
            game_id=row["game_id"],
            home_team=row["home_team"],
            away_team=row["away_team"],
            start_time_utc_iso=row["start_time"],
        )
        for row in rows
    ]


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
    return Game(
        game_id=row["game_id"],
        home_team=row["home_team"],
        away_team=row["away_team"],
        start_time_utc_iso=row["start_time"],
    )


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
