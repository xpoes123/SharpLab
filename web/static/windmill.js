// Free-play Windmill — practice mode. Fetches a random solvable board from the preview endpoint
// and plays it with the shared DailyRenderers["windmill"] renderer (rotate/reset/submit live
// inside the board). No ranking; a small capped coin reward on a valid solve. The ranked version
// is the daily at /daily.
(function () {
  "use strict";
  const app = document.getElementById("app");
  const navRight = document.getElementById("navRight");
  const num = (n) => (n == null ? "0" : Number(n).toLocaleString());
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (ms) => { const s = Math.floor(ms / 1000); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };

  const S = { board: null, par: null, difficulty: "easy", tiles: 0, t0: 0, timer: null, started: false, done: false, boardToken: null };
  const R = () => window.DailyRenderers && window.DailyRenderers.windmill;

  async function nav() {
    try {
      const r = await fetch("/api/v1/hq/me", { credentials: "include" });
      const me = r.ok ? await r.json() : null;
      if (me && me.authenticated) {
        const u = me.user, av = u.avatar ? `https://cdn.discordapp.com/avatars/${u.id}/${u.avatar}.png` : null;
        navRight.innerHTML = `<div class="userbar">${av ? `<img class="avatar" src="${av}" alt="">` : `<div class="avatar"></div>`}<span>${esc(u.username)}</span><span class="pill" style="color:var(--gold)">🪙 ${num(me.balance)}</span></div>`;
      } else {
        navRight.innerHTML = `<a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a>`;
      }
    } catch { navRight.innerHTML = ""; }
  }

  function shell() {
    app.innerHTML = `
      <div class="eyebrow">SharpLab · Arcade</div>
      <h1 style="font-size:26px;margin:6px 0 0">🌀 Windmill</h1>
      <p class="sub">Place every tile so exactly one square in each row <b>and</b> each column stays empty. Rotate with <b>R</b>. Free play — the ranked one is the <a href="/daily">Daily</a>.</p>
      <div class="diffrow">
        ${["easy", "medium", "hard"].map((d) => `<button class="diff ${d === S.difficulty ? "on" : ""}" data-d="${d}">${d}</button>`).join("")}
      </div>
      <div class="stats">
        <div class="stat"><div class="k">Tiles</div><div class="v" id="tiles">0</div></div>
        <div class="stat"><div class="k">Time</div><div class="v" id="time">0:00</div></div>
        <div class="stat par"><div class="k">Par</div><div class="v" id="par">–</div></div>
      </div>
      <div class="stage" id="stage"></div>
      <div class="banner" id="banner"></div>
      <div class="actions"><button class="btn" id="newBtn">New board</button></div>`;
    app.querySelectorAll(".diff").forEach((b) => b.onclick = () => { S.difficulty = b.dataset.d; newBoard(); });
    document.getElementById("newBtn").onclick = newBoard;
  }

  function stopTimer() { if (S.timer) { clearInterval(S.timer); S.timer = null; } }
  function tick() { const e = document.getElementById("time"); if (e) e.textContent = fmt(Date.now() - S.t0); }

  async function newBoard() {
    stopTimer();
    S.tiles = 0; S.started = false; S.done = false;
    document.querySelectorAll(".diff").forEach((b) => b.classList.toggle("on", b.dataset.d === S.difficulty));
    const banner = document.getElementById("banner"); if (banner) banner.className = "banner";
    document.getElementById("tiles").textContent = "0";
    document.getElementById("time").textContent = "0:00";
    let j;
    try {
      const r = await fetch(`/api/v1/daily/preview/windmill?difficulty=${S.difficulty}`, { credentials: "include" });
      j = await r.json();
    } catch { return; }
    S.board = j.board; S.par = j.par; S.boardToken = j.board_token;
    document.getElementById("par").textContent = j.par;
    if (R() && R().teardown) R().teardown();
    R().mount(document.getElementById("stage"), j.board, { onSolved, onMove });
  }

  function onMove() {
    if (!S.started) { S.started = true; S.t0 = Date.now(); S.timer = setInterval(tick, 250); }
    S.tiles = R().getMoves().length;
    document.getElementById("tiles").textContent = S.tiles;
  }

  async function onSolved() {
    if (S.done) return;
    S.done = true; stopTimer();
    const t = fmt(S.started ? Date.now() - S.t0 : 0);
    const b = document.getElementById("banner");
    b.className = "banner show win";
    b.innerHTML = `<h2>🎉 Tiled!</h2><div class="line">Placed all <b>${S.par}</b> tiles · ${t}</div>`;
    // server-validate for a small capped coin reward
    try {
      const r = await fetch("/api/v1/daily/practice-solve", {
        method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ board_token: S.boardToken, solution: { moves: R().getMoves() } }),
      });
      const j = await r.json();
      if (j && j.coins) {
        b.innerHTML += `<div class="line" style="color:var(--gold);margin-top:4px">+🪙 ${j.coins}</div>`;
        const chip = navRight.querySelector(".pill");
        if (chip && j.balance != null) chip.textContent = `🪙 ${num(j.balance)}`;
      }
    } catch { /* practice coins are best-effort */ }
  }

  shell(); nav(); newBoard();
})();
