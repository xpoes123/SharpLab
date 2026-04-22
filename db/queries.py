"""All DB access lives here. No raw SQL anywhere else."""
from __future__ import annotations
import json
from datetime import datetime, timezone
import aiosqlite
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
    """Add/subtract coins. Returns new balance. Raises ValueError if insufficient."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT balance FROM wallets WHERE discord_user = ?",
            (discord_user,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ValueError("No wallet found")
        new_balance = row["balance"] + delta
        if new_balance < 0:
            raise ValueError(f"Insufficient coins (have {row['balance']}, need {-delta})")
        await db.execute(
            "UPDATE wallets SET balance = ? WHERE discord_user = ?",
            (new_balance, discord_user),
        )
        await db.commit()
        return new_balance


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
    """Add/subtract casino coins. Returns new balance. Raises ValueError if insufficient."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT balance FROM casino_wallets WHERE discord_user = ?",
            (discord_user,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ValueError("No casino wallet found")
        new_balance = row["balance"] + delta
        if new_balance < 0:
            raise ValueError(f"Insufficient casino coins (have {row['balance']}, need {-delta})")
        if new_balance < CASINO_MIN_BALANCE:
            new_balance = CASINO_MIN_BALANCE
        await db.execute(
            "UPDATE casino_wallets SET balance = ? WHERE discord_user = ?",
            (new_balance, discord_user),
        )
        await db.commit()
        return new_balance


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


# ── Casino History ────────────────────────────────────────────────────────────


async def log_casino_result(
    discord_user: str, game: str, wagered: int, payout: int,
) -> None:
    """Record a completed casino round for PnL tracking."""
    now_iso = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO casino_history (discord_user, game, wagered, payout, played_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (discord_user, game, wagered, payout, now_iso),
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
