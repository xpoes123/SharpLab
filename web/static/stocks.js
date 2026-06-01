// SharpLab HQ — Stocks dashboard: everyone's portfolio.

const app = document.getElementById("app");
const sign = (n) => (n >= 0 ? "+" : "") + n;
const cls = (n) => (n >= 0 ? "pos" : "neg");
const money = (n) => (n == null ? "—" : "$" + Number(n).toLocaleString(undefined, { maximumFractionDigits: 0 }));
const pnl = (n) => (n >= 0 ? "+" : "−") + "$" + Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 });
const plink = (name) => `<a href="/hq/${encodeURIComponent(name)}">${name}</a>`;
const pct = (n) => (n == null ? "—" : (n >= 0 ? "+" : "") + n.toFixed(2) + "%");

const STYLE = `<style>
.lbhero{display:flex;justify-content:space-between;gap:24px;flex-wrap:wrap;background:linear-gradient(135deg,var(--panel),var(--panel2));border:1px solid var(--line);border-radius:16px;padding:22px 24px;margin:6px 0 18px}
.lbhero .lbl{font-size:11px;text-transform:uppercase;letter-spacing:.6px}
.lbhero .bigval{font-size:40px;font-weight:800;line-height:1.1;margin:2px 0 10px}
.herometa{display:flex;gap:18px;flex-wrap:wrap;font-size:14px;font-weight:600}
.herometa b{color:var(--fg)}
.lbhero-side{display:flex;flex-direction:column;gap:14px;justify-content:center;min-width:170px}
.sidestat .v{font-size:15px;margin-top:2px;font-weight:600}
.lblist{display:flex;flex-direction:column;gap:12px}
.lbcard{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px 16px;transition:border-color .15s}
.lbcard:hover{border-color:var(--accent)}
.lbtop{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.lbid{display:flex;align-items:center;gap:12px}
.rankbadge{width:30px;height:30px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:13px;background:var(--panel2);color:var(--muted);flex:none}
.rankbadge.r1{background:var(--gold);color:#15161e}
.rankbadge.r2{background:#c3c9d6;color:#15161e}
.rankbadge.r3{background:#cd9b63;color:#15161e}
.lbname{font-size:18px;font-weight:800;color:var(--fg)}
.sm{font-size:12px}
.lbval{display:flex;flex-direction:column;align-items:flex-end;gap:3px}
.lbval a{font-size:22px;font-weight:800;color:var(--fg)}
.lbspark{line-height:0}
.daypill{display:inline-block;font-size:12px;font-weight:700;padding:2px 9px;border-radius:999px;margin-top:3px;background:var(--panel2)}
.daypill.pos{color:var(--green)}.daypill.neg{color:var(--red)}
.lbmini{display:flex;gap:24px;flex-wrap:wrap;margin-top:12px;padding-top:12px;border-top:1px solid var(--line)}
.mini{display:flex;flex-direction:column;gap:2px;font-size:14px;font-weight:700}
.mini .k{font-size:11px;text-transform:uppercase;letter-spacing:.5px;font-weight:500;color:var(--muted)}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:12px}
.chip{display:inline-flex;align-items:center;gap:5px;font-size:12px;font-weight:700;padding:3px 9px;border-radius:8px;background:var(--panel2);border:1px solid var(--line);color:var(--fg)}
.chip .w{font-size:11px;color:var(--muted);font-weight:500}
.chip.pos{border-color:#9ece6a55}.chip.neg{border-color:#f7768e55}
.chip.more{color:var(--muted)}
a.chip:hover{border-color:var(--accent)}
</style>`;

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

function spark(vals) {
  if (!vals || vals.length < 2) return "";
  const w = 150, h = 34, min = Math.min(...vals), max = Math.max(...vals), rng = (max - min) || 1;
  const pts = vals.map((v, i) => `${((i / (vals.length - 1)) * w).toFixed(1)},${(h - ((v - min) / rng) * h).toFixed(1)}`).join(" ");
  const col = vals[vals.length - 1] >= vals[0] ? "var(--green)" : "var(--red)";
  return `<div class="lbspark"><svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <polyline points="${pts}" fill="none" stroke="${col}" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg></div>`;
}

function tradeCard(t, i) {
  const link = `/hq/stocks/${encodeURIComponent(t.username)}`;
  const rank = i < 3 ? `<div class="rankbadge r${i + 1}">${i + 1}</div>` : `<div class="rankbadge">${i + 1}</div>`;
  const avatar = t.avatar_url ? `<img class="avatar" src="${t.avatar_url}" alt="">` : `<div class="avatar"></div>`;
  const day = t.day_change == null ? ""
    : `<div><span class="daypill ${cls(t.day_change)}">${pnl(t.day_change)} · ${pct(t.day_pct)} today</span></div>`;
  const acct = t.account_value || 0;
  const mini = (k, v, c = "") => `<div class="mini"><span class="k">${k}</span><span class="${c}">${v}</span></div>`;
  const chips = (t.holdings || []).slice(0, 7).map((h) => {
    const raw = h.value && acct ? (h.value / acct) * 100 : null;
    const wTxt = raw == null ? "" : (raw < 0.95 ? "<1%" : Math.round(raw) + "%");
    const sign = h.unrealized == null ? 0 : h.unrealized;
    return `<a href="${link}" class="chip ${cls(sign)}">${h.ticker}${wTxt ? ` <span class="w">${wTxt}</span>` : ""}</a>`;
  }).join("");
  const more = (t.holdings || []).length > 7 ? `<span class="chip more">+${t.holdings.length - 7} more</span>` : "";
  return `<div class="lbcard">
    <div class="lbtop">
      <div class="lbid">${rank}${avatar}
        <div><a href="${link}" class="lbname">${t.username}</a>
          <div class="muted sm">${t.positions} position${t.positions !== 1 ? "s" : ""}</div></div></div>
      <div class="lbval">${spark(t.spark)}<a href="${link}">${money(acct)}</a>${day}</div>
    </div>
    <div class="lbmini">
      ${mini("Stocks", money(t.stock_value))}
      ${mini("Options", money(t.options_value))}
      ${mini("Cash", money(t.cash))}
      ${mini("Unrealized", t.unrealized_pnl == null ? "—" : pnl(t.unrealized_pnl), cls(t.unrealized_pnl || 0))}
      ${mini("Realized", pnl(t.realized_pnl || 0), cls(t.realized_pnl || 0))}
    </div>
    ${chips ? `<div class="chips">${chips}${more}</div>` : ""}
  </div>`;
}

function render(traders) {
  if (!traders.length) {
    app.innerHTML = tradePanel() + `<div class="hero"><h1>No traders yet</h1>
      <p class="muted">Buy something above or with <code>/stock buy</code> to appear here.</p></div>`;
    wireTradePanel();
    return;
  }
  const sum = (f) => traders.reduce((s, t) => s + (f(t) || 0), 0);
  const totVal = sum((t) => t.account_value), totDay = sum((t) => t.day_change);
  const totReal = sum((t) => t.realized_pnl), totUnreal = sum((t) => t.unrealized_pnl);
  const movers = traders.filter((t) => t.day_pct != null);
  const top = movers.length ? movers.reduce((a, b) => (b.day_pct > a.day_pct ? b : a)) : null;
  const counts = {};
  traders.forEach((t) => (t.holdings || []).forEach((h) => (counts[h.ticker] = (counts[h.ticker] || 0) + 1)));
  const popular = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];

  let html = STYLE + tradePanel() + `<div class="lbhero">
    <div class="lbhero-main">
      <div class="muted lbl">Combined portfolio value</div>
      <div class="bigval">${money(totVal)}</div>
      <div class="herometa">
        <span><b>${traders.length}</b> traders</span>
        <span class="${cls(totDay)}">${pnl(totDay)} today</span>
        <span class="${cls(totUnreal)}">${pnl(totUnreal)} unrealized</span>
        <span class="${cls(totReal)}">${pnl(totReal)} realized</span>
      </div>
    </div>
    <div class="lbhero-side">
      ${top ? `<div class="sidestat"><div class="muted lbl">Top mover today</div>
        <div class="v">${plink2(top)} <span class="${cls(top.day_pct)}">${pct(top.day_pct)}</span></div></div>` : ""}
      ${popular ? `<div class="sidestat"><div class="muted lbl">Most held</div>
        <div class="v"><b>${popular[0]}</b> <span class="muted">held by ${popular[1]}</span></div></div>` : ""}
    </div>
  </div>
  <div class="lblist">${traders.map(tradeCard).join("")}</div>`;

  app.innerHTML = html;
  wireTradePanel();
}

function plink2(t) {
  return `<a href="/hq/stocks/${encodeURIComponent(t.username)}" style="color:var(--fg);font-weight:700">${t.username}</a>`;
}

main();
