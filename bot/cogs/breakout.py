"""Breakout Blackjack — bet Player or Dealer, plus a 'Breakout' total-cards side bet.

Both hands auto-play by fixed house rules (hit until >=17, stand on all 17s), so
there's no player skill — you bet which side wins (like baccarat) and peel the
cards one at a time for the slow reveal.

Edges tuned to ~1% by simulation (see scripts/breakout_edge.py):
  Player 2.17x (1.0%), Dealer 1.82x (0.8%), Breakout side bet ~0.96%.
Launched via the `/blackjack breakout` command group (see casino.py).
"""
import random
from dataclasses import dataclass, field

import discord
from discord import ui
from discord.ext import commands

from db import queries
import logging

log = logging.getLogger(__name__)

# ── Cards ──────────────────────────────────────────────────────────────────────

SUITS = ("♠", "♥", "♦", "♣")
RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
VALUES = {"A": 11, "J": 10, "Q": 10, "K": 10, **{str(n): n for n in range(2, 11)}}
SHOE_DECKS = 6
RESHUFFLE_THRESHOLD = 60
MAX_PLAYERS = 8
CARD_BACK = "🂠"

# Main-bet payout multipliers (returned per unit staked, incl. stake). Push = stake back.
PLAYER_MULT = 2.17
DEALER_MULT = 1.82
# Breakout side bet: total cards across both hands -> win odds (X:1). <7 loses.
BREAKOUT_TABLE = {7: 1, 8: 4, 9: 12, 10: 25, 11: 75}
BREAKOUT_12PLUS = 200


def _breakout_odds(total_cards: int) -> int:
    if total_cards >= 12:
        return BREAKOUT_12PLUS
    return BREAKOUT_TABLE.get(total_cards, 0)


def _new_shoe() -> list[str]:
    cards = [f"{r}{s}" for s in SUITS for r in RANKS] * SHOE_DECKS
    random.shuffle(cards)
    return cards


def _hand_value(hand: list[str]) -> int:
    """Best blackjack total (aces drop 11->1 while busting)."""
    total = sum(VALUES[c[:-1]] for c in hand)
    aces = sum(1 for c in hand if c[:-1] == "A")
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def _play_out(shoe: list[str]) -> list[str]:
    hand = [shoe.pop(), shoe.pop()]
    while _hand_value(hand) < 17:  # stand on all 17s (S17)
        hand.append(shoe.pop())
    return hand


def _resolve_winner(p_val: int, d_val: int) -> str:
    """player | dealer | push. Busts (incl. double bust) favour the house (dealer)."""
    if p_val > 21:  # player busts first -> dealer wins even on a double bust
        return "dealer"
    if d_val > 21:
        return "player"
    if p_val > d_val:
        return "player"
    if d_val > p_val:
        return "dealer"
    return "push"


def _fmt_hand(hand: list[str]) -> str:
    return " ".join(f"`{c}`" for c in hand)


# ── State ──────────────────────────────────────────────────────────────────────


@dataclass
class Seat:
    user_id: int
    display_name: str
    bet_type: str  # "player" | "dealer"
    wager: int
    breakout_wager: int = 0
    payout: int = 0
    breakout_payout: int = 0

    @property
    def total_wager(self) -> int:
        return self.wager + self.breakout_wager


@dataclass
class Table:
    channel_id: int
    opener_name: str
    player_hand: list[str] = field(default_factory=list)
    dealer_hand: list[str] = field(default_factory=list)
    player_revealed: int = 0
    dealer_revealed: int = 0
    dealt: bool = False  # True once peeling starts (bets locked)
    seats: dict[int, Seat] = field(default_factory=dict)
    message: discord.Message | None = None

    @property
    def initial_done(self) -> bool:
        return self.player_revealed >= 2 and self.dealer_revealed >= 2

    @property
    def all_revealed(self) -> bool:
        return (self.dealt
                and self.player_revealed >= len(self.player_hand)
                and self.dealer_revealed >= len(self.dealer_hand))

    def player_peelable(self) -> int:
        return len(self.player_hand) if self.initial_done else 2

    def dealer_peelable(self) -> int:
        return len(self.dealer_hand) if self.initial_done else 2


def _resolve(table: Table) -> None:
    """Set payouts on each seat once the hand is fully revealed."""
    p_val = _hand_value(table.player_hand)
    d_val = _hand_value(table.dealer_hand)
    winner = _resolve_winner(p_val, d_val)
    odds = _breakout_odds(len(table.player_hand) + len(table.dealer_hand))
    for seat in table.seats.values():
        if seat.bet_type == winner:
            mult = PLAYER_MULT if winner == "player" else DEALER_MULT
            seat.payout = int(seat.wager * mult)
        elif winner == "push":
            seat.payout = seat.wager  # stake back
        else:
            seat.payout = 0
        if seat.breakout_wager > 0:
            seat.breakout_payout = seat.breakout_wager * (odds + 1) if odds else 0


# ── Embed ──────────────────────────────────────────────────────────────────────

BET_EMOJI = {"player": "\U0001f535", "dealer": "\U0001f534"}


def _paytable_text() -> str:
    return ("Total cards: **7**→1:1 · **8**→4:1 · **9**→12:1 · "
            "**10**→25:1 · **11**→75:1 · **12+**→200:1")


def _display_hand(hand: list[str], revealed: int, show_all: bool) -> str:
    positions = len(hand) if show_all else min(2, len(hand))
    parts = [f"`{hand[i]}`" if i < revealed else CARD_BACK for i in range(positions)]
    out = " ".join(parts)
    if revealed >= 2:
        out += f"  ({_hand_value(hand[:revealed])})"
    return out


def _embed(table: Table, *, balances: dict[int, int] | None = None) -> discord.Embed:
    finished = table.all_revealed
    if not table.dealt:
        title, colour = "Breakout Blackjack — Place your bets!", discord.Colour.blurple()
    elif finished:
        title, colour = "Breakout Blackjack — Results", discord.Colour.gold()
    else:
        title, colour = "Breakout Blackjack — Peel your cards!", discord.Colour.blurple()
    embed = discord.Embed(title=title, colour=colour)

    if not table.dealt:
        embed.description = (
            f"Bet on **{BET_EMOJI['player']} Player** (2.17×) or "
            f"**{BET_EMOJI['dealer']} Dealer** (1.82×) — both hands auto-play, "
            f"you pick the winner. Then peel to reveal.\n\n"
            f"🚀 **Breakout side bet** — {_paytable_text()}")
    elif finished:
        p_val, d_val = _hand_value(table.player_hand), _hand_value(table.dealer_hand)
        winner = _resolve_winner(p_val, d_val)
        total_cards = len(table.player_hand) + len(table.dealer_hand)
        pb = " 💥BUST" if p_val > 21 else ""
        db = " 💥BUST" if d_val > 21 else ""
        embed.add_field(name=f"{BET_EMOJI['player']} Player",
                        value=f"{_fmt_hand(table.player_hand)}  (**{p_val}**){pb}", inline=False)
        embed.add_field(name=f"{BET_EMOJI['dealer']} Dealer",
                        value=f"{_fmt_hand(table.dealer_hand)}  (**{d_val}**){db}", inline=False)
        odds = _breakout_odds(total_cards)
        bo = f"🎉 **{odds}:1**" if odds else "no payout (<7)"
        wtxt = "Push" if winner == "push" else f"**{winner.capitalize()}** wins"
        embed.add_field(name="Outcome",
                        value=f"{wtxt} · Breakout: **{total_cards} cards** → {bo}", inline=False)
    else:
        embed.add_field(name=f"{BET_EMOJI['player']} Player",
                        value=_display_hand(table.player_hand, table.player_revealed,
                                            table.initial_done), inline=False)
        embed.add_field(name=f"{BET_EMOJI['dealer']} Dealer",
                        value=_display_hand(table.dealer_hand, table.dealer_revealed,
                                            table.initial_done), inline=False)

    if table.seats:
        lines = []
        for seat in table.seats.values():
            line = (f"{BET_EMOJI.get(seat.bet_type, '')} **{seat.display_name}** — "
                    f"{seat.bet_type.capitalize()} {seat.wager}c")
            if seat.breakout_wager:
                line += f" + 🚀 {seat.breakout_wager}c"
            if finished and balances is not None:
                net = (seat.payout + seat.breakout_payout) - seat.total_wager
                sign = "+" if net > 0 else ""
                line += f"\n → **{sign}{net}c** (bal: {balances.get(seat.user_id, 0)}c)"
            lines.append(line)
        embed.add_field(name="Seats", value="\n".join(lines[:MAX_PLAYERS]), inline=False)
    else:
        embed.add_field(name="Seats", value="*No bets yet — pick a side!*", inline=False)

    if table.dealt and not finished:
        p_left = table.player_peelable() - table.player_revealed
        d_left = table.dealer_peelable() - table.dealer_revealed
        if p_left + d_left == 1:
            embed.set_footer(text="🃏 Final card!")
        else:
            parts = ([f"Player: {p_left}"] if p_left else []) + \
                    ([f"Dealer: {d_left}"] if d_left else [])
            if parts:
                embed.set_footer(text="Cards remaining — " + " • ".join(parts))
    return embed


# ── Modal ───────────────────────────────────────────────────────────────────────


class WagerModal(ui.Modal):
    amount = ui.TextInput(label="Wager (coins)", placeholder="e.g. 50", max_length=10)

    def __init__(self, table: Table, view: "TableView", kind: str, balance: int) -> None:
        titles = {"player": "Bet Player", "dealer": "Bet Dealer", "breakout": "🚀 Breakout side bet"}
        super().__init__(title=titles[kind])
        self.table, self.table_view, self.kind = table, view, kind
        self.amount.placeholder = f"e.g. 50 (bal: {balance}c)"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            amt = int(self.amount.value)
        except ValueError:
            await interaction.response.send_message("Enter a whole number.", ephemeral=True)
            return
        if amt < 1:
            await interaction.response.send_message("Must be at least 1 coin.", ephemeral=True)
            return
        if self.table.dealt:
            await interaction.response.send_message("Cards already being dealt!", ephemeral=True)
            return

        uid = interaction.user.id
        seat = self.table.seats.get(uid)
        if self.kind == "breakout":
            if seat is None:
                await interaction.response.send_message("Place a main bet first!", ephemeral=True)
                return
            if seat.breakout_wager > 0:
                await interaction.response.send_message("You already have a Breakout bet!", ephemeral=True)
                return
        else:
            if seat is not None:
                await interaction.response.send_message("You already have a main bet!", ephemeral=True)
                return
            if len(self.table.seats) >= MAX_PLAYERS:
                await interaction.response.send_message("Table is full!", ephemeral=True)
                return

        try:
            await queries.update_casino_balance(str(uid), -amt)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(f"Not enough coins! (have {bal})", ephemeral=True)
            return

        if self.kind == "breakout":
            seat.breakout_wager = amt
        else:
            self.table.seats[uid] = Seat(uid, interaction.user.display_name, self.kind, amt)
        self.table_view._update_buttons()
        await interaction.response.edit_message(embed=_embed(self.table), view=self.table_view)


# ── View ────────────────────────────────────────────────────────────────────────


class TableView(ui.View):
    def __init__(self, table: Table, active: dict[int, Table], cog: "BreakoutCog") -> None:
        super().__init__(timeout=120)
        self.table, self.active, self.cog = table, active, cog
        self._update_buttons()

    def _update_buttons(self) -> None:
        finished = self.table.all_revealed
        dealt = self.table.dealt
        has_bets = bool(self.table.seats)

        self.new_hand_btn.disabled = not finished
        self.close_btn.disabled = not finished
        if finished:
            for b in (self.peel_player_btn, self.peel_dealer_btn, self.reveal_all_btn,
                      self.bet_player_btn, self.bet_dealer_btn, self.breakout_btn, self.leave_btn):
                b.disabled = True
            return

        if dealt:
            self.peel_player_btn.disabled = self.table.player_revealed >= self.table.player_peelable()
            self.peel_dealer_btn.disabled = self.table.dealer_revealed >= self.table.dealer_peelable()
            self.reveal_all_btn.disabled = False
        else:  # before dealing: peel enabled only once there are bets (first peel deals)
            self.peel_player_btn.disabled = not has_bets
            self.peel_dealer_btn.disabled = not has_bets
            self.reveal_all_btn.disabled = not has_bets

        for b in (self.bet_player_btn, self.bet_dealer_btn, self.breakout_btn):
            b.disabled = dealt
        self.leave_btn.disabled = dealt

    # ── Row 0: peel ──────────────────────────────────────────
    @ui.button(label="Peel Player", style=discord.ButtonStyle.primary, emoji="\U0001f0cf", row=0)
    async def peel_player_btn(self, interaction: discord.Interaction, _: ui.Button) -> None:
        await self._peel(interaction, "player")

    @ui.button(label="Peel Dealer", style=discord.ButtonStyle.danger, emoji="\U0001f0cf", row=0)
    async def peel_dealer_btn(self, interaction: discord.Interaction, _: ui.Button) -> None:
        await self._peel(interaction, "dealer")

    @ui.button(label="Reveal All", style=discord.ButtonStyle.secondary, emoji="\U0001f441", row=0)
    async def reveal_all_btn(self, interaction: discord.Interaction, _: ui.Button) -> None:
        if not self.table.seats:
            await interaction.response.send_message("No bets on the table yet!", ephemeral=True)
            return
        self.table.dealt = True
        self.table.player_revealed = len(self.table.player_hand)
        self.table.dealer_revealed = len(self.table.dealer_hand)
        await self._finish(interaction)

    async def _peel(self, interaction: discord.Interaction, side: str) -> None:
        if not self.table.seats:
            await interaction.response.send_message("No bets on the table yet!", ephemeral=True)
            return
        self.table.dealt = True
        if side == "player":
            self.table.player_revealed += 1
        else:
            self.table.dealer_revealed += 1
        if self.table.all_revealed:
            await self._finish(interaction)
        else:
            self._update_buttons()
            await interaction.response.edit_message(embed=_embed(self.table), view=self)

    # ── Row 1: main bets ─────────────────────────────────────
    @ui.button(label="Bet Player", style=discord.ButtonStyle.primary, emoji="\U0001f535", row=1)
    async def bet_player_btn(self, interaction: discord.Interaction, _: ui.Button) -> None:
        await self._bet(interaction, "player")

    @ui.button(label="Bet Dealer", style=discord.ButtonStyle.danger, emoji="\U0001f534", row=1)
    async def bet_dealer_btn(self, interaction: discord.Interaction, _: ui.Button) -> None:
        await self._bet(interaction, "dealer")

    @ui.button(label="Breakout", style=discord.ButtonStyle.success, emoji="\U0001f680", row=1)
    async def breakout_btn(self, interaction: discord.Interaction, _: ui.Button) -> None:
        await self._bet(interaction, "breakout")

    @ui.button(label="Leave", style=discord.ButtonStyle.secondary, emoji="\U0001f6aa", row=1)
    async def leave_btn(self, interaction: discord.Interaction, _: ui.Button) -> None:
        uid = interaction.user.id
        seat = self.table.seats.get(uid)
        if seat is None:
            await interaction.response.send_message("You're not at this table.", ephemeral=True)
            return
        if self.table.dealt:
            await interaction.response.send_message("Cards already being dealt!", ephemeral=True)
            return
        await queries.update_casino_balance(str(uid), seat.total_wager)
        del self.table.seats[uid]
        if not self.table.seats:
            await self._close(interaction, "All players left — table closed.")
            return
        self._update_buttons()
        await interaction.response.edit_message(embed=_embed(self.table), view=self)

    # ── Row 2: post-finish ───────────────────────────────────
    @ui.button(label="New Hand", style=discord.ButtonStyle.success, emoji="\U0001f504", row=2, disabled=True)
    async def new_hand_btn(self, interaction: discord.Interaction, _: ui.Button) -> None:
        shoe = self.cog._ensure_shoe()
        self.table.player_hand = _play_out(shoe)
        self.table.dealer_hand = _play_out(shoe)
        self.table.player_revealed = 0
        self.table.dealer_revealed = 0
        self.table.dealt = False
        self.table.seats.clear()
        self._update_buttons()
        await interaction.response.edit_message(embed=_embed(self.table), view=self)

    @ui.button(label="Close Table", style=discord.ButtonStyle.secondary, emoji="❌", row=2, disabled=True)
    async def close_btn(self, interaction: discord.Interaction, _: ui.Button) -> None:
        await self._close(interaction, "Table closed.")

    # ── helpers ──────────────────────────────────────────────
    async def _bet(self, interaction: discord.Interaction, kind: str) -> None:
        if self.table.dealt:
            await interaction.response.send_message("Cards already being dealt!", ephemeral=True)
            return
        if kind == "breakout" and interaction.user.id not in self.table.seats:
            await interaction.response.send_message("Place a main bet first!", ephemeral=True)
            return
        bal = await queries.get_or_create_casino_wallet(str(interaction.user.id))
        await interaction.response.send_modal(WagerModal(self.table, self, kind, bal))

    async def _finish(self, interaction: discord.Interaction) -> None:
        table = self.table
        _resolve(table)
        balances: dict[int, int] = {}
        for seat in table.seats.values():
            total_payout = seat.payout + seat.breakout_payout
            if total_payout > 0:
                balances[seat.user_id] = await queries.update_casino_balance(
                    str(seat.user_id), total_payout)
            else:
                balances[seat.user_id] = await queries.get_casino_balance(str(seat.user_id)) or 0
            await queries.log_casino_result(
                str(seat.user_id), "breakout", seat.total_wager, total_payout)
        self._update_buttons()
        await interaction.response.edit_message(embed=_embed(table, balances=balances), view=self)

    async def _close(self, interaction: discord.Interaction, reason: str) -> None:
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active.pop(self.table.channel_id, None)
        embed = discord.Embed(title="Breakout Blackjack — Closed", description=reason,
                              colour=discord.Colour.dark_grey())
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        table = self.table
        self.active.pop(table.channel_id, None)
        if not table.all_revealed:  # refund undealt/in-progress bets
            for seat in table.seats.values():
                try:
                    await queries.update_casino_balance(str(seat.user_id), seat.total_wager)
                except Exception:
                    log.exception("Unhandled error in breakout.py")
        if table.message:
            try:
                await table.message.edit(view=None)
            except Exception:
                log.exception("Unhandled error in breakout.py")


# ── Cog ─────────────────────────────────────────────────────────────────────────


class BreakoutCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.shoe = _new_shoe()
        self.active_tables: dict[int, Table] = {}

    def _ensure_shoe(self) -> list[str]:
        if len(self.shoe) < RESHUFFLE_THRESHOLD:
            self.shoe = _new_shoe()
        return self.shoe

    async def _open(self, interaction: discord.Interaction) -> None:
        """Open a Breakout table. Reached via `/blackjack breakout` and `/play`."""
        channel_id = interaction.channel_id
        existing = self.active_tables.get(channel_id)
        if existing and not existing.all_revealed and existing.seats:
            await interaction.response.send_message(
                "There's already a Breakout table here! Use the buttons to bet.", ephemeral=True)
            return
        self.active_tables.pop(channel_id, None)

        await queries.get_or_create_casino_wallet(str(interaction.user.id))
        shoe = self._ensure_shoe()
        table = Table(channel_id=channel_id, opener_name=interaction.user.display_name,
                      player_hand=_play_out(shoe), dealer_hand=_play_out(shoe))
        view = TableView(table, self.active_tables, self)
        try:
            await interaction.response.send_message(embed=_embed(table), view=view)
        except discord.NotFound:
            return
        self.active_tables[channel_id] = table
        table.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BreakoutCog(bot))
