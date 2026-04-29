"""
Prefix command API — bot-friendly text interface for programmatic access.

All commands use the ! prefix and return plain-text, machine-parseable responses.
See SPEC.md §6 for the full output format specification.

Response format summary:
  - Type token (GAME / ODDS / BET / CONVERT / EV / KELLY / PARLAY / ERR / NONE)
  - Fields as space-separated key=value pairs
  - Null values represented as the literal string "null"
  - Multi-record responses wrapped in a code block
  - Single-record and error responses sent as plain text
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from discord.ext import commands

from db import queries
from shared.odds_utils import (
    american_to_decimal,
    american_to_prob,
    parse_odds_input,
)
import logging
log = logging.getLogger(__name__)


def _v(val: object) -> str:
    """Format a field value: None → 'null', everything else → str."""
    return "null" if val is None else str(val)


def _game_line(g) -> str:
    return (
        f"GAME game_id={g.game_id} away={g.away_team} home={g.home_team}"
        f" start={g.start_time_utc_iso}"
    )


def _bet_line(b) -> str:
    return (
        f"BET bet_id={b.bet_id} game_id={b.game_id} user={b.discord_user}"
        f" book={b.book} market={b.market} side={b.side}"
        f" line={_v(b.line)} odds={b.odds} units={b.units}"
        f" status={b.status} clv={_v(b.clv)} placed_at={b.placed_at}"
    )


def _code(lines: list[str]) -> str:
    return "```\n" + "\n".join(lines) + "\n```"


class PrefixCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── !prefix-help ──────────────────────────────────────────────────────────

    @commands.command(name="prefix-help", aliases=["phelp"])
    async def prefix_help(self, ctx: commands.Context) -> None:
        """List all prefix commands (bot API)."""
        await ctx.send(
            "**SharpLab Prefix API** — machine-parseable text interface\n"
            "```\n"
            "ID Lookup\n"
            "  !games [today|yesterday|YYYY-MM-DD]   list games with full UUIDs\n"
            "  !game <game_id>                        single game by full or 8-char prefix ID\n"
            "  !find-game <query>                     search games by team name\n"
            "  !bet <bet_id>                          single bet by integer ID\n"
            "  !bets [discord_user_id]                all bets for a user (default: you)\n"
            "\n"
            "Odds\n"
            "  !odds <game_id>                        latest poll snapshot, all sources\n"
            "\n"
            "Math\n"
            "  !convert <odds>                        any format → American/decimal/prob\n"
            "  !ev <odds> <true_prob>                 EV per unit\n"
            "  !kelly <bankroll> <odds> <edge%>       Kelly stake\n"
            "  !parlay <odds1> [odds2 ...]            parlay calculator\n"
            "```\n"
            "Responses start with a type token: GAME / ODDS / BET / CONVERT / EV / KELLY / PARLAY / ERR / NONE\n"
            "Fields are `key=value` space-separated. Null fields appear as `null`.\n"
            "See SPEC.md §6–7 for full format and integration guide."
        )

    # ── !games ────────────────────────────────────────────────────────────────

    @commands.command(name="games")
    async def games(self, ctx: commands.Context, date_str: str = "today", sport: str = "nba") -> None:
        """
        List games for a date with full game IDs.
        Usage: !games [today|yesterday|YYYY-MM-DD] [nba|mlb]
        """
        now_utc = datetime.now(timezone.utc)
        try:
            if date_str.lower() == "today":
                target = now_utc.date()
            elif date_str.lower() == "yesterday":
                target = (now_utc - timedelta(days=1)).date()
            else:
                target = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            await ctx.send(
                f"ERR invalid date '{date_str}'. Use today, yesterday, or YYYY-MM-DD."
            )
            return

        start = datetime(target.year, target.month, target.day, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(target.year, target.month, target.day, 23, 59, 59, tzinfo=timezone.utc)

        game_list = await queries.get_games_in_window(start.isoformat(), end.isoformat(), sport=sport)
        if not game_list:
            await ctx.send(f"NONE no games found for {target}")
            return

        await ctx.send(_code([_game_line(g) for g in game_list]))

    # ── !game ─────────────────────────────────────────────────────────────────

    @commands.command(name="game")
    async def game(self, ctx: commands.Context, game_id: str) -> None:
        """
        Get game details by full UUID or 8-char prefix.
        Usage: !game <game_id>
        """
        g = await queries.get_game_by_id(game_id)
        if g is None and len(game_id) >= 4:
            results = await queries.get_games_by_id_prefix(game_id, limit=1)
            g = results[0] if results else None

        if g is None:
            await ctx.send(f"ERR game not found: {game_id}")
            return

        await ctx.send(_game_line(g))

    # ── !find-game ────────────────────────────────────────────────────────────

    @commands.command(name="find-game")
    async def find_game(self, ctx: commands.Context, *, query: str) -> None:
        """
        Search games by partial team name (newest first, max 10).
        Usage: !find-game <team_name>
        """
        game_list = await queries.get_all_games_filtered(query, limit=10, sport="nba")
        if not game_list:
            await ctx.send(f"NONE no games matching '{query}'")
            return

        await ctx.send(_code([_game_line(g) for g in game_list]))

    # ── !odds ─────────────────────────────────────────────────────────────────

    @commands.command(name="odds")
    async def odds(self, ctx: commands.Context, game_id: str) -> None:
        """
        Latest poll odds for a game across all tracked sources.
        Usage: !odds <game_id>
        """
        # Resolve prefix to full ID
        if len(game_id) < 36:
            results = await queries.get_games_by_id_prefix(game_id, limit=1)
            if not results:
                await ctx.send(f"ERR game not found: {game_id}")
                return
            game_id = results[0].game_id

        snaps = await queries.get_latest_snapshots_for_game(game_id)
        if not snaps:
            await ctx.send(f"NONE no odds snapshots found for game {game_id[:8]}")
            return

        lines = []
        for snap in snaps:
            p = snap.payload
            lines.append(
                f"ODDS game_id={game_id} source={snap.source} kind={snap.kind}"
                f" captured={snap.captured_at_utc_iso}"
                f" spread={_v(p.get('spread'))}"
                f" spread_odds={_v(p.get('spread_odds'))}"
                f" ml_home={_v(p.get('ml_home'))}"
                f" ml_away={_v(p.get('ml_away'))}"
                f" total={_v(p.get('total'))}"
                f" total_over_odds={_v(p.get('total_over_odds'))}"
                f" total_under_odds={_v(p.get('total_under_odds'))}"
            )
        await ctx.send(_code(lines))

    # ── !bet ──────────────────────────────────────────────────────────────────

    @commands.command(name="bet")
    async def bet(self, ctx: commands.Context, bet_id: str) -> None:
        """
        Look up a single bet by integer ID.
        Usage: !bet <bet_id>
        """
        try:
            bid = int(bet_id)
        except ValueError:
            await ctx.send(f"ERR invalid bet_id '{bet_id}' — must be an integer")
            return

        b = await queries.get_bet_by_id(bid)
        if b is None:
            await ctx.send(f"ERR bet #{bid} not found")
            return

        await ctx.send(_bet_line(b))

    # ── !bets ─────────────────────────────────────────────────────────────────

    @commands.command(name="bets")
    async def bets(self, ctx: commands.Context, user_id: str | None = None) -> None:
        """
        List all bets for a user (newest first, max 20).
        Usage: !bets [discord_user_id]   (defaults to the calling user)
        """
        target_id = user_id or str(ctx.author.id)
        bet_list = await queries.get_bets_for_user(target_id)
        if not bet_list:
            await ctx.send(f"NONE no bets found for user {target_id}")
            return

        await ctx.send(_code([_bet_line(b) for b in bet_list[:20]]))

    # ── !convert ──────────────────────────────────────────────────────────────

    @commands.command(name="convert")
    async def convert(self, ctx: commands.Context, odds_str: str) -> None:
        """
        Convert odds between American, decimal, cents, and implied probability.
        Usage: !convert <odds>  (e.g. -110  1.91  52  52%  0.52)
        """
        try:
            american, fmt = parse_odds_input(odds_str)
        except Exception:
            await ctx.send(
                f"ERR couldn't parse odds '{odds_str}'. Use -110, 1.91, 52, 52%, or 0.52"
            )
            return

        prob = american_to_prob(american)
        decimal = american_to_decimal(american)
        await ctx.send(
            f"CONVERT input={odds_str} format={fmt}"
            f" american={american:+d} decimal={decimal:.4f}"
            f" implied_prob={prob:.4f} implied_pct={prob * 100:.2f}%"
        )

    # ── !ev ───────────────────────────────────────────────────────────────────

    @commands.command(name="ev")
    async def ev(self, ctx: commands.Context, odds_str: str, true_prob_str: str) -> None:
        """
        Expected value per unit.
        Usage: !ev <odds> <true_prob>   (e.g. !ev -110 0.55  or  !ev -110 55)
        """
        try:
            american, _ = parse_odds_input(odds_str)
        except Exception:
            await ctx.send(f"ERR couldn't parse odds '{odds_str}'")
            return

        try:
            p = float(true_prob_str)
            if p > 1:
                p /= 100
            if not (0 < p < 1):
                raise ValueError
        except (ValueError, ZeroDivisionError):
            await ctx.send(
                f"ERR invalid true_prob '{true_prob_str}' — use decimal (0.55) or percent (55)"
            )
            return

        implied = american_to_prob(american)
        decimal = american_to_decimal(american)
        edge = p - implied
        ev = p * (decimal - 1) - (1 - p)

        await ctx.send(
            f"EV odds={american:+d} true_prob={p:.4f}"
            f" implied_prob={implied:.4f} edge={edge:+.4f} ev_per_unit={ev:+.4f}"
        )

    # ── !kelly ────────────────────────────────────────────────────────────────

    @commands.command(name="kelly")
    async def kelly(
        self,
        ctx: commands.Context,
        bankroll_str: str,
        odds_str: str,
        edge_str: str,
    ) -> None:
        """
        Kelly criterion stake.
        Usage: !kelly <bankroll> <odds> <edge%>   (e.g. !kelly 100 -110 5)
        """
        try:
            bankroll = float(bankroll_str)
            american, _ = parse_odds_input(odds_str)
            edge_pct = float(edge_str)
        except Exception:
            await ctx.send("ERR usage: !kelly <bankroll> <odds> <edge%>")
            return

        decimal = american_to_decimal(american)
        b = decimal - 1
        implied = american_to_prob(american)
        true_prob = implied + (edge_pct / 100)

        if not (0 < true_prob < 1):
            await ctx.send("ERR edge too large — true probability out of range")
            return

        q = 1 - true_prob
        kelly_frac = (b * true_prob - q) / b
        full = bankroll * kelly_frac
        half = full / 2

        await ctx.send(
            f"KELLY bankroll={bankroll} odds={american:+d} edge={edge_pct}%"
            f" true_prob={true_prob:.4f} kelly_fraction={kelly_frac:.4f}"
            f" full_kelly={full:.2f}u half_kelly={half:.2f}u"
        )

    # ── !parlay ───────────────────────────────────────────────────────────────

    @commands.command(name="parlay")
    async def parlay(self, ctx: commands.Context, *legs: str) -> None:
        """
        Parlay odds calculator.
        Usage: !parlay <odds1> [odds2 ...]   (e.g. !parlay -110 -110 +150)
        """
        if not legs:
            await ctx.send("ERR usage: !parlay <odds1> [odds2 ...]")
            return

        parsed: list[int] = []
        for leg in legs:
            try:
                american, _ = parse_odds_input(leg)
                parsed.append(american)
            except Exception:
                await ctx.send(f"ERR couldn't parse leg '{leg}'")
                return

        combined_dec = 1.0
        for american in parsed:
            combined_dec *= american_to_decimal(american)

        if combined_dec >= 2:
            parlay_american = round((combined_dec - 1) * 100)
        else:
            parlay_american = round(-100 / (combined_dec - 1))

        combined_prob = american_to_prob(parlay_american)
        legs_str = " ".join(f"{x:+d}" for x in parsed)

        await ctx.send(
            f"PARLAY legs=[{legs_str}] decimal={combined_dec:.4f}"
            f" american={parlay_american:+d} implied_prob={combined_prob:.4f}"
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PrefixCog(bot))
