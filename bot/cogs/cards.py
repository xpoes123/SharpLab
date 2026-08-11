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
from discord.ext import commands

from db import queries
from shared import cards as engine

log = logging.getLogger(__name__)

SPORTS = ["nba", "nfl", "mlb"]
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
SPORT_EMOJI = {"nba": "🏀", "nfl": "🏈", "mlb": "⚾"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


class CardsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ----- pack group -----
    pack = app_commands.Group(name="pack", description="Open sports card packs")
    cards = app_commands.Group(name="cards", description="Your card collection, catalog & trading")
    wishlist = app_commands.Group(name="wishlist", description="Card wishlist", parent=cards)
    cardtrade = app_commands.Group(name="cardtrade", description="Trade cards card-for-card")

    async def _post_notable(self, cards_out: list[dict], opener: discord.abc.User) -> None:
        """Shout-out for notable pulls to the configured alert channel (best-effort)."""
        notable = [c for c in cards_out if engine.is_notable_pull(c)]
        if not notable:
            return
        try:
            chan_id = await queries.get_bot_setting(ALERT_CHANNEL_KEY)
            if not chan_id:
                return
            channel = self.bot.get_channel(int(chan_id))
            if channel is None:
                return
            for c in notable:
                emb = discord.Embed(
                    title=f"{SPORT_EMOJI.get(c['sport'],'🎴')} Notable pull!",
                    description=f"{opener.mention} pulled {_card_line(c)}",
                    color=RARITY_COLOR.get(c["rarity"], 0x7AA2F7),
                )
                if c.get("headshot_url"):
                    emb.set_thumbnail(url=c["headshot_url"])
                await channel.send(embed=emb)
        except Exception:
            log.exception("cards: notable-pull alert failed")

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

    def _reveal_embed(self, cards_out: list[dict], title: str, opener: discord.abc.User) -> discord.Embed:
        total = round(sum(c["book_value"] for c in cards_out))
        best = max(cards_out, key=lambda c: c["book_value"])
        emb = discord.Embed(
            title=title,
            description="\n".join(_card_line(c) for c in cards_out),
            color=RARITY_COLOR.get(best["rarity"], 0x7AA2F7),
        )
        if best.get("headshot_url"):
            emb.set_thumbnail(url=best["headshot_url"])
        emb.set_footer(text=f"Pack book value: {total} 🪙  ·  opened by {opener.display_name}")
        return emb

    @pack.command(name="open", description="Buy & open card packs with coins")
    @app_commands.describe(sport="League", season="Season year (e.g. 2024)", n="How many packs (1-10)")
    @app_commands.choices(sport=SPORT_CHOICES)
    async def pack_open(
        self, interaction: discord.Interaction, sport: app_commands.Choice[str], season: int, n: int = 1
    ):
        await interaction.response.defer()
        n = max(1, min(10, n))
        uid = str(interaction.user.id)
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
        await interaction.followup.send(embed=self._reveal_embed(all_cards, title, interaction.user))
        await self._post_notable(all_cards, interaction.user)
        await self._dm_wanters(all_cards, interaction.user)

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
        await interaction.followup.send(embed=self._reveal_embed(cards_out, title, interaction.user))
        await self._post_notable(cards_out, interaction.user)
        await self._dm_wanters(cards_out, interaction.user)

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

    @cards.command(name="sets", description="Browse available card sets & pack prices")
    async def sets_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer()
        sets = await queries.list_card_sets()
        if not sets:
            await interaction.followup.send("No card sets seeded yet.")
            return
        lines = []
        for s in sets:
            pct = round(100 * s["packs_opened"] / s["total_packs"]) if s["total_packs"] else 0
            status = "SOLD OUT" if s["closed"] else f"{s['pack_cost']}🪙/pack · {pct}% opened"
            lines.append(f"{SPORT_EMOJI.get(s['sport'],'🎴')} **{s['name']}** — {status}")
        emb = discord.Embed(title="🎴 Card sets", description="\n".join(lines), color=0x7AA2F7)
        emb.set_footer(text="Older seasons cost more. Open with /pack open <sport> <season>")
        await interaction.followup.send(embed=emb)

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
    )
    async def trade_offer(self, interaction: discord.Interaction, user: discord.User, give: str, want: str):
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
        if len(mine) != len(give_ids) or not give_ids:
            await interaction.followup.send("You must own every card you offer (check the instance ids).")
            return
        theirs = await queries.get_owned_instances(str(user.id), want_ids)
        if len(theirs) != len(want_ids) or not want_ids:
            await interaction.followup.send(f"{user.display_name} must own every card you request.")
            return
        tid = await queries.create_card_trade(uid, str(user.id), give_ids, want_ids, _now_iso())
        give_s = ", ".join(f"{m['rarity'].title()} {m['subject_name']}" for m in mine)
        want_s = ", ".join(f"{t['rarity'].title()} {t['subject_name']}" for t in theirs)
        emb = discord.Embed(
            title=f"🔄 Trade offer #{tid}",
            description=(
                f"{interaction.user.mention} offers **{give_s}**\n"
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
