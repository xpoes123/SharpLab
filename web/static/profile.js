// SharpLab HQ — player profile at /hq/{username}.

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
const num = (n) => (n == null ? "—" : Number(n).toLocaleString());

const app = document.getElementById("app");

async function main() {
  const parts = location.pathname.split("/").filter(Boolean); // ["hq","xpoes"]
  const handle = decodeURIComponent(parts[1] || "");
  if (!handle) { app.innerHTML = `<div class="hero"><p class="muted">No player.</p></div>`; return; }

  const r = await fetch(`/api/v1/hq/profile/${encodeURIComponent(handle)}`);
  if (r.status === 404) {
    app.innerHTML = `<div class="hero"><h1>Not found</h1>
      <p class="muted">No player matching “${handle}”.</p>
      <a class="btn" href="/hq">Back to server</a></div>`;
    return;
  }
  render(await r.json());
}

function render(d) {
  const u = d.user;
  const p = d.pickem || {};
  const pr = d.progression || {};
  const lvl = pr.level || 1;
  const pct = pr.xp_for_next ? Math.min(100, Math.round((100 * (pr.xp_into_level || 0)) / pr.xp_for_next)) : 0;
  const elo = (d.elo || []).slice().sort((a, b) => b.rating - a.rating);
  const av = u.avatar_url;

  let html = `<div class="profhead">
    ${av ? `<img class="avatar lg" src="${av}" alt="">` : `<div class="avatar lg"></div>`}
    <h1>${u.username}</h1>
    <span class="lvlpill">Lv ${lvl}</span></div>

    <div class="grid">
      <div class="card stat"><div class="label">Level</div>
        <div class="value">${lvl}</div>
        <div class="lvlbar"><span style="width:${pct}%"></span></div>
        <div class="muted" style="font-size:12px">${num(pr.xp_into_level || 0)}/${num(pr.xp_for_next || 0)} XP · ${num(pr.total_xp || 0)} total</div></div>
      <div class="card stat"><div class="label">Pick'em Units</div>
        <div class="value ${cls(p.units || 0)}">${sign((p.units || 0).toFixed(1))}u</div></div>
      <div class="card stat"><div class="label">Pick'em Record</div>
        <div class="value">${p.correct || 0}-${(p.total || 0) - (p.correct || 0)}
          <span class="muted" style="font-size:14px">(${p.accuracy || 0}%)</span></div></div>
      <div class="card stat"><div class="label">Coins</div>
        <div class="value">${num(d.casino && d.casino.balance)}</div></div>
      <div class="card stat"><div class="label">Stock P&L</div>
        <div class="value ${cls((d.stocks && d.stocks.realized_pnl) || 0)}">
          ${sign(num((d.stocks && d.stocks.realized_pnl) || 0))}</div>
        <div class="muted" style="font-size:12px">${(d.stocks && d.stocks.open_positions) || 0} open</div></div>
    </div>`;

  const ach = pr.achievements || {};
  const unlocked = ach.unlocked || [];
  html += `<h2>🏆 Achievements <span class="muted" style="font-size:14px">${ach.unlocked_count || 0}/${ach.total || 0}</span></h2>
    <div class="card">${
      unlocked.length
        ? `<div class="achgrid">` + unlocked.map((a) =>
            `<div class="achbadge" title="${a.name} — ${a.description} (+${a.xp} XP)">
               <span class="ae">${a.emoji}</span><span class="an">${a.name}</span></div>`).join("") + `</div>`
        : `<span class="muted">No achievements yet.</span>`
    }</div>`;

  if (d.chess) {
    const c = d.chess;
    html += `<h2>♟️ Hearthstone Chess</h2><div class="card">
      <strong>${c.rating}</strong> rating · ${c.wins}-${c.losses}${c.draws ? "-" + c.draws : ""}
      <span class="muted">(${c.games_played} games)</span></div>`;
  }

  html += `<h2>ELO ratings</h2><div class="card" style="padding:0">
    <table><thead><tr><th>Game</th><th class="num">Rating</th><th class="num">W/L</th><th class="num">Games</th></tr></thead>
    <tbody>${
      elo.length
        ? elo.map((r) => `<tr><td>${label(r.game)}</td><td class="num">${r.rating}</td>
            <td class="num muted">${r.wins}/${r.losses}</td><td class="num muted">${r.games_played}</td></tr>`).join("")
        : `<tr><td colspan="4" class="muted" style="padding:18px">No rated games yet.</td></tr>`
    }</tbody></table></div>`;

  app.innerHTML = html;
}

main();
