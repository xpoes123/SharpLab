// SharpLab HQ — Daily Game page. Everyone gets today's server-generated board
// (GET /api/v1/daily/today); you fence the pig in as few moves as possible and
// POST /api/v1/daily/submit to rank. The board engine + pig AI live in
// trappig_board.js and are byte-identical to the server (shared/daily_games/
// trappig.py), so a trap computed here replays to a trap on the server.
//
// One-submit rule: only a genuine WIN is posted, and it posts exactly once. If
// the pig escapes, we never submit — the player resets to the original server
// board and retries freely until they trap it.

const B = window.TrapPigBoard;
const app = document.getElementById("app");
const navRight = document.getElementById("navRight");

// ── Helpers (copied from threecardpoker.js) ──
const num = (n) => (n == null ? "—" : Number(n).toLocaleString());
const esc = (s) =>
  String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function getJSON(url) {
  try {
    const r = await fetch(url, { credentials: "include" });
    if (!r.ok) return { _status: r.status };
    return await r.json();
  } catch (_) {
    return { _status: 0 };
  }
}

const fmtTime = (ms) => {
  const s = Math.floor((Number(ms) || 0) / 1000);
  return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
};

// ── App state ──
const state = { me: null, balance: 0 };
const D = {
  today: null, // /today payload
  work: null, // live board {rows, cols, pig:[r,c], fences:Set}
  moves: [], // ordered [[r,c], ...] the player has fenced
  started: false,
  t0: 0,
  timer: null,
  over: false,
  submitting: false,
  submitted: false,
  lbTab: "today",
};

const $ = (id) => document.getElementById(id);

// ── Nav (mirrors threecardpoker.js) ──
function renderNav(user) {
  if (!user) {
    navRight.innerHTML = `<a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a>`;
    return;
  }
  const av = user.avatar ? `https://cdn.discordapp.com/avatars/${user.id}/${user.avatar}.png` : null;
  navRight.innerHTML = `<div class="userbar">
    ${av ? `<img class="avatar" src="${av}" alt="">` : `<div class="avatar"></div>`}
    <span>${esc(user.username)}</span>
    <span class="coinschip" title="Casino coins">🪙 ${num(state.balance)}</span>
    <a class="btn ghost" href="/api/v1/auth/logout">Sign out</a></div>`;
}
function applyBalance(bal) {
  if (bal == null) return;
  state.balance = bal;
  renderNav(state.me && state.me.user);
}
const myName = () => (state.me && state.me.user && state.me.user.username) || null;

// ── Build a fresh working board from the server puzzle (deep-copied) ──
function freshWork() {
  const bd = D.today.board;
  return {
    rows: bd.rows,
    cols: bd.cols,
    pig: [bd.pig[0], bd.pig[1]],
    fences: B.toKeySet(bd.fences ? bd.fences.map((f) => [f[0], f[1]]) : []),
  };
}

// ── Header + skeleton ──
function headerHTML() {
  const t = D.today;
  const g = t.game || {};
  const diff = String(t.difficulty || t.board.difficulty || "").toLowerCase();
  const parNum = t.par != null ? `${t.par_approx ? "~" : ""}${t.par}` : "—";
  const streak = t.streak != null
    ? `<span class="streakbadge" title="Your daily streak">🔥 ${num(t.streak)}</span>`
    : "";
  return `<div class="daily-head">
    <div class="daily-title">
      <span class="icon">${esc(g.icon || "🐷")}</span>
      <h1>${esc(g.name || "Daily Game")}</h1>
      <span class="daily-badges">
        ${diff ? `<span class="diffbadge ${esc(diff)}">${esc(diff)}</span>` : ""}
        <span class="parbadge">Par ${esc(parNum)}</span>
        ${streak}
      </span>
    </div>
    <p class="rule">Fence the pig in as few moves as you can — everyone gets today's board; time breaks ties.</p>
  </div>`;
}

function buildSkeleton(opts) {
  const t = D.today;
  const parNum = t.par != null ? `${t.par_approx ? "~" : ""}${t.par}` : "—";
  app.innerHTML = `<div class="wrap">
    ${headerHTML()}
    <div class="stats">
      <div class="stat-box"><div class="k">Fences</div><div class="v" id="fences">0</div></div>
      <div class="stat-box"><div class="k">Time</div><div class="v" id="time">0:00</div></div>
      <div class="stat-box par"><div class="k">Par</div><div class="v">${esc(parNum)}</div></div>
    </div>
    <div class="stage${opts.preview ? " preview" : ""}" id="stage"><svg id="board"></svg></div>
    <div id="notice"></div>
    ${opts.showReset ? `<div class="actions"><button class="btn ghost" id="resetBtn" style="flex:1">Reset board</button></div>` : ""}
    <div id="resultArea"></div>
    <h2>Leaderboard</h2>
    <div id="lbArea"><p class="muted">Loading…</p></div>
  </div>`;
}

function drawBoard(interactive) {
  B.renderInto($("board"), D.work, interactive ? onPlace : null);
}

function showNotice(kind, html) {
  const n = $("notice");
  if (n) n.innerHTML = html ? `<div class="notice ${kind}">${html}</div>` : "";
}

// ── Play flow ──
function onPlace(r, c) {
  if (D.over || D.submitted) return;
  const k = B.key(r, c);
  if (D.work.fences.has(k) || (D.work.pig[0] === r && D.work.pig[1] === c)) return;
  if (!D.started) {
    D.started = true;
    D.t0 = Date.now();
    D.timer = setInterval(() => {
      const el = $("time");
      if (el) el.textContent = fmtTime(Date.now() - D.t0);
    }, 250);
  }
  D.work.fences.add(k);
  D.moves.push([r, c]);
  const el = $("fences");
  if (el) el.textContent = String(D.moves.length);
  // Move the pig with the shared (server-identical) AI.
  const nxt = B.pigStep(D.work.pig, D.work.fences, D.work.rows, D.work.cols);
  if (nxt === null) return onTrapped();
  D.work.pig = nxt;
  drawBoard(true);
  if (B.isBorder(D.work.pig[0], D.work.pig[1], D.work.rows, D.work.cols)) return onEscaped();
}

function stopTimer() {
  if (D.timer) {
    clearInterval(D.timer);
    D.timer = null;
  }
}

function onEscaped() {
  D.over = true;
  stopTimer();
  drawBoard(false);
  showNotice(
    "warn",
    `🐷 It escaped — the daily wants a <b>WIN</b>. Reset and try again; only a trap counts, and you can retry as many times as you need.`
  );
}

function onTrapped() {
  D.over = true;
  stopTimer();
  drawBoard(false);
  showNotice("info", `🎉 Trapped in ${D.moves.length} fences — submitting your result…`);
  submit();
}

function resetPlay() {
  D.work = freshWork();
  D.moves = [];
  D.started = false;
  D.over = false;
  D.t0 = 0;
  stopTimer();
  const f = $("fences"), tm = $("time");
  if (f) f.textContent = "0";
  if (tm) tm.textContent = "0:00";
  showNotice("", "");
  const ra = $("resultArea");
  if (ra) ra.innerHTML = "";
  drawBoard(true);
}

// ── Submit (debounced to exactly one successful post) ──
async function submit() {
  if (D.submitting || D.submitted) return;
  D.submitting = true;
  const elapsed_ms = D.started ? Math.max(0, Date.now() - D.t0) : 0;
  const body = { solution: { moves: D.moves, elapsed_ms: Math.round(elapsed_ms) } };
  let r, j;
  try {
    r = await fetch("/api/v1/daily/submit", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    j = await r.json().catch(() => ({}));
  } catch (_) {
    D.submitting = false;
    showNotice("warn", "Network error submitting — reset and try again.");
    return;
  }
  if (r.status === 401) {
    D.submitting = false;
    showNotice("warn", `Your session expired. <a href="/api/v1/auth/discord/login">Sign in</a> to rank.`);
    return;
  }
  if (r.status === 409) {
    D.submitted = true;
    D.submitting = false;
    showNotice("info", "You already played today — come back after 4am ET for a new puzzle.");
    loadLeaderboard();
    return;
  }
  if (r.status === 400 || !r.ok) {
    D.submitting = false;
    showNotice("warn", "That didn't register as a trap — reset and try again.");
    return;
  }
  // 200 — genuine trap accepted.
  D.submitted = true;
  D.submitting = false;
  if (j.balance != null) applyBalance(j.balance);
  showNotice("", "");
  renderResult(j);
  loadLeaderboard();
}

// ── Result panel ──
// Accepts either a submit response {result:{...}, par, rank, field, coins,
// streak, share} or a /today your_result {solved, primary, secondary, ...}.
function renderResult(payload, opts) {
  opts = opts || {};
  const res = payload.result || payload; // your_result is the bare result
  const primary = res.primary;
  const secondary = res.secondary;
  const par = payload.par != null ? payload.par : D.today ? D.today.par : null;
  const rank = payload.rank;
  const field = payload.field;
  const coins = payload.coins;
  const streak = payload.streak;
  const share = payload.share;

  const parStr = par != null ? ` (par ${esc(par)})` : "";
  const grid = "🟩".repeat(Math.min(Number(primary) || 0, 12));

  const metrics = [];
  if (rank != null) {
    const fieldStr = field != null ? ` of ${num(field)}` : "";
    metrics.push(`<div class="metric"><div class="k">Rank</div><div class="v">#${num(rank)}${fieldStr}</div></div>`);
  }
  if (coins != null) {
    metrics.push(`<div class="metric"><div class="k">Coins</div><div class="v" style="color:var(--gold)">🪙 ${num(coins)}</div></div>`);
  }
  if (streak != null) {
    metrics.push(`<div class="metric"><div class="k">Streak</div><div class="v">🔥 ${num(streak)}</div></div>`);
  }

  const shareText = share || defaultShare(primary, par, secondary);
  const shareBox = `<div class="sharebox">
      <pre id="shareText">${esc(shareText)}</pre>
      <button class="btn" id="copyShare">Copy result</button>
    </div>`;

  const title = opts.title || "🎉 Trapped!";
  $("resultArea").innerHTML = `<div class="result">
    <h2>${esc(title)}</h2>
    <p class="sub">Trapped in <b>${esc(primary)}</b> fences${parStr} · ${esc(fmtTime(secondary))}</p>
    <div class="grid2">${grid}</div>
    ${metrics.length ? `<div class="metrics">${metrics.join("")}</div>` : ""}
    ${shareBox}
  </div>`;

  const copyBtn = $("copyShare");
  if (copyBtn) {
    copyBtn.onclick = async () => {
      try {
        await navigator.clipboard.writeText(shareText);
        copyBtn.textContent = "Copied!";
        setTimeout(() => (copyBtn.textContent = "Copy result"), 1800);
      } catch (_) {
        copyBtn.textContent = "Copy failed";
      }
    };
  }
}

function defaultShare(primary, par, secondary) {
  const g = (D.today && D.today.game) || {};
  const diff = (D.today && D.today.difficulty) || "";
  const parStr = par != null ? ` (par ${par})` : "";
  const blocks = "🟩".repeat(Math.min(Number(primary) || 0, 12));
  return `${g.icon || "🐷"} ${g.name || "Trap the Pig"} · ${diff} · ${primary} fences${parStr} · ${fmtTime(secondary)}\n${blocks}`;
}

// ── Leaderboard ──
async function loadLeaderboard() {
  const area = $("lbArea");
  if (!area) return;
  const data = await getJSON("/api/v1/daily/leaderboard");
  if (data && data._status) {
    area.innerHTML = `<p class="muted">Leaderboard unavailable.</p>`;
    return;
  }
  renderLeaderboard(data);
}

function renderLeaderboard(data) {
  const area = $("lbArea");
  const today = (data && data.today) || [];
  const season = (data && data.season) || [];
  area.innerHTML = `<div class="lb-tabs">
      <button class="lb-tab${D.lbTab === "today" ? " on" : ""}" data-tab="today">Today</button>
      <button class="lb-tab${D.lbTab === "season" ? " on" : ""}" data-tab="season">Season</button>
    </div>
    <div class="card lb-card" id="lbTable"></div>`;
  area.querySelectorAll(".lb-tab").forEach((b) => {
    b.onclick = () => {
      D.lbTab = b.dataset.tab;
      renderLeaderboard(data);
    };
  });
  const table = $("lbTable");
  const me = myName();
  if (D.lbTab === "today") {
    if (!today.length) {
      table.innerHTML = `<p class="muted" style="margin:0">No entries yet today — be the first.</p>`;
      return;
    }
    const rows = today
      .map((r) => {
        const mine = me && r.name === me ? " me" : "";
        return `<tr class="${mine.trim()}">
          <td class="lb-rank">${num(r.rank)}</td>
          <td>${esc(r.name)}</td>
          <td class="num">${esc(r.primary)} fences</td>
          <td class="num">${esc(fmtTime(r.secondary))}</td>
          <td class="num">+${num(r.points)} pts</td>
        </tr>`;
      })
      .join("");
    table.innerHTML = `<table><thead><tr>
        <th class="lb-rank">#</th><th>Player</th>
        <th class="num">Fences</th><th class="num">Time</th><th class="num">Points</th>
      </tr></thead><tbody>${rows}</tbody></table>`;
  } else {
    if (!season.length) {
      table.innerHTML = `<p class="muted" style="margin:0">No season standings yet.</p>`;
      return;
    }
    const rows = season
      .map((r) => {
        const mine = me && r.name === me ? " me" : "";
        return `<tr class="${mine.trim()}">
          <td class="lb-rank">${num(r.rank)}</td>
          <td>${esc(r.name)}</td>
          <td class="num">${num(r.points)} pts</td>
          <td class="num">${num(r.days)} days</td>
        </tr>`;
      })
      .join("");
    table.innerHTML = `<table><thead><tr>
        <th class="lb-rank">#</th><th>Player</th>
        <th class="num">Points</th><th class="num">Days</th>
      </tr></thead><tbody>${rows}</tbody></table>`;
  }
}

// ── Top-level render by mode ──
function renderSignedOut() {
  D.work = freshWork();
  buildSkeleton({ preview: true, showReset: false });
  drawBoard(false);
  showNotice(
    "info",
    `<b>Sign in to play & rank.</b> This is today's board — everyone gets the same one.
     <div class="signin"><a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a></div>`
  );
  loadLeaderboard();
}

function renderAlreadyPlayed() {
  D.work = freshWork();
  D.submitted = true;
  buildSkeleton({ preview: true, showReset: false });
  drawBoard(false);
  showNotice("info", "You already played today — come back after 4am ET for a new puzzle.");
  if (D.today.your_result) {
    renderResult({ result: D.today.your_result, par: D.today.par, streak: D.today.streak }, { title: "Today's result" });
  }
  loadLeaderboard();
}

function renderPlay() {
  D.work = freshWork();
  D.moves = [];
  D.started = false;
  D.over = false;
  D.submitted = false;
  buildSkeleton({ preview: false, showReset: true });
  drawBoard(true);
  const rb = $("resetBtn");
  if (rb) rb.onclick = resetPlay;
  loadLeaderboard();
}

async function main() {
  const [me, today] = await Promise.all([
    getJSON("/api/v1/hq/me"),
    getJSON("/api/v1/daily/today"),
  ]);
  const loggedIn = me && me.authenticated;
  state.me = loggedIn ? me : null;
  state.balance = loggedIn ? me.balance || 0 : 0;
  renderNav(loggedIn ? me.user : null);

  if (!today || today._status || !today.board) {
    app.innerHTML = `<div class="hero"><h1>Daily Game</h1><p class="muted">Couldn't load today's puzzle. Try again shortly.</p></div>`;
    return;
  }
  D.today = today;

  if (today.signed_in === false || !loggedIn) return renderSignedOut();
  if (today.played) return renderAlreadyPlayed();
  return renderPlay();
}

main();
