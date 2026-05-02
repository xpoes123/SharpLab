"""CLV auto-post — fires when a close snapshot lands, posts closing lines and pings bettors."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

from db import queries
from shared.models import Bet, Game
from shared.odds_utils import compute_clv, fmt_prob, side_is_home

load_dotenv()


# ── CLV computation ───────────────────────────────────────────────────────────

def _close_odds_for_bet(bet: Bet, game: Game, payload: dict) -> int | None:
    """Match a bet's market + side to the right field in the close snapshot payload."""
    market = bet.market.lower()
    side = bet.side.lower()
    home = game.home_team.lower()
    away = game.away_team.lower()

    if market == "moneyline":
        if side in home and side not in away:
            return payload.get("ml_home")
        if side in away and side not in home:
            return payload.get("ml_away")

    elif market == "spread":
        if side in home and side not in away:
            return payload.get("spread_odds")
        if side in away and side not in home:
            return payload.get("spread_odds")  # away_odds not stored separately; juice is typically identical

    elif market == "total":
        if side == "over":
            return payload.get("total_over_odds")
        if side == "under":
            return payload.get("total_under_odds")

    return None


# ── Formatting ────────────────────────────────────────────────────────────────

def _fmt_odds(odds: int) -> str:
    return fmt_prob(odds)


def _fmt_clv(clv: float) -> str:
    sign = "+" if clv >= 0 else ""
    return f"{sign}{clv:.1f} pp"


def _clv_emoji(clv: float) -> str:
    if clv > 0.5:
        return "✅"
    if clv < -0.5:
        return "❌"
    return "➖"


def _build_bet_line(
    bet: Bet, close_odds: int | None, clv: float | None,
    close_line_display: str | None = None,
) -> str:
    market_str = bet.market.capitalize()
    line_str = f" {bet.line:+.1f}" if bet.line is not None else ""
    desc = f"{market_str}{line_str} {bet.side} @ **{_fmt_odds(bet.odds)}**"

    if close_odds is None:
        return f"{desc}\n→ no close data for this market ➖"

    clv_str = _fmt_clv(clv) if clv is not None else "n/a"
    emoji = _clv_emoji(clv) if clv is not None else "➖"
    if close_line_display is not None:
        return f"{desc}\n→ closed at **{close_line_display}** ({_fmt_odds(close_odds)}) | CLV **{clv_str}** {emoji}"
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
        _channel_id_str = os.getenv("CLV_CHANNEL_ID")
        if _channel_id_str:
            self.clv_channel_id: int | None = int(_channel_id_str)
        else:
            self.clv_channel_id = None
            log.warning("CLV_CHANNEL_ID environment variable is not set — CLV auto-post disabled")
        self.clv_check.start()

    def cog_unload(self) -> None:
        self.clv_check.cancel()

    @tasks.loop(minutes=5)
    async def clv_check(self) -> None:
        if self.clv_channel_id is None:
            return
        channel = self.bot.get_channel(self.clv_channel_id)
        if channel is None:
            log.warning(
                "CLV channel %d not found — bot may not be in that server or the channel was deleted",
                self.clv_channel_id,
            )
            return

        game_ids = await queries.get_games_with_close_not_posted()
        for game_id in game_ids:
            try:
                game = await queries.get_game_by_id(game_id)
                if game is None:
                    continue

                kalshi_close = await queries.get_close_snapshot(game_id, "kalshi")
                dk_close = await queries.get_close_snapshot(game_id, "draftkings")

                if kalshi_close is None and dk_close is None:
                    continue

                dk_open = await queries.get_first_poll_snapshot(game_id, "draftkings")

                ref_snap = kalshi_close or dk_close
                captured_dt = datetime.fromisoformat(ref_snap.captured_at_utc_iso)
                if captured_dt.tzinfo is None:
                    captured_dt = captured_dt.replace(tzinfo=timezone.utc)
                captured_dt = captured_dt.astimezone(_ET)
                ch = captured_dt.hour % 12 or 12
                campm = "AM" if captured_dt.hour < 12 else "PM"
                captured_str = f"{ch}:{captured_dt.strftime('%M')} {campm} {captured_dt.strftime('%Z')}"
                sources = ", ".join(
                    s for s, snap in [("Kalshi", kalshi_close), ("DraftKings", dk_close)]
                    if snap is not None
                )

                embed = discord.Embed(
                    title=f"Closing Lines — {game.away_team} @ {game.home_team}",
                    description=(
                        f"Source: **{sources}** at {captured_str}\n"
                        f"Positive CLV = you got a better price than closing"
                    ),
                    color=0xF1C40F,
                )

                # Always show the closing line numbers
                embed.add_field(
                    name="Close",
                    value=_build_closing_lines_field(kalshi_close, dk_close, dk_open),
                    inline=False,
                )

                # Only post CLV if someone has an active bet on this game.
                bets = await queries.get_open_bets_for_game(game_id)
                if not bets:
                    await queries.mark_game_clv_posted(game_id)
                    continue

                # If anyone has bets, compute CLV and add their lines.
                # Discord caps embeds at 25 fields. We use 1 for "Close", so the
                # first embed holds up to 24 bet fields. Overflow goes into
                # follow-up embeds (25 fields each) to avoid HTTP 400.
                mention_ids: set[str] = set()
                bet_fields: list[tuple[str, str]] = []
                for bet in bets:
                    ref = kalshi_close if (bet.market.lower() == "moneyline" and kalshi_close) else dk_close
                    close_odds = _close_odds_for_bet(bet, game, ref.payload) if ref else None
                    clv: float | None = None
                    close_line_display: str | None = None
                    if close_odds is not None:
                        market = bet.market.lower()
                        is_home = side_is_home(bet.side, game.home_team, game.away_team)
                        close_spread = ref.payload.get("spread") if ref else None
                        close_total = ref.payload.get("total") if ref else None

                        clv = compute_clv(
                            bet.odds, close_odds,
                            market=market,
                            bet_line=bet.line,
                            close_line=close_spread if market == "spread" else close_total if market == "total" else None,
                            is_home=is_home if market == "spread" else None,
                            is_over=(bet.side.lower() == "over") if market == "total" else None,
                        )

                        # Show closing line number for spread/total
                        if market == "spread" and close_spread is not None and is_home is not None:
                            close_for_bettor = close_spread if is_home else -close_spread
                            close_line_display = f"{close_for_bettor:+.1f}"
                        elif market == "total" and close_total is not None:
                            close_line_display = f"{close_total}"

                    await queries.update_bet_clv(bet.bet_id, clv)
                    bet_fields.append((f"<@{bet.discord_user}>", _build_bet_line(bet, close_odds, clv, close_line_display)))
                    mention_ids.add(bet.discord_user)

                # Fill the first embed (1 slot already used by "Close")
                for name, value in bet_fields[:24]:
                    embed.add_field(name=name, value=value, inline=False)

                # Build overflow embeds for any bets beyond the first 24
                overflow_embeds: list[discord.Embed] = []
                overflow = bet_fields[24:]
                while overflow:
                    chunk, overflow = overflow[:25], overflow[25:]
                    ov_embed = discord.Embed(
                        title=f"CLV (continued) — {game.away_team} @ {game.home_team}",
                        color=0xF1C40F,
                    )
                    for name, value in chunk:
                        ov_embed.add_field(name=name, value=value, inline=False)
                    overflow_embeds.append(ov_embed)

                # Mark posted before sending so a crash between these two lines
                # results in a missed post (recoverable) rather than a duplicate
                # ping on the next 5-minute tick (not recoverable without complaints).
                await queries.mark_game_clv_posted(game_id)
                # Ping bettors in message content so Discord actually notifies them
                ping_str = " ".join(f"<@{uid}>" for uid in mention_ids) if mention_ids else None
                await channel.send(content=ping_str, embed=embed)
                for ov_embed in overflow_embeds:
                    await channel.send(embed=ov_embed)
            except Exception:
                log.exception("CLV auto-post failed for game %s", game_id)

    @clv_check.before_loop
    async def before_clv_check(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CLVCog(bot))
