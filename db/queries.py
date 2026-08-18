"""All DB access lives here. No raw SQL anywhere else."""
from __future__ import annotations
import json
import logging
import math
import random
from datetime import datetime, timedelta, timezone
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


async def purge_post_tip_closes(grace_seconds: int = 0) -> int:
    """Delete `close` snapshots captured after their game's tip-off (+grace).

    These are live-contaminated: the close-capture used to fire AT tip and retry
    into the first in-game price. Parsed in Python so mixed 'Z'/'+00:00' offsets
    compare correctly. Returns the number deleted."""
    def _p(s: str):
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT s.snapshot_id, s.captured_at, g.start_time
               FROM odds_snapshots s JOIN games g ON g.game_id = s.game_id
               WHERE s.kind = 'close'""",
        )
        rows = await cursor.fetchall()
        late = [r["snapshot_id"] for r in rows
                if (c := _p(r["captured_at"])) and (st := _p(r["start_time"]))
                and (c - st).total_seconds() > grace_seconds]
        if late:
            placeholders = ",".join("?" * len(late))
            await db.execute(
                f"DELETE FROM odds_snapshots WHERE snapshot_id IN ({placeholders})",
                late,
            )
        await db.commit()
    return len(late)


async def get_games_started_before(cutoff_utc_iso: str) -> list[Game]:
    """All games (any sport) whose start_time is at/before a cutoff — i.e. their
    line has already closed. Used to backfill closing-line snapshots."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM games WHERE start_time <= ? ORDER BY start_time ASC",
            (cutoff_utc_iso,),
        )
        rows = await cursor.fetchall()
    return [_row_to_game(row) for row in rows]


async def get_live_games(sport: str | None = None, window_hours: int = 6) -> list[Game]:
    """In-progress games: started but not yet final, within the last `window_hours`.
    Used by the live in-play Kalshi swing watcher. (Game has no status field, so
    the `!= 'final'` gate is applied in SQL.)"""
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    lo = (now - timedelta(hours=window_hours)).isoformat()
    hi = now.isoformat()
    q = "SELECT * FROM games WHERE start_time <= ? AND start_time >= ? AND status != 'final'"
    params: list = [hi, lo]
    if sport:
        q += " AND sport = ?"
        params.append(sport)
    q += " ORDER BY start_time ASC"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(q, params)
        rows = await cursor.fetchall()
    return [_row_to_game(row) for row in rows]


async def get_loggable_games(filter_str: str = "", sport: str = "nba") -> list[Game]:
    """Games you can still log a bet on: upcoming OR live (started, not yet final),
    within the last 6h. Ordered soonest-first. For /bet log incl. in-game bets."""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
    pattern = f"%{filter_str}%"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM games
            WHERE sport = ? AND status != 'final'
              AND start_time >= ?
              AND (home_team LIKE ? OR away_team LIKE ?)
            ORDER BY start_time ASC
            LIMIT 25
            """,
            (sport, cutoff, pattern, pattern),
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


async def get_streamable_games(filter_str: str = "", sport: str = "nba") -> list[Game]:
    """Upcoming games plus those that tipped off within the last 6 hours.

    Used by /stream so post-game wrap-ups stay selectable after the game ends.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
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
            (sport, cutoff, pattern, pattern),
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


async def get_bets_for_user(discord_user: str, limit: int = 100) -> list[Bet]:
    """Return bets for a user, newest first."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM bets WHERE discord_user = ? ORDER BY placed_at DESC LIMIT ?",
            (discord_user, limit),
        )
        rows = await cursor.fetchall()
    return [_row_to_bet(r) for r in rows]


async def get_all_settled_bets(since_iso: str | None = None) -> list[Bet]:
    """Every settled (won/lost/push) bet across all users — for leaderboards. If
    `since_iso` is given, only bets placed at/after it (for a weekly digest)."""
    q = "SELECT * FROM bets WHERE status IN ('won','lost','push')"
    params: tuple = ()
    if since_iso:
        q += " AND placed_at >= ?"
        params = (since_iso,)
    q += " ORDER BY placed_at DESC"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(q, params)
        rows = await cursor.fetchall()
    return [_row_to_bet(r) for r in rows]


async def get_graded_bets_for_user(discord_user: str, limit: int = 100) -> list[Bet]:
    """Return bets with CLV computed (graded/won/lost/push/void), newest first."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM bets WHERE discord_user = ? AND clv IS NOT NULL "
            "ORDER BY placed_at DESC LIMIT ?",
            (discord_user, limit),
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


async def set_bet_clv(bet_id: int, clv: float | None) -> None:
    """Set CLV on a bet without touching its status. For backfill of already-resolved bets."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE bets SET clv = ? WHERE bet_id = ?",
            (clv, bet_id),
        )
        await db.commit()


async def upsert_player_props(records: list[dict]) -> None:
    """Upsert the latest prop line per (game, source, player, market)."""
    if not records:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            """INSERT INTO player_props (game_id, source, player, market, line, over_odds, under_odds, captured_at)
               VALUES (:game_id, :source, :player, :market, :line, :over_odds, :under_odds, :captured_at)
               ON CONFLICT(game_id, source, player, market) DO UPDATE SET
                 line=excluded.line, over_odds=excluded.over_odds,
                 under_odds=excluded.under_odds, captured_at=excluded.captured_at""",
            records,
        )
        await db.commit()


async def get_player_props_for_game(game_id: str) -> list[dict]:
    """All current prop lines for a game (across books/players/markets)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT source, player, market, line, over_odds, under_odds, captured_at "
            "FROM player_props WHERE game_id = ? ORDER BY player, market",
            (game_id,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def upsert_player_prop_alts(records: list[dict]) -> None:
    """Upsert alternate-line prop ladder rows (one per game/source/player/market/line)."""
    if not records:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            """INSERT INTO player_prop_alts (game_id, source, player, market, line, over_odds, under_odds, captured_at)
               VALUES (:game_id, :source, :player, :market, :line, :over_odds, :under_odds, :captured_at)
               ON CONFLICT(game_id, source, player, market, line) DO UPDATE SET
                 over_odds=excluded.over_odds, under_odds=excluded.under_odds,
                 captured_at=excluded.captured_at""",
            records,
        )
        await db.commit()


async def get_player_prop_alts_for_game(game_id: str) -> list[dict]:
    """All alternate-line prop rows for a game (full ladders across books/players)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT source, player, market, line, over_odds, under_odds, captured_at "
            "FROM player_prop_alts WHERE game_id = ? ORDER BY player, market, line",
            (game_id,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_prop_close_rows(
    player: str, market: str, game_date: str, sport: str = "wnba"
) -> list[dict]:
    """Captured CLOSING prop rows for one player+market on a given game date, merged
    main board + alt ladders. Rows come out in exactly the shape
    shared.prop_clv.prop_clv_at_line expects for its `rows` arg:
    [{source, line, over_odds, under_odds, captured_at}].

    `game_date` is a 'YYYY-MM-DD' UTC calendar date matched against games.start_time
    (stored UTC ISO). A US-evening WNBA tip can land on the next UTC day, so pass the
    UTC date of the tip (or query the adjacent day too if unsure).

    Read-only; intended for external consumers (e.g. vigil) to compute prop CLV off
    SharpLab's captured closes. The values are the last pre-tip poll per book — the
    polling loop stops refreshing a game once it tips, so that poll IS the close."""
    game_sub = (
        "SELECT game_id FROM games WHERE sport = ? AND substr(start_time, 1, 10) = ?"
    )
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            f"SELECT source, line, over_odds, under_odds, captured_at FROM player_props "
            f"  WHERE game_id IN ({game_sub}) AND player = ? AND market = ? "
            f"UNION ALL "
            f"SELECT source, line, over_odds, under_odds, captured_at FROM player_prop_alts "
            f"  WHERE game_id IN ({game_sub}) AND player = ? AND market = ?",
            (sport, game_date, player, market, sport, game_date, player, market),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_games_with_open_prop_bets() -> list[str]:
    """Distinct game_ids that have at least one open player-prop bet (for targeting
    which games' alternate ladders we bother polling)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT DISTINCT game_id FROM bets WHERE market = 'prop' AND status = 'open'"
        )
        rows = await cursor.fetchall()
    return [r[0] for r in rows]


async def get_open_bets_on_live_games(window_hours: int = 6) -> list[dict]:
    """Open bets whose game has started but isn't final yet (i.e. live), within
    the last `window_hours`. Joins game info. For live in-game swing alerts."""
    now = datetime.now(timezone.utc)
    lo = (now - timedelta(hours=window_hours)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT b.bet_id, b.discord_user, b.game_id, b.market, b.side, b.line, b.odds,
                      g.home_team, g.away_team, g.sport, g.start_time, g.status
               FROM bets b JOIN games g ON g.game_id = b.game_id
               WHERE b.status = 'open' AND g.status != 'final'
                 AND g.start_time <= ? AND g.start_time >= ?""",
            (now.isoformat(), lo),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


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


async def get_all_unresolved_bets() -> list[Bet]:
    """All bets still awaiting a final result (open/graded), oldest first. Backfill use."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM bets WHERE status IN ('open', 'graded') ORDER BY bet_id ASC"
        )
        rows = await cursor.fetchall()
    return [_row_to_bet(r) for r in rows]


async def get_all_bets_missing_clv() -> list[Bet]:
    """All non-void bets with no CLV recorded yet, oldest first. Backfill use."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM bets WHERE clv IS NULL AND status != 'void' ORDER BY bet_id ASC"
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


async def get_games_by_team_suffixes(
    home_last: str, away_last: str, after_utc_iso: str
) -> list[Game]:
    """All non-final games for a matchup (so the caller can date-match the right
    one — the same two teams often play on consecutive days)."""
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
            """,
            (f"%{home_last.lower()}", f"%{away_last.lower()}", after_utc_iso),
        )
        rows = await cursor.fetchall()
    return [_row_to_game(r) for r in rows]


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


async def get_injury_status(record_id: str) -> str | None:
    """Return the current tracked status for a player, or None if not in DB."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT status FROM injuries WHERE record_id = ?", (record_id,)
        )
        row = await cursor.fetchone()
    return row[0] if row else None


async def record_injury_alert(alert: InjuryAlert) -> None:
    """
    Persist an injury alert to DB with notified=0.
    Idempotent: if the exact same status is already stored the notified flag
    is preserved unchanged, so a retry never resets an already-processed alert.
    """
    async with aiosqlite.connect(DB_PATH) as db:
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
            # Status changed — reset notification flag so Discord gets alerted
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
        # else: status unchanged — leave notified flag alone (may already be processed)
        await db.commit()


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


async def get_last_poll_snapshots_before(game_id: str, before_utc_iso: str) -> list[OddsSnapshot]:
    """Most recent poll snapshot per source captured at/before a cutoff.

    Used as a closing-line proxy when backfilling CLV for games whose real
    'close' snapshot was never captured: the last line seen before tip-off.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT s.*
            FROM odds_snapshots s
            INNER JOIN (
                SELECT source, MAX(captured_at) AS max_captured
                FROM odds_snapshots
                WHERE game_id = ? AND kind = 'poll' AND captured_at <= ?
                GROUP BY source
            ) latest ON s.source = latest.source AND s.captured_at = latest.max_captured
            WHERE s.game_id = ? AND s.kind = 'poll' AND s.captured_at <= ?
            """,
            (game_id, before_utc_iso, game_id, before_utc_iso),
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
# Floor for debits/tips — you can lose all the way down to 0. It used to be 1000, which
# both topped every wallet back up to 1000 on play AND floored losses at 1000 — a coin
# faucet that made coins meaningless. Earning is now the 500/day message reward, the free
# daily pack, set-completion bonuses, quick-selling dupes, and winning games.
CASINO_MIN_BALANCE = 0


async def get_or_create_casino_wallet(discord_user: str) -> int:
    """Return casino balance. Creates a wallet with starting coins if new — but never tops an
    existing wallet back up (no faucet)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT balance FROM casino_wallets WHERE discord_user = ?",
            (discord_user,),
        )
        row = await cursor.fetchone()
        if row is not None:
            return row["balance"]
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


async def get_casino_net_since(since_iso: str) -> list[dict]:
    """Per-user casino net (payout − wagered), wagered, and rounds since a UTC ISO
    timestamp. For the weekly recap. Ordered by net desc."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT discord_user,
                   SUM(payout) - SUM(wagered) AS net,
                   SUM(wagered) AS wagered,
                   COUNT(id) AS rounds
            FROM casino_history
            WHERE played_at >= ?
            GROUP BY discord_user
            ORDER BY net DESC
            """,
            (since_iso,),
        )
        return [dict(r) for r in await cursor.fetchall()]


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


async def find_upcoming_game_by_team(fragment: str) -> Game | None:
    """Fuzzy-match an upcoming/in-progress game by a team-name fragment (e.g. 'lakers',
    'knicks') across all sports — for NLP bet logging."""
    if not fragment or len(fragment) < 3:
        return None
    now = datetime.now(timezone.utc)
    floor = (now - timedelta(hours=6)).isoformat()
    frag = f"%{fragment.strip().lower()}%"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM games WHERE status != 'final' AND start_time >= ? "
            "AND (lower(home_team) LIKE ? OR lower(away_team) LIKE ?) "
            "ORDER BY start_time ASC LIMIT 1",
            (floor, frag, frag),
        )
        row = await cursor.fetchone()
    return _row_to_game(row) if row else None


async def get_next_game_for_team(team: str, within_days: int = 5) -> Game | None:
    """Next unfinished game for a team within `within_days`. Unlike the today-only
    lookup, this handles playoff series where the next game is a day or two out."""
    now = datetime.now(timezone.utc)
    floor = (now - timedelta(hours=6)).isoformat()      # include a game already in progress
    horizon = (now + timedelta(days=within_days)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM games
            WHERE (home_team = ? OR away_team = ?)
              AND status != 'final'
              AND start_time >= ? AND start_time <= ?
            ORDER BY start_time ASC
            LIMIT 1
            """,
            (team, team, floor, horizon),
        )
        row = await cursor.fetchone()
    return _row_to_game(row) if row else None


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
) -> int:
    """Mark a paper bet as resolved with final status, payout, and optional CLV.

    Returns rowcount (1 if updated, 0 if already resolved/missing)."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            UPDATE paper_bets
            SET status = ?, payout = ?, resolved_at = ?, clv = ?
            WHERE paper_bet_id = ? AND status = 'open'
            """,
            (status, payout, now, clv, paper_bet_id),
        )
        await db.commit()
        return cursor.rowcount


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
            LIMIT 200
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


async def get_web_activity(discord_user: str) -> dict:
    """Count a member's HQ logins + page-load visits (for web achievements)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        try:
            cur = await db.execute(
                "SELECT SUM(type='login') AS logins, SUM(type='member_visit') AS visits "
                "FROM web_events WHERE sid = ? AND type IN ('login','member_visit')",
                (discord_user,),
            )
            row = await cur.fetchone()
        except Exception:
            return {"logins": 0, "visits": 0}
    return {"logins": (row["logins"] or 0) if row else 0,
            "visits": (row["visits"] or 0) if row else 0}


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


async def add_voice_minutes(discord_user: str, minutes: int) -> int:
    """Add to a user's cumulative voice minutes; return the new total."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO user_engagement (discord_user, voice_minutes) VALUES (?, ?) "
            "ON CONFLICT(discord_user) DO UPDATE SET voice_minutes = voice_minutes + ?",
            (discord_user, minutes, minutes),
        )
        cur = await db.execute("SELECT voice_minutes FROM user_engagement WHERE discord_user = ?", (discord_user,))
        row = await cur.fetchone()
        await db.commit()
    return row[0] if row else minutes


async def increment_message_count(discord_user: str) -> int:
    """Increment a user's cumulative message count; return the new total."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO user_engagement (discord_user, messages) VALUES (?, 1) "
            "ON CONFLICT(discord_user) DO UPDATE SET messages = messages + 1",
            (discord_user,),
        )
        cur = await db.execute("SELECT messages FROM user_engagement WHERE discord_user = ?", (discord_user,))
        row = await cur.fetchone()
        await db.commit()
    return row[0] if row else 1


async def get_engagement(discord_user: str) -> dict:
    """Return {voice_minutes, messages} for a user (zeros if none)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT voice_minutes, messages FROM user_engagement WHERE discord_user = ?",
            (discord_user,),
        )
        row = await cur.fetchone()
    return {"voice_minutes": row[0], "messages": row[1]} if row else {"voice_minutes": 0, "messages": 0}


async def get_user_odds_format(discord_user: str) -> str:
    """A user's preferred odds display format ('american' default)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT odds_format FROM user_settings WHERE discord_user = ?", (discord_user,))
        row = await cur.fetchone()
    return row[0] if row else "american"


async def set_user_odds_format(discord_user: str, fmt: str) -> None:
    """Set a user's preferred odds display format."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO user_settings (discord_user, odds_format) VALUES (?, ?) "
            "ON CONFLICT(discord_user) DO UPDATE SET odds_format = excluded.odds_format",
            (discord_user, fmt),
        )
        await db.commit()


async def get_user_books(discord_user: str) -> list[str]:
    """Sportsbook source-keys a user holds (for personalized best-line)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT books FROM user_settings WHERE discord_user = ?", (discord_user,))
        row = await cur.fetchone()
    return [b for b in (row[0].split(",") if row and row[0] else []) if b]


async def set_user_books(discord_user: str, books: list[str]) -> None:
    """Set the sportsbooks a user holds."""
    val = ",".join(books)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO user_settings (discord_user, books) VALUES (?, ?) "
            "ON CONFLICT(discord_user) DO UPDATE SET books = excluded.books",
            (discord_user, val),
        )
        await db.commit()


async def get_livebet_alerts_enabled(discord_user: str) -> bool:
    """Whether a user has opted in to live in-game bet swing pings (default off)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT livebet_alerts FROM user_settings WHERE discord_user = ?", (discord_user,))
        row = await cur.fetchone()
    return bool(row[0]) if row else False


async def set_livebet_alerts_enabled(discord_user: str, enabled: bool) -> None:
    """Opt a user in/out of live in-game bet swing pings."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO user_settings (discord_user, livebet_alerts) VALUES (?, ?) "
            "ON CONFLICT(discord_user) DO UPDATE SET livebet_alerts = excluded.livebet_alerts",
            (discord_user, 1 if enabled else 0),
        )
        await db.commit()


async def get_cards_fast_open(discord_user: str) -> bool:
    """Whether the user opted to always fast-open packs (skip the reveal animation)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT cards_fast_open FROM user_settings WHERE discord_user = ?", (discord_user,))
        row = await cur.fetchone()
    return bool(row[0]) if row else False


async def set_cards_fast_open(discord_user: str, enabled: bool) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO user_settings (discord_user, cards_fast_open) VALUES (?, ?) "
            "ON CONFLICT(discord_user) DO UPDATE SET cards_fast_open = excluded.cards_fast_open",
            (discord_user, 1 if enabled else 0),
        )
        await db.commit()


async def get_livebet_optin_users() -> set[str]:
    """All users who've opted in to live in-game bet swing pings."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT discord_user FROM user_settings WHERE livebet_alerts = 1")
        rows = await cur.fetchall()
    return {r[0] for r in rows}


async def get_stock_alerts_enabled(discord_user: str) -> bool:
    """Whether a user has opted in to portfolio swing alerts (default off)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT stock_alerts FROM user_settings WHERE discord_user = ?", (discord_user,))
        row = await cur.fetchone()
    return bool(row[0]) if row else False


async def set_stock_alerts_enabled(discord_user: str, enabled: bool) -> None:
    """Opt a user in/out of portfolio swing alerts (≥10% daily moves on a held ticker)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO user_settings (discord_user, stock_alerts) VALUES (?, ?) "
            "ON CONFLICT(discord_user) DO UPDATE SET stock_alerts = excluded.stock_alerts",
            (discord_user, 1 if enabled else 0),
        )
        await db.commit()


async def get_stock_alert_optin_users() -> set[str]:
    """All users who've opted in to portfolio swing alerts."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT discord_user FROM user_settings WHERE stock_alerts = 1")
        rows = await cur.fetchall()
    return {r[0] for r in rows}


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


async def get_xp_leaderboard(limit: int = 15) -> list[dict]:
    """Top users by total XP (with level + name/avatar) for the HQ levels board."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT x.discord_user, x.total_xp, x.level, u.username, u.avatar_url "
            "FROM user_xp x LEFT JOIN discord_users u ON u.discord_user = x.discord_user "
            "WHERE x.total_xp > 0 ORDER BY x.total_xp DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def get_achievement_leaderboard(limit: int = 15) -> list[dict]:
    """Top users by number of achievements unlocked, for the HQ achievements board."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT a.discord_user, COUNT(*) AS unlocked, u.username, u.avatar_url "
            "FROM user_achievements a LEFT JOIN discord_users u ON u.discord_user = a.discord_user "
            "GROUP BY a.discord_user ORDER BY unlocked DESC, MAX(a.unlocked_at) ASC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in await cur.fetchall()]


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


_DUEL_COLUMNS = frozenset({
    "status", "winner_id", "score_challenger", "score_opponent",
    "games_played", "finished_at",
})


async def update_duel(duel_id: int, **kwargs: object) -> None:
    """Update duel fields dynamically."""
    if not kwargs:
        return
    invalid = set(kwargs) - _DUEL_COLUMNS
    if invalid:
        raise ValueError(f"Invalid duel column(s): {invalid}")
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


_TOURNAMENT_COLUMNS = frozenset({
    "status", "bracket_json", "finished_at",
})


async def update_tournament(tournament_id: int, **kwargs: object) -> None:
    """Update tournament fields dynamically."""
    if not kwargs:
        return
    invalid = set(kwargs) - _TOURNAMENT_COLUMNS
    if invalid:
        raise ValueError(f"Invalid tournament column(s): {invalid}")
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
                   COALESCE(SUM(CASE WHEN final_place = 1 THEN 1 ELSE 0 END), 0) AS wins,
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
            "WHERE discord_user = ? ORDER BY id DESC LIMIT 200",
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


async def get_elo_game_popularity() -> list[dict]:
    """Per-game activity, most-played first: {game, total_plays, players}.

    total_plays = sum of games_played across all rated players in that game
    (a 5-player match adds 5). Used to order the leaderboard browser so the
    most popular games surface first.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT game,
                      SUM(games_played) AS total_plays,
                      COUNT(*) AS players
               FROM elo_ratings
               GROUP BY game
               HAVING total_plays > 0
               ORDER BY total_plays DESC, players DESC, game ASC""",
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


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
        if stale:
            placeholders = ",".join("?" * len(stale))
            ids = [t["tournament_id"] for t in stale]
            await db.execute(
                f"UPDATE tournaments SET status = 'cancelled', finished_at = ? "
                f"WHERE tournament_id IN ({placeholders})",
                [now_iso, *ids],
            )
        await db.commit()
    return len(stale)


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


# ── Error Logs ──────────────────────────────────────────────────────────────


async def insert_error_log(
    timestamp: str,
    error_type: str,
    command: str | None,
    user_id: str | None,
    guild_id: str | None,
    channel_id: str | None,
    stack_trace: str | None,
    severity: str,
    error_signature: str,
    ticket_id: str | None = None,
) -> int:
    """Insert a new error log row and return the id."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO error_logs
                (timestamp, error_type, command, user_id, guild_id, channel_id,
                 stack_trace, severity, error_signature, ticket_id, last_occurred)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (timestamp, error_type, command, user_id, guild_id, channel_id,
             stack_trace, severity, error_signature, ticket_id, timestamp),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore[return-value]


async def find_recent_error_by_signature(
    error_signature: str, window_minutes: int = 5,
) -> dict | None:
    """Find an error with the same signature within the dedup window."""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=window_minutes)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM error_logs
            WHERE error_signature = ? AND last_occurred >= ?
            ORDER BY last_occurred DESC LIMIT 1
            """,
            (error_signature, cutoff),
        )
        row = await cursor.fetchone()
    return dict(row) if row else None


async def increment_error_occurrence(error_id: int, timestamp: str) -> None:
    """Increment occurrence_count and update last_occurred."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE error_logs SET occurrence_count = occurrence_count + 1, "
            "last_occurred = ? WHERE id = ?",
            (timestamp, error_id),
        )
        await db.commit()


async def get_recent_errors(
    limit: int = 20,
    severity: str | None = None,
    command: str | None = None,
    resolved: bool | None = None,
) -> list[dict]:
    """Query recent errors with optional filters."""
    conditions: list[str] = []
    params: list[object] = []
    if severity is not None:
        conditions.append("severity = ?")
        params.append(severity)
    if command is not None:
        conditions.append("command = ?")
        params.append(command)
    if resolved is not None:
        conditions.append("resolved = ?")
        params.append(1 if resolved else 0)
    where = " AND ".join(conditions)
    sql = "SELECT * FROM error_logs"
    if where:
        sql += f" WHERE {where}"
    sql += " ORDER BY last_occurred DESC LIMIT ?"
    params.append(limit)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_unresolved_high_severity(limit: int = 20) -> list[dict]:
    """Return unresolved high-severity errors."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM error_logs WHERE resolved = 0 AND severity = 'high' "
            "ORDER BY last_occurred DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def resolve_error(
    error_id: int, resolved_by: str, resolution_note: str,
) -> None:
    """Mark an error as resolved."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE error_logs SET resolved = 1, resolved_at = ?, "
            "resolved_by = ?, resolution_note = ? WHERE id = ?",
            (now, resolved_by, resolution_note, error_id),
        )
        await db.commit()


async def reopen_error(error_id: int) -> None:
    """Reopen a resolved error (fix verification failed)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE error_logs SET resolved = 0, resolved_at = NULL, "
            "reopen_count = reopen_count + 1 WHERE id = ?",
            (error_id,),
        )
        await db.commit()


async def update_error_ticket_id(error_id: int, ticket_id: str) -> None:
    """Associate an error with a ticket."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE error_logs SET ticket_id = ? WHERE id = ?",
            (ticket_id, error_id),
        )
        await db.commit()


# ── Stock trades + derived holdings ─────────────────────────────────────────
# stock_trades is the source of truth. Holdings (shares + DCA) are aggregated
# from the trade log using SIGNED average-cost-basis accounting — net shares may
# be negative (a short). A sell beyond the held long opens/extends a short; a buy
# covers it (and can flip back to long). Same machinery as _aggregate_option_trades.


def _aggregate_trades(trades: list[dict]) -> dict:
    """Reduce a chronologically-ordered list of trades for one ticker.

    Signed average-cost accounting — `shares` may be NEGATIVE (a short). A sell
    that exceeds the held long opens/extends a short; a buy covers it (and any
    excess flips back to long). Returns: shares (signed), dca_price (avg OPEN
    price of the current position, always > 0 while open), cost_basis (gross =
    |shares|·dca_price, always >= 0), realized_pnl, invested, closed.

    Realized P/L accrues on any trade that REDUCES the position:
        sign(shares) * (close_price - avg) * closed_qty
    i.e. (price - avg) closing a long, (avg - price) covering a short. `invested`
    is lifetime capital deployed (every trade that opens or adds to a position,
    long or short) — the denominator for % return. Closed positions (net 0) still
    return their realized_pnl and invested; filter on `closed` for open-only.
    """
    net = 0.0          # signed shares
    avg = 0.0          # avg open price of the current position
    realized_pnl = 0.0
    invested = 0.0
    for t in trades:
        qty = t["shares"]
        px = t["price"]
        d = qty if t["side"] == "buy" else -qty
        if net == 0:
            net, avg = d, px
            invested += qty * px                  # opens a fresh position
            continue
        if (net > 0) == (d > 0):
            # Same side: blend into the running average open price.
            total = abs(net) + qty
            avg = (avg * abs(net) + px * qty) / total
            net += d
            invested += qty * px                  # adds to the position
            continue
        # Opposing side: close up to |net|, then flip any remainder.
        close_qty = min(qty, abs(net))
        realized_pnl += (1 if net > 0 else -1) * (px - avg) * close_qty
        net += d
        remaining = qty - close_qty
        if remaining > 1e-9:
            avg = px                              # flipped: new lot opens here
            invested += remaining * px            # capital for the flipped-into side
        elif abs(net) <= 1e-9:
            net, avg = 0.0, 0.0
    closed = abs(net) <= 1e-9
    return {
        "shares": 0.0 if closed else net,
        "dca_price": 0.0 if closed else avg,
        "cost_basis": 0.0 if closed else abs(net) * avg,
        "realized_pnl": realized_pnl,
        "invested": invested,
        "closed": closed,
    }


async def add_stock_trade(
    discord_user: str,
    ticker: str,
    side: str,
    shares: float,
    price: float,
    executed_at: str | None = None,
    notes: str | None = None,
) -> int:
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
    if shares <= 0 or price <= 0:
        raise ValueError("shares and price must be > 0")
    ts = executed_at or datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO stock_trades "
            "(discord_user, ticker, side, shares, price, executed_at, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (discord_user, ticker.upper(), side, shares, price, ts, notes),
        )
        await db.commit()
        return cur.lastrowid


async def get_stock_trades(
    discord_user: str, ticker: str | None = None, limit: int | None = None
) -> list[dict]:
    sql = (
        "SELECT trade_id, ticker, side, shares, price, executed_at, notes "
        "FROM stock_trades WHERE discord_user = ?"
    )
    params: list = [discord_user]
    if ticker:
        sql += " AND ticker = ?"
        params.append(ticker.upper())
    sql += " ORDER BY executed_at ASC, trade_id ASC"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(sql, params)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def delete_stock_trade(discord_user: str, trade_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM stock_trades WHERE discord_user = ? AND trade_id = ?",
            (discord_user, trade_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def update_stock_trade(discord_user: str, trade_id: int, shares: float, price: float,
                             executed_at: str | None = None) -> bool:
    """Correct a trade's shares/price (and optionally date). Scoped to the owner."""
    if shares <= 0 or price <= 0:
        raise ValueError("shares and price must be > 0")
    sets, params = ["shares = ?", "price = ?"], [shares, price]
    if executed_at is not None:
        sets.append("executed_at = ?")
        params.append(executed_at)
    params += [discord_user, trade_id]
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            f"UPDATE stock_trades SET {', '.join(sets)} WHERE discord_user = ? AND trade_id = ?",
            params,
        )
        await db.commit()
        return cur.rowcount > 0


async def delete_stock_trades_for_ticker(discord_user: str, ticker: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM stock_trades WHERE discord_user = ? AND ticker = ?",
            (discord_user, ticker.upper()),
        )
        await db.commit()
        return cur.rowcount


async def get_stock_holdings(discord_user: str) -> list[dict]:
    """Open positions across all tickers the user has traded. Closed positions
    (net shares == 0) are omitted; use get_stock_positions_full for those."""
    return [p for p in await get_stock_positions_full(discord_user) if not p["closed"]]


async def get_stock_holding(discord_user: str, ticker: str) -> dict | None:
    trades = await get_stock_trades(discord_user, ticker)
    if not trades:
        return None
    agg = _aggregate_trades(trades)
    if agg["closed"]:
        return None
    return {"ticker": ticker.upper(), **agg}


async def get_ticker_position(discord_user: str, ticker: str) -> dict | None:
    """Aggregate for a single ticker incl. realized_pnl, whether it's open OR fully
    closed. Unlike get_stock_holding (which returns None once closed), this keeps the
    realized P/L of a sold-out position — for /stock lookup. None only if never traded."""
    trades = await get_stock_trades(discord_user, ticker)
    if not trades:
        return None
    return {"ticker": ticker.upper(), **_aggregate_trades(trades)}


async def get_stock_positions_full(discord_user: str) -> list[dict]:
    """All tickers the user has touched, open OR closed. Each row carries
    realized_pnl; closed rows have shares=0 but still report their realized P/L."""
    trades = await get_stock_trades(discord_user)
    by_ticker: dict[str, list[dict]] = {}
    for t in trades:
        by_ticker.setdefault(t["ticker"], []).append(t)
    return [
        {"ticker": ticker, **_aggregate_trades(by_ticker[ticker])}
        for ticker in sorted(by_ticker)
    ]


async def get_all_stock_positions() -> dict[str, list[dict]]:
    """Bulk version of get_stock_positions_full for EVERY user, in one query.

    Reads all of stock_trades ordered by (discord_user, ticker, executed_at) and
    runs the same _aggregate_trades per (user, ticker). Returns
    {discord_user: [position dict, ...]} where each list is identical in shape to
    what get_stock_positions_full(uid) returns. Users with no trades are absent."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT discord_user, trade_id, ticker, side, shares, price, executed_at, notes "
            "FROM stock_trades "
            "ORDER BY discord_user ASC, ticker ASC, executed_at ASC, trade_id ASC"
        )
        rows = await cur.fetchall()
    by_user: dict[str, dict[str, list[dict]]] = {}
    for r in rows:
        d = dict(r)
        by_user.setdefault(d["discord_user"], {}).setdefault(d["ticker"], []).append(d)
    out: dict[str, list[dict]] = {}
    for uid, by_ticker in by_user.items():
        out[uid] = [
            {"ticker": ticker, **_aggregate_trades(by_ticker[ticker])}
            for ticker in sorted(by_ticker)
        ]
    return out


async def get_users_with_trades() -> list[str]:
    """All discord_user IDs that have at least one stock trade recorded.
    Used by the /stock leaderboard."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT DISTINCT discord_user FROM stock_trades ORDER BY discord_user"
        )
        rows = await cur.fetchall()
        return [r[0] for r in rows]


async def get_all_achievement_users() -> list[str]:
    """Every user with an achievement-relevant footprint: casino play, a bet, a
    stock trade, or an option trade. Used to backfill achievements."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT discord_user FROM casino_history "
            "UNION SELECT discord_user FROM bets "
            "UNION SELECT discord_user FROM stock_trades "
            "UNION SELECT discord_user FROM option_trades"
        )
        rows = await cur.fetchall()
        return [r[0] for r in rows]


# ── Reaction roles (auto-role panels) ────────────────────────────────────────


async def add_reaction_role(
    message_id: str, emoji: str, role_id: str, guild_id: str, channel_id: str
) -> None:
    """Bind an emoji on a message to a role (idempotent on message_id+emoji)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO reaction_roles (message_id, emoji, role_id, guild_id, channel_id) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(message_id, emoji) DO UPDATE SET role_id=excluded.role_id",
            (message_id, emoji, role_id, guild_id, channel_id),
        )
        await db.commit()


async def remove_reaction_role(message_id: str, emoji: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM reaction_roles WHERE message_id = ? AND emoji = ?",
            (message_id, emoji),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_reaction_role(message_id: str, emoji: str) -> str | None:
    """Role id bound to (message, emoji), or None. Hot path for reaction events."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT role_id FROM reaction_roles WHERE message_id = ? AND emoji = ?",
            (message_id, emoji),
        )
        row = await cur.fetchone()
        return row[0] if row else None


async def get_reaction_role_message_ids() -> set[str]:
    """All message ids that have reaction-role bindings (quick membership test)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT DISTINCT message_id FROM reaction_roles")
        return {r[0] for r in await cur.fetchall()}


async def get_reaction_roles_for_message(message_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT emoji, role_id, channel_id FROM reaction_roles WHERE message_id = ?",
            (message_id,),
        )
        return [dict(r) for r in await cur.fetchall()]


# ── Stock cash positions ────────────────────────────────────────────────────
# A per-user cash balance. Buys debit it and sells credit it (see the /stock
# buy|sell and /option handlers). Trade-driven debits are FLOORED AT 0, not
# overdrafted: most users never /stock cash deposit, so a buy bigger than the
# balance is assumed to be funded by money they already had — cash only becomes
# meaningful once selling generates proceeds. /stock cash lets users
# set/deposit/withdraw manually. An account's value = positions (stocks + options) + cash.


async def get_stock_cash(discord_user: str) -> float:
    """Return the user's cash balance (0.0 if they've never set one)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT balance FROM stock_cash WHERE discord_user = ?", (discord_user,)
        )
        row = await cur.fetchone()
        return float(row[0]) if row else 0.0


async def get_all_stock_cash() -> dict[str, float]:
    """Bulk version of get_stock_cash for every user, in one query. Returns
    {discord_user: balance}. Users with no row are absent — default to 0.0 at
    the call site (matching get_stock_cash's per-user default)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT discord_user, balance FROM stock_cash")
        rows = await cur.fetchall()
        return {r[0]: float(r[1]) for r in rows}


async def set_stock_cash(discord_user: str, amount: float) -> float:
    """Set the cash balance to an absolute amount (floored at 0). Returns it."""
    amount = max(amount, 0.0)
    ts = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO stock_cash (discord_user, balance, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(discord_user) DO UPDATE SET "
            "balance = excluded.balance, updated_at = excluded.updated_at",
            (discord_user, amount, ts),
        )
        await db.commit()
    return amount


async def adjust_stock_cash(discord_user: str, delta: float,
                            allow_negative: bool = False) -> float:
    """Add delta (may be negative) to the cash balance. Floored at 0 by default;
    pass allow_negative=True for trade-driven moves where the balance may go into
    the red (a buy bigger than available cash). Returns the new balance."""
    ts = datetime.now(timezone.utc).isoformat()
    if allow_negative:
        insert_val, update_expr = "?", "balance + ?"
    else:
        insert_val, update_expr = "MAX(?, 0)", "MAX(balance + ?, 0)"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"INSERT INTO stock_cash (discord_user, balance, updated_at) "
            f"VALUES (?, {insert_val}, ?) "
            f"ON CONFLICT(discord_user) DO UPDATE SET "
            f"balance = {update_expr}, updated_at = ?",
            (discord_user, delta, ts, delta, ts),
        )
        await db.commit()
        cur = await db.execute(
            "SELECT balance FROM stock_cash WHERE discord_user = ?", (discord_user,)
        )
        row = await cur.fetchone()
        return float(row[0]) if row else 0.0


# ── Manual realized-gain adjustments ─────────────────────────────────────────


async def add_realized_adjustment(discord_user: str, amount: float,
                                  note: str | None = None) -> None:
    """Record a manually-injected realized gain/loss (added to the realized total
    at display time). The matching cash credit is applied by the caller."""
    ts = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO realized_adjustments (discord_user, amount, note, created_at) "
            "VALUES (?, ?, ?, ?)",
            (discord_user, amount, note, ts),
        )
        await db.commit()


async def get_realized_adjustment_total(discord_user: str) -> float:
    """Sum of a user's manual realized-gain adjustments (0.0 if none)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM realized_adjustments WHERE discord_user = ?",
            (discord_user,),
        )
        row = await cur.fetchone()
        return float(row[0]) if row else 0.0


async def get_all_realized_adjustments() -> dict[str, float]:
    """Per-user manual realized-gain totals, in one query. Users with none are
    absent — default to 0.0 at the call site."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT discord_user, COALESCE(SUM(amount), 0) FROM realized_adjustments "
            "GROUP BY discord_user"
        )
        rows = await cur.fetchall()
        return {r[0]: float(r[1]) for r in rows}


async def get_day_realized_pnl(discord_user: str, since_iso: str) -> float:
    """Realized P/L booked on/after since_iso (UTC ISO): stock + option closes
    plus manual adjustments. Computed as realized(all) − realized(before since_iso)
    per ticker/spec, so each close is valued at the average cost basis it actually
    closed against. since_iso must use the same '+00:00' UTC format as executed_at
    for the lexical comparison to be correct."""
    total = 0.0
    prev_closes = {t: q.get("prev_close") for t, q in (await get_all_ticker_quotes()).items()}

    by_ticker: dict[str, list[dict]] = {}
    for t in await get_stock_trades(discord_user):
        by_ticker.setdefault(t["ticker"], []).append(t)
    for tk, ts in by_ticker.items():
        before = [t for t in ts if t["executed_at"] < since_iso]
        today = [t for t in ts if t["executed_at"] >= since_iso]
        prev = prev_closes.get(tk.upper())
        if prev:
            # Value today's closes from yesterday's close, not from cost basis: a sale
            # should book only TODAY's move — the rest of the P/L was already counted
            # on the days it happened. Collapse the start-of-day position into one lot
            # priced at prev_close, then replay today's trades on top.
            start = _aggregate_trades(before)
            synth = [] if start["closed"] else [{
                "side": "buy" if start["shares"] > 0 else "sell",
                "shares": abs(start["shares"]), "price": prev, "executed_at": since_iso,
            }]
            total += _aggregate_trades(synth + today)["realized_pnl"]
        else:
            # ponytail: no warm prev_close for this ticker → fall back to cost-basis
            # realized (the old behavior). Rare; add a prev_close source if it bites.
            total += _aggregate_trades(ts)["realized_pnl"] - _aggregate_trades(before)["realized_pnl"]

    by_spec: dict[tuple, list[dict]] = {}
    for t in await get_option_trades(discord_user):
        key = (t["underlying"], t["opt_type"], t["strike"], t["expiry"])
        by_spec.setdefault(key, []).append(t)
    for ts in by_spec.values():
        before = [t for t in ts if t["executed_at"] < since_iso]
        total += (_aggregate_option_trades(ts)["realized_pnl"]
                  - _aggregate_option_trades(before)["realized_pnl"])

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM realized_adjustments "
            "WHERE discord_user = ? AND created_at >= ?",
            (discord_user, since_iso),
        )
        row = await cur.fetchone()
        total += float(row[0]) if row else 0.0

    return total


# ── Portfolio snapshots (equity curve for /stock graph) ──────────────────────


async def insert_portfolio_snapshot(
    discord_user: str,
    account_value: float,
    stock_value: float,
    options_value: float,
    cash: float,
    captured_at: str | None = None,
    kind: str = "live",
) -> None:
    """Record one point on a user's equity curve. captured_at defaults to now."""
    ts = captured_at or datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO portfolio_snapshots "
            "(discord_user, captured_at, account_value, stock_value, options_value, cash, kind) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (discord_user, ts, account_value, stock_value, options_value, cash, kind),
        )
        await db.commit()


async def insert_portfolio_snapshots_bulk(rows: list[dict]) -> int:
    """Bulk-insert snapshot rows (used by backfill). Each dict needs
    discord_user, captured_at, account_value, stock_value, options_value, cash, kind.
    Returns the number inserted."""
    if not rows:
        return 0
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            "INSERT INTO portfolio_snapshots "
            "(discord_user, captured_at, account_value, stock_value, options_value, cash, kind) "
            "VALUES (:discord_user, :captured_at, :account_value, :stock_value, "
            ":options_value, :cash, :kind)",
            rows,
        )
        await db.commit()
    return len(rows)


async def get_portfolio_snapshots(
    discord_user: str, since_utc_iso: str | None = None
) -> list[dict]:
    """A user's equity-curve points, oldest first. Optionally only those at/after a cutoff."""
    sql = "SELECT captured_at, account_value, stock_value, options_value, cash, kind " \
          "FROM portfolio_snapshots WHERE discord_user = ?"
    params: list = [discord_user]
    if since_utc_iso:
        sql += " AND captured_at >= ?"
        params.append(since_utc_iso)
    sql += " ORDER BY captured_at ASC"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(sql, params)
        return [dict(r) for r in await cur.fetchall()]


async def get_all_portfolio_snapshots() -> list[dict]:
    """Every user's (discord_user, captured_at, account_value), oldest first.
    Used to build the server-wide equity curve."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT discord_user, captured_at, account_value "
            "FROM portfolio_snapshots ORDER BY captured_at ASC"
        )
        return [dict(r) for r in await cur.fetchall()]


async def get_ticker_meta_bulk(tickers: list[str]) -> dict[str, dict]:
    """Cached metadata (quote_type, sector, category, beta, name, updated_at) for
    the given tickers. Missing tickers are simply absent from the result."""
    if not tickers:
        return {}
    placeholders = ",".join("?" for _ in tickers)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT * FROM ticker_meta WHERE ticker IN ({placeholders})",
            [t.upper() for t in tickers],
        )
        return {r["ticker"]: dict(r) for r in await cur.fetchall()}


async def upsert_ticker_meta(
    ticker: str, quote_type: str | None, sector: str | None,
    category: str | None, beta: float | None, name: str | None,
) -> None:
    """Insert or refresh one ticker's cached metadata, stamping updated_at."""
    ts = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO ticker_meta (ticker, quote_type, sector, category, beta, name, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(ticker) DO UPDATE SET "
            "quote_type=excluded.quote_type, sector=excluded.sector, category=excluded.category, "
            "beta=excluded.beta, name=excluded.name, updated_at=excluded.updated_at",
            (ticker.upper(), quote_type, sector, category, beta, name, ts),
        )
        await db.commit()


async def get_all_ticker_quotes() -> dict[str, dict]:
    """Warm quote store: {ticker: {price, prev_close, currency, extended, updated_at}}.
    `extended` is decoded back to a dict (or None). Read by the HQ stock pages so a
    page load never blocks on yfinance — a background loop keeps this fresh."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM ticker_quotes")
        out = {}
        for r in await cur.fetchall():
            q = dict(r)
            q["extended"] = json.loads(q["extended"]) if q.get("extended") else None
            out[r["ticker"]] = q
        return out


async def upsert_ticker_quotes_bulk(quotes: dict[str, dict]) -> int:
    """Insert/refresh many tickers' live quotes (from fetch_quotes), stamping updated_at.
    `quotes` is {ticker: {price, prev_close, currency, extended?}}. Returns rows written."""
    if not quotes:
        return 0
    ts = datetime.now(timezone.utc).isoformat()
    rows = [
        (t.upper(), q.get("price"), q.get("prev_close"), q.get("currency"),
         json.dumps(q["extended"]) if q.get("extended") else None, ts)
        for t, q in quotes.items()
    ]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            "INSERT INTO ticker_quotes (ticker, price, prev_close, currency, extended, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(ticker) DO UPDATE SET price=excluded.price, prev_close=excluded.prev_close, "
            "currency=excluded.currency, extended=excluded.extended, updated_at=excluded.updated_at",
            rows,
        )
        await db.commit()
    return len(rows)


async def get_latest_snapshot_at(discord_user: str) -> str | None:
    """captured_at of the user's most recent snapshot, or None. For cadence dedup."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT MAX(captured_at) FROM portfolio_snapshots WHERE discord_user = ?",
            (discord_user,),
        )
        row = await cur.fetchone()
        return row[0] if row and row[0] else None


async def get_snapshot_value_asof(discord_user: str, cutoff_utc_iso: str) -> float | None:
    """Account value from the latest snapshot at/before a cutoff (the leaderboard's
    period baseline). None if the user has no snapshot that far back."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT account_value FROM portfolio_snapshots "
            "WHERE discord_user = ? AND captured_at <= ? "
            "ORDER BY captured_at DESC LIMIT 1",
            (discord_user, cutoff_utc_iso),
        )
        row = await cur.fetchone()
        return float(row[0]) if row else None


async def get_all_snapshot_values_asof(cutoff_utc_iso: str) -> dict[str, float]:
    """Account value per user from their latest snapshot at/before a cutoff.
    Returns {discord_user: account_value}; missing = no snapshot that far back."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT discord_user, account_value FROM portfolio_snapshots "
            "WHERE captured_at <= ? "
            "GROUP BY discord_user HAVING captured_at = MAX(captured_at)",
            (cutoff_utc_iso,),
        )
        rows = await cur.fetchall()
        return {r[0]: float(r[1]) for r in rows}


async def get_all_portfolio_users() -> list[str]:
    """Every user that has any portfolio footprint: a stock trade, an option
    trade, or a cash balance. Used by the hourly snapshot loop."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT discord_user FROM stock_trades "
            "UNION SELECT discord_user FROM option_trades "
            "UNION SELECT discord_user FROM stock_cash WHERE balance > 0"
        )
        rows = await cur.fetchall()
        return [r[0] for r in rows]


async def has_backfill_snapshots(discord_user: str) -> bool:
    """True if this user already has reconstructed (backfill) snapshots."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM portfolio_snapshots WHERE discord_user = ? AND kind = 'backfill' LIMIT 1",
            (discord_user,),
        )
        return await cur.fetchone() is not None


# ── Option trades + derived positions ───────────────────────────────────────
# option_trades is the source of truth. Positions are keyed by the full
# contract spec (underlying, opt_type, strike, expiry) and aggregated with
# SIGNED average-cost accounting so a single helper handles both long and
# short (written) positions:
#   net > 0  -> net long   (you're holding contracts you bought)
#   net < 0  -> net short  (you wrote/sold contracts to open)
# Premiums are per-share; every dollar figure is scaled by OPTION_MULTIPLIER
# because one contract controls 100 shares.

OPTION_MULTIPLIER = 100


def _aggregate_option_trades(trades: list[dict]) -> dict:
    """Reduce chronologically-ordered trades for ONE contract spec.

    Returns {net_contracts, avg_premium, realized_pnl, invested, closed}.
    `avg_premium` is the average open premium of the current position (always >
    0 while open). Realized P/L accrues on any trade that reduces the position —
    including a flip (e.g. long 2, sell 5 -> close 2 long then open 3 short):
        sign(net) * (close_premium - avg_premium) * closed_qty * 100
    which is (premium - avg) for closing a long and (avg - premium) for a short.
    `invested` is lifetime capital deployed (premium ×100 on every contract that
    OPENS or adds to a position, long or short) — the denominator for % return.
    """
    net = 0          # signed contracts
    avg = 0.0        # avg open premium of the current position
    realized = 0.0
    invested = 0.0
    for t in trades:
        qty = int(t["contracts"])
        px = float(t["premium"])
        d = qty if t["side"] == "buy" else -qty
        if net == 0:
            net, avg = d, px
            invested += qty * px * OPTION_MULTIPLIER  # opens a fresh position
            continue
        if (net > 0) == (d > 0):
            # Same side: blend into the running average open premium.
            total = abs(net) + qty
            avg = (avg * abs(net) + px * qty) / total
            net += d
            invested += qty * px * OPTION_MULTIPLIER  # adds to the position
            continue
        # Opposing side: close up to |net|, then flip any remainder.
        close_qty = min(qty, abs(net))
        realized += (1 if net > 0 else -1) * (px - avg) * close_qty * OPTION_MULTIPLIER
        net += d
        remaining = qty - close_qty
        if remaining > 0:
            avg = px           # flipped: new lot opens at this premium
            invested += remaining * px * OPTION_MULTIPLIER  # capital for the flipped-into side
        elif net == 0:
            avg = 0.0
    closed = net == 0
    return {
        "net_contracts": 0 if closed else net,
        "avg_premium": 0.0 if closed else avg,
        "realized_pnl": realized,
        "invested": invested,
        "closed": closed,
    }


async def add_option_trade(
    discord_user: str,
    underlying: str,
    opt_type: str,
    strike: float,
    expiry: str,
    side: str,
    contracts: int,
    premium: float,
    executed_at: str | None = None,
    notes: str | None = None,
) -> int:
    if opt_type not in ("call", "put"):
        raise ValueError(f"opt_type must be 'call' or 'put', got {opt_type!r}")
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
    if contracts <= 0 or premium <= 0 or strike <= 0:
        raise ValueError("contracts, premium, and strike must be > 0")
    ts = executed_at or datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO option_trades "
            "(discord_user, underlying, opt_type, strike, expiry, side, "
            "contracts, premium, executed_at, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (discord_user, underlying.upper(), opt_type, strike, expiry, side,
             contracts, premium, ts, notes),
        )
        await db.commit()
        return cur.lastrowid


async def get_option_trades(
    discord_user: str, underlying: str | None = None, limit: int | None = None
) -> list[dict]:
    sql = (
        "SELECT trade_id, underlying, opt_type, strike, expiry, side, "
        "contracts, premium, executed_at, notes "
        "FROM option_trades WHERE discord_user = ?"
    )
    params: list = [discord_user]
    if underlying:
        sql += " AND underlying = ?"
        params.append(underlying.upper())
    sql += " ORDER BY executed_at ASC, trade_id ASC"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(sql, params)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def delete_option_trade(discord_user: str, trade_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM option_trades WHERE discord_user = ? AND trade_id = ?",
            (discord_user, trade_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_option_positions_full(discord_user: str) -> list[dict]:
    """All option contracts the user has touched, open OR closed. Each row
    carries the contract spec plus net_contracts / avg_premium / realized_pnl /
    closed. Closed rows keep their realized P/L for the lifetime total."""
    trades = await get_option_trades(discord_user)
    by_spec: dict[tuple, list[dict]] = {}
    for t in trades:
        key = (t["underlying"], t["opt_type"], t["strike"], t["expiry"])
        by_spec.setdefault(key, []).append(t)
    out = []
    for key in sorted(by_spec):
        underlying, opt_type, strike, expiry = key
        agg = _aggregate_option_trades(by_spec[key])
        out.append({
            "underlying": underlying,
            "opt_type": opt_type,
            "strike": strike,
            "expiry": expiry,
            **agg,
        })
    return out


async def get_all_option_positions() -> dict[str, list[dict]]:
    """Bulk version of get_option_positions_full for EVERY user, in one query.

    Reads all of option_trades ordered by (discord_user, contract spec,
    executed_at) and runs the same _aggregate_option_trades per (user, spec).
    Returns {discord_user: [position dict, ...]} identical in shape to what
    get_option_positions_full(uid) returns. Users with no trades are absent."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT discord_user, trade_id, underlying, opt_type, strike, expiry, "
            "side, contracts, premium, executed_at, notes "
            "FROM option_trades "
            "ORDER BY discord_user ASC, underlying ASC, opt_type ASC, strike ASC, "
            "expiry ASC, executed_at ASC, trade_id ASC"
        )
        rows = await cur.fetchall()
    by_user: dict[str, dict[tuple, list[dict]]] = {}
    for r in rows:
        d = dict(r)
        key = (d["underlying"], d["opt_type"], d["strike"], d["expiry"])
        by_user.setdefault(d["discord_user"], {}).setdefault(key, []).append(d)
    out: dict[str, list[dict]] = {}
    for uid, by_spec in by_user.items():
        positions = []
        for key in sorted(by_spec):
            underlying, opt_type, strike, expiry = key
            agg = _aggregate_option_trades(by_spec[key])
            positions.append({
                "underlying": underlying,
                "opt_type": opt_type,
                "strike": strike,
                "expiry": expiry,
                **agg,
            })
        out[uid] = positions
    return out


async def get_users_with_option_trades() -> list[str]:
    """All discord_user IDs with at least one option trade. Used by the
    /stock leaderboard to union option traders with stock traders."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT DISTINCT discord_user FROM option_trades ORDER BY discord_user"
        )
        rows = await cur.fetchall()
        return [r[0] for r in rows]


# ── QBReader answer cache (party-game science pool) ──────────────────────────


async def upsert_qb_answers(
    category: str, items: list[tuple[str, list[str]]]
) -> int:
    """Persist (answer, aliases) pairs for a QBReader category. Idempotent:
    re-seeing an answer refreshes its aliases but keeps the original created_at.
    Returns the number of rows written."""
    if not items:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    rows = [(ans, category, json.dumps(aliases), now) for ans, aliases in items]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            "INSERT INTO qb_answers (answer, category, aliases, created_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(answer) DO UPDATE SET aliases=excluded.aliases",
            rows,
        )
        await db.commit()
    return len(rows)


async def get_qb_answers(category: str) -> list[tuple[str, list[str]]]:
    """All accumulated (answer, aliases) for a QBReader category."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT answer, aliases FROM qb_answers WHERE category = ?", (category,)
        )
        rows = await cur.fetchall()
    return [(r[0], json.loads(r[1]) if r[1] else []) for r in rows]


# ── Stock monitors & bot settings ────────────────────────────────────────────


async def add_stock_monitor(
    discord_user: str, channel_id: str, ticker: str,
    direction: str, target_price: float,
) -> int:
    """Create a price monitor. Returns the new monitor_id."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO stock_monitors
               (discord_user, channel_id, ticker, direction, target_price, created_at, active)
               VALUES (?, ?, ?, ?, ?, ?, 1)""",
            (discord_user, channel_id, ticker, direction, target_price, now),
        )
        await db.commit()
        return cur.lastrowid


async def get_active_stock_monitors() -> list[dict]:
    """All armed monitors (active=1), for the polling loop."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM stock_monitors WHERE active = 1 ORDER BY monitor_id",
        )
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_stock_monitors_for_user(discord_user: str) -> list[dict]:
    """A user's active monitors."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM stock_monitors WHERE discord_user = ? AND active = 1 "
            "ORDER BY monitor_id",
            (discord_user,),
        )
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def deactivate_stock_monitor(monitor_id: int) -> None:
    """Disarm a monitor after it fires."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE stock_monitors SET active = 0 WHERE monitor_id = ?", (monitor_id,),
        )
        await db.commit()


async def delete_stock_monitor(monitor_id: int, discord_user: str) -> bool:
    """Remove a monitor the user owns. Returns True if one was deleted."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM stock_monitors WHERE monitor_id = ? AND discord_user = ?",
            (monitor_id, discord_user),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_bot_setting(key: str) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT value FROM bot_settings WHERE key = ?", (key,))
        row = await cur.fetchone()
    return row[0] if row else None


async def set_bot_setting(key: str, value: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO bot_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await db.commit()


async def get_all_stock_holdings() -> list[dict]:
    """Every user's CURRENT open holdings across the server (for the swing monitor),
    computed from the trade log — the authoritative source. (The old stock_holdings
    table is no longer maintained, so reading it missed every recent position.)"""
    out: list[dict] = []
    for uid, positions in (await get_all_stock_positions()).items():
        for p in positions:
            if not p.get("closed") and abs(p.get("shares") or 0) > 1e-9:
                out.append({"discord_user": uid, "ticker": p["ticker"], "shares": p["shares"]})
    return out


# ── Daily NBA/MLB pick'em ─────────────────────────────────────────────────────


async def add_pickem_game(
    message_id: str, game_id: str, sport: str, home_team: str,
    away_team: str, start_time: str, posted_date: str,
    home_prob: float | None = None, away_prob: float | None = None,
    odds_source: str | None = None,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR IGNORE INTO pickem_games
               (message_id, game_id, sport, home_team, away_team, start_time,
                posted_date, home_prob, away_prob, odds_source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (message_id, game_id, sport, home_team, away_team, start_time,
             posted_date, home_prob, away_prob, odds_source),
        )
        await db.commit()


async def set_pickem_game_probs(
    message_id: str, home_prob: float, away_prob: float, odds_source: str,
) -> None:
    """Backfill/update the win probabilities on an existing pick'em game."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE pickem_games SET home_prob = ?, away_prob = ?, odds_source = ? "
            "WHERE message_id = ?",
            (home_prob, away_prob, odds_source, message_id),
        )
        await db.commit()


async def get_pickem_game(message_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM pickem_games WHERE message_id = ?", (message_id,),
        )
        row = await cur.fetchone()
    return dict(row) if row else None


async def pickem_game_exists(game_id: str, posted_date: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM pickem_games WHERE game_id = ? AND posted_date = ?",
            (game_id, posted_date),
        )
        return await cur.fetchone() is not None


async def get_unlocked_pickem_games() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM pickem_games WHERE locked = 0")
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def lock_pickem_game(message_id: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE pickem_games SET locked = 1 WHERE message_id = ?", (message_id,),
        )
        await db.commit()


async def get_unresolved_pickem_games() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM pickem_games WHERE resolved = 0")
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def resolve_pickem_game(message_id: str, winner: str) -> None:
    """Mark a game resolved and score every pick on it (correct = pick == winner)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE pickem_games SET resolved = 1, locked = 1, winner = ? WHERE message_id = ?",
            (winner, message_id),
        )
        await db.execute(
            "UPDATE pickem_picks SET correct = CASE WHEN pick = ? THEN 1 ELSE 0 END "
            "WHERE message_id = ?",
            (winner, message_id),
        )
        await db.commit()


async def record_pickem_pick(
    message_id: str, discord_user: str, pick: str, stake: int = 1,
) -> bool:
    """Place a bet. Bets are FINAL — returns True if this locked it in, False if
    the user had already bet on this game (no overwrite)."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT OR IGNORE INTO pickem_picks
               (message_id, discord_user, pick, stake, picked_at)
               VALUES (?, ?, ?, ?, ?)""",
            (message_id, discord_user, pick, stake, now),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_pickem_pick_full(message_id: str, discord_user: str) -> dict | None:
    """The user's locked-in pick + stake for a game, or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT pick, stake FROM pickem_picks WHERE message_id = ? AND discord_user = ?",
            (message_id, discord_user),
        )
        row = await cur.fetchone()
    return dict(row) if row else None


async def remove_pickem_pick(message_id: str, discord_user: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM pickem_picks WHERE message_id = ? AND discord_user = ?",
            (message_id, discord_user),
        )
        await db.commit()


async def get_pickem_pick(message_id: str, discord_user: str) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT pick FROM pickem_picks WHERE message_id = ? AND discord_user = ?",
            (message_id, discord_user),
        )
        row = await cur.fetchone()
    return row[0] if row else None


async def get_pickem_vote_counts(message_id: str) -> dict[str, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT pick, COUNT(*) FROM pickem_picks WHERE message_id = ? GROUP BY pick",
            (message_id,),
        )
        rows = await cur.fetchall()
    out = {"home": 0, "away": 0}
    for pick, n in rows:
        out[pick] = n
    return out


async def get_pickem_games_for_date(posted_date: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM pickem_games WHERE posted_date = ? ORDER BY start_time",
            (posted_date,),
        )
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_pickem_picks_for_message(message_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT discord_user, pick, stake, correct FROM pickem_picks WHERE message_id = ?",
            (message_id,),
        )
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_pickem_picks_for_date(posted_date: str) -> list[dict]:
    """Every pick (all users) for a given pick'em day, joined with its game's
    probs / resolution. Drives the `/pickem today` card and its mini-board."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT p.message_id, p.discord_user, p.pick, p.stake, p.correct,
                      g.home_prob, g.away_prob, g.resolved, g.winner
               FROM pickem_picks p JOIN pickem_games g ON p.message_id = g.message_id
               WHERE g.posted_date = ?""",
            (posted_date,),
        )
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_pickem_resolved_picks() -> list[dict]:
    """Every scored pick joined with its game's start_time, ordered for streak calc."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT p.discord_user, p.correct, p.pick, p.stake, g.start_time,
                      g.home_prob, g.away_prob
               FROM pickem_picks p JOIN pickem_games g ON p.message_id = g.message_id
               WHERE p.correct IS NOT NULL
               ORDER BY p.discord_user, g.start_time""",
        )
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Sports trading cards (see docs/superpowers/specs/2026-08-11-sports-card-packs-design.md)
# ---------------------------------------------------------------------------
QUICK_SELL_FRACTION = 0.75  # quick-sell returns this share of book value in coins


async def card_set_exists(sport: str, season: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM card_sets WHERE sport = ? AND season = ?", (sport, season)
        )
        return await cur.fetchone() is not None


async def create_card_set(
    sport: str, season: int, name: str, total_packs: int, base_cost: int, created_at: str
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO card_sets (sport, season, name, total_packs, base_cost, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sport, season, name, total_packs, base_cost, created_at),
        )
        await db.commit()
        return cur.lastrowid


async def insert_card_designs(set_id: int, designs: list[dict]) -> int:
    """Bulk-insert designs for a set. Each design dict: subject_key, subject_name, team,
    rarity, is_rookie, career_fame, total_copies, stats(dict), headshot_url, book_value."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            "INSERT OR IGNORE INTO card_designs "
            "(set_id, subject_key, subject_name, team, rarity, is_rookie, career_fame, "
            " total_copies, stats, headshot_url, book_value) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    set_id,
                    d["subject_key"],
                    d["subject_name"],
                    d.get("team"),
                    d["rarity"],
                    1 if d.get("is_rookie") else 0,
                    d.get("career_fame"),
                    d["total_copies"],
                    json.dumps(d.get("stats") or {}),
                    d.get("headshot_url"),
                    d["book_value"],
                )
                for d in designs
            ],
        )
        await db.commit()
        return len(designs)


def _set_row(r: aiosqlite.Row) -> dict:
    return {
        "set_id": r["set_id"],
        "sport": r["sport"],
        "season": r["season"],
        "name": r["name"],
        "total_packs": r["total_packs"],
        "packs_opened": r["packs_opened"],
        "pack_cost": r["base_cost"],
        "closed": bool(r["closed"]),
    }


async def list_card_sets(include_closed: bool = True) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        sql = "SELECT * FROM card_sets"
        if not include_closed:
            sql += " WHERE closed = 0"
        sql += " ORDER BY sport, season DESC"
        cur = await db.execute(sql)
        return [_set_row(r) for r in await cur.fetchall()]


async def get_card_set(sport: str, season: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM card_sets WHERE sport = ? AND season = ?", (sport, season)
        )
        r = await cur.fetchone()
        return _set_row(r) if r else None


async def get_card_set_by_id(set_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM card_sets WHERE set_id = ?", (set_id,))
        r = await cur.fetchone()
        return _set_row(r) if r else None


def _design_public(r: aiosqlite.Row) -> dict:
    return {
        "design_id": r["design_id"],
        "name": r["subject_name"],
        "team": r["team"],
        "rarity": r["rarity"],
        "is_rookie": bool(r["is_rookie"]),
        "total_copies": r["total_copies"],
        "minted": r["minted"],
        "headshot_url": r["headshot_url"],
        "stats": json.loads(r["stats"]) if r["stats"] else {},
        "book_value": r["book_value"],
    }


async def mint_pack(user: str, set_id: int, pack_size: int, source: str, now_iso: str) -> list[dict]:
    """Open one pack: real coin debit (packs are a true sink — no 1000-coin faucet floor here,
    else free packs + quick-sell would print coins), weighted draw from the finite pool, mint
    serial-numbered instances. Atomic in one connection. Raises ValueError on any refusal."""
    from shared import cards as engine

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN IMMEDIATE")
        try:
            srow = (
                await (await db.execute("SELECT * FROM card_sets WHERE set_id = ?", (set_id,))).fetchone()
            )
            if srow is None:
                raise ValueError("no such set")
            if srow["closed"] or srow["packs_opened"] >= srow["total_packs"]:
                raise ValueError("this set is sold out")
            cost = 0 if source == "daily" else srow["base_cost"]
            if cost > 0:
                bal = (
                    await (await db.execute(
                        "SELECT balance FROM casino_wallets WHERE discord_user = ?", (user,)
                    )).fetchone()
                )
                have = bal["balance"] if bal else 0
                if have < cost:
                    raise ValueError(f"need {cost} coins — you have {have}")
                await db.execute(
                    "INSERT INTO casino_wallets (discord_user, balance) VALUES (?, ?) "
                    "ON CONFLICT(discord_user) DO UPDATE SET balance = balance - ?",
                    (user, CASINO_STARTING_COINS - cost, cost),
                )
            drows = (
                await (await db.execute(
                    "SELECT * FROM card_designs WHERE set_id = ?", (set_id,)
                )).fetchall()
            )
            manifest = [{"rarity": d["rarity"]} for d in drows]
            pool = {
                i: (d["total_copies"] - d["minted"])
                for i, d in enumerate(drows)
                if d["total_copies"] > d["minted"]
            }
            drawn = engine.open_pack(pool, manifest, random.Random(), pack_size)
            if not drawn:
                raise ValueError("no cards left in this set")
            out = []
            for c in drawn:
                d = drows[c["design_index"]]
                serial = d["minted"] + 1
                await db.execute(
                    "UPDATE card_designs SET minted = minted + 1 WHERE design_id = ?",
                    (d["design_id"],),
                )
                # keep our local copy's minted in sync in case the same design is drawn twice
                drows[c["design_index"]] = {**dict(d), "minted": serial}
                cur = await db.execute(
                    "INSERT INTO card_instances "
                    "(design_id, owner_id, serial, is_holo, gem, book_value, acquired_cost, source, acquired_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        d["design_id"], user, serial, 1 if c["is_holo"] else 0, c["gem"],
                        c["book_value"], (cost / len(drawn)), source, now_iso,
                    ),
                )
                out.append({
                    "instance_id": cur.lastrowid,
                    "design_id": d["design_id"],
                    "name": d["subject_name"],
                    "team": d["team"],
                    "sport": srow["sport"],
                    "season": srow["season"],
                    "rarity": d["rarity"],
                    "is_rookie": bool(d["is_rookie"]),
                    "is_holo": c["is_holo"],
                    "gem": c["gem"],
                    "serial": serial,
                    "total_copies": d["total_copies"],
                    "book_value": c["book_value"],
                    "headshot_url": d["headshot_url"],
                    "stats": json.loads(d["stats"]) if d["stats"] else {},
                })
            await db.execute(
                "UPDATE card_sets SET packs_opened = packs_opened + 1, "
                "closed = CASE WHEN packs_opened + 1 >= total_packs THEN 1 ELSE closed END "
                "WHERE set_id = ?",
                (set_id,),
            )
            await db.commit()
            return out
        except Exception:
            await db.execute("ROLLBACK")
            raise


async def get_collection(user: str) -> tuple[list[dict], float]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT i.*, d.subject_name, d.team, d.rarity, d.headshot_url, d.stats, d.total_copies, "
            "       s.sport, s.season "
            "FROM card_instances i JOIN card_designs d ON i.design_id = d.design_id "
            "JOIN card_sets s ON d.set_id = s.set_id WHERE i.owner_id = ? "
            "ORDER BY i.book_value DESC",
            (user,),
        )
        rows = await cur.fetchall()
    cards_out = [
        {
            "instance_id": r["instance_id"],
            "design_id": r["design_id"],
            "name": r["subject_name"],
            "team": r["team"],
            "sport": r["sport"],
            "season": r["season"],
            "rarity": r["rarity"],
            "is_holo": bool(r["is_holo"]),
            "gem": r["gem"],
            "serial": r["serial"],
            "total_copies": r["total_copies"],
            "book_value": r["book_value"],
            "headshot_url": r["headshot_url"],
            "stats": json.loads(r["stats"]) if r["stats"] else {},
        }
        for r in rows
    ]
    return cards_out, round(sum(r["book_value"] for r in rows), 2)


async def sell_instance(user: str, instance_id: int) -> tuple[dict, int]:
    """Quick-sell one owned card for QUICK_SELL_FRACTION of book value in coins.
    Returns (sold_card_summary, coins_credited). Raises ValueError if not owned."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN IMMEDIATE")
        try:
            r = await (await db.execute(
                "SELECT i.*, d.subject_name, d.rarity FROM card_instances i "
                "JOIN card_designs d ON i.design_id = d.design_id "
                "WHERE i.instance_id = ? AND i.owner_id = ?",
                (instance_id, user),
            )).fetchone()
            if r is None:
                raise ValueError("you don't own that card")
            coins = max(1, round(r["book_value"] * QUICK_SELL_FRACTION))
            await db.execute("DELETE FROM card_instances WHERE instance_id = ?", (instance_id,))
            await db.execute(
                "INSERT INTO casino_wallets (discord_user, balance) VALUES (?, ?) "
                "ON CONFLICT(discord_user) DO UPDATE SET balance = balance + ?",
                (user, CASINO_STARTING_COINS + coins, coins),
            )
            await db.commit()
            return ({"name": r["subject_name"], "rarity": r["rarity"], "serial": r["serial"]}, coins)
        except Exception:
            await db.execute("ROLLBACK")
            raise


async def get_catalog(set_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        srow = await (await db.execute("SELECT * FROM card_sets WHERE set_id = ?", (set_id,))).fetchone()
        if srow is None:
            return None
        rows = await (await db.execute(
            "SELECT * FROM card_designs WHERE set_id = ? ORDER BY book_value DESC", (set_id,)
        )).fetchall()
    designs = [_design_public(r) for r in rows]
    return {"set": _set_row(srow), "designs": designs}


async def find_designs_by_name(name: str, limit: int = 25) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT d.*, s.sport, s.season FROM card_designs d JOIN card_sets s ON d.set_id = s.set_id "
            "WHERE d.subject_name LIKE ? ORDER BY d.book_value DESC LIMIT ?",
            (f"%{name}%", limit),
        )
        rows = await cur.fetchall()
    return [{**_design_public(r), "sport": r["sport"], "season": r["season"]} for r in rows]


async def get_design_owners(design_id: int) -> tuple[dict | None, list[dict]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        d = await (await db.execute(
            "SELECT d.*, s.sport, s.season FROM card_designs d JOIN card_sets s ON d.set_id = s.set_id "
            "WHERE d.design_id = ?", (design_id,)
        )).fetchone()
        if d is None:
            return None, []
        rows = await (await db.execute(
            "SELECT owner_id, serial, is_holo, gem FROM card_instances WHERE design_id = ? ORDER BY serial",
            (design_id,),
        )).fetchall()
    owners = [
        {"owner_id": r["owner_id"], "serial": r["serial"], "is_holo": bool(r["is_holo"]), "gem": r["gem"]}
        for r in rows
    ]
    return {**_design_public(d), "sport": d["sport"], "season": d["season"]}, owners


# --- daily free pack ---
async def has_claimed_daily_pack(user: str, day: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM card_pack_claims WHERE discord_user = ? AND day = ?", (user, day)
        )
        return await cur.fetchone() is not None


async def record_daily_pack_claim(user: str, day: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO card_pack_claims (discord_user, day) VALUES (?, ?)", (user, day)
        )
        await db.commit()


DAILY_MESSAGE_REWARD = 500  # (legacy) coins for your first chat message each day (UTC)

# Activity coin rewards: (coins per event, daily cap per source). Ascending by effort —
# chatting is a trickle, logging real bets/trades pays more, all capped so nothing is farmable.
ACTIVITY_REWARDS = {
    "message": (5, 500),      # small trickle, capped
    "pickem_pick": (25, 250),  # daily pick'em
    "bet_log": (50, 1000),     # logging a real sports bet
    "trade_log": (50, 1000),   # logging a stock/option trade
    "pokemon_guess": (15, 300),  # web arcade: correct "Who's That Pokémon?" guess
    "wordle_win": (25, 200),     # web arcade: solved Wordle
    "geo_guess": (15, 200),      # web arcade: correct Geography guess
    "nba_guess": (15, 200),      # web arcade: correct NBA player guess
    "pokedle_win": (25, 200),    # web arcade: solved Pokédle
    "mastermind_win": (30, 200),  # web arcade: cracked the Mastermind code
    # (paper trades already stake coins — no reward, else you could farm the loop)
}


async def grant_activity_reward(discord_user: str, source: str, day: str) -> int:
    """Credit the per-event coin reward for `source`, up to that source's daily cap. Returns the
    coins actually granted (0 once capped). Atomic in one connection — the cap read + write + wallet
    credit can't race."""
    amount, cap = ACTIVITY_REWARDS.get(source, (0, 0))
    if amount <= 0:
        return 0
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN IMMEDIATE")
        try:
            row = await (await db.execute(
                "SELECT earned FROM daily_coin_earn WHERE discord_user=? AND day=? AND source=?",
                (discord_user, day, source),
            )).fetchone()
            earned = row["earned"] if row else 0
            grant = max(0, min(amount, cap - earned))
            if grant <= 0:
                await db.execute("ROLLBACK")
                return 0
            await db.execute(
                "INSERT INTO daily_coin_earn (discord_user, day, source, earned) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(discord_user, day, source) DO UPDATE SET earned = earned + ?",
                (discord_user, day, source, grant, grant),
            )
            await db.execute(
                "INSERT INTO casino_wallets (discord_user, balance) VALUES (?, ?) "
                "ON CONFLICT(discord_user) DO UPDATE SET balance = balance + ?",
                (discord_user, CASINO_STARTING_COINS + grant, grant),
            )
            await db.commit()
            return grant
        except Exception:
            await db.execute("ROLLBACK")
            raise


async def grant_daily_message_reward(discord_user: str, day: str) -> int:
    """Grant the once-a-day chat reward on the user's first message of the day. Returns the coins
    granted (0 if already claimed today). The INSERT OR IGNORE is the atomic per-day lock."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT OR IGNORE INTO daily_message_reward (discord_user, day) VALUES (?, ?)",
            (discord_user, day),
        )
        if cur.rowcount == 0:
            return 0  # already claimed today
        await db.execute(
            "INSERT INTO casino_wallets (discord_user, balance) VALUES (?, ?) "
            "ON CONFLICT(discord_user) DO UPDATE SET balance = balance + ?",
            (discord_user, CASINO_STARTING_COINS + DAILY_MESSAGE_REWARD, DAILY_MESSAGE_REWARD),
        )
        await db.commit()
        return DAILY_MESSAGE_REWARD


async def get_max_card_instance_id() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COALESCE(MAX(instance_id), 0) FROM card_instances")
        return (await cur.fetchone())[0]


async def get_card_instances_after(after_id: int, limit: int = 200) -> list[dict]:
    """New minted cards with instance_id > after_id, joined to design + set for the rare-pull
    watcher. Ordered oldest-first so the cursor advances monotonically."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT i.instance_id, i.owner_id, i.serial, i.is_holo, i.gem, i.book_value, "
            "d.subject_name, d.total_copies, d.headshot_url, d.rarity, d.is_rookie, s.sport, s.name AS set_name "
            "FROM card_instances i "
            "JOIN card_designs d ON i.design_id = d.design_id "
            "JOIN card_sets s ON d.set_id = s.set_id "
            "WHERE i.instance_id > ? ORDER BY i.instance_id ASC LIMIT ?",
            (after_id, limit),
        )
        rows = await cur.fetchall()
    return [
        {
            "instance_id": r["instance_id"], "owner_id": r["owner_id"], "serial": r["serial"],
            "is_holo": bool(r["is_holo"]), "gem": r["gem"], "book_value": r["book_value"],
            "name": r["subject_name"], "total_copies": r["total_copies"], "headshot_url": r["headshot_url"],
            "rarity": r["rarity"], "is_rookie": bool(r["is_rookie"]), "sport": r["sport"], "set_name": r["set_name"],
        }
        for r in rows
    ]


# --- wishlist ---
async def add_card_want(user: str, design_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO card_wants (discord_user, design_id) VALUES (?, ?)",
            (user, design_id),
        )
        await db.commit()


async def remove_card_want(user: str, design_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM card_wants WHERE discord_user = ? AND design_id = ?", (user, design_id)
        )
        await db.commit()


async def get_card_wanters(design_id: int) -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT discord_user FROM card_wants WHERE design_id = ?", (design_id,)
        )
        return [r["discord_user"] for r in await cur.fetchall()]


# --- trading (card-for-card) ---
async def create_card_trade(
    from_user: str, to_user: str, offer_ids: list[int], want_ids: list[int], now_iso: str
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO card_trades (from_user, to_user, offer_ids, want_ids, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (from_user, to_user, json.dumps(offer_ids), json.dumps(want_ids), now_iso),
        )
        await db.commit()
        return cur.lastrowid


async def get_card_trade(trade_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        r = await (await db.execute("SELECT * FROM card_trades WHERE trade_id = ?", (trade_id,))).fetchone()
        if r is None:
            return None
        return {
            "trade_id": r["trade_id"], "from_user": r["from_user"], "to_user": r["to_user"],
            "offer_ids": json.loads(r["offer_ids"]), "want_ids": json.loads(r["want_ids"]),
            "status": r["status"], "created_at": r["created_at"],
        }


async def list_incoming_card_trades(user: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM card_trades WHERE to_user = ? AND status = 'pending' ORDER BY trade_id DESC",
            (user,),
        )
        return [
            {
                "trade_id": r["trade_id"], "from_user": r["from_user"], "to_user": r["to_user"],
                "offer_ids": json.loads(r["offer_ids"]), "want_ids": json.loads(r["want_ids"]),
                "status": r["status"], "created_at": r["created_at"],
            }
            for r in await cur.fetchall()
        ]


async def set_card_trade_status(trade_id: int, status: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE card_trades SET status = ? WHERE trade_id = ?", (status, trade_id))
        await db.commit()


async def accept_card_trade(trade_id: int, accepting_user: str) -> dict:
    """Atomically swap ownership. Verifies the trade is pending, addressed to accepting_user, and
    that both sides still own exactly the instances they committed. Raises ValueError otherwise."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN IMMEDIATE")
        try:
            t = await (await db.execute("SELECT * FROM card_trades WHERE trade_id = ?", (trade_id,))).fetchone()
            if t is None:
                raise ValueError("no such trade")
            if t["status"] != "pending":
                raise ValueError("trade is no longer pending")
            if t["to_user"] != accepting_user:
                raise ValueError("this trade isn't addressed to you")
            offer_ids = json.loads(t["offer_ids"])
            want_ids = json.loads(t["want_ids"])
            from_user, to_user = t["from_user"], t["to_user"]

            async def _owned_by(ids: list[int], owner: str) -> bool:
                if not ids:
                    return True
                q = f"SELECT COUNT(*) c FROM card_instances WHERE owner_id = ? AND instance_id IN ({','.join('?' * len(ids))})"
                row = await (await db.execute(q, (owner, *ids))).fetchone()
                return row["c"] == len(ids)

            if not await _owned_by(offer_ids, from_user):
                raise ValueError("the offering player no longer owns those cards")
            if not await _owned_by(want_ids, to_user):
                raise ValueError("you no longer own the requested cards")
            for iid in offer_ids:
                await db.execute(
                    "UPDATE card_instances SET owner_id = ?, source = 'trade' WHERE instance_id = ?",
                    (to_user, iid),
                )
            for iid in want_ids:
                await db.execute(
                    "UPDATE card_instances SET owner_id = ?, source = 'trade' WHERE instance_id = ?",
                    (from_user, iid),
                )
            await db.execute("UPDATE card_trades SET status = 'accepted' WHERE trade_id = ?", (trade_id,))
            await db.commit()
            return {"offer_ids": offer_ids, "want_ids": want_ids, "from_user": from_user, "to_user": to_user}
        except Exception:
            await db.execute("ROLLBACK")
            raise


async def get_owned_instances(user: str, instance_ids: list[int]) -> list[dict]:
    """Fetch specific owned instances (for validating a proposed trade)."""
    if not instance_ids:
        return []
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        q = (
            "SELECT i.instance_id, i.serial, i.is_holo, i.gem, d.subject_name, d.rarity "
            "FROM card_instances i JOIN card_designs d ON i.design_id = d.design_id "
            f"WHERE i.owner_id = ? AND i.instance_id IN ({','.join('?' * len(instance_ids))})"
        )
        cur = await db.execute(q, (user, *instance_ids))
        return [dict(r) for r in await cur.fetchall()]


async def get_card_stats(uid: str) -> dict:
    """Card-collection counters for the achievement engine."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        r = await (await db.execute(
            "SELECT COUNT(*) total, COUNT(DISTINCT d.set_id) sets, "
            "MAX(d.rarity = 'legendary') has_leg, MAX(i.is_holo) has_holo, "
            "MAX(i.gem IS NOT NULL) has_gem, MAX(d.total_copies = 1) has_1of1 "
            "FROM card_instances i JOIN card_designs d ON i.design_id = d.design_id "
            "WHERE i.owner_id = ?",
            (uid,),
        )).fetchone()
    return {
        "cards_total": r["total"] or 0,
        "cards_sets": r["sets"] or 0,
        "cards_has_legendary": bool(r["has_leg"]),
        "cards_has_holo": bool(r["has_holo"]),
        "cards_has_gem": bool(r["has_gem"]),
        "cards_has_1of1": bool(r["has_1of1"]),
    }


# ── Pick'em favorite teams (auto-pick) ───────────────────────────────────────
async def add_pickem_favorite(discord_user: str, team: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO pickem_favorites (discord_user, team) VALUES (?, ?)",
            (discord_user, team.strip().lower()),
        )
        await db.commit()


async def remove_pickem_favorite(discord_user: str, team: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM pickem_favorites WHERE discord_user = ? AND team = ?",
            (discord_user, team.strip().lower()),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_pickem_favorites(discord_user: str) -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT team FROM pickem_favorites WHERE discord_user = ? ORDER BY team", (discord_user,)
        )
        return [r[0] for r in await cur.fetchall()]


async def get_all_pickem_favorites() -> dict[str, dict]:
    """Every user's favorite teams + their auto-pick stake, for the auto-pick loop.
    Returns {discord_user: {"teams": [..], "stake": int}}. Stake defaults to 1."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT f.discord_user, f.team, COALESCE(s.pickem_fav_stake, 1) stake "
            "FROM pickem_favorites f LEFT JOIN user_settings s ON s.discord_user = f.discord_user"
        )).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        entry = out.setdefault(r["discord_user"], {"teams": [], "stake": r["stake"] or 1})
        entry["teams"].append(r["team"])
    return out


async def get_pickem_fav_stake(discord_user: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT pickem_fav_stake FROM user_settings WHERE discord_user = ?", (discord_user,)
        )
        row = await cur.fetchone()
    return (row[0] if row and row[0] is not None else 1)


async def set_pickem_fav_stake(discord_user: str, stake: int) -> None:
    stake = max(1, min(5, stake))
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO user_settings (discord_user, pickem_fav_stake) VALUES (?, ?) "
            "ON CONFLICT(discord_user) DO UPDATE SET pickem_fav_stake = excluded.pickem_fav_stake",
            (discord_user, stake),
        )
        await db.commit()


# ── Earnings-results post dedupe ─────────────────────────────────────────────
async def earnings_result_posted(ticker: str, report_date: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM earnings_posted WHERE ticker = ? AND report_date = ?",
            (ticker, report_date),
        )
        return await cur.fetchone() is not None


async def mark_earnings_result_posted(ticker: str, report_date: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO earnings_posted (ticker, report_date) VALUES (?, ?)",
            (ticker, report_date),
        )
        await db.commit()


# ── Stock dividends ──────────────────────────────────────────────────────────
async def dividend_paid(discord_user: str, ticker: str, ex_date: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM dividends_paid WHERE discord_user = ? AND ticker = ? AND ex_date = ?",
            (discord_user, ticker, ex_date),
        )
        return await cur.fetchone() is not None


async def record_dividend(discord_user: str, ticker: str, ex_date: str, amount: float) -> None:
    """Log a dividend credit (dedupe key = user+ticker+ex_date). Does NOT move cash —
    call adjust_stock_cash separately so the caller controls ordering."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO dividends_paid (discord_user, ticker, ex_date, amount, paid_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (discord_user, ticker, ex_date, amount, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()


async def get_dividend_income(discord_user: str) -> float:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM dividends_paid WHERE discord_user = ?",
            (discord_user,),
        )
        row = await cur.fetchone()
    return float(row[0] or 0.0)


# ── Card set-completion + trade-up ───────────────────────────────────────────
async def get_set_completion(discord_user: str, set_id: int) -> dict:
    """How much of a set the user owns: {owned, total, complete}. owned = distinct
    designs in the set they hold >=1 copy of."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        total = (await (await db.execute(
            "SELECT COUNT(*) c FROM card_designs WHERE set_id = ?", (set_id,)
        )).fetchone())["c"]
        owned = (await (await db.execute(
            "SELECT COUNT(DISTINCT d.design_id) c FROM card_designs d "
            "JOIN card_instances i ON i.design_id = d.design_id "
            "WHERE d.set_id = ? AND i.owner_id = ?", (set_id, discord_user)
        )).fetchone())["c"]
    return {"owned": owned, "total": total, "complete": total > 0 and owned >= total}


async def set_completion_claimed(discord_user: str, set_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM card_set_completed WHERE discord_user = ? AND set_id = ?",
            (discord_user, set_id),
        )
        return await cur.fetchone() is not None


async def mark_set_completion(discord_user: str, set_id: int) -> bool:
    """Record a set-completion claim. Returns True if newly recorded (caller then pays
    the reward), False if already claimed."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT OR IGNORE INTO card_set_completed (discord_user, set_id, claimed_at) VALUES (?, ?, ?)",
            (discord_user, set_id, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()
        return cur.rowcount > 0


async def consume_card_instances(discord_user: str, instance_ids: list[int]) -> bool:
    """Delete the given instances iff the user owns ALL of them (for trade-up). Atomic;
    returns False (and changes nothing) if any isn't owned."""
    if not instance_ids:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN IMMEDIATE")
        try:
            ph = ",".join("?" * len(instance_ids))
            cnt = (await (await db.execute(
                f"SELECT COUNT(*) c FROM card_instances WHERE owner_id = ? AND instance_id IN ({ph})",
                (discord_user, *instance_ids),
            )).fetchone())["c"]
            if cnt != len(instance_ids):
                await db.execute("ROLLBACK")
                return False
            await db.execute(
                f"DELETE FROM card_instances WHERE instance_id IN ({ph})", tuple(instance_ids)
            )
            await db.commit()
            return True
        except Exception:
            await db.execute("ROLLBACK")
            raise


async def mint_tradeup_card(discord_user: str, set_id: int, target_rarity: str, now_iso: str) -> dict | None:
    """Mint one random card of `target_rarity` from `set_id` to the user (for trade-up
    rewards). Rolls holo/gem like a pack pull. Returns the minted card dict, or None if
    no design of that rarity has copies left."""
    from shared import cards as engine

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN IMMEDIATE")
        try:
            designs = await (await db.execute(
                "SELECT * FROM card_designs WHERE set_id = ? AND rarity = ? AND total_copies > minted",
                (set_id, target_rarity),
            )).fetchall()
            if not designs:
                await db.execute("ROLLBACK")
                return None
            d = random.choice(designs)
            serial = d["minted"] + 1
            is_holo = engine.roll_holo(random.Random())
            gem = engine.roll_gem(random.Random())
            book = engine.book_value(d["rarity"], is_holo, gem)
            await db.execute("UPDATE card_designs SET minted = minted + 1 WHERE design_id = ?", (d["design_id"],))
            cur = await db.execute(
                "INSERT INTO card_instances "
                "(design_id, owner_id, serial, is_holo, gem, book_value, acquired_cost, source, acquired_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 0, 'tradeup', ?)",
                (d["design_id"], discord_user, serial, 1 if is_holo else 0, gem, book, now_iso),
            )
            await db.commit()
            return {
                "instance_id": cur.lastrowid, "design_id": d["design_id"], "name": d["subject_name"],
                "rarity": d["rarity"], "is_holo": is_holo, "gem": gem, "serial": serial,
                "total_copies": d["total_copies"], "book_value": book, "headshot_url": d["headshot_url"],
            }
        except Exception:
            await db.execute("ROLLBACK")
            raise
