"""Casino cog — 1v1 /rps (Rock Paper Scissors) duel game."""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.cogs._elo_helpers import update_elo_1v1
from bot.cogs._minigames import RPSLogic
from bot.cogs._pool import compute_side_pot_payouts
from db import queries
import logging

log = logging.getLogger(__name__)
# ── Constants (derived from shared RPSLogic) ─────────────────────────────────

CHOICES = RPSLogic.CHOICES
CHOICE_EMOJI = RPSLogic.EMOJI
MATCH_TIMEOUT = 180  # seconds


# ── Helpers (delegate to shared RPSLogic) ────────────────────────────────────


def _rps_winner(a: str, b: str) -> str:
    """Return 'a', 'b', or 'tie'."""
    return RPSLogic.winner(a, b)


# ── Dataclass ────────────────────────────────────────────────────────────────


@dataclass
class RPSGame:
    channel_id: int
    challenger_id: int
    opponent_id: int
    challenger_name: str
    opponent_name: str
    bet: int
    phase: str = "pending"  # pending | playing | finished
    best_of: int = 3
    round_num: int = 1
    challenger_score: int = 0
    opponent_score: int = 0
    challenger_choice: str | None = None
    opponent_choice: str | None = None
    round_history: list[tuple[str, str, str]] = field(default_factory=list)
    vs_bot: bool = False
    message: discord.Message | None = None

    @property
    def wins_needed(self) -> int:
        return self.best_of // 2 + 1


# ── Embed functions ─────────────────────────────────────────────────────────


def _pending_embed(game: RPSGame) -> discord.Embed:
    bo_label = f"Best of {game.best_of}"
    embed = discord.Embed(
        title="\u270a\u270b\u270c\ufe0f Rock Paper Scissors — Challenge!",
        description=(
            f"**{game.challenger_name}** challenges **{game.opponent_name}** "
            f"to Rock Paper Scissors for **{game.bet}c**!\n\n"
            f"Format: **{bo_label}** (first to {game.wins_needed})\n"
            f"Pot: **{game.bet * 2}c**"
        ),
        colour=discord.Colour.blurple(),
    )
    embed.set_footer(text=f"Waiting for {game.opponent_name} to accept or decline")
    return embed


def _playing_embed(game: RPSGame) -> discord.Embed:
    c_check = "\u2705" if game.challenger_choice else "\u2b1c"
    o_check = "\u2705" if game.opponent_choice else "\u2b1c"

    desc = (
        f"**{game.challenger_name}** vs **{game.opponent_name}**\n"
        f"Score: **{game.challenger_score}** - **{game.opponent_score}** "
        f"(first to {game.wins_needed})\n\n"
        f"**Round {game.round_num}**\n"
        f"{c_check} {game.challenger_name}\n"
        f"{o_check} {game.opponent_name}\n"
    )

    if game.round_history:
        history_lines = []
        for i, (cc, oc, result) in enumerate(game.round_history, 1):
            ce = CHOICE_EMOJI[cc]
            oe = CHOICE_EMOJI[oc]
            history_lines.append(f"R{i}: {ce} vs {oe} — {result}")
        desc += "\n" + "\n".join(history_lines)

    embed = discord.Embed(
        title="\u270a\u270b\u270c\ufe0f Rock Paper Scissors",
        description=desc,
        colour=discord.Colour.orange(),
    )
    return embed


def _result_embed(game: RPSGame, payouts: dict) -> discord.Embed:
    if game.challenger_score > game.opponent_score:
        winner_name = game.challenger_name
        winner_id = game.challenger_id
        loser_name = game.opponent_name
        loser_id = game.opponent_id
    else:
        winner_name = game.opponent_name
        winner_id = game.opponent_id
        loser_name = game.challenger_name
        loser_id = game.challenger_id

    history_lines = []
    for i, (cc, oc, result) in enumerate(game.round_history, 1):
        ce = CHOICE_EMOJI[cc]
        oe = CHOICE_EMOJI[oc]
        history_lines.append(f"R{i}: {ce} vs {oe} — {result}")

    winner_payout = payouts.get(winner_id, 0)

    desc = (
        f"**{game.challenger_name}** vs **{game.opponent_name}**\n"
        f"Final Score: **{game.challenger_score}** - **{game.opponent_score}**\n\n"
        + "\n".join(history_lines)
        + f"\n\n\U0001f3c6 **{winner_name}** wins **{winner_payout}c**!"
    )

    embed = discord.Embed(
        title="\u270a\u270b\u270c\ufe0f Rock Paper Scissors — Result",
        description=desc,
        colour=discord.Colour.gold(),
    )
    embed.add_field(
        name="Results",
        value=(
            f"\U0001f3c6 **{winner_name}** — {game.bet}c \u2192 {winner_payout}c "
            f"(**+{winner_payout - game.bet}c**)\n"
            f"\u274c **{loser_name}** — {game.bet}c \u2192 0c "
            f"(**-{game.bet}c**)"
        ),
        inline=False,
    )
    return embed


# ── View ─────────────────────────────────────────────────────────────────────


class RPSView(ui.View):
    def __init__(self, game: RPSGame, active_tables: dict[int, RPSGame]) -> None:
        super().__init__(timeout=MATCH_TIMEOUT)
        self.game = game
        self.active_tables = active_tables
        self._update_buttons()

    # ── Choice buttons (row=1) ───────────────────────────────────────────

    @ui.button(label="Rock \u270a", style=discord.ButtonStyle.secondary, row=1)
    async def rock_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._handle_choice(interaction, "rock")

    @ui.button(label="Paper \u270b", style=discord.ButtonStyle.secondary, row=1)
    async def paper_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._handle_choice(interaction, "paper")

    @ui.button(label="Scissors \u270c\ufe0f", style=discord.ButtonStyle.secondary, row=1)
    async def scissors_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._handle_choice(interaction, "scissors")

    async def _handle_choice(self, interaction: discord.Interaction, choice: str) -> None:
        game = self.game
        uid = interaction.user.id

        if game.phase != "playing":
            await interaction.response.send_message("Game isn't active!", ephemeral=True)
            return

        if uid == game.challenger_id:
            if game.challenger_choice is not None:
                await interaction.response.send_message("You already chose!", ephemeral=True)
                return
            game.challenger_choice = choice
        elif uid == game.opponent_id:
            if game.opponent_choice is not None:
                await interaction.response.send_message("You already chose!", ephemeral=True)
                return
            game.opponent_choice = choice
        else:
            await interaction.response.send_message("You're not in this game!", ephemeral=True)
            return

        # vs bot: auto-pick for bot when human chooses
        if game.vs_bot and uid == game.challenger_id:
            game.opponent_choice = random.choice(CHOICES)

        # Both chosen?
        if game.challenger_choice is not None and game.opponent_choice is not None:
            await self._resolve_round(interaction)
        else:
            await interaction.response.send_message(
                f"You chose **{choice}** {CHOICE_EMOJI[choice]}! Waiting for opponent...",
                ephemeral=True,
            )
            await interaction.message.edit(embed=_playing_embed(game), view=self)

    def _resolve_round_logic(self) -> None:
        """Resolve current round, updating game state. No I/O."""
        game = self.game
        cc = game.challenger_choice
        oc = game.opponent_choice
        result_code = _rps_winner(cc, oc)

        if result_code == "a":
            game.challenger_score += 1
            result_str = f"{game.challenger_name} wins"
        elif result_code == "b":
            game.opponent_score += 1
            result_str = f"{game.opponent_name} wins"
        else:
            result_str = "Tie"

        game.round_history.append((cc, oc, result_str))
        game.challenger_choice = None
        game.opponent_choice = None

        if result_code == "tie":
            # Don't increment round_num on tie — replay
            return

        game.round_num += 1

    async def _resolve_round(self, interaction: discord.Interaction) -> None:
        game = self.game
        self._resolve_round_logic()

        # Check if someone won
        if game.challenger_score >= game.wins_needed or game.opponent_score >= game.wins_needed:
            await self._finish_game(interaction)
        else:
            self._update_buttons()
            await interaction.response.edit_message(embed=_playing_embed(game), view=self)

    async def _finish_game(self, interaction: discord.Interaction) -> None:
        game = self.game
        game.phase = "finished"

        if game.challenger_score > game.opponent_score:
            winner_id = game.challenger_id
        else:
            winner_id = game.opponent_id

        if game.vs_bot:
            # Bot games: simple payout, no side pot
            if winner_id == game.challenger_id:
                payout_amount = game.bet * 2
                await queries.update_casino_balance(str(game.challenger_id), payout_amount)
                payouts = {game.challenger_id: payout_amount, game.opponent_id: 0}
            else:
                payouts = {game.challenger_id: 0, game.opponent_id: game.bet * 2}

            # Log result for human only
            await queries.log_casino_result(
                str(game.challenger_id), "rps", game.bet,
                payouts.get(game.challenger_id, 0),
            )
        else:
            bets = {game.challenger_id: game.bet, game.opponent_id: game.bet}
            payouts = compute_side_pot_payouts(bets, [winner_id])

            for uid, payout in payouts.items():
                if payout > 0:
                    await queries.update_casino_balance(str(uid), payout)

            for uid in (game.challenger_id, game.opponent_id):
                await queries.log_casino_result(
                    str(uid), "rps", game.bet, payouts.get(uid, 0),
                )

        # ELO update (human vs human only)
        if not game.vs_bot:
            loser_id = game.opponent_id if winner_id == game.challenger_id else game.challenger_id
            try:
                await update_elo_1v1(str(winner_id), str(loser_id), "rps", "rps")
            except Exception:
                log.exception("Unhandled error in rps.py")

        embed = _result_embed(game, payouts)
        self._update_buttons()
        await interaction.response.edit_message(embed=embed, view=self)
        self.active_tables.pop(game.channel_id, None)

    # ── Accept / Decline (row=0) ─────────────────────────────────────────

    @ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="\u2705", row=0)
    async def accept_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.game.opponent_id:
            await interaction.response.send_message("Not your challenge!", ephemeral=True)
            return
        if self.game.phase != "pending":
            await interaction.response.send_message("Already started!", ephemeral=True)
            return

        try:
            await queries.update_casino_balance(str(self.game.opponent_id), -self.game.bet)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(self.game.opponent_id))
            await interaction.response.send_message(
                f"Not enough coins! (have {bal}c)", ephemeral=True,
            )
            return

        self.game.phase = "playing"
        self._update_buttons()
        await interaction.response.edit_message(embed=_playing_embed(self.game), view=self)

    @ui.button(label="Decline", style=discord.ButtonStyle.danger, emoji="\u274c", row=0)
    async def decline_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.game.opponent_id:
            await interaction.response.send_message("Not your challenge!", ephemeral=True)
            return
        if self.game.phase != "pending":
            await interaction.response.send_message("Already started!", ephemeral=True)
            return

        await queries.update_casino_balance(str(self.game.challenger_id), self.game.bet)
        embed = discord.Embed(
            title="\u270a\u270b\u270c\ufe0f Rock Paper Scissors — Cancelled",
            description=f"{self.game.opponent_name} declined the challenge.",
            colour=discord.Colour.dark_grey(),
        )
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_tables.pop(self.game.channel_id, None)
        await interaction.response.edit_message(embed=embed, view=self)

    # ── Rematch (row=2) ──────────────────────────────────────────────────

    @ui.button(label="Rematch", style=discord.ButtonStyle.primary, emoji="\U0001f504", row=2)
    async def rematch_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        game = self.game
        if game.phase != "finished":
            await interaction.response.send_message("Game isn't over yet!", ephemeral=True)
            return
        clicker = interaction.user.id
        if clicker not in (game.challenger_id, game.opponent_id):
            await interaction.response.send_message("You weren't in this game!", ephemeral=True)
            return

        other = game.opponent_id if clicker == game.challenger_id else game.challenger_id
        other_name = game.opponent_name if clicker == game.challenger_id else game.challenger_name
        clicker_name = game.challenger_name if clicker == game.challenger_id else game.opponent_name

        # Deduct clicker's coins
        try:
            await queries.update_casino_balance(str(clicker), -game.bet)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(clicker))
            await interaction.response.send_message(
                f"Not enough coins! (have {bal}c)", ephemeral=True,
            )
            return

        # Disable old view
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()

        new_game = RPSGame(
            channel_id=game.channel_id,
            challenger_id=clicker,
            opponent_id=other,
            challenger_name=clicker_name,
            opponent_name=other_name,
            bet=game.bet,
            best_of=game.best_of,
            vs_bot=game.vs_bot,
        )

        if game.vs_bot:
            new_game.phase = "playing"

        self.active_tables[game.channel_id] = new_game
        new_view = RPSView(new_game, self.active_tables)

        if game.vs_bot:
            embed = _playing_embed(new_game)
        else:
            embed = _pending_embed(new_game)

        await interaction.response.edit_message(view=self)
        msg = await interaction.followup.send(
            content=f"<@{other}>", embed=embed, view=new_view,
        )
        new_game.message = msg

    # ── Button state management ──────────────────────────────────────────

    def _update_buttons(self) -> None:
        pending = self.game.phase == "pending"
        playing = self.game.phase == "playing"
        finished = self.game.phase == "finished"

        # Accept / Decline (row=0)
        self.accept_btn.disabled = not pending
        self.decline_btn.disabled = not pending
        if not pending:
            self.accept_btn.row = None  # type: ignore[assignment]
            self.decline_btn.row = None  # type: ignore[assignment]
        else:
            self.accept_btn.row = 0
            self.decline_btn.row = 0

        # Choice buttons (row=1)
        self.rock_btn.disabled = not playing
        self.paper_btn.disabled = not playing
        self.scissors_btn.disabled = not playing

        # Rematch (row=2)
        self.rematch_btn.disabled = not finished
        if not finished:
            self.rematch_btn.row = None  # type: ignore[assignment]
        else:
            self.rematch_btn.row = 2

    # ── Timeout ──────────────────────────────────────────────────────────

    async def on_timeout(self) -> None:
        game = self.game

        if game.phase == "pending":
            try:
                await queries.update_casino_balance(str(game.challenger_id), game.bet)
            except Exception:
                log.exception("Unhandled error in rps.py")
            self.active_tables.pop(game.channel_id, None)
            if game.message:
                try:
                    embed = discord.Embed(
                        title="\u270a\u270b\u270c\ufe0f Rock Paper Scissors — Expired",
                        description=f"Challenge expired. {game.challenger_name}'s coins refunded.",
                        colour=discord.Colour.dark_grey(),
                    )
                    await game.message.edit(embed=embed, view=None)
                except Exception:
                    log.exception("Unhandled error in rps.py")
            return

        if game.phase == "playing":
            try:
                await queries.update_casino_balance(str(game.challenger_id), game.bet)
                if not game.vs_bot:
                    await queries.update_casino_balance(str(game.opponent_id), game.bet)
            except Exception:
                log.exception("Unhandled error in rps.py")

        self.active_tables.pop(game.channel_id, None)
        if game.message:
            try:
                embed = discord.Embed(
                    title="\u270a\u270b\u270c\ufe0f Rock Paper Scissors — Timed Out",
                    description="Game timed out. All bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await game.message.edit(embed=embed, view=None)
            except Exception:
                log.exception("Unhandled error in rps.py")


# ── Cog ──────────────────────────────────────────────────────────────────────


class RPSCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, RPSGame] = {}

    async def rps(
        self,
        interaction: discord.Interaction,
        opponent: discord.User | None = None,
        bet: int = 10,
        best_of: app_commands.Choice[int] | None = None,
    ) -> None:
        uid = interaction.user.id
        channel_id = interaction.channel_id

        if channel_id in self.active_tables:
            existing = self.active_tables[channel_id]
            _has_running = any(
                (t := getattr(existing, n, None)) is not None and not t.done()
                for n in ("game_task", "race_task", "sim_task", "round_task", "_round_task", "trade_task", "fly_task", "_shot_clock_task", "_countdown_task")
            )
            if _has_running:
                await interaction.response.send_message(
                    "There's already an RPS game in this channel!", ephemeral=True,
                )
                return
            del self.active_tables[channel_id]
        if bet < 1:
            await interaction.response.send_message("Bet must be at least 1c.", ephemeral=True)
            return
        if bet > 500:
            await interaction.response.send_message("Bet cannot exceed 500c.", ephemeral=True)
            return
        if opponent is not None and opponent.id == uid:
            await interaction.response.send_message("Can't challenge yourself!", ephemeral=True)
            return

        bo = best_of.value if best_of else 3

        # Deduct challenger's coins
        await queries.get_or_create_casino_wallet(str(uid))
        try:
            await queries.update_casino_balance(str(uid), -bet)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins! (have {bal}c)", ephemeral=True,
            )
            return

        vs_bot = opponent is None or opponent.id == self.bot.user.id

        if vs_bot:
            bot_user = self.bot.user
            game = RPSGame(
                channel_id=channel_id,
                challenger_id=uid,
                opponent_id=bot_user.id,
                challenger_name=interaction.user.display_name,
                opponent_name=bot_user.display_name,
                bet=bet,
                best_of=bo,
                vs_bot=True,
                phase="playing",
            )
            view = RPSView(game, self.active_tables)
            embed = _playing_embed(game)
            try:
                await interaction.response.send_message(embed=embed, view=view)
            except discord.NotFound:
                await queries.update_casino_balance(str(uid), bet)
                return  # interaction expired — don't leave a ghost table
            self.active_tables[channel_id] = game
            game.message = await interaction.original_response()
        else:
            await queries.get_or_create_casino_wallet(str(opponent.id))
            game = RPSGame(
                channel_id=channel_id,
                challenger_id=uid,
                opponent_id=opponent.id,
                challenger_name=interaction.user.display_name,
                opponent_name=opponent.display_name,
                bet=bet,
                best_of=bo,
            )
            view = RPSView(game, self.active_tables)
            embed = _pending_embed(game)
            try:
                await interaction.response.send_message(
                    content=f"<@{opponent.id}>", embed=embed, view=view,
                )
            except discord.NotFound:
                await queries.update_casino_balance(str(uid), bet)
                return  # interaction expired — don't leave a ghost table
            self.active_tables[channel_id] = game
            game.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RPSCog(bot))
