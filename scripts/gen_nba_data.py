"""One-time script to generate NBA player data for the guessing game.

Pulls career history + stats from nba_api for 100+ notable players.
Outputs a Python constant ready to paste into nbaguess.py.

Usage: uv run python scripts/gen_nba_data.py > scripts/nba_players_output.py
"""

import time
import json
from nba_api.stats.static import players as static_players
from nba_api.stats.endpoints import commonplayerinfo, playercareerstats

# ── Target players ──────────────────────────────────────────────────────────
# These are the 100 players we want data for (current name as known).
# We'll look them up by name in nba_api's static player list.

TARGET_PLAYERS: list[tuple[str, list[str]]] = [
    # (canonical_name, [alt_names_for_guessing])
    ("LeBron James", ["LeBron", "Bron", "King James"]),
    ("Stephen Curry", ["Steph Curry", "Steph", "Chef Curry"]),
    ("Kevin Durant", ["KD", "Durant"]),
    ("James Harden", ["Harden", "The Beard"]),
    ("Chris Paul", ["CP3"]),
    ("Jimmy Butler", ["Jimmy Buckets", "Jimmy"]),
    ("Kawhi Leonard", ["Kawhi", "The Claw"]),
    ("Paul George", ["PG13", "PG"]),
    ("Damian Lillard", ["Dame", "Dame Lillard", "Dame Time"]),
    ("Bradley Beal", ["Beal"]),
    ("Draymond Green", ["Draymond"]),
    ("Nikola Jokic", ["Jokic", "The Joker"]),
    ("Joel Embiid", ["Embiid", "The Process"]),
    ("Giannis Antetokounmpo", ["Giannis", "Greek Freak"]),
    ("Russell Westbrook", ["Russ", "Westbrook", "Brodie"]),
    ("Kyle Lowry", ["Lowry"]),
    ("DeMar DeRozan", ["DeRozan", "DeMar"]),
    ("Al Horford", ["Horford"]),
    ("Brook Lopez", ["Brook"]),
    ("Jrue Holiday", ["Jrue"]),
    ("Derrick Rose", ["D-Rose", "Rose"]),
    ("Mike Conley", ["Conley"]),
    ("Marcus Smart", ["Smart"]),
    ("Khris Middleton", ["Middleton"]),
    ("Tobias Harris", ["Tobias"]),
    ("Andre Drummond", ["Drummond"]),
    ("Clint Capela", ["Capela"]),
    ("Steven Adams", ["Adams"]),
    ("Julius Randle", ["Randle"]),
    ("Buddy Hield", ["Buddy"]),
    ("Myles Turner", ["Turner"]),
    ("CJ McCollum", ["CJ"]),
    ("Zach LaVine", ["LaVine"]),
    ("Terry Rozier", ["Scary Terry", "Rozier"]),
    ("Aaron Gordon", ["AG", "Gordon"]),
    ("Nikola Vucevic", ["Vucevic", "Vooch"]),
    ("Rudy Gobert", ["Gobert", "The Stifle Tower"]),
    ("Donovan Mitchell", ["Spida", "Mitchell"]),
    ("Pascal Siakam", ["Siakam", "Spicy P"]),
    ("Fred VanVleet", ["FVV", "VanVleet"]),
    ("Andrew Wiggins", ["Wiggins"]),
    ("D'Angelo Russell", ["DLo", "D'Angelo"]),
    ("Spencer Dinwiddie", ["Dinwiddie"]),
    ("Kemba Walker", ["Kemba"]),
    ("John Collins", ["Collins"]),
    ("Jonas Valanciunas", ["JV", "Jonas"]),
    ("Eric Gordon", ["EG"]),
    ("Reggie Jackson", ["Reggie"]),
    ("Harrison Barnes", ["Barnes"]),
    ("Kentavious Caldwell-Pope", ["KCP"]),
    ("Tim Hardaway Jr.", ["THJ", "Hardaway Jr"]),
    ("Robert Covington", ["RoCo", "Covington"]),
    ("Caris LeVert", ["LeVert"]),
    ("Norman Powell", ["Norm Powell", "Powell"]),
    ("Jusuf Nurkic", ["Nurkic", "The Bosnian Beast"]),
    ("Kelly Oubre Jr.", ["Oubre"]),
    ("Marcus Morris Sr.", ["Marcus Morris"]),
    ("Montrezl Harrell", ["Trezz", "Harrell"]),
    ("Bojan Bogdanovic", ["Bojan", "Bogdanovic"]),
    ("Gordon Hayward", ["Hayward"]),
    ("LaMarcus Aldridge", ["LMA", "Aldridge"]),
    ("Andre Iguodala", ["Iggy", "Iguodala"]),
    ("Dwight Howard", ["Dwight", "Superman"]),
    ("Danilo Gallinari", ["Gallo", "Gallinari"]),
    ("JJ Redick", ["Redick"]),
    ("Jarrett Allen", ["Allen"]),
    ("Domantas Sabonis", ["Sabonis"]),
    ("Malcolm Brogdon", ["Brogdon"]),
    ("De'Aaron Fox", ["Fox"]),
    ("Bam Adebayo", ["Bam"]),
    ("OG Anunoby", ["OG"]),
    ("Jaren Jackson Jr.", ["JJJ", "Jaren Jackson"]),
    ("Shai Gilgeous-Alexander", ["SGA", "Shai"]),
    ("Dejounte Murray", ["Dejounte"]),
    ("Brandon Ingram", ["BI", "Ingram"]),
    ("Tyler Herro", ["Herro"]),
    ("Jamal Murray", ["Murray"]),
    ("Jayson Tatum", ["Tatum", "JT"]),
    ("Jaylen Brown", ["Brown"]),
    ("Devin Booker", ["Book", "Booker", "D-Book"]),
    ("Karl-Anthony Towns", ["KAT", "Towns"]),
    ("Trae Young", ["Trae", "Ice Trae"]),
    ("Luka Doncic", ["Luka"]),
    ("Kyrie Irving", ["Kyrie", "Uncle Drew"]),
    ("Anthony Davis", ["AD", "The Brow"]),
    ("Klay Thompson", ["Klay"]),
    ("Dillon Brooks", ["Brooks"]),
    ("Mitchell Robinson", ["Mitch Rob", "Robinson"]),
    ("Ivica Zubac", ["Zubac"]),
    ("P.J. Tucker", ["PJ Tucker", "Tucker"]),
    ("Malik Beasley", ["Beasley"]),
    ("Lonzo Ball", ["Zo", "Lonzo"]),
    ("Anthony Edwards", ["Ant", "Ant-Man"]),
    ("Victor Wembanyama", ["Wemby", "Wembanyama"]),
    ("Chet Holmgren", ["Chet"]),
    ("Paolo Banchero", ["Paolo"]),
    ("Tyrese Haliburton", ["Haliburton"]),
    ("Tyrese Maxey", ["Maxey"]),
    ("Ja Morant", ["Ja"]),
    ("Scottie Barnes", ["Scottie"]),
    ("LaMelo Ball", ["Melo", "LaMelo"]),
]


def find_player_id(name: str) -> int | None:
    """Find nba_api player_id by full name."""
    all_players = static_players.get_players()
    # Try exact match first
    for p in all_players:
        if p["full_name"].lower() == name.lower():
            return p["id"]
    # Try partial match
    for p in all_players:
        if name.lower() in p["full_name"].lower():
            return p["id"]
    return None


def get_career_data(player_id: int) -> dict:
    """Pull career teams (with seasons) and career stats."""
    # Career stats by season
    career = playercareerstats.PlayerCareerStats(player_id=player_id)
    time.sleep(0.6)  # Rate limit

    season_rows = career.get_normalized_dict()["SeasonTotalsRegularSeason"]

    # Build team stints with season ranges
    stints: list[tuple[str, str, str]] = []  # (team_abbr, first_season, last_season)
    for row in season_rows:
        team = row["TEAM_ABBREVIATION"]
        season = row["SEASON_ID"]  # e.g. "2003-04"
        if stints and stints[-1][0] == team:
            # Extend current stint
            stints[-1] = (team, stints[-1][1], season)
        else:
            stints.append((team, season, season))

    # Career totals
    career_totals = career.get_normalized_dict().get("CareerTotalsRegularSeason", [])
    stats = {}
    if career_totals:
        ct = career_totals[0]
        gp = ct.get("GP", 0)
        if gp > 0:
            stats = {
                "ppg": round(ct.get("PTS", 0) / gp, 1),
                "rpg": round(ct.get("REB", 0) / gp, 1),
                "apg": round(ct.get("AST", 0) / gp, 1),
                "gp": gp,
            }

    return {"stints": stints, "stats": stats}


# Map common abbreviations to full team names
ABBR_TO_FULL = {
    "ATL": "Hawks", "BOS": "Celtics", "BKN": "Nets", "CHA": "Hornets",
    "CHI": "Bulls", "CLE": "Cavaliers", "DAL": "Mavericks", "DEN": "Nuggets",
    "DET": "Pistons", "GSW": "Warriors", "HOU": "Rockets", "IND": "Pacers",
    "LAC": "Clippers", "LAL": "Lakers", "MEM": "Grizzlies", "MIA": "Heat",
    "MIL": "Bucks", "MIN": "Timberwolves", "NOP": "Pelicans", "NYK": "Knicks",
    "OKC": "Thunder", "ORL": "Magic", "PHI": "76ers", "PHX": "Suns",
    "POR": "Trail Blazers", "SAC": "Kings", "SAS": "Spurs", "TOR": "Raptors",
    "UTA": "Jazz", "WAS": "Wizards",
    # Historical
    "NJN": "Nets", "NOH": "Hornets", "NOK": "Hornets", "SEA": "Supersonics",
    "VAN": "Grizzlies", "CHH": "Hornets", "WSB": "Wizards",
    "CHA": "Hornets", "CHO": "Hornets",
}


def format_season(s: str) -> str:
    """Convert '2003-04' to '03-04' for compact display."""
    parts = s.split("-")
    if len(parts) == 2:
        return f"'{parts[0][2:]}-'{parts[1]}"
    return s


def main():
    results = []
    failed = []

    for i, (name, alts) in enumerate(TARGET_PLAYERS, 1):
        print(f"[{i}/{len(TARGET_PLAYERS)}] Looking up {name}...", flush=True)
        player_id = find_player_id(name)
        if player_id is None:
            print(f"  !! NOT FOUND: {name}")
            failed.append(name)
            continue

        try:
            data = get_career_data(player_id)
        except Exception as e:
            print(f"  !! ERROR for {name}: {e}")
            failed.append(name)
            continue

        # Convert stints to (team_full_name, start_year, end_year)
        team_stints = []
        for abbr, first, last in data["stints"]:
            team_name = ABBR_TO_FULL.get(abbr, abbr)
            # Extract years: "2003-04" → 2003, "2003-04" to "2009-10" → 2003-2010
            start_year = first.split("-")[0]
            end_parts = last.split("-")
            end_year = str(int(end_parts[0]) + 1)  # "2009-10" → 2010
            team_stints.append((team_name, int(start_year), int(end_year)))

        stats = data["stats"]
        results.append({
            "id": i,
            "name": name,
            "alts": alts,
            "stints": team_stints,  # [(team, start_year, end_year), ...]
            "stats": stats,         # {ppg, rpg, apg, gp}
        })
        print(f"  OK: {len(team_stints)} stints, {stats.get('ppg', '?')} ppg")

    # Save to JSON
    import pathlib
    out_path = pathlib.Path(__file__).parent / "nba_players.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nSaved {len(results)} players to {out_path}")

    if failed:
        print(f"FAILED ({len(failed)}): {failed}")


if __name__ == "__main__":
    main()
