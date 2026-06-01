"""Natural-language trade entry for Rohan.

When Rohan posts in the stock channel, Claude decides whether the message is a
COMMITTED trade (not a hypothetical like "I want to buy"). If so, the bot replies
with a parsed card and buttons:
  • Confirm  — record it (only enabled when complete & valid)
  • Edit / Add details — opens a pre-filled form to fill missing fields or fix a misparse
  • Reject   — dismiss

Before recording it runs safety checks: blocks selling more than you hold, and
warns if the entered price is far from the live market price (fat-finger guard).
Only Confirm/Edit by Rohan records anything.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone

import discord
import httpx
from discord.ext import commands

from db import queries
from .stock import fetch_quote, _normalize_symbol

log = logging.getLogger(__name__)

ROHAN_USER_ID = 688444350325456939
NLP_CHANNEL_ID = 1510100250411536495
HAIKU = "claude-haiku-4-5-20251001"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

# Cheap pre-filter so we don't call Claude on ordinary chatter.
_HINTS = ("bought", "sold", "buy", "sell", "grab", "adding", "added", "wrote",
          "writing", "short", "long", "call", "put", "trim", "closed", "close",
          "scalp", "sold to close", "btc", "eth", "shares", "contracts")

_PROMPT = """You watch a Discord channel where a trader named Rohan posts. Decide if his \
message is a COMMITTED trade — one he has made or is firmly executing right now \
("bought", "sold", "buying", "grabbed", "adding", "writing", "sold to close", \
"shorting", "trimmed"). It counts as committed even if some specifics are missing — \
extract whatever is given and use null for anything absent (we'll ask him for the rest).

Count ONLY trades ROHAN HIMSELF executed (first person: "bought", "I sold", \
"grabbed", "adding"). Set is_trade=false for:
- second/third-person or rhetorical mentions: "you bought", "did you buy", "he sold", \
"you should buy", "u buying?"
- hypothetical/uncommitted: "I want to buy", "I think I'll buy", "should I buy?", \
"thinking about", "might", "considering", "watching", "eyeing", price targets, questions
- slang/banter where the "ticker" isn't a real stock/crypto symbol (e.g. "ts", "fr", \
"idk", "ngl", "this") — these are NOT tickers.

kind is "option" if it mentions calls/puts/strikes/expiry, "crypto" for coins \
(BTC, ETH, SOL, …), else "stock". For options resolve the expiry to YYYY-MM-DD using \
the provided current date (soonest FUTURE such date; never the past).

Respond with ONLY JSON, no prose:
{"is_trade": true|false, "side": "buy"|"sell"|null, "kind": "stock"|"crypto"|"option"|null,
 "ticker": "AAPL"|null, "quantity": <number>|null, "price": <number>|null,
 "opt_type": "call"|"put"|null, "strike": <number>|null, "expiry": "YYYY-MM-DD"|null,
 "summary": "<one-line summary>"}

Rohan's message: %s"""


# ── helpers ───────────────────────────────────────────────────────────────────

def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _missing(t: dict) -> list[str]:
    """Required fields still absent for this trade kind."""
    need = []
    if t.get("side") not in ("buy", "sell"):
        need.append("side")
    if not t.get("ticker"):
        need.append("ticker")
    if not (_num(t.get("quantity")) and _num(t.get("quantity")) > 0):
        need.append("quantity")
    if not (_num(t.get("price")) and _num(t.get("price")) > 0):
        need.append("price")
    if t.get("kind") == "option":
        if t.get("opt_type") not in ("call", "put"):
            need.append("call/put")
        if not (_num(t.get("strike")) and _num(t.get("strike")) > 0):
            need.append("strike")
        if not t.get("expiry"):
            need.append("expiry")
    return need


def _parse_strike_type(raw: str):
    """'130c' / '130 put' / '4.5P' → (strike, opt_type)."""
    raw = (raw or "").strip().lower()
    m = re.search(r"(\d+(?:\.\d+)?)", raw)
    strike = float(m.group(1)) if m else None
    opt = "call" if ("c" in raw or "call" in raw) else "put" if ("p" in raw or "put" in raw) else None
    return strike, opt


def _describe(t: dict) -> str:
    side = (t.get("side") or "?").upper()
    tk = str(t.get("ticker") or "?").upper()
    q = _num(t.get("quantity"))
    px = _num(t.get("price"))
    qty = f"{q:g}" if q else "?"
    pxs = f"${px:,.2f}" if px else "$?"
    if t.get("kind") == "option":
        strike = _num(t.get("strike"))
        ot = (t.get("opt_type") or "?")[0].upper()
        strike_s = f"{strike:g}" if strike else "?"
        return f"**{side}** {qty} × {tk} {strike_s}{ot} {t.get('expiry') or '?'} @ {pxs}"
    return f"**{side}** {qty} {tk} @ {pxs}"


class RohanNLP(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        c = message.content.strip()
        if (message.author.id != ROHAN_USER_ID or message.channel.id != NLP_CHANNEL_ID
                or message.author.bot or not c or not self.api_key):
            return
        if c.startswith(("/", "!", "?")) or len(c) < 5 or len(c) > 300:
            return
        if not any(h in c.lower() for h in _HINTS):
            return
        # Real trade reports carry a number (size/price/strike). Skips banter like
        # "you bought ts" while keeping "bought 10 AAPL".
        if not any(ch.isdigit() for ch in c):
            return
        # Second-person / questions aren't Rohan reporting his own trade.
        low = c.lower()
        if low.startswith(("you ", "u ", "did you", "should ", "would you", "does ")) or "?" in c:
            return
        try:
            trade = await self._classify(c)
        except Exception:
            log.exception("rohan nlp classify failed")
            return
        if not trade or not trade.get("is_trade"):
            return
        # Need at least a side or ticker to be actionable.
        if not trade.get("ticker") and trade.get("side") not in ("buy", "sell"):
            return
        review = await self._review(trade)
        await message.reply(embed=self._card(trade, review),
                            view=TradeReviewView(self, trade), mention_author=False)

    # ── classification ────────────────────────────────────────────────────────
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

    # ── review: missing fields, blocking errors, warnings ─────────────────────
    async def _review(self, t: dict) -> dict:
        missing = _missing(t)
        blocking: list[str] = []
        warnings: list[str] = []
        sell_pnl = None
        tk = (t.get("ticker") or "").upper()
        q = _num(t.get("quantity"))
        px = _num(t.get("price"))
        is_stock_sell = t.get("side") == "sell" and t.get("kind") != "option" and bool(tk)

        if is_stock_sell and q:
            holding = await queries.get_stock_holding(str(ROHAN_USER_ID), tk)
            held = holding["shares"] if holding else 0.0
            if q > held + 1e-9:
                blocking.append(f"You only hold **{held:g}** {tk} — can't sell {q:g}.")
            elif holding and px and holding.get("dca_price"):
                # Preview realized P/L so a wrong sell price stands out.
                sell_pnl = {"realized": q * (px - holding["dca_price"]), "dca": holding["dca_price"]}

        if t.get("kind") != "option" and tk and px:
            try:
                live = (await fetch_quote(_normalize_symbol(tk))).get("price")
            except Exception:
                live = None
            if live and live > 0 and not (0.66 < px / live < 1.5):
                warnings.append(f"You entered **${px:,.2f}** but {tk} is trading ~**${live:,.2f}** — double-check.")

        return {"missing": missing, "blocking": blocking, "warnings": warnings,
                "sell_pnl": sell_pnl, "is_sell": t.get("side") == "sell",
                "ok": not missing and not blocking}

    def _card(self, t: dict, review: dict) -> discord.Embed:
        ok = review["ok"]
        is_sell = review.get("is_sell")
        color = 0x9ece6a if ok else 0xf7768e if review["blocking"] else 0xe0af68
        if not ok:
            title = "🤖 Trade detected — needs a detail"
        elif is_sell:
            title = "🤖 Sale detected — confirm the price?"
        else:
            title = "🤖 Trade detected — confirm?"
        e = discord.Embed(title=title, description=_describe(t), color=color)
        if review["missing"]:
            e.add_field(name="📝 Missing", value="Add it with **Edit**: " + ", ".join(review["missing"]), inline=False)
        # Sells: surface the price + realized P/L so a wrong price is obvious.
        px = _num(t.get("price"))
        if is_sell and px:
            val = f"**${px:,.2f}** — is that the fill? Tap **Edit** to change it."
            sp = review.get("sell_pnl")
            if sp:
                s = "+" if sp["realized"] >= 0 else "−"
                val += f"\nRealizes **{s}${abs(sp['realized']):,.2f}** vs your avg cost ${sp['dca']:,.2f}."
            e.add_field(name="💵 Confirm your sell price", value=val, inline=False)
        for b in review["blocking"]:
            e.add_field(name="🚫 Can't record", value=b, inline=False)
        for w in review["warnings"]:
            e.add_field(name="⚠️ Heads up", value=w, inline=False)
        e.set_footer(text="Only Rohan can act. Edit to fix/complete, Confirm to log, Reject to dismiss.")
        return e

    # ── recording ─────────────────────────────────────────────────────────────
    async def record(self, t: dict) -> None:
        uid, now = str(ROHAN_USER_ID), datetime.now(timezone.utc).isoformat()
        if t.get("kind") == "option":
            await queries.add_option_trade(
                uid, str(t["ticker"]).upper(), t["opt_type"], float(t["strike"]), str(t["expiry"]),
                t["side"], int(float(t["quantity"])), float(t["price"]), now, "via NLP")
        else:
            await queries.add_stock_trade(
                uid, str(t["ticker"]).upper(), t["side"], float(t["quantity"]), float(t["price"]), now, "via NLP")

    async def fire_pick_alert(self, interaction: discord.Interaction, t: dict) -> None:
        cog = self.bot.get_cog("StockCog")
        if not cog:
            return
        action = "BOUGHT" if t["side"] == "buy" else "SOLD"
        if t.get("kind") == "option":
            detail = f"{int(float(t['quantity']))} × `{str(t['ticker']).upper()} {float(t['strike']):g}{t['opt_type'][0].upper()} {t['expiry']}` @ ${float(t['price']):,.2f}"
        else:
            detail = f"**{float(t['quantity']):g}** {str(t['ticker']).upper()} @ ${float(t['price']):,.2f}"
        await cog._maybe_post_rohan_pick(interaction, action, detail)


# ── interactive views / modals ────────────────────────────────────────────────

class TradeReviewView(discord.ui.View):
    def __init__(self, cog: RohanNLP, trade: dict) -> None:
        super().__init__(timeout=1800)
        self.cog = cog
        self.trade = trade

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != ROHAN_USER_ID:
            await interaction.response.send_message("Only Rohan can act on his trade.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        review = await self.cog._review(self.trade)
        if not review["ok"]:
            await interaction.response.send_message(
                "Not ready — " + (review["blocking"][0] if review["blocking"]
                                  else "still missing: " + ", ".join(review["missing"]) + ". Use **Edit**."),
                ephemeral=True)
            return
        try:
            await self.cog.record(self.trade)
        except Exception:
            log.exception("nlp record failed")
            await interaction.response.send_message("Couldn't record — try `/stock buy`.", ephemeral=True)
            return
        e = discord.Embed(title="✅ Trade recorded", description=_describe(self.trade), color=0x9ece6a)
        await interaction.response.edit_message(embed=e, view=None)
        await self.cog.fire_pick_alert(interaction, self.trade)
        self.stop()

    @discord.ui.button(label="✏️ Edit / Add details", style=discord.ButtonStyle.secondary)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        modal = (_OptionModal if self.trade.get("kind") == "option" else _StockModal)(self, interaction.message)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="✖ Reject", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        e = discord.Embed(title="✖ Dismissed", description="Not recorded.", color=0x565f89)
        await interaction.response.edit_message(embed=e, view=None)
        self.stop()


class _StockModal(discord.ui.Modal, title="Trade details"):
    def __init__(self, view: TradeReviewView, message: discord.Message) -> None:
        super().__init__()
        self.view = view
        self.message = message
        t = view.trade
        self.ticker = discord.ui.TextInput(label="Ticker", default=(t.get("ticker") or "").upper(), max_length=12)
        self.qty = discord.ui.TextInput(label="Shares", default=(f"{_num(t.get('quantity')):g}" if _num(t.get("quantity")) else ""), required=True)
        self.price = discord.ui.TextInput(label="Price per share", default=(f"{_num(t.get('price')):g}" if _num(t.get("price")) else ""), required=True)
        self.side = discord.ui.TextInput(label="buy or sell", default=t.get("side") or "buy", max_length=4)
        for f in (self.ticker, self.qty, self.price, self.side):
            self.add_item(f)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        t = dict(self.view.trade)
        t["ticker"] = self.ticker.value.strip().upper()
        t["side"] = self.side.value.strip().lower()
        t["quantity"] = _num(self.qty.value)
        t["price"] = _num(self.price.value)
        if t.get("kind") not in ("stock", "crypto"):
            t["kind"] = "stock"
        await _finish_modal(self, interaction, t)


class _OptionModal(discord.ui.Modal, title="Option trade details"):
    def __init__(self, view: TradeReviewView, message: discord.Message) -> None:
        super().__init__()
        self.view = view
        self.message = message
        t = view.trade
        st = _num(t.get("strike"))
        st_default = (f"{st:g}{(t.get('opt_type') or '')[:1].upper()}" if st else "")
        self.underlying = discord.ui.TextInput(label="Underlying", default=(t.get("ticker") or "").upper(), max_length=12)
        self.qty = discord.ui.TextInput(label="Contracts", default=(f"{int(_num(t.get('quantity')))}" if _num(t.get("quantity")) else ""))
        self.price = discord.ui.TextInput(label="Premium per share", default=(f"{_num(t.get('price')):g}" if _num(t.get("price")) else ""))
        self.strike = discord.ui.TextInput(label="Strike & type (e.g. 130C / 4.5P)", default=st_default)
        self.expiry = discord.ui.TextInput(label="Expiry (YYYY-MM-DD)", default=t.get("expiry") or "")
        for f in (self.underlying, self.qty, self.price, self.strike, self.expiry):
            self.add_item(f)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        t = dict(self.view.trade)
        t["kind"] = "option"
        t["ticker"] = self.underlying.value.strip().upper()
        t["quantity"] = _num(self.qty.value)
        t["price"] = _num(self.price.value)
        t["expiry"] = self.expiry.value.strip()
        strike, opt = _parse_strike_type(self.strike.value)
        t["strike"], t["opt_type"] = strike, opt
        if t.get("side") not in ("buy", "sell"):
            t["side"] = "buy"
        await _finish_modal(self, interaction, t)


async def _finish_modal(modal, interaction: discord.Interaction, t: dict) -> None:
    cog = modal.view.cog
    review = await cog._review(t)
    if not review["ok"]:
        problem = review["blocking"][0] if review["blocking"] else "still missing: " + ", ".join(review["missing"])
        modal.view.trade = t  # keep edits for the next attempt
        await modal.message.edit(embed=cog._card(t, review), view=modal.view)
        await interaction.response.send_message("Almost — " + problem, ephemeral=True)
        return
    try:
        await cog.record(t)
    except Exception:
        log.exception("nlp modal record failed")
        await interaction.response.send_message("Couldn't record — try `/stock buy`.", ephemeral=True)
        return
    e = discord.Embed(title="✅ Trade recorded", description=_describe(t), color=0x9ece6a)
    await modal.message.edit(embed=e, view=None)
    await interaction.response.send_message("✅ Logged.", ephemeral=True)
    await cog.fire_pick_alert(interaction, t)
    modal.view.stop()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RohanNLP(bot))
