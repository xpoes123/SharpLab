// SharpLab HQ — Pick'em: the FULL daily slate (every game), pickable here even when
// only the marquee games are posted to Discord. Backs onto /api/v1/hq/pickem/open + /bet.

const app = document.getElementById("app");
const navRight = document.getElementById("navRight");

const esc = (s) =>
  String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const SPORT_EMOJI = { nba: "🏀", wnba: "🏀", mlb: "⚾", nfl: "🏈" };
const SPORT_LABEL = { nba: "NBA", wnba: "WNBA", mlb: "MLB", nfl: "NFL" };
const SPORT_ORDER = ["nfl", "nba", "wnba", "mlb"];

const pct = (p) => (p == null ? "" : Math.round(p * 100) + "%");

async function getJSON(url) {
  const r = await fetch(url, { credentials: "include" });
  if (!r.ok) return { _status: r.status };
  return r.json();
}

// ── Nav (login / logout) — mirrors hq.js ──
function renderNav(user) {
  if (!user) {
    navRight.innerHTML = `<a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a>`;
    return;
  }
  const av = user.avatar ? `https://cdn.discordapp.com/avatars/${user.id}/${user.avatar}.png` : null;
  navRight.innerHTML = `<div class="userbar">
    ${av ? `<img class="avatar" src="${av}" alt="">` : `<div class="avatar"></div>`}
    <span>${esc(user.username)}</span>
    <a class="btn ghost" href="/api/v1/auth/logout">Sign out</a></div>`;
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
  if (!bets.length) return `<div class="muted" style="margin-top:8px;font-size:13px">No picks yet.</div>`;
  const lines = bets.map((b) =>
    `${esc(b.username)} — <strong>${b.stake}u</strong> on ${esc(b.pick === "away" ? g.away_team : g.home_team)}`).join("<br>");
  return `<details style="margin-top:8px">
    <summary class="muted" style="cursor:pointer;font-size:13px">👥 ${bets.length} pick${bets.length > 1 ? "s" : ""} so far</summary>
    <div style="padding:8px 4px 2px;font-size:13px">${lines}</div></details>`;
}

function gameCard(g, loggedIn) {
  const ap = pct(g.away_prob), hp = pct(g.home_prob);
  const em = SPORT_EMOJI[g.sport] || "🏟️";
  const mine = g.my_pick
    ? `<span class="pill">🔒 ${g.my_stake}u on ${esc(g.my_pick === "away" ? g.away_team : g.home_team)}</span>`
    : "";
  let controls;
  if (!loggedIn) {
    controls = `<div class="teamrow muted">✈️ ${esc(g.away_team)} ${ap} · 🏠 ${esc(g.home_team)} ${hp}
      — <a href="/api/v1/auth/discord/login">sign in to pick</a></div>`;
  } else if (g.my_pick) {
    controls = `<div class="teamrow muted">Pick locked — picks are final.</div>`;
  } else {
    controls = `<div class="teamrow" data-mid="${esc(g.message_id)}">
         <button class="btn ghost team" data-team="away">✈️ ${esc(g.away_team)} ${ap}</button>
         <button class="btn ghost team" data-team="home">🏠 ${esc(g.home_team)} ${hp}</button>
         <span class="stakebtns" data-stakes hidden>
           ${[1, 2, 3, 4, 5].map((n) => `<button data-stake="${n}">${n}u</button>`).join("")}
         </span>
         <span class="betmsg muted"></span>
       </div>`;
  }
  return `<div class="gamecard">
    <div style="margin-bottom:2px">${em} <strong>${esc(g.away_team)}</strong> @ <strong>${esc(g.home_team)}</strong> ${mine}</div>
    <div class="muted" style="font-size:12px;margin-bottom:8px">🕒 ${fmtGameTime(g.start_time)}</div>
    ${controls}
    ${betsDetails(g)}</div>`;
}

// ── Leaderboard (This Week / All-Time) ──
function leaderboardSection(board) {
  const period = board.period || "week";
  const data = board[period];
  const rows = (data && data.leaderboard) || [];
  const toggle = `<span class="pk-lb-toggle">
    <button class="pk-lb-btn${period === "week" ? " sel" : ""}" data-period="week">This Week</button>
    <button class="pk-lb-btn${period === "alltime" ? " sel" : ""}" data-period="alltime">All-Time</button>
  </span>`;
  let body;
  if (!data) {
    body = `<div class="muted" style="font-size:13px">Loading…</div>`;
  } else if (!rows.length) {
    body = `<div class="muted" style="font-size:13px">No scored picks ${period === "week" ? "this week yet" : "yet"}.</div>`;
  } else {
    const medals = ["🥇", "🥈", "🥉"];
    const items = rows.slice(0, 10).map((r, i) => {
      const rank = medals[i] || `#${i + 1}`;
      const sub = r.total ? `${r.accuracy}% · ${r.correct}/${r.total}` : "";
      const cls = r.units > 0 ? "pos" : (r.units < 0 ? "neg" : "");
      return `<li class="pk-lb-row">
        <span class="pk-lb-rank">${rank}</span>
        <span class="pk-lb-name">${esc(r.username)}</span>
        <span class="pk-lb-sub muted">${sub}</span>
        <span class="pk-lb-units ${cls}">${r.units >= 0 ? "+" : ""}${r.units}u</span></li>`;
    }).join("");
    body = `<ol class="pk-lb-list">${items}</ol>`;
  }
  return `<div class="pk-board">
    <h2>🏆 Leaderboard ${toggle}</h2>
    ${body}</div>`;
}

function render(open) {
  const loggedIn = !!open.authenticated;
  const games = open.games || [];
  if (!games.length) {
    app.innerHTML = `<div class="pk-wrap"><h1>🎯 Pick'em</h1>
      ${leaderboardSection(state.board)}
      <div class="pk-empty">No games on today's slate yet. Check back closer to game time.</div></div>`;
    return;
  }
  const bySport = {};
  for (const g of games) (bySport[g.sport] ||= []).push(g);
  const sports = Object.keys(bySport).sort(
    (a, b) => (SPORT_ORDER.indexOf(a) + 99 * (SPORT_ORDER.indexOf(a) < 0)) -
              (SPORT_ORDER.indexOf(b) + 99 * (SPORT_ORDER.indexOf(b) < 0)));

  const groups = sports.map((sp) => {
    const list = bySport[sp].map((g) => gameCard(g, loggedIn)).join("");
    return `<div class="pk-group">
      <h2>${SPORT_EMOJI[sp] || "🏟️"} ${SPORT_LABEL[sp] || sp.toUpperCase()}
        <span class="count">${bySport[sp].length} game${bySport[sp].length > 1 ? "s" : ""}</span></h2>
      <div class="pk-list">${list}</div></div>`;
  }).join("");

  const note = loggedIn
    ? `Pick every game — <strong>1–5 units</strong> each. Picks are final and lock at tip-off.`
    : `<a href="/api/v1/auth/discord/login">Sign in with Discord</a> to make your picks.`;

  app.innerHTML = `<div class="pk-wrap">
    <h1>🎯 Pick'em — full slate</h1>
    <p class="pk-intro">${note} Only the marquee games get posted to Discord; here's everything.</p>
    ${leaderboardSection(state.board)}
    ${loggedIn ? favSection(state.fav) : ""}
    ${groups}</div>`;
}

// Fetch a leaderboard period once (cached in state.board), then re-render.
async function loadBoard(period) {
  if (!state.board[period]) {
    const data = await getJSON(`/api/v1/hq/pickem/leaderboard?period=${period}`);
    state.board[period] = data && !data._status ? data : { leaderboard: [] };
  }
  state.board.period = period;
  renderAll();
}

// ── Favorite teams (auto-pick) ──
function favSection(fav) {
  fav = fav || { favorites: [], stake: 1 };
  const chips = (fav.favorites || []).map((t) =>
    `<span class="fav-chip">${esc(t)}<button class="fav-rm" data-team="${esc(t)}" title="remove">✕</button></span>`
  ).join("") || `<span class="muted" style="font-size:13px">No favorites yet.</span>`;
  const opts = [1, 2, 3, 4, 5].map((n) =>
    `<option value="${n}" ${n === fav.stake ? "selected" : ""}>${n}u</option>`).join("");
  return `<div class="pk-favs">
    <h2>⭐ Favorite teams <span class="count">auto-picked every game</span></h2>
    <p class="muted" style="font-size:13px;margin:0 0 10px">
      Any game your favorite plays gets auto-picked (their side) at
      <select class="fav-stake">${opts}</select> per game — a few minutes after each slate posts.</p>
    <div class="fav-chips">${chips}</div>
    <form class="fav-add">
      <input class="fav-input" placeholder="Add a team — e.g. Dodgers, Lakers, Eagles" maxlength="40" />
      <button class="btn" type="submit">Add</button>
    </form>
    <div class="fav-msg muted"></div></div>`;
}

async function postFav(body) {
  const r = await fetch("/api/v1/hq/pickem/favorites", {
    method: "POST", credentials: "include",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (r.ok) { state.fav = await r.json(); renderAll(); return null; }
  return (await r.json().catch(() => ({}))).error || "error";
}

function wireEvents() {
  app.addEventListener("submit", async (e) => {
    const form = e.target.closest(".fav-add");
    if (!form) return;
    e.preventDefault();
    const input = form.querySelector(".fav-input");
    const team = input.value.trim();
    if (team.length < 3) { setFavMsg("Team name must be at least 3 characters."); return; }
    const err = await postFav({ add: team });
    if (err) setFavMsg(err === "too_short" ? "Team name too short." : "Couldn't add that.");
  });
  app.addEventListener("change", async (e) => {
    if (e.target.classList.contains("fav-stake")) await postFav({ stake: Number(e.target.value) });
  });
  app.addEventListener("click", async (e) => {
    // leaderboard period toggle
    const lbBtn = e.target.closest(".pk-lb-btn");
    if (lbBtn) { await loadBoard(lbBtn.dataset.period); return; }
    // favorite remove
    const rm = e.target.closest(".fav-rm");
    if (rm) { await postFav({ remove: rm.dataset.team }); return; }
    // pick'em betting
    const teamBtn = e.target.closest(".team");
    const stakeBtn = e.target.closest("[data-stake]");
    if (teamBtn) {
      const row = teamBtn.closest("[data-mid]");
      row.dataset.selected = teamBtn.dataset.team;
      row.querySelectorAll(".team").forEach((b) => b.classList.remove("sel"));
      teamBtn.classList.add("sel");
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
        row.innerHTML = `<span class="pos">🔒 Locked ${stakeBtn.dataset.stake}u — picks are final.</span>`;
      } else {
        const err = (await r.json().catch(() => ({}))).error;
        msg.textContent = err === "already_bet" ? "✖ already picked — final" : "✖ closed";
        msg.className = "betmsg neg";
        row.querySelectorAll("button").forEach((b) => (b.disabled = true));
      }
    }
  });
}

function setFavMsg(t) {
  const el = document.querySelector(".fav-msg");
  if (el) el.textContent = t;
}

function renderAll() {
  render(state.open);
}

const state = {
  open: { authenticated: false, games: [] },
  fav: null,
  board: { period: "week", week: null, alltime: null },
};

(async function init() {
  wireEvents();
  const me = await getJSON("/api/v1/hq/me");
  const loggedIn = !!(me && me.authenticated);
  renderNav(loggedIn ? me.user : null);
  const [open, fav, week] = await Promise.all([
    getJSON("/api/v1/hq/pickem/open"),
    loggedIn ? getJSON("/api/v1/hq/pickem/favorites") : Promise.resolve(null),
    getJSON("/api/v1/hq/pickem/leaderboard?period=week"),
  ]);
  state.open = open._status ? { authenticated: loggedIn, games: [] } : open;
  state.fav = fav && !fav._status ? fav : { favorites: [], stake: 1 };
  state.board.week = week && !week._status ? week : { leaderboard: [] };
  renderAll();
})();
