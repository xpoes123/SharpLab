"""Pure-math utility commands — no API calls, no DB."""
import discord
from discord import app_commands
from discord.ext import commands
from math import erf, sqrt

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


def _norm_cdf(x: float) -> float:
    return (1.0 + erf(x / sqrt(2))) / 2


_SPORT_SIGMA = {"nfl": 13.5, "nba": 11.5, "mlb": 1.5}


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

    # ── /calc arb ────────────────────────────────────────────────────────────

    @calc.command(name="arb", description="Arbitrage calculator — checks if a cross-book arb exists and sizes stakes")
    @app_commands.describe(
        odds="Space-separated odds for each side from different books (e.g. -110 +115). "
             "Any format: American, decimal, cents, or %.",
        total="Total amount to stake across all sides (default: 100)",
    )
    async def arb(self, interaction: discord.Interaction, odds: str, total: float = 100.0) -> None:
        if total <= 0:
            await interaction.response.send_message("Total stake must be positive.", ephemeral=True)
            return

        try:
            legs = [odds_breakdown(x) for x in odds.split()]
        except Exception:
            await interaction.response.send_message(
                "Couldn't parse those odds. Space-separate each side, e.g. `-110 +115` or `1.91 2.15`.",
                ephemeral=True,
            )
            return

        if len(legs) < 2:
            await interaction.response.send_message(
                "Give odds for at least 2 sides (one per book).", ephemeral=True
            )
            return

        # sum of implied probs; < 1.0 means an arb exists
        implied_total = sum(l["prob"] for l in legs)
        is_arb = implied_total < 1.0
        margin = 1.0 - implied_total  # positive = arb gap

        # guaranteed return when arb exists: R = total / sum(1/d_i) = total / implied_total
        guaranteed_return = total / implied_total
        guaranteed_profit = guaranteed_return - total
        roi_pct = (guaranteed_return / total - 1) * 100

        # optimal stake per side: s_i = total * (1/d_i) / sum(1/d_j)
        rows = []
        for i, l in enumerate(legs, 1):
            stake = total * l["prob"] / implied_total
            payout = stake * l["decimal"]
            a = l["american"]
            sign = "+" if a > 0 else ""
            rows.append(
                f"Side {i}: `{sign}{a}` → stake `{stake:.2f}` → return `{payout:.2f}`"
            )

        if is_arb:
            color = 0x57F287  # green
            title = "Arbitrage Detected"
            verdict = f"Guaranteed profit: `{guaranteed_profit:.2f}` on `{total:.2f}` staked"
        else:
            color = 0xED4245  # red
            title = "No Arbitrage"
            deficit = implied_total - 1.0
            verdict = f"Book margin: `{deficit * 100:.2f}%` against you — odds don't cover all outcomes"

        embed = discord.Embed(title=title, color=color)
        embed.add_field(name="Stakes (optimal split)", value="\n".join(rows), inline=False)
        embed.add_field(name="Total implied", value=f"`{implied_total * 100:.2f}%`", inline=True)
        embed.add_field(name="Arb margin", value=f"`{margin * 100:+.2f}%`", inline=True)
        if is_arb:
            embed.add_field(name="Guaranteed ROI", value=f"`{roi_pct:+.2f}%`", inline=True)
        embed.add_field(name="Verdict", value=verdict, inline=False)
        await interaction.response.send_message(embed=embed)

    # ── /calc hedge ──────────────────────────────────────────────────────────

    @calc.command(name="hedge", description="Hedge calculator — lock in profit or cut variance on an open bet")
    @app_commands.describe(
        stake="Your original stake amount",
        original_odds="Odds at which you originally bet (any format)",
        hedge_odds="Current odds on the OTHER side to hedge against (any format)",
    )
    async def hedge(self, interaction: discord.Interaction, stake: float, original_odds: str, hedge_odds: str) -> None:
        if stake <= 0:
            await interaction.response.send_message("Stake must be positive.", ephemeral=True)
            return

        try:
            orig = odds_breakdown(original_odds)
            hedg = odds_breakdown(hedge_odds)
        except Exception:
            await interaction.response.send_message(
                "Couldn't parse those odds. Try `-110`, `+150`, `1.91`, or `0.52`.", ephemeral=True
            )
            return

        d1 = orig["decimal"]
        d2 = hedg["decimal"]

        # Full lock: stake * d1 / d2 on the other side equalises both outcomes
        hedge_stake = stake * d1 / d2
        total_at_risk = stake + hedge_stake
        guaranteed_return = stake * d1          # both sides pay out stake * d1
        guaranteed_profit = guaranteed_return - total_at_risk
        guaranteed_roi = guaranteed_profit / total_at_risk * 100

        a1 = orig["american"]
        a2 = hedg["american"]
        s1 = "+" if a1 > 0 else ""
        s2 = "+" if a2 > 0 else ""

        profitable = guaranteed_profit > 0
        color = 0x57F287 if profitable else 0xFAA61A

        embed = discord.Embed(title="Hedge Calculator", color=color)
        embed.add_field(name="Original bet", value=f"`{stake:.2f}` at `{s1}{a1}`", inline=True)
        embed.add_field(name="No-hedge win", value=f"`+{stake * (d1 - 1):.2f}`", inline=True)
        embed.add_field(name="No-hedge loss", value=f"`-{stake:.2f}`", inline=True)
        embed.add_field(
            name="Full lock hedge",
            value=f"Stake `{hedge_stake:.2f}` at `{s2}{a2}` (other side)",
            inline=False,
        )
        embed.add_field(name="Total at risk", value=f"`{total_at_risk:.2f}`", inline=True)
        embed.add_field(name="Guaranteed profit", value=f"`{guaranteed_profit:+.2f}`", inline=True)
        embed.add_field(name="Guaranteed ROI", value=f"`{guaranteed_roi:+.2f}%`", inline=True)
        if not profitable:
            embed.add_field(
                name="Note",
                value="Full lock guarantees a loss — line has moved against you. "
                      "Hedge only to reduce variance, not to lock profit.",
                inline=False,
            )
        await interaction.response.send_message(embed=embed)

    # ── /calc middle ──────────────────────────────────────────────────────────

    @calc.command(name="middle", description="Middle calculator — probability and EV of a two-position middle")
    @app_commands.describe(
        gap="Size of the middle window in points (e.g. 1 for +3 vs -2, 3 for +6 vs -3)",
        odds1="Odds on side 1 (any format)",
        odds2="Odds on side 2 (any format)",
        total="Total to stake across both sides (default: 100, split evenly)",
        sport="Sport for margin distribution: nfl (default), nba, or mlb",
    )
    async def middle(
        self,
        interaction: discord.Interaction,
        gap: float,
        odds1: str,
        odds2: str,
        total: float = 100.0,
        sport: str = "nfl",
    ) -> None:
        if gap <= 0:
            await interaction.response.send_message("Gap must be positive.", ephemeral=True)
            return
        if total <= 0:
            await interaction.response.send_message("Total stake must be positive.", ephemeral=True)
            return

        sport_key = sport.lower().strip()
        if sport_key not in _SPORT_SIGMA:
            await interaction.response.send_message(
                f"Unknown sport `{sport}`. Use `nfl`, `nba`, or `mlb`.", ephemeral=True
            )
            return

        try:
            leg1 = odds_breakdown(odds1)
            leg2 = odds_breakdown(odds2)
        except Exception:
            await interaction.response.send_message("Couldn't parse those odds.", ephemeral=True)
            return

        sigma = _SPORT_SIGMA[sport_key]
        # P(result lands inside gap) using normal approximation centred on midpoint
        p_middle = 2 * _norm_cdf(gap / (2 * sigma)) - 1

        stake_each = total / 2
        d1, d2 = leg1["decimal"], leg2["decimal"]

        both_win = stake_each * (d1 - 1) + stake_each * (d2 - 1)   # middle fires
        s1_only  = stake_each * (d1 - 1) - stake_each               # side 1 wins, side 2 loses
        s2_only  = stake_each * (d2 - 1) - stake_each               # side 2 wins, side 1 loses

        # Expected value: assume 50/50 split on misses (symmetric lines)
        miss_ev = (s1_only + s2_only) / 2
        ev = p_middle * both_win + (1 - p_middle) * miss_ev
        ev_pct = ev / total * 100

        a1, a2 = leg1["american"], leg2["american"]
        sg1 = "+" if a1 > 0 else ""
        sg2 = "+" if a2 > 0 else ""

        color = 0x57F287 if ev > 0 else 0xFAA61A
        embed = discord.Embed(title=f"{sport_key.upper()} Middle Calculator", color=color)
        embed.add_field(name="Middle gap", value=f"`{gap} pts`", inline=True)
        embed.add_field(name="Middle probability", value=f"`{p_middle * 100:.2f}%`", inline=True)
        embed.add_field(name="Stake each side", value=f"`{stake_each:.2f}`", inline=True)
        embed.add_field(
            name="Scenarios",
            value=(
                f"**Both win (middle fires):** `{both_win:+.2f}`\n"
                f"**Side 1 only** (`{sg1}{a1}`): `{s1_only:+.2f}`\n"
                f"**Side 2 only** (`{sg2}{a2}`): `{s2_only:+.2f}`"
            ),
            inline=False,
        )
        embed.add_field(name="EV", value=f"`{ev:+.2f}` (`{ev_pct:+.2f}%`)", inline=True)
        embed.set_footer(text=f"Normal approx σ={sigma} pts • fair if lines symmetric")
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
                "`/calc convert <odds>` — American ↔ decimal ↔ %\n"
                "`/calc arb <odds…> [total]` — cross-book arb check + stake split\n"
                "`/calc hedge <stake> <orig_odds> <hedge_odds>` — lock in profit\n"
                "`/calc middle <gap> <odds1> <odds2> [sport]` — middle EV + probability\n"
                "`/calc vig <odds…>` — market hold + de-vigged fair odds"
            ),
            inline=False,
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(UtilsCog(bot))
