// SharpLab HQ — Quiz Bowl (solo). Answer qbreader.org 3-part bonus questions for
// coins, one part at a time. Rounds POST to /api/v1/arcade/quizbowl/* (session-cookie
// auth); each correct answer response carries the authoritative new balance, which we
// push back into the nav chip + on-page header. Answers live server-side (signed token).

const app = document.getElementById("app");
const navRight = document.getElementById("navRight");

// ── Helpers (copied verbatim from casino.js) ──
const num = (n) => (n == null ? "—" : Number(n).toLocaleString());
const coins = (n) => "🪙 " + num(Math.round(n || 0));
const esc = (s) =>
  String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function getJSON(url) {
  if (MOCK) return mockJSON(url);
  const r = await fetch(url, { credentials: "include" });
  if (!r.ok) return { _status: r.status };
  return r.json();
}

// ── Nav (login / logout) — mirrors casino.js (reads state.balance) ──
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

// ── State ──
const state = { me: null, balance: 0, correct: 0 };
// bonus: the current 3-part bonus we're walking through.
const bonus = {
  token: null, category: "", leadin: "", parts: [],
  part: 0, results: [], busy: false, answered: false, done: false,
};

// ── Toast (copied verbatim from casino.js) ──
function toast(msg) {
  const t = document.createElement("div");
  t.className = "cardtoast";
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3200);
}

// ── Coins hub (copied verbatim from casino.js) ──
const EARN_WAYS = [
  ["💬", "Chat in the server", "5 coins per message, up to 500 a day"],
  ["🎯", "Log a bet", "50 coins for logging a sports bet with /bet log"],
  ["📈", "Log a trade", "50 coins for recording a stock or option trade"],
  ["🏀", "Daily pick'em", "25 coins per pick — plus a coin payout when your pick wins"],
  ["🎁", "Free daily pack", "Open one free card pack every day — pure upside"],
  ["🃏", "Complete a set", "One-time coin bonus for owning every card in a set"],
  ["♻️", "Quick-sell dupes", "Sell duplicate cards back for coins in Discord"],
  ["🎮", "Win in the casino", "Win at /casino or /play — playing no longer hands out free coins"],
];
function showCoinsHub() {
  if (document.querySelector(".hubov")) return;
  const rows = EARN_WAYS.map(
    ([i, t, d]) => `<div class="hubrow"><div class="hubicon">${i}</div>
      <div><div class="hubt">${esc(t)}</div><div class="hubd">${esc(d)}</div></div></div>`
  ).join("");
  const ov = document.createElement("div");
  ov.className = "hubov";
  ov.innerHTML = `<div class="hubcard">
    <div class="hubhead"><h3>Ways to earn 🪙</h3><button class="hubx" aria-label="Close">✕</button></div>
    ${rows}
    <div class="hubfoot">Your balance: <b>🪙 ${num(state.balance)}</b></div>
  </div>`;
  ov.addEventListener("click", (e) => { if (e.target === ov || e.target.closest(".hubx")) ov.remove(); });
  document.body.appendChild(ov);
}
document.addEventListener("click", (e) => { if (e.target.closest(".coinschip")) showCoinsHub(); });

const REDUCE = matchMedia("(prefers-reduced-motion: reduce)").matches;

function applyBalance(bal) {
  if (bal == null) return;
  state.balance = bal;
  renderNav(state.me && state.me.user);
  const pb = document.getElementById("pageBal");
  if (pb) pb.textContent = coins(bal);
}

const $ = (id) => document.getElementById(id);

function setBusy(busy) {
  bonus.busy = busy;
  app.querySelectorAll(".qb-controls .btn, .qbinput").forEach((el) => (el.disabled = busy));
}

// ── POST to a quizbowl endpoint. Returns parsed JSON (with _status on error). ──
async function postQB(path, body) {
  if (MOCK) return mockQB(path, body);
  const r = await fetch("/api/v1/arcade/quizbowl" + path, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok && j.error == null) {
    j.error = r.status === 401 ? "sign in to play" : `error ${r.status}`;
  }
  return j;
}

// ── Fetch and start a new bonus ──
async function newBonus() {
  const btn = $("qbNew");
  if (btn) { btn.disabled = true; btn.textContent = "Loading…"; }
  bonus.busy = true;
  const res = await postQB("/new", {});
  if (res.error || res._status) {
    if (btn) { btn.disabled = false; btn.textContent = bonus.token ? "Next bonus" : "New question"; }
    bonus.busy = false;
    return toast("❌ " + (res.error || "couldn't start a bonus"));
  }
  bonus.token = res.token;
  bonus.category = res.category || "";
  bonus.leadin = res.leadin || "";
  bonus.parts = res.parts || [];
  bonus.part = 0;
  bonus.results = [];
  bonus.answered = false;
  bonus.done = false;
  bonus.busy = false;
  renderPart();
}

// ── Render the current part ──
function renderPart() {
  const i = bonus.part;
  const qtext = bonus.parts[i] || "";
  app.querySelector("#qbCard").innerHTML = `
    <div class="qb-meta">
      <span class="qb-cat">${esc(bonus.category)}</span>
      <span class="qb-progress">Part ${i + 1} / 3</span>
    </div>
    ${bonus.leadin ? `<div class="qb-leadin">${esc(bonus.leadin)}</div>` : ""}
    <div class="qb-question">${esc(qtext)}</div>
    <div class="qbinputrow">
      <input class="qbinput" id="qbInput" type="text" autocomplete="off" autocapitalize="off"
             spellcheck="false" placeholder="Type your answer…" />
    </div>
    <div class="qb-result" id="qbResult"></div>
    <div class="qb-controls" id="qbControls">
      <button class="btn primary big" id="qbAnswer">Answer</button>
    </div>`;
  const input = $("qbInput");
  if (input) input.focus();
}

// ── Submit an answer for the current part ──
async function submitAnswer() {
  if (bonus.busy || bonus.answered || bonus.done) return;
  const input = $("qbInput");
  const given = (input && input.value || "").trim();
  if (!given) { if (input) input.focus(); return; }
  setBusy(true);
  const res = await postQB("/answer", { token: bonus.token, part: bonus.part, given });
  setBusy(false);
  if (res.error || res._status) {
    return toast("❌ " + (res.error || "something went wrong"));
  }
  bonus.answered = true;
  bonus.results.push({ correct: !!res.correct, answer: res.answer, reward: res.reward || 0 });
  if (res.correct) {
    applyBalance(res.balance);
    state.correct += 1;
    const c = $("qbCorrect");
    if (c) c.textContent = num(state.correct);
  } else if (input && !REDUCE) {
    input.classList.remove("shake"); void input.offsetWidth; input.classList.add("shake");
    input.addEventListener("animationend", () => input.classList.remove("shake"), { once: true });
  }
  showResult(res);
}

// ── Reveal correct/incorrect + accepted answer, then offer to advance ──
function showResult(res) {
  const input = $("qbInput");
  if (input) input.disabled = true;
  const resEl = $("qbResult");
  const last = bonus.part >= 2;
  const rewardLine = res.correct && res.reward
    ? `<span class="qb-reward">+${num(res.reward)} 🪙</span>` : "";
  if (resEl) {
    resEl.className = "qb-result " + (res.correct ? "ok" : "no");
    resEl.innerHTML = `
      <div class="qb-verdict">${res.correct ? "✅ Correct!" : "❌ Incorrect"} ${rewardLine}</div>
      <div class="qb-answer">Answer: <b>${esc(res.answer || "???")}</b></div>`;
  }
  const controls = $("qbControls");
  if (controls) {
    controls.innerHTML = last
      ? `<button class="btn primary big" id="qbSummary">See summary →</button>`
      : `<button class="btn primary big" id="qbNextPart">Next part →</button>`;
  }
}

// ── Advance to the next part ──
function nextPart() {
  if (bonus.part >= 2) return;
  bonus.part += 1;
  bonus.answered = false;
  renderPart();
}

// ── Bonus summary after all 3 parts ──
function showSummary() {
  bonus.done = true;
  const got = bonus.results.filter((r) => r.correct).length;
  const earned = bonus.results.reduce((s, r) => s + (r.reward || 0), 0);
  const rows = bonus.results.map((r, i) => `
    <div class="qb-srow">
      <span class="qb-smark">${r.correct ? "✅" : "❌"}</span>
      <span class="qb-spart">Part ${i + 1}</span>
      <span class="qb-sans">${esc(r.answer || "???")}</span>
    </div>`).join("");
  app.querySelector("#qbCard").innerHTML = `
    <div class="qb-meta">
      <span class="qb-cat">${esc(bonus.category)}</span>
      <span class="qb-progress">Bonus complete</span>
    </div>
    <div class="qb-scoreline">You got <b>${got}</b> / 3${earned ? ` · <span class="qb-reward">+${num(earned)} 🪙</span>` : ""}</div>
    <div class="qb-summary">${rows}</div>
    <div class="qb-controls" id="qbControls">
      <button class="btn primary big" id="qbNew">Next bonus</button>
    </div>`;
}

// ── Delegated events ──
app.addEventListener("click", (e) => {
  if (e.target.closest("#qbNew")) return newBonus();
  if (e.target.closest("#qbAnswer")) return submitAnswer();
  if (e.target.closest("#qbNextPart")) return nextPart();
  if (e.target.closest("#qbSummary")) return showSummary();
});
app.addEventListener("keydown", (e) => {
  if (e.target.id === "qbInput" && e.key === "Enter") { e.preventDefault(); submitAnswer(); }
});

function buildPage() {
  const signedOut = !state.me
    ? `<div class="card" style="max-width:520px;margin:0 auto 16px;text-align:center">
        <p class="muted" style="margin:0 0 10px">Sign in with Discord to play for coins.</p>
        <a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a></div>`
    : "";
  app.innerHTML = `
    <div class="qb-head">
      <h1>🧠 Quiz Bowl <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Answer 3-part bonus questions from qbreader.org. Earn coins for every part you get right.</p>
    </div>
    ${signedOut}
    <div class="qb-wrap">
      <div class="card qbcard" id="qbCard">
        <div class="qb-empty">
          <div class="qb-emptybig">🧠</div>
          <p class="muted">Hit “New question” for a fresh 3-part bonus.</p>
        </div>
        <div class="qb-controls" id="qbControls">
          <button class="btn primary big" id="qbNew">New question</button>
        </div>
      </div>
      <div class="qbcounter">Parts correct this session: <b id="qbCorrect">0</b></div>
    </div>`;
}

async function main() {
  const me = await getJSON("/api/v1/hq/me");
  const loggedIn = me && me.authenticated;
  state.me = loggedIn ? me : null;
  state.balance = loggedIn ? (me.balance || 0) : 0;
  renderNav(loggedIn ? me.user : null);
  buildPage();
}

// ─────────────────────────────────────────────────────────────
// Mock mode (?mock=1): fake bonuses with local case-insensitive
// substring answer-checking so the page can be screenshot-tested
// offline. Real endpoints win by default.
// ─────────────────────────────────────────────────────────────
const MOCK = new URLSearchParams(location.search).get("mock") === "1";

const MOCK_BONUS = {
  category: "Literature / European",
  leadin: "For 10 points each, answer the following about a landmark of world literature.",
  parts: [
    "This Russian author wrote the epic novels War and Peace and Anna Karenina.",
    "Anna Karenina opens with a famous line about happy and unhappy families of this kind.",
    "Anna Karenina's fatal despair culminates when she throws herself under one of these vehicles.",
  ],
  answers: ["Leo Tolstoy", "families", "a train"],
};

function mockJSON(url) {
  if (url.startsWith("/api/v1/hq/me"))
    return { authenticated: true, user: { id: "1", username: "davidj", avatar: null }, balance: 12500 };
  return {};
}

function mockQB(path, body) {
  if (path === "/new") {
    return { token: "mock-token", category: MOCK_BONUS.category, leadin: MOCK_BONUS.leadin, parts: MOCK_BONUS.parts };
  }
  if (path === "/answer") {
    const part = body.part || 0;
    const answer = MOCK_BONUS.answers[part] || "";
    const given = String(body.given || "").trim().toLowerCase();
    const correct = given.length > 1 &&
      (answer.toLowerCase().includes(given) || given.includes(answer.toLowerCase().split(" ").pop()));
    if (correct) {
      const reward = 20;
      state.balance += reward;
      return { correct: true, answer, directive: "accept", reward, balance: state.balance };
    }
    return { correct: false, answer, directive: "reject" };
  }
  if (path === "/next-part") {
    return { part: body.part, question: MOCK_BONUS.parts[body.part] || "" };
  }
  return {};
}

main();
