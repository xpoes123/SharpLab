"""Bet tracking commands — /log and /record."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from db import queries
from shared.models import Bet
from shared.odds_utils import american_to_decimal, fmt_prob, parse_odds_input
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
                home_label += f" ({fmt_prob(spread_odds)})"
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
                over_label += f" ({fmt_prob(over_odds)})"
            under_label = f"Under {total}"
            if under_odds is not None:
                under_label += f" ({fmt_prob(under_odds)})"
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
    return fmt_prob(odds)


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

        if market in ("spread", "total") and final_line is None:
            await interaction.followup.send(
                "Spread and total bets require a line number. "
                "Either select from autocomplete (if lines are available) or enter it in the `line` field.",
                ephemeral=True,
            )
            return

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

        line_str = f" {final_line:+.1f}" if final_line is not None else ""

        embed = discord.Embed(title="Bet logged", color=0x57F287)
        embed.add_field(name="Game", value=f"{target.away_team} @ {target.home_team}", inline=False)
        embed.add_field(name="Book", value=book, inline=True)
        embed.add_field(name="Market", value=f"{market}{line_str}", inline=True)
        embed.add_field(name="Pick", value=side, inline=True)
        embed.add_field(name="Odds", value=f"`{fmt_prob(american_odds)}`", inline=True)
        embed.add_field(name="Units", value=f"`{units}u`", inline=True)
        embed.set_footer(text=f"Bet ID: {bet_id}")
        await interaction.followup.send(embed=embed)

    # ── /open ─────────────────────────────────────────────────────────────────

    @app_commands.command(name="open", description="View your open and graded bets")
    async def open_bets(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        bets = await queries.get_open_bets_for_user(str(interaction.user.id))

        if not bets:
            await interaction.followup.send("You have no open or graded bets.", ephemeral=True)
            return

        lines = []
        for bet in bets:
            game = await queries.get_game_by_id(bet.game_id)
            if game is not None:
                away_short = game.away_team.split()[-1]
                home_short = game.home_team.split()[-1]
                game_str = f"{away_short} @ {home_short}"
            else:
                game_str = bet.game_id[:8]

            if bet.market == "spread" and bet.line is not None:
                market_str = f"spread {bet.line:+.1f}"
            elif bet.market == "total" and bet.line is not None:
                direction = "O" if bet.side.lower() == "over" else "U"
                market_str = f"total {direction}{bet.line:.1f}"
            else:
                market_str = bet.market

            icon = "📊" if bet.status == "graded" else "⏳"
            clv_str = f"  CLV: {bet.clv:+.1f}pp" if bet.status == "graded" and bet.clv is not None else ""

            lines.append(
                f"{icon}  {game_str:<12}  {market_str:<14}  {bet.side:<12}  {fmt_prob(bet.odds):<6}  {bet.units}u{clv_str}"
            )

        embed = discord.Embed(
            title=f"Open bets — {interaction.user.display_name}",
            description="```\n" + "\n".join(lines) + "\n```",
            color=0x5865F2,
        )
        embed.set_footer(text=f"{len(bets)} bet{'s' if len(bets) != 1 else ''} pending")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /clv-summary ──────────────────────────────────────────────────────────

    @app_commands.command(name="clv-summary", description="CLV breakdown and EV gained from beating the closing line")
    @app_commands.describe(user="User to look up (defaults to you)")
    async def clv_summary(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ) -> None:
        await interaction.response.defer()

        target_user = user or interaction.user
        bets = await queries.get_graded_bets_for_user(str(target_user.id))

        if not bets:
            await interaction.followup.send(
                f"No CLV data yet for **{target_user.display_name}** — bets are graded at tip-off."
            )
            return

        # EV gained = units × (clv_pp / 100)
        def _ev(b: Bet) -> float:
            return b.units * (b.clv / 100)  # type: ignore[operator]

        total_ev = sum(_ev(b) for b in bets)
        avg_clv = sum(b.clv for b in bets) / len(bets)  # type: ignore[arg-type]

        # Breakdown helpers
        def _breakdown(groups: dict[str, list[Bet]]) -> str:
            rows = []
            for key in sorted(groups):
                group = groups[key]
                g_avg = sum(b.clv for b in group) / len(group)  # type: ignore[arg-type]
                g_ev = sum(_ev(b) for b in group)
                rows.append(f"  {key:<14} {len(group):>3}  avg {g_avg:+.1f}pp  EV {g_ev:+.3f}u")
            return "\n".join(rows)

        by_market: dict[str, list[Bet]] = {}
        by_book: dict[str, list[Bet]] = {}
        for b in bets:
            by_market.setdefault(b.market, []).append(b)
            by_book.setdefault(b.book, []).append(b)

        color = 0x57F287 if total_ev > 0 else (0xED4245 if total_ev < 0 else 0x5865F2)
        embed = discord.Embed(
            title=f"CLV Summary — {target_user.display_name}",
            color=color,
        )
        embed.add_field(name="Bets graded", value=f"`{len(bets)}`", inline=True)
        embed.add_field(name="Avg CLV", value=f"`{avg_clv:+.2f}pp`", inline=True)
        embed.add_field(name="Total EV gained", value=f"`{total_ev:+.3f}u`", inline=True)
        embed.add_field(
            name="By market",
            value="```\n" + _breakdown(by_market) + "\n```",
            inline=False,
        )
        embed.add_field(
            name="By book",
            value="```\n" + _breakdown(by_book) + "\n```",
            inline=False,
        )
        embed.set_footer(text="EV gained = Σ (units × CLV / 100) — theoretical edge vs. closing line")
        await interaction.followup.send(embed=embed)

    # ── /void ─────────────────────────────────────────────────────────────────

    @app_commands.command(name="void", description="Void a bet you logged (cancelled game or entry error)")
    @app_commands.describe(bet_id="Bet ID shown in the /log confirmation footer")
    async def void_bet(self, interaction: discord.Interaction, bet_id: int) -> None:
        await interaction.response.defer(ephemeral=True)

        bet = await queries.get_bet_by_id(bet_id)
        if bet is None:
            await interaction.followup.send(f"Bet #{bet_id} not found.", ephemeral=True)
            return

        if bet.discord_user != str(interaction.user.id):
            await interaction.followup.send("That bet doesn't belong to you.", ephemeral=True)
            return

        if bet.status in ("won", "lost", "push", "void"):
            await interaction.followup.send(
                f"Bet #{bet_id} is already **{bet.status}** and can't be voided.",
                ephemeral=True,
            )
            return

        await queries.update_bet_result(bet_id, "void")

        game = await queries.get_game_by_id(bet.game_id)
        game_str = (
            f"{game.away_team} @ {game.home_team}" if game else bet.game_id[:8]
        )
        line_str = f" {bet.line:+.1f}" if bet.line is not None and bet.market in ("spread", "total") else ""

        embed = discord.Embed(title="Bet voided", color=0xED4245)
        embed.add_field(name="Game", value=game_str, inline=False)
        embed.add_field(name="Market", value=f"{bet.market}{line_str}", inline=True)
        embed.add_field(name="Pick", value=bet.side, inline=True)
        embed.add_field(name="Odds", value=f"`{fmt_prob(bet.odds)}`", inline=True)
        embed.add_field(name="Units", value=f"`{bet.units}u`", inline=True)
        embed.set_footer(text=f"Bet ID: {bet_id} — was {bet.status}")
        await interaction.followup.send(embed=embed, ephemeral=True)

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
