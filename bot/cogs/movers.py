"""/movers — top S&P 100 gainers and losers today."""
import asyncio
import logging
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)


# Curated S&P 100 — the OEX constituents. Used as the universe for /movers.
# Updating this list every so often keeps the universe fresh; exact membership
# isn't critical for a "what moved today" view.
SP100: list[str] = [
    "AAPL", "ABBV", "ABT", "ACN", "ADBE", "AIG", "AMD", "AMGN", "AMT", "AMZN",
    "AVGO", "AXP", "BA", "BAC", "BK", "BKNG", "BLK", "BMY", "BRK-B", "C",
    "CAT", "CHTR", "CL", "CMCSA", "COF", "COP", "COST", "CRM", "CSCO", "CVS",
    "CVX", "DE", "DHR", "DIS", "DUK", "EMR", "F", "FDX", "GD", "GE",
    "GILD", "GM", "GOOG", "GOOGL", "GS", "HD", "HON", "IBM", "INTC", "INTU",
    "ISRG", "JNJ", "JPM", "KHC", "KO", "LIN", "LLY", "LMT", "LOW", "MA",
    "MCD", "MDLZ", "MDT", "MET", "META", "MMM", "MO", "MRK", "MS", "MSFT",
    "NEE", "NFLX", "NKE", "NVDA", "ORCL", "PEP", "PFE", "PG", "PLTR", "PM",
    "PYPL", "QCOM", "RTX", "SBUX", "SCHW", "SO", "SPG", "T", "TGT", "TMO",
    "TMUS", "TSLA", "TXN", "UNH", "UNP", "UPS", "USB", "V", "VZ", "WFC",
    "WMT", "XOM",
]


@dataclass
class Mover:
    ticker: str
    price: float
    prev_close: float
    pct: float

    @property
    def change(self) -> float:
        return self.price - self.prev_close


async def fetch_movers(tickers: list[str]) -> list[Mover]:
    """Batch-fetch 2 days of closes for tickers via yfinance, return Movers.

    yfinance.download is sync and chunks the request internally; runs once
    in a thread executor.
    """
    import yfinance as yf

    def _fetch() -> list[Mover]:
        df = yf.download(
            tickers=tickers,
            period="2d",
            interval="1d",
            group_by="ticker",
            auto_adjust=False,
            progress=False,
            threads=True,
        )
        movers: list[Mover] = []
        for sym in tickers:
            try:
                # When passed a list, yfinance returns a multi-index DataFrame
                # keyed by ticker at the outer level.
                col = df[sym]["Close"].dropna()
                if len(col) < 2:
                    continue
                prev = float(col.iloc[-2])
                price = float(col.iloc[-1])
                if prev <= 0:
                    continue
                pct = (price - prev) / prev * 100
                movers.append(Mover(ticker=sym, price=price, prev_close=prev, pct=pct))
            except Exception as e:
                log.debug(f"movers: skip {sym}: {e}")
        return movers

    return await asyncio.get_running_loop().run_in_executor(None, _fetch)


def _yahoo_url(sym: str) -> str:
    return f"https://finance.yahoo.com/quote/{sym}"


def _fmt_row(m: Mover) -> str:
    sign = "+" if m.pct >= 0 else ""
    return f"[`{m.ticker:<5}`]({_yahoo_url(m.ticker)}) `{m.price:>8,.2f}` `{sign}{m.pct:>5.2f}%`"


class MoversCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="movers", description="Top S&P 100 gainers and losers today")
    async def movers(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        try:
            movers = await fetch_movers(SP100)
        except Exception as e:
            log.warning(f"/movers fetch failed: {e}")
            await interaction.followup.send("Couldn't pull market data right now.", ephemeral=True)
            return

        if not movers:
            await interaction.followup.send(
                "No price data available — markets may not have opened yet.",
                ephemeral=True,
            )
            return

        movers.sort(key=lambda m: m.pct, reverse=True)
        gainers = movers[:5]
        losers = list(reversed(movers[-5:]))  # biggest loser first

        avg = sum(m.pct for m in movers) / len(movers)
        breadth_up = sum(1 for m in movers if m.pct > 0)
        breadth_down = len(movers) - breadth_up

        color = 0x57F287 if avg >= 0 else 0xED4245
        embed = discord.Embed(
            title="Today's Movers — S&P 100",
            description=(
                f"Avg move `{'+' if avg >= 0 else ''}{avg:.2f}%` · "
                f"🟢 {breadth_up} up · 🔴 {breadth_down} down"
            ),
            color=color,
        )
        embed.add_field(
            name="🟢 Top Gainers",
            value="\n".join(_fmt_row(m) for m in gainers) or "—",
            inline=True,
        )
        embed.add_field(
            name="🔴 Top Losers",
            value="\n".join(_fmt_row(m) for m in losers) or "—",
            inline=True,
        )
        embed.set_footer(text=f"{len(movers)} of {len(SP100)} tickers priced")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MoversCog(bot))
