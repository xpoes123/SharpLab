// SharpLab HQ — server home + your dashboard.

const sign = (n) => (n >= 0 ? "+" : "") + n;
const cls = (n) => (n >= 0 ? "pos" : "neg");
const num = (n) => (n == null ? "—" : Number(n).toLocaleString());
const plink = (name) => `<a href="/hq/${encodeURIComponent(name)}">${name}</a>`;

const app = document.getElementById("app");
const navRight = document.getElementById("navRight");

async function getJSON(url) {
  const r = await fetch(url, { credentials: "include" });
  if (!r.ok) return { _status: r.status };
  return r.json();
}

const SPORT_EMOJI = { nba: "🏀", mlb: "⚾" };
const pct = (p) => (p == null ? "" : Math.round(p * 100) + "%");

async function main() {
  const params = new URLSearchParams(location.search);
  const notMember = params.get("error") === "not_member";
  const [server, me, open] = await Promise.all([
    getJSON("/api/v1/hq/server"),
    getJSON("/api/v1/hq/me"),
    getJSON("/api/v1/hq/pickem/open"),
  ]);
  const loggedIn = me && me.authenticated;
  renderNav(loggedIn ? me.user : null);
  renderHome(server || {}, loggedIn ? me : null, notMember, open || {});
  wireBetting();
}

function pickemSection(open, loggedIn) {
  const games = open.games || [];
  if (!games.length) return "";
  const rows = games.map((g) => {
    const ap = pct(g.away_prob), hp = pct(g.home_prob);
    const em = SPORT_EMOJI[g.sport] || "🏟️";
    const mine = g.my_pick
      ? `<span class="pill">your bet: ${g.my_stake}u on ${g.my_pick === "away" ? g.away_team : g.home_team}</span>`
      : "";
    const controls = loggedIn
      ? `<div class="teamrow" data-mid="${g.message_id}">
           <button class="btn ghost team" data-team="away">✈️ ${g.away_team} ${ap}</button>
           <button class="btn ghost team" data-team="home">🏠 ${g.home_team} ${hp}</button>
           <span class="stakebtns" data-stakes hidden>
             ${[1, 2, 3, 4, 5].map((n) => `<button data-stake="${n}">${n}u</button>`).join("")}
           </span>
           <span class="betmsg muted"></span>
         </div>`
      : `<div class="teamrow muted">✈️ ${g.away_team} ${ap} · 🏠 ${g.home_team} ${hp}
           — <a href="/api/v1/auth/discord/login">sign in to bet</a></div>`;
    return `<div class="gamecard">
      <div style="margin-bottom:8px">${em} <strong>${g.away_team}</strong> @ <strong>${g.home_team}</strong> ${mine}</div>
      ${controls}</div>`;
  }).join("");
  return `<h2>🎯 Today's Pick'em — bet 1–5 units</h2><div class="card" style="padding:0">${rows}</div>`;
}

function wireBetting() {
  document.getElementById("app").addEventListener("click", async (e) => {
    const teamBtn = e.target.closest(".team");
    const stakeBtn = e.target.closest("[data-stake]");
    if (teamBtn) {
      const row = teamBtn.closest("[data-mid]");
      row.dataset.selected = teamBtn.dataset.team;
      row.querySelectorAll(".team").forEach((b) => (b.style.borderColor = ""));
      teamBtn.style.borderColor = "var(--accent)";
      row.querySelector("[data-stakes]").hidden = false;
    } else if (stakeBtn) {
      const row = stakeBtn.closest("[data-mid]");
      const team = row.dataset.selected;
      if (!team) return;
      const msg = row.querySelector(".betmsg");
      msg.textContent = "…";
      const r = await fetch("/api/v1/hq/pickem/bet", {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message_id: row.dataset.mid, team, stake: Number(stakeBtn.dataset.stake) }),
      });
      msg.textContent = r.ok ? `✅ ${stakeBtn.dataset.stake}u in` : "✖ closed";
      msg.className = r.ok ? "betmsg pos" : "betmsg neg";
    }
  });
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

function renderHome(server, me, notMember, open) {
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

  html += pickemSection(open || {}, !!me);

  html += `<h2>Server</h2><div class="grid">
    <div class="card stat"><div class="label">Coins in circulation</div><div class="value">${num(t.coins)}</div></div>
    <div class="card stat"><div class="label">Stock traders</div><div class="value">${num(t.stock_traders)}</div></div>
    <div class="card stat"><div class="label">Pick'em players</div><div class="value">${num(t.pickem_players)}</div></div></div>`;

  // ELO champions
  html += section("🏆 ELO Champions", table(
    [{ t: "Player" }, { t: "Pts", num: true }],
    (server.elo_champions || []).map((r) => [{ t: plink(r.username) }, { t: r.points }])));

  // Pick'em P&L
  const myName = me && me.user.username;
  html += section("🎯 Pick'em — Market P&amp;L", table(
    [{ t: "Player" }, { t: "Units", num: true }, { t: "Record", num: true }],
    (server.pickem || []).map((r) => {
      const row = [{ t: plink(r.username) }, { t: sign(r.units.toFixed(1)) + "u", cls: cls(r.units) },
        { t: `${r.correct}/${r.total}`, cls: "muted" }];
      row._me = r.username === myName; return row;
    })));

  // Casino
  html += section("🪙 Casino — Top Balances", table(
    [{ t: "Player" }, { t: "Coins", num: true }],
    (server.casino || []).map((r) => [{ t: plink(r.username) }, { t: num(r.balance) }])));

  // Stocks
  html += section("📈 Stocks — Realized P&amp;L", table(
    [{ t: "Trader" }, { t: "P&L ($)", num: true }],
    (server.stocks || []).map((r) => [{ t: plink(r.username) },
      { t: sign(num(r.realized_pnl)), cls: cls(r.realized_pnl) }])));

  // Chess
  html += section("♟️ Hearthstone Chess", table(
    [{ t: "Player" }, { t: "Rating", num: true }, { t: "Record", num: true }],
    (server.chess || []).map((r) => [{ t: r.handle }, { t: r.rating },
      { t: `${r.wins}-${r.losses}${r.draws ? "-" + r.draws : ""}`, cls: "muted" }])));

  app.innerHTML = html;
}

main();
