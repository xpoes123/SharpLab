"""Live in-play Kalshi swing alerts (per game, not per bet).

The odds pipeline stops polling a game the moment it tips off — that keeps the
closing-line history clean but means nothing watches a live game's market. This
cog fills the gap: every ~45s it pulls the current Kalshi moneyline for every
in-progress NBA/MLB game and posts an alert when a side's live win probability
swings ≥ MOVE_CENTS within WINDOW_SECONDS. It never writes odds_snapshots, so it
can't pollute CLV data.

Velocity-based (one fast-moving market), unlike /signals steam which needs
multi-book consensus. Detection math is shared.signals.detect_live_swing.
Batches one Kalshi call per sport per tick (same helpers as livebets.py).
"""
from __future__ import annotations

import logging
import time

import discord
import httpx
from discord import app_commands
from discord.ext import commands, tasks

from db import queries
from shared import signals as sig
from shared.models import get_kalshi_code
from .markets import _kalshi_markets, _matches_game, _et_date_code, KALSHI_DERIV
from .livebets import _kalshi_mid

log = logging.getLogger(__name__)

POLL_SECONDS = 45          # two polls span the 90s window
WINDOW_SECONDS = 90        # look-back for a "rapid" move
MOVE_CENTS = 15            # min swing in implied-prob cents to alert
ALERT_TTL_SEC = 180        # at most one alert per game per this period
SPORTS = ("nba", "mlb")

DEFAULT_CHANNEL_ID = 1510752551014760658  # signals channel; override via setting
_CHANNEL_SETTING = "live_kalshi_channel_id"
_ROLE_SETTING = "live_kalshi_role_id"


class LiveKalshi(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # game_id -> ascending list[(monotonic_ts, home_cents)] within the window
        self._hist: dict[str, list[tuple[float, float]]] = {}
        self._alerted: dict[str, float] = {}  # game_id -> last-alert monotonic ts
        self.watch_loop.start()

    def cog_unload(self) -> None:
        self.watch_loop.cancel()

    group = app_commands.Group(name="livekalshi", description="Live in-play Kalshi swing alerts")

    # ── config ───────────────────────────────────────────────────────────────
    async def _channel_id(self) -> int:
        raw = await queries.get_bot_setting(_CHANNEL_SETTING)
        try:
            return int(raw) if raw else DEFAULT_CHANNEL_ID
        except ValueError:
            return DEFAULT_CHANNEL_ID

    @group.command(name="channel", description="Set the channel for live Kalshi swing alerts")
    async def set_channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("You need **Manage Server**.", ephemeral=True)
            return
        await queries.set_bot_setting(_CHANNEL_SETTING, str(channel.id))
        await interaction.response.send_message(f"✅ Live Kalshi swings will post in {channel.mention}.", ephemeral=True)

    @group.command(name="role", description="Set (or clear) a role to ping on live Kalshi swings")
    async def set_role(self, interaction: discord.Interaction, role: discord.Role | None = None) -> None:
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("You need **Manage Server**.", ephemeral=True)
            return
        await queries.set_bot_setting(_ROLE_SETTING, str(role.id) if role else "")
        msg = f"✅ Will ping {role.mention} on live swings." if role else "✅ Cleared the live-swing ping role."
        await interaction.response.send_message(msg, ephemeral=True)

    # ── watch loop ─────────────────────────────────────────────────────────────
    @tasks.loop(seconds=POLL_SECONDS)
    async def watch_loop(self) -> None:
        try:
            await self._tick()
        except Exception:
            log.exception("livekalshi watch_loop failed")

    @watch_loop.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()

    def _home_prob(self, g, ml_markets: list[dict]) -> float | None:
        """Live home win probability (0–1) for game `g` from the ML markets."""
        h = get_kalshi_code(g.home_team, g.sport)
        a = get_kalshi_code(g.away_team, g.sport)
        if not h or not a:
            return None
        date = _et_date_code(g.start_time_utc_iso)
        et = next((m.get("event_ticker") for m in ml_markets
                   if _matches_game(m.get("event_ticker", ""), h, a, date)), None)
        if not et:
            return None
        for m in ml_markets:
            if m.get("event_ticker") == et and m.get("ticker", "").split("-")[-1].upper() == h:
                return _kalshi_mid(m)
        return None

    async def _tick(self) -> None:
        now = time.monotonic()
        live_by_sport: dict[str, list] = {}
        for sport in SPORTS:
            gs = await queries.get_live_games(sport)
            if gs:
                live_by_sport[sport] = gs

        # Drop history for games that are no longer live.
        live_ids = {g.game_id for gs in live_by_sport.values() for g in gs}
        self._hist = {gid: h for gid, h in self._hist.items() if gid in live_ids}
        if not live_by_sport:
            return

        async with httpx.AsyncClient() as client:
            for sport, games in live_by_sport.items():
                series = KALSHI_DERIV.get(sport, {}).get("ml")
                if not series:
                    continue
                try:
                    ml = await _kalshi_markets(client, series)
                except Exception:
                    log.debug("livekalshi: kalshi fetch failed for %s", sport, exc_info=True)
                    continue
                if not ml:
                    continue
                for g in games:
                    prob = self._home_prob(g, ml)
                    if prob is None:
                        continue
                    hist = self._hist.setdefault(g.game_id, [])
                    hist.append((now, prob * 100))
                    cut = now - WINDOW_SECONDS
                    while hist and hist[0][0] < cut:
                        hist.pop(0)
                    swing = sig.detect_live_swing(hist, now, WINDOW_SECONDS, MOVE_CENTS)
                    if swing and self._fresh(g.game_id):
                        await self._post(g, swing)

    def _fresh(self, game_id: str) -> bool:
        now = time.monotonic()
        self._alerted = {k: t for k, t in self._alerted.items() if now - t < ALERT_TTL_SEC}
        if game_id in self._alerted:
            return False
        self._alerted[game_id] = now
        return True

    async def _post(self, g, swing: tuple[float, float, float]) -> None:
        old, new, delta = swing
        toward = g.home_team if delta > 0 else g.away_team
        emoji = "🏀" if g.sport == "nba" else "⚾"
        role_raw = (await queries.get_bot_setting(_ROLE_SETTING)) or ""
        content = f"<@&{role_raw}>" if role_raw.isdigit() else None
        e = discord.Embed(
            title=f"⚡ Live Kalshi swing — {g.away_team} @ {g.home_team}",
            description=(f"{emoji} **{toward}** firmed **{abs(delta):.0f}¢** in the last ≤{WINDOW_SECONDS}s on Kalshi.\n"
                         f"{g.home_team} live win prob: **{old:.0f}% → {new:.0f}%**"),
            color=0xf7768e,
        )
        e.set_footer(text="¢ = implied win probability · live Kalshi mid · in-play moves fast — verify before betting.")
        try:
            cid = await self._channel_id()
            ch = self.bot.get_channel(cid) or await self.bot.fetch_channel(cid)
            await ch.send(content=content, embed=e,
                          allowed_mentions=discord.AllowedMentions(roles=True))
        except Exception:
            log.debug("livekalshi: alert send failed", exc_info=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LiveKalshi(bot))
