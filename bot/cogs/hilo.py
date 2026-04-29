"""Casino cog — multiplayer /hilo card game."""

import random
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries

# ── Constants ────────────────────────────────────────────────────────────────

SUITS = ("♠", "♥", "♦", "♣")
RANKS = ("2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A")
RANK_VALUE = {r: i for i, r in enumerate(RANKS, 2)}  # 2→2 … A→14
MAX_PLAYERS = 8
MIN_DECK = 5  # auto cash-out when deck gets this small


# ── Helpers ──────────────────────────────────────────────────────────────────


def _new_deck() -> list[str]:
    deck = [f"{r}{s}" for s in SUITS for r in RANKS]
    random.shuffle(deck)
    return deck


def _card_rank(card: str) -> int:
    return RANK_VALUE[card[:-1]]


def _fmt_card(card: str) -> str:
    return f"`{card}`"


def _deck_counts(deck: list[str], current: str) -> tuple[int, int, int]:
    """(higher, lower, equal) counts relative to current card."""
    rank = _card_rank(current)
    h = l = e = 0
    for c in deck:
        r = _card_rank(c)
        if r > rank:
            h += 1
        elif r < rank:
            l += 1
        else:
            e += 1
    return h, l, e


def _calc_mult(deck: list[str], current: str, guess: str) -> float:
    """Multiplier for a correct guess (fair odds, no house edge).

    Returns 0.0 when the guess is impossible (no cards in that direction).
    """
    higher, lower, _ = _deck_counts(deck, current)
    remaining = len(deck)
    if remaining == 0:
        return 0.0
    favorable = higher if guess == "higher" else lower
    if favorable == 0:
        return 0.0
    return round(remaining / favorable, 2)


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class HiLoPlayer:
    user_id: int
    display_name: str
    bet: int
    active: bool = True
    cashed_out: bool = False
    busted: bool = False
    payout: int = 0
    multiplier: float = 1.0
    guess: str = ""  # "higher" | "lower" | ""


@dataclass
class HiLoTable:
    channel_id: int
    host_id: int
    host_name: str
    phase: str = "betting"  # betting | playing | finished
    deck: list[str] = field(default_factory=list)
    current_card: str = ""
    card_history: list[str] = field(default_factory=list)
    players: dict[int, HiLoPlayer] = field(default_factory=dict)
    message: discord.Message | None = None
    round_num: int = 1
    streak: int = 0
    last_bets: dict[int, tuple[str, int]] = field(default_factory=dict)
    last_result: str = ""


# ── Embeds ───────────────────────────────────────────────────────────────────


def _betting_embed(table: HiLoTable) -> discord.Embed:
    embed = discord.Embed(
        title=f"Hi-Lo — Place Your Bets (Round {table.round_num})",
        description="Join the table, then the host starts the game!",
        colour=discord.Colour.blurple(),
    )
    if table.players:
        lines = [
            f"💰 **{p.display_name}** — {p.bet}c"
            for p in table.players.values()
        ]
        embed.add_field(name="Players", value="\n".join(lines), inline=False)
    else:
        embed.add_field(
            name="Players",
            value="*No players yet — click Join!*",
            inline=False,
        )
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


def _playing_embed(table: HiLoTable) -> discord.Embed:
    embed = discord.Embed(
        title=f"Hi-Lo — Round {table.round_num}",
        colour=discord.Colour.blurple(),
    )

    # Card display
    history = ""
    if table.card_history:
        recent = table.card_history[-6:]
        history = " → ".join(_fmt_card(c) for c in recent) + " → "
    embed.description = f"# {_fmt_card(table.current_card)}\n{history}{_fmt_card(table.current_card)}"

    # Last result banner
    if table.last_result:
        embed.description += f"\n\n{table.last_result}"

    # Odds
    higher, lower, equal = _deck_counts(table.deck, table.current_card)
    remaining = len(table.deck)
    h_mult = _calc_mult(table.deck, table.current_card, "higher")
    l_mult = _calc_mult(table.deck, table.current_card, "lower")

    odds_lines = []
    if higher > 0:
        odds_lines.append(f"⬆️ Higher: **{h_mult:.2f}x** ({higher}/{remaining})")
    else:
        odds_lines.append(f"⬆️ Higher: — (0/{remaining})")
    if lower > 0:
        odds_lines.append(f"⬇️ Lower: **{l_mult:.2f}x** ({lower}/{remaining})")
    else:
        odds_lines.append(f"⬇️ Lower: — (0/{remaining})")
    if equal > 0:
        odds_lines.append(f"➡️ Tie: push ({equal}/{remaining})")
    embed.add_field(name="Odds", value="\n".join(odds_lines), inline=False)

    # Players
    lines = []
    for p in table.players.values():
        if p.cashed_out:
            net = p.payout - p.bet
            lines.append(
                f"💰 **{p.display_name}** — Cashed out **{p.multiplier:.2f}x** (+{net}c)"
            )
        elif p.busted:
            lines.append(f"💥 **{p.display_name}** — Busted! (-{p.bet}c)")
        elif p.guess:
            lines.append(f"✅ **{p.display_name}** — {p.multiplier:.2f}x — picked!")
        else:
            potential = int(p.bet * p.multiplier)
            lines.append(
                f"🎴 **{p.display_name}** — {p.multiplier:.2f}x ({potential}c)"
            )
    embed.add_field(
        name="Players", value="\n".join(lines) or "*—*", inline=False,
    )

    embed.set_footer(
        text=f"Host: {table.host_name} · Streak: {table.streak} · "
        f"Cards left: {remaining}",
    )
    return embed


def _finished_embed(
    table: HiLoTable, *, balances: dict[int, int] | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"Hi-Lo — Round {table.round_num} Complete",
        description=f"Streak: **{table.streak}**",
        colour=discord.Colour.dark_grey(),
    )
    # Card history
    if table.card_history or table.current_card:
        all_cards = table.card_history + ([table.current_card] if table.current_card else [])
        recent = all_cards[-8:]
        embed.description += "\n" + " → ".join(_fmt_card(c) for c in recent)

    lines = []
    for p in table.players.values():
        bal = balances.get(p.user_id, 0) if balances else 0
        if p.cashed_out:
            net = p.payout - p.bet
            lines.append(
                f"💰 **{p.display_name}** — {p.multiplier:.2f}x "
                f"(**+{net}c**) — bal: {bal}c"
            )
        else:
            lines.append(
                f"💥 **{p.display_name}** — Busted! "
                f"(**-{p.bet}c**) — bal: {bal}c"
            )
    embed.add_field(
        name="Results", value="\n".join(lines) or "*—*", inline=False,
    )
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


# ── Modal ────────────────────────────────────────────────────────────────────


class JoinHiLoModal(ui.Modal):
    amount = ui.TextInput(
        label="Bet amount (coins)",
        placeholder="e.g. 100",
        required=True,
        max_length=10,
    )

    def __init__(self, table: HiLoTable, view: "HiLoTableView", balance: int) -> None:
        super().__init__(title="Join Hi-Lo")
        self.table = table
        self.table_view = view
        self.amount.placeholder = f"e.g. 100 (bal: {balance}c)"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            amt = int(self.amount.value)
        except ValueError:
            await interaction.response.send_message(
                "Enter a whole number.", ephemeral=True,
            )
            return
        if amt < 1:
            await interaction.response.send_message(
                "Must be at least 1 coin.", ephemeral=True,
            )
            return
        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message(
                "You're already in this round!", ephemeral=True,
            )
            return

        try:
            await queries.update_casino_balance(str(uid), -amt)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins! (have {bal}c)", ephemeral=True,
            )
            return

        self.table.players[uid] = HiLoPlayer(
            user_id=uid,
            display_name=interaction.user.display_name,
            bet=amt,
        )
        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self.table_view,
        )


# ── View ─────────────────────────────────────────────────────────────────────


class HiLoTableView(ui.View):
    def __init__(
        self, table: HiLoTable, active_tables: dict[int, HiLoTable],
    ) -> None:
        super().__init__(timeout=300)
        self.table = table
        self.active_tables = active_tables
        self._update_buttons()

    # ── Button state ──────────────────────────────────────────────────────

    def _update_buttons(self) -> None:
        t = self.table
        betting = t.phase == "betting"
        playing = t.phase == "playing"
        finished = t.phase == "finished"

        # Row 0
        self.start_btn.disabled = not betting or not t.players
        self.join_btn.disabled = not betting
        self.rebet_btn.disabled = not betting or not t.last_bets
        self.leave_btn.disabled = playing

        # Row 1 — update labels with current multipliers
        if playing and t.current_card:
            h_mult = _calc_mult(t.deck, t.current_card, "higher")
            l_mult = _calc_mult(t.deck, t.current_card, "lower")
            higher, lower, _ = _deck_counts(t.deck, t.current_card)
            self.higher_btn.label = f"Higher {h_mult:.2f}x" if higher else "Higher —"
            self.lower_btn.label = f"Lower {l_mult:.2f}x" if lower else "Lower —"
        else:
            self.higher_btn.label = "Higher"
            self.lower_btn.label = "Lower"

        self.higher_btn.disabled = not playing
        self.lower_btn.disabled = not playing
        self.cashout_btn.disabled = not playing

        # Row 2
        self.new_round_btn.disabled = not finished
        self.close_btn.disabled = playing

    # ── Row 0: Start / Join / Re-bet / Leave ─────────────────────────────

    @ui.button(
        label="Start", style=discord.ButtonStyle.success, emoji="🃏", row=0,
    )
    async def start_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can start!", ephemeral=True,
            )
            return
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Already started!", ephemeral=True,
            )
            return
        if not self.table.players:
            await interaction.response.send_message(
                "No players yet!", ephemeral=True,
            )
            return
        await self._start_game(interaction)

    @ui.button(
        label="Join", style=discord.ButtonStyle.primary, emoji="💰", row=0,
    )
    async def join_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Game in progress! Wait for next round.", ephemeral=True,
            )
            return
        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message(
                "You're already in!", ephemeral=True,
            )
            return
        if len(self.table.players) >= MAX_PLAYERS:
            await interaction.response.send_message(
                "Table is full!", ephemeral=True,
            )
            return
        bal = await queries.get_or_create_casino_wallet(str(uid))
        await interaction.response.send_modal(JoinHiLoModal(self.table, self, bal))

    @ui.button(
        label="Re-bet", style=discord.ButtonStyle.primary, emoji="🔄", row=0,
    )
    async def rebet_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Game in progress!", ephemeral=True,
            )
            return
        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message(
                "You're already in!", ephemeral=True,
            )
            return
        last = self.table.last_bets.get(uid)
        if last is None:
            await interaction.response.send_message(
                "No previous bet — use Join instead.", ephemeral=True,
            )
            return
        if len(self.table.players) >= MAX_PLAYERS:
            await interaction.response.send_message(
                "Table is full!", ephemeral=True,
            )
            return
        name, amt = last
        try:
            await queries.update_casino_balance(str(uid), -amt)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins for {amt}c re-bet! (have {bal}c)",
                ephemeral=True,
            )
            return
        self.table.players[uid] = HiLoPlayer(
            user_id=uid, display_name=name, bet=amt,
        )
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    @ui.button(
        label="Leave", style=discord.ButtonStyle.secondary, emoji="🚪", row=0,
    )
    async def leave_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if player is None:
            await interaction.response.send_message(
                "You're not at this table.", ephemeral=True,
            )
            return
        if self.table.phase == "playing":
            await interaction.response.send_message(
                "Can't leave mid-game! Cash out instead.", ephemeral=True,
            )
            return
        if self.table.phase == "betting":
            await queries.update_casino_balance(str(uid), player.bet)
            del self.table.players[uid]
            self._update_buttons()
            await interaction.response.edit_message(
                embed=_betting_embed(self.table), view=self,
            )
            return
        await interaction.response.send_message(
            "Round is over. Wait for New Round or Close.", ephemeral=True,
        )

    # ── Row 1: Higher / Lower / Cash Out ─────────────────────────────────

    @ui.button(
        label="Higher", style=discord.ButtonStyle.success, emoji="⬆️", row=1,
    )
    async def higher_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        await self._pick(interaction, "higher")

    @ui.button(
        label="Lower", style=discord.ButtonStyle.danger, emoji="⬇️", row=1,
    )
    async def lower_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        await self._pick(interaction, "lower")

    @ui.button(
        label="Cash Out", style=discord.ButtonStyle.primary, emoji="💰", row=1,
    )
    async def cashout_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "playing":
            await interaction.response.send_message(
                "No game running!", ephemeral=True,
            )
            return
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if player is None or not player.active:
            await interaction.response.send_message(
                "You're not active in this round!", ephemeral=True,
            )
            return
        if player.cashed_out:
            await interaction.response.send_message(
                "You already cashed out!", ephemeral=True,
            )
            return

        await self._cashout(player)

        # Check if all players done
        active = [p for p in self.table.players.values() if p.active]
        if not active:
            await self._finish(interaction)
        elif all(p.guess for p in active):
            await self._reveal(interaction)
        else:
            self._update_buttons()
            await interaction.response.edit_message(
                embed=_playing_embed(self.table), view=self,
            )

    # ── Row 2: New Round / Close ─────────────────────────────────────────

    @ui.button(
        label="New Round", style=discord.ButtonStyle.success, emoji="▶️", row=2,
    )
    async def new_round_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can start a new round!", ephemeral=True,
            )
            return
        if self.table.phase != "finished":
            await interaction.response.send_message(
                "Round still in progress!", ephemeral=True,
            )
            return
        self._reset_round()
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    @ui.button(
        label="Close Table", style=discord.ButtonStyle.danger, emoji="✖️", row=2,
    )
    async def close_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can close the table!", ephemeral=True,
            )
            return
        if self.table.phase == "playing":
            await interaction.response.send_message(
                "Can't close mid-game!", ephemeral=True,
            )
            return
        if self.table.phase == "betting":
            for p in self.table.players.values():
                try:
                    await queries.update_casino_balance(str(p.user_id), p.bet)
                except Exception:
                    pass
        await self._close(interaction, "Table closed by host.")

    # ── Game logic ────────────────────────────────────────────────────────

    async def _start_game(self, interaction: discord.Interaction) -> None:
        table = self.table
        table.phase = "playing"
        table.deck = _new_deck()
        table.current_card = table.deck.pop()
        table.card_history.clear()
        table.streak = 0
        table.last_result = ""

        self._update_buttons()
        await interaction.response.edit_message(
            embed=_playing_embed(table), view=self,
        )

    async def _pick(
        self, interaction: discord.Interaction, guess: str,
    ) -> None:
        if self.table.phase != "playing":
            await interaction.response.send_message(
                "No game running!", ephemeral=True,
            )
            return
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if player is None or not player.active:
            await interaction.response.send_message(
                "You're not active in this round!", ephemeral=True,
            )
            return
        if player.guess:
            await interaction.response.send_message(
                "You already picked! Wait for the reveal.", ephemeral=True,
            )
            return

        player.guess = guess

        # Auto-reveal when all active players have picked
        active = [p for p in self.table.players.values() if p.active]
        if all(p.guess for p in active):
            await self._reveal(interaction)
        else:
            self._update_buttons()
            await interaction.response.edit_message(
                embed=_playing_embed(self.table), view=self,
            )

    async def _reveal(self, interaction: discord.Interaction) -> None:
        table = self.table

        # Compute multipliers before drawing
        h_mult = _calc_mult(table.deck, table.current_card, "higher")
        l_mult = _calc_mult(table.deck, table.current_card, "lower")

        # Draw next card
        new_card = table.deck.pop()
        old_rank = _card_rank(table.current_card)
        new_rank = _card_rank(new_card)

        if new_rank > old_rank:
            actual = "higher"
            result_text = "Higher!"
        elif new_rank < old_rank:
            actual = "lower"
            result_text = "Lower!"
        else:
            actual = "tie"
            result_text = "Tie — push!"

        # Process players
        correct_names: list[str] = []
        busted_names: list[str] = []
        for p in table.players.values():
            if not p.active:
                continue
            if actual == "tie":
                p.guess = ""  # reset, play again
                correct_names.append(p.display_name)
                continue
            if p.guess == actual:
                mult = h_mult if p.guess == "higher" else l_mult
                p.multiplier = round(p.multiplier * mult, 2)
                p.guess = ""
                table.streak += 1
                correct_names.append(p.display_name)
            else:
                p.active = False
                p.busted = True
                p.guess = ""
                busted_names.append(p.display_name)

        # Update card state
        table.card_history.append(table.current_card)
        table.current_card = new_card

        # Build result banner
        parts = [f"{_fmt_card(table.card_history[-1])} → {_fmt_card(new_card)} — **{result_text}**"]
        if correct_names:
            parts.append(f"✅ {', '.join(correct_names)}")
        if busted_names:
            parts.append(f"💥 {', '.join(busted_names)}")
        table.last_result = " ".join(parts)

        # Check end conditions
        active = [p for p in table.players.values() if p.active]
        if not active or len(table.deck) < MIN_DECK:
            # Auto cash-out any remaining active players (deck exhausted)
            for p in active:
                await self._cashout(p)
            await self._finish(interaction)
        else:
            self._update_buttons()
            await interaction.response.edit_message(
                embed=_playing_embed(table), view=self,
            )

    async def _cashout(self, player: HiLoPlayer) -> None:
        player.active = False
        player.cashed_out = True
        player.payout = int(player.bet * player.multiplier)
        await queries.update_casino_balance(str(player.user_id), player.payout)

    async def _finish(self, interaction: discord.Interaction) -> None:
        table = self.table
        table.phase = "finished"

        # Save last bets
        for p in table.players.values():
            table.last_bets[p.user_id] = (p.display_name, p.bet)

        # Log casino history
        for p in table.players.values():
            await queries.log_casino_result(str(p.user_id), "hilo", p.bet, p.payout)

        # Gather balances
        balances: dict[int, int] = {}
        for p in table.players.values():
            bal = await queries.get_casino_balance(str(p.user_id))
            balances[p.user_id] = bal or 0

        self._update_buttons()
        await interaction.response.edit_message(
            embed=_finished_embed(table, balances=balances), view=self,
        )

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def _reset_round(self) -> None:
        t = self.table
        t.players.clear()
        t.phase = "betting"
        t.deck.clear()
        t.current_card = ""
        t.card_history.clear()
        t.streak = 0
        t.round_num += 1
        t.last_result = ""

    async def _close(
        self, interaction: discord.Interaction, reason: str,
    ) -> None:
        embed = discord.Embed(
            title="Hi-Lo Table — Closed",
            description=reason,
            colour=discord.Colour.dark_grey(),
        )
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_tables.pop(self.table.channel_id, None)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        table = self.table

        if table.phase == "finished":
            self.active_tables.pop(table.channel_id, None)
            if table.message:
                try:
                    embed = discord.Embed(
                        title="Hi-Lo Table — Timed Out",
                        description="Table timed out between rounds.",
                        colour=discord.Colour.dark_grey(),
                    )
                    await table.message.edit(embed=embed, view=None)
                except Exception:
                    pass
            return

        # Betting or playing — refund/cash-out active players
        for p in table.players.values():
            if p.active and not p.cashed_out and not p.busted:
                try:
                    if table.phase == "playing" and p.multiplier > 1.0:
                        # Cash them out at current multiplier
                        payout = int(p.bet * p.multiplier)
                        await queries.update_casino_balance(
                            str(p.user_id), payout,
                        )
                    else:
                        # Refund original bet
                        await queries.update_casino_balance(
                            str(p.user_id), p.bet,
                        )
                except Exception:
                    pass

        self.active_tables.pop(table.channel_id, None)
        if table.message:
            try:
                embed = discord.Embed(
                    title="Hi-Lo Table — Timed Out",
                    description="Table timed out. Active players refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                pass


# ── Cog ──────────────────────────────────────────────────────────────────────


class HiLoCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, HiLoTable] = {}

    @app_commands.command(
        name="hilo",
        description="Open a Hi-Lo table (guess if the next card is higher or lower)",
    )
    async def hilo(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            existing = self.active_tables[channel_id]
            if getattr(existing, "phase", None) == "closed":
                del self.active_tables[channel_id]
            else:
                await interaction.response.send_message(
                    "There's already a Hi-Lo table in this channel!",
                    ephemeral=True,
                )
                return

        await queries.get_or_create_casino_wallet(str(interaction.user.id))

        table = HiLoTable(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
        )
        self.active_tables[channel_id] = table

        view = HiLoTableView(table, self.active_tables)
        embed = _betting_embed(table)
        await interaction.response.send_message(embed=embed, view=view)
        table.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HiLoCog(bot))
