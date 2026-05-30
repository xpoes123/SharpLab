"""Reaction roles — react to a panel message to self-assign a role.

A panel is just a bot message with emoji→role bindings stored in the
reaction_roles table. on_raw_reaction_add/remove (raw, so it survives restarts
without the message cache) grants/removes the role. Uses fetch_member, so the
privileged members intent is not required.
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from db import queries

log = logging.getLogger(__name__)


class ReactionRolesCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._panel_ids: set[str] = set()  # message ids that have bindings

    async def cog_load(self) -> None:
        self._panel_ids = await queries.get_reaction_role_message_ids()

    # ── Reaction handling ────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self._handle(payload, add=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self._handle(payload, add=False)

    async def _handle(self, payload: discord.RawReactionActionEvent, add: bool) -> None:
        if payload.guild_id is None or str(payload.message_id) not in self._panel_ids:
            return
        if self.bot.user and payload.user_id == self.bot.user.id:
            return
        role_id = await queries.get_reaction_role(str(payload.message_id), str(payload.emoji))
        if role_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        role = guild.get_role(int(role_id))
        if role is None:
            return
        member = payload.member if (add and payload.member) else None
        if member is None:
            try:
                member = await guild.fetch_member(payload.user_id)
            except discord.HTTPException:
                return
        if member.bot:
            return
        try:
            if add:
                await member.add_roles(role, reason="reaction role")
            else:
                await member.remove_roles(role, reason="reaction role")
        except discord.Forbidden:
            log.warning("reaction role: missing permission/hierarchy for role %s", role_id)
        except discord.HTTPException:
            log.debug("reaction role: HTTP error toggling %s for %s", role_id, payload.user_id)

    # ── Admin commands ───────────────────────────────────────────────────────

    group = app_commands.Group(
        name="reactionrole",
        description="Manage self-assign role panels",
        default_permissions=discord.Permissions(manage_roles=True),
    )

    @group.command(name="create", description="Post a new (empty) role panel; then add emojis with /reactionrole bind")
    @app_commands.describe(channel="Channel to post in", title="Panel title", description="Panel body text")
    async def create(
        self, interaction: discord.Interaction,
        channel: discord.TextChannel, title: str, description: str,
    ) -> None:
        embed = discord.Embed(title=title, description=description, colour=0x5865F2)
        msg = await channel.send(embed=embed)
        await interaction.response.send_message(
            f"Panel posted in {channel.mention}. Add roles with:\n"
            f"`/reactionrole bind channel:{channel.mention} message_id:{msg.id} emoji:🏀 role:@NBA`",
            ephemeral=True,
        )

    @group.command(name="bind", description="Bind an emoji on a panel message to a role")
    @app_commands.describe(
        channel="Channel the panel is in", message_id="Panel message id",
        emoji="Emoji to react with", role="Role to grant",
    )
    async def bind(
        self, interaction: discord.Interaction, channel: discord.TextChannel,
        message_id: str, emoji: str, role: discord.Role,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        # Hierarchy / permission sanity check.
        me = interaction.guild.me
        if not me.guild_permissions.manage_roles:
            await interaction.followup.send("I don't have Manage Roles permission.", ephemeral=True)
            return
        if role >= me.top_role:
            await interaction.followup.send(
                f"My role is below **{role.name}** — move **{me.top_role.name}** above it in "
                "Server Settings → Roles, then try again.", ephemeral=True,
            )
            return
        try:
            msg = await channel.fetch_message(int(message_id))
        except (discord.HTTPException, ValueError):
            await interaction.followup.send("Couldn't find that message in that channel.", ephemeral=True)
            return
        pe = discord.PartialEmoji.from_str(emoji.strip())
        try:
            await msg.add_reaction(pe)
        except discord.HTTPException:
            await interaction.followup.send(f"Couldn't add the reaction {emoji}.", ephemeral=True)
            return
        await queries.add_reaction_role(
            str(msg.id), str(pe), str(role.id), str(interaction.guild_id), str(channel.id)
        )
        self._panel_ids.add(str(msg.id))
        await interaction.followup.send(f"Bound {emoji} → {role.mention} on that panel.", ephemeral=True)

    @group.command(name="unbind", description="Remove an emoji→role binding from a panel")
    @app_commands.describe(channel="Channel the panel is in", message_id="Panel message id", emoji="Emoji to unbind")
    async def unbind(
        self, interaction: discord.Interaction, channel: discord.TextChannel,
        message_id: str, emoji: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        pe = discord.PartialEmoji.from_str(emoji.strip())
        removed = await queries.remove_reaction_role(message_id, str(pe))
        if removed and str(message_id) not in (await queries.get_reaction_role_message_ids()):
            self._panel_ids.discard(str(message_id))
        try:
            msg = await channel.fetch_message(int(message_id))
            await msg.clear_reaction(pe)
        except (discord.HTTPException, ValueError):
            pass
        await interaction.followup.send(
            "Unbound." if removed else "No such binding.", ephemeral=True
        )

    @group.command(name="list", description="List configured role panels")
    async def list_panels(self, interaction: discord.Interaction) -> None:
        ids = await queries.get_reaction_role_message_ids()
        if not ids:
            await interaction.response.send_message("No role panels configured yet.", ephemeral=True)
            return
        lines: list[str] = []
        for mid in ids:
            binds = await queries.get_reaction_roles_for_message(mid)
            chan = binds[0]["channel_id"] if binds else "?"
            pairs = " ".join(f"{b['emoji']}→<@&{b['role_id']}>" for b in binds)
            lines.append(f"<#{chan}> · `{mid}`\n{pairs}")
        await interaction.response.send_message("\n\n".join(lines), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReactionRolesCog(bot))
