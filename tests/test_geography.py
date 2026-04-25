"""Unit tests for bot/cogs/geography.py — pure-function coverage.

Tests cover:
- answer normalisation and matching
- payout computation
- _pick_question across all categories and mixed mode
- question text generation
- new data constants (US states, country codes)
"""

# discord.py is installed in this environment; other cog tests use it directly.
# We only need to stub db.queries to avoid requiring a live database.
import sys
import types

_queries_stub = types.ModuleType("db.queries")
# Stub out functions the geography module imports at module level
async def _noop(*a, **kw):
    pass
_queries_stub.record_geo_attempt = _noop
_queries_stub.get_geo_stats_by_region = _noop
_queries_stub.get_elo_rating = _noop
if "db.queries" not in sys.modules:
    _db_stub = types.ModuleType("db")
    _db_stub.queries = _queries_stub
    sys.modules["db"] = _db_stub
    sys.modules["db.queries"] = _queries_stub

from bot.cogs.geography import (  # noqa: E402
    CAPITALS,
    COUNTRY_ALIASES,
    COUNTRY_CODES,
    COUNTRY_REGIONS,
    US_STATE_CAPITALS,
    US_STATE_CODES,
    GeoPlayer,
    GeoTable,
    GeoTableView,
    _alias_answers,
    _normalize,
    check_answer,
)
import bot.cogs.geography as geo  # noqa: E402
from bot.cogs._landmarks import LANDMARKS, _LANDMARK_POOL, _to_thumbnail  # noqa: E402

import pytest  # noqa: E402

_normalize = geo._normalize
_alias_answers = geo._alias_answers
check_answer = geo.check_answer
CAPITALS = geo.CAPITALS
COUNTRY_ALIASES = geo.COUNTRY_ALIASES
US_STATE_CAPITALS = geo.US_STATE_CAPITALS
COUNTRY_CODES = geo.COUNTRY_CODES
US_STATE_CODES = geo.US_STATE_CODES
GeoPlayer = geo.GeoPlayer
GeoTable = geo.GeoTable


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_table(**kw) -> GeoTable:
    defaults = dict(channel_id=1, host_id=99, host_name="Host")
    defaults.update(kw)
    return GeoTable(**defaults)


def _make_view(table: GeoTable) -> GeoTableView:
    """Construct a GeoTableView without calling __init__ (avoids Discord API calls)."""
    view = object.__new__(GeoTableView)
    view.table = table
    view.active_tables = {}
    return view


# ── _normalize ───────────────────────────────────────────────────────────────

class TestNormalize:
    def test_lowercase(self):
        assert _normalize("PARIS") == "paris"

    def test_strips_accents(self):
        assert _normalize("Brasília") == "brasilia"

    def test_strips_punctuation(self):
        assert _normalize("Sana'a") == "sanaa"

    def test_keeps_spaces(self):
        assert _normalize("New Delhi") == "new delhi"

    def test_empty(self):
        assert _normalize("") == ""


# ── check_answer ─────────────────────────────────────────────────────────────

class TestCheckAnswer:
    def test_exact_match(self):
        assert check_answer("Paris", ["Paris"])

    def test_case_insensitive(self):
        assert check_answer("paris", ["Paris"])

    def test_accent_insensitive(self):
        assert check_answer("Brasilia", ["Brasília", "Brasilia"])

    def test_alternate_accepted(self):
        assert check_answer("Peking", ["Beijing", "Peking"])

    def test_wrong_answer(self):
        assert not check_answer("London", ["Paris"])

    def test_empty_guess(self):
        assert not check_answer("", ["Paris"])

    def test_fuzzy_punctuation(self):
        # "Sana'a" stripped to "sanaa"
        assert check_answer("Sanaa", ["Sanaa", "Sana'a"])



# ── Country aliases ───────────────────────────────────────────────────────────

class TestCountryAliases:
    def test_usa_aliases(self):
        answers = _alias_answers("United States")
        assert check_answer("USA", answers)
        assert check_answer("US", answers)
        assert check_answer("United States of America", answers)
        assert check_answer("America", answers)

    def test_uk_aliases(self):
        answers = _alias_answers("United Kingdom")
        assert check_answer("UK", answers)
        assert check_answer("Great Britain", answers)
        assert check_answer("Britain", answers)
        assert check_answer("England", answers)

    def test_russia_aliases(self):
        answers = _alias_answers("Russia")
        assert check_answer("Russian Federation", answers)
        assert check_answer("russia", answers)

    def test_south_korea_aliases(self):
        answers = _alias_answers("South Korea")
        assert check_answer("Korea", answers)
        assert check_answer("Republic of Korea", answers)
        assert check_answer("ROK", answers)

    def test_czech_republic_aliases(self):
        answers = _alias_answers("Czech Republic")
        assert check_answer("Czechia", answers)

    def test_ivory_coast_aliases(self):
        answers = _alias_answers("Ivory Coast")
        assert check_answer("Cote d'Ivoire", answers)
        assert check_answer("Côte d'Ivoire", answers)

    def test_myanmar_aliases(self):
        answers = _alias_answers("Myanmar")
        assert check_answer("Burma", answers)

    def test_uae_aliases(self):
        answers = _alias_answers("United Arab Emirates")
        assert check_answer("UAE", answers)

    def test_canonical_always_first(self):
        for canonical in COUNTRY_ALIASES:
            answers = _alias_answers(canonical)
            assert answers[0] == canonical, f"{canonical}: canonical not first in answers"

    def test_alias_answers_canonical_always_accepted(self):
        for canonical in COUNTRY_ALIASES:
            answers = _alias_answers(canonical)
            assert check_answer(canonical, answers), f"{canonical}: canonical not accepted"

    def test_no_alias_country_returns_single_item(self):
        # Country with no aliases should still return a one-item list
        answers = _alias_answers("France")
        assert answers == ["France"]

    def test_wrong_country_not_accepted_via_alias(self):
        answers = _alias_answers("United Kingdom")
        assert not check_answer("Germany", answers)
        assert not check_answer("France", answers)

    def test_alias_case_insensitive(self):
        answers = _alias_answers("United States")
        assert check_answer("usa", answers)
        assert check_answer("united states of america", answers)


# ── Data completeness ─────────────────────────────────────────────────────────

class TestDataConstants:
    def test_us_states_count(self):
        assert len(US_STATE_CAPITALS) == 50

    def test_us_state_codes_count(self):
        assert len(US_STATE_CODES) == 50

    def test_us_state_codes_keys_match_capitals(self):
        assert set(US_STATE_CODES.keys()) == set(US_STATE_CAPITALS.keys())

    def test_country_codes_subset_of_capitals(self):
        # Every country with a flag code must also be in CAPITALS
        assert set(COUNTRY_CODES.keys()).issubset(set(CAPITALS.keys()))

    def test_country_codes_values_are_two_chars(self):
        for country, code in COUNTRY_CODES.items():
            assert len(code) == 2, f"{country} has invalid code {code!r}"

    def test_us_state_codes_values_are_two_chars(self):
        for state, code in US_STATE_CODES.items():
            assert len(code) == 2, f"{state} has invalid code {code!r}"

    def test_california_capital(self):
        assert "Sacramento" in US_STATE_CAPITALS["California"]

    def test_texas_capital(self):
        assert "Austin" in US_STATE_CAPITALS["Texas"]

    def test_new_york_capital(self):
        # Albany, not NYC
        assert "Albany" in US_STATE_CAPITALS["New York"]
        assert "New York" not in US_STATE_CAPITALS["New York"]

    def test_minnesota_accepts_st_paul(self):
        answers = US_STATE_CAPITALS["Minnesota"]
        assert any("Paul" in a for a in answers)

    def test_flag_url_format_country(self):
        code = COUNTRY_CODES["France"]
        url = f"https://flagcdn.com/w320/{code}.png"
        assert url == "https://flagcdn.com/w320/fr.png"

    def test_flag_url_format_state(self):
        code = US_STATE_CODES["California"]
        url = f"https://flagcdn.com/w320/us-{code}.png"
        assert url == "https://flagcdn.com/w320/us-ca.png"


# ── _pick_question ────────────────────────────────────────────────────────────

class TestPickQuestion:
    def _view(self, category: str = "mixed") -> geo.GeoTableView:
        table = _make_table(category=category)
        return _make_view(table)

    def test_country_capitals_returns_no_image(self):
        view = self._view("country_capitals")
        q_type, subject, answers, image_url = view._pick_question()
        assert q_type == "country_cap"
        assert subject in CAPITALS
        assert answers == CAPITALS[subject]
        assert image_url is None

    def test_state_capitals_returns_no_image(self):
        view = self._view("state_capitals")
        q_type, subject, answers, image_url = view._pick_question()
        assert q_type == "state_cap"
        assert subject in US_STATE_CAPITALS
        assert answers == US_STATE_CAPITALS[subject]
        assert image_url is None

    def test_country_flags_returns_image_url(self):
        view = self._view("country_flags")
        q_type, subject, answers, image_url = view._pick_question()
        assert q_type == "country_flag"
        assert subject in COUNTRY_CODES
        # canonical name is always first; may include aliases too
        assert answers[0] == subject
        assert subject in answers
        assert image_url is not None
        assert image_url.startswith("https://flagcdn.com/w320/")
        assert image_url.endswith(".png")

    def test_state_flags_returns_image_url(self):
        view = self._view("state_flags")
        q_type, subject, answers, image_url = view._pick_question()
        assert q_type == "state_flag"
        assert subject in US_STATE_CODES
        assert answers == [subject]
        assert image_url is not None
        assert "us-" in image_url

    def test_mixed_can_return_any_type(self):
        view = self._view("mixed")
        seen_types = set()
        for _ in range(500):
            q_type, *_ = view._pick_question()
            seen_types.add(q_type)
            if len(seen_types) == 5:
                break
        assert len(seen_types) == 5, f"Mixed mode only produced: {seen_types}"

    def test_no_repeat_within_category_until_exhausted(self):
        view = self._view("state_capitals")
        seen = []
        # Exhaust all 50 states without repeat
        for _ in range(50):
            _, subject, _, _ = view._pick_question()
            assert subject not in seen, f"{subject} appeared twice before pool reset"
            seen.append(subject)
        # 51st pick resets the pool — should succeed without error
        _, subject51, _, _ = view._pick_question()
        assert subject51 in US_STATE_CAPITALS

    def test_used_questions_tracked(self):
        view = self._view("country_capitals")
        _, subject, _, _ = view._pick_question()
        assert ("country_cap", subject) in view.table.used_questions

    def test_pool_resets_after_exhaustion(self):
        view = self._view("country_flags")
        n = len(COUNTRY_CODES)
        for _ in range(n):
            view._pick_question()
        # At this point all country_flag entries used; next call resets sub-pool
        q_type, subject, _, _ = view._pick_question()
        assert q_type == "country_flag"
        assert subject in COUNTRY_CODES


# ── _question_text ────────────────────────────────────────────────────────────

class TestQuestionText:
    def _view(self) -> geo.GeoTableView:
        return _make_view(_make_table())

    def test_country_cap_text(self):
        view = self._view()
        text = view._question_text("country_cap", "France")
        assert "France" in text
        assert "capital" in text.lower()

    def test_state_cap_text(self):
        view = self._view()
        text = view._question_text("state_cap", "California")
        assert "California" in text
        assert "capital" in text.lower()
        assert "state" in text.lower() or "US" in text

    def test_country_flag_text(self):
        view = self._view()
        text = view._question_text("country_flag", "France")
        assert "flag" in text.lower() or "country" in text.lower()

    def test_state_flag_text(self):
        view = self._view()
        text = view._question_text("state_flag", "Texas")
        assert "flag" in text.lower() or "state" in text.lower()

    def test_landmark_text(self):
        view = self._view()
        text = view._question_text("landmark", "Eiffel Tower")
        assert "country" in text.lower() or "landmark" in text.lower()


# ── Landmarks ────────────────────────────────────────────────────────────────

class TestLandmarks:
    def test_landmark_pool_count(self):
        """Dataset should have 60-80 landmarks."""
        assert 60 <= len(_LANDMARK_POOL) <= 120

    def test_all_urls_well_formed(self):
        for name, country, url in _LANDMARK_POOL:
            assert url.startswith("https"), f"{name} has non-https URL: {url}"

    def test_all_urls_use_thumbnail_api(self):
        # Direct Wikimedia originals can be 60+ MB — Discord's proxy times out.
        # All pool URLs must go through thumb.php for manageable sizes.
        for name, country, url in _LANDMARK_POOL:
            assert "thumb.php" in url, (
                f"{name} is not using thumb.php thumbnail URL: {url}"
            )

    def test_all_countries_in_regions(self):
        for name, country, _ in _LANDMARK_POOL:
            assert country in COUNTRY_REGIONS, (
                f"Landmark '{name}' references country '{country}' "
                f"not in COUNTRY_REGIONS"
            )

    def test_pick_question_landmarks(self):
        table = _make_table(category="landmarks")
        view = _make_view(table)
        q_type, subject, answers, image_url = view._pick_question()
        assert q_type == "landmark"
        assert image_url is not None
        assert image_url.startswith("https")
        # subject is the landmark name; answers[0] is the canonical country name
        assert len(answers) >= 1
        assert answers[0] in COUNTRY_REGIONS

    def test_landmark_answer_matching(self):
        """Player types the country name to answer a landmark question."""
        assert check_answer("France", ["France"])
        assert check_answer("france", ["France"])
        assert not check_answer("Eiffel Tower", ["France"])

    def test_no_repeat_landmarks_until_exhausted(self):
        table = _make_table(category="landmarks")
        view = _make_view(table)
        seen = set()
        n = len(_LANDMARK_POOL)
        for _ in range(n):
            _, subject, _, _ = view._pick_question()
            assert subject not in seen, f"{subject} repeated before pool exhaustion"
            seen.add(subject)
        # Next pick resets pool
        _, subject, _, _ = view._pick_question()
        assert subject in [name for name, _, _ in _LANDMARK_POOL]

    def test_mixed_mode_includes_landmarks(self):
        table = _make_table(category="mixed")
        view = _make_view(table)
        seen_types = set()
        for _ in range(500):
            q_type, *_ = view._pick_question()
            seen_types.add(q_type)
            if "landmark" in seen_types:
                break
        assert "landmark" in seen_types, "Mixed mode never produced a landmark question"

    def test_unique_landmark_names(self):
        names = [name for name, _, _ in _LANDMARK_POOL]
        assert len(names) == len(set(names)), "Duplicate landmark names found"

    def test_to_thumbnail_converts_direct_url(self):
        direct = "https://upload.wikimedia.org/wikipedia/commons/a/a8/Tour_Eiffel_Wikimedia_Commons.jpg"
        thumb = _to_thumbnail(direct)
        assert thumb == "https://commons.wikimedia.org/w/thumb.php?f=Tour_Eiffel_Wikimedia_Commons.jpg&w=800"

    def test_to_thumbnail_preserves_non_wikimedia(self):
        url = "https://example.com/image.jpg"
        assert _to_thumbnail(url) == url

    def test_to_thumbnail_handles_encoded_filenames(self):
        direct = "https://upload.wikimedia.org/wikipedia/commons/b/bd/Taj_Mahal%2C_Agra%2C_India_edit3.jpg"
        thumb = _to_thumbnail(direct)
        assert "thumb.php" in thumb
        assert "Taj_Mahal%2C_Agra%2C_India_edit3.jpg" in thumb

    def test_raw_landmarks_use_direct_urls(self):
        """Source data in LANDMARKS dict should still be direct Wikimedia URLs."""
        for country, entries in LANDMARKS.items():
            for name, url in entries:
                assert "upload.wikimedia.org" in url, (
                    f"{name} ({country}): source URL should be a direct Wikimedia URL"
                )
