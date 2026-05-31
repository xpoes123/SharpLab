// SharpLab HQ — betting lines, per-book open/close, and movement graphs.

const app = document.getElementById("app");
const SPORT = { nba: "🏀", mlb: "⚾" };
const BOOK_PREF = ["draftkings", "fanduel", "betmgm", "caesars", "fanatics"];
const LM = {};            // game_id -> line-movement payload
let sport = "all";

const am = (v) => (v == null ? "—" : (v > 0 ? "+" : "") + v);
const mlPair = (o) => (o && o.ml_home != null ? `${am(o.ml_away)} / ${am(o.ml_home)}` : "—");
const fmtML = (src) => (src.ml_home == null ? "—" : `${am(src.ml_away)} / ${am(src.ml_home)}`);

function consensus(odds) {
  for (const b of BOOK_PREF) if (odds[b]) return [b, odds[b]];
  const k = Object.keys(odds)[0];
  return k ? [k, odds[k]] : [null, null];
}
function dateKey(iso) {
  try { return new Date(iso).toLocaleDateString("en-US", { weekday: "long", month: "short", day: "numeric", timeZone: "America/New_York" }); } catch { return ""; }
}
function timeOnly(iso) {
  try { return new Date(iso).toLocaleString("en-US", { hour: "numeric", minute: "2-digit", timeZone: "America/New_York", timeZoneName: "short" }); } catch { return ""; }
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
      const [book, src] = consensus(g.odds || {});
      const em = SPORT[g.sport] || "🏟️";
      const homeFav = src && src.spread != null && src.spread < 0;
      const awayFav = src && src.spread != null && src.spread > 0;
      const away = `<span class="${awayFav ? "fav" : homeFav ? "dog" : ""}">${g.away_team}</span>`;
      const home = `<span class="${homeFav ? "fav" : awayFav ? "dog" : ""}">${g.home_team}</span>`;
      let lineHtml = `<span class="muted">No lines yet.</span>`;
      if (src) {
        const spr = src.spread != null ? `<span class="spread">${src.spread < 0 ? g.home_team : g.away_team} −${Math.abs(src.spread)}</span> · ` : "";
        lineHtml = `${spr}<span class="muted">ML ${fmtML(src)}</span> · <span class="muted">O/U ${src.total ?? "—"}</span> <span class="pill">${book}</span>`;
      }
      return `${hdr}<div class="gamecard">
        <div>${em} ${away} @ ${home} <span class="muted" style="font-size:12px">· ${timeOnly(g.start_time)}</span></div>
        <div style="font-size:13px;margin-top:4px">${lineHtml}</div>
        ${movementDetails(g.game_id, "📈 all books · line movement")}</div>`;
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
          <span class="muted" style="font-size:11px">${g.book || ""}</span> ${ats}${ou}</div>
        <div class="muted" style="font-size:13px;margin-top:4px">ML ${mlPair(g.open)} → <span style="color:var(--fg)">${mlPair(g.close)}</span>${tag}${move}</div>
        ${movementDetails(g.game_id, "📊 all books · open → close")}</div>`;
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

function buildMovementUI(box, gid) {
  const lm = LM[gid];
  const sources = [...new Set((lm.snapshots || []).map((s) => s.source))];
  if (!sources.length) { box.innerHTML = `<span class="muted">No line history yet.</span>`; return; }
  const def = ["draftkings", "fanduel"].find((b) => sources.includes(b)) || sources[0];
  box.innerHTML = `
    <div style="display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap">
      <select class="metricSel lbselect"><option value="ml_home">Moneyline</option><option value="spread">Spread</option><option value="total">Total</option></select>
      <select class="bookSel lbselect">${sources.map((s) => `<option${s === def ? " selected" : ""}>${s}</option>`).join("")}</select>
    </div>
    <div class="bookTbl" style="margin-bottom:12px"></div>
    <div class="lmchart"></div>
    <div class="lmvals muted" style="font-size:12px;margin-top:6px"></div>`;
  const metricSel = box.querySelector(".metricSel"), bookSel = box.querySelector(".bookSel");
  const all = () => { renderBookTable(box, gid, metricSel.value); drawGraph(box, gid); };
  metricSel.addEventListener("change", all);
  bookSel.addEventListener("change", () => drawGraph(box, gid));
  all();
}

function bySource(lm) {
  const m = {};
  for (const s of lm.snapshots || []) (m[s.source] ||= []).push(s);
  for (const k in m) m[k].sort((a, b) => new Date(a.captured_at) - new Date(b.captured_at));
  return m;
}

function renderBookTable(box, gid, metric) {
  const lm = LM[gid], grp = bySource(lm);
  const fmt = metric === "total" ? (v) => (v == null ? "—" : v) : (v) => am(v);
  const rows = Object.keys(grp).sort().map((src) => {
    const arr = grp[src];
    const open = arr[0][metric];
    const closeObj = (lm.close && lm.close[src]) || arr[arr.length - 1];
    const close = closeObj[metric];
    const real = lm.close && lm.close[src];
    let d = (typeof open === "number" && typeof close === "number") ? close - open : null;
    const dStr = d == null ? "—" : (Math.abs(d) < (metric === "total" ? 0.05 : 1) ? `<span class="muted">0</span>`
      : `<span class="${d < 0 ? "pos" : "neg"}">${d > 0 ? "+" : ""}${metric === "total" ? d.toFixed(1) : d}</span>`);
    return `<tr><td><span class="pill">${src}</span></td><td class="num muted">${fmt(open)}</td>
      <td class="num">${fmt(close)}${real ? "" : ` <span class="muted" style="font-size:10px">(last)</span>`}</td><td class="num">${dStr}</td></tr>`;
  });
  box.querySelector(".bookTbl").innerHTML =
    `<table><thead><tr><th>Book</th><th class="num">Open</th><th class="num">Close</th><th class="num">Δ</th></tr></thead><tbody>${rows.join("")}</tbody></table>`;
}

function drawGraph(box, gid) {
  const lm = LM[gid];
  const src = box.querySelector(".bookSel").value, metric = box.querySelector(".metricSel").value;
  const snaps = (lm.snapshots || []).filter((s) => s.source === src).sort((a, b) => new Date(a.captured_at) - new Date(b.captured_at));
  const closeVal = lm.close && lm.close[src] ? lm.close[src][metric] : null;
  box.querySelector(".lmchart").innerHTML = lineChart(snaps, metric, closeVal);
  const pts = snaps.map((s) => s[metric]).filter((v) => v != null);
  const f = metric === "total" ? (v) => v : am;
  box.querySelector(".lmvals").innerHTML = pts.length
    ? `${src}: Open <strong>${f(pts[0])}</strong> → ${closeVal != null ? "Close" : "Now"} <strong>${f(closeVal != null ? closeVal : pts[pts.length - 1])}</strong>`
    : "No data for this book/metric.";
}

function lineChart(snaps, metric, closeVal) {
  const pts = snaps.map((s) => ({ t: new Date(s.captured_at).getTime(), v: s[metric] })).filter((p) => p.v != null);
  if (pts.length < 2) return `<div class="muted" style="padding:16px">Not enough data points yet.</div>`;
  const w = 720, h = 170;
  const ts = pts.map((p) => p.t), vs = pts.map((p) => p.v);
  const tmin = Math.min(...ts), tmax = Math.max(...ts);
  let vmin = Math.min(...vs), vmax = Math.max(...vs);
  if (closeVal != null) { vmin = Math.min(vmin, closeVal); vmax = Math.max(vmax, closeVal); }
  const pad = (vmax - vmin) * 0.15 || 1; vmin -= pad; vmax += pad;
  const X = (t) => (tmax > tmin ? ((t - tmin) / (tmax - tmin)) * w : w / 2);
  const Y = (v) => h - ((v - vmin) / (vmax - vmin)) * h;
  const poly = pts.map((p) => `${X(p.t).toFixed(1)},${Y(p.v).toFixed(1)}`).join(" ");
  const closeLine = closeVal != null ? `<line x1="0" y1="${Y(closeVal).toFixed(1)}" x2="${w}" y2="${Y(closeVal).toFixed(1)}" stroke="var(--gold)" stroke-width="1" stroke-dasharray="4 3"/>` : "";
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="width:100%;height:${h}px;display:block;background:var(--panel2);border-radius:8px">
    ${closeLine}<polyline points="${poly}" fill="none" stroke="var(--accent)" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>`;
}

load();
