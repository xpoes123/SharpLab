// Showdown — hand-reading speed game. Every hand + the board face up: name the winner, fast.
// Daily Time Trial: the server deals the same 10 deals per (game, hands) to everyone, times the run and grades it
// (web/g_showdown.py). Practice: dealt here with deals.js, unlimited, explained after every answer.
// eval.js / deals.js are verbatim copies of the TV repo's games/showdown/ (see the note at the top of each).
import { VARIANTS, CAT_NAMES, maxHands } from "/showdown/eval.js";
import { makeDeal, fromCards, grade, LETTERS, LEVELS } from "/showdown/deals.js";

const app = document.getElementById("app");
const navRight = document.getElementById("navRight");
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const SUIT = { s: "♠", h: "♥", d: "♦", c: "♣" };
const ORDER = ["plo4", "nlh", "plo5", "plo6", "short", "o8", "bigo"];
const DEFAULT_HANDS = { nlh: 6, short: 6, plo4: 4, plo5: 4, plo6: 4, o8: 4, bigo: 4 };
const API = "/api/v1/arcade/showdown";
const store = { get(k, d) { try { const v = localStorage.getItem("sd_" + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("sd_" + k, JSON.stringify(v)); } catch { /* private mode */ } } };

const S = {
  tab: store.get("tab", "daily"), variant: store.get("variant", "plo4"), hands: store.get("hands", 4),
  level: store.get("level", "Ramp"), four: store.get("four", true), me: null, today: null,
  run: null,   // {kind: "daily"|"practice", deals, i, picks, t0, ...}
};
if (!VARIANTS[S.variant]) S.variant = "plo4";
S.hands = Math.min(maxHands(S.variant), Math.max(2, S.hands));

const fmt = (ms) => { const s = ms / 1000; return s >= 60 ? `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}` : `${s.toFixed(1)} s`; };
const levelIdx = (i, n) => (S.level === "Ramp" ? (i < n / 3 ? 0 : i < (2 * n) / 3 ? 1 : 2) : LEVELS.indexOf(S.level));

async function nav() {
  try {
    const r = await fetch("/api/v1/hq/me", { credentials: "include" });
    S.me = r.ok ? await r.json() : null;
  } catch { S.me = null; }
  if (S.me && S.me.authenticated) navRight.innerHTML = `<div class="userbar"><span>${esc(S.me.user.username)}</span></div>`;
  else navRight.innerHTML = `<a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a>`;
}

function rulesLine(v) {
  const V = VARIANTS[v];
  if (V.omaha) return `${V.hole} cards each. Exactly 2 from the hand + 3 from the board${V.hilo ? ". Name the high and the 8-or-better low" : ""}.`;
  if (V.deck === 36) return "Hold'em with 6 to A. Flush beats full house, trips beat a straight, A-6-7-8-9 is a straight.";
  return "2 cards each. Best 5 of 7 in any mix: the board can play.";
}

function shell() {
  const V = VARIANTS[S.variant];
  app.innerHTML = `
    <div><div class="sd-eyebrow">SharpLab · Arcade</div>
    <h1 class="sd-title">🃏 Showdown</h1>
    <p class="sd-sub">Every hand is face up. Who wins? Read it fast. Medium and Hard deals are picked to fool the wrong rules.</p></div>
    <div class="ui-tabs" role="tablist">
      <button class="ui-tab" role="tab" data-tab="daily" aria-selected="${S.tab === "daily"}">Daily Time Trial</button>
      <button class="ui-tab" role="tab" data-tab="practice" aria-selected="${S.tab === "practice"}">Practice</button>
    </div>
    <div class="sd-row"><span class="lbl">Game</span>${ORDER.map((v) => `<button class="sd-chip" data-v="${v}" aria-pressed="${v === S.variant}">${esc(VARIANTS[v].name)}</button>`).join("")}</div>
    <div class="sd-row"><span class="lbl">Hands</span>${[2, 3, 4, 5, 6, 7, 8, 9].map((n) => `<button class="sd-chip" data-n="${n}" aria-pressed="${n === S.hands}" ${n > maxHands(S.variant) ? "disabled" : ""}>${n}</button>`).join("")}</div>
    ${S.tab === "practice" ? `<div class="sd-row"><span class="lbl">Level</span>${["Ramp", ...LEVELS].map((l) => `<button class="sd-chip" data-l="${l}" aria-pressed="${l === S.level}">${l}</button>`).join("")}</div>` : ""}
    <div class="sd-row"><span class="lbl">Deck</span><button class="sd-chip" id="deckBtn" aria-pressed="${S.four}">Four-colour</button>
      <span class="ui-muted" style="font-size:var(--text-sm)">${esc(rulesLine(S.variant))}</span></div>
    <div id="body"></div>`;
  app.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => { S.tab = b.dataset.tab; store.set("tab", S.tab); S.run = null; render(); }));
  app.querySelectorAll("[data-v]").forEach((b) => (b.onclick = () => {
    S.variant = b.dataset.v; S.hands = Math.min(maxHands(S.variant), store.get("hands_" + S.variant, DEFAULT_HANDS[S.variant]));
    store.set("variant", S.variant); S.run = null; render();
  }));
  app.querySelectorAll("[data-n]").forEach((b) => (b.onclick = () => { S.hands = +b.dataset.n; store.set("hands", S.hands); store.set("hands_" + S.variant, S.hands); S.run = null; render(); }));
  app.querySelectorAll("[data-l]").forEach((b) => (b.onclick = () => { S.level = b.dataset.l; store.set("level", S.level); S.run = null; render(); }));
  $("deckBtn").onclick = () => { S.four = !S.four; store.set("four", S.four); render(); };
  app.classList.toggle("four", S.four); app.classList.toggle("two", !S.four);
}

// ── cards + table ───────────────────────────────────────────────────────────
function cardHtml(c, used) {
  const r = c[0] === "T" ? "10" : c[0];
  return `<div class="card ${c[1]}${used ? " used" : ""}"><span class="r">${r}</span><span class="s">${SUIT[c[1]]}</span></div>`;
}

function tableHtml(deal, reveal) {
  const hole = VARIANTS[deal.variant].hole;
  const vw = Math.min(window.innerWidth, 980) - 32;
  const cw = Math.max(28, Math.min(52, Math.floor((vw - 76) / (hole + 0.3))));
  const wh = deal.hands.find((h) => h.win), lh = deal.hands.find((h) => h.lowWin);
  const hands = deal.hands.map((h, i) => {
    const used = (c) => reveal && (h.use.includes(c) || (h.lowWin && h.low && h.low.use.includes(c)));
    return `<div class="sd-hand${reveal && (h.win || h.lowWin) ? " win" : ""}">
      <div class="who"><span class="seat">${LETTERS[i]}</span></div>
      <div class="sd-cards">${h.cards.map((c) => cardHtml(c, used(c))).join("")}</div>
      ${reveal ? `<div class="name">${esc(h.name)}${h.low ? ` · ${esc(h.low.name)}` : ""}</div>` : ""}
    </div>`;
  }).join("");
  const bUsed = (c) => reveal && ((wh && wh.board.includes(c)) || (lh && lh.low && lh.low.board.includes(c)));
  return `<div class="sd-table${reveal ? " revealed" : ""}" style="--card-w:${cw}px;--hand-min:${hole * (cw + 4) + 56}px;--board-w:${Math.min(72, cw + 18)}px">
    <div class="sd-hands">${hands}</div>
    <div class="sd-board">${deal.board.map((c) => cardHtml(c, bUsed(c))).join("")}</div>
  </div>`;
}

function whyHtml(deal, right, picks) {
  const mark = right === null ? "" : right ? `<span class="ok">✓ Right.</span> ` : `<span class="bad">✗ ${esc(pickLabels(deal, picks))}.</span> `;
  return `<div class="sd-why"><div class="head">${mark}${esc(deal.headline)}</div>
    <p>${esc(deal.used)}${deal.lowLine ? ` · ${esc(deal.lowLine)}` : ""}</p>
    ${deal.notes.map((n) => `<p>${esc(n)}</p>`).join("")}</div>`;
}

function pickLabels(deal, picks) {
  return "You said " + picks.map((p, i) => { const it = deal.steps[i] && deal.steps[i].items.find((x) => x.name === p); return it ? it.label : "nothing"; }).join(", ");
}

function askHtml(deal, picks) {
  const st = deal.steps[picks.length];
  return `<div class="sd-ask"><div class="prompt">${esc(st.prompt)}${deal.steps.length > 1 ? ` <span class="ui-muted">(${picks.length + 1}/${deal.steps.length})</span>` : ""}</div>
    <div class="sd-choices">${st.items.map((it, k) => `<button class="ui-btn" data-pick="${it.name}">${esc(it.label)}<kbd>${keyFor(it, k)}</kbd></button>`).join("")}</div></div>`;
}
function keyFor(it, k) {
  if (it.name[0] === "s" && it.name !== "split") return LETTERS[+it.name.slice(1)].toLowerCase();
  if (it.name === "split") return "x";
  if (it.name === "nolow") return "n";
  return String(k + 1);
}

// ── play loop (shared by daily + practice) ──────────────────────────────────
function play() {
  const R = S.run, deal = R.deals[R.i];
  const body = $("body");
  const head = R.kind === "daily"
    ? `<div class="sd-stats">
        <div class="ui-stat"><div class="ui-stat__label">Deal</div><div class="ui-stat__value">${R.i + 1}/${R.deals.length}</div></div>
        <div class="ui-stat"><div class="ui-stat__label">Time</div><div class="ui-stat__value" id="clock">–</div></div>
        <div class="ui-stat"><div class="ui-stat__label">Level</div><div class="ui-stat__value">${LEVELS[deal.level]}</div></div>
        <div class="ui-stat"><div class="ui-stat__label">Penalty</div><div class="ui-stat__value">+10 s/miss</div></div></div>`
    : `<div class="sd-stats">
        <div class="ui-stat"><div class="ui-stat__label">Right</div><div class="ui-stat__value">${R.right}/${R.done}</div></div>
        <div class="ui-stat"><div class="ui-stat__label">Streak</div><div class="ui-stat__value">${R.streak}</div></div>
        <div class="ui-stat"><div class="ui-stat__label">Read</div><div class="ui-stat__value" id="clock">–</div></div>
        <div class="ui-stat"><div class="ui-stat__label">Best read</div><div class="ui-stat__value">${R.pb ? fmt(R.pb) : "–"}</div></div></div>`;
  const revealing = R.kind === "practice" && R.revealed;
  body.innerHTML = head + `<div id="tbl">${tableHtml(deal, revealing)}</div>` +
    (revealing ? whyHtml(deal, R.lastRight, R.picks) + `<button class="ui-btn ui-btn--primary" id="nextBtn">Next deal <kbd>Space</kbd></button>` : askHtml(deal, R.picks));
  body.querySelectorAll("[data-pick]").forEach((b) => (b.onclick = () => pick(b.dataset.pick)));
  if (revealing) $("nextBtn").onclick = nextPractice;
  if (!revealing && R.picks.length === 0 && !R.dealStart) R.dealStart = performance.now();
}

function pick(name) {
  const R = S.run; if (!R || (R.kind === "practice" && R.revealed)) return;
  const deal = R.deals[R.i];
  if (!deal.steps[R.picks.length].items.some((it) => it.name === name)) return;
  R.picks.push(name);
  const g = grade(deal, R.picks);
  if (R.picks.length < deal.steps.length && g.wrongStep < 0) { play(); return; }
  const right = g.right;
  if (R.kind === "daily") {
    while (R.picks.length < deal.steps.length) R.picks.push("x");   // a miss mid-way: the rest can't save it
    R.all.push(R.picks); R.rights.push(right);
    flash(right);
    R.i++; R.picks = []; R.dealStart = 0;
    if (R.i >= R.deals.length) submitDaily(); else setTimeout(play, 220);
  } else {
    const took = performance.now() - R.dealStart;
    R.done++; R.lastRight = right; R.revealed = true;
    if (right) {
      R.right++; R.streak++;
      if (!R.pb || took < R.pb) { R.pb = took; store.set(`pb_${S.variant}_${S.hands}`, took); }
    } else R.streak = 0;
    R.lastTook = took;
    play();
  }
}

function flash(ok) {
  const t = $("tbl"); if (!t) return;
  t.classList.add(ok ? "sd-flash-ok" : "sd-flash-bad");
}

function nextPractice() {
  const R = S.run;
  R.i++; R.picks = []; R.revealed = false; R.dealStart = 0;
  R.deals.push(makeDeal(R.rng, S.variant, S.hands, levelIdx(R.done, 15)));
  play();
}

// ── practice ────────────────────────────────────────────────────────────────
function practice() {
  const rng = { float: Math.random };
  S.run = { kind: "practice", rng, deals: [], i: 0, picks: [], right: 0, done: 0, streak: 0, revealed: false,
    pb: store.get(`pb_${S.variant}_${S.hands}`, null) };
  S.run.deals.push(makeDeal(rng, S.variant, S.hands, levelIdx(0, 15)));
  play();
}

// ── daily ───────────────────────────────────────────────────────────────────
async function daily() {
  const body = $("body");
  body.innerHTML = `<p class="ui-muted">Loading today's table…</p>`;
  let t = null;
  try { t = await (await fetch(`${API}/today?variant=${S.variant}&hands=${S.hands}`, { credentials: "include" })).json(); } catch { /* offline */ }
  if (!t || t.error) { body.innerHTML = `<p class="ui-muted">Couldn't load today's table.</p>`; return; }
  S.today = t;
  const V = VARIANTS[S.variant];
  const who = t.signed_in || t.guest;
  body.innerHTML = `<div class="ui-card ui-stack">
      <div><b>${esc(V.name)} · ${S.hands} hands · ${t.deals} deals</b> <span class="ui-muted">· ${esc(t.day)} · ramps Easy → Hard</span></div>
      <div class="ui-muted" style="font-size:var(--text-sm)">Same deals for everyone today. The clock starts when you press Start and runs until the last answer; each miss adds 10 s. One ranked run per game and table size per day. Keys: A-I pick a hand, X chop, N no low, 1-4 the hand type.</div>
      ${t.played ? `<div>Your run: <b>${fmt(t.your_result.score_ms)}</b> <span class="ui-muted">(${fmt(t.your_result.elapsed_ms)} + ${t.your_result.wrong} miss${t.your_result.wrong === 1 ? "" : "es"})</span></div>`
        : who ? `<div><button class="ui-btn ui-btn--primary" id="startBtn">Start the clock</button>${t.guest ? ` <span class="ui-muted">as ${esc(t.guest)} (guest)</span>` : ""}</div>`
        : `<div class="sd-row"><a class="ui-btn ui-btn--primary" href="/api/v1/auth/discord/login">Sign in with Discord</a>
            <input class="ui-input" id="guestName" maxlength="20" placeholder="or just a name"><button class="ui-btn" id="guestBtn">Play as guest</button></div>`}
    </div><div id="lb"></div>`;
  if ($("startBtn")) $("startBtn").onclick = startDaily;
  if ($("guestBtn")) {
    const go = async () => {
      const name = ($("guestName").value || "").trim(); if (!name) return $("guestName").focus();
      const r = await fetch("/api/v1/arcade/pokehoops/guest", { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) }).catch(() => null);
      if (r && r.ok) daily();
    };
    $("guestBtn").onclick = go; $("guestName").onkeydown = (e) => e.key === "Enter" && go();
  }
  leaderboard();
}

async function leaderboard() {
  let lb = null;
  try { lb = await (await fetch(`${API}/leaderboard?variant=${S.variant}&hands=${S.hands}`, { credentials: "include" })).json(); } catch { /* offline */ }
  const el = $("lb"); if (!el || !lb || !lb.top) return;
  el.innerHTML = `<h3 style="margin:var(--space-3) 0 var(--space-2)">Today · ${esc(VARIANTS[S.variant].name)} · ${S.hands} hands</h3>` + (lb.top.length
    ? `<table class="ui-table"><thead><tr><th>#</th><th>Player</th><th>Score</th><th>Time</th><th>Misses</th></tr></thead><tbody>${lb.top.map((r) =>
      `<tr${r.me ? ' style="color:var(--accent)"' : ""}><td>${r.rank}</td><td>${esc(r.name)}${r.guest ? ' <small class="ui-muted">guest</small>' : ""}</td><td class="ui-mono">${fmt(r.score_ms)}</td><td class="ui-mono">${fmt(r.elapsed_ms)}</td><td>${r.wrong}</td></tr>`).join("")}</tbody></table>`
    : `<p class="ui-muted">Nobody has run this table today. Be first.</p>`);
}

async function startDaily() {
  const r = await fetch(`${API}/start`, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ variant: S.variant, hands: S.hands }) }).catch(() => null);
  const j = r ? await r.json().catch(() => null) : null;
  if (!r || !r.ok || !j) { $("body").insertAdjacentHTML("afterbegin", `<p class="ui-pill ui-pill--bad">${esc((j && j.error) || "Couldn't start")}</p>`); return; }
  const deals = j.deals.map((d) => fromCards(S.variant, d.hands, d.board, d.level));
  S.run = { kind: "daily", deals, i: 0, picks: [], all: [], rights: [], token: j.token, t0: performance.now() - j.elapsed_ms };
  tick();
  play();
}

function tick() {
  const R = S.run; if (!R) return;
  const el = $("clock");
  if (el) el.textContent = R.kind === "daily" ? fmt(performance.now() - R.t0) : R.revealed ? fmt(R.lastTook) : R.dealStart ? fmt(performance.now() - R.dealStart) : "–";
  if (!R.finished) requestAnimationFrame(tick);
}

async function submitDaily() {
  const R = S.run; R.finished = true;
  $("body").innerHTML = `<p class="ui-muted">Grading…</p>`;
  const r = await fetch(`${API}/submit`, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token: R.token, picks: R.all }) }).catch(() => null);
  const j = r ? await r.json().catch(() => null) : null;
  if (!r || !r.ok || !j) { $("body").innerHTML = `<p class="ui-pill ui-pill--bad">${esc((j && j.error) || "Couldn't submit")}</p>`; return; }
  const review = R.deals.map((d, i) => `<details class="ui-card"${j.right[i] ? "" : " open"}><summary>${j.right[i] ? "✓" : "✗"} Deal ${i + 1} · ${LEVELS[d.level]} · ${esc(d.headline)}</summary>
      ${tableHtml(d, true)}${whyHtml(d, j.right[i], R.all[i])}</details>`).join("");
  $("body").innerHTML = `<div class="ui-card ui-stack">
      <div style="font-size:var(--text-xl);font-weight:700">${fmt(j.score_ms)}</div>
      <div class="ui-muted">${fmt(j.elapsed_ms)} on the clock + ${j.wrong} miss${j.wrong === 1 ? "" : "es"} × 10 s · #${j.rank} of ${j.field} today</div>
    </div><div class="sd-review">${review}</div><div id="lb"></div>`;
  leaderboard();
}

// ── keys ────────────────────────────────────────────────────────────────────
document.addEventListener("keydown", (e) => {
  const R = S.run; if (!R || e.target.tagName === "INPUT" || e.metaKey || e.ctrlKey || e.altKey) return;
  if (R.kind === "practice" && R.revealed) { if (e.key === " " || e.key === "Enter") { e.preventDefault(); nextPractice(); } return; }
  const deal = R.deals[R.i]; if (!deal || R.finished) return;
  const st = deal.steps[R.picks.length], k = e.key.toLowerCase();
  const it = st.items.find((x, i) => keyFor(x, i) === k);
  if (it) { e.preventDefault(); pick(it.name); }
});

function render() {
  shell();
  if (S.tab === "practice") practice(); else daily();
}

nav();
render();
