"""Daily Games — Discord surface.

Posts the daily rally once a day (default 7pm ET) to the daily channel, pings the @Daily role,
and opens a discussion thread. A poller announces each player's result into that day's thread as
they finish on the web. A 4am job settles the previous day's placement coins.

The post uses a "catch-up" loop (not a fixed-time task) so it survives restarts and fires
immediately if the bot comes up after post-time with today's post still owed. All scheduling is in
America/New_York to match pick'em.
"""

from __future__ import annotations

import logging
from datetime import datetime
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
POST_HOUR = 19          # 7pm ET rally
PAYOUT_HOUR = 4         # settle yesterday's coins after the 4am rollover

_CHANNEL_KEY = "daily_channel"
_ROLE_KEY = "daily_role"
_LAST_POST_KEY = "daily_last_post"


def _now_et() -> datetime:
    return datetime.now(ET)


async def _setting(key: str, default: str) -> str:
    return (await queries.get_bot_setting(key)) or default


class Daily(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.post_loop.start()
        self.results_loop.start()
        self.payout_loop.start()

    async def cog_unload(self) -> None:
        self.post_loop.cancel()
        self.results_loop.cancel()
        self.payout_loop.cancel()

    # ── helpers ──────────────────────────────────────────────────────────────
    async def _channel(self) -> discord.TextChannel | None:
        cid = await _setting(_CHANNEL_KEY, CHANNEL_DEFAULT)
        ch = self.bot.get_channel(int(cid))
        return ch if isinstance(ch, discord.TextChannel) else None

    async def _thread(self, day: str) -> discord.Thread | None:
        raw = await queries.get_bot_setting(f"daily_thread:{day}")
        if not raw:
            return None
        ch = self.bot.get_channel(int(raw))
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(int(raw))
            except discord.HTTPException:
                return None
        return ch if isinstance(ch, discord.Thread) else None

    async def _name(self, uid: str) -> str:
        try:
            return (await self.bot.fetch_user(int(uid))).display_name
        except (discord.HTTPException, ValueError):
            return f"Player {uid[:6]}"

    def _post_embed(self, puz: dict, number: int, winner: str | None) -> discord.Embed:
        game = daily.DAILY_GAMES[puz["game_id"]]
        e = discord.Embed(
            title=f"🧩 Daily #{number} — {game.NAME}",
            description=(f"Today's **{puz['difficulty']}** puzzle is live — same board for "
                        f"everyone, fastest solve wins.\n\n**[▶ Play today's daily]({SITE})**"),
            colour=0xBB9AF7,
        )
        e.add_field(name="Par", value=f"~{puz['par']}", inline=True)
        e.add_field(name="Difficulty", value=puz["difficulty"].title(), inline=True)
        if winner:
            e.add_field(name="Yesterday's winner", value=winner, inline=False)
        e.set_footer(text="🔥 Keep your streak • coins for playing + the podium")
        return e

    # ── daily rally post (catch-up loop) ─────────────────────────────────────
    @tasks.loop(minutes=5)
    async def post_loop(self) -> None:
        now = _now_et()
        day = daily.puzzle_day()
        if now.hour < POST_HOUR:                       # not time yet today
            return
        if await queries.get_bot_setting(_LAST_POST_KEY) == day:   # already posted today
            return
        await self._post_rally(day)

    async def _post_rally(self, day: str) -> bool:
        """Do the actual rally post. Returns True on success. Callable by the admin command to
        force a post outside the scheduled window."""
        channel = await self._channel()
        if channel is None:
            log.warning("daily: channel not found")
            return False
        try:
            puz = await queries.get_or_create_daily_puzzle(day)
            number = daily.puzzle_number(day)
            winner = await self._winner_line(daily.schedule(day)[0],
                                             self._prev_day(day))
            role_id = await _setting(_ROLE_KEY, ROLE_DEFAULT)
            msg = await channel.send(content=f"<@&{role_id}>",
                                     embed=self._post_embed(puz, number, winner),
                                     allowed_mentions=discord.AllowedMentions(roles=True))
            try:
                thread = await msg.create_thread(name=f"Daily #{number} · {daily.DAILY_GAMES[puz['game_id']].NAME}")
                await queries.set_bot_setting(f"daily_thread:{day}", str(thread.id))
            except discord.HTTPException:
                log.exception("daily: could not create thread")
            await queries.set_bot_setting(_LAST_POST_KEY, day)
            log.info("daily: posted rally for %s (#%s)", day, number)
            return True
        except Exception:
            log.exception("daily: rally post failed")
            return False

    @post_loop.before_loop
    async def _before_post(self) -> None:
        await self.bot.wait_until_ready()

    @staticmethod
    def _prev_day(day: str) -> str:
        from datetime import date, timedelta
        return (date.fromisoformat(day) - timedelta(days=1)).isoformat()

    async def _winner_line(self, game_id: str, day: str) -> str | None:
        rows = await queries.get_daily_results(game_id, day)
        ranked = daily.rank_results(rows, game_id)
        if not ranked or not ranked[0]["solved"]:
            return None
        top = ranked[0]
        secs = top["secondary_score"] // 1000
        return f"🏆 **{await self._name(top['discord_user'])}** — {secs // 60}:{secs % 60:02d} · {top['primary_score']} fences"

    # ── results poller ───────────────────────────────────────────────────────
    @tasks.loop(seconds=30)
    async def results_loop(self) -> None:
        day = daily.puzzle_day()
        thread = await self._thread(day)
        if thread is None:
            return
        try:
            game_id = daily.schedule(day)[0]
            fresh = await queries.get_unposted_daily_results(day)
            if not fresh:
                return
            ranked = {r["discord_user"]: r for r in daily.rank_results(
                await queries.get_daily_results(game_id, day), game_id)}
            for r in fresh:
                if not r["solved"]:
                    await queries.mark_daily_result_posted(r["game_id"], day, r["discord_user"])
                    continue
                rk = ranked.get(r["discord_user"], {}).get("rank", "?")
                secs = r["secondary_score"] // 1000
                await thread.send(
                    f"🎉 **{await self._name(r['discord_user'])}** trapped it in "
                    f"{secs // 60}:{secs % 60:02d} · {r['primary_score']} fences · rank #{rk}")
                await queries.mark_daily_result_posted(r["game_id"], day, r["discord_user"])
        except Exception:
            log.exception("daily: results poller failed")

    @results_loop.before_loop
    async def _before_results(self) -> None:
        await self.bot.wait_until_ready()

    # ── placement payout (runs after the 4am rollover) ───────────────────────
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
    async def _before_payout(self) -> None:
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
        thread = await self._thread(day)
        if thread and lines:
            e = discord.Embed(title=f"🏁 Daily #{daily.puzzle_number(day)} — final",
                              description="\n".join(lines), colour=0x9ECE6A)
            try:
                await thread.send(embed=e)
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
                await interaction.response.send_message("🔔 You'll be pinged when each daily drops.", ephemeral=True)
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
        lines = []
        for r in ranked[:15]:
            secs = r["secondary_score"] // 1000
            tag = "✅" if r["solved"] else "❌"
            lines.append(f"`#{r['rank']}` {tag} **{await self._name(r['discord_user'])}** — "
                         f"{secs // 60}:{secs % 60:02d} · {r['primary_score']} fences")
        e = discord.Embed(title=f"🧩 Daily #{daily.puzzle_number(day)} — today",
                          description="\n".join(lines), colour=0xBB9AF7)
        await interaction.followup.send(embed=e, ephemeral=True)

    @group.command(name="post", description="Force today's daily rally now (admin)")
    async def post_now(self, interaction: discord.Interaction) -> None:
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("You need **Manage Server**.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        ok = await self._post_rally(daily.puzzle_day())   # force, ignores the 7pm guard
        await interaction.followup.send("✅ Posted." if ok else "❌ Couldn't post — check the channel/perms.",
                                        ephemeral=True)

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
