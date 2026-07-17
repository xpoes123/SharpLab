"""Weekly SharpLab recap — a Saturday-morning digest of the trailing 7 days:
betting (record / CLV / ROI + best & worst bet), stock P/L, and casino net.

Built entirely from data already stored (bets, portfolio_snapshots, casino_history)
— no schema changes. Posts to the stock-alerts channel by default; `/recap channel`
overrides it. A `last_weekly_recap` bot_setting guards against a double-post.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from db import queries

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
POST_TIME = time(hour=10, minute=0, tzinfo=ET)   # Saturday ~10am ET
RECAP_WEEKDAY = 5                                 # Monday=0 … Saturday=5
DEFAULT_RECAP_CHANNEL_ID = 1510100250411536495   # stock-alerts channel
_CHANNEL_SETTING = "recap_channel"
_LAST_SETTING = "last_weekly_recap"


def _american_profit(odds: int, units: float, status: str) -> float:
    """Profit in units for a settled bet (won → winnings, lost → −stake, push → 0)."""
    if status == "won":
        return units * (odds / 100.0 if odds > 0 else 100.0 / abs(odds))
    if status == "lost":
        return -units
    return 0.0


def _fmt_u(u: float) -> str:
    return f"{'+' if u >= 0 else ''}{u:.2f}u"


class WeeklyRecapCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._names: dict[str, str] = {}

    async def cog_load(self) -> None:
        self.recap_loop.start()

    async def cog_unload(self) -> None:
        self.recap_loop.cancel()

    async def _channel_id(self) -> int:
        raw = await queries.get_bot_setting(_CHANNEL_SETTING)
        try:
            return int(raw) if raw else DEFAULT_RECAP_CHANNEL_ID
        except ValueError:
            return DEFAULT_RECAP_CHANNEL_ID

    async def _name(self, uid: str) -> str:
        if uid not in self._names:
            try:
                self._names[uid] = (await self.bot.fetch_user(int(uid))).display_name
            except Exception:
                self._names[uid] = f"User {uid[:6]}"
        return self._names[uid]

    # ── Digest builder ───────────────────────────────────────────────────────

    async def _build_embed(self) -> discord.Embed | None:
        now = datetime.now(timezone.utc)
        since_iso = (now - timedelta(days=7)).isoformat()
        embed = discord.Embed(
            title="🏆 SharpLab Weekly Recap",
            description=f"The last 7 days · {(now - timedelta(days=7)).strftime('%b %d')} – {now.strftime('%b %d')}",
            colour=0x7AA2F7,
        )
        has_content = False

        # 🎯 Betting
        bets = await queries.get_all_settled_bets(since_iso)
        if bets:
            has_content = True
            won = sum(1 for b in bets if b.status == "won")
            lost = sum(1 for b in bets if b.status == "lost")
            push = sum(1 for b in bets if b.status == "push")
            clvs = [b.clv for b in bets if b.clv is not None]
            avg_clv = sum(clvs) / len(clvs) if clvs else None
            profits = [(_american_profit(b.odds, b.units, b.status), b) for b in bets]
            risked = sum(b.units for b in bets if b.status in ("won", "lost"))
            net = sum(p for p, _ in profits)
            roi = (net / risked * 100) if risked else 0.0
            best_p, best_b = max(profits, key=lambda t: t[0])
            worst_p, worst_b = min(profits, key=lambda t: t[0])
            lines = [
                f"Record **{won}-{lost}-{push}** · ROI `{'+' if roi >= 0 else ''}{roi:.1f}%` · net `{_fmt_u(net)}`",
                f"Avg CLV `{'+' if (avg_clv or 0) >= 0 else ''}{avg_clv:.1f}pp`" if avg_clv is not None else "Avg CLV `—`",
                f"🟢 Best: {await self._name(best_b.discord_user)} · {best_b.side} {best_b.market} `{_fmt_u(best_p)}`",
                f"🔴 Worst: {await self._name(worst_b.discord_user)} · {worst_b.side} {worst_b.market} `{_fmt_u(worst_p)}`",
            ]
            embed.add_field(name="🎯 Betting", value="\n".join(lines), inline=False)

        # 📈 Stocks (snapshot delta: latest vs ~7 days ago)
        base = await queries.get_all_snapshot_values_asof(since_iso)
        current = await queries.get_all_snapshot_values_asof(now.isoformat())
        deltas = sorted(
            ((uid, current[uid] - base[uid]) for uid in base if uid in current),
            key=lambda t: t[1], reverse=True,
        )
        if deltas:
            has_content = True
            server_net = sum(d for _, d in deltas)
            top = deltas[:3]
            lines = [f"Server net `{'+' if server_net >= 0 else ''}${server_net:,.0f}`"]
            for uid, d in top:
                lines.append(f"• {await self._name(uid)} `{'+' if d >= 0 else ''}${d:,.0f}`")
            embed.add_field(name="📈 Stocks", value="\n".join(lines), inline=False)

        # 🎰 Casino
        casino = await queries.get_casino_net_since(since_iso)
        if casino:
            has_content = True
            total_wagered = sum(r["wagered"] for r in casino)
            server_net = sum(r["net"] for r in casino)
            top = casino[0]  # already ordered by net desc
            lines = [
                f"Wagered `{total_wagered:,.0f}` · server net `{'+' if server_net >= 0 else ''}{server_net:,.0f}`",
                f"👑 Biggest winner: {await self._name(top['discord_user'])} `{'+' if top['net'] >= 0 else ''}{top['net']:,.0f}`",
            ]
            embed.add_field(name="🎰 Casino", value="\n".join(lines), inline=False)

        if not has_content:
            return None
        embed.set_footer(text="Trailing 7 days · SharpLab")
        return embed

    async def _post(self, channel: discord.abc.Messageable) -> bool:
        embed = await self._build_embed()
        if embed is None:
            return False
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        return True

    # ── Scheduled loop ───────────────────────────────────────────────────────

    @tasks.loop(time=POST_TIME)
    async def recap_loop(self) -> None:
        try:
            now_et = datetime.now(ET)
            if now_et.weekday() != RECAP_WEEKDAY:
                return
            today = now_et.date().isoformat()
            if await queries.get_bot_setting(_LAST_SETTING) == today:
                return
            channel = self.bot.get_channel(await self._channel_id())
            if channel is None:
                return
            await self._post(channel)
            await queries.set_bot_setting(_LAST_SETTING, today)
        except Exception:
            log.exception("weekly recap failed")

    @recap_loop.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()

    # ── Admin commands ───────────────────────────────────────────────────────

    recap = app_commands.Group(
        name="recap", description="Weekly SharpLab recap",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @recap.command(name="channel", description="Set the channel the weekly recap posts in")
    async def channel_cmd(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await queries.set_bot_setting(_CHANNEL_SETTING, str(channel.id))
        await interaction.response.send_message(
            f"✅ Weekly recap will post in {channel.mention} (Saturdays ~10am ET).", ephemeral=True)

    @recap.command(name="now", description="Post the weekly recap right now (for the current channel)")
    async def now_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        posted = await self._post(interaction.channel)
        await interaction.followup.send(
            "Posted." if posted else "No activity in the last 7 days to recap.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WeeklyRecapCog(bot))
