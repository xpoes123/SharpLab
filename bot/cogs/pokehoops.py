"""Poké Hoops — draft eight Gen 1–2 Pokémon with buttons (3 options a pick, a Legendary first), then
coach them: five starters at PG..C (select menus) and which spot each of the three reserves backs
up (a second message with three selects + Lock in). Then an 82-game season against the league,
graded. 82-0 is the dream. Overall ratings are never shown — skills, positions, moves only.

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
        title=f"{c['name']} — plays {plays(c)}",
        description=(f"*{c['archetype']}* · {'/'.join(c['types'])}\n"
                     f"**{c['move']}** — {fx}\n`{sk}`\n{c['blurb']}"),
        colour=0xE0AF68 if legend else 0x7AA2F7,
    )
    e.set_thumbnail(url=ph.SPRITE_URL.format(c["n"]))
    return e


def plays(c: dict) -> str:
    return "/".join(c.get("plays") or [c.get("pos") or "?"])


def pick_embeds(name: str, picks: list[dict], pack: list[dict]) -> list[discord.Embed]:
    i = len(picks)
    so_far = " · ".join(p["name"] for p in picks) or "—"
    head = discord.Embed(
        title=f"🏀 Poké Hoops — pick {i + 1} of {ph.PICKS}{' · Legendary' if i == 0 else ''}",
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
    backs = res.get("team", {}).get("backs", [])
    if res.get("bench_cards"):
        lines.append("Bench: " + " · ".join(f"{c['name']} (backs {POS[s]})" for c, s in zip(res["bench_cards"], backs)))
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


def lineup_embed(name: str, coach: "Coach", note: str = "") -> discord.Embed:
    by_n = coach.cards
    rows = [f"`{POS[i]:<2}` **{by_n[n]['name']}** — plays {plays(by_n[n])}" for i, n in enumerate(coach.five)]
    rows.append("Bench: " + " · ".join(f"{by_n[n]['name']} (backs {POS[s]})" for n, s in zip(coach.bench, coach.backs)))
    return discord.Embed(
        title=f"🏀 {name}'s lineup — you're the coach",
        description=("Pick a starter for each spot below (picking someone already in the lineup swaps them). "
                     "Then, in the next message, choose which spot each reserve backs up and **Lock in**.\n\n"
                     + "\n".join(rows) + (f"\n\n⚠️ {note}" if note else "")),
        colour=ORANGE,
    )


class Coach:
    """Lineup state after the draft: starts as draft order (backs PG, SG, SF) — never auto-optimized."""

    def __init__(self, cards: list[dict]) -> None:
        self.cards = {c["n"]: c for c in cards}
        self.picks = [c["n"] for c in cards]
        t = ph.default_team(self.picks)
        self.five, self.bench, self.backs = t["five"], t["bench"], t["backs"]

    def put(self, spot: int, n: int) -> None:
        """Start n at spot; whoever was there takes n's old place (another spot or n's bench slot)."""
        old = self.five[spot]
        if n == old or n not in self.picks:
            return
        if n in self.five:
            self.five[self.five.index(n)] = old
        else:
            self.bench[self.bench.index(n)] = old
        self.five[spot] = n

    def team(self) -> dict:
        return {"five": list(self.five), "bench": list(self.bench), "backs": list(self.backs)}

    def problem(self) -> str | None:
        m = self.five + self.bench
        if len(set(m)) != len(m) or sorted(m) != sorted(self.picks):
            return "Each drafted player must appear exactly once — fix the starters."
        if len(self.backs) != len(self.bench) or not all(0 <= s < 5 for s in self.backs):
            return "Every reserve needs a spot to back up (PG..C)."
        return None


class _Owned(ui.View):
    def __init__(self, user: discord.abc.User) -> None:
        super().__init__(timeout=DRAFT_TIMEOUT)
        self.user = user

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(
                "This is someone else's draft — start your own with `/play pokehoops`.", ephemeral=True)
            return False
        return True


class DraftView(_Owned):
    def __init__(self, cog: "PokeHoopsCog", user: discord.abc.User, seed: int, pack: list[dict]) -> None:
        super().__init__(user)
        self.cog, self.seed = cog, seed
        self.picks: list[dict] = []
        self.pack = pack
        self.message: discord.Message | None = None
        self.finished = False
        self._build()

    def _build(self) -> None:
        self.clear_items()
        for c in self.pack:
            btn = ui.Button(label=f"{c['name']} · {plays(c)}"[:80],
                            style=discord.ButtonStyle.primary if not self.picks else discord.ButtonStyle.secondary)
            btn.callback = self._make_cb(c)
            self.add_item(btn)

    def _make_cb(self, c: dict):
        async def cb(interaction: discord.Interaction) -> None:
            await self.choose(interaction, c)
        return cb

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
        # draft done → the coaching step: starters on this message, bench + Lock in on a follow-up
        self.finished = True
        self.stop()
        coach = Coach(self.picks)
        starters = StartersView(self.cog, self.user, self.seed, coach)
        await interaction.response.edit_message(embeds=[lineup_embed(self.user.display_name, coach)], view=starters)
        starters.message = self.message
        bench = BenchView(starters)
        starters.bench = bench
        bench.message = await interaction.followup.send(
            content=bench.content(), view=bench, wait=True)

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


class StartersView(_Owned):
    """Five selects, PG..C, each listing all eight drafted players (the current starter preselected)."""

    def __init__(self, cog: "PokeHoopsCog", user: discord.abc.User, seed: int, coach: Coach) -> None:
        super().__init__(user)
        self.cog, self.seed, self.coach = cog, seed, coach
        self.message: discord.Message | None = None
        self.bench: "BenchView | None" = None
        self.finished = False
        self._build()

    def _build(self) -> None:
        self.clear_items()
        for spot in range(5):
            sel = ui.Select(placeholder=POS[spot], row=spot, options=[
                discord.SelectOption(label=f"{POS[spot]}: {c['name']}"[:100], value=str(n),
                                     description=f"plays {plays(c)} · {c['archetype']}"[:100],
                                     default=n == self.coach.five[spot])
                for n, c in self.coach.cards.items()])
            sel.callback = self._make_cb(spot, sel)
            self.add_item(sel)

    def _make_cb(self, spot: int, sel: ui.Select):
        async def cb(interaction: discord.Interaction) -> None:
            await self.set_starter(interaction, spot, int(sel.values[0]))
        return cb

    async def set_starter(self, interaction: discord.Interaction, spot: int, n: int) -> None:
        if self.finished:
            await interaction.response.defer()
            return
        self.coach.put(spot, n)
        self._build()
        await interaction.response.edit_message(embeds=[lineup_embed(self.user.display_name, self.coach)], view=self)
        await self.bench.refresh()

    async def lock_in(self, interaction: discord.Interaction) -> None:
        if self.finished:
            await interaction.response.defer()
            return
        bad = self.coach.problem()
        if bad:
            await interaction.response.send_message(f"⚠️ {bad}", ephemeral=True)
            return
        self.finished = True
        self.stop()
        self.bench.stop()
        team = self.coach.team()
        await interaction.response.edit_message(
            content=None, embed=discord.Embed(title="🏀 Poké Hoops", colour=ORANGE,
                                              description=f"🔒 **{self.user.display_name}** locked in. Simming 82 games…"),
            view=None)
        if self.message is not None:
            try:
                await self.message.edit(embeds=[lineup_embed(self.user.display_name, self.coach)], view=None)
            except discord.HTTPException:
                pass
        uid = str(self.user.id)
        try:
            res = await ph.finish_season(uid, self.seed, self.coach.picks, team, "discord")
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

    async def on_timeout(self) -> None:
        if self.finished:
            return
        self.finished = True
        self.cog.release(self.user.id)
        for msg in (self.message, self.bench.message if self.bench else None):
            if msg is not None:
                try:
                    await msg.edit(content="⏰ Poké Hoops lineup expired — no season played.", embeds=[], view=None)
                except discord.HTTPException:
                    pass


class BenchView(_Owned):
    """Three selects (one per reserve: which spot it backs up) + Lock in. Rebuilt when the starters change."""

    def __init__(self, starters: StartersView) -> None:
        super().__init__(starters.user)
        self.starters, self.coach = starters, starters.coach
        self.timeout = None  # the starters view owns the expiry
        self.message: discord.Message | None = None
        self._build()

    def content(self) -> str:
        return "**Bench** — each reserve backs up one spot (two at one spot split the minutes). Then lock it in."

    def _build(self) -> None:
        self.clear_items()
        cards = self.coach.cards
        for j, n in enumerate(self.coach.bench):
            sel = ui.Select(placeholder=f"{cards[n]['name']} backs up…", row=j, options=[
                discord.SelectOption(label=f"{cards[n]['name']} backs up {POS[s]}"[:100], value=str(s),
                                     description=f"behind {cards[self.coach.five[s]]['name']} · {cards[n]['name']} plays {plays(cards[n])}"[:100],
                                     default=s == self.coach.backs[j])
                for s in range(5)])
            sel.callback = self._make_cb(j, sel)
            self.add_item(sel)
        btn = ui.Button(label="Lock in lineup", style=discord.ButtonStyle.success, emoji="🔒", row=3)
        btn.callback = self.starters.lock_in
        self.add_item(btn)

    def _make_cb(self, j: int, sel: ui.Select):
        async def cb(interaction: discord.Interaction) -> None:
            await self.set_backs(interaction, j, int(sel.values[0]))
        return cb

    async def set_backs(self, interaction: discord.Interaction, j: int, spot: int) -> None:
        if self.starters.finished:
            await interaction.response.defer()
            return
        self.coach.backs[j] = spot
        self._build()
        await interaction.response.edit_message(content=self.content(), view=self)
        if self.starters.message is not None:
            try:
                await self.starters.message.edit(embeds=[lineup_embed(self.user.display_name, self.coach)])
            except discord.HTTPException:
                pass

    async def refresh(self) -> None:
        self._build()
        if self.message is not None:
            try:
                await self.message.edit(content=self.content(), view=self)
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
