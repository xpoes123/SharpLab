"""Casino cog — multiplayer /wordle race game.

Everyone gets the same secret word. Fewest guesses wins the round.
First to WINS_TO_WIN round wins takes the pot. Guesses via button modal (private).
"""

import asyncio
import random
import time
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.cogs._elo_helpers import fmt_elo_change, update_elo_multiplayer
import logging

log = logging.getLogger(__name__)
# ── Constants ────────────────────────────────────────────────────────────────

MAX_PLAYERS = 8
MIN_PLAYERS = 1
ROUND_TIME = 300  # 5 minutes safety cap per round
ROUND_DELAY = 5  # seconds between rounds
WINS_TO_WIN = 3  # first to N wins
MAX_ROUNDS = 15  # safety cap
MAX_GUESSES = 6  # guesses per player per round

MEDALS = ["\U0001f947", "\U0001f948", "\U0001f949"]

# ── Word list ────────────────────────────────────────────────────────────────

WORDS: list[str] = [
    # ── A ──
    "ABACK", "ABATE", "ABBEY", "ABBOT", "ABIDE", "ABORT", "ABOUT", "ABOVE",
    "ABUSE", "ABYSS", "ACING", "ACORN", "ACUTE", "ADAPT", "ADDED", "ADEPT",
    "ADMIN", "ADMIT", "ADOBE", "ADOPT", "ADORE", "ADORN", "ADULT", "AFTER",
    "AGAIN", "AGENT", "AGILE", "AGING", "AGONY", "AGREE", "AHEAD", "AISLE",
    "ALARM", "ALBUM", "ALERT", "ALGAE", "ALIBI", "ALIEN", "ALIGN", "ALIKE",
    "ALIVE", "ALLEY", "ALLOT", "ALLOW", "ALLOY", "ALOFT", "ALONE", "ALONG",
    "ALOOF", "ALTER", "AMAZE", "AMBER", "AMEND", "AMPLE", "AMUSE", "ANGEL",
    "ANGER", "ANGLE", "ANGRY", "ANIME", "ANKLE", "ANNEX", "ANTIC", "ANVIL",
    "APART", "APPLE", "APPLY", "APRON", "ARENA", "ARGUE", "ARISE", "ARMOR",
    "AROMA", "AROSE", "ARRAY", "ARROW", "ARSON", "ASSET", "ATLAS", "ATONE",
    "ATTIC", "AUDIO", "AUDIT", "AVAIL", "AVERT", "AVOID", "AWAIT", "AWAKE",
    "AWARD", "AWARE", "AWFUL", "AWOKE", "AXIOM",
    # ── B ──
    "BADGE", "BADLY", "BAGEL", "BARON", "BASIC", "BASIN", "BASIS", "BATCH",
    "BEACH", "BEARD", "BEAST", "BEGAN", "BEGIN", "BEGUN", "BEING", "BELLE",
    "BELOW", "BENCH", "BERRY", "BIBLE", "BLACK", "BLADE", "BLAME", "BLAND",
    "BLANK", "BLAST", "BLAZE", "BLEAK", "BLEED", "BLEND", "BLESS", "BLIMP",
    "BLIND", "BLINK", "BLISS", "BLITZ", "BLOAT", "BLOCK", "BLOKE", "BLOND",
    "BLOOD", "BLOOM", "BLOWN", "BLUFF", "BLUNT", "BLURT", "BLUSH", "BOARD",
    "BOAST", "BONUS", "BOOTH", "BOUND", "BRACE", "BRAID", "BRAIN", "BRAND",
    "BRASS", "BRAVE", "BRAWN", "BREAD", "BREAK", "BREED", "BRICK", "BRIDE",
    "BRIEF", "BRING", "BRINK", "BRISK", "BROAD", "BROIL", "BROKE", "BROOK",
    "BROOD", "BROWN", "BRUSH", "BRUTE", "BUILD", "BUILT", "BULGE", "BUMPY",
    "BUNCH", "BURST", "BUYER",
    # ── C ──
    "CABIN", "CABLE", "CAMEL", "CANDY", "CARGO", "CARRY", "CARVE", "CATCH",
    "CATER", "CAUSE", "CEASE", "CEDAR", "CHAIN", "CHAIR", "CHALK", "CHAMP",
    "CHAOS", "CHARM", "CHART", "CHASE", "CHEAP", "CHEAT", "CHECK", "CHEEK",
    "CHEER", "CHESS", "CHEST", "CHICK", "CHIEF", "CHILD", "CHILL", "CHINA",
    "CHIRP", "CHOIR", "CHOSE", "CHUNK", "CHURN", "CIDER", "CIGAR", "CINCH",
    "CIVIC", "CIVIL", "CLAIM", "CLAMP", "CLANG", "CLASH", "CLASP", "CLASS",
    "CLEAN", "CLEAR", "CLERK", "CLICK", "CLIFF", "CLIMB", "CLING", "CLOCK",
    "CLONE", "CLOSE", "CLOTH", "CLOUD", "CLOWN", "COACH", "COAST", "COLOR",
    "COMET", "COMIC", "CORAL", "COUNT", "COUCH", "COULD", "COURT", "COVER",
    "CRACK", "CRAFT", "CRANE", "CRASH", "CRATE", "CRAVE", "CRAWL", "CRAZE",
    "CRAZY", "CREAK", "CREAM", "CREEK", "CREEP", "CREST", "CREW", "CRIME",
    "CRISP", "CROSS", "CROWD", "CROWN", "CRUDE", "CRUEL", "CRUSH", "CRUST",
    "CUBIC", "CURSE", "CURVE", "CYCLE",
    # ── D ──
    "DAILY", "DAIRY", "DANCE", "DEALT", "DEATH", "DEBUT", "DECAY", "DECOR",
    "DECOY", "DECRY", "DEFER", "DEITY", "DELAY", "DELTA", "DELVE", "DEMON",
    "DENSE", "DEPOT", "DEPTH", "DERBY", "DEVIL", "DIARY", "DIGIT", "DINER",
    "DIRTY", "DISCO", "DITCH", "DIZZY", "DODGE", "DONOR", "DOUBT", "DOUGH",
    "DRAFT", "DRAIN", "DRAKE", "DRAMA", "DRANK", "DRAPE", "DRAWN", "DREAD",
    "DREAM", "DRESS", "DRIED", "DRIFT", "DRILL", "DRINK", "DRIVE", "DRONE",
    "DROWN", "DRUNK", "DRYER", "DRYLY", "DUMMY", "DUNCE", "DUSTY", "DWARF",
    "DWELL", "DYING",
    # ── E ──
    "EAGER", "EAGLE", "EARLY", "EARTH", "EASEL", "EATER", "EDICT", "EIGHT",
    "ELDER", "ELECT", "ELITE", "ELUDE", "EMAIL", "EMBER", "ЕМРТУ", "EMPTY",
    "ENEMY", "ENJOY", "ENTER", "ENTRY", "ENVOY", "EPOCH", "EQUAL", "EQUIP",
    "ERASE", "ERODE", "ERROR", "ERUPT", "ESSAY", "ETHIC", "EVADE", "EVENT",
    "EVERY", "EVICT", "EVOKE", "EXACT", "EXALT", "EXCEL", "EXERT", "EXILE",
    "EXIST", "EXPEL", "EXTRA",
    # ── F ──
    "FABLE", "FACET", "FAITH", "FALSE", "FANCY", "FATAL", "FATTY", "FAULT",
    "FAUNA", "FAVOR", "FEAST", "FEIGN", "FENCE", "FERRY", "FETCH", "FEVER",
    "FIBER", "FIBRE", "FIELD", "FIEND", "FIGHT", "FILTH", "FINAL", "FINCH",
    "FIRST", "FIXED", "FLAIR", "FLAKE", "FLAME", "FLANK", "FLARE", "FLASH",
    "FLASK", "FLEET", "FLESH", "FLICK", "FLING", "FLINT", "FLOAT", "FLOCK",
    "FLOOD", "FLOOR", "FLORA", "FLOUR", "FLOWN", "FLUID", "FLUKE", "FLUNG",
    "FLUSH", "FLUTE", "FOCAL", "FOCUS", "FOGGY", "FOLLY", "FORCE", "FORGE",
    "FORTY", "FORUM", "FOUND", "FRAME", "FRANK", "FRAUD", "FREAK", "FREED",
    "FRESH", "FRIAR", "FRONT", "FROST", "FROZE", "FRUIT", "FULLY", "FUNGI",
    "FUNKY", "FUNNY", "FURY", "FUSSY", "FUZZY",
    # ── G ──
    "GAFFE", "GAUGE", "GAVEL", "GIDDY", "GIVEN", "GLAND", "GLARE", "GLASS",
    "GLAZE", "GLEAM", "GLIDE", "GLOBE", "GLOOM", "GLORY", "GLOSS", "GLOVE",
    "GOING", "GOOSE", "GORGE", "GRACE", "GRADE", "GRAFT", "GRAIN", "GRAND",
    "GRANT", "GRAPE", "GRAPH", "GRASP", "GRASS", "GRATE", "GRAVE", "GRAVY",
    "GRAZE", "GREAT", "GREED", "GREEN", "GREET", "GRIEF", "GRILL", "GRIME",
    "GRIND", "GRIPE", "GROAN", "GROOM", "GROPE", "GROSS", "GROUP", "GROUT",
    "GROVE", "GROWL", "GROWN", "GRUEL", "GUARD", "GUESS", "GUEST", "GUIDE",
    "GUILD", "GUILT", "GUISE", "GULCH", "GULLY", "GUMBO", "GUPPY", "GUSTO",
    "GUSTY", "GYPSY",
    # ── H ──
    "HABIT", "HANDY", "HAPPY", "HARSH", "HASTE", "HASTY", "HATCH", "HAUNT",
    "HAVEN", "HAVOC", "HEART", "HEAVY", "HEDGE", "HEIST", "HENCE", "HIPPO",
    "HITCH", "HOARD", "HOBBY", "HOMER", "HONOR", "HORSE", "HOTEL", "HOUND",
    "HOUSE", "HOVER", "HUMAN", "HUMID", "HUMOR", "HUMPS", "HURRY", "HYENA",
    "HYPER",
    # ── I ──
    "ICING", "IDEAL", "IMAGE", "IMPLY", "INANE", "INCUR", "INDEX", "INERT",
    "INFER", "INLET", "INNER", "INPUT", "INTER", "INTRO", "IONIC", "IRATE",
    "IRONY", "IVORY", "ISSUE",
    # ── J ──
    "JEWEL", "JIFFY", "JOINT", "JOKER", "JOLLY", "JOUST", "JUDGE", "JUICE",
    "JUICY", "JUMBO", "JUMPY", "JUROR",
    # ── K ──
    "KAYAK", "KEBAB", "KHAKI", "KINKY", "KNACK", "KNEAD", "KNEEL", "KNELT",
    "KNIFE", "KNOCK", "KNOLL", "KNOWN",
    # ── L ──
    "LABEL", "LABOR", "LAPSE", "LARGE", "LARVA", "LASER", "LATCH", "LATER",
    "LAUGH", "LAYER", "LEACH", "LEAFY", "LEAKY", "LEAPT", "LEARN", "LEASE",
    "LEASH", "LEAST", "LEAVE", "LEDGE", "LEGAL", "LEMON", "LEVEL", "LEVER",
    "LIGHT", "LIMBO", "LINEN", "LINER", "LINGO", "LIVER", "LLAMA", "LOBBY",
    "LOCAL", "LODGE", "LOFTY", "LOGIC", "LONER", "LOOSE", "LORRY", "LOSER",
    "LOTUS", "LOVER", "LOWER", "LOYAL", "LUCID", "LUCKY", "LUMEN", "LUNAR",
    "LUNCH", "LUNGE", "LUSTY", "LYING", "LYRIC",
    # ── M ──
    "MACRO", "MAGIC", "MAJOR", "MAKER", "MANOR", "MAPLE", "MARCH", "MARSH",
    "MATCH", "MAYOR", "MEALY", "MEDIA", "MERCY", "MERGE", "MERIT", "MERRY",
    "METAL", "METER", "MIGHT", "MIMIC", "MINCE", "MINER", "MINOR", "MINUS",
    "MIRTH", "MISER", "MISTY", "MIXER", "MOCHA", "MODEL", "MOIST", "MOLAR",
    "MONEY", "MONTH", "MOODY", "MORAL", "MORPH", "MOTEL", "MOTIF", "MOTOR",
    "MOTTO", "MOUND", "MOUNT", "MOURN", "MOUSE", "MOUTH", "MOVER", "MOVIE",
    "MUDDY", "MURAL", "MURKY", "MUSIC", "MUSTY", "MYRRH",
    # ── N ──
    "NAIVE", "NASTY", "NAVAL", "NERVE", "NEVER", "NEWLY", "NICHE", "NIGHT",
    "NOBLE", "NOISE", "NOISY", "NORTH", "NOTCH", "NOTED", "NOVEL", "NUDGE",
    "NURSE",
    # ── O ──
    "OASIS", "OCCUR", "OCEAN", "OLIVE", "ONSET", "OPERA", "OPTIC", "ORBIT",
    "ORDER", "OTHER", "OUGHT", "OUNCE", "OUTER", "OUTDO", "OVERT", "OXIDE",
    "OZONE",
    # ── P ──
    "PADDY", "PAGAN", "PAINT", "PANDA", "PANEL", "PANIC", "PAPER", "PARCH",
    "PARTY", "PASTA", "PASTE", "PATCH", "PAUSE", "PEACE", "PEACH", "PEARL",
    "PENAL", "PENCE", "PENNY", "PERCH", "PERIL", "PERKY", "PESTO", "PETTY",
    "PHASE", "PHONE", "PHOTO", "PIANO", "PIECE", "PILOT", "PINCH", "PITCH",
    "PIXEL", "PIXIE", "PIZZA", "PLACE", "PLAID", "PLAIN", "PLANE", "PLANK",
    "PLANT", "PLATE", "PLAZA", "PLEAD", "PLEAT", "PLIER", "PLUCK", "PLUMB",
    "PLUME", "PLUMP", "PLUNGE", "PLUNK", "POINT", "POISE", "POLAR", "POOCH",
    "POPPY", "PORCH", "POSER", "POUCH", "POUND", "POWER", "PRANK", "PRAWN",
    "PRESS", "PRICE", "PRIDE", "PRIME", "PRINT", "PRIOR", "PRISM", "PRIVY",
    "PRIZE", "PROBE", "PRONE", "PROOF", "PROSE", "PROUD", "PROVE", "PROWL",
    "PROXY", "PRUDE", "PRUNE", "PSALM", "PULSE", "PUNCH", "PUPIL", "PUPPY",
    "PURSE", "PUSHY",
    # ── Q ──
    "QUACK", "QUALM", "QUART", "QUEEN", "QUERY", "QUEST", "QUEUE", "QUICK",
    "QUIET", "QUILL", "QUIRK", "QUOTA", "QUOTE",
    # ── R ──
    "RABBI", "RADAR", "RADIO", "RAINY", "RAISE", "RALLY", "RANCH", "RANGE",
    "RAPID", "RARER", "RATIO", "RAVEN", "REACH", "REACT", "READY", "REALM",
    "REBEL", "REBUS", "RECAP", "REFER", "REIGN", "RELAX", "RELAY", "RELIC",
    "REMIT", "RENEW", "REPAY", "REPEL", "REPLY", "RESIN", "RETRO", "RETRY",
    "REVEL", "RIDER", "RIDGE", "RIFLE", "RIGHT", "RIGID", "RIGOR", "RINSE",
    "RISKY", "RIVAL", "RIVER", "RIVET", "ROAST", "ROBIN", "ROBOT", "ROCKY",
    "RODEO", "ROGUE", "ROMAN", "ROOST", "ROUND", "ROUTE", "ROVER", "ROWDY",
    "ROYAL", "RUGBY", "RULER", "RUMBA", "RUMOR", "RUPEE", "RURAL", "RUSTY",
    # ── S ──
    "SAINT", "SALAD", "SALON", "SALSA", "SALTY", "SALVE", "SANDY", "SAUCE",
    "SAUNA", "SAVOR", "SAVVY", "SCALE", "SCALP", "SCALD", "SCARE", "SCARF",
    "SCARY", "SCENE", "SCENT", "SCOFF", "SCOLD", "SCONE", "SCOPE", "SCORE",
    "SCORN", "SCOUT", "SCOWL", "SCRAM", "SCRAP", "SCREW", "SCRUB", "SEDAN",
    "SEIZE", "SENSE", "SERVE", "SETUP", "SEVEN", "SHADE", "SHADY", "SHAFT",
    "SHALL", "SHAME", "SHAPE", "SHARD", "SHARE", "SHARK", "SHARP", "SHAWL",
    "SHEAR", "SHEEN", "SHEEP", "SHEER", "SHELF", "SHELL", "SHIFT", "SHINE",
    "SHINY", "SHIRT", "SHOCK", "SHORE", "SHORN", "SHORT", "SHOUT", "SHOVE",
    "SHOWN", "SHRUG", "SIGHT", "SIGMA", "SILLY", "SINCE", "SIREN", "SIXTH",
    "SIXTY", "SKATE", "SKETC", "SKILL", "SKIMP", "SKULL", "SKUNK", "SLACK",
    "SLAIN", "SLANG", "SLANT", "SLASH", "SLATE", "SLEEK", "SLEEP", "SLEET",
    "SLICE", "SLIDE", "SLIME", "SLING", "SLOPE", "SLOTH", "SLUMP", "SLUNG",
    "SMART", "SMEAR", "SMELL", "SMILE", "SMIRK", "SMITH", "SMOKE", "SNACK",
    "SNAIL", "SNAKE", "SNARE", "SNEAK", "SNEER", "SNIDE", "SNIFF", "SNORE",
    "SOLAR", "SOLID", "SOLVE", "SONIC", "SORRY", "SOUND", "SOUTH", "SPACE",
    "SPADE", "SPARE", "SPARK", "SPAWN", "SPEAK", "SPEAR", "SPEED", "SPELL",
    "SPEND", "SPENT", "SPICE", "SPICY", "SPILL", "SPINE", "SPITE", "SPLIT",
    "SPOKE", "SPOOK", "SPOON", "SPORT", "SPOUT", "SPRAY", "SPREE", "SQUAD",
    "SQUAT", "SQUID", "STACK", "STAFF", "STAGE", "STAIN", "STAIR", "STAKE",
    "STALE", "STALK", "STALL", "STAMP", "STAND", "STANK", "STARE", "STARK",
    "START", "STASH", "STATE", "STAVE", "STAYS", "STEAK", "STEAL", "STEAM",
    "STEEL", "STEEP", "STEER", "STERN", "STICK", "STIFF", "STILL", "STING",
    "STINK", "STINT", "STOCK", "STOIC", "STOKE", "STOLE", "STOMP", "STONE",
    "STOOD", "STOOL", "STOOP", "STORE", "STORK", "STORM", "STORY", "STOUT",
    "STOVE", "STRAW", "STRAY", "STRIP", "STRUM", "STRUT", "STUCK", "STUDY",
    "STUFF", "STUMP", "STUNG", "STUNK", "STUNT", "STYLE", "SUAVE", "SUGAR",
    "SUITE", "SULKY", "SUNNY", "SUPER", "SURGE", "SUSHI", "SWAMP", "SWARM",
    "SWEAR", "SWEAT", "SWEEP", "SWEET", "SWELL", "SWEPT", "SWIFT", "SWILL",
    "SWINE", "SWING", "SWIPE", "SWIRL", "SWOOP", "SWORD", "SWORE", "SWORN",
    "SWUNG", "SYRUP",
    # ── T ──
    "TABBY", "TABLE", "TACIT", "TAINT", "TAKEN", "TALLY", "TALON", "TANGY",
    "TANGO", "TAPIR", "TASTE", "TASTY", "TAUNT", "TEACH", "TEASE", "TEMPO",
    "TENET", "TENOR", "TENSE", "TEPID", "TERRA", "THEME", "THERE", "THICK",
    "THIEF", "THIGH", "THING", "THINK", "THIRD", "THORN", "THOSE", "THREE",
    "THREW", "THROW", "THRUM", "THUMB", "TIARA", "TIGER", "TIGHT", "TILTS",
    "TIMER", "TIMID", "TITLE", "TOAST", "TODAY", "TOKEN", "TOTAL", "TOUCH",
    "TOUGH", "TOWEL", "TOWER", "TOXIC", "TRACE", "TRACK", "TRADE", "TRAIL",
    "TRAIN", "TRAIT", "TRAMP", "TRASH", "TRAWL", "TREAT", "TREND", "TRIAL",
    "TRIBE", "TRICK", "TRIED", "TRILL", "TRIPE", "TRITE", "TROLL", "TROOP",
    "TROPE", "TROUT", "TRUCK", "TRULY", "TRUMP", "TRUNK", "TRUSS", "TRUST",
    "TRUTH", "TUBBY", "TULIP", "TUMOR", "TUNER", "TUNIC", "TURBO", "TUTOR",
    "TWANG", "TWEED", "TWICE", "TWINE", "TWIST",
    # ── U ──
    "UDDER", "ULCER", "ULTRA", "UMBRA", "UNCLE", "UNCUT", "UNDER", "UNDID",
    "UNDUE", "UNFIT", "UNION", "UNITE", "UNITY", "UNLIT", "UNTIL", "UPPER",
    "UPSET", "URBAN", "USAGE", "USHER", "USUAL", "UTTER",
    # ── V ──
    "VAGUE", "VALID", "VALOR", "VALUE", "VALVE", "VAULT", "VEINS", "VENUE",
    "VERGE", "VERSE", "VIGOR", "VINYL", "VIOLA", "VIPER", "VIRAL", "VISOR",
    "VISTA", "VITAL", "VIVID", "VOCAL", "VODKA", "VOGUE", "VOICE", "VOTER",
    "VOUCH", "VOWEL", "VULVA",
    # ── W ──
    "WAFER", "WAGER", "WAGON", "WAIST", "WATCH", "WATER", "WAVER", "WEARY",
    "WEAVE", "WEDGE", "WEIGH", "WEIRD", "WHALE", "WHEAT", "WHERE", "WHICH",
    "WHILE", "WHINE", "WHIRL", "WHISK", "WHITE", "WHOLE", "WHOSE", "WIDEN",
    "WIDTH", "WIELD", "WINCH", "WITCH", "WOMAN", "WORLD", "WORRY", "WORSE",
    "WORST", "WORTH", "WOULD", "WOUND", "WRATH", "WREAK", "WRECK", "WRING",
    "WRIST", "WRITE", "WRONG", "WROTE", "WRYLY",
    # ── X/Y/Z ──
    "XENON", "YACHT", "YEARN", "YEAST", "YIELD", "YOUNG", "YOUTH", "ZEBRA",
    "ZESTY",
]

# Filter to only true 5-letter words (safety)
WORDS = sorted(set(w for w in WORDS if len(w) == 5 and w.isalpha()))

VOWELS = set("AEIOUY")


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


def check_hard_mode(guess: str, previous_guesses: list[str], secret: str) -> str | None:
    """Enforce hard mode: greens must stay, yellows must be reused.

    Returns an error message if the guess violates hard mode, or None if valid.
    """
    if not previous_guesses:
        return None  # first guess — no constraints

    # Build constraints from ALL previous guesses
    locked_positions: dict[int, str] = {}  # pos → letter (must be there)
    required_letters: set[str] = set()  # letters that must appear somewhere

    for prev in previous_guesses:
        prev = prev.upper()
        secret_upper = secret.upper()
        remaining = list(secret_upper)

        # First pass: find greens
        for i in range(5):
            if prev[i] == secret_upper[i]:
                locked_positions[i] = prev[i]
                remaining[i] = ""

        # Second pass: find yellows
        for i in range(5):
            if prev[i] == secret_upper[i]:
                continue  # already green
            if prev[i] in remaining:
                required_letters.add(prev[i])
                idx = remaining.index(prev[i])
                remaining[idx] = ""

    # Check the new guess against constraints
    guess = guess.upper()

    # Greens: letter must be in the exact same position
    for pos, letter in locked_positions.items():
        if guess[pos] != letter:
            return f"Position {pos + 1} must be **{letter}** (green)."

    # Yellows: letter must appear somewhere in the guess
    for letter in required_letters:
        if letter not in guess:
            return f"Guess must contain **{letter}** (yellow)."

    return None


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


# ── Dataclasses ─────────────────────────────────────────────────────────────


@dataclass
class WordlePlayer:
    user_id: int
    display_name: str
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
    embed = discord.Embed(
        title="\U0001f1fc Wordle Race",
        description=(
            f"Guess the 5-letter word! **First to {WINS_TO_WIN} wins** takes the match.\n"
            "Click **Guess** to submit privately \u2014 "
            "**fewest guesses** wins each round!\n"
            "\U0001f525 **Hard mode** \u2014 you must use all revealed hints."
        ),
        colour=discord.Colour.blue(),
    )

    embed.add_field(name="Goal", value=f"First to {WINS_TO_WIN}", inline=True)

    if table.players:
        lines = [
            f"\U0001f4dd **{p.display_name}**"
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
        f"You get **{MAX_GUESSES} guesses** \u2014 fewest guesses wins the round.\n"
        "\U0001f525 **Hard mode** \u2014 greens must stay, yellows must be reused."
    )

    secs = remaining if remaining is not None else ROUND_TIME
    embed.add_field(name="\u23f1\ufe0f Time", value=f"**{secs}s**", inline=True)

    embed.add_field(name="Guesses", value=_guess_status(table), inline=False)
    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


def _all_grids(table: WordleTable) -> list[tuple[str, str]]:
    """Build (name, grid_text) for every player who made at least one guess."""
    results: list[tuple[str, str]] = []
    for p in table.players.values():
        if not p.guesses:
            continue
        grid = format_grid(p.guesses, table.secret_word)
        tag = "\u2705" if p.solved else f"\u274c ({len(p.guesses)}/{MAX_GUESSES})"
        results.append((f"{p.display_name} {tag}", grid))
    return results


def _round_result_embed(table: WordleTable) -> discord.Embed:
    winner = table.players[table.round_winner]
    is_last = winner.rounds_won >= WINS_TO_WIN or table.round_num >= MAX_ROUNDS

    embed = discord.Embed(
        title=f"\U0001f1fc Wordle \u2014 Round {table.round_num} \u2705",
        colour=discord.Colour.green(),
    )

    desc = (
        f"\U0001f3c6 **{winner.display_name}** wins with "
        f"**{len(winner.guesses)}** {'guess' if len(winner.guesses) == 1 else 'guesses'}!\n"
        f"The word was: **{table.secret_word}**"
    )
    embed.description = desc

    # Show everyone's grids
    for name, grid in _all_grids(table):
        embed.add_field(name=name, value=grid, inline=True)

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
    embed.description = f"The word was: **{table.secret_word}**"

    # Show everyone's grids
    for name, grid in _all_grids(table):
        embed.add_field(name=name, value=grid, inline=True)

    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    if not is_last:
        embed.set_footer(text="Next round in a few seconds\u2026")
    else:
        embed.set_footer(text="Final round complete \u2014 calculating results\u2026")
    return embed


def _final_embed(table: WordleTable, elo_changes: dict[int, tuple[float, float]] | None = None) -> discord.Embed:
    max_wins = max((p.rounds_won for p in table.players.values()), default=0)

    embed = discord.Embed(
        title="\U0001f1fc Wordle Race \u2014 Results",
        colour=discord.Colour.gold() if max_wins > 0 else discord.Colour.dark_grey(),
    )

    if max_wins == 0:
        embed.description = "No rounds were won."
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
        medal = MEDALS[i] if i < len(MEDALS) and p.rounds_won > 0 else "\u25aa\ufe0f"
        lines.append(f"{medal} **{p.display_name}** \u2014 {p.rounds_won}W")
    embed.add_field(name="Results", value="\n".join(lines), inline=False)

    embed.add_field(
        name="Rounds Played", value=str(table.total_rounds_played), inline=True,
    )

    if elo_changes:
        sorted_players = sorted(table.players.values(), key=lambda p: p.rounds_won, reverse=True)
        elo_lines: list[str] = []
        for p in sorted_players:
            if p.user_id in elo_changes:
                old, new = elo_changes[p.user_id]
                elo_lines.append(f"**{p.display_name}**: {fmt_elo_change(old, new)}")
        if elo_lines:
            embed.add_field(name="\U0001f4c8 ELO", value="\n".join(elo_lines), inline=False)

    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


# ── Modals ──────────────────────────────────────────────────────────────────


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

        # Basic sanity: must contain at least one vowel
        if not any(c in VOWELS for c in guess):
            await interaction.response.send_message(
                f"**{guess}** doesn't look like a real word.", ephemeral=True,
            )
            return

        # Hard mode: must reuse green/yellow info from previous guesses
        hard_err = check_hard_mode(guess, player.guesses, self.table.secret_word)
        if hard_err:
            await interaction.response.send_message(
                f"\U0001f6ab **Hard mode:** {hard_err}", ephemeral=True,
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
        self.table.players[uid] = WordlePlayer(
            user_id=uid,
            display_name=interaction.user.display_name,
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
            log.exception("Unexpected error in _game_loop — closing table")
            table.phase = "closed"
            self.active_tables.pop(table.channel_id, None)
            self.stop()
            if table.message:
                try:
                    embed = discord.Embed(
                        title="Wordle — Error",
                        description="An unexpected error occurred. The game has been closed.",
                        colour=discord.Colour.red(),
                    )
                    await table.message.edit(embed=embed, view=None)
                except discord.HTTPException:
                    pass

    async def _end_game(self) -> None:
        table = self.table
        table.phase = "closed"

        elo_changes: dict[int, tuple[float, float]] = {}
        if len(table.players) >= 2:
            sorted_p = sorted(table.players.values(), key=lambda p: p.rounds_won, reverse=True)
            finish_order = [p.user_id for p in sorted_p]
            try:
                elo_changes = await update_elo_multiplayer(finish_order, "wordle", "wordle")
            except Exception:
                log.exception("Unhandled error in wordle.py")

        embed = _final_embed(table, elo_changes)

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
            embed = discord.Embed(
                title="\U0001f1fc Wordle Table \u2014 Closed",
                description="Table closed.",
                colour=discord.Colour.dark_grey(),
            )
            for child in self.children:
                child.disabled = True  # type: ignore[union-attr]
            self.stop()
            self.active_tables.pop(table.channel_id, None)
            await interaction.response.edit_message(embed=embed, view=self)
            return

        table.phase = "closed"
        embed = _final_embed(table)

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

        table.phase = "closed"
        self.active_tables.pop(table.channel_id, None)

        if table.message:
            try:
                embed = discord.Embed(
                    title="\U0001f1fc Wordle Table \u2014 Timed Out",
                    description="Table timed out.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                log.exception("Unhandled error in wordle.py")


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
            existing = self.active_tables[channel_id]
            _has_running = any(
                (t := getattr(existing, n, None)) is not None and not t.done()
                for n in ("game_task", "race_task", "sim_task", "round_task", "_round_task", "trade_task", "fly_task", "_shot_clock_task", "_countdown_task")
            )
            if _has_running:
                await interaction.response.send_message(
                    "There's already a Wordle table in this channel!",
                    ephemeral=True,
                )
                return
            del self.active_tables[channel_id]

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
