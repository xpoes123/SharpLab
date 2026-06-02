"""Live in-game swing alerts for logged bets.

Every few minutes, for each open bet on a game that's currently live, pull the
live probability of the bet's side from Kalshi (a continuous market) and ping the
bettor in #games when it has swung hard since they placed it.

Adding a new market type is just one resolver in _RESOLVERS — it maps a bet to a
live win-probability (0–1) for the side that was bet.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict

import discord
import httpx
from discord import app_commands
from discord.ext import commands, tasks

from db import queries
from shared.models import get_kalshi_code
from shared.odds_utils import american_to_prob, side_is_home
from .markets import _kalshi_markets, _matches_game, _et_date_code, KALSHI_DERIV

log = logging.getLogger(__name__)

GAMES_CHANNEL_ID = 1510694785034354849
_CHANNEL_SETTING = "livebet_alert_channel"
POLL_MINUTES = 5
SWING_THRESHOLD = 0.20   # ≥20 percentage-point move since last alert


def _kalshi_mid(m: dict) -> float | None:
    """Live win probability (0–1) from a Kalshi market's bid/ask mid or last."""
    yb, ya = float(m.get("yes_bid_dollars") or 0), float(m.get("yes_ask_dollars") or 0)
    p = (yb + ya) / 2 if (yb or ya) else float(m.get("last_price_dollars") or 0)
    return p if 0 < p < 1 else None


def _find_event_markets(markets: list[dict], bet: dict) -> list[dict]:
    h, a = get_kalshi_code(bet["home_team"], bet["sport"]), get_kalshi_code(bet["away_team"], bet["sport"])
    date = _et_date_code(bet["start_time"])
    et = next((m.get("event_ticker") for m in markets
               if _matches_game(m.get("event_ticker", ""), h, a, date)), None)
    return [m for m in markets if m.get("event_ticker") == et] if et else []


def _ml_prob(bet: dict, mkts: dict) -> float | None:
    """Live win prob of the bet's team (moneyline)."""
    ish = side_is_home(bet["side"], bet["home_team"], bet["away_team"])
    if ish is None:
        return None
    code = get_kalshi_code(bet["home_team"] if ish else bet["away_team"], bet["sport"])
    if not code:
        return None
    for m in _find_event_markets(mkts.get("ml", []), bet):
        if m.get("ticker", "").split("-")[-1].upper() == code:
            return _kalshi_mid(m)
    return None


def _total_prob(bet: dict, mkts: dict) -> float | None:
    """Live prob the bet's over/under cashes."""
    if bet["side"] not in ("over", "under") or bet["line"] is None:
        return None
    line = float(bet["line"])
    for m in _find_event_markets(mkts.get("total", []), bet):
        mt = re.search(r"Over (\d+(?:\.\d+)?)", m.get("yes_sub_title") or "")
        if mt and abs(float(mt.group(1)) - line) < 0.01:
            over = _kalshi_mid(m)
            if over is None:
                return None
            return over if bet["side"] == "over" else 1 - over
    return None


# market type → (resolver, which Kalshi series to fetch)
_RESOLVERS = {
    "moneyline": (_ml_prob, "ml"),
    "total": (_total_prob, "total"),
    # "spread": (_spread_prob, "spread"),  # add a resolver here to track spreads
}


class LiveBets(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._last: dict[int, float] = {}  # bet_id → last-alerted prob
        self.watch_loop.start()

    def cog_unload(self) -> None:
        self.watch_loop.cancel()

    async def _channel_id(self) -> int:
        raw = await queries.get_bot_setting(_CHANNEL_SETTING)
        try:
            return int(raw) if raw else GAMES_CHANNEL_ID
        except ValueError:
            return GAMES_CHANNEL_ID

    @app_commands.command(name="livebet-channel", description="Set the channel for live in-game bet swing alerts")
    async def set_channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("You need **Manage Server**.", ephemeral=True)
            return
        await queries.set_bot_setting(_CHANNEL_SETTING, str(channel.id))
        await interaction.response.send_message(f"✅ Live-bet swing alerts will post in {channel.mention}.", ephemeral=True)

    @app_commands.command(name="livebet-alerts", description="Opt in/out of live in-game swing pings on your bets")
    @app_commands.describe(enabled="On to get pinged when your live bets swing; off to stop (default: off)")
    @app_commands.choices(enabled=[
        app_commands.Choice(name="on", value="on"),
        app_commands.Choice(name="off", value="off"),
    ])
    async def set_alerts(self, interaction: discord.Interaction, enabled: app_commands.Choice[str]) -> None:
        on = enabled.value == "on"
        await queries.set_livebet_alerts_enabled(str(interaction.user.id), on)
        msg = ("✅ You'll get pinged when your live in-game bets swing hard."
               if on else "✅ Live-bet swing pings are off for you.")
        await interaction.response.send_message(msg, ephemeral=True)

    @tasks.loop(minutes=POLL_MINUTES)
    async def watch_loop(self) -> None:
        try:
            bets = await queries.get_open_bets_on_live_games()
            opted_in = await queries.get_livebet_optin_users()
        except Exception:
            log.exception("livebets: failed to load live bets")
            return
        # Opt-in only: never ping a bettor who hasn't enabled live swing alerts.
        bets = [b for b in bets if b["market"] in _RESOLVERS and b["sport"] in KALSHI_DERIV
                and b["discord_user"] in opted_in]
        if not bets:
            return
        by_sport: dict[str, list[dict]] = defaultdict(list)
        for b in bets:
            by_sport[b["sport"]].append(b)

        async with httpx.AsyncClient() as client:
            for sport, sbets in by_sport.items():
                # Fetch only the series we actually need this cycle.
                needed = {_RESOLVERS[b["market"]][1] for b in sbets}
                mkts = {k: await _kalshi_markets(client, KALSHI_DERIV[sport][k]) for k in needed}
                for b in sbets:
                    try:
                        await self._check_bet(b, mkts)
                    except Exception:
                        log.exception("livebets: check failed for bet %s", b["bet_id"])

    async def _check_bet(self, b: dict, mkts: dict) -> None:
        resolver = _RESOLVERS[b["market"]][0]
        prob = resolver(b, mkts)
        if prob is None:
            return
        baseline = self._last.get(b["bet_id"])
        if baseline is None:
            # First sighting: anchor to the implied prob when the bet was placed.
            baseline = self._last[b["bet_id"]] = american_to_prob(b["odds"])
        if abs(prob - baseline) < SWING_THRESHOLD:
            return
        self._last[b["bet_id"]] = prob
        await self._alert(b, baseline, prob)

    async def _alert(self, b: dict, was: float, now: float) -> None:
        up = now > was
        label = b["side"] if b["market"] == "moneyline" else f"{b['side'].title()} {b['line']:g}"
        emoji = "🟢📈" if up else "🔴📉"
        verb = "surging" if up else "sliding"
        embed = discord.Embed(
            title=f"{emoji} Live swing — {label}",
            description=(f"<@{b['discord_user']}> your **{label}** bet is {verb}.\n"
                        f"Live win prob **{now * 100:.0f}%** (was {was * 100:.0f}% when you bet).\n"
                        f"*{b['away_team']} @ {b['home_team']}* · via Kalshi"),
            color=0x9ece6a if up else 0xf7768e,
        )
        try:
            cid = await self._channel_id()
            ch = self.bot.get_channel(cid) or await self.bot.fetch_channel(cid)
            await ch.send(content=f"<@{b['discord_user']}>", embed=embed)
        except Exception:
            log.debug("livebets: alert send failed", exc_info=True)

    @watch_loop.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LiveBets(bot))
