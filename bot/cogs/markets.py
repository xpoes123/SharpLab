"""Prediction market commands — /kalshi."""
from __future__ import annotations

import os
import time
from datetime import datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
_RECAP_TIME = dtime(hour=10, minute=0, tzinfo=_ET)  # daily slate recap
RECAP_CHANNEL_ID = 1515214409079853116

import discord
import httpx
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

from db import queries
from shared.models import get_team_abbr, get_kalshi_code
from shared.odds_utils import prob_to_american, kalshi_exec_price, KALSHI_TAKER_FEE
from shared import kalshi_account as ka
from .odds import (KALSHI_SERIES, game_autocomplete, mlb_game_autocomplete,
                   log_game_autocomplete, mlb_log_game_autocomplete)
import logging

log = logging.getLogger(__name__)
load_dotenv()

KALSHI_API_KEY = os.getenv("KALSHI_API_KEY", "")
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"

# Game-level Kalshi series by sport: moneyline, spread/run-line, total.
KALSHI_DERIV = {
    "nba": {"ml": "KXNBAGAME", "spread": "KXNBASPREAD", "total": "KXNBATOTAL"},
    "mlb": {"ml": "KXMLBGAME", "spread": "KXMLBSPREAD", "total": "KXMLBTOTAL"},
}


def _et_date_code(start_utc_iso: str) -> str:
    """Kalshi event-ticker date code (e.g. '26JUN01') for a game's ET date."""
    try:
        dt = datetime.fromisoformat(start_utc_iso.replace("Z", "+00:00")).astimezone(_ET)
        return dt.strftime("%y%b%d").upper()
    except (ValueError, AttributeError):
        return ""


def _event_key(event_ticker: str) -> str:
    """The '<date><time><teams>' middle segment of a Kalshi event ticker."""
    parts = event_ticker.split("-")
    return parts[1] if len(parts) > 1 else event_ticker


def _matches_game(event_ticker: str, h: str, a: str, date_code: str) -> bool:
    """True if a Kalshi event is this game: team codes concatenated at the end of
    the key, and (if known) the right date."""
    key = _event_key(event_ticker).upper()
    teams_ok = key.endswith(a + h) or key.endswith(h + a)
    return teams_ok and (not date_code or key.startswith(date_code))


async def _kalshi_markets(client: httpx.AsyncClient, series: str) -> list[dict]:
    try:
        r = await client.get(
            f"{KALSHI_BASE}/markets",
            headers={"Authorization": f"Bearer {KALSHI_API_KEY}"},
            params={"limit": 200, "status": "open", "series_ticker": series},
            timeout=10.0,
        )
        return r.json().get("markets", []) if r.status_code == 200 else []
    except Exception:
        return []


async def _orderbook(client: httpx.AsyncClient, ticker: str, depth: int = 6) -> dict | None:
    try:
        r = await client.get(
            f"{KALSHI_BASE}/markets/{ticker}/orderbook",
            headers={"Authorization": f"Bearer {KALSHI_API_KEY}"},
            params={"depth": depth}, timeout=10.0,
        )
        if r.status_code != 200:
            return None
        j = r.json()
        return j.get("orderbook_fp") or j.get("orderbook") or {}
    except Exception:
        return None


def _ask_ladder(ob: dict) -> list[tuple[float, float]]:
    """Cost-to-BUY-yes ladder from the NO-side bids: each resting no bid at price
    n (size q) lets you buy q yes contracts at (1 - n). Best (lowest) ask first."""
    levels = [(1 - float(p), float(q)) for p, q in (ob.get("no_dollars") or []) if 0 < float(p) < 1]
    return sorted(levels, key=lambda x: x[0])


def _am(american: int) -> str:
    return f"+{american}" if american > 0 else str(american)


def _fee_american(price: float | None) -> str:
    """Fee-inclusive American odds for a raw yes price (cost to back)."""
    if price is None or not (0 < price < 1):
        return "—"
    eff = min(price + KALSHI_TAKER_FEE * price * (1 - price), 0.99)
    try:
        return _am(prob_to_american(eff))
    except (ValueError, ZeroDivisionError):
        return "—"


def _fmt_prob(p: float) -> str:
    return f"{p * 100:.1f}%"


def _mid_cell(bid: float | None, ask: float | None, last: float | None) -> tuple[str, str, str]:
    """Return (bid_str, ask_str, mid_str) for display."""
    bid_str = _fmt_prob(bid) if bid else "—"
    ask_str = _fmt_prob(ask) if ask else "—"

    if bid and ask:
        mid = (bid + ask) / 2
    elif last:
        mid = last
    else:
        return bid_str, ask_str, "—"

    if not (0 < mid < 1):
        return bid_str, ask_str, "—"

    try:
        american = prob_to_american(mid)
        sign = "+" if american > 0 else ""
        mid_str = f"{_fmt_prob(mid)} ({sign}{american})"
    except (ValueError, ZeroDivisionError):
        mid_str = _fmt_prob(mid)

    return bid_str, ask_str, mid_str


class MarketsCog(commands.Cog):
    kalshi_group = app_commands.Group(name="kalshi", description="Kalshi prediction-market depth & orderbooks")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.recap_loop.start()

    def cog_unload(self) -> None:
        self.recap_loop.cancel()

    # ── Daily slate recap for linked Kalshi accounts (posted in RECAP_CHANNEL_ID) ──

    @tasks.loop(time=_RECAP_TIME)
    async def recap_loop(self) -> None:
        slate = (datetime.now(_ET).date() - timedelta(days=1)).isoformat()
        for uid in await queries.list_kalshi_account_users():
            key = f"kalshi_recap:{uid}"
            try:
                if await queries.get_bot_setting(key) == slate:
                    continue
                embed = await _recap_embed(uid, slate)
                if embed:  # no tracked markets on that slate -> stay quiet
                    channel = self.bot.get_channel(RECAP_CHANNEL_ID) or await self.bot.fetch_channel(RECAP_CHANNEL_ID)
                    await channel.send(f"<@{uid}>", embed=embed, allowed_mentions=discord.AllowedMentions.none())
                await queries.set_bot_setting(key, slate)
            except Exception:
                log.warning("kalshi recap failed for %s", uid, exc_info=True)

    @recap_loop.before_loop
    async def _before_recap(self) -> None:
        await self.bot.wait_until_ready()

    async def _kalshi_impl(self, interaction: discord.Interaction, game: str, sport: str) -> None:
        await interaction.response.defer()

        target = await queries.get_game_by_id(game)
        if target is None:
            await interaction.followup.send("Game not found.")
            return

        if not KALSHI_API_KEY:
            await interaction.followup.send("Kalshi API key not configured.")
            return

        h_abbr = get_kalshi_code(target.home_team, sport)
        a_abbr = get_kalshi_code(target.away_team, sport)
        if not h_abbr or not a_abbr:
            await interaction.followup.send(
                f"No Kalshi code for `{target.home_team}` or `{target.away_team}`."
            )
            return

        date_code = _et_date_code(target.start_time_utc_iso)
        try:
            async with httpx.AsyncClient() as client:
                markets = await _kalshi_markets(client, KALSHI_SERIES.get(sport, "KXNBAGAME"))
        except Exception as e:
            await interaction.followup.send(f"Could not reach Kalshi: {e}")
            return

        # Find the two ML markets for this game by team-code pair (+ date).
        game_markets: dict[str, dict] = {}
        for m in markets:
            if not _matches_game(m.get("event_ticker", ""), h_abbr, a_abbr, date_code):
                continue
            suffix = m.get("ticker", "").split("-")[-1].upper()
            game_markets[suffix] = m

        if not game_markets:
            await interaction.followup.send(
                f"No open Kalshi market found for **{target.away_team} @ {target.home_team}**. "
                "The market may not be open yet or has already closed."
            )
            return

        fetched_at = datetime.now(timezone.utc).astimezone(_ET)

        # Build one row per side
        table_rows: list[tuple[str, str, str, str, str]] = []
        for abbr, team_name in [(a_abbr, target.away_team), (h_abbr, target.home_team)]:
            m = game_markets.get(abbr)
            if m is None:
                table_rows.append((team_name.split()[-1], "—", "—", "—", "—"))
                continue
            bid = float(m["yes_bid_dollars"]) if m.get("yes_bid_dollars") else None
            ask = float(m["yes_ask_dollars"]) if m.get("yes_ask_dollars") else None
            last = float(m["last_price_dollars"]) if m.get("last_price_dollars") else None
            volume = m.get("volume") or 0
            bid_str, ask_str, mid_str = _mid_cell(bid, ask, last)
            table_rows.append((team_name.split()[-1], bid_str, ask_str, mid_str, f"{volume:,}" if volume else "—"))

        # Column widths
        name_w = max(len(r[0]) for r in table_rows) + 2
        C = [name_w, 8, 8, 22, 8]
        header = f"{'Team':<{C[0]}}{'Bid':<{C[1]}}{'Ask':<{C[2]}}{'Mid':<{C[3]}}Volume"
        divider = "─" * (sum(C) + 2)
        lines = [header, divider]
        for name, bid_s, ask_s, mid_s, vol_s in table_rows:
            lines.append(f"{name:<{C[0]}}{bid_s:<{C[1]}}{ask_s:<{C[2]}}{mid_s:<{C[3]}}{vol_s}")

        h = fetched_at.hour % 12 or 12
        ampm = "AM" if fetched_at.hour < 12 else "PM"
        fetched_str = f"{fetched_at.strftime('%b')} {fetched_at.day}, {h}:{fetched_at.strftime('%M')} {ampm} {fetched_at.strftime('%Z')}"

        embed = discord.Embed(
            title=f"Kalshi — {target.away_team} @ {target.home_team}",
            description=(
                f"*fetched {fetched_str}*\n\n"
                "```\n" + "\n".join(lines) + "\n```"
            ),
            color=0x5865F2,
        )
        await interaction.followup.send(embed=embed)

    async def _orderbook_impl(self, interaction: discord.Interaction, game: str, sport: str) -> None:
        await interaction.response.defer()
        target = await queries.get_game_by_id(game)
        if target is None:
            await interaction.followup.send("Game not found."); return
        if not KALSHI_API_KEY:
            await interaction.followup.send("Kalshi API key not configured."); return

        h, a = get_kalshi_code(target.home_team, sport), get_kalshi_code(target.away_team, sport)
        if not h or not a:
            await interaction.followup.send(f"No Kalshi code for `{target.home_team}`/`{target.away_team}`."); return
        date_code = _et_date_code(target.start_time_utc_iso)
        series = KALSHI_DERIV.get(sport, KALSHI_DERIV["nba"])

        async with httpx.AsyncClient() as client:
            ml_mkts = [m for m in await _kalshi_markets(client, series["ml"])
                       if _matches_game(m.get("event_ticker", ""), h, a, date_code)]
            sp_mkts = [m for m in await _kalshi_markets(client, series["spread"])
                       if _matches_game(m.get("event_ticker", ""), h, a, date_code)]
            tot_mkts = [m for m in await _kalshi_markets(client, series["total"])
                        if _matches_game(m.get("event_ticker", ""), h, a, date_code)]
            if not (ml_mkts or sp_mkts or tot_mkts):
                await interaction.followup.send(
                    f"No open Kalshi markets for **{target.away_team} @ {target.home_team}**."); return

            # ── Moneyline: full ask-ladder depth per team ──
            ml_by = {m.get("ticker", "").split("-")[-1].upper(): m for m in ml_mkts}
            ml_lines = []
            for code, name in [(a, target.away_team), (h, target.home_team)]:
                m = ml_by.get(code)
                if not m:
                    ml_lines.append(f"**{name}** — no market"); continue
                ob = await _orderbook(client, m["ticker"])
                ladder = _ask_ladder(ob) if ob else []
                best = ladder[0][0] if ladder else (float(m["yes_ask_dollars"]) if m.get("yes_ask_dollars") else None)
                depth = " · ".join(f"{int(p*100)}¢×{int(q)}" for p, q in ladder[:5]) or "—"
                ml_lines.append(f"**{name}** — back at **{_fee_american(best)}** *(w/ fee)*\n   ask depth: {depth}")

        def _line_rows(mkts, kind):
            rows = []
            for m in mkts:
                ask = float(m["yes_ask_dollars"]) if m.get("yes_ask_dollars") else None
                bid = float(m["yes_bid_dollars"]) if m.get("yes_bid_dollars") else None
                vol = m.get("volume") or 0
                sub = m.get("yes_sub_title") or m.get("ticker", "").split("-")[-1]
                px = f"{int(ask*100)}¢" if ask else "—"
                rows.append((sub, f"{sub} — bid {int(bid*100) if bid else '—'}¢ / ask {px} → **{_fee_american(ask)}**"
                                  + (f"  ·  {vol:,} vol" if vol else "")))
            rows.sort(key=lambda r: r[0])
            return [r[1] for r in rows]

        embed = discord.Embed(
            title=f"📖 Kalshi orderbook — {target.away_team} @ {target.home_team}",
            description="Prices are **fee-inclusive** (Kalshi taker fee added to the ask).",
            color=0x5865F2,
        )
        if ml_lines:
            embed.add_field(name="💰 Moneyline", value="\n".join(ml_lines)[:1024], inline=False)
        sp_rows = _line_rows(sp_mkts, "spread")
        if sp_rows:
            embed.add_field(name=("⚾ Run line" if sport == "mlb" else "🏀 Spread"),
                            value="\n".join(sp_rows)[:1024], inline=False)
        tot_rows = _line_rows(tot_mkts, "total")
        if tot_rows:
            embed.add_field(name="📊 Total", value="\n".join(tot_rows)[:1024], inline=False)
        await interaction.followup.send(embed=embed)

    @kalshi_group.command(name="nba", description="Kalshi market depth for an NBA game")
    @app_commands.describe(game="Select a game")
    @app_commands.autocomplete(game=game_autocomplete)
    async def kalshi(self, interaction: discord.Interaction, game: str) -> None:
        await self._kalshi_impl(interaction, game, "nba")

    @kalshi_group.command(name="mlb", description="Kalshi market depth for an MLB game")
    @app_commands.describe(game="Select a game")
    @app_commands.autocomplete(game=mlb_game_autocomplete)
    async def mlb_kalshi(self, interaction: discord.Interaction, game: str) -> None:
        await self._kalshi_impl(interaction, game, "mlb")

    @kalshi_group.command(name="nba-book", description="Full Kalshi orderbook + spread/total markets for an NBA game")
    @app_commands.describe(game="Select a game (includes 🔴 LIVE in-progress games)")
    @app_commands.autocomplete(game=log_game_autocomplete)
    async def kalshi_orderbook(self, interaction: discord.Interaction, game: str) -> None:
        await self._orderbook_impl(interaction, game, "nba")

    @kalshi_group.command(name="mlb-book", description="Full Kalshi orderbook + run line/total markets for an MLB game")
    @app_commands.describe(game="Select a game (includes 🔴 LIVE in-progress games)")
    @app_commands.autocomplete(game=mlb_log_game_autocomplete)
    async def mlb_kalshi_orderbook(self, interaction: discord.Interaction, game: str) -> None:
        await self._orderbook_impl(interaction, game, "mlb")


    # ── Linked-account reads: /kalshi link|unlink|positions|pnl ──────────

    @kalshi_group.command(name="link", description="Link your Kalshi account with a READ-ONLY API key")
    async def kalshi_link(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(_LinkModal())

    @kalshi_group.command(name="recap", description="Your NBA/NFL/MLB/Esports slate recap (posted daily 10am ET)")
    @app_commands.describe(days_ago="Which slate: 1 = last night (default)")
    async def kalshi_recap(self, interaction: discord.Interaction,
                           days_ago: app_commands.Range[int, 0, 60] = 1) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        uid = str(interaction.user.id)
        if not await queries.get_kalshi_account(uid):
            await interaction.followup.send("No Kalshi account linked — run `/kalshi link` first.", ephemeral=True)
            return
        slate = (datetime.now(_ET).date() - timedelta(days=days_ago)).isoformat()
        try:
            embed = await _recap_embed(uid, slate)
        except ka.KalshiAuthError:
            await interaction.followup.send("Kalshi rejected your key. Re-run `/kalshi link`.", ephemeral=True)
            return
        await interaction.followup.send(embed=embed or discord.Embed(
            description=f"No settled NBA/NFL/MLB/Esports markets on the {slate} slate."), ephemeral=True)

    @kalshi_group.command(name="unlink", description="Delete your stored Kalshi API key")
    async def kalshi_unlink(self, interaction: discord.Interaction) -> None:
        gone = await queries.delete_kalshi_account(str(interaction.user.id))
        _ACCT_CACHE.pop(str(interaction.user.id), None)
        await interaction.response.send_message(
            "🗑️ Key deleted. Revoke it on Kalshi too (Account → API Keys)." if gone
            else "No Kalshi account linked.", ephemeral=True)

    @kalshi_group.command(name="positions", description="Your open Kalshi positions, marked to market")
    @app_commands.describe(public="Post in the channel instead of only to you")
    async def kalshi_positions(self, interaction: discord.Interaction, public: bool = False) -> None:
        acct = await _load_account(interaction, public, with_settlements=False)
        if not acct:
            return
        rows = acct["open"]
        unreal = sum(r["unreal"] for r in rows)
        embed = discord.Embed(title=f"📂 {interaction.user.display_name}'s Kalshi positions",
                              color=0x22C55E if unreal >= 0 else 0xEF4444)
        lines = [f"`{r['side']:<3}` **{r['qty']:g}** × {r['title'][:60]}\n"
                 f"  cost ${r['cost']:,.2f} · mark {r['mark']*100:.0f}¢ · **{r['unreal']:+,.2f}**"
                 for r in rows[:15]]
        embed.description = "\n".join(lines)[:4000] or "No open positions."
        if len(rows) > 15:
            embed.set_footer(text=f"+{len(rows) - 15} more")
        embed.add_field(name="Cash", value=f"${acct['balance']:,.2f}")
        embed.add_field(name="Open value", value=f"${sum(r['value'] for r in rows):,.2f}")
        embed.add_field(name="Unrealized", value=f"{unreal:+,.2f}")
        await interaction.followup.send(embed=embed, ephemeral=not public)

    @kalshi_group.command(name="pnl", description="Your realized + unrealized Kalshi PnL")
    @app_commands.describe(days="Only count markets settled in the last N days (default: all time)",
                           public="Post in the channel instead of only to you")
    async def kalshi_pnl(self, interaction: discord.Interaction,
                         days: app_commands.Range[int, 1, 3650] | None = None, public: bool = False) -> None:
        acct = await _load_account(interaction, public, with_settlements=True)
        if not acct:
            return
        since = None
        if days:
            since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
        s = ka.summarize(acct["settlements"], acct["open"], since)
        total = s["realized"] + s["unrealized"]
        embed = discord.Embed(title=f"📈 {interaction.user.display_name}'s Kalshi PnL"
                                    + (f" — last {days}d" if days else " — all time"),
                              color=0x22C55E if total >= 0 else 0xEF4444)
        embed.add_field(name="Realized", value=f"**{s['realized']:+,.2f}**")
        embed.add_field(name="Unrealized", value=f"{s['unrealized']:+,.2f}")
        embed.add_field(name="Fees", value=f"${s['fees']:,.2f}")
        wr = f"{s['won'] / s['n'] * 100:.0f}%" if s["n"] else "—"
        embed.add_field(name="Settled", value=f"{s['n']} markets · {wr} green")
        embed.add_field(name="Cash", value=f"${acct['balance']:,.2f}")
        if s["by_sport"]:
            embed.add_field(name="By sport", inline=False, value="\n".join(
                f"`{sp:<8}` {n:>4} mkts · staked ${cost:,.0f} · **{pnl:+,.2f}**"
                + (f" ({pnl / cost * 100:+.1f}%)" if cost else "")
                for sp, n, pnl, cost in s["by_sport"][:10]))
        embed.set_footer(text="Unrealized = open positions marked at current bid, before fees")
        await interaction.followup.send(embed=embed, ephemeral=not public)

_ACCT_CACHE: dict[str, tuple[float, bool, dict]] = {}
_ACCT_TTL = 60


async def _load_account(interaction: discord.Interaction, public: bool, with_settlements: bool) -> dict | None:
    """Defer, fetch the caller's Kalshi account (60s cache), or reply with why not."""
    await interaction.response.defer(ephemeral=not public, thinking=True)
    uid = str(interaction.user.id)
    hit = _ACCT_CACHE.get(uid)
    if hit and time.monotonic() - hit[0] < _ACCT_TTL and (hit[1] or not with_settlements):
        return hit[2]
    row = await queries.get_kalshi_account(uid)
    if not row:
        await interaction.followup.send("No Kalshi account linked — run `/kalshi link` first.", ephemeral=True)
        return None
    try:
        acct = await ka.fetch_account(row[0], ka.decrypt(row[1]), with_settlements)
    except ka.KalshiAuthError:
        await interaction.followup.send("Kalshi rejected your key (revoked/expired?). Re-run `/kalshi link`.",
                                        ephemeral=True)
        return None
    except Exception:
        log.warning("kalshi account fetch failed for %s", uid, exc_info=True)
        await interaction.followup.send("Couldn't reach Kalshi — try again in a minute.", ephemeral=True)
        return None
    _ACCT_CACHE[uid] = (time.monotonic(), with_settlements, acct)
    return acct




async def _recap_embed(uid: str, slate: str) -> discord.Embed | None:
    """Recap of one slate (game date, tracked sports) + the 7 days ending there. None if nothing settled that day."""
    row = await queries.get_kalshi_account(uid)
    if not row:
        return None
    key = ka.load_key(ka.decrypt(row[1]))
    async with httpx.AsyncClient(timeout=20) as c:
        settles = await ka._all(c, row[0], key, "/portfolio/settlements", "settlements")
    slates = ka.slates(settles)
    day = slates.get(slate)
    if not day:
        return None
    rows = sorted(day["rows"], key=lambda r: r[1])
    picks = rows[-3:][::-1] + [r for r in rows[:3] if r[1] < 0 and r not in rows[-3:]]
    try:
        titles = await ka.market_titles([t for t, _ in picks])
    except Exception:
        titles = {}
    pretty = datetime.fromisoformat(slate).strftime("%a %b %-d")
    embed = discord.Embed(title=f"📊 Slate recap — {pretty}",
                          color=0x22C55E if day["pnl"] >= 0 else 0xEF4444)
    embed.description = (f"**{day['pnl']:+,.2f}** on ${day['cost']:,.0f} staked · "
                         f"{day['won']}-{day['n'] - day['won']} across {day['n']} markets")
    icon = {"NBA": "🏀", "NFL": "🏈", "MLB": "⚾", "Esports": "🎮"}
    embed.add_field(name="By sport", inline=False, value="\n".join(
        f"{icon.get(sp, '•')} {sp} {w}-{n - w} · **{p:+,.2f}**"
        for sp, (n, w, p) in sorted(day["sports"].items(), key=lambda kv: -abs(kv[1][2]))))
    if picks:
        embed.add_field(name="Biggest swings", inline=False, value="\n".join(
            f"{'🟢' if p >= 0 else '🔴'} **{p:+,.2f}** {titles.get(t, t)[:70]}" for t, p in picks))
    start = (datetime.fromisoformat(slate) - timedelta(days=6)).date().isoformat()
    week = [(d, slates[d]) for d in sorted(slates) if start <= d <= slate]
    wk_pnl = sum(s["pnl"] for _, s in week)
    wk_cost = sum(s["cost"] for _, s in week)
    embed.add_field(name=f"Last 7 days · {wk_pnl:+,.2f}" + (f" ({wk_pnl / wk_cost * 100:+.1f}%)" if wk_cost else ""),
                    inline=False, value="\n".join(
        f"`{datetime.fromisoformat(d).strftime('%a %m/%d')}` {s['won']}-{s['n'] - s['won']} · **{s['pnl']:+,.2f}**"
        for d, s in week))
    embed.set_footer(text="NBA/NFL/MLB/Esports, grouped by game date · /kalshi recap to re-check")
    return embed


class _LinkModal(discord.ui.Modal, title="Link Kalshi (read-only key)"):
    key_id = discord.ui.TextInput(label="API Key ID", placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
                                  max_length=64)
    pem = discord.ui.TextInput(label="Private key (paste the whole .key/.pem file)",
                               style=discord.TextStyle.paragraph, max_length=4000,
                               placeholder="-----BEGIN RSA PRIVATE KEY-----\n...")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        key_id, pem = self.key_id.value.strip(), self.pem.value.strip()
        try:
            ka.load_key(pem)
        except Exception:
            await interaction.followup.send("That doesn't parse as an RSA private key.", ephemeral=True)
            return
        try:
            bal = await ka.fetch_balance(key_id, pem)
        except ka.KalshiAuthError:
            await interaction.followup.send("Kalshi rejected that key ID + private key pair.", ephemeral=True)
            return
        except Exception:
            log.warning("kalshi link check failed", exc_info=True)
            await interaction.followup.send("Couldn't reach Kalshi to verify the key — try again.", ephemeral=True)
            return
        await queries.set_kalshi_account(str(interaction.user.id), key_id, ka.encrypt(pem))
        _ACCT_CACHE.pop(str(interaction.user.id), None)
        await interaction.followup.send(
            f"✅ Linked (cash ${bal:,.2f}). Key stored encrypted; SharpLab only makes GET requests. "
            "Use a **read-only** key — `/kalshi unlink` deletes it.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MarketsCog(bot))
