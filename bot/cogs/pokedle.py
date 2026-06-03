"""Pokédle — a Squirdle-style Pokémon DEDUCTION game (problem-solving, not recall).

A mystery Pokémon is chosen. Players guess any Pokémon and get Wordle-style
feedback — generation ⬆️/⬇️/✓, each type 🟩/🟨/⬛, height & weight ⬆️/⬇️/✓ — and
deduce the answer within a handful of guesses. Runs in a dedicated thread so
guesses don't clutter the channel; first to solve wins coins (logged for
XP/achievements)."""
from __future__ import annotations

import asyncio
import logging
import random
import re

import discord
import httpx
from discord import app_commands
from discord.ext import commands

from db import queries

log = logging.getLogger(__name__)

POKEAPI = "https://pokeapi.co/api/v2"
# Per-guess inactivity window grows each guess — later guesses are harder and the
# player is more invested, so they earn more thinking time. Capped so a stalled
# game can't hold the channel's single-game lock for hours.
INACTIVITY_BASE_SECS = 120
INACTIVITY_GROWTH = 1.5
INACTIVITY_MAX_SECS = 1200


def _inactivity_secs(guessed: int) -> float:
    """Timeout for the next guess: base * growth^guessed, capped. guessed is the
    count already made, so the first guess (guessed=0) gets the base window."""
    return min(INACTIVITY_MAX_SECS, INACTIVITY_BASE_SECS * INACTIVITY_GROWTH ** guessed)
NATIONAL_DEX_MAX = 1025  # highest national-dex id to roll for Hard

# Difficulty tiers: secret-pool breadth, guess budget, and base reward (scaled
# down by guesses used). Easy = famous mons + extra guesses; Hard = any Pokémon
# in the dex + fewer guesses.
DIFFICULTIES: dict[str, dict] = {
    "easy":   {"label": "Easy",   "guesses": 10, "reward": 150},
    "medium": {"label": "Medium", "guesses": 8,  "reward": 200},
    "hard":   {"label": "Hard",   "guesses": 6,  "reward": 300},
}

# Recognizable secret pool (guesses can be ANY valid Pokémon — validated live).
POOL = [
    "bulbasaur", "charizard", "blastoise", "butterfree", "pikachu", "raichu", "sandslash",
    "nidoking", "clefable", "ninetales", "arcanine", "poliwrath", "alakazam", "machamp",
    "golem", "rapidash", "slowbro", "gengar", "onix", "hypno", "kingler", "exeggutor",
    "marowak", "hitmonlee", "lickitung", "weezing", "rhydon", "chansey", "kangaskhan",
    "seadra", "starmie", "scyther", "jynx", "electabuzz", "magmar", "pinsir", "tauros",
    "gyarados", "lapras", "ditto", "vaporeon", "jolteon", "flareon", "snorlax", "dragonite",
    "mewtwo", "mew", "meganium", "typhlosion", "feraligatr", "ampharos", "bellossom",
    "azumarill", "sudowoodo", "espeon", "umbreon", "forretress", "steelix", "scizor",
    "heracross", "ursaring", "houndoom", "kingdra", "donphan", "porygon2", "tyranitar",
    "lugia", "ho-oh", "celebi", "sceptile", "blaziken", "swampert", "gardevoir", "breloom",
    "slaking", "aggron", "flygon", "altaria", "milotic", "salamence", "metagross", "latias",
    "latios", "kyogre", "groudon", "rayquaza", "jirachi", "torterra", "infernape", "empoleon",
    "staraptor", "luxray", "roserade", "garchomp", "lucario", "hippowdon", "drapion", "toxicroak",
    "weavile", "magnezone", "rhyperior", "mamoswine", "porygon-z", "gallade", "froslass",
    "rotom", "dialga", "palkia", "giratina", "darkrai", "arceus", "serperior", "emboar",
    "samurott", "zoroark", "krookodile", "darmanitan", "scrafty", "sigilyph", "ferrothorn",
    "klinklang", "eelektross", "haxorus", "beartic", "cryogonal", "golurk", "bisharp",
    "braviary", "hydreigon", "volcarona", "cobalion", "terrakion", "virizion", "reshiram",
    "zekrom", "kyurem", "greninja", "talonflame", "aegislash", "sylveon", "goodra", "trevenant",
    "noivern", "xerneas", "yveltal", "zygarde", "decidueye", "incineroar", "primarina",
    "lycanroc", "mimikyu", "kommo-o", "solgaleo", "lunala", "necrozma", "corviknight",
    "dragapult", "zacian", "zamazenta", "eternatus", "grimmsnarl", "toxapex", "mimikyu",
    "cinderace", "rillaboom", "inteleon", "meowscarada", "skeledirge", "quaquaval",
    "gholdengo", "kingambit", "annihilape", "garganacl", "tinkaton", "baxcalibur",
    "koraidon", "miraidon", "flutter-mane", "iron-valiant", "ogerpon",
]

# Easy tier: only instantly-recognizable Pokémon (mostly Gen 1 icons).
EASY_POOL = [
    "bulbasaur", "charmander", "squirtle", "venusaur", "charizard", "blastoise",
    "pikachu", "raichu", "jigglypuff", "meowth", "psyduck", "machamp", "gengar",
    "onix", "gyarados", "lapras", "eevee", "vaporeon", "jolteon", "flareon",
    "snorlax", "dragonite", "mewtwo", "mew", "magikarp", "ditto", "arcanine",
    "alakazam", "golem", "slowbro", "kangaskhan", "scyther", "pinsir", "tauros",
    "articuno", "zapdos", "moltres", "pidgeot", "nidoking", "clefairy",
]

_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9}
_mon_cache: dict[str, dict | None] = {}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


async def _fetch_mon(name: str) -> dict | None:
    """Return {name, gen, types, height, weight} for a Pokémon, or None if invalid.
    Cached. Combines /pokemon (types/size) and /pokemon-species (generation)."""
    key = _norm(name)
    if key in _mon_cache:
        return _mon_cache[key]
    slug = re.sub(r"[^a-z0-9-]", "", name.lower().strip().replace(" ", "-"))
    result = None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            p = await client.get(f"{POKEAPI}/pokemon/{slug}")
            if p.status_code == 200:
                pd = p.json()
                sp = await client.get(f"{POKEAPI}/pokemon-species/{pd['species']['name']}")
                gen = 0
                if sp.status_code == 200:
                    roman = sp.json()["generation"]["name"].split("-")[-1]
                    gen = _ROMAN.get(roman, 0)
                result = {
                    "name": pd["name"], "gen": gen,
                    "types": [t["type"]["name"] for t in sorted(pd["types"], key=lambda t: t["slot"])],
                    "height": pd["height"], "weight": pd["weight"],
                }
    except Exception:
        log.debug("pokeapi fetch failed for %s", name, exc_info=True)
        return None  # don't cache transient failures
    _mon_cache[key] = result
    return result


def _arrow(guess_v: int, secret_v: int) -> str:
    if guess_v == secret_v:
        return "✅"
    return "⬆️" if secret_v > guess_v else "⬇️"


def _type_cells(guess_types: list[str], secret_types: list[str]) -> str:
    cells = []
    for i in range(2):
        gt = guess_types[i] if i < len(guess_types) else None
        if gt is None:
            cells.append("`—`")
        elif i < len(secret_types) and gt == secret_types[i]:
            cells.append(f"🟩`{gt[:4]}`")
        elif gt in secret_types:
            cells.append(f"🟨`{gt[:4]}`")
        else:
            cells.append(f"⬛`{gt[:4]}`")
    return " ".join(cells)


def _feedback_row(guess: dict, secret: dict) -> str:
    return (f"**{guess['name'].title()}** — Gen {guess['gen']}{_arrow(guess['gen'], secret['gen'])} · "
            f"{_type_cells(guess['types'], secret['types'])} · "
            f"Ht {_arrow(guess['height'], secret['height'])} · Wt {_arrow(guess['weight'], secret['weight'])}")


class PokedleCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._active: set[int] = set()  # user ids with a live game (one per person)

    @app_commands.command(name="pokedle", description="Pokédle — deduce the mystery Pokémon from clues")
    @app_commands.describe(difficulty="Easy: famous mons, 10 guesses · Medium: default · Hard: any Pokémon, 6 guesses")
    @app_commands.choices(difficulty=[
        app_commands.Choice(name="Easy", value="easy"),
        app_commands.Choice(name="Medium", value="medium"),
        app_commands.Choice(name="Hard", value="hard"),
    ])
    async def pokedle(self, interaction: discord.Interaction,
                      difficulty: app_commands.Choice[str] | None = None) -> None:
        # One game per person (each runs in its own thread), so a live game no
        # longer blocks the whole channel — others can start their own.
        if interaction.user.id in self._active:
            await interaction.response.send_message(
                "You already have a Pokédle game running — finish it first!", ephemeral=True)
            return
        # Claim the per-user slot immediately so a fast double-invoke can't open two
        # games. Held across the whole flow and always released in the finally.
        self._active.add(interaction.user.id)
        thread: discord.Thread | None = None
        try:
            cfg = DIFFICULTIES[difficulty.value if difficulty else "medium"]
            await interaction.response.defer()
            secret = await self._pick_secret(cfg)
            if not secret:
                await interaction.followup.send("Couldn't reach PokéAPI right now — try again shortly.")
                return

            # Play in a dedicated thread so guesses don't clutter the channel.
            anchor = await interaction.followup.send(embed=discord.Embed(
                title="🔍 Pokédle",
                description=(f"**{interaction.user.display_name}** started a **{cfg['label']}** game "
                             "— play in the thread below 👇"),
                colour=0xF1C40F,
            ))
            # Create the thread off the channel (not the WebhookMessage): a followup
            # message has no guild attached, so anchor.create_thread() raises a bare
            # ValueError that the HTTPException handler below would miss.
            try:
                thread = await interaction.channel.create_thread(
                    name=f"Pokédle ({cfg['label']}) — {interaction.user.display_name}",
                    message=anchor,
                )
            except (discord.HTTPException, ValueError):
                await interaction.followup.send(
                    "Couldn't create a thread — the bot needs **Create Public Threads** permission here.")
                return

            await self._run(thread, secret, cfg)
        finally:
            self._active.discard(interaction.user.id)
            if thread is not None:
                try:
                    await thread.edit(archived=True)
                except discord.HTTPException:
                    pass

    async def _pick_secret(self, cfg: dict) -> dict | None:
        """Choose a mystery Pokémon for the tier. Hard rolls the whole National
        Dex by id; easy/medium sample a curated pool. Tolerates a few bad slugs."""
        if cfg["label"] == "Hard":
            for _ in range(8):
                mon = await _fetch_mon(str(random.randint(1, NATIONAL_DEX_MAX)))
                if mon:
                    return mon
            return None
        pool = EASY_POOL if cfg["label"] == "Easy" else POOL
        for name in random.sample(pool, k=min(8, len(pool))):
            mon = await _fetch_mon(name)
            if mon:
                return mon
        return None

    async def _run(self, thread: discord.Thread, secret: dict, cfg: dict) -> None:
        max_guesses = cfg["guesses"]
        await thread.send(embed=discord.Embed(
            title=f"🔍 Pokédle ({cfg['label']}) — guess the mystery Pokémon!",
            description=(
                f"Type any Pokémon name to guess. You have **{max_guesses}** guesses.\n"
                "Feedback per guess:\n"
                "• **Gen / Height / Weight**: ✅ exact · ⬆️ secret is higher · ⬇️ lower\n"
                "• **Types**: 🟩 right type & slot · 🟨 right type, wrong slot · ⬛ not a type\n\n"
                "*First to solve wins coins. Go!*"
            ),
            colour=0xF1C40F,
        ))

        guessed = 0
        history: list[str] = []
        while guessed < max_guesses:
            try:
                msg = await self.bot.wait_for(
                    "message",
                    timeout=_inactivity_secs(guessed),
                    check=lambda m: m.channel.id == thread.id and not m.author.bot and len(m.content) <= 25,
                )
            except asyncio.TimeoutError:
                await thread.send(f"⏰ Pokédle ended — it was **{secret['name'].title()}**. (no guesses)")
                return

            guess = await _fetch_mon(msg.content)
            if guess is None:
                continue  # not a valid Pokémon name — ignore quietly (could be normal chat)

            guessed += 1
            if _norm(guess["name"]) == _norm(secret["name"]):
                reward = max(cfg["reward"] - (guessed - 1) * 20, 40)
                try:
                    await queries.give_casino_coins(str(msg.author.id), reward)
                    await queries.log_casino_result(str(msg.author.id), "pokedle", 0, reward)
                except Exception:
                    log.debug("pokedle reward failed", exc_info=True)
                history.append(f"✅ **{msg.author.display_name}** got it!")
                await thread.send(embed=discord.Embed(
                    title=f"🎉 {msg.author.display_name} solved it — {secret['name'].title()}!",
                    description="\n".join(history) + f"\n\nSolved in **{guessed}** guess(es) · **+{reward}** coins 🪙",
                    colour=0x57F287,
                ))
                return

            history.append(_feedback_row(guess, secret))
            left = max_guesses - guessed
            await thread.send(embed=discord.Embed(
                description="\n".join(history) + (f"\n\n*{left} guess(es) left*" if left else ""),
                colour=0xF1C40F if left else 0xED4245,
            ))

        await thread.send(f"❌ Out of guesses — it was **{secret['name'].title()}**!")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PokedleCog(bot))
