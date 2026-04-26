"""Multiplayer /pokemon guessing game.

Progressive hints about a Pokemon; first to type its name wins the round.
First to WINS_TO_WIN rounds wins the match.
"""

import asyncio
import random
import time
import unicodedata
from dataclasses import dataclass, field
import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.cogs._elo_helpers import fmt_elo_change, update_elo_multiplayer

# ── Constants ────────────────────────────────────────────────────────────────

MAX_PLAYERS = 8
MIN_PLAYERS = 1
ROUND_TIME = 30  # seconds per round
ROUND_DELAY = 4  # seconds between rounds
WINS_TO_WIN = 3  # first to N wins
MAX_ROUNDS = 15  # safety cap
INACTIVITY_ROUNDS = 5  # auto-end after N consecutive unanswered rounds

# Hint reveal timing (seconds into the round)
HINT2_AT = 10
HINT3_AT = 20

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
    # ── Gen 1 (Kanto) ─────────────────────────────────────────────────────
    (1, "Bulbasaur", [], ["Grass", "Poison"], 1,
     "While it is young, it uses the nutrients that are stored in the seed on its back in order to grow.", False),
    (2, "Ivysaur", [], ["Grass", "Poison"], 1,
     "Exposure to sunlight adds to its strength. Sunlight also makes the bud on its back grow larger.", False),
    (3, "Venusaur", [], ["Grass", "Poison"], 1,
     "A bewitching aroma wafts from its flower. The fragrance becalms those engaged in a battle.", False),
    (4, "Charmander", [], ["Fire"], 1,
     "From the time it is born, a flame burns at the tip of its tail. Its life would end if the flame were to go out.", False),
    (5, "Charmeleon", [], ["Fire"], 1,
     "If it becomes agitated during battle, it spouts intense flames, incinerating its surroundings.", False),
    (6, "Charizard", [], ["Fire", "Flying"], 1,
     "Its wings can carry this Pok\u00e9mon close to an altitude of 4,600 feet. It blows out fire at very high temperatures.", False),
    (7, "Squirtle", [], ["Water"], 1,
     "When it feels threatened, it draws its limbs inside its shell and sprays water from its mouth.", False),
    (8, "Wartortle", [], ["Water"], 1,
     "It cleverly controls its furry ears and tail to maintain its balance while swimming.", False),
    (9, "Blastoise", [], ["Water"], 1,
     "The rocket cannons on its shell fire jets of water capable of punching holes through thick steel.", False),
    (10, "Caterpie", [], ["Bug"], 1,
     "Its short feet are tipped with suction pads that enable it to tirelessly climb slopes and walls.", False),
    (11, "Metapod", [], ["Bug"], 1,
     "Even though it is encased in a sturdy shell, the body inside is tender. It can\u2019t withstand a harsh attack.", False),
    (12, "Butterfree", [], ["Bug", "Flying"], 1,
     "It collects honey every day. It rubs honey onto the hairs on its legs to carry it back to its nest.", False),
    (13, "Weedle", [], ["Bug", "Poison"], 1,
     "Beware of the sharp stinger on its head. It hides in grass and bushes where it eats leaves.", False),
    (14, "Kakuna", [], ["Bug", "Poison"], 1,
     "Able to move only slightly. When endangered, it may stick out its stinger and poison its enemy.", False),
    (15, "Beedrill", [], ["Bug", "Poison"], 1,
     "It has three poisonous stingers on its forelegs and its tail. They are used to jab its enemy repeatedly.", False),
    (16, "Pidgey", [], ["Normal", "Flying"], 1,
     "Very docile. If attacked, it will often kick up sand to protect itself rather than fight back.", False),
    (17, "Pidgeotto", [], ["Normal", "Flying"], 1,
     "This Pok\u00e9mon is full of vitality. It constantly flies around its large territory in search of prey.", False),
    (18, "Pidgeot", [], ["Normal", "Flying"], 1,
     "This Pok\u00e9mon flies at Mach 2 speed, seeking prey. Its large talons are feared as wicked weapons.", False),
    (19, "Rattata", [], ["Normal"], 1,
     "Will chew on anything with its fangs. If you see one, you can be certain that 40 more live in the area.", False),
    (20, "Raticate", [], ["Normal"], 1,
     "Its hind feet are webbed. They act as flippers, so it can swim in rivers and hunt for prey.", False),
    (21, "Spearow", [], ["Normal", "Flying"], 1,
     "Inept at flying high. However, it can fly around very fast to protect its territory.", False),
    (22, "Fearow", [], ["Normal", "Flying"], 1,
     "A Pok\u00e9mon that dates back many years. If it senses danger, it flies high and away, instantly.", False),
    (23, "Ekans", [], ["Poison"], 1,
     "The older it gets, the longer it grows. At night, it wraps its long body around tree branches to rest.", False),
    (24, "Arbok", [], ["Poison"], 1,
     "The frightening patterns on its belly have been studied. Six variations have been confirmed.", False),
    (25, "Pikachu", [], ["Electric"], 1,
     "Possesses cheek sacs in which it stores electricity. This clever forest-dweller roasts tough berries with an electric shock before consuming them.", False),
    (26, "Raichu", [], ["Electric"], 1,
     "It can discharge bursts of electricity exceeding 100,000 volts\u2014 a single strike with that amount of power would incapacitate one of the Copperajah of my homeland.", False),
    (27, "Sandshrew", [], ["Ground"], 1,
     "It burrows into the ground to create its nest. If hard stones impede its tunneling, it uses its sharp claws to shatter them and then carries on digging.", False),
    (28, "Sandslash", [], ["Ground"], 1,
     "It climbs trees by hooking on with its sharp claws. this Pok\u00e9mon shares the berries it gathers, dropping them down to Sandshrew waiting below the tree.", False),
    (29, "Nidoran\u2640", [], ["Poison"], 1,
     "It uses its hard incisor teeth to crush and eat berries. The tip of a female Nidoran\u2019s horn is a bit more rounded than the tip of a male\u2019s horn.", False),
    (30, "Nidorina", [], ["Poison"], 1,
     "If the group is threatened, these Pok\u00e9mon will band together to assault enemies with a chorus of ultrasonic waves.", False),
    (31, "Nidoqueen", [], ["Poison", "Ground"], 1,
     "It pacifies offspring by placing them in the gaps between the spines on its back. The spines will never secrete poison while young are present.", False),
    (32, "Nidoran\u2642", [], ["Poison"], 1,
     "Small but brave, this Pok\u00e9mon will hold its ground and even risk its life in battle to protect the female it\u2019s friendly with.", False),
    (33, "Nidorino", [], ["Poison"], 1,
     "It\u2019s nervous and quick to act aggressively. The potency of its poison increases along with the level of adrenaline present in its body.", False),
    (34, "Nidoking", [], ["Poison", "Ground"], 1,
     "this Pok\u00e9mon prides itself on its strength. It\u2019s forceful and spirited in battle, making use of its thick tail and diamond-crushing horn.", False),
    (35, "Clefairy", [], ["Fairy"], 1,
     "It can be found in quiet mountain areas on a full moon's night. Its dancing and its tiny, faintly glowing wings confer upon it a lovely fairylike quality.", False),
    (36, "Clefable", [], ["Fairy"], 1,
     "Legend says that on clear, quiet nights, it listens for the voices of its kin living on the moon. I, too, often think of my homeland, so far away.", False),
    (37, "Vulpix", [], ["Fire"], 1,
     "In its belly burns a fire, which this Pok\u00e9mon spits out in the form of fireballs. When young, this Pok\u00e9mon has but one white tail. As the Pok\u00e9mon matures, this single tail splits into six.", False),
    (38, "Ninetales", [], ["Fire"], 1,
     "The coat of gleaming golden fur is quite magnificent. This species is said to store sacred power in its nine long tails and to live for a millennium.", False),
    (39, "Jigglypuff", [], ["Normal", "Fairy"], 1,
     "By freely changing the wavelength of its voice, this Pok\u00e9mon sings a mysterious melody sure to make any listener sleepy.", False),
    (40, "Wigglytuff", [], ["Normal", "Fairy"], 1,
     "It\u2019s proud of its fur, which is fine and delicate. In particular, the curl on its forehead has a texture that\u2019s perfectly heavenly.", False),
    (41, "Zubat", [], ["Poison", "Flying"], 1,
     "Makes its home in gloomy caves. Atrophied eyes have left this Pok\u00e9mon blind, so it scans its surroundings via sound waves that it emits from its mouth as it flies.", False),
    (42, "Golbat", [], ["Poison", "Flying"], 1,
     "It sinks its sharp fangs into other creatures and slurps up their blood. A closer look at the fangs reveals that they are hollow and akin to straws.", False),
    (43, "Oddish", [], ["Grass", "Poison"], 1,
     "During the day, it stays in the cold underground to avoid the sun. It grows by bathing in moonlight.", False),
    (44, "Gloom", [], ["Grass", "Poison"], 1,
     "What appears to be drool is actually sweet honey. It is very sticky and clings stubbornly if touched.", False),
    (45, "Vileplume", [], ["Grass", "Poison"], 1,
     "The larger its petals, the more toxic pollen it contains. Its big head is heavy and hard to hold up.", False),
    (46, "Paras", [], ["Bug", "Grass"], 1,
     "Sometimes seen at the foot of trees in humid forests. The mushrooms on its back\u2014called tochukaso\u2014are not present on infant specimens and instead emerge as this Pok\u00e9mon matures.", False),
    (47, "Parasect", [], ["Bug", "Grass"], 1,
     "Mushroom-lacking specimens of this Pok\u00e9mon lie unmoving in the forest, lending credence to the hypothesis that the large mushroom is in control of this Pok\u00e9mon's actions.", False),
    (48, "Venonat", [], ["Bug", "Poison"], 1,
     "Its large eyes act as radar. In a bright place, you can see that they are clusters of many tiny eyes.", False),
    (49, "Venomoth", [], ["Bug", "Poison"], 1,
     "The powdery scales on its wings are hard to remove from skin. They also contain poison that leaks out on contact.", False),
    (50, "Diglett", [], ["Ground"], 1,
     "It burrows through the ground at a shallow depth. It leaves raised earth in its wake, making it easy to spot.", False),
    (51, "Dugtrio", [], ["Ground"], 1,
     "These Diglett triplets dig over 60 miles below sea level. No one knows what it\u2019s like underground.", False),
    (52, "Meowth", [], ["Normal"], 1,
     "It washes its face regularly to keep the coin on its forehead spotless. It doesn\u2019t get along with Galarian this Pok\u00e9mon.", False),
    (53, "Persian", [], ["Normal"], 1,
     "Its elegant and refined behavior clashes with that of the barbaric Perrserker. The relationship between the two is one of mutual disdain.", False),
    (54, "Psyduck", [], ["Water"], 1,
     "Suffers perpetual headaches. If the agony grows too great, this Pok\u00e9mon's latent power erupts, contrary to this Pok\u00e9mon's intent. Ergo, I am exploring ways to ease the pain.", False),
    (55, "Golduck", [], ["Water"], 1,
     "Its body is strong, and it has webbing on its hands and feet. this Pok\u00e9mon can swim easily through rough seas, clawing its way through the high waves.", False),
    (56, "Mankey", [], ["Fighting"], 1,
     "An agile Pok\u00e9mon that lives in trees. It angers easily and will not hesitate to attack anything.", False),
    (57, "Primeape", [], ["Fighting"], 1,
     "It stops being angry only when nobody else is around. To view this moment is very difficult.", False),
    (58, "Growlithe", [], ["Fire"], 1,
     "They patrol their territory in pairs. I believe the igneous rock components in the fur of this species are the result of volcanic activity in its habitat.", False),
    (59, "Arcanine", [], ["Fire"], 1,
     "Snaps at its foes with fangs cloaked in blazing flame. Despite its bulk, it deftly feints every which way, leading opponents on a deceptively merry chase as it all but dances around them.", False),
    (60, "Poliwag", [], ["Water"], 1,
     "In rivers with fast-flowing water, this Pok\u00e9mon will cling to a rock by using its thick lips, which act like a suction cup.", False),
    (61, "Poliwhirl", [], ["Water"], 1,
     "This Pok\u00e9mon\u2019s sweat is a slimy mucus. When captured, this Pok\u00e9mon can slither from its enemies\u2019 grasp and escape.", False),
    (62, "Poliwrath", [], ["Water", "Fighting"], 1,
     "this Pok\u00e9mon is skilled at both swimming and martial arts. It uses its well-trained arms to dish out powerful punches.", False),
    (63, "Abra", [], ["Psychic"], 1,
     "Spends 18 hours of the day sleeping. Even while asleep, this Pok\u00e9mon can control its psychic powers\u2014should danger approach, the Pok\u00e9mon will simply teleport away.", False),
    (64, "Kadabra", [], ["Psychic"], 1,
     "There are rumors that a child with mystical powers became a this Pok\u00e9mon; however, this remains unverified. I suspect that the spoon this Pok\u00e9mon holds enhances its brain waves.", False),
    (65, "Alakazam", [], ["Psychic"], 1,
     "The longer this Pok\u00e9mon lives, the larger and heavier its head becomes. Our tests have shown that the strength of its psychic powers correlates positively to the weight of its head.", False),
    (66, "Machop", [], ["Fighting"], 1,
     "Though as small as a child, it has strength enough to easily throw a well-built adult. Striving to become ever stronger, this Pok\u00e9mon trains by carrying a Graveler on its shoulders.", False),
    (67, "Machoke", [], ["Fighting"], 1,
     "A sturdy creature boasting a robust physique and boundless stamina. Loves training above all else and voluntarily assists with tasks such as construction and clearing land.", False),
    (68, "Machamp", [], ["Fighting"], 1,
     "In close combat, its four arms afford it offensive and defensive supremacy. In but a blink, this valiant Pok\u00e9mon can overwhelm its foes with more than 1,000 blows from its fists.", False),
    (69, "Bellsprout", [], ["Grass", "Poison"], 1,
     "Prefers hot and humid places. It ensnares tiny bugs with its vines and devours them.", False),
    (70, "Weepinbell", [], ["Grass", "Poison"], 1,
     "When hungry, it swallows anything that moves. Its hapless prey is dissolved by strong acids.", False),
    (71, "Victreebel", [], ["Grass", "Poison"], 1,
     "Lures prey with the sweet aroma of honey. Swallowed whole, the prey is dissolved in a day, bones and all.", False),
    (72, "Tentacool", [], ["Water", "Poison"], 1,
     "They fire beams from the glassy, magenta orbs that resemble eyes atop their heads, and they drift in shallow seas. During low tide, they can sometimes be found on beaches, desiccated.", False),
    (73, "Tentacruel", [], ["Water", "Poison"], 1,
     "It has 80 tentacles, each with a venomous tip. These tentacles are also extendible, lengthening when this Pok\u00e9mon attempts to catch prey. Use caution.", False),
    (74, "Geodude", [], ["Rock", "Ground"], 1,
     "Makes its home in mountainous regions, using its arms to climb along harsh mountain roads. Can be troublesome\u2014carelessly kicking one will cause it to fly into a rage and chase after you.", False),
    (75, "Graveler", [], ["Rock", "Ground"], 1,
     "Dwells in holes dug into sheer walls of stone. It enjoys rolling down slopes as though it were a boulder during a rockfall, so keep an eye upward while traversing mountain roads.", False),
    (76, "Golem", [], ["Rock", "Ground"], 1,
     "The rocklike shell is shed each year. The cast-off shell then crumbles, reverting to a mass of soil, which can be spread across fields to promote crop growth.", False),
    (77, "Ponyta", [], ["Fire"], 1,
     "These Pok\u00e9mon live in herds out in the grassland. Newborn foals lack their fiery manes, which will develop about an hour after birth.", False),
    (78, "Rapidash", [], ["Fire"], 1,
     "Fiery mane aglow, this Pok\u00e9mon darts like an arrow across the land. This prodigiously swift creature can traverse the vast region of Hisui in a day and a half.", False),
    (79, "Slowpoke", [], ["Water", "Psychic"], 1,
     "When this Pok\u00e9mon\u2019s tail is soaked in water, sweetness seeps from it. this Pok\u00e9mon uses this trait to lure in and fish up other Pok\u00e9mon.", False),
    (80, "Slowbro", [], ["Water", "Psychic"], 1,
     "Being bitten by a Shellder shocked this Pok\u00e9mon into standing on two legs. If the Shellder lets go, it seems this Pok\u00e9mon will turn back into a Slowpoke.", False),
    (81, "Magnemite", [], ["Electric", "Steel"], 1,
     "A bizarre Pok\u00e9mon with but a single eye embedded in an iron sphere. I suspect this creature levitates due to the magnetism it emits from its arms, which resemble horseshoe-shaped magnets.", False),
    (82, "Magneton", [], ["Electric", "Steel"], 1,
     "Three Magnemite gathered to evolve into this Pok\u00e9mon. The source of much vexation on my part, as its powerful magnetism destroys my research equipment.", False),
    (83, "Farfetch\u2019d", [], ["Normal", "Flying"], 1,
     "They use a plant stalk as a weapon, but not all of them use it in the same way. Several distinct styles of stalk fighting have been observed.", False),
    (84, "Doduo", [], ["Normal", "Flying"], 1,
     "Its short wings make flying difficult. Instead, this Pok\u00e9mon runs at high speed on developed legs.", False),
    (85, "Dodrio", [], ["Normal", "Flying"], 1,
     "One of Doduo\u2019s two heads splits to form a unique species. It runs close to 40 mph in prairies.", False),
    (86, "Seel", [], ["Water"], 1,
     "Loves freezing-cold conditions. Relishes swimming in a frigid climate of around 14 degrees Fahrenheit.", False),
    (87, "Dewgong", [], ["Water", "Ice"], 1,
     "Its entire body is a snowy white. Unharmed by even intense cold, it swims powerfully in icy waters.", False),
    (88, "Grimer", [], ["Poison"], 1,
     "Made of congealed sludge. It smells too putrid to touch. Even weeds won\u2019t grow in its path.", False),
    (89, "Muk", [], ["Poison"], 1,
     "Smells so awful, it can cause fainting. Through degeneration of its nose, it lost its sense of smell.", False),
    (90, "Shellder", [], ["Water"], 1,
     "Its hard shell repels any kind of attack. It is vulnerable only when its shell is open.", False),
    (91, "Cloyster", [], ["Water", "Ice"], 1,
     "Once it slams its shell shut, it is impossible to open, even by those with superior strength.", False),
    (92, "Gastly", [], ["Ghost", "Poison"], 1,
     "Gaseous and completely impalpable. Also highly dangerous\u2014 inhaling part of its poisonous body will cause one to faint instantly.", False),
    (93, "Haunter", [], ["Ghost", "Poison"], 1,
     "This frightful, malevolent spirit can glide through walls, appearing wherever it likes. According to rumor, victims of a this Pok\u00e9mon's lick will wither to death day by day.", False),
    (94, "Gengar", [], ["Ghost", "Poison"], 1,
     "Possesses potential victims' shadows in an effort to steal away the victims' lives. If your shadow begins to laugh, you must take hold of a protective charm posthaste!", False),
    (95, "Onix", [], ["Rock", "Ground"], 1,
     "This chain of immense stones resembles a giant serpent. Tremors shake the earth above as it burrows deep beneath the ground, feeding on boulders as it goes.", False),
    (96, "Drowzee", [], ["Psychic"], 1,
     "If you sleep by it all the time, it will sometimes show you dreams it had eaten in the past.", False),
    (97, "Hypno", [], ["Psychic"], 1,
     "Avoid eye contact if you come across one. It will try to put you to sleep by using its pendulum.", False),
    (98, "Krabby", [], ["Water"], 1,
     "If it senses danger approaching, it cloaks itself with bubbles from its mouth so it will look bigger.", False),
    (99, "Kingler", [], ["Water"], 1,
     "Its oversized claw is very powerful, but when it\u2019s not in battle, the claw just gets in the way.", False),
    (100, "Voltorb", [], ["Electric"], 1,
     "An enigmatic Pok\u00e9mon that happens to bear a resemblance to a Pok\u00e9 Ball. When excited, it discharges the electric current it has stored in its belly, then lets out a great, uproarious laugh.", False),
    (101, "Electrode", [], ["Electric"], 1,
     "The tissue on the surface of its body is curiously similar in composition to an Apricorn. When irritated, this Pok\u00e9mon lets loose an electric current equal to 20 lightning bolts.", False),
    (102, "Exeggcute", [], ["Grass", "Psychic"], 1,
     "These Pok\u00e9mon get nervous when they\u2019re not in a group of six. The minute even one member of the group goes missing, this Pok\u00e9mon become cowardly.", False),
    (103, "Exeggutor", [], ["Grass", "Psychic"], 1,
     "When they work together, this Pok\u00e9mon\u2019s three heads can put out powerful psychic energy. Cloudy days make this Pok\u00e9mon sluggish.", False),
    (104, "Cubone", [], ["Ground"], 1,
     "This Pok\u00e9mon wears the skull of its deceased mother. Sometimes this Pok\u00e9mon\u2019s dreams make it cry, but each tear this Pok\u00e9mon sheds makes it stronger.", False),
    (105, "Marowak", [], ["Ground"], 1,
     "When this Pok\u00e9mon evolved, the skull of its mother fused to it. this Pok\u00e9mon\u2019s temperament also turned vicious at the same time.", False),
    (106, "Hitmonlee", [], ["Fighting"], 1,
     "The legs freely contract and stretch. The stretchy legs allow it to hit a distant foe with a rising kick.", False),
    (107, "Hitmonchan", [], ["Fighting"], 1,
     "Its punches slice the air. However, it seems to need a short break after fighting for three minutes.", False),
    (108, "Lickitung", [], ["Normal"], 1,
     "Wields its long tongue deftly, as though it were an arm. The Pok\u00e9mon's viscous saliva, once it has been collected and boiled down, yields a strong and highly useful adhesive.", False),
    (109, "Koffing", [], ["Poison"], 1,
     "It adores polluted air. Some claim that this Pok\u00e9mon used to be more plentiful in the Galar region than they are now.", False),
    (110, "Weezing", [], ["Poison"], 1,
     "It can\u2019t suck in air quite as well as a Galarian this Pok\u00e9mon, but the toxins it creates are more potent than those of its counterpart.", False),
    (111, "Rhyhorn", [], ["Ground", "Rock"], 1,
     "Ludicrously strong\u2014when it butts heads with a mountain, it is the mountain that shatters. But its short legs struggle with turns, and it is incapable of stopping unless it collides with something.", False),
    (112, "Rhydon", [], ["Ground", "Rock"], 1,
     "Rapidly rotates its horn to bore through bedrock. It swaggers around volcanic regions, protected from the lava's heat by its tough, armorlike hide.", False),
    (113, "Chansey", [], ["Normal"], 1,
     "This purehearted Pok\u00e9mon shares its eggs with the injured.", False),
    (114, "Tangela", [], ["Grass"], 1,
     "It is cloaked entirely in blue vines, preventing any glimpse of its true identity. The vines impart a refreshing sensation when chewed\u2014they're useful as a spice.", False),
    (115, "Kangaskhan", [], ["Normal"], 1,
     "There are records of a lost human child being raised by a childless this Pok\u00e9mon.", False),
    (116, "Horsea", [], ["Water"], 1,
     "They swim with dance-like motions and cause whirlpools to form. this Pok\u00e9mon compete to see which of them can generate the biggest whirlpool.", False),
    (117, "Seadra", [], ["Water"], 1,
     "this Pok\u00e9mon\u2019s mouth is slender, but its suction power is strong. In an instant, this Pok\u00e9mon can suck in food that\u2019s larger than the opening of its mouth.", False),
    (118, "Goldeen", [], ["Water"], 1,
     "Its dorsal and pectoral fins are strongly developed like muscles. It can swim at a speed of five knots.", False),
    (119, "Seaking", [], ["Water"], 1,
     "Using its horn, it bores holes in riverbed boulders, making nests to prevent its eggs from washing away.", False),
    (120, "Staryu", [], ["Water"], 1,
     "Fish Pok\u00e9mon nibble at it, but this Pok\u00e9mon isn\u2019t bothered. Its body regenerates quickly, even if part of it is completely torn off.", False),
    (121, "Starmie", [], ["Water", "Psychic"], 1,
     "this Pok\u00e9mon swims by spinning its body at high speed. As this Pok\u00e9mon cruises through the ocean, it absorbs tiny plankton.", False),
    (122, "Mr. Mime", [], ["Psychic", "Fairy"], 1,
     "The behavior of this clown-like Pok\u00e9mon reminds one of pantomime. It creates invisible walls using a force emitted from its fingertips.", False),
    (123, "Scyther", [], ["Bug", "Flying"], 1,
     "The large, wickedly sharp scythes on its forearms are truly fearsome weapons. Prey's attempts to flee are unfailingly thwarted by this Pok\u00e9mon's nimble motions.", False),
    (124, "Jynx", [], ["Ice", "Psychic"], 1,
     "The this Pok\u00e9mon of Galar often have beautiful and delicate voices. Some of these Pok\u00e9mon have even gathered a fan base.", False),
    (125, "Electabuzz", [], ["Electric"], 1,
     "Feeds on electrical energy. During sudden showers beneath looming thunderclouds, one can observe this Pok\u00e9mon scaling tall trees, where the Pok\u00e9mon will then wait for lightning to strike.", False),
    (126, "Magmar", [], ["Fire"], 1,
     "Legend has it that this Pok\u00e9mon was born from the crater of a volcano. When wounded, it bathes in lava to heal its body, much as one would soak in a hot spring.", False),
    (127, "Pinsir", [], ["Bug"], 1,
     "This Pok\u00e9mon clamps its pincers down on its prey and then either splits the prey in half or flings it away.", False),
    (128, "Tauros", [], ["Normal"], 1,
     "The this Pok\u00e9mon of the Galar region are volatile in nature, and they won\u2019t allow people to ride on their backs.", False),
    (129, "Magikarp", [], ["Water"], 1,
     "A feeble, pitiful imbecile of a Pok\u00e9mon that is nonetheless very hardy. Unperturbed by turbid water, it can be found living in all sorts of places.", False),
    (130, "Gyarados", [], ["Water", "Flying"], 1,
     "I suspect this Pok\u00e9mon to be the true identity of a dragon written of in ancient texts, which claimed that it razed an entire village with white-hot beams from its maw.", False),
    (131, "Lapras", [], ["Water", "Ice"], 1,
     "Crossing icy seas is no issue for this cold-resistant Pok\u00e9mon. Its smooth skin is a little cool to the touch.", False),
    (132, "Ditto", [], ["Normal"], 1,
     "When it encounters another this Pok\u00e9mon, it will move faster than normal to duplicate that opponent exactly.", False),
    (133, "Eevee", [], ["Normal"], 1,
     "Harbors the potential to evolve into manifold forms. Within this Pok\u00e9mon lies the key to the mysteries of Pok\u00e9mon evolution\u2014I'm certain of it.", False),
    (134, "Vaporeon", [], ["Water"], 1,
     "Tests show that its cells closely resemble water molecules, which perhaps explains its ability to conceal its form while submerged. I believe the origins of mermaid folklore lie with this Pok\u00e9mon.", False),
    (135, "Jolteon", [], ["Electric"], 1,
     "Bristles its fur into sharp, needlelike points when enraged. One can hear electricity crackle in its breath when it exhales.", False),
    (136, "Flareon", [], ["Fire"], 1,
     "Flames burn within a saclike organ inside this Pok\u00e9mon. When this Pok\u00e9mon inhales, these flames grow in intensity, reaching a mighty 3,000 degrees Fahrenheit.", False),
    (137, "Porygon", [], ["Normal"], 1,
     "It has no discernible heartbeat and does not seem to draw breath, and yet it appears to function without issue. I cannot even begin to explain this utterly bizarre anomaly.", False),
    (138, "Omanyte", [], ["Rock", "Water"], 1,
     "This Pok\u00e9mon is a member of an ancient, extinct species. this Pok\u00e9mon paddles through water with its 10 tentacles, looking like it\u2019s just drifting along.", False),
    (139, "Omastar", [], ["Rock", "Water"], 1,
     "this Pok\u00e9mon\u2019s sharp fangs could crush rock, but the Pok\u00e9mon can attack only the prey that come within reach of its tentacles.", False),
    (140, "Kabuto", [], ["Rock", "Water"], 1,
     "While some say this species has gone extinct, this Pok\u00e9mon sightings are apparently fairly common in some places.", False),
    (141, "Kabutops", [], ["Rock", "Water"], 1,
     "The cause behind the extinction of this species is unknown. this Pok\u00e9mon were aggressive Pok\u00e9mon that inhabited warm seas.", False),
    (142, "Aerodactyl", [], ["Rock", "Flying"], 1,
     "this Pok\u00e9mon\u2019s sawlike fangs can shred skin to tatters\u2014even the skin of Steel-type Pok\u00e9mon.", False),
    (143, "Snorlax", [], ["Normal"], 1,
     "This glutton appears in villages without warning and devours the entirety of their rice granaries\u2014such occurrences have long been counted among the gravest of disasters.", False),
    (144, "Articuno", [], ["Ice", "Flying"], 1,
     "This Pok\u00e9mon can control ice at will. this Pok\u00e9mon is said to live in snowy mountains riddled with permafrost.", True),
    (145, "Zapdos", [], ["Electric", "Flying"], 1,
     "this Pok\u00e9mon is a legendary bird Pok\u00e9mon. It\u2019s said that when this Pok\u00e9mon rubs its feathers together, lightning will fall immediately after.", True),
    (146, "Moltres", [], ["Fire", "Flying"], 1,
     "There are stories of this Pok\u00e9mon using its radiant, flame-cloaked wings to light up paths for those lost in the mountains.", True),
    (147, "Dratini", [], ["Dragon"], 1,
     "This Pok\u00e9mon was long considered to be no more than a myth. The small lump on a this Pok\u00e9mon\u2019s forehead is actually a horn that\u2019s still coming in.", False),
    (148, "Dragonair", [], ["Dragon"], 1,
     "This Pok\u00e9mon gathers power in the orbs on its tail and controls the weather. When enshrouded by an aura, this Pok\u00e9mon has a mystical appearance.", False),
    (149, "Dragonite", [], ["Dragon", "Flying"], 1,
     "This Pok\u00e9mon is known as the Sea Incarnate. Figureheads that resemble this Pok\u00e9mon decorate the bows of many ships.", False),
    (150, "Mewtwo", [], ["Psychic"], 1,
     "Its DNA is almost the same as Mew\u2019s. However, its size and disposition are vastly different.", True),
    (151, "Mew", [], ["Psychic"], 1,
     "When viewed through a microscope, this Pok\u00e9mon\u2019s short, fine, delicate hair can be seen.", True),
    # ── Gen 2 (Johto) ─────────────────────────────────────────────────────
    (152, "Chikorita", [], ["Grass"], 2,
     "In battle, this Pok\u00e9mon waves its leaf around to keep the foe at bay. However, a sweet fragrance also wafts from the leaf, becalming the battling Pok\u00e9mon and creating a cozy, friendly atmosphere all around.", False),
    (153, "Bayleef", [], ["Grass"], 2,
     "this Pok\u00e9mon\u2019s neck is ringed by curled-up leaves. Inside each tubular leaf is a small shoot of a tree. The fragrance of this shoot makes people peppy.", False),
    (154, "Meganium", [], ["Grass"], 2,
     "The fragrance of this Pok\u00e9mon\u2019s flower soothes and calms emotions. In battle, this Pok\u00e9mon gives off more of its becalming scent to blunt the foe\u2019s fighting spirit.", False),
    (155, "Cyndaquil", [], ["Fire"], 2,
     "Hails from the Johto region. Though usually curled into a ball due to its timid disposition, it harbors tremendous firepower.", False),
    (156, "Quilava", [], ["Fire"], 2,
     "This creature's fur is most mysterious\u2014it is wholly impervious to the burning touch of flame. Should this Pok\u00e9mon turn its back to you, take heed! Such a posture indicates a forthcoming attack.", False),
    (157, "Typhlosion", [], ["Fire"], 2,
     "Said to purify lost, forsaken souls with its flames and guide them to the afterlife. I believe its form has been influenced by the energy of the sacred mountain towering at Hisui's center.", False),
    (158, "Totodile", [], ["Water"], 2,
     "Despite the smallness of its body, this Pok\u00e9mon\u2019s jaws are very powerful. While the Pok\u00e9mon may think it is just playfully nipping, its bite has enough power to cause serious injury.", False),
    (159, "Croconaw", [], ["Water"], 2,
     "Once this Pok\u00e9mon has clamped its jaws on its foe, it will absolutely not let go. Because the tips of its fangs are forked back like barbed fishhooks, they become impossible to remove when they have sunk in.", False),
    (160, "Feraligatr", [], ["Water"], 2,
     "this Pok\u00e9mon intimidates its foes by opening its huge mouth. In battle, it will kick the ground hard with its thick and powerful hind legs to charge at the foe at an incredible speed.", False),
    (161, "Sentret", [], ["Normal"], 2,
     "When this Pok\u00e9mon sleeps, it does so while another stands guard. The sentry wakes the others at the first sign of danger. When this Pok\u00e9mon becomes separated from its pack, it becomes incapable of sleep due to fear.", False),
    (162, "Furret", [], ["Normal"], 2,
     "this Pok\u00e9mon has a very slim build. When under attack, it can slickly squirm through narrow spaces and get away. In spite of its short limbs, this Pok\u00e9mon is very nimble and fleet.", False),
    (163, "Hoothoot", [], ["Normal", "Flying"], 2,
     "It begins to hoot at the same time every day. Some Trainers use them in place of clocks.", False),
    (164, "Noctowl", [], ["Normal", "Flying"], 2,
     "When it needs to think, it rotates its head 180 degrees to sharpen its intellectual power.", False),
    (165, "Ledyba", [], ["Bug", "Flying"], 2,
     "These very cowardly Pok\u00e9mon join together and use Reflect to protect their nest.", False),
    (166, "Ledian", [], ["Bug", "Flying"], 2,
     "It flies through the night sky, sprinkling sparkly dust. According to some, if that dust sticks to you, good things will happen to you.", False),
    (167, "Spinarak", [], ["Bug", "Poison"], 2,
     "Although the poison from its fangs isn\u2019t that strong, it\u2019s potent enough to weaken prey that gets caught in its web.", False),
    (168, "Ariados", [], ["Bug", "Poison"], 2,
     "It spews threads from its mouth to catch its prey. When night falls, it leaves its web to go hunt aggressively.", False),
    (169, "Crobat", [], ["Poison", "Flying"], 2,
     "Its hind limbs have become another set of wings. this Pok\u00e9mon expertly maneuvers its four wings to dart in exquisite fashion through even the most confined caves without losing any speed.", False),
    (170, "Chinchou", [], ["Water", "Electric"], 2,
     "On the dark ocean floor, its only means of communication is its constantly flashing lights.", False),
    (171, "Lanturn", [], ["Water", "Electric"], 2,
     "This Pok\u00e9mon flashes a bright light that blinds its prey. This creates an opening for it to deliver an electrical attack.", False),
    (172, "Pichu", [], ["Electric"], 2,
     "this Pok\u00e9mon stores electricity in the sacs on its cheeks but discharges it inadvertently when agitated or excited. Being yet immature, the Pok\u00e9mon's handling of electricity is rather inept.", False),
    (173, "Cleffa", [], ["Fairy"], 2,
     "In silhouette, they resemble twinkling starlight. When shooting stars rain from the night sky, this Pok\u00e9mon gather in numbers and dance as though they are indeed incarnations of the stars.", False),
    (174, "Igglybuff", [], ["Normal", "Fairy"], 2,
     "Taking advantage of the softness of its body, this Pok\u00e9mon moves as if bouncing. Its body turns a deep pink when its temperature rises.", False),
    (175, "Togepi", [], ["Fairy"], 2,
     "This ovate creature is frequently mistaken for a moving egg when encountered out in the fields or in the mountains. Its guileless smile soothes the soul.", False),
    (176, "Togetic", [], ["Fairy", "Flying"], 2,
     "No records exist of this Pok\u00e9mon being seen in the wilds. Rumors abound that it evolves under the loving care of a trusted human companion, upon whom the Pok\u00e9mon then bestows great joy.", False),
    (177, "Natu", [], ["Psychic", "Flying"], 2,
     "Because its wings aren\u2019t yet fully grown, it has to hop to get around. It is always staring at something.", False),
    (178, "Xatu", [], ["Psychic", "Flying"], 2,
     "This odd Pok\u00e9mon can see both the past and the future. It eyes the sun\u2019s movement all day.", False),
    (179, "Mareep", [], ["Electric"], 2,
     "Rubbing its fleece generates electricity. You\u2019ll want to pet it because it\u2019s cute, but if you use your bare hand, you\u2019ll get a painful shock.", False),
    (180, "Flaaffy", [], ["Electric"], 2,
     "It stores electricity in its fluffy fleece. If it stores up too much, it will start to go bald in those patches.", False),
    (181, "Ampharos", [], ["Electric"], 2,
     "Its tail shines bright and strong. It has been prized since long ago as a beacon for sailors.", False),
    (182, "Bellossom", [], ["Grass"], 2,
     "this Pok\u00e9mon gather at times and appear to dance. They say that the dance is a ritual to summon the sun.", False),
    (183, "Marill", [], ["Water", "Fairy"], 2,
     "Even after this Pok\u00e9mon swims in a cold sea, its water- repellent fur dries almost as soon as this Pok\u00e9mon leaves the water. That\u2019s why this Pok\u00e9mon is never cold.", False),
    (184, "Azumarill", [], ["Water", "Fairy"], 2,
     "These Pok\u00e9mon create air-filled bubbles. When Azurill play in rivers, this Pok\u00e9mon will cover them with these bubbles.", False),
    (185, "Sudowoodo", [], ["Rock"], 2,
     "Though it pretends to be a tree, it fails to fool even children. To the touch, its body feels more like rock than tree bark. this Pok\u00e9mon's extreme aversion to water merits special note.", False),
    (186, "Politoed", [], ["Water"], 2,
     "The cry of a male is louder than that of a female. Male this Pok\u00e9mon with deep, menacing voices find more popularity with the opposite gender.", False),
    (187, "Hoppip", [], ["Grass", "Flying"], 2,
     "This Pok\u00e9mon drifts and floats with the wind. If it senses the approach of strong winds, this Pok\u00e9mon links its leaves with other this Pok\u00e9mon to prepare against being blown away.", False),
    (188, "Skiploom", [], ["Grass", "Flying"], 2,
     "this Pok\u00e9mon\u2019s flower blossoms when the temperature rises above 64 degrees Fahrenheit. How much the flower opens depends on the temperature. For that reason, this Pok\u00e9mon is sometimes used as a thermometer.", False),
    (189, "Jumpluff", [], ["Grass", "Flying"], 2,
     "this Pok\u00e9mon rides warm southern winds to cross the sea and fly to foreign lands. The Pok\u00e9mon descends to the ground when it encounters cold air while it is floating.", False),
    (190, "Aipom", [], ["Normal"], 2,
     "This treetop dweller possesses a tail as dexterous as a hand. Ancient writings describe this Pok\u00e9mon as a one-armed oddity.", False),
    (191, "Sunkern", [], ["Grass"], 2,
     "this Pok\u00e9mon tries to move as little as it possibly can. It does so because it tries to conserve all the nutrients it has stored in its body for its evolution. It will not eat a thing, subsisting only on morning dew.", False),
    (192, "Sunflora", [], ["Grass"], 2,
     "this Pok\u00e9mon converts solar energy into nutrition. It moves around actively in the daytime when it is warm. It stops moving as soon as the sun goes down for the night.", False),
    (193, "Yanma", [], ["Bug", "Flying"], 2,
     "Its frail wings are so thin that one can see clear through them. However, during flight these wings exhibit the power to churn air with force enough to launch a house skyward.", False),
    (194, "Wooper", [], ["Water", "Ground"], 2,
     "When walking on land, it covers its body with a poisonous film that keeps its skin from dehydrating.", False),
    (195, "Quagsire", [], ["Water", "Ground"], 2,
     "Its body is always slimy. It often bangs its head on the river bottom as it swims but seems not to care.", False),
    (196, "Espeon", [], ["Psychic"], 2,
     "Wields an arcane power with which it can predict the weather and even people's thoughts. When bathed in sunshine, the scarlet orb on its brow glows and builds energy.", False),
    (197, "Umbreon", [], ["Dark"], 2,
     "It is most active in the wee hours of the night, when moonlight bathes the land. Its large eyes can pierce the darkness and perceive prey with absolute clarity.", False),
    (198, "Murkrow", [], ["Dark", "Flying"], 2,
     "Widely shunned as a bearer of ill fortune. Upon crossing paths with this creature, I've been told one must chant \u201dWorkrum, Workrum\u2014bad luck, don't come\u201d as a protective incantation.", False),
    (199, "Slowking", [], ["Water", "Psychic"], 2,
     "this Pok\u00e9mon can solve any problem presented to it, but no one can understand a thing this Pok\u00e9mon says.", False),
    (200, "Misdreavus", [], ["Ghost"], 2,
     "It conceals itself in darkness, sending chills up travelers' spines with its childlike weeping. As it observes the frightened travelers with glee, the red orbs upon its chest let off an eerie light.", False),
    (201, "Unown", [], ["Psychic"], 2,
     "It is hard to believe these strangely shaped Pok\u00e9mon are truly living creatures. I've pointed out that the species' many forms resemble writing from other lands; no one will take me seriously.", False),
    (202, "Wobbuffet", [], ["Psychic"], 2,
     "To keep its pitch-black tail hidden, it lives quietly in the darkness. It is never first to attack.", False),
    (203, "Girafarig", [], ["Normal", "Psychic"], 2,
     "this Pok\u00e9mon\u2019s rear head contains a tiny brain that is too small for thinking. However, the rear head doesn\u2019t need to sleep, so it can keep watch over its surroundings 24 hours a day.", False),
    (204, "Pineco", [], ["Bug"], 2,
     "It sticks tree bark to itself with its saliva, making itself thicker and larger. Elderly this Pok\u00e9mon are ridiculously huge.", False),
    (205, "Forretress", [], ["Bug", "Steel"], 2,
     "In the moment that it gulps down its prey, the inside of its shell is exposed, but to this day, no one has ever seen that sight.", False),
    (206, "Dunsparce", [], ["Normal"], 2,
     "The nests this Pok\u00e9mon live in are mazes of tunnels. They never get lost in their own nests\u2014they can tell where they are by the scent of the dirt.", False),
    (207, "Gligar", [], ["Ground", "Flying"], 2,
     "Its tail is tipped by a thick, horrible stinger. To bring down prey, it will first obscure their vision by covering their faces with its body, and then it will use the stinger to inject them with venom.", False),
    (208, "Steelix", [], ["Steel", "Ground"], 2,
     "This Pok\u00e9mon evolved through use of a strange item. Its body is coated with steel powder and notably hard\u2014not even diamond can leave so much as a scratch.", False),
    (209, "Snubbull", [], ["Fairy"], 2,
     "In contrast to its appearance, it\u2019s quite timid. When playing with other puppy Pok\u00e9mon, it sometimes gets bullied.", False),
    (210, "Granbull", [], ["Fairy"], 2,
     "Although it\u2019s popular with young people, this Pok\u00e9mon is timid and sensitive, so it\u2019s totally incompetent as a watchdog.", False),
    (211, "Qwilfish", [], ["Water", "Poison"], 2,
     "Fishers detest this troublesome Pok\u00e9mon because it sprays poison from its spines, getting it everywhere. A different form of this Pok\u00e9mon lives in other regions.", False),
    (212, "Scizor", [], ["Bug", "Steel"], 2,
     "Evolved by way of a curious item. The shell covering its body has been shown to be stronger than forged steel.", False),
    (213, "Shuckle", [], ["Bug", "Rock"], 2,
     "The berries stored in its vaselike shell eventually become a thick, pulpy juice.", False),
    (214, "Heracross", [], ["Bug", "Fighting"], 2,
     "This Pok\u00e9mon has an unparalleled horn. this Pok\u00e9mon itself demonstrates tremendous power\u2014it's capable of throwing several people trained in the traditional arts of war at once.", False),
    (215, "Sneasel", [], ["Dark", "Ice"], 2,
     "Its sturdy, curved claws are ideal for traversing precipitous cliffs. From the tips of these claws drips a venom that infiltrates the nerves of any prey caught in this Pok\u00e9mon's grasp.", False),
    (216, "Teddiursa", [], ["Normal"], 2,
     "It licks its paws because of the sweet honey that has soaked into them. It is cunning, stealing into the nests of Combee and taking for itself the honey that the Combee have amassed.", False),
    (217, "Ursaring", [], ["Normal"], 2,
     "When the cold season arrives in Hisui, this Pok\u00e9mon will wander fields and mountains alike in search of its favorite berries. this Pok\u00e9mon's hunger during this time makes it a ferocious danger.", False),
    (218, "Slugma", [], ["Fire"], 2,
     "this Pok\u00e9mon does not have any blood in its body. Instead, intensely hot magma circulates throughout this Pok\u00e9mon\u2019s body, carrying essential nutrients and oxygen to its organs.", False),
    (219, "Magcargo", [], ["Fire", "Rock"], 2,
     "this Pok\u00e9mon\u2019s body temperature is approximately 18,000 degrees Fahrenheit. Water is vaporized on contact. If this Pok\u00e9mon is caught in the rain, the raindrops instantly turn into steam, cloaking the area in a thick fog.", False),
    (220, "Swinub", [], ["Ice", "Ground"], 2,
     "this Pok\u00e9mon excels at sniffing out mushrooms buried beneath grass or snow. Since ancient times, the people of Hisui have often relied upon this skill.", False),
    (221, "Piloswine", [], ["Ice", "Ground"], 2,
     "The long fur of this Pok\u00e9mon covers its eyes, ears, and even limbs, allowing this Pok\u00e9mon to resist harshly frigid conditions. The Pok\u00e9mon's white tusks can be used to defeat its enemies.", False),
    (222, "Corsola", [], ["Water", "Rock"], 2,
     "These Pok\u00e9mon live in warm seas. In prehistoric times, many lived in the oceans around the Galar region as well.", False),
    (223, "Remoraid", [], ["Water"], 2,
     "Spits water from its mouth with incredible accuracy. It captures Burmy by shooting them down off the branches from which they dangle.", False),
    (224, "Octillery", [], ["Water"], 2,
     "While this Pok\u00e9mon still shoots water from its mouth, the drastic anatomical difference between it and Remoraid meant that for a long time, no one believed the former evolved from the latter.", False),
    (225, "Delibird", [], ["Ice", "Flying"], 2,
     "It has a generous habit of sharing its food with people and Pok\u00e9mon, so it\u2019s always scrounging around for more food.", False),
    (226, "Mantine", [], ["Water", "Flying"], 2,
     "This calm and gentle Pok\u00e9mon swims gracefully through the sea. After building speed, it can leap out of the water. It is often misidentified as a bird Pok\u00e9mon due to this behavior.", False),
    (227, "Skarmory", [], ["Steel", "Flying"], 2,
     "People fashion swords from this Pok\u00e9mon\u2019s shed feathers, so this Pok\u00e9mon is a popular element in heraldic designs.", False),
    (228, "Houndour", [], ["Dark", "Fire"], 2,
     "They make repeated eerie howls before dawn to call attention to their pack.", False),
    (229, "Houndoom", [], ["Dark", "Fire"], 2,
     "Identifiable by its eerie howls, people a long time ago thought it was the grim reaper and feared it.", False),
    (230, "Kingdra", [], ["Water", "Dragon"], 2,
     "Scales shed by this Pok\u00e9mon have such a splendorous gleam to them that they\u2019ve been given to royalty as gifts.", False),
    (231, "Phanpy", [], ["Ground"], 2,
     "this Pok\u00e9mon uses its long nose to shower itself. When others gather around, they thoroughly douse each other with water. These Pok\u00e9mon can be seen drying their soaking-wet bodies at the edge of water.", False),
    (232, "Donphan", [], ["Ground"], 2,
     "If this Pok\u00e9mon were to tackle with its hard body, even a house could be destroyed. Using its massive strength, the Pok\u00e9mon helps clear rock and mud slides that block mountain trails.", False),
    (233, "Porygon2", [], ["Normal"], 2,
     "A bizarre item caused this Pok\u00e9mon to evolve. While it now exhibits many new gestures and expressions, its biology remains inscrutable.", False),
    (234, "Stantler", [], ["Normal"], 2,
     "Its strangely shaped antlers have the power to bewitch those who see them. Medicine made by grinding up the black orbs from fallen antlers is an effective treatment for insomnia.", False),
    (235, "Smeargle", [], ["Normal"], 2,
     "It draws symbols with the fluid that oozes from the tip of its tail. Depending on the symbol, this Pok\u00e9mon fanatics will pay big money for them.", False),
    (236, "Tyrogue", [], ["Fighting"], 2,
     "Even though it is small, it can\u2019t be ignored because it will slug any handy target without warning.", False),
    (237, "Hitmontop", [], ["Fighting"], 2,
     "After doing a handstand to throw off the opponent\u2019s timing, it presents its fancy kick moves.", False),
    (238, "Smoochum", [], ["Ice", "Psychic"], 2,
     "This is a very curious Pok\u00e9mon. this Pok\u00e9mon decides what it likes and dislikes by touching things with its lips.", False),
    (239, "Elekid", [], ["Electric"], 2,
     "They generate electricity by spinning their arms. During a thunderstorm, if one hears the lively voices of children out in the wilderness, what one is actually hearing are the voices of this Pok\u00e9mon.", False),
    (240, "Magby", [], ["Fire"], 2,
     "This Pok\u00e9mon lives in volcanic areas. With each breath, sparks spurt from its mouth and nose. When this Pok\u00e9mon is in good health, its flames gain a yellow tint.", False),
    (241, "Miltank", [], ["Normal"], 2,
     "This Pok\u00e9mon needs to be milked every day, or else it will fall ill. The flavor of this Pok\u00e9mon milk changes with the seasons.", False),
    (242, "Blissey", [], ["Normal"], 2,
     "A kindhearted Pok\u00e9mon that will care for any sick person or Pok\u00e9mon until their health improves. The eggs it lays are delicious and bring good fortune to those who eat them.", False),
    (243, "Raikou", [], ["Electric"], 2,
     "this Pok\u00e9mon embodies the speed of lightning. The roars of this Pok\u00e9mon send shock waves shuddering through the air and shake the ground as if lightning bolts had come crashing down.", True),
    (244, "Entei", [], ["Fire"], 2,
     "this Pok\u00e9mon embodies the passion of magma. This Pok\u00e9mon is thought to have been born in the eruption of a volcano. It sends up massive bursts of fire that utterly consume all that they touch.", True),
    (245, "Suicune", [], ["Water"], 2,
     "this Pok\u00e9mon embodies the compassion of a pure spring of water. It runs across the land with gracefulness. This Pok\u00e9mon has the power to purify dirty water.", True),
    (246, "Larvitar", [], ["Rock", "Ground"], 2,
     "It feeds on soil. After it has eaten a large mountain, it will fall asleep so it can grow.", False),
    (247, "Pupitar", [], ["Rock", "Ground"], 2,
     "It will not stay still, even while it\u2019s a pupa. It already has arms and legs under its solid shell.", False),
    (248, "Tyranitar", [], ["Rock", "Dark"], 2,
     "The quakes caused when it walks make even great mountains crumble and change the surrounding terrain.", False),
    (249, "Lugia", [], ["Psychic", "Flying"], 2,
     "this Pok\u00e9mon\u2019s wings pack devastating power\u2014a light fluttering of its wings can blow apart regular houses. As a result, this Pok\u00e9mon chooses to live out of sight deep under the sea.", True),
    (250, "Ho-Oh", [], ["Fire", "Flying"], 2,
     "this Pok\u00e9mon\u2019s feathers glow in seven colors depending on the angle at which they are struck by light. These feathers are said to bring happiness to the bearers. This Pok\u00e9mon is said to live at the foot of a rainbow.", True),
    (251, "Celebi", [], ["Psychic", "Grass"], 2,
     "This Pok\u00e9mon traveled through time to come from the future. It bolsters grass and trees with its own strength, and it can heal wounds, too.", True),
    # ── Gen 3 (Hoenn) ─────────────────────────────────────────────────────
    (252, "Treecko", [], ["Grass"], 3,
     "this Pok\u00e9mon is cool, calm, and collected\u2014it never panics under any situation. If a bigger foe were to glare at this Pok\u00e9mon, it would glare right back without conceding an inch of ground.", False),
    (253, "Grovyle", [], ["Grass"], 3,
     "This Pok\u00e9mon adeptly flies from branch to branch in trees. In a forest, no Pok\u00e9mon can ever hope to catch a fleeing this Pok\u00e9mon however fast they may be.", False),
    (254, "Sceptile", [], ["Grass"], 3,
     "this Pok\u00e9mon has seeds growing on its back. They are said to be bursting with nutrients that revitalize trees. This Pok\u00e9mon raises the trees in a forest with loving care.", False),
    (255, "Torchic", [], ["Fire"], 3,
     "this Pok\u00e9mon has a place inside its body where it keeps its flame. Give it a hug\u2014it will be glowing with warmth. This Pok\u00e9mon is covered all over by a fluffy coat of down.", False),
    (256, "Combusken", [], ["Fire", "Fighting"], 3,
     "this Pok\u00e9mon battles with the intensely hot flames it spews from its beak and with outstandingly destructive kicks. This Pok\u00e9mon\u2019s cry is very loud and distracting.", False),
    (257, "Blaziken", [], ["Fire", "Fighting"], 3,
     "this Pok\u00e9mon has incredibly strong legs\u2014it can easily clear a 30-story building in one leap. This Pok\u00e9mon\u2019s blazing punches leave its foes scorched and blackened.", False),
    (258, "Mudkip", [], ["Water"], 3,
     "In water, this Pok\u00e9mon breathes using the gills on its cheeks. If it is faced with a tight situation in battle, this Pok\u00e9mon will unleash its amazing power\u2014it can crush rocks bigger than itself.", False),
    (259, "Marshtomp", [], ["Water", "Ground"], 3,
     "this Pok\u00e9mon is much faster at traveling through mud than it is at swimming. This Pok\u00e9mon\u2019s hindquarters exhibit obvious development, giving it the ability to walk on just its hind legs.", False),
    (260, "Swampert", [], ["Water", "Ground"], 3,
     "this Pok\u00e9mon predicts storms by sensing subtle differences in the sounds of waves and tidal winds with its fins. If a storm is approaching, it piles up boulders to protect itself.", False),
    (261, "Poochyena", [], ["Dark"], 3,
     "this Pok\u00e9mon is an omnivore\u2014it will eat anything. A distinguishing feature is how large its fangs are compared to its body. This Pok\u00e9mon tries to intimidate its foes by making the hair on its tail bristle out.", False),
    (262, "Mightyena", [], ["Dark"], 3,
     "this Pok\u00e9mon travel and act as a pack in the wild. The memory of its life in the wild compels the Pok\u00e9mon to obey only those Trainers that it recognizes to possess superior skill.", False),
    (263, "Zigzagoon", [], ["Normal"], 3,
     "this Pok\u00e9mon that adapted to regions outside Galar acquired this appearance. If you\u2019ve lost something, this Pok\u00e9mon can likely find it.", False),
    (264, "Linoone", [], ["Normal"], 3,
     "It uses its explosive speed and razor-sharp claws to bring down prey. Running along winding paths is not its strong suit.", False),
    (265, "Wurmple", [], ["Bug"], 3,
     "Likes sap and is abundant in the wild. Why it evolves into various different forms is unknown. One cannot tell from a this Pok\u00e9mon's appearance which form it will take when it evolves.", False),
    (266, "Silcoon", [], ["Bug"], 3,
     "Wraps itself in thin strings of silk while it stores energy for evolution. It can't extend its limbs and its movement is slow, but its eyes keep a sharp lookout\u2014this Pok\u00e9mon is always on guard.", False),
    (267, "Beautifly", [], ["Bug", "Flying"], 3,
     "A colorful and incredibly beautiful but also greedy Pok\u00e9mon. In an effort to keep its favorite food all to itself, it will chase away Combee as they try to gather nectar.", False),
    (268, "Cascoon", [], ["Bug"], 3,
     "The silk coating its body is thin but sufficiently strong. this Pok\u00e9mon's silk has a luster and texture superior to that of Silcoon's, and clothes made using this Pok\u00e9mon silk are regarded as top-notch.", False),
    (269, "Dustox", [], ["Bug", "Poison"], 3,
     "Tends to be drawn to bonfires on dark nights. Difficult to chase away from settlements because of the way it scatters highly toxic scales.", False),
    (270, "Lotad", [], ["Water", "Grass"], 3,
     "Its leaf grew too large for it to live on land. That is how it began to live floating in the water.", False),
    (271, "Lombre", [], ["Water", "Grass"], 3,
     "It lives at the water\u2019s edge where it is sunny. It sleeps on a bed of water grass by day and becomes active at night.", False),
    (272, "Ludicolo", [], ["Water", "Grass"], 3,
     "If it hears festive music, it begins moving in rhythm in order to amplify its power.", False),
    (273, "Seedot", [], ["Grass"], 3,
     "It attaches itself to a tree branch using the top of its head. Strong winds can sometimes make it fall.", False),
    (274, "Nuzleaf", [], ["Grass", "Dark"], 3,
     "They live in holes bored in large trees. The sound of this Pok\u00e9mon\u2019s grass flute fills listeners with dread.", False),
    (275, "Shiftry", [], ["Grass", "Dark"], 3,
     "It lives quietly in the deep forest. It is said to create chilly winter winds with the fans it holds.", False),
    (276, "Taillow", [], ["Normal", "Flying"], 3,
     "this Pok\u00e9mon is young\u2014it has only just left its nest. As a result, it sometimes becomes lonesome and cries at night. This Pok\u00e9mon feeds on Wurmple that live in forests.", False),
    (277, "Swellow", [], ["Normal", "Flying"], 3,
     "this Pok\u00e9mon is very conscientious about the upkeep of its glossy wings. Once two this Pok\u00e9mon are gathered, they diligently take care of cleaning each other\u2019s wings.", False),
    (278, "Wingull", [], ["Water", "Flying"], 3,
     "It soars on updrafts without flapping its wings. It makes a nest on sheer cliffs at the sea\u2019s edge.", False),
    (279, "Pelipper", [], ["Water", "Flying"], 3,
     "Skimming the water\u2019s surface, it dips its large bill in the sea, scoops up food and water, and carries it.", False),
    (280, "Ralts", [], ["Psychic", "Fairy"], 3,
     "Tends to prefer people with a chipper disposition to those who are gloomy, but it has shown no discrimination with regard to age or gender. Needs more research.", False),
    (281, "Kirlia", [], ["Psychic", "Fairy"], 3,
     "It resembles a maiden in appearance, but it wields strange powers to project visions of paradise. I suspect the crimson ornaments on its head are the key to its abilities.", False),
    (282, "Gardevoir", [], ["Psychic", "Fairy"], 3,
     "It will dedicate itself to defending a master it has come to adore. Its pure white dress, reminiscent of those worn by ladies of nobility, is the dress of one who is willing to risk their life.", False),
    (283, "Surskit", [], ["Bug", "Water"], 3,
     "It lives in ponds and marshes that feature lots of plant life. It often fights with Dewpider, whose habitat and diet are similar.", False),
    (284, "Masquerain", [], ["Bug", "Flying"], 3,
     "Its thin, winglike antennae are highly absorbent. It waits out rainy days in tree hollows.", False),
    (285, "Shroomish", [], ["Grass"], 3,
     "If this Pok\u00e9mon senses danger, it shakes its body and scatters spores from the top of its head. This Pok\u00e9mon\u2019s spores are so toxic, they make trees and weeds wilt.", False),
    (286, "Breloom", [], ["Grass", "Fighting"], 3,
     "The seeds ringing this Pok\u00e9mon\u2019s tail are made of hardened toxic spores. It is horrible to eat the seeds. Just taking a bite of this Pok\u00e9mon\u2019s seed will cause your stomach to rumble.", False),
    (287, "Slakoth", [], ["Normal"], 3,
     "this Pok\u00e9mon\u2019s heart beats just once a minute. Whatever happens, it is content to loaf around motionless. It is rare to see this Pok\u00e9mon in motion.", False),
    (288, "Vigoroth", [], ["Normal"], 3,
     "this Pok\u00e9mon is simply incapable of remaining still. Even when it tries to sleep, the blood in its veins grows agitated, compelling this Pok\u00e9mon to run wild throughout the jungle before it can settle down.", False),
    (289, "Slaking", [], ["Normal"], 3,
     "Wherever this Pok\u00e9mon live, rings of over a yard in diameter appear in grassy fields. They are made by the Pok\u00e9mon as it eats all the grass within reach while lying prone on the ground.", False),
    (290, "Nincada", [], ["Bug", "Ground"], 3,
     "It can sometimes live underground for more than 10 years. It absorbs nutrients from the roots of trees.", False),
    (291, "Ninjask", [], ["Bug", "Flying"], 3,
     "This Pok\u00e9mon is so quick, it is said to be able to avoid any attack. It loves to feed on tree sap.", False),
    (292, "Shedinja", [], ["Bug", "Ghost"], 3,
     "A strange Pok\u00e9mon\u2014it flies without moving its wings, has a hollow shell for a body, and does not breathe.", False),
    (293, "Whismur", [], ["Normal"], 3,
     "When this Pok\u00e9mon cries, the sound of its own voice startles it, making the Pok\u00e9mon cry even louder. It cries until it\u2019s exhausted, then it falls asleep.", False),
    (294, "Loudred", [], ["Normal"], 3,
     "The force of this Pok\u00e9mon\u2019s loud voice isn\u2019t just the sound\u2014it\u2019s also the wave of air pressure that blows opponents away and damages them.", False),
    (295, "Exploud", [], ["Normal"], 3,
     "This Pok\u00e9mon can do more than just shout. To communicate with others of its kind, it\u2019ll emit all sorts of sounds from the holes in its body.", False),
    (296, "Makuhita", [], ["Fighting"], 3,
     "There\u2019s a rumor of a traditional recipe for stew that Trainers can use to raise strong this Pok\u00e9mon.", False),
    (297, "Hariyama", [], ["Fighting"], 3,
     "this Pok\u00e9mon that are big and fat aren\u2019t necessarily strong. There are some small ones that move nimbly and use moves skillfully.", False),
    (298, "Azurill", [], ["Normal", "Fairy"], 3,
     "Although this Pok\u00e9mon are normally docile, an angry one will swing around the big ball on its tail and try to smash its opponents.", False),
    (299, "Nosepass", [], ["Rock"], 3,
     "Once the people of Hisui discovered that its red nose always points north, they grew to rely on it greatly when traveling afar. The nose seems to work in a similar way to ancient compasses.", False),
    (300, "Skitty", [], ["Normal"], 3,
     "this Pok\u00e9mon is known to chase around playfully after its own tail. In the wild, this Pok\u00e9mon lives in holes in the trees of forests. It is very popular as a pet because of its adorable looks.", False),
    (301, "Delcatty", [], ["Normal"], 3,
     "this Pok\u00e9mon sleeps anywhere it wants without keeping a permanent nest. If other Pok\u00e9mon approach it as it sleeps, this Pok\u00e9mon will never fight\u2014it will just move away somewhere else.", False),
    (302, "Sableye", [], ["Dark", "Ghost"], 3,
     "It feeds on gemstone crystals. In darkness, its eyes sparkle with the glitter of jewels.", False),
    (303, "Mawile", [], ["Steel", "Fairy"], 3,
     "It chomps with its gaping mouth. Its huge jaws are actually steel horns that have been transformed.", False),
    (304, "Aron", [], ["Steel", "Rock"], 3,
     "When this Pok\u00e9mon evolves, its steel armor peels off. In ancient times, people would collect this Pok\u00e9mon\u2019s shed armor and make good use of it in their daily lives.", False),
    (305, "Lairon", [], ["Steel", "Rock"], 3,
     "During territorial disputes, this Pok\u00e9mon fight by slamming into each other. Close inspection of their steel armor reveals scratches and dents.", False),
    (306, "Aggron", [], ["Steel", "Rock"], 3,
     "Long ago, there was a king who wore a helmet meant to resemble the head of an this Pok\u00e9mon. He was trying to channel the Pok\u00e9mon\u2019s strength.", False),
    (307, "Meditite", [], ["Fighting", "Psychic"], 3,
     "this Pok\u00e9mon heightens its inner energy through meditation. It survives on just one berry a day. Minimal eating is another aspect of this Pok\u00e9mon\u2019s training.", False),
    (308, "Medicham", [], ["Fighting", "Psychic"], 3,
     "Through the power of meditation, this Pok\u00e9mon developed its sixth sense. It gained the ability to use psychokinetic powers. This Pok\u00e9mon is known to meditate for a whole month without eating.", False),
    (309, "Electrike", [], ["Electric"], 3,
     "It stores electricity in its fur. It gives off sparks from all over its body in seasons when the air is dry.", False),
    (310, "Manectric", [], ["Electric"], 3,
     "It rarely appears before people. It is said to nest where lightning has fallen.", False),
    (311, "Plusle", [], ["Electric"], 3,
     "When this Pok\u00e9mon is cheering on its partner, it flashes with electric sparks from all over its body. If its partner loses, this Pok\u00e9mon cries loudly.", False),
    (312, "Minun", [], ["Electric"], 3,
     "this Pok\u00e9mon loves to cheer on its partner in battle. It gives off sparks from its body while it is doing so. If its partner is in trouble, this Pok\u00e9mon gives off increasing amounts of sparks.", False),
    (313, "Volbeat", [], ["Bug"], 3,
     "this Pok\u00e9mon\u2019s tail glows like a lightbulb. With other this Pok\u00e9mon, it uses its tail to draw geometric shapes in the night sky. This Pok\u00e9mon loves the sweet aroma given off by Illumise.", False),
    (314, "Illumise", [], ["Bug"], 3,
     "this Pok\u00e9mon leads a flight of illuminated Volbeat to draw signs in the night sky. This Pok\u00e9mon is said to earn greater respect from its peers by composing more complex designs in the sky.", False),
    (315, "Roselia", [], ["Grass", "Poison"], 3,
     "Though beautiful, it has highly poisonous thorns. There is an old tradition in my homeland wherein one would send these thorns to an opponent to challenge them to a duel.", False),
    (316, "Gulpin", [], ["Poison"], 3,
     "Most of this Pok\u00e9mon\u2019s body is made up of its stomach\u2014its heart and brain are very small in comparison. This Pok\u00e9mon\u2019s stomach contains special enzymes that dissolve anything.", False),
    (317, "Swalot", [], ["Poison"], 3,
     "this Pok\u00e9mon has no teeth, so what it eats, it swallows whole, no matter what. Its cavernous mouth yawns widely. An automobile tire could easily fit inside this Pok\u00e9mon\u2019s mouth.", False),
    (318, "Carvanha", [], ["Water", "Dark"], 3,
     "These Pok\u00e9mon have sharp fangs and powerful jaws. Sailors avoid this Pok\u00e9mon dens at all costs.", False),
    (319, "Sharpedo", [], ["Water", "Dark"], 3,
     "This Pok\u00e9mon is known as the Bully of the Sea. Any ship entering the waters this Pok\u00e9mon calls home will be attacked\u2014no exceptions.", False),
    (320, "Wailmer", [], ["Water"], 3,
     "When it sucks in a large volume of seawater, it becomes like a big, bouncy ball. It eats a ton of food daily.", False),
    (321, "Wailord", [], ["Water"], 3,
     "Its immense size is the reason for its popularity. this Pok\u00e9mon watching is a favorite sightseeing activity in various parts of the world.", False),
    (322, "Numel", [], ["Fire", "Ground"], 3,
     "this Pok\u00e9mon stores magma of almost 2,200 degrees Fahrenheit within its body. If it gets wet, the magma cools and hardens. In that event, the Pok\u00e9mon\u2019s body grows heavy and its movements become sluggish.", False),
    (323, "Camerupt", [], ["Fire", "Ground"], 3,
     "The humps on this Pok\u00e9mon\u2019s back are formed by a transformation of its bones. They sometimes blast out molten magma. This Pok\u00e9mon apparently erupts often when it is enraged.", False),
    (324, "Torkoal", [], ["Fire"], 3,
     "You find abandoned coal mines full of them. They dig tirelessly in search of coal.", False),
    (325, "Spoink", [], ["Psychic"], 3,
     "this Pok\u00e9mon keeps a pearl on top of its head. The pearl functions to amplify this Pok\u00e9mon\u2019s psychokinetic powers. It is therefore on a constant search for a bigger pearl.", False),
    (326, "Grumpig", [], ["Psychic"], 3,
     "this Pok\u00e9mon uses the black pearls on its body to wield its fantastic powers. When it is doing so, it dances bizarrely. This Pok\u00e9mon\u2019s black pearls are valuable as works of art.", False),
    (327, "Spinda", [], ["Normal"], 3,
     "Each this Pok\u00e9mon\u2019s spot pattern is different. With its stumbling movements, it evades opponents\u2019 attacks brilliantly!", False),
    (328, "Trapinch", [], ["Ground"], 3,
     "It makes an inescapable conical pit and lies in wait at the bottom for prey to come tumbling down.", False),
    (329, "Vibrava", [], ["Ground", "Dragon"], 3,
     "To help make its wings grow, it dissolves quantities of prey in its digestive juices and guzzles them down every day.", False),
    (330, "Flygon", [], ["Ground", "Dragon"], 3,
     "It is nicknamed the Desert Spirit because the flapping of its wings sounds like a woman singing.", False),
    (331, "Cacnea", [], ["Grass"], 3,
     "The more arid and harsh the environment, the more pretty and fragrant a flower this Pok\u00e9mon grows. This Pok\u00e9mon battles by wildly swinging its thorny arms.", False),
    (332, "Cacturne", [], ["Grass", "Dark"], 3,
     "If a traveler is going through a desert in the thick of night, this Pok\u00e9mon will follow in a ragtag group. The Pok\u00e9mon are biding their time, waiting for the traveler to tire and become incapable of moving.", False),
    (333, "Swablu", [], ["Normal", "Flying"], 3,
     "Since this Pok\u00e9mon looks like a cumulus cloud, foes can have a hard time finding it. Apparently its wings turned white over many generations.", False),
    (334, "Altaria", [], ["Dragon", "Flying"], 3,
     "This Pok\u00e9mon has a kind disposition, but if it\u2019s provoked, it will threaten opponents with shrill cries before attacking them without mercy.", False),
    (335, "Zangoose", [], ["Normal"], 3,
     "this Pok\u00e9mon usually stays on all fours, but when angered, it gets up on its hind legs and extends its claws. This Pok\u00e9mon shares a bitter rivalry with Seviper that dates back over generations.", False),
    (336, "Seviper", [], ["Poison"], 3,
     "this Pok\u00e9mon\u2019s swordlike tail serves two purposes\u2014it slashes foes and douses them with secreted poison. This Pok\u00e9mon will not give up its long-running blood feud with Zangoose.", False),
    (337, "Lunatone", [], ["Rock", "Psychic"], 3,
     "It was discovered at the site of a meteor strike 40 years ago. Its stare can lull its foes to sleep.", False),
    (338, "Solrock", [], ["Rock", "Psychic"], 3,
     "Solar energy is the source of its power, so it is strong during the daytime. When it spins, its body shines.", False),
    (339, "Barboach", [], ["Water", "Ground"], 3,
     "Makes its home in swamps with murky water. The poor visibility hides this Pok\u00e9mon from predators, and the slime on its body makes grasping it difficult.", False),
    (340, "Whiscash", [], ["Water", "Ground"], 3,
     "Strikes its caudal fin against the swamp bed to shake the ground and startle its prey. It will then swallow the fleeing prey whole. People mistook this behavior as the cause of earthquakes.", False),
    (341, "Corphish", [], ["Water"], 3,
     "It was originally a Pok\u00e9mon from afar that escaped to the wild. It can adapt to the dirtiest river.", False),
    (342, "Crawdaunt", [], ["Water", "Dark"], 3,
     "A brutish Pok\u00e9mon that loves to battle. It will crash itself into any foe that approaches its nest.", False),
    (343, "Baltoy", [], ["Ground", "Psychic"], 3,
     "It was discovered in ancient ruins. While moving, it constantly spins. It stands on one foot even when asleep.", False),
    (344, "Claydol", [], ["Ground", "Psychic"], 3,
     "It appears to have been born from clay dolls made by ancient people. It uses telekinesis to float and move.", False),
    (345, "Lileep", [], ["Rock", "Grass"], 3,
     "this Pok\u00e9mon clings to rocks on the seabed. When prey comes close, this Pok\u00e9mon entangles it with petallike tentacles.", False),
    (346, "Cradily", [], ["Rock", "Grass"], 3,
     "Once this Pok\u00e9mon catches prey in its tentacles, it digests them whole and absorbs their nutrients.", False),
    (347, "Anorith", [], ["Rock", "Bug"], 3,
     "this Pok\u00e9mon can swim swiftly by pulling its eight wings through the water like oars on a boat. This Pok\u00e9mon is an ancestor of modern bug Pok\u00e9mon.", False),
    (348, "Armaldo", [], ["Rock", "Bug"], 3,
     "Though it lives on land, it\u2019s also a good swimmer. It dives into the ocean in search of prey, using its sharp claws to take down its quarry.", False),
    (349, "Feebas", [], ["Water"], 3,
     "It is a shabby and ugly Pok\u00e9mon. However, it is very hardy and can survive on little water.", False),
    (350, "Milotic", [], ["Water"], 3,
     "It\u2019s said that a glimpse of a this Pok\u00e9mon and its beauty will calm any hostile emotions you\u2019re feeling.", False),
    (351, "Castform", [], ["Normal"], 3,
     "Its form changes depending on the weather. The rougher conditions get, the rougher this Pok\u00e9mon\u2019s disposition!", False),
    (352, "Kecleon", [], ["Normal"], 3,
     "Its color changes for concealment and also when its mood or health changes. The darker the color, the healthier it is.", False),
    (353, "Shuppet", [], ["Ghost"], 3,
     "There\u2019s a proverb that says, \u201cShun the house where this Pok\u00e9mon gather in the growing dusk.\u201d", False),
    (354, "Banette", [], ["Ghost"], 3,
     "Resentment at being cast off made it spring into being. Some say that treating it well will satisfy it, and it will once more become a stuffed toy.", False),
    (355, "Duskull", [], ["Ghost"], 3,
     "I've heard that the children of Hisui all begin to behave once they've been told the story of how this Pok\u00e9mon roams about before the witching hour to spirit away misbehaving children.", False),
    (356, "Dusclops", [], ["Ghost"], 3,
     "There are rumors that peeking inside its bandage-wrapped body will cause one to get pulled in through the gaps between the bandages, never to return. I've been too scared to verify.", False),
    (357, "Tropius", [], ["Grass", "Flying"], 3,
     "Bunches of delicious fruit grow around its neck. In warm areas, many ranches raise this Pok\u00e9mon.", False),
    (358, "Chimecho", [], ["Psychic"], 3,
     "Can emit waves of air powerful enough to knock out prey taller than itself. I hypothesize that it amplifies the faint sound of wind within its body.", False),
    (359, "Absol", [], ["Dark"], 3,
     "Because of this Pok\u00e9mon\u2019s ability to detect danger, people mistook this Pok\u00e9mon as a bringer of doom.", False),
    (360, "Wynaut", [], ["Psychic"], 3,
     "It tends to move in a pack with others. They cluster in a tight group to sleep in a cave.", False),
    (361, "Snorunt", [], ["Ice"], 3,
     "Arrives alongside the first snow. It's thought that homes this Pok\u00e9mon visit will prosper for many generations. By tradition, one might offer a lump of ice made from pure water at one's front door.", False),
    (362, "Glalie", [], ["Ice"], 3,
     "It covers its body with an armor of ice harder than steel. Uses its breath to freeze prey, which it then devours as if they were frozen desserts.", False),
    (363, "Spheal", [], ["Ice", "Water"], 3,
     "During the season when drift ice approaches the shore, this Pok\u00e9mon prefers living on the ice\u2014where fewer predators lurk\u2014rather than the land. Its fur retains heat superbly and resists harsh cold.", False),
    (364, "Sealeo", [], ["Ice", "Water"], 3,
     "Its white whiskers are very sensitive. this Pok\u00e9mon will balance Spheal on the tip of its nose, checking its scent and its feel to be sure the Spheal is healthy.", False),
    (365, "Walrein", [], ["Ice", "Water"], 3,
     "Its thick tusks are strong enough to shatter drift ice. They have been known to break, but they will grow back by the next year. The Hisui region is well known for these broken tusks.", False),
    (366, "Clamperl", [], ["Water"], 3,
     "this Pok\u00e9mon\u2019s pearls are exceedingly precious. They can be more than 10 times as costly as Shellder\u2019s pearls.", False),
    (367, "Huntail", [], ["Water"], 3,
     "Deep seas are their habitat. According to tradition, when this Pok\u00e9mon wash up onshore, something unfortunate will happen.", False),
    (368, "Gorebyss", [], ["Water"], 3,
     "It sucks bodily fluids out of its prey. The leftover meat sinks to the seafloor, where it becomes food for other Pok\u00e9mon.", False),
    (369, "Relicanth", [], ["Water", "Rock"], 3,
     "This Pok\u00e9mon was discovered during deep-sea exploration. Its appearance hasn\u2019t changed in 100,000,000 years, so it\u2019s called a living fossil.", False),
    (370, "Luvdisc", [], ["Water"], 3,
     "this Pok\u00e9mon makes its home in coral reefs in warm seas. It especially likes sleeping in the space between Corsola\u2019s branches.", False),
    (371, "Bagon", [], ["Dragon"], 3,
     "this Pok\u00e9mon is a solitary Pok\u00e9mon that doesn\u2019t form groups with others of its kind. It also has a head hard enough to cleave a boulder in one strike.", False),
    (372, "Shelgon", [], ["Dragon"], 3,
     "this Pok\u00e9mon ignores its hunger entirely, never eating any food. Apparently, this Pok\u00e9mon will evolve once all its energy stores are used up.", False),
    (373, "Salamence", [], ["Dragon", "Flying"], 3,
     "While basking in the joy of flight generally keeps this Pok\u00e9mon in high spirits, this Pok\u00e9mon turns into an uncontrollable menace if something angers it.", False),
    (374, "Beldum", [], ["Steel", "Psychic"], 3,
     "The cells in this Pok\u00e9mon\u2019s body are composed of magnetic material. Instead of blood, magnetic forces flow through this Pok\u00e9mon\u2019s body.", False),
    (375, "Metang", [], ["Steel", "Psychic"], 3,
     "Using magnetic forces to stay aloft, this Pok\u00e9mon flies at high speeds, weaving through harsh mountain terrain in pursuit of prey.", False),
    (376, "Metagross", [], ["Steel", "Psychic"], 3,
     "this Pok\u00e9mon is the result of the fusion of two Metang. This Pok\u00e9mon defeats its opponents through use of its supercomputer-level brain.", False),
    (377, "Regirock", [], ["Rock"], 3,
     "Cutting-edge technology was used to study the internals of this Pok\u00e9mon\u2019s rock body, but nothing was found\u2014not even a brain or a heart.", True),
    (378, "Regice", [], ["Ice"], 3,
     "This Pok\u00e9mon\u2019s body is made of solid ice. It\u2019s said that this Pok\u00e9mon was born beneath thick ice in the ice age.", True),
    (379, "Registeel", [], ["Steel"], 3,
     "It\u2019s rumored that this Pok\u00e9mon was born deep underground in the planet\u2019s mantle and that it emerged onto the surface 10,000 years ago.", True),
    (380, "Latias", [], ["Dragon", "Psychic"], 3,
     "this Pok\u00e9mon is highly intelligent and capable of understanding human speech. It is covered with a glass-like down. The Pok\u00e9mon enfolds its body with its down and refracts light to alter its appearance.", True),
    (381, "Latios", [], ["Dragon", "Psychic"], 3,
     "this Pok\u00e9mon will only open its heart to a Trainer with a compassionate spirit. This Pok\u00e9mon can fly faster than a jet plane by folding its forelegs to minimize air resistance.", True),
    (382, "Kyogre", [], ["Water"], 3,
     "this Pok\u00e9mon is said to be the personification of the sea itself. Legends tell of its many clashes against Groudon, as each sought to gain the power of nature.", True),
    (383, "Groudon", [], ["Ground"], 3,
     "Through Primal Reversion and with nature\u2019s full power, it will take back its true form. It can cause magma to erupt and expand the landmass of the world.", True),
    (384, "Rayquaza", [], ["Dragon", "Flying"], 3,
     "It flies forever through the ozone layer, consuming meteoroids for sustenance. The many meteoroids in its body provide the energy it needs to Mega Evolve.", True),
    (385, "Jirachi", [], ["Steel", "Psychic"], 3,
     "It\u2019s believed that when this Pok\u00e9mon wakes from its 1,000-year slumber, it will grant any wishes written on the notes attached to its head.", True),
    (386, "Deoxys", [], ["Psychic"], 3,
     "this Pok\u00e9mon emerged from a virus that came from space. It is highly intelligent and wields psychokinetic powers. This Pok\u00e9mon shoots lasers from the crystalline organ on its chest.", True),
    # ── Gen 4 (Sinnoh) ────────────────────────────────────────────────────
    (387, "Turtwig", [], ["Grass"], 4,
     "This Pok\u00e9mon becomes more energetic the more sunlight there is. The part resembling a shell is similar to silt and is slightly damp and warm to the touch.", False),
    (388, "Grotle", [], ["Grass"], 4,
     "Appears where there is clean spring water. The fruit that grows on the shrubs on its shell is sweet, nutritious, and truly delicious.", False),
    (389, "Torterra", [], ["Grass", "Ground"], 4,
     "This remarkable, large-bodied Pok\u00e9mon would serve beautifully as borrowed scenery for a garden, and its strength is peerless. this Pok\u00e9mon roams the wilderness in search of clean water.", False),
    (390, "Chimchar", [], ["Fire"], 4,
     "Full of vigor and always in high spirits. It was once known by the name \u201dLantern-Tail\u201d and feared as some kind of apparition.", False),
    (391, "Monferno", [], ["Fire", "Fighting"], 4,
     "The deeper the blue on its face, the more powerful it will grow to become. It leaps about every which way and lands powerful blows against its opponents with the flame on its tail.", False),
    (392, "Infernape", [], ["Fire", "Fighting"], 4,
     "A tall, hardy Pok\u00e9mon with a dazzling appearance. It shrouds itself in flame and battles as if engaged in dance\u2014truly a sight to behold.", False),
    (393, "Piplup", [], ["Water"], 4,
     "Prefers cold climes and appears along coasts. It's an adorable little thing\u2014as cute as any child\u2014but it's also prideful, unwilling to accept handouts from people.", False),
    (394, "Prinplup", [], ["Water"], 4,
     "It swims gracefully through the frigid sea and sings with a voice like the roaring tide. It has powerful, sturdy wings and dignity to match.", False),
    (395, "Empoleon", [], ["Water", "Steel"], 4,
     "Since ancient times, it has been revered by the people of Hisui, who call it the Master of the Waves. Its wings are a match for even master-crafted blades.", False),
    (396, "Starly", [], ["Normal", "Flying"], 4,
     "They live in the fields and mountains, gathering in large flocks. Their cries are quite obnoxious. Though small, their wings are strong\u2014a strike from them leaves pain that persists for a week.", False),
    (397, "Staravia", [], ["Normal", "Flying"], 4,
     "They form remarkably large flocks and are constantly fighting amongst themselves. I suspect that those with magnificent plumes on their heads are the strong ones.", False),
    (398, "Staraptor", [], ["Normal", "Flying"], 4,
     "It has left the flock, having gained strength enough to survive on its own. The astounding force with which this Pok\u00e9mon flies through the air allows it to carry away large, burly targets.", False),
    (399, "Bidoof", [], ["Normal"], 4,
     "this Pok\u00e9mon has an unsophisticated face and is rarely flustered by anything. There have been incidents involving this Pok\u00e9mon sauntering into villages and gnawing on the houses without a single care.", False),
    (400, "Bibarel", [], ["Normal", "Water"], 4,
     "this Pok\u00e9mon fur repels water and is also a fantastic material for heat retention. These Pok\u00e9mon create dams on rivers to live in.", False),
    (401, "Kricketot", [], ["Bug"], 4,
     "When the trees take on new hues, more of these Pok\u00e9mon appear. The tone they create by striking their antennae together resembles that of the marimba, an instrument of foreign lands.", False),
    (402, "Kricketune", [], ["Bug"], 4,
     "It uses its cutlass-like arms to produce sound, the melody of which varies from individual to individual. It is a worthwhile endeavor to seek out one's favorite tunes.", False),
    (403, "Shinx", [], ["Electric"], 4,
     "Shakes its body to generate electricity. Its stature belies its aggression\u2014one must be patient to tame this Pok\u00e9mon.", False),
    (404, "Luxio", [], ["Electric"], 4,
     "Proudly uses its electrified claws as weapons. It seems to be a gracious Pok\u00e9mon, evenly sharing the spoils of the hunt with others of its kind.", False),
    (405, "Luxray", [], ["Electric"], 4,
     "They form packs, each having one male as leader. Legends say that when this Pok\u00e9mon's two eyes shimmer with gold, the Pok\u00e9mon can see through anything.", False),
    (406, "Budew", [], ["Grass", "Poison"], 4,
     "When the sun's light strengthens, the bud atop this Pok\u00e9mon's head opens. This is a sign to the people that the harsh winter is over, and the season of budding has begun.", False),
    (407, "Roserade", [], ["Grass", "Poison"], 4,
     "Hidden within the bouquet on each hand are thorned whips loaded with virulent poison. this Pok\u00e9mon moves gracefully as it corners its prey and mercilessly lashes them with its whips.", False),
    (408, "Cranidos", [], ["Rock"], 4,
     "An incredibly rare sight. They duel each other by ramming their heads together, and the resulting sound echoes throughout the area like the pealing of a bell.", False),
    (409, "Rampardos", [], ["Rock"], 4,
     "Very little is known about its biology. Can knock down massive trees by smashing its beautiful, pearl-like crown against them.", False),
    (410, "Shieldon", [], ["Rock", "Steel"], 4,
     "Much remains unknown about this Pok\u00e9mon, as few have ever seen it. However, we know that it is calm and dislikes conflict, and it enjoys polishing its face against trees and rocks.", False),
    (411, "Bastiodon", [], ["Rock", "Steel"], 4,
     "Its face is sturdy\u2014as strong as diamond\u2014and this hardness offers a very stable defense. Much about this species is still unknown, such as its natural habitat.", False),
    (412, "Burmy", [], ["Bug"], 4,
     "If its cloak is even slightly damaged, this Pok\u00e9mon will immediately repair it with whatever is close at hand. The Pok\u00e9mon within the cloak is scrawny and vulnerable to the cold.", False),
    (413, "Wormadam", [], ["Bug", "Grass"], 4,
     "When Burmy evolved, its cloak became a part of this Pok\u00e9mon\u2019s body. The cloak is never shed.", False),
    (414, "Mothim", [], ["Bug", "Flying"], 4,
     "Scatters steel-colored scales as it flaps its wings. Despite being observed feeding primarily on the nectar of flowers, this Pok\u00e9mon is not often seen around flower gardens.", False),
    (415, "Combee", [], ["Bug", "Flying"], 4,
     "They swear fealty to a queen Pok\u00e9mon and work diligently to gather nectar. Each swarm produces a different flavor of honey.", False),
    (416, "Vespiquen", [], ["Bug", "Flying"], 4,
     "Commands its subjects to build its hive. It will dispatch any interlopers who dare sneak into its nest and use them as nourishment for itself.", False),
    (417, "Pachirisu", [], ["Electric"], 4,
     "A species related to the Pikachu line. Though this Pok\u00e9mon is a calm Pok\u00e9mon, it still presents a danger should one touch its electrified tail or cheeks.", False),
    (418, "Buizel", [], ["Water"], 4,
     "It moves freely in the water by spinning its forked tail for propulsion. The resemblance to the screw of a steamboat is coincidental.", False),
    (419, "Floatzel", [], ["Water"], 4,
     "Has a long, rather splendid flotation sac, which prevents this Pok\u00e9mon from drowning even in stormy seas. One might glimpse this species around fishing hamlets from time to time.", False),
    (420, "Cherubi", [], ["Grass"], 4,
     "Once the fruit growing alongside the main body is large and plump, this Pok\u00e9mon will use the nutrients within to evolve. The fruit then detaches, becoming nourishment for other creatures.", False),
    (421, "Cherrim", [], ["Grass"], 4,
     "Motionless, save for the occasional quiver. A rich array of Pok\u00e9mon can be found gathered around it, drawn by the scent exuded from this Pok\u00e9mon's folded petals.", False),
    (422, "Shellos", [], ["Water"], 4,
     "Found in abundance on seashores bordering warm waters. this Pok\u00e9mon are unexpectedly friendly and will crawl toward any person they see. Take care not to get coated in mucus!", False),
    (423, "Gastrodon", [], ["Water", "Ground"], 4,
     "Eats beach sand for nourishment. Should one this Pok\u00e9mon encounter another of a different color, a fierce battle will inevitably ensue.", False),
    (424, "Ambipom", [], ["Normal"], 4,
     "To affirm their kinship, members of this species will form a ring by linking their newly doubled tails together. On rare occasions, humans have been accepted into such rings.", False),
    (425, "Drifloon", [], ["Ghost", "Flying"], 4,
     "Said to lure away young children and carry them off to the afterlife. Some whisper that this Pok\u00e9mon are formed of reincarnated human souls, but these rumors are as yet unconfirmed.", False),
    (426, "Drifblim", [], ["Ghost", "Flying"], 4,
     "It drifts along at dusk, perfectly silent. Its transient, melancholy aspect touches some people deeply\u2014every so often, one will come upon a song or poem devoted to this Pok\u00e9mon.", False),
    (427, "Buneary", [], ["Normal"], 4,
     "My hypothesis as to why this Pok\u00e9mon rolls up its ears is that its hearing is far too keen. I surmise that the Pok\u00e9mon protects its hearing by limiting the sound that may enter its ears.", False),
    (428, "Lopunny", [], ["Normal"], 4,
     "Its fur is warm and yet remarkably light. This Pok\u00e9mon kicks as though it were a master of karate, driving back its opponents with ease.", False),
    (429, "Mismagius", [], ["Ghost"], 4,
     "The incantations this Pok\u00e9mon chants can ward against misfortune, so a custom exists of inviting it into one's home. Incur the Pok\u00e9mon's displeasure, however, and disaster will surely ensue.", False),
    (430, "Honchkrow", [], ["Dark", "Flying"], 4,
     "One cry from this Pok\u00e9mon, and a murder of Murkrow come flying. At such times, one would think the curtain of night had fallen, plunging the world into jet-black darkness.", False),
    (431, "Glameow", [], ["Normal"], 4,
     "Bewitches humans with its helical tail and piercing gaze. Its hidden claws are quite sharp as well, making this Pok\u00e9mon an exceedingly tricky opponent if antagonized.", False),
    (432, "Purugly", [], ["Normal"], 4,
     "Though impudent and difficult to tame, this Pok\u00e9mon enjoys great popularity due to its fur, the beauty of which surpasses even velveteen.", False),
    (433, "Chingling", [], ["Psychic"], 4,
     "This Pok\u00e9mon gave me an excruciating headache when it seemingly cried out without making a sound. Perhaps there are some sounds that the human ear is simply incapable of hearing.", False),
    (434, "Stunky", [], ["Poison", "Dark"], 4,
     "The poison that gushes from its aft end is accompanied by an utterly evil-smelling odor with such potency that one whiff can induce memory loss.", False),
    (435, "Skuntank", [], ["Poison", "Dark"], 4,
     "Sprays a poisonous fluid to take down prey. Sometimes, unable to stomach the stench of its own fluid, it leaves the bested prey uneaten.", False),
    (436, "Bronzor", [], ["Steel", "Psychic"], 4,
     "Floats using a mysterious energy. The pattern engraved upon its back is held as sacred and can sometimes be found in imagery from ancient cemeteries and other such timeworn places.", False),
    (437, "Bronzong", [], ["Steel", "Psychic"], 4,
     "Some believe that its bell-like cry opens holes to another world. It has been revered as a deity since ancient times.", False),
    (438, "Bonsly", [], ["Rock"], 4,
     "Its tears elicit sympathy from those who see them, but do not be deceived! This expulsion of body water is merely a physiological mechanism for keeping itself in good health.", False),
    (439, "Mime Jr.", [], ["Psychic", "Fairy"], 4,
     "Known to turn up in bustling marketplaces now and again. It mimics people much as a child would, then watches how they react, eyes sparkling.", False),
    (440, "Happiny", [], ["Normal"], 4,
     "In imitation of Chansey, it keeps a round stone tucked into its belly pouch and cherishes it dearly. It gets along well with children and will sometimes play house with them for fun.", False),
    (441, "Chatot", [], ["Normal", "Flying"], 4,
     "A versatile performer skilled in the imitation of human speech. It is said that older, more experienced this Pok\u00e9mon can even understand the meaning of the words they mimic.", False),
    (442, "Spiritomb", [], ["Ghost", "Dark"], 4,
     "It lays curses by thinking wicked thoughts. Writings tell that this Pok\u00e9mon was born out of the assembly of five score and eight malevolent spirits.", False),
    (443, "Gible", [], ["Dragon", "Ground"], 4,
     "It nests in caves untouched by sunlight. Its sharp teeth may fall out when worn away or after an impact, but they regrow within a few days.", False),
    (444, "Gabite", [], ["Dragon", "Ground"], 4,
     "Though this Pok\u00e9mon are usually of a violent disposition, when I gave one a glass bead it had been eyeing covetously, it suddenly became quite docile.", False),
    (445, "Garchomp", [], ["Dragon", "Ground"], 4,
     "Soars across the heavens at blinding speed\u2014a magnificent sight! It has a feral disposition. Utmost caution is required if one meets a this Pok\u00e9mon out in the wilds.", False),
    (446, "Munchlax", [], ["Normal"], 4,
     "Its robust stomach allows it to nonchalantly devour even rotted matter. It pays frequent visits to villages, seeking out food scraps intended for compost.", False),
    (447, "Riolu", [], ["Fighting"], 4,
     "Though infantile in appearance, it has the mysterious ability to read the minds of humans. The pure of heart are met with this Pok\u00e9mon's approval, while those of ill nature earn only its loathing.", False),
    (448, "Lucario", [], ["Fighting", "Steel"], 4,
     "A most gallant-looking creature. It emits energy waves and controls them with precision, using them to sense even faraway beings. I have given the name \u201daura\u201d to this power.", False),
    (449, "Hippopotas", [], ["Ground"], 4,
     "Though large and languid, this Pok\u00e9mon is difficult to detect due to its tendency to burrow into and lurk beneath the soil. When agitated or excited, it expels sand from its nostrils.", False),
    (450, "Hippowdon", [], ["Ground"], 4,
     "Short-tempered and easily moved to violence. It whips up whirlwinds of sand to crush its foes' spirits, then goes in for the attack.", False),
    (451, "Skorupi", [], ["Poison", "Bug"], 4,
     "Its claws are not only razor-sharp but poisonous, making this Pok\u00e9mon a highly dangerous Pok\u00e9mon. It seems to be weakened by cold temperatures, however.", False),
    (452, "Drapion", [], ["Poison", "Dark"], 4,
     "Has a brutish, ferocious temperament. With immense strength and a sturdy shell off which swords will bounce, it rampages about and wreaks havoc.", False),
    (453, "Croagunk", [], ["Poison", "Fighting"], 4,
     "A poison wielder with a dastardly personality. Despite such qualities, this species is afforded a measure of popularity due to its peculiar cry and comical features.", False),
    (454, "Toxicroak", [], ["Poison", "Fighting"], 4,
     "Its crimson claws contain a virulent toxin. This toxin can be made into a tonic by diluting it, mixing it with several types of wild grass, and boiling it down over two days.", False),
    (455, "Carnivine", [], ["Grass"], 4,
     "Though this is a plant Pok\u00e9mon, it has a gluttonous and unruly temperament. this Pok\u00e9mon attacks its prey with its cavernous maw wide open.", False),
    (456, "Finneon", [], ["Water"], 4,
     "What a gorgeous sight this Pok\u00e9mon is as it swims with its long, pink-painted caudal fins fluttering behind it. this Pok\u00e9mon's beautiful appearance has led to its nickname: \u201dfinery fish.\u201d", False),
    (457, "Lumineon", [], ["Water"], 4,
     "Uses its gleaming fins to hunt its prey. The view of this Pok\u00e9mon schooling near the surface of the sea at night is breathtaking\u2014 it's as though there were shining stars right there.", False),
    (458, "Mantyke", [], ["Water", "Flying"], 4,
     "Though ball-like in shape, this Pok\u00e9mon is a proficient swimmer. I have discovered that if a this Pok\u00e9mon spends much time with schools of Remoraid, it will eventually achieve evolution.", False),
    (459, "Snover", [], ["Grass", "Ice"], 4,
     "One is likely to encounter this Pok\u00e9mon while out in the snow. There are stories of this Pok\u00e9mon appearing in human settlements but doing no harm\u2014rather, they bond with the children.", False),
    (460, "Abomasnow", [], ["Grass", "Ice"], 4,
     "A powerful Pok\u00e9mon that can split huge boulders with ease. Dislikes associating with others and chooses to live quietly deep within the mountains, playing with the snow.", False),
    (461, "Weavile", [], ["Dark", "Ice"], 4,
     "This species corners prey as a pack, under the guidance of a leader. this Pok\u00e9mon displays increased cunning, leading me to speculate that its evolution caused further brain development.", False),
    (462, "Magnezone", [], ["Electric", "Steel"], 4,
     "I theorize that a special magnetic field influenced this Pok\u00e9mon, changing its molecular structure and causing it to evolve. It emits strange radio waves toward space from its antenna.", False),
    (463, "Lickilicky", [], ["Normal"], 4,
     "Its tongue can extend and contract freely, and it is capable of reaching lengths over 10 times this Pok\u00e9mon's height. Beware of the saliva, as it contains corrosive elements.", False),
    (464, "Rhyperior", [], ["Ground", "Rock"], 4,
     "This Pok\u00e9mon evolved through use of a curious item. Its rocklike hide is composed of a mysterious substance and can withstand a blow from a masterwork sword with nary a scratch.", False),
    (465, "Tangrowth", [], ["Grass"], 4,
     "Draped with long vines, it resembles a shrub in appearance. It swings bundles of vines as though they were arms, wrapping them around prey to ensnare them.", False),
    (466, "Electivire", [], ["Electric"], 4,
     "Its evolution was induced by an unusual item, and its electrical output rises along with its heart rate. From its tails, it can unleash an electric current measuring 20,000 volts.", False),
    (467, "Magmortar", [], ["Fire"], 4,
     "Use of a strange item caused this Pok\u00e9mon to evolve. Fireballs launched from the ends of its tubelike arms are hot enough to melt an iron pot in an instant.", False),
    (468, "Togekiss", [], ["Fairy", "Flying"], 4,
     "Scant few have ever sighted this Pok\u00e9mon. After studying what literature remains, I am certain this Pok\u00e9mon will reveal itself when peace reigns in the land.", False),
    (469, "Yanmega", [], ["Bug", "Flying"], 4,
     "Extremely violent. When hunting, it wastes none of its energy, aiming only for prey's most vulnerable spots. Any who manage to tame this Pok\u00e9mon must be of incredible bravery.", False),
    (470, "Leafeon", [], ["Grass"], 4,
     "Cells similar to those of plants have been found in its fur. Its hard tail can fell a large tree with one stroke, and the tail's sharpness exceeds even that of a sword crafted by a master.", False),
    (471, "Glaceon", [], ["Ice"], 4,
     "this Pok\u00e9mon is able to lower its body temperature very quickly. It freezes the atmosphere, creating diamond dust that glitters like gems while it flutters and dances around.", False),
    (472, "Gliscor", [], ["Ground", "Flying"], 4,
     "It glides soundlessly on pitch-black wings and sinks sharp fangs into the throat of its prey. It takes on a look of satisfaction once it has entirely drained its prey of blood.", False),
    (473, "Mamoswine", [], ["Ice", "Ground"], 4,
     "This species reached its zenith during the period known as the ice age. I suspect that Hisui's frigid climate is in harmony with this Pok\u00e9mon's constitution, thus awakening hidden potential.", False),
    (474, "Porygon-Z", [], ["Normal"], 4,
     "A curious item induced this evolution. The Pok\u00e9mon's offensive capabilities have greatly increased, but the strangeness of its behavior has magnified in equal measure. This worries me.", False),
    (475, "Gallade", [], ["Psychic", "Fighting"], 4,
     "The blades extending from its elbows are sharper than the finest swords. Its swordsmanship, albeit self-taught, is astonishingly impressive.", False),
    (476, "Probopass", [], ["Rock", "Steel"], 4,
     "It is able to emit powerful magnetism, allowing it control over the iron sand that forms its luscious mustache. Using this iron sand, this Pok\u00e9mon forms hard stones with which it smites its prey.", False),
    (477, "Dusknoir", [], ["Ghost"], 4,
     "Comes to those whose lives have come to an end and escorts their souls to the afterlife. Known to mistakenly take the souls of those who yet have life left in them, albeit rarely.", False),
    (478, "Froslass", [], ["Ice", "Ghost"], 4,
     "A Pok\u00e9mon inhabited by the soul of a woman who died bearing a grudge in the snowy mountains. Legends of this Pok\u00e9mon placing deathly curses on misbehaving men send shivers down my spine.", False),
    (479, "Rotom", [], ["Electric", "Ghost"], 4,
     "This bizarre Pok\u00e9mon appears to be a will-o'-the-wisp powered by electricity. Be wary, as this Pok\u00e9mon is both smart and mischievous.", False),
    (480, "Uxie", [], ["Psychic"], 4,
     "A Pok\u00e9mon feared but also respected for stealing away the memories of evildoers. I have found records that suggest this Pok\u00e9mon holds dominion over knowledge.", True),
    (481, "Mesprit", [], ["Psychic"], 4,
     "Known as the Being of Emotion. In legend, this Pok\u00e9mon was feared, as any who showed disrespect would have their emotions thrown into disarray.", True),
    (482, "Azelf", [], ["Psychic"], 4,
     "The dreaded Being of Willpower. Legends tell of this Pok\u00e9mon manipulating the will of its adversaries and turning them into puppets of its own.", True),
    (483, "Dialga", [], ["Steel", "Dragon"], 4,
     "This Pok\u00e9mon is revered as a deity in Hisuian legend. The birth of this Pok\u00e9mon was what caused the vast river of time to begin flowing in our world.", True),
    (484, "Palkia", [], ["Water", "Dragon"], 4,
     "This Pok\u00e9mon is feared as a deity in Hisuian legend. The birth of this Pok\u00e9mon was what caused the walls of our world to disappear, creating a sky that spans for infinity.", True),
    (485, "Heatran", [], ["Fire", "Steel"], 4,
     "Stories tell of this Pok\u00e9mon being birthed from the boiling magma within Mount Coronet. Its molten-steel body holds many mysteries.", True),
    (486, "Regigigas", [], ["Normal"], 4,
     "According to legend, this Pok\u00e9mon pulled landmasses together and bound them with rope to create the continent of Hisui. Though I have my doubts, the story could well contain a shred of truth.", True),
    (487, "Giratina", [], ["Ghost", "Dragon"], 4,
     "There is one Hisuian verse that tells of a powerful light creating a deep shadow. I imagine that this deep shadow is this Pok\u00e9mon.", True),
    (488, "Cresselia", [], ["Psychic"], 4,
     "this Pok\u00e9mon is reminiscent of the crescent moon. It leaves a brilliant line of light in its wake as it flies across the night sky. I daresay it resembles the heavenly maiden who created the Milky Way.", True),
    (489, "Phione", [], ["Water"], 4,
     "Can be seen floating offshore during seasons when the seas are warm. Its azure body blends in with the ocean waters\u2014logic suggests this is a defense mechanism against natural predators.", True),
    (490, "Manaphy", [], ["Water"], 4,
     "Rumored to migrate across the oceans and visit Hisui's coastal waters only rarely. Although this Pok\u00e9mon resembles Phione, it is also quite different. The relation between the two is unclear.", True),
    (491, "Darkrai", [], ["Dark"], 4,
     "On a moonless night, a strange incident occurred in which every one of a village's inhabitants suffered nightmares. The villagers attested that this Pok\u00e9mon appeared before them in these nightmares.", True),
    (492, "Shaymin", [], ["Grass"], 4,
     "When the turning of seasons brings the cruel winter to its end and the joyous people give thanks to the heavens, this Pok\u00e9mon appears and covers the withered land with flowers.", True),
    (493, "Arceus", [], ["Normal"], 4,
     "It is the heavenly fount from which pours the light that shines across Hisui. Its luminance guides and protects all Pok\u00e9mon. Hisuian mythology states that this Pok\u00e9mon is the creator of all things.", True),
    # ── Gen 5 (Unova) ─────────────────────────────────────────────────────
    (494, "Victini", [], ["Psychic", "Fire"], 5,
     "When it shares the infinite energy it creates, that being\u2019s entire body will be overflowing with power.", True),
    (495, "Snivy", [], ["Grass"], 5,
     "They photosynthesize by bathing their tails in sunlight. When they are not feeling well, their tails droop.", False),
    (496, "Servine", [], ["Grass"], 5,
     "When it gets dirty, its leaves can\u2019t be used in photosynthesis, so it always keeps itself clean.", False),
    (497, "Serperior", [], ["Grass"], 5,
     "It can stop its opponents\u2019 movements with just a glare. It takes in solar energy and boosts it internally.", False),
    (498, "Tepig", [], ["Fire"], 5,
     "It loves to eat roasted berries, but sometimes it gets too excited and burns them to a crisp.", False),
    (499, "Pignite", [], ["Fire", "Fighting"], 5,
     "When its internal fire flares up, its movements grow sharper and faster. When in trouble, it emits smoke.", False),
    (500, "Emboar", [], ["Fire", "Fighting"], 5,
     "It has mastered fast and powerful fighting moves. It grows a beard of fire.", False),
    (501, "Oshawott", [], ["Water"], 5,
     "This Pok\u00e9mon from the Unova region uses the shell on its belly as a weapon to cut down its foes. Thus, I've conferred upon this shell the name \u201dscalchop\u201d.", False),
    (502, "Dewott", [], ["Water"], 5,
     "Its exquisite double-scalchop technique is likely the result of daily training, and it can send even masters of the blade fleeing in defeat.", False),
    (503, "Samurott", [], ["Water"], 5,
     "Hard of heart and deft of blade, this rare form of this Pok\u00e9mon is a product of the Pok\u00e9mon's evolution in the region of Hisui. Its turbulent blows crash into foes like ceaseless pounding waves.", False),
    (504, "Patrat", [], ["Normal"], 5,
     "Extremely cautious, one of them will always be on the lookout, but it won\u2019t notice a foe coming from behind.", False),
    (505, "Watchog", [], ["Normal"], 5,
     "When they see an enemy, their tails stand high, and they spit the seeds of berries stored in their cheek pouches.", False),
    (506, "Lillipup", [], ["Normal"], 5,
     "This Pok\u00e9mon is far brighter than the average child, and this Pok\u00e9mon won\u2019t forget the love it receives or any abuse it suffers.", False),
    (507, "Herdier", [], ["Normal"], 5,
     "The black fur that covers this Pok\u00e9mon\u2019s body is dense and springy. Even sharp fangs bounce right off.", False),
    (508, "Stoutland", [], ["Normal"], 5,
     "this Pok\u00e9mon is immensely proud of its impressive moustache. It\u2019s said that moustache length is what determines social standing among this species.", False),
    (509, "Purrloin", [], ["Dark"], 5,
     "Opponents that get drawn in by its adorable behavior come away with stinging scratches from its claws and stinging pride from its laughter.", False),
    (510, "Liepard", [], ["Dark"], 5,
     "This stealthy Pok\u00e9mon sneaks up behind prey without making any sound at all. It competes with Thievul for territory.", False),
    (511, "Pansage", [], ["Grass"], 5,
     "It\u2019s good at finding berries and gathers them from all over. It\u2019s kind enough to share them with friends.", False),
    (512, "Simisage", [], ["Grass"], 5,
     "Ill tempered, it fights by swinging its barbed tail around wildly. The leaf growing on its head is very bitter.", False),
    (513, "Pansear", [], ["Fire"], 5,
     "This Pok\u00e9mon lives in caves in volcanoes. The fire within the tuft on its head can reach 600 degrees Fahrenheit.", False),
    (514, "Simisear", [], ["Fire"], 5,
     "When it gets excited, embers rise from its head and tail and it gets hot. For some reason, it loves sweets.", False),
    (515, "Panpour", [], ["Water"], 5,
     "The water stored inside the tuft on its head is full of nutrients. Plants that receive its water grow large.", False),
    (516, "Simipour", [], ["Water"], 5,
     "It prefers places with clean water. When its tuft runs low, it replenishes it by siphoning up water with its tail.", False),
    (517, "Munna", [], ["Psychic"], 5,
     "It eats dreams and releases mist. The mist is pink when it\u2019s eating a good dream, and black when it\u2019s eating a nightmare.", False),
    (518, "Musharna", [], ["Psychic"], 5,
     "It drowses and dreams all the time. It\u2019s best to leave it be if it\u2019s just woken up, as it\u2019s a terrible grump when freshly roused from sleep.", False),
    (519, "Pidove", [], ["Normal", "Flying"], 5,
     "It\u2019s forgetful and not very bright, but many Trainers love it anyway for its friendliness and sincerity.", False),
    (520, "Tranquill", [], ["Normal", "Flying"], 5,
     "These bright Pok\u00e9mon have acute memories. Apparently delivery workers often choose them as their partners.", False),
    (521, "Unfezant", [], ["Normal", "Flying"], 5,
     "This Pok\u00e9mon is intelligent and intensely proud. People will sit up and take notice if you become the Trainer of one.", False),
    (522, "Blitzle", [], ["Electric"], 5,
     "Its mane shines when it discharges electricity. They use the frequency and rhythm of these flashes to communicate.", False),
    (523, "Zebstrika", [], ["Electric"], 5,
     "They have lightning-like movements. When this Pok\u00e9mon run at full speed, the sound of thunder reverberates.", False),
    (524, "Roggenrola", [], ["Rock"], 5,
     "When it detects a noise, it starts to move. The energy core inside it makes this Pok\u00e9mon slightly warm to the touch.", False),
    (525, "Boldore", [], ["Rock"], 5,
     "It relies on sound in order to monitor what\u2019s in its vicinity. When angered, it will attack without ever changing the direction it\u2019s facing.", False),
    (526, "Gigalith", [], ["Rock"], 5,
     "Although its energy blasts can blow away a dump truck, they have a limitation\u2014 they can only be fired when the sun is out.", False),
    (527, "Woobat", [], ["Psychic", "Flying"], 5,
     "It emits ultrasonic waves as it flutters about, searching for its prey\u2014bug Pok\u00e9mon.", False),
    (528, "Swoobat", [], ["Psychic", "Flying"], 5,
     "The auspicious shape of this Pok\u00e9mon\u2019s nose apparently led some regions to consider this Pok\u00e9mon a symbol of good luck.", False),
    (529, "Drilbur", [], ["Ground"], 5,
     "It\u2019s a digger, using its claws to burrow through the ground. It causes damage to vegetable crops, so many farmers have little love for it.", False),
    (530, "Excadrill", [], ["Ground", "Steel"], 5,
     "Known as the Drill King, this Pok\u00e9mon can tunnel through the terrain at speeds of over 90 mph.", False),
    (531, "Audino", [], ["Normal"], 5,
     "This Pok\u00e9mon has a kind heart. By touching with its feelers, this Pok\u00e9mon can gauge other creatures\u2019 feelings and physical conditions.", False),
    (532, "Timburr", [], ["Fighting"], 5,
     "this Pok\u00e9mon that have started carrying logs that are about three times their size are nearly ready to evolve.", False),
    (533, "Gurdurr", [], ["Fighting"], 5,
     "this Pok\u00e9mon excels at demolition\u2014construction is not its forte. In any case, there\u2019s skill in the way this Pok\u00e9mon wields its metal beam.", False),
    (534, "Conkeldurr", [], ["Fighting"], 5,
     "When going all out, this Pok\u00e9mon throws aside its concrete pillars and leaps at opponents to pummel them with its fists.", False),
    (535, "Tympole", [], ["Water"], 5,
     "It uses sound waves to communicate with others of its kind. People and other Pok\u00e9mon species can\u2019t hear its cries of warning.", False),
    (536, "Palpitoad", [], ["Water", "Ground"], 5,
     "On occasion, their cries are sublimely pleasing to the ear. this Pok\u00e9mon with larger lumps on their bodies can sing with a wider range of sounds.", False),
    (537, "Seismitoad", [], ["Water", "Ground"], 5,
     "This Pok\u00e9mon is popular among the elderly, who say the vibrations of its lumps are great for massages.", False),
    (538, "Throh", [], ["Fighting"], 5,
     "They train in groups of five. Any member that can\u2019t keep up will discard its belt and leave the group.", False),
    (539, "Sawk", [], ["Fighting"], 5,
     "The karate chops of a this Pok\u00e9mon that\u2019s trained itself to the limit can cleave the ocean itself.", False),
    (540, "Sewaddle", [], ["Bug", "Grass"], 5,
     "Since this Pok\u00e9mon makes its own clothes out of leaves, it is a popular mascot for fashion designers.", False),
    (541, "Swadloon", [], ["Bug", "Grass"], 5,
     "It protects itself from the cold by wrapping up in leaves. It stays on the move, eating leaves in forests.", False),
    (542, "Leavanny", [], ["Bug", "Grass"], 5,
     "It keeps its eggs warm with heat from fermenting leaves. It also uses leaves to make warm wrappings for Sewaddle.", False),
    (543, "Venipede", [], ["Bug", "Poison"], 5,
     "Its fangs are highly venomous. If this Pok\u00e9mon finds prey it thinks it can eat, it leaps for them without any thought of how things might turn out.", False),
    (544, "Whirlipede", [], ["Bug", "Poison"], 5,
     "this Pok\u00e9mon protects itself with a sturdy shell and poisonous spikes while it stores up the energy it\u2019ll need for evolution.", False),
    (545, "Scolipede", [], ["Bug", "Poison"], 5,
     "this Pok\u00e9mon engage in fierce territorial battles with Centiskorch. At the end of one of these battles, the victor makes a meal of the loser.", False),
    (546, "Cottonee", [], ["Grass", "Fairy"], 5,
     "Weaving together the cotton of both this Pok\u00e9mon and Eldegoss produces exquisite cloth that\u2019s highly prized by many luxury brands.", False),
    (547, "Whimsicott", [], ["Grass", "Fairy"], 5,
     "As long as this Pok\u00e9mon bathes in sunlight, its cotton keeps growing. If too much cotton fluff builds up, this Pok\u00e9mon tears it off and scatters it.", False),
    (548, "Petilil", [], ["Grass"], 5,
     "The leaves on its head are highly valued for medicinal purposes. Dry the leaves in the sun, boil them, and then drink the bitter decoction for remarkably effective relief from fatigue.", False),
    (549, "Lilligant", [], ["Grass"], 5,
     "I suspect that its well-developed legs are the result of a life spent on mountains covered in deep snow. The scent it exudes from its flower crown heartens those in proximity.", False),
    (550, "Basculin", [], ["Water"], 5,
     "Though it differs from other this Pok\u00e9mon in several respects, including demeanor\u2014this one is gentle\u2014I have categorized it as a regional form given the vast array of shared qualities.", False),
    (551, "Sandile", [], ["Ground", "Dark"], 5,
     "this Pok\u00e9mon is small, but its legs and lower body are powerful. Pushing sand aside as it goes, this Pok\u00e9mon moves through the desert as if it\u2019s swimming.", False),
    (552, "Krokorok", [], ["Ground", "Dark"], 5,
     "Although this Pok\u00e9mon has specialized eyes that allow it to see in the dark, this Pok\u00e9mon won\u2019t move much at night\u2014the desert gets cold after sunset.", False),
    (553, "Krookodile", [], ["Ground", "Dark"], 5,
     "While terribly aggressive, this Pok\u00e9mon also has the patience to stay hidden under sand for days, lying in wait for prey.", False),
    (554, "Darumaka", [], ["Fire"], 5,
     "This popular symbol of good fortune will never fall over in its sleep, no matter how it\u2019s pushed or pulled.", False),
    (555, "Darmanitan", [], ["Fire"], 5,
     "This Pok\u00e9mon\u2019s power level rises along with the temperature of its fire, which can reach 2,500 degrees Fahrenheit.", False),
    (556, "Maractus", [], ["Grass"], 5,
     "Once each year, this Pok\u00e9mon scatters its seeds. They\u2019re jam-packed with nutrients, making them a precious food source out in the desert.", False),
    (557, "Dwebble", [], ["Bug", "Rock"], 5,
     "It first tries to find a rock to live in, but if there are no suitable rocks to be found, this Pok\u00e9mon may move in to the ports of a Hippowdon.", False),
    (558, "Crustle", [], ["Bug", "Rock"], 5,
     "Its thick claws are its greatest weapons. They\u2019re mighty enough to crack Rhyperior\u2019s carapace.", False),
    (559, "Scraggy", [], ["Dark", "Fighting"], 5,
     "It protects itself with its durable skin. It\u2019s thought that this Pok\u00e9mon will evolve once its skin has completely stretched out.", False),
    (560, "Scrafty", [], ["Dark", "Fighting"], 5,
     "While mostly known for having the temperament of an aggressive ruffian, this Pok\u00e9mon takes very good care of its family, friends, and territory.", False),
    (561, "Sigilyph", [], ["Psychic", "Flying"], 5,
     "A discovery was made in the desert where this Pok\u00e9mon fly. The ruins of what may have been an ancient city were found beneath the sands.", False),
    (562, "Yamask", [], ["Ghost"], 5,
     "The spirit of a person from a bygone age became this Pok\u00e9mon. It rambles through ruins, searching for someone who knows its face.", False),
    (563, "Cofagrigus", [], ["Ghost"], 5,
     "There are many depictions of this Pok\u00e9mon decorating ancient tombs. They\u2019re symbols of the wealth that kings of bygone eras had.", False),
    (564, "Tirtouga", [], ["Water", "Rock"], 5,
     "this Pok\u00e9mon is considered to be the ancestor of many turtle Pok\u00e9mon. It was restored to life from a fossil.", False),
    (565, "Carracosta", [], ["Water", "Rock"], 5,
     "This Pok\u00e9mon emerges from the water in search of prey despite the fact that it moves more slowly on land.", False),
    (566, "Archen", [], ["Rock", "Flying"], 5,
     "this Pok\u00e9mon is said to be the ancestor of bird Pok\u00e9mon. It lived in treetops, eating berries and bug Pok\u00e9mon.", False),
    (567, "Archeops", [], ["Rock", "Flying"], 5,
     "Though capable of flight, this Pok\u00e9mon was apparently better at hunting on the ground.", False),
    (568, "Trubbish", [], ["Poison"], 5,
     "This Pok\u00e9mon was born from a bag stuffed with trash. Galarian Weezing relish the fumes belched by this Pok\u00e9mon.", False),
    (569, "Garbodor", [], ["Poison"], 5,
     "The toxic liquid it launches from its right arm is so virulent that it can kill a weakened creature instantly.", False),
    (570, "Zorua", [], ["Dark"], 5,
     "A once-departed soul, returned to life in Hisui. Derives power from resentment, which rises as energy atop its head and takes on the forms of foes. In this way, this Pok\u00e9mon vents lingering malice.", False),
    (571, "Zoroark", [], ["Dark"], 5,
     "With its disheveled white fur, it looks like an embodiment of death. Heedless of its own safety, this Pok\u00e9mon attacks its nemeses with a bitter energy so intense, it lacerates this Pok\u00e9mon's own body.", False),
    (572, "Minccino", [], ["Normal"], 5,
     "They pet each other with their tails as a form of greeting. Of the two, the one whose tail is fluffier is a bit more boastful.", False),
    (573, "Cinccino", [], ["Normal"], 5,
     "A special oil that seeps through their fur helps them avoid attacks. The oil fetches a high price at market.", False),
    (574, "Gothita", [], ["Psychic"], 5,
     "Even when nobody seems to be around, this Pok\u00e9mon can still be heard making a muted cry. Many believe it\u2019s speaking to something only it can see.", False),
    (575, "Gothorita", [], ["Psychic"], 5,
     "On nights when the stars shine, this Pok\u00e9mon\u2019s psychic power is at its strongest. It\u2019s unknown just what link this Pok\u00e9mon has to the greater universe.", False),
    (576, "Gothitelle", [], ["Psychic"], 5,
     "A criminal who was shown his fate by a this Pok\u00e9mon went missing that same day and was never seen again.", False),
    (577, "Solosis", [], ["Psychic"], 5,
     "Many say that the special liquid covering this Pok\u00e9mon\u2019s body would allow it to survive in the vacuum of space.", False),
    (578, "Duosion", [], ["Psychic"], 5,
     "Its brain has split into two, and the two halves rarely think alike. Its actions are utterly unpredictable.", False),
    (579, "Reuniclus", [], ["Psychic"], 5,
     "It\u2019s said that drinking the liquid surrounding this Pok\u00e9mon grants wisdom. Problem is, the liquid is highly toxic to anything besides this Pok\u00e9mon itself.", False),
    (580, "Ducklett", [], ["Water", "Flying"], 5,
     "They are better at swimming than flying, and they happily eat their favorite food, peat moss, as they dive underwater.", False),
    (581, "Swanna", [], ["Water", "Flying"], 5,
     "this Pok\u00e9mon start to dance at dusk. The one dancing in the middle is the leader of the flock.", False),
    (582, "Vanillite", [], ["Ice"], 5,
     "Supposedly, this Pok\u00e9mon was born from an icicle. It spews out freezing air at \u221258 degrees Fahrenheit to make itself more comfortable.", False),
    (583, "Vanillish", [], ["Ice"], 5,
     "It blasts enemies with cold air reaching \u2212148 degrees Fahrenheit, freezing them solid. But it spares their lives afterward\u2014it\u2019s a kind Pok\u00e9mon.", False),
    (584, "Vanilluxe", [], ["Ice"], 5,
     "People believe this Pok\u00e9mon formed when two Vanillish stuck together. Its body temperature is roughly 21 degrees Fahrenheit.", False),
    (585, "Deerling", [], ["Normal", "Grass"], 5,
     "The turning of the seasons changes the color and scent of this Pok\u00e9mon\u2019s fur. People use it to mark the seasons.", False),
    (586, "Sawsbuck", [], ["Normal", "Grass"], 5,
     "They migrate according to the seasons, so some people call this Pok\u00e9mon the harbingers of spring.", False),
    (587, "Emolga", [], ["Electric", "Flying"], 5,
     "This Pok\u00e9mon absolutely loves sweet berries. Sometimes it stuffs its cheeks full of so much food that it can\u2019t fly properly.", False),
    (588, "Karrablast", [], ["Bug"], 5,
     "It spits a liquid from its mouth to melt through Shelmet\u2019s shell. this Pok\u00e9mon doesn\u2019t eat the shell\u2014 it eats only the contents.", False),
    (589, "Escavalier", [], ["Bug", "Steel"], 5,
     "It charges its enemies, lances at the ready. An image of one of its duels is captured in a famous painting of this Pok\u00e9mon clashing with Sirfetch\u2019d.", False),
    (590, "Foongus", [], ["Grass", "Poison"], 5,
     "The spores released from this Pok\u00e9mon\u2019s hands are highly poisonous, but when thoroughly dried, the spores can be used as stomach medicine.", False),
    (591, "Amoonguss", [], ["Grass", "Poison"], 5,
     "this Pok\u00e9mon generally doesn\u2019t move much. It tends to stand still near Pok\u00e9 Balls that have been dropped on the ground.", False),
    (592, "Frillish", [], ["Water", "Ghost"], 5,
     "Legend has it that the residents of a sunken ancient city changed into these Pok\u00e9mon.", False),
    (593, "Jellicent", [], ["Water", "Ghost"], 5,
     "Whenever a full moon hangs in the night sky, schools of this Pok\u00e9mon gather near the surface of the sea, waiting for their prey to appear.", False),
    (594, "Alomomola", [], ["Water"], 5,
     "The reason it helps Pok\u00e9mon in a weakened condition is that any Pok\u00e9mon coming after them may also attack this Pok\u00e9mon.", False),
    (595, "Joltik", [], ["Bug", "Electric"], 5,
     "this Pok\u00e9mon latch on to other Pok\u00e9mon and suck out static electricity. They\u2019re often found sticking to Yamper\u2019s hindquarters.", False),
    (596, "Galvantula", [], ["Bug", "Electric"], 5,
     "It lays traps of electrified threads near the nests of bird Pok\u00e9mon, aiming to snare chicks that are not yet good at flying.", False),
    (597, "Ferroseed", [], ["Grass", "Steel"], 5,
     "Mossy caves are their preferred dwellings. Enzymes contained in mosses help this Pok\u00e9mon\u2019s spikes grow big and strong.", False),
    (598, "Ferrothorn", [], ["Grass", "Steel"], 5,
     "Its spikes are harder than steel. This Pok\u00e9mon crawls across rock walls by stabbing the spikes on its feelers into the stone.", False),
    (599, "Klink", [], ["Steel"], 5,
     "It\u2019s suspected that this Pok\u00e9mon were the inspiration behind ancient people\u2019s invention of the first gears.", False),
    (600, "Klang", [], ["Steel"], 5,
     "Many companies in the Galar region choose this Pok\u00e9mon as their logo. This Pok\u00e9mon is considered the symbol of industrial technology.", False),
    (601, "Klinklang", [], ["Steel"], 5,
     "The three gears that compose this Pok\u00e9mon spin at high speed. Its new spiked gear isn\u2019t a living creature.", False),
    (602, "Tynamo", [], ["Electric"], 5,
     "One alone can emit only a trickle of electricity, so a group of them gathers to unleash a powerful electric shock.", False),
    (603, "Eelektrik", [], ["Electric"], 5,
     "These Pok\u00e9mon have a big appetite. When they spot their prey, they attack it and paralyze it with electricity.", False),
    (604, "Eelektross", [], ["Electric"], 5,
     "They crawl out of the ocean using their arms. They will attack prey on shore and immediately drag it into the ocean.", False),
    (605, "Elgyem", [], ["Psychic"], 5,
     "This Pok\u00e9mon was discovered about 50 years ago. Its highly developed brain enables it to exert its psychic powers.", False),
    (606, "Beheeyem", [], ["Psychic"], 5,
     "Sometimes found drifting above wheat fields, this Pok\u00e9mon can control the memories of its opponents.", False),
    (607, "Litwick", [], ["Ghost", "Fire"], 5,
     "The younger the life this Pok\u00e9mon absorbs, the brighter and eerier the flame on its head burns.", False),
    (608, "Lampent", [], ["Ghost", "Fire"], 5,
     "It lurks in cities, pretending to be a lamp. Once it finds someone whose death is near, it will trail quietly after them.", False),
    (609, "Chandelure", [], ["Ghost", "Fire"], 5,
     "In homes illuminated by this Pok\u00e9mon instead of lights, funerals were a constant occurrence\u2014 or so it\u2019s said.", False),
    (610, "Axew", [], ["Dragon"], 5,
     "They play with each other by knocking their large tusks together. Their tusks break sometimes, but they grow back so quickly that it isn\u2019t a concern.", False),
    (611, "Fraxure", [], ["Dragon"], 5,
     "Its skin is as hard as a suit of armor. this Pok\u00e9mon\u2019s favorite strategy is to tackle its opponents, stabbing them with its tusks at the same time.", False),
    (612, "Haxorus", [], ["Dragon"], 5,
     "While usually kindhearted, it can be terrifying if angered. Tusks that can slice through steel beams are how this Pok\u00e9mon deals with its adversaries.", False),
    (613, "Cubchoo", [], ["Ice"], 5,
     "It sniffles before performing a move, using its frosty snot to provide an icy element to any move that needs it.", False),
    (614, "Beartic", [], ["Ice"], 5,
     "It swims energetically through frigid seas. When it gets tired, it freezes the seawater with its breath so it can rest on the ice.", False),
    (615, "Cryogonal", [], ["Ice"], 5,
     "When the weather gets hot, these Pok\u00e9mon turn into water vapor. this Pok\u00e9mon are almost never seen during summer.", False),
    (616, "Shelmet", [], ["Bug"], 5,
     "It has a strange physiology that responds to electricity. When together with Karrablast, this Pok\u00e9mon evolves for some reason.", False),
    (617, "Accelgor", [], ["Bug"], 5,
     "Discarding its shell made it nimble. To keep itself from dehydrating, it wraps its body in bands of membrane.", False),
    (618, "Stunfisk", [], ["Ground", "Electric"], 5,
     "For some reason, this Pok\u00e9mon smiles slightly when it emits a strong electric current from the yellow markings on its body.", False),
    (619, "Mienfoo", [], ["Fighting"], 5,
     "Though small, this Pok\u00e9mon\u2019s temperament is fierce. Any creature that approaches this Pok\u00e9mon carelessly will be greeted with a flurry of graceful attacks.", False),
    (620, "Mienshao", [], ["Fighting"], 5,
     "Delivered at blinding speeds, kicks from this Pok\u00e9mon can shatter massive boulders into tiny pieces.", False),
    (621, "Druddigon", [], ["Dragon"], 5,
     "this Pok\u00e9mon are vicious and cunning. They take up residence in nests dug out by other Pok\u00e9mon, treating the stolen nests as their own lairs.", False),
    (622, "Golett", [], ["Ground", "Ghost"], 5,
     "This Pok\u00e9mon was created from clay. It received orders from its master many thousands of years ago, and it still follows those orders to this day.", False),
    (623, "Golurk", [], ["Ground", "Ghost"], 5,
     "There\u2019s a theory that inside this Pok\u00e9mon is a perpetual motion machine that produces limitless energy, but this belief hasn\u2019t been proven.", False),
    (624, "Pawniard", [], ["Dark", "Steel"], 5,
     "A pack of these Pok\u00e9mon forms to serve a Bisharp boss. Each this Pok\u00e9mon trains diligently, dreaming of one day taking the lead.", False),
    (625, "Bisharp", [], ["Dark", "Steel"], 5,
     "Violent conflicts erupt between this Pok\u00e9mon and Fraxure over places where sharpening stones can be found.", False),
    (626, "Bouffalant", [], ["Normal"], 5,
     "These Pok\u00e9mon live in herds of about 20 individuals. this Pok\u00e9mon that betray the herd will lose the hair on their heads for some reason.", False),
    (627, "Rufflet", [], ["Normal", "Flying"], 5,
     "Its chick-like looks belie its hotheadedness. It challenges its parents at every opportunity, desperate to prove its strength.", False),
    (628, "Braviary", [], ["Normal", "Flying"], 5,
     "Screaming a bloodcurdling battle cry, this huge and ferocious bird Pok\u00e9mon goes out on the hunt. It blasts lakes with shock waves, then scoops up any prey that float to the water's surface.", False),
    (629, "Vullaby", [], ["Dark", "Flying"], 5,
     "this Pok\u00e9mon grow quickly. Bones that have gotten too small for older this Pok\u00e9mon to wear often get passed down to younger ones in the nest.", False),
    (630, "Mandibuzz", [], ["Dark", "Flying"], 5,
     "They adorn themselves with bones. There seem to be fashion trends among them, as different bones come into and fall out of popularity.", False),
    (631, "Heatmor", [], ["Fire"], 5,
     "A flame serves as its tongue, melting through the hard shell of Durant so that this Pok\u00e9mon can devour their insides.", False),
    (632, "Durant", [], ["Bug", "Steel"], 5,
     "With their large mandibles, these Pok\u00e9mon can crunch their way through rock. They work together to protect their eggs from Sandaconda.", False),
    (633, "Deino", [], ["Dark", "Dragon"], 5,
     "Because it can\u2019t see, this Pok\u00e9mon is constantly biting at everything it touches, trying to keep track of its surroundings.", False),
    (634, "Zweilous", [], ["Dark", "Dragon"], 5,
     "Their two heads will fight each other over a single piece of food. this Pok\u00e9mon are covered in scars even without battling others.", False),
    (635, "Hydreigon", [], ["Dark", "Dragon"], 5,
     "The three heads take turns sinking their teeth into the opponent. Their attacks won\u2019t slow until their target goes down.", False),
    (636, "Larvesta", [], ["Bug", "Fire"], 5,
     "this Pok\u00e9mon\u2019s body is warm all over. It spouts fire from the tips of its horns to intimidate predators and scare prey.", False),
    (637, "Volcarona", [], ["Bug", "Fire"], 5,
     "This Pok\u00e9mon emerges from a cocoon formed of raging flames. Ancient murals depict this Pok\u00e9mon as a deity of fire.", False),
    (638, "Cobalion", [], ["Steel", "Fighting"], 5,
     "From the moment it\u2019s born, this Pok\u00e9mon radiates the air of a leader. Its presence will calm even vicious foes.", True),
    (639, "Terrakion", [], ["Rock", "Fighting"], 5,
     "In Unovan legend, this Pok\u00e9mon battled against humans in an effort to protect other Pok\u00e9mon.", True),
    (640, "Virizion", [], ["Grass", "Fighting"], 5,
     "It darts around opponents with a flurry of quick movements, slicing them up with its horns.", True),
    (641, "Tornadus", [], ["Flying"], 5,
     "This storm-stirring Pok\u00e9mon is said to cause the seasons to turn by whipping up the air. I suspect its humanlike form to be a false one.", True),
    (642, "Thundurus", [], ["Electric", "Flying"], 5,
     "They say this wielder of electricity has waged war with its nemesis, Tornadus, since time immemorial. The lightning bolts it hurls pierce the very earth and enrich the soil.", True),
    (643, "Reshiram", [], ["Dragon", "Fire"], 5,
     "According to myth, if people ignore truth and let themselves become consumed by greed, this Pok\u00e9mon will arrive to burn their kingdoms down.", True),
    (644, "Zekrom", [], ["Dragon", "Electric"], 5,
     "Mythology tells us that if people lose the righteousness in their hearts, their kingdoms will be razed by this Pok\u00e9mon\u2019s lightning.", True),
    (645, "Landorus", [], ["Ground", "Flying"], 5,
     "When the incarnations of wind and of lightning clash, this Pok\u00e9mon arrives to quell the conflict. After the tempests and thunderbolts abate, the land is sure to be blessed with bountiful harvests.", True),
    (646, "Kyurem", [], ["Dragon", "Ice"], 5,
     "It appears that this Pok\u00e9mon uses its powers over ice to freeze its own body in order to stabilize its cellular structure.", True),
    (647, "Keldeo", [], ["Water", "Fighting"], 5,
     "They say that this Pok\u00e9mon must survive harsh battles and fully develop the horn on its forehead before this Pok\u00e9mon\u2019s true power will awaken.", True),
    (648, "Meloetta", [], ["Normal", "Psychic"], 5,
     "Its melodies are sung with a special vocalization method that can control the feelings of those who hear it.", True),
    (649, "Genesect", [], ["Bug", "Steel"], 5,
     "This Pok\u00e9mon existed 300 million years ago. Team Plasma altered it and attached a cannon to its back.", True),
    # ── Gen 6 (Kalos) ─────────────────────────────────────────────────────
    (650, "Chespin", [], ["Grass"], 6,
     "Such a thick shell of wood covers its head and back that even a direct hit from a truck wouldn\u2019t faze it.", False),
    (651, "Quilladin", [], ["Grass"], 6,
     "They strengthen their lower bodies by running into one another. They are very kind and won\u2019t start fights.", False),
    (652, "Chesnaught", [], ["Grass", "Fighting"], 6,
     "When it takes a defensive posture with its fists guarding its face, it could withstand a bomb blast.", False),
    (653, "Fennekin", [], ["Fire"], 6,
     "As it walks, it munches on a twig in place of a snack. It intimidates opponents by puffing hot air out of its ears.", False),
    (654, "Braixen", [], ["Fire"], 6,
     "When the twig is plucked from its tail, friction sets the twig alight. The flame is used to send signals to its allies.", False),
    (655, "Delphox", [], ["Fire", "Psychic"], 6,
     "Using psychic power, it generates a fiery vortex of 5,400 degrees Fahrenheit, incinerating foes swept into this whirl of flame.", False),
    (656, "Froakie", [], ["Water"], 6,
     "It protects its skin by covering its body in delicate bubbles. Beneath its happy-go-lucky air, it keeps a watchful eye on its surroundings.", False),
    (657, "Frogadier", [], ["Water"], 6,
     "Its swiftness is unparalleled. It can scale a tower of more than 2,000 feet in a minute\u2019s time.", False),
    (658, "Greninja", [], ["Water", "Dark"], 6,
     "It appears and vanishes with a ninja\u2019s grace. It toys with its enemies using swift movements, while slicing them with throwing stars of sharpest water.", False),
    (659, "Bunnelby", [], ["Normal"], 6,
     "It\u2019s very sensitive to danger. The sound of Corviknight\u2019s flapping will have this Pok\u00e9mon digging a hole to hide underground in moments.", False),
    (660, "Diggersby", [], ["Normal", "Ground"], 6,
     "The fur on its belly retains heat exceptionally well. People used to make heavy winter clothing from fur shed by this Pok\u00e9mon.", False),
    (661, "Fletchling", [], ["Normal", "Flying"], 6,
     "When this Pok\u00e9mon gets excited, its body temperature increases sharply. If you touch a this Pok\u00e9mon with bare hands, you might get burned.", False),
    (662, "Fletchinder", [], ["Fire", "Flying"], 6,
     "this Pok\u00e9mon are exceedingly territorial and aggressive. These Pok\u00e9mon fight among themselves over feeding grounds.", False),
    (663, "Talonflame", [], ["Fire", "Flying"], 6,
     "this Pok\u00e9mon dives toward prey at speeds of up to 310 mph and assaults them with powerful kicks, giving the prey no chance to escape.", False),
    (664, "Scatterbug", [], ["Bug"], 6,
     "The powder that covers its body regulates its temperature, so it can live in any region or climate.", False),
    (665, "Spewpa", [], ["Bug"], 6,
     "The beaks of bird Pok\u00e9mon can\u2019t begin to scratch its stalwart body. To defend itself, it spews powder.", False),
    (666, "Vivillon", [], ["Bug", "Flying"], 6,
     "The patterns on this Pok\u00e9mon\u2019s wings depend on the climate and topography of its habitat. It scatters colorful scales.", False),
    (667, "Litleo", [], ["Fire", "Normal"], 6,
     "This hot-blooded Pok\u00e9mon is filled with curiosity. When it gets angry or starts fighting, its short mane gets hot.", False),
    (668, "Pyroar", [], ["Fire", "Normal"], 6,
     "The temperature of its breath is over 10,000 degrees Fahrenheit, but this Pok\u00e9mon doesn\u2019t use it on its prey. This Pok\u00e9mon prefers to eat raw meat.", False),
    (669, "Flab\u00e9b\u00e9", [], ["Fairy"], 6,
     "this Pok\u00e9mon wears a crown made from pollen it\u2019s collected from its flower. The crown has hidden healing properties.", False),
    (670, "Floette", [], ["Fairy"], 6,
     "It gives its own power to flowers, pouring its heart into caring for them. this Pok\u00e9mon never forgives anyone who messes up a flower bed.", False),
    (671, "Florges", [], ["Fairy"], 6,
     "Its life can span several hundred years. It\u2019s said to devote its entire life to protecting gardens.", False),
    (672, "Skiddo", [], ["Grass"], 6,
     "If it has sunshine and water, it doesn\u2019t need to eat, because it can generate energy from the leaves on its back.", False),
    (673, "Gogoat", [], ["Grass"], 6,
     "They inhabit mountainous regions. The leader of the herd is decided by a battle of clashing horns.", False),
    (674, "Pancham", [], ["Fighting"], 6,
     "Wanting to make sure it\u2019s taken seriously, this Pok\u00e9mon\u2019s always giving others a glare. But if it\u2019s not focusing, it ends up smiling.", False),
    (675, "Pangoro", [], ["Fighting", "Dark"], 6,
     "Using its leaf, this Pok\u00e9mon can predict the moves of its opponents. It strikes with punches that can turn a dump truck into scrap with just one hit.", False),
    (676, "Furfrou", [], ["Normal"], 6,
     "Left alone, its fur will grow longer and longer, but it will only allow someone it trusts to cut it.", False),
    (677, "Espurr", [], ["Psychic"], 6,
     "There\u2019s enough psychic power in this Pok\u00e9mon to send a wrestler flying, but because this power can\u2019t be controlled, this Pok\u00e9mon finds it troublesome.", False),
    (678, "Meowstic", [], ["Psychic"], 6,
     "The defensive instinct of the males is strong. It\u2019s when they\u2019re protecting themselves or their partners that they unleash their full power.", False),
    (679, "Honedge", [], ["Steel", "Ghost"], 6,
     "The blue eye on the sword\u2019s handguard is the true body of this Pok\u00e9mon. With its old cloth, it drains people\u2019s lives away.", False),
    (680, "Doublade", [], ["Steel", "Ghost"], 6,
     "The two swords employ a strategy of rapidly alternating between offense and defense to bring down their prey.", False),
    (681, "Aegislash", [], ["Steel", "Ghost"], 6,
     "Its potent spectral powers allow it to manipulate others. It once used its powers to force people and Pok\u00e9mon to build a kingdom to its liking.", False),
    (682, "Spritzee", [], ["Fairy"], 6,
     "The scent its body gives off enraptures those who smell it. Noble ladies had no shortage of love for this Pok\u00e9mon.", False),
    (683, "Aromatisse", [], ["Fairy"], 6,
     "The scents this Pok\u00e9mon can produce range from sweet smells that bolster allies to foul smells that sap an opponent\u2019s will to fight.", False),
    (684, "Swirlix", [], ["Fairy"], 6,
     "The sweet smell of cotton candy perfumes this Pok\u00e9mon\u2019s fluffy fur. This Pok\u00e9mon spits out sticky string to tangle up its enemies.", False),
    (685, "Slurpuff", [], ["Fairy"], 6,
     "this Pok\u00e9mon\u2019s fur contains a lot of air, making it soft to the touch and lighter than it looks.", False),
    (686, "Inkay", [], ["Dark", "Psychic"], 6,
     "By exposing foes to the blinking of its luminescent spots, this Pok\u00e9mon demoralizes them, and then it seizes the chance to flee.", False),
    (687, "Malamar", [], ["Dark", "Psychic"], 6,
     "It\u2019s said that this Pok\u00e9mon\u2019s hypnotic powers played a role in certain history-changing events.", False),
    (688, "Binacle", [], ["Rock", "Water"], 6,
     "If the two don\u2019t work well together, both their offense and defense fall apart. Without good teamwork, they won\u2019t survive.", False),
    (689, "Barbaracle", [], ["Rock", "Water"], 6,
     "Having an eye on each palm allows it to keep watch in all directions. In a pinch, its limbs start to act on their own to ensure the enemy\u2019s defeat.", False),
    (690, "Skrelp", [], ["Poison", "Water"], 6,
     "this Pok\u00e9mon looks like a piece of rotten seaweed, so it can blend in with seaweed drifting on the ocean and avoid being detected by enemies.", False),
    (691, "Dragalge", [], ["Poison", "Dragon"], 6,
     "this Pok\u00e9mon generates dragon energy by sticking the plume on its head out above the ocean\u2019s surface and bathing it in sunlight.", False),
    (692, "Clauncher", [], ["Water"], 6,
     "By detonating gas that accumulates in its right claw, this Pok\u00e9mon launches water like a bullet. This is how this Pok\u00e9mon defeats its enemies.", False),
    (693, "Clawitzer", [], ["Water"], 6,
     "this Pok\u00e9mon\u2019s right arm is a cannon that launches projectiles made of seawater. Shots from a this Pok\u00e9mon\u2019s cannon arm can sink a tanker.", False),
    (694, "Helioptile", [], ["Electric", "Normal"], 6,
     "The sun powers this Pok\u00e9mon\u2019s electricity generation. Interruption of that process stresses this Pok\u00e9mon to the point of weakness.", False),
    (695, "Heliolisk", [], ["Electric", "Normal"], 6,
     "One this Pok\u00e9mon basking in the sun with its frill outspread is all it would take to produce enough electricity to power a city.", False),
    (696, "Tyrunt", [], ["Rock", "Dragon"], 6,
     "This Pok\u00e9mon is selfish and likes to be pampered. It can also inflict grievous wounds on its Trainer just by playing around.", False),
    (697, "Tyrantrum", [], ["Rock", "Dragon"], 6,
     "A single bite of this Pok\u00e9mon\u2019s massive jaws will demolish a car. This Pok\u00e9mon was the king of the ancient world.", False),
    (698, "Amaura", [], ["Rock", "Ice"], 6,
     "this Pok\u00e9mon is an ancient Pok\u00e9mon that has gone extinct. Specimens of this species can sometimes be found frozen in ice.", False),
    (699, "Aurorus", [], ["Rock", "Ice"], 6,
     "When gripped by rage, this Pok\u00e9mon will emanate freezing air, covering everything around it in ice.", False),
    (700, "Sylveon", [], ["Fairy"], 6,
     "It emits a soothing aura from its ribbon-shaped organs. It wraps these appendages around quarrelers to instantly restore calm to the situation.", False),
    (701, "Hawlucha", [], ["Fighting", "Flying"], 6,
     "It always strikes a pose before going for its finishing move. Sometimes opponents take advantage of that time to counterattack.", False),
    (702, "Dedenne", [], ["Electric", "Fairy"], 6,
     "Since this Pok\u00e9mon can\u2019t generate much electricity on its own, it steals electricity from outlets or other electric Pok\u00e9mon.", False),
    (703, "Carbink", [], ["Rock", "Fairy"], 6,
     "It\u2019s said that somewhere in the world, there\u2019s a mineral vein housing a large pack of slumbering this Pok\u00e9mon. It\u2019s also said that this pack has a queen.", False),
    (704, "Goomy", [], ["Dragon"], 6,
     "this Pok\u00e9mon hides away in the shade of trees, where it's nice and humid. If the slime coating its body dries out, the Pok\u00e9mon instantly becomes lethargic.", False),
    (705, "Sliggoo", [], ["Dragon"], 6,
     "A creature given to melancholy. I suspect its metallic shell developed as a result of the mucus on its skin reacting with the iron in Hisui's water.", False),
    (706, "Goodra", [], ["Dragon"], 6,
     "Able to freely control the hardness of its metallic shell. It loathes solitude and is extremely clingy\u2014it will fume and run riot if those dearest to it ever leave its side.", False),
    (707, "Klefki", [], ["Steel", "Fairy"], 6,
     "this Pok\u00e9mon sucks in metal ions with the horn topping its head. It seems this Pok\u00e9mon loves keys so much that its head needed to look like one, too.", False),
    (708, "Phantump", [], ["Ghost", "Grass"], 6,
     "With a voice like a human child\u2019s, it cries out to lure adults deep into the forest, getting them lost among the trees.", False),
    (709, "Trevenant", [], ["Ghost", "Grass"], 6,
     "Small roots that extend from the tips of this Pok\u00e9mon\u2019s feet can tie into the trees of the forest and give this Pok\u00e9mon control over them.", False),
    (710, "Pumpkaboo", [], ["Ghost", "Grass"], 6,
     "The light that streams out from the holes in the pumpkin can hypnotize and control the people and Pok\u00e9mon that see it.", False),
    (711, "Gourgeist", [], ["Ghost", "Grass"], 6,
     "In the darkness of a new-moon night, this Pok\u00e9mon will come knocking. Whoever answers the door will be swept off to the afterlife.", False),
    (712, "Bergmite", [], ["Ice"], 6,
     "Lives on mountains blanketed in perennial snow. It freezes water vapor in the air to make the ice helmet that it dons for defense.", False),
    (713, "Avalugg", [], ["Ice"], 6,
     "The armor of ice covering its lower jaw puts steel to shame and can shatter rocks with ease. This Pok\u00e9mon barrels along steep mountain paths, cleaving through the deep snow.", False),
    (714, "Noibat", [], ["Flying", "Dragon"], 6,
     "No wavelength of sound is beyond this Pok\u00e9mon\u2019s ability to produce. The ultrasonic waves it generates can overcome much larger Pok\u00e9mon.", False),
    (715, "Noivern", [], ["Flying", "Dragon"], 6,
     "Flying through the darkness, it weakens enemies with ultrasonic waves that could crush stone. Its fangs finish the fight.", False),
    (716, "Xerneas", [], ["Fairy"], 6,
     "When the horns on its head shine in seven colors, it is said to be sharing everlasting life.", True),
    (717, "Yveltal", [], ["Dark", "Flying"], 6,
     "When its life comes to an end, it absorbs the life energy of every living thing and turns into a cocoon once more.", True),
    (718, "Zygarde", [], ["Dragon", "Ground"], 6,
     "Some say it can change to an even more powerful form when battling those who threaten the ecosystem.", True),
    (719, "Diancie", [], ["Rock", "Fairy"], 6,
     "It can instantly create many diamonds by compressing the carbon in the air between its hands.", True),
    (720, "Hoopa", [], ["Psychic", "Ghost"], 6,
     "It is said to be able to seize anything it desires with its six rings and six huge arms. With its power sealed, it is transformed into a much smaller form.", True),
    (721, "Volcanion", [], ["Fire", "Water"], 6,
     "It expels its internal steam from the arms on its back. It has enough power to blow away a mountain.", True),
    # ── Gen 7 (Alola) ─────────────────────────────────────────────────────
    (722, "Rowlet", [], ["Grass", "Flying"], 7,
     "Flies noiselessly on delicate wings. It has mastered the art of deftly launching dagger-sharp feathers from those same wings.", False),
    (723, "Dartrix", [], ["Grass", "Flying"], 7,
     "Regularly basks in sunlight to gather power\u2014presumably due to the frigid climate. Nonetheless, the edges of the blade quills set into its wings are keen as ever.", False),
    (724, "Decidueye", [], ["Grass", "Ghost"], 7,
     "The air stored inside the rachises of this Pok\u00e9mon's feathers insulates the Pok\u00e9mon against Hisui's extreme cold. This is firm proof that evolution can be influenced by environment.", False),
    (725, "Litten", [], ["Fire"], 7,
     "Trying to pet this Pok\u00e9mon before it trusts you will result in a nasty scratch from its sharp claws. Be careful.", False),
    (726, "Torracat", [], ["Fire"], 7,
     "When facing a powerful enemy, this Pok\u00e9mon\u2019s fighting spirit gets pumped up, and its fire bell blazes hotter.", False),
    (727, "Incineroar", [], ["Fire", "Dark"], 7,
     "this Pok\u00e9mon\u2019s rough and aggressive behavior is its most notable trait, but the way it helps out small Pok\u00e9mon shows that it has a kind side as well.", False),
    (728, "Popplio", [], ["Water"], 7,
     "If this Pok\u00e9mon want to create big, powerful balloons, they must be persistent. It takes daily practice for these Pok\u00e9mon to develop their skills.", False),
    (729, "Brionne", [], ["Water"], 7,
     "On nights when the sea is calm, this Pok\u00e9mon dance with one another to the singing of the Primarina that\u2019s leading them.", False),
    (730, "Primarina", [], ["Water", "Fairy"], 7,
     "For this Pok\u00e9mon, every battle\u2019s a stage. Its singing and the dancing of its balloons will mesmerize the audience.", False),
    (731, "Pikipek", [], ["Normal", "Flying"], 7,
     "It may look spindly, but its neck muscles are heavy-duty. It can peck at a tree 16 times per second!", False),
    (732, "Trumbeak", [], ["Normal", "Flying"], 7,
     "From its mouth, it fires the seeds of berries it has eaten. The scattered seeds give rise to new plants.", False),
    (733, "Toucannon", [], ["Normal", "Flying"], 7,
     "Known for forming harmonious couples, this Pok\u00e9mon is brought to wedding ceremonies as a good luck charm.", False),
    (734, "Yungoos", [], ["Normal"], 7,
     "Although it will eat anything, it prefers fresh living things, so it marches down streets in search of prey.", False),
    (735, "Gumshoos", [], ["Normal"], 7,
     "Patient by nature, this Pok\u00e9mon loses control of itself and pounces when it spots its favorite meal\u2014Rattata!", False),
    (736, "Grubbin", [], ["Bug"], 7,
     "It uses its big jaws to dig nests into the forest floor, and it loves to feed on sweet tree sap.", False),
    (737, "Charjabug", [], ["Bug", "Electric"], 7,
     "Its digestive processes convert the leaves it eats into electricity. An electric sac in its belly stores the electricity for later use.", False),
    (738, "Vikavolt", [], ["Bug", "Electric"], 7,
     "If it carries a Charjabug to use as a spare battery, a flying this Pok\u00e9mon can rapidly fire high-powered beams of electricity.", False),
    (739, "Crabrawler", [], ["Fighting"], 7,
     "this Pok\u00e9mon has been known to mistake Exeggutor for a coconut tree and climb it. The enraged Exeggutor shakes it off and stomps it.", False),
    (740, "Crabominable", [], ["Fighting", "Ice"], 7,
     "Before it stops to think, it just starts pummeling. There are records of its turning back avalanches with a flurry of punches.", False),
    (741, "Oricorio", [], ["Fire", "Flying"], 7,
     "This this Pok\u00e9mon has drunk red nectar. If its Trainer gives the wrong order, this passionate Pok\u00e9mon becomes fiercely angry.", False),
    (742, "Cutiefly", [], ["Bug", "Fairy"], 7,
     "An opponent\u2019s aura can tell this Pok\u00e9mon what that opponent\u2019s next move will be. Then this Pok\u00e9mon can glide around the attack and strike back.", False),
    (743, "Ribombee", [], ["Bug", "Fairy"], 7,
     "this Pok\u00e9mon absolutely hate getting wet or rained on. In the cloudy Galar region, they are very seldom seen.", False),
    (744, "Rockruff", [], ["Rock"], 7,
     "This Pok\u00e9mon intimidates opponents by striking the ground with the rocks on its neck. The moment an opponent flinches, this Pok\u00e9mon attacks.", False),
    (745, "Lycanroc", [], ["Rock"], 7,
     "With swift movements, this Pok\u00e9mon gradually backs its prey into a corner. this Pok\u00e9mon\u2019s fangs are always aimed toward opponents\u2019 weak spots.", False),
    (746, "Wishiwashi", [], ["Water"], 7,
     "When it senses danger, its eyes tear up. The sparkle of its tears signals other this Pok\u00e9mon to gather.", False),
    (747, "Mareanie", [], ["Poison", "Water"], 7,
     "Unlike their Alolan counterparts, the this Pok\u00e9mon of the Galar region have not yet figured out that the branches of Corsola are delicious.", False),
    (748, "Toxapex", [], ["Poison", "Water"], 7,
     "Within the poison sac in its body is a poison so toxic that Pok\u00e9mon as large as Wailord will still be suffering three days after it first takes effect.", False),
    (749, "Mudbray", [], ["Ground"], 7,
     "It eats dirt to create mud and smears this mud all over its feet, giving them the grip needed to walk on rough terrain without slipping.", False),
    (750, "Mudsdale", [], ["Ground"], 7,
     "this Pok\u00e9mon has so much stamina that it could carry over 10 tons across the Galar region without rest or sleep.", False),
    (751, "Dewpider", [], ["Water", "Bug"], 7,
     "this Pok\u00e9mon normally lives underwater. When it comes onto land in search of food, it takes water with it in the form of a bubble on its head.", False),
    (752, "Araquanid", [], ["Water", "Bug"], 7,
     "It acts as a caretaker for Dewpider, putting them inside its bubble and letting them eat any leftover food.", False),
    (753, "Fomantis", [], ["Grass"], 7,
     "During the day, this Pok\u00e9mon basks in sunlight and sleeps peacefully. It wakes and moves around at night.", False),
    (754, "Lurantis", [], ["Grass"], 7,
     "The petals on this Pok\u00e9mon\u2019s arms are thin and super sharp, and they can fire laser beams if this Pok\u00e9mon gathers light first.", False),
    (755, "Morelull", [], ["Grass", "Fairy"], 7,
     "this Pok\u00e9mon live in forests that stay dark even during the day. They scatter flickering spores that put enemies to sleep.", False),
    (756, "Shiinotic", [], ["Grass", "Fairy"], 7,
     "If you see a light deep in a forest at night, don\u2019t go near. this Pok\u00e9mon will make you fall fast asleep.", False),
    (757, "Salandit", [], ["Poison", "Fire"], 7,
     "This sneaky Pok\u00e9mon will slink behind its prey and immobilize it with poisonous gas before the prey even realizes this Pok\u00e9mon is there.", False),
    (758, "Salazzle", [], ["Poison", "Fire"], 7,
     "The winner of competitions between this Pok\u00e9mon is decided by which one has the most male Salandit with it.", False),
    (759, "Stufful", [], ["Normal", "Fighting"], 7,
     "The way it protects itself by flailing its arms may be an adorable sight, but stay well away. This is flailing that can snap thick tree trunks.", False),
    (760, "Bewear", [], ["Normal", "Fighting"], 7,
     "The moves it uses to take down its prey would make a martial artist jealous. It tucks subdued prey under its arms to carry them to its nest.", False),
    (761, "Bounsweet", [], ["Grass"], 7,
     "When under attack, it secretes a sweet and delicious sweat. The scent only calls more enemies to it.", False),
    (762, "Steenee", [], ["Grass"], 7,
     "Any Corvisquire that pecks at this Pok\u00e9mon will be greeted with a smack from its sepals followed by a sharp kick.", False),
    (763, "Tsareena", [], ["Grass"], 7,
     "A kick from the hardened tips of this Pok\u00e9mon\u2019s legs leaves a wound in the opponent\u2019s body and soul that will never heal.", False),
    (764, "Comfey", [], ["Fairy"], 7,
     "These Pok\u00e9mon smell very nice. All this Pok\u00e9mon wear different flowers, so each of these Pok\u00e9mon has its own individual scent.", False),
    (765, "Oranguru", [], ["Normal", "Psychic"], 7,
     "It knows the forest inside and out. If it comes across a wounded Pok\u00e9mon, this Pok\u00e9mon will gather medicinal herbs to treat it.", False),
    (766, "Passimian", [], ["Fighting"], 7,
     "this Pok\u00e9mon live in groups of about 20, with each member performing an assigned role. Through cooperation, the group survives.", False),
    (767, "Wimpod", [], ["Bug", "Water"], 7,
     "this Pok\u00e9mon gather in swarms, constantly on the lookout for danger. They scatter the moment they detect an enemy\u2019s presence.", False),
    (768, "Golisopod", [], ["Bug", "Water"], 7,
     "They live in sunken ships or in holes in the seabed. When this Pok\u00e9mon and Grapploct battle, the loser becomes the winner\u2019s meal.", False),
    (769, "Sandygast", [], ["Ghost", "Ground"], 7,
     "this Pok\u00e9mon mainly inhabits beaches. It takes control of anyone who puts their hand into its mouth, forcing them to make its body bigger.", False),
    (770, "Palossand", [], ["Ghost", "Ground"], 7,
     "This Pok\u00e9mon lives on beaches, but it hates water. this Pok\u00e9mon can\u2019t maintain its castle-like shape if it gets drenched by a heavy rain.", False),
    (771, "Pyukumuku", [], ["Water"], 7,
     "It\u2019s covered in a slime that keeps its skin moist, allowing it to stay on land for days without drying up.", False),
    (772, "Type: Null", [], ["Normal"], 7,
     "It was modeled after a mighty Pok\u00e9mon of myth. The mask placed upon it limits its power in order to keep it under control.", True),
    (773, "Silvally", [], ["Normal"], 7,
     "The final factor needed to release this Pok\u00e9mon\u2019s true power was a strong bond with a Trainer it trusts.", True),
    (774, "Minior", [], ["Rock", "Flying"], 7,
     "Although its outer shell is uncommonly durable, the shock of falling to the ground smashes the shell to smithereens.", False),
    (775, "Komala", [], ["Normal"], 7,
     "It remains asleep from birth to death as a result of the sedative properties of the leaves that form its diet.", False),
    (776, "Turtonator", [], ["Fire", "Dragon"], 7,
     "Eating sulfur in its volcanic habitat is what causes explosive compounds to develop in its shell. Its droppings are also dangerously explosive.", False),
    (777, "Togedemaru", [], ["Electric", "Steel"], 7,
     "When it\u2019s in trouble, it curls up into a ball, makes its fur spikes stand on end, and then discharges electricity indiscriminately.", False),
    (778, "Mimikyu", [], ["Ghost", "Fairy"], 7,
     "There was a scientist who peeked under this Pok\u00e9mon\u2019s old rag in the name of research. The scientist died of a mysterious disease.", False),
    (779, "Bruxish", [], ["Water", "Psychic"], 7,
     "Its skin is thick enough to fend off Mareanie\u2019s spikes. With its robust teeth, this Pok\u00e9mon crunches up the spikes and eats them.", False),
    (780, "Drampa", [], ["Normal", "Dragon"], 7,
     "this Pok\u00e9mon is a kind and friendly Pok\u00e9mon\u2014up until it\u2019s angered. When that happens, it stirs up a gale and flattens everything around.", False),
    (781, "Dhelmise", [], ["Ghost", "Grass"], 7,
     "After lowering its anchor, it waits for its prey. It catches large Wailord and drains their life-force.", False),
    (782, "Jangmo-o", [], ["Dragon"], 7,
     "this Pok\u00e9mon strikes its scales to communicate with others of its kind. Its scales are actually fur that\u2019s become as hard as metal.", False),
    (783, "Hakamo-o", [], ["Dragon", "Fighting"], 7,
     "Before attacking its enemies, it clashes its scales together and roars. Its sharp claws shred the opposition.", False),
    (784, "Kommo-o", [], ["Dragon", "Fighting"], 7,
     "Certain ruins have paintings of ancient warriors wearing armor made of this Pok\u00e9mon scales.", False),
    (785, "Tapu Koko", [], ["Electric", "Fairy"], 7,
     "The lightning-wielding guardian deity of Melemele, this Pok\u00e9mon is brimming with curiosity and appears before people from time to time.", True),
    (786, "Tapu Lele", [], ["Psychic", "Fairy"], 7,
     "Although called a guardian deity, this Pok\u00e9mon is devoid of guilt about its cruel disposition and can be described as nature incarnate.", True),
    (787, "Tapu Bulu", [], ["Grass", "Fairy"], 7,
     "It makes ringing sounds with its tail to let others know where it is, avoiding unneeded conflicts. This guardian deity of Ula\u2019ula controls plants.", True),
    (788, "Tapu Fini", [], ["Water", "Fairy"], 7,
     "Although it\u2019s called a guardian deity, terrible calamities sometimes befall those who recklessly approach this Pok\u00e9mon.", True),
    (789, "Cosmog", [], ["Psychic"], 7,
     "this Pok\u00e9mon is very curious but not very cautious, often placing itself in danger. If things start to look dicey, it teleports away.", True),
    (790, "Cosmoem", [], ["Psychic"], 7,
     "It sucks in dust from the air at an astounding rate, frantically building up energy within its core as preparation for evolution.", True),
    (791, "Solgaleo", [], ["Psychic", "Steel"], 7,
     "this Pok\u00e9mon was once known as the Beast That Devours the Sun. Energy in the form of light radiates boundlessly from it.", True),
    (792, "Lunala", [], ["Psychic", "Ghost"], 7,
     "It steals the light from its surroundings and then becomes the full moon, showering its own light across the night sky.", True),
    (793, "Nihilego", [], ["Rock", "Poison"], 7,
     "It appeared in this world from an Ultra Wormhole. this Pok\u00e9mon appears to be a parasite that lives by feeding on people and Pok\u00e9mon.", False),
    (794, "Buzzwole", [], ["Bug", "Fighting"], 7,
     "this Pok\u00e9mon goes around showing off its abnormally swollen muscles. It is one kind of Ultra Beast.", False),
    (795, "Pheromosa", [], ["Bug", "Fighting"], 7,
     "Although it\u2019s alien to this world and a danger here, it\u2019s apparently a common organism in the world where it normally lives.", False),
    (796, "Xurkitree", [], ["Electric"], 7,
     "They\u2019ve been dubbed Ultra Beasts. Some of them stand unmoving, like trees, with their arms and legs stuck into the ground.", False),
    (797, "Celesteela", [], ["Steel", "Flying"], 7,
     "Although it\u2019s alien to this world and a danger here, it\u2019s apparently a common organism in the world where it normally lives.", False),
    (798, "Kartana", [], ["Grass", "Steel"], 7,
     "Although it\u2019s alien to this world and a danger here, it\u2019s apparently a common organism in the world where it normally lives.", False),
    (799, "Guzzlord", [], ["Dark", "Dragon"], 7,
     "An unknown life-form called a UB. It may be constantly hungry\u2014it is certainly always devouring something.", False),
    (800, "Necrozma", [], ["Psychic"], 7,
     "It needs light to survive, and it goes on a rampage seeking it out. Its laser beams will cut anything to pieces.", True),
    (801, "Magearna", [], ["Steel", "Fairy"], 7,
     "Built roughly 500 years ago by a scientist, the part called the Soul-Heart is the actual life-form.", True),
    (802, "Marshadow", [], ["Fighting", "Ghost"], 7,
     "This Pok\u00e9mon can conceal itself in any shadow, so it went undiscovered for a long time.", True),
    (803, "Poipole", [], ["Poison"], 7,
     "An Ultra Beast that lives in a different world, it cackles wildly as it sprays its opponents with poison from the needles on its head.", False),
    (804, "Naganadel", [], ["Poison", "Dragon"], 7,
     "One kind of Ultra Beast, it fires a glowing, venomous liquid from its needles. This liquid is also immensely adhesive.", False),
    (805, "Stakataka", [], ["Rock", "Steel"], 7,
     "When stone walls started moving and attacking, the brute\u2019s true identity was this mysterious life-form, which brings to mind an Ultra Beast.", False),
    (806, "Blacephalon", [], ["Fire", "Ghost"], 7,
     "A UB that appeared from an Ultra Wormhole, it causes explosions, then takes advantage of opponents\u2019 surprise to rob them of their vitality.", False),
    (807, "Zeraora", [], ["Electric"], 7,
     "Electricity sparks from the pads on its limbs. Wherever this Pok\u00e9mon runs, lightning flashes and thunder echoes.", True),
    (808, "Meltan", [], ["Steel"], 7,
     "They live as a group, but when the time comes, one strong this Pok\u00e9mon will absorb all the others and evolve.", True),
    (809, "Melmetal", [], ["Steel"], 7,
     "Centrifugal force is behind the punches of this Pok\u00e9mon\u2019s heavy hex-nut arms. this Pok\u00e9mon is said to deliver the strongest punches of all Pok\u00e9mon.", True),
    # ── Gen 8 (Galar) ─────────────────────────────────────────────────────
    (810, "Grookey", [], ["Grass"], 8,
     "It attacks with rapid beats of its stick. As it strikes with amazing speed, it gets more and more pumped.", False),
    (811, "Thwackey", [], ["Grass"], 8,
     "When it\u2019s drumming out rapid beats in battle, it gets so caught up in the rhythm that it won\u2019t even notice that it\u2019s already knocked out its opponent.", False),
    (812, "Rillaboom", [], ["Grass"], 8,
     "The one with the best drumming techniques becomes the boss of the troop. It has a gentle disposition and values harmony among its group.", False),
    (813, "Scorbunny", [], ["Fire"], 8,
     "It has special pads on the backs of its feet, and one on its nose. Once it\u2019s raring to fight, these pads radiate tremendous heat.", False),
    (814, "Raboot", [], ["Fire"], 8,
     "It kicks berries right off the branches of trees and then juggles them with its feet, practicing its footwork.", False),
    (815, "Cinderace", [], ["Fire"], 8,
     "It\u2019s skilled at both offense and defense, and it gets pumped up when cheered on. But if it starts showboating, it could put itself in a tough spot.", False),
    (816, "Sobble", [], ["Water"], 8,
     "When it gets wet, its skin changes color, and this Pok\u00e9mon becomes invisible as if it were camouflaged.", False),
    (817, "Drizzile", [], ["Water"], 8,
     "Highly intelligent but also very lazy, it keeps enemies out of its territory by laying traps everywhere.", False),
    (818, "Inteleon", [], ["Water"], 8,
     "Its nictitating membranes let it pick out foes\u2019 weak points so it can precisely blast them with water that shoots from its fingertips at Mach 3.", False),
    (819, "Skwovet", [], ["Normal"], 8,
     "It eats berries nonstop\u2014a habit that has made it more resilient than it looks. It\u2019ll show up on farms, searching for yet more berries.", False),
    (820, "Greedent", [], ["Normal"], 8,
     "Common throughout the Galar region, this Pok\u00e9mon has strong teeth and can chew through the toughest of berry shells.", False),
    (821, "Rookidee", [], ["Flying"], 8,
     "Jumping nimbly about, this small-bodied Pok\u00e9mon takes advantage of even the slightest opportunity to disorient larger opponents.", False),
    (822, "Corvisquire", [], ["Flying"], 8,
     "The lessons of many harsh battles have taught it how to accurately judge an opponent\u2019s strength.", False),
    (823, "Corviknight", [], ["Flying", "Steel"], 8,
     "With their great intellect and flying skills, these Pok\u00e9mon very successfully act as the Galar region\u2019s airborne taxi service.", False),
    (824, "Blipbug", [], ["Bug"], 8,
     "Often found in gardens, this Pok\u00e9mon has hairs on its body that it uses to assess its surroundings.", False),
    (825, "Dottler", [], ["Bug", "Psychic"], 8,
     "As it grows inside its shell, it uses its psychic abilities to monitor the outside world and prepare for evolution.", False),
    (826, "Orbeetle", [], ["Bug", "Psychic"], 8,
     "It emits psychic energy to observe and study what\u2019s around it\u2014and what\u2019s around it can include things over six miles away.", False),
    (827, "Nickit", [], ["Dark"], 8,
     "Cunning and cautious, this Pok\u00e9mon survives by stealing food from others. It erases its tracks with swipes of its tail as it makes off with its plunder.", False),
    (828, "Thievul", [], ["Dark"], 8,
     "With a lithe body and sharp claws, it goes around stealing food and eggs. Boltund is its natural enemy.", False),
    (829, "Gossifleur", [], ["Grass"], 8,
     "It whirls around in the wind while singing a joyous song. This delightful display has charmed many into raising this Pok\u00e9mon.", False),
    (830, "Eldegoss", [], ["Grass"], 8,
     "The cotton on the head of this Pok\u00e9mon can be spun into a glossy, gorgeous yarn\u2014a Galar regional specialty.", False),
    (831, "Wooloo", [], ["Normal"], 8,
     "If its fleece grows too long, this Pok\u00e9mon won\u2019t be able to move. Cloth made with the wool of this Pok\u00e9mon is surprisingly strong.", False),
    (832, "Dubwool", [], ["Normal"], 8,
     "Its majestic horns are meant only to impress the opposite gender. They never see use in battle.", False),
    (833, "Chewtle", [], ["Water"], 8,
     "It starts off battles by attacking with its rock-hard horn, but as soon as the opponent flinches, this Pok\u00e9mon bites down and never lets go.", False),
    (834, "Drednaw", [], ["Water", "Rock"], 8,
     "This Pok\u00e9mon rapidly extends its retractable neck to sink its sharp fangs into distant enemies and take them down.", False),
    (835, "Yamper", [], ["Electric"], 8,
     "This gluttonous Pok\u00e9mon only assists people with their work because it wants treats. As it runs, it crackles with electricity.", False),
    (836, "Boltund", [], ["Electric"], 8,
     "It sends electricity through its legs to boost their strength. Running at top speed, it easily breaks 50 mph.", False),
    (837, "Rolycoly", [], ["Rock"], 8,
     "It can race around like a unicycle, even on rough, rocky terrain. Burning coal sustains it.", False),
    (838, "Carkol", [], ["Rock", "Fire"], 8,
     "By rapidly rolling its legs, it can travel at over 18 mph. The temperature of the flames it breathes exceeds 1,800 degrees Fahrenheit.", False),
    (839, "Coalossal", [], ["Rock", "Fire"], 8,
     "While it\u2019s engaged in battle, its mountain of coal will burn bright red, sending off sparks that scorch the surrounding area.", False),
    (840, "Applin", [], ["Grass", "Dragon"], 8,
     "As soon as it\u2019s born, it burrows into an apple. Not only does the apple serve as its food source, but the flavor of the fruit determines its evolution.", False),
    (841, "Flapple", [], ["Grass", "Dragon"], 8,
     "It flies on wings of apple skin and spits a powerful acid. It can also change its shape into that of an apple.", False),
    (842, "Appletun", [], ["Grass", "Dragon"], 8,
     "Its body is covered in sweet nectar, and the skin on its back is especially yummy. Children used to have it as a snack.", False),
    (843, "Silicobra", [], ["Ground"], 8,
     "It spews sand from its nostrils. While the enemy is blinded, it burrows into the ground to hide.", False),
    (844, "Sandaconda", [], ["Ground"], 8,
     "Its unique style of coiling allows it to blast sand out of its sand sac more efficiently.", False),
    (845, "Cramorant", [], ["Flying", "Water"], 8,
     "This hungry Pok\u00e9mon swallows Arrokuda whole. Occasionally, it makes a mistake and tries to swallow a Pok\u00e9mon other than its preferred prey.", False),
    (846, "Arrokuda", [], ["Water"], 8,
     "After it\u2019s eaten its fill, its movements become extremely sluggish. That\u2019s when Cramorant swallows it up.", False),
    (847, "Barraskewda", [], ["Water"], 8,
     "It spins its tail fins to propel itself, surging forward at speeds of over 100 knots before ramming prey and spearing into them.", False),
    (848, "Toxel", [], ["Electric", "Poison"], 8,
     "It manipulates the chemical makeup of its poison to produce electricity. The voltage is weak, but it can cause a tingling paralysis.", False),
    (849, "Toxtricity", [], ["Electric", "Poison"], 8,
     "This short-tempered and aggressive Pok\u00e9mon chugs stagnant water to absorb any toxins it might contain.", False),
    (850, "Sizzlipede", [], ["Fire", "Bug"], 8,
     "It wraps prey up with its heated body, cooking them in its coils. Once they\u2019re well-done, it will voraciously nibble them down to the last morsel.", False),
    (851, "Centiskorch", [], ["Fire", "Bug"], 8,
     "While its burning body is already dangerous on its own, this excessively hostile Pok\u00e9mon also has large and very sharp fangs.", False),
    (852, "Clobbopus", [], ["Fighting"], 8,
     "Its tentacles tear off easily, but it isn\u2019t alarmed when that happens\u2014it knows they\u2019ll grow back. It\u2019s about as smart as a three-year-old.", False),
    (853, "Grapploct", [], ["Fighting"], 8,
     "Searching for an opponent to test its skills against, it emerges onto land. Once the battle is over, it returns to the sea.", False),
    (854, "Sinistea", [], ["Ghost"], 8,
     "The teacup in which this Pok\u00e9mon makes its home is a famous piece of antique tableware. Many forgeries are in circulation.", False),
    (855, "Polteageist", [], ["Ghost"], 8,
     "Leaving leftover black tea unattended is asking for this Pok\u00e9mon to come along and pour itself into it, turning the tea into a new this Pok\u00e9mon.", False),
    (856, "Hatenna", [], ["Psychic"], 8,
     "If this Pok\u00e9mon senses a strong emotion, it will run away as fast as it can. It prefers areas without people.", False),
    (857, "Hattrem", [], ["Psychic"], 8,
     "Using the braids on its head, it pummels foes to get them to quiet down. One blow from those braids would knock out a professional boxer.", False),
    (858, "Hatterene", [], ["Psychic", "Fairy"], 8,
     "If you\u2019re too loud around it, you risk being torn apart by the claws on its tentacle. This Pok\u00e9mon is also known as the Forest Witch.", False),
    (859, "Impidimp", [], ["Dark", "Fairy"], 8,
     "It sneaks into people\u2019s homes, stealing things and feasting on the negative energy of the frustrated occupants.", False),
    (860, "Morgrem", [], ["Dark", "Fairy"], 8,
     "With sly cunning, it tries to lure people into the woods. Some believe it to have the power to make crops grow.", False),
    (861, "Grimmsnarl", [], ["Dark", "Fairy"], 8,
     "Its hairs work like muscle fibers. When its hairs unfurl, they latch on to opponents, ensnaring them as tentacles would.", False),
    (862, "Obstagoon", [], ["Dark", "Normal"], 8,
     "It evolved after experiencing numerous fights. While crossing its arms, it lets out a shout that would make any opponent flinch.", False),
    (863, "Perrserker", [], ["Steel"], 8,
     "After many battles, it evolved dangerous claws that come together to form daggers when extended.", False),
    (864, "Cursola", [], ["Ghost"], 8,
     "Be cautious of the ectoplasmic body surrounding its soul. You\u2019ll become stiff as stone if you touch it.", False),
    (865, "Sirfetch\u2019d", [], ["Fighting"], 8,
     "After deflecting attacks with its hard leaf shield, it strikes back with its sharp leek stalk. The leek stalk is both weapon and food.", False),
    (866, "Mr. Rime", [], ["Ice", "Psychic"], 8,
     "Its amusing movements make it very popular. It releases its psychic power from the pattern on its belly.", False),
    (867, "Runerigus", [], ["Ground", "Ghost"], 8,
     "Never touch its shadowlike body, or you\u2019ll be shown the horrific memories behind the picture carved into it.", False),
    (868, "Milcery", [], ["Fairy"], 8,
     "They say that any patisserie visited by this Pok\u00e9mon is guaranteed success and good fortune.", False),
    (869, "Alcremie", [], ["Fairy"], 8,
     "When this Pok\u00e9mon is content, the cream it secretes from its hands becomes sweeter and richer.", False),
    (870, "Falinks", [], ["Fighting"], 8,
     "The six of them work together as one Pok\u00e9mon. Teamwork is also their battle strategy, and they constantly change their formation as they fight.", False),
    (871, "Pincurchin", [], ["Electric"], 8,
     "It stores electricity in each spine. Even if one gets broken off, it still continues to emit electricity for at least three hours.", False),
    (872, "Snom", [], ["Ice", "Bug"], 8,
     "It eats snow that piles up on the ground. The more snow it eats, the bigger and more impressive the spikes on its back grow.", False),
    (873, "Frosmoth", [], ["Ice", "Bug"], 8,
     "It shows no mercy to any who desecrate fields and mountains. It will fly around on its icy wings, causing a blizzard to chase offenders away.", False),
    (874, "Stonjourner", [], ["Rock"], 8,
     "Once a year, on a specific date and at a specific time, they gather out of nowhere and form up in a circle.", False),
    (875, "Eiscue", [], ["Ice"], 8,
     "This Pok\u00e9mon keeps its heat-sensitive head cool with ice. It fishes for its food, dangling its single hair into the sea to lure in prey.", False),
    (876, "Indeedee", [], ["Psychic", "Normal"], 8,
     "Through its horns, it can pick up on the emotions of creatures around it. Positive emotions are the source of its strength.", False),
    (877, "Morpeko", [], ["Electric", "Dark"], 8,
     "It carries electrically roasted seeds with it as if they\u2019re precious treasures. No matter how much it eats, it always gets hungry again in short order.", False),
    (878, "Cufant", [], ["Steel"], 8,
     "If a job requires serious strength, this Pok\u00e9mon will excel at it. Its copper body tarnishes in the rain, turning a vibrant green color.", False),
    (879, "Copperajah", [], ["Steel"], 8,
     "These Pok\u00e9mon live in herds. Their trunks have incredible grip strength, strong enough to crush giant rocks into powder.", False),
    (880, "Dracozolt", [], ["Electric", "Dragon"], 8,
     "The powerful muscles in its tail generate its electricity. Compared to its lower body, its upper half is entirely too small.", False),
    (881, "Arctozolt", [], ["Electric", "Ice"], 8,
     "This Pok\u00e9mon lived on prehistoric seashores and was able to preserve food with the ice on its body. It went extinct because it moved so slowly.", False),
    (882, "Dracovish", [], ["Water", "Dragon"], 8,
     "Its mighty legs are capable of running at speeds exceeding 40 mph, but this Pok\u00e9mon can\u2019t breathe unless it\u2019s underwater.", False),
    (883, "Arctovish", [], ["Water", "Ice"], 8,
     "The skin on its face is impervious to attack, but breathing difficulties made this Pok\u00e9mon go extinct anyway.", False),
    (884, "Duraludon", [], ["Steel", "Dragon"], 8,
     "The special metal that composes its body is very light, so this Pok\u00e9mon has considerable agility. It lives in caves because it dislikes the rain.", False),
    (885, "Dreepy", [], ["Dragon", "Ghost"], 8,
     "If this weak Pok\u00e9mon is by itself, a mere child could defeat it. But if this Pok\u00e9mon has friends to help it train, it can evolve and become much stronger.", False),
    (886, "Drakloak", [], ["Dragon", "Ghost"], 8,
     "Without a Dreepy to place on its head and care for, it gets so uneasy it\u2019ll try to substitute any Pok\u00e9mon it finds for the missing Dreepy.", False),
    (887, "Dragapult", [], ["Dragon", "Ghost"], 8,
     "Apparently the Dreepy inside this Pok\u00e9mon\u2019s horns eagerly look forward to being launched out at Mach speeds.", False),
    (888, "Zacian", [], ["Fairy"], 8,
     "This Pok\u00e9mon has slumbered for many years. Some say it\u2019s Zamazenta\u2019s elder sister\u2014others say the two Pok\u00e9mon are rivals.", True),
    (889, "Zamazenta", [], ["Fighting"], 8,
     "This Pok\u00e9mon slept for aeons while in the form of a statue. It was asleep for so long, people forgot that it ever existed.", True),
    (890, "Eternatus", [], ["Poison", "Dragon"], 8,
     "It was inside a meteorite that fell 20,000 years ago. There seems to be a connection between this Pok\u00e9mon and the Dynamax phenomenon.", True),
    (891, "Kubfu", [], ["Fighting"], 8,
     "If this Pok\u00e9mon pulls the long white hair on its head, its fighting spirit heightens and power wells up from the depths of its belly.", True),
    (892, "Urshifu", [], ["Fighting", "Dark"], 8,
     "Inhabiting the mountains of a distant region, this Pok\u00e9mon races across sheer cliffs, training its legs and refining its moves.", True),
    (893, "Zarude", [], ["Dark", "Grass"], 8,
     "Once the vines on this Pok\u00e9mon\u2019s body tear off, they become nutrients in the soil. This helps the plants of the forest grow.", True),
    (894, "Regieleki", [], ["Electric"], 8,
     "Its entire body is made up of a single organ that generates electrical energy. this Pok\u00e9mon is capable of creating all Galar\u2019s electricity.", True),
    (895, "Regidrago", [], ["Dragon"], 8,
     "Its body is composed of crystallized dragon energy. this Pok\u00e9mon is said to have the powers of every dragon Pok\u00e9mon.", True),
    (896, "Glastrier", [], ["Ice"], 8,
     "this Pok\u00e9mon has tremendous physical strength, and the mask of ice covering its face is 100 times harder than diamond.", True),
    (897, "Spectrier", [], ["Ghost"], 8,
     "As it dashes through the night, this Pok\u00e9mon absorbs the life-force of sleeping creatures. It craves silence and solitude.", True),
    (898, "Calyrex", [], ["Psychic", "Grass"], 8,
     "this Pok\u00e9mon is known in legend as a king that ruled over Galar in ancient times. It has the power to cause hearts to mend and plants to spring forth.", True),
    (899, "Wyrdeer", [], ["Normal", "Psychic"], 8,
     "The black orbs shine with an uncanny light when the Pok\u00e9mon is erecting invisible barriers. The fur shed from its beard retains heat well and is a highly useful material for winter clothing.", False),
    (900, "Kleavor", [], ["Bug", "Rock"], 8,
     "A violent creature that fells towering trees with its crude axes and shields itself with hard stone. If one should chance upon this Pok\u00e9mon in the wilds, one's only recourse is to flee.", False),
    (901, "Ursaluna", [], ["Ground", "Normal"], 8,
     "I believe it was Hisui's swampy terrain that gave this Pok\u00e9mon its burly physique and newfound capacity to manipulate peat at will.", False),
    (902, "Basculegion", [], ["Water", "Ghost"], 8,
     "Clads itself in the souls of comrades that perished before fulfilling their goals of journeying upstream. No other species throughout all Hisui's rivers is this Pok\u00e9mon's equal.", False),
    (903, "Sneasler", [], ["Fighting", "Poison"], 8,
     "Because of this Pok\u00e9mon's virulent poison and daunting physical prowess, no other species could hope to best it on the frozen highlands. Preferring solitude, this species does not form packs.", False),
    (904, "Overqwil", [], ["Dark", "Poison"], 8,
     "Its lancelike spikes and savage temperament have earned it the nickname \u201dsea fiend.\u201d It slurps up poison to nourish itself.", False),
    (905, "Enamorus", [], ["Fairy", "Flying"], 8,
     "When it flies to this land from across the sea, the bitter winter comes to an end. According to legend, this Pok\u00e9mon's love gives rise to the budding of fresh life across Hisui.", True),
    # ── Gen 9 (Paldea) ────────────────────────────────────────────────────
    (906, "Sprigatito", [], ["Grass"], 9,
     "The sweet scent its body gives off mesmerizes those around it. The scent grows stronger when this Pok\u00e9mon is in the sun.", False),
    (907, "Floragato", [], ["Grass"], 9,
     "The hardness of this Pok\u00e9mon\u2019s fur depends on the Pok\u00e9mon\u2019s mood. When this Pok\u00e9mon is prepared to battle, its fur becomes pointed and needle sharp.", False),
    (908, "Meowscarada", [], ["Grass", "Dark"], 9,
     "With skillful misdirection, it rigs foes with pollen-packed flower bombs. this Pok\u00e9mon sets off the bombs before its foes realize what\u2019s going on.", False),
    (909, "Fuecoco", [], ["Fire"], 9,
     "Its flame sac is small, so energy is always leaking out. This energy is released from the dent atop this Pok\u00e9mon\u2019s head and flickers to and fro.", False),
    (910, "Crocalor", [], ["Fire"], 9,
     "The valve in this Pok\u00e9mon\u2019s flame sac is closely connected to its vocal cords. This Pok\u00e9mon utters a guttural cry as it spews flames every which way.", False),
    (911, "Skeledirge", [], ["Fire", "Ghost"], 9,
     "this Pok\u00e9mon\u2019s gentle singing soothes the souls of all that hear it. It burns its enemies to a crisp with flames of over 5,400 degrees Fahrenheit.", False),
    (912, "Quaxly", [], ["Water"], 9,
     "Its strong legs let it easily swim around in even fast-flowing rivers. It likes to keep things tidy and is prone to overthinking things.", False),
    (913, "Quaxwell", [], ["Water"], 9,
     "The hardworking this Pok\u00e9mon observes people and Pok\u00e9mon from various regions and incorporates their movements into its own dance routines.", False),
    (914, "Quaquaval", [], ["Water", "Fighting"], 9,
     "Dancing in ways that evoke far-away places, this Pok\u00e9mon mesmerizes all that see it. Flourishes of its decorative water feathers slice into its foes.", False),
    (915, "Lechonk", [], ["Normal"], 9,
     "This Pok\u00e9mon spurns all but the finest of foods. Its body gives off an herblike scent that bug Pok\u00e9mon detest.", False),
    (916, "Oinkologne", [], ["Normal"], 9,
     "It entrances female Pok\u00e9mon with the sweet, alluring scent that wafts from all over its body.", False),
    (917, "Tarountula", [], ["Bug"], 9,
     "The thread it secretes from its rear is as strong as wire. The secret behind the thread\u2019s strength is the topic of ongoing research.", False),
    (918, "Spidops", [], ["Bug"], 9,
     "this Pok\u00e9mon covers its territory in tough, sticky threads to set up traps for intruders.", False),
    (919, "Nymble", [], ["Bug"], 9,
     "It\u2019s highly skilled at a fighting style in which it uses its jumping capabilities to dodge incoming attacks while also dealing damage to opponents.", False),
    (920, "Lokix", [], ["Bug", "Dark"], 9,
     "It uses its normally folded third set of legs when in Showdown Mode. This places a huge burden on its body, so it can\u2019t stay in this mode for long.", False),
    (921, "Pawmi", [], ["Electric"], 9,
     "The pads of its paws are electricity-discharging organs. this Pok\u00e9mon fires electricity from its forepaws while standing unsteadily on its hind legs.", False),
    (922, "Pawmo", [], ["Electric", "Fighting"], 9,
     "this Pok\u00e9mon uses a unique fighting technique in which it uses its forepaws to strike foes and zap them with electricity from its paw pads simultaneously.", False),
    (923, "Pawmot", [], ["Electric", "Fighting"], 9,
     "this Pok\u00e9mon\u2019s fluffy fur acts as a battery. It can store the same amount of electricity as an electric car.", False),
    (924, "Tandemaus", [], ["Normal"], 9,
     "The pair sticks together no matter what. They split any food they find exactly in half and then eat it together.", False),
    (925, "Maushold", [], ["Normal"], 9,
     "The larger pair protects the little ones during battles. When facing strong opponents, the whole group will join the fight.", False),
    (926, "Fidough", [], ["Fairy"], 9,
     "The yeast in this Pok\u00e9mon\u2019s breath is useful for cooking, so this Pok\u00e9mon has been protected by people since long ago.", False),
    (927, "Dachsbun", [], ["Fairy"], 9,
     "The surface of this Pok\u00e9mon\u2019s skin hardens when exposed to intense heat, and its body has an appetizing aroma.", False),
    (928, "Smoliv", [], ["Grass", "Normal"], 9,
     "This Pok\u00e9mon converts nutrients into oil, which it stores in the fruit on its head. It can easily go a whole week without eating or drinking.", False),
    (929, "Dolliv", [], ["Grass", "Normal"], 9,
     "It basks in the sun to its heart\u2019s content until the fruits on its head ripen. After that, this Pok\u00e9mon departs from human settlements and goes on a journey.", False),
    (930, "Arboliva", [], ["Grass", "Normal"], 9,
     "This Pok\u00e9mon drives back enemies by launching its rich, aromatic oil at them with enough force to smash a boulder.", False),
    (931, "Squawkabilly", [], ["Normal", "Flying"], 9,
     "Green-feathered flocks hold the most sway. When they\u2019re out searching for food in the mornings and evenings, it gets very noisy.", False),
    (932, "Nacli", [], ["Rock"], 9,
     "The ground scrapes its body as it travels, causing it to leave salt behind. Salt is constantly being created and replenished inside this Pok\u00e9mon\u2019s body.", False),
    (933, "Naclstack", [], ["Rock"], 9,
     "It compresses rock salt inside its body and shoots out hardened salt pellets with enough force to perforate an iron sheet.", False),
    (934, "Garganacl", [], ["Rock"], 9,
     "Many Pok\u00e9mon gather around this Pok\u00e9mon, hoping to lick at its mineral-rich salt.", False),
    (935, "Charcadet", [], ["Fire"], 9,
     "Its firepower increases when it fights, reaching over 1,800 degrees Fahrenheit. It likes berries that are rich in fat.", False),
    (936, "Armarouge", [], ["Fire", "Psychic"], 9,
     "This Pok\u00e9mon clads itself in armor that has been fortified by psychic and fire energy, and it shoots blazing fireballs.", False),
    (937, "Ceruledge", [], ["Fire", "Ghost"], 9,
     "An old set of armor steeped in grudges caused this Pok\u00e9mon\u2019s evolution. this Pok\u00e9mon cuts its enemies to pieces without mercy.", False),
    (938, "Tadbulb", [], ["Electric"], 9,
     "It floats using the electricity stored in its body. When thunderclouds are around, this Pok\u00e9mon will float higher off the ground.", False),
    (939, "Bellibolt", [], ["Electric"], 9,
     "What appear to be eyeballs are actually organs for discharging the electricity generated by this Pok\u00e9mon\u2019s belly-button dynamo.", False),
    (940, "Wattrel", [], ["Electric", "Flying"], 9,
     "These Pok\u00e9mon make their nests on coastal cliffs. The nests have a strange, crackling texture, and they\u2019re a popular delicacy.", False),
    (941, "Kilowattrel", [], ["Electric", "Flying"], 9,
     "It uses its throat sac to store electricity generated by its wings. There\u2019s hardly any oil in its feathers, so it is a poor swimmer.", False),
    (942, "Maschiff", [], ["Dark"], 9,
     "Its well-developed jaw and fangs are strong enough to crunch through boulders, and its thick fat makes for an excellent defense.", False),
    (943, "Mabosstiff", [], ["Dark"], 9,
     "this Pok\u00e9mon loves playing with children. Though usually gentle, it takes on an intimidating look when protecting its family.", False),
    (944, "Shroodle", [], ["Poison", "Normal"], 9,
     "To keep enemies away from its territory, it paints markings around its nest using a poisonous liquid that has an acrid odor.", False),
    (945, "Grafaiai", [], ["Poison", "Normal"], 9,
     "Each this Pok\u00e9mon paints its own individual pattern, and it will paint that same pattern over and over again throughout its life.", False),
    (946, "Bramblin", [], ["Grass", "Ghost"], 9,
     "Not even this Pok\u00e9mon knows where it is headed as it tumbles across the wilderness, blown by the wind. It loathes getting wet.", False),
    (947, "Brambleghast", [], ["Grass", "Ghost"], 9,
     "this Pok\u00e9mon wanders around arid regions. On rare occasions, mass outbreaks of these Pok\u00e9mon will bury an entire town.", False),
    (948, "Toedscool", [], ["Ground", "Grass"], 9,
     "Though it looks like Tentacool, this Pok\u00e9mon is a completely different species. Its legs may be thin, but it can run at a speed of 30 mph.", False),
    (949, "Toedscruel", [], ["Ground", "Grass"], 9,
     "It coils its 10 tentacles around prey and sucks out their nutrients, causing the prey pain. The folds along the rim of its head are a popular delicacy.", False),
    (950, "Klawf", [], ["Rock"], 9,
     "This Pok\u00e9mon lives on sheer cliffs. It sidesteps opponents\u2019 attacks, then lunges for their weak spots with its claws.", False),
    (951, "Capsakid", [], ["Grass"], 9,
     "Traditional Paldean dishes can be extremely spicy because they include the shed front teeth of this Pok\u00e9mon among their ingredients.", False),
    (952, "Scovillain", [], ["Grass", "Fire"], 9,
     "The green head has turned vicious due to the spicy chemicals stimulating its brain. Once it goes on a rampage, there is no stopping it.", False),
    (953, "Rellor", [], ["Bug"], 9,
     "It rolls its mud ball around while the energy it needs for evolution matures. Eventually the time comes for it to evolve.", False),
    (954, "Rabsca", [], ["Bug", "Psychic"], 9,
     "An infant sleeps inside the ball. this Pok\u00e9mon rolls the ball soothingly with its legs to ensure the infant sleeps comfortably.", False),
    (955, "Flittle", [], ["Psychic"], 9,
     "It spends its time running around wastelands. If anyone steals its beloved berries, it will chase them down and exact its revenge.", False),
    (956, "Espathra", [], ["Psychic"], 9,
     "It emits psychic power from the gaps between its multicolored frills and sprints at speeds greater than 120 mph.", False),
    (957, "Tinkatink", [], ["Fairy", "Steel"], 9,
     "This Pok\u00e9mon pounds iron scraps together to make a hammer. It will remake the hammer again and again until it\u2019s satisfied with the result.", False),
    (958, "Tinkatuff", [], ["Fairy", "Steel"], 9,
     "These Pok\u00e9mon make their homes in piles of scrap metal. They test the strength of each other\u2019s hammers by smashing them together.", False),
    (959, "Tinkaton", [], ["Fairy", "Steel"], 9,
     "The hammer tops 220 pounds, yet it gets swung around easily by this Pok\u00e9mon as it steals whatever it pleases and carries its plunder back home.", False),
    (960, "Wiglett", [], ["Water"], 9,
     "Though it looks like Diglett, this Pok\u00e9mon is an entirely different species. The resemblance seems to be a coincidental result of environmental adaptation.", False),
    (961, "Wugtrio", [], ["Water"], 9,
     "A variety of fish Pok\u00e9mon, this Pok\u00e9mon was once considered to be a regional form of Dugtrio.", False),
    (962, "Bombirdier", [], ["Flying", "Dark"], 9,
     "this Pok\u00e9mon uses the apron on its chest to bundle up food, which it carries back to its nest. It enjoys dropping things that make loud noises.", False),
    (963, "Finizen", [], ["Water"], 9,
     "Its water ring is made from seawater mixed with a sticky fluid that this Pok\u00e9mon secretes from its blowhole.", False),
    (964, "Palafin", [], ["Water"], 9,
     "Its physical capabilities are no different than a Finizen\u2019s, but when its allies are in danger, it transforms and powers itself up.", False),
    (965, "Varoom", [], ["Steel", "Poison"], 9,
     "The steel section is this Pok\u00e9mon\u2019s actual body. This Pok\u00e9mon clings to rocks and converts the minerals within into energy to fuel its activities.", False),
    (966, "Revavroom", [], ["Steel", "Poison"], 9,
     "this Pok\u00e9mon viciously threatens others with the sound of its exhaust. It sticks its tongue out from its cylindrical mouth and sprays toxic fluids.", False),
    (967, "Cyclizar", [], ["Dragon", "Normal"], 9,
     "It can sprint at over 70 mph while carrying a human. The rider\u2019s body heat warms this Pok\u00e9mon\u2019s back and lifts the Pok\u00e9mon\u2019s spirit.", False),
    (968, "Orthworm", [], ["Steel"], 9,
     "This Pok\u00e9mon lives in arid deserts. It maintains its metal body by consuming iron from the soil.", False),
    (969, "Glimmet", [], ["Rock", "Poison"], 9,
     "this Pok\u00e9mon\u2019s toxic mineral crystals look just like flower petals. This Pok\u00e9mon scatters poisonous powder like pollen to protect itself.", False),
    (970, "Glimmora", [], ["Rock", "Poison"], 9,
     "this Pok\u00e9mon\u2019s petals are made of crystallized poison energy. It has recently become evident that these petals resemble Tera Jewels.", False),
    (971, "Greavard", [], ["Ghost"], 9,
     "This friendly Pok\u00e9mon doesn\u2019t like being alone. Pay it even the slightest bit of attention, and it will follow you forever.", False),
    (972, "Houndstone", [], ["Ghost"], 9,
     "A lovingly mourned Pok\u00e9mon was reborn as this Pok\u00e9mon. It doesn\u2019t like anyone touching the protuberance atop its head.", False),
    (973, "Flamigo", [], ["Flying", "Fighting"], 9,
     "Thanks to a behavior of theirs known as \u201csynchronizing,\u201d an entire flock of these Pok\u00e9mon can attack simultaneously in perfect harmony.", False),
    (974, "Cetoddle", [], ["Ice"], 9,
     "It lives in frigid regions in pods of five or so individuals. It loves the minerals found in snow and ice.", False),
    (975, "Cetitan", [], ["Ice"], 9,
     "Ice energy builds up in the horn on its upper jaw, causing the horn to reach cryogenic temperatures that freeze its surroundings.", False),
    (976, "Veluza", [], ["Water", "Psychic"], 9,
     "this Pok\u00e9mon has excellent regenerative capabilities. It sheds spare flesh from its body to boost its agility, then charges at its prey.", False),
    (977, "Dondozo", [], ["Water"], 9,
     "It treats Tatsugiri like its boss and follows it loyally. Though powerful, this Pok\u00e9mon is apparently not very smart.", False),
    (978, "Tatsugiri", [], ["Dragon", "Water"], 9,
     "this Pok\u00e9mon is an extremely cunning Pok\u00e9mon. It feigns weakness to lure in prey, then orders its partner to attack.", False),
    (979, "Annihilape", [], ["Fighting", "Ghost"], 9,
     "It imbues its fists with the power of the rage that it kept hidden in its heart. Opponents struck by these imbued fists will be shattered to their core.", False),
    (980, "Clodsire", [], ["Poison", "Ground"], 9,
     "It lives at the bottom of ponds and swamps. It will carry Wooper on its back and ferry them across water from one shore to the other.", False),
    (981, "Farigiraf", [], ["Normal", "Psychic"], 9,
     "The hardened head from the tail protects the head of the main body as this Pok\u00e9mon whips its long neck around to headbutt enemies.", False),
    (982, "Dudunsparce", [], ["Normal"], 9,
     "It drives enemies out of its nest by sucking in enough air to fill its long, narrow lungs, then releasing the air in an intense blast.", False),
    (983, "Kingambit", [], ["Dark", "Steel"], 9,
     "Though it commands a massive army in battle, it\u2019s not skilled at devising complex strategies. It just uses brute strength to keep pushing.", False),
    (984, "Great Tusk", [], ["Ground", "Fighting"], 9,
     "This creature resembles a mysterious Pok\u00e9mon that, according to a paranormal magazine, has lived since ancient times.", False),
    (985, "Scream Tail", [], ["Fairy", "Psychic"], 9,
     "It resembles a mysterious Pok\u00e9mon described in a paranormal magazine as a Jigglypuff from one billion years ago.", False),
    (986, "Brute Bonnet", [], ["Grass", "Dark"], 9,
     "It bears a slight resemblance to a Pok\u00e9mon described in a dubious magazine as a cross between a dinosaur and a mushroom.", False),
    (987, "Flutter Mane", [], ["Ghost", "Fairy"], 9,
     "It has similar features to a ghostly pterosaur that was covered in a paranormal magazine, but the two have little else in common.", False),
    (988, "Slither Wing", [], ["Bug", "Fighting"], 9,
     "This Pok\u00e9mon somewhat resembles an ancient form of Volcarona that was introduced in a dubious magazine.", False),
    (989, "Sandy Shocks", [], ["Electric", "Ground"], 9,
     "It slightly resembles a Magneton that lived for 10,000 years and was featured in an article in a paranormal magazine.", False),
    (990, "Iron Treads", [], ["Ground", "Steel"], 9,
     "Sightings of this Pok\u00e9mon have occurred in recent years. It resembles a mysterious object described in an old expedition journal.", False),
    (991, "Iron Bundle", [], ["Ice", "Water"], 9,
     "It resembles a mysterious object mentioned in an old book. There are only two reported sightings of this Pok\u00e9mon.", False),
    (992, "Iron Hands", [], ["Fighting", "Electric"], 9,
     "This Pok\u00e9mon shares many similarities with this Pok\u00e9mon, an object mentioned in a certain expedition journal.", False),
    (993, "Iron Jugulis", [], ["Dark", "Flying"], 9,
     "It\u2019s possible that this Pok\u00e9mon, an object described in an old book, may actually be this Pok\u00e9mon.", False),
    (994, "Iron Moth", [], ["Fire", "Poison"], 9,
     "No records exist of this species being caught. Data is lacking, but the Pok\u00e9mon\u2019s traits match up with an object described in an old book.", False),
    (995, "Iron Thorns", [], ["Rock", "Electric"], 9,
     "Some of its notable features match those of an object named within a certain expedition journal as this Pok\u00e9mon.", False),
    (996, "Frigibax", [], ["Dragon", "Ice"], 9,
     "This Pok\u00e9mon lives in forests and craggy areas. Using the power of its dorsal fin, it cools the inside of its nest like a refrigerator.", False),
    (997, "Arctibax", [], ["Dragon", "Ice"], 9,
     "It attacks with the blade of its frozen dorsal fin by doing a front flip in the air. this Pok\u00e9mon\u2019s strong back and legs allow it to pull off this technique.", False),
    (998, "Baxcalibur", [], ["Dragon", "Ice"], 9,
     "It launches itself into battle by flipping upside down and spewing frigid air from its mouth. It finishes opponents off with its dorsal blade.", False),
    (999, "Gimmighoul", [], ["Ghost"], 9,
     "It lives inside an old treasure chest. Sometimes it gets left in shop corners since no one realizes it\u2019s actually a Pok\u00e9mon.", False),
    (1000, "Gholdengo", [], ["Steel", "Ghost"], 9,
     "It has a sturdy body made up of stacked coins. this Pok\u00e9mon overwhelms its enemies by firing coin after coin at them in quick succession.", False),
    (1001, "Wo-Chien", [], ["Dark", "Grass"], 9,
     "It drains the life-force from vegetation, causing nearby forests to instantly wither and fields to turn barren.", True),
    (1002, "Chien-Pao", [], ["Dark", "Ice"], 9,
     "The hatred of those who perished by the sword long ago has clad itself in snow and become a Pok\u00e9mon.", True),
    (1003, "Ting-Lu", [], ["Dark", "Ground"], 9,
     "It slowly brings its exceedingly heavy head down upon the ground, splitting the earth open with huge fissures that run over 160 feet deep.", True),
    (1004, "Chi-Yu", [], ["Dark", "Fire"], 9,
     "The envy accumulated within curved beads that sparked multiple conflicts has clad itself in fire and become a Pok\u00e9mon.", True),
    (1005, "Roaring Moon", [], ["Dragon", "Dark"], 9,
     "According to an article in a dubious magazine, this Pok\u00e9mon has some connection to a phenomenon that occurs in a certain region.", False),
    (1006, "Iron Valiant", [], ["Fairy", "Fighting"], 9,
     "It\u2019s possible that this is the object listed as this Pok\u00e9mon in a certain expedition journal.", False),
    (1007, "Koraidon", [], ["Fighting", "Dragon"], 9,
     "This Pok\u00e9mon resembles Cyclizar, but it is far burlier and more ferocious. Nothing is known about its ecology or other features.", True),
    (1008, "Miraidon", [], ["Electric", "Dragon"], 9,
     "This seems to be the Iron Serpent mentioned in an old book. The Iron Serpent is said to have turned the land to ash with its lightning.", True),
    (1009, "Walking Wake", [], ["Water", "Dragon"], 9,
     "It resembles an illustration published in a paranormal magazine, said to be a depiction of a super-ancient Suicune.", False),
    (1010, "Iron Leaves", [], ["Grass", "Psychic"], 9,
     "According to the few eyewitness accounts that exist, it used its shining blades to julienne large trees and boulders.", False),
    (1011, "Dipplin", [], ["Grass", "Dragon"], 9,
     "The head sticking out belongs to the fore-wyrm, while the tail belongs to the core-wyrm. The two share one apple and help each other out.", False),
    (1012, "Poltchageist", [], ["Grass", "Ghost"], 9,
     "this Pok\u00e9mon looks like a regional form of Sinistea, but it was recently discovered that the two Pok\u00e9mon are entirely unrelated.", False),
    (1013, "Sinistcha", [], ["Grass", "Ghost"], 9,
     "It prefers cool, dark places, such as the back of a shelf or the space beneath a home's floorboards. It wanders in search of prey after sunset.", False),
    (1014, "Okidogi", [], ["Poison", "Fighting"], 9,
     "this Pok\u00e9mon is a ruffian with a short temper. It can pulverize anything by swinging around the chain on its neck.", True),
    (1015, "Munkidori", [], ["Poison", "Psychic"], 9,
     "this Pok\u00e9mon keeps itself somewhere safe while it toys with its foes, using psychokinesis to induce intense dizziness.", True),
    (1016, "Fezandipiti", [], ["Poison", "Fairy"], 9,
     "this Pok\u00e9mon beats its glossy wings to scatter pheromones that captivate people and Pok\u00e9mon.", True),
    (1017, "Ogerpon", [], ["Grass"], 9,
     "This mischief-loving Pok\u00e9mon is full of curiosity. It battles by drawing out the type-based energy contained within its masks.", True),
    (1018, "Archaludon", [], ["Steel", "Dragon"], 9,
     "It digs holes on mountains, searching for food. It\u2019s so durable that being caught in a cave-in won\u2019t faze it.", False),
    (1019, "Hydrapple", [], ["Grass", "Dragon"], 9,
     "These capricious syrpents have banded together. On the rare occasion that their moods align, their true power is unleashed.", False),
    (1020, "Gouging Fire", [], ["Fire", "Dragon"], 9,
     "It resembles an eerie Pok\u00e9mon once shown in a paranormal magazine. That Pok\u00e9mon was said to be an Entei regenerated from a fossil.", False),
    (1021, "Raging Bolt", [], ["Electric", "Dragon"], 9,
     "It bears resemblance to a Pok\u00e9mon that became a hot topic for a short while after a paranormal magazine touted it as Raikou's ancestor.", False),
    (1022, "Iron Boulder", [], ["Rock", "Psychic"], 9,
     "It was named after a mysterious object recorded in an old book. Its body seems to be metallic.", False),
    (1023, "Iron Crown", [], ["Steel", "Psychic"], 9,
     "There was supposedly an incident in which it launched shining blades to cut everything around it to pieces. Little else is known about it.", False),
    (1024, "Terapagos", [], ["Normal"], 9,
     "It\u2019s thought that this Pok\u00e9mon lived in ancient Paldea until it got caught in seismic shifts and went extinct.", True),
    (1025, "Pecharunt", [], ["Poison", "Ghost"], 9,
     "Its peach-shaped shell serves as storage for a potent poison. It makes poisonous mochi and serves them to people and Pok\u00e9mon.", True),
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


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class PokePlayer:
    user_id: int
    display_name: str
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
    thread: discord.Thread | None = field(default=None, repr=False)
    round_solved: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    total_rounds_played: int = 0
    used_ids: set[int] = field(default_factory=set)
    round_messages: list[discord.Message] = field(default_factory=list)
    stop_requested: bool = False


@dataclass
class SoloSession:
    user_id: int
    channel_id: int
    display_name: str
    category: str = "all"
    phase: str = "playing"  # playing | between_rounds | closed
    message: discord.Message | None = None
    anchor_message: discord.Message | None = None
    thread: discord.Thread | None = None
    current_entry: tuple | None = None
    hint_level: int = 1
    round_start_time: float = 0.0
    round_num: int = 0
    streak: int = 0
    best_streak: int = 0
    total_correct: int = 0
    last_solve_time: float = 0.0
    used_ids: set[int] = field(default_factory=set)
    round_solved: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    race_task: asyncio.Task | None = field(default=None, repr=False)
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
    cat_label = _CATEGORY_LABELS.get(table.category, table.category)

    embed = discord.Embed(
        title="\u2753 Who's That Pokemon?",
        description=(
            f"**Category:** {cat_label}\n"
            f"**First to {WINS_TO_WIN} wins** takes the match.\n"
            "Hints are revealed over time \u2014 type the Pokemon's name in chat!"
        ),
        colour=discord.Colour.red(),
    )

    embed.add_field(name="Goal", value=f"First to {WINS_TO_WIN}", inline=True)

    if table.players:
        lines = [
            f"\U0001f534 **{p.display_name}**"
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


def _final_embed(table: PokeTable, elo_changes: dict[int, tuple[float, float]] | None = None) -> discord.Embed:
    max_wins = max((p.rounds_won for p in table.players.values()), default=0)
    no_winner = max_wins == 0

    embed = discord.Embed(
        title="\u2753 Who's That Pokemon? \u2014 Results",
        colour=discord.Colour.gold() if not no_winner else discord.Colour.dark_grey(),
    )

    if no_winner:
        embed.description = "No rounds were won \u2014 game over!"
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


# ── Solo embeds ──────────────────────────────────────────────────────────


def _solo_playing_embed(
    session: SoloSession, remaining: int | None = None,
) -> discord.Embed:
    entry = session.current_entry
    dex_id, name, alts, types, gen, hint, legendary = entry
    cat_label = _CATEGORY_LABELS.get(session.category, session.category)

    embed = discord.Embed(
        title=f"\u2753 Solo Mode \u2014 Round {session.round_num}",
        colour=discord.Colour.dark_red(),
    )

    hint_parts: list[str] = []
    type_line = _type_str(types)
    gen_label = GEN_LABEL.get(gen, f"Gen {gen}")
    hint_parts.append(f"**Type:** {type_line}")
    hint_parts.append(f"**Generation:** {gen_label}")
    if legendary:
        hint_parts.append("**Status:** Legendary / Mythical")
    if session.hint_level >= 2:
        hint_parts.append(f"\n\U0001f4a1 **Clue:** {hint}")
    if session.hint_level >= 3:
        hint_parts.append(
            f"\n\U0001f520 **Name:** `{_blank_name(name)}` ({len(name)} letters)",
        )

    embed.description = "\n".join(hint_parts) + "\n\n**Type your answer in chat!**"

    secs = remaining if remaining is not None else ROUND_TIME
    embed.add_field(name="\u23f1\ufe0f Time", value=f"**{secs}s**", inline=True)
    embed.add_field(
        name="\U0001f525 Streak", value=f"**{session.streak}**", inline=True,
    )
    embed.add_field(
        name="\u2705 Correct", value=f"**{session.total_correct}**", inline=True,
    )
    embed.set_footer(text=f"{session.display_name} \u2502 {cat_label}")
    return embed


def _solo_correct_embed(session: SoloSession) -> discord.Embed:
    entry = session.current_entry
    dex_id, name, *_ = entry
    solve_time = session.last_solve_time - session.round_start_time

    embed = discord.Embed(
        title=f"\u2753 Round {session.round_num} \u2705",
        colour=discord.Colour.green(),
    )
    embed.description = (
        f"**{name}**! (#{dex_id}) \u2014 {solve_time:.1f}s\n\n"
        f"\U0001f525 Streak: **{session.streak}** \u2502 "
        f"Best: **{session.best_streak}** \u2502 "
        f"Total: **{session.total_correct}**"
    )
    embed.set_thumbnail(url=SPRITE_URL.format(dex_id))
    embed.set_footer(text="Next round in a few seconds\u2026")
    return embed


def _solo_gameover_embed(session: SoloSession) -> discord.Embed:
    entry = session.current_entry
    dex_id, name, *_ = entry

    embed = discord.Embed(
        title="\u2753 Solo Mode \u2014 Game Over",
        colour=discord.Colour.dark_grey(),
    )
    pct = session.total_correct / max(session.round_num, 1) * 100
    embed.description = (
        f"Time's up! It was **{name}** (#{dex_id}).\n\n"
        f"\U0001f3c6 **Best Streak:** {session.best_streak}\n"
        f"\u2705 **Total Correct:** {session.total_correct} / {session.round_num}\n"
        f"\U0001f4ca **Accuracy:** {pct:.0f}%"
    )
    embed.set_thumbnail(url=SPRITE_URL.format(dex_id))
    embed.set_footer(
        text=f"{session.display_name} \u2502 Click Play Again below or /pokemon-solo",
    )
    return embed


# ── Modals ───────────────────────────────────────────────────────────────────


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


class SoloRestartView(ui.View):
    """Play Again button shown after a solo game ends."""

    def __init__(self, cog: "PokemonCog", user_id: int, display_name: str,
                 category: str, thread: discord.Thread,
                 anchor_message: discord.Message | None) -> None:
        super().__init__(timeout=120)
        self.cog = cog
        self.user_id = user_id
        self.display_name = display_name
        self.category = category
        self.thread = thread
        self.anchor_message = anchor_message

    @ui.button(
        label="Play Again", style=discord.ButtonStyle.green,
        emoji="\U0001f504", row=0,
    )
    async def restart_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Only the original player can restart!", ephemeral=True,
            )
            return

        if self.user_id in self.cog.active_solos:
            await interaction.response.send_message(
                "You already have a solo game running!", ephemeral=True,
            )
            return

        # Disable button immediately
        button.disabled = True
        button.label = "Starting\u2026"
        await interaction.response.edit_message(view=self)
        self.stop()

        # Build fresh session reusing the existing thread
        session = SoloSession(
            user_id=self.user_id,
            channel_id=self.thread.id,
            display_name=self.display_name,
            category=self.category,
            thread=self.thread,
            anchor_message=self.anchor_message,
        )
        entry = _pick_from_pool(self.category, session.used_ids)
        session.current_entry = entry
        session.round_num = 1
        session.round_start_time = time.monotonic()

        self.cog.active_solos[self.user_id] = session

        await self.thread.send(
            "\U0001f3c1 **New game started!** Type your guesses here. "
            "Type `quit` to end.",
        )
        session.message = await self.thread.send(
            embed=_solo_playing_embed(session),
        )
        session.race_task = asyncio.create_task(
            self.cog._solo_loop(session),
        )

    async def on_timeout(self) -> None:
        # Disable button and archive thread when restart window expires
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
        if self.thread:
            try:
                await self.thread.edit(archived=True)
            except discord.HTTPException:
                pass


class PokeEndGameView(ui.View):
    """Button posted in the thread so any player can stop the game early."""

    def __init__(self, table: PokeTable) -> None:
        super().__init__(timeout=None)
        self.table = table

    @ui.button(
        label="End Game", style=discord.ButtonStyle.danger,
        emoji="\u23f9\ufe0f", row=0,
    )
    async def end_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase == "closed":
            await interaction.response.send_message(
                "The game has already ended.", ephemeral=True,
            )
            return
        if (
            interaction.user.id != self.table.host_id
            and interaction.user.id not in self.table.players
        ):
            await interaction.response.send_message(
                "Only players can end the game!", ephemeral=True,
            )
            return
        if self.table.stop_requested:
            await interaction.response.send_message(
                "Already ending\u2026", ephemeral=True,
            )
            return
        self.table.stop_requested = True
        self.table.round_solved.set()  # wake up the game loop immediately
        button.disabled = True
        button.label = "Ending\u2026"
        await interaction.response.edit_message(view=self)


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
        """Free join — no bet required, ELO only."""
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
        self.table.players[uid] = PokePlayer(
            user_id=uid,
            display_name=interaction.user.display_name,
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
        in_progress = discord.Embed(
            title="\u2753 Who's That Pokémon? \u2014 In Progress",
            description="Game running in the thread below! Type your answers there.",
            colour=discord.Colour.red(),
        )
        players_text = ", ".join(p.display_name for p in table.players.values())
        in_progress.add_field(name="Players", value=players_text or "\u2014", inline=False)
        await interaction.response.edit_message(embed=in_progress, view=self)

        msg = await interaction.original_response()
        thread = await msg.create_thread(name=f"Pokémon Trivia \u2014 {table.host_name}")
        table.thread = thread

        await thread.send(
            "\U0001f3c1 **Pokémon Trivia started!** Type your answers here.",
            view=PokeEndGameView(table),
        )

        table.message = await thread.send(embed=_playing_embed(table))
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
            consecutive_unanswered = 0
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
                    if table.thread:
                        try:
                            table.message = await table.thread.send(
                                embed=_playing_embed(table),
                            )
                        except discord.HTTPException:
                            pass

                solved = await self._wait_for_solve_or_timeout()
                table.total_rounds_played += 1

                if table.stop_requested:
                    break

                if solved and table.round_winner is not None:
                    if table.message:
                        try:
                            await table.message.edit(
                                embed=_round_result_embed(table),
                            )
                        except discord.HTTPException:
                            pass
                else:
                    if table.message:
                        try:
                            await table.message.edit(
                                embed=_timeout_embed(table),
                            )
                        except discord.HTTPException:
                            pass

                # Inactivity: end if nobody answered N rounds in a row
                if table.round_winner is None:
                    consecutive_unanswered += 1
                else:
                    consecutive_unanswered = 0
                if consecutive_unanswered >= INACTIVITY_ROUNDS:
                    if table.thread:
                        try:
                            await table.thread.send(
                                "\u23f8\ufe0f No one answered for 5 consecutive rounds — ending due to inactivity."
                            )
                        except discord.HTTPException:
                            pass
                    break

                if any(p.rounds_won >= WINS_TO_WIN for p in table.players.values()):
                    break
                if rnd >= MAX_ROUNDS:
                    break

                await self._clear_round_messages()

                table.phase = "between_rounds"
                await asyncio.sleep(ROUND_DELAY)

                if table.stop_requested:
                    break

            await self._clear_round_messages()
            if table.stop_requested and table.thread:
                try:
                    await table.thread.send("\u23f9\ufe0f Game ended early.")
                except discord.HTTPException:
                    pass
            await self._end_game()

        except asyncio.CancelledError:
            table.phase = "closed"
            self.active_tables.pop(table.channel_id, None)
            if table.thread:
                try:
                    await table.thread.edit(archived=True)
                except Exception:
                    pass
        except Exception:
            table.phase = "closed"
            self.active_tables.pop(table.channel_id, None)
            if table.thread:
                try:
                    await table.thread.edit(archived=True)
                except Exception:
                    pass

    async def _end_game(self) -> None:
        table = self.table
        table.phase = "closed"

        # ELO update — rank by rounds_won (most wins = 1st)
        elo_changes: dict[int, tuple[float, float]] = {}
        max_wins = max((p.rounds_won for p in table.players.values()), default=0)
        if max_wins > 0 and len(table.players) >= 2:
            sorted_players = sorted(
                table.players.values(),
                key=lambda p: p.rounds_won,
                reverse=True,
            )
            finish_order = [p.user_id for p in sorted_players]
            try:
                elo_changes = await update_elo_multiplayer(finish_order, "pokemon", "pokemon")
            except Exception:
                pass  # don't break game end over ELO errors

        embed = _final_embed(table, elo_changes)

        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_tables.pop(table.channel_id, None)

        if table.message:
            try:
                await table.message.edit(embed=embed)
            except discord.HTTPException:
                pass

        if table.thread:
            try:
                await table.thread.edit(archived=True)
            except discord.HTTPException:
                pass

    async def _close_table(self, interaction: discord.Interaction) -> None:
        table = self.table

        if table.total_rounds_played == 0:
            embed = discord.Embed(
                title="\u2753 Pokemon Table \u2014 Closed",
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
                    title="\u2753 Pokemon Table \u2014 Timed Out",
                    description="Table timed out.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                pass

        if table.thread:
            try:
                await table.thread.edit(archived=True)
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


def _pick_from_pool(category: str, used_ids: set[int]) -> tuple:
    """Pick a random Pokemon matching category, avoiding repeats."""
    pool = [e for e in POKEMON if _cat_filter(e, category) and e[0] not in used_ids]
    if not pool:
        used_ids.clear()
        pool = [e for e in POKEMON if _cat_filter(e, category)]
    choice = random.choice(pool)
    used_ids.add(choice[0])
    return choice


_SOLO_CATEGORY_CHOICES = [
    app_commands.Choice(name="All Generations", value="all"),
    app_commands.Choice(name="Gen 1 (Kanto)", value="gen1"),
    app_commands.Choice(name="Gen 2\u20134 (Johto/Hoenn/Sinnoh)", value="gen2to4"),
    app_commands.Choice(name="Gen 5+ (Unova and beyond)", value="gen5plus"),
    app_commands.Choice(name="Legendary & Mythical", value="legendary"),
]


# ── Cog ──────────────────────────────────────────────────────────────────────


class PokemonCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, PokeTable] = {}
        self.active_solos: dict[int, SoloSession] = {}  # keyed by user_id

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

    @app_commands.command(
        name="pokemon-solo",
        description="Solo Pokemon quiz \u2014 unlimited rounds, no betting!",
    )
    @app_commands.describe(category="Pokemon category to play")
    @app_commands.choices(category=_SOLO_CATEGORY_CHOICES)
    async def pokemon_solo(
        self,
        interaction: discord.Interaction,
        category: app_commands.Choice[str] | None = None,
    ) -> None:
        uid = interaction.user.id
        if uid in self.active_solos:
            await interaction.response.send_message(
                "You already have a solo game running!", ephemeral=True,
            )
            return

        cat = category.value if category else "all"
        session = SoloSession(
            user_id=uid,
            channel_id=interaction.channel_id,
            display_name=interaction.user.display_name,
            category=cat,
        )

        entry = _pick_from_pool(cat, session.used_ids)
        session.current_entry = entry
        session.round_num = 1
        session.round_start_time = time.monotonic()

        # Post anchor message, then create a thread for gameplay
        anchor_embed = discord.Embed(
            title="\u2753 Pok\u00e9mon Solo \u2014 In Progress",
            description=f"**{interaction.user.display_name}** is playing solo!",
            colour=discord.Colour.orange(),
        )
        await interaction.response.send_message(embed=anchor_embed)
        anchor = await interaction.original_response()
        session.anchor_message = anchor
        try:
            thread = await anchor.create_thread(
                name=f"Pok\u00e9mon Solo \u2014 {interaction.user.display_name}",
            )
        except discord.HTTPException:
            await anchor.edit(
                embed=discord.Embed(
                    title="Could not start solo game",
                    description="Failed to create a thread. Make sure the bot has "
                    "**Create Public Threads** permission in this channel.",
                    colour=discord.Colour.red(),
                ),
            )
            return
        session.thread = thread

        # Only register AFTER thread creation succeeds — prevents
        # leaked sessions that block future /pokemon-solo calls.
        self.active_solos[uid] = session

        await thread.send(
            "\U0001f3c1 **Pok\u00e9mon Solo started!** Type your guesses here. "
            "Type `quit` to end.",
        )
        session.message = await thread.send(embed=_solo_playing_embed(session))
        session.race_task = asyncio.create_task(self._solo_loop(session))

    # ── Solo loop ─────────────────────────────────────────────

    async def _solo_wait(self, session: SoloSession) -> bool:
        """Wait for solo solve or timeout. Returns True if solved."""
        deadline = session.round_start_time + ROUND_TIME
        hint2_time = session.round_start_time + HINT2_AT
        hint3_time = session.round_start_time + HINT3_AT

        while True:
            now = time.monotonic()
            remaining = deadline - now
            if remaining <= 0:
                return session.round_solved.is_set()

            next_events: list[float] = []
            if session.hint_level < 2 and hint2_time > now:
                next_events.append(hint2_time - now)
            if session.hint_level < 3 and hint3_time > now:
                next_events.append(hint3_time - now)
            next_events.append(min(5.0, remaining))
            wait = min(next_events)

            try:
                await asyncio.wait_for(session.round_solved.wait(), timeout=wait)
                return True
            except asyncio.TimeoutError:
                if session.round_solved.is_set():
                    return True

                now2 = time.monotonic()
                if session.hint_level < 2 and now2 >= hint2_time:
                    session.hint_level = 2
                if session.hint_level < 3 and now2 >= hint3_time:
                    session.hint_level = 3

                secs_left = max(0, int(deadline - now2))
                if secs_left > 0 and session.message:
                    try:
                        await session.message.edit(
                            embed=_solo_playing_embed(session, remaining=secs_left),
                        )
                    except discord.HTTPException:
                        pass

    async def _solo_clear_messages(self, session: SoloSession) -> None:
        messages = list(session.round_messages)
        session.round_messages.clear()
        for msg in messages:
            try:
                await msg.delete()
            except discord.HTTPException:
                pass

    async def _solo_loop(self, session: SoloSession) -> None:
        try:
            rnd = 0
            while True:
                rnd += 1

                if rnd > 1:
                    entry = _pick_from_pool(session.category, session.used_ids)
                    session.current_entry = entry
                    session.hint_level = 1
                    session.round_num = rnd
                    session.round_solved.clear()
                    session.round_messages.clear()
                    session.phase = "playing"
                    session.round_start_time = time.monotonic()

                    if session.message:
                        try:
                            await session.message.edit(
                                embed=_solo_playing_embed(session),
                            )
                        except discord.HTTPException:
                            pass

                solved = await self._solo_wait(session)

                if solved:
                    session.streak += 1
                    session.total_correct += 1
                    session.best_streak = max(
                        session.best_streak, session.streak,
                    )

                    if session.message:
                        try:
                            await session.message.edit(
                                embed=_solo_correct_embed(session),
                            )
                        except discord.HTTPException:
                            pass

                    await self._solo_clear_messages(session)
                    session.phase = "between_rounds"
                    await asyncio.sleep(ROUND_DELAY)
                else:
                    # Timeout — game over
                    session.streak = 0
                    session.phase = "closed"
                    restart_view = SoloRestartView(
                        self, session.user_id, session.display_name,
                        session.category, session.thread,
                        session.anchor_message,
                    )
                    if session.thread:
                        try:
                            await session.thread.send(
                                embed=_solo_gameover_embed(session),
                                view=restart_view,
                            )
                        except discord.HTTPException:
                            pass
                    break

        except asyncio.CancelledError:
            pass
        finally:
            session.phase = "closed"
            # Only pop if this session is still the active one (avoids
            # race where quit handler already popped and user started
            # a new game — we must not remove the new session).
            if self.active_solos.get(session.user_id) is session:
                self.active_solos.pop(session.user_id, None)
            # Update anchor embed to show game is finished
            if session.anchor_message:
                done_embed = discord.Embed(
                    title="\u2705 Pok\u00e9mon Solo \u2014 Finished",
                    description=(
                        f"**{session.display_name}** finished with "
                        f"**{session.total_correct}** correct "
                        f"(best streak: {session.best_streak})"
                    ),
                    colour=discord.Colour.green(),
                )
                try:
                    await session.anchor_message.edit(embed=done_embed)
                except discord.HTTPException:
                    pass
            # Thread archiving is handled by SoloRestartView.on_timeout
            # so the player has time to click "Play Again".

    @commands.Cog.listener("on_message")
    async def on_message(self, message: discord.Message) -> None:
        """Listen for chat guesses during active Pokemon rounds."""
        if message.author.bot:
            return

        uid = message.author.id
        guess = message.content.strip()

        # ── Multiplayer table: find by thread ID ──
        table = None
        for t in self.active_tables.values():
            if t.thread is not None and t.thread.id == message.channel.id:
                table = t
                break
        if table is not None and table.phase == "playing":
            if uid in table.players and table.round_winner is None:
                if len(guess) < 3:
                    return
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
                return

        # ── Solo session for this user ──
        solo = self.active_solos.get(uid)
        if (
            solo is not None
            and solo.phase in ("playing", "between_rounds")
            and solo.thread is not None
            and solo.thread.id == message.channel.id
        ):
            # Quit command — works during any active phase
            if guess.lower() in ("quit", "stop", "end"):
                solo.phase = "closed"
                if solo.race_task and not solo.race_task.done():
                    solo.race_task.cancel()
                restart_view = SoloRestartView(
                    self, solo.user_id, solo.display_name,
                    solo.category, solo.thread,
                    solo.anchor_message,
                )
                if solo.thread:
                    try:
                        await solo.thread.send(
                            embed=_solo_gameover_embed(solo),
                            view=restart_view,
                        )
                    except discord.HTTPException:
                        pass
                if solo.anchor_message:
                    done_embed = discord.Embed(
                        title="\u2705 Pok\u00e9mon Solo \u2014 Finished",
                        description=(
                            f"**{solo.display_name}** finished with "
                            f"**{solo.total_correct}** correct "
                            f"(best streak: {solo.best_streak})"
                        ),
                        colour=discord.Colour.green(),
                    )
                    try:
                        await solo.anchor_message.edit(embed=done_embed)
                    except discord.HTTPException:
                        pass
                self.active_solos.pop(uid, None)
                return
            # Only process guesses during playing phase
            if solo.phase != "playing":
                return
            if len(guess) < 3:
                return
            alpha_chars = sum(1 for c in guess if c.isalpha())
            if alpha_chars < len(guess) * 0.5:
                return
            solo.round_messages.append(message)
            if check_pokemon_answer(guess, solo.current_entry):
                solo.last_solve_time = time.monotonic()
                try:
                    await message.add_reaction("\u2705")
                except discord.HTTPException:
                    pass
                solo.round_solved.set()
            else:
                try:
                    await message.add_reaction("\u274c")
                except discord.HTTPException:
                    pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PokemonCog(bot))
