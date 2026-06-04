"""Live blunder alerts for Norway Chess — posts to Discord the moment a game swings
hard against the player who just moved.

Why this exists: Lichess broadcasts carry the live moves, but live games only have
[%clk] comments — engine [%eval] is added later (post-game server analysis). So to flag
blunders *as they happen* we compute the eval ourselves: poll the live broadcast round,
replay the moves to FENs (python-chess), and run a local Stockfish on each new position.
A swing of >= threshold pawns against the mover (who was not already lost) is a blunder.

Auto-finds the current live round of the configured Lichess tour via the `ongoing` flag,
so it idles cheaply between rounds and after the event. Config lives in bot_settings
(runtime-settable, no redeploy). The ping reuses the self-assign "Chess" role created by
chessnews.py (`chess_news_role`).
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os

import chess
import chess.engine
import chess.pgn
import discord
import httpx
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

from db import queries

log = logging.getLogger(__name__)
load_dotenv()

LICHESS = "https://lichess.org/api/broadcast"
# Norway Chess 2026 — Open section (Carlsen, Gukesh, Firouzja, Keymer, Pragg, So).
DEFAULT_TOUR = "kDQUxYbE"
DEFAULT_CHANNEL_ID = int(os.getenv("CHESS_BLUNDER_CHANNEL_ID") or 1510752551014760658)
DEFAULT_THRESHOLD = 3.0          # pawns of swing against the mover to count as a blunder
LOST_FLOOR = -3.0                # don't ping when the mover was already this lost
ENGINE_PATH = os.getenv("STOCKFISH_PATH") or "stockfish"
_EVAL_TIME = float(os.getenv("CHESS_BLUNDER_EVAL_TIME") or 0.4)  # seconds/position
_POLL_SECONDS = 30
_MAX_CATCHUP = 12                # cap evals/poll if we somehow fell far behind
_SEEN_CAP = 500
CHESS_COLOR = 0x769656

_CHANNEL_SETTING = "chess_blunder_channel"
_TOUR_SETTING = "chess_blunder_tour"
_THRESH_SETTING = "chess_blunder_threshold"
_SEEN_SETTING = "chess_blunder_seen"     # JSON capped list of posted "{gameId}:{ply}"
_ROLE_SETTING = "chess_news_role"        # reuse the self-assign Chess role


def score_to_pawns(score: chess.engine.PovScore) -> float:
    """White-POV evaluation in pawns; mate folded to a large finite number."""
    return score.white().score(mate_score=100000) / 100.0


def classify_blunder(white_moved: bool, before: float, after: float,
                     threshold: float, lost_floor: float = LOST_FLOOR) -> float | None:
    """Given White-POV evals before/after the move, return how many pawns the *mover*
    threw away if it qualifies as a blunder, else None. Skips piling on an already-lost
    position (mover was worse than `lost_floor` before moving)."""
    mover_before = before if white_moved else -before
    mover_after = after if white_moved else -after
    drop = mover_before - mover_after
    if drop >= threshold and mover_before > lost_floor:
        return drop
    return None


def parse_broadcast_pgn(pgn_text: str) -> list[dict]:
    """Parse a Lichess broadcast round PGN into per-game dicts with the move list replayed
    to FENs: {id, white, black, url, round, positions, sans}. `positions[k]` is the FEN
    after ply k (positions[0] = start); `sans[k-1]` is the SAN of ply k."""
    games: list[dict] = []
    buf = io.StringIO(pgn_text)
    while True:
        try:
            game = chess.pgn.read_game(buf)
        except Exception:
            log.debug("chess broadcast PGN parse error", exc_info=True)
            break
        if game is None:
            break
        h = game.headers
        url = h.get("GameURL") or h.get("BroadcastURL") or ""
        gid = url.rstrip("/").rsplit("/", 1)[-1] if url else f'{h.get("White")}-{h.get("Black")}'
        board = game.board()
        positions = [board.fen()]
        sans: list[str] = []
        for mv in game.mainline_moves():
            sans.append(board.san(mv))
            board.push(mv)
            positions.append(board.fen())
        if not sans:                # not-yet-started game (or junk text) — nothing to eval
            continue
        games.append({
            "id": gid,
            "white": h.get("White", "White"),
            "black": h.get("Black", "Black"),
            "url": url,
            "round": h.get("Round", ""),
            "positions": positions,
            "sans": sans,
        })
    return games


def fmt_eval(pawns: float) -> str:
    """Mover-POV eval string. Mate scores render as +M / −M instead of a huge number."""
    if abs(pawns) >= 100:
        return "+M" if pawns > 0 else "−M"
    return f"{pawns:+.1f}".replace("-", "−")


class ChessBlunderCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._engine: chess.engine.SimpleEngine | None = None
        self._engine_failed = False
        self._engine_lock = asyncio.Lock()
        self._round_id: str | None = None
        self._baseline: dict[str, int] = {}   # game_id -> last ply we've accounted for
        self.blunder_check.start()

    def cog_unload(self) -> None:
        self.blunder_check.cancel()
        if self._engine is not None:
            try:
                self._engine.quit()
            except Exception:
                pass

    # ── config ────────────────────────────────────────────────────────────────
    async def _channel_id(self) -> int:
        raw = await queries.get_bot_setting(_CHANNEL_SETTING)
        try:
            return int(raw) if raw else DEFAULT_CHANNEL_ID
        except ValueError:
            return DEFAULT_CHANNEL_ID

    async def _tour_id(self) -> str:
        return (await queries.get_bot_setting(_TOUR_SETTING)) or DEFAULT_TOUR

    async def _threshold(self) -> float:
        raw = await queries.get_bot_setting(_THRESH_SETTING)
        try:
            return float(raw) if raw else DEFAULT_THRESHOLD
        except ValueError:
            return DEFAULT_THRESHOLD

    async def _role_id(self) -> int | None:
        raw = await queries.get_bot_setting(_ROLE_SETTING)
        try:
            return int(raw) if raw else None
        except ValueError:
            return None

    # ── engine ────────────────────────────────────────────────────────────────
    async def _get_engine(self) -> chess.engine.SimpleEngine | None:
        if self._engine_failed:
            return None
        if self._engine is None:
            try:
                self._engine = await asyncio.to_thread(
                    chess.engine.SimpleEngine.popen_uci, ENGINE_PATH)
            except Exception:
                log.warning("Stockfish unavailable at %r; chess blunder alerts disabled",
                            ENGINE_PATH, exc_info=True)
                self._engine_failed = True
                return None
        return self._engine

    async def _eval_fen(self, fen: str) -> float:
        eng = await self._get_engine()
        if eng is None:
            raise RuntimeError("engine unavailable")
        board = chess.Board(fen)
        async with self._engine_lock:
            info = await asyncio.to_thread(eng.analyse, board,
                                           chess.engine.Limit(time=_EVAL_TIME))
        return score_to_pawns(info["score"])

    async def _detect(self, game: dict, from_ply: int, threshold: float) -> list[dict]:
        """Evaluate the new plies of a game and return the blunders among them."""
        positions, sans = game["positions"], game["sans"]
        n = len(positions) - 1
        lo = max(from_ply, n - _MAX_CATCHUP)
        evals = {ply: await self._eval_fen(positions[ply]) for ply in range(lo, n + 1)}
        out: list[dict] = []
        for p in range(lo + 1, n + 1):
            white_moved = (p % 2 == 1)
            drop = classify_blunder(white_moved, evals[p - 1], evals[p], threshold)
            if drop is not None:
                out.append({
                    "ply": p, "san": sans[p - 1], "white_moved": white_moved,
                    "before": evals[p - 1], "after": evals[p], "drop": drop,
                    "fen": positions[p],
                })
        return out

    def _embed(self, game: dict, bl: dict) -> discord.Embed:
        player = game["white"] if bl["white_moved"] else game["black"]
        mb = bl["before"] if bl["white_moved"] else -bl["before"]
        ma = bl["after"] if bl["white_moved"] else -bl["after"]
        movenum = (bl["ply"] + 1) // 2
        dots = "." if bl["white_moved"] else "..."
        e = discord.Embed(
            title=f"💥 Blunder — {player} played {movenum}{dots} {bl['san']}"[:256],
            url=game["url"] or None, color=CHESS_COLOR)
        swing = "into forced mate" if abs(ma) >= 100 else f"−{bl['drop']:.1f}"
        e.add_field(name="Eval swing",
                    value=f"`{fmt_eval(mb)} → {fmt_eval(ma)}`  ({swing})", inline=True)
        e.add_field(name="Game", value=f"{game['white']} vs {game['black']}", inline=True)
        board_only = bl["fen"].split(" ", 1)[0]
        orient = "white" if bl["white_moved"] else "black"
        e.set_image(url=f"https://backscattering.de/web-boardimage/board.png"
                        f"?fen={board_only}&orientation={orient}")
        rnd = f" · {game['round']}" if game["round"] else ""
        e.set_footer(text=f"♟️ Norway Chess{rnd} · Lichess")
        return e

    async def _live_round_id(self, client: httpx.AsyncClient, tour: str,
                             allow_default: bool = False) -> str | None:
        r = await client.get(f"{LICHESS}/{tour}", timeout=20.0)
        if r.status_code != 200:
            return None
        data = r.json()
        live = next((x for x in data.get("rounds", []) if x.get("ongoing")), None)
        if live:
            return live["id"]
        return data.get("defaultRoundId") if allow_default else None

    # ── poll loop ───────────────────────────────────────────────────────────────
    @tasks.loop(seconds=_POLL_SECONDS)
    async def blunder_check(self) -> None:
        try:
            if await self._get_engine() is None:
                return
            channel = self.bot.get_channel(await self._channel_id())
            if channel is None:
                return
            tour = await self._tour_id()
            async with httpx.AsyncClient(headers={"User-Agent": "SharpLab/1.0"}) as client:
                rid = await self._live_round_id(client, tour)
                if rid is None:
                    return
                pr = await client.get(f"{LICHESS}/round/{rid}.pgn", timeout=20.0)
                if pr.status_code != 200:
                    return
                pgn = pr.text

            if rid != self._round_id:           # new round → drop stale baselines
                self._round_id = rid
                self._baseline = {}

            games = parse_broadcast_pgn(pgn)
            threshold = await self._threshold()
            raw = await queries.get_bot_setting(_SEEN_SETTING)
            seen = json.loads(raw) if raw else []
            seen_set = set(seen)
            role_id = await self._role_id()
            content = f"<@&{role_id}>" if role_id else None
            posted = False

            for game in games:
                gid = game["id"]
                n = len(game["positions"]) - 1
                base = self._baseline.get(gid)
                if base is None:                # first sighting → seed silently
                    self._baseline[gid] = n
                    continue
                if n <= base:
                    continue
                blunders = await self._detect(game, base, threshold)
                self._baseline[gid] = n
                for bl in blunders:
                    key = f"{gid}:{bl['ply']}"
                    if key in seen_set:
                        continue
                    seen_set.add(key)
                    seen = [key] + seen
                    posted = True
                    await channel.send(content=content, embed=self._embed(game, bl),
                                       allowed_mentions=discord.AllowedMentions(roles=True))
            if posted:
                await queries.set_bot_setting(_SEEN_SETTING, json.dumps(seen[:_SEEN_CAP]))
        except Exception:
            log.exception("Unhandled error in chess blunder_check loop")

    @blunder_check.before_loop
    async def before_blunder_check(self) -> None:
        await self.bot.wait_until_ready()

    # ── admin commands ──────────────────────────────────────────────────────────
    group = app_commands.Group(
        name="chessblunder",
        description="Configure live Norway Chess blunder alerts",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @group.command(name="channel", description="Set the channel where blunder alerts post")
    async def channel_cmd(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await queries.set_bot_setting(_CHANNEL_SETTING, str(channel.id))
        await interaction.response.send_message(
            f"✅ Chess blunder alerts will post in {channel.mention}.", ephemeral=True)

    @group.command(name="threshold", description="Set the eval swing (pawns) that counts as a blunder")
    @app_commands.describe(pawns="Swing against the mover, in pawns (default 3.0)")
    async def threshold_cmd(self, interaction: discord.Interaction, pawns: float) -> None:
        if pawns <= 0:
            await interaction.response.send_message("Threshold must be positive.", ephemeral=True)
            return
        await queries.set_bot_setting(_THRESH_SETTING, str(pawns))
        await interaction.response.send_message(
            f"✅ Blunder threshold set to **{pawns:g}** pawns.", ephemeral=True)

    @group.command(name="tournament", description="Set the Lichess broadcast tour id to watch")
    @app_commands.describe(tour_id="Lichess broadcast tour id (e.g. kDQUxYbE for Norway Chess Open)")
    async def tournament_cmd(self, interaction: discord.Interaction, tour_id: str) -> None:
        await queries.set_bot_setting(_TOUR_SETTING, tour_id.strip())
        self._round_id = None
        self._baseline = {}
        await interaction.response.send_message(
            f"✅ Now watching Lichess tour `{tour_id.strip()}`.", ephemeral=True)

    @group.command(name="status", description="Show blunder-alert configuration and live state")
    async def status_cmd(self, interaction: discord.Interaction) -> None:
        cid = await self._channel_id()
        rid = await self._role_id()
        tour = await self._tour_id()
        thr = await self._threshold()
        eng = await self._get_engine()
        await interaction.response.send_message(
            f"**Chess blunder alerts**\n"
            f"Channel: <#{cid}>\n"
            f"Ping role: " + (f"<@&{rid}>" if rid else "*(none)*") + "\n"
            f"Tour: `{tour}` · threshold: **{thr:g}** pawns · checks every {_POLL_SECONDS}s\n"
            f"Engine: " + ("✅ Stockfish ready" if eng else "❌ Stockfish unavailable") + "\n"
            f"Live round: " + (f"`{self._round_id}`" if self._round_id else "*(none / idle)*"),
            ephemeral=True)

    @group.command(name="test", description="Evaluate the current live round now and report swings (no posting)")
    async def test_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if await self._get_engine() is None:
            await interaction.followup.send("❌ Stockfish isn't available on the host.", ephemeral=True)
            return
        tour = await self._tour_id()
        try:
            async with httpx.AsyncClient(headers={"User-Agent": "SharpLab/1.0"}) as client:
                rid = await self._live_round_id(client, tour, allow_default=True)
                if rid is None:
                    await interaction.followup.send("No round found for that tour.", ephemeral=True)
                    return
                pr = await client.get(f"{LICHESS}/round/{rid}.pgn", timeout=20.0)
                pgn = pr.text if pr.status_code == 200 else ""
        except Exception:
            await interaction.followup.send("Couldn't reach Lichess.", ephemeral=True)
            return
        games = parse_broadcast_pgn(pgn)
        if not games:
            await interaction.followup.send("Round has no games yet.", ephemeral=True)
            return
        threshold = await self._threshold()
        lines: list[str] = []
        for game in games:
            n = len(game["positions"]) - 1
            from_ply = max(0, n - 8)            # scan the last few plies only
            bls = await self._detect(game, from_ply, threshold)
            tag = f"**{game['white']} vs {game['black']}**"
            if bls:
                for bl in bls:
                    player = game["white"] if bl["white_moved"] else game["black"]
                    mn = (bl["ply"] + 1) // 2
                    lines.append(f"💥 {tag}: {player} {mn}. {bl['san']} (−{bl['drop']:.1f})")
            else:
                lines.append(f"• {tag}: no swing ≥{threshold:g} in last moves")
        await interaction.followup.send(
            f"Live round `{rid}`, threshold {threshold:g}:\n" + "\n".join(lines)[:1900],
            ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ChessBlunderCog(bot))
