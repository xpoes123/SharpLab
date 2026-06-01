"""Automated major sports news — polls ESPN's news API per league, asks Haiku
which headlines would actually spark conversation, and posts those to a Discord
channel while pinging the matching league role.

Same unofficial ESPN API family the injury alerts use (no key). Cross-posted
articles (one story appearing in several league feeds) are deduped by article id
and ping every relevant league role. Seen ids are persisted in bot_settings so
restarts don't re-post, and a freshness window bounds how stale a post can be.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import discord
import httpx
from discord.ext import commands, tasks
from dotenv import load_dotenv

from db import queries

log = logging.getLogger(__name__)
load_dotenv()

HAIKU = "claude-haiku-4-5-20251001"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ESPN_NEWS = "https://site.api.espn.com/apis/site/v2/sports/{path}/news"

NEWS_CHANNEL_ID = int(os.getenv("SPORTS_NEWS_CHANNEL_ID") or 1511062528116129932)

# Ordered: first match wins when picking an article's "primary" league (color/label).
LEAGUES: dict[str, dict] = {
    "nba": {"path": "basketball/nba", "label": "NBA", "emoji": "🏀",
            "role": 1510114648358256730, "color": 0xC8102E},
    "nfl": {"path": "football/nfl", "label": "NFL", "emoji": "🏈",
            "role": 1510114702565445904, "color": 0x013369},
    "mlb": {"path": "baseball/mlb", "label": "MLB", "emoji": "⚾",
            "role": 1510114684999565362, "color": 0x002D72},
}

_SEEN_KEY = "sports_news_seen"   # bot_settings JSON list of recently-seen article ids
_SEEN_CAP = 800                  # keep the newest N ids; older ones age out
_FRESH_HOURS = 12                # never post an article older than this (bounds cold-start/backfill)

_JUDGE_PROMPT = """You are a sports news editor for a sports-betting & fan Discord. \
From the JSON list of headlines below, pick the ones that would actually spark \
conversation among casual-to-serious fans.

INCLUDE: blockbuster trades, major signings/extensions, significant injuries \
(out for season, surgery, IL), playoff/elimination results & series swings, big \
upsets, records & milestones, suspensions, coach/GM firings or hirings, \
retirements, breaking off-field news with on-field impact.

EXCLUDE: routine game recaps, opinion/debate columns, podcasts & media segments, \
listicles, betting-odds explainers, power rankings, minor roster moves, fantasy advice.

Lean inclusive for broadly-followed events; be stricter for niche items with \
narrow appeal.

Return ONLY JSON: {"major": ["<id>", ...]} listing the ids worth posting.

Headlines:
%s"""


def _parse_ts(iso: str) -> datetime | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _primary(leagues: set[str]) -> str:
    """The league used for an article's color/label — first in LEAGUES order."""
    return next((k for k in LEAGUES if k in leagues), next(iter(leagues)))


def _merge(batches: list[list[dict]]) -> dict[str, dict]:
    """Dedupe articles by id across league feeds, unioning the leagues each appears in."""
    merged: dict[str, dict] = {}
    for arts in batches:
        for a in arts:
            m = merged.get(a["id"])
            if m:
                m["leagues"].add(a["league"])
            else:
                merged[a["id"]] = {**a, "leagues": {a["league"]}}
    return merged


async def _fetch_league(client: httpx.AsyncClient, key: str) -> list[dict]:
    cfg = LEAGUES[key]
    try:
        r = await client.get(ESPN_NEWS.format(path=cfg["path"]), timeout=20.0)
        if r.status_code != 200:
            return []
        arts = r.json().get("articles", [])
    except Exception:
        log.debug("ESPN news fetch failed for %s", key, exc_info=True)
        return []
    out: list[dict] = []
    for a in arts:
        aid = str(a.get("id") or "")
        if not aid:
            continue
        out.append({
            "id": aid,
            "league": key,
            "headline": a.get("headline") or "",
            "description": a.get("description") or "",
            "type": a.get("type") or "",
            "published": a.get("published") or "",
            "url": ((a.get("links") or {}).get("web") or {}).get("href") or "",
        })
    return out


class SportsNewsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        self.news_check.start()

    def cog_unload(self) -> None:
        self.news_check.cancel()

    async def _judge(self, articles: list[dict]) -> set[str]:
        """Return the subset of article ids worth posting. Falls back to ESPN's
        HeadlineNews tag if Haiku is unavailable or errors."""
        fallback = {a["id"] for a in articles if a["type"] == "HeadlineNews"}
        if not self.api_key:
            return fallback
        items = [{"id": a["id"], "league": LEAGUES[_primary(a["leagues"])]["label"],
                  "type": a["type"], "headline": a["headline"],
                  "description": a["description"][:200]} for a in articles]
        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    ANTHROPIC_URL,
                    headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                             "content-type": "application/json"},
                    json={"model": HAIKU, "max_tokens": 500,
                          "messages": [{"role": "user",
                                        "content": _JUDGE_PROMPT % json.dumps(items, ensure_ascii=False)}]},
                    timeout=30.0,
                )
            if r.status_code != 200:
                raise RuntimeError(f"haiku {r.status_code}")
            text = "".join(b.get("text", "") for b in r.json().get("content", []))
            obj = json.loads(text[text.find("{"): text.rfind("}") + 1])
            return {str(x) for x in obj.get("major", [])}
        except Exception:
            log.warning("news Haiku judge failed; falling back to HeadlineNews", exc_info=True)
            return fallback

    def _embed(self, a: dict) -> discord.Embed:
        cfg = LEAGUES[_primary(a["leagues"])]
        e = discord.Embed(title=a["headline"][:256], url=a["url"] or None,
                          description=(a["description"][:300] or None), color=cfg["color"])
        pub = _parse_ts(a["published"])
        if pub:
            e.timestamp = pub
        tags = " ".join(LEAGUES[k]["emoji"] for k in LEAGUES if k in a["leagues"])
        e.set_footer(text=f"{tags} {cfg['label']} · ESPN")
        return e

    @tasks.loop(minutes=10)
    async def news_check(self) -> None:
        try:
            channel = self.bot.get_channel(NEWS_CHANNEL_ID)
            if channel is None:
                return

            async with httpx.AsyncClient() as client:
                batches = await asyncio.gather(*[_fetch_league(client, k) for k in LEAGUES])
            merged = _merge(batches)
            if not merged:
                return

            raw = await queries.get_bot_setting(_SEEN_KEY)
            seen = json.loads(raw) if raw else []
            seen_set = set(seen)

            # Cold start: seed the seen-set silently so we don't dump the current feed.
            if not seen:
                await queries.set_bot_setting(_SEEN_KEY, json.dumps(list(merged)[:_SEEN_CAP]))
                log.info("sports news cold-start: seeded %d ids, posted none", len(merged))
                return

            now = datetime.now(timezone.utc)
            new_ids = [aid for aid in merged if aid not in seen_set]
            fresh = []
            for aid in new_ids:
                pub = _parse_ts(merged[aid]["published"])
                if pub and (now - pub) > timedelta(hours=_FRESH_HOURS):
                    continue  # too old to be interesting
                fresh.append(merged[aid])

            # Mark every new id seen before posting — judge each article once, and
            # prefer a missed post over a duplicate if we crash mid-loop.
            if new_ids:
                seen = (new_ids + seen)[:_SEEN_CAP]
                await queries.set_bot_setting(_SEEN_KEY, json.dumps(seen))

            if not fresh:
                return

            major = await self._judge(fresh)
            for a in fresh:
                if a["id"] not in major:
                    continue
                content = " ".join(f"<@&{LEAGUES[k]['role']}>" for k in LEAGUES if k in a["leagues"])
                await channel.send(content=content or None, embed=self._embed(a),
                                   allowed_mentions=discord.AllowedMentions(roles=True))
        except Exception:
            log.exception("Unhandled error in news_check loop")

    @news_check.before_loop
    async def before_news_check(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SportsNewsCog(bot))
