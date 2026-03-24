"""Bet tracking commands — /log and /record."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from db import queries
from shared.models import Bet
from shared.odds_utils import american_to_decimal, american_to_prob, parse_odds_input
from .odds import game_autocomplete


# ── Choices ────────────────────────────────────────────────────────────────────

BOOK_CHOICES = [
    app_commands.Choice(name="DraftKings", value="draftkings"),
    app_commands.Choice(name="FanDuel", value="fanduel"),
    app_commands.Choice(name="BetMGM", value="betmgm"),
    app_commands.Choice(name="Caesars", value="caesars"),
    app_commands.Choice(name="Bet365", value="bet365"),
    app_commands.Choice(name="PointsBet", value="pointsbet"),
    app_commands.Choice(name="Kalshi", value="kalshi"),
    app_commands.Choice(name="Polymarket", value="polymarket"),
    app_commands.Choice(name="Other", value="other"),
]

MARKET_CHOICES = [
    app_commands.Choice(name="Spread", value="spread"),
    app_commands.Choice(name="Moneyline", value="moneyline"),
    app_commands.Choice(name="Total", value="total"),
    app_commands.Choice(name="Kalshi", value="kalshi"),
]


# ── Autocomplete ───────────────────────────────────────────────────────────────

async def pick_autocomplete(
    interaction: discord.Interaction, _current: str
) -> list[app_commands.Choice[str]]:
    """Context-aware pick options: team names, spread sides with lines, over/under, yes/no."""
    game_id = getattr(interaction.namespace, "game", None)
    market = getattr(interaction.namespace, "market", None)
    if not game_id or not market:
        return []

    game = await queries.get_game_by_id(game_id)
    if game is None:
        return []

    if market == "moneyline":
        return [
            app_commands.Choice(name=f"{game.away_team} (Away)", value=game.away_team),
            app_commands.Choice(name=f"{game.home_team} (Home)", value=game.home_team),
        ]
    if market == "kalshi":
        return [
            app_commands.Choice(name="Yes", value="yes"),
            app_commands.Choice(name="No", value="no"),
        ]

    # spread or total — pull latest lines from DB (prefer DraftKings)
    snaps = await queries.get_latest_snapshots_for_game(game_id)
    payload: dict = {}
    for snap in snaps:
        if snap.source == "draftkings":
            try:
                payload = json.loads(snap.payload)
                break
            except Exception:
                pass
    if not payload and snaps:
        try:
            payload = json.loads(snaps[0].payload)
        except Exception:
            pass

    if market == "spread":
        spread = payload.get("spread")
        spread_odds = payload.get("spread_odds")
        if spread is not None:
            away_spread = -spread
            home_label = f"{game.home_team} {spread:+.1f}"
            if spread_odds is not None:
                home_label += f" ({spread_odds:+d})"
            away_label = f"{game.away_team} {away_spread:+.1f}"
            return [
                app_commands.Choice(name=home_label, value=f"{game.home_team}:{spread}"),
                app_commands.Choice(name=away_label, value=f"{game.away_team}:{away_spread}"),
            ]
        # No snapshot yet — show team names without lines
        return [
            app_commands.Choice(name=f"{game.home_team} (Home)", value=game.home_team),
            app_commands.Choice(name=f"{game.away_team} (Away)", value=game.away_team),
        ]

    if market == "total":
        total = payload.get("total")
        over_odds = payload.get("total_over_odds")
        under_odds = payload.get("total_under_odds")
        if total is not None:
            over_label = f"Over {total}"
            if over_odds is not None:
                over_label += f" ({over_odds:+d})"
            under_label = f"Under {total}"
            if under_odds is not None:
                under_label += f" ({under_odds:+d})"
            return [
                app_commands.Choice(name=over_label, value=f"over:{total}"),
                app_commands.Choice(name=under_label, value=f"under:{total}"),
            ]
        return [
            app_commands.Choice(name="Over", value="over"),
            app_commands.Choice(name="Under", value="under"),
        ]

    return []


# ── Helpers ────────────────────────────────────────────────────────────────────

def _fmt_american(odds: int) -> str:
    return f"+{odds}" if odds > 0 else str(odds)


def _parse_pick(pick: str) -> tuple[str, float | None]:
    """Parse autocomplete value 'side:line' or bare 'side' into (side, line | None)."""
    if ":" in pick:
        side, _, line_str = pick.partition(":")
        try:
            return side.strip(), float(line_str)
        except ValueError:
            return side.strip(), None
    return pick.strip(), None


def _record_embed(user: discord.Member | discord.User, bets: list[Bet]) -> discord.Embed:
    total = len(bets)
    if total == 0:
        embed = discord.Embed(
            title=f"Record — {user.display_name}",
            description="No bets logged yet.",
            color=0x5865F2,
        )
        return embed

    by_status: dict[str, list[Bet]] = {}
    for bet in bets:
        by_status.setdefault(bet.status, []).append(bet)

    won = by_status.get("won", [])
    lost = by_status.get("lost", [])
    push = by_status.get("push", [])
    open_ = by_status.get("open", [])

    settled = won + lost + push

    # Net units
    net = 0.0
    units_risked = 0.0
    for b in won:
        net += b.units * (american_to_decimal(b.odds) - 1)
        units_risked += b.units
    for b in lost:
        net -= b.units
        units_risked += b.units
    for b in push:
        units_risked += b.units  # push: no profit/loss

    roi = (net / units_risked * 100) if units_risked > 0 else 0.0

    wl = f"{len(won)}-{len(lost)}" + (f"-{len(push)}" if push else "")
    color = 0x57F287 if net > 0 else (0xED4245 if net < 0 else 0x5865F2)

    embed = discord.Embed(
        title=f"Record — {user.display_name}",
        color=color,
    )
    embed.add_field(name="W-L" + ("-P" if push else ""), value=f"`{wl}`", inline=True)
    embed.add_field(name="Net units", value=f"`{net:+.2f}u`", inline=True)
    embed.add_field(name="ROI", value=f"`{roi:+.1f}%`" if settled else "`—`", inline=True)
    embed.add_field(name="Open bets", value=f"`{len(open_)}`", inline=True)
    embed.add_field(name="Total logged", value=f"`{total}`", inline=True)

    # Last 5 bets
    recent = bets[:5]
    recent_lines = []
    for b in recent:
        status_icon = {"won": "✅", "lost": "❌", "push": "➖", "open": "⏳", "void": "🚫"}.get(b.status, "?")
        line_str = f" {b.line:+.1f}" if b.line is not None and b.market in ("spread", "total") else ""
        recent_lines.append(
            f"{status_icon} {b.market}{line_str} {_fmt_american(b.odds)} ({b.units}u) — {b.book}"
        )
    embed.add_field(name="Recent bets", value="\n".join(recent_lines), inline=False)

    return embed


# ── Cog ────────────────────────────────────────────────────────────────────────

class BetsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /log ──────────────────────────────────────────────────────────────────

    @app_commands.command(name="log", description="Log a bet to your record")
    @app_commands.autocomplete(game=game_autocomplete, pick=pick_autocomplete)
    @app_commands.describe(
        game="Select a game",
        book="Sportsbook",
        market="Market type",
        pick="Your pick — autocomplete shows live lines (team, spread side, over/under, yes/no)",
        odds="Odds in any format: American (-110, +150), decimal (1.91), or cents (52)",
        units="Units risked (e.g. 1.0)",
        line="Spread or total number if not auto-filled by autocomplete (e.g. -4.5, 224.5)",
        notes="Optional notes",
    )
    @app_commands.choices(book=BOOK_CHOICES, market=MARKET_CHOICES)
    async def log(
        self,
        interaction: discord.Interaction,
        game: str,
        book: str,
        market: str,
        pick: str,
        odds: str,
        units: float,
        line: float | None = None,
        notes: str | None = None,
    ) -> None:
        await interaction.response.defer()

        try:
            american_odds, odds_fmt = parse_odds_input(odds)
        except Exception:
            await interaction.followup.send(
                f"Couldn't parse odds `{odds}`. Use American (-110), decimal (1.91), or cents (52).",
                ephemeral=True,
            )
            return

        target = await queries.get_game_by_id(game)
        if target is None:
            await interaction.followup.send(
                "Game not found. Select a game from the autocomplete dropdown.",
                ephemeral=True,
            )
            return

        side, pick_line = _parse_pick(pick)
        # Explicit line param overrides autocomplete-encoded line
        final_line = line if line is not None else pick_line

        now_iso = datetime.now(timezone.utc).isoformat()
        bet = Bet(
            game_id=target.game_id,
            placed_at=now_iso,
            discord_user=str(interaction.user.id),
            book=book,
            market=market,
            side=side,
            odds=american_odds,
            units=units,
            line=final_line,
            notes=notes,
        )
        bet_id = await queries.insert_bet(bet)

        sign = "+" if american_odds > 0 else ""
        line_str = f" {final_line:+.1f}" if final_line is not None else ""
        implied = american_to_prob(american_odds)
        if odds_fmt == "american":
            odds_display = f"`{sign}{american_odds}`"
        else:
            odds_display = f"`{odds}` ({odds_fmt}) → `{sign}{american_odds}`"

        embed = discord.Embed(title="Bet logged", color=0x57F287)
        embed.add_field(name="Game", value=f"{target.away_team} @ {target.home_team}", inline=False)
        embed.add_field(name="Book", value=book, inline=True)
        embed.add_field(name="Market", value=f"{market}{line_str}", inline=True)
        embed.add_field(name="Pick", value=side, inline=True)
        embed.add_field(name="Odds", value=odds_display, inline=True)
        embed.add_field(name="Implied %", value=f"`{implied * 100:.1f}%`", inline=True)
        embed.add_field(name="Units", value=f"`{units}u`", inline=True)
        embed.set_footer(text=f"Bet ID: {bet_id}")
        await interaction.followup.send(embed=embed)

    # ── /record ───────────────────────────────────────────────────────────────

    @app_commands.command(name="record", description="View bet record and ROI")
    @app_commands.describe(user="User to look up (defaults to you)")
    async def record(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ) -> None:
        await interaction.response.defer()

        target_user = user or interaction.user
        bets = await queries.get_bets_for_user(str(target_user.id))
        embed = _record_embed(target_user, bets)
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BetsCog(bot))
