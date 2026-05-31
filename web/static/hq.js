// SharpLab HQ — Discord-authed dashboard.

const ELO_LABELS = {
  cluemaster: "Clue Master", imposter: "Imposter", wordle: "Wordle", pokemon: "Pokemon",
  quizbowl: "Quiz Bowl", geography: "Geography", countdown: "Countdown", mathsprint: "Math Sprint",
  math24: "Math 24", stockguess: "Stock Guess", nbaguess: "NBA Guess", sequence: "Sequence",
  prisoner: "Prisoner's Dilemma", mastermind: "Mastermind", liarsdice: "Liar's Dice",
  blotto: "Colonel Blotto", figgie: "Figgie", sudoku: "Sudoku", bingo: "Bingo",
  minesweeper: "Minesweeper", valorant: "Valorant", indian_poker: "Indian Poker",
  solitairechess: "Solitaire Chess", tradingfloor: "Trading Floor", rps: "RPS",
};
const label = (k) => ELO_LABELS[k] || k;
const sign = (n) => (n >= 0 ? "+" : "") + n;
const cls = (n) => (n >= 0 ? "pos" : "neg");

const app = document.getElementById("app");
const navRight = document.getElementById("navRight");

async function main() {
  const params = new URLSearchParams(location.search);
  if (params.get("error") === "not_member") {
    return renderError("You need to be a member of the SharpLab server to sign in.");
  }
  let me;
  try {
    const r = await fetch("/api/v1/hq/me", { credentials: "include" });
    if (r.status === 401) return renderLogin();
    me = await r.json();
  } catch (e) {
    return renderError("Couldn't reach HQ. Try again shortly.");
  }
  renderDashboard(me);
}

function renderLogin() {
  navRight.innerHTML = "";
  app.innerHTML = `
    <div class="hero">
      <h1>Your edge, all in one place.</h1>
      <p>Live ELO across every game, your pick'em units, and the leaderboards — sign in with Discord.</p>
      <a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a>
    </div>`;
}

function renderError(msg) {
  app.innerHTML = `<div class="hero"><p class="muted">${msg}</p>
    <a class="btn ghost" href="/api/v1/auth/discord/login">Sign in</a></div>`;
}

async function renderDashboard(me) {
  const u = me.user;
  const av = u.avatar
    ? `https://cdn.discordapp.com/avatars/${u.id}/${u.avatar}.png`
    : null;
  navRight.innerHTML = `
    <div class="userbar">
      ${av ? `<img class="avatar" src="${av}" alt="">` : `<div class="avatar"></div>`}
      <span>${u.username}</span>
      <a class="btn ghost" href="/api/v1/auth/logout">Sign out</a>
    </div>`;

  const p = me.pickem || {};
  const units = (p.units || 0).toFixed(1);
  const elo = (me.elo || []).slice().sort((a, b) => b.rating - a.rating);

  app.innerHTML = `
    <div class="grid">
      <div class="card stat"><div class="label">Pick'em Units</div>
        <div class="value ${cls(p.units || 0)}">${sign(units)}u</div></div>
      <div class="card stat"><div class="label">Pick'em Record</div>
        <div class="value">${p.correct || 0}-${(p.total || 0) - (p.correct || 0)}
          <span class="muted" style="font-size:14px">(${p.accuracy || 0}%)</span></div></div>
      <div class="card stat"><div class="label">Streak Points</div>
        <div class="value">${p.points || 0}</div></div>
      <div class="card stat"><div class="label">Coins</div>
        <div class="value">${me.balance == null ? "—" : me.balance.toLocaleString()}</div></div>
    </div>

    <h2>Your ELO ratings</h2>
    <div class="card" style="padding:0">
      <table><thead><tr><th>Game</th><th class="num">Rating</th><th class="num">W/L</th><th class="num">Games</th></tr></thead>
      <tbody>${
        elo.length
          ? elo.map(r => `<tr><td>${label(r.game)}</td>
              <td class="num">${Math.round(r.rating)}</td>
              <td class="num muted">${r.wins}/${r.losses}</td>
              <td class="num muted">${r.games_played}</td></tr>`).join("")
          : `<tr><td colspan="4" class="muted" style="padding:18px">No rated games yet — play some!</td></tr>`
      }</tbody></table>
    </div>

    <h2>Pick'em leaderboard — Market P&amp;L</h2>
    <div class="card" style="padding:0"><div id="board" class="muted" style="padding:18px">Loading…</div></div>`;

  loadBoard(u.id);
}

async function loadBoard(myId) {
  const el = document.getElementById("board");
  try {
    const r = await fetch("/api/v1/hq/pickem/leaderboard");
    const data = await r.json();
    const rows = data.leaderboard || [];
    if (!rows.length) { el.textContent = "No scored picks yet — first games settle tonight."; return; }
    el.outerHTML = `<table><thead><tr><th class="rank">#</th><th>Player</th>
      <th class="num">Units</th><th class="num">Record</th><th class="num">Acc</th></tr></thead>
      <tbody>${rows.slice(0, 25).map((p, i) => `
        <tr class="${p.user_id === myId ? "me" : ""}">
          <td class="rank">${i + 1}</td>
          <td>${p.username}</td>
          <td class="num ${cls(p.units)}">${sign(p.units.toFixed(1))}u</td>
          <td class="num muted">${p.correct}/${p.total}</td>
          <td class="num muted">${p.accuracy}%</td>
        </tr>`).join("")}</tbody></table>`;
  } catch (e) {
    el.textContent = "Couldn't load the leaderboard.";
  }
}

main();
