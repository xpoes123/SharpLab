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

function render(open) {
  const loggedIn = !!open.authenticated;
  const games = open.games || [];
  if (!games.length) {
    app.innerHTML = `<div class="pk-wrap"><h1>🎯 Pick'em</h1>
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
    ${groups}</div>`;
}

function wireBetting() {
  app.addEventListener("click", async (e) => {
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

(async function init() {
  wireBetting();
  const [me, open] = await Promise.all([
    getJSON("/api/v1/hq/me"),
    getJSON("/api/v1/hq/pickem/open"),
  ]);
  renderNav(me && me.authenticated ? me.user : null);
  render(open._status ? { authenticated: false, games: [] } : open);
})();
