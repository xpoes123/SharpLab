"""Unit tests for quizbowl answer-checking helpers.

Tests cover:
  1. _normalize — lowercasing, HTML stripping, punctuation/article removal
  2. _extract_alternates — bracket parsing for [accept X] and [or Y] forms
  3. _local_check_answer — fuzzy fallback matching
"""
import pytest

from bot.cogs.quizbowl import _extract_alternates, _local_check_answer, _normalize


# ── _normalize ──────────────────────────────────────────────────────────────

def test_normalize_lowercases():
    assert _normalize("Soviet Union") == "soviet union"


def test_normalize_strips_html():
    assert _normalize("<b>Elizabeth</b> Cady <i>Stanton</i>") == "elizabeth cady stanton"


def test_normalize_removes_punctuation():
    assert _normalize("U.S.S.R.") == "ussr"  # dots removed, letters kept


def test_normalize_removes_articles():
    assert _normalize("The United States") == "united states"
    assert _normalize("A Nation") == "nation"


def test_normalize_collapses_whitespace():
    assert _normalize("  foo   bar  ") == "foo bar"


# ── _extract_alternates ─────────────────────────────────────────────────────

def test_extract_main_only():
    alts = _extract_alternates("Elizabeth Cady Stanton")
    assert alts[0] == "Elizabeth Cady Stanton"
    assert len(alts) == 1


def test_extract_accept_bracket():
    alts = _extract_alternates("Soviet Union [accept USSR]")
    assert "Soviet Union " in alts[0]  # main
    assert any("USSR" in a for a in alts)


def test_extract_or_bracket():
    alts = _extract_alternates("Soviet Union [or USSR or Russia]")
    flat = " ".join(alts)
    assert "USSR" in flat
    assert "Russia" in flat


def test_extract_multiple_brackets():
    alts = _extract_alternates("Stanton [accept Elizabeth Stanton] [or E.C. Stanton]")
    assert len(alts) >= 3


def test_extract_case_insensitive_bracket_keyword():
    alts = _extract_alternates("foo [ACCEPT bar]")
    assert any("bar" in a for a in alts)


# ── _local_check_answer ─────────────────────────────────────────────────────

def test_exact_match():
    assert _local_check_answer("Elizabeth Cady Stanton", "Elizabeth Cady Stanton")


def test_case_insensitive():
    assert _local_check_answer("Elizabeth Cady Stanton", "elizabeth cady stanton")


def test_partial_name_fuzzy():
    # "Elizabeth Stanton" should fuzzy-match "Elizabeth Cady Stanton"
    assert _local_check_answer("Elizabeth Cady Stanton", "Elizabeth Stanton")


def test_bracket_alternate_accept():
    # "russia" should hit [accept USSR] path after normalization fuzzy check
    assert _local_check_answer("Soviet Union [accept USSR]", "USSR")


def test_bracket_alternate_or():
    assert _local_check_answer("Stanton [or Elizabeth Stanton]", "Elizabeth Stanton")


def test_substring_match():
    # Candidate is a substring of the answerline main answer
    assert _local_check_answer("Elizabeth Cady Stanton", "Stanton")


def test_reject_unrelated():
    assert not _local_check_answer("Elizabeth Cady Stanton", "suffrage")


def test_reject_too_short_after_normalize():
    # Empty string after normalization should not match
    assert not _local_check_answer("Elizabeth Cady Stanton", "  ")


def test_fuzzy_ratio_threshold():
    # "sufferage" (typo) vs "suffrage" — close enough (ratio > 0.75)
    assert _local_check_answer("suffrage", "sufferage")


def test_fuzzy_ratio_too_different():
    assert not _local_check_answer("Abraham Lincoln", "George Washington")
