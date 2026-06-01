// SharpLab HQ — one trader's portfolio: equity chart, holdings, options, txns.

const app = document.getElementById("app");
const cls = (n) => (n >= 0 ? "pos" : "neg");
const money = (n) => (n == null ? "—" : "$" + Number(n).toLocaleString(undefined, { maximumFractionDigits: 0 }));
const money2 = (n) => (n == null ? "—" : "$" + Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const pnl = (n) => (n >= 0 ? "+" : "−") + "$" + Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 });

const RANGES = [["1D", 1], ["1W", 7], ["1M", 30], ["3M", 90], ["YTD", "ytd"], ["1Y", 365], ["ALL", 1e9]];
const rangeCutoff = (r) => (r === "ytd" ? Date.UTC(new Date().getFullYear(), 0, 1) : Date.now() - r * 86400000);
let EQUITY = [];
let BENCH = [];
let range = 7;
let HOLD = [];                     // holdings (for the per-stock change table)
let tblRange = "1D";               // selected period for the holdings change column
const TBL_RANGES = ["1D", "1W", "1M", "YTD", "1Y"];
const BENCH_COLOR = "#58a6ff";     // S&P line — distinct from grey reconstructed segment

function holdingsTable() {
  if (!HOLD.length) return `<div class="muted" style="padding:18px">No open stock positions.</div>`;
  const chgCell = (h) => {
    const v = (h.changes || {})[tblRange];
    if (v == null) return `<span class="muted">—</span>`;
    // Dollar move for the period = current shares × per-share price move.
    // price_then = price_now / (1 + pct/100); $Δ = shares × (price_now − price_then).
    const pct = `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
    let dol = "";
    if (h.price && h.shares) {
      const then = h.price / (1 + v / 100);
      dol = ` <span class="muted" style="font-size:11px">${pnl(h.shares * (h.price - then))}</span>`;
    }
    return `<span class="${cls(v)}">${pct}</span>${dol}`;
  };
  const hRow = (h) => {
    const u = h.unrealized;
    const upct = (u != null && h.cost_basis) ? ` <span class="muted" style="font-size:11px">${u >= 0 ? "+" : ""}${(u / h.cost_basis * 100).toFixed(1)}%</span>` : "";
    const ucell = u == null ? `<span class="muted">—</span>` : `<span class="${cls(u)}">${pnl(u)}</span>${upct}`;
    const rcell = h.realized ? `<span class="${cls(h.realized)}">${pnl(h.realized)}</span>` : `<span class="muted">—</span>`;
    return `<tr><td><strong>${h.ticker}</strong></td><td class="num">${h.shares}</td>
      <td class="num muted">${money2(h.dca)}</td><td class="num">${money(h.cost_basis)}</td>
      <td class="num">${chgCell(h)}</td><td class="num">${ucell}</td><td class="num">${rcell}</td></tr>`;
  };
  return `<div class="stakebtns" style="padding:10px 12px 2px">
      ${TBL_RANGES.map((r) => `<button data-hrange="${r}" class="rangebtn${r === tblRange ? " on" : ""}">${r}</button>`).join("")}
    </div>
    <table><thead><tr><th>Ticker</th><th class="num">Shares</th><th class="num">Avg</th><th class="num">Cost basis</th>
      <th class="num">Change</th><th class="num">Unrealized</th><th class="num">Realized</th></tr></thead>
    <tbody>${HOLD.map(hRow).join("")}</tbody></table>`;
}

async function main() {
  const parts = location.pathname.split("/").filter(Boolean); // ["hq","stocks","xpoes"]
  const handle = decodeURIComponent(parts[2] || "");
  const r = await fetch(`/api/v1/hq/stocks/${encodeURIComponent(handle)}`);
  if (r.status === 404) {
    app.innerHTML = `<div class="hero"><h1>Not found</h1><a class="btn" href="/hq/stocks">All portfolios</a></div>`;
    return;
  }
  render(await r.json());
}

function svgChart(series, bench, liveIdx, h = 220) {
  if (series.length < 2) return `<div class="muted" style="padding:28px;text-align:center">Not enough history yet — snapshots build hourly.</div>`;
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
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="width:100%;height:${h}px;display:block">
    <polygon points="0,${h} ${ptsOf(series)} ${w},${h}" fill="${fill}"/>
    ${benchLine}${backLine}${marker}${liveLine}</svg>`;
}

function pctChange(arr) {
  return arr.length >= 2 && arr[0].v ? ((arr[arr.length - 1].v - arr[0].v) / arr[0].v) * 100 : 0;
}

function drawChart() {
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

function render(d) {
  const u = d.user, s = d.summary || {};
  EQUITY = d.equity || [];
  BENCH = d.benchmark || [];
  const av = u.avatar_url;

  let html = `<div class="profhead">
    ${av ? `<img class="avatar lg" src="${av}" alt="">` : `<div class="avatar lg"></div>`}
    <div><h1>${u.username}</h1><div class="muted">Portfolio</div></div>
    <div style="margin-left:auto;text-align:right">
      <div style="font-size:30px;font-weight:800">${money(s.account_value)}</div>
      <div id="chartDelta" style="font-size:14px"></div></div></div>

    <div class="card" style="margin-top:8px">
      <div class="stakebtns" style="margin-bottom:10px">
        ${RANGES.map(([lbl, days]) => `<button data-days="${days}" class="rangebtn${days === range ? " on" : ""}">${lbl}</button>`).join("")}
      </div>
      <div id="chart"></div>
      <div class="muted" style="font-size:12px;margin-top:8px;display:flex;gap:16px;flex-wrap:wrap">
        <span><span style="color:var(--green)">━</span> Portfolio</span>
        <span><span style="color:${BENCH_COLOR}">┄</span> S&P 500</span>
        ${d.live_since ? `<span><span style="color:var(--accent)">┊</span> Live since ${fmtDate(d.live_since)} — earlier is reconstructed</span>` : ""}
      </div></div>

    <div class="grid" style="margin-top:16px">
      <div class="card stat"><div class="label">Stocks</div><div class="value" style="font-size:20px">${money(s.stock_value)}</div></div>
      <div class="card stat"><div class="label">Options</div><div class="value" style="font-size:20px">${money(s.options_value)}</div></div>
      <div class="card stat"><div class="label">Cash</div><div class="value" style="font-size:20px">${money(s.cash)}</div></div>
      <div class="card stat"><div class="label">Unrealized P&L</div>
        <div class="value ${cls(s.unrealized_pnl || 0)}" style="font-size:20px">${s.unrealized_pnl == null ? "—" : pnl(s.unrealized_pnl)}</div></div>
      <div class="card stat"><div class="label">Realized P&L</div>
        <div class="value ${cls(s.realized_pnl || 0)}" style="font-size:20px">${pnl(s.realized_pnl || 0)}</div></div>
    </div>`;

  HOLD = d.stock_holdings || [];
  html += `<h2>Stock Holdings</h2><div class="card" style="padding:0" id="holdBox">${holdingsTable()}</div>`;

  const op = d.option_positions || [];
  html += `<h2>Options</h2><div class="card" style="padding:0">
    ${op.length ? `<table><thead><tr><th>Contract</th><th class="num">Qty</th><th class="num">Avg premium</th></tr></thead>
      <tbody>${op.map((o) => `<tr><td><strong>${o.underlying}</strong> $${o.strike}${o.opt_type[0].toUpperCase()}
        <span class="muted">${o.expiry}</span></td><td class="num ${cls(o.contracts)}">${o.contracts > 0 ? "+" : ""}${o.contracts}</td>
        <td class="num muted">${money2(o.avg_premium)}</td></tr>`).join("")}</tbody></table>`
      : `<div class="muted" style="padding:18px">No open option positions.</div>`}</div>`;

  const tx = d.transactions || [];
  html += `<h2>Transactions</h2><div class="card" style="padding:0">
    ${tx.length ? `<table><thead><tr><th>When</th><th></th><th>Trade</th></tr></thead>
      <tbody>${tx.map((t) => `<tr><td class="muted" style="white-space:nowrap">${fmtDate(t.at)}</td>
        <td><span class="pill">${t.kind}</span></td><td>${t.desc}</td></tr>`).join("")}</tbody></table>`
      : `<div class="muted" style="padding:18px">No trades yet.</div>`}</div>`;

  app.innerHTML = html;
  drawChart();
  app.addEventListener("click", (e) => {
    const b = e.target.closest(".rangebtn");
    if (!b) return;
    if (b.dataset.hrange) {  // holdings-table period toggle
      tblRange = b.dataset.hrange;
      document.getElementById("holdBox").innerHTML = holdingsTable();
      return;
    }
    range = b.dataset.days === "ytd" ? "ytd" : Number(b.dataset.days);
    document.querySelectorAll(".rangebtn[data-days]").forEach((x) => x.classList.toggle("on", x === b));
    drawChart();
  });
}

function fmtDate(iso) {
  try {
    return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "2-digit" });
  } catch { return iso; }
}

main();
