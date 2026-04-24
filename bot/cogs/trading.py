"""Paper trading cog — predict outcomes on real games at real odds."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")

import discord
import httpx
from discord import app_commands
from discord.ext import commands, tasks

from db import queries
from shared.odds_utils import american_to_decimal, american_to_prob, compute_clv, fmt_prob, side_is_home
from .odds import game_autocomplete, mlb_game_autocomplete

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

# ESPN public scoreboard endpoints (no API key required).
# Used as a fallback to detect completed games when the Temporal pipeline
# hasn't yet written scores to the DB.
_ESPN_SCOREBOARD: dict[str, str] = {
    "nba": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
    "mlb": "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard",
}

# How long after scheduled tip-off before we assume a game could be final.
# NBA games typically run ≤ 2.5 h; 3.5 h gives a comfortable margin for OT.
_GAME_COMPLETION_BUFFER = timedelta(hours=3, minutes=30)

MARKET_CHOICES = [
    app_commands.Choice(name="Spread", value="spread"),
    app_commands.Choice(name="Moneyline", value="moneyline"),
    app_commands.Choice(name="Total", value="total"),
]

EMBED_COLOR = 0xF5A623  # gold/orange — visually distinct from /log green


# ── Score fetching (ESPN fallback) ────────────────────────────────────────────


async def _fetch_espn_score_for_game(game) -> tuple[int, int] | None:
    """Fetch the final score for *game* directly from ESPN's free scoreboard API.

    Returns ``(home_score, away_score)`` when the game is marked STATUS_FINAL
    and has non-zero scores, otherwise ``None``.

    This is used as a fallback by the paper-resolution loop when the Temporal
    ``BetResolutionWorkflow`` hasn't yet written scores to the local DB.
    Not writing to the DB here keeps the Temporal pipeline as the authoritative
    source for real-bet resolution.
    """
    url = _ESPN_SCOREBOARD.get(game.sport)
    if not url:
        return None

    start = datetime.fromisoformat(game.start_time_utc_iso)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    espn_date = start.strftime("%Y%m%d")

    home_last = game.home_team.split()[-1].lower()
    away_last = game.away_team.split()[-1].lower()

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params={"dates": espn_date}, timeout=10.0)
            if resp.status_code != 200:
                return None
            data = resp.json()
    except Exception as exc:
        log.warning("[paper_resolution] ESPN fetch failed for %s: %s", game.game_id, exc)
        return None

    for event in data.get("events", []):
        status_type = event.get("status", {}).get("type", {}).get("name", "")
        if status_type != "STATUS_FINAL":
            continue
        comp = event.get("competitions", [{}])[0]
        competitors = comp.get("competitors", [])
        home_name = away_name = ""
        home_score = away_score = 0
        for c in competitors:
            team_name = c.get("team", {}).get("displayName", "")
            try:
                score = int(c.get("score", "0") or "0")
            except (ValueError, TypeError):
                score = 0
            if c.get("homeAway") == "home":
                home_name = team_name
                home_score = score
            else:
                away_name = team_name
                away_score = score

        if not home_name or not away_name:
            continue
        if home_score == 0 and away_score == 0:
            continue  # scores not populated yet despite STATUS_FINAL

        # Match game by last word of each team name (same heuristic as Temporal).
        if home_last in home_name.lower() and away_last in away_name.lower():
            return (home_score, away_score)

    return None


# ── Helpers ───────────────────────────────────────────────────────────────────


def _parse_pick(pick: str) -> tuple[str, float | None]:
    """Parse autocomplete value 'side:line' or bare 'side' into (side, line | None)."""
    if ":" in pick:
        side, _, line_str = pick.partition(":")
        try:
            return side.strip(), float(line_str)
        except ValueError:
            return side.strip(), None
    return pick.strip(), None


def _fmt_gametime(iso: str) -> str:
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(_ET)
    h = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{dt.strftime('%a %b')} {dt.day}, {h}:{dt.strftime('%M')} {ampm} {dt.strftime('%Z')}"


def _compute_payout(wager: int, odds: int) -> int:
    """Total return on a winning bet (wager + profit)."""
    return round(american_to_decimal(odds) * wager)


def _compute_cashout(wager: int, locked_odds: int, current_odds: int) -> int:
    """Fair cashout value based on how odds moved since placement.

    cashout = wager * (current_prob / locked_prob)
    If the line moved in your favor, you get back more than you wagered.
    If it moved against you, you get back less.
    """
    locked_prob = american_to_prob(locked_odds)
    current_prob = american_to_prob(current_odds)
    if locked_prob <= 0:
        return 0
    return max(0, round(wager * current_prob / locked_prob))


def _pick_odds_from_snapshots(
    snapshots: list, market: str, side: str, home_team: str, away_team: str,
) -> int | None:
    """Extract the relevant American odds from the latest poll snapshots.

    Priority: Kalshi for ML (no vig), DraftKings for spread/total.
    """
    snap_by_source: dict[str, object] = {}
    for s in snapshots:
        snap_by_source[s.source] = s

    if market == "moneyline":
        snap = snap_by_source.get("kalshi") or snap_by_source.get("draftkings")
    else:
        snap = snap_by_source.get("draftkings") or snap_by_source.get("fanduel")

    if snap is None:
        return None

    payload = snap.payload if isinstance(snap.payload, dict) else json.loads(snap.payload)
    side_l = side.lower()
    home_l = home_team.lower()
    away_l = away_team.lower()

    if market == "moneyline":
        if side_l in home_l or home_l.split()[-1] in side_l:
            return payload.get("ml_home")
        if side_l in away_l or away_l.split()[-1] in side_l:
            return payload.get("ml_away")

    elif market == "spread":
        return payload.get("spread_odds")

    elif market == "total":
        if side_l == "over":
            return payload.get("total_over_odds")
        if side_l == "under":
            return payload.get("total_under_odds")

    return None


# ── Pick autocomplete (reuses same pattern as bets.py) ───────────────────────


async def trade_pick_autocomplete(
    interaction: discord.Interaction, _current: str,
) -> list[app_commands.Choice[str]]:
    """Context-aware pick options for paper trades."""
    game_id = getattr(interaction.namespace, "game", None)
    market = getattr(interaction.namespace, "market", None)
    if not game_id or not market:
        return []

    game = await queries.get_game_by_id(game_id)
    if game is None:
        return []

    if market == "moneyline":
        return [
            app_commands.Choice(name=f"{game.away_team} (Away)", value=game.away_team),
            app_commands.Choice(name=f"{game.home_team} (Home)", value=game.home_team),
        ]

    # spread or total — pull latest lines from DB (prefer DraftKings)
    snaps = await queries.get_latest_snapshots_for_game(game_id)
    payload: dict = {}
    for snap in snaps:
        if snap.source == "draftkings":
            try:
                payload = json.loads(snap.payload) if isinstance(snap.payload, str) else snap.payload
                break
            except Exception:
                pass
    if not payload and snaps:
        try:
            payload = json.loads(snaps[0].payload) if isinstance(snaps[0].payload, str) else snaps[0].payload
        except Exception:
            pass

    if market == "spread":
        spread = payload.get("spread")
        spread_odds = payload.get("spread_odds")
        if spread is not None:
            away_spread = -spread
            home_label = f"{game.home_team} {spread:+.1f}"
            if spread_odds is not None:
                home_label += f" ({fmt_prob(spread_odds)})"
            away_label = f"{game.away_team} {away_spread:+.1f}"
            return [
                app_commands.Choice(name=home_label, value=f"{game.home_team}:{spread}"),
                app_commands.Choice(name=away_label, value=f"{game.away_team}:{away_spread}"),
            ]
        return [
            app_commands.Choice(name=f"{game.home_team} (Home)", value=game.home_team),
            app_commands.Choice(name=f"{game.away_team} (Away)", value=game.away_team),
        ]

    if market == "total":
        total = payload.get("total")
        over_odds = payload.get("total_over_odds")
        under_odds = payload.get("total_under_odds")
        if total is not None:
            over_label = f"Over {total}"
            if over_odds is not None:
                over_label += f" ({fmt_prob(over_odds)})"
            under_label = f"Under {total}"
            if under_odds is not None:
                under_label += f" ({fmt_prob(under_odds)})"
            return [
                app_commands.Choice(name=over_label, value=f"over:{total}"),
                app_commands.Choice(name=under_label, value=f"under:{total}"),
            ]
        return [
            app_commands.Choice(name="Over", value="over"),
            app_commands.Choice(name="Under", value="under"),
        ]

    return []


# ── Void-trade autocomplete ──────────────────────────────────────────────────


async def void_trade_autocomplete(
    interaction: discord.Interaction, _current: str,
) -> list[app_commands.Choice[str]]:
    """Show the user's open paper bets."""
    bets = await queries.get_open_paper_bets_for_user(str(interaction.user.id))
    choices = []
    for pb in bets[:25]:
        game = await queries.get_game_by_id(pb["game_id"])
        if game:
            game_str = f"{game.away_team.split()[-1]}@{game.home_team.split()[-1]}"
        else:
            game_str = pb["game_id"][:8]
        market_str = pb["market"]
        if pb["line"] is not None and pb["market"] in ("spread", "total"):
            market_str += f" {pb['line']:+.1f}"
        label = f"#{pb['paper_bet_id']} — {game_str} {market_str} {pb['side']}"
        choices.append(app_commands.Choice(name=label[:100], value=str(pb["paper_bet_id"])))
    return choices


# ── Resolution logic ─────────────────────────────────────────────────────────


def _resolve_paper_bet(
    pb: dict, home_team: str, away_team: str, home_score: int, away_score: int,
) -> str:
    """Return won/lost/push/void for a paper bet given the final score.

    Same logic as temporal.activities._resolve_bet, adapted for dict input.
    """
    side = pb["side"].lower()
    market = pb["market"].lower()
    home_l = home_team.lower()
    away_l = away_team.lower()

    def _is_home(s: str) -> bool:
        return s in home_l or home_l.split()[-1] in s

    def _is_away(s: str) -> bool:
        return s in away_l or away_l.split()[-1] in s

    if market == "moneyline":
        if _is_home(side):
            return "won" if home_score > away_score else "lost"
        if _is_away(side):
            return "won" if away_score > home_score else "lost"

    elif market == "spread":
        if pb["line"] is None:
            return "void"
        if _is_home(side):
            side_score, opp_score = home_score, away_score
        elif _is_away(side):
            side_score, opp_score = away_score, home_score
        else:
            return "void"
        margin = (side_score - opp_score) + pb["line"]
        if abs(margin) < 0.01:
            return "push"
        return "won" if margin > 0 else "lost"

    elif market == "total":
        if pb["line"] is None:
            return "void"
        total = home_score + away_score
        diff = total - pb["line"]
        if abs(diff) < 0.01:
            return "push"
        if side == "over":
            return "won" if diff > 0 else "lost"
        if side == "under":
            return "won" if diff < 0 else "lost"

    return "void"


def _close_odds_for_paper_bet(pb: dict, home_team: str, away_team: str, payload: dict) -> int | None:
    """Extract close odds for a paper bet from a close snapshot payload."""
    market = pb["market"].lower()
    side = pb["side"].lower()
    home = home_team.lower()
    away = away_team.lower()

    if market == "moneyline":
        if side in home or home.split()[-1] in side:
            return payload.get("ml_home")
        if side in away or away.split()[-1] in side:
            return payload.get("ml_away")
    elif market == "spread":
        return payload.get("spread_odds")
    elif market == "total":
        if side == "over":
            return payload.get("total_over_odds")
        if side == "under":
            return payload.get("total_under_odds")
    return None


async def _compute_paper_clv(pb: dict, game) -> float | None:
    """Compute CLV for a paper bet using close snapshots.

    Source: Kalshi for ML (no vig), DraftKings for spread/total.
    """
    market = pb["market"].lower()
    if market == "moneyline":
        close_snap = await queries.get_close_snapshot(pb["game_id"], "kalshi")
        if close_snap is None:
            close_snap = await queries.get_close_snapshot(pb["game_id"], "draftkings")
    else:
        close_snap = await queries.get_close_snapshot(pb["game_id"], "draftkings")
        if close_snap is None:
            close_snap = await queries.get_close_snapshot(pb["game_id"], "fanduel")

    if close_snap is None:
        return None

    payload = close_snap.payload if isinstance(close_snap.payload, dict) else json.loads(close_snap.payload)
    close_odds = _close_odds_for_paper_bet(pb, game.home_team, game.away_team, payload)
    if close_odds is None:
        return None

    # Build compute_clv kwargs
    kwargs: dict = {"market": market}
    if market == "spread":
        kwargs["bet_line"] = pb["line"]
        kwargs["close_line"] = payload.get("spread")
        kwargs["is_home"] = side_is_home(pb["side"], game.home_team, game.away_team)
    elif market == "total":
        kwargs["bet_line"] = pb["line"]
        kwargs["close_line"] = payload.get("total")
        kwargs["is_over"] = pb["side"].lower() == "over"

    return compute_clv(pb["odds"], close_odds, **kwargs)


# ── Cog ───────────────────────────────────────────────────────────────────────


class TradingCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.paper_resolution.start()

    def cog_unload(self) -> None:
        self.paper_resolution.cancel()

    # ── Resolution loop ───────────────────────────────────────────────────

    @tasks.loop(minutes=5)
    async def paper_resolution(self) -> None:
        """Check for final games and auto-resolve paper bets.

        Two-path score lookup:
        1. DB path  — Temporal's BetResolutionWorkflow has already written
                      final scores and marked the game 'final'.
        2. ESPN path — Temporal hasn't processed the game yet (workflow not
                       running, API down, etc.).  When the game's scheduled
                       start + _GAME_COMPLETION_BUFFER has passed we fetch
                       scores directly from ESPN's free public API.
                       We do NOT mark the game 'final' here; that remains
                       the Temporal pipeline's responsibility for real-bet
                       resolution.  Once paper bets are resolved their status
                       leaves 'open', so this loop won't visit the game again.

        All exceptions are caught per-game so a single bad row can't kill
        the loop for every subsequent game.
        """
        try:
            game_ids = await queries.get_games_with_open_paper_bets()
        except Exception as exc:
            log.exception("[paper_resolution] failed to fetch open paper bets: %s", exc)
            return

        for game_id in game_ids:
            try:
                await self._resolve_one_game(game_id)
            except Exception as exc:
                log.exception(
                    "[paper_resolution] unhandled error resolving game %s: %s", game_id, exc
                )

    async def _resolve_one_game(self, game_id: str) -> None:
        """Resolve all open paper bets for a single game."""
        game = await queries.get_game_by_id(game_id)
        if game is None:
            return

        # ── Path 1: scores already in DB (Temporal ran) ───────────────────────
        scores = await queries.get_game_scores(game_id)

        if scores is None:
            # ── Path 2: ESPN fallback ─────────────────────────────────────────
            start = datetime.fromisoformat(game.start_time_utc_iso)
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) < start + _GAME_COMPLETION_BUFFER:
                return  # game still in progress — check again later
            scores = await _fetch_espn_score_for_game(game)
            if scores is None:
                return  # ESPN doesn't have a final score yet

        home_score, away_score = scores

        paper_bets = await queries.get_open_paper_bets_for_game(game_id)
        for pb in paper_bets:
            outcome = _resolve_paper_bet(
                pb, game.home_team, game.away_team, home_score, away_score,
            )
            if outcome == "won":
                payout = pb["potential_payout"]
            elif outcome == "push":
                payout = pb["wager"]
            else:
                payout = 0

            clv = await _compute_paper_clv(pb, game)

            await queries.resolve_paper_bet(pb["paper_bet_id"], outcome, payout, clv)

            log.info(
                "[paper_resolution] bet #%d → %s, payout=%d, clv=%s (%s @ %s %d-%d)",
                pb["paper_bet_id"], outcome, payout,
                f"{clv:+.1f}pp" if clv is not None else "n/a",
                game.away_team, game.home_team, away_score, home_score,
            )

    @paper_resolution.before_loop
    async def _wait_ready(self) -> None:
        await self.bot.wait_until_ready()

    # ── /trade ────────────────────────────────────────────────────────────

    async def _trade_impl(
        self,
        interaction: discord.Interaction,
        game: str,
        market: str,
        pick: str,
    ) -> None:
        await interaction.response.defer()

        # Look up game
        target = await queries.get_game_by_id(game)
        if target is None:
            await interaction.followup.send(
                "Game not found. Select a game from the autocomplete dropdown.",
                ephemeral=True,
            )
            return

        # Reject live/final games
        start = datetime.fromisoformat(target.start_time_utc_iso)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if start <= datetime.now(timezone.utc):
            await interaction.followup.send(
                "This game has already started. You can only trade on upcoming games.",
                ephemeral=True,
            )
            return

        # Parse pick
        side, pick_line = _parse_pick(pick)

        if market in ("spread", "total") and pick_line is None:
            await interaction.followup.send(
                "Spread and total trades require a line. "
                "Select from autocomplete (if lines are available).",
                ephemeral=True,
            )
            return

        # Fetch odds from latest snapshots
        snaps = await queries.get_latest_snapshots_for_game(target.game_id)
        odds = _pick_odds_from_snapshots(
            snaps, market, side, target.home_team, target.away_team,
        )
        if odds is None:
            await interaction.followup.send(
                "No odds available for this market yet. Try again after the pipeline polls.",
                ephemeral=True,
            )
            return

        user_id = str(interaction.user.id)

        # Insert paper bet (wager=0, potential_payout=0 — coin-free tracking)
        now_iso = datetime.now(timezone.utc).isoformat()
        paper_bet_id = await queries.insert_paper_bet(
            game_id=target.game_id,
            discord_user=user_id,
            placed_at=now_iso,
            market=market,
            side=side,
            line=pick_line,
            odds=odds,
            wager=0,
            potential_payout=0,
        )

        # Confirmation embed
        line_str = f" {pick_line:+.1f}" if pick_line is not None else ""
        embed = discord.Embed(title="Paper Trade Placed", color=EMBED_COLOR)
        embed.add_field(
            name="Game",
            value=f"{target.away_team} @ {target.home_team}\n{_fmt_gametime(target.start_time_utc_iso)}",
            inline=False,
        )
        embed.add_field(name="Market", value=f"{market}{line_str}", inline=True)
        embed.add_field(name="Pick", value=side, inline=True)
        embed.add_field(name="Odds", value=f"`{fmt_prob(odds)}`", inline=True)
        embed.set_footer(text=f"Trade #{paper_bet_id}")
        await interaction.followup.send(embed=embed)

    _TRADE_DESCRIBE = dict(
        game="Select a game",
        market="Market type",
        pick="Your pick — autocomplete shows live lines",
    )

    @app_commands.command(name="trade", description="Paper trade on an NBA game")
    @app_commands.autocomplete(game=game_autocomplete, pick=trade_pick_autocomplete)
    @app_commands.describe(**_TRADE_DESCRIBE)
    @app_commands.choices(market=MARKET_CHOICES)
    async def trade(
        self, interaction: discord.Interaction,
        game: str, market: str, pick: str,
    ) -> None:
        await self._trade_impl(interaction, game, market, pick)

    @app_commands.command(name="mlb-trade", description="Paper trade on an MLB game")
    @app_commands.autocomplete(game=mlb_game_autocomplete, pick=trade_pick_autocomplete)
    @app_commands.describe(**_TRADE_DESCRIBE)
    @app_commands.choices(market=MARKET_CHOICES)
    async def mlb_trade(
        self, interaction: discord.Interaction,
        game: str, market: str, pick: str,
    ) -> None:
        await self._trade_impl(interaction, game, market, pick)

    # ── /portfolio ────────────────────────────────────────────────────────

    @app_commands.command(name="portfolio", description="View your open paper trades")
    @app_commands.describe(user="Check another user's portfolio")
    async def portfolio(
        self, interaction: discord.Interaction,
        user: discord.Member | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        target = user or interaction.user
        user_id = str(target.id)

        open_bets = await queries.get_open_paper_bets_for_user(user_id)
        stats = await queries.get_paper_bet_stats(user_id)

        embed = discord.Embed(
            title=f"{target.display_name}'s Portfolio",
            color=EMBED_COLOR,
        )

        if not open_bets:
            embed.description = "No open paper trades."
        else:
            lines = []
            for pb in open_bets:
                game = await queries.get_game_by_id(pb["game_id"])
                if game:
                    game_str = f"{game.away_team.split()[-1]}@{game.home_team.split()[-1]}"
                else:
                    game_str = pb["game_id"][:8]
                market_str = pb["market"]
                if pb["line"] is not None and pb["market"] in ("spread", "total"):
                    market_str += f" {pb['line']:+.1f}"
                lines.append(
                    f"`#{pb['paper_bet_id']}` {game_str} — "
                    f"{market_str} **{pb['side']}** "
                    f"`{fmt_prob(pb['odds'])}`"
                )
            embed.description = "\n".join(lines)

        if stats["num_bets"] > 0:
            embed.add_field(
                name="Resolved",
                value=(
                    f"{stats['num_won']}W-{stats['num_lost']}L-{stats['num_push']}P"
                ),
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /profile ──────────────────────────────────────────────────────────

    @app_commands.command(name="profile", description="Paper trading stats and history")
    @app_commands.describe(user="Check another user's profile")
    async def profile(
        self, interaction: discord.Interaction,
        user: discord.Member | None = None,
    ) -> None:
        await interaction.response.defer()
        target = user or interaction.user
        user_id = str(target.id)
        is_self = target.id == interaction.user.id

        stats = await queries.get_paper_bet_stats(user_id)
        market_stats = await queries.get_paper_stats_by_market(user_id)
        streak_status, streak_count = await queries.get_paper_streak(user_id)
        recent = await queries.get_recent_paper_bets(user_id, limit=5)
        open_bets = await queries.get_open_paper_bets_for_user(user_id)

        embed = discord.Embed(
            title=f"{target.display_name}'s Trading Profile",
            color=EMBED_COLOR,
        )

        # Overview
        if stats["num_bets"] == 0:
            embed.description = "No resolved trades yet."
            embed.add_field(name="Open Trades", value=f"`{len(open_bets)}`", inline=True)
            await interaction.followup.send(embed=embed)
            return

        total_w = stats["num_won"] or 0
        total_l = stats["num_lost"] or 0
        total_p = stats["num_push"] or 0
        win_rate = (total_w / (total_w + total_l) * 100) if (total_w + total_l) > 0 else 0

        embed.add_field(
            name="Record",
            value=f"**{total_w}**W - **{total_l}**L - **{total_p}**P ({stats['num_bets']} total)",
            inline=False,
        )
        embed.add_field(name="Win Rate", value=f"`{win_rate:.1f}%`", inline=True)

        # CLV
        if stats.get("avg_clv") is not None and stats.get("clv_count", 0) > 0:
            avg_clv = stats["avg_clv"]
            clv_emoji = "\u2705" if avg_clv > 0.5 else ("\u274c" if avg_clv < -0.5 else "\u2796")
            embed.add_field(
                name="Avg CLV",
                value=f"`{avg_clv:+.1f} pp` {clv_emoji} ({stats['clv_count']} trades)",
                inline=True,
            )

        # Streak
        if streak_count > 1:
            streak_emoji = "\U0001f525" if streak_status == "won" else "\U0001f4a9"
            embed.add_field(
                name="Streak",
                value=f"{streak_emoji} {streak_count}{'W' if streak_status == 'won' else 'L'}",
                inline=True,
            )

        # Per-market breakdown
        if market_stats:
            market_lines = []
            for ms in market_stats:
                mw = ms["num_won"] or 0
                ml = ms["num_lost"] or 0
                mp = ms["num_push"] or 0
                mwr = (mw / (mw + ml) * 100) if (mw + ml) > 0 else 0
                market_lines.append(
                    f"**{ms['market'].title()}**: {mw}W-{ml}L-{mp}P "
                    f"({mwr:.0f}%) | `{ms['net_profit']:+d}`c"
                )
            embed.add_field(
                name="By Market",
                value="\n".join(market_lines),
                inline=False,
            )

        # Recent trades
        if recent:
            recent_lines = []
            for pb in recent:
                status_icon = {"won": "\u2705", "lost": "\u274c", "push": "\u2796", "void": "\u23ed\ufe0f"}.get(pb["status"], "?")
                game = await queries.get_game_by_id(pb["game_id"])
                if game:
                    game_str = f"{game.away_team.split()[-1]}@{game.home_team.split()[-1]}"
                else:
                    game_str = pb["game_id"][:8]
                market_str = pb["market"]
                if pb["line"] is not None and pb["market"] in ("spread", "total"):
                    market_str += f" {pb['line']:+.1f}"
                clv_bit = f" ({pb['clv']:+.1f}pp)" if pb.get("clv") is not None else ""
                recent_lines.append(
                    f"{status_icon} {game_str} {market_str} {pb['side']}{clv_bit}"
                )
            embed.add_field(
                name="Recent Trades",
                value="\n".join(recent_lines),
                inline=False,
            )

        if open_bets:
            embed.set_footer(text=f"{len(open_bets)} open trade(s)")

        await interaction.followup.send(embed=embed)

    # ── /leaderboard ──────────────────────────────────────────────────────

    @app_commands.command(name="leaderboard", description="Paper trading leaderboard")
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        rows = await queries.get_paper_leaderboard(limit=10)
        if not rows:
            await interaction.followup.send("No resolved paper trades yet.")
            return

        embed = discord.Embed(title="Paper Trading Leaderboard", color=EMBED_COLOR)
        lines = []
        for i, row in enumerate(rows, 1):
            try:
                member = await self.bot.fetch_user(int(row["discord_user"]))
                name = member.display_name
            except Exception:
                name = f"User {row['discord_user'][:8]}"

            medal = {1: "\U0001f947", 2: "\U0001f948", 3: "\U0001f949"}.get(i, f"**{i}.**")
            nw = row["num_won"] or 0
            nl = row["num_lost"] or 0
            wr = (nw / (nw + nl) * 100) if (nw + nl) > 0 else 0
            clv_str = f" | CLV `{row['avg_clv']:+.1f}pp`" if row.get("avg_clv") is not None else ""
            lines.append(
                f"{medal} **{name}** — "
                f"{nw}W-{nl}L ({wr:.0f}%){clv_str}"
            )

        embed.description = "\n".join(lines)
        await interaction.followup.send(embed=embed)

    # ── /cashout ──────────────────────────────────────────────────────────

    @app_commands.command(name="cashout", description="Cash out an open paper trade at current odds")
    @app_commands.describe(trade_id="Select a trade to cash out")
    @app_commands.autocomplete(trade_id=void_trade_autocomplete)
    async def cashout(self, interaction: discord.Interaction, trade_id: str) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            bet_id = int(trade_id.lstrip("#"))
        except ValueError:
            await interaction.followup.send("Invalid trade ID.", ephemeral=True)
            return

        pb = await queries.get_paper_bet_by_id(bet_id)
        if pb is None:
            await interaction.followup.send("Trade not found.", ephemeral=True)
            return

        if pb["discord_user"] != str(interaction.user.id):
            await interaction.followup.send("You can only cash out your own trades.", ephemeral=True)
            return

        if pb["status"] != "open":
            await interaction.followup.send(
                f"This trade is already **{pb['status']}** and can't be cashed out.",
                ephemeral=True,
            )
            return

        # Fetch current odds to price the cashout
        game = await queries.get_game_by_id(pb["game_id"])
        if game is None:
            await interaction.followup.send("Game not found.", ephemeral=True)
            return

        snaps = await queries.get_latest_snapshots_for_game(pb["game_id"])
        current_odds = _pick_odds_from_snapshots(
            snaps, pb["market"], pb["side"], game.home_team, game.away_team,
        )
        if current_odds is None:
            await interaction.followup.send(
                "No current odds available — can't price the cashout right now.",
                ephemeral=True,
            )
            return

        # Resolve as void (cashout = no coin transactions)
        await queries.resolve_paper_bet(pb["paper_bet_id"], "void", 0)

        embed = discord.Embed(
            title="Trade Cashed Out",
            color=0x57F287,
        )
        embed.add_field(
            name="Odds",
            value=f"Locked `{fmt_prob(pb['odds'])}` \u2192 Current `{fmt_prob(current_odds)}`",
            inline=False,
        )
        embed.set_footer(text=f"Trade #{bet_id}")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TradingCog(bot))
