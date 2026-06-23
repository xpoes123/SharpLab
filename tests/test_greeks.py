"""Black-Scholes Greeks sanity checks (pure math, no network)."""
from bot.cogs.stock import _bs_greeks, _bs_price, _implied_vol, option_greeks


def test_implied_vol_roundtrips():
    # price an option at a known vol, then recover that vol from the price.
    px = _bs_price("call", 100, 100, 1.0, 0.045, 0.30)
    iv = _implied_vol("call", px, 100, 100, 1.0, 0.045)
    assert iv is not None and abs(iv - 0.30) < 0.01


def test_implied_vol_falls_back_in_option_greeks():
    # deep-ITM call near intrinsic with NO chain IV → greeks still computed via solver.
    prem = _bs_price("call", 550, 70, 1.5, 0.045, 0.35)
    pos = {"opt_type": "call", "strike": 70, "expiry": "2099-01-01", "net_contracts": 1}
    g = option_greeks(pos, {"spot": 550, "iv": None, "premium": prem})
    assert g and g["delta"] > 0.9   # deep ITM → delta near 1


def test_atm_call_textbook():
    # S=K=100, T=1y, r=5%, vol=20% — standard textbook case.
    g = _bs_greeks("call", 100, 100, 1.0, 0.05, 0.20)
    assert abs(g["delta"] - 0.6368) < 0.005          # known N(d1)
    assert g["gamma"] > 0
    assert g["vega"] > 0
    assert g["theta"] < 0                             # long option bleeds time value


def test_put_call_delta_parity():
    c = _bs_greeks("call", 100, 100, 1.0, 0.05, 0.20)
    p = _bs_greeks("put", 100, 100, 1.0, 0.05, 0.20)
    assert abs((c["delta"] - p["delta"]) - 1.0) < 1e-9   # Δc − Δp = 1
    assert abs(c["gamma"] - p["gamma"]) < 1e-9           # gamma identical


def test_degenerate_inputs_return_none():
    assert _bs_greeks("call", 100, 100, 0, 0.05, 0.20) is None   # expired
    assert _bs_greeks("call", 100, 100, 1.0, 0.05, 0) is None    # no vol


def test_option_greeks_scaling_and_guards():
    pos = {"opt_type": "call", "strike": 100, "expiry": "2099-01-01", "net_contracts": 3}
    assert option_greeks(pos, {"spot": None, "iv": 0.2}) is None  # no spot
    assert option_greeks(pos, {"spot": 100, "iv": None}) is None  # no IV
    g = option_greeks(pos, {"spot": 100, "iv": 0.2})
    assert g and 0 < g["delta"] < 1 and g["dte"] > 0
    # theta is scaled to one contract (×100 shares) → bigger magnitude than per-share
    per_share = _bs_greeks("call", 100, 100, g["dte"] / 365.0, 0.045, 0.2)["theta"]
    assert abs(g["theta"] - per_share * 100) < 1e-6


if __name__ == "__main__":
    for fn in [test_atm_call_textbook, test_put_call_delta_parity,
               test_degenerate_inputs_return_none, test_option_greeks_scaling_and_guards,
               test_implied_vol_roundtrips, test_implied_vol_falls_back_in_option_greeks]:
        fn()
    print("greeks checks OK")
