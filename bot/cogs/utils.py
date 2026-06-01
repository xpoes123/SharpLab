"""Pure-math utility commands — no API calls, no DB."""
import discord
from discord import app_commands
from discord.ext import commands

from shared.odds_utils import (
    american_to_decimal,
    american_to_prob,
    decimal_to_american,
    odds_breakdown,
    parse_odds_input,
    prob_to_american,
)
import logging
log = logging.getLogger(__name__)


def _parse_odds(raw: str) -> tuple[str, float, int, float]:
    """Wrapper around shared odds_breakdown — derives decimal/prob from the input's
    native precision (no decimal→american→decimal round-trip artifacts)."""
    b = odds_breakdown(raw)
    return (b["fmt"], b["decimal"], b["american"], b["prob"])


class UtilsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    calc = app_commands.Group(name="calc", description="Betting math utilities (no API calls)")

    # ── /calc convert ────────────────────────────────────────────────────────

    @calc.command(name="convert", description="Convert odds between American, decimal, cents, and implied %")
    @app_commands.describe(odds="Odds to convert: American (-110), decimal (1.91), cents (52), or probability (0.52 / 52%)")
    async def convert(self, interaction: discord.Interaction, odds: str) -> None:
        try:
            fmt, decimal, american, prob = _parse_odds(odds)
        except Exception:
            await interaction.response.send_message(
                f"Couldn't parse `{odds}`. Try `-110`, `+150`, `1.91`, or `0.52`.", ephemeral=True
            )
            return

        sign = "+" if american > 0 else ""
        embed = discord.Embed(title="Odds Converter", color=0x2B2D31)
        embed.add_field(name="Input", value=f"`{odds}` ({fmt})", inline=False)
        embed.add_field(name="American", value=f"`{sign}{american}`", inline=True)
        embed.add_field(name="Decimal", value=f"`{decimal:.4f}`", inline=True)
        embed.add_field(name="Implied %", value=f"`{prob * 100:.2f}%`", inline=True)
        await interaction.response.send_message(embed=embed)

    # ── /calc ev ─────────────────────────────────────────────────────────────

    @calc.command(name="ev", description="Expected value per unit staked")
    @app_commands.describe(
        odds="American odds (e.g. -110 or +150)",
        true_prob="Your estimated win probability (0–1 or 0–100)",
    )
    async def ev(self, interaction: discord.Interaction, odds: int, true_prob: float) -> None:
        # Accept both 0.55 and 55 as 55%
        if true_prob > 1:
            true_prob /= 100

        if not (0 < true_prob < 1):
            await interaction.response.send_message("true_prob must be between 0 and 1 (or 0–100).", ephemeral=True)
            return

        decimal = american_to_decimal(odds)
        ev_per_unit = true_prob * (decimal - 1) - (1 - true_prob)
        implied = american_to_prob(odds)
        edge = true_prob - implied

        sign = "+" if odds > 0 else ""
        color = 0x57F287 if ev_per_unit > 0 else 0xED4245  # green / red
        embed = discord.Embed(title="Expected Value", color=color)
        embed.add_field(name="Odds", value=f"`{sign}{odds}`", inline=True)
        embed.add_field(name="True prob", value=f"`{true_prob * 100:.2f}%`", inline=True)
        embed.add_field(name="Implied prob", value=f"`{implied * 100:.2f}%`", inline=True)
        embed.add_field(name="Edge", value=f"`{edge * 100:+.2f}%`", inline=True)
        embed.add_field(name="EV / unit", value=f"`{ev_per_unit:+.4f}u`", inline=True)
        await interaction.response.send_message(embed=embed)

    # ── /calc kelly ──────────────────────────────────────────────────────────

    @calc.command(name="kelly", description="Kelly criterion stake sizing")
    @app_commands.describe(
        bankroll="Bankroll in units",
        odds="American odds (e.g. -110 or +150)",
        edge="Your edge as a percentage (e.g. 5 for 5%)",
    )
    async def kelly(self, interaction: discord.Interaction, bankroll: float, odds: int, edge: float) -> None:
        if edge <= 0:
            await interaction.response.send_message("Edge must be positive.", ephemeral=True)
            return

        edge_dec = edge / 100
        implied = american_to_prob(odds)
        p = implied + edge_dec          # true win probability
        q = 1 - p
        b = american_to_decimal(odds) - 1  # payout per unit staked

        kelly_fraction = (b * p - q) / b
        full_stake = kelly_fraction * bankroll
        half_stake = full_stake / 2

        if kelly_fraction <= 0:
            await interaction.response.send_message(
                f"Kelly fraction is non-positive ({kelly_fraction:.4f}). No bet recommended.", ephemeral=True
            )
            return

        sign = "+" if odds > 0 else ""
        embed = discord.Embed(title="Kelly Criterion", color=0x5865F2)
        embed.add_field(name="Odds", value=f"`{sign}{odds}`", inline=True)
        embed.add_field(name="Edge", value=f"`{edge:.2f}%`", inline=True)
        embed.add_field(name="True prob", value=f"`{p * 100:.2f}%`", inline=True)
        embed.add_field(name="Kelly %", value=f"`{kelly_fraction * 100:.2f}%`", inline=True)
        embed.add_field(name="Full Kelly", value=f"`{full_stake:.2f}u`", inline=True)
        embed.add_field(name="Half Kelly", value=f"`{half_stake:.2f}u`", inline=True)
        await interaction.response.send_message(embed=embed)

    # ── /calc parlay ─────────────────────────────────────────────────────────

    @calc.command(name="parlay", description="Parlay odds calculator")
    @app_commands.describe(legs="Space-separated American odds (e.g. -110 -110 +150)")
    async def parlay(self, interaction: discord.Interaction, legs: str) -> None:
        try:
            raw_legs = [int(x.lstrip("+")) for x in legs.split()]
        except ValueError:
            await interaction.response.send_message(
                "Couldn't parse legs. Use space-separated American odds, e.g. `-110 -110 +150`.", ephemeral=True
            )
            return

        if len(raw_legs) < 2:
            await interaction.response.send_message("Need at least 2 legs.", ephemeral=True)
            return

        decimal_total = 1.0
        for leg in raw_legs:
            decimal_total *= american_to_decimal(leg)

        parlay_american = decimal_to_american(decimal_total)
        implied_prob = american_to_prob(parlay_american)
        sign = "+" if parlay_american > 0 else ""

        leg_lines = "\n".join(
            f"`{'+' if l > 0 else ''}{l}`  →  `{american_to_decimal(l):.3f}x`"
            for l in raw_legs
        )

        embed = discord.Embed(title=f"{len(raw_legs)}-Leg Parlay", color=0x5865F2)
        embed.add_field(name="Legs", value=leg_lines, inline=False)
        embed.add_field(name="Parlay odds", value=f"`{sign}{parlay_american}`", inline=True)
        embed.add_field(name="Decimal", value=f"`{decimal_total:.4f}x`", inline=True)
        embed.add_field(name="Implied %", value=f"`{implied_prob * 100:.2f}%`", inline=True)
        await interaction.response.send_message(embed=embed)

    # ── /calc vig ────────────────────────────────────────────────────────────

    @calc.command(name="vig", description="Market hold/vig + de-vigged fair odds (2-way or multi-way)")
    @app_commands.describe(
        odds="Space-separated odds for EVERY outcome (e.g. -110 -110, or 3-way -150 +130 +400). "
             "Each may be American, decimal, cents, or %."
    )
    async def vig(self, interaction: discord.Interaction, odds: str) -> None:
        try:
            legs = [odds_breakdown(x) for x in odds.split()]
        except Exception:
            await interaction.response.send_message(
                "Couldn't parse those odds. Space-separate every outcome, e.g. `-110 -110` or `-150 +130 +400`.",
                ephemeral=True,
            )
            return
        if len(legs) < 2:
            await interaction.response.send_message(
                "Give the odds for **all** outcomes of the market (at least 2).", ephemeral=True
            )
            return

        overround = sum(l["prob"] for l in legs)          # total implied prob (>1 means vig)
        hold = (overround - 1) / overround if overround > 0 else 0.0  # book's theoretical hold

        rows = []
        for l in legs:
            fair = l["prob"] / overround                  # de-vigged fair prob
            try:
                fair_am = prob_to_american(fair)
                fair_s = f"{'+' if fair_am > 0 else ''}{fair_am}"
            except ValueError:
                fair_s = "—"
            a = l["american"]
            rows.append(f"`{'+' if a > 0 else ''}{a}`  {l['prob']*100:5.1f}%  →  fair `{fair_s}` ({fair*100:.1f}%)")

        color = 0x57F287 if hold < 0.04 else 0xFAA61A if hold < 0.06 else 0xED4245
        embed = discord.Embed(title=f"{len(legs)}-Way Market — Vig / Hold", color=color)
        embed.add_field(name="Outcomes (odds · implied → fair)", value="\n".join(rows), inline=False)
        embed.add_field(name="Total implied", value=f"`{overround*100:.2f}%`", inline=True)
        embed.add_field(name="Hold / vig", value=f"`{hold*100:.2f}%`", inline=True)
        await interaction.response.send_message(embed=embed)

    # ── /help ──────────────────────────────────────────────────────────────────

    @app_commands.command(name="help", description="List all SharpLab commands")
    async def help(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(title="SharpLab Commands", color=0x5865F2)

        embed.add_field(
            name="Odds & Lines",
            value=(
                "`/odds nba lines [game]` · `/odds mlb lines [game]`\n"
                "`/odds nba best [game]` — best number per market\n"
                "`/odds nba move [game]` — line movement since open\n"
                "`/odds nba scores` — live scores"
            ),
            inline=False,
        )
        embed.add_field(
            name="Bet Tracking",
            value=(
                "`/bet log nba …` · `/bet log mlb …` — log a bet\n"
                "`/bet view` — open + graded bets\n"
                "`/bet record [@user]` — W/L and ROI\n"
                "`/bet clv [@user]` — CLV breakdown\n"
                "`/bet void <bet_id>`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Paper Trading",
            value=(
                "`/paper trade nba …` · `/paper trade mlb …`\n"
                "`/paper portfolio` · `/paper profile`\n"
                "`/paper leaderboard` · `/paper cashout <trade_id>`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Math",
            value=(
                "`/calc ev <odds> <true_prob>` — expected value\n"
                "`/calc kelly <bankroll> <odds> <edge>` — Kelly stake\n"
                "`/calc parlay <legs>` — parlay calculator\n"
                "`/calc convert <odds>` — American ↔ decimal ↔ %"
            ),
            inline=False,
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(UtilsCog(bot))
