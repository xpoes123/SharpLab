// SharpLab HQ — Daily Game page. START-GATED flow: GET /api/v1/daily/today no
// longer ships the board — it returns puzzle metadata (number, game rules, par,
// difficulty). The player reads the how-to card, presses Start, and only then
// does POST /api/v1/daily/start hand back the board + a start_token and begin
// the clock SERVER-SIDE. This means the board can't be pre-solved before the
// timer runs. The board engine + pig AI live in trappig_board.js and are
// byte-identical to the server (shared/daily_games/trappig.py), so a trap
// computed here replays to a trap on the server.
//
// One-submit rule: only a genuine WIN is posted, and it posts exactly once. The
// server times the solve from the start_token (no client elapsed is trusted).
// If the pig escapes, we never submit — the player presses Reset, which fetches
// a FRESH board + token via /start and retries freely until they trap it.

const B = window.TrapPigBoard;
const app = document.getElementById("app");
const navRight = document.getElementById("navRight");

// ── Helpers (copied from threecardpoker.js) ──
const num = (n) => (n == null ? "—" : Number(n).toLocaleString());
const esc = (s) =>
  String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Render the plain-text how-to string: escape it, turn **bold** into <b>, and
// keep line breaks. Safe because we escape BEFORE injecting the <b> tags.
const renderHowto = (text) =>
  String(text == null ? "" : text)
    .split("\n")
    .map((line) => esc(line).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>"))
    .join("<br>");

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
  today: null, // /today payload (metadata only — no board)
  board: null, // board from /start {rows, cols, pig, fences}
  startToken: null, // opaque token from /start; identifies the timed session
  work: null, // live board {rows, cols, pig:[r,c], fences:Set}
  moves: [], // ordered [[r,c], ...] the player has fenced
  t0: 0, // client display-clock origin (visual only; server is authoritative)
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

// ── Build a fresh working board from the /start board (deep-copied) ──
function freshWork() {
  const bd = D.board;
  return {
    rows: bd.rows,
    cols: bd.cols,
    pig: [bd.pig[0], bd.pig[1]],
    fences: B.toKeySet(bd.fences ? bd.fences.map((f) => [f[0], f[1]]) : []),
  };
}

// ── Header (shown in every mode; carries the puzzle number) ──
function headerHTML() {
  const t = D.today;
  const g = t.game || {};
  const diff = String(t.difficulty || "").toLowerCase();
  const parNum = t.par != null ? `${t.par_approx ? "~" : ""}${t.par}` : "—";
  const numStr = t.number != null ? ` #${esc(t.number)}` : "";
  const streak = t.streak != null
    ? `<span class="streakbadge" title="Your daily streak">🔥 ${num(t.streak)}</span>`
    : "";
  return `<div class="daily-head">
    <div class="daily-title">
      <span class="icon">${esc(g.icon || "🐷")}</span>
      <h1>${esc(g.name || "Daily Game")}${numStr}</h1>
      <span class="daily-badges">
        ${diff ? `<span class="diffbadge ${esc(diff)}">${esc(diff)}</span>` : ""}
        <span class="parbadge">Par ${esc(parNum)}</span>
        ${streak}
      </span>
    </div>
    <p class="rule">Fence the pig in as few moves as you can — everyone gets today's board; time breaks ties.</p>
  </div>`;
}

// ── Info shell (tutorial / signed-out / already-played) — header + body slot ──
function buildInfoShell() {
  app.innerHTML = `<div class="wrap">
    ${headerHTML()}
    <div id="body"></div>
    <div id="notice"></div>
    <div id="resultArea"></div>
    <h2>Leaderboard</h2>
    <div id="lbArea"><p class="muted">Loading…</p></div>
  </div>`;
}

// ── Play shell — the board only ever appears here, after /start ──
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
    <div class="stage" id="stage"><svg id="board"></svg></div>
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

// ── Display clock (purely visual — the server times the real solve) ──
function startTimer(elapsedMs) {
  stopTimer();
  // Anchor to the server's continuous clock: elapsedMs already includes earlier attempts, so a
  // Reset resumes the running total instead of zeroing it. Grinding retries costs you time.
  D.t0 = Date.now() - (elapsedMs || 0);
  const el = $("time");
  if (el) el.textContent = fmtTime(elapsedMs || 0);
  D.timer = setInterval(() => {
    const e = $("time");
    if (e) e.textContent = fmtTime(Date.now() - D.t0);
  }, 250);
}
function stopTimer() {
  if (D.timer) {
    clearInterval(D.timer);
    D.timer = null;
  }
}

// ── Play flow ──
function onPlace(r, c) {
  if (D.over || D.submitted) return;
  const k = B.key(r, c);
  if (D.work.fences.has(k) || (D.work.pig[0] === r && D.work.pig[1] === c)) return;
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

// ── Start / Reset: fetch a fresh board + token and begin the timed session ──
async function startGame() {
  const btn = $("startBtn");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Starting…";
  }
  let r, j;
  try {
    r = await fetch("/api/v1/daily/start", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    j = await r.json().catch(() => ({}));
  } catch (_) {
    return failStart("Network error starting the puzzle — try again.");
  }
  if (r.status === 401) return failStart(`Your session expired. <a href="/api/v1/auth/discord/login">Sign in</a> to play.`);
  if (r.status === 409) {
    // Already played today — bounce to the played view.
    D.today.played = true;
    return renderAlreadyPlayed();
  }
  if (!r.ok || !j || !j.board) return failStart("Couldn't start the puzzle — try again shortly.");

  // Board is here for the first time. Sync any metadata the server refreshed.
  D.board = j.board;
  D.startToken = j.start_token;
  if (j.par != null) D.today.par = j.par;
  if (j.difficulty != null) D.today.difficulty = j.difficulty;
  if (j.number != null) D.today.number = j.number;

  D.moves = [];
  D.over = false;
  D.submitting = false;
  D.submitted = false;
  D.work = freshWork();

  buildSkeleton({ showReset: true });
  drawBoard(true);
  startTimer(j.elapsed_ms);   // continuous clock — includes time from earlier attempts
  const rb = $("resetBtn");
  if (rb) rb.onclick = startGame; // Reset = same board, but the clock keeps running.
  loadLeaderboard();
}

function failStart(html) {
  stopTimer();
  const btn = $("startBtn");
  if (btn) {
    btn.disabled = false;
    btn.textContent = "▶ Start";
  }
  showNotice("warn", html);
}

// ── Submit (debounced to exactly one successful post) ──
async function submit() {
  if (D.submitting || D.submitted) return;
  D.submitting = true;
  const body = { start_token: D.startToken, solution: { moves: D.moves } };
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
    // Didn't register as a trap, or the token expired / was forged. The server
    // message explains which; either way the fix is to press Start again.
    D.submitting = false;
    const msg = (j && (j.detail || j.error || j.message)) || "That didn't register — press Start to play a fresh board.";
    showNotice(
      "warn",
      `${esc(msg)}<div class="signin"><button class="btn" id="startBtn">▶ Start again</button></div>`
    );
    const sb = $("startBtn");
    if (sb) sb.onclick = startGame;
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
  const t = D.today || {};
  const g = t.game || {};
  const numStr = t.number != null ? ` #${t.number}` : "";
  const diff = t.difficulty || "";
  const parStr = par != null ? ` (par ${par})` : "";
  const blocks = "🟩".repeat(Math.min(Number(primary) || 0, 12));
  return `${g.icon || "🐷"} ${g.name || "Trap the Pig"}${numStr} · ${diff} · ${primary} fences${parStr} · ${fmtTime(secondary)}\n${blocks}`;
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
  const number = data && data.number != null ? data.number : D.today && D.today.number;
  const heading = number != null
    ? `<div class="lb-heading">Daily #${esc(number)}</div>`
    : "";
  area.innerHTML = `${heading}<div class="lb-tabs">
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

// ── Tutorial card (the how-to shown before the board is revealed) ──
function tutorialCardHTML(footerHTML) {
  const t = D.today;
  const g = t.game || {};
  const diff = String(t.difficulty || "").toLowerCase();
  const parNum = t.par != null ? `${t.par_approx ? "~" : ""}${t.par}` : "—";
  const howto = g.howto ? `<div class="howto">${renderHowto(g.howto)}</div>` : "";
  return `<div class="tutorial">
    <div class="tut-meta">
      ${diff ? `<span class="diffbadge ${esc(diff)}">${esc(diff)}</span>` : ""}
      <span class="parbadge">Par ${esc(parNum)}</span>
    </div>
    ${howto}
    <p class="locked">🔒 The board stays hidden until you press Start — then the clock runs.</p>
    ${footerHTML || ""}
  </div>`;
}

// ── Top-level render by mode ──
function renderTutorial() {
  buildInfoShell();
  $("body").innerHTML = tutorialCardHTML(
    `<button class="btn big" id="startBtn">▶ Start</button>`
  );
  const sb = $("startBtn");
  if (sb) sb.onclick = startGame;
  loadLeaderboard();
}

function renderSignedOut() {
  buildInfoShell();
  $("body").innerHTML = tutorialCardHTML(
    `<div class="signin"><a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord to play &amp; rank</a></div>`
  );
  loadLeaderboard();
}

function renderAlreadyPlayed() {
  D.submitted = true;
  stopTimer();
  buildInfoShell();
  $("body").innerHTML = "";
  showNotice("info", "You already played today — come back after 4am ET for a new puzzle.");
  if (D.today.your_result) {
    renderResult(
      { result: D.today.your_result, par: D.today.par, streak: D.today.streak },
      { title: "Today's result" }
    );
  }
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

  if (!today || today._status || !today.game) {
    app.innerHTML = `<div class="hero"><h1>Daily Game</h1><p class="muted">Couldn't load today's puzzle. Try again shortly.</p></div>`;
    return;
  }
  D.today = today;

  if (today.signed_in === false || !loggedIn) return renderSignedOut();
  if (today.played) return renderAlreadyPlayed();
  return renderTutorial();
}

main();
