"""Showdown: the Python evaluator vs the phevaluator fixture, rule edge cases, the daily deal/grade
logic, the web API, and the JS copy (web/static/showdown/eval.js) vs the same fixture."""
import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from collections import Counter
from itertools import combinations
from pathlib import Path

import pytest

from db import queries as q
from db import schema as sch
from shared import showdown as sd
from shared import showdown_eval as se
from web import g_showdown as web_sd

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "tests/fixtures/showdown.json").read_text())


def C(s):
    return s.split()


def test_fixture_agrees_in_every_variant():
    seen = Counter()
    for d in FIXTURE["deals"]:
        got = se.showdown(d["variant"], d["hands"], d["board"])
        assert got["high"] == d["high"], d
        assert [e["cat"] for e in got["highs"]] == d["cats"], d
        if se.VARIANTS[d["variant"]]["hilo"]:
            assert got["low"] == d["low"], d
            assert [l and l["ranks"] for l in got["lows"]] == d["lows"], d
        seen[d["variant"]] += 1
    assert set(seen) == set(se.VARIANTS) and min(seen.values()) >= 200


def test_every_short_deck_hand():
    cats, scores = [0] * 9, set()
    for five in combinations(se.deck_for("short"), 5):
        score, cat, _ = se.eval5(five, True)
        cats[cat] += 1
        scores.add(score)
    assert cats == [122400, 193536, 36288, 16128, 6120, 480, 1728, 288, 24]
    assert len(scores) == 1404


@pytest.mark.skipif(not os.environ.get("SHOWDOWN_EXHAUSTIVE"), reason="~10 s; SHOWDOWN_EXHAUSTIVE=1 (the JS test always runs it)")
def test_every_five_card_hand():
    cats, scores = [0] * 9, set()
    for five in combinations(se.deck_for("nlh"), 5):
        score, cat, _ = se.eval5(five)
        cats[cat] += 1
        scores.add(score)
    assert cats == [1302540, 1098240, 123552, 54912, 10200, 5108, 3744, 624, 40]
    assert len(scores) == 7462


def test_omaha_and_holdem_rules():
    assert se.best_high("plo4", C("As Ks Qs Js"), C("2s 7s 9d Tc 3h"))["cat"] != 5      # four-flush in hand
    assert se.best_high("plo4", C("Qs Jh Th 3c"), C("As Ks 9s 4s 2d"))["cat"] != 5      # one-card flush
    assert se.best_high("plo4", C("2c 3d 4s 5c"), C("Ah Kh Qh Jh Th"))["cat"] == 0      # board royal doesn't play
    assert se.best_high("plo4", C("6c 7d 8h Kc"), C("9s Ts 2d 2c Ah"))["cat"] == 1      # needs 3 hole cards
    assert se.showdown("nlh", [C("2c 3d"), C("4s 5c")], C("Ah Kh Qh Jh Th"))["high"] == [0, 1]
    board = C("9s 9d 9h 4c 4d")
    assert se.showdown("plo4", [C("9c Kd 2h 3h"), C("4h 4s 2c 3c")], board)["high"] == [0]


def test_short_deck_and_hilo():
    assert se.eval5(C("Ah 9h 8h 7h Jh"), True)[0] > se.eval5(C("Ks Kd Kc 6s 6d"), True)[0]
    assert se.eval5(C("7s 7d 7c Ks Qd"), True)[0] > se.eval5(C("Ts Jd Qc Ks Ad"), True)[0]
    assert se.eval5(C("As 6d 7c 8s 9d"), True)[1] == 4
    assert se.best_low("o8", C("As 2s Kd Qc"), C("Ah 2d 5c 7s Kh")) is None           # counterfeited
    assert se.best_low("o8", C("As 3s Kd Qc"), C("Ah 2d 5c 7s Kh"))["ranks"] == [7, 5, 3, 2, 1]


def test_daily_deals_are_stable_ramped_and_gradeable():
    for v in se.VARIANTS:
        n = sd.DEFAULT_HANDS[v]
        a, b = sd.daily_deals("2026-11-26", v, n), sd.daily_deals("2026-11-26", v, n)
        assert a == b and a != sd.daily_deals("2026-11-27", v, n)
        assert [d["level"] for d in a] == [0] * 4 + [1] * 3 + [2] * 3
        for d in a:
            cards = [c for h in d["hands"] for c in h] + d["board"]
            assert len(set(cards)) == len(cards) == n * se.VARIANTS[v]["hole"] + 5
            res = se.showdown(v, d["hands"], d["board"])
            if d["level"] < 2:
                assert len(res["high"]) == 1
        right = sd.grade(v, a, [sd.answers(v, d) for d in a])
        assert all(right)
        assert sd.grade(v, a, [["s9"]] * len(a)) == [False] * len(a)
        assert sd.score_ms(5000, [True, False, False]) == 25000


def test_js_copy_matches_fixture_and_rebuilds_daily_answers():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    deals = {v: sd.daily_deals("2026-11-26", v, sd.DEFAULT_HANDS[v]) for v in se.VARIANTS}
    answers = {v: [sd.answers(v, d) for d in ds] for v, ds in deals.items()}
    script = """
import { showdown } from "%s/web/static/showdown/eval.js";
import { fromCards } from "%s/web/static/showdown/deals.js";
import { readFileSync } from "node:fs";
const fx = JSON.parse(readFileSync(process.argv[2], "utf8"));
const daily = JSON.parse(readFileSync(process.argv[3], "utf8"));
let bad = 0;
for (const d of fx.deals) { const s = showdown(d.variant, d.hands, d.board); if (JSON.stringify(s.high) !== JSON.stringify(d.high)) bad++; }
for (const [v, ds] of Object.entries(daily.deals)) ds.forEach((d, i) => {
  const b = fromCards(v, d.hands, d.board, d.level);
  if (JSON.stringify(b.steps.map((s) => s.answer)) !== JSON.stringify(daily.answers[v][i])) bad++;
});
console.log(bad);
""" % (ROOT, ROOT)
    tmp = Path(tempfile.mkdtemp())
    (tmp / "t.mjs").write_text(script)
    (tmp / "daily.json").write_text(json.dumps({"deals": deals, "answers": answers}))
    out = subprocess.run([node, str(tmp / "t.mjs"), str(ROOT / "tests/fixtures/showdown.json"), str(tmp / "daily.json")],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "0"


class _Req:
    cookies: dict = {}
    headers: dict = {}


def test_web_start_submit_leaderboard(monkeypatch):
    sch.DB_PATH = q.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")

    async def go():
        await sch.init_db()
        monkeypatch.setattr(web_sd.auth, "read_session", lambda r: {"id": "p1"})
        monkeypatch.setattr(web_sd.daily, "puzzle_day", lambda: "2026-11-26")
        bad = await web_sd.start(_Req(), web_sd.StartBody(variant="plo6", hands=9))
        assert bad.status_code == 400                                    # PLO6 seats at most 7
        s = await web_sd.start(_Req(), web_sd.StartBody(variant="o8", hands=4))
        assert len(s["deals"]) == sd.DAILY_DEALS and s["token"]
        assert "answers" not in json.dumps(s)
        picks = [sd.answers("o8", d) for d in s["deals"]]
        picks[0] = ["s3", "nolow"]
        if picks[0] == sd.answers("o8", s["deals"][0]):
            picks[0] = ["s2", "nolow"]
        res = await web_sd.submit(_Req(), web_sd.SubmitBody(token=s["token"], picks=picks))
        assert res["wrong"] == 1 and res["right"][1:] == [True] * 9 and res["rank"] == 1
        assert res["score_ms"] == res["elapsed_ms"] + sd.PENALTY_MS
        again = await web_sd.submit(_Req(), web_sd.SubmitBody(token=s["token"], picks=picks))
        assert again.status_code == 409
        restart = await web_sd.start(_Req(), web_sd.StartBody(variant="o8", hands=4))
        assert restart.status_code == 409
        t = await web_sd.today(_Req(), variant="o8", hands=4)
        assert t["played"] and t["your_result"]["wrong"] == 1
        lb = await web_sd.leaderboard(_Req(), variant="o8", hands=4)
        assert lb["top"][0]["me"] and lb["top"][0]["wrong"] == 1
        other = await web_sd.leaderboard(_Req(), variant="o8", hands=5)
        assert other["top"] == []

    asyncio.run(go())
