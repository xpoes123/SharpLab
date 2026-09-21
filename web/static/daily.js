// SharpLab HQ — Daily Game page. START-GATED flow: GET /api/v1/daily/today no
// longer ships the board — it returns puzzle metadata (number, game rules, par,
// difficulty). The player reads the how-to card, presses Start, and only then
// does POST /api/v1/daily/start hand back the board + a start_token and begin
// the clock SERVER-SIDE. This means the board can't be pre-solved before the
// timer runs.
//
// GAME-AWARE: the page renders whatever game the server serves today. Each game
// ships a renderer registered on window.DailyRenderers[gameId], and this page
// dispatches on `today.game.id`. A renderer implements:
//   mount(boardEl, board, { onSolved, onEscaped, onMove })
//   getMoves()   -> the ordered moves in the game's solution format
//   teardown()   -> drop listeners/timers (optional)
// onSolved() = the puzzle is solved → we POST submit with {moves:getMoves()}.
// onEscaped() = a loss (pig-only: the pig reached the edge) → we show the
// "it escaped, Reset" notice and never submit. Rush Hour never calls onEscaped.
// The trappig renderer lives at the bottom of this file (it reuses TrapPigBoard
// from trappig_board.js); Rush Hour's renderer lives in rushhour_board.js.
//
// One-submit rule: only a genuine WIN is posted, and it posts exactly once. The
// server times the solve from the start_token (no client elapsed is trusted).

window.DailyRenderers = window.DailyRenderers || {};

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
  board: null, // board from /start (shape depends on the game)
  startToken: null, // opaque token from /start; identifies the timed session
  renderer: null, // the active game renderer (from window.DailyRenderers)
  t0: 0, // client display-clock origin (visual only; server is authoritative)
  timer: null,
  over: false,
  submitting: false,
  submitted: false,
  lbTab: "today",
};

const $ = (id) => document.getElementById(id);

// ── Game-aware bits ──
function gameId() {
  return (D.today && D.today.game && D.today.game.id) || "trappig";
}
const isRush = () => gameId() === "rushhour";
const isMind = () => gameId() === "mastermind";
const isCount = () => gameId() === "countdown";
const isWind = () => gameId() === "windmill";
const unitWord = () => (isWind() ? "tiles" : isCount() ? "numbers" : isMind() ? "guesses" : isRush() ? "moves" : "fences"); // lowercase, for prose
const unitLabel = () => (isWind() ? "Tiles" : isCount() ? "Numbers" : isMind() ? "Guesses" : isRush() ? "Moves" : "Fences"); // Titlecase, for headers
const solvedTitle = () => (isWind() ? "🎉 Tiled!" : isMind() ? "🎉 Cracked!" : isRush() || isCount() ? "🎉 Solved!" : "🎉 Trapped!");
const solvedVerb = () => (isMind() ? "Cracked" : "Solved");
function ruleText() {
  if (isWind())
    return "Place every tile so each row and each column has exactly ONE empty square — tiles rotate; everyone gets today's bag; fastest valid solve wins, tile count breaks ties.";
  if (isCount())
    return "Use the six numbers and + − × ÷ to hit the target exactly — everyone gets today's numbers; fastest exact solve wins, fewest numbers breaks ties.";
  if (isMind())
    return "Crack the secret code in as few guesses as you can — everyone gets today's code; fewest guesses wins, time breaks ties.";
  return isRush()
    ? "Slide the red car out through the exit in as few moves as you can — everyone gets today's board; time breaks ties."
    : "Fence the pig in as few moves as you can — everyone gets today's board; time breaks ties.";
}

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
    <p class="rule">${esc(ruleText())}</p>
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
      <div class="stat-box"><div class="k">${esc(unitLabel())}</div><div class="v" id="fences">0</div></div>
      <div class="stat-box"><div class="k">Time</div><div class="v" id="time">0:00</div></div>
      <div class="stat-box par"><div class="k">Par</div><div class="v">${esc(parNum)}</div></div>
    </div>
    <div class="stage" id="stage"></div>
    <div id="notice"></div>
    ${opts.showReset ? `<div class="actions"><button class="btn ghost" id="resetBtn" style="flex:1">Reset board</button></div>` : ""}
    <div id="resultArea"></div>
    <h2>Leaderboard</h2>
    <div id="lbArea"><p class="muted">Loading…</p></div>
  </div>`;
}

function showNotice(kind, html) {
  const n = $("notice");
  if (n) n.innerHTML = html ? `<div class="notice ${kind}">${html}</div>` : "";
}

// Live move/fence counter — the renderer calls this after each recorded move.
function updateMoveCount(n) {
  const el = $("fences");
  if (el) el.textContent = String(n);
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

// ── Solve / loss handlers passed to the renderer ──
function onSolvedFlow() {
  if (D.over || D.submitted) return;
  D.over = true;
  stopTimer();
  const n = D.renderer ? D.renderer.getMoves().length : 0;
  showNotice("info", `🎉 ${esc(solvedVerb())} in ${n} ${esc(unitWord())} — submitting your result…`);
  submit();
}

function onEscapedNotice() {
  D.over = true;
  stopTimer();
  showNotice(
    "warn",
    `🐷 It escaped — the daily wants a <b>WIN</b>. Reset and try again; only a trap counts, and you can retry as many times as you need.`
  );
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
  D.mmHistory = j.mm_history || []; // online games: prior guesses to repaint on resume
  if (j.game && j.game.id) D.today.game = Object.assign({}, D.today.game, j.game);
  if (j.par != null) D.today.par = j.par;
  if (j.difficulty != null) D.today.difficulty = j.difficulty;
  if (j.number != null) D.today.number = j.number;

  const R = window.DailyRenderers[gameId()];
  if (!R) return failStart("This game isn't supported in your browser yet — refresh and try again.");

  D.over = false;
  D.submitting = false;
  D.submitted = false;

  // Mastermind has no "stuck/reset" state — you just keep guessing — so hide Reset for it.
  buildSkeleton({ showReset: !isMind() });
  // Tear down any previous renderer before mounting the fresh board.
  if (D.renderer && D.renderer.teardown) {
    try { D.renderer.teardown(); } catch (_) {}
  }
  D.renderer = R;
  R.mount($("stage"), D.board, {
    onSolved: onSolvedFlow,
    onEscaped: onEscapedNotice,
    onMove: updateMoveCount,
  });
  startTimer(j.elapsed_ms); // continuous clock — includes time from earlier attempts
  const rb = $("resetBtn");
  if (rb) rb.onclick = startGame; // Reset = fresh board, but the clock keeps running.
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
  const moves = D.renderer ? D.renderer.getMoves() : [];
  const body = { start_token: D.startToken, solution: { moves } };
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
    // Didn't register as a win, or the token expired / was forged. The server
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
  // 200 — genuine win accepted.
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

  const title = opts.title || solvedTitle();
  $("resultArea").innerHTML = `<div class="result">
    <h2>${esc(title)}</h2>
    <p class="sub">${esc(solvedVerb())} in <b>${esc(primary)}</b> ${esc(unitWord())}${parStr} · ${esc(fmtTime(secondary))}</p>
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
  return `${g.icon || "🐷"} ${g.name || "Daily Game"}${numStr} · ${diff} · ${primary} ${unitWord()}${parStr} · ${fmtTime(secondary)}\n${blocks}`;
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
          <td class="num">${esc(r.primary)} ${esc(unitWord())}</td>
          <td class="num">${esc(fmtTime(r.secondary))}</td>
          <td class="num">+${num(r.points)} pts</td>
        </tr>`;
      })
      .join("");
    table.innerHTML = `<table><thead><tr>
        <th class="lb-rank">#</th><th>Player</th>
        <th class="num">${esc(unitLabel())}</th><th class="num">Time</th><th class="num">Points</th>
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

// ─────────────────────────────────────────────────────────────────────────────
// Trap the Pig renderer — the original pig play logic, now behind the daily
// renderer interface. Behaviour is UNCHANGED: same board (TrapPigBoard), same
// hex-click fencing, same server-identical pig AI, same reset-on-escape, and
// getMoves() returns the fence list [[r,c], ...] exactly as submit sends today.
// ─────────────────────────────────────────────────────────────────────────────
(function () {
  const B = window.TrapPigBoard;
  let svgEl = null;
  let work = null; // live board {rows, cols, pig:[r,c], fences:Set}
  let moves = []; // ordered [[r,c], ...] the player has fenced
  let cbs = {};
  let done = false;

  // Build a fresh working board from the /start board (deep-copied).
  function freshWork(board) {
    return {
      rows: board.rows,
      cols: board.cols,
      pig: [board.pig[0], board.pig[1]],
      fences: B.toKeySet(board.fences ? board.fences.map((f) => [f[0], f[1]]) : []),
    };
  }

  function draw(interactive) {
    B.renderInto(svgEl, work, interactive ? onPlace : null);
  }

  function onPlace(r, c) {
    if (done) return;
    const k = B.key(r, c);
    if (work.fences.has(k) || (work.pig[0] === r && work.pig[1] === c)) return;
    work.fences.add(k);
    moves.push([r, c]);
    if (cbs.onMove) cbs.onMove(moves.length);
    // Move the pig with the shared (server-identical) AI.
    const nxt = B.pigStep(work.pig, work.fences, work.rows, work.cols);
    if (nxt === null) {
      done = true;
      draw(false);
      return cbs.onSolved && cbs.onSolved();
    }
    work.pig = nxt;
    draw(true);
    if (B.isBorder(work.pig[0], work.pig[1], work.rows, work.cols)) {
      done = true;
      draw(false);
      return cbs.onEscaped && cbs.onEscaped();
    }
  }

  window.DailyRenderers["trappig"] = {
    mount(boardEl, board, callbacks) {
      cbs = callbacks || {};
      moves = [];
      done = false;
      boardEl.innerHTML = `<svg id="board"></svg>`;
      svgEl = boardEl.querySelector("svg");
      work = freshWork(board);
      draw(true);
      if (cbs.onMove) cbs.onMove(0);
    },
    getMoves() {
      return moves;
    },
    teardown() {
      svgEl = null;
      work = null;
      moves = [];
      done = true;
    },
  };
})();

// ─────────────────────────────────────────────────────────────────────────────
// Mastermind renderer — the platform's first ONLINE game. Unlike the pig/car
// renderers (which play fully offline and hand back a solution to replay), the
// secret code lives on the server: each guess is POSTed to /api/v1/daily/mm-guess
// which returns (black, white) feedback and PERSISTS the guess, so the move count
// is server-authoritative and survives a refresh (D.mmHistory repaints it).
// getMoves() returns every guess in order; the winning submit's last guess = code.
// ─────────────────────────────────────────────────────────────────────────────
(function () {
  const PALETTE = ["#e05a5a", "#e0913a", "#e8d24a", "#4ec06a",
                   "#4a86e8", "#9a6ce0", "#3ec6c6", "#e06aa8"];
  let stage = null, board = null, cbs = {};
  let rows = [];       // [{guess:[...], black, white}] — every scored guess (history + session)
  let current = [];    // the in-progress guess, entries are colorIdx or null
  let pending = false, solved = false;

  const pegHTML = (c, extra) =>
    `<span style="display:inline-block;width:26px;height:26px;border-radius:50%;` +
    `background:${PALETTE[c % PALETTE.length]};border:1px solid rgba(0,0,0,.35);` +
    `margin:3px;vertical-align:middle;${extra || ""}"></span>`;

  const slotHTML = (c, i) =>
    c == null
      ? `<button class="mm-slot" data-slot="${i}" title="empty" style="width:26px;height:26px;` +
        `border-radius:50%;margin:3px;vertical-align:middle;background:transparent;` +
        `border:2px dashed var(--border,#556);cursor:pointer"></button>`
      : `<button class="mm-slot" data-slot="${i}" title="click to clear" style="width:26px;` +
        `height:26px;border-radius:50%;margin:3px;vertical-align:middle;cursor:pointer;` +
        `background:${PALETTE[c % PALETTE.length]};border:1px solid rgba(0,0,0,.35)"></button>`;

  function feedbackHTML(black, white) {
    const total = board.len;
    let dots = "";
    for (let i = 0; i < black; i++)
      dots += `<span style="display:inline-block;width:11px;height:11px;border-radius:50%;background:#111;border:1px solid #000;margin:1px"></span>`;
    for (let i = 0; i < white; i++)
      dots += `<span style="display:inline-block;width:11px;height:11px;border-radius:50%;background:#fff;border:1px solid #999;margin:1px"></span>`;
    for (let i = black + white; i < total; i++)
      dots += `<span style="display:inline-block;width:11px;height:11px;border-radius:50%;background:transparent;border:1px solid var(--border,#556);margin:1px"></span>`;
    return `<span style="display:inline-block;min-width:70px;margin-left:12px">${dots}</span>`;
  }

  function render() {
    const history = rows
      .map(
        (r) =>
          `<div style="display:flex;align-items:center;margin:2px 0">` +
          r.guess.map((c) => pegHTML(c)).join("") +
          feedbackHTML(r.black, r.white) +
          `</div>`
      )
      .join("");

    let entry = "";
    if (!solved) {
      const slots = current.map((c, i) => slotHTML(c, i)).join("");
      const swatches = PALETTE.slice(0, board.colors)
        .map(
          (_, i) =>
            `<button class="mm-swatch" data-color="${i}" ${pending ? "disabled" : ""} ` +
            `style="width:30px;height:30px;border-radius:50%;margin:4px;cursor:pointer;` +
            `background:${PALETTE[i]};border:2px solid rgba(255,255,255,.25)"></button>`
        )
        .join("");
      const ready = current.every((c) => c != null) && !pending;
      entry =
        `<div style="margin-top:14px;padding-top:12px;border-top:1px solid var(--border,#556)">` +
        `<div style="display:flex;align-items:center">${slots}` +
        `<button class="btn" id="mmGuess" ${ready ? "" : "disabled"} style="margin-left:14px">${pending ? "…" : "Guess"}</button>` +
        `</div><div style="margin-top:8px">${swatches}</div></div>`;
    }

    stage.innerHTML =
      `<div style="max-width:420px;margin:0 auto">${history || ""}${entry}</div>`;

    if (solved) return;
    stage.querySelectorAll(".mm-swatch").forEach((b) => {
      b.onclick = () => {
        const idx = current.indexOf(null);
        if (idx === -1) return;
        current[idx] = Number(b.dataset.color);
        render();
      };
    });
    stage.querySelectorAll(".mm-slot").forEach((b) => {
      b.onclick = () => {
        current[Number(b.dataset.slot)] = null;
        render();
      };
    });
    const g = document.getElementById("mmGuess");
    if (g) g.onclick = sendGuess;
  }

  async function sendGuess() {
    if (pending || solved) return;
    if (current.some((c) => c == null)) return;
    pending = true;
    render();
    let r, j;
    try {
      r = await fetch("/api/v1/daily/mm-guess", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ start_token: D.startToken, guess: current }),
      });
      j = await r.json().catch(() => ({}));
    } catch (_) {
      pending = false;
      showNotice("warn", "Network error — try that guess again.");
      render();
      return;
    }
    pending = false;
    if (!r.ok) {
      showNotice("warn", esc((j && (j.error || j.detail)) || "Guess rejected — press Start again."));
      render();
      return;
    }
    rows.push({ guess: current.slice(), black: j.black, white: j.white });
    if (cbs.onMove) cbs.onMove(j.count != null ? j.count : rows.length);
    current = new Array(board.len).fill(null);
    if (j.solved) {
      solved = true;
      render();
      if (cbs.onSolved) cbs.onSolved();
      return;
    }
    render();
  }

  window.DailyRenderers["mastermind"] = {
    mount(stageEl, b, callbacks) {
      stage = stageEl;
      board = b;
      cbs = callbacks || {};
      pending = false;
      solved = false;
      rows = (D.mmHistory || []).map((h) => ({ guess: h[0], black: h[1], white: h[2] }));
      current = new Array(board.len).fill(null);
      render();
      if (cbs.onMove) cbs.onMove(rows.length);
    },
    getMoves() {
      return rows.map((r) => r.guess);
    },
    teardown() {
      stage = null;
      board = null;
      rows = [];
      current = [];
      solved = true;
    },
  };
})();

// ─────────────────────────────────────────────────────────────────────────────
// Countdown renderer — an OFFLINE game (like the pig/car): the board (six numbers
// + a target) is handed over on Start and the whole solution rides in a single
// expression string. getMoves() returns [expression]; the win is REPLAYED and
// scored server-side by the plugin's validate(), so the client is never trusted
// for the outcome. cdEval() below mirrors the server evaluator EXACTLY (integer
// arithmetic, exact division only, each number usable only as it appears) so the
// "Exact!" the player sees is the same verdict the server will reach on submit.
// Ranking is fastest-exact-solve; the count of numbers used breaks ties.
// ─────────────────────────────────────────────────────────────────────────────
(function () {
  let stage = null, board = null, cbs = {}, expr = "", solved = false;

  const OPS = { "+": 1, "-": 1, "*": 2, "/": 2 };

  // Parse + evaluate an expression under Countdown rules; returns {ok, value, err}.
  // Deliberately identical in behaviour to shared/daily_games/countdown.evaluate_expression.
  function cdEval(s, numbers) {
    const toks = [];
    let i = 0;
    while (i < s.length) {
      const c = s[i];
      if (c === " ") { i++; continue; }
      if (c >= "0" && c <= "9") {
        let j = i;
        while (j < s.length && s[j] >= "0" && s[j] <= "9") j++;
        toks.push(parseInt(s.slice(i, j), 10));
        i = j;
        continue;
      }
      if (c in OPS || c === "(" || c === ")") { toks.push(c); i++; continue; }
      return { ok: false, value: null, err: "illegal character" };
    }
    if (!toks.length) return { ok: false, value: null, err: "" };
    const out = [], st = [];
    let prev = null;
    for (const t of toks) {
      if (typeof t === "number") {
        if (prev === "num" || prev === ")") return { ok: false, value: null, err: "missing operator" };
        out.push(t); prev = "num";
      } else if (t in OPS) {
        if (prev === null || prev === "op" || prev === "(") return { ok: false, value: null, err: "misplaced operator" };
        while (st.length && st[st.length - 1] in OPS && OPS[st[st.length - 1]] >= OPS[t]) out.push(st.pop());
        st.push(t); prev = "op";
      } else if (t === "(") {
        if (prev === "num" || prev === ")") return { ok: false, value: null, err: "missing operator" };
        st.push(t); prev = "(";
      } else if (t === ")") {
        if (prev !== "num" && prev !== ")") return { ok: false, value: null, err: "misplaced )" };
        while (st.length && st[st.length - 1] !== "(") out.push(st.pop());
        if (!st.length) return { ok: false, value: null, err: "unbalanced ()" };
        st.pop(); prev = ")";
      }
    }
    if (prev === "op" || prev === "(") return { ok: false, value: null, err: "incomplete" };
    while (st.length) { const op = st.pop(); if (op === "(") return { ok: false, value: null, err: "unbalanced ()" }; out.push(op); }
    const vs = [], used = [];
    for (const t of out) {
      if (typeof t === "number") { vs.push(t); used.push(t); continue; }
      if (vs.length < 2) return { ok: false, value: null, err: "malformed" };
      const b = vs.pop(), a = vs.pop();
      if (t === "+") vs.push(a + b);
      else if (t === "-") vs.push(a - b);
      else if (t === "*") vs.push(a * b);
      else { if (b === 0 || a % b !== 0) return { ok: false, value: null, err: "division must be exact" }; vs.push(a / b); }
    }
    if (vs.length !== 1) return { ok: false, value: null, err: "malformed" };
    const avail = {};
    for (const x of numbers) avail[x] = (avail[x] || 0) + 1;
    for (const x of used) { if (!avail[x]) return { ok: false, value: null, err: "number " + x + " isn't available" }; avail[x]--; }
    return { ok: true, value: vs[0], err: "" };
  }

  const OP_FACE = { "+": "+", "-": "−", "*": "×", "/": "÷" };

  function render() {
    const tiles = board.numbers
      .map((n) => `<button class="cd-tile" data-ins="${n}">${n}</button>`)
      .join("");
    const ops = ["+", "-", "*", "/", "(", ")"]
      .map((o) => `<button class="cd-op" data-ins="${o}">${OP_FACE[o] || o}</button>`)
      .join("");

    const ev = expr.trim() ? cdEval(expr, board.numbers) : null;
    let status = `<span class="cd-hint">Tap numbers &amp; operators, or type. Hit <b>${esc(board.target)}</b> exactly.</span>`;
    if (ev) {
      if (!ev.ok) {
        status = `<span class="cd-bad">${esc(ev.err || "keep going")}</span>`;
      } else if (ev.value === board.target) {
        status = `<span class="cd-good">✓ Exact — ${esc(ev.value)}! Locking it in…</span>`;
      } else {
        const d = ev.value - board.target;
        status = `<span class="cd-near">= ${esc(ev.value)} · ${d > 0 ? "+" : ""}${esc(d)} from target</span>`;
      }
    }

    stage.innerHTML =
      `<div class="cd-wrap">
        <div class="cd-target">Target <b>${esc(board.target)}</b></div>
        <div class="cd-tiles">${tiles}</div>
        <div class="cd-ops">${ops}</div>
        <input id="cdExpr" class="cd-expr" type="text" inputmode="text" autocomplete="off"
               spellcheck="false" placeholder="e.g. (100 + 25) * 3" value="${esc(expr)}" />
        <div class="cd-controls">
          <button class="cd-op" id="cdBack">⌫</button>
          <button class="cd-op" id="cdClear">Clear</button>
        </div>
        <div class="cd-status">${status}</div>
      </div>`;

    if (solved) {
      stage.querySelectorAll("button, input").forEach((el) => (el.disabled = true));
      return;
    }

    const input = document.getElementById("cdExpr");
    const sync = () => { expr = input.value; check(); };
    input.oninput = () => { expr = input.value; live(); };
    input.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); check(); } };

    stage.querySelectorAll("[data-ins]").forEach((b) => {
      b.onclick = () => { expr += b.dataset.ins; render(); focusEnd(); };
    });
    document.getElementById("cdBack").onclick = () => { expr = expr.slice(0, -1); render(); focusEnd(); };
    document.getElementById("cdClear").onclick = () => { expr = ""; render(); focusEnd(); };
  }

  function focusEnd() {
    const input = document.getElementById("cdExpr");
    if (input) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
  }

  // Repaint the status line only (cheap) as the player types, without rebuilding the input.
  function live() {
    const el = stage && stage.querySelector(".cd-status");
    if (!el) return;
    const ev = expr.trim() ? cdEval(expr, board.numbers) : null;
    if (!ev) { el.innerHTML = `<span class="cd-hint">Hit <b>${esc(board.target)}</b> exactly.</span>`; }
    else if (!ev.ok) { el.innerHTML = `<span class="cd-bad">${esc(ev.err || "keep going")}</span>`; }
    else if (ev.value === board.target) { check(); }
    else { const d = ev.value - board.target; el.innerHTML = `<span class="cd-near">= ${esc(ev.value)} · ${d > 0 ? "+" : ""}${esc(d)} from target</span>`; }
  }

  // The player has (locally) hit the target exactly → hand the expression to the page, which
  // POSTs /submit; the server re-validates it before recording the solve.
  function check() {
    if (solved) return;
    const ev = expr.trim() ? cdEval(expr, board.numbers) : null;
    if (ev && ev.ok && ev.value === board.target) {
      solved = true;
      render();
      if (cbs.onSolved) cbs.onSolved();
    }
  }

  window.DailyRenderers["countdown"] = {
    mount(stageEl, b, callbacks) {
      stage = stageEl;
      board = b;
      cbs = callbacks || {};
      expr = "";
      solved = false;
      render();
      if (cbs.onMove) cbs.onMove(0);
    },
    getMoves() {
      return [expr];
    },
    teardown() {
      stage = null;
      board = null;
      expr = "";
      solved = true;
    },
  };
})();
