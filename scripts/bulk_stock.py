#!/usr/bin/env python3
"""Bulk-set a user's stock portfolio from a CSV. The admin path for "redo my
stocks": wipes the user's existing stock + option trades and reseeds one buy per
CSV row, then (optionally) sets cash. Dry-run by default; pass --apply to commit.

    # preview
    ./venv/bin/python scripts/bulk_stock.py harsha port.csv --cash 20000
    # commit
    ./venv/bin/python scripts/bulk_stock.py harsha port.csv --cash 20000 --apply

CSV (header required), price = your average cost basis:

    ticker,shares,price
    AAPL,3,163.74
    GOOGL,7,150.85

Self-test: ./venv/bin/python scripts/bulk_stock.py --selftest

ponytail: stock-only, full-replace. No option rows, no per-lot edits — add a
`type` column + option handling when someone actually needs it.
"""
import argparse
import asyncio
import csv
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db import queries  # noqa: E402


def parse_csv(text: str) -> list[dict]:
    """Parse ticker,shares,price rows. Skips blanks/#comments. Raises on bad rows."""
    rows = []
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or {"ticker", "shares", "price"} - {f.strip().lower() for f in reader.fieldnames}:
        raise ValueError("CSV needs a header with columns: ticker,shares,price")
    for i, raw in enumerate(reader, start=2):
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        if not r.get("ticker") or r["ticker"].startswith("#"):
            continue
        try:
            shares, price = float(r["shares"]), float(r["price"])
        except ValueError:
            raise ValueError(f"row {i}: shares/price must be numbers ({r!r})")
        if shares <= 0 or price <= 0:
            raise ValueError(f"row {i}: shares and price must be > 0 ({r!r})")
        rows.append({"ticker": r["ticker"].upper(), "shares": shares, "price": price})
    if not rows:
        raise ValueError("no data rows found")
    return rows


async def resolve_user(who: str) -> str:
    """Accept a raw discord id or a username; return the id."""
    if who.isdigit():
        return who
    async with __import__("aiosqlite").connect(queries.DB_PATH) as db:
        cur = await db.execute(
            "SELECT discord_user FROM discord_users WHERE username = ? COLLATE NOCASE", (who,))
        row = await cur.fetchone()
    if not row:
        raise SystemExit(f"no user matching username {who!r} (pass the numeric id instead)")
    return row[0]


async def apply(user: str, rows: list[dict], cash: float | None, commit: bool):
    old_stocks = await queries.get_stock_positions_full(user)
    old_opts = await queries.get_option_positions_full(user)
    old_cash = await queries.get_stock_cash(user)

    print(f"user {user}")
    print(f"  current: {sum(1 for s in old_stocks if not s['closed'])} open stock, "
          f"{sum(1 for o in old_opts if not o['closed'])} open option, cash ${old_cash:,.2f}")
    print(f"  new portfolio ({len(rows)} positions):")
    for r in rows:
        print(f"    {r['shares']:g} {r['ticker']} @ ${r['price']:,.2f}")
    print(f"  cash -> {'$%,.2f' % cash if cash is not None else '(unchanged)'}")

    if not commit:
        print("\nDRY RUN — re-run with --apply to commit.")
        return

    # wipe existing trades (stocks by ticker, options by trade id) — all via queries
    for s in old_stocks:
        await queries.delete_stock_trades_for_ticker(user, s["ticker"])
    for t in await queries.get_option_trades(user):
        await queries.delete_option_trade(user, t["trade_id"])
    # reseed
    for r in rows:
        await queries.add_stock_trade(user, r["ticker"], "buy", r["shares"], r["price"],
                                      notes="bulk import")
    if cash is not None:
        await queries.set_stock_cash(user, cash)

    left = await queries.get_stock_holdings(user)
    left_opt = [o for o in await queries.get_option_positions_full(user) if not o["closed"]]
    assert len(left) == len(rows) and not left_opt, "post-import position count mismatch"
    print(f"\n✅ applied: {len(left)} open positions, cash ${await queries.get_stock_cash(user):,.2f}")


def selftest():
    ok = parse_csv("ticker,shares,price\nAAPL,3,163.74\n# note\nGOOGL, 7 , 150.85\n\n")
    assert ok == [{"ticker": "AAPL", "shares": 3.0, "price": 163.74},
                  {"ticker": "GOOGL", "shares": 7.0, "price": 150.85}], ok
    for bad in ["nope,1,2", "ticker,shares,price\nAAPL,-1,5", "ticker,shares,price\nAAPL,x,5",
                "ticker,shares,price\n"]:
        try:
            parse_csv(bad); assert False, f"should have raised: {bad!r}"
        except ValueError:
            pass
    print("selftest OK")


def main():
    ap = argparse.ArgumentParser(description="Bulk-set a user's stock portfolio from a CSV.")
    ap.add_argument("user", nargs="?", help="discord id or username")
    ap.add_argument("csv", nargs="?", help="path to CSV (ticker,shares,price)")
    ap.add_argument("--cash", type=float, default=None, help="set cash balance too")
    ap.add_argument("--apply", action="store_true", help="commit (default: dry run)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest(); return
    if not a.user or not a.csv:
        ap.error("user and csv are required")
    rows = parse_csv(Path(a.csv).read_text())

    async def run():
        uid = await resolve_user(a.user)
        await apply(uid, rows, a.cash, a.apply)
    asyncio.run(run())


if __name__ == "__main__":
    main()
