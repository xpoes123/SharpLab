// The watch page's Bets panel (members only): opted-in members' open Kalshi positions on the games on the wall and the
// other live games, delayed to *this viewer's* picture. The page's player already knows where its picture is
// (window.__wallSync: the stream's program-date-time and its offset to the server clock, from wall.js); the server
// builds every price as of (that instant − the tile's delay), so nothing here is ahead of the video.
// Kept apart from wall.js on purpose: it only reads window.__wallSync and owns #betsPanel.
(() => {
  "use strict";
  const root = document.getElementById("betsPanel");
  if (!root) return;
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const POLL_MS = 5000;
  let me = null, last = null, prevMarks = new Map(), busy = false;

  async function api(path, body) {
    const r = await fetch("/api/v1/wall/bets" + path, body ? { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : { credentials: "same-origin" });
    let j = null;
    try { j = await r.json(); } catch (e) { j = null; }
    return { ok: r.ok, status: r.status, j };
  }

  // where this viewer's picture is, in the server's (= the TV's) clock; null = not playing yet (the server assumes a synced viewer)
  function pictureMs() {
    const s = window.__wallSync;
    if (!s || s.pdt == null || s.pdtOff == null || s.err == null) return null;
    return Math.round(s.pdt - s.pdtOff);
  }

  const cents = (v) => (v == null ? "–" : Math.round(v) + "¢");
  const book = (r) => (r.bid != null || r.ask != null ? `${cents(r.bid)} / ${cents(r.ask)}` : r.last != null ? `last ${cents(r.last)}` : "");
  const srcText = (g) => `Delayed ${Math.round(g.delay)} s` + (g.src === "auto" ? " (auto)" : g.src === "manual" ? " (manual)" : "");

  function spark(vals) {
    const W = 72, H = 22, svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("class", "spark"); svg.setAttribute("aria-hidden", "true");
    if (!vals || vals.length < 2) return svg;
    const lo = Math.min(...vals), hi = Math.max(...vals), span = Math.max(1, hi - lo);
    const pts = vals.map((v, i) => `${(i / (vals.length - 1) * (W - 2) + 1).toFixed(1)},${(H - 2 - (v - lo) / span * (H - 4)).toFixed(1)}`);
    const pl = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    pl.setAttribute("points", pts.join(" ")); pl.setAttribute("fill", "none"); pl.setAttribute("stroke-width", "1.5");
    pl.setAttribute("class", vals[vals.length - 1] >= vals[0] ? "up" : "down");
    svg.append(pl);
    return svg;
  }

  function row(r) {
    const tr = el("div", "bet-row " + (r.dir || "flat"));
    const key = r.uid + "|" + r.ticker;
    const before = prevMarks.get(key);
    if (before != null && r.mark != null && before !== r.mark) {   // a gentle tint that fades, never a blink
      tr.classList.add(r.mark > before ? "moved-up" : "moved-down");
      setTimeout(() => tr.classList.remove("moved-up", "moved-down"), 4000);
    }
    prevMarks.set(key, r.mark);
    const who = el("span", "b-who", r.who);
    if (me && r.uid === me.uid) who.classList.add("you");
    const what = el("span", "b-what");
    what.append(el("b", "b-side " + r.side.toLowerCase(), r.side), document.createTextNode(" " + r.label));
    const px = el("span", "b-px", `${cents(r.entry)} → ${cents(r.mark)}`);
    px.title = "Entry → now";
    const bk = el("span", "b-book muted", book(r));
    bk.title = "Yes bid / ask";
    const pct = el("span", "b-pct", r.pct == null ? "" : `${r.dir === "down" ? "▼" : r.dir === "up" ? "▲" : "·"} ${Math.abs(r.pct)}%`);
    const size = el("span", "b-size muted", r.size + (r.pnl != null ? ` · ${r.pnl >= 0 ? "+" : "−"}$${Math.abs(r.pnl).toFixed(2)}` : ""));
    tr.append(who, what, px, spark(r.spark), pct, bk, size);
    return tr;
  }

  function tileControls(g) {
    const box = el("div", "bet-sync");
    const send = async (body, btn) => {
      btn.disabled = true;
      const r = await api("/sync", { url: g.url, ...body });
      btn.disabled = false;
      if (!r.ok) note((r.j && r.j.error) || "Couldn't change the delay.");
      refresh();
    };
    const b = (label, title, body) => { const x = el("button", "btn small", label); x.type = "button"; x.title = title; x.onclick = () => send(typeof body === "function" ? body() : body, x); return x; };
    box.append(b("− 5 s", "Show bets 5 s sooner on this tile", { nudge: -1 }), b("+ 5 s", "Show bets 5 s later on this tile", { nudge: 1 }),
      b("Auto", "Set from the scorebug", { auto: true }),
      b("Score just changed", "Tap when the score changes on this tile",
        () => ({ tap: pictureMs() || Date.now() })));
    return box;
  }

  function note(text) { const n = root.querySelector(".bet-note"); if (n) { n.textContent = text; setTimeout(() => { if (n.textContent === text) n.textContent = ""; }, 6000); } }

  function render() {
    const d = last;
    root.replaceChildren();
    const head = el("div", "bets-head");
    head.append(el("h3", null, "Bets"));
    const opt = el("label", "opt");
    const cb = el("input"); cb.type = "checkbox"; cb.checked = !!(me && me.share);
    cb.onchange = async () => { const r = await api("/me", { share: cb.checked, exact: cb.checked && ex.checked }); if (r.ok) { me = { ...me, ...r.j }; refresh(); } };
    opt.append(cb, document.createTextNode(" Share my positions"));
    const exl = el("label", "opt");
    const ex = el("input"); ex.type = "checkbox"; ex.checked = !!(me && me.exact); ex.disabled = !(me && me.share);
    ex.onchange = async () => { const r = await api("/me", { share: true, exact: ex.checked }); if (r.ok) { me = { ...me, ...r.j }; refresh(); } };
    exl.append(ex, document.createTextNode(" Exact sizes and $"));
    if (!(me && me.guest)) head.append(opt, exl);
    root.append(head);
    const sub = el("p", "muted small", "Shared positions show here and on the TV. Sizes are bucketed and P&L is in % unless exact is on.");
    if (me && me.share && !me.linked) sub.textContent = "Kalshi isn't linked. Run /kalshi link in Discord.";
    root.append(sub, el("p", "bet-note small"));
    if (d && d.owner) {
      const ctl = el("div", "bet-owner");
      const t = el("label", "opt"), sh = el("input");
      sh.type = "checkbox"; sh.checked = !!d.show;
      sh.onchange = async () => { await api("/sync", { show: sh.checked }); refresh(); };
      t.append(sh, document.createTextNode(" Show bets on the TV overlay"));
      ctl.append(t, el("span", "muted small", `Default delay ${Math.round(d.default)} s`));
      root.append(ctl);
    }
    if (!d) { root.append(el("p", "muted", "Loading…")); return; }
    if (d.error) root.append(el("p", "muted small", d.error));
    const games = d.games || [], elec = d.election || [];
    if (elec.length) {   // midterm positions: live (results aren't a picture, so no delay), with the race's status
      const box = el("div", "bet-game on-wall");
      const gh = el("div", "bet-game-head");
      gh.append(el("b", null, "Election night"), el("span", "muted small", "Kalshi, live (no delay)"));
      box.append(gh);
      elec.forEach((r) => {
        const tr = row(r);
        if (r.highlight) { tr.classList.add(r.won ? "called-won" : "called-lost"); tr.prepend(el("b", "b-called", r.highlight)); }
        if (r.status) tr.append(el("span", "b-status muted small", r.status));
        box.append(tr);
      });
      root.append(box);
    }
    if (!games.length && !elec.length) root.append(el("p", "muted small", "No live games on the wall."));
    games.forEach((g) => {
      const box = el("div", "bet-game" + (g.tile != null ? " on-wall" : ""));
      const gh = el("div", "bet-game-head");
      gh.append(el("b", null, g.label), el("span", "muted small", (g.tile != null ? `Tile ${g.tile + 1}. ` : "") + srcText(g)));
      box.append(gh);
      if (!g.rows.length) box.append(el("p", "muted small", "No shared positions."));
      g.rows.forEach((r) => box.append(row(r)));
      if (d.owner && g.tile != null && g.url) box.append(tileControls(g));
      root.append(box);
    });
  }

  async function refresh() {
    if (busy) return;
    busy = true;
    try {
      const at = pictureMs();
      const r = await api(at ? `?at=${at}` : "");
      if (r.ok) { last = r.j; render(); }
    } finally { busy = false; }
  }

  // Starts once wall.js knows who's watching (window.__wallMe / "wall:me"). Friends (the keyed link's guests) and
  // public viewers get the panel read-only: no Kalshi key here to share.
  async function start(who) {
    if (!who || me) return;
    const r = await api("/me");
    if (!r.ok) return;
    me = { ...r.j, uid: who.id || "-" };
    root.hidden = false;
    render();
    refresh();
    setInterval(() => { if (!document.hidden) refresh(); }, POLL_MS);
  }
  if (window.__wallMe) start(window.__wallMe);
  else window.addEventListener("wall:me", (e) => start(e.detail), { once: true });
})();
