"""Injury alert auto-post — polls DB for unnotified injury changes and posts to Discord."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

from db import queries
from shared.models import Game, InjuryAlert, OddsSnapshot
from shared.odds_utils import american_to_prob, fmt_prob
import logging

log = logging.getLogger(__name__)
load_dotenv()

INJURY_CHANNEL_ID = int(os.getenv("INJURY_CHANNEL_ID") or 0)

_TRACKED_BOOKS = {"draftkings", "fanduel", "betmgm", "caesars", "kalshi"}

# Noise control: alerts older than this are backfill — mark-notified silently, don't post.
_FRESH_HOURS = 6
# Low-signal statuses nobody acts on — skip (esp. "questionable").
_DROP_STATUSES = {"questionable", "probable", "available", "game time decision"}
# If books disagree on the ML by more than this (implied prob), the matched game /
# snapshot is suspect — omit the ML block rather than print garbage.
_ML_DISAGREE_MAX = 0.30


# ── Formatting ────────────────────────────────────────────────────────────────

def _fmt_odds(odds: int) -> str:
    return fmt_prob(odds)


def _fmt_game_time(utc_iso: str) -> str:
    dt = datetime.fromisoformat(utc_iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(_ET)
    h = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{h}:{dt.strftime('%M')} {ampm} {dt.strftime('%Z')}"


def _status_color(status: str) -> int:
    if status.lower() == "out":
        return 0xE74C3C   # red
    return 0x3498DB        # blue — fallback


def _build_injury_embed(
    alert: InjuryAlert,
    game: Game | None,
    snapshots: list[OddsSnapshot],
) -> discord.Embed:
    embed = discord.Embed(
        title=f"Injury Update — {alert.player_name}",
        color=_status_color(alert.status),
    )

    # Status change
    if alert.prev_status:
        status_str = f"**{alert.prev_status}** → **{alert.status}**"
    else:
        status_str = f"**{alert.status}** *(new listing)*"
    embed.add_field(name="Status", value=status_str, inline=True)

    # Injury detail
    if alert.detail:
        embed.add_field(name="Injury", value=alert.detail, inline=True)

    # Game context
    if game:
        game_str = (
            f"{game.away_team} @ {game.home_team}"
            f" — {_fmt_game_time(game.start_time_utc_iso)}"
        )
        embed.add_field(name="Today's Game", value=game_str, inline=False)

        # ML lines (refreshed post-injury) — filter to tracked books with ML data
        ml_lines = []
        home_probs = []  # to sanity-check cross-book agreement
        for snap in snapshots:
            if snap.source not in _TRACKED_BOOKS:
                continue
            ml_home = snap.payload.get("ml_home")
            ml_away = snap.payload.get("ml_away")
            if ml_home is None and ml_away is None:
                continue
            away_short = game.away_team.split()[-1]
            home_short = game.home_team.split()[-1]
            away_str = _fmt_odds(ml_away) if ml_away is not None else "n/a"
            home_str = _fmt_odds(ml_home) if ml_home is not None else "n/a"
            source_label = snap.source.replace("_", " ").title()
            ml_lines.append(f"**{source_label}**: {away_short} {away_str} / {home_short} {home_str}")
            if ml_home is not None:
                try:
                    home_probs.append(american_to_prob(ml_home))
                except Exception:
                    pass

        # If books wildly disagree, the snapshot is stale / wrong game — drop the ML block.
        if home_probs and (max(home_probs) - min(home_probs)) > _ML_DISAGREE_MAX:
            ml_lines = []

        if ml_lines:
            embed.add_field(
                name="Current ML (refreshed)",
                value="\n".join(ml_lines),
                inline=False,
            )

    updated_dt = datetime.fromisoformat(alert.updated_at_utc_iso)
    if updated_dt.tzinfo is None:
        updated_dt = updated_dt.replace(tzinfo=timezone.utc)
    updated_dt = updated_dt.astimezone(_ET)
    uh = updated_dt.hour % 12 or 12
    uampm = "AM" if updated_dt.hour < 12 else "PM"
    embed.set_footer(text=f"ESPN • {uh}:{updated_dt.strftime('%M')} {uampm} {updated_dt.strftime('%Z')}")

    return embed


# ── Cog ───────────────────────────────────────────────────────────────────────

class InjuryCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.injury_check.start()

    def cog_unload(self) -> None:
        self.injury_check.cancel()

    @tasks.loop(minutes=1)
    async def injury_check(self) -> None:
        try:
            channel = self.bot.get_channel(INJURY_CHANNEL_ID)
            if channel is None:
                return

            now = datetime.now(timezone.utc)
            alerts = await queries.get_unnotified_injuries()
            for alert in alerts:
                # Always mark notified first (crash between mark+send → a missed post,
                # which is recoverable; a duplicate is not).
                await queries.mark_injury_notified(alert.record_id)

                # Noise filters — these consume the alert silently (no post):
                upd = datetime.fromisoformat(alert.updated_at_utc_iso)
                if upd.tzinfo is None:
                    upd = upd.replace(tzinfo=timezone.utc)
                if (now - upd) > timedelta(hours=_FRESH_HOURS):
                    continue  # backfill / stale — don't dump it
                if alert.status.lower() in _DROP_STATUSES:
                    continue  # nobody acts on questionable/probable
                game = await queries.get_next_game_for_team(alert.team)
                if game is None:
                    continue  # team isn't playing soon — irrelevant

                snapshots = await queries.get_latest_snapshots_for_game(game.game_id)
                embed = _build_injury_embed(alert, game, snapshots)
                await channel.send(embed=embed)
        except Exception:
            log.exception("Unhandled error in injury_check loop")

    @injury_check.before_loop
    async def before_injury_check(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InjuryCog(bot))
