"""Cog for ELO ratings and F1-style championship standings."""

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries
from shared.elo import championship_points
from bot.cogs._elo_helpers import (
    ELO_GAME_LABELS,
    MIN_GAMES_FOR_LEADERBOARD,
    fmt_elo_change,
)
import logging
log = logging.getLogger(__name__)

COLOUR_GOLD = 0xF1C40F
COLOUR_DARK = 0x2B2D31

_MEDALS = {1: "\U0001f947", 2: "\U0001f948", 3: "\U0001f949"}


# ── Paginated all-games leaderboard ───────────────────────────────────────────


async def _build_leaderboard_embed(
    bot: commands.Bot, game: dict, idx: int, total: int,
) -> discord.Embed:
    """One page: a single game's ELO leaderboard (top 10, provisional marked)."""
    game_key = game["game"]
    label = ELO_GAME_LABELS.get(game_key, game_key)
    lb = await queries.get_elo_leaderboard(game_key, min_games=1, limit=10)

    embed = discord.Embed(title=f"\U0001f3c6 {label} — ELO Leaderboard", colour=COLOUR_GOLD)
    lines: list[str] = []
    for rank, entry in enumerate(lb, 1):
        try:
            user = await bot.fetch_user(int(entry["discord_user"]))
            name = user.display_name
        except Exception:
            name = f"Player {entry['discord_user'][:8]}"
        prov = "?" if entry["games_played"] < MIN_GAMES_FOR_LEADERBOARD else ""
        record = f"{entry['wins']}W {entry['losses']}L"
        if entry["draws"]:
            record += f" {entry['draws']}D"
        prefix = _MEDALS.get(rank, f"`#{rank}`")
        lines.append(f"{prefix} **{name}** — {entry['rating']:.0f}{prov} ({record})")
    embed.description = "\n".join(lines) or "*No rated players yet.*"
    embed.set_footer(
        text=(
            f"Game {idx + 1}/{total} • {game['total_plays']} plays • "
            f"{game['players']} players • ? = provisional (<{MIN_GAMES_FOR_LEADERBOARD} games)"
        ),
    )
    return embed


class LeaderboardView(ui.View):
    """Flip through every game's ELO leaderboard, ordered by popularity."""

    def __init__(self, bot: commands.Bot, games: list[dict]) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.games = games
        self.index = 0
        self.message: discord.Message | None = None
        self._cache: dict[int, discord.Embed] = {}
        self._sync()

    def _sync(self) -> None:
        self.prev_btn.disabled = self.index == 0
        self.next_btn.disabled = self.index >= len(self.games) - 1
        self.page_btn.label = f"{self.index + 1}/{len(self.games)}"

    async def embed(self) -> discord.Embed:
        if self.index not in self._cache:
            self._cache[self.index] = await _build_leaderboard_embed(
                self.bot, self.games[self.index], self.index, len(self.games),
            )
        return self._cache[self.index]

    async def _show(self, interaction: discord.Interaction) -> None:
        self._sync()
        await interaction.response.edit_message(embed=await self.embed(), view=self)

    @ui.button(emoji="◀️", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        self.index = max(0, self.index - 1)
        await self._show(interaction)

    @ui.button(label="1/1", style=discord.ButtonStyle.primary, disabled=True)
    async def page_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        pass  # indicator only

    @ui.button(emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        self.index = min(len(self.games) - 1, self.index + 1)
        await self._show(interaction)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


# ── Autocomplete ─────────────────────────────────────────────────────────────


async def _game_autocomplete(
    interaction: discord.Interaction, current: str,
) -> list[app_commands.Choice[str]]:
    matches = [
        app_commands.Choice(name=label, value=key)
        for key, label in ELO_GAME_LABELS.items()
        if current.lower() in label.lower() or current.lower() in key
    ]
    return matches[:25]


# ── Cog ──────────────────────────────────────────────────────────────────────


class RatingsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /ratings ──────────────────────────────────────────────────────

    @app_commands.command(name="ratings", description="View ELO ratings")
    @app_commands.describe(
        game="Which game (leave blank for all)",
        user="Which user (leave blank for yourself)",
    )
    @app_commands.autocomplete(game=_game_autocomplete)
    async def ratings(
        self,
        interaction: discord.Interaction,
        game: str | None = None,
        user: discord.Member | None = None,
    ) -> None:
        await interaction.response.defer()
        target = user or interaction.user
        target_id = str(target.id)

        if game:
            # Single-game view
            label = ELO_GAME_LABELS.get(game, game)
            data = await queries.get_elo_rating(target_id, game)

            prov = "?" if data["games_played"] < MIN_GAMES_FOR_LEADERBOARD else ""
            embed = discord.Embed(
                title=f"{label} — {target.display_name}",
                colour=COLOUR_GOLD,
            )
            embed.add_field(
                name="Rating",
                value=f"**{data['rating']:.0f}**{prov}",
                inline=True,
            )
            embed.add_field(
                name="Record",
                value=f"{data['wins']}W {data['losses']}L {data['draws']}D",
                inline=True,
            )
            embed.add_field(
                name="Games",
                value=str(data["games_played"]),
                inline=True,
            )
            embed.add_field(
                name="Peak",
                value=f"{data['peak_rating']:.0f}",
                inline=True,
            )

            # Recent history
            history = await queries.get_elo_history(target_id, game, limit=5)
            if history:
                lines = []
                for h in history:
                    result_emoji = {1.0: "W", 0.0: "L"}.get(h["result"], "D")
                    lines.append(
                        f"`{result_emoji}` {fmt_elo_change(h['rating_before'], h['rating_after'])} "
                        f"({h['context']})"
                    )
                embed.add_field(
                    name="Recent", value="\n".join(lines), inline=False,
                )
        else:
            # All-games view
            all_ratings = await queries.get_elo_ratings_for_user(target_id)

            embed = discord.Embed(
                title=f"ELO Ratings — {target.display_name}",
                colour=COLOUR_GOLD,
            )

            if not all_ratings:
                embed.description = "No rated games yet."
            else:
                lines = []
                for r in all_ratings:
                    label = ELO_GAME_LABELS.get(r["game"], r["game"])
                    prov = "?" if r["games_played"] < MIN_GAMES_FOR_LEADERBOARD else ""
                    record = f"{r['wins']}W {r['losses']}L"
                    if r["draws"]:
                        record += f" {r['draws']}D"
                    lines.append(
                        f"**{label}**: {r['rating']:.0f}{prov} ({record})"
                    )
                embed.description = "\n".join(lines)

                # Championship points
                all_leaderboards = await queries.get_all_elo_leaderboards(
                    min_games=MIN_GAMES_FOR_LEADERBOARD,
                )
                total_pts = 0
                pt_breakdown = []
                for g, lb in all_leaderboards.items():
                    for pos, entry in enumerate(lb, 1):
                        if entry["discord_user"] == target_id:
                            pts = championship_points(pos)
                            if pts > 0:
                                total_pts += pts
                                glabel = ELO_GAME_LABELS.get(g, g)
                                pt_breakdown.append(f"{glabel}: {pts}")
                            break

                if total_pts > 0:
                    embed.add_field(
                        name=f"Championship Points: {total_pts}",
                        value="\n".join(pt_breakdown) if pt_breakdown else "—",
                        inline=False,
                    )

        await interaction.followup.send(embed=embed)

    # ── /leaderboards ─────────────────────────────────────────────────

    @app_commands.command(
        name="leaderboards",
        description="Browse every game's ELO leaderboard, most-played first",
    )
    async def leaderboards(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        games = await queries.get_elo_game_popularity()
        if not games:
            await interaction.followup.send("No games have been played yet!")
            return
        view = LeaderboardView(self.bot, games)
        await interaction.followup.send(embed=await view.embed(), view=view)
        view.message = await interaction.original_response()

    # ── /standings ────────────────────────────────────────────────────

    @app_commands.command(
        name="standings",
        description="F1-style championship standings across all games",
    )
    async def standings(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        all_leaderboards = await queries.get_all_elo_leaderboards(
            min_games=MIN_GAMES_FOR_LEADERBOARD,
        )

        if not all_leaderboards:
            await interaction.followup.send(
                "No qualified players yet. Play at least "
                f"{MIN_GAMES_FOR_LEADERBOARD} games in any mini-game to appear."
            )
            return

        # Calculate F1 points per player
        player_points: dict[str, int] = {}
        player_games: dict[str, list[str]] = {}

        for game_key, lb in all_leaderboards.items():
            label = ELO_GAME_LABELS.get(game_key, game_key)
            for pos, entry in enumerate(lb, 1):
                pts = championship_points(pos)
                if pts == 0:
                    break
                uid = entry["discord_user"]
                player_points[uid] = player_points.get(uid, 0) + pts
                player_games.setdefault(uid, []).append(f"{label}: {pts}")

        if not player_points:
            await interaction.followup.send("No players in the top 10 of any game yet.")
            return

        # Sort by total points
        sorted_players = sorted(
            player_points.items(), key=lambda x: x[1], reverse=True,
        )

        embed = discord.Embed(
            title="Championship Standings",
            colour=COLOUR_GOLD,
        )

        lines = []
        medals = {1: "\U0001f947", 2: "\U0001f948", 3: "\U0001f949"}
        for rank, (uid, total_pts) in enumerate(sorted_players[:15], 1):
            try:
                user = await self.bot.fetch_user(int(uid))
                name = user.display_name
            except Exception:
                name = f"Player {uid[:8]}"

            prefix = medals.get(rank, f"**{rank}.**")
            breakdown = " | ".join(player_games.get(uid, []))
            lines.append(f"{prefix} **{name}** — {total_pts} pts\n> {breakdown}")

        embed.description = "\n".join(lines)
        embed.set_footer(
            text=f"Min {MIN_GAMES_FOR_LEADERBOARD} games per activity to qualify • "
            "Points: 25/18/15/12/10/8/6/4/2/1",
        )

        await interaction.followup.send(embed=embed)

    # ── /game-ratings ─────────────────────────────────────────────────

    @app_commands.command(
        name="game-ratings",
        description="ELO leaderboard for a specific game",
    )
    @app_commands.describe(game="Which game")
    @app_commands.autocomplete(game=_game_autocomplete)
    async def game_ratings(
        self,
        interaction: discord.Interaction,
        game: str,
    ) -> None:
        await interaction.response.defer()

        label = ELO_GAME_LABELS.get(game, game)
        lb = await queries.get_elo_leaderboard(
            game, min_games=MIN_GAMES_FOR_LEADERBOARD, limit=15,
        )

        if not lb:
            await interaction.followup.send(
                f"No qualified players for **{label}** yet. "
                f"Need at least {MIN_GAMES_FOR_LEADERBOARD} games."
            )
            return

        embed = discord.Embed(
            title=f"{label} — ELO Leaderboard",
            colour=COLOUR_GOLD,
        )

        lines = []
        medals = {1: "\U0001f947", 2: "\U0001f948", 3: "\U0001f949"}
        for rank, entry in enumerate(lb, 1):
            try:
                user = await self.bot.fetch_user(int(entry["discord_user"]))
                name = user.display_name
            except Exception:
                name = f"Player {entry['discord_user'][:8]}"

            prefix = medals.get(rank, f"**{rank}.**")
            pts = championship_points(rank)
            pts_label = f" ({pts} pts)" if pts > 0 else ""
            record = f"{entry['wins']}W {entry['losses']}L"
            if entry["draws"]:
                record += f" {entry['draws']}D"

            lines.append(
                f"{prefix} **{name}** — {entry['rating']:.0f}{pts_label} ({record})"
            )

        embed.description = "\n".join(lines)
        embed.set_footer(
            text=f"Min {MIN_GAMES_FOR_LEADERBOARD} games to qualify",
        )

        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RatingsCog(bot))
