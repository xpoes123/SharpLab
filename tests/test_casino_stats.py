"""Tests for /casino-stats embed truncation logic."""

import discord
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.cogs.casino import GAME_LABELS


def _build_per_game_field(by_game: list[dict]) -> str:
    """Reproduce the Per Game field building logic from casino_stats."""
    lines = []
    for row in by_game:
        g_net = row["net_profit"]
        dot = "\U0001f7e2" if g_net > 0 else "\U0001f534" if g_net < 0 else "\u26aa"
        label = GAME_LABELS.get(row["game"], row["game"].capitalize())
        lines.append(f"{dot} **{label}** — {row['rounds']} rounds — {g_net:+,}c")

    text = "\n".join(lines)
    if len(text) > 1024:
        truncated = []
        length = 0
        for i, line in enumerate(lines):
            needed = len(line) + (1 if truncated else 0)
            remaining = len(lines) - i
            suffix = f"\n*...and {remaining} more*"
            if length + needed + len(suffix) > 1024:
                truncated.append(f"*...and {remaining} more*")
                break
            truncated.append(line)
            length += needed
        text = "\n".join(truncated)
    return text


class TestCasinoStatsTruncation:
    def _make_rows(self, count: int) -> list[dict]:
        """Generate fake by-game rows."""
        return [
            {"game": f"game_{i}", "rounds": 100 + i, "net_profit": (-1) ** i * (i * 50)}
            for i in range(count)
        ]

    def test_few_games_no_truncation(self):
        rows = self._make_rows(3)
        text = _build_per_game_field(rows)
        assert len(text) <= 1024
        assert "...and" not in text

    def test_many_games_truncated_under_limit(self):
        rows = self._make_rows(35)
        text = _build_per_game_field(rows)
        assert len(text) <= 1024, f"Field value is {len(text)} chars, exceeds 1024"
        assert "...and" in text
        assert "more*" in text

    def test_exact_boundary(self):
        """Even with 50 games, output stays under 1024."""
        rows = self._make_rows(50)
        text = _build_per_game_field(rows)
        assert len(text) <= 1024

    def test_truncation_mentions_remaining_count(self):
        rows = self._make_rows(35)
        text = _build_per_game_field(rows)
        # The last line should say how many are left
        last_line = text.strip().split("\n")[-1]
        assert last_line.startswith("*...and ")
        assert last_line.endswith(" more*")
        # Extract the number and verify it's positive
        remaining = int(last_line.split("and ")[1].split(" more")[0])
        assert remaining > 0
