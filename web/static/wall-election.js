// The watch page's Election panel: a US map coloured like the TV's (won / leading / no votes yet / no race), office
// toggles, the chamber-control chips, and — click or tap a state — a sheet with that state's races (CNN's live status),
// their Kalshi markets (bid / ask / last, implied %, 24 h change), a price chart (implied % over the last days + today's
// session, poll close + call marked) and, for members only, the opted-in members' positions there. Members can also push
// the state to the TV's spotlight (a vote, or at once for the owner). Calm: nothing flashes; fills fade over 2 s.
// Kept apart from wall.js: it owns #elecPanel, #elecSheet and #elecBtn only (web/wall_election.py is its backend).
(() => {
  "use strict";
  const root = document.getElementById("elecPanel");
  if (!root) return;
  const API = "/api/v1/wall/election";
  const MAP_MS = 30000, STATE_MS = 30000, CHART_MS = 60000;
  const PARTY = { D: "Dem", R: "Rep", I: "Ind", O: "Other" };
  const OFFICE = { S: "Senate", G: "Governor", H: "House" };
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const svgEl = (tag, attrs) => { const e = document.createElementNS("http://www.w3.org/2000/svg", tag); for (const k in attrs || {}) e.setAttribute(k, attrs[k]); return e; };
  const fmtTime = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const pct = (v) => (v == null ? "–" : `${v < 1 && v > 0 ? v.toFixed(1) : Math.round(v)}%`);
  const cents = (v) => (v == null ? "–" : `${Math.round(v)}¢`);
  const partyCls = (p) => ({ D: "D", R: "R", I: "I" })[p] || "O";

  let data = null, shapes = null, office = "S", sheet = null, timers = [];
  let turnout = null;     // official turnout by state (/turnout → wall/turnout.py), refreshed every minute
  const loadTurnout = () => get("/turnout").then((r) => { if (r.ok) turnout = r.j; }).catch(() => {});
  loadTurnout(); setInterval(loadTurnout, 60000);
  function turnoutBlock(t) {   // E-day so far (+ last report) and advance by party; party = registration, not vote choice
    const n = (d) => d ? ["D", "R", "O", "N"].reduce((a, p) => a + (d[p] || 0), 0) : 0, f = (x) => (x || 0).toLocaleString("en-US");
    const m = (v) => v == null ? "" : ` · ${v >= 0 ? "D" : "R"}+${Math.abs(100 * v).toFixed(1)}`;
    const box = el("div", "es-turnout");
    box.append(el("div", "muted small", `Turnout · ${t.source || ""}${t.as_of ? " · as of " + t.as_of : ""}`));
    const b = (t.batches || [])[0];
    if (n(t.eday)) box.append(el("div", null, `Election Day so far: ${f(n(t.eday))}` + (b ? ` (+${f(n(b))} last report)` : "")));
    const a = t.advance;
    if (n(a)) box.append(el("div", null, a.D || a.R ? `Advance (early + mail): ${f(a.D)} D · ${f(a.R)} R · ${f(a.O)} other${m(t.m_adv)}` : `Advance (early + mail): ${f(n(a))}`));
    box.append(el("div", "muted small", "Party = voter registration, not vote choice."));
    return box;
  }
  let localNews = null;   // {ST: [{name, url}]}: local-news.json, YouTube channels' /live pages
  fetch("/local-news.json").then((r) => r.json()).then((j) => (localNews = j)).catch(() => {});
  // phones, portrait or landscape (keep in step with wall-election.css): the panel opens as a sheet from the bar's button
  const phone = () => matchMedia("(max-width: 700px), (orientation: landscape) and (max-height: 500px)").matches;
  const COVERAGE = Date.UTC(2026, 10, 3, 23);            // 6 PM ET, 3 Nov 2026: the first polls close
  const ELECTION_UNTIL = Date.UTC(2026, 10, 5, 12);      // the pre-show / join-card copy stops after this
  const etTime = (ms) => new Date(ms).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: "America/New_York" }) + " ET";

  async function get(path) {
    const r = await fetch(API + path, { credentials: "same-origin" });
    let j = null;
    try { j = await r.json(); } catch (e) { j = null; }
    return { ok: r.ok, status: r.status, j };
  }

  // ---- the map --------------------------------------------------------------------------------------------------------
  function fills(o, races) {   // same rules as the TV map (wall/pages/election/map.html stateFill)
    const out = {}, by = {};
    for (const r of races) if (r.office === o) (by[r.state] = by[r.state] || []).push(r);
    for (const [st, rs] of Object.entries(by)) {
      if (o !== "H") {
        const r = rs.find((x) => x.called) || rs.find((x) => !x.special) || rs[0];
        out[st] = r.called ? fill(r.called, true) : r.leader ? fill(r.leader, false) : "none";
        continue;
      }
      const n = { D: 0, R: 0 };
      let called = 0;
      for (const r of rs) { const p = r.called || r.leader; if (p === "D" || p === "R") n[p]++; if (r.called) called++; }
      const p = n.D > n.R ? "D" : n.R > n.D ? "R" : "";
      out[st] = !p ? "none" : fill(p, called === rs.length);
    }
    return out;
  }
  const fill = (p, called) => { const k = { D: "dem", R: "rep", I: "ind", O: "ind" }[p]; return k ? k + (called ? "" : "-lead") : "none"; };

  function stateSummary(st) {
    const rs = (data.races || []).filter((r) => r.state === st && r.office === office);
    if (!rs.length) return `${data.states[st] || st} · no ${OFFICE[office]} race`;
    if (office === "H") {
      const c = rs.filter((r) => r.called).length;
      return `${data.states[st]} · ${rs.length} House seat${rs.length > 1 ? "s" : ""}${c ? ` · ${c} called` : ""}`;
    }
    const r = rs.find((x) => x.called) || rs.find((x) => !x.special) || rs[0];
    return `${data.states[st]} · ${r.called ? `✓ ${PARTY[r.called]}` : r.votes ? `${Math.round(r.pct)}% in · ${PARTY[r.leader] || ""} +${r.margin ?? 0}` : "no votes yet"}`;
  }

  function buildPanel() {
    root.replaceChildren();
    const head = el("div", "el-head");
    head.append(el("h3", null, "Election"));
    const tog = el("div", "el-seg");
    tog.setAttribute("role", "tablist");
    for (const o of ["S", "H", "G"]) {
      const b = el("button", "el-segbtn", o === "G" ? "Gov" : OFFICE[o]);
      b.type = "button"; b.dataset.o = o; b.setAttribute("role", "tab");
      b.onclick = () => { office = o; drawMap(); };
      tog.append(b);
    }
    const chips = el("div", "el-chips");
    for (const o of ["S", "H"]) {
      const c = el("button", "el-chip", `Control of ${OFFICE[o]}`);
      c.type = "button"; c.dataset.ctl = o;
      c.onclick = () => openControl(o);
      chips.append(c);
    }
    const pulse = el("a", "el-chip", "Turnout & vote-share projector ↗");
    pulse.href = "/pulse.html"; pulse.target = "_blank"; pulse.rel = "noopener";
    pulse.title = "Votes ÷ % expected in → projected finals, against Kalshi's turnout and vote-share markets";
    chips.append(pulse);
    const done = el("button", "btn ghost el-done", "Done"); done.type = "button";
    done.onclick = () => { root.classList.remove("as-sheet"); document.body.classList.remove("elec-on"); };
    head.append(tog, chips, done);
    const bal = el("div", "el-bal");
    const mapBox = el("div", "el-map");
    const tip = el("div", "el-tip"); tip.hidden = true;
    mapBox.append(tip);
    const legend = el("div", "el-legend muted small");
    legend.innerHTML = `<span><i class="f-dem"></i><i class="f-rep"></i> won</span><span><i class="f-dem-lead"></i><i class="f-rep-lead"></i> leading</span>` +
      `<span><i class="f-none"></i> no votes yet</span><span><i class="f-off"></i> no race</span>`;
    const pickRow = el("div", "el-pick");
    const sel = el("select", "el-select");
    sel.setAttribute("aria-label", "Open a state");
    sel.onchange = () => { if (sel.value) openState(sel.value); sel.value = ""; };
    pickRow.append(sel, legend);
    const foot = el("p", "el-foot muted small");
    root.append(head, bal, mapBox, pickRow, foot);
  }

  function drawMap() {
    if (!data) return;
    root.querySelectorAll(".el-segbtn").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.o === office)));
    const box = root.querySelector(".el-map");
    if (shapes && !box.querySelector("svg")) {
      const svg = svgEl("svg", { viewBox: "0 0 975 610", role: "img", "aria-label": "US map — pick a state" });
      for (const [st, x] of Object.entries(shapes)) {
        const p = svgEl("path", { d: x.d, "data-st": st, tabindex: "0", role: "button", "aria-label": x.name || st });
        p.addEventListener("click", () => openState(st));
        p.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openState(st); } });
        p.addEventListener("pointerenter", (e) => tipAt(st, e));
        p.addEventListener("pointermove", (e) => tipAt(st, e));
        p.addEventListener("pointerleave", () => (box.querySelector(".el-tip").hidden = true));
        svg.append(p);
      }
      box.prepend(svg);
    }
    const f = fills(office, data.races || []);
    box.querySelectorAll("path").forEach((p) => { p.setAttribute("class", "f-" + (f[p.dataset.st] || "off")); });
    // the balance strip for this office
    root.querySelector(".el-bal").replaceChildren(...balance(office));
    renderPreshow();
    // the state picker (small states are hard to tap)
    const sel = root.querySelector(".el-select");
    const with_ = [...new Set((data.races || []).filter((r) => r.office === office).map((r) => r.state))].sort((a, b) => (data.states[a] || a).localeCompare(data.states[b] || b));
    sel.replaceChildren(new Option(`Open a state (${with_.length} with a ${OFFICE[office]} race)…`, ""), ...with_.map((st) => new Option(data.states[st] || st, st)));
    root.querySelector(".el-foot").textContent = (data.source ? `${data.source} · ` : "") +
      (data.updated ? `updated ${fmtTime(data.updated)} · ` : "") + "unofficial until certified · prices: Kalshi" + (data.error ? ` · ${data.error}` : "");
  }

  // "D 34 not up · 51 for control · 0 of 35 called · R 31 not up": seats not up this year are not a lead
  const seatText = (p, s) => { const h = s[p].held, w = s[p].won;
    return !w ? `${p} ${h} not up` : !h ? `${p} ${w} won` : `${p} ${h + w} (${h} not up + ${w} won)`; };
  function balance(o) {
    const b = data && (data.offices || {})[o];
    if (!b) return [];
    const s = b.seats, D = s.D.held + s.D.won, R = s.R.held + s.R.won;
    const nums = el("div", "el-nums");
    nums.append(el("b", "D", seatText("D", s)), el("span", "muted small", o === "G" ? `${b.called} of ${b.up} governors called` :
      `${b.control.D} for control · ${b.called} of ${b.up} called${b.winner ? ` · ${PARTY[b.winner]} control` : ""}`), el("b", "R", seatText("R", s)));
    const bar = el("div", "el-bar");
    const seg = (w, c) => { const i = el("i", c); i.style.width = `${(100 * w / b.total).toFixed(3)}%`; return i; };
    bar.append(seg(D, "f-dem"), seg(s.D.lead, "f-dem-lead"), seg(s.I.held + s.I.won + s.I.lead, "f-ind"), seg(b.out, "f-none"), seg(s.R.lead, "f-rep-lead"), seg(R, "f-rep"));
    if (o !== "G") { const t = el("span", "el-tick"); t.style.left = `${100 * b.control.D / b.total}%`; bar.append(t); }
    const out = [nums, bar];
    if (o === "S") out.push(el("p", "el-note muted small", "D includes 2 independents who caucus with Democrats" + (s.I.won || s.I.lead ? "; purple = independents this year" : "")));
    return out;
  }

  // ---- the pre-show: while the wall is off on election night, the player says when, what's next, and opens the map ----
  const preshow = document.getElementById("preshow");
  function nextCloses(now) {   // the next two poll-closing times and their states, from the races' close times
    const by = new Map(), seen = new Set();
    const rs = ((data && data.races) || []).filter((r) => r.closes && r.closes * 1000 > now).sort((a, b) => a.closes - b.closes);
    for (const r of rs) {   // a state split across two closing times is listed at its first
      if (seen.has(r.state)) continue;
      seen.add(r.state);
      const k = r.closes * 1000;
      if (!by.has(k)) by.set(k, new Set());
      by.get(k).add(r.state);
    }
    return [...by.entries()].slice(0, 2);
  }
  function countdown() {
    const left = Math.max(0, COVERAGE - Date.now()), h = Math.floor(left / 3600000), m = Math.floor(left / 60000) % 60, sec = Math.floor(left / 1000) % 60;
    return h ? `in ${h} h ${String(m).padStart(2, "0")} m` : `in ${m}:${String(sec).padStart(2, "0")}`;
  }
  function renderPreshow() {
    if (!preshow) return;
    const now = Date.now();
    if (!data || now > ELECTION_UNTIL) { preshow.hidden = true; return; }
    const before = now < COVERAGE;
    const nodes = [el("p", "ps-kicker", "Election night"),
      el("h2", "ps-title", before ? `Coverage starts ${etTime(COVERAGE)}` : "Results are coming in. The TV feed is back shortly.")];
    if (before) nodes.push(el("p", "ps-count", countdown()));
    const nx = nextCloses(now);
    if (nx.length) {
      const p = el("p", "ps-next");
      p.append("Next polls close: ");
      nx.forEach(([t, sts], i) => { if (i) p.append(" · "); p.append(el("b", null, etTime(t)), ` ${[...sts].sort().join(", ")}`); });
      nodes.push(p);
    }
    const bal = el("div", "ps-bal"); bal.append(...balance("S")); nodes.push(bal);
    const go = el("button", "ps-open", "🗺 Open the election map"); go.type = "button";
    go.onclick = openPanel;
    nodes.push(go);
    preshow.replaceChildren(...nodes);
    preshow.hidden = false;
  }
  setInterval(() => {   // only the countdown ticks (the rest redraws with the map), so a tap on the button never lands on a replaced node
    const c = preshow && !preshow.closest("[hidden]") && preshow.querySelector(".ps-count");
    if (!c) return;
    if (Date.now() >= COVERAGE) { renderPreshow(); return; }
    c.textContent = countdown();
  }, 1000);

  function tipAt(st, e) {
    if (!data || e.pointerType === "touch") return;
    const box = root.querySelector(".el-map"), tip = box.querySelector(".el-tip"), r = box.getBoundingClientRect();
    tip.textContent = stateSummary(st);
    tip.hidden = false;
    tip.style.left = `${Math.min(r.width - 10, Math.max(10, e.clientX - r.left))}px`;
    tip.style.top = `${e.clientY - r.top}px`;
  }

  async function loadMap() {
    const r = await get("/map");
    if (!r.ok) return r.status;
    data = r.j;
    root.hidden = false;
    document.getElementById("elecBtn")?.removeAttribute("hidden");
    if (!root.querySelector(".el-head")) buildPanel();
    drawMap();
    return 200;
  }

  // ---- the sheet ------------------------------------------------------------------------------------------------------
  const sheetEl = document.getElementById("elecSheet");
  const scrim = document.getElementById("elecScrim");

  function stopTimers() { timers.forEach(clearInterval); timers = []; }

  function closeSheet() {
    stopTimers();
    sheet = null;
    sheetEl.classList.remove("open");
    scrim.classList.remove("open");
    sheetEl.setAttribute("aria-hidden", "true");
    setTimeout(() => { if (!sheet) { sheetEl.hidden = true; scrim.hidden = true; } }, 300);
  }

  function showSheet() {
    sheetEl.hidden = false; scrim.hidden = false;
    sheetEl.setAttribute("aria-hidden", "false");
    requestAnimationFrame(() => { sheetEl.classList.add("open"); scrim.classList.add("open"); });
  }
  scrim.onclick = closeSheet;
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && sheet) closeSheet(); });

  function sheetHead(title, sub) {
    const h = el("div", "es-head");
    const grab = el("div", "es-grab"); grab.setAttribute("aria-hidden", "true");
    const t = el("div", "es-title");
    t.append(el("h2", null, title));
    if (sub) t.append(el("span", "muted small", sub));
    const x = el("button", "es-close", "✕"); x.type = "button"; x.setAttribute("aria-label", "Close"); x.onclick = closeSheet;
    h.append(grab, t, x);
    return h;
  }

  async function openState(st) {
    stopTimers();
    sheet = { kind: "state", st, office: null, charts: {}, open: new Set() };
    sheetEl.replaceChildren(sheetHead(data.states[st] || st, "loading…"));
    showSheet();
    await refreshState(true);
    timers.push(setInterval(() => { if (!document.hidden) refreshState(false); }, STATE_MS));
    timers.push(setInterval(() => { if (!document.hidden) refreshCharts(); }, CHART_MS));
  }

  async function refreshState(first) {
    const s = sheet;
    if (!s || s.kind !== "state") return;
    const r = await get(`/state/${s.st}`);
    if (sheet !== s) return;
    if (!r.ok) { if (first) sheetEl.append(el("p", "muted", (r.j && r.j.error) || "Couldn't load this state.")); return; }
    s.d = r.j;
    const offices = [...new Set(s.d.races.map((x) => x.office))];
    if (!s.office || !offices.includes(s.office)) s.office = offices.includes(office) ? office : offices[0] || "S";
    renderState();
    if (first) refreshCharts();
  }

  function renderState() {
    const s = sheet, d = s.d;
    const keep = sheetEl.scrollTop;
    const offices = ["S", "G", "H"].filter((o) => d.races.some((x) => x.office === o));
    const n = d.races.filter((x) => x.office === "H").length;
    const sub = d.races.length ? `${d.races.length} race${d.races.length > 1 ? "s" : ""} tonight` + (d.error ? ` · ${d.error}` : "") : "no races tonight";
    const nodes = [sheetHead(d.name, sub)];
    const bar = el("div", "es-bar");
    if (offices.length > 1) {
      const seg = el("div", "el-seg");
      for (const o of offices) {
        const b = el("button", "el-segbtn", o === "H" ? `House (${n})` : OFFICE[o]);
        b.type = "button"; b.setAttribute("aria-selected", String(o === s.office));
        b.onclick = () => { s.office = o; renderState(); refreshCharts(); };
        seg.append(b);
      }
      bar.append(seg);
    }
    if (data.member) {
      const sp = el("button", "btn small es-spot", data.owner ? "📺 Spotlight on TV" : "📺 Propose TV spotlight");
      sp.type = "button";
      sp.title = "Show this state's races and prices on the TV's sidebar for 30 s (the stream shows it too)";
      sp.onclick = async () => {
        sp.disabled = true;
        const r = await fetch(API + "/spotlight", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ state: s.st }) });
        let j = {}; try { j = await r.json(); } catch (e) { j = {}; }
        sp.textContent = r.ok ? (j.status === "override" || j.status === "passed" ? "📺 On the TV" : "📺 Vote open") : (j.error || "Couldn't");
        setTimeout(() => { sp.disabled = false; sp.textContent = data.owner ? "📺 Spotlight on TV" : "📺 Propose TV spotlight"; }, 6000);
      };
      bar.append(sp);
    }
    if (bar.childNodes.length) nodes.push(bar);
    const to = turnout && turnout.states && turnout.states[s.st];
    if (to) nodes.push(turnoutBlock(to));
    const local = (localNews || {})[s.st] || [];
    if (data.member && local.length && window.wallPropose) {
      const row = el("div", "es-local");
      row.append(el("span", "muted small", "Local coverage → tile 4"));
      for (const n of local) {
        const b = el("button", "btn small", "📡 " + n.name);
        b.type = "button";
        b.title = "Vote to put this station's live stream on tile 4 (the owner's applies at once)";
        // ponytail: always tile 4 (the election preset's spare slot); use the Tile menu if people want to pick
        b.onclick = () => { window.wallPropose({ op: "tile", i: 3, url: n.url, label: n.name }); b.disabled = true; setTimeout(() => (b.disabled = false), 6000); };
        row.append(b);
      }
      nodes.push(row);
    }
    const races = d.races.filter((x) => x.office === s.office);
    if (!races.length) nodes.push(el("p", "muted", "No races here tonight."));
    for (const r of races) nodes.push(raceCard(r, d.positions));
    sheetEl.replaceChildren(...nodes);
    sheetEl.dispatchEvent(new CustomEvent("es-render", { detail: { st: s.st, office: s.office } }));   // wall-election-live.js adds each race's detail
    sheetEl.scrollTop = keep;
  }

  function raceCard(r, positions) {
    const card = el("section", "es-race" + (r.called ? " called" : ""));
    card.dataset.id = r.id;
    const h = el("div", "es-rh");
    const name = r.office === "H" ? `${r.label} House` : `${OFFICE[r.office]}${r.special ? " (special)" : ""}`;
    h.append(el("h3", null, name));
    const st = el("span", "es-status" + (r.called ? " " + partyCls(r.called) : ""), r.status);
    h.append(st);
    card.append(h);
    if (data.member) {   // this one race on top of the TV's spotlight queue (a vote, or at once for the owner)
      const sp = el("button", "btn small es-spot", "📺 Spotlight");
      sp.type = "button";
      sp.title = "Put this race on top of the TV's spotlight queue (the stream shows it too)";
      sp.onclick = async () => {
        sp.disabled = true;
        const r2 = await fetch(API + "/spotlight", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ state: r.state || sheet.st, race: r.id, label: `${data.states[sheet.st] || sheet.st} ${name}` }) });
        let j = {}; try { j = await r2.json(); } catch (e) { j = {}; }
        sp.textContent = r2.ok ? (j.status === "override" || j.status === "passed" ? "📺 On the TV" : "📺 Vote open") : (j.error || "Couldn't");
        setTimeout(() => { sp.disabled = false; sp.textContent = "📺 Spotlight"; }, 6000);
      };
      h.append(sp);
    }
    const meta = [];
    if (r.closes) meta.push(`polls close ${fmtTime(r.closes)}`);
    if (r.called && r.called_at) meta.push(`called ${fmtTime(r.called_at)}`);
    if (r.flip) meta.push("flip");
    if (meta.length) card.append(el("p", "es-meta muted small", meta.join(" · ")));
    // candidates
    const cands = el("div", "es-cands");
    for (const c of r.cands) {
      const row = el("div", "es-cand");
      const nm = el("span", "es-cn");
      nm.append(el("i", "dot " + partyCls(c.party)), document.createTextNode(c.full || c.name), el("span", "muted small", ` ${c.party}${c.inc ? " · inc." : ""}`));
      if (c.winner) nm.append(el("b", "es-win", " ✓"));
      const barw = el("span", "es-cbar"); const fillb = el("i", "f-" + ({ D: "dem", R: "rep" }[c.party] || "ind")); fillb.style.width = `${c.pct || 0}%`; barw.append(fillb);
      row.append(nm, barw, el("span", "es-cp", r.votes ? pct(c.pct) : ""), el("span", "es-cv muted small", r.votes ? (c.votes || 0).toLocaleString() : ""));
      cands.append(row);
    }
    if (!r.cands.length) cands.append(el("p", "muted small", "No candidates listed yet."));
    else if (!r.votes && !r.called) { cands.classList.add("no-votes"); cands.append(el("p", "es-novotes muted small", "No votes yet" + (r.closes ? ` · polls close ${fmtTime(r.closes)}` : ""))); }
    card.append(cands);
    if (r.votes) card.append(el("p", "muted small", `${Math.round(r.pct)}% of the expected vote in · ${(r.votes || 0).toLocaleString()} votes · ${r.caller ? "call: " + r.caller : "not called"}`));
    // markets
    if (r.markets.length) card.append(marketTable(r.markets));
    else card.append(el("p", "muted small", "No Kalshi market for this race."));
    // chart
    if (r.chart.length) {
      const ch = el("div", "es-chart");
      ch.dataset.key = r.id;
      const auto = r.office !== "H" || sheet.open.has(r.id);
      if (auto) { sheet.open.add(r.id); drawChart(ch, sheet.charts[r.id]); }
      else {
        const b = el("button", "btn small ghost", "Show price chart"); b.type = "button";
        b.onclick = () => { sheet.open.add(r.id); drawChart(ch, null); loadChart(r.id); };
        ch.append(b);
      }
      card.append(ch);
    }
    // members' positions (the server only sends these to members)
    const mine = (positions || []).filter((p) => p.race === r.id || (p.race || "").replace("-special", "") === r.id);
    if (mine.length) card.append(posList(mine));
    return card;
  }

  function marketTable(rows) {
    const t = el("table", "es-mk");
    const hd = el("tr");
    for (const [txt, cls] of [["Kalshi", ""], ["bid / ask", "n"], ["last", "n"], ["implied", "n"], ["24 h", "n"]]) hd.append(el("th", cls, txt));
    t.append(el("thead")); t.tHead.append(hd);
    const tb = el("tbody");
    const evs = [...new Set(rows.map((m) => m.event))];
    let ev = null;
    for (const m of rows) {
      if (evs.length > 1 && m.event !== ev) {   // two markets on one race (e.g. by party and by person): say which is which
        const group = rows.filter((x) => x.event === m.event);
        const vol = group.reduce((a, x) => a + (x.vol24 || 0), 0);
        const hr = el("tr", "evh"), td = el("td", null);
        td.colSpan = 5;
        td.append(el("b", null, m.event), el("span", "muted", ` · ${fmtVol(vol)} traded 24 h${m.event === evs[0] ? " · charted below" : ""}`));
        hr.append(td); tb.append(hr);
      }
      const tr = el("tr", ev && m.event !== ev ? "ev" : "");
      ev = m.event;
      const who = el("td", "who"); who.append(el("i", "dot " + partyCls(m.party)), document.createTextNode(m.who || m.label));
      who.title = m.ticker;
      const chg = el("td", "n " + (m.chg24 == null || Math.abs(m.chg24) < 0.5 ? "" : m.chg24 > 0 ? "up" : "down"),
        m.chg24 == null ? "–" : `${m.chg24 > 0 ? "▲" : m.chg24 < 0 ? "▼" : ""}${Math.abs(m.chg24).toFixed(m.chg24 && Math.abs(m.chg24) < 1 ? 1 : 0)}`);
      tr.append(who, el("td", "n", `${cents(m.bid)} / ${cents(m.ask)}`), el("td", "n", cents(m.last)), el("td", "n prob", pct(m.prob)), chg);
      tb.append(tr);
    }
    t.append(tb);
    const wrap = el("div", "es-mkwrap"); wrap.append(t);
    return wrap;
  }

  const fmtVol = (v) => (v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `${Math.round(v / 1e3)}k` : String(Math.round(v))) + " contracts";

  function posList(rows) {
    const box = el("div", "es-pos");
    box.append(el("p", "es-pos-h small", "Members' positions"));
    for (const p of rows) {
      const line = el("div", "es-prow " + (p.dir || "flat"));
      const what = el("span", "w");
      what.append(el("b", null, p.who), document.createTextNode(" "), el("b", "side " + (p.side || "").toLowerCase(), p.side), document.createTextNode(" " + p.label));
      line.append(what, el("span", "n", `${cents(p.entry)} → ${cents(p.mark)}`),
        el("span", "n pc", p.pct == null ? "" : `${p.dir === "down" ? "▼" : p.dir === "up" ? "▲" : "·"} ${Math.abs(p.pct)}%`), el("span", "n muted", p.size || ""));
      if (p.highlight) line.prepend(el("b", "hl " + (p.won ? "won" : "lost"), p.highlight));
      box.append(line);
    }
    return box;
  }

  // ---- charts ---------------------------------------------------------------------------------------------------------
  async function loadChart(key) {
    const s = sheet;
    const r = await get(`/chart/${encodeURIComponent(key)}`);
    if (sheet !== s) return;
    s.charts[key] = r.ok ? r.j : { lines: [], marks: [], error: (r.j && r.j.error) || "Chart unavailable." };
    const box = sheetEl.querySelector(`.es-chart[data-key="${CSS.escape(key)}"]`);
    if (box) drawChart(box, s.charts[key]);
  }

  function refreshCharts() {
    const s = sheet;
    if (!s) return;
    sheetEl.querySelectorAll(".es-chart").forEach((box) => { if (s.open.has(box.dataset.key)) loadChart(box.dataset.key); });
  }

  const COLORS = { D: "var(--e-dem)", R: "var(--e-rep)", I: "var(--e-ind)" };
  const ALT = ["var(--gold)", "var(--accent2)", "var(--green)", "var(--muted)"];

  function drawChart(box, c) {
    box.replaceChildren();
    if (!c) { box.append(el("div", "es-chart-ph muted small", "Loading chart…")); return; }
    if (!c.lines || !c.lines.length || c.lines.every((l) => !l.older.length && !l.today.length)) {
      box.append(el("div", "es-chart-ph muted small", c.error || "No price history yet."));
      return;
    }
    const W = phone() ? 420 : 640, H = phone() ? 190 : 210, L = 34, Rm = 40, T = 10, B = 24;
    const now = c.now, split = c.split;
    const t0 = Math.min(...c.lines.map((l) => (l.older[0] || l.today[0] || [split])[0]));
    const xs = L + (W - L - Rm) * 0.36;   // the older days take the left 36 %, today's session the rest
    const x = (t) => (t < split ? L + (xs - L) * Math.max(0, (t - t0) / Math.max(1, split - t0)) : xs + (W - Rm - xs) * Math.min(1, (t - split) / Math.max(1, now - split)));
    const y = (p) => T + (H - T - B) * (1 - p / 100);
    const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, class: "es-svg", role: "img", "aria-label": "Implied probability over time" });
    for (const g of [0, 25, 50, 75, 100]) {
      svg.append(svgEl("line", { x1: L, x2: W - Rm, y1: y(g), y2: y(g), class: g === 50 ? "g mid" : "g" }));
      const tx = svgEl("text", { x: L - 6, y: y(g) + 3, class: "ax", "text-anchor": "end" }); tx.textContent = g + "%"; svg.append(tx);
    }
    svg.append(svgEl("line", { x1: xs, x2: xs, y1: T, y2: H - B, class: "split" }));
    const lab = (tx, t, anchor) => { const e = svgEl("text", { x: tx, y: H - 8, class: "ax", "text-anchor": anchor || "middle" }); e.textContent = t; svg.append(e); };
    // day ticks on the older side, hour ticks on today's
    const day = new Date(t0 * 1000); day.setHours(0, 0, 0, 0);
    for (day.setDate(day.getDate() + 1); day.getTime() / 1000 < split - 3 * 3600; day.setDate(day.getDate() + 1)) {   // by date: DST-safe
      const d = day.getTime() / 1000;
      svg.append(svgEl("line", { x1: x(d), x2: x(d), y1: H - B, y2: H - B + 3, class: "g" }));
      lab(x(d), day.toLocaleDateString([], { weekday: "short" }));
    }
    const span = now - split, step = span > 8 * 3600 ? 3 * 3600 : span > 4 * 3600 ? 2 * 3600 : 3600;
    for (let h = Math.ceil(split / step) * step; h < now - step / 3; h += step) {
      svg.append(svgEl("line", { x1: x(h), x2: x(h), y1: H - B, y2: H - B + 3, class: "g" }));
      lab(x(h), new Date(h * 1000).toLocaleTimeString([], { hour: "numeric" }));
    }
    const lt = svgEl("text", { x: xs + 4, y: T + 9, class: "mk" }); lt.textContent = "last 12 h →"; svg.append(lt);
    for (const m of c.marks || []) {
      if (m.t < t0 || m.t > now + 3600) continue;
      const mx = x(Math.min(m.t, now));
      svg.append(svgEl("line", { x1: mx, x2: mx, y1: T, y2: H - B, class: "mark" + (m.label.startsWith("called") ? " call" : "") }));
      const e = svgEl("text", { x: mx + 3, y: T + 9, class: "mk" }); e.textContent = m.label; svg.append(e);
    }
    const ends = [];
    c.lines.forEach((l, i) => {
      const pts = [...l.older, ...l.today];
      if (pts.length < 1) return;
      const col = COLORS[l.party] && !c.lines.slice(0, i).some((o) => o.party === l.party) ? COLORS[l.party] : ALT[i % ALT.length];
      svg.append(svgEl("polyline", { points: pts.map(([t, p]) => `${x(t).toFixed(1)},${y(p).toFixed(1)}`).join(" "), class: "ln", style: `stroke:${col}` }));
      const last = pts[pts.length - 1];
      ends.push({ y: y(last[1]), col, text: `${Math.round(last[1])}%`, who: l.who });
    });
    ends.sort((a, b) => a.y - b.y);
    for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 11) ends[i].y = ends[i - 1].y + 11;   // labels don't overlap
    for (const e of ends) { const t = svgEl("text", { x: W - Rm + 4, y: e.y + 3, class: "end", style: `fill:${e.col}` }); t.textContent = e.text; svg.append(t); }
    box.append(svg);
    const key = el("div", "es-key small");
    const ev = c.lines[0] && c.lines[0].ticker ? c.lines[0].ticker.replace(/-[^-]+$/, "") : "";
    if (ev) key.append(el("b", "es-key-ev", ev));
    c.lines.forEach((l, i) => {
      const col = COLORS[l.party] && !c.lines.slice(0, i).some((o) => o.party === l.party) ? COLORS[l.party] : ALT[i % ALT.length];
      const k = el("span"); const sw = el("i"); sw.style.background = col;
      const row = tableRow(l.ticker);   // the 24 h figure the table shows for this very market
      const chg = row ? row.chg24 : l.chg24;
      k.append(sw, document.createTextNode(`${l.who} ${l.now == null ? "" : Math.round(l.now) + "%"}${chg == null ? "" : ` (${chg >= 0 ? "▲" : "▼"}${Math.abs(chg).toFixed(0)} 24 h)`}`));
      key.append(k);
    });
    box.append(key);
  }

  function tableRow(ticker) {
    const d = sheet && sheet.d;
    if (!d || !ticker) return null;
    const all = d.races ? d.races.flatMap((r) => r.markets || []) : d.markets || [];
    return all.find((m) => m.ticker === ticker) || null;
  }

  // ---- chamber control ------------------------------------------------------------------------------------------------
  async function openControl(o) {
    stopTimers();
    sheet = { kind: "control", o, charts: {}, open: new Set([`CTRL-${o}`]) };
    sheetEl.replaceChildren(sheetHead(`Control of the ${OFFICE[o]}`, "loading…"));
    showSheet();
    const load = async () => {
      const s = sheet;
      const r = await get(`/control/${o}`);
      if (sheet !== s || !r.ok) return;
      s.d = r.j;
      const keep = sheetEl.scrollTop;
      const b = r.j.balance;
      const nodes = [sheetHead(`Control of the ${OFFICE[o]}`, b ? `${b.control.D} seats for control · ${b.called} of ${b.up} races called` : "")];
      const card = el("section", "es-race");
      if (b) {
        const s2 = b.seats;
        const p = el("div", "el-nums");
        p.append(el("b", "D", `Dem ${s2.D.held + s2.D.won}`), el("span", "muted small", `leading +${s2.D.lead} · +${s2.R.lead}${b.winner ? ` · ✓ ${PARTY[b.winner]} control` : ""}`), el("b", "R", `${s2.R.held + s2.R.won} Rep`));
        card.append(p);
      }
      card.append(r.j.markets.length ? marketTable(r.j.markets) : el("p", "muted small", r.j.error || "No control market."));
      const ch = el("div", "es-chart"); ch.dataset.key = `CTRL-${o}`; drawChart(ch, s.charts[`CTRL-${o}`]); card.append(ch);
      if (r.j.positions && r.j.positions.length) card.append(posList(r.j.positions));
      nodes.push(card);
      sheetEl.replaceChildren(...nodes);
      sheetEl.scrollTop = keep;
      if (!s.charts[`CTRL-${o}`]) loadChart(`CTRL-${o}`);
    };
    await load();
    timers.push(setInterval(() => { if (!document.hidden) load(); }, STATE_MS));
    timers.push(setInterval(() => { if (!document.hidden) refreshCharts(); }, CHART_MS));
  }

  // ---- phones: the panel itself opens as a sheet from the bar's 🗺 button --------------------------------------------
  const btn = document.getElementById("elecBtn");
  function openPanel(toggle) {
    root.hidden = false;
    if (phone()) {
      const on = toggle === true ? !root.classList.contains("as-sheet") : true;
      root.classList.toggle("as-sheet", on); document.body.classList.toggle("elec-on", on);
      if (on) root.scrollTop = 0;
    } else root.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  if (btn) btn.onclick = () => openPanel(true);
  root.addEventListener("click", (e) => { if (e.target === root && root.classList.contains("as-sheet")) { root.classList.remove("as-sheet"); document.body.classList.remove("elec-on"); } });

  // ---- start: when the page has a viewer (member or guest) -------------------------------------------------------------
  // ---- start: once wall.js says someone (member or guest) is in — never before, so nothing here 401s ------------------
  let started = false;
  async function start() {
    if (started) return;
    started = true;
    if (Date.now() < ELECTION_UNTIL) {   // election night: the button and a placeholder at once, no late layout jump
      btn?.removeAttribute("hidden");
      root.hidden = false;
      if (!root.firstChild) root.append(el("p", "el-loading muted", "Loading the election map…"));
    }
    const st = await loadMap();
    if (st === 401 || st === 403) return;   // the session ended: wall.js shows the join screen; no retry loop
    if (st !== 200 && !data) root.querySelector(".el-loading")?.replaceChildren("Election results aren't loading right now — trying again shortly.");
    fetch(API + "/shapes", { credentials: "same-origin" }).then((r) => (r.ok ? r.json() : null)).then((s) => { if (s) { shapes = s; drawMap(); } }).catch(() => {});
    setInterval(() => { if (!document.hidden) loadMap(); }, MAP_MS);
  }
  if (window.__wallMe) start();
  else window.addEventListener("wall:me", start, { once: true });
  window.__wallElection = { openState, openControl };   // renders / tests
})();
