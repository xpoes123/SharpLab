// SharpLab HQ — one trader's portfolio: equity chart, holdings, options, txns.

const app = document.getElementById("app");
const cls = (n) => (n >= 0 ? "pos" : "neg");
const money = (n) => (n == null ? "—" : "$" + Number(n).toLocaleString(undefined, { maximumFractionDigits: 0 }));
const money2 = (n) => (n == null ? "—" : "$" + Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const pnl = (n) => (n >= 0 ? "+" : "−") + "$" + Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 });

// One shared range selector drives both the chart window and the holdings change column.
const RANGES = ["1D", "1W", "1M", "3M", "YTD", "1Y", "ALL"];
const RANGE_DAYS = { "1D": 1, "1W": 7, "1M": 30, "3M": 90, "YTD": "ytd", "1Y": 365, "ALL": 1e9 };
const rangeCutoff = (label) => {
  const r = RANGE_DAYS[label];
  return r === "ytd" ? Date.UTC(new Date().getFullYear(), 0, 1) : Date.now() - r * 86400000;
};
let EQUITY = [];
let BENCH = [];
let HOLD = [];                     // holdings (each carries a `history` daily-close series)
let range = "1W";                  // shared selected range label
let view = "portfolio";            // "portfolio" | <ticker> — what the main chart shows
let CHART = null;                  // { series, lo, hi } of the drawn line, for hover lookups
let sortCol = null;                // holdings sort column key (null = original order)
let sortDir = -1;                  // 1 = asc, -1 = desc
let pieMode = "alloc";             // allocation donut mode: "alloc" | "stocks" | "all"
let SUMMARY = {};                  // last render()'s d.summary, for the donut
const BENCH_COLOR = "#58a6ff";     // S&P line — distinct from grey reconstructed segment
const PIE_COLORS = ["#7aa2f7", "#bb9af7", "#9ece6a", "#e0af68", "#f7768e", "#7dcfff", "#ff9e64", "#2ac3de", "#c0caf5", "#9d7cd8", "#41a6b5", "#73daca"];
const PIE_GREY = "#565f89";        // Cash / Other slices

const priceFmt = (v) => (Math.abs(v) >= 1000 ? money(v) : money2(v));        // hover tooltip
const axisFmt = (v) => (Math.abs(v) >= 1000                                  // compact Y-axis
  ? "$" + (v / 1000).toFixed(Math.abs(v) >= 10000 ? 0 : 1) + "k"
  : "$" + v.toFixed(v < 100 ? 1 : 0));

// Value for a given sort key on a holding. Change reads the selected-range pnl;
// missing change/unrealized sink to the bottom, missing realized treated as 0.
function sortVal(h, key) {
  switch (key) {
    case "ticker": return h.ticker;
    case "shares": return h.shares;
    case "dca": return h.dca;
    case "cost_basis": return h.cost_basis;
    case "change": { const p = h.period?.[range]?.pnl; return p == null ? -Infinity : p; }
    case "unrealized": return h.unrealized == null ? -Infinity : h.unrealized;
    case "realized": return h.realized || 0;
    default: return 0;
  }
}

// HOLD sorted per sortCol/sortDir; original order when no column is active.
function sortedHoldings() {
  if (!sortCol) return HOLD;
  const rows = HOLD.slice();
  rows.sort((a, b) => {
    const va = sortVal(a, sortCol), vb = sortVal(b, sortCol);
    const c = sortCol === "ticker" ? String(va).localeCompare(String(vb)) : (va - vb);
    return c * sortDir;
  });
  return rows;
}

function holdingsTable() {
  if (!HOLD.length) return `<div class="muted" style="padding:18px">No open stock positions.</div>`;
  // Sortable header: clicking a th sorts by its key; arrow marks the active column.
  const arrow = (key) => sortCol === key ? (sortDir === 1 ? " ▲" : " ▼") : "";
  const th = (key, label, num) => `<th${num ? ' class="num"' : ""} data-sort="${key}" style="cursor:pointer;user-select:none">${label}${arrow(key)}</th>`;
  // Her ACTUAL P/L over the selected period (holding-aware) — a stock bought mid-period
  // only counts gains since the buy, so this won't show a phantom move she didn't capture.
  const chgCell = (h) => {
    const p = (h.period || {})[range];
    if (!p || p.pnl == null) return `<span class="muted">—</span>`;
    const dol = `<span class="muted" style="font-size:11px">${pnl(p.pnl)}</span>`;
    if (p.pct == null) return `<span class="${cls(p.pnl)}">${pnl(p.pnl)}</span>`;
    return `<span class="${cls(p.pct)}">${p.pct >= 0 ? "+" : ""}${p.pct.toFixed(2)}%</span> ${dol}`;
  };
  const hRow = (h) => {
    const u = h.unrealized;
    const upct = (u != null && h.cost_basis) ? ` <span class="muted" style="font-size:11px">${u >= 0 ? "+" : ""}${(u / h.cost_basis * 100).toFixed(1)}%</span>` : "";
    const ucell = u == null ? `<span class="muted">—</span>` : `<span class="${cls(u)}">${pnl(u)}</span>${upct}`;
    const rcell = h.realized ? `<span class="${cls(h.realized)}">${pnl(h.realized)}</span>` : `<span class="muted">—</span>`;
    const sel = h.ticker === view ? ";background:var(--accent-dim)" : "";
    const ah = h.extended
      ? `<span class="muted" style="font-size:10px;margin-left:auto;white-space:nowrap" title="${h.extended.session === "pre" ? "Pre-market" : "After-hours"}">🌙 ${money2(h.extended.price)} <span class="${cls(h.extended.pct)}">${h.extended.pct >= 0 ? "+" : ""}${h.extended.pct.toFixed(1)}%</span></span>`
      : "";
    const shCell = h.shares < 0 ? `<span class="neg">short ${(-h.shares).toLocaleString()}</span>` : `${h.shares}`;
    return `<tr data-ticker="${h.ticker}" style="cursor:pointer${sel}"><td><div style="display:flex;align-items:center;gap:6px"><strong>${h.ticker}</strong>${ah}</div></td><td class="num">${shCell}</td>
      <td class="num muted">${money2(h.dca)}</td><td class="num">${money(h.cost_basis)}</td>
      <td class="num">${chgCell(h)}</td><td class="num">${ucell}</td><td class="num">${rcell}</td></tr>`;
  };
  return `<table><thead><tr>${th("ticker", "Ticker", false)}${th("shares", "Shares", true)}${th("dca", "Avg", true)}${th("cost_basis", "Cost basis", true)}
      ${th("change", "Change", true)}${th("unrealized", "Unrealized", true)}${th("realized", "Realized", true)}</tr></thead>
    <tbody>${sortedHoldings().map(hRow).join("")}</tbody></table>`;
}

async function main() {
  const parts = location.pathname.split("/").filter(Boolean); // ["stocks","xpoes"]
  const handle = decodeURIComponent(parts[parts.length - 1] || "");
  const r = await fetch(`/api/v1/hq/stocks/${encodeURIComponent(handle)}`);
  if (r.status === 404) {
    app.innerHTML = `<div class="hero"><h1>Not found</h1><a class="btn" href="/stocks">All portfolios</a></div>`;
    return;
  }
  render(await r.json());
}

function svgChart(series, bench, liveIdx, h = 220) {
  if (series.length < 2) { CHART = null; return `<div class="muted" style="padding:28px;text-align:center">Not enough history yet — snapshots build hourly.</div>`; }
  const w = 760;
  const n = series.length;
  const allVals = series.map((p) => p.v).concat((bench || []).map((p) => p.v));
  const min = Math.min(...allVals), max = Math.max(...allVals);
  const pad = (max - min) * 0.08 || Math.abs(max) * 0.02 || 1;
  const lo = min - pad, hi = max + pad;
  const X = (i) => (i / (n - 1)) * w;
  const Y = (v) => h - ((v - lo) / (hi - lo)) * h;
  const ptsOf = (arr, off = 0) => arr.map((p, i) => `${X(i + off).toFixed(1)},${Y(p.v).toFixed(1)}`).join(" ");
  const up = series[n - 1].v >= series[0].v;
  const color = up ? "var(--green)" : "var(--red)";
  const fill = up ? "rgba(158,206,106,.12)" : "rgba(247,118,142,.12)";

  liveIdx = Math.max(0, Math.min(n, liveIdx));
  const backfill = series.slice(0, liveIdx);
  const liveStart = Math.max(0, liveIdx - 1);
  const live = series.slice(liveStart);
  const backLine = backfill.length >= 2
    ? `<polyline points="${ptsOf(backfill)}" fill="none" stroke="var(--muted)" stroke-width="1.5" opacity="0.45" vector-effect="non-scaling-stroke"/>` : "";
  const liveLine = live.length >= 2
    ? `<polyline points="${ptsOf(live, liveStart)}" fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke"/>` : "";
  const marker = (liveIdx > 0 && liveIdx < n)
    ? `<line x1="${X(liveIdx).toFixed(1)}" y1="0" x2="${X(liveIdx).toFixed(1)}" y2="${h}" stroke="var(--accent)" stroke-width="1" stroke-dasharray="3 3" opacity="0.7"/>` : "";
  const benchLine = (bench && bench.length >= 2)
    ? `<polyline points="${ptsOf(bench)}" fill="none" stroke="${BENCH_COLOR}" stroke-width="2" stroke-dasharray="5 4" opacity="0.9" vector-effect="non-scaling-stroke"/>` : "";

  // Horizontal price gridlines + HTML Y-axis labels (HTML, since the SVG is stretched
  // non-uniformly and SVG <text> would distort). Hover crosshair + tooltip read CHART.
  const TICKS = 4;
  let grid = "", labels = "";
  for (let i = 0; i <= TICKS; i++) {
    const v = lo + ((hi - lo) * i) / TICKS;
    grid += `<line x1="0" y1="${Y(v).toFixed(1)}" x2="${w}" y2="${Y(v).toFixed(1)}" stroke="var(--muted)" stroke-width="1" opacity="0.12" vector-effect="non-scaling-stroke"/>`;
    labels += `<div style="position:absolute;right:3px;top:calc(${((1 - i / TICKS) * 100).toFixed(2)}% - 7px);font-size:10px;color:var(--muted);background:var(--card,#11131a);padding:0 3px;border-radius:2px;pointer-events:none">${axisFmt(v)}</div>`;
  }
  CHART = { series, lo, hi };
  return `<div style="position:relative">
    <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="width:100%;height:${h}px;display:block">
      ${grid}<polygon points="0,${h} ${ptsOf(series)} ${w},${h}" fill="${fill}"/>
      ${benchLine}${backLine}${marker}${liveLine}</svg>
    ${labels}
    <div id="crosshair" style="position:absolute;top:0;bottom:0;width:1px;background:var(--accent);opacity:0;pointer-events:none"></div>
    <div id="tooltip" style="position:absolute;opacity:0;pointer-events:none;background:#1a1d29;border:1px solid var(--muted);border-radius:4px;padding:3px 7px;font-size:11px;white-space:nowrap;transform:translate(-50%,-135%);z-index:5"></div>
  </div>`;
}

// Crosshair + price tooltip following the cursor over the chart. Attached once to the
// persistent #chart container; reads CHART (set by svgChart) so it works after redraws.
function attachHover() {
  const chart = document.getElementById("chart");
  const hide = () => {
    const c = document.getElementById("crosshair"), t = document.getElementById("tooltip");
    if (c) c.style.opacity = "0";
    if (t) t.style.opacity = "0";
  };
  chart.addEventListener("mousemove", (e) => {
    const wrap = chart.firstElementChild;
    const cross = document.getElementById("crosshair"), tip = document.getElementById("tooltip");
    if (!CHART || !wrap || !cross || !tip) return;
    const rect = wrap.getBoundingClientRect();
    const i = Math.round(((e.clientX - rect.left) / rect.width) * (CHART.series.length - 1));
    if (i < 0 || i >= CHART.series.length) return;
    const p = CHART.series[i];
    const x = (i / (CHART.series.length - 1)) * rect.width;
    const y = (1 - (p.v - CHART.lo) / (CHART.hi - CHART.lo)) * rect.height;
    cross.style.left = x + "px"; cross.style.opacity = "0.5";
    tip.style.left = x + "px"; tip.style.top = y + "px"; tip.style.opacity = "1";
    tip.innerHTML = `<strong>${priceFmt(p.v)}</strong> · <span style="color:var(--muted)">${fmtDate(p.t)}</span>`;
  });
  chart.addEventListener("mouseleave", hide);
}

function pctChange(arr) {
  return arr.length >= 2 && arr[0].v ? ((arr[arr.length - 1].v - arr[0].v) / arr[0].v) * 100 : 0;
}

function drawChart() {
  if (view !== "portfolio") return drawTicker(view);

  const cutoff = rangeCutoff(range);
  let series = EQUITY.filter((p) => new Date(p.t).getTime() >= cutoff);
  let bench = BENCH.filter((p) => new Date(p.t).getTime() >= cutoff);
  if (series.length < 2) { series = EQUITY; bench = BENCH; }

  // Re-base SPY to the window's starting portfolio value so both lines start together.
  let benchUse = [];
  if (bench.length >= 2 && bench[0].v) {
    const k = series[0].v / bench[0].v;
    benchUse = bench.map((p) => ({ t: p.t, v: p.v * k }));
  }
  let liveIdx = series.findIndex((p) => p.k === "live");
  if (liveIdx < 0) liveIdx = series.length;  // all backfill
  document.getElementById("chart").innerHTML = svgChart(series, benchUse, liveIdx);

  const d = series.length >= 2 ? series[series.length - 1].v - series[0].v : 0;
  const port = pctChange(series), spy = pctChange(bench);
  const vs = benchUse.length >= 2
    ? ` · vs S&P <span class="${cls(port - spy)}">${port - spy >= 0 ? "beating by +" : ""}${(port - spy).toFixed(1)}%</span>`
    : "";
  document.getElementById("chartDelta").innerHTML = series.length >= 2
    ? `<span class="${cls(d)}">${pnl(d)} (${port >= 0 ? "+" : ""}${port.toFixed(1)}%)</span>${vs}`
    : "";
}

// Cutoff DATE for a range, matching the server's period-change baselines so the
// chart's start point lines up with the table's % (avoids the chart and the
// Change column disagreeing across a volatile boundary day).
function cutoffDate(label) {
  if (label === "ALL") return null;                       // from the earliest close
  if (label === "YTD") return `${new Date().getFullYear()}-01-01`;
  return new Date(Date.now() - RANGE_DAYS[label] * 86400000).toISOString().slice(0, 10);
}

// Individual ticker's price history (daily closes + a live point), sliced to the range.
function drawTicker(sym) {
  const h = HOLD.find((x) => x.ticker === sym);
  const all = ((h && h.history) || []).map(([t, v]) => ({ t, v, k: "live" }));
  const cd = cutoffDate(range);
  let series;
  if (cd == null) {
    series = all.slice();                                 // ALL → full history
  } else if (range === "YTD") {
    const i = all.findIndex((p) => p.t >= cd);
    series = i >= 0 ? all.slice(i) : all.slice();
  } else {
    // baseline = last close on/before the cutoff date (same as the server), then everything after
    let base = -1;
    for (let i = 0; i < all.length && all[i].t <= cd; i++) base = i;
    series = base >= 0 ? all.slice(base) : all.filter((p) => p.t > cd);
  }
  if (series.length < 2) series = all.slice(-7);          // very short window — show recent closes
  if (series.length < 2) series = all;
  document.getElementById("chart").innerHTML = svgChart(series, [], 0);  // all live, no benchmark

  const d = series.length >= 2 ? series[series.length - 1].v - series[0].v : 0;
  const pc = pctChange(series);
  document.getElementById("chartDelta").innerHTML = series.length >= 2
    ? `<span class="${cls(d)}">${pnl(d)}/sh (${pc >= 0 ? "+" : ""}${pc.toFixed(1)}%)</span>`
    : `<span class="muted">No price history</span>`;
}

// Swap chart between portfolio and a ticker; keep head/legend/highlight in sync.
function updateChart() {
  const head = document.getElementById("chartHead");
  const legend = document.getElementById("chartLegend");
  if (view === "portfolio") {
    head.style.display = "none";
    head.innerHTML = "";
    legend.style.display = "flex";
  } else {
    const h = HOLD.find((x) => x.ticker === view);
    const px = h && h.price != null ? money2(h.price) : "";
    const p = h && (h.period || {})[range];
    const mine = p && p.pnl != null
      ? ` <span class="${cls(p.pnl)}" style="font-size:13px">your ${range}: ${pnl(p.pnl)}${p.pct != null ? ` (${p.pct >= 0 ? "+" : ""}${p.pct.toFixed(1)}%)` : ""}</span>`
      : "";
    head.style.display = "flex";
    head.innerHTML = `<button data-back class="rangebtn">← Portfolio</button>
      <strong style="font-size:16px">${view}</strong> <span class="muted">${px}</span>${mine}`;
    legend.style.display = "none";
  }
  drawChart();
}

function setView(v) {
  view = v;
  document.getElementById("holdBox").innerHTML = holdingsTable();  // refresh row highlight
  updateChart();
}

// Donut: arcs drawn as <circle> strokes via dasharray, accumulating a negative
// dashoffset so each slice begins where the previous ended; rotated -90° to start
// at top. Center hole shows the total. slices = [{label, value, color}].
function donutSVG(slices, total) {
  const size = 180, sw = 30, r = (size - sw) / 2, c = 2 * Math.PI * r, cx = size / 2;
  let off = 0;
  const arcs = slices.map((s) => {
    const frac = total > 0 ? s.value / total : 0;
    const arc = `<circle cx="${cx}" cy="${cx}" r="${r}" fill="none" stroke="${s.color}" stroke-width="${sw}"
      stroke-dasharray="${(frac * c).toFixed(2)} ${c.toFixed(2)}" stroke-dashoffset="${(-off).toFixed(2)}"/>`;
    off += frac * c;
    return arc;
  }).join("");
  return `<svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" style="flex:0 0 auto">
    <g transform="rotate(-90 ${cx} ${cx})">${arcs}</g>
    <text x="${cx}" y="${cx - 6}" text-anchor="middle" font-size="11" fill="var(--muted)">Total</text>
    <text x="${cx}" y="${cx + 13}" text-anchor="middle" font-size="16" font-weight="800" fill="var(--fg)">${money(total)}</text>
  </svg>`;
}

// Build the slice list for the active pieMode, color it, and roll a long tail into
// a single grey "Other" so the donut never gets unreadable.
function pieSlices() {
  const s = SUMMARY;
  let raw;
  if (pieMode === "alloc") {
    raw = [
      { label: "Stocks", value: s.stock_value || 0, grey: false },
      { label: "Options", value: s.options_value || 0, grey: false },
      { label: "Cash", value: s.cash || 0, grey: true },
    ].filter((x) => x.value > 0);
  } else {
    const stocks = HOLD
      .map((h) => ({ label: h.ticker, value: (h.price != null && h.price > 0) ? h.shares * h.price : 0, grey: false }))
      .filter((x) => x.value > 0)
      .sort((a, b) => b.value - a.value);
    if (pieMode === "stocks") {
      raw = stocks;
    } else { // "all": stocks + options + cash
      raw = stocks.slice();
      if ((s.options_value || 0) > 0) raw.push({ label: "Options", value: s.options_value, grey: false });
      if ((s.cash || 0) > 0) raw.push({ label: "Cash", value: s.cash, grey: true });
      raw.sort((a, b) => b.value - a.value);
    }
  }
  if (raw.length > 11) {
    const keep = raw.slice(0, 11);
    const other = raw.slice(11).reduce((sum, x) => sum + x.value, 0);
    keep.push({ label: "Other", value: other, grey: true });
    raw = keep;
  }
  // Assign palette colors; grey slices (Cash/Other) get the fixed grey.
  let ci = 0;
  return raw.map((x) => ({ label: x.label, value: x.value, color: x.grey ? PIE_GREY : PIE_COLORS[ci++ % PIE_COLORS.length] }));
}

const PIE_MODES = [["alloc", "Account"], ["stocks", "Stocks"], ["all", "All"]];

function pieBody() {
  const buttons = `<div class="stakebtns" style="margin-bottom:12px">
    ${PIE_MODES.map(([m, lbl]) => `<button data-pie="${m}" class="rangebtn${m === pieMode ? " on" : ""}">${lbl}</button>`).join("")}</div>`;
  const slices = pieSlices();
  const total = slices.reduce((sum, x) => sum + x.value, 0);
  if (total <= 0) return `${buttons}<div class="muted" style="padding:18px">No allocation data.</div>`;
  const legend = slices.map((x) => {
    const pct = (x.value / total * 100).toFixed(1);
    return `<div style="display:flex;align-items:center;gap:8px;min-width:180px;flex:1 1 200px">
      <span style="width:11px;height:11px;border-radius:3px;background:${x.color};flex:0 0 auto"></span>
      <span style="flex:1">${x.label}</span>
      <span class="muted" style="white-space:nowrap">${money(x.value)} · ${pct}%</span></div>`;
  }).join("");
  return `${buttons}<div style="display:flex;gap:24px;align-items:center;flex-wrap:wrap">
    ${donutSVG(slices, total)}
    <div style="display:flex;flex-wrap:wrap;gap:10px 18px;flex:1 1 280px">${legend}</div>
  </div>`;
}

function render(d) {
  const u = d.user, s = d.summary || {};
  EQUITY = d.equity || [];
  BENCH = d.benchmark || [];
  SUMMARY = d.summary || {};
  const av = u.avatar_url;

  let html = `<div class="profhead">
    ${av ? `<img class="avatar lg" src="${av}" alt="">` : `<div class="avatar lg"></div>`}
    <div><h1>${u.username}</h1><div class="muted">Portfolio</div></div>
    <div style="margin-left:auto;text-align:right">
      <div style="font-size:30px;font-weight:800">${money(s.account_value)}</div>
      <div id="chartDelta" style="font-size:14px"></div></div></div>

    <div class="card" style="margin-top:8px">
      <div class="stakebtns" style="margin-bottom:10px">
        ${RANGES.map((lbl) => `<button data-range="${lbl}" class="rangebtn${lbl === range ? " on" : ""}">${lbl}</button>`).join("")}
      </div>
      <div id="chartHead" style="display:none;align-items:center;gap:10px;margin-bottom:6px"></div>
      <div id="chart"></div>
      <div id="chartLegend" class="muted" style="font-size:12px;margin-top:8px;display:flex;gap:16px;flex-wrap:wrap">
        <span><span style="color:var(--green)">━</span> Portfolio</span>
        <span><span style="color:${BENCH_COLOR}">┄</span> S&P 500</span>
        ${d.live_since ? `<span><span style="color:var(--accent)">┊</span> Live since ${fmtDate(d.live_since)} — earlier is reconstructed</span>` : ""}
      </div></div>

    <div class="grid" style="margin-top:16px">
      <div class="card stat"><div class="label">Stocks</div><div class="value" style="font-size:20px">${money(s.stock_value)}</div></div>
      <div class="card stat"><div class="label">Options</div><div class="value" style="font-size:20px">${money(s.options_value)}</div>
        ${s.options_unrealized_pnl == null ? "" : `<div class="${cls(s.options_unrealized_pnl)}" style="font-size:12px;margin-top:2px">${pnl(s.options_unrealized_pnl)} open</div>`}</div>
      <div class="card stat"><div class="label">Cash</div><div class="value" style="font-size:20px">${money(s.cash)}</div></div>
      <div class="card stat"><div class="label">Unrealized P&L</div>
        <div class="value ${cls(s.unrealized_pnl || 0)}" style="font-size:20px">${s.unrealized_pnl == null ? "—" : pnl(s.unrealized_pnl)}</div></div>
      <div class="card stat"><div class="label">Realized P&L</div>
        <div class="value ${cls(s.realized_pnl || 0)}" style="font-size:20px">${pnl(s.realized_pnl || 0)}</div></div>
    </div>`;

  HOLD = d.stock_holdings || [];
  html += `<h2>Allocation</h2>
    <div class="card" id="pieBox">${pieBody()}</div>`;

  html += `<h2>Stock Holdings <span class="muted" style="font-size:13px;font-weight:400">— click a row to chart it</span></h2>
    <div class="card" style="padding:0" id="holdBox">${holdingsTable()}</div>`;

  const op = d.option_positions || [];
  const signed = (n) => (n == null ? "—" : (n >= 0 ? "+" : "−") + "$" + Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
  html += `<h2>Options</h2><div class="card" style="padding:0">
    ${op.length ? `<table><thead><tr><th>Contract</th><th class="num">Qty</th><th class="num">Avg cost</th><th class="num">Now</th><th class="num">P/L</th><th class="num" title="Days to expiry">DTE</th><th class="num" title="Implied volatility">IV</th><th class="num" title="Delta — price sensitivity to a $1 underlying move">Δ</th><th class="num" title="Theta — $/day decay for one contract">Θ/d</th></tr></thead>
      <tbody>${op.map((o) => {
        const tag = o.expired ? `<span class="muted" style="font-size:11px"> · expired</span>`
          : o.estimated ? `<span class="muted" style="font-size:11px" title="strike not listed by the data source — priced via Black-Scholes"> · est</span>` : "";
        const plCell = o.unrealized == null ? `<span class="muted">—</span>`
          : `<span class="${cls(o.unrealized)}">${signed(o.unrealized)}${o.unrealized_pct != null ? ` <span style="font-size:11px;opacity:.8">(${o.unrealized_pct >= 0 ? "+" : ""}${o.unrealized_pct}%)</span>` : ""}</span>`;
        const dash = `<span class="muted">—</span>`;
        return `<tr><td><strong>${o.underlying}</strong> $${o.strike}${o.opt_type[0].toUpperCase()}
          <span class="muted">${o.expiry}</span>${tag}</td>
          <td class="num ${cls(o.contracts)}">${o.contracts > 0 ? "+" : ""}${o.contracts}</td>
          <td class="num muted">${money2(o.avg_premium)}</td>
          <td class="num">${o.price == null ? dash : money2(o.price)}</td>
          <td class="num">${plCell}</td>
          <td class="num muted">${o.dte == null ? dash : o.dte + "d"}</td>
          <td class="num muted">${o.iv == null ? dash : o.iv + "%"}</td>
          <td class="num muted">${o.delta == null ? dash : o.delta}</td>
          <td class="num muted">${o.theta == null ? dash : signed(o.theta)}</td></tr>`;
      }).join("")}</tbody></table>`
      : `<div class="muted" style="padding:18px">No open option positions.</div>`}</div>`;

  const tx = d.transactions || [];
  html += `<h2>Transactions</h2><div class="card" style="padding:0">
    ${tx.length ? `<table><thead><tr><th>When</th><th></th><th>Trade</th></tr></thead>
      <tbody>${tx.map((t) => `<tr><td class="muted" style="white-space:nowrap">${fmtDate(t.at)}</td>
        <td><span class="pill">${t.kind}</span></td><td>${t.desc}</td></tr>`).join("")}</tbody></table>`
      : `<div class="muted" style="padding:18px">No trades yet.</div>`}</div>`;

  app.innerHTML = html;
  updateChart();
  attachHover();
  app.addEventListener("click", (e) => {
    const back = e.target.closest("[data-back]");
    if (back) { setView("portfolio"); return; }

    const rb = e.target.closest(".rangebtn[data-range]");
    if (rb) {  // shared range — updates both the chart and the holdings change column
      range = rb.dataset.range;
      document.querySelectorAll(".rangebtn[data-range]").forEach((x) => x.classList.toggle("on", x === rb));
      document.getElementById("holdBox").innerHTML = holdingsTable();
      updateChart();
      return;
    }

    const pie = e.target.closest("[data-pie]");
    if (pie) {  // allocation donut mode toggle
      pieMode = pie.dataset.pie;
      document.querySelectorAll("[data-pie]").forEach((x) => x.classList.toggle("on", x === pie));
      document.getElementById("pieBox").innerHTML = pieBody();
      return;
    }

    const th = e.target.closest("th[data-sort]");
    if (th) {  // sort holdings — flip dir on the active column, else set a sensible default dir
      const key = th.dataset.sort;
      if (sortCol === key) sortDir = -sortDir;
      else { sortCol = key; sortDir = key === "ticker" ? 1 : -1; }
      document.getElementById("holdBox").innerHTML = holdingsTable();
      return;
    }

    const row = e.target.closest("tr[data-ticker]");
    if (row) { setView(row.dataset.ticker); return; }
  });
}

function fmtDate(iso) {
  try {
    return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "2-digit" });
  } catch { return iso; }
}

main();
