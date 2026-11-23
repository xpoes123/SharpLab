"""HQ portfolio upload: a raw Fidelity export previews, then full-replaces on apply."""
import asyncio
import os
import tempfile

from fastapi.testclient import TestClient

import db.queries as q
import db.schema as sch
from web import auth
from web.api import app

FID = ("﻿Account number,Account name,Symbol,Description,Quantity,Current value,Cost basis total,Type\n"
       "X1,Individual - TOD,SPAXX**,MM,,$100.00,,Cash,\n"
       "2,ROTH IRA,BRKB,BRK,2,$1.00,$900.00,Cash,\n"
       "3,Health Savings Account,VOO,VOO,1,$1.00,$500.00,Cash,\n")


def test_import_preview_then_apply():
    sch.DB_PATH = q.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
    asyncio.run(sch.init_db())
    asyncio.run(q.add_stock_trade("42", "TSLA", "buy", 5, 200))
    c = TestClient(app)
    assert c.post("/api/v1/hq/stocks/import", json={"csv": FID}).status_code == 401
    c.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie({"id": "42", "username": "n"}))
    prev = c.post("/api/v1/hq/stocks/import", json={"csv": FID}).json()
    assert not prev["applied"] and [r["ticker"] for r in prev["rows"]] == ["BRK-B", "VOO"] and prev["cash"] == 100.0
    assert {h["ticker"] for h in asyncio.run(q.get_stock_holdings("42"))} == {"TSLA"}   # preview wrote nothing
    res = c.post("/api/v1/hq/stocks/import", json={"csv": FID, "apply": True}).json()
    assert res["applied"] and res["positions"] == 2 and res["cash"] == 100.0
    assert {h["ticker"] for h in asyncio.run(q.get_stock_holdings("42"))} == {"BRK-B", "VOO"}
    assert c.post("/api/v1/hq/stocks/import", json={"csv": "junk"}).status_code == 400
