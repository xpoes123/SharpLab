.pragma library
// Bands.qml's one bit of logic, kept here so it can be tested in node: which version of a tile's game its header shows.

// "Q3 4:12", "Halftime", "Final", "7:30 PM" — the same as wall/bands.py status()
function status(g) {
    if (g.state === "post") return g.detail || "Final";
    if (g.state === "pre") return g.detail || "Soon";
    var p = Number(g.period || 0), clock = g.clock || "", detail = g.detail || "";
    if (detail && clock && detail.indexOf(clock) < 0) return detail;
    var per = p <= 4 ? "Q" + p : p === 5 ? "OT" : (p - 4) + "OT";
    return (per + " " + clock).trim();
}

// tg = the service's game for a tile (wall/bands.py, as of now − that tile's delay); data = the overlay's own board
// (Board.qml's `last.data`, already B.delayed with the same per-game delays from the service). When the board holds
// the game, its version wins, so a header and the L-bar's sidebar / ticker always show the same score. A board
// still syncing has no games: the service's version (or its "syncing") stands.
function headerGame(tg, data) {
    if (!tg || !data || data.kind !== "scores" || !data.games) return tg;
    var g = null;
    for (var i = 0; i < data.games.length; i++) if (String(data.games[i].id) === String(tg.id)) { g = data.games[i]; break; }
    if (!g || !g.away || !g.home) return tg;
    var pre = g.state === "pre";
    function side(t, mine) {
        return { abbr: mine.abbr, name: mine.name, color: mine.color, score: pre || t.score === undefined ? null : t.score, poss: !pre && !!t.poss };
    }
    return { league: tg.league, id: tg.id, state: g.state, status: status(g), close: !pre && !!g.close,
             away: side(g.away, tg.away), home: side(g.home, tg.home) };
}
