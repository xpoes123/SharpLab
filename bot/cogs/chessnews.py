"""Automated major-chess-tournament news — polls Chess.com's news RSS, asks Haiku
which headlines report a real, just-happened elite-tournament event, and posts
those to a Discord channel while pinging a self-assignable Chess role.

Modeled on sportsnews.py (ESPN), but chess has no ESPN feed so we read Chess.com's
public RSS (no key). Seen article ids (the item guid) are persisted in bot_settings
so restarts don't re-post, and a freshness window bounds how stale a post can be.

Config lives in bot_settings (set at runtime, no redeploy):
  chess_news_channel — where to post (defaults to the sports-news channel)
  chess_news_role    — role to ping (set by /chessnews setup or /chessnews role)
/chessnews setup creates the Chess role + a self-assign reaction panel in one shot.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import discord
import httpx
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

from db import queries
from shared.llm import haiku_json

log = logging.getLogger(__name__)
load_dotenv()

CHESS_RSS = "https://www.chess.com/rss/news"
# Default to the same channel sports news uses, so it works out of the box.
DEFAULT_CHANNEL_ID = int(os.getenv("SPORTS_NEWS_CHANNEL_ID") or 1511062528116129932)
CHESS_EMOJI = "♟️"
CHESS_COLOR = 0x769656  # chess.com board green
CHESS_ROLE_NAME = "Chess"

_CHANNEL_SETTING = "chess_news_channel"
_ROLE_SETTING = "chess_news_role"
_SEEN_KEY = "chess_news_seen"   # bot_settings JSON list of recently-seen guids
_SEEN_CAP = 400
_FRESH_HOURS = 36               # chess is slower-paced than ball sports; allow more slack
_POLL_MINUTES = 30

_JUDGE_PROMPT = """You are a chess-news editor for a fan Discord. Every post PINGS \
the whole Chess role, so the bar is HIGH: keep ONLY headlines that report a single, \
concrete, just-happened event at an ELITE event that fans would react to in the moment.

KEEP (one specific elite-tournament event that just occurred): a decisive game or \
round result, a sole leader emerging, or final standings at a top event — World \
Championship, Candidates, FIDE World Cup / Grand Swiss / Olympiad, Norway Chess, Tata \
Steel Masters, Sinquefield Cup / Grand Chess Tour, Champions Chess Tour finals; a NEW \
world champion crowned; a major classical title won; a #1-ranking change or a broken \
record; a stunning upset of a top-10 player.

DROP — never post these: puzzles, tactics, opening theory, lessons/how-to, interviews, \
opinion/columns, "best games", recaps, product/feature/site announcements, streamer or \
content-creator news, rating-list minutiae, amateur/corporate/scholastic leagues, and \
low-stakes online blitz/bullet arenas or promos. Also drop anything that summarizes or \
analyzes MULTIPLE events rather than reporting one fresh elite result.

When unsure, DROP it.

Return ONLY JSON: {"major": ["<id>", ...]}.

Headlines:
%s"""


def _parse_pubdate(s: str) -> datetime | None:
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _parse_rss(xml: str) -> list[dict]:
    """Parse Chess.com's RSS into [{id, headline, description, published, url}]."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        log.debug("chess RSS parse failed", exc_info=True)
        return []
    out: list[dict] = []
    for item in root.iterfind(".//item"):
        def txt(tag: str) -> str:
            el = item.find(tag)
            return (el.text or "").strip() if el is not None else ""
        link = txt("link")
        guid = txt("guid") or link
        if not guid:
            continue
        out.append({
            "id": guid,
            "headline": txt("title"),
            "description": txt("description"),
            "published": txt("pubDate"),
            "url": link,
        })
    return out


class ChessNewsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        self.news_check.start()

    def cog_unload(self) -> None:
        self.news_check.cancel()

    async def _channel_id(self) -> int:
        raw = await queries.get_bot_setting(_CHANNEL_SETTING)
        try:
            return int(raw) if raw else DEFAULT_CHANNEL_ID
        except ValueError:
            return DEFAULT_CHANNEL_ID

    async def _role_id(self) -> int | None:
        raw = await queries.get_bot_setting(_ROLE_SETTING)
        try:
            return int(raw) if raw else None
        except ValueError:
            return None

    async def _judge(self, articles: list[dict]) -> set[str]:
        """Subset of article ids worth posting. Haiku refines; if unavailable we
        post nothing (chess RSS has no breaking-news type to fall back on, so a
        silent miss beats pinging the role for a puzzle column)."""
        if not articles or not self.api_key:
            return set()
        import json
        items = [{"id": a["id"], "headline": a["headline"],
                  "description": a["description"][:200]} for a in articles]
        try:
            obj = await haiku_json(_JUDGE_PROMPT % json.dumps(items, ensure_ascii=False),
                                   api_key=self.api_key, max_tokens=500)
            if obj is None:
                return set()
            return {str(x) for x in obj.get("major", [])}
        except Exception:
            log.warning("chess news Haiku judge failed; posting nothing this pass", exc_info=True)
            return set()

    def _embed(self, a: dict) -> discord.Embed:
        e = discord.Embed(title=a["headline"][:256], url=a["url"] or None,
                          description=(a["description"][:300] or None), color=CHESS_COLOR)
        pub = _parse_pubdate(a["published"])
        if pub:
            e.timestamp = pub
        e.set_footer(text="♟️ Chess · Chess.com")
        return e

    @tasks.loop(minutes=_POLL_MINUTES)
    async def news_check(self) -> None:
        import json
        try:
            channel = self.bot.get_channel(await self._channel_id())
            if channel is None:
                return
            try:
                async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0"}) as client:
                    r = await client.get(CHESS_RSS, timeout=20.0)
                articles = _parse_rss(r.text) if r.status_code == 200 else []
            except Exception:
                log.debug("chess RSS fetch failed", exc_info=True)
                return
            if not articles:
                return

            by_id = {a["id"]: a for a in articles}
            raw = await queries.get_bot_setting(_SEEN_KEY)
            seen = json.loads(raw) if raw else []
            seen_set = set(seen)

            # Cold start: seed silently so we don't dump the whole current feed.
            if not seen:
                await queries.set_bot_setting(_SEEN_KEY, json.dumps(list(by_id)[:_SEEN_CAP]))
                log.info("chess news cold-start: seeded %d ids, posted none", len(by_id))
                return

            now = datetime.now(timezone.utc)
            new_ids = [aid for aid in by_id if aid not in seen_set]
            fresh = []
            from datetime import timedelta
            for aid in new_ids:
                pub = _parse_pubdate(by_id[aid]["published"])
                if pub and (now - pub) > timedelta(hours=_FRESH_HOURS):
                    continue
                fresh.append(by_id[aid])

            # Mark seen before posting — judge each once; prefer a miss over a dupe.
            if new_ids:
                seen = (new_ids + seen)[:_SEEN_CAP]
                await queries.set_bot_setting(_SEEN_KEY, json.dumps(seen))
            if not fresh:
                return

            major = await self._judge(fresh)
            if not major:
                return
            role_id = await self._role_id()
            content = f"<@&{role_id}>" if role_id else None
            for a in fresh:
                if a["id"] in major:
                    await channel.send(content=content, embed=self._embed(a),
                                       allowed_mentions=discord.AllowedMentions(roles=True))
        except Exception:
            log.exception("Unhandled error in chess news_check loop")

    @news_check.before_loop
    async def before_news_check(self) -> None:
        await self.bot.wait_until_ready()

    # ── Admin commands ───────────────────────────────────────────────────────

    group = app_commands.Group(
        name="chessnews",
        description="Configure major-chess-tournament news pings",
        default_permissions=discord.Permissions(manage_roles=True),
    )

    @group.command(name="setup", description="Create the Chess role + a self-assign panel and wire up news pings")
    async def setup_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        me = guild.me
        if not me.guild_permissions.manage_roles:
            await interaction.followup.send("I need **Manage Roles** to create the Chess role.", ephemeral=True)
            return
        role = discord.utils.get(guild.roles, name=CHESS_ROLE_NAME)
        created = False
        if role is None:
            try:
                role = await guild.create_role(
                    name=CHESS_ROLE_NAME, mentionable=True, colour=discord.Colour(CHESS_COLOR),
                    reason="chess news self-assign role")
                created = True
            except discord.HTTPException:
                await interaction.followup.send("Couldn't create the role.", ephemeral=True)
                return
        # Post the self-assign panel in this channel and register it with the
        # reaction-role system (also live-register so it works before any restart).
        embed = discord.Embed(
            title="♟️ Chess",
            description=(f"React with {CHESS_EMOJI} to get the **{CHESS_ROLE_NAME}** role and be "
                         "pinged for major chess-tournament news."),
            colour=CHESS_COLOR,
        )
        panel = await interaction.channel.send(embed=embed)
        pe = discord.PartialEmoji.from_str(CHESS_EMOJI)
        try:
            await panel.add_reaction(pe)
        except discord.HTTPException:
            pass
        await queries.add_reaction_role(str(panel.id), str(pe), str(role.id),
                                        str(guild.id), str(interaction.channel_id))
        rr = self.bot.get_cog("ReactionRolesCog")
        if rr is not None:
            rr._panel_ids.add(str(panel.id))
        await queries.set_bot_setting(_ROLE_SETTING, str(role.id))
        hier = ("" if role < me.top_role else
                f"\n⚠️ Move my role above **{role.name}** in Server Settings → Roles so I can assign it.")
        await interaction.followup.send(
            f"{'Created' if created else 'Using existing'} {role.mention}, posted a self-assign panel here, "
            f"and set it as the chess-news ping role.{hier}", ephemeral=True)

    @group.command(name="channel", description="Set the channel where chess news posts")
    @app_commands.describe(channel="Channel for chess-tournament news")
    async def channel_cmd(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await queries.set_bot_setting(_CHANNEL_SETTING, str(channel.id))
        await interaction.response.send_message(f"✅ Chess news will post in {channel.mention}.", ephemeral=True)

    @group.command(name="role", description="Set the role pinged for chess news")
    @app_commands.describe(role="Role to ping on major chess news")
    async def role_cmd(self, interaction: discord.Interaction, role: discord.Role) -> None:
        await queries.set_bot_setting(_ROLE_SETTING, str(role.id))
        await interaction.response.send_message(f"✅ Chess news will ping {role.mention}.", ephemeral=True)

    @group.command(name="status", description="Show chess-news configuration")
    async def status_cmd(self, interaction: discord.Interaction) -> None:
        cid, rid = await self._channel_id(), await self._role_id()
        await interaction.response.send_message(
            f"**Chess news**\nChannel: <#{cid}>\nPing role: " + (f"<@&{rid}>" if rid else "*(none — run `/chessnews setup`)*")
            + f"\nSource: Chess.com RSS · checks every {_POLL_MINUTES} min", ephemeral=True)

    @group.command(name="test", description="Fetch + judge the feed now and report (no posting)")
    async def test_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0"}) as client:
                r = await client.get(CHESS_RSS, timeout=20.0)
            articles = _parse_rss(r.text) if r.status_code == 200 else []
        except Exception:
            await interaction.followup.send("Couldn't reach Chess.com RSS.", ephemeral=True)
            return
        if not articles:
            await interaction.followup.send("Feed returned no items.", ephemeral=True)
            return
        major = await self._judge(articles[:20])
        worthy = [a["headline"] for a in articles if a["id"] in major]
        body = "\n".join(f"• {h}" for h in worthy[:10]) or "*(nothing judged ping-worthy right now)*"
        await interaction.followup.send(
            f"Fetched **{len(articles)}** items; **{len(worthy)}** would ping:\n{body}", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ChessNewsCog(bot))
