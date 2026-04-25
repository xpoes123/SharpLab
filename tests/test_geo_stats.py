"""Unit tests for geography accuracy stats.

Tests cover:
- record_geo_attempt upsert behaviour
- get_geo_stats_by_region aggregation
- COUNTRY_REGIONS completeness (every country in CAPITALS and COUNTRY_CODES has a region)
"""
import asyncio
import importlib
import os
import sys
import types

import pytest
import aiosqlite

# ── Stub db.queries so the geography module can be imported ──────────────────
# The geography cog imports functions from db.queries at module level.
# We install a stub with those names so the import succeeds, then reload the
# real db.schema / db.queries for the actual query tests below.
if "db.queries" not in sys.modules:
    _queries_stub = types.ModuleType("db.queries")

    async def _noop(*a, **kw):
        pass

    _queries_stub.record_geo_attempt = _noop
    _queries_stub.get_geo_stats_by_region = _noop
    _queries_stub.get_elo_rating = _noop

    _schema_stub = types.ModuleType("db.schema")
    _schema_stub.DB_PATH = "data/sharplab.db"

    _db_stub = types.ModuleType("db")
    _db_stub.queries = _queries_stub
    _db_stub.schema = _schema_stub
    sys.modules["db"] = _db_stub
    sys.modules["db.queries"] = _queries_stub
    sys.modules["db.schema"] = _schema_stub

from bot.cogs.geography import (  # noqa: E402
    CAPITALS,
    COUNTRY_CODES,
    COUNTRY_REGIONS,
    US_STATE_CAPITALS,
)

# ── Now force-reload the real db modules for query testing ───────────────────
# Remove stubs and import the real implementations
for _mod in ("db", "db.queries", "db.schema"):
    sys.modules.pop(_mod, None)

import db.schema as _schema  # noqa: E402
import db.queries as _queries  # noqa: E402


@pytest.fixture()
def tmp_db(tmp_path):
    """Create a temporary SQLite database with the full schema."""
    db_path = str(tmp_path / "test.db")
    original_schema = _schema.DB_PATH
    original_queries = _queries.DB_PATH
    _schema.DB_PATH = db_path
    _queries.DB_PATH = db_path
    asyncio.run(_schema.init_db())
    yield db_path
    _schema.DB_PATH = original_schema
    _queries.DB_PATH = original_queries


def _run(coro):
    """Run an async coroutine from sync test code (Python 3.14 compatible)."""
    return asyncio.run(coro)


# ── Region mapping completeness ──────────────────────────────────────────────


class TestCountryRegions:
    def test_all_capitals_have_region(self):
        missing = [c for c in CAPITALS if c not in COUNTRY_REGIONS]
        assert not missing, f"Countries in CAPITALS missing from COUNTRY_REGIONS: {missing}"

    def test_all_country_codes_have_region(self):
        missing = [c for c in COUNTRY_CODES if c not in COUNTRY_REGIONS]
        assert not missing, f"Countries in COUNTRY_CODES missing from COUNTRY_REGIONS: {missing}"

    def test_valid_regions_only(self):
        valid = {"Europe", "Asia", "Africa", "Americas", "Oceania"}
        for country, region in COUNTRY_REGIONS.items():
            assert region in valid, f"{country} has invalid region {region!r}"

    def test_us_states_not_in_regions(self):
        for state in US_STATE_CAPITALS:
            if state in COUNTRY_REGIONS:
                # Georgia is both a country and a US state — that's fine
                if state == "Georgia":
                    continue
                pytest.fail(f"US state {state} found in COUNTRY_REGIONS")


# ── record_geo_attempt upsert ────────────────────────────────────────────────


class TestRecordGeoAttempt:
    def test_first_correct_attempt(self, tmp_db):
        _run(
            _queries.record_geo_attempt("user1", "France", "Europe", "country_cap", True)
        )
        rows = _run(
            _queries.get_geo_stats("user1")
        )
        assert len(rows) == 1
        assert rows[0]["country"] == "France"
        assert rows[0]["correct"] == 1
        assert rows[0]["total"] == 1

    def test_first_incorrect_attempt(self, tmp_db):
        _run(
            _queries.record_geo_attempt("user1", "Japan", "Asia", "country_cap", False)
        )
        rows = _run(
            _queries.get_geo_stats("user1")
        )
        assert len(rows) == 1
        assert rows[0]["correct"] == 0
        assert rows[0]["total"] == 1

    def test_upsert_increments(self, tmp_db):
        _run(_queries.record_geo_attempt("user1", "France", "Europe", "country_cap", True))
        _run(_queries.record_geo_attempt("user1", "France", "Europe", "country_cap", False))
        _run(_queries.record_geo_attempt("user1", "France", "Europe", "country_cap", True))
        rows = _run(_queries.get_geo_stats("user1"))
        assert len(rows) == 1
        assert rows[0]["correct"] == 2
        assert rows[0]["total"] == 3

    def test_different_categories_separate_rows(self, tmp_db):
        _run(_queries.record_geo_attempt("user1", "France", "Europe", "country_cap", True))
        _run(_queries.record_geo_attempt("user1", "France", "Europe", "country_flag", False))
        rows = _run(_queries.get_geo_stats("user1"))
        assert len(rows) == 2

    def test_different_users_separate(self, tmp_db):
        _run(_queries.record_geo_attempt("user1", "France", "Europe", "country_cap", True))
        _run(_queries.record_geo_attempt("user2", "France", "Europe", "country_cap", False))
        rows1 = _run(_queries.get_geo_stats("user1"))
        rows2 = _run(_queries.get_geo_stats("user2"))
        assert len(rows1) == 1
        assert rows1[0]["correct"] == 1
        assert len(rows2) == 1
        assert rows2[0]["correct"] == 0


# ── get_geo_stats_by_region aggregation ──────────────────────────────────────


class TestGetGeoStatsByRegion:
    def test_aggregates_across_countries(self, tmp_db):
        _run(_queries.record_geo_attempt("user1", "France", "Europe", "country_cap", True))
        _run(_queries.record_geo_attempt("user1", "Germany", "Europe", "country_cap", True))
        _run(_queries.record_geo_attempt("user1", "Spain", "Europe", "country_cap", False))
        regions = _run(_queries.get_geo_stats_by_region("user1"))
        assert len(regions) == 1
        assert regions[0]["region"] == "Europe"
        assert regions[0]["correct"] == 2
        assert regions[0]["total"] == 3

    def test_multiple_regions(self, tmp_db):
        _run(_queries.record_geo_attempt("user1", "France", "Europe", "country_cap", True))
        _run(_queries.record_geo_attempt("user1", "Japan", "Asia", "country_cap", False))
        _run(_queries.record_geo_attempt("user1", "Brazil", "Americas", "country_cap", True))
        regions = _run(_queries.get_geo_stats_by_region("user1"))
        assert len(regions) == 3
        region_names = {r["region"] for r in regions}
        assert region_names == {"Europe", "Asia", "Americas"}

    def test_empty_user(self, tmp_db):
        regions = _run(
            _queries.get_geo_stats_by_region("nonexistent")
        )
        assert regions == []

    def test_aggregates_across_categories(self, tmp_db):
        _run(_queries.record_geo_attempt("user1", "France", "Europe", "country_cap", True))
        _run(_queries.record_geo_attempt("user1", "France", "Europe", "country_flag", False))
        regions = _run(_queries.get_geo_stats_by_region("user1"))
        assert len(regions) == 1
        assert regions[0]["correct"] == 1
        assert regions[0]["total"] == 2
