"""Mini-game engine for 1v1 duels and tournaments.

Nine quick games playable between two players. Each game class exposes a
uniform interface so the duel/tournament system can pick and run them
generically.  Most games run best-of-3 rounds via ``_play_best_of_3``.

Interface per game:
    name: str           -- display name
    emoji: str          -- emoji for display
    stakes: int         -- coins transferred to winner (200 luck, 300 skill)
    async def play(message, p1_id, p1_name, p2_id, p2_name) -> int
        Plays the game by editing *message* with embeds/views.
        Returns the winner's user_id, or 0 for a tie.
"""

import asyncio
import random
from typing import Protocol

import discord
from discord import ui


# ── Protocol ────────────────────────────────────────────────────────────────


class MiniGame(Protocol):
    name: str
    emoji: str
    stakes: int
    elo_key: str

    async def play(
        self,
        message: discord.Message,
        p1_id: int,
        p1_name: str,
        p2_id: int,
        p2_name: str,
    ) -> int: ...


# ── Card Utilities ──────────────────────────────────────────────────────────

SUITS = ["♠", "♥", "♦", "♣"]  # Spades > Hearts > Diamonds > Clubs
SUIT_RANK = {s: i for i, s in enumerate(reversed(SUITS))}  # ♠=3, ♥=2, ♦=1, ♣=0
VALUES = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
VALUE_RANK = {v: i for i, v in enumerate(VALUES)}  # 2=0 .. A=12

DECK = [(v, s) for s in SUITS for v in VALUES]


def _card_str(value: str, suit: str) -> str:
    """Render a card like 'A♠'."""
    return f"**{value}{suit}**"


def _card_rank(value: str, suit: str) -> tuple[int, int]:
    """Return (value_rank, suit_rank) for comparison."""
    return (VALUE_RANK[value], SUIT_RANK[suit])


def _bj_value(value: str) -> int:
    """Blackjack face value of a single card (A=11, J/Q/K=10)."""
    if value == "A":
        return 11
    if value in ("J", "Q", "K"):
        return 10
    return int(value)


def _bj_hand_total(cards: list[tuple[str, str]]) -> int:
    """Best blackjack hand total. Aces drop from 11 to 1 to avoid bust."""
    total = sum(_bj_value(v) for v, _ in cards)
    aces = sum(1 for v, _ in cards if v == "A")
    while total > 21 and aces > 0:
        total -= 10
        aces -= 1
    return total


def _bj_hand_display(cards: list[tuple[str, str]]) -> str:
    """Render a blackjack hand: cards + total."""
    card_strs = " ".join(_card_str(v, s) for v, s in cards)
    total = _bj_hand_total(cards)
    bust = " (BUST)" if total > 21 else ""
    return f"{card_strs} = **{total}**{bust}"


# ── Dice Utilities ──────────────────────────────────────────────────────────

DICE_EMOJI = {1: "⚀", 2: "⚁", 3: "⚂", 4: "⚃", 5: "⚄", 6: "⚅"}


def _roll_2d6() -> tuple[int, int]:
    return (random.randint(1, 6), random.randint(1, 6))


def _dice_display(d1: int, d2: int) -> str:
    return f"{DICE_EMOJI[d1]} {DICE_EMOJI[d2]}  =  **{d1 + d2}**"


# ── Best-of-3 Helper ──────────────────────────────────────────────────────


async def _play_best_of_3(
    message: discord.Message,
    p1_id: int,
    p1_name: str,
    p2_id: int,
    p2_name: str,
    game_emoji: str,
    game_name: str,
    round_fn,
) -> int:
    """Run a best-of-3 series.

    *round_fn(message, round_num)* plays one round and returns the winner's
    user-id (or 0 for a draw).  First to 2 round-wins takes the series.
    """
    p1w = p2w = 0
    for rnd in range(1, 4):
        if p1w == 2 or p2w == 2:
            break

        score = f"**{p1_name}** {p1w} \u2014 {p2w} **{p2_name}**"
        embed = discord.Embed(
            title=f"{game_emoji} {game_name} \u2014 Round {rnd}/3",
            description=score,
            colour=discord.Colour.blue(),
        )
        await message.edit(embed=embed, view=None)
        await asyncio.sleep(1.5)

        winner = await round_fn(message, rnd)
        if winner == p1_id:
            p1w += 1
        elif winner == p2_id:
            p2w += 1

        # Let the round result stay visible briefly
        await asyncio.sleep(2)

    # Determine series winner
    if p1w > p2w:
        overall = p1_id
        desc = f"**{p1_name}** wins the series **{p1w}\u2013{p2w}**!"
    elif p2w > p1w:
        overall = p2_id
        desc = f"**{p2_name}** wins the series **{p2w}\u2013{p1w}**!"
    else:
        overall = 0
        desc = f"Series tied **{p1w}\u2013{p2w}**!"

    colour = discord.Colour.gold() if overall else discord.Colour.greyple()
    embed = discord.Embed(
        title=f"{game_emoji} {game_name} \u2014 Final",
        description=desc,
        colour=colour,
    )
    await message.edit(embed=embed, view=None)
    return overall


# ── Shared Game Logic (imported by standalone cogs for feature parity) ─────


class TTTBoard:
    """Pure tic-tac-toe state. No Discord dependency — used by both the
    minigame and the standalone /tictactoe cog."""

    WIN_LINES = [
        (0, 1, 2), (3, 4, 5), (6, 7, 8),
        (0, 3, 6), (1, 4, 7), (2, 5, 8),
        (0, 4, 8), (2, 4, 6),
    ]
    EMPTY = "\u2b1c"
    X = "\u274c"
    O = "\u2b55"

    def __init__(self) -> None:
        self.cells: list[int] = [0] * 9  # 0=empty, 1=X, 2=O

    def place(self, cell: int, player: int) -> bool:
        """Place a mark (1 or 2). Returns False if cell is taken."""
        if self.cells[cell] != 0:
            return False
        self.cells[cell] = player
        return True

    def check_winner(self) -> int:
        """Return 1 (X wins), 2 (O wins), -1 (draw if full), 0 (ongoing)."""
        for a, b, c in self.WIN_LINES:
            if self.cells[a] == self.cells[b] == self.cells[c] != 0:
                return self.cells[a]
        if all(c != 0 for c in self.cells):
            return -1
        return 0

    def render(self) -> str:
        """Render the board as emoji text."""
        symbols = {0: self.EMPTY, 1: self.X, 2: self.O}
        rows = []
        for r in range(3):
            rows.append("".join(symbols[self.cells[r * 3 + c]] for c in range(3)))
        return "\n".join(rows)


class RPSLogic:
    """Pure rock-paper-scissors resolution. Used by both the minigame and
    the standalone /rps cog."""

    CHOICES = ("rock", "paper", "scissors")
    EMOJI = {"rock": "\u270a", "paper": "\u270b", "scissors": "\u270c\ufe0f"}
    BEATS = {"rock": "scissors", "scissors": "paper", "paper": "rock"}

    @staticmethod
    def winner(a: str, b: str) -> str:
        """Return 'a', 'b', or 'tie'."""
        if a == b:
            return "tie"
        if RPSLogic.BEATS[a] == b:
            return "a"
        return "b"


# ── 1. HigherCard ───────────────────────────────────────────────────────────


class _DrawCardView(ui.View):
    """Each player clicks their own 'Draw Card' button to reveal."""

    def __init__(
        self,
        p1_id: int,
        p1_name: str,
        p2_id: int,
        p2_name: str,
        c1: tuple[str, str],
        c2: tuple[str, str],
    ) -> None:
        super().__init__(timeout=20)
        self.p1_id = p1_id
        self.p2_id = p2_id
        self.p1_name = p1_name
        self.p2_name = p2_name
        self.c1 = c1
        self.c2 = c2
        self.drawn: dict[int, tuple[str, str]] = {}
        self.done = asyncio.Event()

        btn1 = ui.Button(
            label=f"{p1_name}: Draw Card",
            emoji="🎴",
            style=discord.ButtonStyle.primary,
            custom_id="draw_p1",
            row=0,
        )
        btn1.callback = self._make_draw_callback(p1_id, c1)
        self.add_item(btn1)

        btn2 = ui.Button(
            label=f"{p2_name}: Draw Card",
            emoji="🎴",
            style=discord.ButtonStyle.primary,
            custom_id="draw_p2",
            row=0,
        )
        btn2.callback = self._make_draw_callback(p2_id, c2)
        self.add_item(btn2)

    def _make_draw_callback(self, player_id: int, card: tuple[str, str]):
        async def callback(interaction: discord.Interaction) -> None:
            uid = interaction.user.id
            if uid not in (self.p1_id, self.p2_id):
                await interaction.response.send_message(
                    "You're not in this duel!", ephemeral=True,
                )
                return
            if uid != player_id:
                await interaction.response.send_message(
                    "That's not your button!", ephemeral=True,
                )
                return
            if uid in self.drawn:
                await interaction.response.send_message(
                    f"You already drew {_card_str(*self.drawn[uid])}!", ephemeral=True,
                )
                return
            self.drawn[uid] = card
            await interaction.response.send_message(
                f"You drew {_card_str(*card)}!", ephemeral=True,
            )
            # Update the embed to reveal this player's card
            if self.p1_id in self.drawn and self.p2_id in self.drawn:
                self.done.set()
                self.stop()

        return callback


class HigherCard:
    name = "Higher Card"
    emoji = "🃏"
    stakes = 200
    elo_key = "higher_card"

    async def play(
        self,
        message: discord.Message,
        p1_id: int,
        p1_name: str,
        p2_id: int,
        p2_name: str,
    ) -> int:
        async def _round(msg: discord.Message, _rnd: int) -> int:
            deck = list(DECK)
            random.shuffle(deck)
            c1 = deck[0]
            c2 = deck[1]

            view = _DrawCardView(p1_id, p1_name, p2_id, p2_name, c1, c2)

            embed = discord.Embed(
                title=f"{self.emoji} Higher Card",
                description="Both players: draw your card!",
                colour=discord.Colour.blue(),
            )
            embed.add_field(name=p1_name, value="🎴", inline=True)
            embed.add_field(name="vs", value="\u200b", inline=True)
            embed.add_field(name=p2_name, value="🎴", inline=True)
            await msg.edit(embed=embed, view=view)

            while not view.done.is_set():
                try:
                    await asyncio.wait_for(view.done.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass

                p1_val = _card_str(*c1) if p1_id in view.drawn else "🎴"
                p2_val = _card_str(*c2) if p2_id in view.drawn else "🎴"

                drawn_count = len(view.drawn)
                if drawn_count == 1:
                    embed = discord.Embed(
                        title=f"{self.emoji} Higher Card",
                        description="Waiting for the other player to draw...",
                        colour=discord.Colour.blue(),
                    )
                    embed.add_field(name=p1_name, value=p1_val, inline=True)
                    embed.add_field(name="vs", value="\u200b", inline=True)
                    embed.add_field(name=p2_name, value=p2_val, inline=True)
                    await msg.edit(embed=embed, view=view)
                elif drawn_count == 2:
                    break

                if not view.is_finished():
                    continue
                else:
                    break

            view.stop()

            if p1_id not in view.drawn:
                view.drawn[p1_id] = c1
            if p2_id not in view.drawn:
                view.drawn[p2_id] = c2

            r1 = _card_rank(*c1)
            r2 = _card_rank(*c2)

            if r1 > r2:
                winner_id, winner_name = p1_id, p1_name
            elif r2 > r1:
                winner_id, winner_name = p2_id, p2_name
            else:
                winner_id, winner_name = 0, "Nobody"

            result_colour = discord.Colour.gold() if winner_id else discord.Colour.greyple()
            embed = discord.Embed(
                title=f"{self.emoji} Higher Card",
                colour=result_colour,
            )
            embed.add_field(name=p1_name, value=_card_str(*c1), inline=True)
            embed.add_field(name="vs", value="\u200b", inline=True)
            embed.add_field(name=p2_name, value=_card_str(*c2), inline=True)

            if winner_id:
                winning_card = c1 if winner_id == p1_id else c2
                embed.description = f"**{winner_name}** wins with {_card_str(*winning_card)}!"
            else:
                embed.description = "It's a tie!"

            await msg.edit(embed=embed, view=None)
            return winner_id

        return await _play_best_of_3(
            message, p1_id, p1_name, p2_id, p2_name,
            self.emoji, self.name, _round,
        )


# ── 2. DiceRoll ─────────────────────────────────────────────────────────────


class _DiceOneView(ui.View):
    """Turn-based dice: each player rolls one die at a time."""

    def __init__(
        self,
        p1_id: int,
        p2_id: int,
        p1_name: str,
        p2_name: str,
    ) -> None:
        super().__init__(timeout=30)
        self.p1_id = p1_id
        self.p2_id = p2_id
        self.p1_name = p1_name
        self.p2_name = p2_name
        self.turn = p1_id
        self.p1_dice: list[int] = []
        self.p2_dice: list[int] = []
        self.done = asyncio.Event()

    def _player_dice(self, uid: int) -> list[int]:
        return self.p1_dice if uid == self.p1_id else self.p2_dice

    def _dice_field(self, dice: list[int]) -> str:
        if not dice:
            return "\u2b1c \u2b1c"
        parts = [DICE_EMOJI[d] for d in dice]
        if len(parts) < 2:
            parts.append("\u2b1c")
        text = " ".join(parts)
        if len(dice) == 2:
            text += f"  =  **{sum(dice)}**"
        return text

    def _make_embed(self, status: str) -> discord.Embed:
        embed = discord.Embed(
            title="\U0001f3b2 Dice Roll",
            description=status,
            colour=discord.Colour.orange(),
        )
        embed.add_field(name=self.p1_name, value=self._dice_field(self.p1_dice), inline=True)
        embed.add_field(name="vs", value="\u200b", inline=True)
        embed.add_field(name=self.p2_name, value=self._dice_field(self.p2_dice), inline=True)
        return embed

    @ui.button(label="Roll!", emoji="\U0001f3b2", style=discord.ButtonStyle.primary)
    async def roll_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        if uid not in (self.p1_id, self.p2_id):
            await interaction.response.send_message(
                "You're not in this duel!", ephemeral=True,
            )
            return
        if uid != self.turn:
            turn_name = self.p1_name if self.turn == self.p1_id else self.p2_name
            await interaction.response.send_message(
                f"It's **{turn_name}**'s turn!", ephemeral=True,
            )
            return

        dice = self._player_dice(uid)
        if len(dice) >= 2:
            await interaction.response.send_message(
                "You already rolled both dice!", ephemeral=True,
            )
            return

        roll = random.randint(1, 6)
        dice.append(roll)
        name = self.p1_name if uid == self.p1_id else self.p2_name

        if len(dice) == 2:
            # Player done rolling — switch turn or finish
            if uid == self.p1_id:
                self.turn = self.p2_id
                next_name = self.p2_name
                status = f"**{name}** rolled {DICE_EMOJI[roll]}! Total: **{sum(dice)}**\n**{next_name}**, your turn!"
            else:
                status = f"**{name}** rolled {DICE_EMOJI[roll]}! Total: **{sum(dice)}**"
                self.done.set()
                self.stop()
        else:
            status = f"**{name}** rolled {DICE_EMOJI[roll]}! Roll again for your second die."

        embed = self._make_embed(status)
        if self.done.is_set():
            await interaction.response.edit_message(embed=embed, view=None)
        else:
            await interaction.response.edit_message(embed=embed, view=self)


class DiceRoll:
    name = "Dice Roll"
    emoji = "\U0001f3b2"
    stakes = 200
    elo_key = "dice_roll"

    async def play(
        self,
        message: discord.Message,
        p1_id: int,
        p1_name: str,
        p2_id: int,
        p2_name: str,
    ) -> int:
        max_rounds = 3

        for attempt in range(max_rounds):
            round_label = f" (Reroll {attempt})" if attempt > 0 else ""

            view = _DiceOneView(p1_id, p2_id, p1_name, p2_name)
            status = f"**{p1_name}**, click Roll!{round_label}"
            embed = view._make_embed(status)
            await message.edit(embed=embed, view=view)

            try:
                await asyncio.wait_for(view.done.wait(), timeout=30)
            except asyncio.TimeoutError:
                view.stop()

            # Auto-roll for anyone who didn't finish
            while len(view.p1_dice) < 2:
                view.p1_dice.append(random.randint(1, 6))
            while len(view.p2_dice) < 2:
                view.p2_dice.append(random.randint(1, 6))

            t1 = sum(view.p1_dice)
            t2 = sum(view.p2_dice)

            if t1 > t2:
                winner_id, winner_name = p1_id, p1_name
            elif t2 > t1:
                winner_id, winner_name = p2_id, p2_name
            else:
                winner_id = 0

            if winner_id or attempt == max_rounds - 1:
                if winner_id:
                    desc = f"**{winner_name}** wins!"
                else:
                    desc = "Tied after 3 rolls \u2014 it's a draw!"

                embed = view._make_embed(desc)
                embed.colour = discord.Colour.gold() if winner_id else discord.Colour.greyple()
                await message.edit(embed=embed, view=None)
                return winner_id

            # Tie — reroll
            embed = view._make_embed("Tied! Rerolling...")
            await message.edit(embed=embed, view=None)
            await asyncio.sleep(1.5)

        return 0


# ── 3. SpeedMath ────────────────────────────────────────────────────────────


def _generate_math_problem() -> tuple[str, int]:
    """Generate a problem with two 2-digit numbers and +/-/*. Returns (display, answer)."""
    a = random.randint(10, 99)
    b = random.randint(10, 99)
    op = random.choice(["+", "-", "\u00d7"])

    if op == "+":
        answer = a + b
    elif op == "-":
        # Ensure non-negative result for cleaner UX
        if a < b:
            a, b = b, a
        answer = a - b
    else:  # multiply
        # Use smaller numbers for multiplication to keep it reasonable
        a = random.randint(10, 49)
        b = random.randint(2, 19)
        answer = a * b

    display = f"{a} {op} {b}"
    return display, answer


class _MathModal(ui.Modal):
    answer_input = ui.TextInput(
        label="Your answer",
        placeholder="Enter a number",
        required=True,
        max_length=10,
    )

    def __init__(self, p1_id: int, p2_id: int, correct: int, done: asyncio.Event) -> None:
        super().__init__(title="Speed Math")
        self.p1_id = p1_id
        self.p2_id = p2_id
        self.correct = correct
        self._done = done
        self.result: tuple[int, bool] | None = None  # (user_id, is_correct)
        self._resolved = False

    async def on_submit(self, interaction: discord.Interaction) -> None:
        uid = interaction.user.id
        if uid not in (self.p1_id, self.p2_id):
            await interaction.response.send_message("You're not in this duel!", ephemeral=True)
            return
        if self._resolved:
            await interaction.response.send_message("Already answered!", ephemeral=True)
            return

        try:
            user_answer = int(self.answer_input.value.strip())
        except ValueError:
            await interaction.response.send_message("That's not a valid number!", ephemeral=True)
            return

        self._resolved = True
        is_correct = user_answer == self.correct
        self.result = (uid, is_correct)

        if is_correct:
            await interaction.response.send_message("Correct!", ephemeral=True)
        else:
            await interaction.response.send_message(
                f"Wrong! You answered {user_answer}.", ephemeral=True,
            )
        self._done.set()


class _MathView(ui.View):
    """A single button that opens the answer modal."""

    def __init__(self, p1_id: int, p2_id: int, correct: int) -> None:
        super().__init__(timeout=20)
        self.p1_id = p1_id
        self.p2_id = p2_id
        self.correct = correct
        self.done = asyncio.Event()
        self._modal = _MathModal(p1_id, p2_id, correct, self.done)

    @property
    def result(self) -> tuple[int, bool] | None:
        return self._modal.result

    @ui.button(label="Submit Answer", emoji="🧮", style=discord.ButtonStyle.success)
    async def submit(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        if uid not in (self.p1_id, self.p2_id):
            await interaction.response.send_message("You're not in this duel!", ephemeral=True)
            return
        if self._modal._resolved:
            await interaction.response.send_message("Already answered!", ephemeral=True)
            return
        # Each click creates a fresh modal instance sharing state
        modal = _MathModal(self.p1_id, self.p2_id, self.correct, self.done)
        modal.result = self._modal.result
        modal._resolved = self._modal._resolved
        # Link back so the result propagates
        old_modal = self._modal
        self._modal = modal
        original_on_submit = modal.on_submit

        async def _linked_submit(inter: discord.Interaction) -> None:
            await original_on_submit(inter)
            old_modal.result = modal.result
            old_modal._resolved = modal._resolved
            self._modal = old_modal
            self._modal.result = modal.result
            self._modal._resolved = modal._resolved

        modal.on_submit = _linked_submit  # type: ignore[assignment]
        await interaction.response.send_modal(modal)


class SpeedMath:
    name = "Speed Math"
    emoji = "🧮"
    stakes = 300
    elo_key = "speed_math"

    async def play(
        self,
        message: discord.Message,
        p1_id: int,
        p1_name: str,
        p2_id: int,
        p2_name: str,
    ) -> int:
        async def _round(msg: discord.Message, _rnd: int) -> int:
            problem, answer = _generate_math_problem()

            embed = discord.Embed(
                title=f"{self.emoji} Speed Math",
                description=(
                    f"**{p1_name}** vs **{p2_name}**\n\n"
                    f"## {problem} = ?\n\n"
                    "First correct answer wins! Wrong answer loses."
                ),
                colour=discord.Colour.green(),
            )

            view = _MathView(p1_id, p2_id, answer)
            await msg.edit(embed=embed, view=view)

            try:
                await asyncio.wait_for(view.done.wait(), timeout=20)
            except asyncio.TimeoutError:
                view.stop()

            result = view.result
            if result is None:
                embed = discord.Embed(
                    title=f"{self.emoji} Speed Math",
                    description=(
                        f"**{problem} = {answer}**\n\n"
                        "Time's up! Neither player answered \u2014 draw."
                    ),
                    colour=discord.Colour.greyple(),
                )
                await msg.edit(embed=embed, view=None)
                return 0

            uid, is_correct = result
            responder_name = p1_name if uid == p1_id else p2_name
            other_id = p2_id if uid == p1_id else p1_id
            other_name = p2_name if uid == p1_id else p1_name

            if is_correct:
                winner_id = uid
                desc = (
                    f"**{problem} = {answer}**\n\n"
                    f"**{responder_name}** answered correctly first!"
                )
            else:
                winner_id = other_id
                desc = (
                    f"**{problem} = {answer}**\n\n"
                    f"**{responder_name}** answered wrong \u2014 **{other_name}** takes it!"
                )

            embed = discord.Embed(
                title=f"{self.emoji} Speed Math",
                description=desc,
                colour=discord.Colour.gold(),
            )
            await msg.edit(embed=embed, view=None)
            return winner_id

        return await _play_best_of_3(
            message, p1_id, p1_name, p2_id, p2_name,
            self.emoji, self.name, _round,
        )


# ── 4. Trivia ───────────────────────────────────────────────────────────────

# Pull question data from existing game cogs (no duplication)
from bot.cogs.geography import CAPITALS, US_STATE_CAPITALS
from bot.cogs.roster import (
    NBA_PLAYERS, NBA_TEAMS,
    NFL_PLAYERS, NFL_TEAMS,
)


def _generate_trivia() -> tuple[str, list[str], int]:
    """Generate a random trivia question from geography or sports data.

    Returns (question, [4 options], correct_index).
    """
    pool = ["geo_country", "geo_state", "nba"]
    if NFL_PLAYERS:  # populated from ESPN at startup
        pool.append("nfl")
    category = random.choice(pool)

    if category == "geo_country":
        country = random.choice(list(CAPITALS.keys()))
        correct = CAPITALS[country][0]
        wrong_pool = [v[0] for k, v in CAPITALS.items() if k != country]
        wrongs = random.sample(wrong_pool, min(3, len(wrong_pool)))
        question = f"What is the capital of **{country}**?"

    elif category == "geo_state":
        state = random.choice(list(US_STATE_CAPITALS.keys()))
        correct = US_STATE_CAPITALS[state][0]
        wrong_pool = [v[0] for k, v in US_STATE_CAPITALS.items() if k != state]
        wrongs = random.sample(wrong_pool, min(3, len(wrong_pool)))
        question = f"What is the capital of **{state}**?"

    elif category == "nba":
        player = random.choice(list(NBA_PLAYERS.keys()))
        _pos, team_key = NBA_PLAYERS[player]
        correct = team_key
        wrong_pool = [k for k in NBA_TEAMS if k != team_key]
        wrongs = random.sample(wrong_pool, min(3, len(wrong_pool)))
        question = f"Which NBA team does **{player}** play for?"

    else:  # nfl
        player = random.choice(list(NFL_PLAYERS.keys()))
        _pos, team_key = NFL_PLAYERS[player]
        correct = team_key
        wrong_pool = [k for k in NFL_TEAMS if k != team_key]
        wrongs = random.sample(wrong_pool, min(3, len(wrong_pool)))
        question = f"Which NFL team does **{player}** play for?"

    options = wrongs + [correct]
    random.shuffle(options)
    correct_index = options.index(correct)
    return question, options, correct_index


OPTION_LABELS = ["A", "B", "C", "D"]


class _TriviaView(ui.View):
    def __init__(
        self, p1_id: int, p2_id: int, correct_index: int, options: list[str],
    ) -> None:
        super().__init__(timeout=15)
        self.p1_id = p1_id
        self.p2_id = p2_id
        self.correct_index = correct_index
        self.done = asyncio.Event()
        self.result: tuple[int, int] | None = None  # (user_id, chosen_index)
        self._answered = False

        for i, option in enumerate(options):
            button = ui.Button(
                label=f"{OPTION_LABELS[i]}: {option}",
                style=discord.ButtonStyle.primary,
                custom_id=f"trivia_{i}",
                row=i // 2,
            )
            button.callback = self._make_callback(i)
            self.add_item(button)

    def _make_callback(self, index: int):
        async def callback(interaction: discord.Interaction) -> None:
            uid = interaction.user.id
            if uid not in (self.p1_id, self.p2_id):
                await interaction.response.send_message(
                    "You're not in this duel!", ephemeral=True,
                )
                return
            if self._answered:
                await interaction.response.send_message(
                    "Someone already answered!", ephemeral=True,
                )
                return
            self._answered = True
            self.result = (uid, index)
            is_correct = index == self.correct_index
            label = OPTION_LABELS[index]
            if is_correct:
                await interaction.response.send_message(
                    f"You picked **{label}** -- Correct!", ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    f"You picked **{label}** -- Wrong!", ephemeral=True,
                )
            self.done.set()
            self.stop()

        return callback


class Trivia:
    name = "Trivia"
    emoji = "🧠"
    stakes = 300
    elo_key = "trivia"

    async def play(
        self,
        message: discord.Message,
        p1_id: int,
        p1_name: str,
        p2_id: int,
        p2_name: str,
    ) -> int:
        async def _round(msg: discord.Message, _rnd: int) -> int:
            question, options, correct_index = _generate_trivia()

            embed = discord.Embed(
                title=f"{self.emoji} Trivia",
                description=(
                    f"**{p1_name}** vs **{p2_name}**\n\n"
                    f"**{question}**\n\n"
                    "First correct answer wins! Wrong answer loses."
                ),
                colour=discord.Colour.teal(),
            )

            view = _TriviaView(p1_id, p2_id, correct_index, options)
            await msg.edit(embed=embed, view=view)

            try:
                await asyncio.wait_for(view.done.wait(), timeout=15)
            except asyncio.TimeoutError:
                view.stop()

            correct_label = f"**{OPTION_LABELS[correct_index]}: {options[correct_index]}**"

            if view.result is None:
                embed = discord.Embed(
                    title=f"{self.emoji} Trivia",
                    description=(
                        f"**{question}**\n\n"
                        f"Answer: {correct_label}\n\n"
                        "Time's up! Neither player answered \u2014 draw."
                    ),
                    colour=discord.Colour.greyple(),
                )
                await msg.edit(embed=embed, view=None)
                return 0

            uid, chosen_index = view.result
            responder_name = p1_name if uid == p1_id else p2_name
            other_id = p2_id if uid == p1_id else p1_id
            other_name = p2_name if uid == p1_id else p1_name
            is_correct = chosen_index == correct_index
            chosen_label = f"{OPTION_LABELS[chosen_index]}: {options[chosen_index]}"

            if is_correct:
                winner_id = uid
                desc = (
                    f"**{question}**\n\n"
                    f"Answer: {correct_label}\n\n"
                    f"**{responder_name}** picked {chosen_label} \u2014 Correct!"
                )
            else:
                winner_id = other_id
                desc = (
                    f"**{question}**\n\n"
                    f"Answer: {correct_label}\n\n"
                    f"**{responder_name}** picked {chosen_label} \u2014 Wrong! **{other_name}** takes it!"
                )

            embed = discord.Embed(
                title=f"{self.emoji} Trivia",
                description=desc,
                colour=discord.Colour.gold(),
            )
            await msg.edit(embed=embed, view=None)
            return winner_id

        return await _play_best_of_3(
            message, p1_id, p1_name, p2_id, p2_name,
            self.emoji, self.name, _round,
        )


# ── 5. GuessTheNumber ──────────────────────────────────────────────────────


class _PickNumberModal(ui.Modal):
    number_input = ui.TextInput(
        label="Pick a number (1-20)",
        placeholder="1-20",
        required=True,
        max_length=2,
    )

    def __init__(self, picker_id: int, done: asyncio.Event) -> None:
        super().__init__(title="Pick a Number")
        self.picker_id = picker_id
        self._done = done
        self.number: int | None = None

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.picker_id:
            await interaction.response.send_message("Not your turn!", ephemeral=True)
            return
        try:
            n = int(self.number_input.value.strip())
        except ValueError:
            await interaction.response.send_message("Enter a valid number!", ephemeral=True)
            return
        if n < 1 or n > 20:
            await interaction.response.send_message("Must be 1\u201320!", ephemeral=True)
            return
        self.number = n
        await interaction.response.send_message(f"You picked **{n}**. Locked in!", ephemeral=True)
        self._done.set()


class _PickNumberView(ui.View):
    def __init__(self, picker_id: int) -> None:
        super().__init__(timeout=20)
        self.picker_id = picker_id
        self.done = asyncio.Event()
        self._modal = _PickNumberModal(picker_id, self.done)

    @property
    def number(self) -> int | None:
        return self._modal.number

    @ui.button(label="Pick Number", emoji="\U0001f512", style=discord.ButtonStyle.primary)
    async def pick(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.picker_id:
            await interaction.response.send_message("Not your turn to pick!", ephemeral=True)
            return
        if self._modal.number is not None:
            await interaction.response.send_message("Already picked!", ephemeral=True)
            return
        modal = _PickNumberModal(self.picker_id, self.done)
        old = self._modal
        self._modal = modal
        original = modal.on_submit

        async def _linked(inter: discord.Interaction) -> None:
            await original(inter)
            old.number = modal.number
            self._modal = old

        modal.on_submit = _linked  # type: ignore[assignment]
        await interaction.response.send_modal(modal)


class _GuessNumberModal(ui.Modal):
    guess_input = ui.TextInput(
        label="Guess (1-20)",
        placeholder="1-20",
        required=True,
        max_length=2,
    )

    def __init__(self, guesser_id: int, done: asyncio.Event) -> None:
        super().__init__(title="Guess the Number")
        self.guesser_id = guesser_id
        self._done = done
        self.guess: int | None = None

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.guesser_id:
            await interaction.response.send_message("Not your turn!", ephemeral=True)
            return
        try:
            g = int(self.guess_input.value.strip())
        except ValueError:
            await interaction.response.send_message("Enter a valid number!", ephemeral=True)
            return
        if g < 1 or g > 20:
            await interaction.response.send_message("Must be 1\u201320!", ephemeral=True)
            return
        self.guess = g
        await interaction.response.defer()
        self._done.set()


class _GuessNumberView(ui.View):
    def __init__(self, guesser_id: int) -> None:
        super().__init__(timeout=15)
        self.guesser_id = guesser_id
        self.done = asyncio.Event()
        self._modal = _GuessNumberModal(guesser_id, self.done)

    @property
    def guess(self) -> int | None:
        return self._modal.guess

    @ui.button(label="Guess", emoji="🔢", style=discord.ButtonStyle.success)
    async def guess_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.guesser_id:
            await interaction.response.send_message("Not your turn to guess!", ephemeral=True)
            return
        if self._modal.guess is not None:
            await interaction.response.send_message("Already guessed!", ephemeral=True)
            return
        modal = _GuessNumberModal(self.guesser_id, self.done)
        old = self._modal
        self._modal = modal
        original = modal.on_submit

        async def _linked(inter: discord.Interaction) -> None:
            await original(inter)
            old.guess = modal.guess
            self._modal = old

        modal.on_submit = _linked  # type: ignore[assignment]
        await interaction.response.send_modal(modal)


class GuessTheNumber:
    name = "Guess the Number"
    emoji = "🔢"
    stakes = 300
    elo_key = "guess_number"

    async def play(
        self,
        message: discord.Message,
        p1_id: int,
        p1_name: str,
        p2_id: int,
        p2_name: str,
    ) -> int:
        async def _round(msg: discord.Message, rnd: int) -> int:
            # Alternate picker: odd rounds P1 picks, even rounds P2 picks
            if rnd % 2 == 1:
                picker_id, picker_name = p1_id, p1_name
                guesser_id, guesser_name = p2_id, p2_name
            else:
                picker_id, picker_name = p2_id, p2_name
                guesser_id, guesser_name = p1_id, p1_name

            # Phase 1: Picker picks a number
            pick_view = _PickNumberView(picker_id)
            embed = discord.Embed(
                title=f"{self.emoji} Guess the Number",
                description=f"**{picker_name}**, pick a secret number (1\u201320)!",
                colour=discord.Colour.dark_green(),
            )
            await msg.edit(embed=embed, view=pick_view)

            try:
                await asyncio.wait_for(pick_view.done.wait(), timeout=20)
            except asyncio.TimeoutError:
                pick_view.stop()

            target = pick_view.number or random.randint(1, 20)

            # Phase 2: Guesser gets 3 attempts with higher/lower hints
            attempts_left = 3
            history: list[str] = []

            while attempts_left > 0:
                guess_view = _GuessNumberView(guesser_id)
                hist_text = "\n".join(history) if history else ""
                embed = discord.Embed(
                    title=f"{self.emoji} Guess the Number",
                    description=(
                        f"**{picker_name}** has picked!\n"
                        f"**{guesser_name}**, guess the number (1\u201320)!\n"
                        f"Attempts left: **{attempts_left}**\n\n"
                        f"{hist_text}"
                    ),
                    colour=discord.Colour.dark_green(),
                )
                await msg.edit(embed=embed, view=guess_view)

                try:
                    await asyncio.wait_for(guess_view.done.wait(), timeout=15)
                except asyncio.TimeoutError:
                    guess_view.stop()

                guess = guess_view.guess
                if guess is None:
                    attempts_left -= 1
                    history.append("*(timed out)*")
                    continue

                if guess == target:
                    embed = discord.Embed(
                        title=f"{self.emoji} Guess the Number",
                        description=(
                            f"**{guesser_name}** guessed **{guess}** \u2014 Correct!\n"
                            f"**{guesser_name}** wins this round!"
                        ),
                        colour=discord.Colour.gold(),
                    )
                    await msg.edit(embed=embed, view=None)
                    return guesser_id

                attempts_left -= 1
                hint = "Too high!" if guess > target else "Too low!"
                history.append(f"**{guess}** \u2014 {hint}")

            # Guesser failed
            embed = discord.Embed(
                title=f"{self.emoji} Guess the Number",
                description=(
                    f"Out of attempts! The number was **{target}**.\n"
                    f"**{picker_name}** wins this round!"
                ),
                colour=discord.Colour.gold(),
            )
            await msg.edit(embed=embed, view=None)
            return picker_id

        return await _play_best_of_3(
            message, p1_id, p1_name, p2_id, p2_name,
            self.emoji, self.name, _round,
        )


# ── 6. CoinFlip ─────────────────────────────────────────────────────────────


class _CoinCallView(ui.View):
    """The caller picks Heads or Tails."""

    def __init__(self, caller_id: int) -> None:
        super().__init__(timeout=15)
        self.caller_id = caller_id
        self.pick: str | None = None
        self.done = asyncio.Event()

    async def _handle_pick(self, interaction: discord.Interaction, pick: str) -> None:
        uid = interaction.user.id
        if uid != self.caller_id:
            await interaction.response.send_message(
                "It's not your call!", ephemeral=True,
            )
            return
        if self.pick is not None:
            await interaction.response.send_message(
                f"You already called {self.pick}!", ephemeral=True,
            )
            return
        self.pick = pick
        await interaction.response.send_message(
            f"You called **{pick}**!", ephemeral=True,
        )
        self.done.set()
        self.stop()

    @ui.button(label="Heads", emoji="\U0001fa99", style=discord.ButtonStyle.primary)
    async def heads(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._handle_pick(interaction, "Heads")

    @ui.button(label="Tails", emoji="\U0001fa99", style=discord.ButtonStyle.primary)
    async def tails(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._handle_pick(interaction, "Tails")


class CoinFlip:
    name = "Coin Flip"
    emoji = "\U0001fa99"
    stakes = 200
    elo_key = "coin_flip"

    async def play(
        self,
        message: discord.Message,
        p1_id: int,
        p1_name: str,
        p2_id: int,
        p2_name: str,
    ) -> int:
        async def _round(msg: discord.Message, rnd: int) -> int:
            # Alternate caller: odd rounds P1 calls, even rounds P2 calls
            if rnd % 2 == 1:
                caller_id, caller_name = p1_id, p1_name
                other_id, other_name = p2_id, p2_name
            else:
                caller_id, caller_name = p2_id, p2_name
                other_id, other_name = p1_id, p1_name

            view = _CoinCallView(caller_id)
            embed = discord.Embed(
                title=f"{self.emoji} Coin Flip",
                description=f"**{caller_name}**, call it! Heads or Tails?",
                colour=discord.Colour.blue(),
            )
            await msg.edit(embed=embed, view=view)

            try:
                await asyncio.wait_for(view.done.wait(), timeout=15)
            except asyncio.TimeoutError:
                view.stop()

            call = view.pick or random.choice(["Heads", "Tails"])

            # Flip animation
            embed = discord.Embed(
                title=f"{self.emoji} Coin Flip",
                description=f"**{caller_name}** calls **{call}**!\n\nFlipping...",
                colour=discord.Colour.yellow(),
            )
            await msg.edit(embed=embed, view=None)
            await asyncio.sleep(1.5)

            result = random.choice(["Heads", "Tails"])

            if call == result:
                winner_id = caller_id
                desc = f"It's **{result}**! **{caller_name}** called it right!"
            else:
                winner_id = other_id
                desc = (
                    f"It's **{result}**! {caller_name} called {call} "
                    f"\u2014 **{other_name}** takes it!"
                )

            embed = discord.Embed(
                title=f"{self.emoji} Coin Flip",
                description=desc,
                colour=discord.Colour.gold(),
            )
            await msg.edit(embed=embed, view=None)
            return winner_id

        return await _play_best_of_3(
            message, p1_id, p1_name, p2_id, p2_name,
            self.emoji, self.name, _round,
        )


# ── 7. TicTacToe ────────────────────────────────────────────────────────────

# Use shared TTTBoard for game logic — aliases for local readability
_TTT_EMPTY = TTTBoard.EMPTY
_TTT_X = TTTBoard.X
_TTT_O = TTTBoard.O


class _TicTacToeView(ui.View):
    """3x3 grid of buttons for Tic Tac Toe."""

    def __init__(
        self,
        p1_id: int,
        p2_id: int,
        p1_name: str,
        p2_name: str,
        message: discord.Message,
    ) -> None:
        super().__init__(timeout=None)  # Timeout managed per-turn externally
        self.p1_id = p1_id
        self.p2_id = p2_id
        self.p1_name = p1_name
        self.p2_name = p2_name
        self.message = message
        self.board: list[int] = [0] * 9  # 0=empty, 1=P1(X), 2=P2(O)
        self.current_player = 1  # 1 = P1's turn, 2 = P2's turn
        self.winner_id = 0
        self.game_over = False
        self.done = asyncio.Event()
        self.turn_event = asyncio.Event()  # Set each time a valid move is made

        # Create the 9 buttons
        for i in range(9):
            button = ui.Button(
                label="\u200b",
                style=discord.ButtonStyle.secondary,
                custom_id=f"ttt_{i}",
                row=i // 3,
            )
            button.callback = self._make_callback(i)
            self.add_item(button)

    def _make_callback(self, cell: int):
        async def callback(interaction: discord.Interaction) -> None:
            uid = interaction.user.id
            if uid not in (self.p1_id, self.p2_id):
                await interaction.response.send_message(
                    "You're not in this game!", ephemeral=True,
                )
                return
            if self.game_over:
                await interaction.response.send_message(
                    "Game is over!", ephemeral=True,
                )
                return

            expected_id = self.p1_id if self.current_player == 1 else self.p2_id
            if uid != expected_id:
                await interaction.response.send_message(
                    "It's not your turn!", ephemeral=True,
                )
                return
            if self.board[cell] != 0:
                await interaction.response.send_message(
                    "That cell is taken!", ephemeral=True,
                )
                return

            # Place the mark
            self.board[cell] = self.current_player
            self._update_buttons()

            # Check for win
            winner = self._check_winner()
            if winner:
                self.game_over = True
                self.winner_id = self.p1_id if winner == 1 else self.p2_id
                self.done.set()
                self.stop()
                winner_name = self.p1_name if winner == 1 else self.p2_name
                embed = self._make_embed(
                    f"**{winner_name}** wins!",
                    discord.Colour.gold(),
                )
                await interaction.response.edit_message(embed=embed, view=self)
                return

            # Check for draw (board full)
            if all(c != 0 for c in self.board):
                self.game_over = True
                self.winner_id = 0
                self.done.set()
                self.stop()
                embed = self._make_embed(
                    "Board full -- it's a draw!",
                    discord.Colour.greyple(),
                )
                await interaction.response.edit_message(embed=embed, view=self)
                return

            # Switch turns
            self.current_player = 2 if self.current_player == 1 else 1
            next_name = self.p1_name if self.current_player == 1 else self.p2_name
            next_mark = _TTT_X if self.current_player == 1 else _TTT_O
            embed = self._make_embed(
                f"**{next_name}**'s turn ({next_mark})",
                discord.Colour.blue(),
            )
            await interaction.response.edit_message(embed=embed, view=self)
            self.turn_event.set()
            self.turn_event = asyncio.Event()

        return callback

    def _update_buttons(self) -> None:
        """Update button labels/styles to reflect the board."""
        for i, item in enumerate(self.children):
            if not isinstance(item, ui.Button):
                continue
            cell_val = self.board[i]
            if cell_val == 1:
                item.label = _TTT_X
                item.style = discord.ButtonStyle.danger
                item.disabled = True
            elif cell_val == 2:
                item.label = _TTT_O
                item.style = discord.ButtonStyle.success
                item.disabled = True
            else:
                item.label = "\u200b"
                item.style = discord.ButtonStyle.secondary
                item.disabled = self.game_over

    def _check_winner(self) -> int:
        """Return 1 if P1 won, 2 if P2 won, 0 if no winner yet."""
        for a, b, c in TTTBoard.WIN_LINES:
            if self.board[a] == self.board[b] == self.board[c] != 0:
                return self.board[a]
        return 0

    def _make_embed(self, status: str, colour: discord.Colour) -> discord.Embed:
        embed = discord.Embed(
            title=f"\u274c\u2b55 Tic Tac Toe",
            description=(
                f"**{self.p1_name}** ({_TTT_X}) vs **{self.p2_name}** ({_TTT_O})\n\n"
                f"{status}"
            ),
            colour=colour,
        )
        # Show board as text for clarity
        rows = []
        for r in range(3):
            row_str = ""
            for c in range(3):
                val = self.board[r * 3 + c]
                if val == 1:
                    row_str += _TTT_X
                elif val == 2:
                    row_str += _TTT_O
                else:
                    row_str += _TTT_EMPTY
            rows.append(row_str)
        embed.add_field(name="Board", value="\n".join(rows), inline=False)
        return embed


class TicTacToe:
    name = "Tic Tac Toe"
    emoji = "\u274c"
    stakes = 300
    elo_key = "tictactoe"

    async def play(
        self,
        message: discord.Message,
        p1_id: int,
        p1_name: str,
        p2_id: int,
        p2_name: str,
    ) -> int:
        view = _TicTacToeView(p1_id, p2_id, p1_name, p2_name, message)

        embed = view._make_embed(
            f"**{p1_name}**'s turn ({_TTT_X})",
            discord.Colour.blue(),
        )
        await message.edit(embed=embed, view=view)

        # Run the game with a 15-second per-turn timeout
        while not view.game_over:
            current_name = p1_name if view.current_player == 1 else p2_name
            opponent_id = p2_id if view.current_player == 1 else p1_id
            opponent_name = p2_name if view.current_player == 1 else p1_name

            try:
                # Wait for either the game to end or a turn to be taken
                done_task = asyncio.ensure_future(view.done.wait())
                turn_task = asyncio.ensure_future(view.turn_event.wait())
                finished, pending = await asyncio.wait(
                    [done_task, turn_task],
                    timeout=15,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()

                if not finished:
                    # Timeout -- current player loses
                    view.game_over = True
                    view.winner_id = opponent_id
                    view.stop()
                    embed = view._make_embed(
                        f"**{current_name}** ran out of time! **{opponent_name}** wins!",
                        discord.Colour.gold(),
                    )
                    view._update_buttons()
                    # Disable all remaining buttons
                    for item in view.children:
                        if isinstance(item, ui.Button):
                            item.disabled = True
                    await message.edit(embed=embed, view=view)
                    return opponent_id

            except asyncio.CancelledError:
                view.stop()
                return 0

        return view.winner_id


# ── 8. BlackjackShowdown ────────────────────────────────────────────────────


class _BJTurnView(ui.View):
    """Turn-based blackjack. Only the active player can hit/stand."""

    def __init__(
        self,
        active_id: int,
        p1_id: int,
        p2_id: int,
        p1_name: str,
        p2_name: str,
        p1_hand: list[tuple[str, str]],
        p2_hand: list[tuple[str, str]],
        deck: list[tuple[str, str]],
        show_p2: bool,
    ) -> None:
        super().__init__(timeout=25)
        self.active_id = active_id
        self.p1_id = p1_id
        self.p2_id = p2_id
        self.p1_name = p1_name
        self.p2_name = p2_name
        self.p1_hand = p1_hand
        self.p2_hand = p2_hand
        self.deck = deck
        self.show_p2 = show_p2
        self.done = asyncio.Event()
        self.busted = False

    def _make_embed(self, status: str) -> discord.Embed:
        embed = discord.Embed(
            title="\U0001f0cf Blackjack",
            description=status,
            colour=discord.Colour.dark_purple(),
        )
        embed.add_field(
            name=self.p1_name,
            value=_bj_hand_display(self.p1_hand),
            inline=True,
        )
        embed.add_field(name="\u200b", value="\u200b", inline=True)
        if self.show_p2:
            embed.add_field(
                name=self.p2_name,
                value=_bj_hand_display(self.p2_hand),
                inline=True,
            )
        else:
            embed.add_field(name=self.p2_name, value="\U0001f0cf \U0001f0cf", inline=True)
        return embed

    def _active_hand(self) -> list[tuple[str, str]]:
        return self.p1_hand if self.active_id == self.p1_id else self.p2_hand

    @ui.button(label="Hit", emoji="\U0001f0cf", style=discord.ButtonStyle.primary, row=1)
    async def hit(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        if uid != self.active_id:
            await interaction.response.send_message("Not your turn!", ephemeral=True)
            return

        hand = self._active_hand()
        card = self.deck.pop()
        hand.append(card)
        total = _bj_hand_total(hand)
        name = self.p1_name if uid == self.p1_id else self.p2_name

        if total > 21:
            self.busted = True
            self.done.set()
            self.stop()
            embed = self._make_embed(f"**{name}** draws {_card_str(*card)} \u2014 **BUST!**")
            await interaction.response.edit_message(embed=embed, view=None)
        else:
            embed = self._make_embed(
                f"**{name}** draws {_card_str(*card)}. Hit or Stand?",
            )
            await interaction.response.edit_message(embed=embed, view=self)

    @ui.button(label="Stand", emoji="\U0001f6d1", style=discord.ButtonStyle.danger, row=1)
    async def stand(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        if uid != self.active_id:
            await interaction.response.send_message("Not your turn!", ephemeral=True)
            return

        name = self.p1_name if uid == self.p1_id else self.p2_name
        total = _bj_hand_total(self._active_hand())
        self.done.set()
        self.stop()
        embed = self._make_embed(f"**{name}** stands at **{total}**.")
        await interaction.response.edit_message(embed=embed, view=None)


class BlackjackShowdown:
    name = "Blackjack"
    emoji = "\U0001f0cf"
    stakes = 300
    elo_key = "blackjack_showdown"

    async def play(
        self,
        message: discord.Message,
        p1_id: int,
        p1_name: str,
        p2_id: int,
        p2_name: str,
    ) -> int:
        deck = list(DECK)
        random.shuffle(deck)
        p1_hand = [deck.pop(), deck.pop()]
        p2_hand = [deck.pop(), deck.pop()]

        # --- Phase 1: P1 plays (P2's hand hidden) ---
        p1_total = _bj_hand_total(p1_hand)
        p1_natural = p1_total == 21 and len(p1_hand) == 2

        if not p1_natural:
            view = _BJTurnView(
                p1_id, p1_id, p2_id, p1_name, p2_name,
                p1_hand, p2_hand, deck, show_p2=False,
            )
            embed = view._make_embed(f"**{p1_name}**'s turn. Hit or Stand?")
            await message.edit(embed=embed, view=view)

            try:
                await asyncio.wait_for(view.done.wait(), timeout=25)
            except asyncio.TimeoutError:
                view.stop()

        await asyncio.sleep(1)

        # --- Phase 2: P2 plays (both hands visible) ---
        p1_total = _bj_hand_total(p1_hand)
        p1_bust = p1_total > 21
        p2_total = _bj_hand_total(p2_hand)
        p2_natural = p2_total == 21 and len(p2_hand) == 2

        if p1_bust:
            # P1 busted \u2014 P2 wins automatically, just reveal
            pass
        elif not p2_natural:
            view2 = _BJTurnView(
                p2_id, p1_id, p2_id, p1_name, p2_name,
                p1_hand, p2_hand, deck, show_p2=True,
            )
            status = (
                f"**{p1_name}** finished at **{p1_total}**."
                + (" Blackjack!" if p1_natural else "")
                + f"\n**{p2_name}**'s turn. Hit or Stand?"
            )
            embed = view2._make_embed(status)
            await message.edit(embed=embed, view=view2)

            try:
                await asyncio.wait_for(view2.done.wait(), timeout=25)
            except asyncio.TimeoutError:
                view2.stop()

        # --- Resolve ---
        t1 = _bj_hand_total(p1_hand)
        t2 = _bj_hand_total(p2_hand)
        b1 = t1 > 21
        b2 = t2 > 21
        bj1 = t1 == 21 and len(p1_hand) == 2
        bj2 = t2 == 21 and len(p2_hand) == 2

        if b1 and b2:
            winner_id = 0
            desc = "Both players busted \u2014 draw!"
        elif b1:
            winner_id = p2_id
            desc = f"**{p1_name}** busted! **{p2_name}** wins with {t2}!"
        elif b2:
            winner_id = p1_id
            desc = f"**{p2_name}** busted! **{p1_name}** wins with {t1}!"
        elif bj1 and not bj2:
            winner_id = p1_id
            desc = f"**{p1_name}** has Blackjack! **{p1_name}** wins!"
        elif bj2 and not bj1:
            winner_id = p2_id
            desc = f"**{p2_name}** has Blackjack! **{p2_name}** wins!"
        elif t1 > t2:
            winner_id = p1_id
            desc = f"**{p1_name}** wins with {t1} vs {t2}!"
        elif t2 > t1:
            winner_id = p2_id
            desc = f"**{p2_name}** wins with {t2} vs {t1}!"
        else:
            winner_id = 0
            desc = f"Both have {t1} \u2014 draw!"

        colour = discord.Colour.gold() if winner_id else discord.Colour.greyple()
        # Build final embed showing both hands
        final = discord.Embed(
            title="\U0001f0cf Blackjack",
            description=desc,
            colour=colour,
        )
        final.add_field(name=p1_name, value=_bj_hand_display(p1_hand), inline=True)
        final.add_field(name="\u200b", value="\u200b", inline=True)
        final.add_field(name=p2_name, value=_bj_hand_display(p2_hand), inline=True)
        await message.edit(embed=final, view=None)
        return winner_id


# ── 9. WordScramble ────────────────────────────────────────────────────────

WORD_POOL = [
    "planet", "bridge", "castle", "frozen", "galaxy", "jungle", "knight",
    "market", "orange", "pirate", "rocket", "silver", "throne", "valley",
    "wizard", "anchor", "breeze", "circus", "dragon", "empire", "flight",
    "goblin", "harbor", "insect", "jigsaw", "kitten", "locket", "magnet",
    "nickel", "oyster", "parrot", "quartz", "ribbon", "saddle", "temple",
    "unwind", "velvet", "walnut", "zenith", "blazer", "candle", "desert",
    "falcon", "gentle", "hermit", "impact", "jacket", "lantern", "muffin",
    "noodle", "puzzle", "random", "shield", "turnip", "violin", "wrench",
    "basket", "copper", "digest", "fabric", "golden", "honest", "island",
    "launch", "mental", "object", "pencil", "result", "simple", "travel",
    "wealth", "branch", "custom", "donkey", "expert", "forest", "guitar",
    "helmet", "legend", "meteor", "notice", "prison", "salmon", "ticket",
]


def _scramble_word(word: str) -> str:
    """Shuffle letters ensuring it differs from the original."""
    letters = list(word)
    for _ in range(50):
        random.shuffle(letters)
        if "".join(letters) != word:
            return "".join(letters)
    return "".join(letters)


class _UnscrambleModal(ui.Modal):
    answer_input = ui.TextInput(
        label="Unscramble the word",
        placeholder="Type the word",
        required=True,
        max_length=20,
    )

    def __init__(
        self, p1_id: int, p2_id: int, word: str, done: asyncio.Event,
    ) -> None:
        super().__init__(title="Word Scramble")
        self.p1_id = p1_id
        self.p2_id = p2_id
        self.word = word
        self._done = done
        self.result: tuple[int, bool] | None = None
        self._resolved = False

    async def on_submit(self, interaction: discord.Interaction) -> None:
        uid = interaction.user.id
        if uid not in (self.p1_id, self.p2_id):
            await interaction.response.send_message("You're not in this duel!", ephemeral=True)
            return
        if self._resolved:
            await interaction.response.send_message("Already answered!", ephemeral=True)
            return

        guess = self.answer_input.value.strip().lower()
        self._resolved = True
        is_correct = guess == self.word
        self.result = (uid, is_correct)

        if is_correct:
            await interaction.response.send_message("Correct!", ephemeral=True)
        else:
            await interaction.response.send_message(
                f"Wrong! You guessed '{guess}'.", ephemeral=True,
            )
        self._done.set()


class _UnscrambleView(ui.View):
    def __init__(self, p1_id: int, p2_id: int, word: str) -> None:
        super().__init__(timeout=20)
        self.p1_id = p1_id
        self.p2_id = p2_id
        self.word = word
        self.done = asyncio.Event()
        self._modal = _UnscrambleModal(p1_id, p2_id, word, self.done)

    @property
    def result(self) -> tuple[int, bool] | None:
        return self._modal.result

    @ui.button(label="Submit Answer", emoji="\U0001f524", style=discord.ButtonStyle.success)
    async def submit(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        if uid not in (self.p1_id, self.p2_id):
            await interaction.response.send_message("You're not in this duel!", ephemeral=True)
            return
        if self._modal._resolved:
            await interaction.response.send_message("Already answered!", ephemeral=True)
            return
        modal = _UnscrambleModal(self.p1_id, self.p2_id, self.word, self.done)
        modal._resolved = self._modal._resolved
        old = self._modal
        self._modal = modal

        original_submit = modal.on_submit

        async def _linked(inter: discord.Interaction) -> None:
            await original_submit(inter)
            old.result = modal.result
            old._resolved = modal._resolved
            self._modal = old
            self._modal.result = modal.result
            self._modal._resolved = modal._resolved

        modal.on_submit = _linked  # type: ignore[assignment]
        await interaction.response.send_modal(modal)


class WordScramble:
    name = "Word Scramble"
    emoji = "\U0001f524"
    stakes = 300
    elo_key = "word_scramble"

    async def play(
        self,
        message: discord.Message,
        p1_id: int,
        p1_name: str,
        p2_id: int,
        p2_name: str,
    ) -> int:
        async def _round(msg: discord.Message, _rnd: int) -> int:
            word = random.choice(WORD_POOL)
            scrambled = _scramble_word(word)

            embed = discord.Embed(
                title=f"{self.emoji} Word Scramble",
                description=(
                    f"**{p1_name}** vs **{p2_name}**\n\n"
                    f"## `{scrambled.upper()}`\n\n"
                    "Unscramble the word! First correct answer wins."
                ),
                colour=discord.Colour.dark_teal(),
            )

            view = _UnscrambleView(p1_id, p2_id, word)
            await msg.edit(embed=embed, view=view)

            try:
                await asyncio.wait_for(view.done.wait(), timeout=20)
            except asyncio.TimeoutError:
                view.stop()

            result = view.result
            if result is None:
                embed = discord.Embed(
                    title=f"{self.emoji} Word Scramble",
                    description=(
                        f"`{scrambled.upper()}` = **{word.upper()}**\n\n"
                        "Time's up! Neither player answered \u2014 draw."
                    ),
                    colour=discord.Colour.greyple(),
                )
                await msg.edit(embed=embed, view=None)
                return 0

            uid, is_correct = result
            responder_name = p1_name if uid == p1_id else p2_name
            other_id = p2_id if uid == p1_id else p1_id
            other_name = p2_name if uid == p1_id else p1_name

            if is_correct:
                winner_id = uid
                desc = (
                    f"`{scrambled.upper()}` = **{word.upper()}**\n\n"
                    f"**{responder_name}** unscrambled it first!"
                )
            else:
                winner_id = other_id
                desc = (
                    f"`{scrambled.upper()}` = **{word.upper()}**\n\n"
                    f"**{responder_name}** guessed wrong \u2014 **{other_name}** takes it!"
                )

            embed = discord.Embed(
                title=f"{self.emoji} Word Scramble",
                description=desc,
                colour=discord.Colour.gold(),
            )
            await msg.edit(embed=embed, view=None)
            return winner_id

        return await _play_best_of_3(
            message, p1_id, p1_name, p2_id, p2_name,
            self.emoji, self.name, _round,
        )


# ── 10. Nim ────────────────────────────────────────────────────────────────

PILE_EMOJI = "\U0001f7e6"  # blue square
PILE_GONE = "\u2b1b"       # black square


class _NimView(ui.View):
    """Multi-pile Poison Nim. Take from one pile per turn; last to take loses."""

    def __init__(
        self,
        p1_id: int,
        p2_id: int,
        p1_name: str,
        p2_name: str,
        piles: list[int],
    ) -> None:
        super().__init__(timeout=60)
        self.p1_id = p1_id
        self.p2_id = p2_id
        self.p1_name = p1_name
        self.p2_name = p2_name
        self.piles = list(piles)
        self.turn = p1_id
        self.done = asyncio.Event()
        self.loser_id = 0
        self._selected_pile: int | None = None
        self._rebuild_buttons()

    # ── helpers ──────────────────────────────────────────────────────────

    def _turn_name(self) -> str:
        return self.p1_name if self.turn == self.p1_id else self.p2_name

    def _other_id(self) -> int:
        return self.p2_id if self.turn == self.p1_id else self.p1_id

    def _render_piles(self) -> str:
        lines = []
        labels = ["A", "B", "C"]
        for i, count in enumerate(self.piles):
            bar = PILE_EMOJI * count + PILE_GONE * (max(self.piles) - count) if count else PILE_GONE
            lines.append(f"**{labels[i]}**: {bar}  ({count})")
        return "\n".join(lines)

    def _make_embed(self, status: str) -> discord.Embed:
        embed = discord.Embed(
            title="\U0001f9e0 Nim",
            description=f"{status}\n\n{self._render_piles()}",
            colour=discord.Colour.dark_magenta(),
        )
        embed.set_footer(text="Take the last piece and you LOSE!")
        return embed

    # ── button management ────────────────────────────────────────────────

    def _rebuild_buttons(self) -> None:
        self.clear_items()
        self._selected_pile = None
        labels = ["A", "B", "C"]
        for i, count in enumerate(self.piles):
            btn = ui.Button(
                label=f"Pile {labels[i]} ({count})",
                style=discord.ButtonStyle.primary if count > 0 else discord.ButtonStyle.secondary,
                custom_id=f"nim_pile_{i}",
                disabled=count == 0,
                row=0,
            )
            btn.callback = self._make_pile_callback(i)
            self.add_item(btn)

    def _build_take_buttons(self, pile_idx: int) -> None:
        self.clear_items()
        self._selected_pile = pile_idx
        labels = ["A", "B", "C"]
        count = self.piles[pile_idx]
        # Back button
        back = ui.Button(
            label="Back",
            style=discord.ButtonStyle.secondary,
            custom_id="nim_back",
            row=0,
        )
        back.callback = self._back_callback
        self.add_item(back)
        # Take 1..count buttons (max 4 per row, cap at 8 to stay within limits)
        max_take = min(count, 8)
        for n in range(1, max_take + 1):
            btn = ui.Button(
                label=f"Take {n}",
                style=discord.ButtonStyle.danger,
                custom_id=f"nim_take_{n}",
                row=1 + (n - 1) // 4,
            )
            btn.callback = self._make_take_callback(pile_idx, n)
            self.add_item(btn)

    # ── callbacks ────────────────────────────────────────────────────────

    def _make_pile_callback(self, pile_idx: int):
        async def callback(interaction: discord.Interaction) -> None:
            if interaction.user.id != self.turn:
                await interaction.response.send_message(
                    f"It's **{self._turn_name()}**'s turn!", ephemeral=True,
                )
                return
            self._build_take_buttons(pile_idx)
            labels = ["A", "B", "C"]
            status = f"**{self._turn_name()}** selected pile **{labels[pile_idx]}**. How many?"
            embed = self._make_embed(status)
            await interaction.response.edit_message(embed=embed, view=self)
        return callback

    async def _back_callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.turn:
            await interaction.response.send_message(
                f"It's **{self._turn_name()}**'s turn!", ephemeral=True,
            )
            return
        self._rebuild_buttons()
        status = f"**{self._turn_name()}**'s turn \u2014 pick a pile!"
        embed = self._make_embed(status)
        await interaction.response.edit_message(embed=embed, view=self)

    def _make_take_callback(self, pile_idx: int, amount: int):
        async def callback(interaction: discord.Interaction) -> None:
            if interaction.user.id != self.turn:
                await interaction.response.send_message(
                    f"It's **{self._turn_name()}**'s turn!", ephemeral=True,
                )
                return

            name = self._turn_name()
            labels = ["A", "B", "C"]

            self.piles[pile_idx] -= amount

            # Check if all piles empty — current player took the last piece(s) and loses
            if sum(self.piles) == 0:
                self.loser_id = self.turn
                self.done.set()
                self.stop()
                embed = self._make_embed(
                    f"**{name}** takes {amount} from pile **{labels[pile_idx]}** "
                    f"\u2014 took the last piece! **{name}** loses!"
                )
                await interaction.response.edit_message(embed=embed, view=None)
                return

            # Switch turn
            self.turn = self._other_id()
            self._rebuild_buttons()
            status = (
                f"**{name}** takes {amount} from pile **{labels[pile_idx]}**.\n"
                f"**{self._turn_name()}**'s turn \u2014 pick a pile!"
            )
            embed = self._make_embed(status)
            await interaction.response.edit_message(embed=embed, view=self)
        return callback


class Nim:
    name = "Nim"
    emoji = "\U0001f9e0"
    stakes = 300
    elo_key = "nim"

    async def play(
        self,
        message: discord.Message,
        p1_id: int,
        p1_name: str,
        p2_id: int,
        p2_name: str,
    ) -> int:
        # Random pile sizes — keeps it unsolvable by memorization
        piles = [random.randint(2, 7) for _ in range(3)]

        view = _NimView(p1_id, p2_id, p1_name, p2_name, piles)
        status = f"**{p1_name}**'s turn \u2014 pick a pile!"
        embed = view._make_embed(status)
        await message.edit(embed=embed, view=view)

        try:
            await asyncio.wait_for(view.done.wait(), timeout=60)
        except asyncio.TimeoutError:
            view.stop()
            # Whoever's turn it was loses (timed out)
            view.loser_id = view.turn

        if view.loser_id:
            winner_id = p2_id if view.loser_id == p1_id else p1_id
            winner_name = p2_name if view.loser_id == p1_id else p1_name
            loser_name = p1_name if view.loser_id == p1_id else p2_name
        else:
            winner_id = 0
            winner_name = ""
            loser_name = ""

        if winner_id:
            embed = discord.Embed(
                title="\U0001f9e0 Nim",
                description=f"**{winner_name}** wins! {loser_name} took the last piece.",
                colour=discord.Colour.gold(),
            )
        else:
            embed = discord.Embed(
                title="\U0001f9e0 Nim",
                description="Game timed out \u2014 draw!",
                colour=discord.Colour.greyple(),
            )
        await message.edit(embed=embed, view=None)
        return winner_id


# ── 11. PushYourLuck ──────────────────────────────────────────────────────


class _PushLuckView(ui.View):
    """Draw tiles for points. Hit a bomb and you bust (score = 0)."""

    def __init__(self, player_id: int, player_name: str, target: int) -> None:
        super().__init__(timeout=25)
        self.player_id = player_id
        self.player_name = player_name
        self.target = target  # score to beat (0 for first player)
        self.score = 0
        self.draws = 0
        self.busted = False
        self.stopped = False
        self.done = asyncio.Event()
        # 12 tiles: 9 safe (+25 each), 3 bombs
        tiles = [True] * 9 + [False] * 3
        random.shuffle(tiles)
        self._tiles = tiles
        self.history: list[str] = []

    def _make_embed(self, status: str) -> discord.Embed:
        embed = discord.Embed(
            title="\U0001f3b0 Push Your Luck",
            description=status,
            colour=discord.Colour.red() if self.busted else discord.Colour.dark_gold(),
        )
        if self.history:
            embed.add_field(
                name="Draws",
                value="\n".join(self.history[-6:]),
                inline=False,
            )
        return embed

    @ui.button(label="Draw!", emoji="\U0001f3b0", style=discord.ButtonStyle.primary)
    async def draw_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("Not your turn!", ephemeral=True)
            return

        tile = self._tiles[self.draws]
        self.draws += 1

        if not tile:
            self.busted = True
            self.history.append("\U0001f4a3 **BOMB!**")
            self.done.set()
            self.stop()
            embed = self._make_embed(
                f"**{self.player_name}** drew a bomb! Score: **0**",
            )
            await interaction.response.edit_message(embed=embed, view=None)
        else:
            self.score += 25
            self.history.append(f"\u2705 +25 (total: **{self.score}**)")

            remaining = len(self._tiles) - self.draws
            remaining_bombs = sum(1 for t in self._tiles[self.draws:] if not t)

            if remaining == 0 or remaining_bombs == remaining:
                self.stopped = True
                self.done.set()
                self.stop()
                embed = self._make_embed(
                    f"**{self.player_name}** clears the deck! Score: **{self.score}**",
                )
                await interaction.response.edit_message(embed=embed, view=None)
            else:
                status = (
                    f"**{self.player_name}**: **{self.score}** pts\n"
                    f"{remaining} tiles left ({remaining_bombs} \U0001f4a3)\n"
                    f"Draw again or stop?"
                )
                if self.target > 0:
                    status += f"\nNeed to beat: **{self.target}**"
                embed = self._make_embed(status)
                await interaction.response.edit_message(embed=embed, view=self)

    @ui.button(label="Stop", emoji="\U0001f6d1", style=discord.ButtonStyle.danger)
    async def stop_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("Not your turn!", ephemeral=True)
            return
        self.stopped = True
        self.done.set()
        self.stop()
        embed = self._make_embed(f"**{self.player_name}** stops at **{self.score}**!")
        await interaction.response.edit_message(embed=embed, view=None)


class PushYourLuck:
    name = "Push Your Luck"
    emoji = "\U0001f3b0"
    stakes = 300
    elo_key = "push_your_luck"

    async def play(
        self,
        message: discord.Message,
        p1_id: int,
        p1_name: str,
        p2_id: int,
        p2_name: str,
    ) -> int:
        # Phase 1: P1 draws
        view1 = _PushLuckView(p1_id, p1_name, target=0)
        embed = view1._make_embed(
            f"**{p1_name}** goes first!\n"
            "Draw tiles for **25 pts** each \u2014 but hit a \U0001f4a3 and you bust!\n"
            "12 tiles, 3 are bombs.",
        )
        await message.edit(embed=embed, view=view1)

        try:
            await asyncio.wait_for(view1.done.wait(), timeout=25)
        except asyncio.TimeoutError:
            view1.stop()
            view1.stopped = True

        p1_score = 0 if view1.busted else view1.score

        await asyncio.sleep(1.5)

        # Phase 2: P2 draws, knowing P1's score
        view2 = _PushLuckView(p2_id, p2_name, target=p1_score)
        status = (
            f"**{p1_name}** scored **{p1_score}**"
            + (" (busted!)" if view1.busted else "")
            + f"\n**{p2_name}**'s turn! Beat **{p1_score}** to win.\n"
            "12 tiles, 3 are bombs."
        )
        embed = view2._make_embed(status)
        await message.edit(embed=embed, view=view2)

        try:
            await asyncio.wait_for(view2.done.wait(), timeout=25)
        except asyncio.TimeoutError:
            view2.stop()
            view2.stopped = True

        p2_score = 0 if view2.busted else view2.score

        if p1_score > p2_score:
            winner_id = p1_id
            desc = f"**{p1_name}** wins! ({p1_score} vs {p2_score})"
        elif p2_score > p1_score:
            winner_id = p2_id
            desc = f"**{p2_name}** wins! ({p2_score} vs {p1_score})"
        else:
            winner_id = 0
            desc = f"Tied at **{p1_score}**!"

        colour = discord.Colour.gold() if winner_id else discord.Colour.greyple()
        final = discord.Embed(
            title="\U0001f3b0 Push Your Luck", description=desc, colour=colour,
        )
        p1_note = " (bust)" if view1.busted else ""
        p2_note = " (bust)" if view2.busted else ""
        final.add_field(name=p1_name, value=f"**{p1_score}** pts{p1_note}", inline=True)
        final.add_field(name="vs", value="\u200b", inline=True)
        final.add_field(name=p2_name, value=f"**{p2_score}** pts{p2_note}", inline=True)
        await message.edit(embed=final, view=None)
        return winner_id


# ── 12. Chicken ───────────────────────────────────────────────────────────


class _ChickenView(ui.View):
    """Alternating push. Hidden bomb threshold. Push past it and you lose."""

    def __init__(
        self,
        p1_id: int,
        p2_id: int,
        p1_name: str,
        p2_name: str,
        bomb: int,
    ) -> None:
        super().__init__(timeout=45)
        self.p1_id = p1_id
        self.p2_id = p2_id
        self.p1_name = p1_name
        self.p2_name = p2_name
        self.bomb = bomb
        self.counter = 0
        self.turn = p1_id
        self.done = asyncio.Event()
        self.result: str = ""  # "chicken" | "boom" | "timeout"
        self.loser_id = 0

    def _turn_name(self) -> str:
        return self.p1_name if self.turn == self.p1_id else self.p2_name

    def _other_id(self) -> int:
        return self.p2_id if self.turn == self.p1_id else self.p1_id

    def _make_embed(self, status: str) -> discord.Embed:
        embed = discord.Embed(
            title="\U0001f414 Chicken",
            description=status,
            colour=discord.Colour.orange(),
        )
        bar_len = 20
        fill = min(bar_len, int(self.counter / 350 * bar_len))
        bar = "\U0001f7e5" * fill + "\u2b1c" * (bar_len - fill)
        embed.add_field(name=f"Danger: {self.counter}", value=bar, inline=False)
        embed.set_footer(text="Push or chicken out!")
        return embed

    @ui.button(label="Push!", emoji="\U0001f4a3", style=discord.ButtonStyle.danger)
    async def push_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.turn:
            await interaction.response.send_message(
                f"It's **{self._turn_name()}**'s turn!", ephemeral=True,
            )
            return

        name = self._turn_name()
        add = random.randint(15, 35)
        self.counter += add

        if self.counter >= self.bomb:
            self.result = "boom"
            self.loser_id = self.turn
            self.done.set()
            self.stop()
            embed = self._make_embed(
                f"**{name}** pushes +{add}... "
                f"\U0001f4a5 **BOOM!** The bomb was at **{self.bomb}**!",
            )
            await interaction.response.edit_message(embed=embed, view=None)
            return

        self.turn = self._other_id()
        next_name = self._turn_name()
        status = (
            f"**{name}** pushes +{add} (total: **{self.counter}**)\n"
            f"**{next_name}**, push or chicken out?"
        )
        embed = self._make_embed(status)
        await interaction.response.edit_message(embed=embed, view=self)

    @ui.button(label="Chicken Out!", emoji="\U0001f414", style=discord.ButtonStyle.secondary)
    async def chicken_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.turn:
            await interaction.response.send_message(
                f"It's **{self._turn_name()}**'s turn!", ephemeral=True,
            )
            return

        self.result = "chicken"
        self.loser_id = self.turn
        self.done.set()
        self.stop()
        name = self._turn_name()
        embed = self._make_embed(
            f"\U0001f414 **{name}** chickens out! The bomb was at **{self.bomb}**.",
        )
        await interaction.response.edit_message(embed=embed, view=None)


class Chicken:
    name = "Chicken"
    emoji = "\U0001f414"
    stakes = 300
    elo_key = "chicken"

    async def play(
        self,
        message: discord.Message,
        p1_id: int,
        p1_name: str,
        p2_id: int,
        p2_name: str,
    ) -> int:
        bomb = random.randint(150, 300)
        view = _ChickenView(p1_id, p2_id, p1_name, p2_name, bomb)
        status = f"**{p1_name}**, push or chicken out?"
        embed = view._make_embed(status)
        await message.edit(embed=embed, view=view)

        try:
            await asyncio.wait_for(view.done.wait(), timeout=45)
        except asyncio.TimeoutError:
            view.stop()
            view.result = "timeout"
            view.loser_id = view.turn

        if view.loser_id:
            winner_id = p2_id if view.loser_id == p1_id else p1_id
            winner_name = p2_name if view.loser_id == p1_id else p1_name
            loser_name = p1_name if view.loser_id == p1_id else p2_name

            if view.result == "boom":
                desc = f"\U0001f4a5 **{loser_name}** hit the bomb! **{winner_name}** wins!"
            elif view.result == "chicken":
                desc = f"\U0001f414 **{loser_name}** chickened out! **{winner_name}** wins!"
            else:
                desc = f"**{loser_name}** timed out! **{winner_name}** wins!"
        else:
            winner_id = 0
            desc = "Game timed out \u2014 draw!"

        colour = discord.Colour.gold() if winner_id else discord.Colour.greyple()
        embed = discord.Embed(
            title="\U0001f414 Chicken", description=desc, colour=colour,
        )
        await message.edit(embed=embed, view=None)
        return winner_id


# ── 13. Battleship ────────────────────────────────────────────────────────

_BS_COLS = "ABCD"


def _place_ships() -> set[tuple[int, int]]:
    """Place 2 ships of length 2 on a 4x4 grid (no overlap)."""
    occupied: set[tuple[int, int]] = set()
    for _ in range(2):
        for _ in range(100):
            r = random.randint(0, 3)
            c = random.randint(0, 3)
            if random.choice([True, False]):
                if c + 1 > 3:
                    continue
                cells = {(r, c), (r, c + 1)}
            else:
                if r + 1 > 3:
                    continue
                cells = {(r, c), (r + 1, c)}
            if cells & occupied:
                continue
            occupied |= cells
            break
    return occupied


class _BattleshipView(ui.View):
    """4x4 grid, 2 ships each (length 2). First to sink both wins."""

    def __init__(
        self,
        p1_id: int,
        p2_id: int,
        p1_name: str,
        p2_name: str,
        p1_ships: set[tuple[int, int]],
        p2_ships: set[tuple[int, int]],
    ) -> None:
        super().__init__(timeout=90)
        self.p1_id = p1_id
        self.p2_id = p2_id
        self.p1_name = p1_name
        self.p2_name = p2_name
        self.p1_ships = p1_ships
        self.p2_ships = p2_ships
        self.p1_shots: dict[tuple[int, int], bool] = {}
        self.p2_shots: dict[tuple[int, int], bool] = {}
        self.turn = p1_id
        self.done = asyncio.Event()
        self.winner_id = 0
        self._rebuild_buttons()

    def _active_shots(self) -> dict[tuple[int, int], bool]:
        return self.p1_shots if self.turn == self.p1_id else self.p2_shots

    def _target_ships(self) -> set[tuple[int, int]]:
        return self.p2_ships if self.turn == self.p1_id else self.p1_ships

    def _turn_name(self) -> str:
        return self.p1_name if self.turn == self.p1_id else self.p2_name

    def _render_grid(self, shots: dict[tuple[int, int], bool]) -> str:
        rows = []
        for r in range(4):
            row = ""
            for c in range(4):
                if (r, c) in shots:
                    row += "\U0001f4a5" if shots[(r, c)] else "\U0001f30a"
                else:
                    row += "\u2b1c"
            rows.append(row)
        return "\n".join(rows)

    def _make_embed(self, status: str) -> discord.Embed:
        embed = discord.Embed(
            title="\U0001f6a2 Battleship",
            description=status,
            colour=discord.Colour.dark_blue(),
        )
        p1_hits = sum(1 for v in self.p1_shots.values() if v)
        p2_hits = sum(1 for v in self.p2_shots.values() if v)
        embed.add_field(
            name=f"{self.p1_name} ({p1_hits}/{len(self.p2_ships)} hits)",
            value=self._render_grid(self.p1_shots),
            inline=True,
        )
        embed.add_field(name="\u200b", value="\u200b", inline=True)
        embed.add_field(
            name=f"{self.p2_name} ({p2_hits}/{len(self.p1_ships)} hits)",
            value=self._render_grid(self.p2_shots),
            inline=True,
        )
        return embed

    def _rebuild_buttons(self) -> None:
        self.clear_items()
        shots = self._active_shots()
        for r in range(4):
            for c in range(4):
                coord = (r, c)
                if coord in shots:
                    hit = shots[coord]
                    btn = ui.Button(
                        label="\U0001f4a5" if hit else "\u00b7",
                        style=discord.ButtonStyle.danger if hit else discord.ButtonStyle.secondary,
                        custom_id=f"bs_{r}_{c}",
                        disabled=True,
                        row=r,
                    )
                else:
                    btn = ui.Button(
                        label=f"{_BS_COLS[c]}{r + 1}",
                        style=discord.ButtonStyle.primary,
                        custom_id=f"bs_{r}_{c}",
                        row=r,
                    )
                    btn.callback = self._make_fire_callback(r, c)
                self.add_item(btn)

    def _make_fire_callback(self, r: int, c: int):
        async def callback(interaction: discord.Interaction) -> None:
            if interaction.user.id != self.turn:
                await interaction.response.send_message("Not your turn!", ephemeral=True)
                return

            shots = self._active_shots()
            target = self._target_ships()
            hit = (r, c) in target
            shots[(r, c)] = hit

            name = self._turn_name()
            coord_str = f"{_BS_COLS[c]}{r + 1}"
            result = "\U0001f4a5 **HIT!**" if hit else "\U0001f30a Miss"

            hits = sum(1 for v in shots.values() if v)
            if hits >= len(target):
                self.winner_id = self.turn
                self.done.set()
                self.stop()
                embed = self._make_embed(
                    f"**{name}** fires at **{coord_str}** \u2014 {result}\n"
                    f"\U0001f3c6 **{name}** sinks all ships!",
                )
                await interaction.response.edit_message(embed=embed, view=None)
                return

            self.turn = self.p2_id if self.turn == self.p1_id else self.p1_id
            self._rebuild_buttons()
            next_name = self._turn_name()
            status = (
                f"**{name}** fires at **{coord_str}** \u2014 {result}\n"
                f"**{next_name}**'s turn!"
            )
            embed = self._make_embed(status)
            await interaction.response.edit_message(embed=embed, view=self)
        return callback


class Battleship:
    name = "Battleship"
    emoji = "\U0001f6a2"
    stakes = 300
    elo_key = "battleship"

    async def play(
        self,
        message: discord.Message,
        p1_id: int,
        p1_name: str,
        p2_id: int,
        p2_name: str,
    ) -> int:
        p1_ships = _place_ships()
        p2_ships = _place_ships()

        view = _BattleshipView(
            p1_id, p2_id, p1_name, p2_name, p1_ships, p2_ships,
        )
        status = f"**{p1_name}**'s turn! Fire at a coordinate."
        embed = view._make_embed(status)
        await message.edit(embed=embed, view=view)

        try:
            await asyncio.wait_for(view.done.wait(), timeout=120)
        except asyncio.TimeoutError:
            view.stop()

        if not view.winner_id:
            # Timeout \u2014 most hits wins
            p1h = sum(1 for v in view.p1_shots.values() if v)
            p2h = sum(1 for v in view.p2_shots.values() if v)
            if p1h > p2h:
                view.winner_id = p1_id
                desc = f"Time's up! **{p1_name}** wins ({p1h} vs {p2h} hits)!"
            elif p2h > p1h:
                view.winner_id = p2_id
                desc = f"Time's up! **{p2_name}** wins ({p2h} vs {p1h} hits)!"
            else:
                desc = f"Time's up! Tied at {p1h} hits \u2014 draw!"
            colour = discord.Colour.gold() if view.winner_id else discord.Colour.greyple()
            embed = discord.Embed(
                title="\U0001f6a2 Battleship", description=desc, colour=colour,
            )
            await message.edit(embed=embed, view=None)

        return view.winner_id


# ── Registry & Picker ───────────────────────────────────────────────────────

ALL_GAMES: list[MiniGame] = [
    HigherCard(),
    DiceRoll(),
    SpeedMath(),
    Trivia(),
    GuessTheNumber(),
    CoinFlip(),
    TicTacToe(),
    BlackjackShowdown(),
    WordScramble(),
    Nim(),
    PushYourLuck(),
    Chicken(),
    Battleship(),
]


def pick_games(n: int = 3) -> list[MiniGame]:
    """Pick *n* random mini-games (no repeats)."""
    return random.sample(ALL_GAMES, min(n, len(ALL_GAMES)))
