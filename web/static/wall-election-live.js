// Election night, higher fidelity (web/wall_election_live.py): the watch page's "Live updates" stream (vote drops, lead
// changes, calls, poll closings — newest first; key races or all; tap one → that state's sheet) and, inside the state
// sheet, each race's detail: votes + margin in votes, % of the expected vote in, votes still out, counties (closest /
// most vote out / biggest swing), and its vote-updates timeline with the Kalshi price at each update. Calm: no flashing,
// no motion; new rows just appear. Kept apart from wall-election.js: it adds #elecLive and .lv blocks in the sheet's race
// cards (wall-election.js says when it re-renders the sheet with an "es-render" event).
(() => {
  "use strict";
  const panel = document.getElementById("elecPanel"), sheetEl = document.getElementById("elecSheet");
  if (!panel || !sheetEl) return;
  const API = "/api/v1/wall/election";
  const FEED_MS = 15000, RACE_MS = 10000;
  const OFFICE = { S: "Senate", G: "Governor", H: "House" };
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const fmtTime = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const n0 = (v) => (v == null ? "–" : Math.round(v).toLocaleString());
  const big = (v) => (v == null ? "–" : v >= 1e6 ? `${(v / 1e6).toFixed(v >= 1e7 ? 0 : 1)}M` : v >= 1e4 ? `${Math.round(v / 1e3)}K` : v.toLocaleString());
  const sgn = (v, d = 1) => (v == null ? "–" : `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(d)}`);
  const pcls = (p) => ({ D: "D", R: "R", I: "I" })[p] || "";
  const store = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } return null; };

  async function get(path) {
    try {
      const r = await fetch(API + path, { credentials: "same-origin" });
      let j = null;
      try { j = await r.json(); } catch (e) { j = null; }
      return { ok: r.ok, status: r.status, j };
    } catch (e) { return { ok: false, status: 0, j: null }; }
  }
  const openState = (st) => window.__wallElection && window.__wallElection.openState(st);

  // ---- the "Live updates" stream ---------------------------------------------------------------------------------------
  const live = el("section", "card lv-feed");
  live.id = "elecLive"; live.hidden = true; live.setAttribute("aria-label", "Live election updates");
  panel.after(live);
  // where it sits (right rail on wide desktops, under the map, inside the Election sheet on phones): wall-election.js
  if (window.__wallElection && window.__wallElection.placePanels) window.__wallElection.placePanels();
  let scope = store("elecLiveScope") || "key", feed = null;

  function buildFeed() {
    const head = el("div", "el-head");
    head.append(el("h3", null, "Live updates"));
    const seg = el("div", "el-seg"); seg.setAttribute("role", "tablist");
    for (const [k, t] of [["key", "Key races"], ["all", "All"]]) {
      const b = el("button", "el-segbtn", t); b.type = "button"; b.dataset.s = k; b.setAttribute("role", "tab");
      b.onclick = () => { scope = k; store("elecLiveScope", k); loadFeed(); };
      seg.append(b);
    }
    head.append(seg);
    live.append(head, el("div", "lv-next muted small"), el("ol", "lv-items"), el("p", "lv-foot muted small"));
  }

  function feedItem(u) {
    const li = el("li", "lv-it k-" + u.kind);
    const b = el("button", "lv-row"); b.type = "button";
    b.append(el("i", "lv-tally " + (u.kind === "polls" ? "acc" : pcls(u.party))));
    const body = el("span", "lv-body");
    const top = el("span", "lv-top");
    const tag = u.kind === "lead" ? "Lead change" : u.kind === "call" ? (u.flip ? "Call, flip" : "Call") : u.kind === "big" ? "Big drop" : u.kind === "polls" ? "Polls" : "";
    top.append(el("b", "lv-h", u.kind === "polls" ? "Polls closed" : u.head));
    if (tag && u.kind !== "polls") top.append(el("span", "lv-tag t-" + u.kind, tag));
    top.append(el("time", "lv-t muted", fmtTime(u.t)));
    const txt = u.kind === "polls" ? u.states.join(" · ") : u.text.replace(/^[^·]+ takes the lead · /, "");
    body.append(top, el("span", "lv-txt", txt + (u.pct && u.kind !== "polls" && u.kind !== "call" ? ` · ${Math.round(u.pct)}% in` : "")));
    b.append(body);
    if (u.state) { b.onclick = () => openState(u.state); b.title = "Open " + u.state; } else b.disabled = true;
    li.append(b);
    return li;
  }

  function drawFeed() {
    if (!feed) return;
    live.querySelectorAll(".el-segbtn").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.s === scope)));
    const nx = live.querySelector(".lv-next");
    nx.textContent = (feed.next || []).length ? "Next polls close: " + feed.next.slice(0, 2).map((n) => `${fmtTime(n.t)} ${n.states.join(" ")}`).join("; ") : "";
    nx.hidden = !nx.textContent;
    const ol = live.querySelector(".lv-items");
    const items = (feed.items || []).slice(0, 40);
    ol.replaceChildren(...(items.length ? items.map(feedItem) : [el("li", "lv-empty muted small",
      (feed.next || []).length ? "No votes yet." : "Nothing yet.")]));
    live.querySelector(".lv-foot").textContent = (feed.source ? feed.source + ". " : "") + "Unofficial, updates every 30 s." + (feed.error ? " " + feed.error : "");
  }

  async function loadFeed() {
    const r = await get(`/live?scope=${scope}`);
    if (!r.ok) return r.status;
    feed = r.j;
    if (!live.firstChild) buildFeed();
    live.hidden = false;
    drawFeed();
    return 200;
  }

  async function start() {   // once wall.js says someone's in ("wall:me"): never polled into 401s before joining
    let st = await loadFeed();
    if (st === 401 || st === 403) return;
    while (st === 0) { await new Promise((r) => setTimeout(r, 8000)); st = await loadFeed(); }
    setInterval(() => { if (!document.hidden) loadFeed(); }, FEED_MS);
  }
  if (window.__wallMe) start();
  else window.addEventListener("wall:me", start, { once: true });

  // ---- the sheet: each race's detail ----------------------------------------------------------------------------------
  const det = {}, ui = {};       // race id → its last /race payload; race id → { tab, counties, updates } open state
  let timer = null, want = new Set();

  function stat(v, k, cls) { const d = el("div", "lv-stat"); d.append(el("b", cls || "", v), el("span", "muted small", k)); return d; }

  function stats(d) {
    const t = d.totals, row = el("div", "lv-stats");
    row.append(stat(n0(t.votes), "votes counted"));
    row.append(t.votes ? stat(`+${n0(t.margin_votes)}`, `${t.leader} leads · ${sgn(t.margin, 2)} pts`, pcls(t.party)) : stat("–", "no votes yet"));
    row.append(stat(`${Math.round(t.pct)}%`, t.expected ? "of the expected vote in" : "reporting"));
    row.append(stat(t.left == null ? "–" : t.left === 0 ? "0" : "~" + big(t.left), t.left == null ? "votes left: no estimate yet" : `votes left · ${t.left_how}`));
    return row;
  }

  function countyTable(c, tab) {
    const rows = c[tab] || [];
    const t = el("table", "lv-ct");
    const hd = el("tr");
    for (const [x, cls] of [["County", ""], ["In", "n"], ["Leader", ""], ["Margin", "n"], ["Swing", "n"], ["Left", "n"]]) hd.append(el("th", cls, x));
    t.append(el("thead")); t.tHead.append(hd);
    const tb = el("tbody");
    for (const r of rows) {
      const tr = el("tr");
      const sw = r.swing == null ? "–" : `${r.swing >= 0 ? "D" : "R"}+${Math.abs(r.swing).toFixed(1)}`;
      tr.append(el("td", "nm", r.name), el("td", "n", r.votes ? `${r.pct}%` : "0%"), el("td", pcls(r.party), r.lead || "–"),
        el("td", "n", r.m == null ? "–" : `+${r.m.toFixed(1)}`), el("td", "n " + (r.swing == null ? "" : r.swing >= 0 ? "D" : "R"), sw),
        el("td", "n muted", r.left == null ? "–" : big(r.left)));
      tb.append(tr);
    }
    if (!rows.length) { const tr = el("tr"); const td = el("td", "muted small", "No counties counting yet."); td.colSpan = 6; tr.append(td); tb.append(tr); }
    t.append(tb);
    const w = el("div", "lv-ctwrap"); w.append(t);
    return w;
  }

  function countiesBox(id, c) {
    const u = ui[id] = ui[id] || {};
    const sec = el("details", "lv-sec");
    sec.open = u.counties !== false;
    sec.ontoggle = () => { u.counties = sec.open; };
    const sw = c.swing_all == null ? "" : `, swing ${c.swing_all >= 0 ? "D" : "R"}+${Math.abs(c.swing_all).toFixed(1)} vs ${c.swing_label}`;
    sec.append(el("summary", null, `Counties: ${c.reporting} of ${c.n} counting${sw}`));
    const tabs = el("div", "el-seg lv-tabs");
    const TABS = [["closest", "Closest"], ["outstanding", "Most vote out"], ["swing", "Swing"], ["biggest", "Biggest"]];
    u.tab = u.tab || "closest";
    const body = el("div");
    for (const [k, txt] of TABS) {
      const b = el("button", "el-segbtn", txt); b.type = "button"; b.setAttribute("aria-selected", String(u.tab === k));
      b.onclick = () => { u.tab = k; tabs.querySelectorAll("button").forEach((x, i) => x.setAttribute("aria-selected", String(TABS[i][0] === k))); body.replaceChildren(countyTable(c, k)); };
      tabs.append(b);
    }
    body.append(countyTable(c, u.tab));
    sec.append(tabs, body);
    if (c.swing_label) sec.append(el("p", "muted small lv-note", `Swing: D−R margin change vs ${c.swing_label}, same county.`));
    return sec;
  }

  function timelineBox(id, tl) {
    const u = ui[id] = ui[id] || {};
    const sec = el("details", "lv-sec");
    sec.open = u.updates !== false;
    sec.ontoggle = () => { u.updates = sec.open; };
    sec.append(el("summary", null, `Vote updates${tl.length ? ` (${tl.length})` : ""}`));
    if (!tl.length) { sec.append(el("p", "muted small", "No updates yet.")); return sec; }
    const ol = el("ol", "lv-tl");
    tl.slice(0, 30).forEach((x, i) => {
      const li = el("li", "k-" + x.kind);
      li.append(el("time", "muted", fmtTime(x.t)));
      const txt = el("span", "tx");
      if (x.kind === "lead") txt.append(el("b", "lv-tag t-lead", "Lead change"), document.createTextNode(" "));
      if (x.kind === "call") txt.append(el("b", "lv-tag t-call", "Call"), document.createTextNode(" "));
      txt.append(document.createTextNode(x.text.replace(/^[^·]+ takes the lead · /, "") + (x.kind !== "call" ? ` · ${Math.round(x.pct)}% in` : "")));
      li.append(txt);
      if (x.px && x.px.prob != null) {
        const prev = tl.slice(i + 1).find((y) => y.px && y.px.prob != null && y.px.who === x.px.who);
        const ch = prev ? x.px.prob - prev.px.prob : null;
        li.append(el("span", "px " + (ch == null || Math.abs(ch) < 1 ? "" : ch > 0 ? "up" : "down"),
          `Kalshi ${x.px.who} ${Math.round(x.px.prob)}%${ch == null || Math.abs(ch) < 1 ? "" : ` ${ch > 0 ? "▲" : "▼"}${Math.abs(ch).toFixed(0)}`}`));
      }
      ol.append(li);
    });
    sec.append(ol);
    return sec;
  }

  function block(id) {
    const d = det[id];
    const box = el("div", "lv");
    box.dataset.id = id;
    if (!d) { box.append(el("p", "muted small", "Loading vote detail…")); return box; }
    box.append(stats(d));
    if (d.totals.history && d.totals.history.label && d.totals.history.margin != null)
      box.append(el("p", "muted small lv-note", `${d.totals.history.label}: ${d.totals.history.margin >= 0 ? "D" : "R"}+${Math.abs(d.totals.history.margin).toFixed(1)}`));
    if (d.counties && d.counties.reporting) box.append(countiesBox(id, d.counties));
    else if (d.counties) box.append(el("p", "muted small lv-note", `County results (${d.counties.n}) appear once counting starts.`));
    box.append(timelineBox(id, d.timeline || []));
    box.append(el("p", "muted small lv-note", `${d.source ? d.source + ". " : ""}Refreshes every ${d.fast_s || 10} s.`));
    return box;
  }

  function place(card, node) {
    const old = card.querySelector(".lv");
    if (old) { old.replaceWith(node); return; }
    const cands = card.querySelector(".es-cands");
    let after = cands;
    if (cands && cands.nextElementSibling && cands.nextElementSibling.tagName === "P") after = cands.nextElementSibling;   // its "% in · votes" line
    if (after) after.after(node); else card.append(node);
  }

  function inject() {
    want = new Set();
    sheetEl.querySelectorAll(".es-race[data-id]").forEach((card) => {
      const id = card.dataset.id;
      if (!/^[SGH]-/.test(id)) return;
      if (id.startsWith("H-") && !(ui[id] && ui[id].on)) {
        if (card.querySelector(".lv-more")) return;
        const b = el("button", "btn small ghost lv-more", "Vote details"); b.type = "button";
        b.onclick = () => { ui[id] = ui[id] || {}; ui[id].on = true; b.remove(); place(card, block(id)); want.add(id); loadRace(id); };
        place(card, b);
        return;
      }
      want.add(id);
      place(card, block(id));
    });
  }

  async function loadRace(id) {
    const r = await get(`/race/${encodeURIComponent(id)}`);
    if (!r.ok) return;
    det[id] = r.j;
    const card = sheetEl.querySelector(`.es-race[data-id="${CSS.escape(id)}"]`);
    if (card) place(card, block(id));
  }

  function tick() {
    if (document.hidden || sheetEl.hidden || !sheetEl.classList.contains("open")) return;
    [...want].slice(0, 8).forEach(loadRace);
  }

  sheetEl.addEventListener("es-render", () => {
    inject();
    if (!timer) { tick(); timer = setInterval(tick, RACE_MS); }
  });
  new MutationObserver(() => { if (sheetEl.hidden && timer) { clearInterval(timer); timer = null; } }).observe(sheetEl, { attributes: true, attributeFilter: ["hidden"] });
  window.__wallElectionLive = { loadFeed, det };
})();
