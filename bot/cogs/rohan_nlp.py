"""Natural-language trade entry for Rohan.

When Rohan posts in the stock channel, Claude decides whether the message reports
a CONCRETE executed/committed trade (not a hypothetical like "I want to buy").
If so, the bot replies with the parsed trade and a Confirm / Reject button — only
on Confirm is it recorded (and the @rohan stock picks alert fires).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import discord
import httpx
from discord.ext import commands

from db import queries

log = logging.getLogger(__name__)

ROHAN_USER_ID = 688444350325456939
NLP_CHANNEL_ID = 1510100250411536495
HAIKU = "claude-haiku-4-5-20251001"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

_PROMPT = """You watch a Discord channel where a trader named Rohan posts. Decide if his \
message reports a CONCRETE trade he has just made or is firmly executing RIGHT NOW, \
with enough detail to record it.

Record ONLY when the message is committed (past tense or firm present — "bought", \
"sold", "buying", "grabbed", "adding", "sold to close", "writing") AND has ALL of:
- a side (buy or sell)
- a ticker
- a size (shares / contracts / coin amount)
- a price (per share, per coin, or option premium per share)

Do NOT record hesitant or hypothetical messages — return is_trade=false for: \
"I want to buy", "I think I'll buy", "should I buy?", "thinking about", "might", \
"considering", "watching", "eyeing", price targets, questions, or anything missing \
a concrete price or size.

For options also require: call/put, strike, and expiry as YYYY-MM-DD. Use the \
provided current date to resolve dates like "6/20" to the SOONEST FUTURE such date \
(never a past date).

Respond with ONLY JSON, no prose:
{"is_trade": true|false, "side": "buy"|"sell", "kind": "stock"|"crypto"|"option",
 "ticker": "AAPL", "quantity": <number>, "price": <number>,
 "opt_type": "call"|"put"|null, "strike": <number>|null, "expiry": "YYYY-MM-DD"|null,
 "summary": "<one-line human summary>"}
If it is not a concrete trade, return {"is_trade": false}.

Rohan's message: %s"""


class RohanNLP(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if (message.author.id != ROHAN_USER_ID or message.channel.id != NLP_CHANNEL_ID
                or message.author.bot or not message.content.strip() or not self.api_key):
            return
        # Ignore slash/prefix commands and very short noise.
        if message.content.startswith(("/", "!", "?")) or len(message.content) < 6:
            return
        try:
            trade = await self._classify(message.content)
        except Exception:
            log.exception("rohan nlp classify failed")
            return
        if not trade or not trade.get("is_trade"):
            return
        if not self._valid(trade):
            return
        embed = discord.Embed(
            title="🤖 Detected a trade — confirm?",
            description=trade.get("summary") or self._describe(trade),
            color=0xe0af68,
        )
        embed.add_field(name="Parsed", value=self._describe(trade), inline=False)
        embed.set_footer(text="Only you can confirm. Reject if this isn't right.")
        await message.reply(embed=embed, view=TradeConfirmView(self, trade, ROHAN_USER_ID),
                            mention_author=False)

    async def _classify(self, content: str) -> dict | None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        prompt = f"The current date is {today}.\n\n" + (_PROMPT % content)
        async with httpx.AsyncClient() as client:
            r = await client.post(
                ANTHROPIC_URL,
                headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": HAIKU, "max_tokens": 300,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=30.0,
            )
            if r.status_code != 200:
                return None
            text = "".join(b.get("text", "") for b in r.json().get("content", []))
        try:
            return json.loads(text[text.find("{"): text.rfind("}") + 1])
        except (ValueError, json.JSONDecodeError):
            return None

    @staticmethod
    def _valid(t: dict) -> bool:
        if t.get("side") not in ("buy", "sell") or not t.get("ticker"):
            return False
        try:
            if float(t.get("quantity") or 0) <= 0 or float(t.get("price") or 0) <= 0:
                return False
        except (TypeError, ValueError):
            return False
        if t.get("kind") == "option":
            return t.get("opt_type") in ("call", "put") and bool(t.get("strike")) and bool(t.get("expiry"))
        return True

    @staticmethod
    def _describe(t: dict) -> str:
        verb = "Buy" if t["side"] == "buy" else "Sell"
        tk = str(t["ticker"]).upper()
        if t.get("kind") == "option":
            return f"**{verb}** {int(t['quantity'])} × {tk} {float(t['strike']):g}{t['opt_type'][0].upper()} {t['expiry']} @ ${float(t['price']):,.2f}"
        return f"**{verb}** {float(t['quantity']):g} {tk} @ ${float(t['price']):,.2f}"

    async def record(self, t: dict) -> None:
        uid = str(ROHAN_USER_ID)
        now = datetime.now(timezone.utc).isoformat()
        if t.get("kind") == "option":
            await queries.add_option_trade(
                uid, str(t["ticker"]).upper(), t["opt_type"], float(t["strike"]), str(t["expiry"]),
                t["side"], int(t["quantity"]), float(t["price"]), now, "via NLP")
        else:
            await queries.add_stock_trade(
                uid, str(t["ticker"]).upper(), t["side"], float(t["quantity"]), float(t["price"]), now, "via NLP")

    async def fire_pick_alert(self, interaction: discord.Interaction, t: dict) -> None:
        stock_cog = self.bot.get_cog("StockCog")
        if not stock_cog:
            return
        action = "BOUGHT" if t["side"] == "buy" else "SOLD"
        if t.get("kind") == "option":
            detail = f"{int(t['quantity'])} × `{str(t['ticker']).upper()} {float(t['strike']):g}{t['opt_type'][0].upper()} {t['expiry']}` option @ ${float(t['price']):,.2f}"
        else:
            detail = f"**{float(t['quantity']):g}** {str(t['ticker']).upper()} @ ${float(t['price']):,.2f}"
        await stock_cog._maybe_post_rohan_pick(interaction, action, detail)


class TradeConfirmView(discord.ui.View):
    def __init__(self, cog: RohanNLP, trade: dict, author_id: int) -> None:
        super().__init__(timeout=1800)
        self.cog = cog
        self.trade = trade
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Only Rohan can confirm his own trade.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            await self.cog.record(self.trade)
        except Exception:
            log.exception("rohan nlp record failed")
            await interaction.response.send_message("Couldn't record that — log it manually with `/stock buy`.", ephemeral=True)
            return
        for c in self.children:
            c.disabled = True
        e = discord.Embed(title="✅ Trade recorded", description=self.cog._describe(self.trade), color=0x9ece6a)
        await interaction.response.edit_message(embed=e, view=self)
        await self.cog.fire_pick_alert(interaction, self.trade)
        self.stop()

    @discord.ui.button(label="✖ Reject", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        for c in self.children:
            c.disabled = True
        e = discord.Embed(title="✖ Dismissed", description="Not recorded.", color=0x565f89)
        await interaction.response.edit_message(embed=e, view=self)
        self.stop()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RohanNLP(bot))
