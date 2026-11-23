#!/usr/bin/env python3
"""Bulk-set a user's stock portfolio from a CSV. The admin path for "redo my
stocks": wipes the user's existing stock + option trades and reseeds one buy per
CSV row, then (optionally) sets cash. Dry-run by default; pass --apply to commit.

    # preview
    ./venv/bin/python scripts/bulk_stock.py harsha port.csv --cash 20000
    # commit
    ./venv/bin/python scripts/bulk_stock.py harsha port.csv --cash 20000 --apply

CSV (header required), price = your average cost basis. Optional `account`
column tags each row with a sub-account (Roth IRA, HSA, 401k, Brokerage, …);
the same ticker may appear once per account:

    ticker,shares,price,account
    AAPL,3,163.74,Roth IRA
    AAPL,1,170.00,Brokerage

A raw Fidelity "Portfolio_Positions_*.csv" download also works as-is (see
parse_fidelity): positions keep their account, money-market sweeps become cash.

Self-test: ./venv/bin/python scripts/bulk_stock.py --selftest

ponytail: stock-only, full-replace. No option rows, no per-lot edits — add a
`type` column + option handling when someone actually needs it.
"""
import argparse
import asyncio
import csv
import io
import re
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
        rows.append({"ticker": r["ticker"].upper(), "shares": shares, "price": price,
                     "account": r.get("account") or None})
    if not rows:
        raise ValueError("no data rows found")
    return rows


# Fidelity account names -> the sub-account names the brokerage already uses.
FIDELITY_ACCOUNTS = {"individual - tod": "Brokerage", "individual": "Brokerage",
                     "roth ira": "Roth IRA", "health savings account": "HSA",
                     "traditional ira": "Traditional IRA", "rollover ira": "Traditional IRA"}
# Fidelity drops the class dash Yahoo needs (BRKB -> BRK-B).
FIDELITY_SYMBOLS = {"BRKB": "BRK-B", "BRKA": "BRK-A", "BFB": "BF-B", "BFA": "BF-A"}
_OPT_RE = re.compile(r"^-([A-Z.]+)(\d{2})(\d{2})(\d{2})([CP])([\d.]+)$")


def _money(v: str) -> float | None:
    v = (v or "").replace("$", "").replace(",", "").replace("+", "").strip()
    try:
        return float(v)
    except ValueError:
        return None


def parse_fidelity(text: str) -> tuple[list[dict], list[dict], float]:
    """Parse a raw Fidelity Portfolio_Positions export -> (stock rows, option rows, cash).
    Lots of the same ticker in the same account (Cash+Margin) merge at blended cost;
    money-market sweeps (SPAXX**, FDRXX**) sum into cash; "Pending activity" is skipped."""
    reader = csv.DictReader(io.StringIO(text))
    stocks: dict[tuple, dict] = {}
    options, cash = [], 0.0
    for raw in reader:
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        sym = r.get("symbol", "").upper().replace(" ", "")
        if not sym or not r.get("account name"):
            continue   # the disclaimer footer
        acct = FIDELITY_ACCOUNTS.get(r["account name"].lower(), r["account name"])
        if sym.endswith("**"):
            cash += _money(r.get("current value", "")) or 0.0
            continue
        qty, cost = _money(r.get("quantity", "")), _money(r.get("cost basis total", ""))
        if not qty or not cost or cost <= 0:
            continue   # pending activity, or a lot with no basis
        m = _OPT_RE.match(sym)
        if m:
            u, yy, mm, dd, cp, strike = m.groups()
            options.append({"underlying": u, "opt_type": "call" if cp == "C" else "put",
                            "strike": float(strike), "expiry": f"20{yy}-{mm}-{dd}",
                            "side": "buy" if qty > 0 else "sell", "contracts": int(abs(qty)),
                            "premium": round(cost / (abs(qty) * 100), 4), "account": acct})
            continue
        sym = FIDELITY_SYMBOLS.get(sym, sym)
        lot = stocks.setdefault((sym, acct), {"ticker": sym, "shares": 0.0, "cost": 0.0, "account": acct})
        lot["shares"] += qty
        lot["cost"] += cost
    rows = [{"ticker": l["ticker"], "shares": round(l["shares"], 6),
             "price": round(l["cost"] / l["shares"], 6), "account": l["account"]}
            for l in stocks.values() if l["shares"] > 0]
    if not rows and not options:
        raise ValueError("no positions found in that Fidelity export")
    return rows, options, round(cash, 2)


def parse_portfolio(text: str) -> tuple[list[dict], list[dict], float | None]:
    """Either format -> (stock rows, option rows, cash or None). Sniffs the header."""
    first = text.lstrip("\ufeff").split("\n", 1)[0].lower()
    if "symbol" in first and "cost basis total" in first:
        return parse_fidelity(text.lstrip("\ufeff"))
    return parse_csv(text), [], None


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


async def apply_portfolio(user: str, rows: list[dict], cash: float | None,
                          options: list[dict] = ()) -> dict:
    """Wipe the user's stock+option trades and reseed one buy per row (plus any
    option rows); optionally set cash. Returns {positions, options, cash}.
    Shared by the CLI, /stock import and the HQ upload page."""
    for s in await queries.get_stock_positions_full(user):
        await queries.delete_stock_trades_for_ticker(user, s["ticker"])
    for t in await queries.get_option_trades(user):
        await queries.delete_option_trade(user, t["trade_id"])
    for r in rows:
        await queries.add_stock_trade(user, r["ticker"], "buy", r["shares"], r["price"],
                                      notes="bulk import", account=r.get("account"))
    for o in options:
        await queries.add_option_trade(user, o["underlying"], o["opt_type"], o["strike"], o["expiry"],
                                       o["side"], o["contracts"], o["premium"], notes="bulk import")
    if cash is not None:
        await queries.set_stock_cash(user, cash)
    left = await queries.get_stock_holdings(user)
    left_opt = [o for o in await queries.get_option_positions_full(user) if not o["closed"]]
    assert len(left) == len({r["ticker"] for r in rows}) and len(left_opt) == len(options), \
        "post-import position count mismatch"
    return {"positions": len(left), "options": len(left_opt), "cash": await queries.get_stock_cash(user)}


async def apply(user: str, rows: list[dict], cash: float | None, commit: bool, options=()):
    old_stocks = await queries.get_stock_positions_full(user)
    old_opts = await queries.get_option_positions_full(user)
    old_cash = await queries.get_stock_cash(user)

    print(f"user {user}")
    print(f"  current: {sum(1 for s in old_stocks if not s['closed'])} open stock, "
          f"{sum(1 for o in old_opts if not o['closed'])} open option, cash ${old_cash:,.2f}")
    print(f"  new portfolio ({len(rows)} positions):")
    for r in rows:
        print(f"    {r['shares']:g} {r['ticker']} @ ${r['price']:,.2f}"
              + (f"  [{r['account']}]" if r.get("account") else ""))
    print(f"  cash -> {f'${cash:,.2f}' if cash is not None else '(unchanged)'}")

    if not commit:
        print("\nDRY RUN — re-run with --apply to commit.")
        return
    res = await apply_portfolio(user, rows, cash, options)
    print(f"\n✅ applied: {res['positions']} open positions, cash ${res['cash']:,.2f}")


def selftest():
    ok = parse_csv("ticker,shares,price\nAAPL,3,163.74\n# note\nGOOGL, 7 , 150.85\n\n")
    assert ok == [{"ticker": "AAPL", "shares": 3.0, "price": 163.74, "account": None},
                  {"ticker": "GOOGL", "shares": 7.0, "price": 150.85, "account": None}], ok
    acct = parse_csv("ticker,shares,price,account\nVOO,1,500,Roth IRA\nVOO,2,510,HSA\n")
    assert [r["account"] for r in acct] == ["Roth IRA", "HSA"], acct
    for bad in ["nope,1,2", "ticker,shares,price\nAAPL,-1,5", "ticker,shares,price\nAAPL,x,5",
                "ticker,shares,price\n"]:
        try:
            parse_csv(bad); assert False, f"should have raised: {bad!r}"
        except ValueError:
            pass
    fid = ("\ufeffAccount number,Account name,Symbol,Description,Quantity,Current value,Cost basis total,Type\n"
           "X1,Individual - TOD,SPAXX**,MM,,$682.95,,Cash,\n"
           "X1,Individual - TOD,Pending activity,,,$37.48,,,\n"
           "2,ROTH IRA,BRKB,BRK,20,$1.00,$9000.00,Cash,\n"
           "2,ROTH IRA,BRKB,BRK,2,$1.00,$1461.59,Margin,\n"
           "2,ROTH IRA, -WDS260717C25,WDS CALL,3,$75.00,$566.01,Cash,\n"
           "3,Health Savings Account,FDRXX**,MM,,$38.35,,Cash,\n"
           "3,Health Savings Account,VOO,VOO,2,$1.00,$1000.00,Cash,\n"
           '\n"Date downloaded Nov-23-2026 2:05 p.m ET"\n')
    rows, opts, cash = parse_portfolio(fid)
    assert cash == 721.30, cash
    assert rows == [{"ticker": "BRK-B", "shares": 22.0, "price": round(10461.59 / 22, 6), "account": "Roth IRA"},
                    {"ticker": "VOO", "shares": 2.0, "price": 500.0, "account": "HSA"}], rows
    assert opts == [{"underlying": "WDS", "opt_type": "call", "strike": 25.0, "expiry": "2026-07-17",
                     "side": "buy", "contracts": 3, "premium": 1.8867, "account": "Roth IRA"}], opts
    assert parse_portfolio("ticker,shares,price\nAAPL,1,2\n")[2] is None
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
    rows, options, cash = parse_portfolio(Path(a.csv).read_text())
    if options:
        print(f"note: {len(options)} option position(s) will be imported too")

    async def run():
        uid = await resolve_user(a.user)
        await apply(uid, rows, a.cash if a.cash is not None else cash, a.apply, options)
    asyncio.run(run())


if __name__ == "__main__":
    main()
