"""The overlay's spoiler delay (wall/qml/board.js, run under node): the scoreboard and ticker are drawn as of
(now − the game's tile delay) — a score seen at t never shows before t + D — and the bets' formatting for the L layout."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

BOARD = Path(__file__).resolve().parent.parent / "wall" / "qml" / "board.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def js(expr: str):
    src = BOARD.read_text().replace(".pragma library", "")
    prog = f"const B = (function () {{ {src}; return {{ delayed, trim, betLine, betRows, betTicker, tileBets, view }}; }})();\n" \
           f"process.stdout.write(JSON.stringify((function () {{ {expr} }})()));"
    r = subprocess.run(["node", "-e", prog], capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


SNAP = """
const g = (id, a, h, last) => ({id, state: "in", away: {abbr: "PHX", score: a}, home: {abbr: "GS", score: h}, last, detail: "Q4"});
const snap = (a, h, a2, h2) => ({shown: "nba", updated: 0, data: {kind: "scores", title: "NBA", games: [g("1", a, h, "play " + a), g("2", a2, h2, "x")]}});
"""


def test_a_score_seen_at_t_is_invisible_before_t_plus_delay():
    out = js(SNAP + """
      const hist = [{t: 1000, s: snap(97, 104, 10, 10)}, {t: 1100, s: snap(100, 104, 12, 10)}];   // the 3 at t=1100
      const at = (now) => B.delayed(hist, now, {"1": 40}, 45).data.games.find(x => x.id === "1").away.score;
      return [at(1100), at(1139.9), at(1140), at(1200)];""")
    assert out == [97, 97, 100, 100]


def test_each_game_gets_its_own_tiles_delay():
    out = js(SNAP + """
      const hist = [{t: 1000, s: snap(97, 104, 10, 10)}, {t: 1100, s: snap(100, 104, 12, 10)}];
      const d = B.delayed(hist, 1150, {"1": 40, "2": 60}, 45).data.games;
      return d.map(x => [x.id, x.away.score, x.last]);""")
    assert out == [["1", 100, "play 100"], ["2", 10, "x"]]


def test_not_enough_history_shows_a_note_never_the_live_board():
    out = js(SNAP + """
      const hist = [{t: 1000, s: snap(97, 104, 10, 10)}];
      const s = B.delayed(hist, 1010, {}, 45);
      const v = B.view(s, 1010);
      return [s.data.games.length, v.empty, B.delayed(hist, 1010, {}, 0).data.games[0].away.score];""")
    assert out == [0, "Syncing · 45 s", 97]


def test_trim_keeps_what_a_delay_can_reach():
    out = js("""const h = [{t: 1, s: 1}, {t: 50, s: 2}, {t: 90, s: 3}, {t: 100, s: 4}];
                return [B.trim(h, 100, 45).map(x => x.s), B.trim(h, 100, 5).map(x => x.s), B.trim(h, 1000, 5).map(x => x.s)];""")
    assert out == [[2, 3, 4], [3, 4], [4]]


def test_bets_for_the_l_layout():
    out = js("""
      const b = {show: true, ticker: true, n: 2, tiles: [
        {i: 0, delay: 40, src: "auto", label: "GS @ LAL", rows: [{who: "David", side: "YES", label: "GSW −3.5", entry: 42, mark: 51.4, dir: "up", pct: 21, size: "50+ ct"},
                                             {who: "Steph", side: "NO", label: "Over 224.5", entry: 56, mark: 49, dir: "down", pct: 13, size: "8 ct", pnl: -0.56}]},
        {i: 1, delay: 45, src: "default", rows: []}, {i: 5, delay: 45, src: "default", rows: [{who: "x"}]}]};
      const off = Object.assign({}, b, {show: false}), noTicker = Object.assign({}, b, {ticker: false});
      return [B.betRows(b, 2), B.betRows(off, 2), B.betTicker(b, 2), B.betTicker(noTicker, 2), B.tileBets(b, 0), B.tileBets(b, 1), B.tileBets(off, 0)];""")
    rows, hidden, tick, no_tick, t0, t1, t_off = out
    assert hidden == [] and len(rows) == 2 and rows[0]["tile"] == 1 and rows[0]["game"] == "GS @ LAL" and rows[0]["delay"] == "40 s"
    assert {k: rows[0][k] for k in ("who", "what", "px", "dir", "pct", "size", "pnl")} == {
        "who": "David", "what": "YES GSW −3.5", "px": "@42¢ → 51¢", "dir": "up", "pct": "▲ 21%", "size": "50+ ct", "pnl": ""}
    assert rows[1]["pct"] == "▼ 13%" and rows[1]["pnl"] == "−$0.56"
    assert tick[0] == {"head": "David", "text": "YES GSW −3.5", "tail": "42¢→51¢ ▲21%", "tone": "ok"} and tick[1]["tone"] == "danger"
    assert no_tick == []
    assert t0 == {"count": 2, "dir": ""} and t1 == {"count": 0, "dir": ""} and t_off["count"] == 0
