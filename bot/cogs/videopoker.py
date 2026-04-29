"""Video Poker (Jacks or Better) cog — multiplayer /videopoker table game."""
import random
from collections import Counter
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries

# ── Card helpers ──────────────────────────────────────────────────────────────

SUITS = ("♠", "♥", "♦", "♣")
RANKS = ("2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A")
RANK_VAL: dict[str, int] = {r: i for i, r in enumerate(RANKS, 2)}  # 2..14
MAX_PLAYERS = 5
POS_EMOJI = ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣")


def _new_deck() -> list[str]:
    cards = [f"{r}{s}" for s in SUITS for r in RANKS]
    random.shuffle(cards)
    return cards


def _fmt_card(card: str) -> str:
    return f"`{card}`"


def _fmt_hand(hand: list[str]) -> str:
    return " ".join(_fmt_card(c) for c in hand)


# ── 5-card poker hand evaluation ─────────────────────────────────────────────

_RANK_WORD: dict[int, str] = {
    2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven",
    8: "Eight", 9: "Nine", 10: "Ten", 11: "Jack", 12: "Queen", 13: "King", 14: "Ace",
}
_RANK_PLURAL: dict[int, str] = {
    2: "Twos", 3: "Threes", 4: "Fours", 5: "Fives", 6: "Sixes", 7: "Sevens",
    8: "Eights", 9: "Nines", 10: "Tens", 11: "Jacks", 12: "Queens", 13: "Kings", 14: "Aces",
}


def _evaluate_5(cards: list[str]) -> tuple[int, ...]:
    """Evaluate a 5-card hand. Returns a comparable tuple (higher = better).

    Tiers: 0=high card, 1=pair, 2=two pair, 3=trips, 4=straight,
           5=flush, 6=full house, 7=quads, 8=straight flush (non-royal),
           9=royal flush (straight flush with ace-high).
    """
    ranks = sorted([RANK_VAL[c[:-1]] for c in cards], reverse=True)
    suits = [c[-1] for c in cards]
    is_flush = len(set(suits)) == 1

    unique = sorted(set(ranks), reverse=True)
    is_straight = False
    high = ranks[0]

    if len(unique) == 5:
        if unique[0] - unique[4] == 4:
            is_straight = True
            high = unique[0]
        elif unique == [14, 5, 4, 3, 2]:  # ace-low straight
            is_straight = True
            high = 5

    counts = Counter(ranks)
    freq = sorted(counts.values(), reverse=True)

    if is_straight and is_flush:
        return (9, high) if high == 14 else (8, high)
    if freq == [4, 1]:
        quad = [r for r, c in counts.items() if c == 4][0]
        kick = [r for r, c in counts.items() if c == 1][0]
        return (7, quad, kick)
    if freq == [3, 2]:
        trip = [r for r, c in counts.items() if c == 3][0]
        pair = [r for r, c in counts.items() if c == 2][0]
        return (6, trip, pair)
    if is_flush:
        return (5,) + tuple(ranks)
    if is_straight:
        return (4, high)
    if freq == [3, 1, 1]:
        trip = [r for r, c in counts.items() if c == 3][0]
        kicks = sorted([r for r, c in counts.items() if c == 1], reverse=True)
        return (3, trip) + tuple(kicks)
    if freq == [2, 2, 1]:
        pairs = sorted([r for r, c in counts.items() if c == 2], reverse=True)
        kick = [r for r, c in counts.items() if c == 1][0]
        return (2, pairs[0], pairs[1], kick)
    if freq == [2, 1, 1, 1]:
        pair = [r for r, c in counts.items() if c == 2][0]
        kicks = sorted([r for r, c in counts.items() if c == 1], reverse=True)
        return (1, pair) + tuple(kicks)
    return (0,) + tuple(ranks)


def _hand_name(score: tuple[int, ...]) -> str:
    tier = score[0]
    if tier == 9:
        return "Royal Flush"
    if tier == 8:
        return f"Straight Flush ({_RANK_WORD[score[1]]}-high)"
    if tier == 7:
        return f"Four {_RANK_PLURAL[score[1]]}"
    if tier == 6:
        return f"Full House ({_RANK_PLURAL[score[1]]} full of {_RANK_PLURAL[score[2]]})"
    if tier == 5:
        return f"Flush ({_RANK_WORD[score[1]]}-high)"
    if tier == 4:
        return f"Straight ({_RANK_WORD[score[1]]}-high)"
    if tier == 3:
        return f"Three {_RANK_PLURAL[score[1]]}"
    if tier == 2:
        return f"Two Pair ({_RANK_PLURAL[score[1]]} and {_RANK_PLURAL[score[2]]})"
    if tier == 1:
        return f"Pair of {_RANK_PLURAL[score[1]]}"
    return f"{_RANK_WORD[score[1]]}-high"


# ── Jacks or Better paytable ─────────────────────────────────────────────────

# Maps hand tier → payout multiplier (on the bet).
# Tier 1 (pair) only pays if pair rank >= Jack (11).
_VP_MULTIPLIER: dict[int, int] = {
    9: 250,  # Royal Flush
    8: 50,   # Straight Flush
    7: 25,   # Four of a Kind
    6: 9,    # Full House
    5: 6,    # Flush
    4: 4,    # Straight
    3: 3,    # Three of a Kind
    2: 2,    # Two Pair
    1: 1,    # Jacks or Better (pair J/Q/K/A)
}

PAYTABLE_LINE = "RF 250x · SF 50x · 4K 25x · FH 9x · FL 6x · ST 4x · 3K 3x · 2P 2x · J+ 1x"


def _vp_payout(score: tuple[int, ...], bet: int) -> tuple[int, str]:
    """Return (total coins back, hand label). 0 coins = loss."""
    tier = score[0]
    if tier == 1:
        # Only Jacks or better qualifies
        pair_rank = score[1]
        if pair_rank < 11:  # below Jack
            return 0, f"Pair of {_RANK_PLURAL[pair_rank]} (no pay)"
    mult = _VP_MULTIPLIER.get(tier, 0)
    if mult == 0:
        return 0, _hand_name(score)
    payout = bet * mult + bet  # winnings + original bet back
    return payout, _hand_name(score)


# ── Game state ────────────────────────────────────────────────────────────────


@dataclass
class VPPlayer:
    user_id: int
    display_name: str
    bet: int
    deck: list[str] = field(default_factory=list)
    hand: list[str] = field(default_factory=list)
    held: set[int] = field(default_factory=set)  # indices 0-4
    drawn: bool = False
    payout: int = 0
    hand_name: str = ""


@dataclass
class VPTable:
    channel_id: int
    dealer_id: int
    dealer_name: str
    phase: str = "betting"  # betting | draw | finished
    players: dict[int, VPPlayer] = field(default_factory=dict)
    message: discord.Message | None = None
    round_num: int = 1
    last_bets: dict[int, tuple[str, int]] = field(default_factory=dict)

    def all_drawn(self) -> bool:
        return all(p.drawn for p in self.players.values())


# ── Embed ─────────────────────────────────────────────────────────────────────


def _table_embed(
    table: VPTable, *, balances: dict[int, int] | None = None,
) -> discord.Embed:
    phase = table.phase

    if phase == "betting":
        colour = discord.Colour.blurple()
        title = f"Video Poker — Place Your Bets (Round {table.round_num})"
    elif phase == "draw":
        colour = discord.Colour.blurple()
        title = f"Video Poker — Hold & Draw (Round {table.round_num})"
    else:
        colour = discord.Colour.gold()
        title = f"Video Poker — Round {table.round_num} Complete"

    embed = discord.Embed(title=title, colour=colour)
    embed.set_footer(text=f"Dealer: {table.dealer_name}")

    if phase == "betting":
        embed.description = f"**Jacks or Better**\n{PAYTABLE_LINE}\n\nJoin the table, then the dealer deals!"
    elif phase == "draw":
        embed.description = f"**Jacks or Better** | {PAYTABLE_LINE}\n\nToggle **Hold** on cards you want to keep, then click **Draw**."
    elif phase == "finished":
        embed.description = "Click **New Round** to continue or **Close Table** to end."

    if not table.players:
        if phase == "betting":
            embed.add_field(
                name="Players", value="*No players yet — click Join!*", inline=False,
            )
    else:
        lines: list[str] = []
        for p in table.players.values():
            if phase == "betting":
                lines.append(f"🃏 **{p.display_name}** — {p.bet}c")

            elif phase == "draw":
                # Show cards with position numbers and HOLD markers
                card_line = "  ".join(
                    f"{POS_EMOJI[i]} {_fmt_card(p.hand[i])}" for i in range(5)
                )
                hold_line = "  ".join(
                    " **HOLD** " if i in p.held else "       " for i in range(5)
                )
                status = "✅ Done" if p.drawn else "🟦 Choosing..."
                lines.append(
                    f"**{p.display_name}** ({p.bet}c) — {status}\n{card_line}\n{hold_line}"
                )

            else:  # finished
                cards = _fmt_hand(p.hand)
                net = p.payout - p.bet
                sign = "+" if net > 0 else ""
                bal = balances.get(p.user_id, 0) if balances else 0
                if p.payout > 0:
                    mult = (p.payout - p.bet) // p.bet if p.bet else 0
                    lines.append(
                        f"**{p.display_name}** ({p.bet}c): {cards}\n"
                        f"  {p.hand_name} → {mult}x → **{sign}{net}c** (bal: {bal}c)"
                    )
                else:
                    lines.append(
                        f"**{p.display_name}** ({p.bet}c): {cards}\n"
                        f"  {p.hand_name} → **{net}c** (bal: {bal}c)"
                    )

        if lines:
            embed.add_field(name="Players", value="\n".join(lines), inline=False)

    return embed


# ── Modal ─────────────────────────────────────────────────────────────────────


class JoinVPModal(ui.Modal):
    amount = ui.TextInput(
        label="Bet amount (coins)",
        placeholder="e.g. 50",
        required=True, max_length=10,
    )

    def __init__(self, table: VPTable, view: "VPTableView", balance: int) -> None:
        super().__init__(title="Join Video Poker")
        self.table = table
        self.table_view = view
        self.amount.placeholder = f"e.g. 50 (bal: {balance}c)"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            amt = int(self.amount.value)
        except ValueError:
            await interaction.response.send_message("Enter a whole number.", ephemeral=True)
            return
        if amt < 1:
            await interaction.response.send_message("Bet must be at least 1 coin.", ephemeral=True)
            return

        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message("You're already at the table!", ephemeral=True)
            return

        try:
            await queries.update_casino_balance(str(uid), -amt)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins! Need {amt}c (have {bal}c).", ephemeral=True,
            )
            return

        self.table.players[uid] = VPPlayer(
            user_id=uid,
            display_name=interaction.user.display_name,
            bet=amt,
        )
        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_table_embed(self.table), view=self.table_view,
        )


# ── View ──────────────────────────────────────────────────────────────────────


class VPTableView(ui.View):
    def __init__(
        self, table: VPTable, active_tables: dict[int, "VPTable"],
    ) -> None:
        super().__init__(timeout=180)
        self.table = table
        self.active_tables = active_tables
        self._update_buttons()

    def _update_buttons(self) -> None:
        phase = self.table.phase
        betting = phase == "betting"
        drawing = phase == "draw"
        finished = phase == "finished"

        # Row 0: Deal, Join, Re-bet, Leave
        self.deal_btn.disabled = not betting or not self.table.players
        self.join_btn.disabled = not betting
        self.rebet_btn.disabled = not betting or not self.table.last_bets
        self.leave_btn.disabled = drawing

        # Row 1: Hold 1-5
        self.hold1_btn.disabled = not drawing
        self.hold2_btn.disabled = not drawing
        self.hold3_btn.disabled = not drawing
        self.hold4_btn.disabled = not drawing
        self.hold5_btn.disabled = not drawing

        # Row 2: Draw, New Round, Close
        self.draw_btn.disabled = not drawing
        self.new_round_btn.disabled = not finished
        self.close_btn.disabled = drawing

    # ── Row 0: Deal / Join / Re-bet / Leave ──────────────────────

    @ui.button(label="Deal", style=discord.ButtonStyle.success, emoji="🃏", row=0)
    async def deal_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.table.dealer_id:
            await interaction.response.send_message("Only the table opener can deal!", ephemeral=True)
            return
        if self.table.phase != "betting":
            await interaction.response.send_message("Already dealt!", ephemeral=True)
            return
        if not self.table.players:
            await interaction.response.send_message("No players yet!", ephemeral=True)
            return
        await self._deal(interaction)

    @ui.button(label="Join", style=discord.ButtonStyle.primary, emoji="🪑", row=0)
    async def join_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message("Cards already dealt! Wait for the next round.", ephemeral=True)
            return
        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message("You're already at the table!", ephemeral=True)
            return
        if len(self.table.players) >= MAX_PLAYERS:
            await interaction.response.send_message("Table is full!", ephemeral=True)
            return
        bal = await queries.get_or_create_casino_wallet(str(uid))
        await interaction.response.send_modal(JoinVPModal(self.table, self, bal))

    @ui.button(label="Re-bet", style=discord.ButtonStyle.primary, emoji="🔄", row=0)
    async def rebet_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message("Cards already dealt!", ephemeral=True)
            return
        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message("You're already at the table!", ephemeral=True)
            return
        last = self.table.last_bets.get(uid)
        if last is None:
            await interaction.response.send_message("No previous bet — use Join instead.", ephemeral=True)
            return
        if len(self.table.players) >= MAX_PLAYERS:
            await interaction.response.send_message("Table is full!", ephemeral=True)
            return
        name, bet = last
        try:
            await queries.update_casino_balance(str(uid), -bet)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins for re-bet ({bet}c)! Have {bal}c.", ephemeral=True,
            )
            return
        self.table.players[uid] = VPPlayer(
            user_id=uid, display_name=name, bet=bet,
        )
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_table_embed(self.table), view=self,
        )

    @ui.button(label="Leave", style=discord.ButtonStyle.secondary, emoji="🚪", row=0)
    async def leave_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if player is None:
            await interaction.response.send_message("You're not at this table.", ephemeral=True)
            return

        if self.table.phase == "draw" and not player.drawn:
            await interaction.response.send_message(
                "Can't leave mid-hand! Draw first.", ephemeral=True,
            )
            return

        if uid == self.table.dealer_id and self.table.phase == "betting":
            await self._abort(interaction, "Dealer left — all bets refunded.")
            return

        if self.table.phase == "betting":
            await queries.update_casino_balance(str(uid), player.bet)
            del self.table.players[uid]
            self._update_buttons()
            await interaction.response.edit_message(
                embed=_table_embed(self.table), view=self,
            )
            return

        await interaction.response.send_message(
            "You'll see results when the round ends.", ephemeral=True,
        )

    # ── Row 1: Hold 1-5 ──────────────────────────────────────────

    async def _toggle_hold(self, interaction: discord.Interaction, idx: int) -> None:
        if self.table.phase != "draw":
            await interaction.response.send_message("Not in draw phase!", ephemeral=True)
            return
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if player is None:
            await interaction.response.send_message("You're not at this table!", ephemeral=True)
            return
        if player.drawn:
            await interaction.response.send_message("You already drew!", ephemeral=True)
            return

        if idx in player.held:
            player.held.discard(idx)
        else:
            player.held.add(idx)

        await interaction.response.edit_message(
            embed=_table_embed(self.table), view=self,
        )

    @ui.button(label="Hold 1", style=discord.ButtonStyle.secondary, row=1)
    async def hold1_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._toggle_hold(interaction, 0)

    @ui.button(label="Hold 2", style=discord.ButtonStyle.secondary, row=1)
    async def hold2_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._toggle_hold(interaction, 1)

    @ui.button(label="Hold 3", style=discord.ButtonStyle.secondary, row=1)
    async def hold3_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._toggle_hold(interaction, 2)

    @ui.button(label="Hold 4", style=discord.ButtonStyle.secondary, row=1)
    async def hold4_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._toggle_hold(interaction, 3)

    @ui.button(label="Hold 5", style=discord.ButtonStyle.secondary, row=1)
    async def hold5_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._toggle_hold(interaction, 4)

    # ── Row 2: Draw / New Round / Close ───────────────────────────

    @ui.button(label="Draw", style=discord.ButtonStyle.success, emoji="🎴", row=2)
    async def draw_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.table.phase != "draw":
            await interaction.response.send_message("Not in draw phase!", ephemeral=True)
            return
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if player is None:
            await interaction.response.send_message("You're not at this table!", ephemeral=True)
            return
        if player.drawn:
            await interaction.response.send_message("You already drew!", ephemeral=True)
            return

        # Replace non-held cards
        for i in range(5):
            if i not in player.held:
                player.hand[i] = player.deck.pop()
        player.drawn = True

        if self.table.all_drawn():
            await self._resolve(interaction)
        else:
            await interaction.response.edit_message(
                embed=_table_embed(self.table), view=self,
            )

    @ui.button(label="New Round", style=discord.ButtonStyle.success, emoji="▶️", row=2)
    async def new_round_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.table.dealer_id:
            await interaction.response.send_message("Only the table opener can start a new round!", ephemeral=True)
            return
        if self.table.phase != "finished":
            await interaction.response.send_message("Round still in progress!", ephemeral=True)
            return
        self._start_new_round()
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_table_embed(self.table), view=self,
        )

    @ui.button(label="Close Table", style=discord.ButtonStyle.danger, emoji="✖️", row=2)
    async def close_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.table.dealer_id:
            await interaction.response.send_message("Only the table opener can close!", ephemeral=True)
            return
        if self.table.phase == "draw":
            await interaction.response.send_message("Can't close mid-round!", ephemeral=True)
            return
        if self.table.phase == "betting":
            await self._abort(interaction, "Table closed by dealer. All bets refunded.")
        else:
            await self._close(interaction)

    # ── Deal logic ────────────────────────────────────────────────

    async def _deal(self, interaction: discord.Interaction) -> None:
        table = self.table
        table.phase = "draw"

        for p in table.players.values():
            p.deck = _new_deck()
            p.hand = [p.deck.pop() for _ in range(5)]

        self._update_buttons()
        await interaction.response.edit_message(
            embed=_table_embed(table), view=self,
        )

    # ── Resolve ───────────────────────────────────────────────────

    async def _resolve(self, interaction: discord.Interaction) -> None:
        table = self.table
        table.phase = "finished"
        balances: dict[int, int] = {}

        for p in table.players.values():
            score = _evaluate_5(p.hand)
            p.payout, p.hand_name = _vp_payout(score, p.bet)
            if p.payout > 0:
                balances[p.user_id] = await queries.update_casino_balance(
                    str(p.user_id), p.payout,
                )
            else:
                balances[p.user_id] = (
                    await queries.get_casino_balance(str(p.user_id))
                ) or 0
            await queries.log_casino_result(str(p.user_id), "videopoker", p.bet, p.payout)

        # Save last bets for re-bet
        for p in table.players.values():
            table.last_bets[p.user_id] = (p.display_name, p.bet)

        self._update_buttons()
        await interaction.response.edit_message(
            embed=_table_embed(table, balances=balances), view=self,
        )

    # ── Lifecycle ─────────────────────────────────────────────────

    def _start_new_round(self) -> None:
        self.table.players.clear()
        self.table.phase = "betting"
        self.table.round_num += 1

    async def _abort(self, interaction: discord.Interaction, reason: str) -> None:
        for p in self.table.players.values():
            if p.bet > 0:
                try:
                    await queries.update_casino_balance(str(p.user_id), p.bet)
                except Exception:
                    pass
        embed = discord.Embed(
            title="Video Poker — Closed",
            description=reason,
            colour=discord.Colour.dark_grey(),
        )
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_tables.pop(self.table.channel_id, None)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _close(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="Video Poker — Closed",
            description=f"Table closed after {self.table.round_num} round(s). Thanks for playing!",
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
        if table.phase == "finished":
            self.active_tables.pop(table.channel_id, None)
            if table.message:
                try:
                    embed = discord.Embed(
                        title="Video Poker — Timed Out",
                        description="Table timed out between rounds.",
                        colour=discord.Colour.dark_grey(),
                    )
                    await table.message.edit(embed=embed, view=None)
                except Exception:
                    pass
            return
        # Active phase — refund bets
        for p in table.players.values():
            if p.bet > 0:
                try:
                    await queries.update_casino_balance(str(p.user_id), p.bet)
                except Exception:
                    pass
        self.active_tables.pop(table.channel_id, None)
        if table.message:
            try:
                embed = discord.Embed(
                    title="Video Poker — Timed Out",
                    description="Table timed out. All bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                pass


# ── Cog ───────────────────────────────────────────────────────────────────────


class VideoPokerCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, VPTable] = {}

    @app_commands.command(
        name="videopoker",
        description="Open a Video Poker (Jacks or Better) table",
    )
    async def videopoker(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            existing = self.active_tables[channel_id]
            if getattr(existing, "phase", None) == "closed":
                del self.active_tables[channel_id]
            else:
                await interaction.response.send_message(
                    "There's already a Video Poker table in this channel! Use the buttons to join.",
                    ephemeral=True,
                )
                return

        await queries.get_or_create_casino_wallet(str(interaction.user.id))

        table = VPTable(
            channel_id=channel_id,
            dealer_id=interaction.user.id,
            dealer_name=interaction.user.display_name,
        )
        self.active_tables[channel_id] = table

        view = VPTableView(table, self.active_tables)
        embed = _table_embed(table)
        await interaction.response.send_message(embed=embed, view=view)
        table.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VideoPokerCog(bot))
