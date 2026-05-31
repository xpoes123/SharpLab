// SharpLab HQ — one trader's portfolio: equity chart, holdings, options, txns.

const app = document.getElementById("app");
const cls = (n) => (n >= 0 ? "pos" : "neg");
const money = (n) => (n == null ? "—" : "$" + Number(n).toLocaleString(undefined, { maximumFractionDigits: 0 }));
const money2 = (n) => (n == null ? "—" : "$" + Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const pnl = (n) => (n >= 0 ? "+" : "−") + "$" + Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 });

const RANGES = [["1W", 7], ["1M", 30], ["3M", 90], ["1Y", 365], ["ALL", 1e9]];
let EQUITY = [];
let range = 30;

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

function svgChart(series, h = 220) {
  if (series.length < 2) return `<div class="muted" style="padding:28px;text-align:center">Not enough history yet — snapshots build hourly.</div>`;
  const w = 760;
  const vals = series.map((p) => p.v);
  const min = Math.min(...vals), max = Math.max(...vals);
  const pad = (max - min) * 0.08 || Math.abs(max) * 0.02 || 1;
  const lo = min - pad, hi = max + pad;
  const n = series.length;
  const X = (i) => (i / (n - 1)) * w;
  const Y = (v) => h - ((v - lo) / (hi - lo)) * h;
  const pts = series.map((p, i) => `${X(i).toFixed(1)},${Y(p.v).toFixed(1)}`).join(" ");
  const up = series[n - 1].v >= series[0].v;
  const color = up ? "var(--green)" : "var(--red)";
  const fill = up ? "rgba(158,206,106,.12)" : "rgba(247,118,142,.12)";
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="width:100%;height:${h}px;display:block">
    <polygon points="0,${h} ${pts} ${w},${h}" fill="${fill}"/>
    <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>`;
}

function drawChart() {
  const cutoff = Date.now() - range * 86400000;
  const series = EQUITY.filter((p) => new Date(p.t).getTime() >= cutoff);
  const use = series.length >= 2 ? series : EQUITY;
  document.getElementById("chart").innerHTML = svgChart(use);
  let delta = "";
  if (use.length >= 2) {
    const d = use[use.length - 1].v - use[0].v;
    const pctv = use[0].v ? (d / use[0].v) * 100 : 0;
    delta = `<span class="${cls(d)}">${pnl(d)} (${d >= 0 ? "+" : ""}${pctv.toFixed(1)}%)</span>`;
  }
  document.getElementById("chartDelta").innerHTML = delta;
}

function render(d) {
  const u = d.user, s = d.summary || {};
  EQUITY = d.equity || [];
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
      <div id="chart"></div></div>

    <div class="grid" style="margin-top:16px">
      <div class="card stat"><div class="label">Stocks</div><div class="value" style="font-size:20px">${money(s.stock_value)}</div></div>
      <div class="card stat"><div class="label">Options</div><div class="value" style="font-size:20px">${money(s.options_value)}</div></div>
      <div class="card stat"><div class="label">Cash</div><div class="value" style="font-size:20px">${money(s.cash)}</div></div>
      <div class="card stat"><div class="label">Realized P&L</div>
        <div class="value ${cls(s.realized_pnl || 0)}" style="font-size:20px">${pnl(s.realized_pnl || 0)}</div></div>
    </div>`;

  const sh = d.stock_holdings || [];
  html += `<h2>Stock Holdings</h2><div class="card" style="padding:0">
    ${sh.length ? `<table><thead><tr><th>Ticker</th><th class="num">Shares</th><th class="num">Avg</th><th class="num">Cost basis</th></tr></thead>
      <tbody>${sh.map((h) => `<tr><td><strong>${h.ticker}</strong></td><td class="num">${h.shares}</td>
        <td class="num muted">${money2(h.dca)}</td><td class="num">${money(h.cost_basis)}</td></tr>`).join("")}</tbody></table>`
      : `<div class="muted" style="padding:18px">No open stock positions.</div>`}</div>`;

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
    range = Number(b.dataset.days);
    document.querySelectorAll(".rangebtn").forEach((x) => x.classList.toggle("on", x === b));
    drawChart();
  });
}

function fmtDate(iso) {
  try {
    return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "2-digit" });
  } catch { return iso; }
}

main();
