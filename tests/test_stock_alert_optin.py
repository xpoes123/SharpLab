"""Portfolio swing alerts are opt-in: nobody is pinged unless they enabled it."""
from __future__ import annotations

import asyncio

import pytest

import db.schema as _schema
import db.queries as _queries


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def tmp_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    orig_s, orig_q = _schema.DB_PATH, _queries.DB_PATH
    _schema.DB_PATH = _queries.DB_PATH = db_path
    _run(_schema.init_db())
    yield db_path
    _schema.DB_PATH, _queries.DB_PATH = orig_s, orig_q


def test_default_is_opted_out(tmp_db):
    # A user who never touched the setting must not receive swing pings.
    assert _run(_queries.get_stock_alerts_enabled("never_set")) is False
    assert _run(_queries.get_stock_alert_optin_users()) == set()


def test_opt_in_then_out(tmp_db):
    _run(_queries.set_stock_alerts_enabled("u1", True))
    _run(_queries.set_stock_alerts_enabled("u2", False))
    assert _run(_queries.get_stock_alerts_enabled("u1")) is True
    assert _run(_queries.get_stock_alert_optin_users()) == {"u1"}

    _run(_queries.set_stock_alerts_enabled("u1", False))
    assert _run(_queries.get_stock_alerts_enabled("u1")) is False
    assert _run(_queries.get_stock_alert_optin_users()) == set()


def test_opt_in_coexists_with_other_user_settings(tmp_db):
    # Toggling the flag must not clobber unrelated settings on the same row.
    _run(_queries.set_user_books("u1", ["fanduel", "draftkings"]))
    _run(_queries.set_livebet_alerts_enabled("u1", True))
    _run(_queries.set_stock_alerts_enabled("u1", True))
    assert _run(_queries.get_user_books("u1")) == ["fanduel", "draftkings"]
    assert _run(_queries.get_livebet_optin_users()) == {"u1"}
    assert _run(_queries.get_stock_alert_optin_users()) == {"u1"}
