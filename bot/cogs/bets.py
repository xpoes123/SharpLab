"""Bet tracking commands — /log and /record."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")

import discord
from discord import app_commands
from discord.ext import commands, tasks

# Where the weekly CLV/record digest posts (falls back to the injury/closing-lines channel).
_DIGEST_CHANNEL_ID = int(os.getenv("BET_DIGEST_CHANNEL_ID") or os.getenv("INJURY_CHANNEL_ID") or 0)
_DIGEST_SETTING = "last_bet_digest"

from db import queries
from shared.models import Bet, Game, OddsSnapshot
from shared.odds_utils import american_to_decimal, compute_clv, fmt_odds, fmt_prob, parse_odds_input, side_is_home
from shared.prop_clv import parse_prop_note, consensus_main_line, prop_clv_at_line
from .odds import (game_autocomplete, mlb_game_autocomplete, log_game_autocomplete,
                   mlb_log_game_autocomplete, props_player_autocomplete)
import logging

log = logging.getLogger(__name__)


async def _check_achievements(interaction: discord.Interaction) -> None:
    """Evaluate achievements after a betting action and announce any unlocks
    in-channel (best-effort)."""
    try:
        from bot.cogs.progression import evaluate_user_achievements, announce_achievements
        uid = str(interaction.user.id)
        newly = await evaluate_user_achievements(uid)
        if newly:
            await announce_achievements(interaction.client, uid, newly, interaction.channel)
    except Exception:
        log.debug("achievement check failed for %s", interaction.user.id, exc_info=True)

# ── Choices ────────────────────────────────────────────────────────────────────

BOOK_CHOICES = [
    app_commands.Choice(name="DraftKings", value="draftkings"),
    app_commands.Choice(name="FanDuel", value="fanduel"),
    app_commands.Choice(name="BetMGM", value="betmgm"),
    app_commands.Choice(name="Caesars", value="caesars"),
    app_commands.Choice(name="Bet365", value="bet365"),
    app_commands.Choice(name="PointsBet", value="pointsbet"),
    app_commands.Choice(name="Kalshi", value="kalshi"),
    app_commands.Choice(name="Polymarket", value="polymarket"),
    app_commands.Choice(name="Other", value="other"),
]

MARKET_CHOICES = [
    app_commands.Choice(name="Spread", value="spread"),
    app_commands.Choice(name="Moneyline", value="moneyline"),
    app_commands.Choice(name="Total", value="total"),
    app_commands.Choice(name="Kalshi", value="kalshi"),
]


# ── Autocomplete ───────────────────────────────────────────────────────────────

async def pick_autocomplete(
    interaction: discord.Interaction, _current: str
) -> list[app_commands.Choice[str]]:
    """Context-aware pick options: team names, spread sides with lines, over/under, yes/no."""
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
    if market == "kalshi":
        return [
            app_commands.Choice(name="Yes", value="yes"),
            app_commands.Choice(name="No", value="no"),
        ]

    # spread or total — pull latest lines from DB (prefer DraftKings)
    snaps = await queries.get_latest_snapshots_for_game(game_id)
    payload: dict = {}
    for snap in snaps:
        if snap.source == "draftkings":
            payload = snap.payload  # already deserialized by queries layer
            break
    if not payload and snaps:
        payload = snaps[0].payload

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
        # No snapshot yet — show team names without lines
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


async def void_autocomplete(
    interaction: discord.Interaction, _current: str
) -> list[app_commands.Choice[str]]:
    """Show the user's open/graded bets for /void autocomplete."""
    bets = await queries.get_open_bets_for_user(str(interaction.user.id))
    choices = []
    for bet in bets[:25]:
        game = await queries.get_game_by_id(bet.game_id)
        game_str = (
            f"{game.away_team.split()[-1]}@{game.home_team.split()[-1]}"
            if game else bet.game_id[:8]
        )
        market_str = bet.market
        if bet.line is not None and bet.market in ("spread", "total"):
            market_str += f" {bet.line:+.1f}"
        label = f"#{bet.bet_id} — {game_str} {market_str} {bet.side}"
        choices.append(app_commands.Choice(name=label[:100], value=str(bet.bet_id)))
    return choices


# ── Helpers ────────────────────────────────────────────────────────────────────

def _fmt_american(odds: int) -> str:
    return fmt_prob(odds)


def _fmt_gametime(iso: str) -> str:
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(_ET)
    h = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{dt.strftime('%a %b')} {dt.day}, {h}:{dt.strftime('%M')} {ampm} {dt.strftime('%Z')}"


def _parse_pick(pick: str) -> tuple[str, float | None]:
    """Parse autocomplete value 'side:line' or bare 'side' into (side, line | None)."""
    if ":" in pick:
        side, _, line_str = pick.partition(":")
        try:
            return side.strip(), float(line_str)
        except ValueError:
            return side.strip(), None
    return pick.strip(), None


def _get_current_odds_for_bet(
    bet: Bet, game: Game, snapshots: list[OddsSnapshot],
) -> int | None:
    """Extract current market odds for a bet from the latest poll snapshots.

    Source priority: Kalshi (no vig) for ML, DraftKings for spread/total.
    """
    market = bet.market.lower()
    side = bet.side.lower()
    home = game.home_team.lower()
    away = game.away_team.lower()

    snap_by_source = {s.source: s for s in snapshots}

    if market in ("moneyline", "kalshi"):
        snap = snap_by_source.get("kalshi") or snap_by_source.get("draftkings")
    else:
        snap = snap_by_source.get("draftkings") or snap_by_source.get("fanduel")

    if snap is None:
        return None

    payload = snap.payload

    if market in ("moneyline", "kalshi"):
        if side in ("yes",) or (side in home and side not in away):
            return payload.get("ml_home")
        if side in ("no",) or (side in away and side not in home):
            return payload.get("ml_away")

    elif market == "spread":
        return payload.get("spread_odds")

    elif market == "total":
        if side == "over":
            return payload.get("total_over_odds")
        if side == "under":
            return payload.get("total_under_odds")

    return None


def _get_current_line_for_bet(
    bet: Bet, snapshots: list[OddsSnapshot],
) -> float | None:
    """Extract current spread/total number for spread/total bets."""
    market = bet.market.lower()
    if market not in ("spread", "total"):
        return None

    snap_by_source = {s.source: s for s in snapshots}
    snap = snap_by_source.get("draftkings") or snap_by_source.get("fanduel")
    if snap is None:
        return None

    payload = snap.payload
    if market == "spread":
        return payload.get("spread")
    if market == "total":
        return payload.get("total")
    return None


def _record_embed(
    user: discord.Member | discord.User,
    bets: list[Bet],
    game_labels: dict[str, str] | None = None,
) -> discord.Embed:
    total = len(bets)
    if total == 0:
        embed = discord.Embed(
            title=f"Record — {user.display_name}",
            description="No bets logged yet.",
            color=0x5865F2,
        )
        return embed

    by_status: dict[str, list[Bet]] = {}
    for bet in bets:
        by_status.setdefault(bet.status, []).append(bet)

    won = by_status.get("won", [])
    lost = by_status.get("lost", [])
    push = by_status.get("push", [])
    open_ = by_status.get("open", [])

    settled = won + lost + push

    # Net units
    net = 0.0
    units_risked = 0.0
    for b in won:
        net += b.units * (american_to_decimal(b.odds) - 1)
        units_risked += b.units
    for b in lost:
        net -= b.units
        units_risked += b.units
    for b in push:
        units_risked += b.units  # push: no profit/loss

    roi = (net / units_risked * 100) if units_risked > 0 else 0.0

    wl = f"{len(won)}-{len(lost)}" + (f"-{len(push)}" if push else "")
    color = 0x57F287 if net > 0 else (0xED4245 if net < 0 else 0x5865F2)

    embed = discord.Embed(
        title=f"Record — {user.display_name}",
        color=color,
    )
    embed.add_field(name="W-L" + ("-P" if push else ""), value=f"`{wl}`", inline=True)
    embed.add_field(name="Net units", value=f"`{net:+.2f}u`", inline=True)
    embed.add_field(name="ROI", value=f"`{roi:+.1f}%`" if settled else "`—`", inline=True)
    embed.add_field(name="Open bets", value=f"`{len(open_)}`", inline=True)
    embed.add_field(name="Total logged", value=f"`{total}`", inline=True)

    # Last 5 bets
    recent = bets[:5]
    labels = game_labels or {}
    recent_lines = []
    for b in recent:
        status_icon = {"won": "✅", "lost": "❌", "push": "➖", "open": "⏳", "void": "🚫"}.get(b.status, "?")
        line_str = f" {b.line:+.1f}" if b.line is not None and b.market in ("spread", "total") else ""
        game_prefix = f"{labels[b.game_id]} " if b.game_id in labels else ""
        recent_lines.append(
            f"{status_icon} {game_prefix}{b.market}{line_str} {_fmt_american(b.odds)} ({b.units}u) — {b.book}"
        )
    embed.add_field(name="Recent bets", value="\n".join(recent_lines), inline=False)

    return embed


# ── Cog ────────────────────────────────────────────────────────────────────────

class BetsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.weekly_digest.start()

    def cog_unload(self) -> None:
        self.weekly_digest.cancel()

    @tasks.loop(hours=12)
    async def weekly_digest(self) -> None:
        """Once a week, post the past 7 days' CLV/record leaderboard to the digest
        channel — turns logged bets into a recurring competition."""
        try:
            if not _DIGEST_CHANNEL_ID:
                return
            now = datetime.now(timezone.utc)
            last = await queries.get_bot_setting(_DIGEST_SETTING)
            if last is None:
                await queries.set_bot_setting(_DIGEST_SETTING, now.isoformat())  # seed; don't post on first boot
                return
            last_dt = datetime.fromisoformat(last)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            if (now - last_dt) < timedelta(days=7):
                return
            rows = _aggregate_leaderboard(await queries.get_all_settled_bets((now - timedelta(days=7)).isoformat()))
            await queries.set_bot_setting(_DIGEST_SETTING, now.isoformat())
            if not rows:
                return
            channel = self.bot.get_channel(_DIGEST_CHANNEL_ID)
            if channel is None:
                return
            embed = _leaderboard_embed(getattr(channel, "guild", None), rows, "clv", title_prefix="📅 This Week · ")
            await channel.send(embed=embed)
        except Exception:
            log.exception("weekly bet digest failed")

    @weekly_digest.before_loop
    async def _before_digest(self) -> None:
        await self.bot.wait_until_ready()

    # ── /log ──────────────────────────────────────────────────────────────────

    async def _log_impl(
        self,
        interaction: discord.Interaction,
        game: str,
        book: str,
        market: str,
        pick: str,
        odds: str,
        units: float,
        line: float | None = None,
        notes: str | None = None,
    ) -> None:
        await interaction.response.defer()

        try:
            american_odds, odds_fmt = parse_odds_input(odds)
        except Exception:
            await interaction.followup.send(
                f"Couldn't parse odds `{odds}`. Use American (-110), decimal (1.91), or cents (52).",
                ephemeral=True,
            )
            return

        if not (american_odds <= -100 or american_odds >= 100):
            await interaction.followup.send(
                f'Invalid odds `{odds}` — must be ≤ -100 or ≥ +100 (e.g. -110, +150).',
                ephemeral=True,
            )
            return

        if units <= 0:
            await interaction.followup.send('Units must be a positive number.', ephemeral=True)
            return

        target = await queries.get_game_by_id(game)
        if target is None:
            await interaction.followup.send(
                "Game not found. Select a game from the autocomplete dropdown.",
                ephemeral=True,
            )
            return

        side, pick_line = _parse_pick(pick)
        # Explicit line param overrides autocomplete-encoded line
        final_line = line if line is not None else pick_line

        if market in ("spread", "total") and final_line is None:
            await interaction.followup.send(
                "Spread and total bets require a line number. "
                "Either select from autocomplete (if lines are available) or enter it in the `line` field.",
                ephemeral=True,
            )
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        bet = Bet(
            game_id=target.game_id,
            placed_at=now_iso,
            discord_user=str(interaction.user.id),
            book=book,
            market=market,
            side=side,
            odds=american_odds,
            units=units,
            line=final_line,
            notes=notes,
        )
        bet_id = await queries.insert_bet(bet)
        try:
            from bot.cogs.progression import award_xp, XP_BET
            await award_xp(interaction.client, str(interaction.user.id), XP_BET, interaction.channel)
        except Exception:
            log.debug("bet xp award failed", exc_info=True)
        await _check_achievements(interaction)

        line_str = f" {final_line:+.1f}" if final_line is not None else ""

        embed = discord.Embed(title="Bet logged", color=0x57F287)
        embed.add_field(
            name="Game",
            value=f"{target.away_team} @ {target.home_team}\n{_fmt_gametime(target.start_time_utc_iso)}",
            inline=False,
        )
        embed.add_field(name="Book", value=book, inline=True)
        embed.add_field(name="Market", value=f"{market}{line_str}", inline=True)
        embed.add_field(name="Pick", value=side, inline=True)
        embed.add_field(name="Odds", value=f"`{fmt_prob(american_odds)}`", inline=True)
        embed.add_field(name="Units", value=f"`{units}u`", inline=True)
        embed.set_footer(text=f"Bet ID: {bet_id}")
        await interaction.followup.send(embed=embed)

    _LOG_DESCRIBE = dict(
        game="Select a game",
        book="Sportsbook",
        market="Market type",
        pick="Your pick — autocomplete shows live lines (team, spread side, over/under, yes/no)",
        odds="Odds in any format: American (-110, +150), decimal (1.91), or cents (52)",
        units="Units risked (e.g. 1.0)",
        line="Spread or total number if not auto-filled by autocomplete (e.g. -4.5, 224.5)",
        notes="Optional notes",
    )

    # ── /bet group ────────────────────────────────────────────────────────────

    bet_group = app_commands.Group(name="bet", description="Bet tracking — log, view, void, record, CLV")
    log_group = app_commands.Group(name="log", description="Log a bet to your record", parent=bet_group)

    @log_group.command(name="nba", description="Log an NBA bet (incl. live in-game) to your record")
    @app_commands.autocomplete(game=log_game_autocomplete, pick=pick_autocomplete)
    @app_commands.describe(**_LOG_DESCRIBE)
    @app_commands.choices(book=BOOK_CHOICES, market=MARKET_CHOICES)
    async def bet_log_nba(self, interaction: discord.Interaction, game: str, book: str, market: str, pick: str, odds: str, units: float, line: float | None = None, notes: str | None = None) -> None:
        await self._log_impl(interaction, game, book, market, pick, odds, units, line, notes)

    @log_group.command(name="mlb", description="Log an MLB bet (incl. live in-game) to your record")
    @app_commands.autocomplete(game=mlb_log_game_autocomplete, pick=pick_autocomplete)
    @app_commands.describe(**_LOG_DESCRIBE)
    @app_commands.choices(book=BOOK_CHOICES, market=MARKET_CHOICES)
    async def bet_log_mlb(self, interaction: discord.Interaction, game: str, book: str, market: str, pick: str, odds: str, units: float, line: float | None = None, notes: str | None = None) -> None:
        await self._log_impl(interaction, game, book, market, pick, odds, units, line, notes)

    _PROP_STATS = [
        app_commands.Choice(name="Points", value="points"),
        app_commands.Choice(name="Rebounds", value="rebounds"),
        app_commands.Choice(name="Assists", value="assists"),
        app_commands.Choice(name="Threes", value="threes"),
        app_commands.Choice(name="PRA (pts+reb+ast)", value="PRA"),
    ]
    _OU = [app_commands.Choice(name="Over", value="over"), app_commands.Choice(name="Under", value="under")]

    @log_group.command(name="prop", description="Log an NBA player-prop bet")
    @app_commands.autocomplete(game=log_game_autocomplete, player=props_player_autocomplete)
    @app_commands.describe(game="NBA game", player="Player (pick the game first)", stat="Stat",
                           side="Over or Under", line="The prop line (e.g. 15.5)",
                           odds="American (-115), decimal (1.87), or cents", units="Units risked",
                           book="Sportsbook", notes="Optional note")
    @app_commands.choices(book=BOOK_CHOICES, stat=_PROP_STATS, side=_OU)
    async def bet_log_prop(self, interaction: discord.Interaction, game: str, player: str, stat: str,
                           side: str, line: float, odds: str, units: float, book: str,
                           notes: str | None = None) -> None:
        await interaction.response.defer()
        try:
            american_odds, _ = parse_odds_input(odds)
        except Exception:
            await interaction.followup.send(f"Couldn't parse odds `{odds}`. Use American (-115), decimal, or cents.", ephemeral=True)
            return

        if not (american_odds <= -100 or american_odds >= 100):
            await interaction.followup.send(
                f'Invalid odds `{odds}` — must be ≤ -100 or ≥ +100 (e.g. -110, +150).',
                ephemeral=True,
            )
            return

        if units <= 0:
            await interaction.followup.send('Units must be a positive number.', ephemeral=True)
            return

        target = await queries.get_game_by_id(game)
        if target is None:
            await interaction.followup.send("Game not found — pick from the dropdown.", ephemeral=True)
            return
        stat_label = next((c.name for c in self._PROP_STATS if c.value == stat), stat)
        prop = f"{player} {stat_label}"
        bet = Bet(
            game_id=target.game_id, placed_at=datetime.now(timezone.utc).isoformat(),
            discord_user=str(interaction.user.id), book=book, market="prop", side=side,
            odds=american_odds, units=units, line=line,
            notes=prop + (f" — {notes}" if notes else ""),
        )
        await queries.insert_bet(bet)
        try:
            from bot.cogs.progression import award_xp, XP_BET
            await award_xp(interaction.client, str(interaction.user.id), XP_BET, interaction.channel)
        except Exception:
            log.debug("prop xp failed", exc_info=True)
        await _check_achievements(interaction)
        am = f"+{american_odds}" if american_odds > 0 else str(american_odds)
        await interaction.followup.send(
            f"✅ Logged prop — **{player} {side.title()} {line:g} {stat_label}** @ `{am}` · {units:g}u on `{book}`\n"
            f"*{target.away_team} @ {target.home_team}*  ·  grade it later with `/bet record` once the game's final.")

    # ── /bet view ─────────────────────────────────────────────────────────────

    @bet_group.command(name="view", description="View your open and graded bets")
    async def open_bets(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        bets = await queries.get_open_bets_for_user(str(interaction.user.id))

        if not bets:
            await interaction.followup.send("You have no open or graded bets.", ephemeral=True)
            return

        odds_fmt = await queries.get_user_odds_format(str(interaction.user.id))

        # Pre-fetch games and latest snapshots per game (for live CLV)
        game_cache: dict[str, Game | None] = {}
        snap_cache: dict[str, list[OddsSnapshot]] = {}
        prop_cache: dict[str, dict] = {}  # game_id -> {"main": [...], "all": main+alt rows}
        for bet in bets:
            if bet.game_id not in game_cache:
                game_cache[bet.game_id] = await queries.get_game_by_id(bet.game_id)
            if bet.market == "prop":
                if bet.game_id not in prop_cache:
                    main = await queries.get_player_props_for_game(bet.game_id)
                    alts = await queries.get_player_prop_alts_for_game(bet.game_id)
                    prop_cache[bet.game_id] = {"main": main, "all": main + alts}
            elif bet.game_id not in snap_cache:
                snap_cache[bet.game_id] = await queries.get_latest_snapshots_for_game(bet.game_id)

        lines = []
        for bet in bets:
            game = game_cache.get(bet.game_id)
            if game is not None:
                away_short = game.away_team.split()[-1]
                home_short = game.home_team.split()[-1]
                game_str = f"{away_short} @ {home_short}"
            else:
                game_str = bet.game_id[:8]

            prop_player, prop_market = (parse_prop_note(bet.notes) if bet.market == "prop" else (None, None))

            if bet.market == "spread" and bet.line is not None:
                market_str = f"spread {bet.line:+.1f}"
            elif bet.market == "total" and bet.line is not None:
                direction = "O" if bet.side.lower() == "over" else "U"
                market_str = f"total {direction}{bet.line:.1f}"
            elif bet.market == "prop" and prop_market:
                stat = prop_market.replace("player_points_rebounds_assists", "PRA").replace("player_", "")
                who = prop_player.split()[-1] if prop_player else "prop"
                market_str = f"{who} {stat} {bet.line:g}"
            else:
                market_str = bet.market

            icon = "📊" if bet.status == "graded" else "⏳"

            # CLV: graded bets use stored CLV, open bets get live CLV from latest snapshot
            clv_str = ""
            now_str = ""
            if bet.market == "prop" and prop_market:
                cache = prop_cache.get(bet.game_id, {"main": [], "all": []})
                rows = [r for r in cache["all"] if r["player"] == prop_player and r["market"] == prop_market]
                res = prop_clv_at_line(bet.side, bet.line, bet.odds, rows)
                main_rows = [r for r in cache["main"] if r["player"] == prop_player and r["market"] == prop_market]
                main_line = consensus_main_line(main_rows)
                is_alt = main_line is not None and abs(bet.line - main_line) > 0.01
                if res is not None:
                    clv_str = f"  {res['clv']:+.1f}pp{'*' if is_alt else ''}"
                else:
                    clv_str = "  —"  # no real price captured at this line yet
            elif bet.status == "graded" and bet.clv is not None:
                clv_str = f"  {bet.clv:+.1f}pp"
            elif bet.status == "open" and game is not None:
                snaps = snap_cache.get(bet.game_id, [])
                current_odds = _get_current_odds_for_bet(bet, game, snaps)
                if current_odds is not None:
                    market = bet.market.lower()
                    current_line = _get_current_line_for_bet(bet, snaps)
                    live_clv = compute_clv(
                        bet.odds, current_odds,
                        market=market,
                        bet_line=bet.line,
                        close_line=current_line,
                        is_home=side_is_home(bet.side, game.home_team, game.away_team) if market == "spread" else None,
                        is_over=(bet.side.lower() == "over") if market == "total" else None,
                    )
                    now_str = f"→{fmt_odds(current_odds, odds_fmt)}"
                    clv_str = f"  {live_clv:+.1f}pp"

            odds_str = f"{fmt_odds(bet.odds, odds_fmt)}{now_str}"
            lines.append(
                f"{icon}  {game_str}  {market_str}  {bet.side}  {odds_str}  {bet.units}u{clv_str}  #{bet.bet_id}"
            )

        # Build description within Discord's 4096-char embed limit.
        truncated = False
        kept: list[str] = []
        total = len("```\n") + len("\n```")
        for line in lines:
            addition = len(line) + 1  # +1 for the joining newline
            if total + addition > 4000:
                truncated = True
                break
            kept.append(line)
            total += addition

        description = "```\n" + "\n".join(kept) + "\n```"
        footer_text = f"{len(bets)} bet{'s' if len(bets) != 1 else ''} pending · CLV = placed vs current"
        if any(b.market == "prop" for b in bets):
            footer_text += " · prop *=alt line, —=no close yet"
        if truncated:
            footer_text += f" · showing {len(kept)}/{len(bets)} bets (list truncated)"

        embed = discord.Embed(
            title=f"Open bets — {interaction.user.display_name}",
            description=description,
            color=0x5865F2,
        )
        embed.set_footer(text=footer_text)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /bet clv ──────────────────────────────────────────────────────────────

    @bet_group.command(name="clv", description="CLV breakdown and EV gained from beating the closing line")
    @app_commands.describe(user="User to look up (defaults to you)")
    async def clv_summary(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ) -> None:
        await interaction.response.defer()

        target_user = user or interaction.user
        bets = await queries.get_graded_bets_for_user(str(target_user.id))

        if not bets:
            await interaction.followup.send(
                f"No CLV data yet for **{target_user.display_name}** — bets are graded at tip-off."
            )
            return

        # Only bets with CLV populated (CLV is filled by the close-capture flow,
        # which may lag behind bet resolution).
        clv_bets = [b for b in bets if b.clv is not None]

        if not clv_bets:
            await interaction.followup.send(
                f"**{target_user.display_name}** has {len(bets)} graded bet{'s' if len(bets) != 1 else ''} "
                "but CLV hasn't been captured yet — check back after the games close."
            )
            return

        # EV gained = units × (clv_pp / 100)
        def _ev(b: Bet) -> float:
            return b.units * (b.clv / 100)  # type: ignore[operator]

        total_ev = sum(_ev(b) for b in clv_bets)
        avg_clv = sum(b.clv for b in clv_bets) / len(clv_bets)  # type: ignore[arg-type]

        # Breakdown helpers
        def _breakdown(groups: dict[str, list[Bet]]) -> str:
            rows = []
            for key in sorted(groups):
                group = groups[key]
                g_avg = sum(b.clv for b in group) / len(group)  # type: ignore[arg-type]
                g_ev = sum(_ev(b) for b in group)
                rows.append(f"  {key:<14} {len(group):>3}  avg {g_avg:+.1f}pp  EV {g_ev:+.2f}u")
            return "\n".join(rows)

        by_market: dict[str, list[Bet]] = {}
        by_book: dict[str, list[Bet]] = {}
        for b in clv_bets:
            by_market.setdefault(b.market, []).append(b)
            by_book.setdefault(b.book, []).append(b)

        color = 0x57F287 if total_ev > 0 else (0xED4245 if total_ev < 0 else 0x5865F2)
        embed = discord.Embed(
            title=f"CLV Summary — {target_user.display_name}",
            color=color,
        )
        pending = len(bets) - len(clv_bets)
        graded_value = f"`{len(clv_bets)}`" if not pending else f"`{len(clv_bets)}` (+{pending} pending CLV)"
        embed.add_field(name="Bets graded", value=graded_value, inline=True)
        embed.add_field(name="Avg CLV", value=f"`{avg_clv:+.2f}pp`", inline=True)
        embed.add_field(name="Total EV gained", value=f"`{total_ev:+.3f}u`", inline=True)
        embed.add_field(
            name="By market",
            value="```\n" + _breakdown(by_market) + "\n```",
            inline=False,
        )
        embed.add_field(
            name="By book",
            value="```\n" + _breakdown(by_book) + "\n```",
            inline=False,
        )
        embed.set_footer(text="EV gained = Σ (units × CLV / 100) — theoretical edge vs. closing line")
        await interaction.followup.send(embed=embed)

    # ── /bet void ─────────────────────────────────────────────────────────────

    @bet_group.command(name="void", description="Void a bet you logged (cancelled game or entry error)")
    @app_commands.describe(bet_id="Your open bet — select from autocomplete or type the ID")
    @app_commands.autocomplete(bet_id=void_autocomplete)
    async def void_bet(self, interaction: discord.Interaction, bet_id: str) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            bid = int(bet_id)
        except ValueError:
            await interaction.followup.send("Invalid bet ID.", ephemeral=True)
            return

        bet = await queries.get_bet_by_id(bid)
        if bet is None:
            await interaction.followup.send(f"Bet #{bid} not found.", ephemeral=True)
            return

        if bet.discord_user != str(interaction.user.id):
            await interaction.followup.send("That bet doesn't belong to you.", ephemeral=True)
            return

        if bet.status in ("won", "lost", "push", "void"):
            await interaction.followup.send(
                f"Bet #{bid} is already **{bet.status}** and can't be voided.",
                ephemeral=True,
            )
            return

        await queries.update_bet_result(bid, "void")

        game = await queries.get_game_by_id(bet.game_id)
        game_str = (
            f"{game.away_team} @ {game.home_team}" if game else bet.game_id[:8]
        )
        line_str = f" {bet.line:+.1f}" if bet.line is not None and bet.market in ("spread", "total") else ""

        embed = discord.Embed(title="Bet voided", color=0xED4245)
        embed.add_field(name="Game", value=game_str, inline=False)
        embed.add_field(name="Market", value=f"{bet.market}{line_str}", inline=True)
        embed.add_field(name="Pick", value=bet.side, inline=True)
        embed.add_field(name="Odds", value=f"`{fmt_prob(bet.odds)}`", inline=True)
        embed.add_field(name="Units", value=f"`{bet.units}u`", inline=True)
        embed.set_footer(text=f"Bet ID: {bid} — was {bet.status}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /bet record ───────────────────────────────────────────────────────────

    @bet_group.command(name="record", description="View bet record and ROI")
    @app_commands.describe(user="User to look up (defaults to you)")
    async def record(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ) -> None:
        await interaction.response.defer()

        target_user = user or interaction.user
        bets = await queries.get_bets_for_user(str(target_user.id))

        # Pre-fetch game labels for the 5 most recent bets
        game_labels: dict[str, str] = {}
        for b in bets[:5]:
            if b.game_id not in game_labels:
                game = await queries.get_game_by_id(b.game_id)
                if game:
                    away = game.away_team.split()[-1]
                    home = game.home_team.split()[-1]
                    game_labels[b.game_id] = f"{away}@{home}"

        embed = _record_embed(target_user, bets, game_labels)
        await interaction.followup.send(embed=embed)

    @bet_group.command(name="leaderboard", description="Who's beating the closing line — CLV, ROI & record leaderboard")
    @app_commands.describe(metric="Rank by (default: CLV)")
    @app_commands.choices(metric=[
        app_commands.Choice(name="CLV — avg beat vs close", value="clv"),
        app_commands.Choice(name="EV gained — units of edge", value="ev"),
        app_commands.Choice(name="Profit — net units", value="profit"),
        app_commands.Choice(name="ROI %", value="roi"),
        app_commands.Choice(name="Wins", value="wins"),
    ])
    async def leaderboard(self, interaction: discord.Interaction, metric: str = "clv") -> None:
        await interaction.response.defer()
        rows = _aggregate_leaderboard(await queries.get_all_settled_bets())
        embed = _leaderboard_embed(interaction.guild, rows, metric)
        await interaction.followup.send(embed=embed)


def _aggregate_leaderboard(bets: list[Bet]) -> list[dict]:
    """Per-user betting stats from settled bets: record, net units, ROI, CLV, EV gained."""
    by_user: dict[str, list[Bet]] = {}
    for b in bets:
        by_user.setdefault(b.discord_user, []).append(b)
    out = []
    for uid, ub in by_user.items():
        won = [b for b in ub if b.status == "won"]
        lost = [b for b in ub if b.status == "lost"]
        push = [b for b in ub if b.status == "push"]
        net = sum(b.units * (american_to_decimal(b.odds) - 1) for b in won) - sum(b.units for b in lost)
        risked = sum(b.units for b in won + lost + push)
        clvs = [b.clv for b in ub if b.clv is not None]
        out.append({
            "uid": uid, "n": len(ub), "w": len(won), "l": len(lost), "p": len(push),
            "net": net, "roi": (net / risked * 100) if risked else 0.0,
            "avg_clv": (sum(clvs) / len(clvs)) if clvs else None,
            "ev": sum(b.units * (b.clv / 100) for b in ub if b.clv is not None),
        })
    return out


_LB_META = {
    "clv":    ("🎯 CLV Leaderboard", lambda r: r["avg_clv"] if r["avg_clv"] is not None else -1e9,
               lambda r: f"{r['avg_clv']:+.1f}pp" if r["avg_clv"] is not None else "—"),
    "ev":     ("📈 EV Gained Leaderboard", lambda r: r["ev"], lambda r: f"{r['ev']:+.2f}u"),
    "profit": ("💰 Profit Leaderboard", lambda r: r["net"], lambda r: f"{r['net']:+.2f}u"),
    "roi":    ("📊 ROI Leaderboard", lambda r: r["roi"], lambda r: f"{r['roi']:+.1f}%"),
    "wins":   ("🏆 Wins Leaderboard", lambda r: r["w"], lambda r: f"{r['w']}W"),
}


def _leaderboard_embed(guild, rows: list[dict], metric: str, title_prefix: str = "") -> discord.Embed:
    title, key, fmt = _LB_META.get(metric, _LB_META["clv"])
    title = title_prefix + title
    # ROI/CLV need a minimum sample to be meaningful
    pool = [r for r in rows if r["n"] >= (3 if metric in ("roi", "clv") else 1)]
    if metric == "clv":
        pool = [r for r in pool if r["avg_clv"] is not None]
    pool.sort(key=key, reverse=True)
    if not pool:
        return discord.Embed(title=title, description="No graded bets yet — log some with `/bet log`!", color=0x5865F2)

    def name(uid: str) -> str:
        m = guild.get_member(int(uid)) if guild and uid.isdigit() else None
        return m.display_name if m else f"User {uid[:6]}"

    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, r in enumerate(pool[:10]):
        rank = medals[i] if i < 3 else f"`{i+1}.`"
        rec = f"{r['w']}-{r['l']}" + (f"-{r['p']}" if r["p"] else "")
        lines.append(f"{rank} **{name(r['uid'])}** — {fmt(r)}  ·  {rec} ({r['n']})")
    embed = discord.Embed(title=title, description="\n".join(lines), color=0x5865F2)
    embed.set_footer(text="CLV = avg points beaten vs the closing line · (n) = settled bets · /bet leaderboard metric:…")
    return embed


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BetsCog(bot))
