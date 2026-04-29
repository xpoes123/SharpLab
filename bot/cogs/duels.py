"""Casino cog — 1v1 /duel using random mini-games."""

import asyncio
import logging
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries
from bot.cogs._minigames import pick_games, MiniGame
from bot.cogs._elo_helpers import update_elo_1v1, update_elo_draw, fmt_elo_change, ELO_GAME_LABELS

log = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

MIN_WAGER = 50
MAX_WAGER = 2000
STARTING_DUEL_COINS = 1000
NUM_ROUNDS = 3
CHALLENGE_TIMEOUT = 60  # seconds to accept/decline
ROUND_PAUSE = 2  # seconds between rounds

COLOR_NEUTRAL = 0x2B2D31
COLOR_WIN = 0x57F287
COLOR_LOSS = 0xED4245

XP_PLAY = 20
XP_WIN_BONUS = 30


# ── Dataclass ────────────────────────────────────────────────────────────────


@dataclass
class DuelState:
    duel_id: int
    channel_id: int
    challenger_id: int
    opponent_id: int
    challenger_name: str
    opponent_name: str
    wager: int
    score_challenger: int = STARTING_DUEL_COINS
    score_opponent: int = STARTING_DUEL_COINS
    games: list[MiniGame] = field(default_factory=list)
    current_round: int = 0
    phase: str = "pending"  # pending | active | finished
    message: discord.Message | None = None


# ── Embed builders ───────────────────────────────────────────────────────────


def _challenge_embed(state: DuelState) -> discord.Embed:
    if state.wager > 0:
        desc = (
            f"**{state.challenger_name}** challenges **{state.opponent_name}** "
            f"for **{state.wager}c**!\n\n"
            f"<@{state.opponent_id}>, do you accept?"
        )
    else:
        desc = (
            f"**{state.challenger_name}** challenges **{state.opponent_name}** "
            f"to a ranked duel!\n\n"
            f"<@{state.opponent_id}>, do you accept?"
        )
    embed = discord.Embed(
        title="\u2694\ufe0f Duel Challenge!",
        description=desc,
        colour=COLOR_NEUTRAL,
    )
    embed.set_footer(text=f"Expires in {CHALLENGE_TIMEOUT}s")
    return embed


def _scoreboard_embed(state: DuelState, *, header: str | None = None) -> discord.Embed:
    game_list = "\n".join(
        f"{'**\u25b6**' if i == state.current_round else '\u2003'} "
        f"{'~~' if i < state.current_round else ''}"
        f"{g.emoji} {g.name} ({g.stakes} coins)"
        f"{'~~' if i < state.current_round else ''}"
        for i, g in enumerate(state.games)
    )

    desc = (
        f"**{state.challenger_name}**: {state.score_challenger} coins\n"
        f"**{state.opponent_name}**: {state.score_opponent} coins\n"
        f"\n**Games:**\n{game_list}"
    )
    if header:
        desc = f"{header}\n\n{desc}"

    embed = discord.Embed(
        title="\u2694\ufe0f Duel Scoreboard",
        description=desc,
        colour=COLOR_NEUTRAL,
    )
    return embed


def _round_header_embed(round_num: int, game: MiniGame) -> discord.Embed:
    return discord.Embed(
        title=f"Round {round_num}/{NUM_ROUNDS}: {game.emoji} {game.name}",
        description=f"Stakes: **{game.stakes}** duel coins",
        colour=COLOR_NEUTRAL,
    )


def _results_embed(
    state: DuelState,
    winner_id: int | None,
    winner_name: str | None,
    payout: int,
    balances: dict[int, int],
    elo_summary: str = "",
) -> discord.Embed:
    score_line = (
        f"**{state.challenger_name}** {state.score_challenger} \u2014 "
        f"{state.score_opponent} **{state.opponent_name}**"
    )
    if winner_id is None:
        if state.wager > 0:
            outcome = f"It's a tie! Both players get their **{state.wager}c** back."
        else:
            outcome = "It's a tie!"
        embed = discord.Embed(
            title="\u2694\ufe0f Duel Complete \u2014 Draw!",
            description=f"{score_line}\n\n{outcome}",
            colour=COLOR_NEUTRAL,
        )
    else:
        loser_name = (
            state.opponent_name if winner_id == state.challenger_id
            else state.challenger_name
        )
        if state.wager > 0:
            outcome = (
                f"\U0001f3c6 **{winner_name}** takes the pot: **{payout}c**!\n"
                f"{loser_name} walks away with nothing."
            )
        else:
            outcome = f"\U0001f3c6 **{winner_name}** wins!"
        embed = discord.Embed(
            title=f"\u2694\ufe0f Duel Complete \u2014 {winner_name} Wins!",
            description=f"{score_line}\n\n{outcome}",
            colour=COLOR_WIN,
        )

    if elo_summary:
        embed.add_field(name="ELO Updates", value=elo_summary, inline=False)

    if state.wager > 0:
        bal_text = (
            f"**{state.challenger_name}**: {balances.get(state.challenger_id, '?')}c | "
            f"**{state.opponent_name}**: {balances.get(state.opponent_id, '?')}c"
        )
        embed.add_field(name="Balances", value=bal_text, inline=False)
    return embed


# ── View ─────────────────────────────────────────────────────────────────────


class DuelView(ui.View):
    def __init__(self, state: DuelState, active_duels: dict[int, int]) -> None:
        super().__init__(timeout=CHALLENGE_TIMEOUT)
        self.state = state
        self.active_duels = active_duels

    # ── Accept / Decline ─────────────────────────────────────────────────

    @ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="\u2705")
    async def accept_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.state.opponent_id:
            await interaction.response.send_message(
                "Only the challenged player can accept.", ephemeral=True,
            )
            return
        if self.state.phase != "pending":
            await interaction.response.send_message(
                "This challenge is no longer pending.", ephemeral=True,
            )
            return

        # Deduct coins (skip for free duels)
        if self.state.wager > 0:
            try:
                await queries.update_casino_balance(
                    str(self.state.opponent_id), -self.state.wager,
                )
            except ValueError:
                bal = await queries.get_or_create_casino_wallet(
                    str(self.state.opponent_id),
                )
                await interaction.response.send_message(
                    f"You don't have enough coins! (have {bal}c, need {self.state.wager}c)",
                    ephemeral=True,
                )
                return

            try:
                await queries.update_casino_balance(
                    str(self.state.challenger_id), -self.state.wager,
                )
            except ValueError:
                # Refund opponent since challenger can't cover
                await queries.update_casino_balance(
                    str(self.state.opponent_id), self.state.wager,
                )
                bal = await queries.get_or_create_casino_wallet(
                    str(self.state.challenger_id),
                )
                for child in self.children:
                    child.disabled = True  # type: ignore[union-attr]
                self.stop()
                self.active_duels.pop(self.state.channel_id, None)
                embed = discord.Embed(
                    title="\u2694\ufe0f Duel Cancelled",
                    description=(
                        f"{self.state.challenger_name} no longer has enough coins "
                        f"({bal}c). Duel cancelled."
                    ),
                    colour=COLOR_LOSS,
                )
                await interaction.response.edit_message(embed=embed, view=self)
                await queries.update_duel(self.state.duel_id, status="expired")
                return

        # Start the duel
        self.state.phase = "active"
        await queries.update_duel(self.state.duel_id, status="active")

        # Disable buttons — game flow is driven by asyncio, not buttons
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()

        # Pick mini-games
        self.state.games = pick_games(NUM_ROUNDS)

        # Show initial scoreboard
        embed = _scoreboard_embed(
            self.state, header="\u2694\ufe0f **Duel Accepted!** Let the games begin!",
        )
        await interaction.response.edit_message(embed=embed, view=None)

        # Run the games
        await self._run_duel()

    @ui.button(label="Decline", style=discord.ButtonStyle.danger, emoji="\u274c")
    async def decline_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.state.opponent_id:
            await interaction.response.send_message(
                "Only the challenged player can decline.", ephemeral=True,
            )
            return
        if self.state.phase != "pending":
            await interaction.response.send_message(
                "This challenge is no longer pending.", ephemeral=True,
            )
            return

        self.state.phase = "finished"
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_duels.pop(self.state.channel_id, None)
        await queries.update_duel(self.state.duel_id, status="expired")

        embed = discord.Embed(
            title="\u2694\ufe0f Duel Declined",
            description=f"{self.state.opponent_name} declined the challenge.",
            colour=COLOR_NEUTRAL,
        )
        await interaction.response.edit_message(embed=embed, view=self)

    # ── Game loop ────────────────────────────────────────────────────────

    async def _run_duel(self) -> None:
        state = self.state
        message = state.message
        if message is None:
            return

        self._elo_changes: list[str] = []

        await asyncio.sleep(ROUND_PAUSE)

        for i, game in enumerate(state.games):
            state.current_round = i

            # Round header
            try:
                await message.edit(embed=_round_header_embed(i + 1, game))
            except discord.HTTPException:
                pass

            await asyncio.sleep(ROUND_PAUSE)

            # Play the mini-game
            try:
                winner_uid = await game.play(
                    message,
                    state.challenger_id,
                    state.challenger_name,
                    state.opponent_id,
                    state.opponent_name,
                )
            except Exception:
                log.exception("Mini-game %s failed, treating as draw", game.name)
                winner_uid = 0

            # Update duel coins
            if winner_uid == state.challenger_id:
                state.score_challenger += game.stakes
                state.score_opponent -= game.stakes
            elif winner_uid == state.opponent_id:
                state.score_opponent += game.stakes
                state.score_challenger -= game.stakes
            # else: tie, no change

            await queries.update_duel(
                state.duel_id,
                score_challenger=state.score_challenger,
                score_opponent=state.score_opponent,
            )

            # ELO update for skill games only
            if game.elo_key in ELO_GAME_LABELS:
                try:
                    if winner_uid == state.challenger_id:
                        w_old, w_new, l_old, l_new = await update_elo_1v1(
                            str(state.challenger_id), str(state.opponent_id),
                            game.elo_key, "duel",
                        )
                        self._elo_changes.append(
                            f"{game.emoji} {game.name}: "
                            f"**{state.challenger_name}** {fmt_elo_change(w_old, w_new)} | "
                            f"**{state.opponent_name}** {fmt_elo_change(l_old, l_new)}"
                        )
                    elif winner_uid == state.opponent_id:
                        w_old, w_new, l_old, l_new = await update_elo_1v1(
                            str(state.opponent_id), str(state.challenger_id),
                            game.elo_key, "duel",
                        )
                        self._elo_changes.append(
                            f"{game.emoji} {game.name}: "
                            f"**{state.opponent_name}** {fmt_elo_change(w_old, w_new)} | "
                            f"**{state.challenger_name}** {fmt_elo_change(l_old, l_new)}"
                        )
                    else:
                        p1_old, p1_new, p2_old, p2_new = await update_elo_draw(
                            str(state.challenger_id), str(state.opponent_id),
                            game.elo_key, "duel",
                        )
                        self._elo_changes.append(
                            f"{game.emoji} {game.name}: "
                            f"**{state.challenger_name}** {fmt_elo_change(p1_old, p1_new)} | "
                            f"**{state.opponent_name}** {fmt_elo_change(p2_old, p2_new)}"
                        )
                except Exception:
                    log.exception("ELO update failed for %s", game.elo_key)

            # Updated scoreboard
            await asyncio.sleep(1)
            try:
                await message.edit(embed=_scoreboard_embed(state))
            except discord.HTTPException:
                pass

            if i < len(state.games) - 1:
                await asyncio.sleep(ROUND_PAUSE)

        # All rounds done — resolve
        await self._resolve_duel()

    async def _resolve_duel(self) -> None:
        state = self.state
        state.phase = "finished"

        # Determine winner
        if state.score_challenger > state.score_opponent:
            winner_id = state.challenger_id
            winner_name = state.challenger_name
            loser_id = state.opponent_id
        elif state.score_opponent > state.score_challenger:
            winner_id = state.opponent_id
            winner_name = state.opponent_name
            loser_id = state.challenger_id
        else:
            winner_id = None
            winner_name = None
            loser_id = None

        pot = state.wager * 2
        payout = 0

        # Coin payouts (only for wagered duels)
        if state.wager > 0:
            if winner_id is not None:
                payout = pot
                await queries.update_casino_balance(str(winner_id), payout)
            else:
                payout = state.wager
                await queries.update_casino_balance(
                    str(state.challenger_id), state.wager,
                )
                await queries.update_casino_balance(
                    str(state.opponent_id), state.wager,
                )

        await queries.update_duel(
            state.duel_id, status="finished",
            **({"winner_id": str(winner_id)} if winner_id else {}),
        )

        # XP: 20 for playing, +30 bonus for winner
        await queries.add_xp(str(state.challenger_id), XP_PLAY)
        await queries.add_xp(str(state.opponent_id), XP_PLAY)
        if winner_id is not None:
            await queries.add_xp(str(winner_id), XP_WIN_BONUS)

        # Log casino results
        if winner_id is not None:
            await queries.log_casino_result(
                str(winner_id), "duel", state.wager, payout,
            )
            assert loser_id is not None
            await queries.log_casino_result(
                str(loser_id), "duel", state.wager, 0,
            )
        else:
            await queries.log_casino_result(
                str(state.challenger_id), "duel", state.wager, state.wager,
            )
            await queries.log_casino_result(
                str(state.opponent_id), "duel", state.wager, state.wager,
            )

        # Fetch final balances
        balances: dict[int, int] = {}
        if state.wager > 0:
            bal_c = await queries.get_or_create_casino_wallet(str(state.challenger_id))
            bal_o = await queries.get_or_create_casino_wallet(str(state.opponent_id))
            balances = {state.challenger_id: bal_c, state.opponent_id: bal_o}

        # Clean up
        self.active_duels.pop(state.channel_id, None)

        # ELO summary
        elo_summary = "\n".join(self._elo_changes) if self._elo_changes else ""

        # Final embed
        embed = _results_embed(
            state, winner_id, winner_name, payout, balances, elo_summary,
        )

        await asyncio.sleep(ROUND_PAUSE)
        if state.message:
            try:
                await state.message.edit(embed=embed)
            except discord.HTTPException:
                pass

    # ── Timeout ──────────────────────────────────────────────────────────

    async def on_timeout(self) -> None:
        state = self.state

        if state.phase == "pending":
            # No coins were taken yet — just expire
            self.active_duels.pop(state.channel_id, None)
            await queries.update_duel(state.duel_id, status="expired")

            if state.message:
                try:
                    embed = discord.Embed(
                        title="\u2694\ufe0f Duel Expired",
                        description=(
                            f"Challenge from {state.challenger_name} expired "
                            f"(no response in {CHALLENGE_TIMEOUT}s)."
                        ),
                        colour=COLOR_NEUTRAL,
                    )
                    await state.message.edit(embed=embed, view=None)
                except discord.HTTPException:
                    pass
            return

        if state.phase == "active":
            # Mid-game timeout (shouldn't happen normally) — refund both
            if state.wager > 0:
                try:
                    await queries.update_casino_balance(
                        str(state.challenger_id), state.wager,
                    )
                except Exception:
                    log.exception("Unhandled error in duels.py")
                try:
                    await queries.update_casino_balance(
                        str(state.opponent_id), state.wager,
                    )
                except Exception:
                    log.exception("Unhandled error in duels.py")

            self.active_duels.pop(state.channel_id, None)
            await queries.update_duel(state.duel_id, status="expired")

            if state.message:
                try:
                    embed = discord.Embed(
                        title="\u2694\ufe0f Duel Timed Out",
                        description="Game timed out. All bets refunded.",
                        colour=COLOR_NEUTRAL,
                    )
                    await state.message.edit(embed=embed, view=None)
                except discord.HTTPException:
                    pass


# ── Cog ──────────────────────────────────────────────────────────────────────


class DuelsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_duels: dict[int, int] = {}  # channel_id -> duel_id

    @app_commands.command(name="duel", description="Challenge someone to a duel")
    @app_commands.describe(
        opponent="Who to challenge",
        amount="Optional coin wager (0 = free ranked duel, 50-2000 for coins)",
    )
    async def duel(
        self,
        interaction: discord.Interaction,
        opponent: discord.Member,
        amount: int = 0,
    ) -> None:
        uid = interaction.user.id
        channel_id = interaction.channel_id

        # ── Validations ──────────────────────────────────────────────────

        if opponent.id == uid:
            await interaction.response.send_message(
                "You can't duel yourself!", ephemeral=True,
            )
            return

        if opponent.bot:
            await interaction.response.send_message(
                "You can't duel a bot!", ephemeral=True,
            )
            return

        if amount < 0:
            await interaction.response.send_message(
                "Wager can't be negative.", ephemeral=True,
            )
            return

        if amount > 0 and amount < MIN_WAGER:
            await interaction.response.send_message(
                f"Minimum wager is {MIN_WAGER}c (or 0 for a free duel).", ephemeral=True,
            )
            return

        if amount > MAX_WAGER:
            await interaction.response.send_message(
                f"Maximum wager is {MAX_WAGER}c.", ephemeral=True,
            )
            return

        # Check for existing duel in channel (both in-memory and DB)
        if channel_id in self.active_duels:
            await interaction.response.send_message(
                "There's already an active duel in this channel!",
                ephemeral=True,
            )
            return

        existing = await queries.get_active_duel_in_channel(str(channel_id))
        if existing is not None:
            await interaction.response.send_message(
                "There's already an active duel in this channel!",
                ephemeral=True,
            )
            return

        # Verify coins (only for wagered duels)
        if amount > 0:
            challenger_bal = await queries.get_or_create_casino_wallet(str(uid))
            if challenger_bal < amount:
                await interaction.response.send_message(
                    f"You don't have enough coins! (have {challenger_bal}c, need {amount}c)",
                    ephemeral=True,
                )
                return

            opponent_bal = await queries.get_or_create_casino_wallet(str(opponent.id))
            if opponent_bal < amount:
                await interaction.response.send_message(
                    f"{opponent.display_name} doesn't have enough coins! "
                    f"(they have {opponent_bal}c, need {amount}c)",
                    ephemeral=True,
                )
                return

        # ── Create duel ──────────────────────────────────────────────────

        duel_id = await queries.create_duel(
            channel_id=str(channel_id),
            challenger_id=str(uid),
            opponent_id=str(opponent.id),
            wager=amount,
        )

        state = DuelState(
            duel_id=duel_id,
            channel_id=channel_id,
            challenger_id=uid,
            opponent_id=opponent.id,
            challenger_name=interaction.user.display_name,
            opponent_name=opponent.display_name,
            wager=amount,
        )

        self.active_duels[channel_id] = duel_id
        view = DuelView(state, self.active_duels)

        embed = _challenge_embed(state)
        await interaction.response.send_message(
            content=f"<@{opponent.id}>",
            embed=embed,
            view=view,
        )
        state.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DuelsCog(bot))
