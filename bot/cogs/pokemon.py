"""Casino cog — multiplayer /pokemon guessing game.

Progressive hints about a Pokemon; first to type its name wins the round.
First to WINS_TO_WIN rounds takes the pot.
"""

import asyncio
import random
import time
import unicodedata
from dataclasses import dataclass, field
from itertools import groupby

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries

# ── Constants ────────────────────────────────────────────────────────────────

MAX_PLAYERS = 8
MIN_PLAYERS = 1
ROUND_TIME = 30  # seconds per round
ROUND_DELAY = 4  # seconds between rounds
WINS_TO_WIN = 3  # first to N wins
MAX_ROUNDS = 15  # safety cap

# Hint reveal timing (seconds into the round)
HINT2_AT = 10
HINT3_AT = 20

PAYTABLE: dict[int, list[float]] = {
    1: [1.0],
    2: [1.0],
    3: [0.70, 0.30],
    4: [0.55, 0.30, 0.15],
    5: [0.45, 0.25, 0.18, 0.12],
    6: [0.40, 0.24, 0.16, 0.12, 0.08],
    7: [0.36, 0.22, 0.16, 0.12, 0.08, 0.06],
    8: [0.33, 0.21, 0.16, 0.12, 0.08, 0.06, 0.04],
}

MEDALS = ["\U0001f947", "\U0001f948", "\U0001f949"]

SPRITE_URL = (
    "https://raw.githubusercontent.com/PokeAPI/sprites/"
    "master/sprites/pokemon/other/official-artwork/{}.png"
)

TYPE_EMOJI: dict[str, str] = {
    "Normal": "\u2b1c",
    "Fire": "\U0001f525",
    "Water": "\U0001f4a7",
    "Grass": "\U0001f33f",
    "Electric": "\u26a1",
    "Ice": "\u2744\ufe0f",
    "Fighting": "\U0001f94a",
    "Poison": "\u2620\ufe0f",
    "Ground": "\U0001f30d",
    "Flying": "\U0001fab6",
    "Psychic": "\U0001f52e",
    "Bug": "\U0001f41b",
    "Rock": "\U0001faa8",
    "Ghost": "\U0001f47b",
    "Dragon": "\U0001f409",
    "Dark": "\U0001f311",
    "Steel": "\u2699\ufe0f",
    "Fairy": "\u2728",
}

GEN_LABEL: dict[int, str] = {
    1: "Gen 1 (Kanto)",
    2: "Gen 2 (Johto)",
    3: "Gen 3 (Hoenn)",
    4: "Gen 4 (Sinnoh)",
    5: "Gen 5 (Unova)",
    6: "Gen 6 (Kalos)",
    7: "Gen 7 (Alola)",
    8: "Gen 8 (Galar)",
    9: "Gen 9 (Paldea)",
}

# ── Pokemon Data ─────────────────────────────────────────────────────────────
# (dex_id, name, [alt_names], [types], gen, hint, is_legendary)

POKEMON: list[tuple[int, str, list[str], list[str], int, str, bool]] = [
    # ── Gen 1 ────────────────────────────────────────────────────────────────
    (1, "Bulbasaur", [], ["Grass", "Poison"], 1,
     "The very first Pokemon in the National Dex — a seed grows on its back", False),
    (4, "Charmander", [], ["Fire"], 1,
     "This starter's tail flame shows its life force", False),
    (7, "Squirtle", [], ["Water"], 1,
     "A tiny turtle starter that shoots water from its mouth", False),
    (3, "Venusaur", [], ["Grass", "Poison"], 1,
     "The flower on its back blooms when absorbing sunlight", False),
    (6, "Charizard", [], ["Fire", "Flying"], 1,
     "A fan-favorite fire-breather that isn't actually Dragon-type", False),
    (9, "Blastoise", [], ["Water"], 1,
     "Has twin water cannons protruding from its shell", False),
    (25, "Pikachu", [], ["Electric"], 1,
     "The franchise mascot that stores electricity in its cheeks", False),
    (26, "Raichu", [], ["Electric"], 1,
     "The evolved form of the franchise mascot", False),
    (35, "Clefairy", [], ["Fairy"], 1,
     "Believed to come from the moon; dances under moonlight", False),
    (37, "Vulpix", [], ["Fire"], 1,
     "A fox with six curling tails that grows more as it ages", False),
    (39, "Jigglypuff", [], ["Normal", "Fairy"], 1,
     "Puts opponents to sleep with its lullaby, then draws on their faces", False),
    (50, "Diglett", [], ["Ground"], 1,
     "Lives underground; only its head is ever seen above the surface", False),
    (52, "Meowth", [], ["Normal"], 1,
     "A cat Pokemon obsessed with collecting coins", False),
    (54, "Psyduck", [], ["Water"], 1,
     "Constantly suffers from headaches that unleash psychic power", False),
    (58, "Growlithe", [], ["Fire"], 1,
     "A loyal puppy Pokemon with a brave and trustworthy nature", False),
    (59, "Arcanine", [], ["Fire"], 1,
     "A majestic canine considered legendary in Chinese mythology", False),
    (65, "Alakazam", [], ["Psychic"], 1,
     "Holds two spoons and has an IQ of 5000", False),
    (68, "Machamp", [], ["Fighting"], 1,
     "A four-armed fighter that can throw 500 punches per second", False),
    (74, "Geodude", [], ["Rock", "Ground"], 1,
     "A living rock with arms found on mountain trails", False),
    (79, "Slowpoke", [], ["Water", "Psychic"], 1,
     "Incredibly slow to react; takes 5 seconds to feel pain", False),
    (92, "Gastly", [], ["Ghost", "Poison"], 1,
     "A gaseous ghost barely visible to the eye", False),
    (94, "Gengar", [], ["Ghost", "Poison"], 1,
     "A shadowy trickster known for its sinister grin", False),
    (95, "Onix", [], ["Rock", "Ground"], 1,
     "A massive rock snake that tunnels underground at 50 mph", False),
    (104, "Cubone", [], ["Ground"], 1,
     "Wears the skull of its deceased mother as a helmet", False),
    (113, "Chansey", [], ["Normal"], 1,
     "A pink Pokemon that carries a healing egg in its pouch", False),
    (123, "Scyther", [], ["Bug", "Flying"], 1,
     "A mantis-like Pokemon with razor-sharp scythe arms", False),
    (127, "Pinsir", [], ["Bug"], 1,
     "Grips prey with its powerful horned pincers", False),
    (129, "Magikarp", [], ["Water"], 1,
     "Widely considered the weakest Pokemon; can only splash", False),
    (130, "Gyarados", [], ["Water", "Flying"], 1,
     "A fearsome sea serpent that evolved from a helpless fish", False),
    (131, "Lapras", [], ["Water", "Ice"], 1,
     "A gentle sea creature that gives rides across water", False),
    (132, "Ditto", [], ["Normal"], 1,
     "Can transform into an exact copy of any opponent", False),
    (133, "Eevee", [], ["Normal"], 1,
     "Known for its unstable DNA and many evolution possibilities", False),
    (134, "Vaporeon", [], ["Water"], 1,
     "An Eevee evolution that can dissolve into water", False),
    (135, "Jolteon", [], ["Electric"], 1,
     "An Eevee evolution with bristling electric fur", False),
    (136, "Flareon", [], ["Fire"], 1,
     "An Eevee evolution with a fluffy flame-colored mane", False),
    (137, "Porygon", [], ["Normal"], 1,
     "An artificial Pokemon made entirely of programming code", False),
    (142, "Aerodactyl", [], ["Rock", "Flying"], 1,
     "An ancient pterodactyl revived from amber", False),
    (143, "Snorlax", [], ["Normal"], 1,
     "Blocks roads with its massive sleeping body — eats 900 lbs daily", False),
    (103, "Exeggutor", [], ["Grass", "Psychic"], 1,
     "A walking palm tree with three coconut-like heads", False),
    (115, "Kangaskhan", [], ["Normal"], 1,
     "A parent Pokemon that carries its baby in a belly pouch", False),
    (144, "Articuno", [], ["Ice", "Flying"], 1,
     "A legendary bird that controls ice and freezing cold", True),
    (145, "Zapdos", [], ["Electric", "Flying"], 1,
     "A legendary bird that appears from thunderclouds", True),
    (146, "Moltres", [], ["Fire", "Flying"], 1,
     "A legendary bird wreathed in flames", True),
    (147, "Dratini", [], ["Dragon"], 1,
     "A rare serpentine Pokemon found in bodies of water", False),
    (149, "Dragonite", [], ["Dragon", "Flying"], 1,
     "A friendly dragon that can fly around the globe in 16 hours", False),
    (150, "Mewtwo", ["Mew Two"], ["Psychic"], 1,
     "A genetically engineered Pokemon created from Mew's DNA", True),
    (151, "Mew", [], ["Psychic"], 1,
     "Said to contain the genetic code of every Pokemon species", True),

    # ── Gen 2 ────────────────────────────────────────────────────────────────
    (152, "Chikorita", [], ["Grass"], 2,
     "A gentle leaf-headed starter from Johto", False),
    (155, "Cyndaquil", [], ["Fire"], 2,
     "A timid fire mouse that ignites flames on its back when startled", False),
    (158, "Totodile", [], ["Water"], 2,
     "A playful crocodile starter that loves to bite everything", False),
    (169, "Crobat", [], ["Poison", "Flying"], 2,
     "Evolves through friendship; flies silently on four wings", False),
    (181, "Ampharos", [], ["Electric"], 2,
     "A sheep-like Pokemon whose tail tip glows like a lighthouse", False),
    (196, "Espeon", [], ["Psychic"], 2,
     "An Eevee evolution that developed psychic powers from sunlight loyalty", False),
    (197, "Umbreon", [], ["Dark"], 2,
     "An Eevee evolution with glowing rings that appears at night", False),
    (208, "Steelix", [], ["Steel", "Ground"], 2,
     "An iron snake tempered harder than diamond deep underground", False),
    (212, "Scizor", [], ["Bug", "Steel"], 2,
     "A red metallic mantis evolved using a Metal Coat trade", False),
    (214, "Heracross", [], ["Bug", "Fighting"], 2,
     "A beetle that hurls opponents with its mighty horn", False),
    (229, "Houndoom", [], ["Dark", "Fire"], 2,
     "A hellhound whose flame burns cause pain that never stops", False),
    (235, "Smeargle", [], ["Normal"], 2,
     "Paints using the fluid oozing from its tail tip — can copy any move", False),
    (242, "Blissey", [], ["Normal"], 2,
     "The happiest Pokemon; its egg brings joy to anyone who eats it", False),
    (243, "Raikou", [], ["Electric"], 2,
     "A legendary beast that embodies the speed of lightning", True),
    (244, "Entei", [], ["Fire"], 2,
     "A legendary beast born from a volcanic eruption", True),
    (245, "Suicune", [], ["Water"], 2,
     "A legendary beast that purifies polluted water", True),
    (248, "Tyranitar", [], ["Rock", "Dark"], 2,
     "A Godzilla-like pseudo-legendary that can topple mountains", False),
    (249, "Lugia", [], ["Psychic", "Flying"], 2,
     "Guardian of the seas; its wings can cause 40-day storms", True),
    (250, "Ho-Oh", ["Hooh", "Ho Oh"], ["Fire", "Flying"], 2,
     "A rainbow-winged bird said to grant eternal happiness", True),
    (251, "Celebi", [], ["Psychic", "Grass"], 2,
     "A time-traveling forest guardian from the future", True),

    # ── Gen 3 ────────────────────────────────────────────────────────────────
    (252, "Treecko", [], ["Grass"], 3,
     "A cool-headed gecko starter that climbs vertical walls", False),
    (255, "Torchic", [], ["Fire"], 3,
     "A chick starter with a fire burning inside its belly", False),
    (258, "Mudkip", [], ["Water"], 3,
     "An amphibious mud fish starter beloved by internet memes", False),
    (282, "Gardevoir", [], ["Psychic", "Fairy"], 3,
     "Will create a small black hole to protect its Trainer", False),
    (289, "Slaking", [], ["Normal"], 3,
     "Has the highest base stats of non-legendaries but loafs every other turn", False),
    (302, "Sableye", [], ["Dark", "Ghost"], 3,
     "Lives in caves and has gemstone eyes — has no type weaknesses (pre-Gen 6)", False),
    (306, "Aggron", [], ["Steel", "Rock"], 3,
     "Fiercely territorial; restores its mountain after natural disasters", False),
    (319, "Sharpedo", [], ["Water", "Dark"], 3,
     "Known as the bully of the sea; swims at 75 mph", False),
    (330, "Flygon", [], ["Ground", "Dragon"], 3,
     "Known as the Desert Spirit for its singing sandstorm wings", False),
    (334, "Altaria", [], ["Dragon", "Flying"], 3,
     "A fluffy cloud bird that hums in a beautiful soprano", False),
    (350, "Milotic", [], ["Water"], 3,
     "Considered the most beautiful Pokemon in the world", False),
    (354, "Banette", [], ["Ghost"], 3,
     "A discarded doll that came to life seeking its former owner", False),
    (359, "Absol", [], ["Dark"], 3,
     "Appears before natural disasters to warn people", False),
    (373, "Salamence", [], ["Dragon", "Flying"], 3,
     "A dragon that grew wings from sheer willpower and desire to fly", False),
    (376, "Metagross", [], ["Steel", "Psychic"], 3,
     "A supercomputer Pokemon formed from four Beldum brains", False),
    (380, "Latias", [], ["Dragon", "Psychic"], 3,
     "A jet-shaped legendary that communicates telepathically — the red one", True),
    (381, "Latios", [], ["Dragon", "Psychic"], 3,
     "A jet-shaped legendary that flies faster than a jet — the blue one", True),
    (382, "Kyogre", [], ["Water"], 3,
     "A legendary titan said to have expanded the seas", True),
    (383, "Groudon", [], ["Ground"], 3,
     "A legendary titan said to have expanded the continents", True),
    (384, "Rayquaza", [], ["Dragon", "Flying"], 3,
     "Lives in the ozone layer and calms Groudon and Kyogre's battles", True),
    (385, "Jirachi", [], ["Steel", "Psychic"], 3,
     "A wish-granting Pokemon that sleeps for a thousand years", True),
    (386, "Deoxys", [], ["Psychic"], 3,
     "An alien Pokemon born from a space virus on a meteor", True),

    # ── Gen 4 ────────────────────────────────────────────────────────────────
    (387, "Turtwig", [], ["Grass"], 4,
     "A turtle starter with a sprout growing from its shell", False),
    (390, "Chimchar", [], ["Fire"], 4,
     "A monkey starter with a flame burning on its rear end", False),
    (393, "Piplup", [], ["Water"], 4,
     "A proud penguin starter that hates receiving food from people", False),
    (405, "Luxray", [], ["Electric"], 4,
     "A lion that can see through walls with X-ray vision eyes", False),
    (407, "Roserade", [], ["Grass", "Poison"], 4,
     "Lures prey with sweet scent from its bouquet hands, then poisons them", False),
    (442, "Spiritomb", [], ["Ghost", "Dark"], 4,
     "Formed by exactly 108 malevolent spirits bound to a keystone", False),
    (445, "Garchomp", [], ["Dragon", "Ground"], 4,
     "A jet-speed land shark that is Sinnoh's Champion's signature Pokemon", False),
    (448, "Lucario", [], ["Fighting", "Steel"], 4,
     "Senses aura to read thoughts and emotions from great distances", False),
    (461, "Weavile", [], ["Dark", "Ice"], 4,
     "Hunts in coordinated packs and carves signals into trees", False),
    (468, "Togekiss", [], ["Fairy", "Flying"], 4,
     "Brings blessings of joy; never appears where there is conflict", False),
    (470, "Leafeon", [], ["Grass"], 4,
     "An Eevee evolution that photosynthesizes like a plant", False),
    (471, "Glaceon", [], ["Ice"], 4,
     "An Eevee evolution that can lower its body temp to freeze the air", False),
    (475, "Gallade", [], ["Psychic", "Fighting"], 4,
     "Extends its elbows like swords to protect others — Gardevoir's male counterpart", False),
    (483, "Dialga", [], ["Steel", "Dragon"], 4,
     "Controls time itself; born when the universe was created", True),
    (484, "Palkia", [], ["Water", "Dragon"], 4,
     "Controls space itself; can warp and distort dimensions", True),
    (487, "Giratina", [], ["Ghost", "Dragon"], 4,
     "Banished to the Distortion World for its violent behavior", True),
    (491, "Darkrai", [], ["Dark"], 4,
     "Causes never-ending nightmares to all who sleep near it", True),
    (492, "Shaymin", [], ["Grass"], 4,
     "A gratitude hedgehog that purifies toxins and transforms with a Gracidea flower", True),
    (493, "Arceus", [], ["Normal"], 4,
     "The Alpha Pokemon — said to have created the entire universe with its 1000 arms", True),

    # ── Gen 5 ────────────────────────────────────────────────────────────────
    (495, "Snivy", [], ["Grass"], 5,
     "A smug snake-like starter with a leaf tail and a condescending stare", False),
    (498, "Tepig", [], ["Fire"], 5,
     "A fire pig starter that roasts berries by sneezing embers", False),
    (501, "Oshawott", [], ["Water"], 5,
     "A sea otter starter that fights with its detachable scalchop shell", False),
    (530, "Excadrill", [], ["Ground", "Steel"], 5,
     "A mole that drills through iron plates at 93 mph", False),
    (563, "Cofagrigus", [], ["Ghost"], 5,
     "A sarcophagus Pokemon that swallows grave robbers whole", False),
    (571, "Zoroark", [], ["Dark"], 5,
     "A master of illusions that disguises itself as other Pokemon or people", False),
    (598, "Ferrothorn", [], ["Grass", "Steel"], 5,
     "Clings to cave ceilings and drops spiked barbed feelers", False),
    (609, "Chandelure", [], ["Ghost", "Fire"], 5,
     "A chandelier that hypnotizes prey then burns their spirit", False),
    (612, "Haxorus", [], ["Dragon"], 5,
     "Has axe-like tusks that can cut through steel beams effortlessly", False),
    (635, "Hydreigon", [], ["Dark", "Dragon"], 5,
     "A brutal three-headed dragon that devours everything in its path", False),
    (643, "Reshiram", [], ["Dragon", "Fire"], 5,
     "A legendary white dragon that embodies truth", True),
    (644, "Zekrom", [], ["Dragon", "Electric"], 5,
     "A legendary black dragon that embodies ideals", True),
    (646, "Kyurem", [], ["Dragon", "Ice"], 5,
     "An empty dragon that can fuse with Reshiram or Zekrom", True),
    (649, "Genesect", [], ["Bug", "Steel"], 5,
     "An ancient bug modified by Team Plasma with a back-mounted cannon", True),

    # ── Gen 6 ────────────────────────────────────────────────────────────────
    (650, "Chespin", [], ["Grass"], 6,
     "A spiny nut starter that wears a tough green shell on its head", False),
    (653, "Fennekin", [], ["Fire"], 6,
     "A fennec fox starter that snacks on twigs for energy", False),
    (656, "Froakie", [], ["Water"], 6,
     "A frog starter with protective bubbles around its neck", False),
    (658, "Greninja", [], ["Water", "Dark"], 6,
     "A ninja frog that creates deadly throwing stars from compressed water", False),
    (681, "Aegislash", [], ["Steel", "Ghost"], 6,
     "A haunted royal sword and shield that switches between attack and defense", False),
    (700, "Sylveon", [], ["Fairy"], 6,
     "An Eevee evolution with ribbon-like feelers that calms conflict", False),
    (706, "Goodra", [], ["Dragon"], 6,
     "A gooey, friendly dragon that hugs its Trainer with slimy affection", False),
    (716, "Xerneas", [], ["Fairy"], 6,
     "A legendary life deer that grants eternal life — shaped like the letter X", True),
    (717, "Yveltal", [], ["Dark", "Flying"], 6,
     "A legendary destruction bird that absorbs all life force — shaped like Y", True),
    (718, "Zygarde", [], ["Dragon", "Ground"], 6,
     "An ecosystem guardian made of cells — has 10%, 50%, and Complete formes", True),
    (719, "Diancie", [], ["Rock", "Fairy"], 6,
     "A mythical jewel Pokemon that creates diamonds from thin air", True),

    # ── Gen 7 ────────────────────────────────────────────────────────────────
    (722, "Rowlet", [], ["Grass", "Flying"], 7,
     "A round owl starter that attacks silently at night with leaf blades", False),
    (725, "Litten", [], ["Fire"], 7,
     "A cool cat starter that grooms itself with flaming saliva", False),
    (728, "Popplio", [], ["Water"], 7,
     "A sea lion starter that performs tricks with water balloons", False),
    (745, "Lycanroc", [], ["Rock"], 7,
     "A wolf with different forms depending on the time of day it evolves", False),
    (778, "Mimikyu", [], ["Ghost", "Fairy"], 7,
     "Wears a Pikachu disguise because seeing its true form causes illness", False),
    (785, "Tapu Koko", ["Tapukoko"], ["Electric", "Fairy"], 7,
     "The guardian deity of Melemele Island in the Alola region", True),
    (791, "Solgaleo", [], ["Psychic", "Steel"], 7,
     "An emissary of the sun that devours light and has a radiant mane", True),
    (792, "Lunala", [], ["Psychic", "Ghost"], 7,
     "An emissary of the moon that absorbs light with bat-like wings", True),
    (800, "Necrozma", [], ["Psychic"], 7,
     "A prism Pokemon that hungers for light and can fuse with Solgaleo or Lunala", True),
    (802, "Marshadow", [], ["Fighting", "Ghost"], 7,
     "A mythical shadow that hides in others' shadows to copy their moves", True),

    # ── Gen 8 ────────────────────────────────────────────────────────────────
    (810, "Grookey", [], ["Grass"], 8,
     "A chimp starter that keeps rhythm by tapping its special stick", False),
    (813, "Scorbunny", [], ["Fire"], 8,
     "An energetic rabbit starter always running to warm up its fire sacs", False),
    (816, "Sobble", [], ["Water"], 8,
     "A timid chameleon starter that turns invisible when it touches water", False),
    (849, "Toxtricity", [], ["Electric", "Poison"], 8,
     "A punk lizard that generates electricity by strumming its chest protrusions", False),
    (858, "Hatterene", [], ["Psychic", "Fairy"], 8,
     "The Forest Witch — punishes anyone who radiates strong emotions nearby", False),
    (862, "Obstagoon", [], ["Dark", "Normal"], 8,
     "A Galarian evolution that looks like a KISS rock band member", False),
    (879, "Copperajah", [], ["Steel"], 8,
     "A green-patina elephant originally from India, incredibly strong", False),
    (887, "Dragapult", [], ["Dragon", "Ghost"], 8,
     "A stealth bomber dragon that launches its baby Dreepy as missiles", False),
    (888, "Zacian", [], ["Fairy"], 8,
     "A legendary wolf that holds a rusted sword in its mouth", True),
    (889, "Zamazenta", [], ["Fighting"], 8,
     "A legendary wolf whose body serves as a rusted shield", True),
    (890, "Eternatus", [], ["Poison", "Dragon"], 8,
     "A gigantic skeletal alien dragon that caused the Darkest Day", True),
    (893, "Zarude", [], ["Dark", "Grass"], 8,
     "A mythical rogue monkey from the deep jungle that raised a human child", True),

    # ── Gen 9 ────────────────────────────────────────────────────────────────
    (906, "Sprigatito", [], ["Grass"], 9,
     "A grass cat starter that kneads with sweet-scented paws", False),
    (909, "Fuecoco", [], ["Fire"], 9,
     "A fire croc starter that absorbs heat through its square head crest", False),
    (912, "Quaxly", [], ["Water"], 9,
     "A duckling starter obsessed with keeping its hair gel pompadour clean", False),
    (923, "Pawmot", [], ["Electric", "Fighting"], 9,
     "Can revive fainted allies by rubbing its fuzzy electric paw pads", False),
    (934, "Armarouge", [], ["Fire", "Psychic"], 9,
     "A warrior in fire armor that attacks with its arm cannon", False),
    (936, "Ceruledge", [], ["Fire", "Ghost"], 9,
     "A warrior with ghostly fire blades that feeds on life energy", False),
    (987, "Roaring Moon", ["RoaringMoon"], ["Dragon", "Dark"], 9,
     "An ancient Paradox Pokemon resembling a primal Salamence", True),
    (994, "Iron Valiant", ["IronValiant"], ["Fairy", "Fighting"], 9,
     "A futuristic Paradox Pokemon that looks like Gardevoir fused with Gallade", True),
    (998, "Annihilape", [], ["Fighting", "Ghost"], 9,
     "Evolved through pure rage, becoming a ghostly primate berserker", False),
    (1000, "Gholdengo", [], ["Steel", "Ghost"], 9,
     "Made of exactly 999 Gimmighoul coins that came to life", False),
    (1007, "Koraidon", [], ["Fighting", "Dragon"], 9,
     "A legendary ancient motorcycle dragon that runs on all fours", True),
    (1008, "Miraidon", [], ["Electric", "Dragon"], 9,
     "A legendary futuristic motorcycle dragon that rides on electromagnetic waves", True),
    (1024, "Terapagos", [], ["Normal"], 9,
     "A turtle-like Pokemon related to the Terastal phenomenon", True),
]


# ── Answer matching ──────────────────────────────────────────────────────────


def _normalize(s: str) -> str:
    """Lowercase, strip accents and non-alphanumeric chars."""
    nfkd = unicodedata.normalize("NFKD", s)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return "".join(c.lower() for c in stripped if c.isalnum()).strip()


def _accepted_names(entry: tuple) -> list[str]:
    """Build accepted answer list from a POKEMON entry."""
    _, name, alts, *_ = entry
    return [name] + list(alts)


def check_pokemon_answer(guess: str, entry: tuple) -> bool:
    """Check if a guess matches the Pokemon."""
    norm = _normalize(guess)
    if not norm or len(norm) < 3:
        return False
    for ans in _accepted_names(entry):
        if _normalize(ans) == norm:
            return True
    return False


# ── Hint formatting ──────────────────────────────────────────────────────────


def _type_str(types: list[str]) -> str:
    return " / ".join(f"{TYPE_EMOJI.get(t, '')} {t}" for t in types)


def _blank_name(name: str) -> str:
    """e.g. 'Pikachu' -> 'P _ _ _ _ _ _'"""
    if len(name) <= 1:
        return name
    return name[0] + " " + " ".join("_" for _ in name[1:])


# ── Payout helpers ───────────────────────────────────────────────────────────


def _compute_payouts(
    players: dict[int, "PokePlayer"], prize_pool: int, n_players: int,
) -> dict[int, int]:
    pct_table = PAYTABLE.get(n_players, PAYTABLE[8])
    in_money = sorted(
        [p for p in players.values() if p.rounds_won > 0],
        key=lambda p: p.rounds_won, reverse=True,
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
        combined = sum(pct_table[pos:end])
        per_player = int(prize_pool * combined / len(group))
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


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class PokePlayer:
    user_id: int
    display_name: str
    bet: int
    rounds_won: int = 0
    answer: str | None = None
    answer_time: float | None = None


@dataclass
class PokeTable:
    channel_id: int
    host_id: int
    host_name: str
    phase: str = "betting"  # betting | playing | between_rounds | closed
    players: dict[int, PokePlayer] = field(default_factory=dict)
    message: discord.Message | None = None
    round_num: int = 0
    category: str = "all"  # all | gen1 | gen2to4 | gen5plus | legendary
    current_entry: tuple | None = None
    hint_level: int = 1  # 1, 2, or 3
    round_start_time: float = 0.0
    round_winner: int | None = None
    race_task: asyncio.Task | None = field(default=None, repr=False)
    round_solved: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    last_bets: dict[int, tuple[str, int]] = field(default_factory=dict)
    total_rounds_played: int = 0
    used_ids: set[int] = field(default_factory=set)
    round_messages: list[discord.Message] = field(default_factory=list)


# ── Embeds ───────────────────────────────────────────────────────────────────


def _scoreboard(table: PokeTable) -> str:
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


_CATEGORY_LABELS: dict[str, str] = {
    "all": "All Generations",
    "gen1": "Gen 1 (Kanto)",
    "gen2to4": "Gen 2\u20134 (Johto / Hoenn / Sinnoh)",
    "gen5plus": "Gen 5+ (Unova and beyond)",
    "legendary": "Legendary & Mythical",
}


def _betting_embed(table: PokeTable) -> discord.Embed:
    pot = sum(p.bet for p in table.players.values())
    n = len(table.players)
    cat_label = _CATEGORY_LABELS.get(table.category, table.category)

    embed = discord.Embed(
        title="\u2753 Who's That Pokemon?",
        description=(
            f"**Category:** {cat_label}\n"
            f"**First to {WINS_TO_WIN} wins** takes the pot.\n"
            "Hints are revealed over time \u2014 type the Pokemon's name in chat!"
        ),
        colour=discord.Colour.red(),
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
            f"\U0001f534 **{p.display_name}** \u2014 {p.bet}c"
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
    embed.set_footer(text=f"Host: {table.host_name} \u2502 Min {MIN_PLAYERS} players")
    return embed


def _playing_embed(table: PokeTable, remaining: int | None = None) -> discord.Embed:
    entry = table.current_entry
    dex_id, name, alts, types, gen, hint, legendary = entry

    embed = discord.Embed(
        title=f"\u2753 Who's That Pokemon? \u2014 Round {table.round_num}",
        colour=discord.Colour.dark_red(),
    )

    # Build hint text based on current hint level
    hint_parts: list[str] = []

    # Hint 1 (always shown): Types + Generation
    type_line = _type_str(types)
    gen_label = GEN_LABEL.get(gen, f"Gen {gen}")
    hint_parts.append(f"**Type:** {type_line}")
    hint_parts.append(f"**Generation:** {gen_label}")
    if legendary:
        hint_parts.append("**Status:** Legendary / Mythical")

    # Hint 2 (after HINT2_AT seconds): Descriptive clue
    if table.hint_level >= 2:
        hint_parts.append(f"\n\U0001f4a1 **Clue:** {hint}")

    # Hint 3 (after HINT3_AT seconds): First letter + length
    if table.hint_level >= 3:
        hint_parts.append(f"\n\U0001f520 **Name:** `{_blank_name(name)}` ({len(name)} letters)")

    embed.description = "\n".join(hint_parts) + "\n\n**Type your answer in chat!**"

    secs = remaining if remaining is not None else ROUND_TIME
    embed.add_field(name="\u23f1\ufe0f Time", value=f"**{secs}s**", inline=True)

    pot = sum(p.bet for p in table.players.values())
    embed.add_field(name="Pot", value=f"{pot}c", inline=True)

    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


def _round_result_embed(table: PokeTable) -> discord.Embed:
    winner = table.players[table.round_winner]
    entry = table.current_entry
    dex_id, name, *_ = entry
    solve_time = winner.answer_time - table.round_start_time
    is_last = winner.rounds_won >= WINS_TO_WIN or table.round_num >= MAX_ROUNDS

    embed = discord.Embed(
        title=f"\u2753 Round {table.round_num} \u2705",
        colour=discord.Colour.green(),
    )
    embed.description = (
        f"\U0001f3c6 **{winner.display_name}** got it in **{solve_time:.1f}s**!\n\n"
        f"It's **{name}**! (#{dex_id})"
    )
    embed.set_thumbnail(url=SPRITE_URL.format(dex_id))
    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    if not is_last:
        embed.set_footer(text="Next round in a few seconds\u2026")
    else:
        embed.set_footer(text="Final round \u2014 calculating results\u2026")
    return embed


def _timeout_embed(table: PokeTable) -> discord.Embed:
    entry = table.current_entry
    dex_id, name, *_ = entry
    max_wins = max((p.rounds_won for p in table.players.values()), default=0)
    is_last = max_wins >= WINS_TO_WIN or table.round_num >= MAX_ROUNDS

    embed = discord.Embed(
        title=f"\u2753 Round {table.round_num} (Time's Up!)",
        colour=discord.Colour.dark_grey(),
    )
    embed.description = (
        f"Nobody got it in {ROUND_TIME} seconds!\n\n"
        f"It was **{name}**! (#{dex_id})"
    )
    embed.set_thumbnail(url=SPRITE_URL.format(dex_id))
    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    if not is_last:
        embed.set_footer(text="Next round in a few seconds\u2026")
    else:
        embed.set_footer(text="Final round \u2014 calculating results\u2026")
    return embed


def _final_embed(
    table: PokeTable, *, payouts: dict[int, int], balances: dict[int, int],
) -> discord.Embed:
    max_wins = max((p.rounds_won for p in table.players.values()), default=0)
    is_refund = max_wins == 0

    embed = discord.Embed(
        title="\u2753 Who's That Pokemon? \u2014 Results",
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
            name=f"Paytable ({n} players)", value=" | ".join(pt_parts), inline=True,
        )

    embed.add_field(
        name="Rounds Played", value=str(table.total_rounds_played), inline=True,
    )
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


# ── Modals ───────────────────────────────────────────────────────────────────


class JoinPokeModal(ui.Modal):
    amount = ui.TextInput(
        label="Bet amount (coins)", placeholder="e.g. 100",
        required=True, max_length=10,
    )

    def __init__(
        self, table: PokeTable, view: "PokeTableView", balance: int,
    ) -> None:
        super().__init__(title="Join Who's That Pokemon?")
        self.table = table
        self.table_view = view
        self.amount.placeholder = f"e.g. 100 (bal: {balance}c)"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            amt = int(self.amount.value)
        except ValueError:
            await interaction.response.send_message("Enter a whole number.", ephemeral=True)
            return
        if amt < 1:
            await interaction.response.send_message("Must be at least 1 coin.", ephemeral=True)
            return
        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message("You're already in!", ephemeral=True)
            return
        try:
            await queries.update_casino_balance(str(uid), -amt)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins! (have {bal}c)", ephemeral=True,
            )
            return
        self.table.players[uid] = PokePlayer(
            user_id=uid, display_name=interaction.user.display_name, bet=amt,
        )
        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self.table_view,
        )


# ── View ─────────────────────────────────────────────────────────────────────


_CATEGORY_OPTIONS = [
    discord.SelectOption(
        label="All Generations", value="all",
        description="Pokemon from every generation", emoji="\U0001f30d", default=True,
    ),
    discord.SelectOption(
        label="Gen 1 (Kanto)", value="gen1",
        description="The original 151", emoji="\U0001f534",
    ),
    discord.SelectOption(
        label="Gen 2\u20134", value="gen2to4",
        description="Johto, Hoenn, and Sinnoh", emoji="\U0001f535",
    ),
    discord.SelectOption(
        label="Gen 5+", value="gen5plus",
        description="Unova through Paldea", emoji="\U0001f7e2",
    ),
    discord.SelectOption(
        label="Legendary & Mythical", value="legendary",
        description="Only legendary and mythical Pokemon", emoji="\u2b50",
    ),
]


class PokeTableView(ui.View):
    def __init__(
        self, table: PokeTable, active_tables: dict[int, PokeTable],
    ) -> None:
        super().__init__(timeout=900)
        self.table = table
        self.active_tables = active_tables
        self._update_buttons()

    def _update_buttons(self) -> None:
        phase = self.table.phase
        betting = phase == "betting"
        racing = phase in ("playing", "between_rounds")

        self.start_btn.disabled = not betting or len(self.table.players) < MIN_PLAYERS
        self.join_btn.disabled = not betting
        self.rebet_btn.disabled = not betting or not self.table.last_bets
        self.leave_btn.disabled = not betting
        self.close_btn.disabled = racing
        self.category_select.disabled = not betting

    def _pick_pokemon(self) -> tuple:
        """Pick a random Pokemon matching the table's category, avoiding repeats."""
        table = self.table
        cat = table.category

        pool: list[tuple] = []
        for entry in POKEMON:
            dex_id, name, alts, types, gen, hint, legendary = entry
            if cat == "gen1" and gen != 1:
                continue
            if cat == "gen2to4" and gen not in (2, 3, 4):
                continue
            if cat == "gen5plus" and gen < 5:
                continue
            if cat == "legendary" and not legendary:
                continue
            if dex_id not in table.used_ids:
                pool.append(entry)

        if not pool:
            # All exhausted — reset
            table.used_ids.clear()
            pool = [e for e in POKEMON if _cat_filter(e, cat)]

        choice = random.choice(pool)
        table.used_ids.add(choice[0])
        return choice

    # ── Row 0: Betting ───────────────────────────────────────────

    @ui.button(
        label="Start", style=discord.ButtonStyle.success, emoji="\u25b6\ufe0f", row=0,
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
            await interaction.response.send_message("Already started!", ephemeral=True)
            return
        if len(self.table.players) < MIN_PLAYERS:
            await interaction.response.send_message(
                f"Need at least {MIN_PLAYERS} players!", ephemeral=True,
            )
            return
        await self._start_race(interaction)

    @ui.button(
        label="Join", style=discord.ButtonStyle.primary, emoji="\U0001f534", row=0,
    )
    async def join_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Game in progress! Wait for the next one.", ephemeral=True,
            )
            return
        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message("You're already in!", ephemeral=True)
            return
        if len(self.table.players) >= MAX_PLAYERS:
            await interaction.response.send_message("Table is full!", ephemeral=True)
            return
        bal = await queries.get_or_create_casino_wallet(str(uid))
        await interaction.response.send_modal(JoinPokeModal(self.table, self, bal))

    @ui.button(
        label="Re-bet", style=discord.ButtonStyle.primary, emoji="\U0001f504", row=0,
    )
    async def rebet_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message("Game in progress!", ephemeral=True)
            return
        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message("You're already in!", ephemeral=True)
            return
        last = self.table.last_bets.get(uid)
        if last is None:
            await interaction.response.send_message(
                "No previous bet \u2014 use Join instead.", ephemeral=True,
            )
            return
        if len(self.table.players) >= MAX_PLAYERS:
            await interaction.response.send_message("Table is full!", ephemeral=True)
            return
        name, amt = last
        try:
            await queries.update_casino_balance(str(uid), -amt)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins for {amt}c re-bet! (have {bal}c)", ephemeral=True,
            )
            return
        self.table.players[uid] = PokePlayer(
            user_id=uid, display_name=name, bet=amt,
        )
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    @ui.button(
        label="Leave", style=discord.ButtonStyle.secondary, emoji="\U0001f6aa", row=0,
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
                "Can't leave during a game!", ephemeral=True,
            )
            return
        await queries.update_casino_balance(str(uid), player.bet)
        del self.table.players[uid]
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    # ── Row 1: Close ─────────────────────────────────────────────

    @ui.button(
        label="Close Table", style=discord.ButtonStyle.danger, emoji="\u2716\ufe0f", row=1,
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
                "Can't close during a game! Wait for it to finish.", ephemeral=True,
            )
            return
        await self._close_table(interaction)

    # ── Row 2: Category select ───────────────────────────────────

    @ui.select(
        placeholder="Category: All Generations",
        options=_CATEGORY_OPTIONS,
        row=2,
    )
    async def category_select(
        self, interaction: discord.Interaction, select: ui.Select,
    ) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can change the category!", ephemeral=True,
            )
            return
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Can't change category during a game!", ephemeral=True,
            )
            return
        self.table.category = select.values[0]
        chosen = next(
            (o for o in _CATEGORY_OPTIONS if o.value == self.table.category), None,
        )
        select.placeholder = f"Category: {chosen.label}" if chosen else "Category"
        for opt in select.options:
            opt.default = opt.value == self.table.category
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    # ── Race logic ───────────────────────────────────────────────

    async def _start_race(self, interaction: discord.Interaction) -> None:
        table = self.table

        for uid, p in table.players.items():
            table.last_bets[uid] = (p.display_name, p.bet)

        entry = self._pick_pokemon()
        table.current_entry = entry
        table.hint_level = 1
        table.round_num = 1
        table.round_winner = None
        table.round_solved.clear()
        table.round_messages.clear()
        table.phase = "playing"
        table.round_start_time = time.monotonic()

        for p in table.players.values():
            p.answer = None
            p.answer_time = None

        self._update_buttons()
        await interaction.response.edit_message(
            embed=_playing_embed(table), view=self,
        )
        table.race_task = asyncio.create_task(self._race_loop())

    async def _wait_for_solve_or_timeout(self) -> bool:
        table = self.table
        deadline = table.round_start_time + ROUND_TIME
        hint2_time = table.round_start_time + HINT2_AT
        hint3_time = table.round_start_time + HINT3_AT

        while True:
            now = time.monotonic()
            remaining = deadline - now
            if remaining <= 0:
                return table.round_winner is not None

            # Figure out next event: hint upgrade or 5s timer tick
            next_events: list[float] = []
            if table.hint_level < 2 and hint2_time > now:
                next_events.append(hint2_time - now)
            if table.hint_level < 3 and hint3_time > now:
                next_events.append(hint3_time - now)
            # Also tick every 5 seconds for timer updates
            next_events.append(min(5.0, remaining))
            wait = min(next_events)

            try:
                await asyncio.wait_for(table.round_solved.wait(), timeout=wait)
                return True
            except asyncio.TimeoutError:
                if table.round_winner is not None:
                    return True

                now2 = time.monotonic()
                # Upgrade hint levels
                if table.hint_level < 2 and now2 >= hint2_time:
                    table.hint_level = 2
                if table.hint_level < 3 and now2 >= hint3_time:
                    table.hint_level = 3

                secs_left = max(0, int(deadline - now2))
                if secs_left > 0 and table.message:
                    try:
                        await table.message.edit(
                            embed=_playing_embed(table, remaining=secs_left),
                            view=self,
                        )
                    except discord.HTTPException:
                        pass

    async def _clear_round_messages(self) -> None:
        messages = list(self.table.round_messages)
        self.table.round_messages.clear()
        for msg in messages:
            try:
                await msg.delete()
            except discord.HTTPException:
                pass

    async def _race_loop(self) -> None:
        table = self.table
        try:
            rnd = 0
            while True:
                rnd += 1

                if rnd > 1:
                    entry = self._pick_pokemon()
                    table.current_entry = entry
                    table.hint_level = 1
                    table.round_num = rnd
                    table.round_winner = None
                    table.round_solved.clear()
                    table.round_messages.clear()
                    table.phase = "playing"
                    table.round_start_time = time.monotonic()

                    for p in table.players.values():
                        p.answer = None
                        p.answer_time = None

                    self._update_buttons()
                    if table.message:
                        try:
                            await table.message.edit(
                                embed=_playing_embed(table), view=self,
                            )
                        except discord.HTTPException:
                            pass

                solved = await self._wait_for_solve_or_timeout()
                table.total_rounds_played += 1

                if solved and table.round_winner is not None:
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

                await self._clear_round_messages()

                table.phase = "between_rounds"
                await asyncio.sleep(ROUND_DELAY)

            await self._clear_round_messages()
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
            await queries.log_casino_result(str(uid), "pokemon", p.bet, payout)

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
                title="\u2753 Pokemon Table \u2014 Closed",
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
                    title="\u2753 Pokemon Table \u2014 Timed Out",
                    description="Table timed out. All bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                pass


# ── Category filter helper ───────────────────────────────────────────────────


def _cat_filter(entry: tuple, cat: str) -> bool:
    _, _, _, _, gen, _, legendary = entry
    if cat == "gen1":
        return gen == 1
    if cat == "gen2to4":
        return gen in (2, 3, 4)
    if cat == "gen5plus":
        return gen >= 5
    if cat == "legendary":
        return legendary
    return True  # "all"


# ── Cog ──────────────────────────────────────────────────────────────────────


class PokemonCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, PokeTable] = {}

    @app_commands.command(
        name="pokemon",
        description="Who's That Pokemon? Guess from progressive hints!",
    )
    async def pokemon(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            await interaction.response.send_message(
                "There's already a Pokemon game in this channel!",
                ephemeral=True,
            )
            return

        await queries.get_or_create_casino_wallet(str(interaction.user.id))

        table = PokeTable(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
        )
        self.active_tables[channel_id] = table

        view = PokeTableView(table, self.active_tables)
        embed = _betting_embed(table)
        await interaction.response.send_message(embed=embed, view=view)
        table.message = await interaction.original_response()

    @commands.Cog.listener("on_message")
    async def on_message(self, message: discord.Message) -> None:
        """Listen for chat guesses during active Pokemon rounds."""
        if message.author.bot:
            return

        table = self.active_tables.get(message.channel.id)
        if table is None or table.phase != "playing":
            return

        uid = message.author.id
        if uid not in table.players:
            return

        if table.round_winner is not None:
            return

        guess = message.content.strip()
        if len(guess) < 3:
            return

        # Only react to things that look like Pokemon names (mostly letters)
        alpha_chars = sum(1 for c in guess if c.isalpha())
        if alpha_chars < len(guess) * 0.5:
            return

        table.round_messages.append(message)

        if check_pokemon_answer(guess, table.current_entry):
            now = time.monotonic()
            player = table.players[uid]
            player.answer = guess
            player.answer_time = now
            player.rounds_won += 1
            table.round_winner = uid

            try:
                await message.add_reaction("\u2705")
            except discord.HTTPException:
                pass

            table.round_solved.set()
        else:
            try:
                await message.add_reaction("\u274c")
            except discord.HTTPException:
                pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PokemonCog(bot))
