"""Baccarat cog — /baccarat slash command with standard baccarat rules."""
import random

import discord
from discord import app_commands
from discord.ext import commands

from db import queries

# ── Card helpers ──────────────────────────────────────────────────────────────

SUITS = ("♠", "♥", "♦", "♣")
RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
BACCARAT_VALUES = {
    "A": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7,
    "8": 8, "9": 9, "10": 0, "J": 0, "Q": 0, "K": 0,
}
SHOE_DECKS = 8
RESHUFFLE_THRESHOLD = 80


def _new_shoe() -> list[str]:
    """Create and shuffle an 8-deck shoe."""
    cards = [f"{r}{s}" for s in SUITS for r in RANKS] * SHOE_DECKS
    random.shuffle(cards)
    return cards


def _card_value(card: str) -> int:
    """Baccarat value of a single card."""
    rank = card[:-1]  # strip suit symbol (works for multi-char like "10")
    return BACCARAT_VALUES[rank]


def _hand_value(hand: list[str]) -> int:
    """Baccarat hand value (sum mod 10)."""
    return sum(_card_value(c) for c in hand) % 10


def _fmt_card(card: str) -> str:
    return f"`{card}`"


def _fmt_hand(hand: list[str]) -> str:
    return " ".join(_fmt_card(c) for c in hand)


# ── Baccarat dealing logic ───────────────────────────────────────────────────

def _play_hand(shoe: list[str]) -> tuple[list[str], list[str]]:
    """Deal a full baccarat hand and return (player_hand, banker_hand).

    Implements standard baccarat third-card rules:
    - Natural 8 or 9 on either side: no more draws.
    - Player draws third card on 0-5, stands on 6-7.
    - Banker draws based on the standard tableau when player drew,
      or draws on 0-5 / stands on 6-7 when player stood.
    """
    # Initial deal: player, banker, player, banker
    player = [shoe.pop(), shoe.pop()]
    banker = [shoe.pop(), shoe.pop()]

    p_val = _hand_value(player)
    b_val = _hand_value(banker)

    # Natural — no more cards
    if p_val >= 8 or b_val >= 8:
        return player, banker

    # Player third-card rule
    player_drew = False
    player_third_value = -1  # sentinel; only used if player drew

    if p_val <= 5:
        third = shoe.pop()
        player.append(third)
        player_drew = True
        player_third_value = _card_value(third)

    # Banker third-card rule
    b_val = _hand_value(banker)  # recalc not needed but clear

    if not player_drew:
        # Player stood — banker draws on 0-5, stands on 6-7
        if b_val <= 5:
            banker.append(shoe.pop())
    else:
        # Player drew — banker decision based on standard tableau
        # Banker total : draws if player's third card is ...
        #   0-2       : always draws
        #   3         : draws unless player third = 8
        #   4         : draws if player third in 2-7
        #   5         : draws if player third in 4-7
        #   6         : draws if player third in 6-7
        #   7         : always stands
        if b_val <= 2:
            banker.append(shoe.pop())
        elif b_val == 3:
            if player_third_value != 8:
                banker.append(shoe.pop())
        elif b_val == 4:
            if player_third_value in (2, 3, 4, 5, 6, 7):
                banker.append(shoe.pop())
        elif b_val == 5:
            if player_third_value in (4, 5, 6, 7):
                banker.append(shoe.pop())
        elif b_val == 6:
            if player_third_value in (6, 7):
                banker.append(shoe.pop())
        # b_val == 7: stand

    return player, banker


# ── Embed builder ────────────────────────────────────────────────────────────

BET_CHOICES = [
    app_commands.Choice(name="Player", value="player"),
    app_commands.Choice(name="Banker", value="banker"),
    app_commands.Choice(name="Tie", value="tie"),
]


def _result_embed(
    *,
    player_hand: list[str],
    banker_hand: list[str],
    bet_type: str,
    wager: int,
    outcome: str,
    payout: int,
    new_balance: int,
) -> discord.Embed:
    p_val = _hand_value(player_hand)
    b_val = _hand_value(banker_hand)

    # Determine winner
    if p_val > b_val:
        winner = "Player wins!"
    elif b_val > p_val:
        winner = "Banker wins!"
    else:
        winner = "Tie!"

    # Colour based on player outcome
    colour = {
        "win": discord.Colour.green(),
        "lose": discord.Colour.red(),
        "push": discord.Colour.light_grey(),
    }.get(outcome, discord.Colour.blurple())

    # Title
    result_label = {
        "win": "You win!",
        "lose": "You lose",
        "push": "Push — bet returned",
    }[outcome]
    title = f"Baccarat — {result_label}"

    embed = discord.Embed(title=title, colour=colour)

    # Player hand
    p_third = " *(drew third)*" if len(player_hand) == 3 else ""
    embed.add_field(
        name="Player",
        value=f"{_fmt_hand(player_hand)}  ({p_val}){p_third}",
        inline=False,
    )

    # Banker hand
    b_third = " *(drew third)*" if len(banker_hand) == 3 else ""
    embed.add_field(
        name="Banker",
        value=f"{_fmt_hand(banker_hand)}  ({b_val}){b_third}",
        inline=False,
    )

    # Natural callout
    if len(player_hand) == 2 and p_val >= 8:
        embed.add_field(name="", value=f"Player natural **{p_val}**!", inline=False)
    if len(banker_hand) == 2 and b_val >= 8:
        embed.add_field(name="", value=f"Banker natural **{b_val}**!", inline=False)

    # Game result
    embed.add_field(name="Result", value=winner, inline=True)
    embed.add_field(name="Your Bet", value=f"{bet_type.capitalize()} — {wager} coins", inline=True)

    # Payout
    sign = "+" if payout > 0 else ""
    embed.add_field(name="Payout", value=f"{sign}{payout} coins", inline=True)
    embed.add_field(name="Balance", value=f"{new_balance} coins", inline=True)

    return embed


# ── Cog ──────────────────────────────────────────────────────────────────────

class BaccaratCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.shoe = _new_shoe()

    def _ensure_shoe(self) -> list[str]:
        """Return the shared shoe, reshuffling if low."""
        if len(self.shoe) < RESHUFFLE_THRESHOLD:
            self.shoe = _new_shoe()
        return self.shoe

    @app_commands.command(name="baccarat", description="Play a hand of baccarat")
    @app_commands.describe(
        bet="Player, Banker, or Tie",
        wager="Number of coins to wager",
    )
    @app_commands.choices(bet=BET_CHOICES)
    async def baccarat(
        self,
        interaction: discord.Interaction,
        bet: app_commands.Choice[str],
        wager: int,
    ) -> None:
        user_id = interaction.user.id
        bet_type = bet.value  # "player" | "banker" | "tie"

        if wager < 1:
            await interaction.response.send_message(
                "Wager must be at least 1 coin.", ephemeral=True
            )
            return

        # Ensure wallet exists + credit daily
        balance, daily_credited = await queries.get_or_create_wallet(str(user_id))

        if wager > balance:
            msg = f"You only have **{balance}** coins."
            if daily_credited:
                msg = f"Daily **100 coins** credited! {msg}"
            await interaction.response.send_message(msg, ephemeral=True)
            return

        # Deduct wager
        await queries.update_balance(str(user_id), -wager)

        # Deal
        shoe = self._ensure_shoe()
        player_hand, banker_hand = _play_hand(shoe)

        p_val = _hand_value(player_hand)
        b_val = _hand_value(banker_hand)

        # Determine result
        if p_val > b_val:
            winner = "player"
        elif b_val > p_val:
            winner = "banker"
        else:
            winner = "tie"

        # Calculate payout (amount returned to player, including original wager if won)
        if bet_type == "tie":
            if winner == "tie":
                # Tie bet wins: 8:1 — return wager + 8x wager
                payout = wager + 8 * wager
                outcome = "win"
            else:
                # Tie bet loses
                payout = 0
                outcome = "lose"
        elif bet_type == winner:
            # Player or Banker bet wins
            if bet_type == "banker":
                # 5% commission: return wager + 0.95 * wager
                commission = wager * 5 // 100
                winnings = wager - commission
                payout = wager + winnings
            else:
                # Player bet: 1:1
                payout = wager * 2
            outcome = "win"
        elif winner == "tie":
            # Bet on Player or Banker but result is Tie — push
            payout = wager
            outcome = "push"
        else:
            # Bet on Player/Banker and the other side won
            payout = 0
            outcome = "lose"

        # Credit payout
        if payout > 0:
            new_balance = await queries.update_balance(str(user_id), payout)
        else:
            new_balance = (await queries.get_balance(str(user_id))) or 0

        embed = _result_embed(
            player_hand=player_hand,
            banker_hand=banker_hand,
            bet_type=bet_type,
            wager=wager,
            outcome=outcome,
            payout=payout - wager,  # net payout (positive = profit, negative = loss)
            new_balance=new_balance,
        )

        daily_note = ""
        if daily_credited:
            daily_note = "Daily **100 coins** credited! "

        await interaction.response.send_message(
            content=daily_note if daily_note else None,
            embed=embed,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BaccaratCog(bot))
