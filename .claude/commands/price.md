---
description: Look up a live stock/option price from the API — never guess from training data
---

# /price — live price lookup

**Never state a stock or option price from memory.** Your training data is stale and
will be wrong (a stock can move 30%+ on earnings the day you're asked). SharpLab pulls
live quotes from yfinance — use them.

## Usage

```bash
uv run python scripts/price.py HPE                 # one ticker
uv run python scripts/price.py HPE AMD NVDA        # several
uv run python scripts/price.py HPE 65c 2026-06-12  # + an option contract
uv run python scripts/price.py HPE 142.5p 2026-07-17
```

Output shows the **effective price** (the pre/post-market print when one is live,
e.g. an earnings move after the close), the % vs prev close, and — when an extended
session is active — both the extended and the regular-session price:

```
HPE    $59.93  (+39.3% vs prev close $43.04)  [post-mkt $59.93 +27.5%; regular $47.00]
HPE $65C 2026-06-12: $6.04/contract (Black-Scholes est — strike not listed)
```

## Why the regular price can look wrong

yfinance's `fast_info.last_price` is the **regular-session** price and LAGS the
extended session. After an earnings print the stock can be up 30% in post-market
while `last_price` still shows the pre-earnings close. The codebase handles this with
`effective_price(q)` in `bot/cogs/stock.py` — it prefers the live pre/post-market
print. Option spots and HQ stock valuation both use it, so a post-earnings move flows
through to option Black-Scholes pricing.

## Notes

- Strikes yfinance doesn't list (it caps the ladder) are priced via **Black-Scholes**
  using the nearest listed strike's implied vol — flagged `est` in output.
- Run it **on the VPS** for the exact number a user sees in Discord if local and prod
  ever disagree: `ssh root@87.99.136.82 "cd /opt/sharplab && venv/bin/python scripts/price.py HPE"`.
- This is the source of truth. If a user says a price and you doubt it, run this before
  replying — do not argue from memory.
