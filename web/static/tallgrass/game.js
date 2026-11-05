// Tall Grass: title, army builder, and war playback. Rules live in sim.js, drawing in stage.js.
import { ABILITIES, createWorld, defaultLevel, footprint, outcome, step, useData, W, wildMoves, xpAt, zoneOf } from "./sim.js";
import { BIOMES, Stage } from "./stage.js";
import { PRESETS, WILDS, resolveSquads, wildHorde } from "./presets.js";
import { Sfx } from "./sfx.js";

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const spriteUrl = (n) => `/pokehoops/sprites/${n}.png`;
const params = new URLSearchParams(location.search);
const BASE_TURN = 0.82;

const G = {
  mode: "title", squads: [], biome: "meadow", world: null, seed: 1, speed: 1, nextTurnAt: 0, endAt: 0, demoIndex: 0,
  history: [], dragging: null, nextSquadId: 1000, lastPreset: null, stats: null,
};

let data, stage;
const sfx = new Sfx();

function savedQuality() {
  try { return localStorage.getItem("tg-quality") || "auto"; } catch { return "auto"; }
}
function quality() {
  const q = params.get("q") || savedQuality();
  if (q !== "auto") return q;
  if (Math.min(innerWidth, innerHeight) < 600 || /Mobi|Android/i.test(navigator.userAgent)) return "low";
  // Named discrete GPUs start high; anything else starts medium. Either way it steps down if frames run slow.
  const gl = document.createElement("canvas").getContext("webgl2"), info = gl?.getExtension("WEBGL_debug_renderer_info");
  const gpu = info ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL) : "";
  return /RTX|GTX|Radeon RX|Radeon Pro|Apple M\d|Arc A/i.test(gpu) ? "high" : "medium";
}

function cssColor(name) {
  const probe = document.createElement("canvas").getContext("2d");
  probe.fillStyle = getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#fff";
  const hex = probe.fillStyle;
  return hex.startsWith("#") ? [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255) : [1, 1, 1];
}

async function boot() {
  try {
    data = await (await fetch("./data.json")).json();
  } catch {
    $("loading").textContent = "Couldn't load the Pokédex. Refresh to try again.";
    return;
  }
  useData(data);
  const test = document.createElement("canvas").getContext("webgl2");
  if (!test) { $("loading").textContent = "Tall Grass needs WebGL2, which this browser doesn't have."; return; }
  stage = new Stage($("gl"), $("overlay"), data, { quality: quality(), maxPixelRatio: +(params.get("dpr") ?? 1.5) });
  if (params.get("lockres")) stage.lockRes = true;
  stage.sfx = (n) => { if (n === "rain") sfx.setWeather("rain"); else sfx.play(n); };
  stage.onEvent = moment;
  stage.onContextLost = () => {
    // The driver reset (usually an overloaded GPU). Come back one tier lower instead of a dead black canvas.
    const next = { high: "medium", medium: "low", low: "low" }[stage.quality];
    try { localStorage.setItem("tg-quality", next); } catch { /* private window */ }
    $("loading").textContent = "The graphics driver reset. Restarting at lower quality…";
    document.body.classList.add("is-loading");
    setTimeout(() => location.reload(), 900);
  };
  stage.onCamMode = (m) => { $("btnCam").textContent = m === "director" ? "Director cam" : m === "follow" ? "Following" : "Free cam"; $("btnCam").classList.toggle("is-on", m !== "free"); };
  stage.uSideA.value.setRGB(...cssColor("--info"));
  stage.uSideB.value.setRGB(...cssColor("--bad"));
  const rootStyle = getComputedStyle(document.documentElement);
  stage.css = { ok: rootStyle.getPropertyValue("--ok").trim(), warn: rootStyle.getPropertyValue("--warn").trim(), bad: rootStyle.getPropertyValue("--bad").trim() };
  buildTitle();
  buildRoomChrome();
  wire();
  const war = params.get("war");
  const preset = PRESETS.find((p) => p.id === war);
  if (preset) await startPreset(preset, +(params.get("seed") ?? 1));
  else await showTitle();
  document.body.classList.remove("is-loading");
  let last = performance.now();
  const loop = (now) => {
    requestAnimationFrame(loop);
    // A 120 Hz monitor shouldn't make the GPU work twice as hard for a war that reads fine at 60.
    if (now - last < 13) return;
    const dt = (now - last) / 1000;
    last = now;
    tick();
    stage.frame(dt);
    if (now - (G.perfAt ?? 0) > 1000) {
      G.perfAt = now;
      if (stage.fps) $("fps").textContent = `${Math.round(stage.fps)} fps`;
    }
  };
  requestAnimationFrame(loop);
  window.__tg = { G, stage, tick };
}

function show(mode) {
  G.mode = mode;
  document.body.className = `mode-${mode}`;
  $("title").hidden = mode !== "title";
  $("room").hidden = mode !== "room";
  $("hud").hidden = mode !== "war";
  $("end").hidden = mode !== "end";
  if (mode !== "room" && mode !== "war") closeInspect();
}

// ---------- title ----------
function buildTitle() {
  const box = $("cards");
  for (const p of PRESETS) {
    const c = el("button", "card");
    c.type = "button";
    const row = el("div", "sprites");
    const squads = resolveSquads(p, data);
    const tallest = Math.max(...squads.map((s) => data.species[s.num].heightm));
    const shown = [0, 1].flatMap((sd) => squads.filter((s) => s.side === sd).slice(0, 2));
    let side = 0;
    shown.forEach((s) => {
      if (s.side !== side) { row.append(el("span", "vs", "vs")); side = s.side; }
      const img = el("img");
      img.src = spriteUrl(s.num); img.alt = data.species[s.num].name; img.loading = "lazy";
      // relative sizes on the card match real heights, softened so tiny Pokémon still read
      const px = Math.round(26 + 44 * Math.sqrt(data.species[s.num].heightm / Math.max(tallest, 2)));
      img.width = img.height = px;
      row.append(img);
    });
    c.append(row, el("b", null, p.title), el("span", "blurb", p.blurb));
    c.addEventListener("click", () => { sfx.unlock(); startPreset(p, Math.floor(Math.random() * 1e6)); });
    box.append(c);
  }
}

const DEMOS = ["rats", "chain", "storm", "goliath", "hive", "mines", "legends"];
async function showTitle() {
  show("title");
  await runDemo();
}
async function runDemo() {
  const demoId = DEMOS[G.demoIndex++ % DEMOS.length];
  const p = PRESETS.find((x) => x.id === demoId);
  G.squads = resolveSquads(p, data);
  G.biome = p.biome;
  await launch(Math.floor(Math.random() * 1e6), { demo: true });
}

// ---------- launching a war ----------
function resetBiome(id) {
  if (stage.biomeId !== id) stage.setBiome(id);
  stage.clearScars();
  stage.setWeather(null);
  stage.flash = 0;
}

async function launch(seed, { demo = false } = {}) {
  G.seed = seed;
  const squads = G.squads.map((s) => ({ ...s }));
  resetBiome(G.biome);
  const w = createWorld(squads, seed);
  G.world = w;
  await stage.setWorld(w);
  G.history = [hpShare(w)];
  G.moments = {};
  G.tickerLast = {};
  G.names = sideNames(squads);
  G.nextTurnAt = stage.clock + 1.1;
  G.endAt = 0;
  G.demo = demo;
  stage.paused = false;
  stage.timeScale = 1;
  stage.controls.autoRotate = false;
  frameOpening(w);
  if (!demo) {
    show("war");
    $("ticker").replaceChildren();
    $("nameA").textContent = G.names[0];
    $("nameB").textContent = G.names[1];
    setSpeed(G.speed);
    updateHud();
    setPaused(false);
    sfx.setWeather(null);
    banner(`${G.names[0]} vs. ${G.names[1]}`, 2000);
  }
}

function sideNames(squads) {
  return [0, 1].map((side) => {
    const mine = squads.filter((s) => s.side === side);
    if (!mine.length) return side ? "Red" : "Blue";
    const lead = [...mine].sort((a, b) => b.count * (1 + data.species[b.num].bst / 100) - a.count * (1 + data.species[a.num].bst / 100))[0];
    const total = mine.reduce((t, s) => t + s.count, 0);
    const name = data.species[lead.num].name;
    return mine.length > 1 ? `${name} & co.` : total > 1 ? `${total} ${name}` : name;
  });
}

function frameOpening(w) {
  const front = w.units.filter((u) => u.side === 0);
  const mid = front.reduce((t, u) => t + u.x, 0) / (front.length || 1);
  const zx = (mid - W / 2) * 0.5;
  stage.setCamMode("director");
  stage.actionWeight = 0;
  // start low behind the front rank, looking across at the enemy, then crane out once the march starts
  stage.camera.position.set(zx - 14, 1.5, 15);
  stage.controls.target.set(zx + 14, 1.6, -3);
  stage.cut({ px: zx - 5, py: 2.6, pz: 15, tx: zx + 16, ty: 1.4, tz: -3, until: 3.2, band: 0.2, blur: 0.0075 });
  updateFraming();
}

function updateFraming() {
  let x0 = 1e9, x1 = -1e9, z0 = 1e9, z1 = -1e9, n = 0, sx = 0, sz = 0;
  for (const u of G.world.units) {
    if (!u.alive) continue;
    x0 = Math.min(x0, u.x); x1 = Math.max(x1, u.x + u.size); z0 = Math.min(z0, u.y); z1 = Math.max(z1, u.y + u.size);
    sx += u.x; sz += u.y; n++;
  }
  if (!n) return;
  const wide = (x1 - x0) * 0.5, deep = (z1 - z0) * 0.5;
  const aspect = innerWidth / innerHeight;
  stage.directorDist = Math.max(17, Math.min(70, Math.max((wide * 1.08 * (16 / 9)) / aspect, deep * 1.9) + 5));
  stage.armyX = ((x0 + x1) / 2 - W / 2) * 0.5;
  stage.armyZ = ((z0 + z1) / 2 - 36) * 0.5;
}

function hpShare(w) {
  let a = 0, b = 0;
  for (const u of w.units) if (u.alive) { if (u.side === 0) a += u.hp; else b += u.hp; }
  return [a / (w.peak[0] || 1), b / (w.peak[1] || 1)];
}

function tick() {
  if (!G.world || (G.mode !== "war" && G.mode !== "title")) return;
  const w = G.world;
  if (stage.paused) return;
  if (!w.over && stage.clock >= G.nextTurnAt) {
    stage.syncHp(w);
    const ev = [];
    step(w, ev);
    const dur = BASE_TURN / (G.demo ? 1 : G.speed);
    stage.playTurn(ev, dur);
    G.nextTurnAt = stage.clock + dur;
    updateFraming();
    if (!G.demo) { G.history.push(hpShare(w)); updateHud(); }
    if (w.over) G.endAt = stage.clock + dur + 1.5;
  }
  if (w.over && G.endAt && stage.clock >= G.endAt) {
    G.endAt = 0;
    if (G.mode === "title") { G.demoTimer = setTimeout(() => { if (G.mode === "title") runDemo(); }, 900); }
    else finishWar();
  }
}

// ---------- HUD ----------
function updateHud() {
  const w = G.world;
  const [a, b] = hpShare(w);
  let ca = 0, cb = 0;
  for (const u of w.units) if (u.alive) { if (u.side === 0) ca++; else cb++; }
  $("countA").textContent = ca.toLocaleString();
  $("countB").textContent = cb.toLocaleString();
  $("barA").style.width = `${a * 100}%`;
  $("barB").style.width = `${b * 100}%`;
  $("turn").textContent = `Turn ${w.turn}`;
  $("weatherChip").textContent = w.weather ? { rain: "Rain", sun: "Harsh sun", sand: "Sandstorm" }[w.weather.kind] : " ";
  drawChart();
}

function drawChart() {
  const c = $("chart"), x = c.getContext("2d"), h = G.history;
  x.clearRect(0, 0, c.width, c.height);
  if (h.length < 2) return;
  const styles = getComputedStyle(document.documentElement);
  const line = (i, color) => {
    x.strokeStyle = color; x.lineWidth = 3; x.beginPath();
    h.forEach((p, k) => {
      const px = (k / (h.length - 1)) * c.width, py = c.height - 3 - p[i] * (c.height - 6);
      if (k) x.lineTo(px, py); else x.moveTo(px, py);
    });
    x.stroke();
  };
  line(0, styles.getPropertyValue("--info").trim());
  line(1, styles.getPropertyValue("--bad").trim());
}

const name = (id) => {
  const u = G.world.units[id];
  return u ? data.species[u.tf?.num ?? u.num].name : "?";
};

function tickerAdd(text, cls) {
  const box = $("ticker");
  const same = [...box.children].find((x) => x.dataset.text === text);
  if (same) {
    same.dataset.n = +same.dataset.n + 1;
    same.textContent = `${text} ×${same.dataset.n}`;
    clearTimeout(same.timer);
    same.timer = setTimeout(() => same.remove(), 6500);
    box.append(same);
    return;
  }
  const li = el("li", cls, text);
  li.dataset.text = text; li.dataset.n = 1;
  box.append(li);
  while (box.children.length > 5) box.firstChild.remove();
  li.timer = setTimeout(() => li.remove(), 6500);
}

let bannerTimer;
function banner(text, ms = 1900) {
  const b = $("banner");
  b.textContent = text;
  b.classList.add("on");
  clearTimeout(bannerTimer);
  bannerTimer = setTimeout(() => b.classList.remove("on"), ms);
}

const LOUD = new Set(["earthquake", "surf", "explosion", "selfdestruct", "hyperbeam", "thunder", "blizzard", "fissure", "guillotine", "horndrill", "solarbeam", "rockslide", "fireblast", "hydropump", "sandstorm", "raindance", "sunnyday", "recover", "hypnosis", "spore", "sleeppowder", "sing", "thunderwave", "reflect", "lightscreen"]);

function moment(e) {
  if (G.demo || G.mode !== "war") return;
  const cls = (id) => (G.world.units[id]?.side === 0 ? "a" : "b");
  switch (e.k) {
    case "use": {
      if (!LOUD.has(e.m)) break;
      const key = `${e.u}:${e.m}`, now = performance.now();
      const moveKey = `m:${e.m}`;
      if (now - (G.tickerLast[moveKey] ?? 0) < 1400 || now - (G.tickerLast[key] ?? 0) < 4000) break;
      G.tickerLast[moveKey] = G.tickerLast[key] = now;
      tickerAdd(`${name(e.u)} used ${data.moves[e.m]?.name ?? e.m}`, cls(e.u));
      break;
    }
    case "hit":
      G.lastHit = e;
      break;
    case "faint": {
      const u = G.world.units[e.o];
      if (u && (data.species[u.num].heightm >= 1.6 || G.world.units.filter((x) => x.side === u.side).length <= 12)) tickerAdd(`${name(e.o)} fainted`, cls(e.o));
      break;
    }
    case "evolve": {
      const to = data.species[e.to].name, from = data.species[e.from].name;
      G.moments.evolutions = (G.moments.evolutions ?? 0) + 1;
      if (G.moments.evolutions <= 2) banner(`${from} is evolving into ${to}!`, 2600);
      else if (G.moments.evolutions % 8 === 0) banner(`${G.moments.evolutions} evolutions and counting`, 1400);
      tickerAdd(`${from} evolved into ${to}`, "g");
      if (stage.selected?.id === e.u) renderInspect(stage.selected);
      break;
    }
    case "weather":
      sfx.setWeather(e.kind);
      banner(e.kind ? { rain: "Rain began to fall", sun: "The sunlight turned harsh", sand: "A sandstorm kicked up" }[e.kind] : "The weather cleared");
      break;
    case "transform":
      G.moments.tf = (G.moments.tf ?? 0) + 1;
      if (G.moments.tf === 1) banner(`Ditto transformed into ${data.species[e.to].name}`);
      tickerAdd(`Ditto transformed into ${data.species[e.to].name}`, cls(e.u));
      break;
    case "ohko":
      tickerAdd(`${name(e.o)} was taken out in one hit`, "g");
      break;
    case "roar":
      if (!G.tickerLast.roar) { G.tickerLast.roar = 1; tickerAdd(`${name(e.u)}'s Intimidate cut nearby Attack`, cls(e.u)); }
      break;
    case "immune":
      if (!G.tickerLast.immune) { G.tickerLast.immune = 1; tickerAdd(`${name(e.o)} is immune!`, cls(e.o)); }
      break;
    default: break;
  }
}

function setSpeed(n) {
  G.speed = n;
  for (const b of $("speeds").children) b.classList.toggle("is-on", +b.dataset.speed === n);
}
function setPaused(p) {
  stage.paused = p;
  $("btnPause").textContent = p ? "Resume" : "Pause";
  $("btnPause").classList.toggle("is-on", p);
}

function finishWar() {
  const w = G.world, o = outcome(w);
  const mvp = (side) => [...w.units].filter((u) => u.side === side).sort((a, b) => b.dealt - a.dealt)[0];
  const stat = (label, body) => { const d = el("div"); d.append(el("span", null, label)); if (typeof body === "string") d.append(body); else d.append(body); return d; };
  const mvpBox = (u) => {
    const d = el("div", "mvp");
    if (!u) return d;
    const num = u.tf?.num ?? u.num, img = el("img");
    img.src = spriteUrl(num); img.alt = "";
    const t = el("div");
    t.append(el("b", null, `${data.species[num].name}, L${u.level}`), el("br"), `${u.dealt.toLocaleString()} damage, ${u.kills} KO`);
    d.append(img, t);
    return d;
  };
  const names = G.names;
  $("endEyebrow").textContent = o.winner === null ? `Stalemate, turn ${o.turns}` : `Turn ${o.turns}${w.over && w.turn >= 400 ? ", time called" : ""}`;
  $("endTitle").textContent = o.winner === null ? "Nobody won." : `${names[o.winner]} ${names[o.winner].endsWith("co.") || /^\d/.test(names[o.winner]) ? "win" : "wins"}.`;
  const s = $("endStats");
  s.replaceChildren(
    stat(`${names[0]} left standing`, `${o.alive[0].toLocaleString()} of ${w.units.filter((u) => u.side === 0).length.toLocaleString()}`),
    stat(`${names[1]} left standing`, `${o.alive[1].toLocaleString()} of ${w.units.filter((u) => u.side === 1).length.toLocaleString()}`),
    stat("Evolved in battle", String(o.evolutions)),
    stat(`Top unit, ${names[0]}`, mvpBox(mvp(0))),
    stat(`Top unit, ${names[1]}`, mvpBox(mvp(1))),
  );
  show("end");
}

// ---------- preset ----------
async function startPreset(p, seed) {
  clearTimeout(G.demoTimer);
  G.squads = resolveSquads(p, data);
  G.biome = p.biome;
  G.lastPreset = p;
  await launch(seed);
}

// ---------- war room ----------
function buildRoomChrome() {
  for (const [id, b] of Object.entries(BIOMES)) { const o = el("option", null, b.name); o.value = id; $("biome").append(o); }
  const dex = el("datalist");
  dex.id = "dex";
  for (const s of Object.values(data.species)) { const o = el("option"); o.value = s.name; dex.append(o); }
  document.body.append(dex);
}

function renderRoom() {
  $("biome").value = G.biome;
  renderSide(0, $("sideA"), "Blue");
  renderSide(1, $("sideB"), "Red");
  $("btnBegin").disabled = !(G.squads.some((s) => s.side === 0) && G.squads.some((s) => s.side === 1));
}

function renderSide(side, box, title) {
  box.replaceChildren();
  const mine = G.squads.filter((s) => s.side === side);
  const total = mine.reduce((t, s) => t + s.count, 0);
  const h = el("h3", null, title);
  h.append(el("small", null, `${total.toLocaleString()} ${total === 1 ? "unit" : "units"}`));
  const list = el("div", "r-list");
  for (const s of mine) list.append(squadRow(s));
  if (!mine.length) list.append(el("p", "i-sub", "No one here yet. Add a species below."));
  box.append(h, list, addForm(side));
}

function squadRow(s) {
  const sp = data.species[s.num], row = el("div", "sq");
  const img = el("img");
  img.src = spriteUrl(s.num); img.alt = "";
  img.addEventListener("click", () => inspectSpec(s));
  const nm = el("div", "nm", s.moves ? `${sp.name} ✦` : sp.name);
  nm.title = s.moves ? "Custom moves" : "Wild moves for its level";
  const x = el("button", "x", "×");
  x.type = "button"; x.setAttribute("aria-label", `Remove ${sp.name}`);
  x.addEventListener("click", () => { G.squads = G.squads.filter((o) => o !== s); changed(); });
  const ctl = el("div", "ctl");
  const num = (label, value, min, max, on) => {
    const lab = el("label");
    lab.append(`${label} `);
    const inp = el("input");
    inp.type = "number"; inp.min = min; inp.max = max; inp.value = value;
    inp.addEventListener("change", () => { const v = Math.max(min, Math.min(max, Math.floor(+inp.value) || min)); inp.value = v; on(v); changed(); });
    lab.append(inp);
    return lab;
  };
  ctl.append(
    num("×", s.count, 1, 2000, (v) => { s.count = v; }),
    num("Lv", s.level ?? defaultLevel(sp), 1, 100, (v) => { s.level = v; }),
  );
  const spacing = el("button", null, (s.spacing ?? 1) > 1 ? "Loose" : "Tight");
  spacing.type = "button"; spacing.title = "Loose formations take less splash damage";
  spacing.addEventListener("click", () => { s.spacing = (s.spacing ?? 1) > 1 ? 1 : 2; changed(); });
  const teach = el("button", null, "Moves");
  teach.type = "button";
  teach.addEventListener("click", () => inspectSpec(s, true));
  ctl.append(spacing, teach);
  row.append(img, nm, x, ctl);
  return row;
}

function addForm(side) {
  const box = el("form", "r-add");
  const row = el("div", "row");
  const inp = el("input");
  inp.type = "search"; inp.placeholder = "Add species…"; inp.setAttribute("list", "dex"); inp.setAttribute("aria-label", "Species");
  const count = el("input");
  count.type = "number"; count.min = 1; count.max = 2000; count.value = 30; count.setAttribute("aria-label", "Count");
  const go = el("button", "ui-btn ui-btn--sm", "Add");
  row.append(inp, count, go);
  const row2 = el("div", "row");
  const wild = el("select");
  wild.setAttribute("aria-label", "Wild horde location");
  const blank = el("option", null, "Wild horde from…");
  blank.value = "";
  wild.append(blank);
  for (const [i, w] of WILDS.entries()) { const o = el("option", null, w.name); o.value = i; wild.append(o); }
  wild.addEventListener("change", () => {
    if (wild.value === "") return;
    const n = Math.max(1, Math.min(2000, +count.value || 30));
    for (const h of wildHorde(WILDS[+wild.value], n, data, side)) addSquad(h);
    changed();
  });
  row2.append(wild);
  box.append(row, row2);
  box.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const q = inp.value.trim().toLowerCase();
    const sp = Object.values(data.species).find((s) => s.name.toLowerCase() === q) ?? Object.values(data.species).find((s) => s.name.toLowerCase().startsWith(q) && q);
    if (!sp) { inp.setCustomValidity("No Pokémon by that name"); inp.reportValidity(); setTimeout(() => inp.setCustomValidity(""), 1500); return; }
    addSquad({ side, num: sp.num, count: Math.max(1, Math.min(2000, +count.value || 1)) });
    changed();
  });
  return box;
}

function addSquad(s) {
  G.squads.push({ ...s, id: G.nextSquadId++ });
}

let previewQueued = false;
function changed() {
  $("forecast").textContent = "";
  renderRoom();
  queuePreview();
}
function queuePreview() {
  if (previewQueued) return;
  previewQueued = true;
  setTimeout(() => { previewQueued = false; preview(); }, 40);
}
async function preview() {
  G.world = createWorld(G.squads, 1);
  await stage.setWorld(G.world, { preview: true });
}

async function openRoom() {
  clearTimeout(G.demoTimer);
  G.demo = false;
  show("room");
  stage.paused = false;
  G.squads = G.squads.map((s) => ({ ...s }));
  if (!G.squads.length) G.squads = resolveSquads(PRESETS[0], data);
  G.squads.forEach((s, i) => { if (s.id == null) s.id = G.nextSquadId + i; });
  resetBiome(G.biome);
  stage.setCamMode("free");
  // portrait screens need to stand much further back to fit both zones
  const back = Math.max(1, 1.25 / (innerWidth / innerHeight));
  stage.camera.position.set(0, 60 * back, 58 * back);
  stage.controls.target.set(0, 0, innerWidth < 760 ? 12 : 3);
  stage.controls.update();
  renderRoom();
  await preview();
}

// ---------- inspect ----------
function closeInspect() {
  $("inspect").hidden = true;
  if (stage) { stage.selected = null; if (stage.camMode === "follow") stage.setCamMode("director"); }
}

function moveRows(box, moves, showPp) {
  const list = el("div", "i-moves");
  for (const m of moves) {
    const d = data.moves[m.id];
    if (!d) continue;
    list.append(el("span", null, d.name), el("span", null, d.pow ? `${d.pow} pw` : d.cat === "status" ? "status" : "—"), el("span", null, showPp ? `${m.left}/${d.pp} PP` : `${d.pp} PP`));
  }
  box.append(list);
}

function statGrid(box, values) {
  const g = el("div", "i-stats");
  const top = Math.max(...values, 1);
  for (const [i, label] of ["HP", "Atk", "Def", "SpA", "SpD", "Spe"].entries()) {
    const bar = el("i");
    bar.style.width = `${(values[i] / top) * 100}%`;
    g.append(el("span", null, label), el("span", null, String(values[i])), bar);
  }
  box.append(g);
}

function inspectHead(box, num, sub) {
  const sp = data.species[num], head = el("div", "i-head"), img = el("img"), t = el("div");
  img.src = spriteUrl(num); img.alt = "";
  t.append(el("h4", null, sp.name), el("div", "i-sub", sub));
  const types = el("div");
  for (const ty of sp.types) types.append(el("span", "i-pill", ty));
  t.append(types);
  const x = el("button", "x", "×");
  x.type = "button"; x.setAttribute("aria-label", "Close");
  x.addEventListener("click", closeInspect);
  head.append(img, t, x);
  box.append(head);
}

function abilityLine(box, ability, sp) {
  const n = Object.entries(sp.abilities).find(([, v]) => v === ability)?.[0];
  const title = n != null ? sp.abilityNames[n] : ability;
  const line = el("p", "i-ability");
  line.append(el("b", null, title), `: ${ABILITIES[ability] ?? "No effect in this simulation yet."}`);
  box.append(line);
}

function renderInspect(v) {
  const u = G.world.units[v.id], box = $("inspect");
  if (!u) return;
  const num = u.tf?.num ?? u.num, sp = data.species[num];
  box.replaceChildren();
  inspectHead(box, num, `L${u.level} · ${sp.heightm} m · ${sp.weightkg} kg${u.tf ? " · Ditto" : ""}`);
  box.append(el("p", "i-dex", sp.dex));
  abilityLine(box, u.ability, sp);
  if (u.alive) box.append(el("div", "i-sub", `HP ${Math.max(0, u.hp)}/${u.maxHp}${u.status ? ` · ${u.status.toUpperCase()}` : ""}${u.kills ? ` · ${u.kills} KO` : ""}`));
  else box.append(el("div", "i-sub", "Fainted"));
  statGrid(box, [u.maxHp, u.atk, u.def, u.spa, u.spd, u.spe]);
  moveRows(box, u.moves, true);
  const here = data.species[u.num];
  if (here.evoLevel && !u.tf) {
    const need = xpAt(here.growth, here.evoLevel) - u.xp;
    box.append(el("div", "i-sub", `Evolves into ${data.species[here.evoTo].name} at L${here.evoLevel}: ${need.toLocaleString()} XP to go`));
  }
  if (G.mode === "war") {
    const actions = el("div", "i-actions");
    const follow = el("button", "ui-btn ui-btn--sm", stage.follow === v && stage.camMode === "follow" ? "Stop following" : "Follow");
    follow.type = "button";
    follow.addEventListener("click", () => {
      if (stage.camMode === "follow" && stage.follow === v) stage.setCamMode("director");
      else { stage.follow = v; stage.setCamMode("follow"); }
      renderInspect(v);
    });
    actions.append(follow);
    box.append(actions);
  }
  box.hidden = false;
}

function inspectSpec(s, teach = false) {
  const sp = data.species[s.num], box = $("inspect");
  const level = s.level ?? defaultLevel(sp);
  box.replaceChildren();
  inspectHead(box, s.num, `L${level} · ${sp.heightm} m · ${footprint(sp)}×${footprint(sp)} tiles`);
  box.append(el("p", "i-dex", sp.dex));
  abilityLine(box, s.ability ?? sp.abilities[s.slot ?? "0"], sp);
  const moves = s.moves ?? wildMoves(sp, level);
  moveRows(box, moves.map((id) => ({ id })), false);
  if (teach) {
    const legal = [...new Set([...sp.levelup.map(([, m]) => m), ...sp.tm])].filter((m) => data.moves[m]).sort((a, b) => data.moves[a].name.localeCompare(data.moves[b].name));
    const grid = el("div", "i-teach");
    const picks = [0, 1, 2, 3].map((i) => {
      const sel = el("select");
      sel.setAttribute("aria-label", `Move ${i + 1}`);
      const none = el("option", null, "—");
      none.value = "";
      sel.append(none);
      for (const m of legal) { const o = el("option", null, data.moves[m].name); o.value = m; sel.append(o); }
      sel.value = moves[i] ?? "";
      sel.addEventListener("change", () => {
        const next = picks.map((p) => p.value).filter(Boolean);
        s.moves = [...new Set(next)];
        changed();
        inspectSpec(s, true);
      });
      grid.append(sel);
      return sel;
    });
    box.append(grid);
    if (s.moves) {
      const wildBtn = el("button", "ui-btn ui-btn--sm", "Back to wild moves");
      wildBtn.type = "button"; wildBtn.style.marginTop = "6px";
      wildBtn.addEventListener("click", () => { delete s.moves; changed(); inspectSpec(s, true); });
      box.append(wildBtn);
    }
  }
  box.hidden = false;
}

// ---------- forecast ----------
let forecastWorker = null, forecastId = 0;
function runForecast() {
  forecastWorker ??= new Worker(new URL("./forecast.worker.js", import.meta.url), { type: "module" });
  const id = ++forecastId, games = 200;
  const box = $("forecast");
  box.textContent = "Simulating…";
  forecastWorker.onmessage = ({ data: t }) => {
    if (t.id !== forecastId) return;
    const pct = (x) => Math.round((100 * x) / t.n);
    box.replaceChildren();
    box.append("Blue ", el("b", null, `${pct(t.wins[0])}%`), " · Red ", el("b", null, `${pct(t.wins[1])}%`), ` · ~${Math.round(t.turns / t.n)} turns${t.n < t.games ? ` (${t.n}/${t.games})` : ""}`);
  };
  forecastWorker.postMessage({ id, squads: G.squads, games });
}

// ---------- input ----------
function wire() {
  $("btnBuild").addEventListener("click", () => { sfx.unlock(); openRoom(); });
  $("btnReal").addEventListener("click", () => $("realDialog").showModal());
  $("roomBack").addEventListener("click", () => { G.squads = []; showTitle(); });
  $("biome").addEventListener("change", () => { G.biome = $("biome").value; resetBiome(G.biome); queuePreview(); });
  $("btnBegin").addEventListener("click", () => { sfx.unlock(); launch(Math.floor(Math.random() * 1e6)); });
  $("btnForecast").addEventListener("click", runForecast);
  $("btnPause").addEventListener("click", () => setPaused(!stage.paused));
  $("speeds").addEventListener("click", (e) => { const n = +e.target.dataset?.speed; if (n) setSpeed(n); });
  $("btnSkip").addEventListener("click", skipToEnd);
  $("btnCam").addEventListener("click", cycleCam);
  $("btnQuit").addEventListener("click", () => { G.endAt = 0; G.world = null; setPaused(false); show("title"); showTitle(); });
  $("btnRematch").addEventListener("click", () => launch(Math.floor(Math.random() * 1e6)));
  $("btnReplay").addEventListener("click", () => launch(G.seed));
  $("btnEdit").addEventListener("click", openRoom);
  $("btnEndHome").addEventListener("click", showTitle);
  $("quality").value = savedQuality();
  $("quality").addEventListener("change", () => {
    const v = $("quality").value;
    try { localStorage.setItem("tg-quality", v); } catch { /* private window */ }
    stage.setQuality(v === "auto" ? quality() : v);
  });
  $("btnMute").addEventListener("click", () => {
    sfx.unlock();
    const on = !sfx.muted;
    sfx.setMuted(on);
    $("btnMute").textContent = on ? "Sound off" : "Sound on";
    $("btnMute").setAttribute("aria-pressed", String(on));
  });
  addEventListener("keydown", (e) => {
    if (e.target.matches?.("input,select,textarea")) return;
    if (e.code === "Escape") closeInspect();
    if (G.mode !== "war") return;
    if (e.code === "Space") { e.preventDefault(); setPaused(!stage.paused); }
    if (e.key === "1" || e.key === "2" || e.key === "4") setSpeed(+e.key);
    if (e.key === "c") cycleCam();
  });

  const gl = $("gl");
  let down = null;
  gl.addEventListener("pointerdown", (e) => {
    sfx.unlock();
    down = { x: e.clientX, y: e.clientY, moved: false };
    if (G.mode !== "room" || e.button !== 0) return;
    const v = stage.pick(e.clientX, e.clientY);
    if (!v) return;
    const u = G.world.units[v.id];
    const squad = G.squads.find((s) => s.id === u.squad);
    if (!squad) return;
    const tile = stage.groundTile(e.clientX, e.clientY);
    G.dragging = { squad, dx: squad.x - (tile?.x ?? squad.x), dy: squad.y - (tile?.y ?? squad.y) };
    stage.controls.enabled = false;
    stage.dragLock = true;
  }, { capture: true });
  addEventListener("pointermove", (e) => {
    if (down && Math.hypot(e.clientX - down.x, e.clientY - down.y) > 5) down.moved = true;
    if (!G.dragging || !down?.moved) return;
    const t = stage.groundTile(e.clientX, e.clientY);
    if (!t) return;
    const { squad, dx, dy } = G.dragging;
    const [a, b] = zoneOf(squad.side);
    squad.x = Math.max(a + 1, Math.min(b - 2, t.x + dx));
    squad.y = Math.max(1, Math.min(70, t.y + dy));
    queuePreview();
  });
  addEventListener("pointerup", (e) => {
    const was = G.dragging, click = down && !down.moved;
    G.dragging = null;
    stage.dragLock = false;
    stage.controls.enabled = true;
    if (was && !click) $("forecast").textContent = "";
    if (click && e.target === gl && (G.mode === "war" || G.mode === "room")) {
      const v = stage.pick(e.clientX, e.clientY);
      if (v) { stage.selected = v; renderInspect(v); } else closeInspect();
    }
    down = null;
  });
  gl.addEventListener("contextmenu", (e) => e.preventDefault());
  setInterval(() => { if (G.mode === "war" && stage?.selected && !$("inspect").hidden) renderInspect(stage.selected); }, 700);
}

function cycleCam() {
  stage.setCamMode(stage.camMode === "director" ? "free" : "director");
}

function skipToEnd() {
  const w = G.world;
  while (!w.over) step(w);
  G.endAt = 0;
  stage.queue.length = 0;
  finishWar();
}

boot();
