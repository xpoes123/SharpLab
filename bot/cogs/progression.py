"""Progression cog — achievements, leveling, and player profiles."""


import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.cogs.casino import GAME_LABELS
from db import queries
import logging
log = logging.getLogger(__name__)

# ── Achievement definitions ──────────────────────────────────────────────────

TOTAL_GAMES = 28


from shared.achievements import (  # metadata shared with the web layer
    Achievement, ALL_ACHIEVEMENTS, ACHIEVEMENTS_BY_ID, CATEGORIES as _CATEGORIES,
)

# Thresholds for progress display on countable achievements
_PROGRESS_TARGETS: dict[str, tuple[str, int]] = {
    "first_game": ("rounds", 1),
    "play_10": ("rounds", 10),
    "play_100": ("rounds", 100),
    "play_500": ("rounds", 500),
    "streak_3": ("streak", 3),
    "streak_5": ("streak", 5),
    "streak_10": ("streak", 10),
    "big_win": ("max_profit", 1000),
    "jackpot": ("max_profit", 5000),
    "explore_5": ("distinct", 5),
    "explore_15": ("distinct", 15),
    "explore_all": ("distinct", TOTAL_GAMES),
    "duel_win": ("duel_wins", 1),
    "duel_10": ("duel_wins", 10),
    "tourney_win": ("tourney_wins", 1),
    "tourney_5": ("tourney_wins", 5),
    "coins_5k": ("balance", 5_000),
    "coins_25k": ("balance", 25_000),
    "coins_100k": ("balance", 100_000),
    "wager_50k": ("total_wagered", 50_000),
    "wager_500k": ("total_wagered", 500_000),
    "bet_first": ("num_bets", 1),
    "bet_10": ("num_bets", 10),
    "bet_50": ("num_bets", 50),
    "bet_win": ("bet_wins", 1),
    "clv_beat": ("pos_clv", 1),
    "clv_10": ("pos_clv", 10),
    "stock_first": ("num_trades", 1),
    "stock_10": ("num_trades", 10),
    "stock_50": ("num_trades", 50),
    "stock_diversified": ("distinct_holdings", 10),
    "stock_green": ("realized_pnl", 1000),
    "stock_bull": ("realized_pnl", 10000),
    "options_5": ("num_option_trades", 5),
    "bet_100": ("num_bets", 100),
    "web_login": ("web_logins", 1),
    "web_regular": ("web_visits", 25),
    "level_10": ("level", 10),
    "level_25": ("level", 25),
    "level_50": ("level", 50),
    "voice_1h": ("voice_minutes", 60),
    "voice_10h": ("voice_minutes", 600),
    "voice_50h": ("voice_minutes", 3000),
    "chat_100": ("messages", 100),
    "chat_1k": ("messages", 1000),
    "chat_10k": ("messages", 10000),
}

# Level color thresholds
_LEVEL_COLORS = [
    (20, 0xF1C40F),   # gold
    (10, 0x3498DB),    # blue
    (5, 0x2ECC71),     # green
    (1, 0x95A5A6),     # gray
]


def _level_color(level: int) -> int:
    for threshold, color in _LEVEL_COLORS:
        if level >= threshold:
            return color
    return 0x95A5A6


def _progress_bar(current: int, total: int, width: int = 16) -> str:
    """Render an XP progress bar using block characters."""
    if total <= 0:
        filled = width
    else:
        filled = min(int(current / total * width), width)
    return "\u2588" * filled + "\u2591" * (width - filled)


async def gather_achievement_stats(uid: str) -> dict:
    """All numeric stats used to evaluate achievements + show progress. DB-only
    (no network), so it's cheap to call on every relevant action."""
    stats = await queries.get_casino_stats(uid)
    duel = await queries.get_duel_stats(uid)
    tourney = await queries.get_tournament_stats(uid)

    bets = await queries.get_bets_for_user(uid)
    positions = await queries.get_stock_positions_full(uid)
    open_positions = [p for p in positions if not p["closed"]]
    trades = await queries.get_stock_trades(uid)
    options = await queries.get_option_positions_full(uid)
    option_trades = await queries.get_option_trades(uid)
    web = await queries.get_web_activity(uid)
    xp = await queries.get_or_create_xp(uid)
    eng = await queries.get_engagement(uid)
    cards = await queries.get_card_stats(uid)

    return {
        **cards,
        "rounds": stats["rounds"],
        "total_wagered": stats["total_wagered"],
        "streak": await queries.get_casino_win_streak(uid),
        "max_profit": await queries.get_max_single_profit(uid),
        "distinct": await queries.get_distinct_games_played(uid),
        "duel_wins": duel["wins"],
        "tourney_wins": tourney["wins"],
        "balance": await queries.get_casino_balance(uid) or 0,
        # Betting
        "num_bets": len(bets),
        "bet_wins": sum(1 for b in bets if b.status == "won"),
        "pos_clv": sum(1 for b in bets if b.clv is not None and b.clv > 0),
        # Investing
        "num_trades": len(trades),
        "num_option_trades": len(option_trades),
        "distinct_holdings": len(open_positions),
        "realized_pnl": sum(p["realized_pnl"] for p in positions),
        "traded_crypto": any(t["ticker"].upper().endswith("-USD") for t in trades),
        "has_short": any(p["shares"] < 0 for p in open_positions),
        "has_options": len(options) > 0,
        "web_traded": any((t.get("notes") or "") == "via HQ" for t in trades),
        # Web / HQ
        "web_logins": web["logins"],
        "web_visits": web["visits"],
        "level": xp["level"],
        # Voice / Chat
        "voice_minutes": eng["voice_minutes"],
        "messages": eng["messages"],
    }


def _achievement_checks(s: dict) -> list[tuple[str, bool]]:
    """Map gathered stats → (achievement_id, earned?) conditions.

    Daily achievements (daily_1/all/7) are owned by the challenges cog.
    """
    return [
        ("first_game", s["rounds"] >= 1),
        ("play_10", s["rounds"] >= 10),
        ("play_100", s["rounds"] >= 100),
        ("play_500", s["rounds"] >= 500),
        ("first_win", s["max_profit"] > 0),
        ("streak_3", s["streak"] >= 3),
        ("streak_5", s["streak"] >= 5),
        ("streak_10", s["streak"] >= 10),
        ("big_win", s["max_profit"] >= 1000),
        ("jackpot", s["max_profit"] >= 5000),
        ("explore_5", s["distinct"] >= 5),
        ("explore_15", s["distinct"] >= 15),
        ("explore_all", s["distinct"] >= TOTAL_GAMES),
        ("duel_win", s["duel_wins"] >= 1),
        ("duel_10", s["duel_wins"] >= 10),
        ("tourney_win", s["tourney_wins"] >= 1),
        ("tourney_5", s["tourney_wins"] >= 5),
        ("coins_5k", s["balance"] >= 5_000),
        ("coins_25k", s["balance"] >= 25_000),
        ("coins_100k", s["balance"] >= 100_000),
        ("wager_50k", s["total_wagered"] >= 50_000),
        ("wager_500k", s["total_wagered"] >= 500_000),
        ("bet_first", s["num_bets"] >= 1),
        ("bet_10", s["num_bets"] >= 10),
        ("bet_50", s["num_bets"] >= 50),
        ("bet_win", s["bet_wins"] >= 1),
        ("clv_beat", s["pos_clv"] >= 1),
        ("clv_10", s["pos_clv"] >= 10),
        ("stock_first", s["num_trades"] >= 1),
        ("stock_10", s["num_trades"] >= 10),
        ("stock_50", s["num_trades"] >= 50),
        ("stock_diversified", s["distinct_holdings"] >= 10),
        ("stock_green", s["realized_pnl"] >= 1000),
        ("stock_bull", s["realized_pnl"] >= 10000),
        ("crypto_first", s["traded_crypto"]),
        ("stock_short", s["has_short"]),
        ("options_first", s["has_options"]),
        ("options_5", s["num_option_trades"] >= 5),
        ("bet_100", s["num_bets"] >= 100),
        ("web_login", s["web_logins"] >= 1),
        ("web_trade", s["web_traded"]),
        ("web_regular", s["web_visits"] >= 25),
        ("level_10", s["level"] >= 10),
        ("level_25", s["level"] >= 25),
        ("level_50", s["level"] >= 50),
        ("voice_1h", s["voice_minutes"] >= 60),
        ("voice_10h", s["voice_minutes"] >= 600),
        ("voice_50h", s["voice_minutes"] >= 3000),
        ("chat_100", s["messages"] >= 100),
        ("chat_1k", s["messages"] >= 1000),
        ("chat_10k", s["messages"] >= 10000),
        ("card_first", s["cards_total"] >= 1),
        ("card_50", s["cards_total"] >= 50),
        ("card_holo", s["cards_has_holo"]),
        ("card_gem", s["cards_has_gem"]),
        ("card_legendary", s["cards_has_legendary"]),
        ("card_1of1", s["cards_has_1of1"]),
        ("card_sets_5", s["cards_sets"] >= 5),
    ]


async def evaluate_user_achievements(uid: str) -> list[str]:
    """Unlock any earned-but-missing achievements for a user (idempotent — used
    by the live loop, on-demand views, action hooks, and the backfill). Awards
    XP for each newly unlocked one. Returns the list of newly-unlocked ids."""
    existing = {a["achievement_id"] for a in await queries.get_user_achievements(uid)}
    stats = await gather_achievement_stats(uid)
    newly_unlocked: list[str] = []
    for aid, condition in _achievement_checks(stats):
        if condition and aid not in existing:
            if await queries.unlock_achievement(uid, aid):
                await queries.add_xp(uid, ACHIEVEMENTS_BY_ID[aid].xp_reward)
                newly_unlocked.append(aid)
    return newly_unlocked


async def announce_achievements(bot, uid: str, newly: list[str], channel=None) -> None:
    """Send a celebratory unlock message. Posts in `channel` (pinging the user)
    when given — e.g. the channel where the bet/trade happened — otherwise DMs
    the user. Best-effort: never raises into the caller."""
    if channel is None:
        return  # never DM — announce only in the channel that triggered the unlock
    achs = [ACHIEVEMENTS_BY_ID[a] for a in newly if a in ACHIEVEMENTS_BY_ID]
    if not achs:
        return
    if len(achs) == 1:
        a = achs[0]
        title = "\U0001f3c6 Achievement Unlocked!"
        desc = f"{a.emoji} **{a.name}** — {a.description}\n`+{a.xp_reward} XP`"
    else:
        title = f"\U0001f3c6 {len(achs)} Achievements Unlocked!"
        desc = "\n".join(
            f"{a.emoji} **{a.name}** — {a.description} `+{a.xp_reward} XP`" for a in achs
        )
    embed = discord.Embed(title=title, description=desc, colour=0xF1C40F)
    embed.set_author(name="Achievement")
    embed.description = f"<@{uid}>\n{desc}"
    try:
        # No ping — the mention renders as a name but doesn't notify.
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        log.debug("could not announce achievements to %s", uid, exc_info=True)


# ── XP for actions + level-up announcements ──────────────────────────────────
XP_GAME = 10     # per casino game played
XP_BET = 40      # per bet logged (worth more — real-money tracking)
XP_STOCK = 40    # per stock/option/crypto trade logged
XP_MESSAGE = 1   # per chat message (rate-limited)
VC_TICK_MIN = 5  # voice-XP loop cadence (minutes)
XP_VC_TICK = 10  # XP per tick for active voice time (2 XP/min)
VOICE_MILESTONES = {60, 600, 3000}    # voice-minute thresholds with achievements
CHAT_MILESTONES = {100, 1000, 10000}  # message thresholds with achievements
_LEVELUP_CHANNEL_SETTING = "levelup_channel"
DEFAULT_LEVELUP_CHANNEL_ID = 1510694785034354849  # #games — server fallback


async def activity_channel(bot):
    """The server channel for level-ups / game achievements: the configured one,
    else the #games default. Never DMs."""
    raw = await queries.get_bot_setting(_LEVELUP_CHANNEL_SETTING)
    cid = int(raw) if (raw and raw.isdigit()) else DEFAULT_LEVELUP_CHANNEL_ID
    ch = bot.get_channel(cid)
    if ch is None:
        try:
            ch = await bot.fetch_channel(cid)
        except Exception:
            ch = None
    return ch


async def award_xp(bot, uid: str, amount: int, channel=None) -> None:
    """Add XP for an action. Announce a level-up ONLY in the channel that triggered
    it (never DM, never a fixed channel) and ONLY at 5-level MILESTONES (5, 10, 15,
    …), so routine leveling — especially chat XP — doesn't spam. A channel-less
    caller (the casino batch loop) awards XP silently. Never raises into the caller."""
    try:
        res = await queries.add_xp(uid, amount)
        if channel is not None and res.get("leveled_up") and (res["level"] // 5) > (res["old_level"] // 5):
            await _announce_levelup(bot, uid, res["level"], channel)
    except Exception:
        log.debug("award_xp failed for %s", uid, exc_info=True)


async def _announce_levelup(bot, uid: str, level: int, channel) -> None:
    if channel is None:
        return  # never DM / never fall back to a fixed channel
    next_xp = queries.xp_for_level(level + 1)
    embed = discord.Embed(
        title="\U0001f389 Level Up!",
        description=f"<@{uid}> just reached **Level {level}**! \U0001f680\nNext level at **{next_xp:,} XP**.",
        colour=_level_color(level),
    )
    try:
        # No ping — the mention renders as a name but doesn't notify.
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        log.debug("levelup announce failed for %s", uid, exc_info=True)


# ── Cog ──────────────────────────────────────────────────────────────────────


class ProgressionCog(commands.Cog):
    profile_group = app_commands.Group(name="profile", description="Player profiles, XP, levels & achievements")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._last_check_id = 0
        self._msg_cd: dict[int, float] = {}  # uid → last message-XP monotonic ts

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """1 XP per chat message (≤ once / 12s per user, to curb spam-farming).
        Level-ups post in the channel the message was sent in."""
        if message.author.bot or not message.guild or not message.content.strip():
            return
        if message.content.startswith(("/", "!", "?")):
            return
        import time
        now = time.monotonic()
        if now - self._msg_cd.get(message.author.id, 0) < 5:  # only blocks burst-spam
            return
        self._msg_cd[message.author.id] = now
        uid = str(message.author.id)
        await award_xp(self.bot, uid, XP_MESSAGE, message.channel)
        # First chat message of the (UTC) day pays casino coins — the main earn now that games
        # no longer top wallets up. A subtle 🪙 react signals it without spamming the channel.
        from datetime import datetime, timezone
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if await queries.grant_daily_message_reward(uid, day):
            try:
                await message.add_reaction("🪙")
            except discord.HTTPException:
                pass
        # Count toward Chat achievements; only re-evaluate (heavy) at a milestone.
        count = await queries.increment_message_count(uid)
        if count in CHAT_MILESTONES:
            newly = await evaluate_user_achievements(uid)
            if newly:
                await announce_achievements(self.bot, uid, newly, message.channel)

    async def cog_load(self) -> None:
        self.check_achievements.start()
        self.sync_discord_users.start()
        self.voice_xp.start()

    async def cog_unload(self) -> None:
        self.check_achievements.cancel()
        self.sync_discord_users.cancel()
        self.voice_xp.cancel()

    # ── /player ──────────────────────────────────────────────────────────────

    @profile_group.command(name="player", description="View a player profile (XP, level, achievements)")
    @app_commands.describe(user="View another user's profile (optional)")
    async def player(
        self, interaction: discord.Interaction, user: discord.Member | None = None,
    ) -> None:
        target = user or interaction.user
        uid = str(target.id)

        # Gather data concurrently
        xp_data = await queries.get_or_create_xp(uid)
        balance = await queries.get_or_create_casino_wallet(uid)
        stats = await queries.get_casino_stats(uid)
        duel_stats = await queries.get_duel_stats(uid)
        tourney_stats = await queries.get_tournament_stats(uid)
        achievements = await queries.get_user_achievements(uid)

        level = xp_data["level"]
        total_xp = xp_data["total_xp"]
        current_threshold = queries.xp_for_level(level)
        next_threshold = queries.xp_for_level(level + 1)
        xp_in_level = total_xp - current_threshold
        xp_needed = next_threshold - current_threshold
        pct = int(xp_in_level / xp_needed * 100) if xp_needed > 0 else 100

        bar = _progress_bar(xp_in_level, xp_needed)

        rounds = stats["rounds"]
        total_wagered = stats["total_wagered"]
        total_payout = stats["total_payout"]
        net = stats["net_profit"]
        win_rate = (
            round(total_payout / total_wagered * 100) if total_wagered > 0 else 0
        )
        # Win rate based on rounds won (payout > wagered) isn't available in a
        # single stat; approximate using ROI sign and raw counts is impractical.
        # We'll show net instead.

        net_str = f"+{net:,}c" if net >= 0 else f"{net:,}c"

        embed = discord.Embed(
            title=f"{target.display_name}'s Profile",
            colour=_level_color(level),
        )

        # Level / XP section
        embed.add_field(
            name=f"Level {level} \u2b50 ({total_xp:,}/{next_threshold:,} XP)",
            value=f"`[{bar}]` {pct}%",
            inline=False,
        )

        # Stats
        lines: list[str] = [
            f"**Balance:** {balance:,}c",
            f"**Games Played:** {rounds:,} | **Net Profit:** {net_str}",
        ]

        dw, dl = duel_stats["wins"], duel_stats["losses"]
        tw, te = tourney_stats["wins"], tourney_stats["entries"]
        if dw + dl > 0 or te > 0:
            parts: list[str] = []
            if dw + dl > 0:
                parts.append(f"**Duels:** {dw}W-{dl}L")
            if te > 0:
                parts.append(f"**Tournaments:** {tw}W ({te} played)")
            lines.append(" | ".join(parts))

        unlocked = len(achievements)
        total_ach = len(ALL_ACHIEVEMENTS)
        lines.append(f"**Achievements:** {unlocked}/{total_ach} unlocked")

        embed.add_field(name="Stats", value="\n".join(lines), inline=False)

        embed.set_thumbnail(url=target.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    # ── /achievements ────────────────────────────────────────────────────────

    @profile_group.command(name="achievements", description="View all achievements and progress")
    @app_commands.describe(user="View another user's achievements (optional)")
    async def achievements(
        self, interaction: discord.Interaction, user: discord.Member | None = None,
    ) -> None:
        target = user or interaction.user
        uid = str(target.id)

        # Evaluate first so opening the panel unlocks anything newly earned
        # (covers users whose activity is betting/investing, not casino).
        try:
            await evaluate_user_achievements(uid)
        except Exception:
            log.warning("achievement evaluation on view failed for %s", uid, exc_info=True)

        unlocked_list = await queries.get_user_achievements(uid)
        unlocked_ids = {a["achievement_id"] for a in unlocked_list}

        # Gather progress data for locked achievements
        progress_values = await gather_achievement_stats(uid)

        total_ach = len(ALL_ACHIEVEMENTS)
        unlocked_count = len(unlocked_ids)

        embed = discord.Embed(
            title=f"\U0001f3c6 Achievements \u2014 {unlocked_count}/{total_ach} unlocked",
            colour=0xF1C40F,
        )

        for cat in _CATEGORIES:
            cat_achievements = [a for a in ALL_ACHIEVEMENTS if a.category == cat]
            lines: list[str] = []
            for ach in cat_achievements:
                if ach.id in unlocked_ids:
                    lines.append(f"{ach.emoji} **{ach.name}** \u2014 {ach.description} \u2705")
                else:
                    # Show progress if applicable
                    progress_info = _PROGRESS_TARGETS.get(ach.id)
                    if progress_info is not None:
                        key, target_val = progress_info
                        current_val = progress_values.get(key, 0)
                        prog = f" ({current_val:,}/{target_val:,})"
                    else:
                        prog = ""
                    lines.append(
                        f"{ach.emoji} **{ach.name}** \u2014 {ach.description}{prog} \U0001f512"
                    )

            embed.add_field(
                name=f"\u2500\u2500 {cat} \u2500\u2500",
                value="\n".join(lines),
                inline=False,
            )

        embed.set_footer(text=f"Viewing: {target.display_name}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /level ───────────────────────────────────────────────────────────────

    @profile_group.command(name="level", description="Quick level check")
    @app_commands.describe(user="Check another user's level (optional)")
    async def level(
        self, interaction: discord.Interaction, user: discord.Member | None = None,
    ) -> None:
        target = user or interaction.user
        uid = str(target.id)

        xp_data = await queries.get_or_create_xp(uid)
        level = xp_data["level"]
        total_xp = xp_data["total_xp"]
        next_threshold = queries.xp_for_level(level + 1)
        remaining = next_threshold - total_xp

        embed = discord.Embed(
            description=(
                f"**{target.display_name}**\n"
                f"Level {level} \u2b50 \u2014 {total_xp:,}/{next_threshold:,} XP\n"
                f"Next level in {remaining:,} XP"
            ),
            colour=_level_color(level),
        )
        await interaction.response.send_message(embed=embed)

    # ── Background username sync (for web leaderboard) ──────────────────────

    @tasks.loop(minutes=30)
    async def sync_discord_users(self) -> None:
        """Populate discord_users cache for the web leaderboard."""
        from db.schema import DB_PATH
        import aiosqlite
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                """SELECT DISTINCT discord_user FROM (
                    SELECT discord_user FROM casino_wallets
                    UNION
                    SELECT discord_user FROM user_xp
                    UNION
                    SELECT discord_user FROM paper_bets
                )"""
            )
            all_users = [row[0] for row in await cursor.fetchall()]

        for uid in all_users:
            if not uid.isdigit():
                continue  # skip web session IDs like 'web_90c4adcbc8f2'
            try:
                user = await self.bot.fetch_user(int(uid))
                await queries.upsert_discord_user(
                    uid, user.display_name, str(user.display_avatar.url),
                )
            except Exception:
                log.warning("Failed to sync Discord user %s", uid, exc_info=True)

    @sync_discord_users.before_loop
    async def before_sync(self) -> None:
        await self.bot.wait_until_ready()

    # ── Background achievement checker ───────────────────────────────────────

    @profile_group.command(name="setchannel", description="Admin: set the channel for level-up announcements")
    @app_commands.describe(channel="Where level-up messages are posted")
    async def levelchannel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("You need **Manage Server**.", ephemeral=True)
            return
        await queries.set_bot_setting(_LEVELUP_CHANNEL_SETTING, str(channel.id))
        await interaction.response.send_message(f"✅ Level-ups will post in {channel.mention}.", ephemeral=True)

    @profile_group.command(name="odds", description="Choose how odds are shown to you (american / decimal / probability)")
    @app_commands.describe(format="Your preferred odds format")
    @app_commands.choices(format=[
        app_commands.Choice(name="American (-110 / +150)", value="american"),
        app_commands.Choice(name="Decimal (1.91)", value="decimal"),
        app_commands.Choice(name="Probability (52.4%)", value="probability"),
    ])
    async def odds_format(self, interaction: discord.Interaction, format: str) -> None:
        await queries.set_user_odds_format(str(interaction.user.id), format)
        await interaction.response.send_message(
            f"✅ Your odds will now display as **{format}** (e.g. in `/bet view`).", ephemeral=True)

    @profile_group.command(name="books", description="Set which sportsbooks you have — personalizes /odds best")
    async def my_books(self, interaction: discord.Interaction) -> None:
        current = await queries.get_user_books(str(interaction.user.id))
        await interaction.response.send_message(
            "Select every sportsbook you have an account on — `/odds best` will then also "
            "show the best line **among your books**:",
            view=_BookSelectView(current), ephemeral=True)

    @tasks.loop(seconds=30)
    async def check_achievements(self) -> None:
        try:
            new_entries = await queries.get_casino_history_since(self._last_check_id)
            if not new_entries:
                return
            self._last_check_id = new_entries[-1]["id"]
            from collections import Counter
            plays = Counter(e["discord_user"] for e in new_entries)
            # Casino plays carry no channel in casino_history, so this batch loop
            # awards XP + unlocks achievements SILENTLY (no DM, no fixed channel).
            # In-channel actions (bets/stocks/web/chat) announce at their call site.
            for uid, n in plays.items():
                try:
                    await award_xp(self.bot, uid, n * XP_GAME, None)  # silent XP
                    await evaluate_user_achievements(uid)             # silent unlock
                except Exception:
                    log.warning("Failed to check achievements for user %s", uid, exc_info=True)
        except Exception:
            log.exception("Unhandled error in check_achievements loop")

    @check_achievements.before_loop
    async def before_check(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=VC_TICK_MIN)
    async def voice_xp(self) -> None:
        """Award XP to everyone actively hanging in a voice channel. Anti-farm:
        only counts a member if ≥2 non-bot humans share the channel, it isn't the
        AFK channel, and they aren't self-deafened (i.e. actually present). Silent
        (no channel context), like the casino loop."""
        try:
            for guild in self.bot.guilds:
                afk_id = guild.afk_channel.id if guild.afk_channel else None
                for vc in guild.voice_channels:
                    if vc.id == afk_id:
                        continue
                    humans = [m for m in vc.members if not m.bot]
                    if len(humans) < 2:
                        continue
                    for m in humans:
                        vs = m.voice
                        if vs is None or vs.self_deaf or vs.deaf:
                            continue
                        try:
                            await award_xp(self.bot, str(m.id), XP_VC_TICK, None)
                            total = await queries.add_voice_minutes(str(m.id), VC_TICK_MIN)
                            if total in VOICE_MILESTONES:  # re-evaluate only at a milestone
                                await evaluate_user_achievements(str(m.id))  # silent (no text channel)
                        except Exception:
                            log.debug("voice xp failed for %s", m.id, exc_info=True)
        except Exception:
            log.exception("Unhandled error in voice_xp loop")

    @voice_xp.before_loop
    async def before_voice_xp(self) -> None:
        await self.bot.wait_until_ready()

    async def _check_user_achievements(self, uid: str) -> None:
        await evaluate_user_achievements(uid)


class _BookSelect(discord.ui.Select):
    def __init__(self, current: list[str]) -> None:
        from .odds import MY_BOOK_CHOICES
        opts = [discord.SelectOption(label=label, value=key, default=(key in current))
                for key, label in MY_BOOK_CHOICES]
        super().__init__(placeholder="Pick the books you have…", min_values=0,
                         max_values=len(opts), options=opts)

    async def callback(self, interaction: discord.Interaction) -> None:
        from .odds import BOOK_LABELS
        await queries.set_user_books(str(interaction.user.id), self.values)
        names = ", ".join(BOOK_LABELS.get(b, b) for b in self.values) or "none"
        await interaction.response.edit_message(
            content=f"✅ Saved your books: **{names}**.\n`/odds best` now also shows the best line among them.",
            view=None)


class _BookSelectView(discord.ui.View):
    def __init__(self, current: list[str]) -> None:
        super().__init__(timeout=120)
        self.add_item(_BookSelect(current))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ProgressionCog(bot))
