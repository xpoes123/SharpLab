"""Election Pulse: results × % in → projections, priced against Kalshi count markets (shared/election_pulse.py)."""
from shared import election_pulse as ep
from wall import election as we


def _race(office, st, dist, pct, votes, d, r, names=("Smith", "Jones")):
    cs = [we.cand(names[0], names[0], "DEM", d, 0, False, False), we.cand(names[1], names[1], "REP", r, 0, False, False)]
    return we.race(office, st, dist, False, cs, pct=pct, votes=votes, called="", caller="CNN", flip=False, watch=False)


def _m(ticker, bid, ask, lo=None, hi=None, kind="greater", title="", event=""):
    return {"ticker": ticker, "event_ticker": event, "yes_bid_dollars": str(bid / 100), "yes_ask_dollars": str(ask / 100),
            "strike_type": kind, "floor_strike": lo, "cap_strike": hi, "title": title, "volume_fp": "10"}


def test_projection_and_ladder():
    races = [_race("H", "NJ", 7, 50, 150_000, 80_000, 70_000), _race("S", "NJ", None, 2, 1000, 600, 400)]
    mk = {ep.TURNOUT: [_m("KXMIDTERMVOTETURN-NJ07-250000", 40, 45, lo=250_000), _m("KXMIDTERMVOTETURN-NJ07-350000", 40, 45, lo=350_000),
                       _m("KXMIDTERMVOTETURN-NJSEN-3000000", 50, 55, lo=3_000_000)],
          ep.SHARE: [_m("KXVOTEGENERAL-SENATENJ-26SMI-50", 30, 35, lo=50, kind="greater_or_equal", event="KXVOTEGENERAL-SENATENJ-26SMI",
                        title="Will Smith receive at least 50% of the popular vote?")]}
    rows = {r["id"]: r for r in ep.race_rows(races, mk)}
    nj7 = rows["H-NJ-7"]
    assert nj7["proj"] == 300_000
    lo, hi = nj7["turnout"]
    assert lo["model"] > 85 and lo["edge"] > ep.EDGE and lo["hot"]       # 300K projected, "above 250K" at 42¢ → cheap
    assert hi["model"] < 15 and hi["edge"] < -ep.EDGE
    sen = rows["S-NJ"]
    assert sen["proj"] is None and sen["turnout"][0]["model"] is None    # 2% in: no projection
    assert sen["shares"][0]["name"] == "Smith" and sen["shares"][0]["model"] is None


def test_national_house_and_margin_buckets(monkeypatch):
    monkeypatch.setattr(ep, "NATIONAL_MIN", 4)
    races = [_race("H", "PA", i, 100, 300_000, 160_000, 140_000) for i in range(1, 5)]
    mk = {ep.HOUSE_TURNOUT: [_m("T-A", 10, 20, lo=0, hi=1_199_999, kind="between"), _m("T-B", 10, 20, lo=1_200_000)],
          ep.HOUSE_MARGIN: [{**_m("M-R", 5, 6, lo=0, hi=0, kind="between"), "yes_sub_title": "Republicans win"},
                            {**_m("M-B", 5, 6, lo=6, hi=8, kind="between"), "yes_sub_title": "Democrats, 6 to 8%"}]}
    h = ep.national_house(races, mk)
    assert h["proj"] == 1_200_000 and h["reporting"] == 4
    assert abs(h["margin"] - 6.67) < 0.01
    by = {x["ticker"]: x for x in h["turnout"] + h["margin_mkts"]}
    assert by["M-B"]["model"] > by["M-R"]["model"]


def test_share_race_parsing():
    assert ep.share_race("KXVOTEGENERAL-KXHOUSERACE-MI05-26CVUK")["district"] == 5
    assert ep.share_race("KXVOTEGENERAL-SENATENE26DOSB")["state"] == "NE"
    assert ep.turnout_race_id("AKAL") == ["H-AK-1", "H-AK"]
    assert ep.turnout_race_id("GASEN")[0] == "S-GA"


def test_wall_status_line_carries_projection():
    from shared import election_markets as em
    assert em.status_line(_race("H", "NJ", 7, 50, 150_000, 80_000, 70_000)).endswith("~300K votes projected")
    assert "projected" not in em.status_line(_race("H", "NJ", 7, 2, 1000, 600, 400))


def test_national_waits_for_enough_districts():
    races = [_race("H", "IN", i, 30, 70_000, 40_000, 30_000) for i in range(1, 5)]
    h = ep.national_house(races, {})
    assert h["reporting"] == 4 and h["proj"] is None and h["margin"] is None
