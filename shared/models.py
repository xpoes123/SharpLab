"""Dataclasses shared between the pipeline and the bot."""
from dataclasses import dataclass
from typing import Any

# NBA team full name (as returned by The Odds API) → 3-char Kalshi abbreviation.
# Used to match Temporal pipeline game records to Kalshi KXNBAGAME event tickers,
# and in the bot's live Kalshi fallback fetch.
TEAM_ABBR: dict[str, str] = {
    "Atlanta Hawks": "ATL",
    "Boston Celtics": "BOS",
    "Brooklyn Nets": "BKN",
    "Charlotte Hornets": "CHA",
    "Chicago Bulls": "CHI",
    "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL",
    "Denver Nuggets": "DEN",
    "Detroit Pistons": "DET",
    "Golden State Warriors": "GSW",
    "Houston Rockets": "HOU",
    "Indiana Pacers": "IND",
    "Los Angeles Clippers": "LAC",
    "Los Angeles Lakers": "LAL",
    "Memphis Grizzlies": "MEM",
    "Miami Heat": "MIA",
    "Milwaukee Bucks": "MIL",
    "Minnesota Timberwolves": "MIN",
    "New Orleans Pelicans": "NOP",
    "New York Knicks": "NYK",
    "Oklahoma City Thunder": "OKC",
    "Orlando Magic": "ORL",
    "Philadelphia 76ers": "PHI",
    "Phoenix Suns": "PHX",
    "Portland Trail Blazers": "POR",
    "Sacramento Kings": "SAC",
    "San Antonio Spurs": "SAS",
    "Toronto Raptors": "TOR",
    "Utah Jazz": "UTA",
    "Washington Wizards": "WAS",
}


@dataclass(frozen=True)
class Game:
    game_id: str
    home_team: str
    away_team: str
    start_time_utc_iso: str  # UTC ISO 8601


@dataclass(frozen=True)
class OddsSnapshot:
    snapshot_id: str
    game_id: str
    kind: str               # 'poll' | 'close'
    source: str             # 'draftkings' | 'fanduel' | 'kalshi' | 'polymarket' | ...
    captured_at_utc_iso: str
    payload: dict[str, Any]  # {spread, spread_odds, ml_home, ml_away, total, total_over_odds, total_under_odds}


@dataclass
class Bet:
    game_id: str
    placed_at: str           # UTC ISO 8601
    discord_user: str        # Discord user ID (numeric, as string)
    book: str
    market: str              # 'spread' | 'moneyline' | 'total' | 'kalshi'
    side: str                # team name, 'over', 'under', 'yes', 'no'
    odds: int                # American
    units: float
    line: float | None = None
    status: str = "open"     # open | won | lost | push | void
    clv: float | None = None
    notes: str | None = None
    bet_id: int | None = None  # assigned by DB on insert


@dataclass(frozen=True)
class OddsBatch:
    source: str
    captured_at_utc_iso: str
    snapshots: list[OddsSnapshot]
