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
      ? `<span class="pill">🔒 locked: ${g.my_stake}u on ${g.my_pick === "away" ? g.away_team : g.home_team}</span>`
      : "";
    let controls;
    if (!loggedIn) {
      controls = `<div class="teamrow muted">✈️ ${g.away_team} ${ap} · 🏠 ${g.home_team} ${hp}
        — <a href="/api/v1/auth/discord/login">sign in to bet</a></div>`;
    } else if (g.my_pick) {
      controls = `<div class="teamrow muted">Bet locked — bets are final.</div>`;
    } else {
      controls = `<div class="teamrow" data-mid="${g.message_id}">
           <button class="btn ghost team" data-team="away">✈️ ${g.away_team} ${ap}</button>
           <button class="btn ghost team" data-team="home">🏠 ${g.home_team} ${hp}</button>
           <span class="stakebtns" data-stakes hidden>
             ${[1, 2, 3, 4, 5].map((n) => `<button data-stake="${n}">${n}u</button>`).join("")}
           </span>
           <span class="betmsg muted"></span>
         </div>`;
    }
    return `<div class="gamecard">
      <div style="margin-bottom:2px">${em} <strong>${g.away_team}</strong> @ <strong>${g.home_team}</strong> ${mine}</div>
      <div class="muted" style="font-size:12px;margin-bottom:8px">🕒 ${fmtGameTime(g.start_time)}</div>
      ${controls}
      ${betsDetails(g)}</div>`;
  }).join("");
  return `<h2>🎯 Pick'em — bet 1–5 units</h2><div class="card" style="padding:0">${rows}</div>`;
}

function fmtGameTime(iso) {
  try {
    return new Date(iso).toLocaleString("en-US", {
      weekday: "short", month: "short", day: "numeric",
      hour: "numeric", minute: "2-digit",
      timeZone: "America/New_York", timeZoneName: "short",
    });
  } catch { return ""; }
}

function betsDetails(g) {
  const bets = g.bets || [];
  if (!bets.length) return `<div class="muted" style="margin-top:8px;font-size:13px">No bets yet.</div>`;
  const lines = bets.map((b) =>
    `${b.username} — <strong>${b.stake}u</strong> on ${b.pick === "away" ? g.away_team : g.home_team}`).join("<br>");
  return `<details style="margin-top:8px">
    <summary class="muted" style="cursor:pointer;font-size:13px">👥 ${bets.length} bet${bets.length > 1 ? "s" : ""} so far</summary>
    <div style="padding:8px 4px 2px;font-size:13px">${lines}</div></details>`;
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
      if (r.ok) {
        row.innerHTML = `<span class="pos">🔒 Locked ${stakeBtn.dataset.stake}u — bets are final.</span>`;
      } else {
        const err = (await r.json().catch(() => ({}))).error;
        msg.textContent = err === "already_bet" ? "✖ already bet — final" : "✖ closed";
        msg.className = "betmsg neg";
        row.querySelectorAll("button").forEach((b) => (b.disabled = true));
      }
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
    const prog = me.progression || {};
    const pa = prog.achievements || {};
    html += `<h2>Your dashboard</h2><div class="grid">
      <div class="card stat"><div class="label">Level</div>
        <div class="value">${prog.level || 1}</div>
        <div class="muted" style="font-size:12px">${num(prog.total_xp || 0)} XP · ${pa.unlocked_count || 0}/${pa.total || 0} 🏆</div></div>
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

  // Unified leaderboard: overview boards + one option per ranked game
  const myName = me && me.user.username;
  const eloOpts = (server.elo_games || [])
    .map((g) => `<option value="elo:${g.key}">${ELO_LABELS[g.key] || g.key} (${g.plays})</option>`).join("");
  html += `<h2>Leaderboards</h2><div class="card" style="padding:0">
    <div style="padding:12px 16px;border-bottom:1px solid var(--line)">
      <select id="lbSelect" class="lbselect">
        <optgroup label="Overview">
          ${BOARD_DEFS.map((b) => `<option value="${b.key}">${b.label}</option>`).join("")}
        </optgroup>
        <optgroup label="Game ELO (most played first)">${eloOpts}</optgroup>
      </select>
    </div>
    <div id="lbBody"></div></div>`;

  app.innerHTML = html;

  const lbBody = document.getElementById("lbBody");
  const lbSelect = document.getElementById("lbSelect");
  const draw = () => { lbBody.innerHTML = boardTable(server, lbSelect.value, myName); };
  lbSelect.addEventListener("change", draw);
  draw();
}

const ELO_LABELS = {
  cluemaster: "Clue Master", imposter: "Imposter", wordle: "Wordle", pokemon: "Pokemon",
  quizbowl: "Quiz Bowl", geography: "Geography", countdown: "Countdown", mathsprint: "Math Sprint",
  math24: "Math 24", stockguess: "Stock Guess", nbaguess: "NBA Guess", sequence: "Sequence",
  prisoner: "Prisoner's Dilemma", mastermind: "Mastermind", liarsdice: "Liar's Dice",
  blotto: "Colonel Blotto", figgie: "Figgie", sudoku: "Sudoku", bingo: "Bingo",
  minesweeper: "Minesweeper", valorant: "Valorant", indian_poker: "Indian Poker",
  solitairechess: "Solitaire Chess", tradingfloor: "Trading Floor", rps: "RPS",
  "nba-trivia": "NBA Trivia", "nfl-trivia": "NFL Trivia",
};

const BOARD_DEFS = [
  { key: "levels", label: "🎉 Levels — Top XP" },
  { key: "achievements", label: "🏆 Achievements — Unlocked" },
  { key: "pickem", label: "🎯 Pick'em — Market P&L" },
  { key: "stocks", label: "📈 Stocks — Portfolio Value" },
  { key: "casino", label: "🪙 Casino — Balances" },
  { key: "chess", label: "♟️ Hearthstone Chess" },
];

function boardTable(server, key, myName) {
  if (key.startsWith("elo:")) {
    const g = (server.elo_games || []).find((x) => x.key === key.slice(4));
    return table([{ t: "Player" }, { t: "Rating", num: true }, { t: "W/L", num: true }],
      (g ? g.rows : []).map((r) => [{ t: plink(r.username) }, { t: r.rating },
        { t: `${r.wins}/${r.losses}`, cls: "muted" }]));
  }
  if (key === "pickem")
    return table([{ t: "Player" }, { t: "Units", num: true }, { t: "Record", num: true }],
      (server.pickem || []).map((r) => {
        const row = [{ t: plink(r.username) }, { t: sign(r.units.toFixed(1)) + "u", cls: cls(r.units) },
          { t: `${r.correct}/${r.total}`, cls: "muted" }];
        row._me = r.username === myName; return row;
      }));
  if (key === "casino")
    return table([{ t: "Player" }, { t: "Coins", num: true }],
      (server.casino || []).map((r) => [{ t: plink(r.username) }, { t: num(r.balance) }]));
  if (key === "stocks")
    return table([{ t: "Trader" }, { t: "Value", num: true }],
      (server.stocks || []).map((r) => [{ t: plink(r.username) },
        { t: "$" + num(r.account_value) }]));
  if (key === "chess")
    return table([{ t: "Player" }, { t: "Rating", num: true }, { t: "Record", num: true }],
      (server.chess || []).map((r) => [{ t: r.handle }, { t: r.rating },
        { t: `${r.wins}-${r.losses}${r.draws ? "-" + r.draws : ""}`, cls: "muted" }]));
  if (key === "levels")
    return table([{ t: "Player" }, { t: "Level", num: true }, { t: "XP", num: true }],
      (server.levels || []).map((r) => [{ t: plink(r.username) }, { t: "Lv " + r.level }, { t: num(r.xp) }]));
  if (key === "achievements")
    return table([{ t: "Player" }, { t: "Unlocked", num: true }],
      (server.achievements || []).map((r) => [{ t: plink(r.username) }, { t: `${r.unlocked}/${r.total}` }]));
  return "";
}

main();
