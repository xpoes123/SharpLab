"""Injury alert auto-post — polls DB for unnotified injury changes and posts to Discord."""
from __future__ import annotations

import os
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

from db import queries
from shared.models import Game, InjuryAlert, OddsSnapshot
from shared.odds_utils import fmt_prob

load_dotenv()

INJURY_CHANNEL_ID = int(os.getenv("CLV_CHANNEL_ID", "1485475287054418151"))

_TRACKED_BOOKS = {"draftkings", "fanduel", "betmgm", "caesars", "kalshi"}


# ── Formatting ────────────────────────────────────────────────────────────────

def _fmt_odds(odds: int) -> str:
    return fmt_prob(odds)


def _fmt_game_time(utc_iso: str) -> str:
    dt = datetime.fromisoformat(utc_iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%H:%M UTC")


def _status_color(status: str) -> int:
    s = status.lower()
    if s in ("out", "doubtful"):
        return 0xE74C3C   # red
    if s in ("questionable", "day-to-day"):
        return 0xF1C40F   # yellow
    return 0x3498DB        # blue — probable / other


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

        if ml_lines:
            embed.add_field(
                name="Current ML (refreshed)",
                value="\n".join(ml_lines),
                inline=False,
            )

    updated_dt = datetime.fromisoformat(alert.updated_at_utc_iso)
    if updated_dt.tzinfo is None:
        updated_dt = updated_dt.replace(tzinfo=timezone.utc)
    embed.set_footer(text=f"ESPN • {updated_dt.strftime('%H:%M UTC')}")

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
        channel = self.bot.get_channel(INJURY_CHANNEL_ID)
        if channel is None:
            return

        alerts = await queries.get_unnotified_injuries()
        for alert in alerts:
            game = await queries.get_todays_game_for_team(alert.team)
            snapshots = (
                await queries.get_latest_snapshots_for_game(game.game_id)
                if game else []
            )
            embed = _build_injury_embed(alert, game, snapshots)
            await channel.send(embed=embed)
            await queries.mark_injury_notified(alert.record_id)

    @injury_check.before_loop
    async def before_injury_check(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InjuryCog(bot))
