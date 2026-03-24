"""CLV auto-post — fires when a close snapshot lands, posts closing lines and pings bettors."""
from __future__ import annotations

import os
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

from db import queries
from shared.models import Bet, Game
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


def _build_closing_lines_field(kalshi_close, dk_close, dk_open=None) -> str:
    """Build a summary of the actual closing line numbers, with spread movement if available."""
    lines = []

    if kalshi_close:
        p = kalshi_close.payload
        ml_home = p.get("ml_home")
        ml_away = p.get("ml_away")
        if ml_home is not None and ml_away is not None:
            lines.append(f"ML (Kalshi): **{_fmt_odds(ml_home)}** / **{_fmt_odds(ml_away)}**")

    ref = dk_close or kalshi_close
    if ref:
        p = ref.payload
        spread = p.get("spread")
        spread_odds = p.get("spread_odds")
        total = p.get("total")
        total_over = p.get("total_over_odds")
        total_under = p.get("total_under_odds")
        if spread is not None and spread_odds is not None:
            open_spread = dk_open.payload.get("spread") if dk_open else None
            if open_spread is not None and open_spread != spread:
                lines.append(
                    f"Spread: **{open_spread:+.1f} → {spread:+.1f}** ({_fmt_odds(spread_odds)})"
                )
            else:
                lines.append(f"Spread: **{spread:+.1f}** ({_fmt_odds(spread_odds)})")
        if total is not None:
            over_str = _fmt_odds(total_over) if total_over is not None else "n/a"
            under_str = _fmt_odds(total_under) if total_under is not None else "n/a"
            lines.append(f"Total: **{total}** (O {over_str} / U {under_str})")

    return "\n".join(lines) if lines else "No line data available"


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

        game_ids = await queries.get_games_with_close_not_posted()
        for game_id in game_ids:
            game = await queries.get_game_by_id(game_id)
            if game is None:
                continue

            kalshi_close = await queries.get_close_snapshot(game_id, "kalshi")
            dk_close = await queries.get_any_close_snapshot(game_id)

            if kalshi_close is None and dk_close is None:
                continue

            dk_open = await queries.get_first_poll_snapshot(game_id, "draftkings")

            ref_snap = kalshi_close or dk_close
            captured_dt = datetime.fromisoformat(ref_snap.captured_at_utc_iso)
            if captured_dt.tzinfo is None:
                captured_dt = captured_dt.replace(tzinfo=timezone.utc)
            captured_str = captured_dt.strftime("%H:%M UTC")
            sources = ", ".join(
                s for s, snap in [("Kalshi", kalshi_close), ("DraftKings", dk_close)]
                if snap is not None
            )

            embed = discord.Embed(
                title=f"Closing Lines — {game.away_team} @ {game.home_team}",
                description=(
                    f"Source: **{sources}** at {captured_str}\n"
                    f"Positive CLV = you beat the close (lower implied prob than closing line)"
                ),
                color=0xF1C40F,
            )

            # Always show the closing line numbers
            embed.add_field(
                name="Close",
                value=_build_closing_lines_field(kalshi_close, dk_close, dk_open),
                inline=False,
            )

            # If anyone has bets, compute CLV and add their lines
            bets = await queries.get_open_bets_for_game(game_id)
            mention_ids: set[str] = set()
            for bet in bets:
                ref = kalshi_close if (bet.market.lower() == "moneyline" and kalshi_close) else dk_close
                close_odds = _close_odds_for_bet(bet, game, ref.payload) if ref else None
                clv: float | None = None
                if close_odds is not None:
                    bet_prob = american_to_prob(bet.odds)
                    close_prob = american_to_prob(close_odds)
                    clv = (close_prob - bet_prob) * 100
                await queries.update_bet_clv(bet.bet_id, clv)
                embed.add_field(
                    name=f"<@{bet.discord_user}>",
                    value=_build_bet_line(bet, close_odds, clv),
                    inline=False,
                )
                mention_ids.add(bet.discord_user)

            # Ping bettors in message content so Discord actually notifies them
            ping_str = " ".join(f"<@{uid}>" for uid in mention_ids) if mention_ids else None
            await channel.send(content=ping_str, embed=embed)
            await queries.mark_game_clv_posted(game_id)

    @clv_check.before_loop
    async def before_clv_check(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CLVCog(bot))
