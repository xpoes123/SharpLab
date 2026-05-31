"""Daily NBA/MLB pick'em.

Each morning (10 AM ET) the bot recaps last night's results, then posts today's
NBA + MLB slate — one message per game with ✈️ (away) and 🏠 (home) reactions.
Vote by reacting; voting locks at each game's tip-off. Resolved off the existing
score pipeline (games.status='final'). Leaderboard ranks by total correct,
accuracy, or streak points.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import discord
from discord import app_commands, ui
from discord.ext import commands, tasks

from db import queries
from shared.odds_utils import devig_two_way

log = logging.getLogger(__name__)

# Win-prob source preference: Kalshi first, then sharp books, then anything.
_ODDS_PREFERENCE = ("kalshi", "pinnacle", "fanduel", "draftkings", "fanatics", "betmgm")


async def _win_probs(game_id: str) -> tuple[float, float, str] | None:
    """Fair (home, away) win prob + source for a game, devigged from the best
    available two-sided moneyline (Kalshi preferred). None if unavailable."""
    try:
        snaps = await queries.get_latest_snapshots_for_game(game_id)
    except Exception:
        log.exception("pickem: snapshot fetch failed for %s", game_id)
        return None
    by_source: dict[str, tuple[int, int]] = {}
    for s in snaps:
        payload = s.payload
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                continue
        if not isinstance(payload, dict):
            continue
        mh, ma = payload.get("ml_home"), payload.get("ml_away")
        if mh and ma:
            by_source[s.source] = (int(mh), int(ma))
    if not by_source:
        return None
    src = next((s for s in _ODDS_PREFERENCE if s in by_source), next(iter(by_source)))
    hp, ap = devig_two_way(*by_source[src])
    return hp, ap, src

ET = ZoneInfo("America/New_York")
POST_TIME = time(hour=10, minute=0, tzinfo=ET)

PICKEM_CHANNEL_DEFAULT = 1510694785034354849
_PICKEM_CHANNEL_SETTING = "pickem_channel"

AWAY_EMOJI = "✈️"
HOME_EMOJI = "🏠"
SPORT_EMOJI = {"nba": "🏀", "mlb": "⚾"}

MIN_PICKS_FOR_ACCURACY = 20
STREAK_CAP = 5  # a single correct pick is worth at most this many streak points
COLOUR = 0x1ABC9C


# ── Pure scoring ──────────────────────────────────────────────────────────────


def compute_pickem_standings(rows: list[dict]) -> dict[str, dict]:
    """Aggregate ordered scored picks into per-user standings.

    rows: {discord_user, correct (0/1), start_time}, ordered by user then time.
    Returns uid -> {correct, total, accuracy, points}. Points = 1 per correct
    plus an escalating streak bonus (1,2,3,…) capped at STREAK_CAP per pick.
    """
    stats: dict[str, dict] = {}
    for r in rows:
        uid = r["discord_user"]
        s = stats.setdefault(
            uid, {"correct": 0, "total": 0, "points": 0, "units": 0.0, "_streak": 0},
        )
        s["total"] += 1
        # Market P&L: a 1-unit bet on the pick at its devigged win prob.
        prob = (r.get("home_prob") if r.get("pick") == "home" else r.get("away_prob")) or 0.5
        if prob <= 0:
            prob = 0.5
        if r["correct"]:
            s["correct"] += 1
            s["_streak"] += 1
            s["points"] += min(s["_streak"], STREAK_CAP)
            s["units"] += (1.0 / prob) - 1.0   # profit on the winning unit
        else:
            s["_streak"] = 0
            s["units"] -= 1.0                  # lost the staked unit
    for s in stats.values():
        s["accuracy"] = s["correct"] / s["total"] if s["total"] else 0.0
        s.pop("_streak", None)
    return stats


def _winner_from_scores(home_score: int, away_score: int) -> str:
    return "home" if home_score > away_score else "away"


def _fmt_et_time(start_time_iso: str) -> str:
    try:
        dt = datetime.fromisoformat(start_time_iso.replace("Z", "+00:00"))
        local = dt.astimezone(ET)
        return local.strftime("%-I:%M %p ET")
    except Exception:
        return "TBD"


# ── Message / embed builders ─────────────────────────────────────────────────


def _odds_line(game: dict) -> str:
    hp, ap = game.get("home_prob"), game.get("away_prob")
    if hp is None or ap is None:
        return ""
    src = game.get("odds_source") or "market"
    label = "Kalshi" if src == "kalshi" else src
    return f"\n📊 win%: {game['away_team']} {ap * 100:.0f}% · {game['home_team']} {hp * 100:.0f}% ({label})"


def _game_message(game: dict, *, locked: bool = False, result: str | None = None) -> str:
    emoji = SPORT_EMOJI.get(game["sport"], "🏟️")
    head = f"{emoji} **{game['away_team']}** {AWAY_EMOJI} @ {HOME_EMOJI} **{game['home_team']}**"
    odds = _odds_line(game)
    if result:
        return f"{head}{odds}\n{result}"
    if locked:
        return f"{head}{odds}\n🔒 Voting closed — game underway."
    return f"{head}{odds}\n🕒 {_fmt_et_time(game['start_time'])} — react to pick a winner!"


def _result_line(game: dict, counts: dict[str, int]) -> str:
    winner_team = game["home_team"] if game["winner"] == "home" else game["away_team"]
    tally = f"{AWAY_EMOJI} {counts.get('away', 0)} · {HOME_EMOJI} {counts.get('home', 0)}"
    return f"✅ **{winner_team}** won — votes: {tally}"


# ── Leaderboard view ─────────────────────────────────────────────────────────


class LeaderboardView(ui.View):
    MODES = [
        ("Total Correct", "correct"), ("Accuracy", "accuracy"),
        ("Streak Points", "points"), ("Market P&L", "units"),
    ]

    def __init__(self, bot: commands.Bot, standings: dict[str, dict]) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.standings = standings
        self.mode = "correct"
        self.message: discord.Message | None = None
        self._names: dict[str, str] = {}

    async def _name(self, uid: str) -> str:
        if uid not in self._names:
            try:
                self._names[uid] = (await self.bot.fetch_user(int(uid))).display_name
            except Exception:
                self._names[uid] = f"Player {uid[:6]}"
        return self._names[uid]

    def _ranked(self) -> list[tuple[str, dict]]:
        items = list(self.standings.items())
        if self.mode == "correct":
            items.sort(key=lambda kv: (kv[1]["correct"], kv[1]["accuracy"]), reverse=True)
        elif self.mode == "accuracy":
            items = [kv for kv in items if kv[1]["total"] >= MIN_PICKS_FOR_ACCURACY]
            items.sort(key=lambda kv: (kv[1]["accuracy"], kv[1]["correct"]), reverse=True)
        elif self.mode == "units":
            items.sort(key=lambda kv: (kv[1]["units"], kv[1]["correct"]), reverse=True)
        else:
            items.sort(key=lambda kv: (kv[1]["points"], kv[1]["correct"]), reverse=True)
        return items[:15]

    async def embed(self) -> discord.Embed:
        mode_label = next(lbl for lbl, key in self.MODES if key == self.mode)
        embed = discord.Embed(title=f"🏆 Pick'em Leaderboard — {mode_label}", colour=COLOUR)
        ranked = self._ranked()
        if not ranked:
            embed.description = (
                f"No one qualifies yet (min {MIN_PICKS_FOR_ACCURACY} picks for accuracy)."
                if self.mode == "accuracy" else "No scored picks yet."
            )
            return embed
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for rank, (uid, s) in enumerate(ranked, 1):
            name = await self._name(uid)
            prefix = medals.get(rank, f"`#{rank}`")
            acc = f"{s['accuracy'] * 100:.0f}%"
            if self.mode == "correct":
                stat = f"**{s['correct']}** correct ({acc})"
            elif self.mode == "accuracy":
                stat = f"**{acc}** ({s['correct']}/{s['total']})"
            elif self.mode == "units":
                stat = f"**{s['units']:+.1f}u** ({s['correct']}/{s['total']})"
            else:
                stat = f"**{s['points']}** pts ({s['correct']} correct)"
            lines.append(f"{prefix} **{name}** — {stat}")
        embed.description = "\n".join(lines)
        return embed

    @ui.button(label="Total Correct", style=discord.ButtonStyle.primary)
    async def total_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        self.mode = "correct"
        await interaction.response.edit_message(embed=await self.embed(), view=self)

    @ui.button(label="Accuracy", style=discord.ButtonStyle.secondary)
    async def acc_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        self.mode = "accuracy"
        await interaction.response.edit_message(embed=await self.embed(), view=self)

    @ui.button(label="Streak Points", style=discord.ButtonStyle.secondary)
    async def pts_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        self.mode = "points"
        await interaction.response.edit_message(embed=await self.embed(), view=self)

    @ui.button(label="Market P&L", style=discord.ButtonStyle.secondary)
    async def units_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        self.mode = "units"
        await interaction.response.edit_message(embed=await self.embed(), view=self)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


# ── Cog ──────────────────────────────────────────────────────────────────────


class PickemCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._votable: set[str] = set()  # message ids currently accepting votes

    async def cog_load(self) -> None:
        try:
            self._votable = {g["message_id"] for g in await queries.get_unlocked_pickem_games()}
        except Exception:
            log.exception("pickem: failed to warm votable cache")
        self.post_loop.start()
        self.lock_loop.start()
        self.resolve_loop.start()

    async def cog_unload(self) -> None:
        self.post_loop.cancel()
        self.lock_loop.cancel()
        self.resolve_loop.cancel()

    pickem = app_commands.Group(name="pickem", description="Daily NBA/MLB pick'em")

    async def _channel(self) -> discord.TextChannel | None:
        raw = await queries.get_bot_setting(_PICKEM_CHANNEL_SETTING)
        try:
            cid = int(raw) if raw else PICKEM_CHANNEL_DEFAULT
        except ValueError:
            cid = PICKEM_CHANNEL_DEFAULT
        ch = self.bot.get_channel(cid)
        return ch if isinstance(ch, discord.TextChannel) else None

    # ── Morning routine ──────────────────────────────────────────────────────

    @tasks.loop(time=POST_TIME)
    async def post_loop(self) -> None:
        channel = await self._channel()
        if channel is None:
            log.warning("pickem: alerts channel not found")
            return
        try:
            await self._post_recap(channel)
            await self._post_today(channel)
        except Exception:
            log.exception("pickem: morning routine failed")

    @post_loop.before_loop
    async def _before_post(self) -> None:
        await self.bot.wait_until_ready()

    async def _post_recap(self, channel: discord.TextChannel) -> None:
        yesterday = (datetime.now(ET).date() - timedelta(days=1)).isoformat()
        games = await queries.get_pickem_games_for_date(yesterday)
        resolved = [g for g in games if g["resolved"]]
        if not resolved:
            return
        embed = discord.Embed(
            title=f"🌙 Last Night's Pick'em — {yesterday}", colour=0x9B59B6,
        )
        per_user: dict[str, list[int]] = {}  # uid -> [correct, total]
        game_lines = []
        for g in resolved:
            counts = await queries.get_pickem_vote_counts(g["message_id"])
            game_lines.append(_result_line(g, counts).replace("✅ ", ""))
            for p in await queries.get_pickem_picks_for_message(g["message_id"]):
                rec = per_user.setdefault(p["discord_user"], [0, 0])
                rec[1] += 1
                if p["correct"]:
                    rec[0] += 1
        embed.add_field(name="Results", value="\n".join(game_lines)[:1024], inline=False)
        if per_user:
            ranked = sorted(per_user.items(), key=lambda kv: (kv[1][0], kv[1][0] / kv[1][1]), reverse=True)
            lines = []
            for uid, (correct, total) in ranked[:15]:
                try:
                    name = (await self.bot.fetch_user(int(uid))).display_name
                except Exception:
                    name = f"Player {uid[:6]}"
                lines.append(f"**{name}** — {correct}/{total}")
            embed.add_field(name="How everyone did", value="\n".join(lines)[:1024], inline=False)
        await channel.send(embed=embed)

    async def _post_today(self, channel: discord.TextChannel) -> None:
        now_et = datetime.now(ET)
        today = now_et.date().isoformat()
        start = now_et.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        s_utc = start.astimezone(timezone.utc).isoformat()
        e_utc = end.astimezone(timezone.utc).isoformat()

        games: list = []
        for sport in ("nba", "mlb"):
            try:
                games += await queries.get_games_in_window(s_utc, e_utc, sport=sport)
            except Exception:
                log.exception("pickem: failed to load %s games", sport)
        games.sort(key=lambda g: g.start_time_utc_iso)

        if not games:
            await channel.send(f"📋 **Daily Pick'em — {today}**\nNo NBA or MLB games on the slate today.")
            return

        await channel.send(
            f"📋 **Daily Pick'em — {today}**\n"
            f"React {AWAY_EMOJI} for the away team or {HOME_EMOJI} for the home team. "
            "Each game locks at tip-off!"
        )
        for g in games:
            if await queries.pickem_game_exists(g.game_id, today):
                continue
            if await queries.get_game_scores(g.game_id) is not None:
                continue  # already final earlier today — skip voting
            gd = {
                "game_id": g.game_id, "sport": g.sport,
                "home_team": g.home_team, "away_team": g.away_team,
                "start_time": g.start_time_utc_iso,
            }
            probs = await _win_probs(g.game_id)
            if probs:
                gd["home_prob"], gd["away_prob"], gd["odds_source"] = probs
            try:
                msg = await channel.send(_game_message(gd))
                await msg.add_reaction(AWAY_EMOJI)
                await msg.add_reaction(HOME_EMOJI)
            except discord.HTTPException:
                log.exception("pickem: failed to post game %s", g.game_id)
                continue
            await queries.add_pickem_game(
                str(msg.id), g.game_id, g.sport, g.home_team, g.away_team,
                g.start_time_utc_iso, today,
                gd.get("home_prob"), gd.get("away_prob"), gd.get("odds_source"),
            )
            self._votable.add(str(msg.id))

    # ── Lock + resolve loops ─────────────────────────────────────────────────

    @tasks.loop(minutes=2)
    async def lock_loop(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        try:
            open_games = await queries.get_unlocked_pickem_games()
        except Exception:
            log.exception("pickem: lock loop query failed")
            return
        channel = await self._channel()
        for g in open_games:
            if g["start_time"] > now:
                continue
            await queries.lock_pickem_game(g["message_id"])
            self._votable.discard(g["message_id"])
            if channel is not None:
                try:
                    await channel.get_partial_message(int(g["message_id"])).edit(
                        content=_game_message(g, locked=True),
                    )
                except discord.HTTPException:
                    pass

    @lock_loop.before_loop
    async def _before_lock(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=15)
    async def resolve_loop(self) -> None:
        try:
            pending = await queries.get_unresolved_pickem_games()
        except Exception:
            log.exception("pickem: resolve loop query failed")
            return
        channel = await self._channel()
        for g in pending:
            scores = await queries.get_game_scores(g["game_id"])
            if scores is None:
                continue
            home_score, away_score = scores
            winner = _winner_from_scores(home_score, away_score)
            await queries.resolve_pickem_game(g["message_id"], winner)
            self._votable.discard(g["message_id"])
            if channel is not None:
                g["winner"] = winner
                counts = await queries.get_pickem_vote_counts(g["message_id"])
                result = (
                    f"✅ **{g['home_team'] if winner == 'home' else g['away_team']}** won "
                    f"{home_score}-{away_score} · votes {AWAY_EMOJI} {counts.get('away', 0)} "
                    f"/ {HOME_EMOJI} {counts.get('home', 0)}"
                )
                try:
                    await channel.get_partial_message(int(g["message_id"])).edit(
                        content=_game_message(g, result=result),
                    )
                except discord.HTTPException:
                    pass

    @resolve_loop.before_loop
    async def _before_resolve(self) -> None:
        await self.bot.wait_until_ready()

    # ── Reaction voting ──────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if self.bot.user and payload.user_id == self.bot.user.id:
            return
        mid = str(payload.message_id)
        if mid not in self._votable:
            return
        emoji = str(payload.emoji)
        if emoji not in (AWAY_EMOJI, HOME_EMOJI):
            return
        game = await queries.get_pickem_game(mid)
        if game is None:
            return
        now = datetime.now(timezone.utc).isoformat()
        if game["locked"] or game["start_time"] <= now:
            return
        pick = "away" if emoji == AWAY_EMOJI else "home"
        await queries.record_pickem_pick(mid, str(payload.user_id), pick)
        # Single choice: remove their reaction on the other team.
        opp = HOME_EMOJI if pick == "away" else AWAY_EMOJI
        channel = self.bot.get_channel(payload.channel_id)
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.get_partial_message(payload.message_id).remove_reaction(
                    opp, discord.Object(id=payload.user_id),
                )
            except discord.HTTPException:
                pass

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        mid = str(payload.message_id)
        if mid not in self._votable:
            return
        emoji = str(payload.emoji)
        if emoji not in (AWAY_EMOJI, HOME_EMOJI):
            return
        team = "away" if emoji == AWAY_EMOJI else "home"
        current = await queries.get_pickem_pick(mid, str(payload.user_id))
        if current == team:  # they removed their actual vote (not the cleanup removal)
            await queries.remove_pickem_pick(mid, str(payload.user_id))

    # ── Commands ─────────────────────────────────────────────────────────────

    @pickem.command(name="leaderboard", description="Pick'em prediction leaderboard")
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        rows = await queries.get_pickem_resolved_picks()
        standings = compute_pickem_standings(rows)
        if not standings:
            await interaction.followup.send("No pick'em games have been scored yet!")
            return
        view = LeaderboardView(self.bot, standings)
        await interaction.followup.send(embed=await view.embed(), view=view)
        view.message = await interaction.original_response()

    @pickem.command(name="channel", description="Set the pick'em channel")
    @app_commands.describe(channel="Where the daily pick'em is posted")
    async def channel_cmd(
        self, interaction: discord.Interaction, channel: discord.TextChannel,
    ) -> None:
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "You need **Manage Server** to set the pick'em channel.", ephemeral=True,
            )
            return
        await queries.set_bot_setting(_PICKEM_CHANNEL_SETTING, str(channel.id))
        await interaction.response.send_message(
            f"✅ Daily pick'em will post in {channel.mention}.", ephemeral=True,
        )

    @pickem.command(name="post", description="Post today's pick'em now (admin)")
    async def post_now(self, interaction: discord.Interaction) -> None:
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "You need **Manage Server** to trigger a post.", ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        channel = await self._channel()
        if channel is None:
            await interaction.followup.send("Pick'em channel not found.", ephemeral=True)
            return
        await self._post_recap(channel)
        await self._post_today(channel)
        await interaction.followup.send(f"Posted in {channel.mention}.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PickemCog(bot))
