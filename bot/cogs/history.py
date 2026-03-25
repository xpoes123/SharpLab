"""Historical game browser — /db command with paginated embed."""
from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from db import queries
from shared.models import TEAM_ABBR
from shared.odds_utils import fmt_prob

PAGE_SIZE = 5


# ── Formatting helpers ────────────────────────────────────────────────────────

def _abbr(team: str) -> str:
    return TEAM_ABBR.get(team, team[:3].upper())


def _fmt_spread(home: str, away: str, spread: float | None, spread_odds: int | None) -> str:
    if spread is None:
        return "—"
    if spread == 0:
        return "PK"
    if spread < 0:
        side, val = _abbr(home), spread
    else:
        side, val = _abbr(away), -spread
    val_str = f"{val:+g}"
    odds_str = f" ({fmt_prob(spread_odds)})" if spread_odds is not None else ""
    return f"{side} {val_str}{odds_str}"


def _fmt_date(iso: str) -> str:
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%b %#d")


def _fmt_status(status: str | None) -> str:
    if not status:
        return ""
    return {"final": "Final", "live": "Live", "scheduled": "Scheduled"}.get(
        status.lower(), status.title()
    )


def _build_page_embed(rows: list[dict], page: int, total_pages: int) -> discord.Embed:
    embed = discord.Embed(
        title="Game History",
        color=discord.Color.blurple(),
    )
    lines = []
    for row in rows:
        away = row["away_team"]
        home = row["home_team"]
        date_str = _fmt_date(row["start_time"])
        status_str = _fmt_status(row["status"])
        spread_str = _fmt_spread(home, away, row["spread"], row["spread_odds"])
        short_id = row["game_id"][:8]

        header = f"**{away} @ {home}**"
        meta = f"{date_str}"
        if status_str:
            meta += f" · {status_str}"
        meta += f" · Spread: {spread_str}"
        meta += f"\nID: `{short_id}`"

        lines.append(f"{header} — {meta}")

    embed.description = "\n\n".join(lines) if lines else "No games found."
    embed.set_footer(text=f"Page {page}/{total_pages} · Use ID with /line-move")
    return embed


# ── View ──────────────────────────────────────────────────────────────────────

class HistoryView(discord.ui.View):
    def __init__(self, page: int, total_pages: int) -> None:
        super().__init__(timeout=120)
        self.page = page
        self.total_pages = total_pages
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.prev_btn.disabled = self.page <= 1
        self.next_btn.disabled = self.page >= self.total_pages

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _paginate(interaction, self.page - 1)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _paginate(interaction, self.page + 1)


async def _paginate(interaction: discord.Interaction, page: int) -> None:
    await interaction.response.defer()
    total = await queries.get_past_game_count()
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * PAGE_SIZE
    rows = await queries.get_history_page(offset, PAGE_SIZE)
    embed = _build_page_embed(rows, page, total_pages)
    view = HistoryView(page, total_pages)
    await interaction.edit_original_response(embed=embed, view=view)


# ── Cog ──────────────────────────────────────────────────────────────────────

class HistoryCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="db", description="Browse historical game data — spreads, outcomes, IDs for /line-move")
    async def db(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        total = await queries.get_past_game_count()
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        rows = await queries.get_history_page(0, PAGE_SIZE)
        embed = _build_page_embed(rows, 1, total_pages)
        view = HistoryView(1, total_pages)
        await interaction.followup.send(embed=embed, view=view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HistoryCog(bot))
