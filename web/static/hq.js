// SharpLab HQ — server home + your dashboard.

const sign = (n) => (n >= 0 ? "+" : "") + n;
const cls = (n) => (n >= 0 ? "pos" : "neg");
const num = (n) => (n == null ? "—" : Number(n).toLocaleString());

const app = document.getElementById("app");
const navRight = document.getElementById("navRight");

async function getJSON(url) {
  const r = await fetch(url, { credentials: "include" });
  if (!r.ok) return { _status: r.status };
  return r.json();
}

async function main() {
  const params = new URLSearchParams(location.search);
  const notMember = params.get("error") === "not_member";
  const [server, me] = await Promise.all([
    getJSON("/api/v1/hq/server"),
    getJSON("/api/v1/hq/me"),
  ]);
  const loggedIn = me && me.authenticated;
  renderNav(loggedIn ? me.user : null);
  renderHome(server || {}, loggedIn ? me : null, notMember);
}

function renderNav(user) {
  if (!user) {
    navRight.innerHTML = `<a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a>`;
    return;
  }
  const av = user.avatar ? `https://cdn.discordapp.com/avatars/${user.id}/${user.avatar}.png` : null;
  navRight.innerHTML = `<div class="userbar">
    ${av ? `<img class="avatar" src="${av}" alt="">` : `<div class="avatar"></div>`}
    <span>${user.username}</span>
    <a class="btn ghost" href="/api/v1/auth/logout">Sign out</a></div>`;
}

function table(headers, rows) {
  if (!rows.length) return `<div class="muted" style="padding:18px">No data yet.</div>`;
  const thead = headers.map((h) => `<th class="${h.num ? "num" : ""}">${h.t}</th>`).join("");
  const body = rows.map((cells, i) => {
    const tds = cells.map((c, j) =>
      `<td class="${headers[j].num ? "num " : ""}${c.cls || ""}">${c.t}</td>`).join("");
    return `<tr class="${cells._me ? "me" : ""}"><td class="rank">${i + 1}</td>${tds}</tr>`;
  }).join("");
  return `<table><thead><tr><th class="rank">#</th>${thead}</tr></thead><tbody>${body}</tbody></table>`;
}

function section(title, inner) {
  return `<h2>${title}</h2><div class="card" style="padding:0">${inner}</div>`;
}

function renderHome(server, me, notMember) {
  const t = server.totals || {};
  let html = "";

  if (notMember) {
    html += `<div class="card" style="border-color:var(--red);margin-bottom:16px">
      You're signed in but not a member of the SharpLab server, so personal stats are hidden.</div>`;
  }

  if (me) {
    const p = me.pickem || {};
    html += `<h2>Your dashboard</h2><div class="grid">
      <div class="card stat"><div class="label">Pick'em Units</div>
        <div class="value ${cls(p.units || 0)}">${sign((p.units || 0).toFixed(1))}u</div></div>
      <div class="card stat"><div class="label">Pick'em Record</div>
        <div class="value">${p.correct || 0}-${(p.total || 0) - (p.correct || 0)}
          <span class="muted" style="font-size:14px">(${p.accuracy || 0}%)</span></div></div>
      <div class="card stat"><div class="label">Streak Points</div>
        <div class="value">${p.points || 0}</div></div>
      <div class="card stat"><div class="label">Coins</div>
        <div class="value">${num(me.balance)}</div></div></div>`;
  } else {
    html += `<div class="card" style="margin-bottom:20px;display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">
      <div><strong>Sign in</strong> to see your units, ELO and to bet on pick'em from the browser.</div>
      <a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a></div>`;
  }

  html += `<h2>Server</h2><div class="grid">
    <div class="card stat"><div class="label">Coins in circulation</div><div class="value">${num(t.coins)}</div></div>
    <div class="card stat"><div class="label">Stock traders</div><div class="value">${num(t.stock_traders)}</div></div>
    <div class="card stat"><div class="label">Pick'em players</div><div class="value">${num(t.pickem_players)}</div></div></div>`;

  // ELO champions
  html += section("🏆 ELO Champions", table(
    [{ t: "Player" }, { t: "Pts", num: true }],
    (server.elo_champions || []).map((r) => [{ t: r.username }, { t: r.points }])));

  // Pick'em P&L
  const myName = me && me.user.username;
  html += section("🎯 Pick'em — Market P&amp;L", table(
    [{ t: "Player" }, { t: "Units", num: true }, { t: "Record", num: true }],
    (server.pickem || []).map((r) => {
      const row = [{ t: r.username }, { t: sign(r.units.toFixed(1)) + "u", cls: cls(r.units) },
        { t: `${r.correct}/${r.total}`, cls: "muted" }];
      row._me = r.username === myName; return row;
    })));

  // Casino
  html += section("🪙 Casino — Top Balances", table(
    [{ t: "Player" }, { t: "Coins", num: true }],
    (server.casino || []).map((r) => [{ t: r.username }, { t: num(r.balance) }])));

  // Stocks
  html += section("📈 Stocks — Realized P&amp;L", table(
    [{ t: "Trader" }, { t: "P&L ($)", num: true }],
    (server.stocks || []).map((r) => [{ t: r.username },
      { t: sign(num(r.realized_pnl)), cls: cls(r.realized_pnl) }])));

  // Chess
  html += section("♟️ Hearthstone Chess", table(
    [{ t: "Player" }, { t: "Rating", num: true }, { t: "Record", num: true }],
    (server.chess || []).map((r) => [{ t: r.handle }, { t: r.rating },
      { t: `${r.wins}-${r.losses}${r.draws ? "-" + r.draws : ""}`, cls: "muted" }])));

  app.innerHTML = html;
}

main();
