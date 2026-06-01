#!/usr/bin/env python3
"""Live price lookup — the source of truth, NOT training data.

    uv run python scripts/price.py HPE                 # share price (+ pre/post move)
    uv run python scripts/price.py HPE AMD NVDA        # several at once
    uv run python scripts/price.py HPE 65c 2026-06-12  # an option contract too

yfinance's regular `lastPrice` LAGS the extended session, so a stock that just
moved on earnings shows stale until the next regular tick. This prints both the
regular and the live pre/post-market print so you always see reality.
"""
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.cogs.stock import (
    _bs_price,
    _price_option_positions,
    effective_price,
    fetch_quotes,
)

_OPT = re.compile(r"^(\d+(?:\.\d+)?)([cp])$", re.I)  # e.g. 65c / 142.5p


def _money(x):
    return "—" if x is None else f"${x:,.2f}"


async def main(argv):
    if not argv:
        print("usage: price.py TICKER [TICKER ...] [<strike>c|p <expiry>]")
        return
    # Split tickers from an optional trailing option spec (<strike>c|p <YYYY-MM-DD>).
    opt = None
    if len(argv) >= 3 and _OPT.match(argv[-2]) and re.match(r"\d{4}-\d{2}-\d{2}", argv[-1]):
        m = _OPT.match(argv[-2])
        opt = {"strike": float(m.group(1)),
               "opt_type": "call" if m.group(2).lower() == "c" else "put",
               "expiry": argv[-1]}
        tickers = [t.upper() for t in argv[:-2]]
    else:
        tickers = [t.upper() for t in argv]

    quotes = await fetch_quotes(tickers, extended=True)
    for t in tickers:
        q = quotes.get(t)
        if not q:
            print(f"{t:<6} no quote")
            continue
        eff = effective_price(q)
        line = f"{t:<6} {_money(eff)}"
        prev = q.get("prev_close")
        if prev:
            line += f"  ({(eff - prev) / prev * 100:+.1f}% vs prev close {_money(prev)})"
        ext = q.get("extended")
        if ext and ext.get("price"):
            line += f"  [{ext['session']}-mkt {_money(ext['price'])} {ext.get('pct', 0):+.1f}%; regular {_money(q['price'])}]"
        print(line)

    if opt and tickers:
        und = tickers[0]
        pos = {"underlying": und, "net_contracts": 1, "avg_premium": 0.0,
               "realized_pnl": 0.0, "closed": False, **opt}
        pr = await _price_option_positions([pos])
        key = (und, opt["opt_type"], opt["strike"], opt["expiry"])
        info = pr.get(key, {})
        prem = info.get("premium")
        tag = " (Black-Scholes est — strike not listed)" if info.get("estimated") else \
              " (expired, intrinsic)" if info.get("expired") else ""
        print(f"{und} ${opt['strike']:g}{opt['opt_type'][0].upper()} {opt['expiry']}: "
              f"{_money(prem)}/contract{tag}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
