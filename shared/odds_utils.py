"""
Odds format conversion. This is the ONLY place that converts between formats.
All sources (Kalshi, Polymarket) return probabilities — convert here at the boundary.
"""


def prob_to_american(prob: float) -> int:
    """Convert implied probability (0–1) to American odds."""
    if prob <= 0 or prob >= 1:
        raise ValueError(f"Probability must be between 0 and 1, got {prob}")
    if prob >= 0.5:
        return round(-prob / (1 - prob) * 100)
    else:
        return round((1 - prob) / prob * 100)


def american_to_prob(odds: int) -> float:
    """Convert American odds to implied probability."""
    if odds > 0:
        return 100 / (odds + 100)
    else:
        return abs(odds) / (abs(odds) + 100)


def american_to_decimal(odds: int) -> float:
    """Convert American odds to decimal odds."""
    if odds > 0:
        return (odds / 100) + 1
    else:
        return (100 / abs(odds)) + 1


def decimal_to_american(decimal: float) -> int:
    """Convert decimal odds to American odds."""
    if decimal >= 2.0:
        return round((decimal - 1) * 100)
    else:
        return round(-100 / (decimal - 1))


def fmt_prob(odds: int) -> str:
    """Format American odds as implied probability percentage (e.g. -110 → '52.4%')."""
    return f"{american_to_prob(odds) * 100:.1f}%"


def parse_odds_input(raw: str) -> tuple[int, str]:
    """
    Parse odds in any supported format and return (american_odds, format_label).

    Supported formats:
    - American:  -110, +150, 150  (negative, explicit +, or integer >= 100)
    - Decimal:   1.91, 2.50       (float with decimal point, value >= 1.01)
    - Cents:     52, 65           (integer 1–99, Kalshi/Polymarket style)
    - Prob:      0.52             (float with decimal point, value < 1.0)
    - Percent:   52%              (explicit % suffix)
    """
    raw = raw.strip()

    if raw.endswith("%"):
        prob = float(raw[:-1]) / 100
        return prob_to_american(prob), "percent"

    if "." in raw:
        val = float(raw)
        if val < 1.0:
            return prob_to_american(val), "prob"
        else:
            return decimal_to_american(val), "decimal"

    val = int(raw.lstrip("+"))

    # Cents: unsigned integer 1–99 (Kalshi / Polymarket price)
    if not raw.startswith("-") and not raw.startswith("+") and 1 <= val <= 99:
        return prob_to_american(val / 100), "cents"

    return val, "american"
