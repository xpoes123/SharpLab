"""/request — members pitch features, 👍 votes them up, and shipping one credits the requester by name.

/request new posts the idea publicly (with a 👍 to vote), /request list shows what's open by votes,
/request done (Manage Server) marks it shipped and replies to the original post pinging the requester.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from db import queries

log = logging.getLogger(__name__)
VOTE = "👍"


async def _votes(bot: commands.Bot, r: dict) -> int:
    """👍 count on the request's post, minus the bot's own seed reaction (0 if the post is gone)."""
    if not r.get("message_id"):
        return 0
    try:
        ch = bot.get_channel(int(r["channel_id"])) or await bot.fetch_channel(int(r["channel_id"]))
        msg = await ch.fetch_message(int(r["message_id"]))
    except discord.HTTPException:
        return 0
    return next((x.count - (1 if x.me else 0) for x in msg.reactions if str(x.emoji) == VOTE), 0)


class RequestsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    request = app_commands.Group(name="request", description="Pitch a feature for the bot/site — shipped ones credit you")

    @request.command(name="new", description="Pitch a feature or change — everyone can 👍 it")
    @app_commands.describe(idea="What should SharpLab do?")
    async def new(self, interaction: discord.Interaction, idea: app_commands.Range[str, 5, 400]) -> None:
        rid = await queries.add_feature_request(str(interaction.user.id), idea)
        embed = discord.Embed(title=f"💡 Request #{rid}", description=idea, color=0x7AA2F7)
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.set_footer(text=f"{VOTE} if you want it · shipped requests get a shoutout")
        await interaction.response.send_message(embed=embed)
        msg = await interaction.original_response()
        await queries.set_feature_request_message(rid, str(msg.channel.id), str(msg.id))
        try:
            await msg.add_reaction(VOTE)
        except discord.HTTPException:
            pass

    @request.command(name="list", description="Open requests, most-wanted first")
    async def list_(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        rows = await queries.list_feature_requests("open")
        if not rows:
            await interaction.followup.send("No open requests — `/request new` to pitch one.")
            return
        # ponytail: one message fetch per open request; fine at tens, cache votes if it grows
        scored = sorted(((await _votes(self.bot, r), r) for r in rows), key=lambda t: (-t[0], t[1]["id"]))
        lines = [f"`#{r['id']}` **{v}** {VOTE} · {discord.utils.escape_markdown(r['idea'][:90])} — <@{r['discord_user']}>"
                 for v, r in scored[:20]]
        await interaction.followup.send(
            embed=discord.Embed(title="💡 Open requests", description="\n".join(lines), color=0x7AA2F7)
            .set_footer(text=f"{len(rows)} open · /request new to pitch one"),
            allowed_mentions=discord.AllowedMentions.none())

    @request.command(name="done", description="Mark a request shipped and credit the requester (admin)")
    @app_commands.describe(request_id="Request number", note="What shipped (optional)")
    async def done(self, interaction: discord.Interaction, request_id: int,
                   note: app_commands.Range[str, 1, 300] | None = None) -> None:
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("Only admins can mark requests shipped.", ephemeral=True)
            return
        r = await queries.get_feature_request(request_id)
        if not r or not await queries.mark_feature_request_done(request_id, note):
            await interaction.response.send_message(
                f"Request #{request_id} doesn't exist or is already done.", ephemeral=True)
            return
        text = (f"✅ **Shipped request #{request_id}** — thanks <@{r['discord_user']}>!\n> {r['idea'][:300]}"
                + (f"\n{note}" if note else ""))
        pings = discord.AllowedMentions(users=True, roles=False, everyone=False)
        try:
            ch = self.bot.get_channel(int(r["channel_id"])) or await self.bot.fetch_channel(int(r["channel_id"]))
            await (await ch.fetch_message(int(r["message_id"]))).reply(text, allowed_mentions=pings)
            await interaction.response.send_message(f"Marked #{request_id} shipped.", ephemeral=True)
        except (discord.HTTPException, TypeError, ValueError):  # original post gone → credit here instead
            await interaction.response.send_message(text, allowed_mentions=pings)
        try:  # "Shipped It" achievement for the requester
            from bot.cogs.progression import evaluate_user_achievements, announce_achievements
            if newly := await evaluate_user_achievements(r["discord_user"]):
                await announce_achievements(self.bot, r["discord_user"], newly, channel=interaction.channel)
        except Exception:
            log.debug("request achievement grant failed for %s", r["discord_user"], exc_info=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RequestsCog(bot))
