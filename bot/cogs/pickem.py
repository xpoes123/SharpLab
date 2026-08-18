"""Daily NBA/MLB pick'em.

Each morning (10 AM ET) the bot recaps last night's results, then posts today's
NBA + MLB slate — one message per game with ✈️ (away) and 🏠 (home) reactions.
Vote by reacting; voting locks at each game's tip-off. Resolved off the existing
score pipeline (games.status='final'). Leaderboard ranks by total correct,
accuracy, or streak points.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import discord
from discord import app_commands, ui
from discord.ext import commands, tasks

from db import queries
from shared.models import get_team_abbr
from shared.odds_utils import devig_two_way
from shared.pickem_scoring import (  # noqa: F401 (compute_pickem_standings re-exported)
    STREAK_CAP,
    compute_pickem_standings,
    pick_prob,
    pick_units,
    potential_units,
)

log = logging.getLogger(__name__)

# Win-prob source preference: Kalshi first, then sharp books, then anything.
_ODDS_PREFERENCE = ("kalshi", "pinnacle", "fanduel", "draftkings", "fanatics", "betmgm")


async def _win_probs(game_id: str) -> tuple[float, float, str] | None:
    """Fair (home, away) win prob + source for a game, devigged from the best
    available two-sided moneyline (Kalshi preferred). None if unavailable."""
    try:
        snaps = await queries.get_latest_snapshots_for_game(game_id)
    except Exception:
        log.exception("pickem: snapshot fetch failed for %s", game_id)
        return None
    by_source: dict[str, tuple[int, int]] = {}
    for s in snaps:
        payload = s.payload
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                continue
        if not isinstance(payload, dict):
            continue
        mh, ma = payload.get("ml_home"), payload.get("ml_away")
        if mh and ma:
            by_source[s.source] = (int(mh), int(ma))
    if not by_source:
        return None
    src = next((s for s in _ODDS_PREFERENCE if s in by_source), next(iter(by_source)))
    hp, ap = devig_two_way(*by_source[src])
    return hp, ap, src

ET = ZoneInfo("America/New_York")
POST_TIME = time(hour=10, minute=0, tzinfo=ET)
# Monday-morning "last week's winner" callout — runs daily, but only fires on Mondays.
WEEK_WINNER_TIME = time(hour=11, minute=0, tzinfo=ET)

PICKEM_CHANNEL_DEFAULT = 1510694785034354849
_PICKEM_CHANNEL_SETTING = "pickem_channel"
_WEEK_WINNER_SETTING = "pickem_week_winner"  # stores the ISO-week label already announced

AWAY_EMOJI = "✈️"
HOME_EMOJI = "🏠"
SPORT_EMOJI = {"nba": "🏀", "wnba": "🏀", "mlb": "⚾", "nfl": "🏈"}

# Per-sport daily pick'em cap. Sports not listed post their whole slate. MLB runs
# ~15 games/day, so we keep only 5: any game with a pinned team (below) first,
# then filled out with the marquee (biggest-favorite) games. NFL Sundays run ~13
# games, so cap it too (preseason days stay well under the cap and all post).
_SPORT_DAILY_CAP = {"mlb": 5, "nfl": 8}
PICKEM_SPORTS = ("nba", "wnba", "mlb", "nfl")
PICKEM_WEB_URL = "https://sharplab.djiang.xyz/pickem"  # full slate lives here
# MLB teams whose games are always kept in the daily slate. Matched as a substring
# of either team's full name (e.g. "red sox" ⊂ "Boston Red Sox").
_PINNED_MLB = ("brewers", "red sox", "mets", "rockies", "dodgers")


def _pinned_mlb(game) -> bool:
    names = f"{game.home_team} {game.away_team}".lower()
    return any(p in names for p in _PINNED_MLB)


MIN_FAV_LEN = 3  # a favorite shorter than this would match too many teams


def _is_posted(message_id: str) -> bool:
    """True if this pick'em row was posted to Discord (numeric message id). Web-only
    slate rows use game_id as their message_id, so they have no Discord message/view."""
    return str(message_id).isdigit()


def _fav_side(teams: list[str], home_team: str, away_team: str) -> str | None:
    """Which side to auto-pick for a user's favorite teams in this game.
    'home'/'away' if exactly one side matches a favorite, else None (skip: neither,
    or both — an intra-favorite matchup we can't pick a side on)."""
    h, a = home_team.lower(), away_team.lower()
    home_fav = any(len(t) >= MIN_FAV_LEN and t in h for t in teams)
    away_fav = any(len(t) >= MIN_FAV_LEN and t in a for t in teams)
    if home_fav and not away_fav:
        return "home"
    if away_fav and not home_fav:
        return "away"
    return None

MIN_PICKS_FOR_ACCURACY = 20
COLOUR = 0x1ABC9C


# ── Helpers ──────────────────────────────────────────────────────────────────


def _parse_iso(s: str) -> datetime:
    """Parse a stored UTC ISO timestamp (handles the trailing-Z form)."""
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _week_bounds(dt: datetime) -> tuple[str, str]:
    """The pick'em week containing `dt` — Monday 00:00 America/New_York → the next
    Monday 00:00 ET — returned as (start_utc_iso, end_utc_iso). End is exclusive."""
    et = dt.astimezone(ET)
    monday = et.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=et.weekday())
    end = monday + timedelta(days=7)
    return monday.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat()


def _iso_week_label(dt: datetime) -> str:
    """ISO year-week label (e.g. '2026-W33') for the pick'em week containing `dt`.
    Used as the week-winner dedupe key."""
    et = dt.astimezone(ET)
    y, w, _ = et.isocalendar()
    return f"{y}-W{w:02d}"


def _filter_week(rows: list[dict], s_utc: str, e_utc: str) -> list[dict]:
    """Keep only resolved-pick rows whose game start falls in [s_utc, e_utc).
    Preserves input order (user, then start_time) so streaks still compute right."""
    lo, hi = _parse_iso(s_utc), _parse_iso(e_utc)
    out: list[dict] = []
    for r in rows:
        try:
            st = _parse_iso(r["start_time"])
        except (ValueError, AttributeError, KeyError, TypeError):
            continue
        if lo <= st < hi:
            out.append(r)
    return out


async def _weekly_standings(dt: datetime) -> dict[str, dict]:
    """Pick'em standings over the week that contains `dt`."""
    rows = await queries.get_pickem_resolved_picks()
    s_utc, e_utc = _week_bounds(dt)
    return compute_pickem_standings(_filter_week(rows, s_utc, e_utc))


def _winner_from_scores(home_score: int, away_score: int) -> str:
    return "home" if home_score > away_score else "away"


def _fmt_et_time(start_time_iso: str) -> str:
    try:
        dt = datetime.fromisoformat(start_time_iso.replace("Z", "+00:00"))
        local = dt.astimezone(ET)
        return local.strftime("%-I:%M %p ET")
    except Exception:
        return "TBD"


def _discord_time(start_time_iso: str) -> str:
    """Discord dynamic timestamp — renders in each viewer's own timezone with
    the full date, plus a relative hint. Falls back to ET text."""
    try:
        ts = int(datetime.fromisoformat(start_time_iso.replace("Z", "+00:00")).timestamp())
        return f"<t:{ts}:F> (<t:{ts}:R>)"
    except Exception:
        return _fmt_et_time(start_time_iso)


# ── Message / embed builders ─────────────────────────────────────────────────


def _odds_line(game: dict) -> str:
    hp, ap = game.get("home_prob"), game.get("away_prob")
    if hp is None or ap is None:
        return ""
    src = game.get("odds_source") or "market"
    label = "Kalshi" if src == "kalshi" else src
    return f"\n📊 win%: {game['away_team']} {ap * 100:.0f}% · {game['home_team']} {hp * 100:.0f}% ({label})"


def _game_message(game: dict, *, locked: bool = False, result: str | None = None) -> str:
    emoji = SPORT_EMOJI.get(game["sport"], "🏟️")
    head = f"{emoji} **{game['away_team']}** {AWAY_EMOJI} @ {HOME_EMOJI} **{game['home_team']}**"
    odds = _odds_line(game)
    if result:
        return f"{head}{odds}\n{result}"
    if locked:
        return f"{head}{odds}\n🔒 Voting closed — game underway."
    return f"{head}{odds}\n🕒 {_discord_time(game['start_time'])} — tap a team to bet!"


def _result_line(game: dict, counts: dict[str, int]) -> str:
    winner_team = game["home_team"] if game["winner"] == "home" else game["away_team"]
    tally = f"{AWAY_EMOJI} {counts.get('away', 0)} · {HOME_EMOJI} {counts.get('home', 0)}"
    return f"✅ **{winner_team}** won — votes: {tally}"


# ── Daily card: scores, picks, payouts ───────────────────────────────────────


def _last(name: str) -> str:
    return name.split()[-1].lower()


def _pair_key(home_team: str, away_team: str) -> frozenset:
    """Unordered key by team last-names — matches a scoreboard game to a pick'em
    game without depending on exact name formatting across data sources."""
    return frozenset((_last(home_team), _last(away_team)))


def _team_short(name: str, sport: str) -> str:
    return get_team_abbr(name, sport) or name.split()[-1]


def _build_score_map(fetched: list[dict]) -> dict[frozenset, dict]:
    """Index scoreboard rows (the shape returned by odds.py's fetchers) by
    team-pair key → {as_, hs, state, detail}. state ∈ final|live|pre."""
    out: dict[frozenset, dict] = {}
    for g in fetched:
        try:
            home, away = g["home_team"]["full_name"], g["visitor_team"]["full_name"]
        except (KeyError, TypeError):
            continue
        status, period = str(g.get("status", "")), g.get("period", 0) or 0
        state = "final" if status.startswith("Final") else ("live" if period > 0 else "pre")
        out[_pair_key(home, away)] = {
            "hs": g.get("home_team_score") or 0,
            "as_": g.get("visitor_team_score") or 0,
            "state": state,
            "detail": status.strip(),
        }
    return out


def _format_game(game: dict, mine: dict | None, score: dict | None, now_iso: str) -> dict:
    """Build one card row + its accounting. Returns {line, settled_delta,
    live_risk, live_pot} — settled_delta is None unless the game is resolved."""
    sport = game["sport"]
    emoji = SPORT_EMOJI.get(sport, "🏟️")
    away_s = _team_short(game["away_team"], sport)
    home_s = _team_short(game["home_team"], sport)
    resolved = bool(game["resolved"])
    have_scores = score is not None and (score.get("state") != "pre")

    if have_scores:
        matchup = f"**{away_s}** {score['as_']}–{score['hs']} **{home_s}**"
    else:
        matchup = f"**{away_s}** @ **{home_s}**"

    # Ledger truth: a game is "settled" only once pick'em has resolved it.
    if resolved:
        state = "final"
    elif (score and score["state"] in ("live", "final")) or game["start_time"] <= now_iso:
        state = "live"
    else:
        state = "pre"

    out = {"settled_delta": None, "live_risk": 0.0, "live_pot": 0.0}

    if mine:
        pick = mine["pick"]
        stake = mine["stake"]
        prob = pick_prob(pick, game["home_prob"], game["away_prob"])
        pick_s = away_s if pick == "away" else home_s
        if state == "final":
            won = (pick == game["winner"]) if game.get("winner") else bool(mine.get("correct"))
            delta = pick_units(stake, prob, won)
            out["settled_delta"] = delta
            tail = f"{'✅' if won else '❌'} {pick_s} {stake}u **{delta:+.1f}u**"
        elif state == "live":
            out["live_risk"], out["live_pot"] = float(stake), potential_units(stake, prob)
            lean = ""
            if have_scores:
                mine_sc = score["as_"] if pick == "away" else score["hs"]
                opp_sc = score["hs"] if pick == "away" else score["as_"]
                lean = " 🟢" if mine_sc > opp_sc else (" 🔴" if mine_sc < opp_sc else " 🟡")
            tail = f"🔵 {pick_s} {stake}u{lean}"
        else:
            out["live_risk"], out["live_pot"] = float(stake), potential_units(stake, prob)
            tail = f"🕒 {_fmt_et_time(game['start_time'])} · {pick_s} {stake}u"
    else:
        if state == "final":
            win_s = home_s if game.get("winner") == "home" else away_s
            tail = f"{win_s} won" if game.get("winner") else "Final"
        elif state == "live":
            tail = f"🔴 {score['detail']}" if (score and score.get("detail")) else "🔴 underway"
        else:
            tail = f"🕒 {_fmt_et_time(game['start_time'])}"

    out["line"] = f"{emoji} {matchup} · {tail}"
    return out


def _today_board(all_picks: list[dict]) -> dict[str, dict]:
    """Per-user settled record + net units for one day's picks (resolved only)."""
    board: dict[str, dict] = {}
    for r in all_picks:
        if not r["resolved"] or r["correct"] is None:
            continue
        b = board.setdefault(r["discord_user"], {"net": 0.0, "w": 0, "l": 0})
        won = bool(r["correct"])
        prob = pick_prob(r["pick"], r["home_prob"], r["away_prob"])
        b["net"] += pick_units(r["stake"], prob, won)
        b["w"] += int(won)
        b["l"] += int(not won)
    return board


async def _live_score_map(games: list[dict]) -> dict[frozenset, dict]:
    """Live + final scores for the slate, matched by team-pair. Best-effort —
    returns {} on any failure so the card still renders from DB state."""
    from bot.cogs.odds import _fetch_scores_espn, _fetch_scores_nba  # local: avoid import cycle

    by_sport: dict[str, set[str]] = {}
    for g in games:
        try:
            d = datetime.fromisoformat(
                g["start_time"].replace("Z", "+00:00")
            ).astimezone(ET).date().isoformat()
        except (ValueError, AttributeError, KeyError):
            continue
        by_sport.setdefault(g["sport"], set()).add(d)

    score_map: dict[frozenset, dict] = {}
    for sport, dates in by_sport.items():
        try:
            if sport == "nba":
                fetched = await _fetch_scores_nba(sorted(dates))
            else:
                fetched, _ = await _fetch_scores_espn(sorted(dates), sport)
            score_map.update(_build_score_map(fetched))
        except Exception:
            log.warning("pickem card: score fetch failed for %s", sport, exc_info=True)
    return score_map


# ── Leaderboard view ─────────────────────────────────────────────────────────


class LeaderboardView(ui.View):
    MODES = [
        ("Total Correct", "correct"), ("Accuracy", "accuracy"),
        ("Streak Points", "points"), ("Market P&L", "units"),
    ]

    def __init__(
        self, bot: commands.Bot, alltime: dict[str, dict],
        weekly: dict[str, dict] | None = None, week_label: str = "",
    ) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.alltime = alltime
        self.weekly = weekly or {}
        self.week_label = week_label
        self.period = "alltime"  # or "week"
        self.mode = "correct"
        self.message: discord.Message | None = None
        self._names: dict[str, str] = {}

    @property
    def standings(self) -> dict[str, dict]:
        return self.weekly if self.period == "week" else self.alltime

    async def _name(self, uid: str) -> str:
        if uid not in self._names:
            try:
                self._names[uid] = (await self.bot.fetch_user(int(uid))).display_name
            except Exception:
                self._names[uid] = f"Player {uid[:6]}"
        return self._names[uid]

    def _ranked(self) -> list[tuple[str, dict]]:
        items = list(self.standings.items())
        if self.mode == "correct":
            items.sort(key=lambda kv: (kv[1]["correct"], kv[1]["accuracy"]), reverse=True)
        elif self.mode == "accuracy":
            items = [kv for kv in items if kv[1]["total"] >= MIN_PICKS_FOR_ACCURACY]
            items.sort(key=lambda kv: (kv[1]["accuracy"], kv[1]["correct"]), reverse=True)
        elif self.mode == "units":
            items.sort(key=lambda kv: (kv[1]["units"], kv[1]["correct"]), reverse=True)
        else:
            items.sort(key=lambda kv: (kv[1]["points"], kv[1]["correct"]), reverse=True)
        return items[:15]

    async def embed(self) -> discord.Embed:
        mode_label = next(lbl for lbl, key in self.MODES if key == self.mode)
        period_label = ("This Week" + (f" ({self.week_label})" if self.week_label else "")
                        if self.period == "week" else "All-Time")
        embed = discord.Embed(
            title=f"🏆 Pick'em Leaderboard — {mode_label}",
            colour=COLOUR,
        )
        embed.set_author(name=period_label)
        ranked = self._ranked()
        if not ranked:
            if self.mode == "accuracy":
                embed.description = f"No one qualifies yet (min {MIN_PICKS_FOR_ACCURACY} picks for accuracy)."
            elif self.period == "week":
                embed.description = "No scored picks yet this week."
            else:
                embed.description = "No scored picks yet."
            return embed
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for rank, (uid, s) in enumerate(ranked, 1):
            name = await self._name(uid)
            prefix = medals.get(rank, f"`#{rank}`")
            acc = f"{s['accuracy'] * 100:.0f}%"
            if self.mode == "correct":
                stat = f"**{s['correct']}** correct ({acc})"
            elif self.mode == "accuracy":
                stat = f"**{acc}** ({s['correct']}/{s['total']})"
            elif self.mode == "units":
                stat = f"**{s['units']:+.1f}u** ({s['correct']}/{s['total']})"
            else:
                stat = f"**{s['points']}** pts ({s['correct']} correct)"
            lines.append(f"{prefix} **{name}** — {stat}")
        embed.description = "\n".join(lines)
        return embed

    @ui.button(label="Total Correct", style=discord.ButtonStyle.primary)
    async def total_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        self.mode = "correct"
        await interaction.response.edit_message(embed=await self.embed(), view=self)

    @ui.button(label="Accuracy", style=discord.ButtonStyle.secondary)
    async def acc_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        self.mode = "accuracy"
        await interaction.response.edit_message(embed=await self.embed(), view=self)

    @ui.button(label="Streak Points", style=discord.ButtonStyle.secondary)
    async def pts_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        self.mode = "points"
        await interaction.response.edit_message(embed=await self.embed(), view=self)

    @ui.button(label="Market P&L", style=discord.ButtonStyle.secondary)
    async def units_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        self.mode = "units"
        await interaction.response.edit_message(embed=await self.embed(), view=self)

    @ui.button(label="📅 This Week", style=discord.ButtonStyle.success, row=1)
    async def period_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        self.period = "week" if self.period == "alltime" else "alltime"
        button.label = "🗓️ All-Time" if self.period == "week" else "📅 This Week"
        await interaction.response.edit_message(embed=await self.embed(), view=self)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


# ── Vote buttons (stake 1-5 units) ───────────────────────────────────────────

STAKE_OPTIONS = (1, 2, 3, 4, 5)


def _potential(stake: int, prob: float | None) -> str:
    return f"win **+{potential_units(stake, prob):.1f}u** / lose **-{stake}u**"


async def _game_open(message_id: str) -> dict | None:
    """Return the game row if it's still accepting bets, else None."""
    game = await queries.get_pickem_game(message_id)
    now = datetime.now(timezone.utc).isoformat()
    if game is None or game["locked"] or game["start_time"] <= now:
        return None
    return game


class StakeButton(ui.Button):
    def __init__(self, message_id: str, team: str, team_name: str, n: int) -> None:
        super().__init__(label=f"{n}u", style=discord.ButtonStyle.success)
        self.message_id = message_id
        self.team = team
        self.team_name = team_name
        self.n = n

    async def callback(self, interaction: discord.Interaction) -> None:
        game = await _game_open(self.message_id)
        if game is None:
            await interaction.response.edit_message(content="🔒 Voting is closed for this game.", view=None)
            return
        locked = await queries.record_pickem_pick(
            self.message_id, str(interaction.user.id), self.team, self.n,
        )
        if not locked:
            await interaction.response.edit_message(
                content="❌ You already locked in a bet on this game — bets are final.", view=None,
            )
            return
        _day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        _coins = await queries.grant_activity_reward(str(interaction.user.id), "pickem_pick", _day)
        prob = game["away_prob"] if self.team == "away" else game["home_prob"]
        await interaction.response.edit_message(
            content=f"✅ Bet **{self.n}u** on **{self.team_name}** (final) — {_potential(self.n, prob)} when it resolves."
            + (f"  ·  +{_coins} 🪙" if _coins else ""),
            view=None,
        )


class StakeView(ui.View):
    def __init__(self, message_id: str, team: str, team_name: str) -> None:
        super().__init__(timeout=120)
        for n in STAKE_OPTIONS:
            self.add_item(StakeButton(message_id, team, team_name, n))


class TeamButton(ui.Button):
    def __init__(self, message_id: str, team: str, team_name: str, emoji: str) -> None:
        super().__init__(
            label=team_name[:76], emoji=emoji, style=discord.ButtonStyle.primary,
            custom_id=f"pkm:{message_id}:{team}",
        )
        self.message_id = message_id
        self.team = team
        self.team_name = team_name

    async def callback(self, interaction: discord.Interaction) -> None:
        game = await _game_open(self.message_id)
        if game is None:
            await interaction.response.send_message("🔒 Voting is closed for this game.", ephemeral=True)
            return
        existing = await queries.get_pickem_pick_full(self.message_id, str(interaction.user.id))
        if existing:
            side = game["away_team"] if existing["pick"] == "away" else game["home_team"]
            await interaction.response.send_message(
                f"❌ You already bet **{existing['stake']}u** on **{side}** — bets are final.",
                ephemeral=True,
            )
            return
        prob = game["away_prob"] if self.team == "away" else game["home_prob"]
        pct = f" (market win% {prob * 100:.0f}%)" if prob else ""
        await interaction.response.send_message(
            f"**{self.team_name}** to win{pct} — choose your stake (1–5 units). **Bets are final.**",
            view=StakeView(self.message_id, self.team, self.team_name),
            ephemeral=True,
        )


class GamePickView(ui.View):
    """Persistent vote view — one per game message. Re-registered on startup."""

    def __init__(self, message_id: str, away_team: str, home_team: str) -> None:
        super().__init__(timeout=None)
        self.add_item(TeamButton(message_id, "away", away_team, AWAY_EMOJI))
        self.add_item(TeamButton(message_id, "home", home_team, HOME_EMOJI))


# ── Cog ──────────────────────────────────────────────────────────────────────


class PickemCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        # Re-register persistent vote views for games still open, so buttons keep
        # working across restarts.
        now = datetime.now(timezone.utc).isoformat()
        try:
            for g in await queries.get_unlocked_pickem_games():
                if g["start_time"] <= now or not _is_posted(g["message_id"]):
                    continue
                self.bot.add_view(
                    GamePickView(g["message_id"], g["away_team"], g["home_team"]),
                    message_id=int(g["message_id"]),
                )
        except Exception:
            log.exception("pickem: failed to re-register vote views")
        self.post_loop.start()
        self.lock_loop.start()
        self.resolve_loop.start()
        self.favorites_loop.start()
        self.week_winner_loop.start()

    async def cog_unload(self) -> None:
        self.post_loop.cancel()
        self.lock_loop.cancel()
        self.resolve_loop.cancel()
        self.favorites_loop.cancel()
        self.week_winner_loop.cancel()

    pickem = app_commands.Group(name="pickem", description="Daily NBA/WNBA/MLB pick'em")
    favorites = app_commands.Group(name="favorites", description="Auto-pick your favorite teams", parent=pickem)

    async def _channel(self) -> discord.TextChannel | None:
        raw = await queries.get_bot_setting(_PICKEM_CHANNEL_SETTING)
        try:
            cid = int(raw) if raw else PICKEM_CHANNEL_DEFAULT
        except ValueError:
            cid = PICKEM_CHANNEL_DEFAULT
        ch = self.bot.get_channel(cid)
        return ch if isinstance(ch, discord.TextChannel) else None

    # ── Morning routine ──────────────────────────────────────────────────────

    @tasks.loop(time=POST_TIME)
    async def post_loop(self) -> None:
        channel = await self._channel()
        if channel is None:
            log.warning("pickem: alerts channel not found")
            return
        try:
            await self._post_recap(channel)
            await self._post_today(channel)
        except Exception:
            log.exception("pickem: morning routine failed")

    @post_loop.before_loop
    async def _before_post(self) -> None:
        await self.bot.wait_until_ready()

    async def _post_recap(self, channel: discord.TextChannel) -> None:
        yesterday = (datetime.now(ET).date() - timedelta(days=1)).isoformat()
        games = await queries.get_pickem_games_for_date(yesterday)
        resolved = [g for g in games if g["resolved"]]
        if not resolved:
            return
        embed = discord.Embed(
            title=f"🌙 Last Night's Pick'em — {yesterday}", colour=0x9B59B6,
        )
        per_user: dict[str, list[int]] = {}  # uid -> [correct, total]
        game_lines = []
        for g in resolved:
            counts = await queries.get_pickem_vote_counts(g["message_id"])
            game_lines.append(_result_line(g, counts).replace("✅ ", ""))
            for p in await queries.get_pickem_picks_for_message(g["message_id"]):
                rec = per_user.setdefault(p["discord_user"], [0, 0])
                rec[1] += 1
                if p["correct"]:
                    rec[0] += 1
        embed.add_field(name="Results", value="\n".join(game_lines)[:1024], inline=False)
        if per_user:
            ranked = sorted(per_user.items(), key=lambda kv: (kv[1][0], kv[1][0] / kv[1][1]), reverse=True)
            lines = []
            for uid, (correct, total) in ranked[:15]:
                try:
                    name = (await self.bot.fetch_user(int(uid))).display_name
                except Exception:
                    name = f"Player {uid[:6]}"
                lines.append(f"**{name}** — {correct}/{total}")
            embed.add_field(name="How everyone did", value="\n".join(lines)[:1024], inline=False)
        await channel.send(embed=embed)

    async def _slate(self, s_utc: str, e_utc: str) -> list:
        """The FULL pick'em slate across all sports in [s_utc, e_utc), sorted by tip time.
        Every game is pickable on the website; Discord posts only `_capped(...)` of these."""
        slate: list = []
        for sport in PICKEM_SPORTS:
            try:
                slate += await queries.get_games_in_window(s_utc, e_utc, sport=sport)
            except Exception:
                log.exception("pickem: failed to load %s games", sport)
        slate.sort(key=lambda g: g.start_time_utc_iso)
        return slate

    async def _capped(self, full: list) -> list:
        """The Discord subset of the full slate: a sport with a daily cap
        (see _SPORT_DAILY_CAP) keeps only its most lopsided 'marquee favorite' games —
        ranked by distance from a coin flip — plus any pinned-team games (MLB). The
        website still shows every game; this just keeps the Discord post from spamming."""
        by_sport: dict[str, list] = {}
        for g in full:
            by_sport.setdefault(g.sport, []).append(g)

        slate: list = []
        for sport, games in by_sport.items():
            cap = _SPORT_DAILY_CAP.get(sport)
            if cap is None or len(games) <= cap:
                slate += games
                continue
            pinned = sorted((g for g in games if sport == "mlb" and _pinned_mlb(g)),
                            key=lambda g: g.start_time_utc_iso)
            pinned_ids = {g.game_id for g in pinned}
            scored = []
            for g in games:
                if g.game_id in pinned_ids:
                    continue
                probs = await _win_probs(g.game_id)
                dev = abs(probs[0] - 0.5) if probs else -1.0  # no odds → fill last
                scored.append((dev, g.start_time_utc_iso, g))
            scored.sort(key=lambda t: (-t[0], t[1]))  # most lopsided, then earliest
            fill = max(0, cap - len(pinned))
            slate += pinned[:cap] + [g for _, _, g in scored[:fill]]

        slate.sort(key=lambda g: g.start_time_utc_iso)
        return slate

    async def _persist_web_game(self, g, posted_date: str) -> None:
        """Persist a non-Discord slate game as a web-only pick'em row so it's pickable at
        /pickem. Keyed by game_id (its synthetic message_id) — picks, lock, and resolve all
        key off message_id/game_id, so these rows behave exactly like posted ones."""
        if await queries.pickem_game_exists(g.game_id, posted_date):
            return
        if await queries.get_game_scores(g.game_id) is not None:
            return  # already final — no point opening it for picks
        probs = await _win_probs(g.game_id)
        hp, ap, src = probs if probs else (None, None, None)
        await queries.add_pickem_game(
            g.game_id, g.game_id, g.sport, g.home_team, g.away_team,
            g.start_time_utc_iso, posted_date, hp, ap, src,
        )

    async def _post_today(self, channel: discord.TextChannel) -> None:
        now_et = datetime.now(ET)
        today = now_et.date().isoformat()
        start = now_et.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        s_utc = start.astimezone(timezone.utc).isoformat()
        e_utc = end.astimezone(timezone.utc).isoformat()

        full = await self._slate(s_utc, e_utc)

        if not full:
            await channel.send(f"📋 **Daily Pick'em — {today}**\nNo games on the slate today.")
            return

        posted = await self._capped(full)
        posted_ids = {g.game_id for g in posted}
        extra = len(full) - len(posted)
        more = (f"\n🌐 That's the marquee slate — pick **all {len(full)} games** "
                f"({extra} more) at {PICKEM_WEB_URL}") if extra > 0 else ""
        await channel.send(
            f"📋 **Daily Pick'em — {today}**\n"
            "Tap a team, then stake **1–5 units**. Everyone starts at 0u and can go "
            "negative — beat the market to climb the board. Each game locks at tip-off!" + more
        )
        for g in posted:
            if await queries.pickem_game_exists(g.game_id, today):
                continue
            if await queries.get_game_scores(g.game_id) is not None:
                continue  # already final earlier today — skip voting
            await self._post_game(channel, {
                "game_id": g.game_id, "sport": g.sport,
                "home_team": g.home_team, "away_team": g.away_team,
                "start_time": g.start_time_utc_iso,
            }, today)
        # Persist the rest of the full slate as web-only picks (not posted to Discord).
        for g in full:
            if g.game_id not in posted_ids:
                await self._persist_web_game(g, today)

    async def _post_for_date(self, channel: discord.TextChannel, date: str) -> None:
        """Post (or re-post) the pick'em slate for an explicit date string (YYYY-MM-DD)."""
        dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=ET)
        start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        s_utc = start.astimezone(timezone.utc).isoformat()
        e_utc = end.astimezone(timezone.utc).isoformat()

        full = await self._slate(s_utc, e_utc)

        if not full:
            await channel.send(f"📋 **Pick'em — {date}**\nNo games on the slate for {date}.")
            return

        posted = await self._capped(full)
        posted_ids = {g.game_id for g in posted}
        extra = len(full) - len(posted)
        more = (f"\n🌐 Pick **all {len(full)} games** ({extra} more) at {PICKEM_WEB_URL}") if extra > 0 else ""
        await channel.send(
            f"📋 **Pick'em — {date}**\n"
            "Tap a team, then stake **1–5 units**." + more
        )
        for g in posted:
            if await queries.pickem_game_exists(g.game_id, date):
                continue
            await self._post_game(channel, {
                "game_id": g.game_id, "sport": g.sport,
                "home_team": g.home_team, "away_team": g.away_team,
                "start_time": g.start_time_utc_iso,
            }, date)
        for g in full:
            if g.game_id not in posted_ids:
                await self._persist_web_game(g, date)

    async def _post_game(self, channel: discord.TextChannel, gd: dict, posted_date: str) -> None:
        probs = await _win_probs(gd["game_id"])
        if probs:
            gd["home_prob"], gd["away_prob"], gd["odds_source"] = probs
        try:
            msg = await channel.send(_game_message(gd))
            await msg.edit(view=GamePickView(str(msg.id), gd["away_team"], gd["home_team"]))
        except discord.HTTPException:
            log.exception("pickem: failed to post game %s", gd["game_id"])
            return
        await queries.add_pickem_game(
            str(msg.id), gd["game_id"], gd["sport"], gd["home_team"], gd["away_team"],
            gd["start_time"], posted_date,
            gd.get("home_prob"), gd.get("away_prob"), gd.get("odds_source"),
        )

    # ── Lock + resolve loops ─────────────────────────────────────────────────

    @tasks.loop(minutes=2)
    async def lock_loop(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        try:
            open_games = await queries.get_unlocked_pickem_games()
        except Exception:
            log.exception("pickem: lock loop query failed")
            return
        channel = await self._channel()
        for g in open_games:
            if g["start_time"] > now:
                continue
            await queries.lock_pickem_game(g["message_id"])
            if channel is not None and _is_posted(g["message_id"]):
                # Fold "who bet what" into the existing game message instead of posting a
                # new one — a fresh message notifies everyone with server alerts on (noisy).
                breakdown = await self._bet_breakdown(g)
                content = _game_message(g, locked=True)
                if breakdown:
                    content += "\n" + breakdown
                try:
                    await channel.get_partial_message(int(g["message_id"])).edit(
                        content=content, view=None,
                    )
                except discord.HTTPException:
                    pass

    async def _bet_breakdown(self, game: dict) -> str:
        """Who bet what, as lines folded into the locked game message (no new post = no
        ping). Win% already shows in the message header, so it's omitted here."""
        picks = await queries.get_pickem_picks_for_message(game["message_id"])
        if not picks:
            return ""
        away_list, home_list = [], []
        user_cache: dict[str, str] = {}
        for p in picks:
            uid = p["discord_user"]
            if uid not in user_cache:
                try:
                    user_cache[uid] = (await self.bot.fetch_user(int(uid))).display_name
                except Exception:
                    user_cache[uid] = f"Player {uid[:6]}"
            name = user_cache[uid]
            entry = f"{name} {p['stake']}u"
            (away_list if p["pick"] == "away" else home_list).append(entry)
        return (
            f"{AWAY_EMOJI} **{game['away_team']}**: " + (", ".join(away_list) or "—") + "\n"
            f"{HOME_EMOJI} **{game['home_team']}**: " + (", ".join(home_list) or "—")
        )

    @lock_loop.before_loop
    async def _before_lock(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=15)
    async def resolve_loop(self) -> None:
        try:
            pending = await queries.get_unresolved_pickem_games()
        except Exception:
            log.exception("pickem: resolve loop query failed")
            return
        channel = await self._channel()
        for g in pending:
            scores = await queries.get_game_scores(g["game_id"])
            if scores is None:
                continue
            home_score, away_score = scores
            winner = _winner_from_scores(home_score, away_score)
            await queries.resolve_pickem_game(g["message_id"], winner)
            if channel is not None and _is_posted(g["message_id"]):
                g["winner"] = winner
                result = await self._resolved_content(g, home_score, away_score)
                try:
                    await channel.get_partial_message(int(g["message_id"])).edit(
                        content=_game_message(g, result=result), view=None,
                    )
                except discord.HTTPException:
                    pass

    async def _resolved_content(self, game: dict, home_score: int, away_score: int) -> str:
        """Outcome recap: who called it right (with units won) and who missed."""
        winner = game["winner"]
        winner_team = game["home_team"] if winner == "home" else game["away_team"]
        picks = await queries.get_pickem_picks_for_message(game["message_id"])
        correct, wrong = [], []
        for p in picks:
            try:
                name = (await self.bot.fetch_user(int(p["discord_user"]))).display_name
            except Exception:
                name = f"Player {p['discord_user'][:6]}"
            prob = pick_prob(p["pick"], game["home_prob"], game["away_prob"])
            if p["pick"] == winner:
                gain = potential_units(p["stake"], prob)
                correct.append(f"{name} {p['stake']}u (+{gain:.1f}u)")
            else:
                wrong.append(f"{name} {p['stake']}u (−{p['stake']}u)")
        lines = [f"✅ **{winner_team}** won {home_score}–{away_score}"]
        if not correct and not wrong:
            lines.append("No bets were placed.")
        else:
            if correct:
                lines.append("🟢 Called it: " + ", ".join(correct))
            if wrong:
                lines.append("🔴 Missed: " + ", ".join(wrong))
        return "\n".join(lines)

    @resolve_loop.before_loop
    async def _before_resolve(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=3)
    async def favorites_loop(self) -> None:
        """Auto-place a pick on any unlocked game a user's favorite team is playing,
        at the user's stake. Idempotent (record_pickem_pick won't overwrite a manual
        pick), so it also fills favorites set after the slate posted."""
        try:
            favs = await queries.get_all_pickem_favorites()
            if not favs:
                return
            now = datetime.now(timezone.utc).isoformat()
            games = [g for g in await queries.get_unlocked_pickem_games() if g["start_time"] > now]
        except Exception:
            log.exception("pickem: favorites loop query failed")
            return
        for g in games:
            for uid, pref in favs.items():
                side = _fav_side(pref["teams"], g["home_team"], g["away_team"])
                if side is None:
                    continue
                try:
                    await queries.record_pickem_pick(g["message_id"], uid, side, pref["stake"])
                except Exception:
                    log.debug("pickem: auto-pick failed for %s on %s", uid, g["message_id"])

    @favorites_loop.before_loop
    async def _before_favorites(self) -> None:
        await self.bot.wait_until_ready()

    # ── Week winner callout ──────────────────────────────────────────────────

    @tasks.loop(time=WEEK_WINNER_TIME)
    async def week_winner_loop(self) -> None:
        """On Monday morning ET, celebrate last week's top pick'em player. Deduped
        via a bot_setting so it posts at most once per completed week."""
        if datetime.now(ET).weekday() != 0:  # Mondays only
            return
        try:
            await self._post_week_winner()
        except Exception:
            log.exception("pickem: week-winner callout failed")

    @week_winner_loop.before_loop
    async def _before_week_winner(self) -> None:
        await self.bot.wait_until_ready()

    async def _post_week_winner(self) -> None:
        # "Last completed week" = the week containing 7 days ago.
        last_week = datetime.now(timezone.utc) - timedelta(days=7)
        label = _iso_week_label(last_week)
        if await queries.get_bot_setting(_WEEK_WINNER_SETTING) == label:
            return  # already announced this week
        s_utc, e_utc = _week_bounds(last_week)
        rows = await queries.get_pickem_resolved_picks()
        standings = compute_pickem_standings(_filter_week(rows, s_utc, e_utc))
        channel = await self._channel()
        if channel is None:
            log.warning("pickem: week-winner channel not found")
            return  # retry next run; don't mark done
        if not standings:
            await queries.set_bot_setting(_WEEK_WINNER_SETTING, label)  # nothing to celebrate
            return
        # Winner: most units, then streak points, then raw correct.
        ranked = sorted(
            standings.items(),
            key=lambda kv: (kv[1]["units"], kv[1]["points"], kv[1]["correct"]),
            reverse=True,
        )
        uid, s = ranked[0]
        try:
            name = (await self.bot.fetch_user(int(uid))).display_name
        except Exception:
            name = f"Player {uid[:6]}"
        w, losses = s["correct"], s["total"] - s["correct"]
        week_num = label.split("W")[-1].lstrip("0") or "0"
        embed = discord.Embed(
            title=f"🏆 Week {week_num} Pick'em Winner",
            description=(
                f"👑 **{name}** ran the table last week!\n"
                f"**{w}-{losses}** · **{s['units']:+.1f}u** · {s['points']} streak pts"
            ),
            colour=0xF1C40F,
        )
        if len(ranked) > 1:
            others = []
            medals = {1: "🥈", 2: "🥉"}
            for i, (ouid, os_) in enumerate(ranked[1:3], 1):
                try:
                    onm = (await self.bot.fetch_user(int(ouid))).display_name
                except Exception:
                    onm = f"Player {ouid[:6]}"
                others.append(
                    f"{medals.get(i, f'#{i+1}')} **{onm}** — "
                    f"{os_['units']:+.1f}u ({os_['correct']}-{os_['total'] - os_['correct']})"
                )
            embed.add_field(name="Runners-up", value="\n".join(others), inline=False)
        embed.set_footer(text="Fresh week, fresh slate — /pickem leaderboard to chase the crown.")
        await channel.send(embed=embed)
        await queries.set_bot_setting(_WEEK_WINNER_SETTING, label)

    # ── Commands ─────────────────────────────────────────────────────────────

    @favorites.command(name="add", description="Add a favorite team — auto-picks it in every pick'em game")
    @app_commands.describe(team="Team name (e.g. Dodgers, Lakers, Eagles)")
    async def fav_add(self, interaction: discord.Interaction, team: str) -> None:
        team = team.strip()
        if len(team) < MIN_FAV_LEN:
            await interaction.response.send_message(
                f"Team name must be at least {MIN_FAV_LEN} characters.", ephemeral=True)
            return
        await queries.add_pickem_favorite(str(interaction.user.id), team)
        stake = await queries.get_pickem_fav_stake(str(interaction.user.id))
        await interaction.response.send_message(
            f"⭐ Added **{team}** to your favorites. I'll auto-pick them at **{stake}u** in every "
            f"pick'em game (within a few minutes of each slate). `/pickem favorites stake` to change the stake.",
            ephemeral=True,
        )

    @favorites.command(name="remove", description="Remove a favorite team")
    @app_commands.describe(team="Team name to remove")
    async def fav_remove(self, interaction: discord.Interaction, team: str) -> None:
        removed = await queries.remove_pickem_favorite(str(interaction.user.id), team)
        msg = f"Removed **{team.strip()}** from your favorites." if removed else \
            f"**{team.strip()}** wasn't in your favorites."
        await interaction.response.send_message(msg, ephemeral=True)

    @favorites.command(name="list", description="Show your favorite teams")
    async def fav_list(self, interaction: discord.Interaction) -> None:
        favs = await queries.get_pickem_favorites(str(interaction.user.id))
        stake = await queries.get_pickem_fav_stake(str(interaction.user.id))
        if not favs:
            await interaction.response.send_message(
                "You have no favorite teams. Add one with `/pickem favorites add`.", ephemeral=True)
            return
        await interaction.response.send_message(
            "⭐ **Your favorites** (auto-picked at " + f"{stake}u each):\n"
            + "\n".join(f"• {t.title()}" for t in favs),
            ephemeral=True,
        )

    @favorites.command(name="stake", description="Set how many units to auto-stake on your favorites (1–5)")
    @app_commands.describe(units="Units per auto-pick (1–5)")
    async def fav_stake(self, interaction: discord.Interaction, units: int) -> None:
        if not (1 <= units <= 5):
            await interaction.response.send_message("Stake must be 1–5 units.", ephemeral=True)
            return
        await queries.set_pickem_fav_stake(str(interaction.user.id), units)
        await interaction.response.send_message(
            f"Favorites auto-stake set to **{units}u**.", ephemeral=True)

    @pickem.command(name="leaderboard", description="Pick'em prediction leaderboard")
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        rows = await queries.get_pickem_resolved_picks()
        standings = compute_pickem_standings(rows)
        if not standings:
            await interaction.followup.send("No pick'em games have been scored yet!")
            return
        now = datetime.now(timezone.utc)
        s_utc, e_utc = _week_bounds(now)
        weekly = compute_pickem_standings(_filter_week(rows, s_utc, e_utc))
        view = LeaderboardView(self.bot, standings, weekly, _iso_week_label(now))
        await interaction.followup.send(embed=await view.embed(), view=view)
        view.message = await interaction.original_response()

    @pickem.command(name="today", description="Your pick'em card: today's scores, your picks, and payout")
    @app_commands.describe(user="Whose card to show (defaults to you)")
    async def today(
        self, interaction: discord.Interaction, user: discord.Member | None = None,
    ) -> None:
        await interaction.response.defer()
        target = user or interaction.user
        today = datetime.now(ET).date().isoformat()
        games = await queries.get_pickem_games_for_date(today)
        # Fall back to yesterday if today's card hasn't been posted yet.
        date_label = today
        if not games:
            yesterday = (datetime.now(ET).date() - timedelta(days=1)).isoformat()
            games = await queries.get_pickem_games_for_date(yesterday)
            if not games:
                await interaction.followup.send(f"📋 No pick'em games were posted today ({today}).")
                return
            date_label = yesterday
        games.sort(key=lambda g: g["start_time"])

        all_picks = await queries.get_pickem_picks_for_date(date_label)
        mine_by_msg = {p["message_id"]: p for p in all_picks if p["discord_user"] == str(target.id)}
        score_map = await _live_score_map(games)
        now_iso = datetime.now(timezone.utc).isoformat()

        # Final scores from the DB backfill the score line for resolved games when
        # the live scoreboard didn't return them (e.g. ESPN dropped an old game).
        lines, settled_net, w, l, live_n, risk, pot = [], 0.0, 0, 0, 0, 0.0, 0.0
        for g in games:
            mp = mine_by_msg.get(g["message_id"])
            score = score_map.get(_pair_key(g["home_team"], g["away_team"]))
            if score is None and g["resolved"]:
                db = await queries.get_game_scores(g["game_id"])
                if db is not None:
                    score = {"hs": db[0], "as_": db[1], "state": "final", "detail": "Final"}
            entry = _format_game(g, mp, score, now_iso)
            lines.append(entry["line"])
            if entry["settled_delta"] is not None:  # implies the user picked this game
                settled_net += entry["settled_delta"]
                w, l = (w + 1, l) if mp.get("correct") else (w, l + 1)
            elif mp:
                live_n += 1
                risk += entry["live_risk"]
                pot += entry["live_pot"]

        embed = discord.Embed(title=f"📋 {target.display_name}'s Pick'em — {date_label}", colour=COLOUR)

        if mine_by_msg:
            picked = len(mine_by_msg)
            trend = "🟢" if settled_net > 0 else ("🔴" if settled_net < 0 else "⚪")
            summary = [f"**Picked {picked}/{len(games)}** today"]
            if w or l:
                summary.append(f"**Settled** {w}-{l} · {trend} **{settled_net:+.1f}u**")
            if live_n:
                summary.append(f"**Pending** {live_n} · risking {risk:.0f}u to win **+{pot:.1f}u**")
            embed.description = "\n".join(summary)
        else:
            who = "You haven't" if target == interaction.user else f"{target.display_name} hasn't"
            embed.description = f"_{who} picked any games today — here's the slate._"

        # Game grid, chunked to respect Discord's 1024-char field cap.
        chunk, size = [], 0
        first = True
        for ln in lines:
            if size + len(ln) + 1 > 1024:
                embed.add_field(name="Games" if first else "​", value="\n".join(chunk), inline=False)
                chunk, size, first = [], 0, False
            chunk.append(ln)
            size += len(ln) + 1
        if chunk:
            embed.add_field(name="Games" if first else "​", value="\n".join(chunk), inline=False)

        board = _today_board(all_picks)
        if len(board) > 1:
            ranked = sorted(board.items(), key=lambda kv: kv[1]["net"], reverse=True)
            medals = {0: "🥇", 1: "🥈", 2: "🥉"}
            blines = []
            for i, (uid, b) in enumerate(ranked[:5]):
                try:
                    nm = (await self.bot.fetch_user(int(uid))).display_name
                except Exception:
                    nm = f"Player {uid[:6]}"
                mark = " ⬅️" if uid == str(target.id) else ""
                blines.append(f"{medals.get(i, f'`#{i+1}`')} **{nm}** {b['net']:+.1f}u ({b['w']}-{b['l']}){mark}")
            embed.add_field(name="Today's board (settled)", value="\n".join(blines), inline=False)

        embed.set_footer(text="🔵 live · 🟢/🔴 your side ahead/behind · payouts at market win%")
        await interaction.followup.send(embed=embed)

    @pickem.command(name="channel", description="Set the pick'em channel")
    @app_commands.describe(channel="Where the daily pick'em is posted")
    async def channel_cmd(
        self, interaction: discord.Interaction, channel: discord.TextChannel,
    ) -> None:
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "You need **Manage Server** to set the pick'em channel.", ephemeral=True,
            )
            return
        await queries.set_bot_setting(_PICKEM_CHANNEL_SETTING, str(channel.id))
        await interaction.response.send_message(
            f"✅ Daily pick'em will post in {channel.mention}.", ephemeral=True,
        )

    @pickem.command(name="post", description="Post today's (or a specific date's) pick'em now (admin)")
    @app_commands.describe(date="Date to post in YYYY-MM-DD format (default: today)")
    async def post_now(self, interaction: discord.Interaction, date: str | None = None) -> None:
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "You need **Manage Server** to trigger a post.", ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        channel = await self._channel()
        if channel is None:
            await interaction.followup.send("Pick'em channel not found.", ephemeral=True)
            return
        if date:
            try:
                datetime.strptime(date, "%Y-%m-%d")
            except ValueError:
                await interaction.followup.send("Invalid date format — use YYYY-MM-DD.", ephemeral=True)
                return
            await self._post_for_date(channel, date)
        else:
            await self._post_recap(channel)
            await self._post_today(channel)
        await interaction.followup.send(f"Posted in {channel.mention}.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PickemCog(bot))
