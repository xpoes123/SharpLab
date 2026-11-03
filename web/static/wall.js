// The Discord wall's watch page: an HLS player held at the same wall-clock instant as every other viewer, plus chat,
// who's watching and votes over one socket (/ws/wall). Everything shown from other people goes in via textContent.
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const HLS_URL = "/api/v1/wall/hls/index.m3u8";

  let me = null, cfg = { delay: 6, vote_s: 60 }, ws = null, wall = null, retry = 1000;
  const props = new Map();          // open proposals by id
  const offsets = [];               // server − local clock samples (ms), low-RTT ones kept
  let offset = 0;
  let pdtOff = null;                // stream PDT − server time (ms), from the playlists the server proxies

  // ---- clock: server time from ping/pong, median of the best samples ----
  const serverNow = () => Date.now() + offset;
  function addOffset(c, s) {
    const now = Date.now(), rtt = now - c;
    offsets.push({ off: s - (c + now) / 2, rtt });
    offsets.sort((a, b) => a.rtt - b.rtt);
    if (offsets.length > 12) offsets.length = 12;
    const best = offsets.slice(0, 5).map((o) => o.off).sort((a, b) => a - b);
    offset = best[Math.floor(best.length / 2)];
  }

  // ---- player + sync ----
  const video = $("video");
  let hls = null;
  const sync = { err: null, pdt: null, target: null, rate: 1, offset: 0, seeks: 0 };
  window.__wallSync = sync;

  function playingDate() {
    if (hls && hls.playingDate) return hls.playingDate.getTime();
    if (!hls && video.getStartDate) {                          // Safari's native HLS
      const d = video.getStartDate().getTime();
      return isNaN(d) ? null : d + video.currentTime * 1000;
    }
    return null;
  }

  let runId = null;
  function startPlayer() {
    if (hls || video.src) return;
    if (window.Hls && Hls.isSupported()) {
      hls = new Hls({
        liveSyncDuration: cfg.delay,            // land near the shared target on load …
        liveMaxLatencyDuration: cfg.delay + 30, // … but let our loop, not hls.js, do the fine catch-up
        maxLiveSyncPlaybackRate: 1,
        lowLatencyMode: false,
        backBufferLength: 30,
      });
      hls.on(Hls.Events.ERROR, (_, d) => {
        if (!d.fatal) return;
        if (d.type === Hls.ErrorTypes.MEDIA_ERROR) { hls.recoverMediaError(); return; }
        // network or other fatal: a fresh player (a restarted wall renumbers its segments, which a resumed load never finds)
        stopPlayer(); setTimeout(() => { if (wall && wall.running) startPlayer(); }, 3000);
      });
      hls.loadSource(HLS_URL);
      hls.attachMedia(video);
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = HLS_URL;
    }
    video.play().catch(() => {});
  }

  function stopPlayer() {
    if (hls) { hls.destroy(); hls = null; }
    if (video.src) { video.removeAttribute("src"); video.load(); }
  }

  // Every viewer aims at the same instant: target = server now − delay. Big error → seek; small → nudge the rate.
  function syncTick() {
    const pdt = playingDate();
    sync.offset = offset; sync.pdtOff = pdtOff;
    if (pdt == null || video.paused || video.readyState < 2) { sync.err = null; showSync(); return; }
    // the live edge as the server sees it (cancels any encoder clock offset), minus the shared delay
    const target = serverNow() + (pdtOff || 0) - cfg.delay * 1000;
    const err = (pdt - target) / 1000;                 // + = ahead of the others
    sync.pdt = pdt; sync.target = target; sync.err = err;
    if (Math.abs(err) > 3) {
      video.currentTime = Math.max(0, video.currentTime - err);
      video.playbackRate = 1; sync.seeks++;
    } else if (Math.abs(err) > 0.05) {
      video.playbackRate = Math.min(1.05, Math.max(0.95, 1 - err * 0.1));
    } else video.playbackRate = 1;
    sync.rate = video.playbackRate;
    showSync();
  }

  function showSync() {
    const s = $("sync");
    if (sync.err == null) { s.textContent = wall && wall.running === false ? "wall off" : "syncing…"; s.className = "pill"; return; }
    const a = Math.abs(sync.err);
    s.textContent = `in sync ±${a.toFixed(1)} s`;
    s.className = "pill " + (a < 0.5 ? "ok" : a < 1.5 ? "meh" : "bad");
  }

  $("unmute").onclick = () => { video.muted = false; video.play().catch(() => {}); $("unmute").hidden = true; };

  // ---- socket ----
  function send(m) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(m)); }
  function ping() { send({ t: "ping", c: Date.now() }); }

  function connect() {
    ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws/wall");
    ws.onopen = () => { retry = 1000; for (let i = 0; i < 4; i++) setTimeout(ping, i * 400); };
    ws.onmessage = (e) => onMsg(JSON.parse(e.data));
    ws.onclose = (e) => {
      if (e.code === 4401) { showGate(); return; }
      setTimeout(connect, retry); retry = Math.min(retry * 2, 15000);
    };
  }

  function onMsg(m) {
    switch (m.t) {
      case "hello":
        me = m.me; cfg = { ...cfg, ...m.cfg };
        offset = m.now - Date.now();
        if (m.pdt_off != null) pdtOff = m.pdt_off;
        $("ownerNote").textContent = me.owner ? "· you're the owner: changes apply at once" : me.guest ? "· as a guest you can vote, and change sound / layout / board" : "";
        document.querySelectorAll("[data-member]").forEach((x) => (x.hidden = !!me.guest));
        $("msgs").replaceChildren(); m.history.forEach(addMsg);
        renderViewers(m.viewers);
        props.clear(); m.proposals.forEach((p) => props.set(p.id, p)); renderProps();
        setWall(m.wall);
        break;
      case "pong": addOffset(m.c, m.s); if (m.pdt_off != null) pdtOff = m.pdt_off; break;
      case "clock": pdtOff = m.pdt_off; break;
      case "chat": addMsg(m.msg); break;
      case "del": { const li = document.querySelector(`#msgs li[data-id="${m.id}"]`); if (li) li.remove(); break; }
      case "viewers": renderViewers(m.list); break;
      case "proposal": if (m.p.status === "open") props.set(m.p.id, m.p); else props.delete(m.p.id); renderProps(); break;
      case "wall": setWall(m.state); break;
      case "error": $("chatErr").textContent = m.text; setTimeout(() => ($("chatErr").textContent = ""), 4000); break;
    }
  }

  // ---- wall state ----
  function setWall(st) {
    wall = st || null;
    const on = !!(wall && wall.running);
    $("offline").hidden = on || !wall;
    $("wallName").textContent = on ? wall.name || "" : "";
    $("mirror").hidden = !(on && wall.mirror);
    $("followBtn").hidden = !on || !!wall.mirror;
    if (on && runId !== null && wall.started !== runId) stopPlayer();   // the wall restarted: new segment numbers
    runId = on ? wall.started : null;
    if (on) startPlayer(); else if (wall) stopPlayer();
    const sel = $("preset"), cur = sel.value;
    const tv = el("option", null, "📺 Same as the TV"); tv.value = "";
    sel.replaceChildren(tv, ...((wall && wall.presets) || []).map((n) => { const o = el("option", null, n); o.value = n; return o; }));
    if (cur) sel.value = cur;
    const tiles = $("tiles"); tiles.replaceChildren();
    if (!on) return;
    const labels = (wall.live && wall.live.labels) || [];
    (wall.urls || []).forEach((u, i) => {
      const t = el("div", "tile" + (wall.sound === i ? " sound" : ""));
      t.append(el("b", null, String(i + 1)), el("span", "lbl", labels[i] || shortUrl(u)));
      if (wall.sound === i) t.append(el("span", null, "🔊"));
      else { const b = el("button", null, "🔊 here"); b.onclick = () => propose({ op: "sound", i }); t.append(b); }
      tiles.append(t);
    });
  }
  const shortUrl = (u) => { try { const x = new URL(u); return (x.hostname.replace(/^www\./, "") + x.pathname).replace(/\/$/, ""); } catch { return u; } };

  // ---- viewers ----
  function avatar(v) {
    if (v.avatar) { const i = el("img", "av"); i.src = v.avatar; i.alt = ""; i.referrerPolicy = "no-referrer"; return i; }
    return el("span", "av", v.initials || "?");
  }
  function renderViewers(list) {
    const ul = $("viewers"); ul.replaceChildren();
    list.forEach((v) => {
      const li = el("li", v.away ? "away" : "");
      li.append(avatar(v), el("span", null, v.name));
      if (v.guest) li.append(el("span", "tag", "guest"));
      if (v.away) li.append(el("span", "tag", "away"));
      ul.append(li);
    });
    const active = list.filter((v) => !v.away).length;
    $("count").textContent = `${list.length} watching` + (active < list.length ? ` · ${active} active` : "");
  }

  // ---- chat ----
  function addMsg(m) {
    const li = el("li", m.kind === "system" ? "sys" : ""); li.dataset.id = m.id;
    if (m.kind === "system") li.append(el("span", null, m.text));
    else {
      li.append(avatar({ avatar: m.avatar, initials: (m.name || "?").slice(0, 1).toUpperCase() }));
      const body = el("span"); body.append(el("span", "who", m.name));
      if (m.guest) body.append(el("span", "tag", "guest"));
      body.append(document.createTextNode(m.text)); li.append(body);
      if (me && me.admin) {
        const d = el("button", "mod", "✕"); d.title = "Delete"; d.onclick = () => send({ t: "del", id: m.id });
        li.append(d);
        if (m.uid !== me.id) { const mu = el("button", "mod", "mute"); mu.title = "Mute 10 min"; mu.onclick = () => send({ t: "mute", uid: m.uid, minutes: 10 }); li.append(mu); }
      }
    }
    const box = $("msgs"), stick = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    box.append(li);
    while (box.children.length > 200) box.firstChild.remove();
    if (stick) box.scrollTop = box.scrollHeight;
  }
  $("chatForm").onsubmit = (e) => {
    e.preventDefault();
    const t = $("chatIn").value.trim();
    if (t) { send({ t: "chat", text: t }); $("chatIn").value = ""; }
  };

  // ---- votes ----
  function propose(action) { send({ t: "propose", action }); }
  function renderProps() {
    const box = $("props"); box.replaceChildren();
    for (const p of props.values()) {
      const d = el("div", "prop");
      d.append(el("span", "what", `${p.name}: ${p.text}`));
      d.append(el("span", "tally", `${p.yes.length} ✓ · ${p.no.length} ✗ · needs ${p.need}`));
      const y = el("button", me && p.yes.includes(me.id) ? "mine" : "", "✓"), n = el("button", me && p.no.includes(me.id) ? "mine" : "", "✗");
      y.onclick = () => send({ t: "vote", id: p.id, yes: true });
      n.onclick = () => send({ t: "vote", id: p.id, yes: false });
      const left = el("span", "left", `${Math.ceil(p.left)}s`); left.dataset.end = String(Date.now() + p.left * 1000);
      d.append(y, n, left); box.append(d);
    }
  }
  setInterval(() => document.querySelectorAll(".prop .left").forEach((s) => (s.textContent = `${Math.max(0, Math.ceil((+s.dataset.end - Date.now()) / 1000))}s`)), 500);

  document.querySelectorAll("[data-act]").forEach((b) => (b.onclick = () => {
    const act = b.dataset.act;
    if (act === "start") { const p = $("preset").value; propose(p ? { op: "start", preset: p } : { op: "start", mirror: true }); }
    else if (act === "follow") propose({ op: "follow" });
    else if (act === "stop") propose({ op: "stop" });
    else if (act === "layout") propose({ op: "layout", layout: b.dataset.v });
    else if (act === "board") propose({ op: "board", board: b.dataset.v });
    else if (act === "tile") {
      let u = $("tileUrl").value.trim();
      if (u && !/^https?:\/\//.test(u)) u = "https://" + u;
      if (u) { propose({ op: "tile", i: +$("tileI").value, url: u }); $("tileUrl").value = ""; }
    }
  }));

  // ---- presence ----
  const beat = () => send({ t: "hb", hidden: document.hidden });
  document.addEventListener("visibilitychange", beat);
  setInterval(beat, 15000);
  setInterval(ping, 15000);
  setInterval(syncTick, 250);

  // ---- boot ----
  function showGate(msg) {
    $("room").hidden = true; $("gate").hidden = false;
    if (msg) $("gateErr").textContent = msg;
  }
  const qs = new URLSearchParams(location.search), err = qs.get("error"), key = qs.get("k") || "";
  function boot() {
    fetch("/api/v1/wall/me?k=" + encodeURIComponent(key), { credentials: "same-origin" }).then((r) => r.json().then((j) => ({ ok: r.ok, j }))).then(({ ok, j }) => {
      if (!ok) {
        if (j.login) $("loginBtn").href = j.login;
        $("guestForm").hidden = !j.guest_ok;   // demo mode with the keyed link: a nickname is enough
        showGate(err === "link_expired" ? "That link expired — use Get my link again, or log in." : "");
        return;
      }
      $("navRight").textContent = j.name + (j.guest ? " (guest)" : "");
      $("gate").hidden = true;
      $("room").hidden = false;
      connect();
    }).catch(() => showGate("Couldn't reach SharpLab."));
  }
  $("guestForm").onsubmit = (e) => {
    e.preventDefault();
    fetch("/api/v1/wall/guest", { method: "POST", credentials: "same-origin", headers: { "content-type": "application/json" },
                                  body: JSON.stringify({ k: key, name: $("nick").value.trim() }) })
      .then((r) => r.json().then((j) => ({ ok: r.ok, j })))
      .then(({ ok, j }) => { if (ok) boot(); else $("gateErr").textContent = j.error || "Couldn't join."; })
      .catch(() => ($("gateErr").textContent = "Couldn't reach SharpLab."));
  };
  boot();
})();
