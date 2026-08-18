// SharpLab HQ — Cards: your collection + set browser + catalog/odds (read-only).
// Buying/opening packs happens in Discord; this page just browses.

const app = document.getElementById("app");
const navRight = document.getElementById("navRight");

const num = (n) => (n == null ? "—" : Number(n).toLocaleString());
const coins = (n) => "🪙 " + num(Math.round(n || 0));
const esc = (s) =>
  String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const SPORT_EMOJI = { nba: "🏀", nfl: "🏈", mlb: "⚾" };
const RARITY_ORDER = ["common", "uncommon", "rare", "epic", "legendary"];
const GEM_EMOJI = { sapphire: "🔵", ruby: "🔴", emerald: "🟢", amethyst: "🟣", diamond: "💎", gold: "🟡" };

// Neutral silhouette placeholder (data URI) — swapped in via onerror so the grid never breaks.
const SILHOUETTE =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">' +
      '<rect width="100" height="100" fill="none"/>' +
      '<circle cx="50" cy="38" r="20" fill="#3a3f57"/>' +
      '<path d="M18 92c0-18 14-30 32-30s32 12 32 30z" fill="#3a3f57"/>' +
      "</svg>"
  );
// Expose globally so inline onerror handlers can reach it.
window.__cardSilh = SILHOUETTE;

async function getJSON(url) {
  if (MOCK) return mockJSON(url);
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
    <span class="coinschip" title="Casino coins">🪙 ${num(state.balance)}</span>
    <a class="btn ghost" href="/api/v1/auth/logout">Sign out</a></div>`;
}

// ── State ──
const state = { tab: "collection", mine: null, sets: null, catalog: {}, activeSet: null, me: null, balance: 0, dailyClaimed: null };

// ── Card tile ──
function cardTile(c) {
  const rarity = (c.rarity || "common").toLowerCase();
  const holo = c.is_holo ? " holo" : "";
  const sport = (c.sport || "").toLowerCase();
  const emoji = SPORT_EMOJI[sport] || "🃏";
  const oneOfOne = Number(c.total_copies) === 1;
  const serial = c.serial != null
    ? `<span class="serial${oneOfOne ? " oneofone" : ""}">#${c.serial}/${c.total_copies}</span>`
    : "";
  const gem = c.gem
    ? `<span class="gem-badge">${GEM_EMOJI[String(c.gem).toLowerCase()] || "💠"} ${esc(c.gem)}</span>`
    : "";
  const rookie = c.is_rookie ? `<span class="rookie-badge">RC</span>` : "";
  const src = c.headshot_url || SILHOUETTE;
  return `<div class="ctile rarity-${rarity}${holo}">
    <div class="cimg">
      <img src="${esc(src)}" alt="${esc(c.name)}" loading="lazy"
           onerror="this.onerror=null;this.src=window.__cardSilh;this.classList.add('silh');">
      <span class="sport-badge" title="${esc(sport.toUpperCase())}">${emoji}</span>
      ${gem}${rookie}
    </div>
    <div class="cbody">
      <div class="cname">${esc(c.name)}</div>
      <div class="cteam">${esc(c.team || "")}</div>
      <div class="cmeta">
        <span class="rarity-label">${esc(rarity)}</span>
        ${serial}
      </div>
      <div class="cvalue">${coins(c.book_value)}</div>
    </div>
  </div>`;
}

function emptyBox(icon, msg) {
  return `<div class="emptybox"><div class="big">${icon}</div>${msg}</div>`;
}

// ── Views ──
function renderCollection() {
  const d = state.mine;
  if (d && d._status === 401) {
    return emptyBox("🔒", "<strong>Sign in to see your cards.</strong><br>Then open a pack in Discord with <code>/pack open</code>.");
  }
  const cards = (d && d.cards) || [];
  if (!cards.length) {
    return emptyBox("🃏", "<strong>No cards yet.</strong><br>Open a pack in Discord with <code>/pack open &lt;sport&gt; &lt;season&gt;</code>.");
  }
  // Sort rarest → most common, then by book value.
  const sorted = [...cards].sort((a, b) => {
    const ra = RARITY_ORDER.indexOf((b.rarity || "").toLowerCase()) - RARITY_ORDER.indexOf((a.rarity || "").toLowerCase());
    return ra !== 0 ? ra : (b.book_value || 0) - (a.book_value || 0);
  });
  const total = d.collection_value != null
    ? d.collection_value
    : cards.reduce((s, c) => s + (c.book_value || 0), 0);
  const hero = `<div class="collval">
    <div><div class="lbl">Collection book value</div><div class="bigval">${coins(total)}</div></div>
    <div class="sub">${cards.length} card${cards.length === 1 ? "" : "s"}</div>
  </div>`;
  return hero + `<div class="cardgrid">${sorted.map(cardTile).join("")}</div>`;
}

// Distinct design_ids the logged-in user owns (from /api/v1/cards/mine).
function ownedDesignIds() {
  const owned = new Set();
  const mine = state.mine;
  if (mine && mine.cards) mine.cards.forEach((c) => owned.add(c.design_id));
  return owned;
}

// Per-set completion (owned distinct designs / total designs) — computed from the
// user's collection intersected with each set's cached catalog. Returns null when
// we can't compute it yet (not signed in, or the catalog hasn't loaded).
function setCompletion(setId) {
  if (!state.me) return null;
  const cat = state.catalog[setId];
  if (!cat || !cat.designs) return null;
  const owned = ownedDesignIds();
  const total = cat.designs.length;
  const have = cat.designs.filter((d) => owned.has(d.design_id)).length;
  return { have, total, pct: total ? Math.round((have / total) * 100) : 0, done: total > 0 && have >= total };
}

function renderSets() {
  const d = state.sets;
  if (d && d._status === 401) {
    return emptyBox("🔒", "<strong>Sign in to browse sets.</strong>");
  }
  const sets = (d && d.sets) || [];
  if (!sets.length) return emptyBox("📦", "No sets available yet.");
  const rows = sets.map((s) => {
    const emoji = SPORT_EMOJI[(s.sport || "").toLowerCase()] || "🏟️";
    const opened = s.packs_opened || 0;
    const totalP = s.total_packs || 0;
    const pctVal = totalP ? Math.min(100, Math.round((opened / totalP) * 100)) : 0;
    const closed = s.closed || (totalP && opened >= totalP);
    const comp = setCompletion(s.set_id);
    // Always reserve the completion row when signed in (skeleton until catalogs load) so the
    // async ensureSetCompletion redraw fills it in place instead of resizing every card.
    const compHtml = state.me
      ? `<div class="progwrap">
          <div class="progbar comp${comp && comp.done ? " done" : ""}"><span style="width:${comp ? comp.pct : 0}%"></span></div>
          <div class="progtext">${comp ? `${comp.done ? "✅ " : ""}${num(comp.have)} / ${num(comp.total)} designs` : "…"}</div>
        </div>`
      : "";
    return `<div class="setcard" data-setid="${esc(s.set_id)}">
      <div>
        <div class="setname">${emoji} ${esc(s.name)} ${closed ? `<span class="soldout">Sold out</span>` : ""}</div>
        <div class="setsub">${esc((s.sport || "").toUpperCase())} · ${esc(s.season)}</div>
      </div>
      <div class="setright">
        <div class="price">${coins(s.pack_cost)}<span class="setsub"> / pack</span></div>
        <div class="progwrap">
          <div class="progbar"><span style="width:${pctVal}%"></span></div>
          <div class="progtext">${num(opened)} / ${num(totalP)} packs</div>
        </div>
        ${compHtml}
      </div>
      ${state.me && !closed ? `<div class="setactions"><button class="btn primary openbtn"
        data-sport="${esc((s.sport || "").toLowerCase())}" data-season="${esc(s.season)}"
        data-cost="${esc(s.pack_cost)}">Open pack · ${coins(s.pack_cost)}</button></div>` : ""}
    </div>`;
  }).join("");
  const daily = state.me
    ? (state.dailyClaimed
        ? `<div class="dailybar"><span class="dailychip">🎁 Next free pack in <b id="dailyCountdown">${fmtDur(msToUtcMidnight())}</b></span></div>`
        : `<div class="dailybar"><button class="btn primary dailybtn">🎁 Free daily pack</button>
           <span class="muted">One free pack a day — newest set.</span></div>`)
    : "";
  return `${daily}<p class="muted" style="margin:0 0 14px">Tap a set to see its checklist and pull-rate odds, or open a pack.</p>
    <div class="setlist">${rows}</div>`;
}

function renderCatalog(setId) {
  const d = state.catalog[setId];
  if (!d) return `<div class="hero"><p class="muted">Loading catalog…</p></div>`;
  if (d._status === 401) return emptyBox("🔒", "<strong>Sign in to view the catalog.</strong>");
  const set = d.set || {};
  const emoji = SPORT_EMOJI[(set.sport || "").toLowerCase()] || "🏟️";
  const odds = d.odds || {};
  const pr = odds.pull_rates || {};
  const oddsCards = [];
  RARITY_ORDER.forEach((r) => {
    if (pr[r] != null) oddsCards.push(`<div class="oddscard"><div class="k">${r}</div><div class="v" style="color:var(--r-${r})">${fmtPct(pr[r])}</div></div>`);
  });
  if (odds.holo_pct != null) oddsCards.push(`<div class="oddscard"><div class="k">Holo</div><div class="v">${fmtPct(odds.holo_pct)}</div></div>`);
  const gems = odds.gems || {};
  Object.keys(gems).forEach((g) => {
    const info = gems[g] || {};
    oddsCards.push(`<div class="oddscard"><div class="k">${GEM_EMOJI[g.toLowerCase()] || "💠"} ${esc(g)}</div><div class="v">1 in ${num(info.one_in)}</div><div class="k">${info.mult ? info.mult + "× book" : ""}</div></div>`);
  });

  const designs = [...(d.designs || [])].sort((a, b) =>
    RARITY_ORDER.indexOf((b.rarity || "").toLowerCase()) - RARITY_ORDER.indexOf((a.rarity || "").toLowerCase())
    || (a.name || "").localeCompare(b.name || ""));

  const body = designs.map((x) => {
    const rarity = (x.rarity || "common").toLowerCase();
    const rate = pr[rarity];
    return `<tr>
      <td class="tm">${esc(x.name)}${x.is_rookie ? ` <span class="rookie-badge" style="position:static">RC</span>` : ""}</td>
      <td class="muted">${esc(x.team || "")}</td>
      <td><span class="rarity-label" style="--r:var(--r-${rarity})">${esc(rarity)}</span></td>
      <td class="num">${rate != null ? fmtPct(rate) : "—"}</td>
      <td class="num catrow-mint">${num(x.minted)} / ${num(x.total_copies)}</td>
    </tr>`;
  }).join("");

  return `<button class="backbtn" data-back="1">← All packs</button>
    <h2 style="margin-top:0">${emoji} ${esc(set.name || "")} <span class="muted">· ${esc(set.season || "")}</span></h2>
    <div class="oddsgrid">${oddsCards.join("") || `<div class="muted">No odds published.</div>`}</div>
    <div class="collval" style="padding:12px 16px;margin-bottom:14px">
      <div><div class="lbl">Checklist</div></div>
      <div class="sub">${num(designs.length)} designs · ${num(d.total_cards || 0)}-card print run</div>
    </div>
    <div class="card" style="padding:0;overflow-x:auto">
      <table>
        <thead><tr><th>Player</th><th>Team</th><th>Rarity</th><th class="num">Pull rate</th><th class="num">Minted</th></tr></thead>
        <tbody>${body || `<tr><td colspan="5" class="muted" style="padding:18px">No designs.</td></tr>`}</tbody>
      </table>
    </div>`;
}

function fmtPct(p) {
  if (p == null) return "—";
  const n = Number(p);
  return (n < 1 ? +(n).toFixed(2) : Math.round(n * 10) / 10) + "%";
}

// ── Shell + routing ──
// Hash slugs so each view is its own URL and browser Back works:
//   #collection · #packs · #packs/<setId> (a set's catalog)
function tabbar() {
  const t = (id, label) => `<button class="tab${state.tab === id ? " on" : ""}" data-tab="${id}">${label}</button>`;
  return `<div class="tabbar">
    ${t("collection", "My Collection")}${t("packs", "Packs")}
  </div>`;
}

function draw() {
  let inner = "";
  if (state.tab === "packs") {
    inner = state.activeSet != null ? renderCatalog(state.activeSet) : renderSets();
  } else {
    inner = renderCollection();
  }
  app.innerHTML = tabbar() + `<div id="tabbody">${inner}</div>`;
}

function currentRoute() {
  const h = (location.hash || "").replace(/^#\/?/, "");
  const [tab, sub] = h.split("/");
  return { tab: tab === "packs" ? "packs" : "collection", setId: sub || null };
}

// Derive state from the URL hash and render. Wired to hashchange, so Back/Forward work.
async function render() {
  const { tab, setId } = currentRoute();
  state.tab = tab;
  state.activeSet = setId;
  if (tab === "packs") {
    await ensureSets();
    await ensureDailyStatus();
    if (setId && !state.catalog[setId]) {
      draw(); // loading state
      state.catalog[setId] = await getJSON(`/api/v1/cards/catalog?set_id=${encodeURIComponent(setId)}`);
    }
  }
  draw();
  if (tab === "packs" && !setId) ensureSetCompletion().then(draw);
}

window.addEventListener("hashchange", render);

async function ensureSets() {
  if (!state.sets) state.sets = await getJSON("/api/v1/cards/sets");
}

// Load every set's catalog (cached) so the Sets view can show per-set completion.
// Uses only existing endpoints; catalogs are shared with the Catalog tab's cache.
async function ensureSetCompletion() {
  await ensureSets();
  if (!state.me) return; // completion is per-user; nothing to show when signed out
  const sets = (state.sets && state.sets.sets) || [];
  await Promise.all(
    sets.map(async (s) => {
      if (!state.catalog[s.set_id]) {
        state.catalog[s.set_id] = await getJSON(`/api/v1/cards/catalog?set_id=${encodeURIComponent(s.set_id)}`);
      }
    })
  );
}

async function ensureDailyStatus() {
  if (!state.me || state.dailyClaimed !== null) return;
  const s = await getJSON("/api/v1/cards/daily/status");
  state.dailyClaimed = !!(s && s.claimed);
}

// Free daily pack resets at 00:00 UTC — the countdown is computable client-side.
function msToUtcMidnight() {
  const now = new Date();
  return Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1) - now.getTime();
}
function fmtDur(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  const p = (n) => String(n).padStart(2, "0");
  return `${p(Math.floor(s / 3600))}:${p(Math.floor((s % 3600) / 60))}:${p(s % 60)}`;
}
setInterval(() => {
  const el = document.getElementById("dailyCountdown");
  if (!el) return;
  const ms = msToUtcMidnight();
  if (ms <= 1000) { state.dailyClaimed = false; if (state.tab === "packs") draw(); }
  else el.textContent = fmtDur(ms);
}, 1000);

// ── Open packs (web) ──
async function postJSON(url, body) {
  if (MOCK) return mockOpen(url, body);
  const r = await fetch(url, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const j = await r.json().catch(() => ({}));
  return r.ok ? j : { error: j.error || `error ${r.status}` };
}

function toast(msg) {
  const t = document.createElement("div");
  t.className = "cardtoast";
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3200);
}

// ── Coins hub: click the nav coins chip to see every way to earn ──
const EARN_WAYS = [
  ["💬", "Daily chat reward", "500 coins for your first message in the server each day"],
  ["🎁", "Free daily pack", "Open one free card pack every day — pure upside"],
  ["🃏", "Complete a set", "One-time coin bonus for owning every card in a set"],
  ["♻️", "Quick-sell dupes", "Sell duplicate cards back for coins in Discord"],
  ["🎮", "Win casino games", "Win at /play in Discord — note: playing no longer hands out free coins"],
  ["🔄", "Trade cards", "Swap cards with other collectors to complete sets"],
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

async function doOpen(sport, season, btn) {
  if (btn) btn.disabled = true;
  window.CardSfx && CardSfx.primeAudio(); // unlock audio on the user gesture
  const res = await postJSON("/api/v1/cards/open", { sport, season, n: 1 });
  if (btn) btn.disabled = false;
  if (res.error) return toast("❌ " + res.error);
  applyBalance(res.balance);
  startReveal(res);
}

async function doDaily(btn) {
  if (btn) btn.disabled = true;
  window.CardSfx && CardSfx.primeAudio();
  const res = await postJSON("/api/v1/cards/daily", {});
  if (btn) btn.disabled = false;
  if (res.error) {
    if (res.error.includes("claimed")) { state.dailyClaimed = true; draw(); } // show the countdown
    return toast("🎁 " + res.error);
  }
  state.dailyClaimed = true;
  applyBalance(res.balance);
  startReveal(res);
}

function applyBalance(bal) {
  if (bal == null) return;
  state.balance = bal;
  renderNav(state.me && state.me.user); // refresh the coins chip
}

async function reloadAfterOpen() {
  const [mine, sets] = await Promise.all([
    getJSON("/api/v1/cards/mine"),
    getJSON("/api/v1/cards/sets"),
  ]);
  state.mine = mine;
  state.sets = sets;
  state.catalog = {}; // minted counts changed — drop cached catalogs
  draw();
  if (state.tab === "packs" && !state.activeSet) { await ensureSetCompletion(); draw(); }
}

// ── Reveal overlay (ported from nsba-markets) ──
const PACK_BURST_MS = 900;
const RARITY_CHIP = ["common", "uncommon", "rare", "epic", "legendary"];

function pullLabel(c, pr, gems) {
  const rate = pr[(c.rarity || "").toLowerCase()];
  let base = rate == null ? "—"
    : rate >= 25 ? `${(+rate).toFixed(1).replace(/\.0$/, "")}%`
    : `1 in ${Math.round(100 / rate)}`;
  const extra = [];
  if (c.gem) {
    const one = gems[String(c.gem).toLowerCase()];
    extra.push(`${String(c.gem).replace("_", " ")} 1 in ${one ? num(one.one_in) : "?"}`);
  }
  if (c.is_holo) extra.push("holo 1 in 5");
  return base + (extra.length ? " · " + extra.join(" · ") : "");
}

function startReveal(payload) {
  const cards = payload.cards || [];
  const odds = payload.odds || {};
  const pr = odds.pull_rates || {};
  const gems = odds.gems || {};
  const N = cards.length;
  if (!N) return;
  const revealed = cards.map(() => false);
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;

  const overlay = document.createElement("div");
  overlay.className = "cd-reveal";
  document.body.appendChild(overlay);
  document.body.style.overflow = "hidden";

  const muteLabel = () => (window.CardSfx && CardSfx.isMuted() ? "Sound off" : "Sound on");
  const oddsCaption = (c) => `Odds <b>${pullLabel(c, pr, gems)}</b> · EV <b>${coins(c.book_value)}</b>`;

  function flipCardHTML(c, i) {
    const rev = revealed[i];
    return `<div class="flipcard${rev ? " is-revealed" : ""}" data-flip="${i}">
      <div class="flipcard-face">
        ${cardTile(c)}
        <div class="flipcard-back"><div class="flipcard-back-brand">SharpLab</div><div class="flipcard-back-mark">🃏</div></div>
      </div>
      <div class="flip-odds">${rev ? oddsCaption(c) : ""}</div>
    </div>`;
  }

  function summaryHTML() {
    const counts = {};
    let total = 0;
    cards.forEach((c) => {
      const r = (c.rarity || "common").toLowerCase();
      counts[r] = (counts[r] || 0) + 1;
      total += c.book_value || 0;
    });
    const chips = RARITY_CHIP.filter((r) => counts[r])
      .map((r) => `<span class="cd-summary-chip cd-summary-${r}">${counts[r]}× ${r}</span>`)
      .join("");
    const bal = payload.balance != null ? `<div class="cd-balance">🪙 ${num(payload.balance)} coins left</div>` : "";
    return `<div class="cd-summary-row">${chips}</div>
      <div class="cd-summary-value">+${num(Math.round(total))} book value</div>${bal}`;
  }

  const allDone = () => revealed.every(Boolean);
  const doneCount = () => revealed.filter(Boolean).length;

  function updateChrome() {
    const hint = overlay.querySelector("#revhint");
    if (hint) hint.textContent = allDone() ? `Your haul — ${N} card${N > 1 ? "s" : ""}` : `Tap to flip · ${doneCount()}/${N}`;
    const summary = overlay.querySelector("#revsummary");
    if (summary) { summary.hidden = !allDone(); if (allDone()) summary.innerHTML = summaryHTML(); }
    const actions = overlay.querySelector("#revactions");
    if (actions) {
      actions.innerHTML = allDone()
        ? `<button class="btn primary" data-close>Continue</button>`
        : `<button class="btn" data-flipall>Reveal all</button>`;
    }
  }

  function flip(i) {
    if (revealed[i]) return;
    revealed[i] = true;
    const el = overlay.querySelector(`.flipcard[data-flip="${i}"]`);
    if (el) {
      el.classList.add("is-revealed");
      const cap = el.querySelector(".flip-odds");
      if (cap) cap.innerHTML = oddsCaption(cards[i]);
    }
    const c = cards[i];
    if (window.CardSfx) {
      CardSfx.sfxFlip();
      CardSfx.sfxReveal((c.rarity || "").toLowerCase(), !!c.is_holo, c.gem || null);
    }
    updateChrome();
  }

  function revealAll() {
    cards.forEach((_, i) => {
      if (revealed[i]) return;
      if (reduce) flip(i);
      else setTimeout(() => flip(i), i * 90);
    });
  }

  function renderCards() {
    overlay.innerHTML = `
      <button class="cd-mute">${muteLabel()}</button>
      <button class="btn cd-close" data-close>Close</button>
      <p class="cd-reveal-hint" id="revhint"></p>
      <div class="cd-reveal-grid">${cards.map(flipCardHTML).join("")}</div>
      <div class="cd-summary" id="revsummary" hidden></div>
      <div class="cd-reveal-actions" id="revactions"></div>`;
    updateChrome();
  }

  function renderPack() {
    overlay.innerHTML = `
      <button class="cd-mute">${muteLabel()}</button>
      <div class="cd-pack-wrap">
        <div class="cd-pack">
          <div class="cd-pack-foil"></div><div class="cd-pack-shine"></div>
          <div class="cd-pack-mark">🎴</div>
          <div class="cd-pack-label">SharpLab</div>
          <div class="cd-pack-sub">${N} card${N > 1 ? "s" : ""}</div>
        </div>
        <p class="cd-pack-hint">Opening…</p>
      </div>`;
  }

  function close() {
    document.removeEventListener("keydown", onKey);
    overlay.remove();
    document.body.style.overflow = "";
    reloadAfterOpen();
  }

  function onKey(e) {
    if (e.key === "Escape") { close(); return; }
    if ((e.key === " " || e.key === "ArrowRight") && overlay.querySelector(".cd-reveal-grid")) {
      e.preventDefault();
      const next = revealed.findIndex((r) => !r);
      if (next >= 0) flip(next);
    }
  }

  overlay.addEventListener("click", (e) => {
    if (e.target.closest(".cd-mute")) {
      if (window.CardSfx) CardSfx.toggleMute();
      overlay.querySelector(".cd-mute").textContent = muteLabel();
      return;
    }
    if (e.target.closest("[data-close]")) return close();
    if (e.target.closest("[data-flipall]")) return revealAll();
    const fc = e.target.closest(".flipcard");
    if (fc) flip(Number(fc.dataset.flip));
  });
  document.addEventListener("keydown", onKey);

  if (window.CardSfx) CardSfx.sfxPackOpen();
  renderPack();
  setTimeout(renderCards, reduce ? 0 : PACK_BURST_MS);
}

app.addEventListener("click", async (e) => {
  const openBtn = e.target.closest(".openbtn");
  const dailyBtn = e.target.closest(".dailybtn");
  if (openBtn) {
    e.stopPropagation();
    await doOpen(openBtn.dataset.sport, Number(openBtn.dataset.season), openBtn);
    return;
  }
  if (dailyBtn) {
    e.stopPropagation();
    await doDaily(dailyBtn);
    return;
  }
  const tabBtn = e.target.closest(".tab");
  const setCard = e.target.closest(".setcard");
  const back = e.target.closest("[data-back]");
  if (tabBtn) {
    location.hash = tabBtn.dataset.tab; // hashchange -> render()
  } else if (setCard) {
    location.hash = `packs/${setCard.dataset.setid}`;
  } else if (back) {
    location.hash = "packs"; // its own slug; browser Back also works via hashchange
  }
});

async function main() {
  const me = await getJSON("/api/v1/hq/me");
  const loggedIn = me && me.authenticated;
  state.me = loggedIn ? me : null;
  state.balance = loggedIn ? (me.balance || 0) : 0;
  renderNav(loggedIn ? me.user : null);
  // Preload the two main lists in parallel.
  const [mine, sets] = await Promise.all([getJSON("/api/v1/cards/mine"), getJSON("/api/v1/cards/sets")]);
  state.mine = mine;
  state.sets = sets;
  render(); // route from the URL hash (defaults to #collection)
}

// ─────────────────────────────────────────────────────────────
// Mock mode (?mock=1): render sample data without a backend so the
// DOM can be verified. Guarded off by default — real endpoints win.
// ─────────────────────────────────────────────────────────────
const MOCK = new URLSearchParams(location.search).get("mock") === "1";

function mockJSON(url) {
  if (url.startsWith("/api/v1/hq/me"))
    return { authenticated: true, user: { id: "1", username: "davidj", avatar: null }, balance: 5000 };
  if (url.startsWith("/api/v1/cards/mine"))
    return {
      collection_value: 1730,
      cards: [
        { instance_id: 1, name: "LeBron James", rarity: "legendary", sport: "nba", season: "2003",
          team: "CLE", is_holo: true, gem: "sapphire", serial: 1, total_copies: 1, book_value: 1200,
          is_rookie: true, headshot_url: "https://a.espncdn.com/i/headshots/nba/players/full/1966.png" },
        { instance_id: 2, name: "Dwyane Wade", rarity: "epic", sport: "nba", season: "2003",
          team: "MIA", is_holo: false, gem: null, serial: 7, total_copies: 25, book_value: 380,
          is_rookie: true, headshot_url: "" },
        { instance_id: 3, name: "Random Bench Guy", rarity: "common", sport: "mlb", season: "2011",
          team: "KC", is_holo: false, gem: null, serial: 412, total_copies: 900, book_value: 8,
          is_rookie: false, headshot_url: "https://example.invalid/404.png" },
      ],
    };
  if (url.startsWith("/api/v1/cards/daily/status"))
    return { authenticated: true, claimed: false };
  if (url.startsWith("/api/v1/cards/sets"))
    return {
      sets: [
        { set_id: "nba-2003", sport: "nba", season: "2003", name: "NBA 2003 Rookie Class",
          pack_cost: 235, packs_opened: 480, total_packs: 500, closed: false },
        { set_id: "mlb-2011", sport: "mlb", season: "2011", name: "MLB 2011",
          pack_cost: 108, packs_opened: 500, total_packs: 500, closed: true },
        { set_id: "nfl-2020", sport: "nfl", season: "2020", name: "NFL 2020",
          pack_cost: 63, packs_opened: 120, total_packs: 500, closed: false },
      ],
    };
  if (url.startsWith("/api/v1/cards/catalog"))
    return {
      set: { name: "NBA 2003 Rookie Class", sport: "nba", season: "2003" },
      total_cards: 4,
      designs: [
        { design_id: 1, name: "LeBron James", team: "CLE", rarity: "legendary", is_rookie: true, total_copies: 1, minted: 1, headshot_url: "" },
        { design_id: 2, name: "Dwyane Wade", team: "MIA", rarity: "epic", is_rookie: true, total_copies: 25, minted: 7, headshot_url: "" },
        { design_id: 3, name: "Carmelo Anthony", team: "DEN", rarity: "rare", is_rookie: true, total_copies: 80, minted: 40, headshot_url: "" },
        { design_id: 4, name: "Random Bench Guy", team: "KC", rarity: "common", is_rookie: false, total_copies: 900, minted: 412, headshot_url: "" },
      ],
      odds: {
        holo_pct: 4.0,
        pull_rates: { common: 62, uncommon: 24, rare: 10, epic: 3, legendary: 1 },
        gems: { sapphire: { one_in: 50, mult: 2.0 }, ruby: { one_in: 250, mult: 4.0 }, diamond: { one_in: 2000, mult: 12.0 } },
      },
    };
  return {};
}

function mockOpen() {
  const odds = {
    holo_pct: 20,
    pull_rates: { common: 62, uncommon: 24, rare: 10, epic: 3, legendary: 1 },
    gems: { chrome: { one_in: 50, mult: 1.5 }, sapphire: { one_in: 500, mult: 5 }, ruby: { one_in: 2000, mult: 10 }, black_lotus: { one_in: 100000, mult: 25 } },
  };
  const cards = [
    { instance_id: 101, name: "Bench Guy", rarity: "common", sport: "nba", team: "KC", is_holo: false, gem: null, serial: 512, total_copies: 900, book_value: 3.5, is_rookie: false, headshot_url: "" },
    { instance_id: 102, name: "Role Player", rarity: "uncommon", sport: "nba", team: "DEN", is_holo: false, gem: null, serial: 40, total_copies: 300, book_value: 10, is_rookie: false, headshot_url: "" },
    { instance_id: 103, name: "Solid Starter", rarity: "rare", sport: "nba", team: "MIA", is_holo: true, gem: null, serial: 12, total_copies: 80, book_value: 70, is_rookie: true, headshot_url: "" },
    { instance_id: 104, name: "All-Star", rarity: "epic", sport: "nba", team: "LAL", is_holo: false, gem: null, serial: 7, total_copies: 25, book_value: 100, is_rookie: false, headshot_url: "" },
    { instance_id: 105, name: "LeBron James", rarity: "legendary", sport: "nba", team: "CLE", is_holo: true, gem: "ruby", serial: 1, total_copies: 1, book_value: 5200, is_rookie: true, headshot_url: "https://a.espncdn.com/i/headshots/nba/players/full/1966.png" },
  ];
  return { cards, odds, balance: 4200 };
}

main();
