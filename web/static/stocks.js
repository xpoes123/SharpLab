// SharpLab HQ — Stocks dashboard: everyone's portfolio.

const app = document.getElementById("app");
const sign = (n) => (n >= 0 ? "+" : "") + n;
const cls = (n) => (n >= 0 ? "pos" : "neg");
const money = (n) => (n == null ? "—" : "$" + Number(n).toLocaleString(undefined, { maximumFractionDigits: 0 }));
const pnl = (n) => (n >= 0 ? "+" : "−") + "$" + Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 });
const plink = (name) => `<a href="/hq/${encodeURIComponent(name)}">${name}</a>`;

let me = null;

async function main() {
  let data;
  try {
    [data, me] = await Promise.all([
      fetch("/api/v1/hq/stocks").then((r) => r.json()),
      fetch("/api/v1/hq/me", { credentials: "include" }).then((r) => (r.ok ? r.json() : null)).catch(() => null),
    ]);
  } catch {
    app.innerHTML = `<div class="hero"><p class="muted">Couldn't load portfolios.</p></div>`;
    return;
  }
  render(data.traders || []);
}

function tradePanel() {
  if (!me || !me.authenticated) return "";
  return `<div class="card" id="tradeCard" style="margin-bottom:16px">
    <div style="font-weight:700;font-size:15px;margin-bottom:10px">➕ New Trade
      <span class="muted" style="font-size:12px">— records to your portfolio (${me.user.username})</span></div>
    <div class="seg" id="instr">
      <button data-i="stock" class="on">Stock</button><button data-i="crypto">Crypto</button><button data-i="option">Option</button>
    </div>
    <div class="tradeform">
      <input id="tk" placeholder="Ticker" autocomplete="off" />
      <input id="qty" type="number" step="any" placeholder="Shares" />
      <input id="px" type="number" step="any" placeholder="Price" />
      <span id="optfields" style="display:none;gap:8px">
        <select id="ot" class="lbselect"><option value="call">Call</option><option value="put">Put</option></select>
        <input id="strike" type="number" step="any" placeholder="Strike" />
        <input id="exp" type="date" />
      </span>
    </div>
    <div style="margin-top:12px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <button id="buyBtn" class="btn buy">Buy</button>
      <button id="sellBtn" class="btn sell">Sell</button>
      <span id="tradeMsg" class="muted" style="font-size:13px"></span>
    </div></div>`;
}

function wireTradePanel() {
  const card = document.getElementById("tradeCard");
  if (!card) return;
  const v = (id) => (document.getElementById(id).value || "").trim();
  card.querySelectorAll("#instr button").forEach((b) => b.addEventListener("click", () => {
    card.querySelectorAll("#instr button").forEach((x) => x.classList.remove("on"));
    b.classList.add("on");
    const isOpt = b.dataset.i === "option";
    document.getElementById("optfields").style.display = isOpt ? "inline-flex" : "none";
    document.getElementById("qty").placeholder = isOpt ? "Contracts" : "Shares";
    document.getElementById("px").placeholder = isOpt ? "Premium" : "Price";
    document.getElementById("tk").placeholder = b.dataset.i === "crypto" ? "BTC / ETH" : "Ticker";
  }));

  async function submit(side) {
    const instr = card.querySelector("#instr .on").dataset.i;
    const tk = v("tk").toUpperCase(), qty = parseFloat(v("qty")), px = parseFloat(v("px"));
    const msg = document.getElementById("tradeMsg");
    if (!tk || !(qty > 0) || !(px > 0)) { msg.className = "neg"; msg.textContent = "Fill ticker, quantity, and price."; return; }
    let url, body;
    if (instr === "option") {
      const strike = parseFloat(v("strike")), exp = v("exp");
      if (!(strike > 0) || !exp) { msg.className = "neg"; msg.textContent = "Options need a strike and expiry."; return; }
      url = "/api/v1/hq/options/trade";
      body = { underlying: tk, opt_type: v("ot"), strike, expiry: exp, side, contracts: Math.round(qty), premium: px };
    } else {
      url = "/api/v1/hq/stocks/trade";
      body = { ticker: tk, side, shares: qty, price: px };
    }
    msg.className = "muted"; msg.textContent = "Recording…";
    try {
      const r = await fetch(url, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { msg.className = "neg"; msg.textContent = j.error || "Couldn't record that."; return; }
      msg.className = "pos"; msg.textContent = `✓ ${side === "buy" ? "Bought" : "Sold"} ${qty} ${tk}`;
      ["tk", "qty", "px", "strike"].forEach((id) => { const el = document.getElementById(id); if (el) el.value = ""; });
      setTimeout(main, 1000);
    } catch { msg.className = "neg"; msg.textContent = "Network error."; }
  }
  document.getElementById("buyBtn").addEventListener("click", () => submit("buy"));
  document.getElementById("sellBtn").addEventListener("click", () => submit("sell"));
}

function render(traders) {
  if (!traders.length) {
    app.innerHTML = tradePanel() + `<div class="hero"><h1>No traders yet</h1>
      <p class="muted">Buy something above or with <code>/stock buy</code> to appear here.</p></div>`;
    wireTradePanel();
    return;
  }
  const total = traders.reduce((s, t) => s + (t.account_value || 0), 0);
  let html = tradePanel() + `<h2>📈 Stock Portfolios</h2>
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
    const day = t.day_change == null ? ""
      : `<div class="${cls(t.day_change)}" style="font-size:13px">${pnl(t.day_change)} (${t.day_pct >= 0 ? "+" : ""}${t.day_pct}%) today</div>`;
    return `<div class="card" style="margin-top:14px">
      <div style="display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap">
        <div><span class="muted">#${i + 1}</span> <a href="${link}" style="font-size:18px;font-weight:800">${t.username}</a></div>
        <div style="text-align:right">
          <a href="${link}" style="font-size:22px;font-weight:800;color:var(--fg)">${money(t.account_value)} →</a>${day}
        </div>
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
  wireTradePanel();
}

main();
