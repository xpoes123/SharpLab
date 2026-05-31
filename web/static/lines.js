// SharpLab HQ — betting lines, per-book open/close, and movement graphs.

const app = document.getElementById("app");
const SPORT = { nba: "🏀", mlb: "⚾" };
const BOOK_PREF = ["draftkings", "fanduel", "betmgm", "caesars", "fanatics"];
const LM = {};            // game_id -> line-movement payload
let sport = "all";

const am = (v) => (v == null ? "—" : (v > 0 ? "+" : "") + v);
const mlPair = (o) => (o && o.ml_home != null ? `${am(o.ml_away)} / ${am(o.ml_home)}` : "—");
const fmtML = (src) => (src.ml_home == null ? "—" : `${am(src.ml_away)} / ${am(src.ml_home)}`);
const BOOK_SHORT = { draftkings: "DK", fanduel: "FD", betmgm: "MGM", caesars: "CZR", williamhill_us: "CZR", fanatics: "FAN", betrivers: "BR", bovada: "BOV", betonlineag: "BOL", lowvig: "LV", mybookieag: "MB", betus: "BUS", kalshi: "KAL", polymarket: "POLY" };
const shortBook = (b) => BOOK_SHORT[b] || (b || "").slice(0, 3).toUpperCase();

function consensus(odds) {
  for (const b of BOOK_PREF) if (odds[b]) return [b, odds[b]];
  const k = Object.keys(odds)[0];
  return k ? [k, odds[k]] : [null, null];
}
// Best price available across books for each moneyline side (higher American
// odds = better payout for the bettor). Returns {away:{v,book}, home:{v,book}}.
function bestML(odds) {
  let away = null, home = null;
  for (const [b, o] of Object.entries(odds || {})) {
    if (o.ml_away != null && (!away || o.ml_away > away.v)) away = { v: o.ml_away, book: b };
    if (o.ml_home != null && (!home || o.ml_home > home.v)) home = { v: o.ml_home, book: b };
  }
  return { away, home };
}
const mlCell = (best) => (best == null ? "—" : `${am(best.v)}<sup class="bk">${shortBook(best.book)}</sup>`);
// How trustworthy the close is: a real tip-off capture vs a stale last-poll proxy.
function freshnessTag(g) {
  if (g.close_lead_min == null) return "";
  const m = g.close_lead_min;
  if (g.close_is_real) {
    const ok = m >= 0 && m <= 5;
    return `<div style="font-size:11px;margin-top:1px"><span class="${ok ? "pos" : "muted"}">●</span> <span class="muted">close captured ${m <= 0 ? "right at" : m + " min before"} tip</span></div>`;
  }
  return `<div style="font-size:11px;margin-top:1px"><span class="neg">●</span> <span class="muted">proxy — last line ${m} min before tip</span></div>`;
}

// Freshness of the live line on an upcoming card: how long ago we last polled.
function liveFreshness(g) {
  const caps = Object.values(g.odds || {}).map((p) => p && p.captured_at).filter(Boolean);
  if (!caps.length) return "";
  const newest = Math.max(...caps.map((c) => new Date(c).getTime()));
  if (!isFinite(newest)) return "";
  const mins = Math.round((Date.now() - newest) / 60000);
  const txt = mins <= 1 ? "just now" : mins < 60 ? `${mins} min ago` : `${Math.floor(mins / 60)}h ago`;
  return ` <span class="muted" style="font-size:11px">· updated ${txt}</span>`;
}

// No-vig consensus home win% open → close (the honest closing-line value).
function fairLine(g) {
  if (!g.close_fair) return "";
  const oh = g.open_fair ? g.open_fair.home : null;
  const ch = g.close_fair.home;
  const arrow = oh != null && Math.abs(ch - oh) >= 0.5
    ? ` <span class="${ch > oh ? "neg" : "pos"}">${ch > oh ? "▲" : "▼"}${Math.abs(ch - oh).toFixed(1)}</span>` : "";
  const from = oh != null ? `${oh}% → ` : "";
  return `<div class="muted" style="font-size:12px;margin-top:2px">fair (no-vig) ${g.home_team}: ${from}<span style="color:var(--fg)">${ch}%</span>${arrow}</div>`;
}
function dateKey(iso) {
  try { return new Date(iso).toLocaleDateString("en-US", { weekday: "long", month: "short", day: "numeric", timeZone: "America/New_York" }); } catch { return ""; }
}
function dateTime(iso) {
  try { return new Date(iso).toLocaleString("en-US", { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: "America/New_York", timeZoneName: "short" }); } catch { return ""; }
}

async function load() {
  let slate, results;
  try {
    [slate, results] = await Promise.all([
      fetch(`/api/v1/dashboard/slate?sport=${sport}`).then((r) => r.json()),
      fetch(`/api/v1/dashboard/results?sport=${sport}&limit=25`).then((r) => r.json()),
    ]);
  } catch {
    app.innerHTML = `<div class="hero"><p class="muted">Couldn't load lines.</p></div>`; return;
  }
  render((slate.games || []).filter((g) => g.status !== "final"), results.results || []);
}

function render(upcoming, results) {
  let html = `<div style="display:flex;align-items:center;gap:12px;margin-bottom:8px">
    <h2 style="margin:0">Lines</h2>
    <select id="sportSel" class="lbselect">
      <option value="all"${sport === "all" ? " selected" : ""}>All sports</option>
      <option value="mlb"${sport === "mlb" ? " selected" : ""}>⚾ Baseball</option>
      <option value="nba"${sport === "nba" ? " selected" : ""}>🏀 Basketball</option>
    </select></div>`;

  // ── Upcoming, grouped by date ──
  html += `<h2>🎯 Upcoming</h2><div class="card" style="padding:0">`;
  if (!upcoming.length) {
    html += `<div class="muted" style="padding:18px">No upcoming games on the board.</div>`;
  } else {
    let lastDate = "";
    html += upcoming.map((g) => {
      const dk = dateKey(g.start_time);
      const hdr = dk !== lastDate ? `<div class="date-hd">${dk}</div>` : ""; lastDate = dk;
      const [, src] = consensus(g.odds || {});  // consensus only for spread/total + fav
      const best = bestML(g.odds || {});
      const em = SPORT[g.sport] || "🏟️";
      const homeFav = src && src.spread != null && src.spread < 0;
      const awayFav = src && src.spread != null && src.spread > 0;
      const away = `<span class="${awayFav ? "fav" : homeFav ? "dog" : ""}">${g.away_team}</span>`;
      const home = `<span class="${homeFav ? "fav" : awayFav ? "dog" : ""}">${g.home_team}</span>`;
      let lineHtml = `<span class="muted">No lines yet.</span>`;
      if (src || best.away || best.home) {
        const spr = src && src.spread != null ? `<span class="spread">${src.spread < 0 ? g.home_team : g.away_team} −${Math.abs(src.spread)}</span> · ` : "";
        const ml = (best.away || best.home) ? `${mlCell(best.away)} / ${mlCell(best.home)}` : (src ? fmtML(src) : "—");
        const ou = src && src.total != null ? ` · <span class="muted">O/U ${src.total}</span>` : "";
        lineHtml = `${spr}<span class="muted">ML</span> ${ml}${ou} <span class="muted" style="font-size:11px">best price</span>`;
      }
      const fair = g.fair ? `<div class="muted" style="font-size:12px;margin-top:2px">fair (no-vig): <span style="color:var(--fg)">${g.home_team} ${g.fair.home}%</span> · ${g.away_team} ${g.fair.away}% <span style="font-size:10px">${g.fair.books} books</span></div>` : "";
      return `${hdr}<div class="gamecard">
        <div>${em} ${away} @ ${home} <span class="muted" style="font-size:12px">· ${dateTime(g.start_time)}</span>${liveFreshness(g)}</div>
        <div style="font-size:13px;margin-top:4px">${lineHtml}</div>${fair}
        ${movementDetails(g.game_id, "📈 pre-match line movement")}</div>`;
    }).join("");
  }
  html += `</div>`;

  // ── Results ──
  html += `<h2>📉 Recent Results — Open → Close</h2><div class="card" style="padding:0">`;
  if (!results.length) {
    html += `<div class="muted" style="padding:18px">No completed games yet.</div>`;
  } else {
    html += results.map((g) => {
      const em = SPORT[g.sport] || "";
      const homeWin = g.home_score != null && g.home_score > g.away_score;
      const score = `<span class="${homeWin ? "" : "fav pos"}">${g.away_score}</span>-<span class="${homeWin ? "fav pos" : ""}">${g.home_score}</span>`;
      let move = "";
      if (g.open && g.close && g.open.ml_home != null && g.close.ml_home != null) {
        const d = g.close.ml_home - g.open.ml_home;
        move = Math.abs(d) < 1 ? "" : ` <span class="${d < 0 ? "pos" : "neg"}">${d < 0 ? "▼" : "▲"}${Math.abs(d)}</span>`;
      }
      const ats = g.home_covered == null ? "" : `<span class="pill pos">${g.home_covered ? g.home_team : g.away_team} ATS ✓</span> `;
      const ou = !g.total_result || g.total_result === "push" ? "" :
        `<span class="pill ${g.total_result}">${g.total_result === "over" ? "Over" : "Under"} ${(g.close && g.close.total != null) ? g.close.total : ""}</span>`;
      const tag = g.close && !g.close_is_real ? ' <span class="muted" style="font-size:10px">(last)</span>' : "";
      return `<div class="gamecard">
        <div>${em} ${g.away_team} @ <strong>${g.home_team}</strong> · final ${score}
          <span class="muted" style="font-size:11px">${dateKey(g.start_time)} · ${g.book || ""}</span> ${ats}${ou}</div>
        <div class="muted" style="font-size:13px;margin-top:4px">ML ${mlPair(g.open)} → <span style="color:var(--fg)">${mlPair(g.close)}</span>${tag}${move}</div>
        ${freshnessTag(g)}
        ${fairLine(g)}
        ${movementDetails(g.game_id, "📊 pre-match open → close (all books)")}</div>`;
    }).join("");
  }
  html += `</div>`;

  app.innerHTML = html;
  document.getElementById("sportSel").addEventListener("change", (e) => { sport = e.target.value; load(); });
  wireMovement();
}

function movementDetails(gid, label) {
  return `<details style="margin-top:6px" data-gid="${gid}">
    <summary class="muted" style="cursor:pointer;font-size:13px">${label}</summary>
    <div class="lm" style="padding:8px 2px"><span class="muted">Loading…</span></div></details>`;
}

function wireMovement() {
  app.querySelectorAll("details[data-gid]").forEach((d) => {
    d.addEventListener("toggle", async function () {
      if (!this.open || this.dataset.loaded) return;
      this.dataset.loaded = "1";
      const gid = this.dataset.gid, box = this.querySelector(".lm");
      try { LM[gid] = await fetch(`/api/v1/dashboard/line-movement/${gid}`).then((r) => r.json()); }
      catch { box.textContent = "Couldn't load movement."; return; }
      buildMovementUI(box, gid);
    });
  });
}

// Distinct, legible line colors for overlaying multiple books.
const LM_COLORS = ["#7aa2f7", "#bb9af7", "#9ece6a", "#e0af68", "#f7768e", "#7dcfff", "#ff9e64", "#73daca", "#2ac3de", "#b4f9f8", "#ff75a0", "#c0caf5"];

function buildMovementUI(box, gid) {
  const lm = LM[gid];
  const sources = [...new Set((lm.snapshots || []).map((s) => s.source))].sort();
  if (!sources.length) { box.innerHTML = `<span class="muted">No line history yet.</span>`; return; }

  // Stable color per book; default to overlaying the two sharpest available.
  const colorOf = {};
  sources.forEach((s, i) => { colorOf[s] = LM_COLORS[i % LM_COLORS.length]; });
  const pref = ["draftkings", "fanduel", "betmgm", "caesars", "kalshi"];
  const def = pref.filter((b) => sources.includes(b)).slice(0, 2);
  const selected = new Set(def.length ? def : sources.slice(0, 2));

  box.innerHTML = `
    <div style="display:flex;gap:8px;margin-bottom:8px;flex-wrap:wrap;align-items:center">
      <select class="metricSel lbselect"><option value="ml_home">Moneyline</option><option value="spread">Spread</option><option value="total">Total</option></select>
      <span class="muted" style="font-size:12px">overlay books — click to toggle:</span>
    </div>
    <div class="bookChips" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px"></div>
    ${bestBoard(lm)}
    <div class="bookTbl" style="margin-bottom:12px"></div>
    <div class="lmchart"></div>
    <div class="lmvals" style="font-size:12px;margin-top:8px;display:flex;gap:16px;flex-wrap:wrap"></div>`;

  const metricSel = box.querySelector(".metricSel"), chips = box.querySelector(".bookChips");
  const redraw = () => { renderBookTable(box, gid, metricSel.value); drawGraph(box, gid, [...selected], colorOf); };

  function renderChips() {
    chips.innerHTML = sources.map((s) => {
      const on = selected.has(s), c = colorOf[s];
      return `<button class="bookchip" data-src="${s}" aria-pressed="${on}"
        style="border-color:${on ? c : "var(--line)"};background:${on ? c + "22" : "transparent"};color:${on ? "var(--fg)" : "var(--muted)"}">
        <span class="dot" style="background:${on ? c : "var(--muted)"}"></span>${s}</button>`;
    }).join("");
    chips.querySelectorAll(".bookchip").forEach((b) => b.addEventListener("click", () => {
      const s = b.dataset.src;
      if (selected.has(s)) { if (selected.size > 1) selected.delete(s); }  // keep at least one
      else selected.add(s);
      renderChips(); redraw();
    }));
  }

  metricSel.addEventListener("change", redraw);
  renderChips();
  redraw();
}

// Best available number for each side across books (the /best-line view), from
// each book's latest snapshot. Shown when you expand a game.
function bestBoard(lm) {
  const grp = bySource(lm);
  const cur = {};
  for (const s in grp) cur[s] = grp[s][grp[s].length - 1];  // latest line per book
  const books = Object.keys(cur);
  if (!books.length) return "";
  let mlH = null, mlA = null, sprH = null, sprA = null, totO = null, totU = null;
  for (const s of books) {
    const p = cur[s];
    if (p.ml_home != null && (!mlH || p.ml_home > mlH.v)) mlH = { v: p.ml_home, b: s };
    if (p.ml_away != null && (!mlA || p.ml_away > mlA.v)) mlA = { v: p.ml_away, b: s };
    if (p.spread != null) {
      if (!sprH || p.spread > sprH.v) sprH = { v: p.spread, b: s };   // most points for home backer
      if (!sprA || p.spread < sprA.v) sprA = { v: p.spread, b: s };   // most points for away backer
    }
    if (p.total != null) {
      if (!totO || p.total < totO.v) totO = { v: p.total, b: s };     // lowest line = best over
      if (!totU || p.total > totU.v) totU = { v: p.total, b: s };     // highest line = best under
    }
  }
  const cell = (label, side) => side == null ? "" :
    `${label} <strong style="color:var(--gold)">${side.val}</strong><sup class="bk">${shortBook(side.b)}</sup>`;
  const rows = [];
  if (mlH || mlA) rows.push(`<div>ML &nbsp;${cell(`<span class="muted">${lm.home_team}</span>`, mlH && { val: am(mlH.v), b: mlH.b })} &nbsp;·&nbsp; ${cell(`<span class="muted">${lm.away_team}</span>`, mlA && { val: am(mlA.v), b: mlA.b })}</div>`);
  if (sprH || sprA) rows.push(`<div>Spread &nbsp;${cell(`<span class="muted">${lm.home_team}</span>`, sprH && { val: (sprH.v > 0 ? "+" : "") + sprH.v, b: sprH.b })} &nbsp;·&nbsp; ${cell(`<span class="muted">${lm.away_team}</span>`, sprA && { val: (-sprA.v > 0 ? "+" : "") + (-sprA.v), b: sprA.b })}</div>`);
  if (totO || totU) rows.push(`<div>Total &nbsp;${cell("Over", totO && { val: totO.v, b: totO.b })} &nbsp;·&nbsp; ${cell("Under", totU && { val: totU.v, b: totU.b })}</div>`);
  if (!rows.length) return "";
  return `<div class="card" style="padding:10px 12px;margin-bottom:12px;font-size:13px;line-height:1.7">
    <div class="muted" style="font-size:11px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px">🏆 Best available across books</div>
    ${rows.join("")}</div>`;
}

function bySource(lm) {
  const m = {};
  for (const s of lm.snapshots || []) (m[s.source] ||= []).push(s);
  for (const k in m) m[k].sort((a, b) => new Date(a.captured_at) - new Date(b.captured_at));
  return m;
}

function renderBookTable(box, gid, metric) {
  const lm = LM[gid], grp = bySource(lm);
  const isFinal = lm.status === "final";   // upcoming → the latest value is "Current", not a close
  const colLabel = isFinal ? "Close" : "Current";
  const fmt = metric === "total" ? (v) => (v == null ? "—" : v) : (v) => am(v);
  const rows = Object.keys(grp).sort().map((src) => {
    const arr = grp[src];
    const open = arr[0][metric];
    const closeObj = (lm.close && lm.close[src]) || arr[arr.length - 1];
    const close = closeObj[metric];
    const real = lm.close && lm.close[src];
    // For an upcoming game the latest poll IS the current line — no "(last)" caveat.
    const tag = (!isFinal || real) ? "" : ` <span class="muted" style="font-size:10px">(last)</span>`;
    let d = (typeof open === "number" && typeof close === "number") ? close - open : null;
    const dStr = d == null ? "—" : (Math.abs(d) < (metric === "total" ? 0.05 : 1) ? `<span class="muted">0</span>`
      : `<span class="${d < 0 ? "pos" : "neg"}">${d > 0 ? "+" : ""}${metric === "total" ? d.toFixed(1) : d}</span>`);
    return `<tr><td><span class="pill">${src}</span></td><td class="num muted">${fmt(open)}</td>
      <td class="num">${fmt(close)}${tag}</td><td class="num">${dStr}</td></tr>`;
  });
  box.querySelector(".bookTbl").innerHTML =
    `<table><thead><tr><th>Book</th><th class="num">Open</th><th class="num">${colLabel}</th><th class="num">Δ</th></tr></thead><tbody>${rows.join("")}</tbody></table>`;
}

function drawGraph(box, gid, books, colorOf) {
  const lm = LM[gid], metric = box.querySelector(".metricSel").value;
  const series = books.map((src) => {
    const snaps = (lm.snapshots || []).filter((s) => s.source === src).sort((a, b) => new Date(a.captured_at) - new Date(b.captured_at));
    const pts = snaps.map((s) => ({ t: new Date(s.captured_at).getTime(), v: s[metric] })).filter((p) => p.v != null);
    const closeVal = lm.close && lm.close[src] ? lm.close[src][metric] : null;
    return { src, color: colorOf[src], pts, closeVal };
  }).filter((s) => s.pts.length);

  box.querySelector(".lmchart").innerHTML = multiLineChart(series, metric);

  const f = metric === "total" ? (v) => v : am;
  box.querySelector(".lmvals").innerHTML = series.length
    ? series.map((s) => {
      const last = s.closeVal != null ? s.closeVal : s.pts[s.pts.length - 1].v;
      const d = s.pts[0].v != null && last != null ? last - s.pts[0].v : null;
      const dStr = d == null || Math.abs(d) < (metric === "total" ? 0.05 : 1) ? "" :
        ` <span class="${d < 0 ? "pos" : "neg"}">(${d > 0 ? "+" : ""}${metric === "total" ? d.toFixed(1) : Math.round(d)})</span>`;
      return `<span style="display:inline-flex;align-items:center;gap:6px">
        <span class="dot" style="background:${s.color}"></span><strong>${s.src}</strong>
        <span class="muted">${f(s.pts[0].v)} → <span style="color:var(--fg)">${f(last)}</span></span>${dStr}</span>`;
    }).join("")
    : `<span class="muted">No data for these books on this metric.</span>`;
}

const METRIC_LABEL = { ml_home: "Home moneyline", spread: "Spread", total: "Total (O/U)" };
function axisTime(t) {
  try { return new Date(t).toLocaleString("en-US", { month: "numeric", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: "America/New_York" }); } catch { return ""; }
}

// Overlay one or more books' movement for a metric on a shared, labeled axis.
// Each series ends with a ringed marker at its closing value so you can see how
// the books moved relative to one another (steam, lead-steam, disagreement).
function multiLineChart(series, metric) {
  const allT = [];
  series.forEach((s) => s.pts.forEach((p) => allT.push(p.t)));
  if (!allT.length) return `<div class="muted" style="padding:16px">Not enough data points yet.</div>`;
  const tmin = Math.min(...allT), tmax = Math.max(...allT);

  // Build drawable series: append the closing value at tmax so the line visibly
  // settles to where it closed (real closes can differ from the last poll).
  const allV = [];
  const draw = series.map((s) => {
    const dpts = s.pts.slice();
    if (s.closeVal != null) dpts.push({ t: tmax, v: s.closeVal, close: true });
    dpts.forEach((p) => allV.push(p.v));
    return { ...s, dpts };
  });
  if (draw.reduce((n, s) => n + s.dpts.length, 0) < 2)
    return `<div class="muted" style="padding:16px">Not enough data points yet.</div>`;

  const W = 760, H = 230, mL = 60, mR = 16, mT = 14, mB = 36;
  const pw = W - mL - mR, ph = H - mT - mB;
  let vmin = Math.min(...allV), vmax = Math.max(...allV);
  const pad = (vmax - vmin) * 0.15 || 1; vmin -= pad; vmax += pad;
  const X = (t) => mL + (tmax > tmin ? (t - tmin) / (tmax - tmin) : 0.5) * pw;
  const Y = (v) => mT + ph - ((v - vmin) / (vmax - vmin)) * ph;
  const fmtV = metric === "total" ? (v) => Math.round(v * 10) / 10 : (v) => am(Math.round(v));

  const yvals = [vmin, (vmin + vmax) / 2, vmax];
  const grid = yvals.map((v) => {
    const y = Y(v);
    return `<line x1="${mL}" y1="${y.toFixed(1)}" x2="${W - mR}" y2="${y.toFixed(1)}" stroke="var(--line)" stroke-width="1"/>`
      + `<text x="${mL - 8}" y="${(y + 4).toFixed(1)}" text-anchor="end" font-size="12" fill="var(--muted)">${fmtV(v)}</text>`;
  }).join("");
  const xlab = [tmin, tmax].map((t, i) =>
    `<text x="${X(t).toFixed(1)}" y="${H - 12}" text-anchor="${i === 0 ? "start" : "end"}" font-size="12" fill="var(--muted)">${axisTime(t)}</text>`
  ).join("");
  const ytitle = `<text x="16" y="${mT + ph / 2}" text-anchor="middle" font-size="12" fill="var(--muted)" transform="rotate(-90 16 ${(mT + ph / 2).toFixed(1)})">${METRIC_LABEL[metric] || metric}</text>`;

  const lines = draw.map((s) => {
    if (!s.dpts.length) return "";
    const poly = s.dpts.map((p) => `${X(p.t).toFixed(1)},${Y(p.v).toFixed(1)}`).join(" ");
    const line = s.dpts.length > 1
      ? `<polyline points="${poly}" fill="none" stroke="${s.color}" stroke-width="2"/>` : "";
    const dots = s.dpts.map((p) => p.close
      ? `<circle cx="${X(p.t).toFixed(1)}" cy="${Y(p.v).toFixed(1)}" r="3.6" fill="${s.color}" stroke="var(--panel2)" stroke-width="1.5"/>`
      : `<circle cx="${X(p.t).toFixed(1)}" cy="${Y(p.v).toFixed(1)}" r="2.3" fill="${s.color}"/>`).join("");
    return line + dots;
  }).join("");

  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block;background:var(--panel2);border-radius:8px">
    ${ytitle}${grid}
    <line x1="${mL}" y1="${mT}" x2="${mL}" y2="${mT + ph}" stroke="var(--line)" stroke-width="1.5"/>
    <line x1="${mL}" y1="${(mT + ph).toFixed(1)}" x2="${W - mR}" y2="${(mT + ph).toFixed(1)}" stroke="var(--line)" stroke-width="1.5"/>
    ${lines}${xlab}</svg>`;
}

load();
