"""Shared answer-bank categories for party guessing games (cluemaster, imposter).

Each category exposes a list of items as `(name, [aliases])`. Add new
categories by populating CATEGORIES; both /cluemaster and /imposter pick
them up automatically.
"""

from __future__ import annotations

import unicodedata
from difflib import SequenceMatcher

from bot.cogs.nbaguess import NBA_PLAYERS_DATA


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
    ("Up", []), ("Moana", []), ("Encanto", []), ("Barbie", []),
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


CATEGORIES: dict[str, tuple[str, str, list[CategoryItem]]] = {
    # key -> (display_name, emoji, items)
    "nba": ("NBA Players", "\U0001f3c0", _nba_items()),
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
    return CATEGORIES.get(key, CATEGORIES[DEFAULT_CATEGORY])
