// Free-play Unlock — practice mode. Random pattern from the preview endpoint, played with the shared
// DailyRenderers["unlock"] renderer. Unranked; a server-validated solve pays the capped practice
// reward. (The ranked version is the daily at /daily.)
(function () {
  "use strict";
  const app = document.getElementById("app");
  const navRight = document.getElementById("navRight");
  const num = (n) => (n == null ? "0" : Number(n).toLocaleString());
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (ms) => { const s = Math.floor(ms / 1000); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
  const $ = (id) => document.getElementById(id);

  const S = { difficulty: "easy", t0: 0, timer: null, started: false, done: false, boardToken: null };
  const R = () => window.DailyRenderers.unlock;

  async function nav() {
    try {
      const r = await fetch("/api/v1/hq/me", { credentials: "include" });
      const me = r.ok ? await r.json() : null;
      if (me && me.authenticated) {
        const u = me.user, av = u.avatar ? `https://cdn.discordapp.com/avatars/${esc(u.id)}/${esc(u.avatar)}.png` : null;
        navRight.innerHTML = `<div class="userbar">${av ? `<img class="avatar" src="${av}" alt="">` : `<div class="avatar"></div>`}<span>${esc(u.username)}</span><span class="pill" style="color:var(--gold)">🪙 ${num(me.balance)}</span></div>`;
      } else {
        navRight.innerHTML = `<a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a>`;
      }
    } catch { navRight.innerHTML = ""; }
  }

  function shell() {
    app.innerHTML = `
      <div class="eyebrow">SharpLab · Arcade</div>
      <h1 style="font-size:26px;margin:6px 0 0">🔓 Unlock</h1>
      <p class="sub">Redraw the phone's unlock pattern. You can't land on a used dot, but you can cross one; crossing an unused dot picks it up. Free play. The ranked one is the <a href="/daily">Daily</a>.</p>
      <div class="diffrow">
        ${["easy", "medium", "hard"].map((d) => `<button class="diff ${d === S.difficulty ? "on" : ""}" data-d="${d}">${d}</button>`).join("")}
      </div>
      <div class="stats">
        <div class="stat"><div class="k">Dots</div><div class="v" id="moves">0</div></div>
        <div class="stat"><div class="k">Time</div><div class="v" id="time">0:00</div></div>
        <div class="stat par"><div class="k">Pattern</div><div class="v" id="par">–</div></div>
      </div>
      <div class="stage" id="stage"></div>
      <div class="banner" id="banner"></div>
      <div class="actions"><button class="btn" id="newBtn">New pattern</button></div>`;
    app.querySelectorAll(".diff").forEach((b) => b.onclick = () => { S.difficulty = b.dataset.d; newBoard(); });
    $("newBtn").onclick = newBoard;
  }

  function stopTimer() { if (S.timer) { clearInterval(S.timer); S.timer = null; } }

  async function newBoard() {
    stopTimer();
    S.started = false; S.done = false;
    document.querySelectorAll(".diff").forEach((b) => b.classList.toggle("on", b.dataset.d === S.difficulty));
    $("banner").className = "banner";
    $("moves").textContent = "0";
    $("time").textContent = "0:00";
    let j;
    try {
      const r = await fetch(`/api/v1/daily/preview/unlock?difficulty=${S.difficulty}`, { credentials: "include" });
      j = await r.json();
    } catch { return; }
    S.boardToken = j.board_token;
    $("par").textContent = `${j.par} dots`;
    if (R().teardown) R().teardown();
    R().mount($("stage"), j.board, { onSolved, onMove });
  }

  function onMove(count) {
    if (!S.started && count) { S.started = true; S.t0 = Date.now(); S.timer = setInterval(() => { $("time").textContent = fmt(Date.now() - S.t0); }, 250); }
    $("moves").textContent = count;
  }

  async function onSolved() {
    if (S.done) return;
    S.done = true; stopTimer();
    const b = $("banner");
    b.className = "banner show win";
    b.innerHTML = `<h2>🔓 Unlocked!</h2><div class="line">Cracked it in ${fmt(S.started ? Date.now() - S.t0 : 0)}</div>`;
    try {
      const r = await fetch("/api/v1/daily/practice-solve", {
        method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ board_token: S.boardToken, solution: { moves: R().getMoves() } }),
      });
      const j = await r.json();
      if (j && j.coins) {
        b.innerHTML += `<div class="line" style="color:var(--gold);margin-top:4px">+🪙 ${num(j.coins)}</div>`;
        const chip = navRight.querySelector(".pill");
        if (chip && j.balance != null) chip.textContent = `🪙 ${num(j.balance)}`;
      }
    } catch { /* practice coins are best-effort */ }
  }

  shell(); nav(); newBoard();
})();
