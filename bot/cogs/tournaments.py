"""Casino cog — single-elimination /tournament with best-of-3 mini-game duels."""

import asyncio
import json
import random
from datetime import datetime, timezone

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries
from bot.cogs._minigames import pick_games

# ── Constants ────────────────────────────────────────────────────────────────

REGISTRATION_TIMEOUT = 300  # 5 minutes
READY_TIMEOUT = 60  # seconds for both players to click Ready
MATCH_PAUSE = 3  # seconds between matches
DUEL_STARTING_COINS = 1000

SIZE_CHOICES = [
    app_commands.Choice(name="4 players", value=4),
    app_commands.Choice(name="8 players", value=8),
]

# Prize distribution as fraction of pool
PRIZES_4 = {1: 0.75, 2: 0.25}
PRIZES_8 = {1: 0.60, 2: 0.25, 3: 0.075, 4: 0.075}

XP_PARTICIPATION = 30
XP_WINNER_BONUS = 170  # winner gets 200 total (30 + 170)

COLOUR_DARK = 0x2B2D31
COLOUR_GOLD = 0xF1C40F
COLOUR_GREEN = 0x57F287


# ── Bracket helpers ──────────────────────────────────────────────────────────


def _build_bracket(player_ids: list[str], size: int) -> dict:
    """Build an initial bracket structure from shuffled player IDs.

    Returns a dict with "rounds" — a list of lists of match dicts.
    For 4 players: 2 rounds (semis + final).
    For 8 players: 3 rounds (quarters + semis + final).
    """
    shuffled = list(player_ids)
    random.shuffle(shuffled)

    num_rounds = 2 if size == 4 else 3

    rounds: list[list[dict]] = []

    # First round: pair up players
    first_round: list[dict] = []
    for i in range(0, len(shuffled), 2):
        first_round.append({
            "p1": shuffled[i],
            "p2": shuffled[i + 1],
            "winner": None,
        })
    rounds.append(first_round)

    # Subsequent rounds: empty matches waiting for winners
    matches_in_round = len(first_round) // 2
    for _ in range(1, num_rounds):
        rnd: list[dict] = []
        for _ in range(matches_in_round):
            rnd.append({"p1": None, "p2": None, "winner": None})
        rounds.append(rnd)
        matches_in_round = max(1, matches_in_round // 2)

    return {"rounds": rounds}


def _advance_winner(bracket: dict, round_idx: int, match_idx: int, winner_id: str) -> None:
    """Set the winner of a match and propagate them to the next round."""
    bracket["rounds"][round_idx][match_idx]["winner"] = winner_id

    # Advance to next round if not the final
    if round_idx + 1 < len(bracket["rounds"]):
        next_match_idx = match_idx // 2
        next_match = bracket["rounds"][round_idx + 1][next_match_idx]
        if match_idx % 2 == 0:
            next_match["p1"] = winner_id
        else:
            next_match["p2"] = winner_id


def _round_name(round_idx: int, total_rounds: int) -> str:
    """Human-readable round name."""
    remaining = total_rounds - round_idx
    if remaining == 1:
        return "Final"
    if remaining == 2:
        return "Semifinals"
    if remaining == 3:
        return "Quarterfinals"
    return f"Round {round_idx + 1}"


def _render_bracket(bracket: dict, names: dict[str, str]) -> str:
    """Render the bracket as formatted text for an embed."""
    total_rounds = len(bracket["rounds"])
    lines: list[str] = []

    for r_idx, rnd in enumerate(bracket["rounds"]):
        rnd_name = _round_name(r_idx, total_rounds)
        lines.append(f"**-- {rnd_name} --**")
        for m_idx, match in enumerate(rnd):
            p1 = match["p1"]
            p2 = match["p2"]
            winner = match["winner"]

            # Seed numbers (1-indexed from first round order)
            p1_label = names.get(p1, "TBD") if p1 else "TBD"
            p2_label = names.get(p2, "TBD") if p2 else "TBD"

            if winner:
                winner_label = names.get(winner, "?")
                lines.append(f"`{p1_label}` vs `{p2_label}` -> **{winner_label}**")
            else:
                lines.append(f"`{p1_label}` vs `{p2_label}` -> ?")
        lines.append("")

    return "\n".join(lines)


def _get_all_matches_in_order(bracket: dict) -> list[tuple[int, int, dict]]:
    """Return all matches as (round_idx, match_idx, match) in play order."""
    result = []
    for r_idx, rnd in enumerate(bracket["rounds"]):
        for m_idx, match in enumerate(rnd):
            result.append((r_idx, m_idx, match))
    return result


def _get_next_match(bracket: dict) -> tuple[int, int, dict] | None:
    """Return the next unplayed match that has both players assigned."""
    for r_idx, rnd in enumerate(bracket["rounds"]):
        for m_idx, match in enumerate(rnd):
            if match["winner"] is None and match["p1"] is not None and match["p2"] is not None:
                return (r_idx, m_idx, match)
    return None


def _determine_places(bracket: dict, size: int) -> dict[str, int]:
    """Determine final placement for all players based on bracket results.

    Returns {player_id: place}.
    """
    rounds = bracket["rounds"]
    places: dict[str, int] = {}

    # Final winner = 1st
    final_match = rounds[-1][0]
    if final_match["winner"]:
        places[final_match["winner"]] = 1
        # Final loser = 2nd
        loser = final_match["p1"] if final_match["winner"] == final_match["p2"] else final_match["p2"]
        if loser:
            places[loser] = 2

    # Semi-final losers = 3rd/4th (only meaningful for 8-player)
    if len(rounds) >= 2:
        semi_round = rounds[-2]
        place = 3
        for match in semi_round:
            if match["winner"] and match["p1"] and match["p2"]:
                loser = match["p1"] if match["winner"] == match["p2"] else match["p2"]
                if loser and loser not in places:
                    places[loser] = place
                    place += 1

    # Quarter-final losers and earlier = remaining places
    for r_idx in range(len(rounds) - 3, -1, -1):
        for match in rounds[r_idx]:
            if match["winner"] and match["p1"] and match["p2"]:
                loser = match["p1"] if match["winner"] == match["p2"] else match["p2"]
                if loser and loser not in places:
                    places[loser] = size  # all get last-ish place

    return places


def _prize_distribution(size: int) -> dict[int, float]:
    """Return {place: fraction} prize map."""
    return PRIZES_4 if size == 4 else PRIZES_8


# ── Embeds ───────────────────────────────────────────────────────────────────


def _registration_embed(
    tournament_id: int,
    size: int,
    buy_in: int,
    players: list[str],
    names: dict[str, str],
    host_name: str,
) -> discord.Embed:
    """Embed shown during the registration phase."""
    pool = size * buy_in
    embed = discord.Embed(
        title=f"Tournament #{tournament_id} ({len(players)}/{size}) -- {buy_in}c buy-in",
        description=(
            f"**Prize pool:** {pool}c\n"
            f"**Host:** {host_name}\n\n"
            "Click **Join** to enter. Host clicks **Start** when full."
        ),
        colour=COLOUR_GOLD,
    )

    if players:
        player_lines = [f"{i+1}. {names.get(p, p)}" for i, p in enumerate(players)]
        embed.add_field(name="Entrants", value="\n".join(player_lines), inline=False)
    else:
        embed.add_field(name="Entrants", value="*No players yet*", inline=False)

    prizes = _prize_distribution(size)
    prize_lines = []
    for place, frac in sorted(prizes.items()):
        amount = int(pool * frac)
        medal = {1: "\U0001f947", 2: "\U0001f948", 3: "\U0001f949"}.get(place, f"**{place}.**")
        if place in (3, 4) and size == 8:
            prize_lines.append(f"{medal} 3rd/4th -- {amount}c each")
        else:
            prize_lines.append(f"{medal} -- {amount}c")
    # Deduplicate the 3rd/4th line
    seen = set()
    deduped = []
    for line in prize_lines:
        if line not in seen:
            seen.add(line)
            deduped.append(line)
    embed.add_field(name="Prizes", value="\n".join(deduped), inline=False)

    embed.set_footer(text=f"Registration closes in 5 minutes | Tournament #{tournament_id}")
    return embed


def _bracket_embed(
    tournament_id: int,
    bracket: dict,
    names: dict[str, str],
    *,
    title_suffix: str = "",
) -> discord.Embed:
    """Embed showing the current bracket state."""
    bracket_text = _render_bracket(bracket, names)
    embed = discord.Embed(
        title=f"Tournament #{tournament_id} -- Bracket{title_suffix}",
        description=bracket_text,
        colour=COLOUR_GOLD,
    )
    return embed


def _match_embed(
    tournament_id: int,
    p1_name: str,
    p2_name: str,
    round_name: str,
    match_num: int,
) -> discord.Embed:
    """Embed for a match waiting for players to ready up."""
    embed = discord.Embed(
        title=f"Tournament #{tournament_id} -- {round_name}",
        description=(
            f"**Match {match_num}:** {p1_name} vs {p2_name}\n\n"
            "Both players must click **Ready** within 60 seconds.\n"
            "Failure to ready up results in a forfeit."
        ),
        colour=COLOUR_DARK,
    )
    return embed


def _match_playing_embed(
    tournament_id: int,
    p1_name: str,
    p2_name: str,
    round_name: str,
    game_num: int,
    total_games: int,
    game_name: str,
    game_emoji: str,
    p1_coins: int,
    p2_coins: int,
) -> discord.Embed:
    """Embed during a mini-game."""
    embed = discord.Embed(
        title=f"Tournament #{tournament_id} -- {round_name}",
        description=(
            f"**{p1_name}** vs **{p2_name}**\n\n"
            f"Game {game_num}/{total_games}: {game_emoji} **{game_name}**\n\n"
            f"{p1_name}: **{p1_coins}** coins\n"
            f"{p2_name}: **{p2_coins}** coins"
        ),
        colour=COLOUR_DARK,
    )
    return embed


def _winner_embed(
    tournament_id: int,
    winner_name: str,
    prize_pool: int,
    payouts: dict[str, int],
    names: dict[str, str],
    places: dict[str, int],
) -> discord.Embed:
    """Final embed announcing the tournament winner."""
    embed = discord.Embed(
        title=f"Tournament #{tournament_id} -- Champion!",
        description=f"\U0001f3c6 **{winner_name}** wins the tournament!",
        colour=COLOUR_GREEN,
    )

    # Payout lines
    lines: list[str] = []
    sorted_places = sorted(places.items(), key=lambda x: x[1])
    for player_id, place in sorted_places:
        name = names.get(player_id, player_id)
        payout = payouts.get(player_id, 0)
        medal = {1: "\U0001f947", 2: "\U0001f948", 3: "\U0001f949", 4: "\U0001f949"}.get(place, "")
        if payout > 0:
            lines.append(f"{medal} **{name}** -- +{payout}c")
        else:
            lines.append(f"{medal} **{name}**")

    embed.add_field(name="Results", value="\n".join(lines) if lines else "N/A", inline=False)
    embed.set_footer(text=f"Prize pool: {prize_pool}c")
    return embed


# ── Registration View ────────────────────────────────────────────────────────


class RegistrationView(ui.View):
    """View for the registration phase: Join + Start buttons."""

    def __init__(self, cog: "TournamentsCog", tournament_id: int) -> None:
        super().__init__(timeout=REGISTRATION_TIMEOUT)
        self.cog = cog
        self.tournament_id = tournament_id
        self._started = False

    @ui.button(label="Join", style=discord.ButtonStyle.primary, emoji="\U0001f3ab", row=0)
    async def join_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        tid = self.tournament_id
        uid = str(interaction.user.id)

        tourney = await queries.get_tournament(tid)
        if tourney is None or tourney["status"] != "registration":
            await interaction.response.send_message(
                "This tournament is no longer in registration.", ephemeral=True,
            )
            return

        entries = await queries.get_tournament_entries(tid)
        existing_ids = {e["discord_user"] for e in entries}
        if uid in existing_ids:
            await interaction.response.send_message("You already joined!", ephemeral=True)
            return

        if len(entries) >= tourney["size"]:
            await interaction.response.send_message("Tournament is full!", ephemeral=True)
            return

        # Ensure wallet exists and deduct buy-in
        await queries.get_or_create_casino_wallet(uid)
        try:
            await queries.update_casino_balance(uid, -tourney["buy_in"])
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(uid)
            await interaction.response.send_message(
                f"Not enough coins! (have {bal}c)", ephemeral=True,
            )
            return

        await queries.join_tournament(tid, uid)

        # Rebuild player list
        entries = await queries.get_tournament_entries(tid)
        player_ids = [e["discord_user"] for e in entries]
        names = await self._resolve_names(interaction, player_ids)

        # Resolve host name for display
        try:
            host_user = await interaction.client.fetch_user(int(tourney["host_id"]))
            host_name = host_user.display_name
        except Exception:
            host_name = tourney["host_id"]

        embed = _registration_embed(
            tid, tourney["size"], tourney["buy_in"],
            player_ids, names, host_name,
        )

        # Auto-start if full
        if len(player_ids) >= tourney["size"]:
            self._started = True
            for child in self.children:
                child.disabled = True  # type: ignore[union-attr]
            self.stop()
            await interaction.response.edit_message(embed=embed, view=self)

            # Launch the tournament in background
            asyncio.create_task(
                self.cog._run_tournament(interaction, tid),
            )
            return

        await interaction.response.edit_message(embed=embed, view=self)

    @ui.button(label="Start", style=discord.ButtonStyle.success, emoji="\u25b6", row=0)
    async def start_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        tid = self.tournament_id
        tourney = await queries.get_tournament(tid)

        if tourney is None or tourney["status"] != "registration":
            await interaction.response.send_message(
                "Tournament already started or cancelled.", ephemeral=True,
            )
            return

        if str(interaction.user.id) != tourney["host_id"]:
            await interaction.response.send_message(
                "Only the host can start the tournament.", ephemeral=True,
            )
            return

        entries = await queries.get_tournament_entries(tid)
        if len(entries) < tourney["size"]:
            await interaction.response.send_message(
                f"Need {tourney['size']} players to start ({len(entries)} joined).",
                ephemeral=True,
            )
            return

        self._started = True
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()
        await interaction.response.edit_message(view=self)

        asyncio.create_task(
            self.cog._run_tournament(interaction, tid),
        )

    async def on_timeout(self) -> None:
        if self._started:
            return

        tid = self.tournament_id

        # Refund all joined players
        entries = await queries.get_tournament_entries(tid)
        tourney = await queries.get_tournament(tid)
        if tourney:
            for entry in entries:
                try:
                    await queries.update_casino_balance(
                        entry["discord_user"], tourney["buy_in"],
                    )
                except Exception:
                    pass

            await queries.update_tournament(tid, status="cancelled")

        # Clean up in-memory state
        if tourney:
            self.cog.active_tournaments.pop(int(tourney["channel_id"]), None)

        # Edit the message to show cancellation
        messages = getattr(self.cog, "_tournament_messages", {})
        msg = messages.pop(tid, None)
        if msg:
            try:
                cancel_embed = discord.Embed(
                    title=f"Tournament #{tid} -- Cancelled",
                    description=(
                        "Registration timed out. All buy-ins have been refunded."
                    ),
                    colour=COLOUR_DARK,
                )
                await msg.edit(embed=cancel_embed, view=None)
            except discord.HTTPException:
                pass

    async def _resolve_names(
        self, interaction: discord.Interaction, player_ids: list[str],
    ) -> dict[str, str]:
        """Resolve Discord user IDs to display names."""
        names: dict[str, str] = {}
        for pid in player_ids:
            try:
                user = await interaction.client.fetch_user(int(pid))
                names[pid] = user.display_name
            except Exception:
                names[pid] = f"Player {pid[:6]}"
        return names


# ── Ready-up View ────────────────────────────────────────────────────────────


class ReadyView(ui.View):
    """View for match ready-up phase. Both players click Ready."""

    def __init__(self, p1_id: str, p2_id: str) -> None:
        super().__init__(timeout=READY_TIMEOUT)
        self.p1_id = p1_id
        self.p2_id = p2_id
        self.ready: set[str] = set()
        self._both_ready = asyncio.Event()
        self._timed_out = False

    @ui.button(label="Ready", style=discord.ButtonStyle.success, emoji="\u2705", row=0)
    async def ready_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = str(interaction.user.id)
        if uid not in (self.p1_id, self.p2_id):
            await interaction.response.send_message(
                "You're not in this match!", ephemeral=True,
            )
            return
        if uid in self.ready:
            await interaction.response.send_message(
                "Already ready!", ephemeral=True,
            )
            return

        self.ready.add(uid)

        if len(self.ready) >= 2:
            self._both_ready.set()
            for child in self.children:
                child.disabled = True  # type: ignore[union-attr]
            self.stop()
            await interaction.response.edit_message(view=self)
        else:
            await interaction.response.send_message(
                "You're ready! Waiting for opponent...", ephemeral=True,
            )

    async def wait_for_ready(self) -> str | None:
        """Wait for both players to ready up.

        Returns the ID of the player who forfeited (didn't ready), or None
        if both readied up.
        """
        try:
            await asyncio.wait_for(self._both_ready.wait(), timeout=READY_TIMEOUT)
        except asyncio.TimeoutError:
            self._timed_out = True
            self.stop()

        if len(self.ready) >= 2:
            return None  # both ready
        # Determine who didn't ready
        if self.p1_id not in self.ready:
            return self.p1_id
        if self.p2_id not in self.ready:
            return self.p2_id
        return None

    async def on_timeout(self) -> None:
        self._timed_out = True
        self._both_ready.set()  # unblock wait_for_ready


# ── Cog ──────────────────────────────────────────────────────────────────────


class TournamentsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # channel_id -> tournament_id
        self.active_tournaments: dict[int, int] = {}

    @app_commands.command(name="tournament", description="Start a single-elimination tournament")
    @app_commands.describe(
        size="Number of players (4 or 8)",
        buy_in="Entry fee in coins (50-1000)",
    )
    @app_commands.choices(size=SIZE_CHOICES)
    async def tournament(
        self,
        interaction: discord.Interaction,
        size: app_commands.Choice[int],
        buy_in: int,
    ) -> None:
        channel_id = interaction.channel_id

        # Validate
        if channel_id in self.active_tournaments:
            await interaction.response.send_message(
                "There's already a tournament in this channel!", ephemeral=True,
            )
            return

        if buy_in < 50 or buy_in > 1000:
            await interaction.response.send_message(
                "Buy-in must be between 50 and 1000 coins.", ephemeral=True,
            )
            return

        # Also check DB for any active tournament in this channel
        existing = await queries.get_tournament_in_channel(str(channel_id))
        if existing:
            await interaction.response.send_message(
                "There's already an active tournament in this channel!", ephemeral=True,
            )
            return

        uid = str(interaction.user.id)
        await queries.get_or_create_casino_wallet(uid)

        # Create tournament in DB
        tid = await queries.create_tournament(
            str(channel_id), uid, size.value, buy_in,
        )
        self.active_tournaments[channel_id] = tid

        # Resolve host name
        host_name = interaction.user.display_name

        embed = _registration_embed(tid, size.value, buy_in, [], {}, host_name)
        view = RegistrationView(self, tid)

        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()

        # Store message for later edits
        self._tournament_messages: dict[int, discord.Message] = getattr(
            self, "_tournament_messages", {},
        )
        self._tournament_messages[tid] = msg

    async def _resolve_names(self, player_ids: list[str]) -> dict[str, str]:
        """Resolve Discord user IDs to display names."""
        names: dict[str, str] = {}
        for pid in player_ids:
            try:
                user = await self.bot.fetch_user(int(pid))
                names[pid] = user.display_name
            except Exception:
                names[pid] = f"Player {pid[:6]}"
        return names

    async def _run_tournament(
        self, interaction: discord.Interaction, tournament_id: int,
    ) -> None:
        """Main tournament loop: build bracket, run matches, distribute prizes."""
        tourney = await queries.get_tournament(tournament_id)
        if tourney is None:
            return

        channel = interaction.channel
        if channel is None:
            return

        entries = await queries.get_tournament_entries(tournament_id)
        player_ids = [e["discord_user"] for e in entries]
        size = tourney["size"]
        buy_in = tourney["buy_in"]
        prize_pool = tourney["prize_pool"]

        # Assign seeds
        shuffled = list(player_ids)
        random.shuffle(shuffled)
        for i, pid in enumerate(shuffled):
            await queries.update_tournament_entry(tournament_id, pid, seed=i + 1)

        # Build bracket
        bracket = _build_bracket(shuffled, size)
        await queries.update_tournament(
            tournament_id,
            status="active",
            bracket_json=json.dumps(bracket),
        )

        names = await self._resolve_names(player_ids)

        # Show initial bracket
        bracket_embed = _bracket_embed(tournament_id, bracket, names)
        bracket_msg = await channel.send(embed=bracket_embed)

        # Track members that have left the server
        left_server: set[str] = set()

        # Run matches in order
        match_number = 0
        while True:
            next_match = _get_next_match(bracket)
            if next_match is None:
                break

            r_idx, m_idx, match = next_match
            p1_id = match["p1"]
            p2_id = match["p2"]
            match_number += 1

            total_rounds = len(bracket["rounds"])
            rnd_name = _round_name(r_idx, total_rounds)

            p1_name = names.get(p1_id, "?")
            p2_name = names.get(p2_id, "?")

            # Check if either player has left the server
            p1_gone = p1_id in left_server
            p2_gone = p2_id in left_server

            if not p1_gone:
                try:
                    await self.bot.fetch_user(int(p1_id))
                except Exception:
                    p1_gone = True
                    left_server.add(p1_id)

            if not p2_gone:
                try:
                    await self.bot.fetch_user(int(p2_id))
                except Exception:
                    p2_gone = True
                    left_server.add(p2_id)

            # Handle forfeits due to leaving
            if p1_gone and p2_gone:
                # Both gone - pick randomly
                winner_id = random.choice([p1_id, p2_id])
                _advance_winner(bracket, r_idx, m_idx, winner_id)
                await queries.update_tournament(
                    tournament_id, bracket_json=json.dumps(bracket),
                )
                await bracket_msg.edit(
                    embed=_bracket_embed(tournament_id, bracket, names),
                )
                continue
            elif p1_gone:
                _advance_winner(bracket, r_idx, m_idx, p2_id)
                await channel.send(
                    f"**{p1_name}** forfeited (left server). **{p2_name}** advances!",
                )
                await queries.update_tournament(
                    tournament_id, bracket_json=json.dumps(bracket),
                )
                await bracket_msg.edit(
                    embed=_bracket_embed(tournament_id, bracket, names),
                )
                continue
            elif p2_gone:
                _advance_winner(bracket, r_idx, m_idx, p1_id)
                await channel.send(
                    f"**{p2_name}** forfeited (left server). **{p1_name}** advances!",
                )
                await queries.update_tournament(
                    tournament_id, bracket_json=json.dumps(bracket),
                )
                await bracket_msg.edit(
                    embed=_bracket_embed(tournament_id, bracket, names),
                )
                continue

            # Show match announcement with Ready buttons
            match_emb = _match_embed(
                tournament_id, p1_name, p2_name, rnd_name, match_number,
            )
            ready_view = ReadyView(p1_id, p2_id)
            match_msg = await channel.send(
                content=f"<@{p1_id}> <@{p2_id}>",
                embed=match_emb,
                view=ready_view,
            )

            forfeiter = await ready_view.wait_for_ready()

            if forfeiter:
                # Player who didn't ready up forfeits
                winner_id = p2_id if forfeiter == p1_id else p1_id
                forfeit_name = names.get(forfeiter, "?")
                winner_name = names.get(winner_id, "?")

                _advance_winner(bracket, r_idx, m_idx, winner_id)
                await queries.update_tournament(
                    tournament_id, bracket_json=json.dumps(bracket),
                )

                forfeit_embed = discord.Embed(
                    title=f"Tournament #{tournament_id} -- {rnd_name}",
                    description=(
                        f"**{forfeit_name}** didn't ready up in time.\n"
                        f"**{winner_name}** wins by forfeit!"
                    ),
                    colour=COLOUR_DARK,
                )
                try:
                    await match_msg.edit(embed=forfeit_embed, view=None)
                except discord.HTTPException:
                    pass

                await bracket_msg.edit(
                    embed=_bracket_embed(tournament_id, bracket, names),
                )
                await asyncio.sleep(MATCH_PAUSE)
                continue

            # Both players ready -- play best-of-3 mini-games
            try:
                await match_msg.edit(view=None)
            except discord.HTTPException:
                pass

            winner_id = await self._play_duel(
                channel, match_msg, tournament_id, rnd_name,
                p1_id, p1_name, p2_id, p2_name,
            )

            _advance_winner(bracket, r_idx, m_idx, winner_id)
            await queries.update_tournament(
                tournament_id, bracket_json=json.dumps(bracket),
            )

            # Update bracket display
            await bracket_msg.edit(
                embed=_bracket_embed(tournament_id, bracket, names),
            )

            await asyncio.sleep(MATCH_PAUSE)

        # Tournament finished -- determine places and distribute prizes
        await self._finish_tournament(
            channel, tournament_id, bracket, names, size, buy_in, prize_pool,
        )

    async def _play_duel(
        self,
        channel: discord.abc.Messageable,
        message: discord.Message,
        tournament_id: int,
        round_name: str,
        p1_id: str,
        p1_name: str,
        p2_id: str,
        p2_name: str,
    ) -> str:
        """Play a best-of-3 mini-game duel. Returns the winner's ID."""
        games = pick_games(3)
        p1_coins = DUEL_STARTING_COINS
        p2_coins = DUEL_STARTING_COINS

        for i, game in enumerate(games, 1):
            # Show game announcement
            game_embed = _match_playing_embed(
                tournament_id, p1_name, p2_name, round_name,
                i, len(games), game.name, game.emoji,
                p1_coins, p2_coins,
            )
            try:
                await message.edit(embed=game_embed)
            except discord.HTTPException:
                pass

            await asyncio.sleep(1)  # brief pause before game starts

            # Play the mini-game
            winner_uid = await game.play(
                message, int(p1_id), p1_name, int(p2_id), p2_name,
            )

            # Update coin totals based on stakes
            if winner_uid == int(p1_id):
                p1_coins += game.stakes
                p2_coins -= game.stakes
            elif winner_uid == int(p2_id):
                p2_coins += game.stakes
                p1_coins -= game.stakes
            # tie = no change

            await asyncio.sleep(1)  # pause between games

        # Determine winner by coin total
        if p1_coins > p2_coins:
            winner_id = p1_id
        elif p2_coins > p1_coins:
            winner_id = p2_id
        else:
            # Tie-breaker: random
            winner_id = random.choice([p1_id, p2_id])

        winner_name = p1_name if winner_id == p1_id else p2_name
        loser_name = p2_name if winner_id == p1_id else p1_name

        result_embed = discord.Embed(
            title=f"Tournament #{tournament_id} -- {round_name}",
            description=(
                f"**{winner_name}** defeats **{loser_name}**!\n\n"
                f"{p1_name}: **{p1_coins}** coins\n"
                f"{p2_name}: **{p2_coins}** coins"
            ),
            colour=COLOUR_GREEN,
        )
        try:
            await message.edit(embed=result_embed)
        except discord.HTTPException:
            pass

        return winner_id

    async def _finish_tournament(
        self,
        channel: discord.abc.Messageable,
        tournament_id: int,
        bracket: dict,
        names: dict[str, str],
        size: int,
        buy_in: int,
        prize_pool: int,
    ) -> None:
        """Distribute prizes, award XP, log results, update DB."""
        places = _determine_places(bracket, size)
        prize_fracs = _prize_distribution(size)

        # Calculate payouts
        payouts: dict[str, int] = {}
        for player_id, place in places.items():
            frac = prize_fracs.get(place, 0.0)
            payouts[player_id] = int(prize_pool * frac)

        # Credit payouts to winners
        for player_id, payout in payouts.items():
            if payout > 0:
                await queries.update_casino_balance(player_id, payout)

        # Award XP to all participants
        entries = await queries.get_tournament_entries(tournament_id)
        all_player_ids = [e["discord_user"] for e in entries]

        for pid in all_player_ids:
            xp_amount = XP_PARTICIPATION
            if places.get(pid) == 1:
                xp_amount += XP_WINNER_BONUS
            await queries.add_xp(pid, xp_amount)

        # Log casino results for all participants
        for pid in all_player_ids:
            payout = payouts.get(pid, 0)
            await queries.log_casino_result(pid, "tournament", buy_in, payout)

        # Update tournament entries with final_place and payout
        for pid in all_player_ids:
            place = places.get(pid)
            payout = payouts.get(pid, 0)
            update_kwargs: dict[str, object] = {"payout": payout}
            if place is not None:
                update_kwargs["final_place"] = place
            if place is not None and place != 1:
                update_kwargs["eliminated"] = 1
            await queries.update_tournament_entry(tournament_id, pid, **update_kwargs)

        # Update tournament status
        now_iso = datetime.now(timezone.utc).isoformat()
        await queries.update_tournament(
            tournament_id,
            status="finished",
            finished_at=now_iso,
            bracket_json=json.dumps(bracket),
        )

        # Determine winner name
        winner_id = None
        for pid, place in places.items():
            if place == 1:
                winner_id = pid
                break

        winner_name = names.get(winner_id, "?") if winner_id else "?"

        # Send winner announcement
        embed = _winner_embed(
            tournament_id, winner_name, prize_pool,
            payouts, names, places,
        )
        await channel.send(embed=embed)

        # Clean up in-memory state
        tourney = await queries.get_tournament(tournament_id)
        if tourney:
            self.active_tournaments.pop(int(tourney["channel_id"]), None)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TournamentsCog(bot))
