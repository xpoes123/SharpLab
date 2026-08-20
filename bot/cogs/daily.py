"""Daily Games — Discord surface (channel feed, no threads).

- **4am ET** (rollover): post "today's puzzle is live" to the daily channel — NO ping.
- **7pm ET**: ping the @Daily role with a reminder + current standings.
- **Per play**: every solve posts a new message in the channel, so the channel itself reads like a
  live leaderboard.
- **After 4am**: settle the previous day's placement coins and post the final standings.

All posts go straight to the channel (no discussion thread). Catch-up loops (not fixed-time tasks)
so a restart never misses the 4am post or 7pm ping. Times are America/New_York (matches pick'em).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from db import queries
from shared import daily

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
SITE = "https://sharplab.djiang.xyz/daily"

CHANNEL_DEFAULT = "1539498305162051654"
ROLE_DEFAULT = "1539504644697493524"
PING_HOUR = 19          # 7pm ET — the @Daily reminder ping
PAYOUT_HOUR = 4         # settle yesterday's coins after the 4am rollover

_CHANNEL_KEY = "daily_channel"
_ROLE_KEY = "daily_role"
_LAST_POST_KEY = "daily_last_post"   # date we've announced "live"
_LAST_PING_KEY = "daily_last_ping"   # date we've pinged @Daily


def _now_et() -> datetime:
    return datetime.now(ET)


async def _setting(key: str, default: str) -> str:
    return (await queries.get_bot_setting(key)) or default


def _fmt_ms(ms: int) -> str:
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


class Daily(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.announce_loop.start()
        self.ping_loop.start()
        self.results_loop.start()
        self.payout_loop.start()

    async def cog_unload(self) -> None:
        self.announce_loop.cancel()
        self.ping_loop.cancel()
        self.results_loop.cancel()
        self.payout_loop.cancel()

    # ── helpers ──────────────────────────────────────────────────────────────
    async def _channel(self) -> discord.TextChannel | None:
        cid = await _setting(_CHANNEL_KEY, CHANNEL_DEFAULT)
        ch = self.bot.get_channel(int(cid))
        return ch if isinstance(ch, discord.TextChannel) else None

    async def _name(self, uid: str) -> str:
        try:
            return (await self.bot.fetch_user(int(uid))).display_name
        except (discord.HTTPException, ValueError):
            return f"Player {uid[:6]}"

    @staticmethod
    def _prev_day(day: str) -> str:
        return (date.fromisoformat(day) - timedelta(days=1)).isoformat()

    async def _standings(self, game_id: str, day: str, n: int = 3) -> list[str]:
        ranked = daily.rank_results(await queries.get_daily_results(game_id, day), game_id)
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        out = []
        for r in ranked[:n]:
            if not r["solved"]:
                break
            medal = medals.get(r["rank"], f"#{r['rank']}")
            name = await self._name(r["discord_user"])
            out.append(f"{medal} **{name}** — {_fmt_ms(r['secondary_score'])}")
        return out

    # ── 4am: "today's puzzle is live" (no ping) ──────────────────────────────
    @tasks.loop(minutes=5)
    async def announce_loop(self) -> None:
        day = daily.puzzle_day()
        if await queries.get_bot_setting(_LAST_POST_KEY) == day:
            return
        channel = await self._channel()
        if channel is None:
            log.warning("daily: channel not found")
            return
        try:
            puz = await queries.get_or_create_daily_puzzle(day)
            game = daily.DAILY_GAMES[puz["game_id"]]
            number = daily.puzzle_number(day)
            win = await self._standings(daily.schedule(self._prev_day(day))[0], self._prev_day(day), 1)
            e = discord.Embed(
                title=f"🧩 Daily #{number} — {game.NAME} is live",
                description=(f"Today's **{puz['difficulty']}** puzzle — same board for everyone, "
                            f"fastest solve wins.\n\n**[▶ Play]({SITE})**"),
                colour=0xBB9AF7)
            if win:
                e.add_field(name="Yesterday's winner", value=win[0], inline=False)
            e.set_footer(text="🔔 grab the @Daily role in #roles to get pinged at 7pm")
            await channel.send(embed=e)   # no ping, no thread
            await queries.set_bot_setting(_LAST_POST_KEY, day)
            log.info("daily: announced #%s for %s", number, day)
        except Exception:
            log.exception("daily: announce failed")

    @announce_loop.before_loop
    async def _b1(self) -> None:
        await self.bot.wait_until_ready()

    # ── 7pm: ping @Daily with current standings ──────────────────────────────
    @tasks.loop(minutes=5)
    async def ping_loop(self) -> None:
        if _now_et().hour < PING_HOUR:
            return
        day = daily.puzzle_day()
        if await queries.get_bot_setting(_LAST_PING_KEY) == day:
            return
        channel = await self._channel()
        if channel is None:
            return
        try:
            puz = await queries.get_or_create_daily_puzzle(day)
            game = daily.DAILY_GAMES[puz["game_id"]]
            number = daily.puzzle_number(day)
            role_id = await _setting(_ROLE_KEY, ROLE_DEFAULT)
            standings = await self._standings(puz["game_id"], day, 3)
            e = discord.Embed(
                title=f"🔔 Last call — Daily #{number} ({game.NAME})",
                description=(f"Rolls over at 4am ET. Beat the board 👉 **[Play]({SITE})**"),
                colour=0xE0AF68)
            e.add_field(name="Current standings",
                        value="\n".join(standings) if standings else "_nobody's solved it yet — go!_",
                        inline=False)
            await channel.send(content=f"<@&{role_id}>", embed=e,
                               allowed_mentions=discord.AllowedMentions(roles=True))
            await queries.set_bot_setting(_LAST_PING_KEY, day)
            log.info("daily: pinged @Daily for #%s", number)
        except Exception:
            log.exception("daily: ping failed")

    @ping_loop.before_loop
    async def _b2(self) -> None:
        await self.bot.wait_until_ready()

    # ── per-play results, posted to the CHANNEL (running leaderboard) ─────────
    @tasks.loop(seconds=10)
    async def results_loop(self) -> None:
        day = daily.puzzle_day()
        channel = await self._channel()
        if channel is None:
            return
        try:
            game_id = daily.schedule(day)[0]
            number = daily.puzzle_number(day)
            fresh = await queries.get_unposted_daily_results(day)
            if not fresh:
                return
            ranked = {r["discord_user"]: r for r in daily.rank_results(
                await queries.get_daily_results(game_id, day), game_id)}
            for r in fresh:
                # Claim BEFORE sending — atomically flip posted 0→1. If a second (overlapping)
                # instance already claimed it, we skip, so it's never posted twice.
                if not await queries.claim_daily_result_post(r["game_id"], day, r["discord_user"]):
                    continue
                if not r["solved"]:
                    continue
                rk = ranked.get(r["discord_user"], {}).get("rank")
                name = await self._name(r["discord_user"])
                t = _fmt_ms(r["secondary_score"])
                if rk == 1:
                    body = f"🏆 **{name}** took the lead on Daily #{number} — {t}!"
                else:
                    body = f"🎉 **{name}** solved Daily #{number} — {t} · rank #{rk}"
                await channel.send(body)
        except Exception:
            log.exception("daily: results poller failed")

    @results_loop.before_loop
    async def _b3(self) -> None:
        await self.bot.wait_until_ready()

    # ── placement payout after 4am ───────────────────────────────────────────
    @tasks.loop(minutes=30)
    async def payout_loop(self) -> None:
        if _now_et().hour < PAYOUT_HOUR:
            return
        today = daily.puzzle_day()
        try:
            for row in await queries.get_unawarded_daily_days(today):
                await self._settle_day(row["game_id"], row["puzzle_date"])
        except Exception:
            log.exception("daily: payout loop failed")

    @payout_loop.before_loop
    async def _b4(self) -> None:
        await self.bot.wait_until_ready()

    async def _settle_day(self, game_id: str, day: str) -> None:
        ranked = daily.rank_results(await queries.get_daily_results(game_id, day), game_id)
        lines = []
        for r in ranked:
            coins = daily.placement_coins(r["rank"], bool(r["solved"]))
            if coins:
                await queries.update_casino_balance(r["discord_user"], coins)
            if r["rank"] <= 3 and r["solved"]:
                medal = {1: "🥇", 2: "🥈", 3: "🥉"}[r["rank"]]
                lines.append(f"{medal} **{await self._name(r['discord_user'])}** +🪙{coins}")
        await queries.mark_daily_awarded(game_id, day)
        channel = await self._channel()
        if channel and lines:
            e = discord.Embed(title=f"🏁 Daily #{daily.puzzle_number(day)} — final results",
                              description="\n".join(lines), colour=0x9ECE6A)
            try:
                await channel.send(embed=e)
            except discord.HTTPException:
                pass
        log.info("daily: settled %s (%d players)", day, len(ranked))

    # ── commands ─────────────────────────────────────────────────────────────
    group = app_commands.Group(name="daily", description="Daily puzzle — play, rank, get pinged")

    @group.command(name="notify", description="Toggle the @Daily ping role for yourself")
    async def notify(self, interaction: discord.Interaction) -> None:
        role_id = int(await _setting(_ROLE_KEY, ROLE_DEFAULT))
        role = interaction.guild.get_role(role_id) if interaction.guild else None
        if role is None:
            await interaction.response.send_message("Daily role not set up.", ephemeral=True)
            return
        member = interaction.user
        try:
            if role in member.roles:
                await member.remove_roles(role, reason="daily notify toggle")
                await interaction.response.send_message("🔕 You'll no longer be pinged for the daily.", ephemeral=True)
            else:
                await member.add_roles(role, reason="daily notify toggle")
                await interaction.response.send_message("🔔 You'll be pinged when the daily's up.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("I can't manage that role (permission/hierarchy).", ephemeral=True)

    @group.command(name="leaderboard", description="Today's daily leaderboard")
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        day = daily.puzzle_day()
        game_id = daily.schedule(day)[0]
        ranked = daily.rank_results(await queries.get_daily_results(game_id, day), game_id)
        if not ranked:
            await interaction.followup.send("No one's played today's daily yet — be the first! " + SITE, ephemeral=True)
            return
        lines = [f"`#{r['rank']}` {'✅' if r['solved'] else '❌'} **{await self._name(r['discord_user'])}** "
                 f"— {_fmt_ms(r['secondary_score'])}" for r in ranked[:15]]
        e = discord.Embed(title=f"🧩 Daily #{daily.puzzle_number(day)} — today",
                          description="\n".join(lines), colour=0xBB9AF7)
        await interaction.followup.send(embed=e, ephemeral=True)

    @group.command(name="post", description="Force the 'daily is live' post now (admin)")
    async def post_now(self, interaction: discord.Interaction) -> None:
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("You need **Manage Server**.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await queries.set_bot_setting(_LAST_POST_KEY, "")   # force re-announce
        await self.announce_loop()
        await interaction.followup.send("Posted (if a channel is set).", ephemeral=True)

    @group.command(name="channel", description="Set the daily channel (admin)")
    async def set_channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("You need **Manage Server**.", ephemeral=True)
            return
        await queries.set_bot_setting(_CHANNEL_KEY, str(channel.id))
        await interaction.response.send_message(f"✅ Daily posts in {channel.mention}.", ephemeral=True)

    @group.command(name="role", description="Set the @Daily ping role (admin)")
    async def set_role(self, interaction: discord.Interaction, role: discord.Role) -> None:
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("You need **Manage Server**.", ephemeral=True)
            return
        await queries.set_bot_setting(_ROLE_KEY, str(role.id))
        await interaction.response.send_message(f"✅ Daily pings {role.mention}.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Daily(bot))
