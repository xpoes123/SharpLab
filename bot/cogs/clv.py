"""CLV auto-post — background task that fires when a close snapshot lands for a game with logged bets."""
from __future__ import annotations

import os
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

from db import queries
from shared.models import Bet, Game, OddsSnapshot
from shared.odds_utils import american_to_prob

load_dotenv()

CLV_CHANNEL_ID = int(os.getenv("CLV_CHANNEL_ID", "1485475287054418151"))


# ── CLV computation ───────────────────────────────────────────────────────────

def _close_odds_for_bet(bet: Bet, game: Game, payload: dict) -> int | None:
    """Match a bet's market + side to the right field in the close snapshot payload."""
    market = bet.market.lower()
    side = bet.side.lower()
    home = game.home_team.lower()
    away = game.away_team.lower()

    if market == "moneyline":
        if side in home or home.split()[-1] in side:
            return payload.get("ml_home")
        if side in away or away.split()[-1] in side:
            return payload.get("ml_away")

    elif market == "spread":
        if side in home or home.split()[-1] in side:
            return payload.get("spread_odds")
        # Away spread odds not stored in current payload shape

    elif market == "total":
        if side == "over":
            return payload.get("total_over_odds")
        if side == "under":
            return payload.get("total_under_odds")

    return None


# ── Formatting ────────────────────────────────────────────────────────────────

def _fmt_odds(odds: int) -> str:
    return f"+{odds}" if odds > 0 else str(odds)


def _fmt_clv(clv: float) -> str:
    sign = "+" if clv >= 0 else ""
    return f"{sign}{clv:.1f} pp"


def _clv_emoji(clv: float) -> str:
    if clv > 0.5:
        return "✅"
    if clv < -0.5:
        return "❌"
    return "➖"


def _build_bet_line(bet: Bet, close_odds: int | None, clv: float | None) -> str:
    market_str = bet.market.capitalize()
    line_str = f" {bet.line:+.1f}" if bet.line is not None else ""
    desc = f"{market_str}{line_str} {bet.side} @ **{_fmt_odds(bet.odds)}**"

    if close_odds is None:
        return f"{desc}\n→ no close data for this market ➖"

    clv_str = _fmt_clv(clv) if clv is not None else "n/a"
    emoji = _clv_emoji(clv) if clv is not None else "➖"
    return f"{desc}\n→ closed **{_fmt_odds(close_odds)}** | CLV **{clv_str}** {emoji}"


# ── Cog ───────────────────────────────────────────────────────────────────────

class CLVCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.clv_check.start()

    def cog_unload(self) -> None:
        self.clv_check.cancel()

    @tasks.loop(minutes=5)
    async def clv_check(self) -> None:
        channel = self.bot.get_channel(CLV_CHANNEL_ID)
        if channel is None:
            return

        game_ids = await queries.get_games_with_close_and_open_bets()
        for game_id in game_ids:
            game = await queries.get_game_by_id(game_id)
            close = await queries.get_any_close_snapshot(game_id)
            bets = await queries.get_open_bets_for_game(game_id)

            if game is None or close is None or not bets:
                continue

            # Compute CLV for each bet, write to DB, then post
            results: list[tuple[Bet, int | None, float | None]] = []
            for bet in bets:
                close_odds = _close_odds_for_bet(bet, game, close.payload)
                clv: float | None = None
                if close_odds is not None:
                    bet_prob = american_to_prob(bet.odds)
                    close_prob = american_to_prob(close_odds)
                    clv = (close_prob - bet_prob) * 100  # probability points
                results.append((bet, close_odds, clv))
                await queries.update_bet_clv(bet.bet_id, clv)

            captured_dt = datetime.fromisoformat(close.captured_at_utc_iso)
            if captured_dt.tzinfo is None:
                captured_dt = captured_dt.replace(tzinfo=timezone.utc)
            captured_str = captured_dt.strftime("%H:%M UTC")

            embed = discord.Embed(
                title=f"Closing Lines — {game.away_team} @ {game.home_team}",
                description=(
                    f"Source: **{close.source.capitalize()}** at {captured_str}\n"
                    f"Positive CLV = you beat the close (lower implied prob than closing line)"
                ),
                color=0xF1C40F,
            )
            for bet, close_odds, clv in results:
                embed.add_field(
                    name=f"<@{bet.discord_user}>",
                    value=_build_bet_line(bet, close_odds, clv),
                    inline=False,
                )

            await channel.send(embed=embed)

    @clv_check.before_loop
    async def before_clv_check(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CLVCog(bot))
