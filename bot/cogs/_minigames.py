"""Mini-game engine for 1v1 duels and tournaments.

Eight quick games playable between two players. Each game class exposes a
uniform interface so the duel/tournament system can pick and run them
generically.

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
        await message.edit(embed=embed, view=view)

        # Wait for draws, updating the embed as each player draws
        while not view.done.is_set():
            try:
                await asyncio.wait_for(view.done.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass

            # Check if one player has drawn and update the embed
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
                await message.edit(embed=embed, view=view)
            elif drawn_count == 2:
                break

            # Check if total timeout exceeded (20 seconds handled by view)
            if not view.is_finished():
                continue
            else:
                break

        view.stop()

        # Anyone who didn't draw gets their random card anyway
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

        await message.edit(embed=embed, view=None)
        return winner_id


# ── 2. DiceRoll ─────────────────────────────────────────────────────────────


class _DiceRollView(ui.View):
    """Each player clicks 'Roll!' to roll their 2d6."""

    def __init__(self, p1_id: int, p2_id: int) -> None:
        super().__init__(timeout=20)
        self.p1_id = p1_id
        self.p2_id = p2_id
        self.rolls: dict[int, tuple[int, int]] = {}
        self.done = asyncio.Event()

    @ui.button(label="Roll!", emoji="🎲", style=discord.ButtonStyle.primary)
    async def roll_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        if uid not in (self.p1_id, self.p2_id):
            await interaction.response.send_message(
                "You're not in this duel!", ephemeral=True,
            )
            return
        if uid in self.rolls:
            d1, d2 = self.rolls[uid]
            await interaction.response.send_message(
                f"You already rolled {DICE_EMOJI[d1]}{DICE_EMOJI[d2]} = {d1 + d2}!",
                ephemeral=True,
            )
            return

        d1, d2 = _roll_2d6()
        self.rolls[uid] = (d1, d2)
        await interaction.response.send_message(
            f"You rolled {DICE_EMOJI[d1]}{DICE_EMOJI[d2]} = {d1 + d2}!",
            ephemeral=True,
        )

        if self.p1_id in self.rolls and self.p2_id in self.rolls:
            self.done.set()
            self.stop()


class DiceRoll:
    name = "Dice Roll"
    emoji = "🎲"
    stakes = 200

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

            view = _DiceRollView(p1_id, p2_id)

            embed = discord.Embed(
                title=f"{self.emoji} Dice Roll{round_label}",
                description="Both players: click Roll!",
                colour=discord.Colour.orange(),
            )
            embed.add_field(name=p1_name, value="\u2b1c\u2b1c", inline=True)
            embed.add_field(name="vs", value="\u200b", inline=True)
            embed.add_field(name=p2_name, value="\u2b1c\u2b1c", inline=True)
            await message.edit(embed=embed, view=view)

            # Poll for partial updates
            while not view.done.is_set():
                try:
                    await asyncio.wait_for(view.done.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass

                p1_rolled = p1_id in view.rolls
                p2_rolled = p2_id in view.rolls

                if (p1_rolled or p2_rolled) and not view.done.is_set():
                    p1_val = _dice_display(*view.rolls[p1_id]) if p1_rolled else "\u2b1c\u2b1c"
                    p2_val = _dice_display(*view.rolls[p2_id]) if p2_rolled else "\u2b1c\u2b1c"
                    waiting = "Waiting for the other player..."
                    embed = discord.Embed(
                        title=f"{self.emoji} Dice Roll{round_label}",
                        description=waiting,
                        colour=discord.Colour.orange(),
                    )
                    embed.add_field(name=p1_name, value=p1_val, inline=True)
                    embed.add_field(name="vs", value="\u200b", inline=True)
                    embed.add_field(name=p2_name, value=p2_val, inline=True)
                    await message.edit(embed=embed, view=view)

                if view.is_finished():
                    break

            view.stop()

            # Assign random rolls for anyone who didn't roll
            if p1_id not in view.rolls:
                view.rolls[p1_id] = _roll_2d6()
            if p2_id not in view.rolls:
                view.rolls[p2_id] = _roll_2d6()

            d1a, d1b = view.rolls[p1_id]
            d2a, d2b = view.rolls[p2_id]
            t1 = d1a + d1b
            t2 = d2a + d2b

            if t1 > t2:
                winner_id, winner_name = p1_id, p1_name
            elif t2 > t1:
                winner_id, winner_name = p2_id, p2_name
            else:
                winner_id = 0

            if winner_id or attempt == max_rounds - 1:
                result_colour = discord.Colour.gold() if winner_id else discord.Colour.greyple()
                embed = discord.Embed(
                    title=f"{self.emoji} Dice Roll{round_label}",
                    colour=result_colour,
                )
                embed.add_field(name=p1_name, value=_dice_display(d1a, d1b), inline=True)
                embed.add_field(name="vs", value="\u200b", inline=True)
                embed.add_field(name=p2_name, value=_dice_display(d2a, d2b), inline=True)

                if winner_id:
                    embed.description = f"**{winner_name}** wins!"
                else:
                    embed.description = "Tied after 3 rolls -- it's a draw!"

                await message.edit(embed=embed, view=None)
                return winner_id

            # Tie -- show it and reroll
            embed = discord.Embed(
                title=f"{self.emoji} Dice Roll{round_label}",
                description="Tied! Rerolling...",
                colour=discord.Colour.orange(),
            )
            embed.add_field(name=p1_name, value=_dice_display(d1a, d1b), inline=True)
            embed.add_field(name="vs", value="\u200b", inline=True)
            embed.add_field(name=p2_name, value=_dice_display(d2a, d2b), inline=True)
            await message.edit(embed=embed, view=None)
            await asyncio.sleep(1.5)

        return 0  # unreachable but satisfies type checker


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

    async def play(
        self,
        message: discord.Message,
        p1_id: int,
        p1_name: str,
        p2_id: int,
        p2_name: str,
    ) -> int:
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
        await message.edit(embed=embed, view=view)

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
                    "Time's up! Neither player answered -- draw."
                ),
                colour=discord.Colour.greyple(),
            )
            await message.edit(embed=embed, view=None)
            return 0

        uid, is_correct = result
        responder_name = p1_name if uid == p1_id else p2_name
        other_id = p2_id if uid == p1_id else p1_id
        other_name = p2_name if uid == p1_id else p1_name

        if is_correct:
            winner_id, winner_name = uid, responder_name
            desc = (
                f"**{problem} = {answer}**\n\n"
                f"**{responder_name}** answered correctly first and wins!"
            )
        else:
            winner_id, winner_name = other_id, other_name
            desc = (
                f"**{problem} = {answer}**\n\n"
                f"**{responder_name}** answered wrong -- **{other_name}** wins!"
            )

        embed = discord.Embed(
            title=f"{self.emoji} Speed Math",
            description=desc,
            colour=discord.Colour.gold(),
        )
        await message.edit(embed=embed, view=None)
        return winner_id


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
    category = random.choice(["geo_country", "geo_state", "nba", "nfl"])

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

    async def play(
        self,
        message: discord.Message,
        p1_id: int,
        p1_name: str,
        p2_id: int,
        p2_name: str,
    ) -> int:
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
        await message.edit(embed=embed, view=view)

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
                    "Time's up! Neither player answered -- draw."
                ),
                colour=discord.Colour.greyple(),
            )
            await message.edit(embed=embed, view=None)
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
                f"**{responder_name}** picked {chosen_label} -- Correct! **{responder_name}** wins!"
            )
        else:
            winner_id = other_id
            desc = (
                f"**{question}**\n\n"
                f"Answer: {correct_label}\n\n"
                f"**{responder_name}** picked {chosen_label} -- Wrong! **{other_name}** wins!"
            )

        embed = discord.Embed(
            title=f"{self.emoji} Trivia",
            description=desc,
            colour=discord.Colour.gold(),
        )
        await message.edit(embed=embed, view=None)
        return winner_id


# ── 5. GuessTheNumber ──────────────────────────────────────────────────────


class _GuessModal(ui.Modal):
    guess_input = ui.TextInput(
        label="Your guess (1-100)",
        placeholder="Enter a number between 1 and 100",
        required=True,
        max_length=3,
    )

    def __init__(
        self,
        p1_id: int,
        p2_id: int,
        guesses: dict[int, int],
        done: asyncio.Event,
    ) -> None:
        super().__init__(title="Guess the Number")
        self.p1_id = p1_id
        self.p2_id = p2_id
        self.guesses = guesses
        self._done = done

    async def on_submit(self, interaction: discord.Interaction) -> None:
        uid = interaction.user.id
        if uid not in (self.p1_id, self.p2_id):
            await interaction.response.send_message("You're not in this duel!", ephemeral=True)
            return
        if uid in self.guesses:
            await interaction.response.send_message(
                f"You already guessed {self.guesses[uid]}!", ephemeral=True,
            )
            return

        try:
            guess = int(self.guess_input.value.strip())
        except ValueError:
            await interaction.response.send_message("Enter a valid number!", ephemeral=True)
            return

        if guess < 1 or guess > 100:
            await interaction.response.send_message("Must be between 1 and 100!", ephemeral=True)
            return

        self.guesses[uid] = guess
        await interaction.response.send_message(
            f"You guessed **{guess}**. Locked in!", ephemeral=True,
        )

        if self.p1_id in self.guesses and self.p2_id in self.guesses:
            self._done.set()


class _GuessView(ui.View):
    def __init__(self, p1_id: int, p2_id: int) -> None:
        super().__init__(timeout=20)
        self.p1_id = p1_id
        self.p2_id = p2_id
        self.guesses: dict[int, int] = {}
        self.done = asyncio.Event()

    @ui.button(label="Submit Guess", emoji="🔢", style=discord.ButtonStyle.success)
    async def submit(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        if uid not in (self.p1_id, self.p2_id):
            await interaction.response.send_message("You're not in this duel!", ephemeral=True)
            return
        if uid in self.guesses:
            await interaction.response.send_message(
                f"You already guessed {self.guesses[uid]}!", ephemeral=True,
            )
            return
        modal = _GuessModal(self.p1_id, self.p2_id, self.guesses, self.done)
        await interaction.response.send_modal(modal)


class GuessTheNumber:
    name = "Guess the Number"
    emoji = "🔢"
    stakes = 200

    async def play(
        self,
        message: discord.Message,
        p1_id: int,
        p1_name: str,
        p2_id: int,
        p2_name: str,
    ) -> int:
        target = random.randint(1, 100)

        embed = discord.Embed(
            title=f"{self.emoji} Guess the Number",
            description=(
                f"**{p1_name}** vs **{p2_name}**\n\n"
                "I'm thinking of a number between **1** and **100**.\n"
                "Both players: submit your guess! Closest wins."
            ),
            colour=discord.Colour.dark_green(),
        )

        view = _GuessView(p1_id, p2_id)
        await message.edit(embed=embed, view=view)

        try:
            await asyncio.wait_for(view.done.wait(), timeout=20)
        except asyncio.TimeoutError:
            view.stop()

        g1 = view.guesses.get(p1_id)
        g2 = view.guesses.get(p2_id)

        p1_display = f"**{g1}**" if g1 is not None else "*(no guess)*"
        p2_display = f"**{g2}**" if g2 is not None else "*(no guess)*"

        if g1 is not None and g2 is not None:
            d1 = abs(g1 - target)
            d2 = abs(g2 - target)
            if d1 < d2:
                winner_id, winner_name = p1_id, p1_name
            elif d2 < d1:
                winner_id, winner_name = p2_id, p2_name
            else:
                winner_id = 0
        elif g1 is not None:
            winner_id, winner_name = p1_id, p1_name
        elif g2 is not None:
            winner_id, winner_name = p2_id, p2_name
        else:
            winner_id = 0

        result_colour = discord.Colour.gold() if winner_id else discord.Colour.greyple()
        embed = discord.Embed(
            title=f"{self.emoji} Guess the Number",
            colour=result_colour,
        )
        embed.add_field(name=p1_name, value=p1_display, inline=True)
        embed.add_field(name="Target", value=f"**{target}**", inline=True)
        embed.add_field(name=p2_name, value=p2_display, inline=True)

        if g1 is not None and g2 is not None:
            d1 = abs(g1 - target)
            d2 = abs(g2 - target)
            embed.add_field(
                name="Distance",
                value=f"{p1_name}: {d1} away | {p2_name}: {d2} away",
                inline=False,
            )

        if winner_id:
            embed.description = f"The number was **{target}**! **{winner_name}** wins!"
        elif g1 is None and g2 is None:
            embed.description = f"The number was **{target}**. Neither player guessed -- draw!"
        else:
            embed.description = f"The number was **{target}**. Equal distance -- draw!"

        await message.edit(embed=embed, view=None)
        return winner_id


# ── 6. CoinFlip ─────────────────────────────────────────────────────────────


class _CoinFlipView(ui.View):
    """Both players simultaneously pick Heads or Tails."""

    def __init__(self, p1_id: int, p2_id: int) -> None:
        super().__init__(timeout=15)
        self.p1_id = p1_id
        self.p2_id = p2_id
        self.picks: dict[int, str] = {}
        self.done = asyncio.Event()

    async def _handle_pick(self, interaction: discord.Interaction, pick: str) -> None:
        uid = interaction.user.id
        if uid not in (self.p1_id, self.p2_id):
            await interaction.response.send_message(
                "You're not in this duel!", ephemeral=True,
            )
            return
        if uid in self.picks:
            await interaction.response.send_message(
                f"You already picked {self.picks[uid]}!", ephemeral=True,
            )
            return
        self.picks[uid] = pick
        await interaction.response.send_message(
            f"You picked **{pick}**.", ephemeral=True,
        )
        if self.p1_id in self.picks and self.p2_id in self.picks:
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

    async def play(
        self,
        message: discord.Message,
        p1_id: int,
        p1_name: str,
        p2_id: int,
        p2_name: str,
    ) -> int:
        max_flips = 3

        for attempt in range(max_flips):
            round_label = f" (Reflip {attempt})" if attempt > 0 else ""

            view = _CoinFlipView(p1_id, p2_id)
            embed = discord.Embed(
                title=f"{self.emoji} Coin Flip{round_label}",
                description=(
                    f"**{p1_name}** vs **{p2_name}**\n\n"
                    "Both players: pick **Heads** or **Tails**!"
                ),
                colour=discord.Colour.blue(),
            )
            await message.edit(embed=embed, view=view)

            try:
                await asyncio.wait_for(view.done.wait(), timeout=15)
            except asyncio.TimeoutError:
                view.stop()

            # Default picks for anyone who didn't choose
            if p1_id not in view.picks:
                view.picks[p1_id] = random.choice(["Heads", "Tails"])
            if p2_id not in view.picks:
                view.picks[p2_id] = random.choice(["Heads", "Tails"])

            p1_pick = view.picks[p1_id]
            p2_pick = view.picks[p2_id]

            # Animate the flip
            embed = discord.Embed(
                title=f"{self.emoji} Coin Flip{round_label}",
                description="Flipping...",
                colour=discord.Colour.yellow(),
            )
            await message.edit(embed=embed, view=None)
            await asyncio.sleep(1.5)

            # Flip the coin
            result = random.choice(["Heads", "Tails"])
            p1_correct = p1_pick == result
            p2_correct = p2_pick == result

            if p1_correct and not p2_correct:
                winner_id, winner_name = p1_id, p1_name
            elif p2_correct and not p1_correct:
                winner_id, winner_name = p2_id, p2_name
            else:
                # Both correct or both wrong (same pick) -- reflip
                winner_id = 0

            embed = discord.Embed(
                title=f"{self.emoji} Coin Flip{round_label}",
                colour=discord.Colour.gold() if winner_id else discord.Colour.greyple(),
            )
            result_emoji = "\U0001fa99"
            embed.add_field(
                name=p1_name,
                value=f"Picked: **{p1_pick}** {'-- correct!' if p1_correct else ''}",
                inline=True,
            )
            embed.add_field(
                name=f"{result_emoji} Result",
                value=f"**{result}**",
                inline=True,
            )
            embed.add_field(
                name=p2_name,
                value=f"Picked: **{p2_pick}** {'-- correct!' if p2_correct else ''}",
                inline=True,
            )

            if winner_id:
                embed.description = f"It's **{result}**! **{winner_name}** wins!"
                await message.edit(embed=embed, view=None)
                return winner_id

            if attempt < max_flips - 1:
                embed.description = (
                    f"It's **{result}**! Both picked the same side -- reflipping..."
                )
                await message.edit(embed=embed, view=None)
                await asyncio.sleep(1.5)
            else:
                embed.description = (
                    f"It's **{result}**! Both picked the same side 3 times -- draw!"
                )
                await message.edit(embed=embed, view=None)
                return 0

        return 0  # unreachable


# ── 7. TicTacToe ────────────────────────────────────────────────────────────

_TTT_EMPTY = "\u2b1c"
_TTT_X = "\u274c"
_TTT_O = "\u2b55"

_TTT_WIN_LINES = [
    (0, 1, 2), (3, 4, 5), (6, 7, 8),  # rows
    (0, 3, 6), (1, 4, 7), (2, 5, 8),  # columns
    (0, 4, 8), (2, 4, 6),             # diagonals
]


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
        for a, b, c in _TTT_WIN_LINES:
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


class _BlackjackView(ui.View):
    """Both players independently play Hit/Stand on their blackjack hand."""

    def __init__(
        self,
        p1_id: int,
        p2_id: int,
        p1_name: str,
        p2_name: str,
        p1_hand: list[tuple[str, str]],
        p2_hand: list[tuple[str, str]],
        deck: list[tuple[str, str]],
        message: discord.Message,
    ) -> None:
        super().__init__(timeout=30)
        self.p1_id = p1_id
        self.p2_id = p2_id
        self.p1_name = p1_name
        self.p2_name = p2_name
        self.p1_hand = p1_hand
        self.p2_hand = p2_hand
        self.deck = deck
        self.message = message
        self.stood: set[int] = set()  # player IDs who have stood (or busted)
        self.done = asyncio.Event()

    def _player_hand(self, uid: int) -> list[tuple[str, str]]:
        return self.p1_hand if uid == self.p1_id else self.p2_hand

    def _player_name(self, uid: int) -> str:
        return self.p1_name if uid == self.p1_id else self.p2_name

    def _check_done(self) -> None:
        if self.p1_id in self.stood and self.p2_id in self.stood:
            self.done.set()
            self.stop()

    def _make_embed(self, status: str, colour: discord.Colour) -> discord.Embed:
        embed = discord.Embed(
            title="🃏 Blackjack Showdown",
            description=status,
            colour=colour,
        )
        p1_status = " (standing)" if self.p1_id in self.stood else ""
        p2_status = " (standing)" if self.p2_id in self.stood else ""
        p1_total = _bj_hand_total(self.p1_hand)
        p2_total = _bj_hand_total(self.p2_hand)
        if p1_total > 21:
            p1_status = " (BUST)"
        if p2_total > 21:
            p2_status = " (BUST)"

        embed.add_field(
            name=f"{self.p1_name}{p1_status}",
            value=_bj_hand_display(self.p1_hand),
            inline=True,
        )
        embed.add_field(name="\u200b", value="\u200b", inline=True)
        embed.add_field(
            name=f"{self.p2_name}{p2_status}",
            value=_bj_hand_display(self.p2_hand),
            inline=True,
        )
        return embed

    @ui.button(label="Hit", emoji="🃏", style=discord.ButtonStyle.primary, row=1)
    async def hit(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        if uid not in (self.p1_id, self.p2_id):
            await interaction.response.send_message(
                "You're not in this game!", ephemeral=True,
            )
            return
        if uid in self.stood:
            await interaction.response.send_message(
                "You already stood (or busted)!", ephemeral=True,
            )
            return

        hand = self._player_hand(uid)
        card = self.deck.pop()
        hand.append(card)
        total = _bj_hand_total(hand)

        if total > 21:
            self.stood.add(uid)
            await interaction.response.send_message(
                f"You drew {_card_str(*card)} -- **BUST** ({total})!",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"You drew {_card_str(*card)} -- total: **{total}**.",
                ephemeral=True,
            )

        # Update the public embed
        status = self._turn_status()
        embed = self._make_embed(status, discord.Colour.dark_purple())
        try:
            await self.message.edit(embed=embed, view=self)
        except discord.NotFound:
            pass

        self._check_done()

    @ui.button(label="Stand", emoji="🛑", style=discord.ButtonStyle.danger, row=1)
    async def stand(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        if uid not in (self.p1_id, self.p2_id):
            await interaction.response.send_message(
                "You're not in this game!", ephemeral=True,
            )
            return
        if uid in self.stood:
            await interaction.response.send_message(
                "You already stood!", ephemeral=True,
            )
            return

        self.stood.add(uid)
        total = _bj_hand_total(self._player_hand(uid))
        await interaction.response.send_message(
            f"You stand at **{total}**.", ephemeral=True,
        )

        status = self._turn_status()
        embed = self._make_embed(status, discord.Colour.dark_purple())
        try:
            await self.message.edit(embed=embed, view=self)
        except discord.NotFound:
            pass

        self._check_done()

    def _turn_status(self) -> str:
        waiting = []
        if self.p1_id not in self.stood:
            waiting.append(self.p1_name)
        if self.p2_id not in self.stood:
            waiting.append(self.p2_name)
        if waiting:
            return f"Waiting on: **{'**, **'.join(waiting)}**"
        return "Both players are done!"


class BlackjackShowdown:
    name = "Blackjack Showdown"
    emoji = "🃏"
    stakes = 300

    async def play(
        self,
        message: discord.Message,
        p1_id: int,
        p1_name: str,
        p2_id: int,
        p2_name: str,
    ) -> int:
        # Fresh shuffled deck
        deck = list(DECK)
        random.shuffle(deck)

        # Deal 2 cards each
        p1_hand = [deck.pop(), deck.pop()]
        p2_hand = [deck.pop(), deck.pop()]

        view = _BlackjackView(
            p1_id, p2_id, p1_name, p2_name, p1_hand, p2_hand, deck, message,
        )

        status = f"**{p1_name}** vs **{p2_name}** -- Hit or Stand!"
        embed = view._make_embed(status, discord.Colour.dark_purple())
        await message.edit(embed=embed, view=view)

        try:
            await asyncio.wait_for(view.done.wait(), timeout=30)
        except asyncio.TimeoutError:
            view.stop()
            # Auto-stand anyone who hasn't acted
            if p1_id not in view.stood:
                view.stood.add(p1_id)
            if p2_id not in view.stood:
                view.stood.add(p2_id)

        t1 = _bj_hand_total(p1_hand)
        t2 = _bj_hand_total(p2_hand)
        p1_bust = t1 > 21
        p2_bust = t2 > 21
        p1_bj = t1 == 21 and len(p1_hand) == 2
        p2_bj = t2 == 21 and len(p2_hand) == 2

        if p1_bust and p2_bust:
            winner_id = 0
            desc = "Both players busted -- draw!"
        elif p1_bust:
            winner_id = p2_id
            desc = f"**{p1_name}** busted! **{p2_name}** wins with {t2}!"
        elif p2_bust:
            winner_id = p1_id
            desc = f"**{p2_name}** busted! **{p1_name}** wins with {t1}!"
        elif p1_bj and not p2_bj:
            winner_id = p1_id
            desc = f"**{p1_name}** has Blackjack! **{p1_name}** wins!"
        elif p2_bj and not p1_bj:
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
            desc = f"Both have {t1} -- draw!"

        colour = discord.Colour.gold() if winner_id else discord.Colour.greyple()
        embed = view._make_embed(desc, colour)
        await message.edit(embed=embed, view=None)
        return winner_id


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
]


def pick_games(n: int = 3) -> list[MiniGame]:
    """Pick *n* random mini-games (no repeats)."""
    return random.sample(ALL_GAMES, min(n, len(ALL_GAMES)))
