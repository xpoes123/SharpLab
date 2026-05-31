"""Shared answer-bank categories for party guessing games (cluemaster, imposter).

Each category exposes a list of items as `(name, [aliases])`. Add new
categories by populating CATEGORIES; both /cluemaster and /imposter pick
them up automatically.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from difflib import SequenceMatcher

import httpx

from bot.cogs.nbaguess import NBA_PLAYERS_DATA
from db import queries

log = logging.getLogger(__name__)

SCIENCE_DB_CATEGORY = "science"  # qb_answers.category value for the science pool


# ── Category data ────────────────────────────────────────────────────────────

# (name, [aliases])
CategoryItem = tuple[str, list[str]]


def _nba_items() -> list[CategoryItem]:
    out: list[CategoryItem] = []
    for entry in NBA_PLAYERS_DATA:
        # entry shape: (id, name, [alts], [stints], stats)
        _, name, alts, *_ = entry
        out.append((name, list(alts)))
    return out


# Famous people across music, film, sports, and tech. Last-name matching in
# check_answer means a guess of just the surname counts; aliases cover
# nicknames and accent-free spellings.
_CELEBRITIES: list[CategoryItem] = [
    ("Taylor Swift", []),
    ("Beyoncé", ["Beyonce"]),
    ("Leonardo DiCaprio", ["Leo DiCaprio"]),
    ("Tom Cruise", []),
    ("Brad Pitt", []),
    ("Will Smith", []),
    ("Dwayne Johnson", ["The Rock"]),
    ("Kim Kardashian", []),
    ("Kanye West", ["Ye"]),
    ("Rihanna", []),
    ("Drake", []),
    ("Adele", []),
    ("Lady Gaga", []),
    ("Justin Bieber", []),
    ("Ariana Grande", []),
    ("Ed Sheeran", []),
    ("Bruno Mars", []),
    ("Billie Eilish", []),
    ("The Weeknd", ["Weeknd"]),
    ("Eminem", ["Marshall Mathers", "Slim Shady"]),
    ("Snoop Dogg", []),
    ("Jay-Z", ["Jay Z"]),
    ("Oprah Winfrey", ["Oprah"]),
    ("Jennifer Lawrence", []),
    ("Scarlett Johansson", []),
    ("Robert Downey Jr", ["Robert Downey", "RDJ"]),
    ("Chris Hemsworth", []),
    ("Ryan Reynolds", []),
    ("Keanu Reeves", []),
    ("Morgan Freeman", []),
    ("Denzel Washington", []),
    ("Tom Hanks", []),
    ("Johnny Depp", []),
    ("Angelina Jolie", []),
    ("Emma Watson", []),
    ("Zendaya", []),
    ("Timothée Chalamet", ["Timothee Chalamet"]),
    ("Margot Robbie", []),
    ("Elon Musk", []),
    ("Bill Gates", []),
    ("Jeff Bezos", []),
    ("Cristiano Ronaldo", ["Ronaldo"]),
    ("Lionel Messi", ["Messi"]),
    ("Serena Williams", []),
    ("Michael Jackson", []),
    ("Elvis Presley", ["Elvis"]),
    ("Kylie Jenner", []),
    ("Selena Gomez", []),
    ("Dua Lipa", []),
    ("Post Malone", []),
]

# Single-word commons get a plural alias so "pizzas"/"lions" still match
# (the fuzzy branch only fires on guesses of 5+ chars).
_ANIMALS: list[CategoryItem] = [
    ("Elephant", ["Elephants"]), ("Lion", ["Lions"]), ("Tiger", ["Tigers"]),
    ("Giraffe", ["Giraffes"]), ("Penguin", ["Penguins"]), ("Kangaroo", ["Kangaroos"]),
    ("Dolphin", ["Dolphins"]), ("Shark", ["Sharks"]), ("Octopus", ["Octopuses"]),
    ("Panda", ["Pandas"]), ("Koala", ["Koalas"]), ("Zebra", ["Zebras"]),
    ("Hippopotamus", ["Hippo", "Hippos"]), ("Rhinoceros", ["Rhino", "Rhinos"]),
    ("Cheetah", ["Cheetahs"]), ("Gorilla", ["Gorillas"]), ("Crocodile", ["Crocodiles", "Croc"]),
    ("Alligator", ["Alligators", "Gator"]), ("Eagle", ["Eagles"]), ("Owl", ["Owls"]),
    ("Flamingo", ["Flamingos"]), ("Peacock", ["Peacocks"]), ("Squirrel", ["Squirrels"]),
    ("Hedgehog", ["Hedgehogs"]), ("Raccoon", ["Raccoons"]), ("Wolf", ["Wolves"]),
    ("Fox", ["Foxes"]), ("Bear", ["Bears"]), ("Rabbit", ["Rabbits", "Bunny"]),
    ("Horse", ["Horses"]), ("Camel", ["Camels"]), ("Sloth", ["Sloths"]),
    ("Otter", ["Otters"]), ("Walrus", ["Walruses"]), ("Whale", ["Whales"]),
    ("Jellyfish", []), ("Butterfly", ["Butterflies"]), ("Spider", ["Spiders"]),
    ("Snake", ["Snakes"]), ("Frog", ["Frogs"]), ("Turtle", ["Turtles"]),
    ("Bat", ["Bats"]), ("Ostrich", ["Ostriches"]), ("Platypus", []),
]

_FOOD: list[CategoryItem] = [
    ("Pizza", ["Pizzas"]), ("Hamburger", ["Hamburgers", "Burger"]), ("Sushi", []),
    ("Tacos", ["Taco"]), ("Spaghetti", []), ("Ice Cream", []), ("Pancakes", ["Pancake"]),
    ("French Fries", ["Fries"]), ("Hot Dog", ["Hotdog", "Hot Dogs"]), ("Popcorn", []),
    ("Chocolate", []), ("Donut", ["Donuts", "Doughnut"]), ("Bacon", []),
    ("Cheeseburger", ["Cheeseburgers"]), ("Burrito", ["Burritos"]), ("Ramen", []),
    ("Steak", ["Steaks"]), ("Sandwich", ["Sandwiches"]), ("Cookie", ["Cookies"]),
    ("Cupcake", ["Cupcakes"]), ("Waffle", ["Waffles"]), ("Nachos", ["Nacho"]),
    ("Lasagna", []), ("Pretzel", ["Pretzels"]), ("Bagel", ["Bagels"]),
    ("Croissant", ["Croissants"]), ("Muffin", ["Muffins"]), ("Pie", ["Pies"]),
    ("Brownie", ["Brownies"]), ("Omelette", ["Omelet"]),
    ("Dumpling", ["Dumplings"]), ("Quesadilla", ["Quesadillas"]), ("Meatball", ["Meatballs"]),
    ("Pickle", ["Pickles"]), ("Watermelon", ["Watermelons"]), ("Pineapple", ["Pineapples"]),
    ("Avocado", ["Avocados"]), ("Marshmallow", ["Marshmallows"]), ("Cotton Candy", []),
]

_MOVIES: list[CategoryItem] = [
    ("Titanic", []), ("Avatar", []), ("The Godfather", ["Godfather"]),
    ("Jurassic Park", []), ("Star Wars", []), ("The Lion King", ["Lion King"]),
    ("Frozen", []), ("Inception", []), ("The Matrix", ["Matrix"]),
    ("Forrest Gump", []), ("Jaws", []), ("Gladiator", []),
    ("The Avengers", ["Avengers"]), ("Spider-Man", ["Spiderman"]), ("Batman", []),
    ("Shrek", []), ("Toy Story", []), ("Finding Nemo", []),
    ("Harry Potter", []), ("The Dark Knight", ["Dark Knight"]), ("Pulp Fiction", []),
    ("Interstellar", []), ("Joker", []), ("Aladdin", []),
    ("Inside Out", []), ("Moana", []), ("Encanto", []), ("Barbie", []),
    ("Oppenheimer", []), ("The Wizard of Oz", ["Wizard of Oz"]),
    ("Ghostbusters", []), ("Home Alone", []), ("Top Gun", []),
    ("Pirates of the Caribbean", []), ("The Lord of the Rings", ["Lord of the Rings"]),
    ("Back to the Future", []), ("Jumanji", []), ("Coco", []),
    ("The Incredibles", ["Incredibles"]), ("Despicable Me", []),
]

_OBJECTS: list[CategoryItem] = [
    ("Umbrella", ["Umbrellas"]), ("Toothbrush", ["Toothbrushes"]), ("Bicycle", ["Bicycles", "Bike"]),
    ("Backpack", ["Backpacks"]), ("Refrigerator", ["Fridge"]), ("Microwave", ["Microwaves"]),
    ("Television", ["TV", "Televisions"]), ("Headphones", ["Headphone"]), ("Sunglasses", []),
    ("Pillow", ["Pillows"]), ("Blanket", ["Blankets"]), ("Scissors", []),
    ("Hammer", ["Hammers"]), ("Ladder", ["Ladders"]), ("Vacuum", ["Vacuums"]),
    ("Toaster", ["Toasters"]), ("Wallet", ["Wallets"]), ("Mirror", ["Mirrors"]),
    ("Candle", ["Candles"]), ("Flashlight", ["Flashlights"]), ("Stapler", ["Staplers"]),
    ("Calculator", ["Calculators"]), ("Telescope", ["Telescopes"]), ("Compass", []),
    ("Hourglass", []), ("Lawnmower", ["Lawn Mower"]), ("Skateboard", ["Skateboards"]),
    ("Surfboard", ["Surfboards"]), ("Guitar", ["Guitars"]), ("Piano", ["Pianos"]),
    ("Trumpet", ["Trumpets"]), ("Camera", ["Cameras"]), ("Clock", ["Clocks"]),
    ("Lamp", ["Lamps"]), ("Broom", ["Brooms"]), ("Anchor", ["Anchors"]),
    ("Helmet", ["Helmets"]), ("Whistle", ["Whistles"]), ("Magnet", ["Magnets"]),
    ("Wheelbarrow", ["Wheelbarrows"]),
]


# Curated well-known NFL players (current stars + a few all-time greats), kept
# static like NBA — the live ESPN roster dict in roster.py is async-populated
# and full of obscure depth-chart names that don't play well as guess targets.
# Last-name matching in check_answer handles surname-only guesses; aliases add
# nicknames and accent/punctuation-free spellings.
_NFL_PLAYERS: list[CategoryItem] = [
    # Quarterbacks
    ("Patrick Mahomes", ["Mahomes"]), ("Josh Allen", []), ("Joe Burrow", []),
    ("Justin Herbert", []), ("Lamar Jackson", []), ("Jalen Hurts", []),
    ("Dak Prescott", []), ("Tua Tagovailoa", ["Tua"]), ("Trevor Lawrence", []),
    ("Aaron Rodgers", []), ("Matthew Stafford", []), ("Jared Goff", []),
    ("Brock Purdy", []), ("Jordan Love", []), ("C.J. Stroud", ["CJ Stroud"]),
    ("Caleb Williams", []), ("Jayden Daniels", []), ("Kyler Murray", []),
    ("Baker Mayfield", []), ("Russell Wilson", []),
    # Running backs
    ("Christian McCaffrey", ["CMC", "McCaffrey"]), ("Derrick Henry", ["King Henry"]),
    ("Saquon Barkley", ["Saquon"]), ("Nick Chubb", []), ("Bijan Robinson", ["Bijan"]),
    ("Jonathan Taylor", []), ("Josh Jacobs", []), ("Alvin Kamara", []),
    ("Jahmyr Gibbs", []), ("Breece Hall", []), ("Joe Mixon", []),
    ("De'Von Achane", ["Achane"]), ("James Cook", []),
    # Wide receivers
    ("Tyreek Hill", ["Cheetah"]), ("Justin Jefferson", ["Jettas"]),
    ("Ja'Marr Chase", ["Jamarr Chase"]), ("CeeDee Lamb", ["CeeDee"]),
    ("A.J. Brown", ["AJ Brown"]), ("Davante Adams", []), ("Cooper Kupp", []),
    ("Amon-Ra St. Brown", ["Amon Ra St Brown", "St Brown"]), ("DK Metcalf", []),
    ("Mike Evans", []), ("Garrett Wilson", []), ("Jaylen Waddle", []),
    ("DeVonta Smith", []), ("Puka Nacua", ["Puka"]),
    ("Marvin Harrison Jr", ["Marvin Harrison"]), ("Deebo Samuel", ["Deebo"]),
    ("Keenan Allen", []), ("Stefon Diggs", []), ("Terry McLaurin", []),
    ("DJ Moore", []), ("Nico Collins", []),
    # Tight ends
    ("Travis Kelce", ["Kelce"]), ("George Kittle", ["Kittle"]),
    ("Mark Andrews", []), ("Sam LaPorta", []), ("T.J. Hockenson", ["Hockenson"]),
    ("Kyle Pitts", []),
    # Defense
    ("Myles Garrett", []), ("T.J. Watt", ["TJ Watt"]), ("Micah Parsons", ["Micah"]),
    ("Nick Bosa", []), ("Maxx Crosby", []), ("Chris Jones", []),
    ("Khalil Mack", []), ("Sauce Gardner", ["Sauce"]), ("Patrick Surtain", []),
    ("Fred Warner", []), ("Roquan Smith", []),
    # All-time greats
    ("Tom Brady", ["TB12"]), ("Peyton Manning", []), ("Drew Brees", []),
    ("Brett Favre", []), ("Jerry Rice", []), ("Randy Moss", []),
    ("Barry Sanders", []), ("Emmitt Smith", []), ("Walter Payton", []),
    ("Ray Lewis", []), ("Reggie White", []), ("Lawrence Taylor", ["LT"]),
    ("Deion Sanders", ["Prime Time", "Primetime"]), ("Rob Gronkowski", ["Gronk"]),
    ("J.J. Watt", ["JJ Watt"]), ("Calvin Johnson", ["Megatron"]),
    ("Adrian Peterson", ["AP"]), ("Marshawn Lynch", ["Beast Mode"]),
    ("Larry Fitzgerald", []), ("Von Miller", []), ("Aaron Donald", []),
]


# Curated well-known MLB players (current stars + all-time greats), static for
# the same reason as NFL. Last-name matching + nickname/accent-free aliases.
_MLB_PLAYERS: list[CategoryItem] = [
    # Current stars — hitters
    ("Shohei Ohtani", ["Ohtani", "Shohei"]), ("Aaron Judge", ["Judge"]),
    ("Mookie Betts", ["Mookie"]), ("Mike Trout", ["Trout"]),
    ("Ronald Acuña Jr", ["Ronald Acuna", "Acuna"]), ("Juan Soto", ["Soto"]),
    ("Freddie Freeman", []), ("Jose Altuve", ["Altuve"]), ("Bryce Harper", []),
    ("Fernando Tatis Jr", ["Fernando Tatis", "Tatis"]),
    ("Vladimir Guerrero Jr", ["Vlad Guerrero", "Vladdy", "Guerrero"]),
    ("Bobby Witt Jr", ["Bobby Witt"]), ("Gunnar Henderson", ["Gunnar"]),
    ("Corey Seager", []), ("Trea Turner", []), ("Francisco Lindor", ["Lindor"]),
    ("Manny Machado", ["Machado"]), ("Rafael Devers", ["Devers"]),
    ("Yordan Alvarez", ["Yordan"]), ("Kyle Tucker", []),
    ("Julio Rodriguez", ["Julio", "J-Rod"]), ("Pete Alonso", ["Polar Bear"]),
    ("Matt Olson", []), ("Marcus Semien", []), ("Adley Rutschman", []),
    ("Jose Ramirez", []), ("Paul Goldschmidt", ["Goldy"]),
    ("Nolan Arenado", ["Arenado"]), ("Christian Yelich", ["Yelich"]),
    ("Elly De La Cruz", ["Elly"]),
    # Current stars — pitchers
    ("Gerrit Cole", []), ("Corbin Burnes", []), ("Zack Wheeler", []),
    ("Tarik Skubal", ["Skubal"]), ("Paul Skenes", ["Skenes"]),
    ("Blake Snell", []), ("Max Fried", []), ("Spencer Strider", ["Strider"]),
    ("Emmanuel Clase", ["Clase"]), ("Josh Hader", []),
    # All-time / recent greats
    ("Albert Pujols", ["Pujols"]), ("Miguel Cabrera", ["Miggy"]),
    ("Clayton Kershaw", ["Kershaw"]), ("Justin Verlander", ["Verlander"]),
    ("Max Scherzer", ["Mad Max"]), ("Derek Jeter", ["Jeter"]),
    ("Barry Bonds", ["Bonds"]), ("Ken Griffey Jr", ["Griffey", "The Kid"]),
    ("Babe Ruth", ["Babe", "The Bambino"]), ("Hank Aaron", ["Hammerin Hank"]),
    ("Willie Mays", ["Say Hey Kid"]), ("Ted Williams", []),
    ("Mickey Mantle", ["Mantle"]), ("Jackie Robinson", ["Jackie"]),
    ("Pedro Martinez", ["Pedro"]), ("Mariano Rivera", ["Mo", "Mariano"]),
    ("Ichiro Suzuki", ["Ichiro"]), ("Alex Rodriguez", ["A-Rod", "ARod"]),
    ("David Ortiz", ["Big Papi"]), ("Chipper Jones", ["Chipper"]),
    ("Randy Johnson", ["Big Unit"]), ("Greg Maddux", ["Maddux"]),
    ("Cal Ripken Jr", ["Cal Ripken"]), ("Nolan Ryan", []),
]


# Basic science — high-school / intro-college terms that play well as guess
# targets (one recognizable concept per item). Skews toward the science-bowl
# crowd without being graduate-level. Aliases cover symbols/spelling variants.
_SCIENCE_BASIC: list[CategoryItem] = [
    # Physics
    ("Gravity", []), ("Velocity", []), ("Acceleration", []), ("Friction", []),
    ("Momentum", []), ("Inertia", []), ("Energy", []), ("Voltage", []),
    ("Magnetism", ["Magnet"]), ("Refraction", []), ("Density", []),
    ("Buoyancy", []), ("Pressure", []), ("Frequency", []), ("Wavelength", []),
    # Chemistry
    ("Atom", ["Atoms"]), ("Molecule", ["Molecules"]), ("Electron", ["Electrons"]),
    ("Proton", ["Protons"]), ("Neutron", ["Neutrons"]), ("Isotope", ["Isotopes"]),
    ("Periodic Table", []), ("Covalent Bond", ["Covalent"]), ("Ionic Bond", ["Ionic"]),
    ("Catalyst", []), ("Oxidation", []), ("Acidity", ["pH", "ph scale"]), ("Solvent", []),
    ("Combustion", []), ("Hydrogen", []), ("Oxygen", []), ("Carbon", []),
    # Biology
    ("Photosynthesis", []), ("Mitosis", []), ("Meiosis", []), ("DNA", []),
    ("Chromosome", ["Chromosomes"]), ("Cell", ["Cells"]), ("Mitochondria", ["Mitochondrion"]),
    ("Enzyme", ["Enzymes"]), ("Osmosis", []), ("Ecosystem", []), ("Evolution", []),
    ("Protein", ["Proteins"]), ("Bacteria", ["Bacterium"]), ("Virus", ["Viruses"]),
    ("Nucleus", []), ("Ribosome", ["Ribosomes"]),
    # Earth & space
    ("Magma", ["Lava"]), ("Tectonic Plates", ["Plate Tectonics", "Tectonic"]),
    ("Erosion", []), ("Volcano", ["Volcanoes"]), ("Earthquake", ["Earthquakes"]),
    ("Atmosphere", []), ("Orbit", ["Orbits"]), ("Eclipse", ["Eclipses"]),
    ("Comet", ["Comets"]), ("Galaxy", ["Galaxies"]), ("Black Hole", []),
    ("Supernova", ["Supernovae"]), ("Condensation", []), ("Evaporation", []),
]


# ── QBReader science (live, cached) ──────────────────────────────────────────
#
# "Science (Quiz Bowl)" pulls real answerlines from QBReader's bonus database
# (qbreader.org). Answers are fetched into a module cache at startup and
# periodically refreshed (see CluemasterCog); per-round picks read the cache
# synchronously. If the cache is empty (QBReader down / not yet loaded) the
# category falls back to the static Basic Science bank.

SCIENCE_QB_KEY = "science_qb"
_QB_BONUS_URL = "https://www.qbreader.org/api/random-bonus"
_SCIENCE_QB_CACHE: list[CategoryItem] = []

# Bonus/tossup answers that are bare numbers make poor guess targets.
_QB_STOPWORDS = {
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "infinity",
}


def _clean_qb_answer(html_answer: str, sanitized_answer: str) -> CategoryItem | None:
    """Turn a QBReader answerline into (primary, [accepted alts]).

    The primary answer is the text before any ``[accept …]`` directive; the
    bold/underlined spans in the HTML become aliases (e.g. "Parkinson" for
    "Parkinson's disease"). Returns None for answers that won't play well.
    """
    name = re.split(r"[\[(]", sanitized_answer, maxsplit=1)[0]
    name = re.sub(r"<.*?>", "", name).replace("_", "").strip().strip(".,;:\"'").strip()
    if not name or len(name) > 40:
        return None
    norm = re.sub(r"[^a-z0-9]", "", name.lower())
    if len(norm) < 3 or norm in _QB_STOPWORDS:
        return None
    alts: list[str] = []
    for span in re.findall(r"<b><u>(.*?)</u></b>", html_answer):
        alt = re.sub(r"<.*?>", "", span).replace("_", "").strip().strip(".,;:\"'")
        if alt and alt.lower() != name.lower() and alt not in alts:
            alts.append(alt)
    return (name, alts)


async def _load_science_cache_from_db() -> int:
    """Warm the in-memory cache from the accumulated DB pool. Returns count."""
    try:
        stored = await queries.get_qb_answers(SCIENCE_DB_CATEGORY)
    except Exception:
        log.exception("refresh_science_answers: DB load failed")
        return len(_SCIENCE_QB_CACHE)
    if stored:
        _SCIENCE_QB_CACHE.clear()
        _SCIENCE_QB_CACHE.extend((name, list(alts)) for name, alts in stored)
    return len(_SCIENCE_QB_CACHE)


async def refresh_science_answers(target: int = 200, max_requests: int = 5) -> int:
    """Top up the persistent science pool from QBReader, then reload the cache.

    The DB accumulates every answer ever seen, so the pool grows across games
    and survives restarts. Flow: warm cache from DB → fetch a fresh batch from
    QBReader → persist new answers → reload the full pool. Best-effort at every
    step: a failed fetch or DB call never empties a good cache.
    """
    # 1. Warm from the accumulated DB pool first (fast, offline-safe).
    await _load_science_cache_from_db()

    # 2. Fetch a fresh batch from QBReader.
    collected: dict[str, CategoryItem] = {}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            for _ in range(max_requests):
                resp = await client.get(
                    _QB_BONUS_URL, params={"categories": "Science", "number": 30},
                )
                resp.raise_for_status()
                for bonus in resp.json().get("bonuses", []):
                    answers = bonus.get("answers", [])
                    sanitized = bonus.get("answers_sanitized", [])
                    for html_a, san_a in zip(answers, sanitized):
                        item = _clean_qb_answer(html_a, san_a)
                        if item:
                            collected[item[0].lower()] = item
                if len(collected) >= target:
                    break
    except Exception:
        log.exception("refresh_science_answers: QBReader fetch failed")
        return len(_SCIENCE_QB_CACHE)

    # 3. Persist new answers, then reload the full accumulated pool.
    if collected:
        try:
            await queries.upsert_qb_answers(SCIENCE_DB_CATEGORY, list(collected.values()))
        except Exception:
            log.exception("refresh_science_answers: DB upsert failed")
        if not await _load_science_cache_from_db() and collected:
            # DB unavailable — fall back to just this batch in memory.
            _SCIENCE_QB_CACHE.clear()
            _SCIENCE_QB_CACHE.extend(collected.values())
    return len(_SCIENCE_QB_CACHE)


CATEGORIES: dict[str, tuple[str, str, list[CategoryItem]]] = {
    # key -> (display_name, emoji, items)
    "nba": ("NBA Players", "\U0001f3c0", _nba_items()),
    "nfl": ("NFL Players", "\U0001f3c8", _NFL_PLAYERS),
    "mlb": ("MLB Players", "\U000026be", _MLB_PLAYERS),
    "science_basic": ("Basic Science", "\U0001f9ea", _SCIENCE_BASIC),
    # items filled live from QBReader; empty placeholder so it lists in the menu
    "science_qb": ("Science (Quiz Bowl)", "\U0001f52c", []),
    "celebrities": ("Celebrities", "\U0001f31f", _CELEBRITIES),
    "animals": ("Animals", "\U0001f981", _ANIMALS),
    "food": ("Food & Drink", "\U0001f354", _FOOD),
    "movies": ("Movies", "\U0001f3ac", _MOVIES),
    "objects": ("Everyday Things", "\U0001f4a1", _OBJECTS),
}

DEFAULT_CATEGORY = "nba"


# ── Answer matching ──────────────────────────────────────────────────────────


def _normalize(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return "".join(c.lower() for c in stripped if c.isalnum()).strip()


def _fuzzy(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def check_answer(guess: str, item: CategoryItem) -> bool:
    """Return True if guess matches the item name or any alias.

    Mirrors the matching used by nbaguess: exact normalized match, fuzzy
    match (>=85% on >=5 chars), or last-name-only match (>=4 chars).
    """
    norm_g = _normalize(guess)
    if not norm_g or len(norm_g) < 3:
        return False
    name, alts = item
    for ans in [name, *alts]:
        norm_a = _normalize(ans)
        if not norm_a:
            continue
        if norm_g == norm_a:
            return True
        if len(norm_g) >= 5 and _fuzzy(norm_g, norm_a) >= 0.85:
            return True
        parts = ans.split()
        if len(parts) > 1:
            last = _normalize(parts[-1])
            if norm_g == last and len(last) >= 4:
                return True
    return False


def category_options(default_key: str = DEFAULT_CATEGORY) -> list[tuple[str, str, str, bool]]:
    """SelectOption-friendly tuples: (label, value, emoji, is_default)."""
    out: list[tuple[str, str, str, bool]] = []
    for key, (label, emoji, _items) in CATEGORIES.items():
        out.append((label, key, emoji, key == default_key))
    return out


def get_category(key: str) -> tuple[str, str, list[CategoryItem]]:
    if key == SCIENCE_QB_KEY:
        label, emoji, _ = CATEGORIES[SCIENCE_QB_KEY]
        # Live cache, or fall back to the static Basic Science bank if empty.
        items = _SCIENCE_QB_CACHE or _SCIENCE_BASIC
        return label, emoji, items
    return CATEGORIES.get(key, CATEGORIES[DEFAULT_CATEGORY])
