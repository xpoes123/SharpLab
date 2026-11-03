.pragma library
// /api/overlay → what the native overlay (Board.qml) draws. Pure functions, no Qt: tests/js/overlay_board.test.mjs
// runs this file in node. A new board kind = one entry in KINDS + a provider in boards.py.

function fmt(n) {
    return n === null || n === undefined ? "–" : Number(n).toFixed(Number.isInteger(n) ? 0 : 1);
}

function ago(t, now) {
    var s = Math.max(0, now - t);
    return s < 60 ? "now" : s < 3600 ? Math.round(s / 60) + "m" : Math.round(s / 3600) + "h";
}

function clock(t) {   // epoch seconds → "1:05 PM", the box's local time
    var d = new Date(t * 1000), h = d.getHours(), m = d.getMinutes();
    return ((h + 11) % 12 + 1) + ":" + (m < 10 ? "0" : "") + m + (h < 12 ? " AM" : " PM");
}

// ---- fantasy ----
function pct(p) {   // a probability → "62%"; never 0 % / 100 % while anything is still to play
    if (p === null || p === undefined) return "";
    var n = Math.round(p * 100);
    return (p > 0 && p < 1 ? Math.min(99, Math.max(1, n)) : n) + "%";
}

function chop(l) {
    var m = l.me.points - l.opp.points;
    return l.rank + "/" + l.alive + " alive · " + (m >= 0 ? "+" : "−") + Math.abs(m).toFixed(1) + " " + (l.result === "lose" ? "to safety" : "over chop");
}

function league(l) {
    var g = l.mode === "guillotine" && l.opp;
    return {
        type: "league", result: l.result || "bye", short: l.short || "", name: l.name || "",
        me: fmt(l.me.points), meTeam: l.me.team || "",
        opp: l.opp ? fmt(l.opp.points) : "", oppTeam: l.opp ? (g ? "Chop: " : "") + l.opp.team : "Bye week",
        vs: !l.opp ? "" : g ? chop(l) : "vs " + l.opp.team,
        chop: g ? chop(l) : "",
        win: pct(l.win), winTone: l.win === null || l.win === undefined ? "" : l.win >= 0.5 ? "ok" : "danger"
    };
}

// one item of his feed: every scoring change of one of his starters (big = ≥ his threshold → highlighted)
function play(p) {
    var d = Number(p.delta || 0);
    return {
        key: p.t + ":" + (p.id || p.player), t: p.t, big: !!p.big, td: !!p.td, neg: d < 0,
        delta: (d < 0 ? "−" : "+") + Math.abs(d).toFixed(1),
        player: (p.player || "") + (p.team ? " · " + p.team : "") + (p.td ? " · TD?" : ""),
        where: ((p.leagues && p.leagues.length ? p.leagues.join(" · ") : p.league) || "") + " · " + clock(p.t),
        detail: p.detail || ""
    };
}

// ---- scores (any ESPN league) ----
function team(t, g) {
    return {
        abbr: t.abbr || "?", color: t.color || "#5C5C51", logo: t.logo || "", poss: !!t.poss,
        record: g.state === "pre" ? t.record || "" : "",
        score: g.state === "pre" || t.score === null || t.score === undefined ? "" : String(t.score),
        lost: g.state === "post" && !t.winner
    };
}

function game(g) {
    var sit = g.state === "in" && g.down ? g.down + " · " + g.spot : "";
    return {
        type: "game", live: g.state === "in", pre: g.state === "pre", close: !!g.close, redzone: g.state === "in" && !!g.down && !!g.redzone,
        away: team(g.away, g), home: team(g.home, g), status: g.detail || "", situation: sit
    };
}

// ---- the L-bar's bottom band: one ticker item = {head, text, tail, tone}; tone "ok" | "danger" | "accent" | "ink" ----
function playItem(p) {
    var q = play(p);
    return { head: q.delta, text: q.player, tail: q.where, tone: q.big ? "accent" : q.neg ? "danger" : "ok" };
}

function line(g) { return g.away.abbr + " " + (g.away.score === null ? "" : g.away.score) + "–" + (g.home.score === null ? "" : g.home.score) + " " + g.home.abbr; }

function gameItems(games) {   // live plays first (closest games on top, as sorted), then final headlines, then what's next
    var live = [], post = [], pre = [];
    (games || []).forEach(function (g) {
        if (g.state === "in") live.push({ head: line(g), text: /timeout/i.test(g.last || "") ? "" : (g.last || ""), tail: g.detail || "", tone: g.close ? "accent" : "ink" });
        else if (g.state === "post") post.push({ head: "Final · " + line(g), text: g.headline || "", tail: "", tone: "ink" });
        else pre.push({ head: g.away.abbr + " @ " + g.home.abbr, text: (g.away.record && g.home.record) ? g.away.record + " vs " + g.home.record : "", tail: g.detail || "", tone: "ink" });
    });
    return live.concat(post, pre);
}

// ---- election night (wall/election.py board()) ----
var PARTY = { D: "Dem", R: "Rep", I: "Ind", O: "Other" };
var OFFICE_SHORT = { S: "SEN", H: "HOUSE", G: "GOV" };

function bop(o) {   // one office's balance → the sidebar's block: numbers, bar segments (fractions of all seats), lines
    var s = o.seats, t = o.total || 1, D = s.D.held + s.D.won, R = s.R.held + s.R.won, I = s.I.held + s.I.won;
    var ctl = o.control && o.control.D ? o.control.D : 0, net = o.net || {};
    var n = (net.D || 0) > 0 ? { text: "Dem +" + net.D, tone: "dem" } : (net.R || 0) > 0 ? { text: "Rep +" + net.R, tone: "rep" } : { text: "no net change", tone: "" };
    return {
        id: o.office, name: o.name, dem: String(D), rep: String(R), ind: I ? I + " Ind" : "",
        segs: [{ f: D / t, c: "dem" }, { f: s.D.lead / t, c: "demLead" }, { f: I / t, c: "ind" }, { f: s.I.lead / t, c: "indLead" },
               { f: o.out / t, c: "none" }, { f: s.R.lead / t, c: "repLead" }, { f: R / t, c: "rep" }],
        tick: ctl ? ctl / t : 0,
        need: ctl ? ctl + " for control" : o.up + " up",
        lead: (s.D.lead || s.R.lead) ? "leading " + s.D.lead + "–" + s.R.lead : "",
        net: o.office === "G" ? "" : n.text, netTone: n.tone,
        left: o.out ? o.out + " not counted" : o.called + "/" + o.up + " called",
        winner: o.winner ? PARTY[o.winner] + " control" : "", winTone: o.winner === "D" ? "dem" : o.winner === "R" ? "rep" : ""
    };
}

function callItem(c, now) {
    return { head: (c.id || "").replace(/^([SHG])-([A-Z]{2})(-(\d+))?(-special)?$/, function (m, o, st, x, d, sp) {
                 return st + (d ? "-" + d : "") + " " + OFFICE_SHORT[o] + (sp ? " (sp.)" : ""); }),
             text: c.text.replace(/^[^:]*: /, "").replace(/ · flip$/, ""), tail: (c.by || "") + (c.t ? " · " + clock(c.t) : ""),
             tone: c.party === "D" ? "dem" : c.party === "R" ? "rep" : "ind", flip: !!c.flip, t: c.t || 0 };
}

function electionItems(d, now) {   // the ticker: the calls as they come (newest first); before any, what's next
    var calls = (d.calls || []).map(function (c) { return callItem(c, now); });
    var watch = (d.watch || []).map(function (r) {
        var a = r.cands[0], b = r.cands[1];
        return { head: r.state + " " + OFFICE_SHORT[r.office], text: a.name + " " + a.pct.toFixed(1) + (b ? " – " + b.name + " " + b.pct.toFixed(1) : ""),
                 tail: Math.round(r.pct) + "% in", tone: "ink" };
    });
    var next = (d.next || []).map(function (n) { return { head: "Polls close " + clock(n.t), text: n.states.join(" "), tail: "", tone: "accent" }; });
    return calls.slice(0, 12).concat(watch, next);
}

// members' midterm positions (wall/election.py trades(): opt-in filtered, live — results aren't a picture)
function tradeItem(t) {
    var l = betLine(t);
    return { who: l.who, side: t.side || "", what: t.label || "", px: l.px, pct: l.pct, dir: l.dir, size: l.size, pnl: l.pnl,
             status: t.status || "", chip: t.chip || "", won: t.won === true ? "won" : t.won === false ? "lost" : "",
             highlight: t.highlight || "" };
}

function tradeTicker(d) {   // a call on a race someone holds first ("✓ CALLED · Hansen wins +38%"), then every position
    var called = [], open = [];
    (d.trades || []).forEach(function (t) {
        var l = tradeItem(t);
        if (l.highlight) called.push({ head: l.highlight.replace(/ [+−]\d+%$/, ""), text: l.side + " " + l.what,
                                       tail: (l.highlight.match(/[+−]\d+%$/) || [""])[0],
                                       tone: l.won === "won" ? "ok" : "", called: true });
        else open.push({ head: l.who, text: l.side + " " + l.what + (l.status ? " · " + l.status : ""),
                         tail: l.px.replace("@", "").replace(" → ", "→") + (l.pct ? " " + l.pct.replace(" ", "") : ""),
                         tone: l.dir === "up" ? "ok" : l.dir === "down" ? "danger" : "" });
    });
    return called.concat(open);
}

function interleave(a, b) {   // the ticker: race calls and positions take turns, so neither crowds the other out
    var out = [], i = 0;
    for (; i < Math.max(a.length, b.length); i++) {
        if (i < a.length) out.push(a[i]);
        if (i < b.length) out.push(b[i]);
    }
    return out;
}

var KINDS = {
    election: function (d, now) {
        var tt = tradeTicker(d), calledFirst = tt.filter(function (x) { return x.called; });
        var items = electionItems(d, now);
        return { cells: [], plays: [], side: false, ticker: calledFirst.concat(interleave(items, tt.slice(calledFirst.length))),
                 kicker: (d.calls || []).length ? "Race calls" : "Election",
                 elec: { offices: (d.offices || []).map(bop), trades: (d.trades || []).map(tradeItem), calls: (d.calls || []).slice(0, 10).map(function (c) { return callItem(c, now); }),
                         source: "Unofficial · calls: " + (d.source === "NYT (AP)" ? "AP" : d.source || "–"), counting: d.counting || 0,
                         next: (d.next || []).length ? "Polls close " + clock(d.next[0].t) + " · " + d.next[0].states.join(" ") : "" } };
    },
    fantasy: function (d) { return { cells: (d.leagues || []).map(league), plays: (d.plays || []).map(play), side: true,
                                     ticker: (d.plays || []).map(playItem), kicker: "My plays" }; },
    scores: function (d) {
        var live = (d.games || []).some(function (g) { return g.state === "in"; });
        var cells = (d.games || []).map(game), pre = cells.filter(function (c) { return c.pre; });
        // the L-bar sidebar: only games in progress (already live-close-late first); none live → the next few, dimmed
        return { cells: cells, plays: [], side: false, ticker: gameItems(d.games), kicker: live ? "Live" : "Today",
                 liveCells: cells.filter(function (c) { return c.live; }), upNext: pre.slice(0, 3), nextAt: pre.length ? pre[0].status : "" };
    }
};

// The whole frame: header text + cells (+ his plays feed for fantasy). `s` is the GET /api/overlay body.
function view(s, now) {
    var d = s && s.data, k = d && KINDS[d.kind];
    var b = ((s && s.boards) || []).filter(function (x) { return x.id === s.shown; })[0];
    var name = b ? b.name : (s && s.shown) || "";
    var live = d && d.kind === "scores" ? (d.games || []).filter(function (g) { return g.state === "in"; }).length : 0;
    var empty = !d ? "Loading…" : d.note || (d.kind === "fantasy" && !(d.leagues || []).length ? "No leagues this week"
        : d.kind === "scores" && !(d.games || []).length ? "No games today" : !k ? "?" : "");
    var body = k && !empty ? k(d, now) : { cells: [], plays: [], side: false, ticker: [], kicker: "" };
    if (!body.liveCells) { body.liveCells = []; body.upNext = []; body.nextAt = ""; }
    var parts = ((d && d.title) || "").split("·");
    return {
        name: name, title: (d && d.title) || name, sub: parts.length > 1 ? parts[1].trim() : "", live: live,
        error: (s && s.error) || "", meta: s && s.updated ? "updated " + ago(s.updated, now) : "loading…",
        empty: empty, kind: d ? d.kind : "", cells: body.cells, plays: body.plays, side: body.side,
        ticker: body.ticker, kicker: body.kicker, liveCells: body.liveCells, upNext: body.upNext, nextAt: body.nextAt,
        elec: body.elec || null
    };
}

// Strip paging: how many fixed-width cells fit, and which slice is page p (a mechanical flip, like a stadium board).
function page(n, avail, cellW, gap, p) {
    var per = Math.max(1, Math.floor((avail + gap) / (cellW + gap)));
    var pages = Math.max(1, Math.ceil(n / per));
    var i = p % pages;
    return { start: i * per, end: Math.min(n, (i + 1) * per), pages: pages };
}

// ---- the matchup panel (GET /api/overlay's "matchup", built by boards.matchup_payload) ----
function pts1(n) { return Number(n || 0).toFixed(1); }

function lineRow(r) {
    var proj = r.live === null || r.live === undefined ? null : Number(r.live);
    return { slot: r.slot || "", name: r.name || "", meta: [r.pos === r.slot ? "" : r.pos, r.team].filter(function (x) { return x; }).join(" · "),   // the slot says QB already
             game: r.game || "", live: r.state === "in", done: r.state === "post", pts: pts1(r.pts), empty: !r.pos && !r.team,
             img: r.img || "", logo: r.logo || "", def: r.pos === "DEF",
             proj: proj === null ? "" : "proj " + pts1(proj),
             // the bar: how much of his (live) projection is in; full once his game is over
             frac: r.state === "post" ? 1 : proj === null || proj <= 0 ? 0 : Math.max(0, Math.min(1, Number(r.pts || 0) / proj)) };
}

function lineupSide(s) {
    return s ? { team: s.team || "", pts: pts1(s.points), avatar: s.avatar || "", starters: (s.starters || []).map(lineRow),
                 proj: s.proj === null || s.proj === undefined ? "" : "proj " + pts1(s.proj) } : null;
}

function matchup(m) {
    if (!m || !m.me) return null;
    var g = m.mode === "guillotine" && (m.standings || []).length > 1, status;
    var st = (m.standings || []).map(function (t, i, a) {
        return { rank: i + 1, team: t.team, pts: pts1(t.points), me: !!t.me, chop: i === a.length - 1, avatar: t.avatar || "",
                 proj: t.proj === null || t.proj === undefined ? "" : pts1(t.proj) };
    });
    if (g) {   // same reading as the league card: margin over the chop line, or to safety when he is the one on it
        var r = m.standings.map(function (t) { return t.me; }).indexOf(true), n = m.standings.length;
        var ref = m.standings[r === n - 1 ? n - 2 : n - 1].points, d = m.me.points - ref;
        status = (r + 1) + "/" + n + " alive · " + (d >= 0 ? "+" : "−") + Math.abs(d).toFixed(1) + (r === n - 1 ? " to safety" : " over the chop");
    } else if (m.opp) {
        var diff = m.me.points - m.opp.points;
        status = diff > 0 ? "Up " + diff.toFixed(1) : diff < 0 ? "Down " + (-diff).toFixed(1) : "Tied";
    } else status = "Bye week";
    var hasWin = m.win !== null && m.win !== undefined;
    return {
        name: m.name || "", short: m.short || "", result: m.result || "bye", guillotine: g, status: status,
        where: (m.week ? "Week " + m.week : "") + (m.count > 1 ? " · league " + (m.index + 1) + " of " + m.count : ""),
        me: lineupSide(m.me), opp: g ? null : lineupSide(m.opp), standings: g ? st : [],
        win: hasWin ? pct(m.win) : "", winFrac: hasWin ? m.win : 0, winTone: !hasWin ? "" : m.win >= 0.5 ? "ok" : "danger",
        winLabel: g ? "to survive" : "to win",
        source: m.projections ? "Sleeper projections" : "no projections · actual points only"
    };
}

// ---- spoiler delay: the board as the picture shows it ----
// hist = [{t, s}] (s = a GET /api/overlay body, t = when it came, epoch s), oldest first. Every game is taken from the
// newest body at least its tile's delay old (delays[ESPN id], else dflt); the rest of the board (fantasy plays, titles)
// from the newest at least dflt old. Nothing newer ever shows; until the history is that long: a "matching" note.
function snapAt(hist, cut) {
    var pick = null;
    for (var i = 0; i < hist.length && hist[i].t <= cut; i++) pick = hist[i].s;
    return pick;
}

function delayed(hist, now, delays, dflt) {
    if (!hist || !hist.length) return null;
    var latest = hist[hist.length - 1].s;
    if (latest && latest.data && latest.data.kind === "election") return latest;   // results aren't a picture: nothing to hold back
    if (!(dflt > 0) && !Object.keys(delays || {}).length) return latest;
    var base = snapAt(hist, now - dflt), out = {};
    for (var k in latest) out[k] = latest[k];
    if (!latest.data) return out;
    if (!base || !base.data) {
        out.data = { kind: latest.data.kind, title: latest.data.title, games: [], leagues: [], plays: [],
                     note: "Syncing · " + Math.round(dflt) + " s" };
        out.updated = 0;
        return out;
    }
    var d = {};
    for (var j in base.data) d[j] = base.data[j];
    if (base.data.kind === "scores") {
        d.games = [];
        (base.data.games || []).forEach(function (g) {
            var gd = delays && delays[g.id] !== undefined ? delays[g.id] : dflt;
            var s = gd === dflt ? base : snapAt(hist, now - gd);
            var v = s && s.data && (s.data.games || []).filter(function (x) { return x.id === g.id; })[0];
            if (v) d.games.push(v);
        });
    }
    out.data = d;
    out.updated = base.updated;
    return out;
}

function trim(hist, now, keep) {   // drop what no delay can reach any more (always keep the newest)
    var i = 0;
    while (i < hist.length - 1 && hist[i + 1].t <= now - keep) i++;
    return hist.slice(i);
}

// ---- members' Kalshi positions on a tile (GET <bets>/api/overlay/bets) ----
function cents(v) { return v === null || v === undefined ? "–" : Math.round(v) + "¢"; }

function betLine(r) {
    var pct = r.pct === null || r.pct === undefined ? "" : (r.dir === "down" ? "▼ " : r.dir === "up" ? "▲ " : "") + Math.abs(r.pct) + "%";
    var pnl = r.pnl === null || r.pnl === undefined ? "" : (r.pnl >= 0 ? "+$" : "−$") + Math.abs(r.pnl).toFixed(2);
    return { who: r.who || "", what: (r.side || "") + " " + (r.label || ""), px: "@" + cents(r.entry) + " → " + cents(r.mark),
             dir: r.dir || "", pct: pct, size: r.size || "", pnl: pnl };
}

// The bets live in the L layout, never over the video: the sidebar's Bets section, the ticker's bets line, the
// bands' header chips + detail. Every row is already delayed to its tile's picture (+ opt-in filtered) by the service.
function betRows(b, n) {   // → [{tile (1-based), game, delay, who, what, px, dir, pct, size, pnl}] in tile order
    var out = [];
    ((b && b.show && b.tiles) || []).forEach(function (t) {
        if (t.i < n) (t.rows || []).forEach(function (r) {
            var l = betLine(r);
            l.tile = t.i + 1; l.game = t.label || ""; l.delay = Math.round(t.delay) + " s";
            out.push(l);
        });
    });
    return out;
}

function betTicker(b, n) {   // the ticker's bets line, when the owner turned it on: "David  YES GSW −3.5  42¢→51¢ ▲21%"
    if (!b || !b.ticker) return [];
    return betRows(b, n).map(function (l) {
        return { head: l.who, text: l.what, tail: l.px.replace("@", "").replace(" → ", "→") + (l.pct ? " " + l.pct.replace(" ", "") : ""),
                 tone: l.dir === "up" ? "ok" : l.dir === "down" ? "danger" : "" };
    });
}

function tileBets(b, i) {   // a band header's chip: {count, dir} (dir = where most of its positions moved)
    var t = ((b && b.show && b.tiles) || []).filter(function (x) { return x.i === i; })[0];
    var rows = (t && t.rows) || [], up = 0, down = 0;
    rows.forEach(function (r) { if (r.dir === "up") up++; else if (r.dir === "down") down++; });
    return { count: rows.length, dir: up > down ? "up" : down > up ? "down" : "" };
}
