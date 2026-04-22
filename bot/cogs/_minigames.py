"""Mini-game engine for 1v1 duels and tournaments.

Seven quick games playable between two players. Each game class exposes a
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


# ── 1. HigherCard ───────────────────────────────────────────────────────────

SUITS = ["♠", "♥", "♦", "♣"]  # Spades > Hearts > Diamonds > Clubs
SUIT_RANK = {s: i for i, s in enumerate(reversed(SUITS))}  # ♠=3, ♥=2, ♦=1, ♣=0
VALUES = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
VALUE_RANK = {v: i for i, v in enumerate(VALUES)}  # 2=0 .. A=12

DECK = [(v, s) for s in SUITS for v in VALUES]


def _card_str(value: str, suit: str) -> str:
    """Render a card like 'A♠' with suit coloring hint."""
    return f"**{value}{suit}**"


def _card_rank(value: str, suit: str) -> tuple[int, int]:
    """Return (value_rank, suit_rank) for comparison."""
    return (VALUE_RANK[value], SUIT_RANK[suit])


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

        # Dramatic reveal
        embed = discord.Embed(
            title=f"{self.emoji} Higher Card",
            description="Drawing cards...",
            colour=discord.Colour.blue(),
        )
        embed.add_field(name=p1_name, value="🎴", inline=True)
        embed.add_field(name="vs", value="\u200b", inline=True)
        embed.add_field(name=p2_name, value="🎴", inline=True)
        await message.edit(embed=embed, view=None)
        await asyncio.sleep(1.5)

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
            embed.description = f"**{winner_name}** wins with {_card_str(*({c1} if winner_id == p1_id else {c2}))}!"
            # Simpler approach:
            winning_card = c1 if winner_id == p1_id else c2
            embed.description = f"**{winner_name}** wins with {_card_str(*winning_card)}!"
        else:
            embed.description = "It's a tie!"

        await message.edit(embed=embed, view=None)
        return winner_id


# ── 2. DiceRoll ─────────────────────────────────────────────────────────────

DICE_EMOJI = {1: "⚀", 2: "⚁", 3: "⚂", 4: "⚃", 5: "⚄", 6: "⚅"}


def _roll_2d6() -> tuple[int, int]:
    return (random.randint(1, 6), random.randint(1, 6))


def _dice_display(d1: int, d2: int) -> str:
    return f"{DICE_EMOJI[d1]} {DICE_EMOJI[d2]}  =  **{d1 + d2}**"


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
            d1a, d1b = _roll_2d6()
            d2a, d2b = _roll_2d6()
            t1 = d1a + d1b
            t2 = d2a + d2b

            round_label = f" (Reroll {attempt})" if attempt > 0 else ""

            embed = discord.Embed(
                title=f"{self.emoji} Dice Roll{round_label}",
                description="Rolling...",
                colour=discord.Colour.orange(),
            )
            await message.edit(embed=embed, view=None)
            await asyncio.sleep(1.2)

            if t1 > t2:
                winner_id, winner_name = p1_id, p1_name
            elif t2 > t1:
                winner_id, winner_name = p2_id, p2_name
            else:
                winner_id = 0

            if winner_id or attempt == max_rounds - 1:
                # Final result
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
                    embed.description = "Tied after 3 rolls — it's a draw!"

                await message.edit(embed=embed, view=None)
                return winner_id

            # Tie — show it and reroll
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


# ── 3. Rock Paper Scissors ──────────────────────────────────────────────────

RPS_BEATS = {"Rock": "Scissors", "Paper": "Rock", "Scissors": "Paper"}
RPS_EMOJI = {"Rock": "🪨", "Paper": "📄", "Scissors": "✂️"}


class _RPSView(ui.View):
    """View with Rock/Paper/Scissors buttons for two players."""

    def __init__(self, p1_id: int, p2_id: int) -> None:
        super().__init__(timeout=15)
        self.p1_id = p1_id
        self.p2_id = p2_id
        self.choices: dict[int, str] = {}
        self.done = asyncio.Event()

    def _check_done(self) -> None:
        if self.p1_id in self.choices and self.p2_id in self.choices:
            self.done.set()
            self.stop()

    async def _handle_choice(self, interaction: discord.Interaction, choice: str) -> None:
        uid = interaction.user.id
        if uid not in (self.p1_id, self.p2_id):
            await interaction.response.send_message(
                "You're not in this duel!", ephemeral=True,
            )
            return
        if uid in self.choices:
            await interaction.response.send_message(
                f"You already chose {RPS_EMOJI[self.choices[uid]]} {self.choices[uid]}.",
                ephemeral=True,
            )
            return
        self.choices[uid] = choice
        await interaction.response.send_message(
            f"You chose {RPS_EMOJI[choice]} {choice}.", ephemeral=True,
        )
        self._check_done()

    @ui.button(label="Rock", emoji="🪨", style=discord.ButtonStyle.primary)
    async def rock(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._handle_choice(interaction, "Rock")

    @ui.button(label="Paper", emoji="📄", style=discord.ButtonStyle.primary)
    async def paper(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._handle_choice(interaction, "Paper")

    @ui.button(label="Scissors", emoji="✂️", style=discord.ButtonStyle.primary)
    async def scissors(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._handle_choice(interaction, "Scissors")


class RockPaperScissors:
    name = "Rock Paper Scissors"
    emoji = "✂️"
    stakes = 200

    async def play(
        self,
        message: discord.Message,
        p1_id: int,
        p1_name: str,
        p2_id: int,
        p2_name: str,
    ) -> int:
        max_rounds = 2  # play once, replay on tie, then draw

        for attempt in range(max_rounds):
            round_label = " (Replay)" if attempt > 0 else ""

            embed = discord.Embed(
                title=f"{self.emoji} Rock Paper Scissors{round_label}",
                description=(
                    f"**{p1_name}** vs **{p2_name}**\n\n"
                    "Both players: pick your move!"
                ),
                colour=discord.Colour.purple(),
            )

            view = _RPSView(p1_id, p2_id)
            await message.edit(embed=embed, view=view)

            try:
                await asyncio.wait_for(view.done.wait(), timeout=15)
            except asyncio.TimeoutError:
                view.stop()

            c1 = view.choices.get(p1_id)
            c2 = view.choices.get(p2_id)

            # If one player didn't pick, the other wins
            if c1 and not c2:
                await self._show_result(message, p1_name, c1, p2_name, None, p1_id, p1_name, round_label)
                return p1_id
            if c2 and not c1:
                await self._show_result(message, p1_name, None, p2_name, c2, p2_id, p2_name, round_label)
                return p2_id
            if not c1 and not c2:
                await self._show_timeout(message, round_label)
                return 0

            # Both chose
            if c1 == c2:
                # Tie
                if attempt < max_rounds - 1:
                    embed = discord.Embed(
                        title=f"{self.emoji} Rock Paper Scissors{round_label}",
                        description=(
                            f"{RPS_EMOJI[c1]} **{c1}** vs **{c2}** {RPS_EMOJI[c2]}\n\n"
                            "Tie! Replaying..."
                        ),
                        colour=discord.Colour.orange(),
                    )
                    embed.add_field(name=p1_name, value=f"{RPS_EMOJI[c1]} {c1}", inline=True)
                    embed.add_field(name="vs", value="\u200b", inline=True)
                    embed.add_field(name=p2_name, value=f"{RPS_EMOJI[c2]} {c2}", inline=True)
                    await message.edit(embed=embed, view=None)
                    await asyncio.sleep(1.5)
                    continue
                else:
                    await self._show_result(message, p1_name, c1, p2_name, c2, 0, "Nobody", round_label)
                    return 0

            # Determine winner
            if RPS_BEATS[c1] == c2:
                winner_id, winner_name = p1_id, p1_name
            else:
                winner_id, winner_name = p2_id, p2_name

            await self._show_result(message, p1_name, c1, p2_name, c2, winner_id, winner_name, round_label)
            return winner_id

        return 0  # unreachable

    async def _show_result(
        self,
        message: discord.Message,
        p1_name: str,
        c1: str | None,
        p2_name: str,
        c2: str | None,
        winner_id: int,
        winner_name: str,
        round_label: str,
    ) -> None:
        result_colour = discord.Colour.gold() if winner_id else discord.Colour.greyple()
        embed = discord.Embed(
            title=f"{self.emoji} Rock Paper Scissors{round_label}",
            colour=result_colour,
        )
        p1_display = f"{RPS_EMOJI[c1]} {c1}" if c1 else "*(timed out)*"
        p2_display = f"{RPS_EMOJI[c2]} {c2}" if c2 else "*(timed out)*"
        embed.add_field(name=p1_name, value=p1_display, inline=True)
        embed.add_field(name="vs", value="\u200b", inline=True)
        embed.add_field(name=p2_name, value=p2_display, inline=True)

        if winner_id:
            embed.description = f"**{winner_name}** wins!"
        else:
            embed.description = "It's a draw!"

        await message.edit(embed=embed, view=None)

    async def _show_timeout(self, message: discord.Message, round_label: str) -> None:
        embed = discord.Embed(
            title=f"{self.emoji} Rock Paper Scissors{round_label}",
            description="Neither player chose in time — draw!",
            colour=discord.Colour.greyple(),
        )
        await message.edit(embed=embed, view=None)


# ── 4. Speed Math ───────────────────────────────────────────────────────────

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
        # Proxy resolved state
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
            # Nobody answered
            embed = discord.Embed(
                title=f"{self.emoji} Speed Math",
                description=(
                    f"**{problem} = {answer}**\n\n"
                    "Time's up! Neither player answered — draw."
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
                f"**{responder_name}** answered wrong — **{other_name}** wins!"
            )

        embed = discord.Embed(
            title=f"{self.emoji} Speed Math",
            description=desc,
            colour=discord.Colour.gold(),
        )
        await message.edit(embed=embed, view=None)
        return winner_id


# ── 5. Trivia ───────────────────────────────────────────────────────────────

# (question, [A, B, C, D], correct_index)
TRIVIA_POOL: list[tuple[str, list[str], int]] = [
    (
        "Which NBA team has won the most championships?",
        ["Lakers", "Celtics", "Bulls", "Warriors"],
        1,
    ),
    (
        "Who holds the NBA record for most points in a single game?",
        ["Kobe Bryant", "Michael Jordan", "Wilt Chamberlain", "LeBron James"],
        2,
    ),
    (
        "What year was the first Super Bowl played?",
        ["1965", "1966", "1967", "1968"],
        2,
    ),
    (
        "Which MLB team has won the most World Series titles?",
        ["Boston Red Sox", "St. Louis Cardinals", "New York Yankees", "Los Angeles Dodgers"],
        2,
    ),
    (
        "Who has the most career home runs in MLB history?",
        ["Babe Ruth", "Hank Aaron", "Barry Bonds", "Alex Rodriguez"],
        2,
    ),
    (
        "Which country has won the most FIFA World Cup titles?",
        ["Germany", "Argentina", "Italy", "Brazil"],
        3,
    ),
    (
        "What is the diameter of a basketball hoop in inches?",
        ["16", "18", "20", "22"],
        1,
    ),
    (
        "Who won the first-ever NBA MVP award?",
        ["George Mikan", "Bob Cousy", "Bill Russell", "Bob Pettit"],
        3,
    ),
    (
        "How many players are on an NFL team's active roster?",
        ["46", "48", "52", "53"],
        3,
    ),
    (
        "Which tennis player has won the most Grand Slam singles titles (men's)?",
        ["Roger Federer", "Rafael Nadal", "Novak Djokovic", "Pete Sampras"],
        2,
    ),
    (
        "What is the length of an NBA court in feet?",
        ["84", "90", "94", "100"],
        2,
    ),
    (
        "Who holds the NFL record for most career passing touchdowns?",
        ["Peyton Manning", "Tom Brady", "Drew Brees", "Aaron Rodgers"],
        1,
    ),
    (
        "Which team won the first NBA game ever played?",
        ["New York Knicks", "Toronto Huskies", "Boston Celtics", "Philadelphia Warriors"],
        0,
    ),
    (
        "How many dimples does a regulation golf ball typically have?",
        ["252", "336", "392", "412"],
        1,
    ),
    (
        "Who was the first player to break the MLB color barrier?",
        ["Satchel Paige", "Larry Doby", "Jackie Robinson", "Willie Mays"],
        2,
    ),
    (
        "What is the maximum number of clubs allowed in a golf bag during a round?",
        ["12", "14", "16", "18"],
        1,
    ),
    (
        "Which NBA player is known as 'The Greek Freak'?",
        ["Luka Doncic", "Nikola Jokic", "Giannis Antetokounmpo", "Joel Embiid"],
        2,
    ),
    (
        "How long is a standard NHL ice rink in feet?",
        ["180", "190", "200", "210"],
        2,
    ),
    (
        "Who scored the 'Hand of God' goal in the 1986 World Cup?",
        ["Pele", "Zinedine Zidane", "Diego Maradona", "Ronaldo"],
        2,
    ),
    (
        "What sport is played at Wimbledon?",
        ["Cricket", "Tennis", "Golf", "Polo"],
        1,
    ),
    (
        "Which boxer was known as 'The Greatest'?",
        ["Mike Tyson", "Floyd Mayweather", "Muhammad Ali", "Sugar Ray Leonard"],
        2,
    ),
    (
        "How many periods are in a regulation NHL hockey game?",
        ["2", "3", "4", "5"],
        1,
    ),
    (
        "What is the only team to go 16-0 in the NFL regular season?",
        ["1985 Bears", "2007 Patriots", "1972 Dolphins", "2013 Broncos"],
        1,
    ),
    (
        "Who holds the NBA record for most assists in a career?",
        ["Magic Johnson", "Jason Kidd", "John Stockton", "Steve Nash"],
        2,
    ),
    (
        "In baseball, how many balls result in a walk?",
        ["3", "4", "5", "6"],
        1,
    ),
    (
        "Which team has the most Premier League titles?",
        ["Liverpool", "Chelsea", "Arsenal", "Manchester United"],
        3,
    ),
    (
        "How many rings are on the Olympic flag?",
        ["4", "5", "6", "7"],
        1,
    ),
    (
        "Who was the NBA's first overall draft pick in 2003?",
        ["Carmelo Anthony", "Dwyane Wade", "Chris Bosh", "LeBron James"],
        3,
    ),
    (
        "What is the distance of a marathon in miles (approximately)?",
        ["24.2", "25.2", "26.2", "27.2"],
        2,
    ),
    (
        "Which NFL quarterback has the most Super Bowl wins?",
        ["Joe Montana", "Tom Brady", "Terry Bradshaw", "Peyton Manning"],
        1,
    ),
]

OPTION_LABELS = ["A", "B", "C", "D"]
OPTION_STYLES = [
    discord.ButtonStyle.primary,
    discord.ButtonStyle.primary,
    discord.ButtonStyle.primary,
    discord.ButtonStyle.primary,
]


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

        # Create buttons dynamically
        for i, option in enumerate(options):
            button = ui.Button(
                label=f"{OPTION_LABELS[i]}: {option}",
                style=discord.ButtonStyle.primary,
                custom_id=f"trivia_{i}",
                row=i // 2,  # 2 buttons per row
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
                    f"You picked **{label}** — Correct!", ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    f"You picked **{label}** — Wrong!", ephemeral=True,
                )
            self.done.set()
            self.stop()

        return callback


class Trivia:
    name = "Sports Trivia"
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
        question, options, correct_index = random.choice(TRIVIA_POOL)

        embed = discord.Embed(
            title=f"{self.emoji} Sports Trivia",
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
                title=f"{self.emoji} Sports Trivia",
                description=(
                    f"**{question}**\n\n"
                    f"Answer: {correct_label}\n\n"
                    "Time's up! Neither player answered — draw."
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
                f"**{responder_name}** picked {chosen_label} — Correct! **{responder_name}** wins!"
            )
            colour = discord.Colour.gold()
        else:
            winner_id = other_id
            desc = (
                f"**{question}**\n\n"
                f"Answer: {correct_label}\n\n"
                f"**{responder_name}** picked {chosen_label} — Wrong! **{other_name}** wins!"
            )
            colour = discord.Colour.gold()

        embed = discord.Embed(
            title=f"{self.emoji} Sports Trivia",
            description=desc,
            colour=colour,
        )
        await message.edit(embed=embed, view=None)
        return winner_id


# ── 6. Reaction ─────────────────────────────────────────────────────────────


class _ReactionWaitView(ui.View):
    """View shown during 'Get ready...' phase to catch false starts."""

    def __init__(self, p1_id: int, p2_id: int) -> None:
        super().__init__(timeout=None)  # Managed externally
        self.p1_id = p1_id
        self.p2_id = p2_id
        self.false_starter: int | None = None
        self.done = asyncio.Event()

    @ui.button(label="Wait...", emoji="🔴", style=discord.ButtonStyle.danger, disabled=False)
    async def wait_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        if uid not in (self.p1_id, self.p2_id):
            await interaction.response.send_message(
                "You're not in this duel!", ephemeral=True,
            )
            return
        # False start!
        self.false_starter = uid
        await interaction.response.send_message(
            "False start! You clicked too early!", ephemeral=True,
        )
        self.done.set()
        self.stop()


class _ReactionGoView(ui.View):
    """View shown when 'GO!' appears."""

    def __init__(self, p1_id: int, p2_id: int) -> None:
        super().__init__(timeout=10)
        self.p1_id = p1_id
        self.p2_id = p2_id
        self.clicker: int | None = None
        self.done = asyncio.Event()

    @ui.button(label="CLICK!", emoji="🟢", style=discord.ButtonStyle.success)
    async def go_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        if uid not in (self.p1_id, self.p2_id):
            await interaction.response.send_message(
                "You're not in this duel!", ephemeral=True,
            )
            return
        if self.clicker is not None:
            await interaction.response.send_message(
                "Someone already clicked!", ephemeral=True,
            )
            return
        self.clicker = uid
        await interaction.response.send_message(
            "You got it!", ephemeral=True,
        )
        self.done.set()
        self.stop()


class Reaction:
    name = "Reaction Time"
    emoji = "⚡"
    stakes = 200

    async def play(
        self,
        message: discord.Message,
        p1_id: int,
        p1_name: str,
        p2_id: int,
        p2_name: str,
    ) -> int:
        # Phase 1: "Get ready..." with false-start detection
        wait_view = _ReactionWaitView(p1_id, p2_id)

        embed = discord.Embed(
            title=f"{self.emoji} Reaction Time",
            description=(
                f"**{p1_name}** vs **{p2_name}**\n\n"
                "Get ready... **DO NOT** click yet!\n"
                "Wait for the green button..."
            ),
            colour=discord.Colour.red(),
        )
        await message.edit(embed=embed, view=wait_view)

        delay = random.uniform(2.0, 5.0)
        try:
            await asyncio.wait_for(wait_view.done.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass  # No false start — proceed to GO

        wait_view.stop()

        if wait_view.false_starter is not None:
            # Someone clicked early
            fs_id = wait_view.false_starter
            fs_name = p1_name if fs_id == p1_id else p2_name
            winner_id = p2_id if fs_id == p1_id else p1_id
            winner_name = p2_name if fs_id == p1_id else p1_name

            embed = discord.Embed(
                title=f"{self.emoji} Reaction Time",
                description=(
                    f"**{fs_name}** jumped the gun! False start!\n\n"
                    f"**{winner_name}** wins!"
                ),
                colour=discord.Colour.gold(),
            )
            await message.edit(embed=embed, view=None)
            return winner_id

        # Phase 2: GO!
        go_view = _ReactionGoView(p1_id, p2_id)

        embed = discord.Embed(
            title=f"{self.emoji} Reaction Time",
            description="# GO! Click now!",
            colour=discord.Colour.green(),
        )
        await message.edit(embed=embed, view=go_view)

        try:
            await asyncio.wait_for(go_view.done.wait(), timeout=10)
        except asyncio.TimeoutError:
            go_view.stop()

        if go_view.clicker is None:
            embed = discord.Embed(
                title=f"{self.emoji} Reaction Time",
                description="Neither player clicked in time — draw!",
                colour=discord.Colour.greyple(),
            )
            await message.edit(embed=embed, view=None)
            return 0

        winner_id = go_view.clicker
        winner_name = p1_name if winner_id == p1_id else p2_name

        embed = discord.Embed(
            title=f"{self.emoji} Reaction Time",
            description=f"**{winner_name}** clicked first and wins!",
            colour=discord.Colour.gold(),
        )
        await message.edit(embed=embed, view=None)
        return winner_id


# ── 7. Guess The Number ────────────────────────────────────────────────────


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

        # Build result
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
            embed.description = f"The number was **{target}**. Neither player guessed — draw!"
        else:
            embed.description = f"The number was **{target}**. Equal distance — draw!"

        await message.edit(embed=embed, view=None)
        return winner_id


# ── Registry & Picker ───────────────────────────────────────────────────────

ALL_GAMES: list[MiniGame] = [
    HigherCard(),
    DiceRoll(),
    RockPaperScissors(),
    SpeedMath(),
    Trivia(),
    Reaction(),
    GuessTheNumber(),
]


def pick_games(n: int = 3) -> list[MiniGame]:
    """Pick *n* random mini-games (no repeats)."""
    return random.sample(ALL_GAMES, min(n, len(ALL_GAMES)))
