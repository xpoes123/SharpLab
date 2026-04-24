"""Casino cog — multiplayer /valorant guessing game.

Progressive hints about a Valorant agent, weapon, or map; first to type
its name wins the round.  First to WINS_TO_WIN rounds takes the pot.
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
from bot.cogs._elo_helpers import update_elo_multiplayer

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

# ── Role / Category emoji ───────────────────────────────────────────────────

ROLE_EMOJI: dict[str, str] = {
    "Duelist": "\u2694\ufe0f",
    "Sentinel": "\U0001f6e1\ufe0f",
    "Initiator": "\U0001f4e1",
    "Controller": "\U0001f32b\ufe0f",
}

WEAPON_TYPE_EMOJI: dict[str, str] = {
    "Sidearm": "\U0001f52b",
    "SMG": "\U0001f4a8",
    "Shotgun": "\U0001f4a5",
    "Rifle": "\U0001f3af",
    "Sniper": "\U0001f52d",
    "Machine Gun": "\u26d3\ufe0f",
    "Melee": "\U0001f5e1\ufe0f",
}

# ── Agent Data ───────────────────────────────────────────────────────────────
# (id, name, [alt_names], role, origin, hint)
# Complete roster as of Season 2026 Act 2 (29 agents)

AGENTS: list[tuple[int, str, list[str], str, str, str]] = [
    # ── Launch agents (10) ────────────────────────────────────────────────
    (1, "Brimstone", ["Brim"], "Controller", "United States",
     "A veteran commander who calls in orbital deployments via a wrist tablet. His kit includes three-charge sky smokes, an incendiary grenade, a stim beacon, and a devastating orbital laser strike."),
    (2, "Viper", [], "Controller", "United States",
     "A chemist who deploys a reusable toxic gas emitter and a long toxic screen wall. Her fuel-based abilities decay enemy health through a shared fuel gauge she can toggle on and off."),
    (3, "Omen", [], "Controller", "Unknown",
     "A shadowy phantom who places smokes from anywhere on the map via an astral view. He can blind enemies with a shadow projectile and short-teleport through walls, with an ultimate that lets him teleport anywhere on the map."),
    (4, "Sage", [], "Sentinel", "China",
     "A radiant monk whose healing orb restores ally health over time. She creates ice walls to block paths, slows enemies with a frost orb, and her ultimate resurrects a fallen teammate at full health."),
    (5, "Phoenix", [], "Duelist", "United Kingdom",
     "A fire-wielding agent whose flames can heal himself. He throws a curving flashbang, creates a fire wall, and tosses an incendiary. His ultimate marks his position and returns him there after the timer or upon death."),
    (6, "Jett", [], "Duelist", "South Korea",
     "A wind-powered duelist who can dash forward, updraft vertically, and float in the air. Her ultimate summons five throwing knives that reset on kills, making her the game's signature Operator agent."),
    (7, "Sova", [], "Initiator", "Russia",
     "A master tracker who uses a bow to fire recon bolts that reveal enemies in their radius. He deploys an owl drone for scouting and can fire three wall-piercing energy blasts with his ultimate."),
    (8, "Cypher", [], "Sentinel", "Morocco",
     "A one-man surveillance network who places tripwires, a spy camera that shoots tracking darts, and cyber cages that slow. His ultimate reveals all living enemy positions by targeting a dead enemy."),
    (9, "Breach", [], "Initiator", "Sweden",
     "A bionic man with mechanical arms whose abilities all fire through walls. He sends seismic blasts, flashes, and a ground-pound concussion. His ultimate sends a massive earthquake in a cone that dazes everyone hit."),
    (10, "Raze", [], "Duelist", "Brazil",
     "An explosive specialist from Salvador who uses a robot boom bot, cluster grenades, and blast packs to satchel-jump. Her ultimate fires a rocket launcher that deals massive damage in a large area."),
    # ── Post-launch agents (19) ───────────────────────────────────────────
    (11, "Reyna", [], "Duelist", "Mexico",
     "A vampire-like agent who collects soul orbs from enemies she kills. She consumes them to either heal to full or become briefly invulnerable. Her ultimate enhances all abilities with increased fire rate and automatic soul harvesting."),
    (12, "Killjoy", ["KJ"], "Sentinel", "Germany",
     "A genius inventor who deploys a turret that auto-fires at enemies, an alarm bot that hunts them down, and swarm grenades. Her ultimate deploys a device that detains all enemies caught in its massive radius after a windup."),
    (13, "Skye", [], "Initiator", "Australia",
     "A nature-themed agent who sends out animal trinkets: a hawk that flashes enemies and confirms hits, a Tasmanian tiger that tracks enemies, and a healing pool for allies. Her ultimate sends three jellyfish seekers to chase down enemies."),
    (14, "Yoru", [], "Duelist", "Japan",
     "A dimensional rift walker who places teleport anchors he can warp back to. He sends decoy footstep clones, throws a bouncing flash, and can enter another dimension to move invisible and invulnerable for a short time."),
    (15, "Astra", [], "Controller", "Ghana",
     "A cosmic controller who enters an astral form to place stars on the map. Each star can be activated as a smoke, a gravity well that pulls enemies, or a concussion. Her ultimate creates a massive wall that blocks sound and bullets."),
    (16, "KAY/O", ["Kayo", "KayO", "Kay O", "KAYO"], "Initiator", "Unknown",
     "A war machine robot built to suppress radiants. His knife disables enemy abilities in an area, he has a flash grenade, and a frag grenade. His ultimate creates a suppression field around him and allows allies to revive him if he goes down."),
    (17, "Chamber", [], "Sentinel", "France",
     "An elegant weapons designer who places teleport anchors to instantly retreat. He wields a custom heavy pistol as an ability and his ultimate grants a powerful golden sniper rifle that slows enemies on hit."),
    (18, "Neon", [], "Duelist", "Philippines",
     "A bioelectric sprinter who can run at blazing speed with energy slides. She throws concussive stun bolts and creates an electric wall. Her ultimate channels twin lightning beams from her fingertips that deal damage based on accuracy."),
    (19, "Fade", [], "Initiator", "Turkey",
     "A nightmare-wielding bounty hunter whose prowler creature chases enemies. She throws an eye that reveals and trails enemies hit, and a tethering orb that deafens. Her ultimate sends a wave of nightmares that reveals, deafens, and decays enemies."),
    (20, "Harbor", [], "Controller", "India",
     "An agent who wields an ancient artifact to bend water. He sends a wave forward that slows enemies, creates a water wall that blocks vision, and places a water sphere smoke. His ultimate calls down a geyser strike that repeatedly concusses enemies in the area."),
    (21, "Gekko", [], "Initiator", "United States",
     "A young agent from Los Angeles who commands a crew of small creatures. His Dizzy flashes, Wingman plants or defuses the spike, Mosh creates a delayed area explosion, and his Thrash detains enemies. He can pick up his creatures to reuse them."),
    (22, "Deadlock", [], "Sentinel", "Norway",
     "A sentinel who deploys a barrier mesh of nanowire, sonic sensors that concuss enemies passing through, and a GravNet grenade that forces enemies to crouch. Her ultimate fires a nanowire cocoon that captures an enemy and drags them to a wall for a kill."),
    (23, "Iso", [], "Duelist", "China",
     "A fixer who absorbs ambient energy into a protective shield that blocks one instance of damage. He fires a wall of energy that applies vulnerability, and his ultimate pulls a targeted enemy into a 1v1 arena where only bullets work."),
    (24, "Clove", [], "Controller", "Scotland",
     "An immortal teenager who can use smoke abilities even after dying. They throw a decay-applying orb and gain temporary haste and health on kills. Their ultimate lets them self-revive after dying if they get a kill or damaging assist quickly."),
    (25, "Vyse", [], "Sentinel", "Unknown",
     "A former researcher with radianite-infused liquid metal flowing through her. She creates razorvine walls that block paths, deploys magnetic traps that disable enemy utility on contact, and her ultimate creates a massive field that destroys any abilities thrown into it."),
    (26, "Tejo", [], "Initiator", "Colombia",
     "A veteran intelligence consultant named after a Colombian sport of throwing metal discs at explosives. He throws a sticky concussion grenade, flies a stealth drone that suppresses enemies, and calls in guided missile salvos along a targeted path."),
    (27, "Waylay", [], "Duelist", "Thailand",
     "A radiant who manipulates light and can dash forward at blazing speed with two charges. She throws a cluster of light that debuffs enemies, can refract to reposition, and her ultimate creates converging beams of light."),
    (28, "Veto", [], "Sentinel", "Senegal",
     "An enforcer empowered by a DNA mutation that nullifies powers and technology. He deploys traps that hold enemies in place with deafen and decay, can teleport back to a vortex anchor, and his interceptor destroys enemy utility. His ultimate grants a mutated combat form with regeneration and status immunity."),
    (29, "Miks", [], "Controller", "Croatia",
     "A music-driven controller who fights to the beat. He grants combat stim to allies with a harmony ability, places a device that can concuss enemies or heal allies, and deploys hollow smokes via a tablet. His ultimate unleashes a sonic wave that knocks back, deafens, and slows enemies."),
]

# ── Weapon Data ──────────────────────────────────────────────────────────────
# (id, name, [alt_names], weapon_type, cost, hint)
# Complete arsenal as of Season 2026 (20 weapons)

WEAPONS: list[tuple[int, str, list[str], str, int, str]] = [
    # ── Sidearms ──────────────────────────────────────────────────────────
    (1, "Classic", [], "Sidearm", 0,
     "The default free pistol everyone starts with. Left-click fires single shots, while right-click fires a three-round burst at close range. Zero cost means you always have it on eco rounds."),
    (2, "Shorty", [], "Sidearm", 150,
     "The cheapest purchasable weapon in the game — a tiny double-barreled shotgun sidearm. It holds only 2 shells and is devastating at point-blank range but completely useless at distance."),
    (3, "Frenzy", [], "Sidearm", 450,
     "A fully automatic pistol with a blistering fire rate of 10 rounds per second. It shreds at close range but the recoil becomes uncontrollable with sustained fire. Popular on pistol rounds for aggressive plays."),
    (4, "Ghost", [], "Sidearm", 500,
     "A silenced semi-automatic pistol with excellent first-shot accuracy and no bullet tracers. The go-to eco pistol for players who prefer precision headshots. Its suppressor makes it hard for enemies to locate the shooter."),
    (5, "Bandit", [], "Sidearm", 600,
     "A semi-automatic precision pistol introduced in Season 2026, designed to fill the gap between cheaper and more expensive sidearms. It has an 8-round magazine and can one-tap headshot through light armor, making it lethal on pistol and eco rounds."),
    (6, "Sheriff", [], "Sidearm", 800,
     "The most expensive sidearm — a hand cannon that one-shot headshots at any range. With only 6 rounds per magazine and punishing recoil, it's high risk, high reward. Basically a pocket sniper for players with confident aim."),
    # ── SMGs ──────────────────────────────────────────────────────────────
    (7, "Stinger", [], "SMG", 950,
     "A cheap SMG with a blazing fire rate and a burst-fire alt-mode that tightens the spread. Good for close-quarter force buys but its damage drops off sharply at range. The budget option when you need automatic fire."),
    (8, "Spectre", [], "SMG", 1600,
     "A silenced SMG with no bullet tracers that stays accurate while moving. The most popular force-buy weapon in the game, effective at short to medium range. Its suppressor and run-and-gun accuracy make it a staple eco weapon."),
    # ── Shotguns ──────────────────────────────────────────────────────────
    (9, "Bucky", [], "Shotgun", 850,
     "A pump-action shotgun with two fire modes: left-click fires a wide spread, right-click fires a tighter slug that's effective at slightly longer range. Devastating in close quarters but punishingly slow between shots."),
    (10, "Judge", [], "Shotgun", 1850,
     "A fully automatic shotgun that sprays pellets as fast as you can hold the trigger. Terrifying in tight corridors and around corners, but nearly useless at any real distance. The ultimate cheese weapon for holding close angles."),
    # ── Rifles ────────────────────────────────────────────────────────────
    (11, "Bulldog", [], "Rifle", 2050,
     "An affordable rifle with a three-round burst alt-fire using ADS. It bridges the gap between SMGs and full rifles on a budget. At 2050 credits, it's the cheapest rifle and a solid force-buy option."),
    (12, "Guardian", [], "Rifle", 2250,
     "A semi-automatic designated marksman rifle with ADS and high damage per shot. It one-taps headshots at all ranges and rewards precise aim. Popular on eco rounds as a cheaper alternative to the full-buy rifles."),
    (13, "Phantom", [], "Rifle", 2900,
     "A silenced assault rifle that doesn't show bullet tracers, making it harder for enemies to trace your position. It has a faster fire rate than its same-priced counterpart and is preferred for spraying through smokes and close-range fights."),
    (14, "Vandal", [], "Rifle", 2900,
     "The iconic assault rifle that deals 160 headshot damage at all ranges — no falloff. One tap, one kill at any distance. It shares its price with a silenced competitor but trades fire rate for consistent long-range lethality."),
    # ── Snipers ───────────────────────────────────────────────────────────
    (15, "Marshal", [], "Sniper", 950,
     "A lightweight lever-action sniper rifle and the cheapest scoped weapon. It one-shot headshots, has high movement speed for a sniper, and is a popular eco-round pick. Its ADS zoom is lower than the heavier snipers."),
    (16, "Outlaw", [], "Sniper", 2400,
     "A double-barreled sniper rifle that fires two shots in quick succession before needing to rechamber. Both bullets connecting can kill through body shots. Added in Episode 8 as a mid-tier sniper option."),
    (17, "Operator", ["Op", "Awp", "OP"], "Sniper", 4700,
     "The most expensive weapon in the game. One body shot kill from any range with devastating 255 damage to the legs and above. It's slow to re-scope, heavy to carry, and costs a fortune, but a single click ends any opponent."),
    # ── Machine Guns ──────────────────────────────────────────────────────
    (18, "Ares", [], "Machine Gun", 1600,
     "A light machine gun with a 50-round magazine that becomes more accurate the longer you fire, reversing the usual spray pattern logic. Great for wallbanging and sustained suppression on a budget."),
    (19, "Odin", [], "Machine Gun", 3200,
     "A heavy machine gun with a massive 100-round magazine that can wallbang almost anything. It has a wind-up time where fire rate increases and recoil decreases. The ultimate spam cannon for suppressive fire and prefiring."),
    # ── Melee ─────────────────────────────────────────────────────────────
    (20, "Knife", ["Tactical Knife", "Melee"], "Melee", 0,
     "The default melee weapon every agent carries. Left-click swings fast for 50 damage, right-click stabs for 75 damage (150 backstab) but is slower. Equipping it gives the fastest movement speed in the game."),
]

# ── Map Data ─────────────────────────────────────────────────────────────────
# (id, name, [alt_names], location, hint)
# All 12 standard maps + 5 TDM maps as of Season 2026

MAPS: list[tuple[int, str, list[str], str, str]] = [
    # ── Standard / Competitive Maps (12) ──────────────────────────────────
    (1, "Bind", [], "Rabat, Morocco",
     "A two-site map with no traditional mid lane. Instead, two one-way teleporters connect the sites, giving attackers fast rotation options but making a loud sound that alerts defenders. Teleporters are the defining feature."),
    (2, "Haven", [], "Thimphu, Bhutan",
     "The first map to feature three bomb sites (A, B, and C). Defenders must spread thin across three sites, and attackers have numerous split and fake options. Set near a monastery in a mountainous kingdom."),
    (3, "Split", [], "Tokyo, Japan",
     "A map with two sites connected by a narrow mid area featuring ascender ropes. Extremely tight chokepoints and strong defender-sided positions with lots of verticality. Was temporarily removed from competitive rotation before returning."),
    (4, "Ascent", [], "Venice, Italy",
     "Set in a floating Italian city lifted into the sky by radianite. It has a large, open mid area and mechanical doors on both bomb sites that can be destroyed. Rewards long-range aim and mid control."),
    (5, "Icebox", [], "Bennett Island, Russia",
     "A cold industrial map set on an Arctic island with significant verticality. Features a zipline, tight corridors, elevated container positions, and a distinctive yellow shipping container area on B site. One of the most vertically complex maps."),
    (6, "Breeze", [], "Bermuda Triangle",
     "A large tropical map with wide open spaces and the longest sightlines in the game. Features a one-way vent tube on A and a mechanical door on B. The A site has a distinctive fort/pyramid-like structure surrounded by open ground."),
    (7, "Fracture", [], "New Mexico, United States",
     "An H-shaped map with a unique layout where attackers spawn in the center and can push to either side using ziplines underneath. Defenders spawn sandwiched between two attacker approaches, making it uniquely attacker-sided."),
    (8, "Pearl", [], "Lisbon, Portugal",
     "An underwater city map set in a domed Omega Earth version of Lisbon. No teleporters, ziplines, or mechanical gimmicks — pure gunplay and fundamentals. Features a large mid area, tight corridors, and a distinctive plaza."),
    (9, "Lotus", [], "Western Ghats, India",
     "The second three-site map, set in an ancient temple powered by radianite. Features rotating stone doors that open and close, creating dynamic rotation paths, and destructible walls that attackers can break open for new angles."),
    (10, "Sunset", [], "Los Angeles, United States",
     "A two-site map set in a colorful LA neighborhood with a distinctive boba shop in mid. Features an open mid section connecting the two sites, market stalls, and a relatively straightforward three-lane layout."),
    (11, "Abyss", [], "Jan Mayen, Norway",
     "Set inside a giant volcanic cavern with no safety railings — players can fall off the edges to their death. Located under the island of Jan Mayen, it was a base for the Scions of Hourglass. Death drops fundamentally change positioning."),
    (12, "Corrode", [], "Normandy, France",
     "A medieval fortress inspired by Mont-Saint-Michel, transformed into a radianite salt mining facility on Omega Earth. Features numerous soft walls for wallbanging throughout both sites, sunken bomb sites with elevated defender angles, and no dynamic map elements."),
    # ── Team Deathmatch Maps (5) ──────────────────────────────────────────
    (13, "Piazza", [], "Italy",
     "A compact Team Deathmatch map set in a sunny Italian marketplace. Features short connection paths that enable quick repositioning and constant action. Inspired by Ascent's aesthetic with warm Mediterranean architecture."),
    (14, "District", [], "Japan",
     "A Team Deathmatch map inspired by Split's urban Japanese aesthetic. Features tight lanes perfect for close-range duels and a more vertical layout. One of the three original TDM maps released alongside the mode."),
    (15, "Kasbah", [], "Morocco",
     "A Team Deathmatch map set in North African corridors creating chaotic close-quarters encounters. Tight alleyways and archways funnel players into fast-paced fights. Shares Bind's Moroccan theming."),
    (16, "Drift", [], "Thailand",
     "A compact Team Deathmatch map set in a peaceful Thai village with elevated platforms. Favors mid-to-long range engagements with its open sightlines and tiered height positions."),
    (17, "Glitch", [], "Unknown",
     "A high-tech Team Deathmatch arena that remixes Haven and Sunset assets into a digital/glitch aesthetic. Height control and mid-map dominance are key to winning on this map."),
]

# ── Answer matching ──────────────────────────────────────────────────────────


def _normalize(s: str) -> str:
    """Lowercase, strip accents and non-alphanumeric chars."""
    nfkd = unicodedata.normalize("NFKD", s)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return "".join(c.lower() for c in stripped if c.isalnum()).strip()


def _accepted_names(entry: tuple) -> list[str]:
    """Build accepted answer list from entry (name + alt_names).

    Entry format after _pick_entry: (entry_type, id, name, alts, ...).
    """
    _type, _id, name, alts, *_ = entry
    return [name] + list(alts)


def check_answer(guess: str, entry: tuple) -> bool:
    """Check if a guess matches the entry."""
    norm = _normalize(guess)
    if not norm or len(norm) < 2:
        return False
    for ans in _accepted_names(entry):
        if _normalize(ans) == norm:
            return True
    return False


# ── Hint formatting ──────────────────────────────────────────────────────────


def _blank_name(name: str) -> str:
    """e.g. 'Phoenix' -> 'P _ _ _ _ _ _'"""
    if len(name) <= 1:
        return name
    return name[0] + " " + " ".join("_" for _ in name[1:])


# ── Payout helpers ───────────────────────────────────────────────────────────


def _compute_payouts(
    players: dict[int, "ValPlayer"], prize_pool: int, n_players: int,
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
class ValPlayer:
    user_id: int
    display_name: str
    bet: int
    rounds_won: int = 0
    answer: str | None = None
    answer_time: float | None = None


@dataclass
class ValTable:
    channel_id: int
    host_id: int
    host_name: str
    phase: str = "betting"  # betting | playing | between_rounds | closed
    players: dict[int, ValPlayer] = field(default_factory=dict)
    message: discord.Message | None = None
    round_num: int = 0
    category: str = "agents"  # agents | weapons | maps | all
    current_entry: tuple | None = None
    hint_level: int = 1  # 1, 2, or 3
    round_start_time: float = 0.0
    round_winner: int | None = None
    race_task: asyncio.Task | None = field(default=None, repr=False)
    round_solved: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    last_bets: dict[int, tuple[str, int]] = field(default_factory=dict)
    total_rounds_played: int = 0
    used_ids: set[tuple[str, int]] = field(default_factory=set)  # (type, id) pairs
    round_messages: list[discord.Message] = field(default_factory=list)


@dataclass
class SoloSession:
    user_id: int
    channel_id: int
    display_name: str
    category: str = "agents"
    phase: str = "playing"  # playing | between_rounds | closed
    message: discord.Message | None = None
    current_entry: tuple | None = None
    hint_level: int = 1
    round_start_time: float = 0.0
    round_num: int = 0
    streak: int = 0
    best_streak: int = 0
    total_correct: int = 0
    last_solve_time: float = 0.0
    used_ids: set[tuple[str, int]] = field(default_factory=set)
    round_solved: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    race_task: asyncio.Task | None = field(default=None, repr=False)
    round_messages: list[discord.Message] = field(default_factory=list)


# ── Entry picking ────────────────────────────────────────────────────────────


def _pick_entry(category: str, used_ids: set[tuple[str, int]]) -> tuple:
    """Pick a random entry matching category, avoiding repeats."""
    pools: list[tuple[str, list]] = []
    if category in ("agents", "all"):
        pools.append(("agent", AGENTS))
    if category in ("weapons", "all"):
        pools.append(("weapon", WEAPONS))
    if category in ("maps", "all"):
        pools.append(("map", MAPS))

    candidates: list[tuple[str, tuple]] = []
    for entry_type, data in pools:
        for entry in data:
            if (entry_type, entry[0]) not in used_ids:
                candidates.append((entry_type, entry))

    if not candidates:
        used_ids.clear()
        for entry_type, data in pools:
            for entry in data:
                candidates.append((entry_type, entry))

    entry_type, entry = random.choice(candidates)
    used_ids.add((entry_type, entry[0]))
    # Prepend entry_type as first element for downstream hint logic
    return (entry_type, *entry)


# ── Embeds ───────────────────────────────────────────────────────────────────


def _scoreboard(table: ValTable) -> str:
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
    "agents": "Agents",
    "weapons": "Weapons",
    "maps": "Maps",
    "all": "Everything",
}


def _betting_embed(table: ValTable) -> discord.Embed:
    pot = sum(p.bet for p in table.players.values())
    n = len(table.players)
    cat_label = _CATEGORY_LABELS.get(table.category, table.category)

    embed = discord.Embed(
        title="\U0001f3ae Guess the Valorant!",
        description=(
            f"**Category:** {cat_label}\n"
            f"**First to {WINS_TO_WIN} wins** takes the pot.\n"
            "Hints are revealed over time \u2014 type your answer in chat!"
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


def _build_hints(entry: tuple, hint_level: int) -> str:
    """Build hint text based on entry type and current hint level."""
    entry_type = entry[0]
    parts: list[str] = []

    if entry_type == "agent":
        _, _id, name, alts, role, origin, hint = entry
        role_em = ROLE_EMOJI.get(role, "")
        parts.append(f"**Type:** Agent")
        parts.append(f"**Role:** {role_em} {role}")
        parts.append(f"**Origin:** {origin}")
        if hint_level >= 2:
            parts.append(f"\n\U0001f4a1 **Clue:** {hint}")
        if hint_level >= 3:
            parts.append(f"\n\U0001f520 **Name:** `{_blank_name(name)}` ({len(name)} letters)")

    elif entry_type == "weapon":
        _, _id, name, alts, wtype, cost, hint = entry
        wt_em = WEAPON_TYPE_EMOJI.get(wtype, "")
        parts.append(f"**Type:** Weapon")
        parts.append(f"**Class:** {wt_em} {wtype}")
        parts.append(f"**Cost:** {cost} credits")
        if hint_level >= 2:
            parts.append(f"\n\U0001f4a1 **Clue:** {hint}")
        if hint_level >= 3:
            parts.append(f"\n\U0001f520 **Name:** `{_blank_name(name)}` ({len(name)} letters)")

    elif entry_type == "map":
        _, _id, name, alts, location, hint = entry
        parts.append(f"**Type:** Map")
        parts.append(f"**Location:** {location}")
        if hint_level >= 2:
            parts.append(f"\n\U0001f4a1 **Clue:** {hint}")
        if hint_level >= 3:
            parts.append(f"\n\U0001f520 **Name:** `{_blank_name(name)}` ({len(name)} letters)")

    return "\n".join(parts)


def _get_name(entry: tuple) -> str:
    """Extract name from any entry type."""
    return entry[2]  # (entry_type, id, name, ...)


def _playing_embed(table: ValTable, remaining: int | None = None) -> discord.Embed:
    entry = table.current_entry
    entry_type = entry[0]

    type_labels = {"agent": "Agent", "weapon": "Weapon", "map": "Map"}
    type_label = type_labels.get(entry_type, "???")

    embed = discord.Embed(
        title=f"\U0001f3ae Guess the {type_label}! \u2014 Round {table.round_num}",
        colour=discord.Colour.dark_red(),
    )

    embed.description = _build_hints(entry, table.hint_level) + "\n\n**Type your answer in chat!**"

    secs = remaining if remaining is not None else ROUND_TIME
    embed.add_field(name="\u23f1\ufe0f Time", value=f"**{secs}s**", inline=True)

    pot = sum(p.bet for p in table.players.values())
    embed.add_field(name="Pot", value=f"{pot}c", inline=True)

    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


def _round_result_embed(table: ValTable) -> discord.Embed:
    winner = table.players[table.round_winner]
    entry = table.current_entry
    name = _get_name(entry)
    solve_time = winner.answer_time - table.round_start_time
    is_last = winner.rounds_won >= WINS_TO_WIN or table.round_num >= MAX_ROUNDS

    embed = discord.Embed(
        title=f"\U0001f3ae Round {table.round_num} \u2705",
        colour=discord.Colour.green(),
    )
    embed.description = (
        f"\U0001f3c6 **{winner.display_name}** got it in **{solve_time:.1f}s**!\n\n"
        f"It's **{name}**!"
    )
    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    if not is_last:
        embed.set_footer(text="Next round in a few seconds\u2026")
    else:
        embed.set_footer(text="Final round \u2014 calculating results\u2026")
    return embed


def _timeout_embed(table: ValTable) -> discord.Embed:
    entry = table.current_entry
    name = _get_name(entry)
    max_wins = max((p.rounds_won for p in table.players.values()), default=0)
    is_last = max_wins >= WINS_TO_WIN or table.round_num >= MAX_ROUNDS

    embed = discord.Embed(
        title=f"\U0001f3ae Round {table.round_num} (Time's Up!)",
        colour=discord.Colour.dark_grey(),
    )
    embed.description = (
        f"Nobody got it in {ROUND_TIME} seconds!\n\n"
        f"It was **{name}**!"
    )
    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    if not is_last:
        embed.set_footer(text="Next round in a few seconds\u2026")
    else:
        embed.set_footer(text="Final round \u2014 calculating results\u2026")
    return embed


def _final_embed(
    table: ValTable, *, payouts: dict[int, int], balances: dict[int, int],
) -> discord.Embed:
    max_wins = max((p.rounds_won for p in table.players.values()), default=0)
    is_refund = max_wins == 0

    embed = discord.Embed(
        title="\U0001f3ae Guess the Valorant! \u2014 Results",
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


# ── Solo embeds ──────────────────────────────────────────────────────────────


def _solo_playing_embed(
    session: SoloSession, remaining: int | None = None,
) -> discord.Embed:
    entry = session.current_entry
    entry_type = entry[0]
    cat_label = _CATEGORY_LABELS.get(session.category, session.category)

    type_labels = {"agent": "Agent", "weapon": "Weapon", "map": "Map"}
    type_label = type_labels.get(entry_type, "???")

    embed = discord.Embed(
        title=f"\U0001f3ae Solo Mode \u2014 Round {session.round_num} ({type_label})",
        colour=discord.Colour.dark_red(),
    )

    embed.description = _build_hints(entry, session.hint_level) + "\n\n**Type your answer in chat!**"

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
    name = _get_name(entry)
    solve_time = session.last_solve_time - session.round_start_time

    embed = discord.Embed(
        title=f"\U0001f3ae Round {session.round_num} \u2705",
        colour=discord.Colour.green(),
    )
    embed.description = (
        f"**{name}**! \u2014 {solve_time:.1f}s\n\n"
        f"\U0001f525 Streak: **{session.streak}** \u2502 "
        f"Best: **{session.best_streak}** \u2502 "
        f"Total: **{session.total_correct}**"
    )
    embed.set_footer(text="Next round in a few seconds\u2026")
    return embed


def _solo_gameover_embed(session: SoloSession) -> discord.Embed:
    entry = session.current_entry
    name = _get_name(entry)

    embed = discord.Embed(
        title="\U0001f3ae Solo Mode \u2014 Game Over",
        colour=discord.Colour.dark_grey(),
    )
    pct = session.total_correct / max(session.round_num, 1) * 100
    embed.description = (
        f"Time's up! It was **{name}**.\n\n"
        f"\U0001f3c6 **Best Streak:** {session.best_streak}\n"
        f"\u2705 **Total Correct:** {session.total_correct} / {session.round_num}\n"
        f"\U0001f4ca **Accuracy:** {pct:.0f}%"
    )
    embed.set_footer(
        text=f"{session.display_name} \u2502 Use /valorant-solo to play again!",
    )
    return embed


# ── Modals ───────────────────────────────────────────────────────────────────


class JoinValModal(ui.Modal):
    amount = ui.TextInput(
        label="Bet amount (coins)", placeholder="e.g. 100",
        required=True, max_length=10,
    )

    def __init__(
        self, table: ValTable, view: "ValTableView", balance: int,
    ) -> None:
        super().__init__(title="Join Valorant Guess!")
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
        self.table.players[uid] = ValPlayer(
            user_id=uid, display_name=interaction.user.display_name, bet=amt,
        )
        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self.table_view,
        )


# ── View ─────────────────────────────────────────────────────────────────────


_CATEGORY_OPTIONS = [
    discord.SelectOption(
        label="Agents", value="agents",
        description="Guess the Valorant agent", emoji="\u2694\ufe0f", default=True,
    ),
    discord.SelectOption(
        label="Weapons", value="weapons",
        description="Guess the Valorant weapon", emoji="\U0001f52b",
    ),
    discord.SelectOption(
        label="Maps", value="maps",
        description="Guess the Valorant map", emoji="\U0001f5fa\ufe0f",
    ),
    discord.SelectOption(
        label="Everything", value="all",
        description="Agents, weapons, and maps mixed", emoji="\U0001f30d",
    ),
]


class ValTableView(ui.View):
    def __init__(
        self, table: ValTable, active_tables: dict[int, ValTable],
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
        self.bet_join_btn.disabled = not betting
        self.rebet_btn.disabled = not betting or not self.table.last_bets
        self.leave_btn.disabled = not betting
        self.close_btn.disabled = racing
        self.category_select.disabled = not betting

    def _pick_entry(self) -> tuple:
        """Pick a random entry matching the table's category, avoiding repeats."""
        return _pick_entry(self.table.category, self.table.used_ids)

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
        self.table.players[uid] = ValPlayer(
            user_id=uid, display_name=interaction.user.display_name, bet=0,
        )
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    @ui.button(
        label="Bet", style=discord.ButtonStyle.primary, emoji="\U0001fa99", row=0,
    )
    async def bet_join_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        """Join with a coin wager on top of ELO."""
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
        await interaction.response.send_modal(JoinValModal(self.table, self, bal))

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
        if amt > 0:
            try:
                await queries.update_casino_balance(str(uid), -amt)
            except ValueError:
                bal = await queries.get_or_create_casino_wallet(str(uid))
                await interaction.response.send_message(
                    f"Not enough coins for {amt}c re-bet! (have {bal}c)", ephemeral=True,
                )
                return
        self.table.players[uid] = ValPlayer(
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
        if player.bet > 0:
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
        placeholder="Category: Agents",
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

        entry = self._pick_entry()
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

            next_events: list[float] = []
            if table.hint_level < 2 and hint2_time > now:
                next_events.append(hint2_time - now)
            if table.hint_level < 3 and hint3_time > now:
                next_events.append(hint3_time - now)
            next_events.append(min(5.0, remaining))
            wait = min(next_events)

            try:
                await asyncio.wait_for(table.round_solved.wait(), timeout=wait)
                return True
            except asyncio.TimeoutError:
                if table.round_winner is not None:
                    return True

                now2 = time.monotonic()
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
                    entry = self._pick_entry()
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

        if pot > 0:
            if max_wins == 0:
                payouts = {uid: p.bet for uid, p in table.players.items()}
                for uid, refund in payouts.items():
                    if refund > 0:
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
        else:
            payouts = {uid: 0 for uid in table.players}

        balances: dict[int, int] = {}
        for uid in table.players:
            bal = await queries.get_casino_balance(str(uid))
            balances[uid] = bal or 0

        for uid, p in table.players.items():
            payout = payouts.get(uid, 0)
            await queries.log_casino_result(str(uid), "valorant", p.bet, payout)

        return payouts, balances

    async def _end_game(self) -> None:
        table = self.table
        table.phase = "closed"

        payouts, balances = await self._compute_and_apply_payouts()

        max_wins = max((p.rounds_won for p in table.players.values()), default=0)
        if max_wins > 0 and len(table.players) >= 2:
            sorted_players = sorted(
                table.players.values(),
                key=lambda p: p.rounds_won,
                reverse=True,
            )
            finish_order = [p.user_id for p in sorted_players]
            try:
                await update_elo_multiplayer(finish_order, "valorant", "valorant")
            except Exception:
                pass

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
                if p.bet > 0:
                    try:
                        await queries.update_casino_balance(str(p.user_id), p.bet)
                    except Exception:
                        pass
            embed = discord.Embed(
                title="\U0001f3ae Valorant Table \u2014 Closed",
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
            if p.bet > 0:
                try:
                    await queries.update_casino_balance(str(p.user_id), p.bet)
                except Exception:
                    pass

        table.phase = "closed"
        self.active_tables.pop(table.channel_id, None)

        if table.message:
            try:
                embed = discord.Embed(
                    title="\U0001f3ae Valorant Table \u2014 Timed Out",
                    description="Table timed out. All bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                pass


# ── Solo category choices ────────────────────────────────────────────────────


_SOLO_CATEGORY_CHOICES = [
    app_commands.Choice(name="Agents", value="agents"),
    app_commands.Choice(name="Weapons", value="weapons"),
    app_commands.Choice(name="Maps", value="maps"),
    app_commands.Choice(name="Everything", value="all"),
]


# ── Cog ──────────────────────────────────────────────────────────────────────


class ValorantCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, ValTable] = {}
        self.active_solos: dict[int, SoloSession] = {}

    @app_commands.command(
        name="valorant",
        description="Guess the Valorant agent, weapon, or map from hints!",
    )
    async def valorant(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            await interaction.response.send_message(
                "There's already a Valorant game in this channel!",
                ephemeral=True,
            )
            return

        await queries.get_or_create_casino_wallet(str(interaction.user.id))

        table = ValTable(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
        )
        self.active_tables[channel_id] = table

        view = ValTableView(table, self.active_tables)
        embed = _betting_embed(table)
        await interaction.response.send_message(embed=embed, view=view)
        table.message = await interaction.original_response()

    @app_commands.command(
        name="valorant-solo",
        description="Solo Valorant quiz \u2014 unlimited rounds, no betting!",
    )
    @app_commands.describe(category="What to guess")
    @app_commands.choices(category=_SOLO_CATEGORY_CHOICES)
    async def valorant_solo(
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

        cat = category.value if category else "agents"
        session = SoloSession(
            user_id=uid,
            channel_id=interaction.channel_id,
            display_name=interaction.user.display_name,
            category=cat,
        )
        self.active_solos[uid] = session

        entry = _pick_entry(cat, session.used_ids)
        session.current_entry = entry
        session.round_num = 1
        session.round_start_time = time.monotonic()

        embed = _solo_playing_embed(session)
        await interaction.response.send_message(embed=embed)
        session.message = await interaction.original_response()
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
                    entry = _pick_entry(session.category, session.used_ids)
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
                    session.streak = 0
                    session.phase = "closed"
                    if session.message:
                        try:
                            await session.message.edit(
                                embed=_solo_gameover_embed(session),
                            )
                        except discord.HTTPException:
                            pass
                    break

        except asyncio.CancelledError:
            pass
        finally:
            session.phase = "closed"
            self.active_solos.pop(session.user_id, None)

    @commands.Cog.listener("on_message")
    async def on_message(self, message: discord.Message) -> None:
        """Listen for chat guesses during active Valorant rounds."""
        if message.author.bot:
            return

        uid = message.author.id
        guess = message.content.strip()

        # ── Multiplayer table in this channel ──
        table = self.active_tables.get(message.channel.id)
        if table is not None and table.phase == "playing":
            if uid in table.players and table.round_winner is None:
                if len(guess) < 2:
                    return
                alpha_chars = sum(1 for c in guess if c.isalpha())
                if alpha_chars < len(guess) * 0.5:
                    return
                table.round_messages.append(message)
                if check_answer(guess, table.current_entry):
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
            and solo.phase == "playing"
            and solo.channel_id == message.channel.id
        ):
            if len(guess) < 2:
                return
            alpha_chars = sum(1 for c in guess if c.isalpha())
            if alpha_chars < len(guess) * 0.5:
                return
            if guess.lower() in ("quit", "stop", "end"):
                solo.phase = "closed"
                if solo.race_task and not solo.race_task.done():
                    solo.race_task.cancel()
                if solo.message:
                    try:
                        await solo.message.edit(embed=_solo_gameover_embed(solo))
                    except discord.HTTPException:
                        pass
                self.active_solos.pop(uid, None)
                return
            solo.round_messages.append(message)
            if check_answer(guess, solo.current_entry):
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
    await bot.add_cog(ValorantCog(bot))
