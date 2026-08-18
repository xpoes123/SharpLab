"""card_sources.py — pure-HTTP fetchers of real NBA/NFL/MLB players for the
SharpLab trading-card feature.

No database, no SQLAlchemy. Just ESPN's public (unofficial, keyless) JSON APIs
via async ``httpx``. Each of the three top-level coroutines returns a list of
plain "subject" dicts, one per player on a team's roster for the requested
season:

    {
      "subject_key": "nba|2026|lebron-james-1966",
      "name": "LeBron James",
      "team": "Los Angeles Lakers",
      "is_rookie": False,
      "career_fame": 98.4,          # float 0..100, higher = bigger star
      "stats": {"PPG": 20.9, "RPG": 6.1, "APG": 7.2, "Games": 60},
      "headshot_url": "https://a.espncdn.com/i/headshots/nba/players/full/1966.png",
    }

--- ESPN endpoints used (probed & confirmed 2026-08) ---
  Teams:    https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/teams
  Roster:   .../teams/{teamId}/roster                (current season roster)
  Stats:    https://site.web.api.espn.com/apis/common/v3/sports/{sport}/{league}/athletes/{id}/stats
  Headshot: https://a.espncdn.com/i/headshots/{league}/players/full/{id}.png  (deterministic)

The stats endpoint returns per-season rows grouped into "categories" (each with
aligned ``names`` + ``stats`` arrays). That single call per athlete gives us
career totals (sum the season rows), the requested season's line, and the
debut season (earliest year present) — so we never need a second detail call.

--- ESPN "year" convention ---
ESPN keys a season by its *ending* calendar year: the 2025-26 NBA season is
year 2026; the 2026 NFL/MLB seasons are year 2026. The ``teams`` endpoint
reports the league's current season year; pass that (or any historical ending
year) to the fetchers. As of 2026-08 all three leagues report 2026.

--- career_fame (0..100), per sport ---
Only needs to RANK stars above scrubs. Computed from career counting totals,
then min-max normalized across the fetched cohort (linear; biggest star -> 100,
no career production -> 0).
  * NBA — career total regular-season POINTS (the "totals" category, summed
    over seasons). Clearly ranks high-volume scorers at the top.
  * NFL — career position-agnostic fantasy-style production:
        passYds/25 + passTD*4 - INT*2 + rushYds/10 + rushTD*6
        + recYds/10 + recTD*6
    summed over seasons. Skill players (QB/RB/WR) rank high; linemen ~0.
  * MLB — hitter/pitcher blend. Hitters scored by career (HR + hits); pitchers
    by career (strikeouts + wins). Because the two raw scales differ wildly,
    each group is min-max normalized *within its own group* to 0..100, so the
    best hitter and the best pitcher both land near 100 (documented deviation
    from a single whole-cohort normalization, which would let pitcher strikeout
    counts swamp every hitter).

--- is_rookie ---
True when the requested ``year`` equals the earliest season year present in the
player's regular-season stat rows (their debut season). If the player has no
stat rows (e.g. a not-yet-debuted rookie / practice-squad body), we cannot
determine it and default to False rather than guess.

--- robustness ---
Small concurrency, short timeouts, 2 retries per request, per-player errors are
skipped. The three top-level functions never raise: on any failure they return
whatever was gathered (possibly []).
"""

from __future__ import annotations

import asyncio
import json
import re

import httpx

# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

_LEAGUES = {
    "nba": ("basketball", "nba"),
    "nfl": ("football", "nfl"),
    "mlb": ("baseball", "mlb"),
}

_SITE = "https://site.api.espn.com/apis/site/v2/sports"
_WEB = "https://site.web.api.espn.com/apis/common/v3/sports"

_CONCURRENCY = 8
_RETRIES = 2
_TIMEOUT = httpx.Timeout(15.0, connect=8.0)
# NOTE: ESPN's edge (Akamai) 403s spoofed *browser* UAs from datacenter IPs but
# happily serves generic clients. Do NOT set a "Mozilla/..." UA here.
_HEADERS = {"User-Agent": "sharplab-cards/1.0 (python-httpx)"}


# ---------------------------------------------------------------------------
# low-level helpers
# ---------------------------------------------------------------------------


async def _get_json(client: httpx.AsyncClient, url: str) -> dict | None:
    """GET + parse JSON with retries. Returns None on persistent failure."""
    for attempt in range(_RETRIES + 1):
        try:
            r = await client.get(url)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        if attempt < _RETRIES:
            await asyncio.sleep(0.4 * (attempt + 1))
    return None


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s or "player"


def _num(v) -> float:
    """Parse an ESPN stat string ('3,928', '.331', '54', '9.6-17.9') to float.

    For made-attempted pairs ('made-attempted') the first (made) value is used.
    Non-numeric / missing -> 0.0.
    """
    if v is None:
        return 0.0
    s = str(v).strip().replace(",", "")
    if "-" in s and not s.startswith("-"):
        s = s.split("-", 1)[0]
    try:
        return float(s)
    except ValueError:
        return 0.0


# ---------------------------------------------------------------------------
# roster enumeration
# ---------------------------------------------------------------------------


async def _team_ids(client: httpx.AsyncClient, sport: str, league: str) -> list[str]:
    d = await _get_json(client, f"{_SITE}/{sport}/{league}/teams")
    if not d:
        return []
    try:
        teams = d["sports"][0]["leagues"][0]["teams"]
        return [t["team"]["id"] for t in teams]
    except (KeyError, IndexError, TypeError):
        return []


async def _roster(
    client: httpx.AsyncClient, sport: str, league: str, team_id: str
) -> list[dict]:
    """Return flat list of {id, name, team} for a team's current roster.

    NBA rosters are a flat athlete list; NFL/MLB group athletes by position
    ({position, items}). Both shapes are flattened here.
    """
    d = await _get_json(client, f"{_SITE}/{sport}/{league}/teams/{team_id}/roster")
    if not d:
        return []
    team_name = (d.get("team") or {}).get("displayName") or ""
    out: list[dict] = []
    for grp in d.get("athletes", []):
        items = grp.get("items") if isinstance(grp, dict) and "items" in grp else [grp]
        for p in items or []:
            pid = str(p.get("id") or "")
            name = p.get("fullName") or p.get("displayName") or ""
            if pid and name:
                out.append({"id": pid, "name": name, "team": team_name})
    return out


# ---------------------------------------------------------------------------
# stats parsing
# ---------------------------------------------------------------------------


def _category(cats: list[dict], *wanted: str) -> dict | None:
    """First category whose name matches one of ``wanted`` (exact)."""
    for c in cats:
        if c.get("name") in wanted:
            return c
    return None


def _rows_by_year(cat: dict | None) -> dict[int, dict[str, str]]:
    """Map season year -> {stat_name: value} for a category.

    For traded seasons ESPN may emit several rows sharing a year; keep the one
    with the most games played (the combined line) to avoid double-counting.
    """
    if not cat:
        return {}
    names = cat.get("names", [])
    best: dict[int, tuple[float, dict]] = {}
    for s in cat.get("statistics", []):
        yr = (s.get("season") or {}).get("year")
        if yr is None:
            continue
        row = dict(zip(names, s.get("stats", [])))
        gp = _num(row.get("gamesPlayed"))
        if yr not in best or gp >= best[yr][0]:
            best[yr] = (gp, row)
    return {yr: row for yr, (_, row) in best.items()}


# ----- per-sport parse: (stats_display, fame_raw, debut_year, group) --------


def _parse_nba(cats: list[dict], year: int):
    totals = _rows_by_year(_category(cats, "totals"))
    avgs = _rows_by_year(_category(cats, "averages"))
    years = sorted(set(totals) | set(avgs))
    # Fame = THIS SEASON's production, not career totals. Per-game value × games played,
    # so an injured/limited season (few games) ranks low even for a career star.
    a = avgs.get(year, {})
    fame_raw = (
        _num(a.get("avgPoints")) + 0.5 * _num(a.get("avgRebounds")) + 0.7 * _num(a.get("avgAssists"))
    ) * _num(a.get("gamesPlayed"))
    debut = years[0] if years else None
    stats: dict = {}
    if a:
        stats = {
            "PPG": _num(a.get("avgPoints")),
            "RPG": _num(a.get("avgRebounds")),
            "APG": _num(a.get("avgAssists")),
            "Games": int(_num(a.get("gamesPlayed"))),
        }
    return stats, fame_raw, debut, "all"


def _parse_nfl(cats: list[dict], year: int):
    passing = _rows_by_year(_category(cats, "passing"))
    rushing = _rows_by_year(_category(cats, "rushing"))
    receiving = _rows_by_year(_category(cats, "receiving"))
    years = sorted(set(passing) | set(rushing) | set(receiving))
    debut = years[0] if years else None
    stats: dict = {}
    # Fame = THIS SEASON's fantasy-style production, not career-accumulated.
    p, r, c = passing.get(year, {}), rushing.get(year, {}), receiving.get(year, {})
    fame_raw = max(0.0, (
        _num(p.get("passingYards")) / 25.0
        + _num(p.get("passingTouchdowns")) * 4.0
        - _num(p.get("interceptions")) * 2.0
        + _num(r.get("rushingYards")) / 10.0
        + _num(r.get("rushingTouchdowns")) * 6.0
        + _num(c.get("receivingYards")) / 10.0
        + _num(c.get("receivingTouchdowns")) * 6.0
    ))
    if p:
        stats["PassYds"] = int(_num(p.get("passingYards")))
        stats["PassTD"] = int(_num(p.get("passingTouchdowns")))
        stats["INT"] = int(_num(p.get("interceptions")))
    if r and _num(r.get("rushingYards")):
        stats["RushYds"] = int(_num(r.get("rushingYards")))
        stats["RushTD"] = int(_num(r.get("rushingTouchdowns")))
    if c and _num(c.get("receivingYards")):
        stats["Rec"] = int(_num(c.get("receptions")))
        stats["RecYds"] = int(_num(c.get("receivingYards")))
        stats["RecTD"] = int(_num(c.get("receivingTouchdowns")))
    for src in (p, r, c):
        if src.get("gamesPlayed") is not None:
            stats.setdefault("Games", int(_num(src.get("gamesPlayed"))))
            break
    return stats, fame_raw, debut, "all"


def _parse_mlb(cats: list[dict], year: int):
    batting = _rows_by_year(_category(cats, "career-batting", "batting"))
    pitching = _rows_by_year(_category(cats, "career-pitching", "pitching"))

    hr = sum(_num(r.get("homeRuns")) for r in batting.values())
    hits = sum(_num(r.get("hits")) for r in batting.values())
    ks = sum(_num(r.get("strikeouts")) for r in pitching.values())
    wins = sum(_num(r.get("wins")) for r in pitching.values())
    hit_raw = hr + hits
    pit_raw = ks + wins

    # Classify role by career volume, but score fame from THIS SEASON only.
    is_pitcher = pit_raw > hit_raw
    group = "mlb_pitcher" if is_pitcher else "mlb_hitter"
    _by, _bp = batting.get(year, {}), pitching.get(year, {})
    season_hit = _num(_by.get("homeRuns")) + _num(_by.get("hits"))
    season_pit = _num(_bp.get("strikeouts")) + _num(_bp.get("wins"))
    fame_raw = season_pit if is_pitcher else season_hit

    years = sorted(set(batting) | set(pitching))
    debut = years[0] if years else None

    stats: dict = {}
    if is_pitcher:
        pr = pitching.get(year, {})
        if pr:
            stats = {
                "W": int(_num(pr.get("wins"))),
                "L": int(_num(pr.get("losses"))),
                "ERA": _num(pr.get("ERA")),
                "K": int(_num(pr.get("strikeouts"))),
                "IP": _num(pr.get("innings")),
            }
    else:
        br = batting.get(year, {})
        if br:
            stats = {
                "AVG": _num(br.get("avg")),
                "HR": int(_num(br.get("homeRuns"))),
                "RBI": int(_num(br.get("RBIs"))),
                "H": int(_num(br.get("hits"))),
                "Games": int(_num(br.get("gamesPlayed"))),
            }
    return stats, fame_raw, debut, group


_PARSERS = {"nba": _parse_nba, "nfl": _parse_nfl, "mlb": _parse_mlb}


# ---------------------------------------------------------------------------
# main per-sport driver
# ---------------------------------------------------------------------------


async def _fetch_season(sport_key: str, year: int) -> list[dict]:
    sport, league = _LEAGUES[sport_key]
    parse = _PARSERS[sport_key]
    results: list[dict] = []
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, headers=_HEADERS, follow_redirects=True
        ) as client:
            team_ids = await _team_ids(client, sport, league)
            if not team_ids:
                return []

            # enumerate rosters (concurrently), dedupe players across teams
            sem = asyncio.Semaphore(_CONCURRENCY)

            async def _roster_sem(tid):
                async with sem:
                    return await _roster(client, sport, league, tid)

            rosters = await asyncio.gather(
                *(_roster_sem(t) for t in team_ids), return_exceptions=True
            )
            players: dict[str, dict] = {}
            for r in rosters:
                if isinstance(r, list):
                    for p in r:
                        players.setdefault(p["id"], p)

            # fetch stats per athlete (concurrently)
            async def _one(p: dict):
                async with sem:
                    d = await _get_json(
                        client,
                        f"{_WEB}/{sport}/{league}/athletes/{p['id']}/stats",
                    )
                cats = (d or {}).get("categories", []) or []
                try:
                    stats, fame_raw, debut, group = parse(cats, year)
                except Exception:
                    return None
                is_rookie = debut is not None and debut == year
                return {
                    "_id": p["id"],
                    "_debut": debut,
                    "_fame_raw": fame_raw,
                    "_group": group,
                    "subject_key": f"{sport_key}|{year}|{_slug(p['name'])}-{p['id']}",
                    "name": p["name"],
                    "team": p["team"],
                    "is_rookie": is_rookie,
                    "career_fame": 0.0,  # filled after normalization
                    "stats": stats,
                    "headshot_url": (
                        f"https://a.espncdn.com/i/headshots/{league}/players/full/{p['id']}.png"
                    ),
                }

            raw = await asyncio.gather(
                *(_one(p) for p in players.values()), return_exceptions=True
            )
            gathered = [r for r in raw if isinstance(r, dict)]

            # historical seasons: keep only players active that year (a stat
            # row exists for it). Current season (== roster season) keeps all.
            roster_year = await _roster_season_year(client, sport, league)
            if year != roster_year:
                gathered = [g for g in gathered if _active_in(g, year, gathered)]

            _normalize_fame(gathered)

            for g in gathered:
                for k in ("_id", "_debut", "_fame_raw", "_group"):
                    g.pop(k, None)
            results = gathered
    except Exception:
        return results
    return results


async def _roster_season_year(
    client: httpx.AsyncClient, sport: str, league: str
) -> int | None:
    d = await _get_json(client, f"{_SITE}/{sport}/{league}/teams")
    try:
        return d["sports"][0]["leagues"][0]["season"]["year"]
    except (KeyError, IndexError, TypeError):
        return None


def _active_in(g: dict, year: int, cohort: list[dict]) -> bool:
    # A player is "active in ``year``" if their debut season is <= year and
    # they have any career production (fame_raw > 0 implies stat rows exist).
    debut = g.get("_debut")
    return debut is not None and debut <= year and g.get("_fame_raw", 0) > 0


def _normalize_fame(rows: list[dict]) -> None:
    """Min-max normalize ``_fame_raw`` -> ``career_fame`` (0..100).

    MLB hitters and pitchers are normalized within their own groups (different
    raw scales); NBA/NFL normalize across the whole cohort.
    """
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r.get("_group", "all"), []).append(r)
    for grp in groups.values():
        vals = [r.get("_fame_raw", 0.0) for r in grp]
        lo, hi = min(vals), max(vals)
        span = hi - lo
        for r in grp:
            if span <= 0:
                r["career_fame"] = 0.0
            else:
                r["career_fame"] = round((r["_fame_raw"] - lo) / span * 100.0, 1)


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


async def fetch_nba_season(year: int) -> list[dict]:
    """NBA players on-roster for the season ending in ``year`` (ESPN convention:
    2025-26 == 2026). Never raises; returns [] on total failure."""
    return await _fetch_season("nba", year)


async def fetch_nfl_season(year: int) -> list[dict]:
    """NFL players on-roster for the ``year`` season. Never raises."""
    return await _fetch_season("nfl", year)


async def fetch_mlb_season(year: int) -> list[dict]:
    """MLB players on-roster for the ``year`` season. Never raises."""
    return await _fetch_season("mlb", year)


# ---------------------------------------------------------------------------
# manual smoke test
# ---------------------------------------------------------------------------


def _report(sport: str, rows: list[dict]) -> None:
    print(f"\n{'=' * 70}\n{sport}: {len(rows)} players")
    if not rows:
        print("  (none)")
        return
    fames = sorted((r["career_fame"] for r in rows), reverse=True)
    print(f"  career_fame: max={fames[0]} median={fames[len(fames)//2]} min={fames[-1]}")
    print(f"  rookies: {sum(1 for r in rows if r['is_rookie'])}")
    top = sorted(rows, key=lambda r: r["career_fame"], reverse=True)[:3]
    print("  --- top 3 by fame (sample dicts) ---")
    for r in top:
        print(json.dumps(r, indent=2))


async def _fetch_display_season(fetch, key: str, roster_year: int):
    """Fetch the current roster season; if it hasn't started yet (an offseason
    league like NFL in August reports few/no stat lines), fall back one year so
    the smoke test shows real production."""
    rows = await fetch(roster_year)
    covered = sum(1 for r in rows if r["stats"])
    if rows and covered / len(rows) < 0.10:
        print(f"  [{key}] season {roster_year} looks un-started "
              f"({covered}/{len(rows)} have stats); using {roster_year - 1}")
        return roster_year - 1, await fetch(roster_year - 1)
    return roster_year, rows


async def _main() -> None:
    # Use each league's current roster season (ESPN year) so we exercise the
    # full-roster / include-all path.
    async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS) as client:
        years = {}
        for key, (sport, league) in _LEAGUES.items():
            years[key] = await _roster_season_year(client, sport, league) or 2026
    print(f"current roster seasons: {years}")

    for key, label, fetch in (
        ("nba", "NBA", fetch_nba_season),
        ("nfl", "NFL", fetch_nfl_season),
        ("mlb", "MLB", fetch_mlb_season),
    ):
        yr, rows = await _fetch_display_season(fetch, key, years[key])
        _report(f"{label} (season {yr})", rows)


if __name__ == "__main__":
    asyncio.run(_main())
