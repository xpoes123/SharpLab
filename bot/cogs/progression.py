"""Progression cog — achievements, leveling, and player profiles."""

from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.cogs.casino import GAME_LABELS
from db import queries
import logging
log = logging.getLogger(__name__)

# ── Achievement definitions ──────────────────────────────────────────────────

TOTAL_GAMES = 28


@dataclass
class Achievement:
    id: str
    name: str
    description: str
    category: str  # "Progression", "Winning", "Diversity", "Social", "Daily", "Wealth"
    emoji: str
    xp_reward: int


ALL_ACHIEVEMENTS: list[Achievement] = [
    # ── Progression ──
    Achievement("first_game", "First Steps", "Play your first casino game", "Progression", "\U0001f3ae", 10),
    Achievement("play_10", "Regular", "Play 10 casino games", "Progression", "\U0001f3af", 25),
    Achievement("play_100", "Grinder", "Play 100 casino games", "Progression", "\u2699\ufe0f", 100),
    Achievement("play_500", "Veteran", "Play 500 casino games", "Progression", "\U0001f396\ufe0f", 250),
    # ── Winning ──
    Achievement("first_win", "Winner", "Win your first casino game", "Winning", "\U0001f3c6", 10),
    Achievement("streak_3", "Hot Streak", "Win 3 games in a row", "Winning", "\U0001f525", 50),
    Achievement("streak_5", "On Fire", "Win 5 games in a row", "Winning", "\U0001f525", 100),
    Achievement("streak_10", "Unstoppable", "Win 10 games in a row", "Winning", "\U0001f4a5", 250),
    Achievement("big_win", "Big Winner", "Profit 1000+ coins in one game", "Winning", "\U0001f4b0", 50),
    Achievement("jackpot", "Jackpot", "Profit 5000+ coins in one game", "Winning", "\U0001f48e", 150),
    # ── Diversity ──
    Achievement("explore_5", "Explorer", "Play 5 different games", "Diversity", "\U0001f5fa\ufe0f", 25),
    Achievement("explore_15", "World Traveler", "Play 15 different games", "Diversity", "\U0001f30d", 75),
    Achievement("explore_all", "Completionist", "Play every casino game at least once", "Diversity", "\u2b50", 200),
    # ── Social ──
    Achievement("duel_win", "Challenger", "Win a duel", "Social", "\u2694\ufe0f", 25),
    Achievement("duel_10", "Duelist", "Win 10 duels", "Social", "\U0001f5e1\ufe0f", 100),
    Achievement("tourney_win", "Tournament Victor", "Win a tournament", "Social", "\U0001f3c5", 150),
    Achievement("tourney_5", "Champion", "Win 5 tournaments", "Social", "\U0001f451", 500),
    # ── Daily ──
    Achievement("daily_1", "Task Master", "Complete a daily challenge", "Daily", "\U0001f4cb", 15),
    Achievement("daily_all", "Dedicated", "Complete all 3 daily challenges in one day", "Daily", "\U0001f4cb", 50),
    Achievement("daily_7", "Devoted", "Complete all daily challenges 7 days in a row", "Daily", "\U0001f5d3\ufe0f", 200),
    # ── Wealth ──
    Achievement("coins_5k", "Thousandaire", "Reach 5,000 coin balance", "Wealth", "\U0001f4b5", 25),
    Achievement("coins_25k", "Wealthy", "Reach 25,000 coin balance", "Wealth", "\U0001f4b0", 75),
    Achievement("coins_100k", "Mogul", "Reach 100,000 coin balance", "Wealth", "\U0001f911", 200),
    Achievement("wager_50k", "High Roller", "Wager 50,000+ total coins", "Wealth", "\U0001f3b2", 100),
    Achievement("wager_500k", "Whale", "Wager 500,000+ total coins", "Wealth", "\U0001f40b", 300),
]

ACHIEVEMENTS_BY_ID: dict[str, Achievement] = {a.id: a for a in ALL_ACHIEVEMENTS}

# Ordered category list for display
_CATEGORIES = ["Progression", "Winning", "Diversity", "Social", "Daily", "Wealth"]

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


# ── Cog ──────────────────────────────────────────────────────────────────────


class ProgressionCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._last_check_id = 0

    async def cog_load(self) -> None:
        self.check_achievements.start()
        self.sync_discord_users.start()

    async def cog_unload(self) -> None:
        self.check_achievements.cancel()
        self.sync_discord_users.cancel()

    # ── /player ──────────────────────────────────────────────────────────────

    @app_commands.command(name="player", description="View a player profile (XP, level, achievements)")
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

    @app_commands.command(name="achievements", description="View all achievements and progress")
    @app_commands.describe(user="View another user's achievements (optional)")
    async def achievements(
        self, interaction: discord.Interaction, user: discord.Member | None = None,
    ) -> None:
        target = user or interaction.user
        uid = str(target.id)

        unlocked_list = await queries.get_user_achievements(uid)
        unlocked_ids = {a["achievement_id"] for a in unlocked_list}

        # Gather progress data for locked achievements
        stats = await queries.get_casino_stats(uid)
        streak = await queries.get_casino_win_streak(uid)
        distinct = await queries.get_distinct_games_played(uid)
        max_profit = await queries.get_max_single_profit(uid)
        duel_stats = await queries.get_duel_stats(uid)
        tourney_stats = await queries.get_tournament_stats(uid)
        balance = await queries.get_casino_balance(uid) or 0

        progress_values: dict[str, int] = {
            "rounds": stats["rounds"],
            "streak": streak,
            "distinct": distinct,
            "max_profit": max_profit,
            "duel_wins": duel_stats["wins"],
            "tourney_wins": tourney_stats["wins"],
            "balance": balance,
            "total_wagered": stats["total_wagered"],
        }

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

    @app_commands.command(name="level", description="Quick level check")
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

    @tasks.loop(seconds=30)
    async def check_achievements(self) -> None:
        new_entries = await queries.get_casino_history_since(self._last_check_id)
        if not new_entries:
            return
        self._last_check_id = new_entries[-1]["id"]
        users = {e["discord_user"] for e in new_entries}
        for uid in users:
            await self._check_user_achievements(uid)

    @check_achievements.before_loop
    async def before_check(self) -> None:
        await self.bot.wait_until_ready()

    async def _check_user_achievements(self, uid: str) -> None:
        existing = {a["achievement_id"] for a in await queries.get_user_achievements(uid)}

        stats = await queries.get_casino_stats(uid)
        rounds = stats["rounds"]
        total_wagered = stats["total_wagered"]

        # Progression
        checks: list[tuple[str, bool]] = [
            ("first_game", rounds >= 1),
            ("play_10", rounds >= 10),
            ("play_100", rounds >= 100),
            ("play_500", rounds >= 500),
        ]

        # Winning
        streak = await queries.get_casino_win_streak(uid)
        max_profit = await queries.get_max_single_profit(uid)
        checks += [
            ("first_win", max_profit > 0),
            ("streak_3", streak >= 3),
            ("streak_5", streak >= 5),
            ("streak_10", streak >= 10),
            ("big_win", max_profit >= 1000),
            ("jackpot", max_profit >= 5000),
        ]

        # Diversity
        distinct = await queries.get_distinct_games_played(uid)
        checks += [
            ("explore_5", distinct >= 5),
            ("explore_15", distinct >= 15),
            ("explore_all", distinct >= TOTAL_GAMES),
        ]

        # Social
        duel_stats = await queries.get_duel_stats(uid)
        tourney_stats = await queries.get_tournament_stats(uid)
        checks += [
            ("duel_win", duel_stats["wins"] >= 1),
            ("duel_10", duel_stats["wins"] >= 10),
            ("tourney_win", tourney_stats["wins"] >= 1),
            ("tourney_5", tourney_stats["wins"] >= 5),
        ]

        # Wealth
        balance = await queries.get_casino_balance(uid) or 0
        checks += [
            ("coins_5k", balance >= 5_000),
            ("coins_25k", balance >= 25_000),
            ("coins_100k", balance >= 100_000),
            ("wager_50k", total_wagered >= 50_000),
            ("wager_500k", total_wagered >= 500_000),
        ]

        # Note: daily achievements (daily_1, daily_all, daily_7) are checked
        # by the challenges cog when it completes challenges, not here.

        for aid, condition in checks:
            if aid not in existing and condition:
                newly = await queries.unlock_achievement(uid, aid)
                if newly:
                    ach = ACHIEVEMENTS_BY_ID[aid]
                    await queries.add_xp(uid, ach.xp_reward)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ProgressionCog(bot))
