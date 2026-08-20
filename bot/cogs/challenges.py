"""Daily challenges — deterministic daily quests that reward coins and XP."""
from __future__ import annotations

import logging
import random
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import discord
from discord import app_commands
from discord.ext import commands, tasks

from db import queries

log = logging.getLogger(__name__)

# ── Game categories ──────────────────────────────────────────────────────────

CARD_GAMES = {"blackjack", "baccarat", "paigow", "uth", "videopoker", "hilo"}
TABLE_GAMES = {"roulette", "craps", "crash", "plinko", "slots"}
PARTY_GAMES = {"bingo", "horserace", "stockmarket", "liarsdice", "penalties"}
BRAIN_GAMES = {"math24", "countdown", "mastermind", "geography", "wordle", "nba-trivia", "nfl-trivia", "sudoku"}
SIM_GAMES = {"nbasim", "nflsim", "mlbsim"}

# ── Rewards ──────────────────────────────────────────────────────────────────

CHALLENGE_COINS = 100
BONUS_COINS = 200
CHALLENGE_XP = 50

# ── Challenge checks ─────────────────────────────────────────────────────────


def _check_play_3_games(history: list[dict]) -> bool:
    return len({h["game"] for h in history}) >= 3


def _check_win_any(history: list[dict]) -> bool:
    return any(h["payout"] > h["wagered"] for h in history)


def _check_win_3(history: list[dict]) -> bool:
    return sum(1 for h in history if h["payout"] > h["wagered"]) >= 3


def _check_play_5_rounds(history: list[dict]) -> bool:
    return len(history) >= 5


def _check_play_card(history: list[dict]) -> bool:
    return any(h["game"] in CARD_GAMES for h in history)


def _check_play_brain(history: list[dict]) -> bool:
    return any(h["game"] in BRAIN_GAMES for h in history)


def _check_play_table(history: list[dict]) -> bool:
    return any(h["game"] in TABLE_GAMES for h in history)


def _check_play_party(history: list[dict]) -> bool:
    return any(h["game"] in PARTY_GAMES for h in history)


def _check_profit_500(history: list[dict]) -> bool:
    return any(h["payout"] - h["wagered"] >= 500 for h in history)


def _check_wager_1000(history: list[dict]) -> bool:
    return sum(h["wagered"] for h in history) >= 1000


def _check_play_new(history: list[dict]) -> bool:
    """A game that appears exactly once in today's history (first time today)."""
    if not history:
        return False
    counts = Counter(h["game"] for h in history)
    return any(c == 1 for c in counts.values())


def _check_win_streak_2(history: list[dict]) -> bool:
    """Two consecutive wins in chronological order."""
    prev_win = False
    for h in history:
        won = h["payout"] > h["wagered"]
        if won and prev_win:
            return True
        prev_win = won
    return False


def _check_big_bet(history: list[dict]) -> bool:
    return any(h["wagered"] >= 200 for h in history)


def _check_play_sim(history: list[dict]) -> bool:
    return any(h["game"] in SIM_GAMES for h in history)


def _check_break_even(history: list[dict]) -> bool:
    if not history:
        return False
    return sum(h["payout"] for h in history) >= sum(h["wagered"] for h in history)


# ── Challenge templates ──────────────────────────────────────────────────────


@dataclass
class ChallengeTemplate:
    key: str
    description: str
    check: Callable[[list[dict]], bool]


ALL_CHALLENGES: list[ChallengeTemplate] = [
    ChallengeTemplate("play_3_games", "Play 3 different games", _check_play_3_games),
    ChallengeTemplate("win_any", "Win any game", _check_win_any),
    ChallengeTemplate("win_3", "Win 3 games", _check_win_3),
    ChallengeTemplate("play_5_rounds", "Play 5 rounds", _check_play_5_rounds),
    ChallengeTemplate("play_card", "Play a Card Game", _check_play_card),
    ChallengeTemplate("play_brain", "Play a Brain Game", _check_play_brain),
    ChallengeTemplate("play_table", "Play a Table/Arcade Game", _check_play_table),
    ChallengeTemplate("play_party", "Play a Party Game", _check_play_party),
    ChallengeTemplate("profit_500", "Profit 500+ in one round", _check_profit_500),
    ChallengeTemplate("wager_1000", "Wager 1000+ total", _check_wager_1000),
    ChallengeTemplate("play_new", "Play a game for the first time today", _check_play_new),
    ChallengeTemplate("win_streak_2", "Win 2 in a row", _check_win_streak_2),
    ChallengeTemplate("big_bet", "Place a bet of 200+", _check_big_bet),
    ChallengeTemplate("play_sim", "Play a Sports Sim", _check_play_sim),
    ChallengeTemplate("break_even", "Break even or better today", _check_break_even),
]

_TEMPLATE_MAP: dict[str, ChallengeTemplate] = {c.key: c for c in ALL_CHALLENGES}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _get_todays_challenge_ids(date: str) -> list[str]:
    """Deterministic 3 challenges for a date. Same for everyone."""
    rng = random.Random(date)
    return [c.key for c in rng.sample(ALL_CHALLENGES, 3)]


def _get_template(key: str) -> ChallengeTemplate | None:
    return _TEMPLATE_MAP.get(key)


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _format_date_display(date: str) -> str:
    """'2026-04-22' -> 'April 22, 2026'."""
    dt = datetime.strptime(date, "%Y-%m-%d")
    return dt.strftime("%B %d, %Y").replace(" 0", " ")


# ── Cog ──────────────────────────────────────────────────────────────────────


class ChallengesCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._last_check_id: int = 0

    async def cog_load(self) -> None:
        # Seed _last_check_id to current max so we only process new entries
        recent = await queries.get_casino_history_since(0)
        if recent:
            self._last_check_id = recent[-1]["id"]
        self.check_challenges.start()

    async def cog_unload(self) -> None:
        self.check_challenges.cancel()

    # ── Background task ──────────────────────────────────────────────────────

    @tasks.loop(seconds=30)
    async def check_challenges(self) -> None:
        try:
            new_entries = await queries.get_casino_history_since(self._last_check_id)
            if not new_entries:
                return
            self._last_check_id = new_entries[-1]["id"]

            users = {e["discord_user"] for e in new_entries}
            today = _today_str()
            challenge_ids = _get_todays_challenge_ids(today)

            for uid in users:
                slots = await queries.get_daily_challenge_slots(uid, today, challenge_ids)
                history = await queries.get_todays_casino_history(uid, today)

                for slot in slots:
                    if slot["completed"]:
                        continue
                    template = _get_template(slot["challenge_id"])
                    if template and template.check(history):
                        await queries.complete_daily_challenge(uid, today, slot["slot"], CHALLENGE_COINS)
                        await queries.add_xp(uid, CHALLENGE_XP)

                # Check all-3 bonus
                # Re-fetch slots to see updated completed state
                slots = await queries.get_daily_challenge_slots(uid, today, challenge_ids)
                completed_count = sum(1 for s in slots if s["completed"])
                if completed_count == 3:
                    already_claimed = await queries.is_daily_bonus_claimed(uid, today)
                    if not already_claimed:
                        await queries.claim_daily_bonus(uid, today, BONUS_COINS)
        except Exception:
            log.exception("Error in daily challenges background task")

    @check_challenges.before_loop
    async def before_check(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ChallengesCog(bot))
