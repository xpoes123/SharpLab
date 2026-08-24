"""Sports trading cards — open season-based packs of NBA/NFL/MLB players, collect
serial-numbered cards across rarity tiers, chase rookies + holo/gem parallels,
quick-sell dupes for coins, and trade card-for-card. Casino-coin economy.

Port of the nsba-markets pack system. Engine lives in shared/cards.py; all DB
access in db/queries.py. See docs/superpowers/specs/2026-08-11-sports-card-packs-design.md.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from db import queries
from shared import cards as engine

log = logging.getLogger(__name__)

SPORTS = ["nba", "nfl", "mlb", "pokemon"]
SPORT_CHOICES = [app_commands.Choice(name=s.upper(), value=s) for s in SPORTS]
ALERT_CHANNEL_KEY = "cards_alert_channel"

RARITY_EMOJI = {
    "common": "⚪", "uncommon": "🟢", "rare": "🔵", "epic": "🟣", "legendary": "🟡",
}
RARITY_COLOR = {
    "common": 0x7884B0, "uncommon": 0x9ECE6A, "rare": 0x7AA2F7,
    "epic": 0xBB9AF7, "legendary": 0xE0AF68,
}
GEM_EMOJI = {"chrome": "🔗", "sapphire": "🔷", "ruby": "🔴", "black_lotus": "🌸"}
SPORT_EMOJI = {"nba": "🏀", "nfl": "🏈", "mlb": "⚾", "pokemon": "🔴"}

# --- Set-completion reward ----------------------------------------------------
# Owning >=1 of every design in a set pays a one-time coin bonus. Scaled to the
# set: a fraction of the set's total book value (one of each design), which grows
# naturally with both design count and rarity mix. Floored so tiny sets still pay.
SET_COMPLETION_RATE = 0.25
SET_COMPLETION_MIN = 250

# --- Dupe trade-up (crafting) -------------------------------------------------
# Consume N duplicate cards of a rarity -> mint 1 random card of the next rarity
# up. Only "dupes" (copies beyond the first per design) are eligible, so a
# trade-up can never destroy a completed checklist. Legendary can't trade up.
TRADEUP_COST = {"common": 5, "uncommon": 4, "rare": 3, "epic": 3}
TRADEUP_CHOICES = [app_commands.Choice(name=r.title(), value=r) for r in TRADEUP_COST]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _box_summary_embed(cards: list[dict], guaranteed: bool, title: str, set_name: str) -> discord.Embed:
    """Summary reveal for a 36-pack box: rarity counts + highlighted notable pulls."""
    counts: dict[str, int] = {}
    for c in cards:
        counts[c["rarity"]] = counts.get(c["rarity"], 0) + 1
    breakdown = "  ".join(
        f"{RARITY_EMOJI.get(r, '')}{counts.get(r, 0)}" for r in engine.RARITIES if counts.get(r))
    notables = [c for c in cards if engine.is_notable_pull(c)]
    notables.sort(key=lambda c: c["book_value"], reverse=True)
    emb = discord.Embed(
        title=f"📦 {title}",
        description=f"Opened **{len(cards)}** cards from **{set_name}**\n{breakdown}",
        color=0xBB9AF7,
    )
    if notables:
        lines = []
        for c in notables[:12]:
            holo = "✨" if c.get("is_holo") else ""
            gem = f" 💎{c['gem']}" if c.get("gem") else ""
            lines.append(
                f"{RARITY_EMOJI.get(c['rarity'], '')} **{c['name']}** {holo}{gem} "
                f"#{c['serial']}/{c['total_copies']} · {round(c['book_value'])}🪙")
        emb.add_field(name=f"🔥 Notable pulls ({len(notables)})", value="\n".join(lines), inline=False)
    if guaranteed:
        emb.set_footer(text="Box guarantee: an epic was added — every box hits.")
    return emb


def _completion_reward_for(designs: list[dict]) -> int:
    """Coin reward for completing a set, from its checklist designs (one of each)."""
    total_book = sum(d.get("book_value", 0) for d in designs)
    return max(SET_COMPLETION_MIN, round(SET_COMPLETION_RATE * total_book))


def _tradeup_dupes(collection: list[dict], sport: str, season: int, rarity: str) -> list[dict]:
    """Instances eligible to be traded up: owned cards in this set at `rarity`, minus
    the single best (highest-book) copy of each design — those best copies stay so
    completion is never destroyed. Returned cheapest-book first (consume the worst)."""
    from collections import defaultdict

    by_design: dict[int, list[dict]] = defaultdict(list)
    for c in collection:
        if c["rarity"] == rarity and c["sport"] == sport and c["season"] == season:
            by_design[c["design_id"]].append(c)
    dupes: list[dict] = []
    for insts in by_design.values():
        insts.sort(key=lambda c: c["book_value"], reverse=True)  # best first
        dupes += insts[1:]  # keep the best copy, rest are dupes
    dupes.sort(key=lambda c: c["book_value"])  # consume the cheapest dupes first
    return dupes


def _card_line(c: dict) -> str:
    """One-line label for a pulled/owned card."""
    bits = [RARITY_EMOJI.get(c["rarity"], "")]
    if c.get("is_rookie"):
        bits.append("🌟RC")
    if c.get("gem"):
        bits.append(GEM_EMOJI.get(c["gem"], "💎"))
    if c.get("is_holo"):
        bits.append("✨holo")
    name = f"**{c['name']}**"
    serial = f"#{c['serial']}/{c['total_copies']}" if c.get("serial") else ""
    tail = f"· {c['rarity'].title()} · {serial} · {round(c['book_value'])}🪙"
    return f"{' '.join(b for b in bits if b)} {name} {tail}".strip()


def _badges(c: dict) -> str:
    bits = []
    if c.get("is_rookie"):
        bits.append("🌟 RC")
    if c.get("gem"):
        bits.append(f"{GEM_EMOJI.get(c['gem'], '💎')} {c['gem'].replace('_', ' ').title()}")
    if c.get("is_holo"):
        bits.append("✨ Holo")
    return "  ".join(bits)


class PackRevealView(discord.ui.View):
    """Animated pack reveal: step through the haul one card at a time with ◀ ▶, ascending
    rarity so the chase card lands last. Each card shows its pull odds + price (book value =
    EV). A Summary button (and the end of the haul) shows the full list."""

    def __init__(self, cards: list[dict], pull_rates: dict, title: str, opener: discord.abc.User,
                 start_summary: bool = False):
        super().__init__(timeout=180)
        self.cards = engine.reveal_order(cards)
        self.pull_rates = pull_rates
        self.title = title
        self.opener = opener
        self.i = (len(self.cards) - 1) if start_summary else 0
        self.show_summary = start_summary  # fast-open lands straight on the full haul
        self.message: discord.Message | None = None
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        last = len(self.cards) - 1
        self.prev_btn.disabled = not self.show_summary and self.i == 0
        self.next_btn.disabled = self.show_summary
        self.summary_btn.disabled = self.show_summary or len(self.cards) == 1

    def current_embed(self) -> discord.Embed:
        return self._summary_embed() if self.show_summary else self._card_embed(self.i)

    def _card_embed(self, i: int) -> discord.Embed:
        c = self.cards[i]
        emb = discord.Embed(
            title=f"{RARITY_EMOJI.get(c['rarity'], '')} {c['name']}",
            description=f"{c.get('team') or ''}  {_badges(c)}".strip() or None,
            color=RARITY_COLOR.get(c["rarity"], 0x7AA2F7),
        )
        emb.add_field(name="Rarity", value=c["rarity"].title())
        emb.add_field(name="Odds to pull", value=engine.pull_label(c, self.pull_rates))
        emb.add_field(name="Price (EV)", value=f"{round(c['book_value'])} 🪙")
        if c.get("serial"):
            emb.add_field(name="Serial", value=f"#{c['serial']}/{c['total_copies']}")
        if c.get("headshot_url"):
            emb.set_thumbnail(url=c["headshot_url"])
        emb.set_footer(text=f"{self.title} · card {i + 1}/{len(self.cards)} · opened by {self.opener.display_name}")
        return emb

    def _summary_embed(self) -> discord.Embed:
        total = round(sum(c["book_value"] for c in self.cards))
        best = self.cards[-1]  # ascending order → last is best
        emb = discord.Embed(
            title=f"{self.title} — your haul",
            description="\n".join(_card_line(c) for c in self.cards),
            color=RARITY_COLOR.get(best["rarity"], 0x7AA2F7),
        )
        if best.get("headshot_url"):
            emb.set_thumbnail(url=best["headshot_url"])
        emb.set_footer(text=f"Pack book value: {total} 🪙  ·  opened by {self.opener.display_name}")
        return emb

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != str(self.opener.id):
            await interaction.response.send_message("That's not your pack!", ephemeral=True)
            return False
        return True

    async def _update(self, interaction: discord.Interaction) -> None:
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if self.show_summary:
            self.show_summary = False  # back to the last card
        elif self.i > 0:
            self.i -= 1
        await self._update(interaction)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.primary)
    async def next_btn(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if self.i < len(self.cards) - 1:
            self.i += 1
        else:
            self.show_summary = True
        await self._update(interaction)

    @discord.ui.button(label="📋 Summary", style=discord.ButtonStyle.secondary)
    async def summary_btn(self, interaction: discord.Interaction, _button: discord.ui.Button):
        self.show_summary = True
        await self._update(interaction)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


CURSOR_KEY = "cards_alert_cursor"


class CardsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self._rare_pull_watch.start()

    async def cog_unload(self) -> None:
        self._rare_pull_watch.cancel()

    @tasks.loop(seconds=20)
    async def _rare_pull_watch(self) -> None:
        """Announce rarer-than-1% pulls (legendary / gem / holo-epic+) from ANY source — web or
        Discord opens, daily, trade-ups — by watching the mint log. The DB is the single source
        of truth, so web opens get a Discord shout-out without the web process touching Discord."""
        try:
            raw = await queries.get_bot_setting(CURSOR_KEY)
            if raw is None:
                # First run: start at the newest card so we don't replay the whole backlog.
                await queries.set_bot_setting(CURSOR_KEY, str(await queries.get_max_card_instance_id()))
                return
            # Don't consume the mint log until we have a channel to post to — otherwise
            # pulls during an unconfigured window (e.g. before /cards alertchannel is ever
            # run) are silently lost forever. Leave the cursor put; flush once configured.
            chan_id = await queries.get_bot_setting(ALERT_CHANNEL_KEY)
            channel = self.bot.get_channel(int(chan_id)) if chan_id else None
            if channel is None:
                return
            rows = await queries.get_card_instances_after(int(raw))
            if not rows:
                return
            for c in rows:
                if not engine.is_rare_pull(c):
                    continue
                emb = discord.Embed(
                    title=f"{SPORT_EMOJI.get(c['sport'], '🎴')} Rare pull!",
                    description=f"<@{c['owner_id']}> pulled {_card_line(c)}\n*{c['set_name']}*",
                    color=RARITY_COLOR.get(c["rarity"], 0xE0AF68),
                )
                if c.get("headshot_url"):
                    emb.set_thumbnail(url=c["headshot_url"])
                try:
                    await channel.send(embed=emb)
                except Exception:
                    log.debug("rare-pull post failed for %s", c.get("instance_id"), exc_info=True)
            # Advance only after we've had a channel and processed the batch.
            await queries.set_bot_setting(CURSOR_KEY, str(rows[-1]["instance_id"]))
        except Exception:
            log.exception("rare-pull watcher tick failed")

    @_rare_pull_watch.before_loop
    async def _before_rare_pull_watch(self) -> None:
        await self.bot.wait_until_ready()

    # ----- pack group -----
    pack = app_commands.Group(name="pack", description="Open sports card packs")
    cards = app_commands.Group(name="cards", description="Your card collection, catalog & trading")
    wishlist = app_commands.Group(name="wishlist", description="Card wishlist", parent=cards)
    cardtrade = app_commands.Group(name="cardtrade", description="Trade cards card-for-card")

    async def _grant_achievements(self, uid: str, channel) -> None:
        """Unlock + announce any card achievements earned by this pull (best-effort)."""
        try:
            from bot.cogs.progression import evaluate_user_achievements, announce_achievements
            newly = await evaluate_user_achievements(uid)
            if newly:
                await announce_achievements(self.bot, uid, newly, channel=channel)
        except Exception:
            log.debug("cards: achievement grant failed for %s", uid, exc_info=True)

    async def _completion_reward(self, set_id: int) -> int:
        cat = await queries.get_catalog(set_id)
        return _completion_reward_for(cat["designs"] if cat else [])

    async def _check_set_completion(self, uid: str, set_id: int, interaction: discord.Interaction) -> None:
        """After a pull, pay + announce if this pull just completed the set (once)."""
        try:
            comp = await queries.get_set_completion(uid, set_id)
            if not comp["complete"]:
                return
            if not await queries.mark_set_completion(uid, set_id):
                return  # already claimed
            reward = await self._completion_reward(set_id)
            cset = await queries.get_card_set_by_id(set_id)
            name = cset["name"] if cset else "the set"
            await queries.credit_coins(uid, reward, f"Completed {name}", _now_iso())
            emb = discord.Embed(
                title="🎉 Set complete!",
                description=(
                    f"{interaction.user.mention} completed **{name}** — every design owned!\n"
                    f"Reward: **+{reward}** 🪙"
                ),
                color=0xE0AF68,
            )
            await interaction.followup.send(embed=emb)
        except Exception:
            log.exception("cards: set-completion check failed for %s / set %s", uid, set_id)

    async def _dm_wanters(self, cards_out: list[dict], opener: discord.abc.User) -> None:
        for c in cards_out:
            try:
                wanters = await queries.get_card_wanters(c["design_id"])
                for uid in wanters:
                    if uid == str(opener.id):
                        continue
                    user = self.bot.get_user(int(uid)) or await self.bot.fetch_user(int(uid))
                    if user:
                        await user.send(
                            f"🎴 A card on your wishlist was just pulled: {_card_line(c)} "
                            f"by {opener.display_name}. Offer a trade with `/cardtrade offer`!"
                        )
            except Exception:
                log.debug("cards: wishlist DM failed for design %s", c.get("design_id"))

    async def _send_reveal(
        self, interaction: discord.Interaction, cards_out: list[dict], title: str, set_id: int,
        fast: bool = False,
    ) -> None:
        """Send the pack reveal. `fast` skips the card-by-card animation and lands on the full haul."""
        cat = await queries.get_catalog(set_id)
        pull_rates = engine.set_odds(cat["designs"])["pull_rates"] if cat else {}
        view = PackRevealView(cards_out, pull_rates, title, interaction.user, start_summary=fast)
        view.message = await interaction.followup.send(embed=view.current_embed(), view=view)

    @pack.command(name="open", description="Buy & open card packs with coins")
    @app_commands.describe(
        sport="League", season="Season year (e.g. 2024)", n="How many packs (1-10)",
        fast="Skip the reveal animation and show the whole haul (defaults to your /pack fast setting)",
    )
    @app_commands.choices(sport=SPORT_CHOICES)
    async def pack_open(
        self, interaction: discord.Interaction, sport: app_commands.Choice[str], season: int,
        n: int = 1, fast: bool | None = None,
    ):
        await interaction.response.defer()
        n = max(1, min(10, n))
        uid = str(interaction.user.id)
        use_fast = fast if fast is not None else await queries.get_cards_fast_open(uid)
        cset = await queries.get_card_set(sport.value, season)
        if not cset:
            avail = [s for s in await queries.list_card_sets() if s["sport"] == sport.value]
            seasons = ", ".join(str(s["season"]) for s in avail) or "none yet"
            await interaction.followup.send(
                f"No **{sport.name} {season}** pack exists. Available {sport.name} seasons: {seasons}"
            )
            return
        all_cards: list[dict] = []
        opened = 0
        try:
            for _ in range(n):
                all_cards += await queries.mint_pack(uid, cset["set_id"], 5, "paid", _now_iso())
                opened += 1
        except ValueError as e:
            if opened == 0:
                await interaction.followup.send(f"❌ {e}")
                return
        title = f"{SPORT_EMOJI.get(sport.value,'🎴')} {cset['name']} — {opened} pack{'s' if opened>1 else ''}"
        await self._send_reveal(interaction, all_cards, title, cset["set_id"], fast=use_fast)
        await self._dm_wanters(all_cards, interaction.user)
        await self._check_set_completion(uid, cset["set_id"], interaction)
        await self._grant_achievements(uid, interaction.channel)

    class _BoxConfirm(discord.ui.View):
        def __init__(self, opener: discord.abc.User):
            super().__init__(timeout=30)
            self.opener = opener
            self.value = False

        @discord.ui.button(label="Open box", style=discord.ButtonStyle.danger, emoji="📦")
        async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button):
            if interaction.user.id != self.opener.id:
                await interaction.response.send_message("Not your box.", ephemeral=True)
                return
            self.value = True
            for c in self.children:
                c.disabled = True
            await interaction.response.edit_message(view=self)
            self.stop()

    @pack.command(name="box", description="Buy & open a full box (36 packs) with a guaranteed hit")
    @app_commands.describe(sport="League", season="Season year")
    @app_commands.choices(sport=SPORT_CHOICES)
    async def pack_box(
        self, interaction: discord.Interaction, sport: app_commands.Choice[str], season: int,
    ):
        await interaction.response.defer()
        uid = str(interaction.user.id)
        cset = await queries.get_card_set(sport.value, season)
        if not cset:
            avail = [s for s in await queries.list_card_sets() if s["sport"] == sport.value]
            seasons = ", ".join(str(s["season"]) for s in avail) or "none yet"
            await interaction.followup.send(
                f"No **{sport.name} {season}** set exists. Available {sport.name} seasons: {seasons}")
            return
        price = engine.box_price(cset["pack_cost"])
        view = self._BoxConfirm(interaction.user)
        prompt = await interaction.followup.send(
            f"📦 **{cset['name']}** box = **36 packs** for **{price:,}** 🪙. "
            f"Guaranteed epic+ per box. Confirm?", view=view, wait=True)
        await view.wait()
        if not view.value:
            await prompt.edit(content="Box purchase cancelled.", view=None)
            return
        try:
            res = await queries.mint_box(uid, cset["set_id"], _now_iso())
        except ValueError as e:
            await interaction.followup.send(f"❌ {e}")
            return
        title = f"{SPORT_EMOJI.get(sport.value, '🎴')} {cset['name']} — Box"
        emb = _box_summary_embed(res["cards"], res["guaranteed_upgraded"], title, cset["name"])
        await interaction.followup.send(embed=emb)
        await self._dm_wanters(res["cards"], interaction.user)
        await self._check_set_completion(uid, cset["set_id"], interaction)
        await self._grant_achievements(uid, interaction.channel)

    @pack.command(name="daily", description="Claim your free daily pack")
    async def pack_daily(self, interaction: discord.Interaction):
        await interaction.response.defer()
        uid = str(interaction.user.id)
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if await queries.has_claimed_daily_pack(uid, day):
            await interaction.followup.send("You already claimed your free pack today. Come back tomorrow!")
            return
        sets = [s for s in await queries.list_card_sets(include_closed=False)]
        if not sets:
            await interaction.followup.send("No card sets are available yet.")
            return
        # newest season across sports
        cset = max(sets, key=lambda s: (s["season"], s["set_id"]))
        try:
            cards_out = await queries.mint_pack(uid, cset["set_id"], 5, "daily", _now_iso())
        except ValueError as e:
            await interaction.followup.send(f"❌ {e}")
            return
        await queries.record_daily_pack_claim(uid, day)
        title = f"🎁 Free daily pack — {cset['name']}"
        await self._send_reveal(interaction, cards_out, title, cset["set_id"],
                                fast=await queries.get_cards_fast_open(uid))
        await self._dm_wanters(cards_out, interaction.user)
        await self._check_set_completion(uid, cset["set_id"], interaction)
        await self._grant_achievements(uid, interaction.channel)

    @pack.command(name="fast", description="Always skip the pack reveal animation and show the whole haul")
    @app_commands.describe(enabled="On = fast-open every pack; Off = play the card-by-card reveal")
    async def pack_fast(self, interaction: discord.Interaction, enabled: bool):
        await queries.set_cards_fast_open(str(interaction.user.id), enabled)
        state = "on — packs will open instantly" if enabled else "off — packs play the reveal animation"
        await interaction.response.send_message(f"⚡ Fast open is now **{state}**.", ephemeral=True)

    @cards.command(name="collection", description="View a card collection")
    @app_commands.describe(user="Whose collection (default: you)")
    async def collection(self, interaction: discord.Interaction, user: discord.User | None = None):
        await interaction.response.defer()
        target = user or interaction.user
        cards_out, total = await queries.get_collection(str(target.id))
        if not cards_out:
            await interaction.followup.send(
                f"{target.display_name} has no cards yet. Open one with `/pack open` or `/pack daily`!"
            )
            return
        by_rarity: dict[str, int] = {}
        for c in cards_out:
            by_rarity[c["rarity"]] = by_rarity.get(c["rarity"], 0) + 1
        emb = discord.Embed(
            title=f"🎴 {target.display_name}'s collection",
            description="\n".join(_card_line(c) for c in cards_out[:20]),
            color=0x7AA2F7,
        )
        if len(cards_out) > 20:
            emb.description += f"\n… and {len(cards_out) - 20} more"
        breakdown = "  ".join(f"{RARITY_EMOJI[r]}{by_rarity.get(r,0)}" for r in engine.RARITIES if by_rarity.get(r))
        emb.add_field(name="Cards", value=f"{len(cards_out)} total · {breakdown}", inline=False)
        emb.set_footer(text=f"Collection book value: {round(total)} 🪙  ·  browse at /hq → Cards")
        await interaction.followup.send(embed=emb)

    @cards.command(name="sets", description="Browse available card sets & your completion")
    async def sets_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer()
        sets = await queries.list_card_sets()
        if not sets:
            await interaction.followup.send("No card sets seeded yet.")
            return
        uid = str(interaction.user.id)
        lines = []
        claimable = 0
        for s in sets:
            pct = round(100 * s["packs_opened"] / s["total_packs"]) if s["total_packs"] else 0
            status = "SOLD OUT" if s["closed"] else f"{s['pack_cost']}🪙/pack · {pct}% opened"
            comp = await queries.get_set_completion(uid, s["set_id"])
            cpct = round(100 * comp["owned"] / comp["total"]) if comp["total"] else 0
            mark = ""
            if comp["complete"]:
                if await queries.set_completion_claimed(uid, s["set_id"]):
                    mark = " ✅"
                else:
                    mark = " 🎁 **claimable!**"
                    claimable += 1
            lines.append(
                f"{SPORT_EMOJI.get(s['sport'],'🎴')} **{s['name']}** — {status}\n"
                f"　You: {comp['owned']}/{comp['total']} ({cpct}%){mark}"
            )
        emb = discord.Embed(title="🎴 Card sets", description="\n".join(lines), color=0x7AA2F7)
        footer = "Older seasons cost more. Open with /pack open <sport> <season>"
        if claimable:
            footer = f"You have {claimable} set(s) to claim — run /cards claim. " + footer
        emb.set_footer(text=footer)
        await interaction.followup.send(embed=emb)

    @cards.command(name="claim", description="Claim coin rewards for any sets you've fully completed")
    async def claim(self, interaction: discord.Interaction):
        await interaction.response.defer()
        uid = str(interaction.user.id)
        sets = await queries.list_card_sets()
        paid: list[tuple[str, int]] = []
        for s in sets:
            comp = await queries.get_set_completion(uid, s["set_id"])
            if not comp["complete"]:
                continue
            if await queries.mark_set_completion(uid, s["set_id"]):
                reward = await self._completion_reward(s["set_id"])
                await queries.credit_coins(uid, reward, f"Completed {s['name']}", _now_iso())
                paid.append((s["name"], reward))
        if not paid:
            await interaction.followup.send(
                "No completed sets to claim. Check `/cards sets` for your progress toward each set."
            )
            return
        total = sum(r for _, r in paid)
        emb = discord.Embed(
            title="🎉 Set completion rewards",
            description="\n".join(f"**{n}** — +{r} 🪙" for n, r in paid),
            color=0xE0AF68,
        )
        emb.set_footer(text=f"Total claimed: +{total} 🪙")
        await interaction.followup.send(embed=emb)

    @cards.command(name="tradeup", description="Craft: trade duplicate cards up to the next rarity")
    @app_commands.describe(sport="League", season="Season year", rarity="Rarity of dupes to trade up")
    @app_commands.choices(sport=SPORT_CHOICES, rarity=TRADEUP_CHOICES)
    async def tradeup(
        self,
        interaction: discord.Interaction,
        sport: app_commands.Choice[str],
        season: int,
        rarity: app_commands.Choice[str],
    ):
        await interaction.response.defer()
        uid = str(interaction.user.id)
        rar = rarity.value
        cset = await queries.get_card_set(sport.value, season)
        if not cset:
            await interaction.followup.send(f"No **{sport.name} {season}** set exists.")
            return
        cost = TRADEUP_COST.get(rar)
        idx = engine.RARITIES.index(rar)
        if cost is None or idx >= len(engine.RARITIES) - 1:
            await interaction.followup.send(f"{rar.title()} cards can't be traded up.")
            return
        next_rar = engine.RARITIES[idx + 1]
        collection, _ = await queries.get_collection(uid)
        dupes = _tradeup_dupes(collection, sport.value, season, rar)
        if len(dupes) < cost:
            need = cost - len(dupes)
            await interaction.followup.send(
                f"You need **{cost}** duplicate {RARITY_EMOJI.get(rar,'')} {rar.title()} cards from "
                f"**{cset['name']}** to trade up to {RARITY_EMOJI.get(next_rar,'')} {next_rar.title()} — "
                f"you have {len(dupes)} spare (extra copies beyond your first of each design). "
                f"Pull or trade for **{need}** more."
            )
            return
        consume_ids = [c["instance_id"] for c in dupes[:cost]]
        # Mint FIRST so a sold-out next tier never eats the user's dupes.
        minted = await queries.mint_tradeup_card(uid, cset["set_id"], next_rar, _now_iso())
        if not minted:
            await interaction.followup.send(
                f"No {next_rar.title()} cards remain in **{cset['name']}** — that tier is sold out, "
                f"so there's nothing to trade up into. Your cards are untouched."
            )
            return
        if not await queries.consume_card_instances(uid, consume_ids):
            # Ownership changed between read and consume (e.g. a concurrent trade). The
            # minted card stands; better to over-grant than to double-charge.
            log.warning("cards: tradeup consume failed after mint for %s (ids=%s)", uid, consume_ids)
            await interaction.followup.send(
                "Trade-up hit a snag selecting your duplicates (did they change?). "
                "You kept your card — try again."
            )
            return
        emb = discord.Embed(
            title=f"🛠️ Trade-up: {cost}× {rar.title()} → {next_rar.title()}",
            description=f"You crafted:\n{_card_line(minted)}",
            color=RARITY_COLOR.get(next_rar, 0x7AA2F7),
        )
        if minted.get("headshot_url"):
            emb.set_thumbnail(url=minted["headshot_url"])
        emb.set_footer(text=f"Consumed {cost} duplicate {rar.title()} card(s) from {cset['name']}")
        await interaction.followup.send(embed=emb)
        await self._check_set_completion(uid, cset["set_id"], interaction)
        await self._grant_achievements(uid, interaction.channel)

    @cards.command(name="catalog", description="A set's checklist + pull-rate odds")
    @app_commands.choices(sport=SPORT_CHOICES)
    async def catalog(self, interaction: discord.Interaction, sport: app_commands.Choice[str], season: int):
        await interaction.response.defer()
        cset = await queries.get_card_set(sport.value, season)
        if not cset:
            await interaction.followup.send(f"No **{sport.name} {season}** set exists.")
            return
        cat = await queries.get_catalog(cset["set_id"])
        designs = cat["designs"]
        total = sum(d["total_copies"] for d in designs) or 1
        by_rarity: dict[str, int] = {}
        for d in designs:
            by_rarity[d["rarity"]] = by_rarity.get(d["rarity"], 0) + d["total_copies"]
        odds = "\n".join(
            f"{RARITY_EMOJI[r]} {r.title()}: {round(100*by_rarity.get(r,0)/total,1)}%"
            for r in engine.RARITIES if by_rarity.get(r)
        )
        gems = " · ".join(f"{GEM_EMOJI[g]}{g.replace('_',' ')} 1/{den}" for g, (den, _m, _f) in engine.GEMS.items())
        top = sorted(designs, key=lambda d: d["book_value"], reverse=True)[:10]
        emb = discord.Embed(
            title=f"{SPORT_EMOJI.get(sport.value,'🎴')} {cset['name']} — checklist",
            description=f"{len(designs)} cards · {total} in print run",
            color=0x7AA2F7,
        )
        emb.add_field(name="Pull rates", value=odds, inline=True)
        emb.add_field(name="Holo", value=f"{round(engine.HOLO_RATE*100)}% (×{engine.HOLO_MULT})", inline=True)
        emb.add_field(name="Gems", value=gems, inline=False)
        emb.add_field(
            name="Chase cards",
            value="\n".join(f"{RARITY_EMOJI[d['rarity']]} {d['name']}{' 🌟RC' if d['is_rookie'] else ''}" for d in top),
            inline=False,
        )
        await interaction.followup.send(embed=emb)

    @cards.command(name="lookup", description="Find a player card and who owns copies")
    @app_commands.describe(player="Player name")
    async def lookup(self, interaction: discord.Interaction, player: str):
        await interaction.response.defer()
        matches = await queries.find_designs_by_name(player, limit=5)
        if not matches:
            await interaction.followup.send(f"No card found matching “{player}”.")
            return
        d = matches[0]
        design, owners = await queries.get_design_owners(d["design_id"])
        emb = discord.Embed(
            title=f"{RARITY_EMOJI.get(design['rarity'],'')} {design['name']}",
            description=(
                f"{SPORT_EMOJI.get(design['sport'],'')} {design['sport'].upper()} {design['season']} · "
                f"{design['rarity'].title()}{' · 🌟 Rookie' if design['is_rookie'] else ''}\n"
                f"{design['minted']}/{design['total_copies']} minted · book {round(design['book_value'])}🪙"
            ),
            color=RARITY_COLOR.get(design["rarity"], 0x7AA2F7),
        )
        if design.get("headshot_url"):
            emb.set_thumbnail(url=design["headshot_url"])
        if owners:
            owner_lines = []
            for o in owners[:15]:
                u = self.bot.get_user(int(o["owner_id"]))
                name = u.display_name if u else f"user {o['owner_id']}"
                flags = ("✨" if o["is_holo"] else "") + (GEM_EMOJI.get(o["gem"], "") if o["gem"] else "")
                owner_lines.append(f"#{o['serial']} {name} {flags}")
            emb.add_field(name="Owners", value="\n".join(owner_lines), inline=False)
        else:
            emb.add_field(name="Owners", value="Unowned — pull it from a pack!", inline=False)
        if len(matches) > 1:
            emb.set_footer(text="Other matches: " + ", ".join(m["name"] for m in matches[1:]))
        await interaction.followup.send(embed=emb)

    @cards.command(name="sell", description="Quick-sell a card for coins (75% of book value)")
    @app_commands.describe(instance_id="Card instance id (from /cards collection detail)")
    async def sell(self, interaction: discord.Interaction, instance_id: int):
        await interaction.response.defer(ephemeral=True)
        try:
            card, coins = await queries.sell_instance(str(interaction.user.id), instance_id)
        except ValueError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return
        await interaction.followup.send(
            f"💸 Sold {card['rarity'].title()} **{card['name']}** #{card['serial']} for **{coins}** 🪙.",
            ephemeral=True,
        )

    @wishlist.command(name="add", description="Add a card to your wishlist (DM on pull)")
    @app_commands.describe(player="Player name")
    async def wishlist_add(self, interaction: discord.Interaction, player: str):
        await interaction.response.defer(ephemeral=True)
        matches = await queries.find_designs_by_name(player, limit=1)
        if not matches:
            await interaction.followup.send(f"No card matching “{player}”.", ephemeral=True)
            return
        d = matches[0]
        await queries.add_card_want(str(interaction.user.id), d["design_id"])
        await interaction.followup.send(
            f"⭐ Added **{d['name']}** ({d['sport'].upper()} {d['season']}) to your wishlist.", ephemeral=True
        )

    @wishlist.command(name="remove", description="Remove a card from your wishlist")
    @app_commands.describe(player="Player name")
    async def wishlist_remove(self, interaction: discord.Interaction, player: str):
        await interaction.response.defer(ephemeral=True)
        matches = await queries.find_designs_by_name(player, limit=1)
        if not matches:
            await interaction.followup.send(f"No card matching “{player}”.", ephemeral=True)
            return
        await queries.remove_card_want(str(interaction.user.id), matches[0]["design_id"])
        await interaction.followup.send(f"Removed **{matches[0]['name']}** from your wishlist.", ephemeral=True)

    # ----- trading -----
    @cardtrade.command(name="offer", description="Offer your card(s) for someone else's card(s)")
    @app_commands.describe(
        user="Who to trade with",
        give="Instance id(s) you're giving, comma-separated",
        want="Instance id(s) you want, comma-separated",
        coins="Coins to sweeten your side (positive = you add, negative = you want coins)",
    )
    async def trade_offer(
        self, interaction: discord.Interaction, user: discord.User, give: str, want: str, coins: int = 0,
    ):
        await interaction.response.defer()
        uid = str(interaction.user.id)
        if user.id == interaction.user.id:
            await interaction.followup.send("You can't trade with yourself.")
            return
        try:
            give_ids = [int(x) for x in give.replace(" ", "").split(",") if x]
            want_ids = [int(x) for x in want.replace(" ", "").split(",") if x]
        except ValueError:
            await interaction.followup.send("Instance ids must be numbers, comma-separated.")
            return
        mine = await queries.get_owned_instances(uid, give_ids)
        if len(mine) != len(give_ids):
            await interaction.followup.send("You must own every card you offer (check the instance ids).")
            return
        if not give_ids and coins <= 0:
            await interaction.followup.send("Offer at least one card or some coins.")
            return
        theirs = await queries.get_owned_instances(str(user.id), want_ids)
        if len(theirs) != len(want_ids) or not want_ids:
            await interaction.followup.send(f"{user.display_name} must own every card you request.")
            return
        tid = await queries.create_card_trade(uid, str(user.id), give_ids, want_ids, _now_iso(), coins=coins)
        give_s = ", ".join(f"{m['rarity'].title()} {m['subject_name']}" for m in mine) or "—"
        want_s = ", ".join(f"{t['rarity'].title()} {t['subject_name']}" for t in theirs)
        coin_note = ""
        if coins > 0:
            coin_note = f" + **{coins}🪙**"
        elif coins < 0:
            coin_note = f" (and wants **{-coins}🪙** back)"
        emb = discord.Embed(
            title=f"🔄 Trade offer #{tid}",
            description=(
                f"{interaction.user.mention} offers **{give_s}**{coin_note}\n"
                f"for {user.mention}'s **{want_s}**\n\n"
                f"{user.mention}: `/cardtrade accept {tid}` or `/cardtrade decline {tid}`"
            ),
            color=0x9ECE6A,
        )
        await interaction.followup.send(content=user.mention, embed=emb)

    @cardtrade.command(name="accept", description="Accept a trade offered to you")
    async def trade_accept(self, interaction: discord.Interaction, trade_id: int):
        await interaction.response.defer()
        try:
            await queries.accept_card_trade(trade_id, str(interaction.user.id))
        except ValueError as e:
            await interaction.followup.send(f"❌ {e}")
            return
        await interaction.followup.send(f"✅ Trade #{trade_id} accepted — cards swapped!")

    @cardtrade.command(name="decline", description="Decline a trade offered to you")
    async def trade_decline(self, interaction: discord.Interaction, trade_id: int):
        await interaction.response.defer(ephemeral=True)
        trade = await queries.get_card_trade(trade_id)
        if not trade or trade["to_user"] != str(interaction.user.id) or trade["status"] != "pending":
            await interaction.followup.send("No pending trade with that id addressed to you.", ephemeral=True)
            return
        await queries.set_card_trade_status(trade_id, "declined")
        await interaction.followup.send(f"Declined trade #{trade_id}.", ephemeral=True)

    @cardtrade.command(name="incoming", description="See trades offered to you")
    async def trade_incoming(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        trades = await queries.list_incoming_card_trades(str(interaction.user.id))
        if not trades:
            await interaction.followup.send("No pending incoming trades.", ephemeral=True)
            return
        lines = [f"#{t['trade_id']} from <@{t['from_user']}> — accept with `/cardtrade accept {t['trade_id']}`" for t in trades]
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @cards.command(name="alertchannel", description="(Admin) Set the notable-pull shout-out channel")
    @app_commands.default_permissions(manage_guild=True)
    async def alertchannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await queries.set_bot_setting(ALERT_CHANNEL_KEY, str(channel.id))
        await interaction.response.send_message(f"Notable-pull alerts will post in {channel.mention}.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CardsCog(bot))
