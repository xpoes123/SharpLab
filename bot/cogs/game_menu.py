"""/game — unified launcher for all casino/party/brain games.

Dispatches to each game cog's existing launcher method. Frees Discord
slash-command slots (capped at 100/guild) by removing the per-game
slash registrations from individual cogs.
"""
from __future__ import annotations

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.cogs.casino import CASINO_GAMES, GAME_CATEGORIES, MODE_EMOJI


# command name → (cog class name, launcher method name)
GAME_DISPATCH: dict[str, tuple[str, str]] = {
    "blackjack": ("CasinoCog", "blackjack"),
    "baccarat": ("BaccaratCog", "baccarat"),
    "paigow": ("PaiGowCog", "paigow"),
    "uth": ("UTHCog", "uth"),
    "videopoker": ("VideoPokerCog", "videopoker"),
    "hilo": ("HiLoCog", "hilo"),
    "figgie": ("FiggieCog", "figgie"),
    "indian-poker": ("IndianPokerCog", "indian_poker"),
    "roulette": ("RouletteCog", "roulette"),
    "craps": ("CrapsCog", "craps_play"),
    "crapless": ("CraplessCrapsCog", "crapless_play"),
    "crash": ("CrashCog", "crash"),
    "plinko": ("PlinkoCog", "plinko"),
    "slots": ("SlotsCog", "slots"),
    "bingo": ("BingoCog", "bingo"),
    "horserace": ("HorseRaceCog", "horserace"),
    "stockmarket": ("StockMarketCog", "stockmarket"),
    "liarsdice": ("LiarDiceCog", "liarsdice"),
    "prisoner": ("PrisonerCog", "prisoner"),
    "math24": ("Math24Cog", "math24"),
    "countdown": ("CountdownCog", "countdown"),
    "mastermind": ("MastermindCog", "mastermind"),
    "geography": ("GeographyCog", "geography"),
    "wordle": ("WordleCog", "wordle"),
    "nba-trivia": ("RosterCog", "nba_trivia"),
    "nfl-trivia": ("RosterCog", "nfl_trivia"),
    "sudoku": ("SudokuCog", "sudoku"),
    "stockguess": ("StockGuessCog", "stockguess"),
    "mathsprint": ("MathSprintCog", "mathsprint"),
    "pokemon": ("PokemonCog", "pokemon"),
    "pokedle": ("PokedleCog", "pokedle"),
    "quizbowl": ("QuizBowlCog", "quizbowl"),
    "valorant": ("ValorantCog", "valorant"),
    "solitaire-chess": ("SolChessCog", "solitaire_chess"),
    "nba": ("NbaGuessCog", "nba"),
    "minesweeper": ("MinesweeperCog", "minesweeper"),
    "sequence": ("SequenceCog", "sequence"),
    "battleship": ("BattleshipCog", "battleship"),
    "blotto": ("BlottoCog", "blotto"),
    "cluemaster": ("CluemasterCog", "cluemaster"),
    "imposter": ("ImposterCog", "imposter"),
    "rps": ("RPSCog", "rps"),
    "nbasim": ("NbaSimCog", "nbasim"),
    "nflsim": ("NflSimCog", "nflsim"),
    "mlbsim": ("MlbSimCog", "mlbsim"),
    "tennissim": ("TennisSimCog", "tennissim"),
    "soccersim": ("SoccerSimCog", "soccersim"),
}

# Games that need extra params (opponent, bet). Excluded from /game.
PARAMETERIZED_SHORTCUTS = {"penalties", "tictactoe"}


def _categorized_games() -> dict[str, list[tuple[str, str, str]]]:
    """Group CASINO_GAMES by category, dropping those /game can't dispatch."""
    out: dict[str, list[tuple[str, str, str]]] = {cat: [] for cat, _ in GAME_CATEGORIES}
    for name, desc, cat, mode in CASINO_GAMES:
        if name in PARAMETERIZED_SHORTCUTS:
            continue
        if name not in GAME_DISPATCH:
            continue
        out.setdefault(cat, []).append((name, desc, mode))
    return out


async def _launch(bot: commands.Bot, interaction: discord.Interaction, game: str) -> None:
    # Discord sends the autocomplete *value* ("imposter") when a suggestion is
    # clicked, but the full *label* ("imposter — Imposter — …") when the user
    # types and hits Enter without selecting one. Normalize back to the key.
    game = game.strip().lower()
    if game not in GAME_DISPATCH and " — " in game:
        game = game.split(" — ", 1)[0].strip()
    entry = GAME_DISPATCH.get(game)
    if entry is None:
        await interaction.response.send_message(
            f"Unknown game: `{game}`. Try `/play` with no arg to browse.",
            ephemeral=True,
        )
        return
    cog_name, method_name = entry
    cog = bot.get_cog(cog_name)
    if cog is None:
        await interaction.response.send_message(
            f"Game `{game}` is unavailable right now.", ephemeral=True,
        )
        return
    method = getattr(cog, method_name, None)
    if method is None:
        await interaction.response.send_message(
            f"Game `{game}` is misconfigured (method `{method_name}` not found).",
            ephemeral=True,
        )
        return
    # The launcher is usually a plain coroutine method, but on cogs that still
    # decorate it with @app_commands.command the attribute is a (non-callable)
    # Command object — invoke its underlying callback with the cog bound.
    if isinstance(method, app_commands.Command):
        await method.callback(cog, interaction)
    else:
        await method(interaction)


class CategorySelect(ui.Select):
    def __init__(self, bot: commands.Bot) -> None:
        cats = _categorized_games()
        options = [
            discord.SelectOption(
                label=cat, value=cat, emoji=emoji,
                description=f"{len(cats.get(cat, []))} games",
            )
            for cat, emoji in GAME_CATEGORIES
            if cats.get(cat)
        ]
        super().__init__(placeholder="Pick a category…", options=options, min_values=1, max_values=1)
        self.bot = bot

    async def callback(self, interaction: discord.Interaction) -> None:
        view = GameMenuView(self.bot, category=self.values[0])
        await interaction.response.edit_message(
            content=f"**{self.values[0]}** — pick a game:", view=view,
        )


class GameSelect(ui.Select):
    def __init__(self, bot: commands.Bot, category: str) -> None:
        cats = _categorized_games()
        games = cats.get(category, [])
        options = [
            discord.SelectOption(
                label=name,
                value=name,
                description=desc[:100],
                emoji=MODE_EMOJI.get(mode),
            )
            for name, desc, mode in games[:25]
        ]
        super().__init__(placeholder="Pick a game…", options=options, min_values=1, max_values=1)
        self.bot = bot

    async def callback(self, interaction: discord.Interaction) -> None:
        await _launch(self.bot, interaction, self.values[0])


class GameMenuView(ui.View):
    def __init__(self, bot: commands.Bot, category: str | None = None) -> None:
        super().__init__(timeout=120)
        if category is None:
            self.add_item(CategorySelect(bot))
        else:
            self.add_item(GameSelect(bot, category))


async def _game_autocomplete(
    _interaction: discord.Interaction, current: str,
) -> list[app_commands.Choice[str]]:
    cur = current.lower()
    matches = [
        (name, desc) for name, desc, _, _ in CASINO_GAMES
        if name in GAME_DISPATCH
        and name not in PARAMETERIZED_SHORTCUTS
        and (cur in name.lower() or cur in desc.lower())
    ]
    return [
        app_commands.Choice(name=f"{n} — {d}"[:100], value=n)
        for n, d in matches[:25]
    ]


class GameMenuCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="play", description="Play any casino/party/brain game")
    @app_commands.describe(name="Game to play (leave empty to browse by category)")
    @app_commands.autocomplete(name=_game_autocomplete)
    async def game(self, interaction: discord.Interaction, name: str | None = None) -> None:
        if name is None:
            view = GameMenuView(self.bot)
            await interaction.response.send_message(
                "**Game Menu** — pick a category:", view=view, ephemeral=True,
            )
            return
        await _launch(self.bot, interaction, name)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GameMenuCog(bot))
