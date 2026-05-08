"""Stock price lookup — /stock <ticker>."""
import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)


async def fetch_quote(ticker: str) -> dict:
    """Fetch latest price + day change for a ticker via yfinance.

    Returns {price, prev_close, currency, name}. Raises on missing data.
    """
    import yfinance as yf

    def _fetch() -> dict:
        tk = yf.Ticker(ticker)
        info = tk.fast_info
        price = info.get("last_price") if hasattr(info, "get") else getattr(info, "last_price", None)
        prev = info.get("previous_close") if hasattr(info, "get") else getattr(info, "previous_close", None)
        currency = (info.get("currency") if hasattr(info, "get") else getattr(info, "currency", None)) or "USD"

        if price is None or prev is None:
            hist = tk.history(period="2d")
            if hist.empty:
                raise ValueError(f"No price data for {ticker}")
            price = float(hist["Close"].iloc[-1])
            prev = float(hist["Close"].iloc[0]) if len(hist) >= 2 else price

        name = ticker
        try:
            long_name = tk.info.get("longName") or tk.info.get("shortName")
            if long_name:
                name = long_name
        except Exception:
            pass

        return {
            "price": float(price),
            "prev_close": float(prev),
            "currency": currency,
            "name": name,
        }

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch)


class StockCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="stock", description="Look up a stock's current price and Yahoo Finance link")
    @app_commands.describe(ticker="Ticker symbol (e.g. AAPL, TSLA, NVDA)")
    async def stock(self, interaction: discord.Interaction, ticker: str) -> None:
        symbol = ticker.strip().upper()
        if not symbol:
            await interaction.response.send_message("Please provide a ticker.", ephemeral=True)
            return

        await interaction.response.defer()

        try:
            quote = await fetch_quote(symbol)
        except Exception as e:
            log.warning(f"/stock failed for {symbol}: {e}")
            await interaction.followup.send(f"Couldn't fetch a quote for `{symbol}`.", ephemeral=True)
            return

        price = quote["price"]
        prev = quote["prev_close"]
        change = price - prev
        pct = (change / prev * 100) if prev else 0.0
        sign = "+" if change >= 0 else ""
        color = 0x57F287 if change >= 0 else 0xED4245
        url = f"https://finance.yahoo.com/quote/{symbol}"

        embed = discord.Embed(
            title=f"{quote['name']} ({symbol})",
            url=url,
            color=color,
        )
        embed.add_field(name="Price", value=f"`{price:,.2f} {quote['currency']}`", inline=True)
        embed.add_field(name="Change", value=f"`{sign}{change:,.2f} ({sign}{pct:.2f}%)`", inline=True)
        embed.add_field(name="Yahoo Finance", value=f"[Open ↗]({url})", inline=False)

        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StockCog(bot))
