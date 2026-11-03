"""shared/kalshi_election.py: Kalshi's midterm markets ↔ the results feed's races, and the rows' status / called line.
Fixtures: Kalshi's open election events (public /events, 2026-11-03 03:00 ET, trimmed to the fields we read) and CNN's
2026 races files from the same morning. No network."""
import gzip
import json
from pathlib import Path

import pytest

from shared import kalshi_election as ke
from shared import kalshi_wall as kw
from wall.election import OFFICES, Cnn, balance

FX = Path(__file__).parent / "fixtures" / "wall" / "election"


def _cnn_2026():
    def fetch(url):
        name = url.rsplit("/", 1)[1].removesuffix(".json")
        if "/bop/" in url:
            raise FileNotFoundError(name)
        return json.loads(gzip.decompress((FX / f"cnn-{name}.json.gz").read_bytes()))
    return Cnn(2026, fetch).poll()["races"]


RACES = {r["id"]: r for r in _cnn_2026()}
EVENTS = json.loads(gzip.decompress((FX / "kalshi-2026-11-03-events.json.gz").read_bytes()))
MARKETS = {m["ticker"]: m for e in EVENTS for m in e["markets"]}
OFFICES_NOW = {o: balance(list(RACES.values()), o) for o in OFFICES}


def info(ticker):
    return ke.market_info(MARKETS[ticker])


@pytest.mark.parametrize("event, want", [
    ("SENATEGA-26", {"type": "race", "office": "S", "state": "GA", "district": None, "special": False, "year": 2026}),
    ("SENATEFLS-26", {"type": "race", "office": "S", "state": "FL", "district": None, "special": True, "year": 2026}),
    ("KXSENATELA-26NOV", {"type": "race", "office": "S", "state": "LA", "district": None, "special": False, "year": 2026}),
    ("KXAKSENATE-26NOV03", {"type": "race", "office": "S", "state": "AK", "district": None, "special": False, "year": 2026}),
    ("GOVPARTYGA-26", {"type": "race", "office": "G", "state": "GA", "district": None, "special": False, "year": 2026}),
    ("KXGOVCA-26", {"type": "race", "office": "G", "state": "CA", "district": None, "special": False, "year": 2026}),
    ("HOUSEPA7-26", {"type": "race", "office": "H", "state": "PA", "district": 7, "special": False, "year": 2026}),
    ("HOUSEAKAL-26", {"type": "race", "office": "H", "state": "AK", "district": 1, "special": False, "year": 2026}),
    ("KXHOUSERACE-PA06-26", {"type": "race", "office": "H", "state": "PA", "district": 6, "special": False, "year": 2026}),
    ("KXHOUSEUT01-26", {"type": "race", "office": "H", "state": "UT", "district": 1, "special": False, "year": 2026}),
    ("CONTROLS-2026", {"type": "control", "office": "S"}),
    ("CONTROLH-2026", {"type": "control", "office": "H"}),
    ("KXRHOUSEWON-26NOV03", {"type": "seats", "office": "H", "party": "R", "basis": "won"}),
    ("KXDSENATEWON-26NOV03", {"type": "seats", "office": "S", "party": "D", "basis": "won"}),
    ("RSENATESEATS-27", {"type": "seats", "office": "S", "party": "R", "basis": "held"}),
    ("KXDHOUSESEATS-27", {"type": "seats", "office": "H", "party": "D", "basis": "held"}),
    ("KXSENATESEATSD-27", {"type": "seats", "office": "S", "party": "D", "basis": "held"}),
])
def test_classify_real_tickers(event, want):
    assert event in {e["event_ticker"] for e in EVENTS}
    assert ke.classify(event) == want


@pytest.mark.parametrize("event", ["CONTROLS-2028", "CONTROLH-2028", "KXTXHOUSE-26", "KXLTGOVGA-26", "KXGOVTCUTS-26",
                                   "KXSENATEWITHIN1-26NOV03", "KXGASENGOVCOMBO-26NOV", "KXSENATESEATSD-29", "KXHOUSESEATSD-29",
                                   "KXIHOUSEWON-26NOV03", "KXNBAGAME-26NOV03MIALAL", ""])
def test_not_midterm_markets(event):
    assert ke.classify(event) is None


def test_every_2026_race_market_lands_on_a_cnn_race():
    """Every Senate / Governor / House market Kalshi lists for 2026 with a person CNN has on the ballot maps to that
    race, and the 2028 ones (other candidates) don't."""
    mapped, missed = 0, []
    for e in EVENTS:
        c = ke.classify(e["event_ticker"])
        if not c or c["type"] != "race":
            continue
        for m in e["markets"]:
            i = ke.market_info(m)
            r = ke.resolve(i["class"], i["pick"], RACES)
            if "(2028)" in e["title"]:
                assert r is None or ke.find_cand(r, i["pick"]), m["ticker"]
                continue
            if m.get("status") != "active" or (m.get("yes_bid_dollars") or "0") in ("0", "0.0000"):
                continue   # long shots / finalized primary losers: may not be on the ballot
            if r:
                mapped += 1
            else:
                missed.append(m["ticker"])
    assert mapped > 800
    assert len(missed) <= 0.02 * mapped, missed[:20]


@pytest.mark.parametrize("ticker, race, chip, label", [
    ("SENATEGA-26-D", "S-GA", "YES D", "GA Senate · Ossoff"),
    ("SENATEOHS-26-D", "S-OH-special", "YES D", "OH Senate (sp.) · Brown"),
    ("SENATEFLS-26-R", "S-FL-special", "YES R", "FL Senate (sp.) · Moody"),
    ("SENATENE-26-DOSB", "S-NE", "YES I", "NE Senate · Osborn"),
    ("KXAKSENATE-26NOV03-DSUL", "S-AK", "YES R", "AK Senate · Sullivan"),
    ("KXGOVCA-26-XBEC", "G-CA", "YES D", "CA Governor · Becerra"),
    ("GOVPARTYNH-28-R", "G-NH", "YES R", "NH Governor · Ayotte"),   # Kalshi's "-28" is the 2026 race (Ayotte is on it)
    ("GOVPARTYKS-27-D", "G-KS", "YES D", "KS Governor · Holscher"),
    ("HOUSEPA7-26-D", "H-PA-7", "YES D", "PA-7 House · Brooks"),
    ("HOUSEAKAL-26-R", "H-AK-1", "YES R", "AK-1 House · III"),
    ("KXHOUSERACE-PA06-26-D", "H-PA-6", "YES D", "PA-6 House · Houlahan"),
    ("CONTROLH-2026-D", "CTRL-H", "YES D", "House control · Dem"),
    ("KXRHOUSEWON-26NOV03-AL194", "SEATS-H", "YES R", "Rep House seats won · At least 194"),
])
def test_market_to_race(ticker, race, chip, label):
    i = info(ticker)
    st = ke.status(i, "YES", RACES, OFFICES_NOW)
    assert (st["race"], st["chip"]) == (race, chip)
    assert i["label"] == label or ticker == "HOUSEAKAL-26-R"


def test_alaska_two_sullivans():
    r = RACES["S-AK"]
    dan = ke.find_cand(r, ke.pick(MARKETS["KXAKSENATE-26NOV03-DSUL"]))
    assert dan["party"] == "R"


def test_2028_senate_market_is_not_tonights_race():
    """Oregon has a Senate race tonight too; Kalshi's 2028 one (party only, "-28") must not land on it."""
    i = info("SENATEOR-28-D")
    assert i["class"]["year"] == 2028 and ke.status(i, "YES", RACES, OFFICES_NOW)["race"] == ""
    assert ke.status(info("SENATEOR-26-D"), "YES", RACES, OFFICES_NOW)["race"] == "S-OR"


# ---- status + the called line --------------------------------------------------------------------------------------------
def _count(rid, winner=None, pct=60.0, lead_share=0.54):
    r = json.loads(json.dumps(RACES[rid]))
    a, b = r["cands"][0], r["cands"][1]
    if winner:
        a, b = next(c for c in r["cands"] if c["name"] == winner), next(c for c in r["cands"] if c["name"] != winner)
    a["votes"], b["votes"] = int(1e6 * lead_share), int(1e6 * (1 - lead_share))
    a["pct"], b["pct"] = 100 * lead_share, 100 * (1 - lead_share)
    r["cands"] = [a, b] + [c for c in r["cands"] if c is not a and c is not b]
    r.update(votes=1_000_000, pct=pct, leader=a["party"], margin=round(100 * (2 * lead_share - 1), 1))
    if winner:
        a["winner"] = True
        r.update(called=a["party"], caller="CNN")
    return r


def test_status_counting_then_called():
    i = info("SENATEGA-26-D")
    assert ke.status(i, "YES", RACES, OFFICES_NOW)["text"] == "no votes yet"
    counting = {**RACES, "S-GA": _count("S-GA", pct=34)}
    st = ke.status(i, "YES", counting, OFFICES_NOW)
    assert st["text"].startswith("34% in · ") and st["won"] is None
    called = {**RACES, "S-GA": _count("S-GA", winner="Ossoff", pct=93)}
    assert ke.status(i, "YES", called, OFFICES_NOW)["won"] is True
    assert ke.status(i, "NO", called, OFFICES_NOW)["won"] is False
    lost = {**RACES, "S-GA": _count("S-GA", winner="Collins", pct=93)}
    assert ke.status(i, "YES", lost, OFFICES_NOW)["won"] is False


def test_called_line_and_enrich_order():
    b = kw.Book()
    for t, px in (("SENATEGA-26-D", "0.9800"), ("SENATEME-26-R", "0.6000")):
        b.see_market(100, {**MARKETS[t], "yes_bid_dollars": px, "yes_ask_dollars": px})
        ke.see(b, MARKETS[t])
    b.members["h"] = {"name": "Hansen", "exact": False}
    b.see_positions(100, "h", {"SENATEGA-26-D": ("YES", 400, 280.0), "SENATEME-26-R": ("YES", 10, 6.0)})
    rows = ke.election_rows(b, 101, {"h"})
    races = {**RACES, "S-GA": _count("S-GA", winner="Ossoff", pct=93)}
    out = ke.enrich(rows, races, OFFICES_NOW)
    assert out[0]["highlight"] == "✓ CALLED · Hansen wins +40%"     # 70¢ → 98¢, called first
    assert out[1]["highlight"] == "" and out[1]["chip"] == "YES R"
    assert out[0]["size"] == "250+ ct" and "pnl" not in out[0]


def test_control_and_seat_counts():
    o = json.loads(json.dumps(OFFICES_NOW))
    o["H"]["winner"] = "D"
    assert ke.status(info("CONTROLH-2026-D"), "YES", RACES, o)["won"] is True
    assert ke.status(info("CONTROLH-2026-R"), "YES", RACES, o)["won"] is False
    o["H"]["seats"]["R"]["won"] = 195     # "at least 194" won by media calls: clinched mid-count
    assert ke.status(info("KXRHOUSEWON-26NOV03-AL194"), "NO", RACES, o)["won"] is False
    assert ke.status(info("KXRHOUSEWON-26NOV03-AL198"), "YES", RACES, o)["won"] is None


def test_unmapped_tickers_are_not_election():
    assert ke.is_election("SENATEGA-26-D") and ke.is_election("KXHOUSERACE-PA07-26-D") and ke.is_election("CONTROLS-2026-D")
    assert not ke.is_election("KXNBASPREAD-26NOV02MIALAL-LAL3") and not ke.is_election("KXTXHOUSE-26-R")


def test_market_filed_under_the_wrong_state_finds_its_race_by_full_name():
    # live 2026-11-03: Kalshi's SENATELA-26 lists Kentucky's candidates (Barr, Booker); KXSENATELA-26NOV is Louisiana's
    assert ke.status(info("SENATELA-26-R"), "YES", RACES, OFFICES_NOW)["race"] == "S-KY"
    assert ke.status(info("KXSENATELA-26NOV-R"), "YES", RACES, OFFICES_NOW)["race"] == "S-LA"


def test_county_markets_file_under_the_race_but_never_settle_from_it():
    t = "KXGENERALCOUNTY-GOVPARTYCA-26-RIVE"
    assert ke.is_election(t) and ke.classify(ke.event_of(t))["type"] == "county"
    info = ke.market_info({"ticker": t, "event_ticker": "KXGENERALCOUNTY-GOVPARTYCA-26",
                           "title": "Will Steve Hilton win Riverside County?", "yes_sub_title": "Riverside"})
    assert info["label"] == "CA Governor · Riverside Co. · Hilton"
    st = ke.status(info, "NO", {"G-CA": {"id": "G-CA", "called": "D"}}, {})
    assert st["race"] == "G-CA" and st["won"] is None and st["chip"] == "NO Hilton (county)"
    assert not ke.is_election("KXGENERALCOUNTY-FOO-26-X")
