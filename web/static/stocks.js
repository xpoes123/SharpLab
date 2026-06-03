// SharpLab HQ — Stocks dashboard: everyone's portfolio.

const app = document.getElementById("app");
const sign = (n) => (n >= 0 ? "+" : "") + n;
const cls = (n) => (n >= 0 ? "pos" : "neg");
const money = (n) => (n == null ? "—" : "$" + Number(n).toLocaleString(undefined, { maximumFractionDigits: 0 }));
const pnl = (n) => (n >= 0 ? "+" : "−") + "$" + Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 });
const plink = (name) => `<a href="/u/${encodeURIComponent(name)}">${name}</a>`;
const pct = (n) => (n == null ? "—" : (n >= 0 ? "+" : "") + n.toFixed(2) + "%");

const STYLE = `<style>
.lbhero{background:linear-gradient(135deg,var(--panel),var(--panel2));border:1px solid var(--line);border-radius:16px;padding:22px 24px;margin:6px 0 18px}
.lbhero .lbl{font-size:11px;text-transform:uppercase;letter-spacing:.6px}
.lbhero .bigval{font-size:38px;font-weight:800;line-height:1.1;margin-top:2px}
.lbhero-top{display:flex;justify-content:space-between;align-items:flex-start;gap:24px;flex-wrap:wrap}
.herostats{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:16px;margin-top:18px;padding-top:16px;border-top:1px solid var(--line)}
.hstat .k{font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:var(--muted);margin-bottom:4px}
.hstat .hv{font-size:16px;font-weight:700}
.hstat .hv b{color:var(--fg)}
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
      <input id="tk" placeholder="Ticker — sell? pick a holding" list="myStocks" autocomplete="off" />
      <datalist id="myStocks">${MY_HOLDINGS.map((h) => `<option value="${h.ticker}">${h.ticker} · ${h.shares} sh</option>`).join("")}</datalist>
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
  const link = `/stocks/${encodeURIComponent(t.username)}`;
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

const SPIE_COLORS = ["#7aa2f7", "#bb9af7", "#9ece6a", "#e0af68", "#f7768e", "#7dcfff",
  "#ff9e64", "#2ac3de", "#c0caf5", "#9d7cd8", "#41a6b5", "#73daca"];
const SPIE_GREY = "#565f89";
const SPIE_MODES = [["stock", "By stock"], ["trader", "By trader"], ["account", "Stocks / Options / Cash"]];
let serverPieMode = "stock";
let TRADERS = [];
let MY_HOLDINGS = [];   // the logged-in user's holdings → ticker datalist for the trade form

function serverSlices() {
  if (serverPieMode === "account") {
    const s = (f) => TRADERS.reduce((a, t) => a + (f(t) || 0), 0);
    return [{ label: "Stocks", value: s((t) => t.stock_value) },
            { label: "Options", value: s((t) => t.options_value) },
            { label: "Cash", value: s((t) => t.cash), grey: true }].filter((x) => x.value > 0);
  }
  if (serverPieMode === "trader") {
    return TRADERS.map((t) => ({ label: t.username, value: t.account_value || 0 }))
      .filter((x) => x.value > 0).sort((a, b) => b.value - a.value);
  }
  const agg = {};
  TRADERS.forEach((t) => (t.holdings || []).forEach((h) => {
    if (h.value) agg[h.ticker] = (agg[h.ticker] || 0) + h.value;
  }));
  return Object.entries(agg).map(([label, value]) => ({ label, value })).sort((a, b) => b.value - a.value);
}

function sDonut(slices, total) {
  const r = 70, sw = 30, cx = 90, cy = 90, C = 2 * Math.PI * r;
  let off = 0;
  const arcs = slices.map((x, i) => {
    const len = (x.value / total) * C;
    const col = x.grey ? SPIE_GREY : SPIE_COLORS[i % SPIE_COLORS.length];
    const a = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${col}" stroke-width="${sw}" stroke-dasharray="${len.toFixed(2)} ${(C - len).toFixed(2)}" stroke-dashoffset="${(-off).toFixed(2)}" transform="rotate(-90 ${cx} ${cy})"/>`;
    off += len;
    return a;
  }).join("");
  return `<svg width="180" height="180" viewBox="0 0 180 180" style="flex:none">${arcs}
    <text x="${cx}" y="${cy - 4}" text-anchor="middle" fill="var(--muted)" font-size="11">Server</text>
    <text x="${cx}" y="${cy + 14}" text-anchor="middle" fill="var(--fg)" font-size="15" font-weight="700">${money(total)}</text></svg>`;
}

function serverPieBody() {
  let slices = serverSlices();
  const total = slices.reduce((s, x) => s + x.value, 0);
  if (!total) return `<div class="muted" style="padding:18px">No holdings yet.</div>`;
  if (slices.length > 11) {
    const rest = slices.slice(11).reduce((s, x) => s + x.value, 0);
    slices = [...slices.slice(0, 11), { label: "Other", value: rest, grey: true }];
  }
  const toggle = `<div class="seg" id="spieToggle" style="margin-bottom:14px">${SPIE_MODES.map(([m, lbl]) =>
    `<button data-spie="${m}" class="${m === serverPieMode ? "on" : ""}">${lbl}</button>`).join("")}</div>`;
  const legend = `<div style="flex:1;min-width:220px;display:flex;flex-direction:column;gap:6px">${slices.map((x, i) =>
    `<div style="display:flex;align-items:center;gap:8px;font-size:13px">
      <span style="width:11px;height:11px;border-radius:3px;background:${x.grey ? SPIE_GREY : SPIE_COLORS[i % SPIE_COLORS.length]};flex:none"></span>
      <span style="font-weight:600">${x.label}</span>
      <span class="muted" style="margin-left:auto">${money(x.value)} · ${(x.value / total * 100).toFixed(1)}%</span>
    </div>`).join("")}</div>`;
  return `${toggle}<div style="display:flex;gap:24px;align-items:center;flex-wrap:wrap">${sDonut(slices, total)}${legend}</div>`;
}

function render(traders) {
  if (!traders.length) {
    app.innerHTML = tradePanel() + `<div class="hero"><h1>No traders yet</h1>
      <p class="muted">Buy something above or with <code>/stock buy</code> to appear here.</p></div>`;
    wireTradePanel();
    return;
  }
  TRADERS = traders;
  const myUid = me && me.user ? me.user.id : null;
  MY_HOLDINGS = myUid ? ((traders.find((t) => t.user_id === myUid) || {}).holdings || []) : [];
  const sum = (f) => traders.reduce((s, t) => s + (f(t) || 0), 0);
  const totVal = sum((t) => t.account_value), totDay = sum((t) => t.day_change);
  const totReal = sum((t) => t.realized_pnl), totUnreal = sum((t) => t.unrealized_pnl);
  const movers = traders.filter((t) => t.day_pct != null);
  const top = movers.length ? movers.reduce((a, b) => (b.day_pct > a.day_pct ? b : a)) : null;
  const counts = {};
  traders.forEach((t) => (t.holdings || []).forEach((h) => (counts[h.ticker] = (counts[h.ticker] || 0) + 1)));
  const popular = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];

  const totPnl = totUnreal + totReal;
  const totPos = traders.reduce((s, t) => s + (t.positions || 0), 0);
  // Total invested ≈ combined value − total P&L; gives a server-wide return %.
  const invested = totVal - totPnl;
  const totRet = invested > 0 ? (totPnl / invested) * 100 : null;
  const hstat = (k, v) => `<div class="hstat"><div class="k">${k}</div><div class="hv">${v}</div></div>`;

  let html = STYLE + tradePanel() + `<div class="lbhero">
    <div class="lbhero-top">
      <div><div class="muted lbl">Combined portfolio value</div><div class="bigval">${money(totVal)}</div></div>
      <div style="text-align:right">
        <div class="muted lbl">Server total P&L</div>
        <div class="bigval ${cls(totPnl)}">${pnl(totPnl)}${totRet != null ? ` <span style="font-size:18px">(${pct(totRet)})</span>` : ""}</div>
      </div>
    </div>
    <div class="herostats">
      ${hstat("Today", `<span class="${cls(totDay)}">${pnl(totDay)}</span>`)}
      ${hstat("Unrealized", `<span class="${cls(totUnreal)}">${pnl(totUnreal)}</span>`)}
      ${hstat("Realized", `<span class="${cls(totReal)}">${pnl(totReal)}</span>`)}
      ${hstat("Traders", traders.length)}
      ${hstat("Open positions", totPos)}
      ${top ? hstat("Top mover today", `${plink2(top)} <span class="${cls(top.day_pct)}">${pct(top.day_pct)}</span>`) : ""}
      ${popular ? hstat("Most held", `<b>${popular[0]}</b> <span class="muted" style="font-weight:500">×${popular[1]}</span>`) : ""}
    </div>
  </div>
  <div style="margin:2px 0 8px"><a href="/earnings" class="btn ghost">📅 Earnings calendar →</a></div>
  <h2 style="margin:18px 0 10px">Server allocation</h2>
  <div class="card" id="spieBox">${serverPieBody()}</div>
  <div class="lblist">${traders.map(tradeCard).join("")}</div>`;

  app.innerHTML = html;
  wireTradePanel();
  app.addEventListener("click", (e) => {
    const b = e.target.closest("[data-spie]");
    if (!b) return;
    serverPieMode = b.dataset.spie;
    document.getElementById("spieBox").innerHTML = serverPieBody();
  });
}

function plink2(t) {
  return `<a href="/stocks/${encodeURIComponent(t.username)}" style="color:var(--fg);font-weight:700">${t.username}</a>`;
}

main();
