"""Line movement alert auto-post — polls for significant line changes and posts to Discord."""
from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

from db import queries
from shared.models import Game, LineAlertThresholds, LineMovement

load_dotenv()

LINE_ALERT_CHANNEL_ID = int(
    os.getenv("LINE_ALERT_CHANNEL_ID", os.getenv("CLV_CHANNEL_ID", "0"))
)

THRESHOLDS = LineAlertThresholds()


# ── Movement detection ───────────────────────────────────────────────────────

async def detect_movements_for_game(game_id: str) -> list[LineMovement]:
    """Compare latest two poll snapshots per source and detect significant moves."""
    now_iso = datetime.now(timezone.utc).isoformat()
    movements: list[LineMovement] = []

    # Get all recent snapshots for this game, grouped by source
    snapshots = await queries.get_snapshots_for_game_since(game_id, "2000-01-01")
    if not snapshots:
        return movements

    # Group by source, ordered by captured_at (already ASC from query)
    by_source: dict[str, list] = defaultdict(list)
    for snap in snapshots:
        by_source[snap.source].append(snap)

    for source, snaps in by_source.items():
        if len(snaps) < 2:
            continue
        prev = snaps[-2]
        curr = snaps[-1]
        pp = prev.payload
        cp = curr.payload

        # Spread
        prev_spread = pp.get("spread")
        curr_spread = cp.get("spread")
        if prev_spread is not None and curr_spread is not None:
            delta = curr_spread - prev_spread
            if abs(delta) >= THRESHOLDS.spread_points:
                movements.append(LineMovement(
                    movement_id=None,
                    game_id=game_id,
                    source=source,
                    market="spread",
                    prev_value=prev_spread,
                    curr_value=curr_spread,
                    delta=delta,
                    detected_at_utc_iso=now_iso,
                ))

        # Total
        prev_total = pp.get("total")
        curr_total = cp.get("total")
        if prev_total is not None and curr_total is not None:
            delta = curr_total - prev_total
            if abs(delta) >= THRESHOLDS.total_points:
                movements.append(LineMovement(
                    movement_id=None,
                    game_id=game_id,
                    source=source,
                    market="total",
                    prev_value=prev_total,
                    curr_value=curr_total,
                    delta=delta,
                    detected_at_utc_iso=now_iso,
                ))

        # Moneyline (home)
        prev_ml = pp.get("ml_home")
        curr_ml = cp.get("ml_home")
        if prev_ml is not None and curr_ml is not None:
            delta = curr_ml - prev_ml
            if abs(delta) >= THRESHOLDS.moneyline_cents:
                movements.append(LineMovement(
                    movement_id=None,
                    game_id=game_id,
                    source=source,
                    market="moneyline",
                    prev_value=prev_ml,
                    curr_value=curr_ml,
                    delta=delta,
                    detected_at_utc_iso=now_iso,
                ))

    return movements


# ── Embed building ───────────────────────────────────────────────────────────

def _direction_arrow(delta: float) -> str:
    return "\U0001f4c8" if delta > 0 else "\U0001f4c9"  # 📈 / 📉


def _fmt_game_time(utc_iso: str) -> str:
    dt = datetime.fromisoformat(utc_iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%H:%M UTC")


def _fmt_movement(mov: LineMovement) -> str:
    """Format a single movement line for the embed field value."""
    source_label = mov.source.replace("_", " ").title()
    arrow = _direction_arrow(mov.delta)

    if mov.market == "spread":
        return (
            f"**{source_label}** {arrow}\n"
            f"{mov.prev_value:+.1f} \u2192 {mov.curr_value:+.1f} ({mov.delta:+.1f} pts)"
        )
    elif mov.market == "total":
        return (
            f"**{source_label}** {arrow}\n"
            f"{mov.prev_value:.1f} \u2192 {mov.curr_value:.1f} ({mov.delta:+.1f} pts)"
        )
    else:  # moneyline
        prev_str = f"{int(mov.prev_value):+d}" if mov.prev_value < 0 else f"+{int(mov.prev_value)}"
        curr_str = f"{int(mov.curr_value):+d}" if mov.curr_value < 0 else f"+{int(mov.curr_value)}"
        return (
            f"**{source_label}** {arrow}\n"
            f"{prev_str} \u2192 {curr_str}"
        )


def build_alert_embed(
    game: Game,
    movements: list[LineMovement],
) -> discord.Embed:
    """Build a Discord embed for a group of line movements on the same game."""
    embed = discord.Embed(
        title="\U0001f514 Line Movement Alert",  # 🔔
        description=f"{game.away_team} @ {game.home_team}",
        color=0xF39C12,  # orange
    )

    # Game time context
    embed.add_field(
        name="Game Time",
        value=_fmt_game_time(game.start_time_utc_iso),
        inline=True,
    )

    # Group movements by market for cleaner display
    by_market: dict[str, list[LineMovement]] = defaultdict(list)
    for mov in movements:
        by_market[mov.market].append(mov)

    market_labels = {"spread": "Spread", "total": "Total", "moneyline": "Moneyline"}
    for market in ("spread", "total", "moneyline"):
        if market not in by_market:
            continue
        label = market_labels[market]
        value = "\n".join(_fmt_movement(m) for m in by_market[market])
        embed.add_field(name=label, value=value, inline=False)

    # Footer with detection timestamp
    detected_dt = datetime.fromisoformat(movements[0].detected_at_utc_iso)
    if detected_dt.tzinfo is None:
        detected_dt = detected_dt.replace(tzinfo=timezone.utc)
    embed.set_footer(text=f"Detected {detected_dt.strftime('%H:%M UTC')}")

    return embed


# ── Core loop logic (extracted for testability) ──────────────────────────────

async def post_pending_alerts(channel) -> None:
    """Fetch unnotified movements, group by game, and post embeds."""
    movements = await queries.get_unnotified_line_movements()
    if not movements:
        return

    # Group by game_id for consolidated embeds
    by_game: dict[str, list[LineMovement]] = defaultdict(list)
    for mov in movements:
        by_game[mov.game_id].append(mov)

    for game_id, game_movements in by_game.items():
        game = await queries.get_game_by_id(game_id)
        if game is None:
            # Mark them notified anyway to avoid infinite retry
            for mov in game_movements:
                if mov.movement_id is not None:
                    await queries.mark_movement_notified(mov.movement_id)
            continue

        embed = build_alert_embed(game, game_movements)
        # Mark notified before sending (same pattern as CLV cog —
        # missed post is recoverable, duplicate post is not)
        for mov in game_movements:
            if mov.movement_id is not None:
                await queries.mark_movement_notified(mov.movement_id)
        await channel.send(embed=embed)


# ── Cog ───────────────────────────────────────────────────────────────────────

class LineAlertsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.channel_id = LINE_ALERT_CHANNEL_ID
        self.line_alert_check.start()

    def cog_unload(self) -> None:
        self.line_alert_check.cancel()

    @tasks.loop(minutes=1)
    async def line_alert_check(self) -> None:
        channel = self.bot.get_channel(self.channel_id)
        if channel is None:
            return
        await post_pending_alerts(channel)

    @line_alert_check.before_loop
    async def before_check(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="alert-thresholds",
        description="Show current line movement alert thresholds",
    )
    async def alert_thresholds(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="Line Movement Alert Thresholds",
            color=0xF39C12,
        )
        embed.add_field(
            name="Spread", value=f"\u00b1{THRESHOLDS.spread_points:.1f} pts", inline=True
        )
        embed.add_field(
            name="Total", value=f"\u00b1{THRESHOLDS.total_points:.1f} pts", inline=True
        )
        embed.add_field(
            name="Moneyline", value=f"\u00b1{THRESHOLDS.moneyline_cents} cents", inline=True
        )
        embed.set_footer(text="Alerts fire when any line moves beyond these thresholds")
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LineAlertsCog(bot))
