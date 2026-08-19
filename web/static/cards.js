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
const SELL_FRACTION = 0.75; // quick-sell returns 75% of book value (matches QUICK_SELL_FRACTION)
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
const state = {
  tab: "collection", mine: null, sets: null, catalog: {}, activeSet: null, me: null,
  balance: 0, dailyClaimed: null,
  filter: { sport: "", rarity: "", sort: "rarity", q: "" }, // collection filter/sort
  collectors: null,        // Collectors directory (list)
  viewing: null,           // user_id whose collection is open (null = my own)
  viewMine: {},            // cache of other users' collections by user_id
  market: null,            // trade board listings
  trades: null,            // { incoming, outgoing }
};

// ── Card tile ──
// opts: { sellable, listable, offerable } — which action buttons to show. `c.listed`
// (from /mine) toggles the List/Unlist button + badge. Market tiles pass ownerName/note.
function cardTile(c, opts) {
  opts = opts || {};
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
  const listed = c.listed ? `<span class="listed-badge">🔖 Listed</span>` : "";
  const owner = opts.ownerName ? `<div class="cowner">${esc(opts.ownerName)}</div>` : "";
  const note = opts.note ? `<div class="cnote">“${esc(opts.note)}”</div>` : "";
  const src = c.headshot_url || SILHOUETTE;
  return `<div class="ctile rarity-${rarity}${holo}">
    <div class="cimg">
      <img src="${esc(src)}" alt="${esc(c.name)}" loading="lazy"
           onerror="this.onerror=null;this.src=window.__cardSilh;this.classList.add('silh');">
      <span class="sport-badge" title="${esc(sport.toUpperCase())}">${emoji}</span>
      ${gem}${rookie}${listed}
    </div>
    <div class="cbody">
      <div class="cname">${esc(c.name)}</div>
      <div class="cteam">${esc(c.team || "")}</div>
      ${owner}
      <div class="cmeta">
        <span class="rarity-label">${esc(rarity)}</span>
        ${serial}
      </div>
      <div class="cvalue">${coins(c.book_value)}</div>
      ${note}
      ${tileActions(c, opts)}
    </div>
  </div>`;
}

function tileActions(c, opts) {
  if (c.instance_id == null) return "";
  const bits = [];
  if (opts.sellable) bits.push(sellButton(c));
  if (opts.listable) bits.push(c.listed
    ? `<button class="listbtn on" data-unlist="${c.instance_id}">🔖 Unlist</button>`
    : `<button class="listbtn" data-list="${c.instance_id}" data-name="${esc(c.name)}">🔖 List for trade</button>`);
  if (opts.offerable) bits.push(
    `<button class="offerbtn" data-offer="${c.instance_id}" data-name="${esc(c.name)}" data-owner="${esc(opts.ownerName || "")}">Make offer</button>`);
  return bits.length ? `<div class="tileactions">${bits.join("")}</div>` : "";
}

// Quick-sell button for an owned card (75% of book). Grails get a confirm.
function sellButton(c) {
  const price = Math.round((c.book_value || 0) * SELL_FRACTION);
  const grail = ["epic", "legendary"].includes((c.rarity || "").toLowerCase())
    || Number(c.total_copies) === 1;
  return `<button class="sellbtn" data-iid="${c.instance_id}" data-price="${price}"
    data-name="${esc(c.name)}" data-grail="${grail ? "1" : "0"}">Sell ${coins(price)}</button>`;
}

function emptyBox(icon, msg) {
  return `<div class="emptybox"><div class="big">${icon}</div>${msg}</div>`;
}

// ── Views ──
// The collection data currently on screen: someone else's when viewing, else mine.
function activeCollection() {
  return state.viewing ? state.viewMine[state.viewing] : state.mine;
}

// Filtered + sorted cards for the active collection (drives the grid only).
function filteredCollectionCards() {
  const d = activeCollection();
  const cards = (d && d.cards) || [];
  const f = state.filter;
  const byRarity = (a, b) =>
    (RARITY_ORDER.indexOf((b.rarity || "").toLowerCase()) - RARITY_ORDER.indexOf((a.rarity || "").toLowerCase()))
    || (b.book_value || 0) - (a.book_value || 0);
  const sorts = {
    rarity: byRarity,
    value: (a, b) => (b.book_value || 0) - (a.book_value || 0),
    name: (a, b) => (a.name || "").localeCompare(b.name || ""),
  };
  return cards
    .filter((c) =>
      (!f.sport || (c.sport || "").toLowerCase() === f.sport) &&
      (!f.rarity || (c.rarity || "").toLowerCase() === f.rarity) &&
      (!f.q || (c.name || "").toLowerCase().includes(f.q.toLowerCase())))
    .sort(sorts[f.sort] || byRarity);
}

// Filter/sort controls — options built from what the collection actually contains.
function collectionControls() {
  const cards = (activeCollection() && activeCollection().cards) || [];
  const sports = [...new Set(cards.map((c) => (c.sport || "").toLowerCase()).filter(Boolean))];
  const rarities = RARITY_ORDER.filter((r) => cards.some((c) => (c.rarity || "").toLowerCase() === r));
  const f = state.filter;
  const opt = (v, label, sel) => `<option value="${esc(v)}"${sel === v ? " selected" : ""}>${esc(label)}</option>`;
  return `<div class="collfilters">
    <input class="collsearch" type="search" placeholder="🔍 Search name…" value="${esc(f.q)}">
    <select class="collsport">${opt("", "All sports", f.sport)}${sports.map((s) => opt(s, `${SPORT_EMOJI[s] || ""} ${s.toUpperCase()}`, f.sport)).join("")}</select>
    <select class="collrarity">${opt("", "All rarities", f.rarity)}${rarities.map((r) => opt(r, r[0].toUpperCase() + r.slice(1), f.rarity)).join("")}</select>
    <select class="collsort">${opt("rarity", "Sort: Rarity", f.sort)}${opt("value", "Sort: Value", f.sort)}${opt("name", "Sort: Name", f.sort)}</select>
  </div>`;
}

// Just the tiles (re-rendered on filter change without touching the controls, to keep focus).
function collectionGrid() {
  const cards = filteredCollectionCards();
  const mine = !state.viewing; // only my own cards can be sold / listed
  if (!cards.length) {
    return `<div class="emptybox" style="grid-column:1/-1"><div class="big">🔍</div>No cards match your filters.</div>`;
  }
  return cards.map((c) => cardTile(c, { sellable: mine, listable: mine })).join("");
}

function renderCollection() {
  const d = activeCollection();
  if (!state.viewing && d && d._status === 401) {
    return emptyBox("🔒", "<strong>Sign in to see your cards.</strong><br>Then open a pack in Discord with <code>/pack open</code>.");
  }
  const cards = (d && d.cards) || [];
  const owner = state.viewing ? (d && d.owner) : null;
  const back = state.viewing
    ? `<button class="backbtn" data-back-collectors>← Collectors</button>` : "";
  if (!cards.length) {
    if (state.viewing) {
      return back + emptyBox("🃏", `<strong>${esc((owner && owner.username) || "This collector")} has no cards yet.</strong>`);
    }
    return emptyBox("🃏", "<strong>No cards yet.</strong><br>Open a pack in Discord with <code>/pack open &lt;sport&gt; &lt;season&gt;</code>.");
  }
  const total = d.collection_value != null
    ? d.collection_value
    : cards.reduce((s, c) => s + (c.book_value || 0), 0);
  const title = state.viewing
    ? `${esc((owner && owner.username) || "Collector")}'s collection` : "Collection book value";
  const hero = `<div class="collval">
    <div><div class="lbl">${title}</div><div class="bigval">${coins(total)}</div></div>
    <div class="sub">${cards.length} card${cards.length === 1 ? "" : "s"}</div>
  </div>`;
  return back + hero + collectionControls() + `<div class="cardgrid" id="collgrid">${collectionGrid()}</div>`;
}

// ── Collectors directory ──
function renderCollectors() {
  const list = (state.collectors && state.collectors.collectors) || [];
  if (!list.length) return emptyBox("👥", "<strong>No collectors yet.</strong>");
  const rows = list.map((c, i) => `
    <div class="collectorrow" data-uid="${esc(c.user_id)}">
      <span class="crank">${i + 1}</span>
      ${c.avatar_url ? `<img class="cavatar" src="${esc(c.avatar_url)}" alt="" loading="lazy">` : `<span class="cavatar noimg">👤</span>`}
      <span class="cuser">${esc(c.username)}</span>
      <span class="ccount">${c.cards} card${c.cards === 1 ? "" : "s"}</span>
      <span class="cval">${coins(c.value)}</span>
    </div>`).join("");
  return `<div class="collectorlist">${rows}</div>`;
}

// ── Trade market ──
function coinDelta(n) {
  n = Number(n) || 0;
  if (!n) return "";
  return n > 0 ? `<span class="coindelta plus">+🪙${num(n)}</span>`
               : `<span class="coindelta minus">🪙${num(-n)} back</span>`;
}

// One offered/wanted card as a compact chip (name + rarity dot).
function tradeChip(c) {
  const r = (c.rarity || "common").toLowerCase();
  return `<span class="tchip rarity-${r}"><span class="tdot"></span>${esc(c.name)}</span>`;
}

function offerRow(t, kind) {
  // kind: "incoming" (I decide) or "outgoing" (I sent it)
  const give = kind === "incoming" ? t.want_cards : t.offer_cards; // what *I* give up
  const get = kind === "incoming" ? t.offer_cards : t.want_cards;  // what *I* receive
  // Coins are signed from the sender's side. For me:
  //   incoming: sender pays me when coins>0  -> I receive +coins
  //   outgoing: I'm the sender               -> coins>0 means I pay
  const myCoins = kind === "incoming" ? t.coins : -t.coins;
  const giveHtml = (give.length ? give.map(tradeChip).join("") : `<span class="tnone">—</span>`)
    + (myCoins < 0 ? ` ${coinDelta(myCoins)}` : "");
  const getHtml = (get.length ? get.map(tradeChip).join("") : `<span class="tnone">—</span>`)
    + (myCoins > 0 ? ` ${coinDelta(myCoins)}` : "");
  const actions = kind === "incoming"
    ? `<button class="btn primary tradeaccept" data-tid="${t.trade_id}">Accept</button>
       <button class="btn tradedecline" data-tid="${t.trade_id}">Decline</button>`
    : `<button class="btn tradecancel" data-tid="${t.trade_id}">Cancel</button>`;
  return `<div class="offerrow">
    <div class="offerlegs">
      <div class="offerleg"><div class="leglbl">You give</div><div class="legcards">${giveHtml}</div></div>
      <div class="offerarrow">⇄</div>
      <div class="offerleg"><div class="leglbl">You get</div><div class="legcards">${getHtml}</div></div>
    </div>
    <div class="offeractions">${actions}</div>
  </div>`;
}

function renderMarket() {
  if (state.me === null) {
    return emptyBox("🔒", "<strong>Sign in to trade.</strong><br>Then list cards from your collection and make offers here.");
  }
  const t = state.trades || { incoming: [], outgoing: [] };
  let offers = "";
  if (t.incoming.length || t.outgoing.length) {
    const inc = t.incoming.length
      ? `<div class="offergroup"><h3>Offers to you <span class="badge">${t.incoming.length}</span></h3>${t.incoming.map((x) => offerRow(x, "incoming")).join("")}</div>` : "";
    const out = t.outgoing.length
      ? `<div class="offergroup"><h3>Your sent offers</h3>${t.outgoing.map((x) => offerRow(x, "outgoing")).join("")}</div>` : "";
    offers = `<div class="offers">${inc}${out}</div>`;
  }
  const listings = (state.market && state.market.listings) || [];
  let board;
  if (!listings.length) {
    board = emptyBox("🏪", "<strong>No cards listed yet.</strong><br>List one from your collection to open it up for offers.");
  } else {
    const tiles = listings.map((c) => cardTile(c, {
      offerable: c.owner_id !== (state.me.user && state.me.user.id),
      ownerName: c.owner_name, note: c.note,
    })).join("");
    board = `<h3 class="markethdr">On the block</h3><div class="cardgrid">${tiles}</div>`;
  }
  return offers + board;
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
  const nInc = state.trades && state.trades.incoming ? state.trades.incoming.length : 0;
  const marketLabel = nInc ? `Market <span class="tabbadge">${nInc}</span>` : "Market";
  return `<div class="tabbar">
    ${t("collection", "My Collection")}${t("packs", "Packs")}${t("market", marketLabel)}${t("collectors", "Collectors")}
  </div>`;
}

function draw() {
  let inner = "";
  if (state.tab === "packs") {
    inner = state.activeSet != null ? renderCatalog(state.activeSet) : renderSets();
  } else if (state.tab === "market") {
    inner = renderMarket();
  } else if (state.tab === "collectors") {
    inner = state.viewing != null ? renderCollection() : renderCollectors();
  } else {
    inner = renderCollection();
  }
  app.innerHTML = tabbar() + `<div id="tabbody">${inner}</div>`;
}

function currentRoute() {
  const h = (location.hash || "").replace(/^#\/?/, "");
  const [tab, sub] = h.split("/");
  if (tab === "packs") return { tab: "packs", setId: sub || null, userId: null };
  if (tab === "market") return { tab: "market", setId: null, userId: null };
  if (tab === "collectors") return { tab: "collectors", setId: null, userId: sub || null };
  return { tab: "collection", setId: null, userId: null };
}

// Load the market board + my trade offers (used by the Market tab).
async function ensureMarket(force) {
  if (force || !state.market) state.market = await getJSON("/api/v1/cards/market");
  if (force || !state.trades) {
    const t = await getJSON("/api/v1/cards/trades");
    state.trades = t && t.incoming ? t : { incoming: [], outgoing: [] };
  }
}

// Derive state from the URL hash and render. Wired to hashchange, so Back/Forward work.
async function render() {
  const { tab, setId, userId } = currentRoute();
  state.tab = tab;
  state.activeSet = setId;
  state.viewing = tab === "collectors" ? userId : null;
  if (tab === "packs") {
    await ensureSets();
    await ensureDailyStatus();
    if (setId && !state.catalog[setId]) {
      draw(); // loading state
      state.catalog[setId] = await getJSON(`/api/v1/cards/catalog?set_id=${encodeURIComponent(setId)}`);
    }
  } else if (tab === "market") {
    draw(); // loading state
    await ensureMarket();
  } else if (tab === "collectors") {
    state.filter = { sport: "", rarity: "", sort: "rarity", q: "" }; // fresh filters per view
    if (userId) {
      draw(); // loading state
      if (!state.viewMine[userId]) {
        state.viewMine[userId] = await getJSON(`/api/v1/cards/collection/${encodeURIComponent(userId)}`);
      }
    } else if (!state.collectors) {
      draw();
      state.collectors = await getJSON("/api/v1/cards/collectors");
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

async function doSell(btn) {
  const iid = Number(btn.dataset.iid);
  const price = Number(btn.dataset.price);
  if (btn.dataset.grail === "1" &&
      !confirm(`Sell ${btn.dataset.name} for 🪙${num(price)}? This can't be undone.`)) return;
  btn.disabled = true;
  const res = await postJSON("/api/v1/cards/sell", { instance_id: iid });
  if (res.error) { btn.disabled = false; return toast("❌ " + res.error); }
  applyBalance(res.balance);
  // Drop the sold card locally and let renderCollection recompute the total from what's left.
  if (state.mine && state.mine.cards) {
    state.mine.cards = state.mine.cards.filter((c) => c.instance_id !== iid);
    state.mine.collection_value = null;
  }
  toast(`♻️ Sold ${(res.card && res.card.name) || "card"} · +🪙${num(res.coins)}`);
  draw();
}

// ── Trade actions ──
// Reload /mine (listed flags), market, and trades, then redraw whatever tab is open.
async function reloadTrading() {
  const [mine] = await Promise.all([getJSON("/api/v1/cards/mine"), ensureMarket(true)]);
  state.mine = mine;
  draw();
}

async function doList(iid, name) {
  const note = prompt(`List "${name}" for trade. Add a note (what you're after)? Optional:`, "");
  if (note === null) return; // cancelled
  const res = await postJSON("/api/v1/cards/list", { instance_id: iid, note: note || null });
  if (res.error) return toast("❌ " + res.error);
  toast(`🔖 Listed ${name} for trade`);
  await reloadTrading();
}

async function doUnlist(iid) {
  const res = await postJSON("/api/v1/cards/unlist", { instance_id: iid });
  if (res.error) return toast("❌ " + res.error);
  toast("Removed from the market");
  await reloadTrading();
}

async function doTradeAction(tid, action) {
  const res = await postJSON(`/api/v1/cards/trades/${tid}/${action}`, {});
  if (res.error) return toast("❌ " + res.error);
  if (action === "accept") { applyBalance(res.balance); toast("✅ Trade complete!"); }
  else toast(action === "decline" ? "Declined" : "Cancelled");
  await reloadTrading();
}

// Make-offer modal: choose which of my cards to give + a coin sweetener, for a listed card.
function openOfferModal(wantId, wantName, ownerName) {
  if (document.querySelector(".offerov")) return;
  const mine = (state.mine && state.mine.cards) || [];
  const picks = new Set();
  const ov = document.createElement("div");
  ov.className = "hubov offerov";
  const myTiles = mine.length
    ? mine.map((c) => `<label class="pickcard" data-iid="${c.instance_id}">
        <input type="checkbox" value="${c.instance_id}">
        <span class="pickname">${esc(c.name)}</span>
        <span class="pickrar rarity-label" style="--r:var(--r-${(c.rarity || "common").toLowerCase()})">${esc((c.rarity || "").toLowerCase())}</span>
      </label>`).join("")
    : `<div class="tnone">You have no cards to give — offer coins instead.</div>`;
  ov.innerHTML = `<div class="hubcard offercard">
    <div class="hubhead"><h3>Offer on ${esc(wantName)}</h3><button class="hubx" aria-label="Close">✕</button></div>
    <div class="offerhint">Trading with <b>${esc(ownerName || "collector")}</b>. Pick your card(s) to give and/or add coins.</div>
    <div class="pickgrid">${myTiles}</div>
    <label class="coinrow">Coins to add (or negative to request):
      <input type="number" class="offercoins" value="0" step="10"></label>
    <div class="offerfoot">
      <span class="offersummary"></span>
      <button class="btn primary offersend" data-want="${wantId}">Send offer</button>
    </div>
  </div>`;
  const summ = ov.querySelector(".offersummary");
  const coinsIn = ov.querySelector(".offercoins");
  const update = () => {
    const n = Number(coinsIn.value) || 0;
    summ.innerHTML = `${picks.size} card${picks.size === 1 ? "" : "s"} ${n ? coinDelta(n) : ""}`;
  };
  ov.addEventListener("change", (e) => {
    if (e.target.matches(".pickcard input")) {
      const id = Number(e.target.value);
      e.target.checked ? picks.add(id) : picks.delete(id);
      e.target.closest(".pickcard").classList.toggle("on", e.target.checked);
    }
    update();
  });
  ov.addEventListener("input", (e) => { if (e.target.classList.contains("offercoins")) update(); });
  ov.addEventListener("click", async (e) => {
    if (e.target === ov || e.target.closest(".hubx")) return ov.remove();
    const send = e.target.closest(".offersend");
    if (!send) return;
    const coinsVal = Number(coinsIn.value) || 0;
    if (!picks.size && coinsVal <= 0) return toast("Offer at least a card or some coins");
    send.disabled = true;
    const res = await postJSON("/api/v1/cards/trade", {
      want_ids: [wantId], offer_ids: [...picks], coins: coinsVal,
    });
    if (res.error) { send.disabled = false; return toast("❌ " + res.error); }
    ov.remove();
    toast("📨 Offer sent!");
    await reloadTrading();
  });
  update();
  document.body.appendChild(ov);
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
  const sellBtn = e.target.closest(".sellbtn");
  if (sellBtn) {
    e.stopPropagation();
    await doSell(sellBtn);
    return;
  }
  const listBtn = e.target.closest("[data-list]");
  if (listBtn) { e.stopPropagation(); await doList(Number(listBtn.dataset.list), listBtn.dataset.name); return; }
  const unlistBtn = e.target.closest("[data-unlist]");
  if (unlistBtn) { e.stopPropagation(); await doUnlist(Number(unlistBtn.dataset.unlist)); return; }
  const offerBtn = e.target.closest("[data-offer]");
  if (offerBtn) { e.stopPropagation(); openOfferModal(Number(offerBtn.dataset.offer), offerBtn.dataset.name, offerBtn.dataset.owner); return; }
  const accept = e.target.closest(".tradeaccept");
  if (accept) { e.stopPropagation(); await doTradeAction(Number(accept.dataset.tid), "accept"); return; }
  const decline = e.target.closest(".tradedecline");
  if (decline) { e.stopPropagation(); await doTradeAction(Number(decline.dataset.tid), "decline"); return; }
  const cancel = e.target.closest(".tradecancel");
  if (cancel) { e.stopPropagation(); await doTradeAction(Number(cancel.dataset.tid), "cancel"); return; }
  const collectorRow = e.target.closest(".collectorrow");
  if (collectorRow) {
    location.hash = `collectors/${collectorRow.dataset.uid}`;
    return;
  }
  const tabBtn = e.target.closest(".tab");
  const setCard = e.target.closest(".setcard");
  const back = e.target.closest("[data-back]");
  if (e.target.closest("[data-back-collectors]")) {
    location.hash = "collectors"; // back to the collectors directory
  } else if (tabBtn) {
    location.hash = tabBtn.dataset.tab; // hashchange -> render()
  } else if (setCard) {
    location.hash = `packs/${setCard.dataset.setid}`;
  } else if (back) {
    location.hash = "packs"; // its own slug; browser Back also works via hashchange
  }
});

// Filter/sort controls live inside the collection view — update state and re-render
// only the grid (keeps the search box focused while typing).
function refreshGrid() {
  const g = document.getElementById("collgrid");
  if (g) g.innerHTML = collectionGrid();
}
app.addEventListener("input", (e) => {
  if (e.target.classList.contains("collsearch")) { state.filter.q = e.target.value; refreshGrid(); }
});
app.addEventListener("change", (e) => {
  const t = e.target;
  if (t.classList.contains("collsport")) state.filter.sport = t.value;
  else if (t.classList.contains("collrarity")) state.filter.rarity = t.value;
  else if (t.classList.contains("collsort")) state.filter.sort = t.value;
  else return;
  refreshGrid();
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
  if (url.startsWith("/api/v1/cards/collectors"))
    return {
      collectors: [
        { user_id: "1", username: "davidj", avatar_url: null, cards: 3, value: 1730 },
        { user_id: "2", username: "steph", avatar_url: null, cards: 5, value: 940 },
        { user_id: "3", username: "Player a1b2c3", avatar_url: null, cards: 1, value: 35 },
      ],
    };
  if (url.startsWith("/api/v1/cards/collection/"))
    return {
      owner: { user_id: "2", username: "steph", avatar_url: null },
      collection_value: 940,
      cards: [
        { instance_id: 21, name: "Stephen Curry", rarity: "epic", sport: "nba", season: "2010",
          team: "GSW", is_holo: false, gem: null, serial: 3, total_copies: 25, book_value: 420,
          is_rookie: true, headshot_url: "" },
        { instance_id: 22, name: "Mike Trout", rarity: "rare", sport: "mlb", season: "2012",
          team: "LAA", is_holo: false, gem: null, serial: 15, total_copies: 80, book_value: 60,
          is_rookie: true, headshot_url: "" },
      ],
    };
  if (url.startsWith("/api/v1/cards/market"))
    return {
      listings: [
        { instance_id: 21, owner_id: "2", owner_name: "steph", owner_avatar: null,
          name: "Stephen Curry", team: "GSW", rarity: "epic", sport: "nba", season: "2010",
          serial: 3, total_copies: 25, is_holo: false, gem: null, book_value: 420,
          note: "looking for a LeBron rookie" },
        { instance_id: 22, owner_id: "2", owner_name: "steph", owner_avatar: null,
          name: "Mike Trout", team: "LAA", rarity: "rare", sport: "mlb", season: "2012",
          serial: 15, total_copies: 80, is_holo: false, gem: null, book_value: 60, note: null },
      ],
    };
  if (url.startsWith("/api/v1/cards/trades"))
    return {
      incoming: [
        { trade_id: 5, from_user: "3", to_user: "1", coins: 150,
          offer_ids: [301], want_ids: [1],
          offer_cards: [{ instance_id: 301, name: "Luka Dončić", rarity: "epic" }],
          want_cards: [{ instance_id: 1, name: "LeBron James", rarity: "legendary" }] },
      ],
      outgoing: [
        { trade_id: 6, from_user: "1", to_user: "2", coins: -50,
          offer_ids: [2], want_ids: [21],
          offer_cards: [{ instance_id: 2, name: "Dwyane Wade", rarity: "epic" }],
          want_cards: [{ instance_id: 21, name: "Stephen Curry", rarity: "epic" }] },
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

function mockOpen(url, body) {
  if (url && url.includes("/sell"))
    return { card: { name: "Sold Card", rarity: "rare", serial: 1 }, coins: 26, balance: 5026 };
  if (url && (url.endsWith("/list") || url.endsWith("/unlist"))) return { ok: true };
  if (url && url.endsWith("/cards/trade")) return { trade_id: 99 };
  if (url && url.includes("/trades/")) return { ok: true, balance: 5150 };
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
