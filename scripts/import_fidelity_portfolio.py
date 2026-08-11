"""Re-sync a Fidelity portfolio snapshot into the SharpLab stock brokerage for a
single Discord user. Standalone (stdlib sqlite3 only) so it can run on the VPS
against the production DB without the repo's deps.

This is a FULL REPLACE: the snapshot is the authoritative truth of the real
account, so the user's existing stock_trades / option_trades / stock_cash are
deleted and rebuilt from the snapshot. Any manual trades made between snapshots
are already baked into the new snapshot — keeping them would double-count.

Source: Portfolio_Positions_Aug-10-2026.csv (Fidelity export). Equity rows become
`buy` trades at average cost basis (cost_basis_total / quantity); money-market
sweep balances become the brokerage cash balance. Duplicate ticker rows (same
holding across accounts or Cash+Margin lots) are consolidated by summing shares
and cost basis, so price stays the blended average cost.
"""
import sqlite3
import sys

USER = "695018847874318378"
TS = "2026-08-10T15:32:00+00:00"
NOTE = "Imported from Fidelity portfolio Aug-10-2026"
DB = sys.argv[1] if len(sys.argv) > 1 else "data/sharplab.db"

# (ticker, quantity, cost_basis_total) — price = cost_basis_total / quantity
EQUITIES = [
    # Individual - TOD
    ("HITI", 2, 8.66), ("ERBB", 1000, 11.50),
    # ROTH IRA (BRK-B/SNPS/SMR combine Cash+Margin lots; VOO/FBTC combine w/ HSA)
    ("VOO", 91.662, 48898.40), ("BRK-B", 46, 22168.51), ("MA", 4.015, 1374.36),
    ("MSFT", 4.039, 1505.36), ("GOOGL", 5.012, 356.60), ("EPD", 45.076, 776.20),
    ("TSM", 4.025, 612.67), ("FBTC", 65, 3892.26), ("RTX", 6.068, 606.00),
    ("TSEM", 5, 175.70), ("SNPS", 3, 1297.99), ("AMD", 2, 253.43),
    ("NVDA", 4.007, 47.95), ("JD", 25.327, 615.54), ("NVO", 15.023, 645.10),
    ("AAPL", 2.096, 349.89), ("IDXX", 1, 712.85), ("BABA", 4.534, 319.24),
    ("BIDU", 4, 314.92), ("TCEHY", 7, 298.78), ("RMBS", 4, 190.40),
    ("GE", 1.018, 113.68), ("AMZN", 1, 122.91), ("WDS", 10, 213.74),
    ("OKLO", 3, 73.05), ("MU", 0.1, 24.22), ("SMR", 10, 94.80),
    ("UBER", 1, 29.22), ("OXY", 1.015, 67.25), ("EXC", 1.026, 46.79),
    ("CMP", 1, 11.44), ("AMRC", 1, 10.04), ("LYFT", 1, 13.47),
    ("DEC", 1, 11.07), ("UA", 1, 5.95),
    # HSA (VOO/FBTC/BRK-B folded into ROTH lines above)
    ("VTI", 3.033, 980.15), ("VXUS", 11.953, 884.81), ("FETH", 25, 939.14),
]

# No option positions in the Aug-10 snapshot.
OPTIONS = []

# Money-market sweep -> cash (SPAXX Individual + FDRXX ROTH + FDRXX HSA)
CASH = round(1301.30 + 20.05 + 0.77, 2)


def main():
    con = sqlite3.connect(DB)
    cur = con.cursor()
    for tbl in ("stock_trades", "option_trades", "stock_cash"):
        cur.execute(f"DELETE FROM {tbl} WHERE discord_user=?", (USER,))

    for ticker, qty, cost_total in EQUITIES:
        price = round(cost_total / qty, 6)
        cur.execute(
            "INSERT INTO stock_trades (discord_user, ticker, side, shares, price, executed_at, notes) "
            "VALUES (?,?,?,?,?,?,?)",
            (USER, ticker, "buy", qty, price, TS, NOTE),
        )
    for u, ot, strike, exp, n, prem in OPTIONS:
        cur.execute(
            "INSERT INTO option_trades (discord_user, underlying, opt_type, strike, expiry, side, "
            "contracts, premium, executed_at, notes) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (USER, u, ot, strike, exp, "buy", n, prem, TS, NOTE),
        )
    cur.execute(
        "INSERT INTO stock_cash (discord_user, balance, updated_at) VALUES (?,?,?)",
        (USER, CASH, TS),
    )
    con.commit()
    print(f"Synced {len(EQUITIES)} stock trades, {len(OPTIONS)} option trade(s), cash=${CASH}")
    con.close()


if __name__ == "__main__":
    main()
