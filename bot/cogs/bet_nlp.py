"""NLP bet logging — when someone posts a bet in chat, offer a one-tap 'Log this
bet?' button. Mirrors the stock NLP (rohan_nlp): a cheap keyword pre-filter, then
Haiku classifies first-person committed bets, we match the team to an upcoming
game, and a confirm button writes it to the bettor's record. Lowers the friction
that means nobody but the owner logs bets."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import discord
import httpx
from discord.ext import commands

from db import queries
from shared.models import Bet
from shared.odds_utils import fmt_prob, parse_odds_input

log = logging.getLogger(__name__)

HAIKU = "claude-haiku-4-5-20251001"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

# Channels to watch (discussion + closing-lines by default).
_DEFAULT_CHANNELS = {1477512816863740092, 1485475287054418151}
_env = os.getenv("BET_NLP_CHANNEL_IDS", "")
BET_NLP_CHANNELS = {int(x) for x in _env.split(",") if x.strip().isdigit()} or _DEFAULT_CHANNELS

# Cheap pre-filter so we don't call Claude on ordinary chatter.
_HINTS = ("took ", "take ", "betting", "bet ", "hammer", "tailing", "tail ", "parlay",
          "moneyline", " ml", "spread", "over ", "under ", "u on", " on ", "units", "unit ",
          "love ", "playing ", " play ", "riding", "i have ", "i've got", "locked in")


_PROMPT = """You watch a Discord channel where friends post sports bets. Decide if a message \
is the AUTHOR reporting a bet THEY just made or are firmly making now ("took", "bet", \
"hammering", "I have", "tailing", "parlayed", "riding", "on X"). Missing specifics are \
fine — extract what's given, null for the rest.

Count ONLY first-person committed bets. Set is_bet=false for:
- questions, second/third person ("you taking?", "should I", "he took", "who do we like")
- hypotheticals / opinions with no actual bet ("might take", "thinking", "watching", \
"eyeing", "Lakers are winning", "I like the over" with no stake/commitment)
- general banter where no specific team/side is being bet

Extract:
- sport: "nba" or "mlb"
- team: the team the bet is ON, as mentioned (e.g. "Lakers", "Knicks", "Spurs"). For a \
total, still give a team in the game if mentioned, else null.
- market: "spread" | "moneyline" | "total" | "prop"
- side: for spread/moneyline = the team name; for total = "over" or "under"
- line: the number for spread or total (e.g. -4.5, 218.5); null for moneyline
- odds: American odds if stated (-110, +150); null if absent
- units: stake in units if stated (e.g. "2u", "3 units"); null if absent

Respond with ONLY JSON, no prose:
{"is_bet": true|false, "sport": "nba"|"mlb"|null, "team": str|null, "market": "spread"|"moneyline"|"total"|"prop"|null,
 "side": str|null, "line": number|null, "odds": int|null, "units": number|null, "summary": "<one line>"}

Message: %s"""


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


class BetNLP(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        c = (message.content or "").strip()
        if (message.author.bot or message.channel.id not in BET_NLP_CHANNELS
                or not c or not self.api_key):
            return
        if c.startswith(("/", "!", "?")) or len(c) < 6 or len(c) > 280:
            return
        low = c.lower()
        if not any(h in low for h in _HINTS):
            return
        if not any(ch.isdigit() for ch in c):  # a real bet carries a number (line/odds/stake)
            return
        if "?" in c or low.startswith(("you ", "u ", "did ", "should ", "would ", "who ", "does ")):
            return
        try:
            bet = await self._classify(c)
        except Exception:
            log.debug("bet nlp classify failed", exc_info=True)
            return
        if not bet or not bet.get("is_bet"):
            return

        built = await self._build(bet)
        if built is None:
            return  # couldn't match a game — stay quiet rather than guess
        await message.reply(embed=self._card(built), view=ConfirmBetView(self, built, message.author.id),
                            mention_author=False)

    async def _classify(self, content: str) -> dict | None:
        prompt = _PROMPT % content
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

    async def _build(self, bet: dict) -> dict | None:
        """Match the team to a game, normalize side/odds/units, default the gaps."""
        market = bet.get("market") or "moneyline"
        if market == "prop":
            return None  # props go through /bet log prop (needs player + stat)
        team = (bet.get("team") or "").strip()
        game = await queries.find_upcoming_game_by_team(team) if team else None
        if game is None:
            return None
        # Resolve the side to the matched full team name (spread/ml) or over/under (total).
        if market == "total":
            side = (bet.get("side") or "").lower()
            side = "over" if "o" in side else "under"
        else:
            tl = team.lower()
            side = game.home_team if tl in game.home_team.lower() else game.away_team
        odds = bet.get("odds")
        if odds is None:
            odds = -110  # sensible default; the bettor can fix it via Edit
        units = _num(bet.get("units")) or 1.0
        return {
            "game_id": game.game_id, "game_str": f"{game.away_team.split()[-1]} @ {game.home_team.split()[-1]}",
            "sport": bet.get("sport") or "nba", "market": market, "side": side,
            "line": _num(bet.get("line")), "odds": int(odds), "units": float(units),
            "odds_defaulted": bet.get("odds") is None,
        }

    def _card(self, b: dict) -> discord.Embed:
        parts = []
        if b["market"] == "spread" and b["line"] is not None:
            parts.append(f"{b['side']} {b['line']:+g}")
        elif b["market"] == "total" and b["line"] is not None:
            parts.append(f"{b['side'].title()} {b['line']:g}")
        else:
            parts.append(b["side"])
        odds_s = f"+{b['odds']}" if b["odds"] > 0 else str(b["odds"])
        desc = (f"**{b['game_str']}** · {b['market']}\n"
                f"**{' '.join(parts)}** @ `{odds_s}` ({fmt_prob(b['odds'])}) · {b['units']:g}u")
        if b["odds_defaulted"]:
            desc += "\n*(odds defaulted to -110 — hit Edit to fix)*"
        embed = discord.Embed(title="📝 Log this bet?", description=desc, colour=0x7aa2f7)
        embed.set_footer(text="Confirm to add it to your record · CLV tracked at close")
        return embed

    async def record(self, uid: int, b: dict) -> int:
        bet = Bet(
            game_id=b["game_id"], placed_at=datetime.now(timezone.utc).isoformat(),
            discord_user=str(uid), book="other", market=b["market"], side=b["side"],
            odds=b["odds"], units=b["units"], line=b["line"], notes="via NLP",
        )
        return await queries.insert_bet(bet)


class ConfirmBetView(discord.ui.View):
    def __init__(self, cog: BetNLP, bet: dict, author_id: int) -> None:
        super().__init__(timeout=600)
        self.cog = cog
        self.bet = bet
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Only the person who posted this can log it.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✅ Log it", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        bet_id = await self.cog.record(interaction.user.id, self.bet)
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(content=f"✅ Logged as bet **#{bet_id}** — see `/bet view`.", view=self)

    @discord.ui.button(label="✏️ Edit", style=discord.ButtonStyle.secondary)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(_BetEditModal(self, interaction.message))

    @discord.ui.button(label="✖ Not a bet", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="👍 Ignored.", embed=None, view=None)


class _BetEditModal(discord.ui.Modal, title="Fix bet details"):
    def __init__(self, view: ConfirmBetView, message: discord.Message) -> None:
        super().__init__()
        self.view = view
        self.message = message
        b = view.bet
        self.odds = discord.ui.TextInput(label="Odds (American/decimal/cents)", required=False,
                                         default=(f"+{b['odds']}" if b["odds"] > 0 else str(b["odds"])))
        self.units = discord.ui.TextInput(label="Units", required=False, default=f"{b['units']:g}")
        self.line = discord.ui.TextInput(label="Line (spread/total number)", required=False,
                                         default=("" if b["line"] is None else f"{b['line']:g}"))
        for it in (self.odds, self.units, self.line):
            self.add_item(it)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        b = self.view.bet
        if self.odds.value.strip():
            try:
                b["odds"], _ = parse_odds_input(self.odds.value.strip())
                b["odds_defaulted"] = False
            except Exception:
                pass
        if self.units.value.strip():
            b["units"] = _num(self.units.value.strip()) or b["units"]
        if self.line.value.strip():
            b["line"] = _num(self.line.value.strip())
        await interaction.response.edit_message(embed=self.view.cog._card(b), view=self.view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BetNLP(bot))
