"""Card economics — pure, DB-free so it unit-tests instantly.

Ported ~verbatim from nsba-markets' engine. Everything here is deterministic given
an rng; no DB, no I/O. The seeder assigns each subject a `stardom` score
(`career_fame * (ROOKIE_MULT if rookie else 1)`) and `build_manifest` ranks on it.
"""

from __future__ import annotations

import random

RARITIES = ["common", "uncommon", "rare", "epic", "legendary"]

# book_value == band floor (the un-pumpable rule). Starting values; a calibration
# script may nudge these to lock E[pack] to the pack RTP target.
BOOK = {"common": 3.5, "uncommon": 10.0, "rare": 35.0, "epic": 100.0, "legendary": 260.0}
BAND_CEIL_MULT = 3.0  # ceiling = floor * this

HOLO_RATE = 0.20
HOLO_MULT = 2.0

# gem -> (odds_denominator, value_multiplier, value_floor). Rolled independent of rarity.
GEMS = {
    "chrome": (50, 1.5, 0.0),
    "sapphire": (500, 5.0, 60.0),
    "ruby": (2000, 10.0, 150.0),
    "black_lotus": (100000, 25.0, 500.0),
}

# Relative copies-per-design by rarity — rarer tiers get FEWER copies. Scaled to hit total_cards.
COPIES_REL = {"common": 33, "uncommon": 17, "rare": 10, "epic": 6, "legendary": 5}

# --- Vintage pricing (SharpLab addition) ------------------------------------
# Older season packs cost more — you pay a premium for a shot at a now-legend's
# rookie. Stored per set at seed time, not recomputed live.
BASE_PACK_COST = 50      # coins for a current-season pack
AGE_STEP = 0.08          # per-year-older price growth
ROOKIE_MULT = 1.5        # rookie-year cards rank rarer (multiplies stardom in the seeder)


def pack_cost(season_year: int, current_year: int) -> int:
    """Coins to open one pack from `season_year`. Older seasons cost more (vintage premium)."""
    years_ago = max(0, current_year - season_year)
    return round(BASE_PACK_COST * (1 + AGE_STEP) ** years_ago)


def book_value(rarity: str, is_holo: bool, gem: str | None) -> float:
    v = BOOK[rarity]
    if is_holo:
        v *= HOLO_MULT
    if gem:
        mult, floor = GEMS[gem][1], GEMS[gem][2]
        v = max(v * mult, floor)
    return v


def band(rarity: str, is_holo: bool, gem: str | None) -> tuple[float, float]:
    floor = book_value(rarity, is_holo, gem)
    return floor, floor * BAND_CEIL_MULT


def roll_holo(rng: random.Random) -> bool:
    return rng.random() < HOLO_RATE


def roll_gem(rng: random.Random) -> str | None:
    # Sequential rolls, rarest-first, first hit wins.
    for name in ("black_lotus", "ruby", "sapphire", "chrome"):
        if rng.random() < 1.0 / GEMS[name][0]:
            return name
    return None


# Default rarity mix (by stardom rank): top 1% legendary, next 3% epic, then rare/uncommon/common.
PLAYER_TIERS = [
    ("legendary", 0.01),
    ("epic", 0.03),
    ("rare", 0.10),
    ("uncommon", 0.24),
    ("common", 0.62),
]


def build_manifest(
    subjects: list[dict], total_cards: int, tiers: list[tuple[str, float]] | None = None
) -> list[dict]:
    """Assign each subject a rarity by stardom rank, then copies (rarer = fewer).

    Rarity is by rank within `tiers` (default PLAYER_TIERS). Copies per design come from COPIES_REL
    (rarer tiers get fewer copies), scaled so the grand total lands on total_cards; any rounding
    remainder is absorbed by the LOWEST tier. Pass a custom `tiers` to skew a pool high.
    """
    tiers = tiers or PLAYER_TIERS
    lowest = tiers[-1][0]  # rounding remainder + copy diff land here
    ordered = sorted(subjects, key=lambda s: s["stardom"])  # low -> high stardom
    n = len(ordered)
    counts = {r: max(1, round(n * frac)) for r, frac in tiers}
    counts[lowest] = max(1, counts[lowest] + (n - sum(counts.values())))  # keep sum == n
    out, cursor = [], 0
    for r, _ in reversed(tiers):  # lowest stardom -> highest
        for s in ordered[cursor : cursor + counts[r]]:
            out.append({**s, "rarity": r, "book_value": book_value(r, False, None), "copies": 0})
        cursor += counts[r]
    raw = sum(COPIES_REL[d["rarity"]] for d in out)
    scale = total_cards / raw if raw else 1.0
    for d in out:
        d["copies"] = max(1, round(COPIES_REL[d["rarity"]] * scale))
    diff = total_cards - sum(d["copies"] for d in out)
    fillers = [d for d in out if d["rarity"] == lowest] or out
    i = 0
    while diff != 0 and fillers and i < 100 * len(fillers) + 1000:
        d = fillers[i % len(fillers)]
        step = 1 if diff > 0 else -1
        if d["copies"] + step >= 1:
            d["copies"] += step
            diff -= step
        i += 1
    return out


def expected_pack_value(manifest: list[dict], pack_size: int) -> float:
    total = sum(d["copies"] for d in manifest)
    # mean book value of one card drawn uniformly by copy, plus expected holo/gem uplift
    base = sum(d["book_value"] * d["copies"] for d in manifest) / total
    holo_uplift = base * HOLO_RATE  # doubling ~HOLO_RATE of cards adds ~base*rate
    gem_uplift = sum((1.0 / den) * max(base * mult, floor) for den, mult, floor in GEMS.values())
    return (base + holo_uplift + gem_uplift) * pack_size


def open_pack(
    pool: dict[int, int], manifest: list[dict], rng: random.Random, pack_size: int
) -> list[dict]:
    out = []
    for _ in range(pack_size):
        live = [(i, c) for i, c in pool.items() if c > 0]
        if not live:
            break
        total = sum(c for _, c in live)
        r = rng.randint(1, total)
        acc = 0
        pick = live[-1][0]
        for i, c in live:
            acc += c
            if r <= acc:
                pick = i
                break
        pool[pick] -= 1
        is_holo = roll_holo(rng)
        gem = roll_gem(rng)
        out.append(
            {
                "design_index": pick,
                "is_holo": is_holo,
                "gem": gem,
                "book_value": book_value(manifest[pick]["rarity"], is_holo, gem),
            }
        )
    return out


# gem -> Discord shout-out. Chrome is a ~1-in-50 roll — common enough that alerting on it would
# spam the channel — so only the three rarer gems count as "notable".
GEM_ALERT_TIER = {"sapphire", "ruby", "black_lotus"}


def is_notable_pull(card: dict) -> bool:
    """A pull worth a Discord shout-out — roughly the rarest sub-5% of cards: any epic or
    legendary, a holo rare, or a gem rarer than chrome. `card` carries rarity/is_holo/gem."""
    rarity = card.get("rarity")
    if rarity in ("epic", "legendary"):
        return True
    if card.get("gem") in GEM_ALERT_TIER:
        return True
    return bool(card.get("is_holo")) and rarity == "rare"


def describe_pull(card: dict) -> str:
    """Human-readable tag for a notable pull, e.g. '1-of-1 Legendary LeBron James (2003)'."""
    bits = []
    if card.get("total_copies") == 1:
        bits.append("1-of-1")
    gem = card.get("gem")
    if gem in GEM_ALERT_TIER:
        bits.append(gem.replace("_", " ").title())
    if card.get("is_holo"):
        bits.append("Holo")
    bits.append((card.get("rarity") or "").title())
    bits.append(card.get("name") or "?")
    tag = " ".join(b for b in bits if b)
    season = card.get("season")
    return f"{tag} ({season})" if season else tag
