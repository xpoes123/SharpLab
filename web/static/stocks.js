// SharpLab HQ — Stocks dashboard: everyone's portfolio.

const app = document.getElementById("app");
const sign = (n) => (n >= 0 ? "+" : "") + n;
const cls = (n) => (n >= 0 ? "pos" : "neg");
const money = (n) => (n == null ? "—" : "$" + Number(n).toLocaleString(undefined, { maximumFractionDigits: 0 }));
const pnl = (n) => (n >= 0 ? "+" : "−") + "$" + Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 });
const plink = (name) => `<a href="/hq/${encodeURIComponent(name)}">${name}</a>`;

async function main() {
  let data;
  try {
    data = await (await fetch("/api/v1/hq/stocks")).json();
  } catch {
    app.innerHTML = `<div class="hero"><p class="muted">Couldn't load portfolios.</p></div>`;
    return;
  }
  render(data.traders || []);
}

function render(traders) {
  if (!traders.length) {
    app.innerHTML = `<div class="hero"><h1>No traders yet</h1>
      <p class="muted">Buy something with <code>/stock buy</code> to appear here.</p></div>`;
    return;
  }
  const total = traders.reduce((s, t) => s + (t.account_value || 0), 0);
  let html = `<h2>📈 Stock Portfolios</h2>
    <div class="grid">
      <div class="card stat"><div class="label">Traders</div><div class="value">${traders.length}</div></div>
      <div class="card stat"><div class="label">Combined Value</div><div class="value">${money(total)}</div></div>
    </div>`;

  html += traders.map((t, i) => {
    const rp = t.realized_pnl || 0;
    const holdings = t.holdings || [];
    const rows = holdings.map((h) =>
      `<tr><td>${h.ticker}</td><td class="num">${h.shares}</td>
        <td class="num muted">$${h.dca}</td><td class="num">${money(h.cost_basis)}</td></tr>`).join("");
    const detail = holdings.length
      ? `<details style="margin-top:10px">
           <summary class="muted" style="cursor:pointer;font-size:13px">${t.positions} position${t.positions > 1 ? "s" : ""}</summary>
           <table style="margin-top:8px"><thead><tr><th>Ticker</th><th class="num">Shares</th>
             <th class="num">Avg</th><th class="num">Cost basis</th></tr></thead><tbody>${rows}</tbody></table>
         </details>`
      : `<div class="muted" style="margin-top:8px;font-size:13px">No open positions.</div>`;
    const link = `/hq/stocks/${encodeURIComponent(t.username)}`;
    return `<div class="card" style="margin-top:14px">
      <div style="display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap">
        <div><span class="muted">#${i + 1}</span> <a href="${link}" style="font-size:18px;font-weight:800">${t.username}</a></div>
        <a href="${link}" style="font-size:22px;font-weight:800;color:var(--fg)">${money(t.account_value)} →</a>
      </div>
      <div class="grid" style="margin-top:12px">
        <div class="stat"><div class="label">Stocks</div><div class="value" style="font-size:18px">${money(t.stock_value)}</div></div>
        <div class="stat"><div class="label">Options</div><div class="value" style="font-size:18px">${money(t.options_value)}</div></div>
        <div class="stat"><div class="label">Cash</div><div class="value" style="font-size:18px">${money(t.cash)}</div></div>
        <div class="stat"><div class="label">Realized P&L</div>
          <div class="value ${cls(rp)}" style="font-size:18px">${pnl(rp)}</div></div>
      </div>
      ${detail}</div>`;
  }).join("");

  app.innerHTML = html;
}

main();
