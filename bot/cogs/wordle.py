"""Casino cog — multiplayer /wordle race game.

Everyone gets the same secret word. Fewest guesses wins the round.
First to WINS_TO_WIN round wins takes the pot. Guesses via button modal (private).
"""

import asyncio
import random
import time
from dataclasses import dataclass, field
from itertools import groupby

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries

# ── Constants ────────────────────────────────────────────────────────────────

MAX_PLAYERS = 8
MIN_PLAYERS = 2
ROUND_TIME = 300  # 5 minutes safety cap per round
ROUND_DELAY = 5  # seconds between rounds
WINS_TO_WIN = 3  # first to N wins
MAX_ROUNDS = 15  # safety cap
MAX_GUESSES = 6  # guesses per player per round

# Paytable: fraction of prize pool by finishing position, keyed by player count
PAYTABLE: dict[int, list[float]] = {
    2: [1.0],
    3: [0.70, 0.30],
    4: [0.55, 0.30, 0.15],
    5: [0.45, 0.25, 0.18, 0.12],
    6: [0.40, 0.24, 0.16, 0.12, 0.08],
    7: [0.36, 0.22, 0.16, 0.12, 0.08, 0.06],
    8: [0.33, 0.21, 0.16, 0.12, 0.08, 0.06, 0.04],
}

MEDALS = ["\U0001f947", "\U0001f948", "\U0001f949"]

# ── Word list ────────────────────────────────────────────────────────────────

WORDS: list[str] = [
    "ABOUT", "ABOVE", "ADAPT", "ADORE", "AFTER", "AGILE", "ALIEN", "ALIGN",
    "ALIVE", "ANGEL", "ANGER", "ANGLE", "APPLE", "ARISE", "ARMOR", "ATLAS",
    "AUDIO", "AVOID", "BADGE", "BASIC", "BEACH", "BEGIN", "BEING", "BELOW",
    "BENCH", "BLACK", "BLADE", "BLAME", "BLAND", "BLANK", "BLAST", "BLAZE",
    "BLEED", "BLEND", "BLESS", "BLIND", "BLOCK", "BLOOM", "BLOWN", "BOARD",
    "BONUS", "BOUND", "BRACE", "BRAIN", "BRAND", "BRAVE", "BREAD", "BREAK",
    "BREED", "BRICK", "BRIEF", "BRING", "BROAD", "BROOK", "BROWN", "BRUSH",
    "BUILD", "BUNCH", "BURST", "CANDY", "CATCH", "CAUSE", "CHAIN", "CHAIR",
    "CHARM", "CHASE", "CHEAP", "CHECK", "CHESS", "CHIEF", "CHILD", "CHILL",
    "CLAIM", "CLASS", "CLEAN", "CLEAR", "CLIMB", "CLING", "CLOCK", "CLONE",
    "CLOSE", "CLOUD", "COACH", "COAST", "COLOR", "COMET", "CORAL", "COUNT",
    "COURT", "COVER", "CRACK", "CRAFT", "CRANE", "CRASH", "CRAZY", "CREAM",
    "CREST", "CRIME", "CROSS", "CROWD", "CROWN", "CRUSH", "CURVE", "CYCLE",
    "DANCE", "DEALT", "DECAY", "DEMON", "DEPTH", "DODGE", "DOUBT", "DRAFT",
    "DRAIN", "DRAKE", "DRAWN", "DREAM", "DRESS", "DRIFT", "DRILL", "DRINK",
    "DRIVE", "DROWN", "EAGER", "EARLY", "EARTH", "EIGHT", "ELITE", "EMBER",
    "EMPTY", "ENEMY", "ENJOY", "ENTER", "EQUAL", "ERROR", "EVENT", "EVERY",
    "EXACT", "EXTRA", "FABLE", "FAITH", "FALSE", "FAVOR", "FEAST", "FIBER",
    "FIELD", "FIGHT", "FINAL", "FIRST", "FIXED", "FLAME", "FLASH", "FLEET",
    "FLESH", "FLOAT", "FLOOD", "FLOOR", "FLOSS", "FLOUR", "FLOWN", "FLUID",
    "FLUSH", "FOCAL", "FOCUS", "FORCE", "FORGE", "FOUND", "FRAME", "FRANK",
    "FRAUD", "FRESH", "FRONT", "FROST", "FRUIT", "GHOST", "GIANT", "GIVEN",
    "GLADE", "GLARE", "GLASS", "GLEAM", "GLOBE", "GLOOM", "GLORY", "GLOVE",
    "GOING", "GRACE", "GRADE", "GRAIN", "GRAND", "GRANT", "GRAPE", "GRASP",
    "GRASS", "GRAVE", "GREAT", "GREEN", "GRIND", "GROAN", "GROUP", "GROVE",
    "GROWL", "GUARD", "GUESS", "GUIDE", "GUILT", "HAPPY", "HARSH", "HASN'T",
    "HAVEN", "HEART", "HEAVY", "HENCE", "HONOR", "HORSE", "HOTEL", "HOUSE",
    "HUMAN", "HUMOR", "IDEAL", "IMAGE", "IMPLY", "INDEX", "INNER", "INPUT",
    "INTRO", "ISSUE", "IVORY", "JEWEL", "JOINT", "JOKER", "JUDGE", "JUICE",
    "KNIFE", "KNOCK", "KNOWN", "LABEL", "LARGE", "LASER", "LATER", "LAUGH",
    "LAYER", "LEARN", "LEASE", "LEAVE", "LEGAL", "LEMON", "LEVEL", "LIGHT",
    "LIMIT", "LINEN", "LIVER", "LOGIC", "LOOSE", "LOVER", "LOWER", "LUNAR",
    "LUNCH", "LYING", "MAGIC", "MAJOR", "MAKER", "MANOR", "MAPLE", "MARCH",
    "MATCH", "MAYOR", "MEDIA", "MERCY", "MERIT", "METAL", "MIGHT", "MINER",
    "MINOR", "MINUS", "MODEL", "MONEY", "MONTH", "MORAL", "MOUNT", "MOUSE",
    "MOUTH", "MOVIE", "MUSIC", "NASTY", "NERVE", "NEVER", "NIGHT", "NOBLE",
    "NOISE", "NORTH", "NOTED", "NOVEL", "NURSE", "OCCUR", "OCEAN", "OLIVE",
    "OPERA", "ORBIT", "ORDER", "OTHER", "OUTER", "OXIDE", "OZONE", "PAINT",
    "PANEL", "PAPER", "PATCH", "PAUSE", "PEACE", "PEACH", "PEARL", "PHASE",
    "PHONE", "PHOTO", "PIANO", "PIECE", "PILOT", "PITCH", "PIXEL", "PIZZA",
    "PLACE", "PLAIN", "PLANE", "PLANT", "PLATE", "PLAZA", "PLEAD", "PLUCK",
    "PLUMB", "PLUME", "PLUNGE", "POINT", "POLAR", "POUND", "POWER", "PRESS",
    "PRICE", "PRIDE", "PRIME", "PRINT", "PRIOR", "PRIZE", "PROBE", "PROOF",
    "PROUD", "PROVE", "PROXY", "PULSE", "PUNCH", "QUEEN", "QUEST", "QUEUE",
    "QUICK", "QUIET", "QUOTE", "RADAR", "RADIO", "RAISE", "RANGE", "RAPID",
    "RATIO", "REACH", "READY", "REALM", "REBEL", "REIGN", "RIDER", "RIDGE",
    "RIFLE", "RIGHT", "RIGID", "RISKY", "RIVAL", "RIVER", "ROAST", "ROBIN",
    "ROBOT", "ROCKY", "ROGUE", "ROMAN", "ROOST", "ROUND", "ROUTE", "ROYAL",
    "RUGBY", "RULER", "RURAL", "SAINT", "SALAD", "SAUCE", "SCALE", "SCARE",
    "SCENE", "SCOPE", "SCORE", "SCOUT", "SENSE", "SERVE", "SEVEN", "SHADE",
    "SHALL", "SHAME", "SHAPE", "SHARE", "SHARK", "SHARP", "SHELF", "SHELL",
    "SHIFT", "SHINE", "SHIRT", "SHOCK", "SHORE", "SHORT", "SHOUT", "SIGHT",
    "SINCE", "SIXTH", "SKILL", "SKULL", "SLASH", "SLATE", "SLEEP", "SLICE",
    "SLIDE", "SLOPE", "SMART", "SMELL", "SMILE", "SMOKE", "SNAKE", "SOLAR",
    "SOLID", "SOLVE", "SONIC", "SORRY", "SOUTH", "SPACE", "SPARE", "SPARK",
    "SPEAK", "SPEED", "SPEND", "SPICE", "SPINE", "SPLIT", "SPOKE", "SPOON",
    "SPORT", "SPRAY", "SQUAD", "STACK", "STAFF", "STAGE", "STAIN", "STAKE",
    "STALE", "STALL", "STAMP", "STAND", "STARE", "START", "STATE", "STAVE",
    "STEAL", "STEAM", "STEEL", "STEEP", "STEER", "STERN", "STICK", "STILL",
    "STOCK", "STONE", "STOOD", "STORE", "STORM", "STORY", "STOUT", "STOVE",
    "STRAW", "STRIP", "STUCK", "STUFF", "STYLE", "SUGAR", "SUITE", "SUPER",
    "SURGE", "SWAMP", "SWEAR", "SWEEP", "SWEET", "SWIFT", "SWING", "SWORD",
    "TABLE", "TASTE", "TEACH", "THEME", "THERE", "THICK", "THING", "THINK",
    "THIRD", "THOSE", "THREE", "THROW", "TIGER", "TIGHT", "TIMER", "TITLE",
    "TODAY", "TOKEN", "TOTAL", "TOUCH", "TOUGH", "TOWER", "TOXIC", "TRACE",
    "TRACK", "TRADE", "TRAIL", "TRAIN", "TRAIT", "TRASH", "TREAT", "TREND",
    "TRIAL", "TRIBE", "TRICK", "TRIED", "TROOP", "TRUCK", "TRULY", "TRUMP",
    "TRUNK", "TRUST", "TRUTH", "TWIST", "ULTRA", "UNDER", "UNION", "UNITE",
    "UNITY", "UNTIL", "UPPER", "UPSET", "URBAN", "USAGE", "USUAL", "UTTER",
    "VALID", "VALUE", "VAULT", "VIGOR", "VINYL", "VIRAL", "VITAL", "VIVID",
    "VOCAL", "VOICE", "VOTER", "WATCH", "WATER", "WEAVE", "WEIGH", "WEIRD",
    "WHEAT", "WHERE", "WHICH", "WHILE", "WHITE", "WHOLE", "WHOSE", "WIDTH",
    "WOMAN", "WORLD", "WORRY", "WORST", "WORTH", "WOULD", "WOUND", "WRATH",
    "WRITE", "WRONG", "YACHT", "YIELD", "YOUNG", "YOUTH",
]

# Filter to only true 5-letter words (safety)
WORDS = [w for w in WORDS if len(w) == 5 and w.isalpha()]


# ── Wordle logic ─────────────────────────────────────────────────────────────


def score_guess(guess: str, secret: str) -> list[str]:
    """Score a guess against the secret word.

    Returns list of 5 emojis: 🟩 (correct), 🟨 (wrong position), ⬛ (not in word).
    Handles duplicate letters correctly per standard Wordle rules.
    """
    guess = guess.upper()
    secret = secret.upper()
    result = [""] * 5

    # Track which secret letters are still available
    secret_remaining = list(secret)

    # First pass: mark greens
    for i in range(5):
        if guess[i] == secret[i]:
            result[i] = "\U0001f7e9"  # green
            secret_remaining[i] = ""  # consumed

    # Second pass: mark yellows and grays
    for i in range(5):
        if result[i]:  # already green
            continue
        if guess[i] in secret_remaining:
            result[i] = "\U0001f7e8"  # yellow
            # Consume the first available instance
            idx = secret_remaining.index(guess[i])
            secret_remaining[idx] = ""
        else:
            result[i] = "\u2b1b"  # black

    return result


def format_grid(guesses: list[str], secret: str) -> str:
    """Format all guesses as a Wordle-style grid."""
    lines: list[str] = []
    for guess in guesses:
        tiles = score_guess(guess, secret)
        letters = " ".join(f"**{c}**" for c in guess.upper())
        lines.append(f"{''.join(tiles)}  {letters}")
    # Pad remaining rows
    for _ in range(MAX_GUESSES - len(guesses)):
        lines.append("\u2b1b\u2b1b\u2b1b\u2b1b\u2b1b")
    return "\n".join(lines)


# ── Payout helpers ──────────────────────────────────────────────────────────


def _compute_payouts(
    players: dict[int, "WordlePlayer"], prize_pool: int, n_players: int,
) -> dict[int, int]:
    """Compute per-player payouts using the paytable."""
    pct_table = PAYTABLE.get(n_players, PAYTABLE[8])

    in_money = sorted(
        [p for p in players.values() if p.rounds_won > 0],
        key=lambda p: p.rounds_won,
        reverse=True,
    )

    payouts: dict[int, int] = {uid: 0 for uid in players}

    if not in_money:
        return payouts

    paid_positions = len(pct_table)
    pos = 0
    for _wins, group_iter in groupby(in_money, key=lambda p: p.rounds_won):
        group = list(group_iter)
        if pos >= paid_positions:
            break
        end = min(pos + len(group), paid_positions)
        combined_share = sum(pct_table[pos:end])
        per_player = int(prize_pool * combined_share / len(group))
        for p in group:
            payouts[p.user_id] = per_player
        pos += len(group)

    total_paid = sum(payouts.values())
    leftover = prize_pool - total_paid
    if leftover > 0 and in_money:
        top_wins = in_money[0].rounds_won
        top_group = [p for p in in_money if p.rounds_won == top_wins]
        extra = leftover // len(top_group)
        for p in top_group:
            payouts[p.user_id] += extra

    return payouts


# ── Dataclasses ─────────────────────────────────────────────────────────────


@dataclass
class WordlePlayer:
    user_id: int
    display_name: str
    bet: int
    rounds_won: int = 0
    guesses: list[str] = field(default_factory=list)
    solved: bool = False
    eliminated: bool = False
    solve_time: float = 0.0  # monotonic timestamp when solved (for tie-break)


@dataclass
class WordleTable:
    channel_id: int
    host_id: int
    host_name: str
    phase: str = "betting"  # betting | playing | between_rounds | closed
    players: dict[int, WordlePlayer] = field(default_factory=dict)
    message: discord.Message | None = None
    round_num: int = 0
    secret_word: str = ""
    round_start_time: float = 0.0
    round_winner: int | None = None
    race_task: asyncio.Task | None = field(default=None, repr=False)
    round_solved: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    last_bets: dict[int, tuple[str, int]] = field(default_factory=dict)
    total_rounds_played: int = 0
    used_words: list[str] = field(default_factory=list)


# ── Embeds ──────────────────────────────────────────────────────────────────


def _scoreboard(table: WordleTable) -> str:
    sorted_players = sorted(
        table.players.values(), key=lambda p: p.rounds_won, reverse=True,
    )
    lines: list[str] = []
    for i, p in enumerate(sorted_players):
        prefix = MEDALS[i] if i < len(MEDALS) and p.rounds_won > 0 else "\u25aa\ufe0f"
        line = f"{prefix} **{p.display_name}** \u2014 {p.rounds_won}/{WINS_TO_WIN}"
        if p.rounds_won == WINS_TO_WIN - 1:
            line += " *(match point!)*"
        lines.append(line)
    return "\n".join(lines) if lines else "No scores yet"


def _guess_status(table: WordleTable) -> str:
    """Show how many guesses each player has used (no spoilers)."""
    lines: list[str] = []
    for p in table.players.values():
        n = len(p.guesses)
        if p.solved:
            lines.append(f"\u2705 **{p.display_name}** \u2014 solved in {n}!")
        elif p.eliminated:
            lines.append(f"\U0001f6ab **{p.display_name}** \u2014 {n}/{MAX_GUESSES} (out)")
        else:
            lines.append(f"\U0001f7e6 **{p.display_name}** \u2014 {n}/{MAX_GUESSES}")
    return "\n".join(lines) if lines else "No guesses yet"


def _betting_embed(table: WordleTable) -> discord.Embed:
    pot = sum(p.bet for p in table.players.values())
    n = len(table.players)

    embed = discord.Embed(
        title="\U0001f1fc Wordle Race",
        description=(
            f"Guess the 5-letter word! **First to {WINS_TO_WIN} wins** takes the pot.\n"
            "Click **Guess** to submit privately \u2014 "
            "**fewest guesses** wins each round!"
        ),
        colour=discord.Colour.blue(),
    )

    if pot:
        embed.add_field(name="Pot", value=f"{pot}c", inline=True)
    embed.add_field(name="Goal", value=f"First to {WINS_TO_WIN}", inline=True)

    if n >= MIN_PLAYERS:
        pt = PAYTABLE.get(n, PAYTABLE[8])
        pt_parts = [
            f"{MEDALS[i] if i < 3 else chr(0x25aa) + chr(0xfe0f)} {int(s * 100)}%"
            for i, s in enumerate(pt)
        ]
        embed.add_field(name="Paytable", value=" | ".join(pt_parts), inline=True)

    if table.players:
        lines = [
            f"\U0001f4dd **{p.display_name}** \u2014 {p.bet}c"
            + (f" ({p.rounds_won}W)" if p.rounds_won > 0 else "")
            for p in table.players.values()
        ]
        embed.add_field(name="Players", value="\n".join(lines), inline=False)
    else:
        embed.add_field(
            name="Players",
            value="*No players yet \u2014 click Join!*",
            inline=False,
        )
    embed.set_footer(
        text=(
            f"Host: {table.host_name} \u2502 "
            f"Min {MIN_PLAYERS} players"
        ),
    )
    return embed


def _playing_embed(table: WordleTable, remaining: int | None = None) -> discord.Embed:
    embed = discord.Embed(
        title=f"\U0001f1fc Wordle \u2014 Round {table.round_num} (First to {WINS_TO_WIN})",
        colour=discord.Colour.gold(),
    )

    embed.description = (
        "# \u2753 \u2753 \u2753 \u2753 \u2753\n\n"
        "**Click the Guess button to submit your guess!**\n"
        f"You get **{MAX_GUESSES} guesses** \u2014 fewest guesses wins the round."
    )

    secs = remaining if remaining is not None else ROUND_TIME
    embed.add_field(name="\u23f1\ufe0f Time", value=f"**{secs}s**", inline=True)

    pot = sum(p.bet for p in table.players.values())
    embed.add_field(name="Pot", value=f"{pot}c", inline=True)

    embed.add_field(name="Guesses", value=_guess_status(table), inline=False)
    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


def _round_result_embed(table: WordleTable) -> discord.Embed:
    winner = table.players[table.round_winner]
    is_last = winner.rounds_won >= WINS_TO_WIN or table.round_num >= MAX_ROUNDS

    embed = discord.Embed(
        title=f"\U0001f1fc Wordle \u2014 Round {table.round_num} \u2705",
        colour=discord.Colour.green(),
    )

    # Show winner's grid
    grid = format_grid(winner.guesses, table.secret_word)

    # Who else solved it?
    also_solved: list[str] = []
    for p in table.players.values():
        if p.solved and p.user_id != table.round_winner:
            also_solved.append(f"{p.display_name} ({len(p.guesses)} tries)")

    desc = (
        f"\U0001f3c6 **{winner.display_name}** wins with "
        f"**{len(winner.guesses)}** {'guess' if len(winner.guesses) == 1 else 'guesses'}!\n"
    )
    if also_solved:
        desc += f"Also solved: {', '.join(also_solved)}\n"
    desc += f"\nThe word was: **{table.secret_word}**\n\n{grid}"
    embed.description = desc
    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    if not is_last:
        embed.set_footer(text="Next round in a few seconds\u2026")
    else:
        embed.set_footer(text="Final round complete \u2014 calculating results\u2026")
    return embed


def _timeout_embed(table: WordleTable) -> discord.Embed:
    max_wins = max((p.rounds_won for p in table.players.values()), default=0)
    is_last = max_wins >= WINS_TO_WIN or table.round_num >= MAX_ROUNDS

    embed = discord.Embed(
        title=f"\U0001f1fc Wordle \u2014 Round {table.round_num} (Time's Up!)",
        colour=discord.Colour.dark_grey(),
    )
    embed.description = (
        f"Nobody guessed it!\n\n"
        f"The word was: **{table.secret_word}**"
    )
    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    if not is_last:
        embed.set_footer(text="Next round in a few seconds\u2026")
    else:
        embed.set_footer(text="Final round complete \u2014 calculating results\u2026")
    return embed


def _final_embed(
    table: WordleTable,
    *,
    payouts: dict[int, int],
    balances: dict[int, int],
) -> discord.Embed:
    max_wins = max((p.rounds_won for p in table.players.values()), default=0)
    is_refund = max_wins == 0

    embed = discord.Embed(
        title="\U0001f1fc Wordle Race \u2014 Results",
        colour=discord.Colour.gold() if not is_refund else discord.Colour.dark_grey(),
    )

    if is_refund:
        embed.description = "No rounds were won \u2014 all bets refunded!"
    else:
        sorted_p = sorted(
            table.players.values(), key=lambda p: p.rounds_won, reverse=True,
        )
        winner = sorted_p[0]
        rw = winner.rounds_won
        embed.description = (
            f"\U0001f3c6 **{winner.display_name}** wins with "
            f"**{rw}** round{'s' if rw != 1 else ''}!"
        )

    sorted_players = sorted(
        table.players.values(), key=lambda p: p.rounds_won, reverse=True,
    )
    lines: list[str] = []
    for i, p in enumerate(sorted_players):
        payout = payouts.get(p.user_id, 0)
        bal = balances.get(p.user_id, 0)
        net = payout - p.bet
        sign = "+" if net >= 0 else ""
        medal = MEDALS[i] if i < len(MEDALS) and p.rounds_won > 0 else "\u25aa\ufe0f"
        lines.append(
            f"{medal} **{p.display_name}** ({p.rounds_won}W) \u2014 "
            f"{p.bet}c \u2192 {payout}c "
            f"(**{sign}{net}c**) \u2014 bal: {bal}c"
        )
    embed.add_field(name="Results", value="\n".join(lines), inline=False)

    if not is_refund:
        n = len(table.players)
        pt = PAYTABLE.get(n, PAYTABLE[8])
        pt_parts = [
            f"{MEDALS[i] if i < 3 else chr(0x25aa) + chr(0xfe0f)} {int(s * 100)}%"
            for i, s in enumerate(pt)
        ]
        embed.add_field(
            name=f"Paytable ({n} players)",
            value=" | ".join(pt_parts),
            inline=True,
        )

    embed.add_field(
        name="Rounds Played", value=str(table.total_rounds_played), inline=True,
    )
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


# ── Modals ──────────────────────────────────────────────────────────────────


class JoinWordleModal(ui.Modal):
    amount = ui.TextInput(
        label="Bet amount (coins)",
        placeholder="e.g. 100",
        required=True,
        max_length=10,
    )

    def __init__(
        self, table: WordleTable, view: "WordleTableView", balance: int,
    ) -> None:
        super().__init__(title="Join Wordle Race")
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
                "You're already in this game!", ephemeral=True,
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

        self.table.players[uid] = WordlePlayer(
            user_id=uid,
            display_name=interaction.user.display_name,
            bet=amt,
        )

        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self.table_view,
        )


class GuessModal(ui.Modal):
    word = ui.TextInput(
        label="Your guess (5 letters)",
        placeholder="e.g. CRANE",
        required=True,
        max_length=5,
        min_length=5,
        style=discord.TextStyle.short,
    )

    def __init__(self, table: WordleTable, view: "WordleTableView") -> None:
        super().__init__(title="Wordle \u2014 Guess")
        self.table = table
        self.table_view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        uid = interaction.user.id

        if uid not in self.table.players:
            await interaction.response.send_message(
                "You're not in this game!", ephemeral=True,
            )
            return
        if self.table.phase != "playing":
            await interaction.response.send_message(
                "Round is not active!", ephemeral=True,
            )
            return

        player = self.table.players[uid]
        if player.solved or player.eliminated:
            await interaction.response.send_message(
                "You're done for this round!", ephemeral=True,
            )
            return
        if len(player.guesses) >= MAX_GUESSES:
            await interaction.response.send_message(
                "No guesses remaining!", ephemeral=True,
            )
            return

        guess = self.word.value.strip().upper()
        if len(guess) != 5 or not guess.isalpha():
            await interaction.response.send_message(
                "Must be exactly 5 letters (A\u2013Z).", ephemeral=True,
            )
            return

        # Record the guess
        player.guesses.append(guess)

        # Check if correct
        if guess == self.table.secret_word:
            player.solved = True
            player.solve_time = time.monotonic()

            grid = format_grid(player.guesses, self.table.secret_word)
            await interaction.response.send_message(
                f"\u2705 **Correct!** Wait for others to finish.\n\n{grid}",
                ephemeral=True,
            )
        else:
            # Wrong guess
            if len(player.guesses) >= MAX_GUESSES:
                player.eliminated = True

            grid = format_grid(player.guesses, self.table.secret_word)
            remaining = MAX_GUESSES - len(player.guesses)
            if player.eliminated:
                msg = f"\u274c Out of guesses!\n\n{grid}"
            else:
                msg = f"\u274c Not quite! **{remaining}** guesses left.\n\n{grid}"

            await interaction.response.send_message(msg, ephemeral=True)

        # Update main embed to show guess count
        if self.table.message:
            try:
                await self.table.message.edit(
                    embed=_playing_embed(self.table), view=self.table_view,
                )
            except discord.HTTPException:
                pass

        # If all players are done (solved or eliminated), end the round
        if all(p.solved or p.eliminated for p in self.table.players.values()):
            self.table.round_solved.set()


# ── View ────────────────────────────────────────────────────────────────────


class WordleTableView(ui.View):
    def __init__(
        self, table: WordleTable, active_tables: dict[int, WordleTable],
    ) -> None:
        super().__init__(timeout=900)
        self.table = table
        self.active_tables = active_tables
        self._update_buttons()

    def _update_buttons(self) -> None:
        phase = self.table.phase
        betting = phase == "betting"
        playing = phase == "playing"
        racing = playing or phase == "between_rounds"

        self.start_btn.disabled = (
            not betting or len(self.table.players) < MIN_PLAYERS
        )
        self.join_btn.disabled = not betting
        self.rebet_btn.disabled = not betting or not self.table.last_bets
        self.leave_btn.disabled = not betting
        self.guess_btn.disabled = not playing
        self.close_btn.disabled = racing

    # ── Row 0: Betting ──────────────────────────────────────────────────

    @ui.button(
        label="Start", style=discord.ButtonStyle.success,
        emoji="\u25b6\ufe0f", row=0,
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
        if len(self.table.players) < MIN_PLAYERS:
            await interaction.response.send_message(
                f"Need at least {MIN_PLAYERS} players!", ephemeral=True,
            )
            return
        await self._start_race(interaction)

    @ui.button(
        label="Join", style=discord.ButtonStyle.primary,
        emoji="\U0001f4dd", row=0,
    )
    async def join_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Race in progress! Wait for the next game.", ephemeral=True,
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
        await interaction.response.send_modal(
            JoinWordleModal(self.table, self, bal),
        )

    @ui.button(
        label="Re-bet", style=discord.ButtonStyle.primary,
        emoji="\U0001f504", row=0,
    )
    async def rebet_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Race in progress!", ephemeral=True,
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
                "No previous bet \u2014 use Join instead.", ephemeral=True,
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
        self.table.players[uid] = WordlePlayer(
            user_id=uid, display_name=name, bet=amt,
        )
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    @ui.button(
        label="Leave", style=discord.ButtonStyle.secondary,
        emoji="\U0001f6aa", row=0,
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
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Can't leave during a race!", ephemeral=True,
            )
            return
        await queries.update_casino_balance(str(uid), player.bet)
        del self.table.players[uid]
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    # ── Row 1: Guess / Close ────────────────────────────────────────────

    @ui.button(
        label="Guess", style=discord.ButtonStyle.success,
        emoji="\u270d\ufe0f", row=1,
    )
    async def guess_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "playing":
            await interaction.response.send_message(
                "No round in progress!", ephemeral=True,
            )
            return
        uid = interaction.user.id
        if uid not in self.table.players:
            await interaction.response.send_message(
                "You're not in this game!", ephemeral=True,
            )
            return
        player = self.table.players[uid]
        if player.solved or player.eliminated:
            await interaction.response.send_message(
                "You're done for this round!", ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            GuessModal(self.table, self),
        )

    @ui.button(
        label="Close Table", style=discord.ButtonStyle.danger,
        emoji="\u2716\ufe0f", row=1,
    )
    async def close_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can close the table!", ephemeral=True,
            )
            return
        if self.table.phase in ("playing", "between_rounds"):
            await interaction.response.send_message(
                "Can't close during a race! Wait for it to finish.",
                ephemeral=True,
            )
            return
        await self._close_table(interaction)

    # ── Race logic ──────────────────────────────────────────────────────

    def _pick_word(self) -> str:
        """Pick a random word that hasn't been used yet."""
        available = [w for w in WORDS if w not in self.table.used_words]
        if not available:
            available = WORDS.copy()
            self.table.used_words.clear()
        word = random.choice(available)
        self.table.used_words.append(word)
        return word

    async def _start_race(self, interaction: discord.Interaction) -> None:
        table = self.table

        for uid, p in table.players.items():
            table.last_bets[uid] = (p.display_name, p.bet)

        word = self._pick_word()
        table.secret_word = word
        table.round_num = 1
        table.round_winner = None
        table.round_solved.clear()
        table.phase = "playing"
        table.round_start_time = time.monotonic()

        for p in table.players.values():
            p.guesses = []
            p.solved = False
            p.eliminated = False
            p.solve_time = 0.0

        self._update_buttons()
        await interaction.response.edit_message(
            embed=_playing_embed(table), view=self,
        )

        table.race_task = asyncio.create_task(self._race_loop())

    def _resolve_round_winner(self) -> None:
        """Determine the round winner: fewest guesses, tie-break by solve time."""
        table = self.table
        solvers = [p for p in table.players.values() if p.solved]
        if not solvers:
            table.round_winner = None
            return
        # Sort by fewest guesses, then earliest solve time
        solvers.sort(key=lambda p: (len(p.guesses), p.solve_time))
        winner = solvers[0]
        winner.rounds_won += 1
        table.round_winner = winner.user_id

    async def _wait_for_round_end(self) -> None:
        """Wait for all players to finish or for timeout."""
        table = self.table
        deadline = table.round_start_time + ROUND_TIME

        while True:
            now = time.monotonic()
            remaining = deadline - now
            if remaining <= 0:
                return

            wait = min(15.0, remaining)
            try:
                await asyncio.wait_for(table.round_solved.wait(), timeout=wait)
                return  # all players done
            except asyncio.TimeoutError:
                # Check if all done already
                if all(
                    p.solved or p.eliminated for p in table.players.values()
                ):
                    return
                now = time.monotonic()
                secs_left = max(0, int(deadline - now))
                if secs_left > 0 and table.message:
                    try:
                        await table.message.edit(
                            embed=_playing_embed(table, remaining=secs_left),
                            view=self,
                        )
                    except discord.HTTPException:
                        pass

    async def _race_loop(self) -> None:
        table = self.table
        try:
            rnd = 0
            while True:
                rnd += 1

                if rnd > 1:
                    word = self._pick_word()
                    table.secret_word = word
                    table.round_num = rnd
                    table.round_winner = None
                    table.round_solved.clear()
                    table.phase = "playing"
                    table.round_start_time = time.monotonic()

                    for p in table.players.values():
                        p.guesses = []
                        p.solved = False
                        p.eliminated = False
                        p.solve_time = 0.0

                    self._update_buttons()
                    if table.message:
                        try:
                            await table.message.edit(
                                embed=_playing_embed(table), view=self,
                            )
                        except discord.HTTPException:
                            pass

                await self._wait_for_round_end()
                self._resolve_round_winner()
                table.total_rounds_played += 1

                if table.round_winner is not None:
                    if table.message:
                        try:
                            await table.message.edit(
                                embed=_round_result_embed(table), view=self,
                            )
                        except discord.HTTPException:
                            pass
                else:
                    if table.message:
                        try:
                            await table.message.edit(
                                embed=_timeout_embed(table), view=self,
                            )
                        except discord.HTTPException:
                            pass

                if any(p.rounds_won >= WINS_TO_WIN for p in table.players.values()):
                    break
                if rnd >= MAX_ROUNDS:
                    break

                table.phase = "between_rounds"
                await asyncio.sleep(ROUND_DELAY)

            await self._end_game()

        except asyncio.CancelledError:
            pass
        except Exception:
            table.phase = "closed"
            self.active_tables.pop(table.channel_id, None)

    async def _compute_and_apply_payouts(
        self,
    ) -> tuple[dict[int, int], dict[int, int]]:
        table = self.table
        n_players = len(table.players)
        pot = sum(p.bet for p in table.players.values())
        max_wins = max((p.rounds_won for p in table.players.values()), default=0)

        if max_wins == 0:
            payouts = {uid: p.bet for uid, p in table.players.items()}
            for uid, refund in payouts.items():
                try:
                    await queries.update_casino_balance(str(uid), refund)
                except Exception:
                    pass
        else:
            payouts = _compute_payouts(table.players, pot, n_players)
            for uid, payout in payouts.items():
                if payout > 0:
                    try:
                        await queries.update_casino_balance(str(uid), payout)
                    except Exception:
                        pass

        balances: dict[int, int] = {}
        for uid in table.players:
            bal = await queries.get_casino_balance(str(uid))
            balances[uid] = bal or 0

        for uid, p in table.players.items():
            payout = payouts.get(uid, 0)
            await queries.log_casino_result(str(uid), "wordle", p.bet, payout)

        return payouts, balances

    async def _end_game(self) -> None:
        table = self.table
        table.phase = "closed"

        payouts, balances = await self._compute_and_apply_payouts()
        embed = _final_embed(table, payouts=payouts, balances=balances)

        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_tables.pop(table.channel_id, None)

        if table.message:
            try:
                await table.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass

    async def _close_table(self, interaction: discord.Interaction) -> None:
        table = self.table

        if table.total_rounds_played == 0:
            for p in table.players.values():
                try:
                    await queries.update_casino_balance(str(p.user_id), p.bet)
                except Exception:
                    pass
            embed = discord.Embed(
                title="\U0001f1fc Wordle Table \u2014 Closed",
                description="Table closed. All bets refunded.",
                colour=discord.Colour.dark_grey(),
            )
            for child in self.children:
                child.disabled = True  # type: ignore[union-attr]
            self.stop()
            self.active_tables.pop(table.channel_id, None)
            await interaction.response.edit_message(embed=embed, view=self)
            return

        table.phase = "closed"
        payouts, balances = await self._compute_and_apply_payouts()
        embed = _final_embed(table, payouts=payouts, balances=balances)

        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_tables.pop(table.channel_id, None)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        table = self.table

        if table.race_task and not table.race_task.done():
            table.race_task.cancel()

        if table.phase == "closed":
            return

        for p in table.players.values():
            try:
                await queries.update_casino_balance(str(p.user_id), p.bet)
            except Exception:
                pass

        table.phase = "closed"
        self.active_tables.pop(table.channel_id, None)

        if table.message:
            try:
                embed = discord.Embed(
                    title="\U0001f1fc Wordle Table \u2014 Timed Out",
                    description="Table timed out. All bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                pass


# ── Cog ─────────────────────────────────────────────────────────────────────


class WordleCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, WordleTable] = {}

    @app_commands.command(
        name="wordle",
        description="Open a Wordle Race table (multiplayer)",
    )
    async def wordle(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            await interaction.response.send_message(
                "There's already a Wordle table in this channel!",
                ephemeral=True,
            )
            return

        await queries.get_or_create_casino_wallet(str(interaction.user.id))

        table = WordleTable(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
        )
        self.active_tables[channel_id] = table

        view = WordleTableView(table, self.active_tables)
        embed = _betting_embed(table)
        await interaction.response.send_message(embed=embed, view=view)
        table.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WordleCog(bot))
