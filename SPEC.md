# SharpLab — Technical Specification

> Personal NBA quant trading lab. Ingests live odds from major sportsbooks, Kalshi, and Polymarket
> on a continuous schedule; captures closing lines; and provides a Discord interface for logging bets
> and computing Closing Line Value (CLV).

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Data Pipeline](#2-data-pipeline)
3. [Database Schema](#3-database-schema)
4. [Odds Conventions](#4-odds-conventions)
5. [Discord Bot — Slash Commands](#5-discord-bot--slash-commands)
6. [Discord Bot — Prefix Command API](#6-discord-bot--prefix-command-api)
7. [Bot Integration Guide](#7-bot-integration-guide)
8. [Environment Variables](#8-environment-variables)
9. [Running the Project](#9-running-the-project)

---

## 1. Architecture Overview

```
┌────────────────────────────────────────────────────────────┐
│  External APIs                                             │
│  The Odds API · Kalshi REST · Polymarket CLOB · ESPN       │
│  balldontlie (scores) · The Odds API (sportsbooks)         │
└────────────┬───────────────────────────────────────────────┘
             │ HTTP (httpx async)
             ▼
┌────────────────────────────────────────────────────────────┐
│  Temporal Pipeline                                         │
│  ┌──────────────────────┐  ┌─────────────────────────────┐ │
│  │ OddsPollingWorkflow  │  │ CloseCaptureWorkflow        │ │
│  │ every 30 min         │  │ triggered near tip-off      │ │
│  └──────────────────────┘  └─────────────────────────────┘ │
│  ┌──────────────────────┐  ┌─────────────────────────────┐ │
│  │ InjuryPollingWorkflow│  │ BetResolutionWorkflow       │ │
│  │ every 5 min          │  │ every 2 hours               │ │
│  └──────────────────────┘  └─────────────────────────────┘ │
└────────────┬───────────────────────────────────────────────┘
             │ aiosqlite (WAL mode)
             ▼
┌────────────────────────────────────────────────────────────┐
│  SQLite DB (data/sharplab.db)                              │
│  games · odds_snapshots · bets · injuries                  │
└────────────┬───────────────────────────────────────────────┘
             │ aiosqlite
             ▼
┌────────────────────────────────────────────────────────────┐
│  Discord Bot                                               │
│  Slash commands (app_commands) + Prefix commands (!)       │
│  Read-mostly consumer; writes only bets + CLV updates      │
└────────────────────────────────────────────────────────────┘
```

### Key Principles

- **DB is the integration bus.** The pipeline writes; the bot reads. Neither knows the other exists.
- **American odds in the DB.** All external probability sources (Kalshi, Polymarket) are converted
  to American at the ingest boundary in `shared/odds_utils.py`.
- **All times UTC ISO 8601.** No local time anywhere.
- **All DB access through `db/queries.py`.** No raw SQL in pipeline, bot, or anywhere else.
- **Temporal activities = side effects.** Workflows are deterministic; all API calls, DB writes, and
  I/O live in activities.

---

## 2. Data Pipeline

### Workflows

| Workflow | Schedule | What it does |
|---|---|---|
| `OddsPollingWorkflow` | Every 30 min | Polls The Odds API (sportsbooks) + Kalshi + Polymarket. Writes `kind='poll'` snapshots. |
| `CloseCaptureWorkflow` | Near tip-off | Captures final pre-game lines from all sources. Writes `kind='close'` snapshots. |
| `InjuryPollingWorkflow` | Every 5 min | Polls ESPN injury endpoint. Posts status changes to Discord. |
| `BetResolutionWorkflow` | Every 2 hours | Fetches final scores from balldontlie. Resolves open/graded bets. |

### Activities (`temporal/activities.py`)

| Activity | Source | Returns |
|---|---|---|
| `fetch_odds_batch` | The Odds API | `OddsBatch` for all sportsbooks |
| `fetch_kalshi_odds_batch` | Kalshi REST | `OddsBatch` for KXNBAGAME series |
| `fetch_polymarket_odds_batch` | Polymarket CLOB | `OddsBatch` for NBA markets |
| `fetch_kalshi_close_snapshot` | Kalshi REST | `list[OddsSnapshot]` at tip-off |
| `fetch_polymarket_close_snapshot` | Polymarket CLOB | `list[OddsSnapshot]` at tip-off |
| `fetch_injuries` | ESPN (unofficial) | `list[InjuryAlert]` |
| `fetch_final_scores` | balldontlie | Game results for resolution |

### Game ID

The **game ID is The Odds API's event UUID** (e.g. `a1b2c3d4-e5f6-7890-abcd-ef1234567890`).
Every table that references a game uses this UUID as `game_id`.

The **short ID** used in the `/db` browser is the first 8 characters of this UUID.
Pass the full UUID or the short prefix to prefix commands — the bot resolves both.

---

## 3. Database Schema

```sql
-- Core game record. game_id = The Odds API event UUID.
CREATE TABLE games (
    game_id    TEXT PRIMARY KEY,
    home_team  TEXT NOT NULL,
    away_team  TEXT NOT NULL,
    start_time TEXT NOT NULL,     -- UTC ISO 8601
    season     TEXT,
    status     TEXT DEFAULT 'scheduled',  -- scheduled | live | final
    clv_posted INTEGER DEFAULT 0  -- 1 after CLV auto-post fires
);

-- Every odds fetch result, tagged by source and kind.
CREATE TABLE odds_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    game_id     TEXT REFERENCES games(game_id),
    kind        TEXT NOT NULL,    -- 'poll' | 'close'
    source      TEXT NOT NULL,    -- 'draftkings' | 'fanduel' | 'kalshi' | 'polymarket' | ...
    captured_at TEXT NOT NULL,    -- UTC ISO 8601
    payload     TEXT NOT NULL     -- JSON (see Odds Payload shape below)
);

-- User bets. discord_user = Discord snowflake ID (string).
CREATE TABLE bets (
    bet_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id      TEXT REFERENCES games(game_id),
    placed_at    TEXT NOT NULL,   -- UTC ISO 8601
    discord_user TEXT NOT NULL,   -- Discord user snowflake (as string)
    book         TEXT NOT NULL,   -- 'draftkings' | 'fanduel' | 'kalshi' | 'polymarket' | ...
    market       TEXT NOT NULL,   -- 'spread' | 'moneyline' | 'total' | 'kalshi'
    side         TEXT NOT NULL,   -- team name | 'over' | 'under' | 'yes' | 'no'
    line         REAL,            -- spread or total number; NULL for ML/Kalshi
    odds         INTEGER NOT NULL,-- American odds (-110, +150, etc.)
    units        REAL NOT NULL,
    status       TEXT DEFAULT 'open',  -- open | graded | won | lost | push | void
    clv          REAL,            -- pp vs closing line; positive = beat the close
    notes        TEXT
);

-- ESPN injury report cache.
CREATE TABLE injuries (
    record_id   TEXT PRIMARY KEY,  -- ESPN athlete ID
    player_name TEXT NOT NULL,
    team        TEXT NOT NULL,     -- full team name (matches games.home_team / away_team)
    status      TEXT NOT NULL,     -- Out | Doubtful | Questionable | Day-To-Day | Probable
    prev_status TEXT,
    detail      TEXT,
    updated_at  TEXT NOT NULL,     -- UTC ISO 8601
    notified    INTEGER DEFAULT 0  -- 1 after Discord alert posted
);
```

### Odds Payload (JSON)

Stored in `odds_snapshots.payload`. All odds are American integers.

```json
{
  "spread": -4.5,
  "spread_odds": -110,
  "ml_home": -180,
  "ml_away": 155,
  "total": 224.5,
  "total_over_odds": -110,
  "total_under_odds": -110
}
```

- Kalshi and Polymarket snapshots only populate `ml_home` and `ml_away` (no spread/total).
- Any field may be `null` if not available for that source.

---

## 4. Odds Conventions

### Storage

- **American odds** are stored as integers in the DB (`-110`, `+155`).
- Positive underdog odds are stored unsigned (`155`, not `+155`).

### Display

- **All odds displayed as implied probability** using `fmt_prob()` in `shared/odds_utils.py`.
  `-110` → `"52.4%"`, `+155` → `"39.2%"`.

### Conversion

`shared/odds_utils.py` is the **only** place odds conversions happen.

| Function | Input → Output |
|---|---|
| `american_to_prob(odds)` | American int → float 0–1 |
| `prob_to_american(prob)` | float 0–1 → American int |
| `american_to_decimal(odds)` | American int → decimal float |
| `decimal_to_american(decimal)` | decimal float → American int |
| `fmt_prob(odds)` | American int → `"52.4%"` string |
| `parse_odds_input(raw)` | any format string → `(american_int, format_label)` |

### Input Formats Accepted by `parse_odds_input`

| Format | Example input | Notes |
|---|---|---|
| American | `-110`, `+150`, `150` | Integer; negative or explicit `+`; `>=100` = underdog |
| Decimal | `1.91`, `2.50` | Float `>= 1.01` |
| Cents | `52`, `65` | Unsigned integer `1–99`; Kalshi/Polymarket price |
| Probability | `0.52` | Float `< 1.0` |
| Percent | `52%` | Explicit `%` suffix |

---

## 5. Discord Bot — Slash Commands

Slash commands require Discord's interaction system. Humans use these via the `/` menu.
**Other bots cannot trigger slash commands.** Use the [prefix command API](#6-discord-bot--prefix-command-api) instead.

### Odds & Lines

| Command | Parameters | Description |
|---|---|---|
| `/odds [game]` | `game` (autocomplete) | Live lines for a game across all major books |
| `/best-line [game]` | `game` (autocomplete) | Best available number on each market type |
| `/line-move [game]` | `game` (autocomplete, accepts 8-char ID) | Line movement history — prediction markets ML + DK spread |
| `/scores` | — | Live scores for today's games with spread cover status |

### Bet Tracking

| Command | Parameters | Description |
|---|---|---|
| `/log` | `game`, `book`, `market`, `pick`, `odds`, `units`, `line?`, `notes?` | Log a bet |
| `/bets` | — | Your open and graded bets (ephemeral) |
| `/void [bet_id]` | `bet_id` (autocomplete) | Void an open bet |
| `/record [@user]` | `user?` | W/L record, ROI, and recent bets |
| `/clv-summary [@user]` | `user?` | CLV breakdown and EV gained by market and book |

### Prediction Markets

| Command | Parameters | Description |
|---|---|---|
| `/kalshi [game]` | `game` (autocomplete) | Kalshi bid/ask/mid + volume for a game |

### Utilities (no API, pure math)

| Command | Parameters | Description |
|---|---|---|
| `/convert [odds]` | `odds` (any format) | Odds format converter |
| `/ev [odds] [true_prob]` | `odds`, `true_prob` | Expected value per unit |
| `/kelly [bankroll] [odds] [edge]` | `bankroll`, `odds`, `edge` | Kelly criterion stake sizing |
| `/parlay [legs]` | `legs` (space-separated) | Parlay odds calculator |
| `/help` | — | Command reference |

### History & Data

| Command | Parameters | Description |
|---|---|---|
| `/db` | — | Paginated game history with short IDs for `/line-move` |
| `/rosters [team]` | `team` (autocomplete) | ESPN injury report for a team |

### Automatic Background Tasks

| Task | Trigger | Output |
|---|---|---|
| CLV auto-post | Every 5 min; fires when a game gets a close snapshot | Posts closing lines + CLV to `CLV_CHANNEL_ID`; pings bettors |
| Injury alerts | Every 5 min; fires on Out transitions from healthy players | Posts injury embed to `INJURY_CHANNEL_ID` |

---

## 6. Discord Bot — Prefix Command API

The prefix API is designed for **programmatic / bot-to-bot usage**. All commands use the `!` prefix and
return **plain-text, machine-parseable responses** — no embeds, no interactive buttons.

### Prerequisites

1. The `MESSAGE_CONTENT` privileged intent must be enabled for the bot in the
   [Discord Developer Portal](https://discord.com/developers/applications).
2. Enable it in bot code: `intents.message_content = True` (already done).

### Output Format

Every response line starts with a **type token** indicating what kind of record follows:

| Token | Meaning |
|---|---|
| `GAME` | A game record |
| `ODDS` | An odds snapshot |
| `BET` | A bet record |
| `CONVERT` | Odds conversion result |
| `EV` | Expected value result |
| `KELLY` | Kelly criterion result |
| `PARLAY` | Parlay calculation result |
| `ERR` | Error message |
| `NONE` | Empty result (no data found) |

Fields are `key=value` pairs separated by spaces. Multi-word values use underscores (team names use
full names with spaces — **do not** split on spaces when parsing; split on the first space after
each `key=` token instead, or use a proper key=value parser).

> **Parsing tip:** Split each line on `" "` into tokens. Each token is either a type keyword (first
> token) or `key=value`. Values never contain spaces — team names are quoted or use full name without
> spaces in the key encoding.

Multi-record responses are wrapped in a code block:

```
```
GAME game_id=abc123 ...
GAME game_id=def456 ...
```
```

Single-record responses are sent as plain text (no code block).

Error responses are always plain text: `ERR <message>`

### Prefix Command Reference

---

#### `!games [date]`

List all games for a date with their full game IDs.

**Parameters:**
- `date` — `today` (default), `yesterday`, or `YYYY-MM-DD`

**Example:**
```
!games today
!games yesterday
!games 2026-03-25
```

**Response fields:**
```
GAME game_id=<uuid> away=<team> home=<team> start=<iso> status=<scheduled|live|final>
```

**Example response:**
```
GAME game_id=a1b2c3d4-e5f6-7890-abcd-ef1234567890 away=Los Angeles Lakers home=Boston Celtics start=2026-03-26T19:00:00+00:00 status=scheduled
GAME game_id=b2c3d4e5-f6a7-8901-bcde-f12345678901 away=Denver Nuggets home=Miami Heat start=2026-03-26T20:30:00+00:00 status=final
```

---

#### `!game <game_id>`

Look up a single game by full UUID or 8-character prefix.

**Parameters:**
- `game_id` — full UUID or at least 4-character prefix

**Example:**
```
!game a1b2c3d4
!game a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

**Response fields:**
```
GAME game_id=<uuid> away=<team> home=<team> start=<iso>
```

---

#### `!find-game <query>`

Search games by team name (partial match, newest first, max 10 results).

**Parameters:**
- `query` — partial team name (e.g. `Lakers`, `Celtics`, `Boston`)

**Example:**
```
!find-game Lakers
!find-game Golden State
```

**Response:** Same `GAME` format as `!games`.

---

#### `!odds <game_id>`

Latest poll odds snapshot for a game from all tracked sources.

**Parameters:**
- `game_id` — full UUID or prefix (resolves to most recent match)

**Example:**
```
!odds a1b2c3d4
```

**Response fields (one line per source):**
```
ODDS game_id=<uuid> source=<source> kind=<poll|close> captured=<iso> spread=<float|null> spread_odds=<int|null> ml_home=<int|null> ml_away=<int|null> total=<float|null> total_over_odds=<int|null> total_under_odds=<int|null>
```

**Sources:** `draftkings`, `fanduel`, `betmgm`, `pinnacle`, `kalshi`, `polymarket`

**Example response:**
```
ODDS game_id=a1b2c3d4-... source=draftkings kind=poll captured=2026-03-26T18:30:00+00:00 spread=-4.5 spread_odds=-110 ml_home=-180 ml_away=155 total=224.5 total_over_odds=-110 total_under_odds=-110
ODDS game_id=a1b2c3d4-... source=kalshi kind=poll captured=2026-03-26T18:30:00+00:00 spread=null spread_odds=null ml_home=-172 ml_away=148 total=null total_over_odds=null total_under_odds=null
```

---

#### `!bet <bet_id>`

Look up a single bet by its integer ID.

**Parameters:**
- `bet_id` — integer (find IDs via `!bets` or from `/bets` in Discord)

**Example:**
```
!bet 42
```

**Response fields:**
```
BET bet_id=<int> game_id=<uuid> user=<discord_snowflake> book=<book> market=<market> side=<side> line=<float|null> odds=<int> units=<float> status=<status> clv=<float|null> placed_at=<iso>
```

**Status values:** `open` | `graded` | `won` | `lost` | `push` | `void`

---

#### `!bets [user_id]`

List all bets for a user (newest first, max 20).

**Parameters:**
- `user_id` — Discord snowflake ID (defaults to the calling user if omitted)

**Example:**
```
!bets 123456789012345678
!bets
```

**Response fields:** Same `BET` format as `!bet`, one line per bet.

---

#### `!convert <odds>`

Convert between odds formats.

**Parameters:**
- `odds` — any supported format: `-110`, `+150`, `1.91`, `52`, `52%`, `0.52`

**Example:**
```
!convert -110
!convert 52%
!convert 1.91
```

**Response fields:**
```
CONVERT input=<raw> format=<american|decimal|cents|prob|percent> american=<+/-int> decimal=<float> implied_prob=<float> implied_pct=<float>%
```

**Example response:**
```
CONVERT input=-110 format=american american=-110 decimal=1.9091 implied_prob=0.5238 implied_pct=52.38%
```

---

#### `!ev <odds> <true_prob>`

Expected value per unit risked.

**Parameters:**
- `odds` — any supported odds format
- `true_prob` — win probability as decimal (`0.55`) or percent (`55`)

**Example:**
```
!ev -110 0.55
!ev +150 42
```

**Response fields:**
```
EV odds=<+/-int> true_prob=<float> implied_prob=<float> edge=<+/-float> ev_per_unit=<+/-float>
```

**Example response:**
```
EV odds=-110 true_prob=0.5500 implied_prob=0.5238 edge=+0.0262 ev_per_unit=+0.0262
```

---

#### `!kelly <bankroll> <odds> <edge>`

Kelly criterion stake sizing.

**Parameters:**
- `bankroll` — total bankroll in units
- `odds` — any supported odds format
- `edge` — your edge as a percentage (e.g. `5` for 5%)

**Example:**
```
!kelly 100 -110 5
```

**Response fields:**
```
KELLY bankroll=<float> odds=<+/-int> edge=<float>% true_prob=<float> kelly_fraction=<float> full_kelly=<float>u half_kelly=<float>u
```

**Example response:**
```
KELLY bankroll=100.0 odds=-110 edge=5.0% true_prob=0.5738 kelly_fraction=0.0504 full_kelly=5.04u half_kelly=2.52u
```

---

#### `!parlay <odds1> [odds2 ...]`

Parlay odds calculator.

**Parameters:**
- `odds1 odds2 ...` — space-separated odds in any supported format

**Example:**
```
!parlay -110 -110
!parlay -110 -110 +150
```

**Response fields:**
```
PARLAY legs=[<+/-int> ...] decimal=<float> american=<+/-int> implied_prob=<float>
```

**Example response:**
```
PARLAY legs=[-110 -110] decimal=3.6529 american=+265 implied_prob=0.2736
```

---

#### `!prefix-help`

List all prefix commands with usage.

**Aliases:** `!phelp`

---

### Error Responses

All errors return a single line: `ERR <human-readable message>`

| Scenario | Example response |
|---|---|
| Invalid date | `ERR invalid date 'foo'. Use today, yesterday, or YYYY-MM-DD.` |
| Game not found | `ERR game not found: a1b2c3d4` |
| Bet not found | `ERR bet #999 not found` |
| Unparseable odds | `ERR couldn't parse odds 'xyz'. Use -110, 1.91, 52, or 52%` |
| Missing argument | `ERR usage: !kelly <bankroll> <odds> <edge%>` |

---

## 7. Bot Integration Guide

### Finding a Game ID

Three approaches, in order of convenience:

**1. By date (easiest):**
```
!games today
```
Returns all games today with full UUIDs. Parse the `game_id=` field.

**2. By team name:**
```
!find-game Lakers
```
Returns all games involving the Lakers. Pick the most recent or filter by `start=` date.

**3. Via `/db` in Discord:**
Use `/db` to browse game history. The short ID (8 chars) shown in each row can be passed to
`!game <short_id>` to get the full UUID.

---

### Finding a Bet ID

```
!bets <discord_user_id>
```
Returns all bets for that user. Each line includes `bet_id=<int>`.

Alternatively, the `/bets` slash command shows `#ID` at the end of each line.

---

### Polling for Live Odds

The pipeline updates every 30 minutes. To check freshness, look at the `captured=` timestamp in
the `!odds` response. Stale if more than 35 minutes old (pipeline may be down).

```python
import re
from datetime import datetime, timezone, timedelta

async def get_game_odds(bot_channel, game_id):
    await bot_channel.send(f"!odds {game_id}")
    # ... read response ...

def parse_odds_response(text):
    records = []
    for line in text.strip().split("\n"):
        if not line.startswith("ODDS"):
            continue
        fields = {}
        tokens = line.split(" ")
        for token in tokens[1:]:
            if "=" in token:
                k, _, v = token.partition("=")
                fields[k] = None if v == "null" else v
        records.append(fields)
    return records
```

---

### Checking for Bet Resolution

```
!bets <user_id>
```
Filter on `status=`. Progression: `open` → `graded` (CLV computed) → `won`/`lost`/`push`/`void`.

---

### Sample Integration Flow

```
# 1. Find today's games
send: !games today

# 2. Resolve a team name to a game_id
parse: GAME game_id=<uuid> away=Los Angeles Lakers home=...

# 3. Get current odds
send: !odds <uuid>

# 4. Parse spreads and moneylines
parse: ODDS source=draftkings ... ml_home=-180 ml_away=155 ...

# 5. Look up a specific bet
send: !bet 42

# 6. Check if it's resolved
parse: BET ... status=won clv=+3.2 ...
```

---

## 8. Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DISCORD_BOT_TOKEN` | Yes | Discord bot token |
| `DISCORD_GUILD_ID` | Yes | Target guild ID (for instant slash command sync) |
| `CLV_CHANNEL_ID` | Yes | Channel for CLV auto-posts and injury alerts |
| `ODDS_API_KEY` | Yes | The Odds API key |
| `KALSHI_API_KEY` | Yes | Kalshi REST API key |
| `BALLDONTLIE_API_KEY` | No | balldontlie API key (free tier works without) |

Copy `.env.example` to `.env` and fill in values. Never commit `.env`.

---

## 9. Running the Project

```bash
# Install dependencies
uv sync

# Start everything (Temporal dev server + all workers + bot)
just dev

# Or start components individually:
temporal server start-dev          # Temporal dev server
python -m temporal.worker          # Pipeline worker
python -m temporal.start_odds_polling   # OddsPolling + CloseCapture workflows
python -m bot.main                 # Discord bot

# Run tests
uv run pytest tests/test_activities.py -v     # Fast unit tests
uv run pytest tests/test_workflows.py -v -s   # Workflow tests (slow, downloads Temporal binary)
```

### `just` targets

| Target | What it starts |
|---|---|
| `just dev` | Temporal + worker + odds poller + injury poller + bet resolver + bot |
| `just bot` | Discord bot only |
| `just worker` | Temporal worker only |
| `just injuries` | Injury polling workflow only |
| `just resolve` | Bet resolution workflow only |

---

## Appendix: Tracked Books

| Key (in DB) | Display name | Source |
|---|---|---|
| `draftkings` | DraftKings | The Odds API |
| `fanduel` | FanDuel | The Odds API |
| `betmgm` | BetMGM | The Odds API |
| `pinnacle` | Pinnacle | The Odds API |
| `caesars` | Caesars | The Odds API |
| `kalshi` | Kalshi | Kalshi REST API |
| `polymarket` | Polymarket | Polymarket CLOB API |

## Appendix: Market Types

| Key | Description | `line` field |
|---|---|---|
| `spread` | Point spread | Required: spread number (e.g. `-4.5`) |
| `moneyline` | Straight win/loss | Null |
| `total` | Over/under | Required: total points (e.g. `224.5`) |
| `kalshi` | Kalshi yes/no contract | Null |

## Appendix: Bet Status Lifecycle

```
open  →  graded  →  won
                 →  lost
                 →  push
      →  void
```

- `open` — just logged, no closing line yet
- `graded` — closing line captured; CLV computed; awaiting final score
- `won` / `lost` / `push` — final result set by `BetResolutionWorkflow`
- `void` — manually voided by user (cancelled game, entry error)
