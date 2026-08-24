"""Curated NBA draft-class rosters for premium card sets. Fame score drives rarity
(higher = rarer). All are rookie cards. 1979 is framed as the 1979-80 rookie class,
so Larry Bird (drafted 1978, debuted 1979-80) is intentionally included."""

ROSTERS: dict[tuple[str, int], dict] = {
    ("nba", 2003): {
        "name": "2003 NBA Draft Class",
        "box_price": 50_000,
        "boxes": 20,
        "players": [
            ("LeBron James", 100.0), ("Dwyane Wade", 92.0), ("Carmelo Anthony", 85.0),
            ("Chris Bosh", 75.0), ("Kyle Korver", 45.0), ("David West", 44.0),
            ("Josh Howard", 41.0), ("Boris Diaw", 39.0), ("Mo Williams", 38.0),
            ("Leandro Barbosa", 36.0), ("Kendrick Perkins", 34.0), ("Kirk Hinrich", 33.0),
            ("Nick Collison", 26.0), ("Zaza Pachulia", 24.0), ("T.J. Ford", 23.0),
            ("Steve Blake", 22.0), ("Luke Walton", 20.0), ("Darko Milicic", 19.0),
            ("Willie Green", 15.0), ("Travis Outlaw", 14.0),
        ],
    },
    ("nba", 1979): {
        "name": "1979 Rookies — Magic & Bird",
        "box_price": 150_000,
        "boxes": 12,
        "players": [
            ("Magic Johnson", 100.0), ("Larry Bird", 98.0), ("Sidney Moncrief", 55.0),
            ("Bill Cartwright", 45.0), ("Vinnie Johnson", 40.0), ("Jim Paxson", 38.0),
            ("Calvin Natt", 36.0), ("Bill Laimbeer", 34.0), ("James Bailey", 20.0),
            ("Larry Demic", 15.0), ("Roger Phegley", 13.0), ("Cliff Robinson", 22.0),
        ],
    },
    ("nba", 1984): {
        "name": "1984 NBA Draft Class",
        "box_price": 250_000,
        "boxes": 6,
        "players": [
            ("Michael Jordan", 100.0), ("Hakeem Olajuwon", 92.0), ("Charles Barkley", 88.0),
            ("John Stockton", 84.0), ("Sam Perkins", 45.0), ("Otis Thorpe", 43.0),
            ("Kevin Willis", 42.0), ("Alvin Robertson", 40.0), ("Jerome Kersey", 35.0),
            ("Sam Bowie", 30.0), ("Vern Fleming", 27.0), ("Michael Cage", 26.0),
            ("Jay Humphries", 22.0), ("Tony Campbell", 20.0),
        ],
    },
}
