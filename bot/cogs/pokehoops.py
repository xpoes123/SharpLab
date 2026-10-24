"""Poké Hoops — draft five Gen 1 Pokémon with buttons (3 options a pick, a Legendary first), then
the auto-lineup plays an 82-game season against the league and gets graded. 82-0 is the dream.

Same engine, seeds and verification as the web arcade (shared/pokehoops.py): the bot draws the
draft seed, every pack comes from the vendored JS engine via node, and finish_season() replays
the picks, sims the season server-side and records it on the shared leaderboard
(sharplab.djiang.xyz/pokehoops.html). Launched with /play pokehoops."""
from __future__ import annotations

import logging

import discord
from discord import ui
from discord.ext import commands

from db import queries
from shared import pokehoops as ph

log = logging.getLogger(__name__)

DRAFT_TIMEOUT = 300  # seconds per pick before the draft expires
POS = ["PG", "SG", "SF", "PF", "C"]
EFFECTS = {
    "SHARPSHOOTER": "Sharpshooter", "SLASHER": "Slasher", "POSTERIZER": "Posterizer", "LOCKDOWN": "Lockdown",
    "RIM_PROTECTOR": "Rim protector", "GLASS_CLEANER": "Glass cleaner", "FLOOR_GENERAL": "Floor general",
    "PICKPOCKET": "Pickpocket", "CLUTCH": "Clutch", "HEAT_CHECK": "Heat check", "INTIMIDATOR": "Intimidator",
    "HYPNOTIST": "Hypnotist",
}
SKILL_LABELS = [("shoot", "SHT"), ("finish", "FIN"), ("handle", "HND"), ("perD", "PER-D"), ("intD", "RIM-D"), ("reb", "REB")]
ORANGE = 0xEE8130


def grade_colour(wins: int) -> int:
    if wins == 82:
        return 0xE0AF68
    if wins >= 64:
        return 0x57F287
    if wins >= 50:
        return 0x7AA2F7
    if wins >= 35:
        return 0xBB9AF7
    return 0xED4245


def option_embed(c: dict, legend: bool) -> discord.Embed:
    sk = " · ".join(f"{lab} {c['skills'][k]}" for k, lab in SKILL_LABELS)
    fx = EFFECTS.get(c["effect"], c["effect"])
    e = discord.Embed(
        title=f"{c['name']} — {c['pos']} {c['ovr']}",
        description=(f"*{c['archetype']}* · {'/'.join(c['types'])}\n"
                     f"**{c['move']}** — {fx}\n`{sk}`\n{c['blurb']}"),
        colour=0xE0AF68 if legend else 0x7AA2F7,
    )
    e.set_thumbnail(url=ph.SPRITE_URL.format(c["n"]))
    return e


def pick_embeds(name: str, picks: list[dict], pack: list[dict]) -> list[discord.Embed]:
    i = len(picks)
    so_far = " · ".join(p["name"] for p in picks) or "—"
    head = discord.Embed(
        title=f"🏀 Poké Hoops — pick {i + 1} of 5{' · Legendary' if i == 0 else ''}",
        description=(f"**{name}** is drafting. {'Choose your franchise Legendary.' if i == 0 else 'Take one.'}\n"
                     f"Picks: {so_far}"),
        colour=ORANGE,
    )
    return [head] + [option_embed(c, i == 0) for c in pack]


def strip_lines(strip: str) -> str:
    sq = "".join("🟩" if ch == "W" else "🟥" for ch in strip)
    return "\n".join(sq[k:k + 41] for k in range(0, len(sq), 41))


def season_embed(name: str, res: dict) -> discord.Embed:
    w, l = res["wins"], res["losses"]
    lines = []
    for i, c in enumerate(res["lineup_cards"]):
        fx = EFFECTS.get(c["effect"], c["effect"])
        lines.append(f"`{POS[i]:<2}` **{c['name']}** — {c['move']} ({fx})")
    tag = []
    if res["is_new_best"]:
        tag.append("🏆 **New personal best!**" + (f" (was {res['prev_best']}–{82 - res['prev_best']})"
                                                 if res["prev_best"] is not None else ""))
    if res.get("rank"):
        tag.append(f"#{res['rank']} on the board")
    if res.get("coins"):
        tag.append(f"+{res['coins']} 🪙")
    e = discord.Embed(
        title=f"🏀 {name}'s season: {w}–{l} · {res['grade']}" + (" 🎉" if w == 82 else ""),
        description="\n".join(lines) + ("\n\n" + " · ".join(tag) if tag else ""),
        colour=grade_colour(w),
    )
    e.add_field(name="Season", value=strip_lines(res["strip"]), inline=False)
    ts, bg = res["top_scorer"], res["best_game"]
    e.add_field(name="Top scorer", value=f"{ts['name']} ({ts['pos']}) — team-high in {ts['led']}, season-high {ts['high']}")
    e.add_field(name="Best game", value=f"G{bg['g']}: {bg['us']}–{bg['them']} vs {bg['opp_name']}" if bg else "—")
    e.add_field(name="Streak · PPG", value=f"{res['streak']} straight · {res['ppg']} for / {res['oppg']} against")
    e.add_field(name="Replay + leaderboard", value=f"{ph.WEB_URL}?s={res['id']}", inline=False)
    e.set_thumbnail(url=ph.SPRITE_URL.format(ts["n"]))
    return e


class DraftView(ui.View):
    def __init__(self, cog: "PokeHoopsCog", user: discord.abc.User, seed: int, pack: list[dict]) -> None:
        super().__init__(timeout=DRAFT_TIMEOUT)
        self.cog, self.user, self.seed = cog, user, seed
        self.picks: list[dict] = []
        self.pack = pack
        self.message: discord.Message | None = None
        self.finished = False
        self._build()

    def _build(self) -> None:
        self.clear_items()
        for c in self.pack:
            btn = ui.Button(label=f"{c['name']} · {c['pos']} {c['ovr']}"[:80],
                            style=discord.ButtonStyle.primary if not self.picks else discord.ButtonStyle.secondary)
            btn.callback = self._make_cb(c)
            self.add_item(btn)

    def _make_cb(self, c: dict):
        async def cb(interaction: discord.Interaction) -> None:
            await self.choose(interaction, c)
        return cb

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(
                "This is someone else's draft — start your own with `/play pokehoops`.", ephemeral=True)
            return False
        return True

    async def choose(self, interaction: discord.Interaction, c: dict) -> None:
        if self.finished or c not in self.pack:
            await interaction.response.defer()
            return
        self.picks.append(c)
        picks = [p["n"] for p in self.picks]
        try:
            nxt = await ph.next_pack(self.seed, picks)
        except (ph.IllegalDraft, ph.EngineUnavailable):
            log.exception("pokehoops next_pack failed")
            await self._abort(interaction, "The sim engine hiccuped — draft cancelled, try again.")
            return
        if nxt:
            self.pack = nxt
            self._build()
            await interaction.response.edit_message(
                embeds=pick_embeds(self.user.display_name, self.picks, nxt), view=self)
            return
        self.finished = True
        self.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(title="🏀 Poké Hoops", colour=ORANGE,
                                description=f"**{self.user.display_name}** drafted "
                                            f"{' · '.join(p['name'] for p in self.picks)}.\nSimming 82 games…"),
            view=None)
        uid = str(self.user.id)
        try:
            res = await ph.finish_season(uid, self.seed, picks, "discord")
        except (ph.IllegalDraft, ph.EngineUnavailable):
            log.exception("pokehoops finish_season failed")
            await interaction.edit_original_response(content="The sim engine hiccuped — no season recorded. Try again.",
                                                     embed=None)
            self.cog.release(self.user.id)
            return
        try:
            await queries.log_casino_result(uid, "pokehoops", 0, res["coins"])
        except Exception:
            log.debug("pokehoops log_casino_result failed", exc_info=True)
        self.cog.release(self.user.id)
        await interaction.edit_original_response(embed=season_embed(self.user.display_name, res))

    async def _abort(self, interaction: discord.Interaction, msg: str) -> None:
        self.finished = True
        self.stop()
        self.cog.release(self.user.id)
        await interaction.response.edit_message(content=msg, embeds=[], view=None)

    async def on_timeout(self) -> None:
        if self.finished:
            return
        self.finished = True
        self.cog.release(self.user.id)
        if self.message is not None:
            try:
                await self.message.edit(content="⏰ Poké Hoops draft expired — no season played.", embeds=[], view=None)
            except discord.HTTPException:
                pass


class PokeHoopsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._active: set[int] = set()  # one draft per person

    def release(self, user_id: int) -> None:
        self._active.discard(user_id)

    async def pokehoops(self, interaction: discord.Interaction) -> None:
        if interaction.user.id in self._active:
            await interaction.response.send_message(
                "You already have a Poké Hoops draft going — finish that one first!", ephemeral=True)
            return
        self._active.add(interaction.user.id)
        try:
            seed = ph.new_seed()
            pack = await ph.next_pack(seed, [])
        except (ph.IllegalDraft, ph.EngineUnavailable):
            log.exception("pokehoops start failed")
            self.release(interaction.user.id)
            await interaction.response.send_message("Poké Hoops is unavailable right now.", ephemeral=True)
            return
        view = DraftView(self, interaction.user, seed, pack)
        try:
            await interaction.response.send_message(embeds=pick_embeds(interaction.user.display_name, [], pack), view=view)
        except discord.HTTPException:
            self.release(interaction.user.id)
            return
        view.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PokeHoopsCog(bot))
