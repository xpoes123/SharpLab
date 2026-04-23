"""Prediction markets cog — user-created betting markets with casino coins."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from db import queries

log = logging.getLogger(__name__)

EMBED_COLOR = 0x7B68EE  # medium slate blue


async def _market_autocomplete(
    interaction: discord.Interaction, current: str,
) -> list[app_commands.Choice[int]]:
    """Autocomplete for open market IDs."""
    markets = await queries.list_open_markets()
    choices = []
    for m in markets[:25]:
        label = f"#{m['market_id']}: {m['question']}"
        if len(label) > 100:
            label = label[:97] + "..."
        if current and current.lower() not in label.lower():
            continue
        choices.append(app_commands.Choice(name=label, value=m["market_id"]))
    return choices[:25]


async def _outcome_autocomplete(
    interaction: discord.Interaction, current: str,
) -> list[app_commands.Choice[int]]:
    """Autocomplete for outcome IDs within the selected market."""
    market_id = interaction.namespace.market
    if market_id is None:
        return []
    market = await queries.get_prediction_market(market_id)
    if market is None:
        return []
    choices = []
    for o in market["outcomes"]:
        label = o["label"]
        if current and current.lower() not in label.lower():
            continue
        choices.append(app_commands.Choice(name=label, value=o["outcome_id"]))
    return choices[:25]


class PredictionsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    market_group = app_commands.Group(name="market", description="Prediction markets")

    @market_group.command(name="create", description="Create a new prediction market")
    @app_commands.describe(
        question="The question being predicted",
        outcomes="Comma-separated outcomes (e.g. 'Yes, No' or 'Red, Blue, Green')",
    )
    async def market_create(
        self,
        interaction: discord.Interaction,
        question: str,
        outcomes: str,
    ):
        labels = [o.strip() for o in outcomes.split(",") if o.strip()]
        if len(labels) < 2:
            await interaction.response.send_message(
                "Need at least 2 outcomes (comma-separated).", ephemeral=True,
            )
            return
        if len(labels) > 10:
            await interaction.response.send_message(
                "Maximum 10 outcomes per market.", ephemeral=True,
            )
            return

        creator_id = str(interaction.user.id)
        market_id = await queries.create_prediction_market(creator_id, question, labels)
        market = await queries.get_prediction_market(market_id)

        outcome_lines = "\n".join(
            f"  `{o['outcome_id']}` — **{o['label']}**" for o in market["outcomes"]
        )
        is_binary = len(labels) == 2

        embed = discord.Embed(
            title=f"Market #{market_id} Created",
            description=f"**{question}**\n\n{outcome_lines}",
            color=EMBED_COLOR,
        )
        if is_binary:
            embed.add_field(
                name="How to bet",
                value=(
                    f"`/market bet market:{market_id} outcome:<id> price:<1-99> quantity:<shares>`\n\n"
                    "**Price** = how many coins you pay per share (1-99).\n"
                    "If your outcome wins, each share pays **100 coins**.\n"
                    "Orders match when two sides' prices sum to 100+."
                ),
                inline=False,
            )
        else:
            embed.add_field(
                name="How to bet",
                value=(
                    f"`/market bet market:{market_id} outcome:<id> price:<1-99> quantity:<shares>`\n\n"
                    "Place **buy** or **sell** orders within an outcome.\n"
                    "Orders match when buy price >= sell price."
                ),
                inline=False,
            )
        embed.set_footer(text=f"Created by {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed)

    @market_group.command(name="bet", description="Place a bet on a prediction market")
    @app_commands.describe(
        market="Market to bet on",
        outcome="Outcome to bet on",
        price="Price per share (1-99 coins)",
        quantity="Number of shares to buy",
    )
    @app_commands.autocomplete(market=_market_autocomplete, outcome=_outcome_autocomplete)
    async def market_bet(
        self,
        interaction: discord.Interaction,
        market: int,
        outcome: int,
        price: app_commands.Range[int, 1, 99],
        quantity: app_commands.Range[int, 1, 1000],
    ):
        user_id = str(interaction.user.id)
        escrow = price * quantity

        try:
            order_id = await queries.place_market_order(
                market, outcome, user_id, "buy", price, quantity,
            )
        except ValueError as e:
            await interaction.response.send_message(f"Error: {e}", ephemeral=True)
            return

        # Try to match orders
        fills = await queries.match_orders(market)

        market_data = await queries.get_prediction_market(market)
        outcome_label = next(
            (o["label"] for o in market_data["outcomes"] if o["outcome_id"] == outcome),
            f"#{outcome}",
        )

        bal = await queries.get_casino_balance(user_id)
        embed = discord.Embed(
            title="Order Placed",
            description=(
                f"**{market_data['question']}**\n\n"
                f"Outcome: **{outcome_label}**\n"
                f"Price: **{price}** coins/share\n"
                f"Quantity: **{quantity}** shares\n"
                f"Escrow: **{escrow}** coins\n"
                f"Order ID: `{order_id}`"
            ),
            color=EMBED_COLOR,
        )

        if fills:
            total_filled = sum(f[2] for f in fills)
            embed.add_field(
                name="Matched!",
                value=f"**{total_filled}** shares matched immediately",
                inline=False,
            )

        embed.set_footer(text=f"Balance: {bal:,} coins")
        await interaction.response.send_message(embed=embed)

    @market_group.command(name="view", description="View a market and its order book")
    @app_commands.describe(market="Market to view")
    @app_commands.autocomplete(market=_market_autocomplete)
    async def market_view(self, interaction: discord.Interaction, market: int):
        market_data = await queries.get_prediction_market(market)
        if market_data is None:
            await interaction.response.send_message("Market not found.", ephemeral=True)
            return

        status_emoji = {"open": "\U0001f7e2", "resolved": "\u2705", "cancelled": "\u274c"}.get(
            market_data["status"], ""
        )

        embed = discord.Embed(
            title=f"Market #{market} — {market_data['question']}",
            description=f"Status: {status_emoji} **{market_data['status'].title()}**",
            color=EMBED_COLOR,
        )

        if market_data["status"] == "resolved":
            winner_oid = market_data["winning_outcome_id"]
            winner_label = next(
                (o["label"] for o in market_data["outcomes"] if o["outcome_id"] == winner_oid),
                "?",
            )
            embed.description += f"\nWinner: **{winner_label}**"

        is_binary = len(market_data["outcomes"]) == 2
        for outcome in market_data["outcomes"]:
            oid = outcome["outcome_id"]
            book = await queries.get_order_book(market, oid)

            best_buy = book["buys"][0]["price"] if book["buys"] else "-"
            best_sell = book["sells"][0]["price"] if book["sells"] else "-"
            buy_depth = sum(o["quantity"] - o["filled_qty"] for o in book["buys"])
            sell_depth = sum(o["quantity"] - o["filled_qty"] for o in book["sells"])

            if is_binary:
                value = f"Best bid: **{best_buy}** ({buy_depth} shares)"
            else:
                value = (
                    f"Best bid: **{best_buy}** ({buy_depth} shares)\n"
                    f"Best ask: **{best_sell}** ({sell_depth} shares)"
                )

            embed.add_field(name=f"{outcome['label']} (ID: {oid})", value=value, inline=True)

        # Show positions
        positions = await queries.get_market_positions(market)
        if positions:
            pos_lines = []
            for p in positions[:10]:
                try:
                    user = await self.bot.fetch_user(int(p["discord_user"]))
                    name = user.display_name
                except Exception:
                    name = p["discord_user"]
                pos_lines.append(f"  {name}: **{p['shares']}** {p['label']} @ avg {p['avg_price']}")
            embed.add_field(
                name="Positions",
                value="\n".join(pos_lines),
                inline=False,
            )

        await interaction.response.send_message(embed=embed)

    @market_group.command(name="list", description="List all open prediction markets")
    async def market_list(self, interaction: discord.Interaction):
        markets = await queries.list_open_markets()
        if not markets:
            await interaction.response.send_message("No open markets.", ephemeral=True)
            return

        embed = discord.Embed(title="Open Prediction Markets", color=EMBED_COLOR)
        for m in markets[:20]:
            outcomes_str = " / ".join(o["label"] for o in m["outcomes"])
            embed.add_field(
                name=f"#{m['market_id']}: {m['question']}",
                value=f"Outcomes: {outcomes_str}",
                inline=False,
            )
        await interaction.response.send_message(embed=embed)

    @market_group.command(name="cancel", description="Cancel your open order")
    @app_commands.describe(order_id="Order ID to cancel")
    async def market_cancel(self, interaction: discord.Interaction, order_id: int):
        user_id = str(interaction.user.id)
        try:
            refund = await queries.cancel_market_order(order_id, user_id)
        except ValueError as e:
            await interaction.response.send_message(f"Error: {e}", ephemeral=True)
            return

        bal = await queries.get_casino_balance(user_id)
        await interaction.response.send_message(
            f"Order `{order_id}` cancelled. Refunded **{refund:,}** coins. Balance: **{bal:,}**"
        )

    @market_group.command(name="resolve", description="Resolve a market (creator only)")
    @app_commands.describe(
        market="Market to resolve",
        outcome="Winning outcome",
    )
    @app_commands.autocomplete(market=_market_autocomplete, outcome=_outcome_autocomplete)
    async def market_resolve(
        self,
        interaction: discord.Interaction,
        market: int,
        outcome: int,
    ):
        market_data = await queries.get_prediction_market(market)
        if market_data is None:
            await interaction.response.send_message("Market not found.", ephemeral=True)
            return

        user_id = str(interaction.user.id)
        is_admin = interaction.user.guild_permissions.administrator if interaction.guild else False
        if market_data["creator_id"] != user_id and not is_admin:
            await interaction.response.send_message(
                "Only the market creator or an admin can resolve.", ephemeral=True,
            )
            return

        try:
            payouts = await queries.resolve_market(market, outcome, user_id)
        except ValueError as e:
            await interaction.response.send_message(f"Error: {e}", ephemeral=True)
            return

        outcome_label = next(
            (o["label"] for o in market_data["outcomes"] if o["outcome_id"] == outcome),
            f"#{outcome}",
        )

        embed = discord.Embed(
            title=f"Market #{market} Resolved!",
            description=f"**{market_data['question']}**\n\nWinner: **{outcome_label}**",
            color=0x2ECC71,
        )

        if payouts:
            payout_lines = []
            for uid, amount in sorted(payouts.items(), key=lambda x: -x[1]):
                try:
                    user = await self.bot.fetch_user(int(uid))
                    name = user.display_name
                except Exception:
                    name = uid
                payout_lines.append(f"  {name}: **+{amount:,}** coins")
            embed.add_field(name="Payouts", value="\n".join(payout_lines), inline=False)
        else:
            embed.add_field(name="Payouts", value="No winning positions", inline=False)

        await interaction.response.send_message(embed=embed)

    @market_group.command(name="orders", description="View your open orders in a market")
    @app_commands.describe(market="Market to check")
    @app_commands.autocomplete(market=_market_autocomplete)
    async def market_orders(self, interaction: discord.Interaction, market: int):
        user_id = str(interaction.user.id)
        orders = await queries.get_market_orders_for_user(market, user_id)

        if not orders:
            await interaction.response.send_message("No open orders in this market.", ephemeral=True)
            return

        market_data = await queries.get_prediction_market(market)
        outcome_map = {o["outcome_id"]: o["label"] for o in market_data["outcomes"]}

        lines = []
        for o in orders:
            label = outcome_map.get(o["outcome_id"], f"#{o['outcome_id']}")
            filled = o["filled_qty"]
            total = o["quantity"]
            lines.append(
                f"  `{o['order_id']}` {o['side'].upper()} **{label}** @ {o['price']} "
                f"— {filled}/{total} filled ({o['status']})"
            )

        embed = discord.Embed(
            title=f"Your Orders — Market #{market}",
            description="\n".join(lines),
            color=EMBED_COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(PredictionsCog(bot))
