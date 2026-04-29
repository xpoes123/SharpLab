"""Baccarat cog — multiplayer table with interactive card peeling."""
import random
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries

# ── Card helpers ──────────────────────────────────────────────────────────────

SUITS = ("♠", "♥", "♦", "♣")
RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
BACCARAT_VALUES = {
    "A": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7,
    "8": 8, "9": 9, "10": 0, "J": 0, "Q": 0, "K": 0,
}
SHOE_DECKS = 8
RESHUFFLE_THRESHOLD = 80
CARD_BACK = "🂠"
MAX_PLAYERS = 8


def _new_shoe() -> list[str]:
    """Create and shuffle an 8-deck shoe."""
    cards = [f"{r}{s}" for s in SUITS for r in RANKS] * SHOE_DECKS
    random.shuffle(cards)
    return cards


def _card_value(card: str) -> int:
    return BACCARAT_VALUES[card[:-1]]


def _hand_value(hand: list[str]) -> int:
    return sum(_card_value(c) for c in hand) % 10


def _fmt_card(card: str) -> str:
    return f"`{card}`"


def _fmt_hand(hand: list[str]) -> str:
    return " ".join(_fmt_card(c) for c in hand)


# ── Dealing logic ────────────────────────────────────────────────────────────

def _play_hand(shoe: list[str]) -> tuple[list[str], list[str]]:
    """Deal a full baccarat hand. Returns (player_hand, banker_hand)."""
    player = [shoe.pop(), shoe.pop()]
    banker = [shoe.pop(), shoe.pop()]

    p_val = _hand_value(player)
    b_val = _hand_value(banker)

    if p_val >= 8 or b_val >= 8:
        return player, banker

    player_drew = False
    player_third_value = -1

    if p_val <= 5:
        third = shoe.pop()
        player.append(third)
        player_drew = True
        player_third_value = _card_value(third)

    b_val = _hand_value(banker)

    if not player_drew:
        if b_val <= 5:
            banker.append(shoe.pop())
    else:
        if b_val <= 2:
            banker.append(shoe.pop())
        elif b_val == 3:
            if player_third_value != 8:
                banker.append(shoe.pop())
        elif b_val == 4:
            if player_third_value in (2, 3, 4, 5, 6, 7):
                banker.append(shoe.pop())
        elif b_val == 5:
            if player_third_value in (4, 5, 6, 7):
                banker.append(shoe.pop())
        elif b_val == 6:
            if player_third_value in (6, 7):
                banker.append(shoe.pop())

    return player, banker


# ── Game state ───────────────────────────────────────────────────────────────

PANDA_PAYOUT = 25  # 25:1
DRAGON_PAYOUT = 40  # 40:1


@dataclass
class PlayerSeat:
    user_id: int
    display_name: str
    bet_type: str  # "player" | "banker" | "tie"
    wager: int
    panda_wager: int = 0  # Panda 8 side bet
    dragon_wager: int = 0  # Dragon 7 side bet
    payout: int = 0
    panda_payout: int = 0
    dragon_payout: int = 0

    @property
    def total_wager(self) -> int:
        return self.wager + self.panda_wager + self.dragon_wager


@dataclass
class BaccaratTable:
    channel_id: int
    opener_id: int
    opener_name: str
    player_hand: list[str] = field(default_factory=list)
    banker_hand: list[str] = field(default_factory=list)
    player_revealed: int = 0
    banker_revealed: int = 0
    dealt: bool = False  # True once peeling starts (hands visible)
    players: dict[int, PlayerSeat] = field(default_factory=dict)
    message: discord.Message | None = None

    @property
    def initial_done(self) -> bool:
        """Both sides have their first 2 cards revealed."""
        return self.player_revealed >= 2 and self.banker_revealed >= 2

    @property
    def all_revealed(self) -> bool:
        return (
            self.dealt
            and self.player_revealed >= len(self.player_hand)
            and self.banker_revealed >= len(self.banker_hand)
        )

    def player_peelable(self) -> int:
        """Max player cards visible right now."""
        if not self.initial_done:
            return 2
        return len(self.player_hand)

    def banker_peelable(self) -> int:
        """Max banker cards visible right now."""
        if not self.initial_done:
            return 2
        return len(self.banker_hand)


# ── Resolution ───────────────────────────────────────────────────────────────

BET_EMOJI = {"player": "\U0001f535", "banker": "\U0001f534", "tie": "\U0001f7e1"}


def _resolve_payouts(table: BaccaratTable) -> str:
    """Set payout on each player seat. Returns 'player' | 'banker' | 'tie'."""
    p_val = _hand_value(table.player_hand)
    b_val = _hand_value(table.banker_hand)

    if p_val > b_val:
        winner = "player"
    elif b_val > p_val:
        winner = "banker"
    else:
        winner = "tie"

    # Panda 8: Player wins with 3-card total of 8
    panda_hit = (
        winner == "player"
        and len(table.player_hand) == 3
        and p_val == 8
    )
    # Dragon 7: Banker wins with 3-card total of 7
    dragon_hit = (
        winner == "banker"
        and len(table.banker_hand) == 3
        and b_val == 7
    )

    for seat in table.players.values():
        # Main bet
        if seat.bet_type == "tie":
            seat.payout = (seat.wager + 8 * seat.wager) if winner == "tie" else 0
        elif seat.bet_type == winner:
            if seat.bet_type == "banker":
                commission = seat.wager * 5 // 100
                seat.payout = seat.wager + (seat.wager - commission)
            else:
                seat.payout = seat.wager * 2
        elif winner == "tie":
            seat.payout = seat.wager  # push
        else:
            seat.payout = 0

        # Panda 8 side bet
        if seat.panda_wager > 0:
            if panda_hit:
                seat.panda_payout = seat.panda_wager + PANDA_PAYOUT * seat.panda_wager
            else:
                seat.panda_payout = 0

        # Dragon 7 side bet
        if seat.dragon_wager > 0:
            if dragon_hit:
                seat.dragon_payout = seat.dragon_wager + DRAGON_PAYOUT * seat.dragon_wager
            else:
                seat.dragon_payout = 0

    return winner


# ── Embed ────────────────────────────────────────────────────────────────────


def _display_hand(hand: list[str], revealed: int, show_all: bool) -> str:
    """Format a hand with revealed cards and face-down backs."""
    positions = len(hand) if show_all else min(2, len(hand))
    parts = []
    for i in range(positions):
        if i < revealed:
            parts.append(_fmt_card(hand[i]))
        else:
            parts.append(CARD_BACK)
    display = " ".join(parts)
    if revealed >= 2:
        display += f"  ({_hand_value(hand[:revealed])})"
    return display


def _table_embed(
    table: BaccaratTable, *, balances: dict[int, int] | None = None,
) -> discord.Embed:
    finished = table.all_revealed

    if not table.dealt:
        title = "Baccarat Table \u2014 Place your bets!"
        colour = discord.Colour.blurple()
    elif finished:
        title = "Baccarat Table \u2014 Results"
        colour = discord.Colour.gold()
    else:
        title = "Baccarat Table \u2014 Peel your cards!"
        colour = discord.Colour.blurple()

    embed = discord.Embed(title=title, colour=colour)

    # Hands
    if table.dealt:
        if finished:
            p_val = _hand_value(table.player_hand)
            b_val = _hand_value(table.banker_hand)
            p_third = " *(drew third)*" if len(table.player_hand) == 3 else ""
            b_third = " *(drew third)*" if len(table.banker_hand) == 3 else ""
            embed.add_field(
                name="Player",
                value=f"{_fmt_hand(table.player_hand)}  ({p_val}){p_third}",
                inline=False,
            )
            embed.add_field(
                name="Banker",
                value=f"{_fmt_hand(table.banker_hand)}  ({b_val}){b_third}",
                inline=False,
            )
            if len(table.player_hand) == 2 and p_val >= 8:
                embed.add_field(
                    name="", value=f"Player natural **{p_val}**!", inline=False,
                )
            if len(table.banker_hand) == 2 and b_val >= 8:
                embed.add_field(
                    name="", value=f"Banker natural **{b_val}**!", inline=False,
                )
        else:
            p_str = _display_hand(
                table.player_hand, table.player_revealed, table.initial_done,
            )
            b_str = _display_hand(
                table.banker_hand, table.banker_revealed, table.initial_done,
            )
            embed.add_field(name="Player", value=p_str, inline=False)
            embed.add_field(name="Banker", value=b_str, inline=False)
    else:
        embed.description = "Place your bets, then peel to start!"

    # Seats
    if table.players:
        lines = []
        for seat in table.players.values():
            emoji = BET_EMOJI.get(seat.bet_type, "\U0001f535")
            line = (
                f"{emoji} **{seat.display_name}** \u2014 "
                f"{seat.bet_type.capitalize()} {seat.wager}c"
            )
            # Show side bets
            sides = []
            if seat.panda_wager > 0:
                sides.append(f"\U0001f43c Panda {seat.panda_wager}c")
            if seat.dragon_wager > 0:
                sides.append(f"\U0001f432 Dragon {seat.dragon_wager}c")
            if sides:
                line += f" + {' + '.join(sides)}"

            if finished and balances:
                total_payout = seat.payout + seat.panda_payout + seat.dragon_payout
                total_wager = seat.total_wager
                net = total_payout - total_wager
                sign = "+" if net > 0 else ""
                bal = balances.get(seat.user_id, 0)
                # Show side bet results if they had any
                side_results = []
                if seat.panda_wager > 0:
                    if seat.panda_payout > 0:
                        side_results.append(
                            f"\U0001f43c Panda 8 \u2714 +{seat.panda_payout - seat.panda_wager}c"
                        )
                    else:
                        side_results.append(f"\U0001f43c Panda 8 \u2716")
                if seat.dragon_wager > 0:
                    if seat.dragon_payout > 0:
                        side_results.append(
                            f"\U0001f432 Dragon 7 \u2714 +{seat.dragon_payout - seat.dragon_wager}c"
                        )
                    else:
                        side_results.append(f"\U0001f432 Dragon 7 \u2716")
                side_str = f"\n\u2003{'  '.join(side_results)}" if side_results else ""
                line += f"{side_str}\n\u2003\u2192 **{sign}{net}c** (bal: {bal}c)"
            lines.append(line)
        embed.add_field(
            name="Seats", value="\n".join(lines[:MAX_PLAYERS]), inline=False,
        )
    else:
        embed.add_field(
            name="Seats",
            value="*No players yet \u2014 place a bet to join!*",
            inline=False,
        )

    # Peel progress footer
    if table.dealt and not finished:
        p_left = table.player_peelable() - table.player_revealed
        b_left = table.banker_peelable() - table.banker_revealed
        total = p_left + b_left
        if total == 1:
            embed.set_footer(text="\U0001f941 Final card!")
        elif total > 0:
            parts = []
            if p_left > 0:
                parts.append(f"Player: {p_left}")
            if b_left > 0:
                parts.append(f"Banker: {b_left}")
            embed.set_footer(
                text=f"Cards remaining \u2014 {' \u2022 '.join(parts)}",
            )

    return embed


# ── Modals ───────────────────────────────────────────────────────────────────


class BetModal(ui.Modal):
    amount = ui.TextInput(
        label="Wager (coins)", placeholder="e.g. 50", required=True, max_length=10,
    )

    def __init__(
        self, table: BaccaratTable, bet_type: str, view: "BaccaratTableView", balance: int,
    ) -> None:
        super().__init__(title=f"Bet {bet_type.capitalize()}")
        self.table = table
        self.bet_type = bet_type
        self.table_view = view
        self.amount.placeholder = f"e.g. 50 (bal: {balance}c)"

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
                "You already have a bet!", ephemeral=True,
            )
            return
        if self.table.dealt:
            await interaction.response.send_message(
                "Cards already being dealt!", ephemeral=True,
            )
            return
        try:
            await queries.update_casino_balance(str(uid), -amt)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins! (have {bal})", ephemeral=True,
            )
            return
        self.table.players[uid] = PlayerSeat(
            user_id=uid,
            display_name=interaction.user.display_name,
            bet_type=self.bet_type,
            wager=amt,
        )
        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_table_embed(self.table), view=self.table_view,
        )


class SideBetModal(ui.Modal):
    amount = ui.TextInput(
        label="Side bet amount (coins)", placeholder="e.g. 10",
        required=True, max_length=10,
    )

    def __init__(
        self, table: BaccaratTable, side: str, view: "BaccaratTableView", balance: int,
    ) -> None:
        label = "Panda 8 (25:1)" if side == "panda" else "Dragon 7 (40:1)"
        super().__init__(title=f"Side Bet — {label}")
        self.table = table
        self.side = side
        self.table_view = view
        self.amount.placeholder = f"e.g. 10 (bal: {balance}c)"

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
        seat = self.table.players.get(uid)
        if seat is None:
            await interaction.response.send_message(
                "Place a main bet first!", ephemeral=True,
            )
            return
        if self.table.dealt:
            await interaction.response.send_message(
                "Cards already being dealt!", ephemeral=True,
            )
            return
        # Check if already placed this side bet
        if self.side == "panda" and seat.panda_wager > 0:
            await interaction.response.send_message(
                "You already have a Panda 8 bet!", ephemeral=True,
            )
            return
        if self.side == "dragon" and seat.dragon_wager > 0:
            await interaction.response.send_message(
                "You already have a Dragon 7 bet!", ephemeral=True,
            )
            return
        try:
            await queries.update_casino_balance(str(uid), -amt)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins! (have {bal})", ephemeral=True,
            )
            return

        if self.side == "panda":
            seat.panda_wager = amt
        else:
            seat.dragon_wager = amt

        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_table_embed(self.table), view=self.table_view,
        )


# ── View ─────────────────────────────────────────────────────────────────────


class BaccaratTableView(ui.View):
    def __init__(
        self,
        table: BaccaratTable,
        active_tables: dict[int, "BaccaratTable"],
        cog: "BaccaratCog",
    ) -> None:
        super().__init__(timeout=120)
        self.table = table
        self.active_tables = active_tables
        self.cog = cog
        self._update_buttons()

    def _update_buttons(self) -> None:
        finished = self.table.all_revealed
        dealt = self.table.dealt
        has_bets = bool(self.table.players)

        # Post-finish: only New Hand + Close Table active
        self.new_hand_btn.disabled = not finished
        self.close_table_btn.disabled = not finished

        if finished:
            self.peel_player_btn.disabled = True
            self.peel_banker_btn.disabled = True
            self.reveal_all_btn.disabled = True
            self.bet_player_btn.disabled = True
            self.bet_banker_btn.disabled = True
            self.bet_tie_btn.disabled = True
            self.panda_btn.disabled = True
            self.dragon_btn.disabled = True
            self.leave_btn.disabled = True
            return

        # Peel buttons
        if dealt:
            self.peel_player_btn.disabled = (
                self.table.player_revealed >= self.table.player_peelable()
            )
            self.peel_banker_btn.disabled = (
                self.table.banker_revealed >= self.table.banker_peelable()
            )
            self.reveal_all_btn.disabled = False
        else:
            # Before dealing: peel enabled only if there are bets (first peel deals)
            self.peel_player_btn.disabled = not has_bets
            self.peel_banker_btn.disabled = not has_bets
            self.reveal_all_btn.disabled = not has_bets

        # Bet buttons: locked once dealt
        self.bet_player_btn.disabled = dealt
        self.bet_banker_btn.disabled = dealt
        self.bet_tie_btn.disabled = dealt

        # Side bet buttons: locked once dealt
        self.panda_btn.disabled = dealt
        self.dragon_btn.disabled = dealt

    # ── Row 0: Peel Player, Peel Banker, Reveal All ──────────

    @ui.button(
        label="Peel Player", style=discord.ButtonStyle.primary,
        emoji="\U0001f0cf", row=0,
    )
    async def peel_player_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if not self.table.players:
            await interaction.response.send_message(
                "No bets on the table yet!", ephemeral=True,
            )
            return
        if not self.table.dealt:
            self.table.dealt = True
        self.table.player_revealed += 1
        if self.table.all_revealed:
            await self._finish(interaction)
        else:
            self._update_buttons()
            await interaction.response.edit_message(
                embed=_table_embed(self.table), view=self,
            )

    @ui.button(
        label="Peel Banker", style=discord.ButtonStyle.danger,
        emoji="\U0001f0cf", row=0,
    )
    async def peel_banker_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if not self.table.players:
            await interaction.response.send_message(
                "No bets on the table yet!", ephemeral=True,
            )
            return
        if not self.table.dealt:
            self.table.dealt = True
        self.table.banker_revealed += 1
        if self.table.all_revealed:
            await self._finish(interaction)
        else:
            self._update_buttons()
            await interaction.response.edit_message(
                embed=_table_embed(self.table), view=self,
            )

    @ui.button(
        label="Reveal All", style=discord.ButtonStyle.secondary,
        emoji="\U0001f441", row=0,
    )
    async def reveal_all_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if not self.table.players:
            await interaction.response.send_message(
                "No bets on the table yet!", ephemeral=True,
            )
            return
        self.table.dealt = True
        self.table.player_revealed = len(self.table.player_hand)
        self.table.banker_revealed = len(self.table.banker_hand)
        await self._finish(interaction)

    # ── Row 1: Bet Player, Bet Banker, Bet Tie, Leave ────────

    @ui.button(
        label="Bet Player", style=discord.ButtonStyle.primary,
        emoji="\U0001f535", row=1,
    )
    async def bet_player_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        await self._handle_bet(interaction, "player")

    @ui.button(
        label="Bet Banker", style=discord.ButtonStyle.danger,
        emoji="\U0001f534", row=1,
    )
    async def bet_banker_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        await self._handle_bet(interaction, "banker")

    @ui.button(
        label="Bet Tie", style=discord.ButtonStyle.success,
        emoji="\U0001f7e1", row=1,
    )
    async def bet_tie_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        await self._handle_bet(interaction, "tie")

    @ui.button(
        label="Leave", style=discord.ButtonStyle.secondary,
        emoji="\U0001f6aa", row=1,
    )
    async def leave_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        uid = interaction.user.id
        seat = self.table.players.get(uid)
        if seat is None:
            await interaction.response.send_message(
                "You're not at this table.", ephemeral=True,
            )
            return
        if self.table.dealt:
            await interaction.response.send_message(
                "Cards are already being dealt!", ephemeral=True,
            )
            return
        # Refund all wagers (main + side bets)
        await queries.update_casino_balance(str(uid), seat.total_wager)
        del self.table.players[uid]

        if not self.table.players:
            await self._close(interaction, "All players left \u2014 table closed.")
            return

        self._update_buttons()
        await interaction.response.edit_message(
            embed=_table_embed(self.table), view=self,
        )

    # ── Row 2: Side Bets ─────────────────────────────────────

    @ui.button(
        label="Panda 8 (25:1)", style=discord.ButtonStyle.success,
        emoji="\U0001f43c", row=2,
    )
    async def panda_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        await self._handle_side_bet(interaction, "panda")

    @ui.button(
        label="Dragon 7 (40:1)", style=discord.ButtonStyle.success,
        emoji="\U0001f432", row=2,
    )
    async def dragon_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        await self._handle_side_bet(interaction, "dragon")

    # ── Row 3: New Hand / Close Table (post-finish) ─────────

    @ui.button(
        label="New Hand", style=discord.ButtonStyle.success,
        emoji="\U0001f504", row=3, disabled=True,
    )
    async def new_hand_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        shoe = self.cog._ensure_shoe()
        player_hand, banker_hand = _play_hand(shoe)

        self.table.player_hand = player_hand
        self.table.banker_hand = banker_hand
        self.table.player_revealed = 0
        self.table.banker_revealed = 0
        self.table.dealt = False
        self.table.players.clear()

        self._update_buttons()
        await interaction.response.edit_message(
            embed=_table_embed(self.table), view=self,
        )

    @ui.button(
        label="Close Table", style=discord.ButtonStyle.secondary,
        emoji="\u274c", row=3, disabled=True,
    )
    async def close_table_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        await self._close(interaction, "Table closed.")

    # ── Helpers ──────────────────────────────────────────────

    async def _handle_bet(
        self, interaction: discord.Interaction, bet_type: str,
    ) -> None:
        uid = interaction.user.id
        if self.table.dealt:
            await interaction.response.send_message(
                "Cards already being dealt!", ephemeral=True,
            )
            return
        if uid in self.table.players:
            await interaction.response.send_message(
                "You already have a bet!", ephemeral=True,
            )
            return
        if len(self.table.players) >= MAX_PLAYERS:
            await interaction.response.send_message(
                "Table is full!", ephemeral=True,
            )
            return
        bal = await queries.get_or_create_casino_wallet(str(uid))
        await interaction.response.send_modal(
            BetModal(self.table, bet_type, self, bal),
        )

    async def _handle_side_bet(
        self, interaction: discord.Interaction, side: str,
    ) -> None:
        uid = interaction.user.id
        if self.table.dealt:
            await interaction.response.send_message(
                "Cards already being dealt!", ephemeral=True,
            )
            return
        if uid not in self.table.players:
            await interaction.response.send_message(
                "Place a main bet first!", ephemeral=True,
            )
            return
        seat = self.table.players[uid]
        if side == "panda" and seat.panda_wager > 0:
            await interaction.response.send_message(
                "You already have a Panda 8 bet!", ephemeral=True,
            )
            return
        if side == "dragon" and seat.dragon_wager > 0:
            await interaction.response.send_message(
                "You already have a Dragon 7 bet!", ephemeral=True,
            )
            return
        bal = await queries.get_or_create_casino_wallet(str(interaction.user.id))
        await interaction.response.send_modal(
            SideBetModal(self.table, side, self, bal),
        )

    async def _finish(self, interaction: discord.Interaction) -> None:
        table = self.table
        _resolve_payouts(table)
        balances: dict[int, int] = {}

        for seat in table.players.values():
            total_payout = seat.payout + seat.panda_payout + seat.dragon_payout
            if total_payout > 0:
                balances[seat.user_id] = await queries.update_casino_balance(
                    str(seat.user_id), total_payout,
                )
            else:
                balances[seat.user_id] = (
                    await queries.get_casino_balance(str(seat.user_id))
                ) or 0
            await queries.log_casino_result(
                str(seat.user_id), "baccarat", seat.total_wager, total_payout,
            )

        embed = _table_embed(table, balances=balances)
        self._update_buttons()
        await interaction.response.edit_message(embed=embed, view=self)

    async def _close(self, interaction: discord.Interaction, reason: str) -> None:
        """Close table early (no cards dealt)."""
        embed = discord.Embed(
            title="Baccarat Table \u2014 Closed",
            description=reason,
            colour=discord.Colour.dark_grey(),
        )
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_tables.pop(self.table.channel_id, None)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        table = self.table
        self.active_tables.pop(table.channel_id, None)

        if table.all_revealed:
            # Hand was finished — just close the table quietly
            if table.message:
                try:
                    for child in self.children:
                        if hasattr(child, "disabled"):
                            child.disabled = True  # type: ignore[union-attr]
                    await table.message.edit(view=self)
                except Exception:
                    pass
            return

        # Hand still in progress — refund all wagers
        for seat in table.players.values():
            try:
                await queries.update_casino_balance(
                    str(seat.user_id), seat.total_wager,
                )
            except Exception:
                pass
        if table.message:
            try:
                embed = discord.Embed(
                    title="Baccarat Table \u2014 Timed Out",
                    description="Table inactive. All bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                pass


# ── Cog ──────────────────────────────────────────────────────────────────────


class BaccaratCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.shoe = _new_shoe()
        self.active_tables: dict[int, BaccaratTable] = {}

    def _ensure_shoe(self) -> list[str]:
        if len(self.shoe) < RESHUFFLE_THRESHOLD:
            self.shoe = _new_shoe()
        return self.shoe

    @app_commands.command(
        name="baccarat", description="Open a baccarat table (multiplayer)",
    )
    async def baccarat(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            existing = self.active_tables[channel_id]
            _has_running = any(
                (t := getattr(existing, n, None)) is not None and not t.done()
                for n in ("game_task", "race_task", "sim_task", "round_task", "_round_task", "trade_task", "fly_task", "_shot_clock_task", "_countdown_task")
            )
            if _has_running:
                await interaction.response.send_message(
                    "There's already a baccarat table in this channel!",
                    ephemeral=True,
                )
                return
            del self.active_tables[channel_id]

        await queries.get_or_create_casino_wallet(str(interaction.user.id))

        shoe = self._ensure_shoe()
        player_hand, banker_hand = _play_hand(shoe)

        table = BaccaratTable(
            channel_id=channel_id,
            opener_id=interaction.user.id,
            opener_name=interaction.user.display_name,
            player_hand=player_hand,
            banker_hand=banker_hand,
        )
        self.active_tables[channel_id] = table

        view = BaccaratTableView(table, self.active_tables, self)
        embed = _table_embed(table)
        await interaction.response.send_message(embed=embed, view=view)
        table.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BaccaratCog(bot))
