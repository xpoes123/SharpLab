"""Market-signal alerts: arbitrage, middles, steam, and book divergence.

Every few minutes, scans upcoming games' latest odds across all books (+ Kalshi)
and posts any opportunities to a channel. Detection math lives in shared/signals.py.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from db import queries
from shared import signals as sig
from shared.odds_utils import american_to_prob

log = logging.getLogger(__name__)

DEFAULT_SIGNALS_CHANNEL_ID = 1510752551014760658
_CHANNEL_SETTING = "signals_channel_id"
SCAN_MINUTES = 4
LOOKAHEAD_HOURS = 18      # only games tipping within this window
BASELINE_MINUTES = 30     # steam/divergence compare-against window
ALERT_TTL_SEC = 6 * 3600  # re-alert the same signal at most once per this period
STALE_MINUTES = 45        # ignore games whose latest odds are older than this
ARB_MIN_ROI = 1.0         # ignore arbs below this % (snapshots aren't simultaneous)
MIDDLE_MIN_EV = 0.0       # only fire middles the base-rate model scores as +EV


def _am(v: int) -> str:
    return f"+{v}" if v > 0 else str(v)


def _by_source(snaps) -> dict:
    return {s.source: s.payload for s in snaps}


class Signals(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._alerted: dict[str, float] = {}  # signature -> last-alert monotonic ts
        self.scan_loop.start()

    def cog_unload(self) -> None:
        self.scan_loop.cancel()

    group = app_commands.Group(name="signals", description="Arbitrage & line-movement alerts")

    # ── channel config ───────────────────────────────────────────────────────
    async def _channel_id(self) -> int:
        raw = await queries.get_bot_setting(_CHANNEL_SETTING)
        try:
            return int(raw) if raw else DEFAULT_SIGNALS_CHANNEL_ID
        except ValueError:
            return DEFAULT_SIGNALS_CHANNEL_ID

    @group.command(name="channel", description="Set the channel for market-signal alerts")
    async def set_channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("You need **Manage Server**.", ephemeral=True)
            return
        await queries.set_bot_setting(_CHANNEL_SETTING, str(channel.id))
        await interaction.response.send_message(f"✅ Market signals will post in {channel.mention}.", ephemeral=True)

    @group.command(name="scan", description="Force a market-signal scan right now")
    async def scan_now(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        n = await self._scan()
        await interaction.followup.send(f"Scan complete — {n} alert(s) posted.", ephemeral=True)

    # ── scan loop ────────────────────────────────────────────────────────────
    @tasks.loop(minutes=SCAN_MINUTES)
    async def scan_loop(self) -> None:
        try:
            await self._scan()
        except Exception:
            log.exception("signals scan_loop failed")

    @scan_loop.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()

    async def _post(self, embed: discord.Embed) -> None:
        cid = await self._channel_id()
        ch = self.bot.get_channel(cid) or await self.bot.fetch_channel(cid)
        await ch.send(embed=embed)

    def _fresh(self, sig_key: str) -> bool:
        """True if this signal hasn't been alerted within ALERT_TTL_SEC."""
        now = time.monotonic()
        # prune
        self._alerted = {k: t for k, t in self._alerted.items() if now - t < ALERT_TTL_SEC}
        if sig_key in self._alerted:
            return False
        self._alerted[sig_key] = now
        return True

    async def _scan(self) -> int:
        now = datetime.now(timezone.utc)
        horizon = (now + timedelta(hours=LOOKAHEAD_HOURS)).isoformat()
        baseline_cut = (now - timedelta(minutes=BASELINE_MINUTES)).isoformat()
        stale_cut = now - timedelta(minutes=STALE_MINUTES)

        games = []
        for sport in ("nba", "mlb"):
            games += await queries.get_games_in_window(now.isoformat(), horizon, sport)

        posted = 0
        for g in games:
            try:
                snaps = await queries.get_latest_snapshots_for_game(g.game_id)
                if not snaps:
                    continue
                # Skip games whose newest line is stale (game likely already tipped / inactive).
                newest = max(s.captured_at_utc_iso for s in snaps)
                try:
                    if datetime.fromisoformat(newest.replace("Z", "+00:00")) < stale_cut:
                        continue
                except ValueError:
                    pass
                current = _by_source(snaps)
                label = f"{g.away_team} @ {g.home_team}"
                emoji = "🏀" if g.sport == "nba" else "⚾"

                # Arbitrage / middles use the current cross-book picture. The arb
                # floor filters marginal/stale edges (snapshots aren't simultaneous).
                arb = sig.find_ml_arb(current, min_roi=ARB_MIN_ROI)
                if arb and self._fresh(f"arb:{g.game_id}:{arb['home_book']}:{arb['away_book']}:{round(arb['roi_pct'])}"):
                    await self._post(self._arb_embed(emoji, label, g, arb)); posted += 1

                tot = sig.find_total_middle(current)
                if tot:
                    prof = (None if tot["kind"] == "total_arb" else
                            sig.middle_profit(tot["over_odds"], tot["under_odds"],
                                              sig.estimate_middle_hit(g.sport, "total", tot["over_line"], tot["under_line"])))
                    if self._middle_fires(tot["kind"], prof) and self._fresh(
                            f"{tot['kind']}:{g.game_id}:{tot['over_book']}:{tot['under_book']}:{tot['over_line']}:{tot['under_line']}"):
                        await self._post(self._total_embed(emoji, label, g, tot, prof)); posted += 1

                spr = sig.find_spread_middle(current)
                if spr:
                    prof = sig.middle_profit(spr["home_odds"], spr["away_odds"],
                                             sig.estimate_middle_hit(g.sport, "spread", spr["home_line"], spr["away_line"]))
                    if self._middle_fires("spread_middle", prof) and self._fresh(
                            f"spread_middle:{g.game_id}:{spr['home_book']}:{spr['away_book']}:{spr['home_line']}:{spr['away_line']}"):
                        await self._post(self._spread_embed(emoji, label, g, spr, prof)); posted += 1

                # Steam / divergence compare to ~30 min ago.
                since = await queries.get_snapshots_for_game_since(g.game_id, baseline_cut)
                baseline: dict = {}
                for s in since:  # ascending → first per source is the oldest in window
                    baseline.setdefault(s.source, s.payload)
                move = sig.detect_ml_moves(baseline, current)
                if move:
                    bucket = int(now.timestamp() // (BASELINE_MINUTES * 60))
                    if self._fresh(f"{move['kind']}:{g.game_id}:{bucket}"):
                        await self._post(self._move_embed(emoji, label, g, move)); posted += 1
            except Exception:
                log.exception("signals scan failed for %s", g.game_id)
        if posted:
            log.info("signals: posted %d alert(s)", posted)
        return posted

    # ── embeds ───────────────────────────────────────────────────────────────
    def _arb_embed(self, emoji, label, g, a) -> discord.Embed:
        e = discord.Embed(
            title=f"🚨 Arbitrage — {label}",
            description=f"{emoji} Lock a guaranteed **{a['roi_pct']:.2f}%** by betting both sides.",
            color=0x9ece6a,
        )
        e.add_field(name=f"{g.home_team} (home)",
                    value=f"**{_am(a['home_odds'])}** on `{a['home_book']}`\nstake {a['home_stake_pct']:.0f}%", inline=True)
        e.add_field(name=f"{g.away_team} (away)",
                    value=f"**{_am(a['away_odds'])}** on `{a['away_book']}`\nstake {a['away_stake_pct']:.0f}%", inline=True)
        e.set_footer(text="Moneyline arb across books — verify prices are still live before betting.")
        return e

    @staticmethod
    def _middle_fires(kind: str, prof: dict | None) -> bool:
        """Arbs always; middles only when the base-rate model says +EV (so we don't
        flag losing 'both can win' bets like a -200/-195 run-line middle)."""
        if kind.endswith("arb"):
            return True
        return bool(prof) and prof["ev_roi"] >= MIDDLE_MIN_EV

    @staticmethod
    def _window_text(lo: float, hi: float) -> str:
        a, b = min(lo, hi), max(lo, hi)
        ints = [n for n in range(int(a) - 2, int(b) + 3) if a < n < b]
        if not ints:
            return f"{a:g}–{b:g}"
        return str(ints[0]) if len(ints) == 1 else f"{ints[0]}–{ints[-1]}"

    @staticmethod
    def _ev_field(e: discord.Embed, prof: dict) -> None:
        roi = prof["ev_roi"]
        verdict = "✅ +EV" if roi >= 1 else "🟡 thin +EV" if roi >= 0 else "🔴 −EV as priced"
        e.add_field(name="Profitability",
                    value=(f"hits ~**{prof['hit_p'] * 100:.0f}%** · breakeven **{prof['breakeven'] * 100:.0f}%** · "
                           f"est **{roi:+.1f}%** ROI  {verdict}\n"
                           f"*base-rate model — better for low-total / pick'em games; verify the line isn't stale*"),
                    inline=False)

    def _total_embed(self, emoji, label, g, t, prof) -> discord.Embed:
        is_arb = t["kind"] == "total_arb"
        e = discord.Embed(
            title=f"{'🚨 Total Arb' if is_arb else '🎯 Total Middle'} — {label}",
            description=(f"{emoji} **Over {t['over_line']} ({_am(t['over_odds'])})** on `{t['over_book']}`  +  "
                        f"**Under {t['under_line']} ({_am(t['under_odds'])})** on `{t['under_book']}`"),
            color=0x9ece6a if is_arb else 0xe0af68,
        )
        if is_arb:
            e.add_field(name="Edge", value=f"{(1 - t['cost']) * 100:.2f}% guaranteed", inline=True)
        else:
            e.add_field(name="Window", value=f"both cash if the total is **{self._window_text(t['over_line'], t['under_line'])}**", inline=True)
            if prof:
                self._ev_field(e, prof)
        return e

    def _spread_embed(self, emoji, label, g, s, prof) -> discord.Embed:
        ho, ao = s.get("home_odds"), s.get("away_odds")
        run_line = g.sport == "mlb"
        e = discord.Embed(
            title=f"🎯 {'Run-Line' if run_line else 'Spread'} Middle — {label}",
            description=(f"{emoji} **{g.home_team} {s['home_line']:+g} ({_am(ho) if ho else '?'})** on `{s['home_book']}`  +  "
                        f"**{g.away_team} {s['away_line']:+g} ({_am(ao) if ao else '?'})** on `{s['away_book']}`"),
            color=0xe0af68,
        )
        win = "decided by exactly 1 run" if run_line else f"the margin is **{self._window_text(-s['home_line'], s['away_line'])}**"
        e.add_field(name="Window", value=f"both cash if {win}", inline=True)
        if prof:
            self._ev_field(e, prof)
        return e

    def _move_embed(self, emoji, label, g, m) -> discord.Embed:
        if m["kind"] == "steam":
            side = g.home_team if m["toward"] == "home" else g.away_team
            e = discord.Embed(
                title=f"📈 Steam — {label}",
                description=f"{emoji} **{side}** moneyline steamed across {len(m['books'])} books (~{m['avg_cents']}¢) in the last {BASELINE_MINUTES} min.",
                color=0x7aa2f7,
            )
            e.add_field(name="Moved", value=", ".join(f"`{b}`" for b in m["books"]), inline=False)
            if m.get("lagging"):
                e.add_field(name="⏳ Lagging (haven't moved yet)",
                            value=", ".join(f"`{b}`" for b in m["lagging"]), inline=False)
        else:
            e = discord.Embed(
                title=f"↔️ Books Disagree — {label}",
                description=f"{emoji} Books are moving in **opposite directions** on this line — possible stale numbers or split info.",
                color=0xbb9af7,
            )
            e.add_field(name=f"→ {g.home_team}", value=", ".join(f"`{b}`" for b in m["home_books"]), inline=True)
            e.add_field(name=f"→ {g.away_team}", value=", ".join(f"`{b}`" for b in m["away_books"]), inline=True)
        return e


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Signals(bot))
