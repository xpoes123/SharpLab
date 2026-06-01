"""/props — NBA player props for a game, read from the polled player_props table."""
from __future__ import annotations

import logging
from collections import Counter, defaultdict

import discord
from discord import app_commands
from discord.ext import commands

from db import queries
from .odds import game_autocomplete

log = logging.getLogger(__name__)

_LABELS = {
    "player_points": "🏀 Points",
    "player_rebounds": "🪜 Rebounds",
    "player_assists": "🎯 Assists",
    "player_threes": "🎯 Threes",
    "player_points_rebounds_assists": "📊 PRA",
}
_ORDER = list(_LABELS)


def _am(v) -> str:
    return "—" if v is None else (f"+{v}" if v > 0 else str(v))


def _aggregate(rows: list[dict]) -> dict[str, list[dict]]:
    """(player, market) → consensus line + best over/under across books, grouped by market."""
    by_pm: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        by_pm[(r["player"], r["market"])].append(r)
    out: dict[str, list[dict]] = defaultdict(list)
    for (player, market), rs in by_pm.items():
        lines = Counter(r["line"] for r in rs if r["line"] is not None)
        if not lines:
            continue
        line = lines.most_common(1)[0][0]
        at = [r for r in rs if r["line"] == line]
        over = max((r["over_odds"] for r in at if r["over_odds"] is not None), default=None)
        under = max((r["under_odds"] for r in at if r["under_odds"] is not None), default=None)
        out[market].append({"player": player, "line": line, "over": over, "under": under, "books": len(rs)})
    for market in out:
        out[market].sort(key=lambda x: -x["line"])
    return out


class PropsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="props", description="NBA player props for a game (best line across books)")
    @app_commands.describe(game="Select an NBA game", player="Filter to one player (optional)")
    @app_commands.autocomplete(game=game_autocomplete)
    async def props(self, interaction: discord.Interaction, game: str, player: str | None = None) -> None:
        await interaction.response.defer()
        g = await queries.get_game_by_id(game)
        rows = await queries.get_player_props_for_game(game)
        if player:
            rows = [r for r in rows if player.lower() in r["player"].lower()]
        if not rows:
            await interaction.followup.send(
                "No props yet for that game." + (" Try a different name." if player else
                " The pipeline polls NBA props each cycle — check back shortly."))
            return

        grouped = _aggregate(rows)
        title = f"Player Props — {g.away_team} @ {g.home_team}" if g else "Player Props"
        if player:
            title += f" · {player}"
        embed = discord.Embed(title=f"🏀 {title}", color=0x7aa2f7)
        for market in _ORDER:
            entries = grouped.get(market)
            if not entries:
                continue
            lines = [f"**{e['player']}** {e['line']:g} · O {_am(e['over'])} / U {_am(e['under'])}"
                     for e in entries[:12]]
            val = "\n".join(lines)
            if len(entries) > 12:
                val += f"\n*+{len(entries) - 12} more*"
            embed.add_field(name=_LABELS[market], value=val[:1024], inline=False)
        nbooks = len({r["source"] for r in rows})
        embed.set_footer(text=f"Best line across {nbooks} book(s) · O=Over U=Under")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PropsCog(bot))
