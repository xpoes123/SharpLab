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
  let lastGames = null;   // the Live now list, re-drawn when the wall changes
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
  video.preservesPitch = video.mozPreservesPitch = video.webkitPreservesPitch = true;   // any rate change never deepens voices
  function syncTick() {
    const pdt = playingDate();
    sync.offset = offset; sync.pdtOff = pdtOff;
    if (pdt == null || video.paused || video.readyState < 2) { sync.err = null; showSync(); return; }
    // the live edge as the server sees it (cancels any encoder clock offset), minus the shared delay
    const target = serverNow() + (pdtOff || 0) - cfg.delay * 1000;
    const err = (pdt - target) / 1000;                 // + = ahead of the others
    sync.pdt = pdt; sync.target = target; sync.err = err;
    // with sound on, never touch the speed: rate nudges make phone audio stutter. Drift that big gets one clean seek.
    if (Math.abs(err) > (video.muted ? 3 : 4)) {
      video.currentTime = Math.max(0, video.currentTime - err);
      video.playbackRate = 1; sync.seeks++;
    } else if (video.muted && Math.abs(err) > 0.25) {
      video.playbackRate = Math.min(1.03, Math.max(0.97, 1 - err * 0.05));
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

  const showMute = () => {
    const b = $("muteBtn");
    b.querySelector(".ico").textContent = video.muted ? "🔇" : "🔊";
    b.querySelector(".lbl").textContent = video.muted ? " Sound off" : " Sound on";
    b.setAttribute("aria-label", video.muted ? "Sound off" : "Sound on");
    const live = !!(wall && wall.running);   // nothing to hear while the wall is off: no sound controls at all
    $("unmute").hidden = !video.muted || !live;
    b.hidden = !live; $("vol").hidden = !live;
    $("vol").value = String(video.muted ? 0 : Math.round(video.volume * 100));
  };
  // Volume: this viewer's player only (the TV and other viewers are untouched); remembered in this browser.
  try { const v = localStorage.getItem("wall.vol"); if (v != null && +v > 0) video.volume = Math.min(1, +v / 100); } catch (e) { /* default volume */ }
  $("vol").oninput = () => {
    const v = +$("vol").value;
    if (v > 0) { video.volume = v / 100; if (video.muted) { video.muted = false; video.play().catch(() => {}); } }
    else video.muted = true;
    try { if (v > 0) localStorage.setItem("wall.vol", String(v)); } catch (e) { /* not remembered */ }
  };
  const toggleMute = () => { video.muted = !video.muted; if (!video.muted) video.play().catch(() => {}); showMute(); };
  $("unmute").onclick = toggleMute;
  $("muteBtn").onclick = toggleMute;
  video.addEventListener("volumechange", showMute);
  $("fsBtn").onclick = () => {   // the player box (video + overlays) where the browser allows it; iPhone Safari only does the bare video
    const box = video.parentElement;
    if (document.fullscreenElement || document.webkitFullscreenElement) (document.exitFullscreen || document.webkitExitFullscreen).call(document);
    else if (box.requestFullscreen) box.requestFullscreen().catch(() => video.webkitEnterFullscreen && video.webkitEnterFullscreen());
    else if (box.webkitRequestFullscreen) box.webkitRequestFullscreen();
    else if (video.webkitEnterFullscreen) video.webkitEnterFullscreen();
  };

  // double-click (or double-tap) the video: full screen and back, like any player
  video.parentElement.addEventListener("dblclick", (e) => { if (e.target.closest("button")) return; e.preventDefault(); $("fsBtn").click(); });

  // Hotkeys (not while typing): m mute, f fullscreen, space pause / back to live (the sync loop catches it up), ↑ ↓ volume
  document.addEventListener("keydown", (e) => {
    const t = e.target;
    if (e.ctrlKey || e.metaKey || e.altKey || (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT|BUTTON)$/.test(t.tagName)))) return;
    if (!(wall && wall.running)) return;
    const k = e.key.toLowerCase();
    if (k === "m") toggleMute();
    else if (k === "f") $("fsBtn").click();
    else if (k === " ") { if (video.paused) video.play().catch(() => {}); else video.pause(); }
    else if (k === "arrowup" || k === "arrowdown") {
      const v = Math.max(0, Math.min(100, (video.muted ? 0 : Math.round(video.volume * 100)) + (k === "arrowup" ? 5 : -5)));
      $("vol").value = String(v); $("vol").oninput();
    } else return;
    e.preventDefault();
  });

  // ---- picture-in-picture: keep watching while in other apps ----
  // The standard API (Chrome, Android, desktop Safari), else iPhone Safari's presentation mode. autopictureinpicture
  // on the <video> + the media session's enterpictureinpicture handler let Chrome pop out by itself on an app switch.
  // Nothing here pauses on visibilitychange: hls.js and the sync loop keep running while the page is hidden.
  const stdPip = !!(document.pictureInPictureEnabled && video.requestPictureInPicture && !video.disablePictureInPicture);
  const iosPip = !stdPip && typeof video.webkitSetPresentationMode === "function" &&
    (!video.webkitSupportsPresentationMode || video.webkitSupportsPresentationMode("picture-in-picture"));
  const inPip = () => !!document.pictureInPictureElement || video.webkitPresentationMode === "picture-in-picture";
  $("pipBtn").hidden = !(stdPip || iosPip);
  $("pipBtn").onclick = () => {
    if (stdPip) (inPip() ? document.exitPictureInPicture() : video.requestPictureInPicture()).catch(() => {});
    else if (iosPip) video.webkitSetPresentationMode(inPip() ? "inline" : "picture-in-picture");
  };
  const showPip = () => $("pipBtn").setAttribute("aria-pressed", String(inPip()));
  ["enterpictureinpicture", "leavepictureinpicture", "webkitpresentationmodechanged"].forEach((ev) => video.addEventListener(ev, showPip));

  // lock screen / PiP window: the wall's name + SharpLab artwork, play/pause (the sync loop re-lands a resumed player)
  const media = "mediaSession" in navigator ? navigator.mediaSession : null;
  function setMedia() {
    if (!media || !window.MediaMetadata) return;
    try {
      media.metadata = new MediaMetadata({ title: (wall && wall.running && wall.name) || "The wall", artist: "SharpLab watch party",
                                           album: "SharpLab Wall", artwork: [{ src: "/wall-art.png", sizes: "512x512", type: "image/png" }] });
    } catch (e) { /* older browsers: no metadata */ }
  }
  if (media) {
    const on = (a, f) => { try { media.setActionHandler(a, f); } catch (e) { /* action not supported here */ } };
    on("play", () => video.play().catch(() => {}));
    on("pause", () => video.pause());
    if (stdPip) on("enterpictureinpicture", () => video.requestPictureInPicture().catch(() => {}));
    video.addEventListener("play", () => (media.playbackState = "playing"));
    video.addEventListener("pause", () => (media.playbackState = "paused"));
  }

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
        $("msgs").replaceChildren(); m.history.forEach(addMsg);
        if (me.public) { showCount(m.count); break; }   // public: the chat read-only + a head count, nothing else
        $("ownerNote").textContent = me.owner ? "· you're the owner: changes apply at once" : "";
        $("offSub").textContent = "Start it from “Change the wall” below.";
        renderViewers(m.viewers);
        props.clear(); m.proposals.forEach((p) => props.set(p.id, p)); renderProps();
        setWall(m.wall);
        break;
      case "pong": addOffset(m.c, m.s); if (m.pdt_off != null) pdtOff = m.pdt_off; break;
      case "clock": pdtOff = m.pdt_off; break;
      case "chat": { const seen = chatInView(); addMsg(m.msg); incoming(m.msg, seen); break; }
      case "del": { const li = document.querySelector(`#msgs li[data-id="${m.id}"]`); if (li) li.remove(); break; }
      case "viewers": renderViewers(m.list); break;
      case "count": showCount(m); break;
      case "proposal": if (m.p.status === "open") props.set(m.p.id, m.p); else props.delete(m.p.id); renderProps(); break;
      case "wall": setWall(m.state); break;
      case "error":
        if (m.chat && !$("chatIn").value) $("chatIn").value = lastChat;   // a refused message goes back in the box
        $("chatErr").textContent = m.text; setTimeout(() => ($("chatErr").textContent = ""), 4000); break;
    }
  }

  // ---- wall state ----
  function setWall(st) {
    wall = st || null;
    const on = !!(wall && wall.running);
    $("offline").hidden = on || !wall;
    video.closest(".player").classList.toggle("off", !on);
    showMute(); wake();
    $("wallName").textContent = on ? wall.name || "" : "";
    $("mirror").hidden = !(on && wall.mirror);
    $("followBtn").hidden = !on || !!wall.mirror;
    setMedia();
    if (on && runId !== null && wall.started !== runId) stopPlayer();   // the wall restarted: new segment numbers
    runId = on ? wall.started : null;
    if (on) startPlayer(); else if (wall) stopPlayer();
    const sel = $("preset"), cur = sel.value;
    const tv = el("option", null, "📺 Same as the TV"); tv.value = "";
    sel.replaceChildren(tv, ...((wall && wall.presets) || []).map((n) => { const o = el("option", null, n); o.value = n; return o; }));
    if (cur) sel.value = cur;
    if (lastGames) renderLive(lastGames);
    const elecMode = !!(wall && ((wall.overlay && wall.overlay.board === "election") || (wall.running && (wall.urls || []).some(ownPage))));
    document.querySelectorAll("[data-sports]").forEach((x) => (x.hidden = elecMode));
    document.querySelectorAll("[data-elec]").forEach((x) => (x.hidden = !elecMode));
    if (elecMode) renderElec();
    const tiles = $("tiles"); tiles.replaceChildren();
    if (!on) return;
    const labels = (wall.live && wall.live.labels) || [];
    (wall.urls || []).forEach((u, i) => {
      const name = tileName(u, labels[i]);
      const t = el("div", "tile" + (wall.sound === i ? " sound" : ""));
      t.append(el("b", null, String(i + 1)), el("span", "lbl", name));
      if (wall.sound === i) t.append(el("span", null, "🔊"));
      else if (!ownPage(u)) { const b = el("button", null, "🔊 here"); b.onclick = () => propose({ op: "sound", i }); t.append(b); }   // our data pages are silent
      if (i > 0 && (wall.urls || []).length >= 2) {   // tile 1 is the big one
        const p = el("button", null, "⬆ main"); p.title = "Make this the main (big) tile";
        p.onclick = () => propose({ op: "promote", i, label: name }); t.append(p);
      }
      const live = (wall.streams || [])[i];   // a YouTube tile: is it on yet (wall/streams.py)
      if (live) t.append(el("span", "small " + (live.live ? "live-on" : "muted"), live.live ? "● live" : live.starts
        ? `starts ${new Date(live.starts * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}` : live.live === false ? "not live" : ""));
      const air = onairBadge((wall.onair || [])[i]);   // ad-skip (wall/onair.py): on air / at a break / an ad
      if (air) t.append(air);
      if (!ownPage(u)) t.append(starBtn(u, name));
      if (elecMode && i < 2 && !ownPage(u)) {   // a stream on a dashboard tile: one tap back to a dashboard
        const free = ELEC_SCREENS.find(([scr]) => !(wall.urls || []).includes(OWN + scr));
        if (free) { const d = el("button", null, "↩ dashboard"); d.title = `Back to ${free[1]}`;
          d.onclick = () => propose({ op: "tile", i, url: OWN + free[0], label: free[1] }); t.append(d); }
      }
      const rot = (wall.rotate || []).find((r) => r.i === i);   // an election data tile: rotates every minute
      if (rot) {
        t.append(el("span", "muted small", rot.held ? `held · ${Math.ceil(rot.in / 60)} min` : `next in ${rot.in}s`));
        const h = el("button", null, "🔒 hold"); h.title = "Keep this screen 5 min, then it rotates again";
        h.onclick = () => propose({ op: "rotate", i, act: "hold" }); t.append(h);
        const n = el("button", null, "⏭ next"); n.title = "Switch to the next screen now (it then stays 5 min)";
        n.onclick = () => propose({ op: "rotate", i, act: "next" }); t.append(n);
      }
      tiles.append(t);
    });
  }
  // Ad-skip: each stream's label from wall/adwatch.py, and the ★ favorites (two at most: the tiles' home streams)
  function onairBadge(a) {
    if (!a || !a.label) return null;
    const txt = { coverage: "● on air", ad: "ad", break: "⏸ break", offtopic: "off-topic" }[a.label];
    if (!txt) return null;
    // the break forecast (wall/breaks.py + Jev's "soon"): only while on air
    const ck = a.clock || {};
    const hint = a.label !== "coverage" ? "" : (a.soon || 0) >= 0.6 ? " · break soon" : ck.next_likely ? ` · break likely ${ck.next_likely}` : "";
    const b = el("span", "small " + (a.label === "coverage" ? "live-on" : "muted"), txt + hint);
    b.title = (a.tail ? `“…${a.tail}”` : "") + (a.confidence ? ` (${Math.round(a.confidence * 100)}%)` : "") +
      (ck.n_breaks ? ` · ${ck.n_breaks} breaks seen, ${Math.round((ck.p_soon || 0) * 100)}% one starts in ~1 min` : "");
    return b;
  }
  function starBtn(url, label) {
    const on = ((wall && wall.favorites) || []).includes(url);
    const b = el("button", null, on ? "★" : "☆");
    b.title = on ? "Unstar: no longer a home stream for tiles 3–4" : "Star (two at most): tiles 3–4 come back to it after a break";
    b.onclick = () => propose({ op: "favorite", url, on: !on, label });
    return b;
  }
  function adRow() {   // the election screens row: the ad-skip toggle + the pool of other news streams (with stars)
    const aw = (wall && wall.adwatch) || {};
    const tog = el("button", "btn", `Auto ad-skip: ${aw.on ? "on" : "off"}`);
    tog.title = "Sound leaves a stream at a commercial; a stream tile at a break for a minute swaps to one on air, then comes back";
    tog.onclick = () => propose({ op: "adwatch", on: !aw.on });
    const sd = (wall && wall.stream_director) || {};   // Jev runs tiles 3–4 + the sound (wall/streamdir.py)
    const dir = el("button", "btn", `Auto-director: ${sd.on ? "on" : "off"}`);
    dir.title = "Jev picks the two stream tiles and the sound every minute — sticky, never mid-moment; it pauses 10 min on a tile you change (works with auto ad-skip on)";
    dir.onclick = () => propose({ op: "director", on: !sd.on });
    const picks = ((sd.last || {}).picks || []).map((p) => p.name);
    const line = sd.on && sd.active && picks.length
      ? el("span", "muted small", `🎬 Jev picks: ${picks.join(" · ")}` + (sd.next_in != null ? ` (next look in ${sd.next_in}s)` : ""))
      : null;
    const pool = (aw.pool || []).map((p) => {
      const name = tileName(p.url), w = el("span", "pool");
      const put = el("button", "btn", name); put.title = "Put it on a stream tile";
      put.onclick = () => propose({ op: "tile", i: pickTile(true), url: p.url, label: name });
      w.append(put, starBtn(p.url, name));
      const air = onairBadge(p); if (air) w.append(air);
      return w;
    });
    return [tog, dir, ...(line ? [line] : []), ...pool];
  }
  // Election night: our own /p/election/<screen> pages and local stations get names, not URLs
  const OWN = "http://127.0.0.1:7795/p/election/";
  const ELEC_SCREENS = [["map?view=S", "Senate map"], ["map?view=H", "House map"], ["map?view=G", "Governor map"],
    ["races", "Key races"], ["turnout", "Turnout"], ["exitpoll", "Exit polls"], ["swing", "vs 2024"]];
  const ownPage = (u) => { try { return new URL(u).pathname.startsWith("/p/election/"); } catch { return false; } };
  function ownName(u) {
    const x = new URL(u), scr = x.pathname.slice("/p/election/".length).replace(/\/$/, "");
    if (scr === "map") return { S: "Senate map", H: "House map", G: "Governor map" }[x.searchParams.get("view")] || "Election map";
    return { races: "Key races", turnout: "Turnout", exitpoll: "Exit polls", swing: "vs 2024" }[scr] || "Election " + scr;
  }
  let localNews = null;   // {ST: [{name, url}]}: YouTube channels' /live pages
  fetch("/local-news.json").then((r) => r.json()).then((j) => { localNews = j; if (!$("newsSt").closest("[data-elec]").hidden) renderElec(); }).catch(() => {});
  function tileName(u, label) {
    if (ownPage(u)) return ownName(u);
    const yt = /youtube\.com\/(@[^/?#]+)\/live/i.exec(u);
    if (!yt) return label || shortUrl(u);
    const h = yt[1].toLowerCase();
    for (const list of Object.values(localNews || {})) {
      const n = list.find((s) => s.url.toLowerCase().includes("/" + h + "/live"));
      if (n) return n.name;
    }
    return yt[1] + " live";
  }
  const pickTile = (stream) => {   // the Tile menu's pick, or the next empty one; election streams never on the dashboards (1–2)
    const n = (wall && wall.urls) ? wall.urls.length : 0;
    let picked = +$("tileI").value;
    if (stream && picked < 2 && (wall.urls || []).some(ownPage)) picked = n > 3 ? 3 : Math.max(2, Math.min(n, 3));
    return picked < n ? picked : Math.min(n, 3);
  };
  function renderElec() {
    const onWall = new Set((wall && wall.running && wall.urls) || []);
    const pick = (url, label) => {
      const b = el("button", "btn", label + (onWall.has(url) ? " · on the wall" : ""));
      b.disabled = onWall.has(url);
      b.onclick = () => propose({ op: "tile", i: pickTile(!ownPage(url)), url, label });
      return b;
    };
    const def = el("button", "btn", "↺ Default");
    def.title = "Put the default election screens back on every tile (dashboards on 1–2, the default streams on 3–4)";
    def.onclick = () => propose({ op: "default" });
    $("elecScreens").replaceChildren(def, ...ELEC_SCREENS.map(([scr, label]) => pick(OWN + scr, label)), ...adRow());
    const sel = $("newsSt");
    if (localNews && !sel.options.length) {
      sel.replaceChildren(...Object.keys(localNews).sort().map((st) => { const o = el("option", null, st); o.value = st; return o; }));
      try { const s = localStorage.getItem("wall.newsSt"); if (s && localNews[s]) sel.value = s; } catch (e) { /* first state */ }
      sel.onchange = () => { try { localStorage.setItem("wall.newsSt", sel.value); } catch (e) { /* not remembered */ } renderElec(); };
    }
    $("newsBtns").replaceChildren(...((localNews && localNews[sel.value]) || []).map((n) => pick(n.url, n.name)));
    renderSpectrum(onWall);
  }
  // Election night "Spectrum" (wall/spectrum.py): streams from hard left to hard right, verified viewers only, and only
  // while ad-skip hears coverage (the server refuses a break too)
  let spectrumCat = null;
  fetch("/spectrum.json").then((r) => r.json()).then((j) => { spectrumCat = j; if (!$("elecScreens").closest("[data-elec]").hidden) renderElec(); }).catch(() => {});
  const verifiedMe = () => !!me && (!me.guest || me.tier === "friend");
  function specBadge(st, s) {
    if (st.label === "coverage") return ["● on air", "live-on"];
    if (st.label === "break" || st.label === "ad") return ["⏸ break", "muted"];
    if (st.label === "offtopic") return ["off-topic", "muted"];
    if (st.live === false && st.starts)
      return [`starts ${new Date(st.starts * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`, "muted"];
    if (st.live === false) return [s.note || "not live yet", "muted"];
    if (st.live) return ["listening…", "muted"];
    return [s.note || "", "muted"];
  }
  function renderSpectrum(onWall) {
    const row = $("spectrum").closest("[data-spectrum]");
    const states = (wall && wall.spectrum) || null;
    row.hidden = !(spectrumCat && states && verifiedMe());   // only called in election mode (renderElec)
    if (row.hidden) return;
    $("spectrum").replaceChildren(...(spectrumCat.groups || []).map((g) => {
      const grp = el("div", "spec-grp"); grp.append(el("b", null, g.label));
      (g.streams || []).forEach((s) => {
        const st = states[s.url] || {}, on = onWall.has(s.url), [txt, cls] = specBadge(st, s);
        const b = el("button", "btn", s.name);
        if (on) b.append(el("span", "small muted", "on the wall"));
        else if (txt) b.append(el("span", "small " + cls, txt));
        b.disabled = on || st.label !== "coverage";
        b.title = on ? "Already on the wall" : st.label === "coverage" ? `Put ${s.name} on a stream tile (3–4)` : "Pickable while it's on air (not at a break)";
        b.onclick = () => propose({ op: "tile", i: pickTile(true), url: s.url, label: `${g.label} · ${s.name}` });
        grp.append(b);
      });
      return grp;
    }));
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
    $("count").replaceChildren(`${list.length} watching`);
    if (active < list.length) $("count").append(el("span", "act", ` · ${active} active`));
    // phones: a strip of avatars + a few names; tapping it opens the full list as a sheet
    $("vstrip").replaceChildren(...list.slice(0, 5).map(avatar));
    if (list.length > 5) $("vstrip").append(el("span", "av more", `+${list.length - 5}`));
    const names = list.map((v) => v.name);
    $("vcount").textContent = names.slice(0, 2).join(", ") + (names.length > 2 ? ` +${names.length - 2}` : "");
  }

  function showCount(c) {   // public viewers: how many are at the party (never who)
    if (!c) return;
    $("count").textContent = `${c.watching} at the watch party` + (c.public ? ` · ${c.public} following along` : "");
  }

  // ---- chat ----
  function addMsg(m) {
    const li = el("li", m.kind === "system" ? "sys" : ""); li.dataset.id = m.id;
    if (m.kind === "system") li.append(el("span", null, m.text));
    else {
      li.append(avatar({ avatar: m.avatar, initials: (m.name || "?").slice(0, 1).toUpperCase() }));
      const body = el("span"); body.append(el("span", "who", m.name));
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
  let lastChat = "";
  $("chatForm").onsubmit = (e) => {
    e.preventDefault();
    const t = $("chatIn").value.trim();
    if (t) { lastChat = t; send({ t: "chat", text: t }); $("chatIn").value = ""; }
  };

  // ---- phone layouts (keep these queries in step with wall.css) ----
  const PORTRAIT = matchMedia("(max-width: 700px) and (orientation: portrait), (max-width: 700px) and (min-height: 501px)");
  const LANDSCAPE = matchMedia("(orientation: landscape) and (max-height: 500px)");
  const COARSE = matchMedia("(pointer: coarse)");
  const phone = () => PORTRAIT.matches || LANDSCAPE.matches;
  const vv = window.visualViewport;
  const sheets = [...document.querySelectorAll(".sheet-host")];
  const chatIn = $("chatIn"), msgs = $("msgs");

  // The room is pinned to the *visual* viewport, so the iOS keyboard shrinks the chat instead of covering the input.
  function fitViewport() {
    const st = document.documentElement.style;
    st.setProperty("--vvh", (vv ? vv.height : innerHeight) + "px");
    st.setProperty("--vvtop", (vv ? vv.offsetTop : 0) + "px");
    const wasStuck = msgs.scrollHeight - msgs.scrollTop - msgs.clientHeight < 40;
    document.body.classList.toggle("kb", PORTRAIT.matches && COARSE.matches && document.activeElement === chatIn);
    if (wasStuck) msgs.scrollTop = msgs.scrollHeight;
  }
  if (vv) { vv.addEventListener("resize", fitViewport); vv.addEventListener("scroll", fitViewport); }
  addEventListener("resize", fitViewport);
  chatIn.addEventListener("focus", () => { fitViewport(); setUnread(0); clearTimeout(hideT); });
  chatIn.addEventListener("blur", () => { fitViewport(); if (!pinned) peek(); });

  // sheets ("Change the wall", who's watching): open on desktop, collapsed bottom sheets on phones
  const anySheet = () => phone() && sheets.some((d) => d.open);
  const closeSheets = () => sheets.forEach((d) => (d.open = false));
  sheets.forEach((d) => d.addEventListener("toggle", () => {
    if (phone() && d.open) sheets.forEach((o) => o !== d && (o.open = false));
    $("scrim").hidden = !anySheet();
  }));
  $("scrim").onclick = closeSheets;
  document.querySelectorAll(".sheet-done").forEach((b) => (b.onclick = () => (b.closest("details").open = false)));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && anySheet()) closeSheets(); });

  // landscape: the bar fades after a few still seconds; chat peeks in on a message and hides again when quiet
  let pinned = false, hideT = null, idleT = null, unread = 0;
  function setUnread(n) { unread = n; $("unread").hidden = !n; $("unread").textContent = n > 9 ? "9+" : String(n); }
  function chatOn(on) {
    document.body.classList.toggle("chat-on", on);
    $("chatToggle").setAttribute("aria-pressed", String(on));
    if (on) msgs.scrollTop = msgs.scrollHeight;
  }
  function peek() {
    if (!LANDSCAPE.matches) return;
    if (!document.body.classList.contains("chat-on")) chatOn(true);
    clearTimeout(hideT);
    hideT = setTimeout(() => { if (!pinned && document.activeElement !== chatIn) chatOn(false); }, 5000);
  }
  $("chatToggle").onclick = () => {
    pinned = !pinned; clearTimeout(hideT); chatOn(pinned);
    if (pinned) setUnread(0); else chatIn.blur();
  };
  function wake() {   // landscape: controls fade only over a running stream (with the wall off there's nothing to see behind them)
    document.body.classList.remove("idle");
    clearTimeout(idleT);
    if (LANDSCAPE.matches) idleT = setTimeout(() => { if (wall && wall.running) document.body.classList.add("idle"); }, 4000);
  }
  document.addEventListener("pointerdown", wake, { passive: true });

  function applyMode() {
    const p = phone();
    sheets.forEach((d) => (d.open = !p));
    $("scrim").hidden = true;
    pinned = false; clearTimeout(hideT); chatOn(false); setUnread(0); wake();
    fitViewport();
  }
  PORTRAIT.addEventListener("change", applyMode);
  LANDSCAPE.addEventListener("change", applyMode);

  // ---- a gentle ding for new chat while the chat isn't in view ----
  // Two soft sine notes (~150 ms, low gain) from Web Audio: no file, nothing that grabs the media session, so the
  // stream keeps playing. The context is made on the first tap (mobile autoplay rules) and only when dings are on.
  const DING_GAP_MS = 20000, BURST_QUIET_MS = 10000;
  let actx = null, dingOn = true, lastDing = 0, lastOther = 0, dingedBurst = false;
  const dingDbg = { count: 0 };
  window.__wallDing = dingDbg;
  try { dingOn = localStorage.getItem("wall.ding") !== "0"; } catch (e) { /* storage blocked: default on */ }
  function unlockAudio() {
    if (!dingOn) return;
    if (actx) { if (actx.state !== "running") actx.resume().catch(() => {}); return; }
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    try { actx = new AC(); } catch (e) { return; }
    const s = actx.createBufferSource();          // a silent blip inside the gesture unlocks iOS
    s.buffer = actx.createBuffer(1, 1, 22050); s.connect(actx.destination); s.start(0);
  }
  ["pointerdown", "keydown", "touchend"].forEach((ev) => document.addEventListener(ev, unlockAudio, { passive: true }));
  function chime() {
    if (!actx) return;
    const play = () => {
      const t = actx.currentTime + 0.01, out = actx.createGain();
      out.gain.value = 0.05; out.connect(actx.destination);
      [[659.25, 0], [987.77, 0.07]].forEach(([f, d]) => {     // E5 → B5
        const o = actx.createOscillator(), g = actx.createGain();
        o.type = "sine"; o.frequency.value = f;
        g.gain.setValueAtTime(0.0001, t + d);
        g.gain.exponentialRampToValueAtTime(1, t + d + 0.01);
        g.gain.exponentialRampToValueAtTime(0.0001, t + d + 0.08);
        o.connect(g); g.connect(out); o.start(t + d); o.stop(t + d + 0.09);
      });
      dingDbg.count++;
    };
    if (actx.state === "running") play(); else actx.resume().then(play, () => {});
  }
  function chatInView() {
    if (document.hidden || document.fullscreenElement || document.webkitFullscreenElement || video.webkitDisplayingFullscreen) return false;
    if (LANDSCAPE.matches && !document.body.classList.contains("chat-on")) return false;
    if (anySheet()) return false;
    return msgs.scrollHeight - msgs.scrollTop - msgs.clientHeight < 40;
  }
  function incoming(m, seen) {
    if (m.kind === "system" || !me || m.uid === me.id) return;
    const now = Date.now();
    if (now - lastOther >= BURST_QUIET_MS) dingedBurst = false;   // a new burst
    lastOther = now;
    if (LANDSCAPE.matches) { if (!pinned) setUnread(unread + 1); peek(); }
    if (!dingOn || seen || dingedBurst || now - lastDing < DING_GAP_MS) return;
    lastDing = now; dingedBurst = true;
    chime();
  }
  function showDing() {
    $("dingBtn").textContent = dingOn ? "🔔" : "🔕";
    $("dingBtn").setAttribute("aria-pressed", String(dingOn));
    $("dingBtn").title = dingOn ? "Ding for new messages: on" : "Ding for new messages: off";
  }
  $("dingBtn").onclick = () => {
    dingOn = !dingOn;
    try { localStorage.setItem("wall.ding", dingOn ? "1" : "0"); } catch (e) { /* not remembered, still toggled */ }
    showDing(); unlockAudio();
  };
  showDing();

  // ---- votes ----
  function propose(action) { send({ t: "propose", action }); }
  window.wallPropose = propose;   // wall-election.js: a state's local coverage onto a tile

  // Live now: one tap puts that game on the tile picked in the Tile menu (or the next empty one)
  async function loadLive() {
    try {
      const r = await fetch("/api/v1/wall/games", { credentials: "same-origin" });
      renderLive(lastGames = r.ok ? (await r.json()).games || [] : []);
    } catch (e) { /* offline: try again next round */ }
  }
  function renderLive(games) {
    {
      const box = $("liveGames"); box.replaceChildren();
      box.className = games.length ? "live-games" : "muted small";
      if (!games.length) { box.textContent = "no live games right now"; return; }
      const onWall = new Set((wall && wall.running && wall.urls) || []);
      for (const g of games) {
        const b = el("button", "btn", (g.league === "nfl" ? "🏈 " : "🏀 ") + g.label + (onWall.has(g.url) ? " · on the wall" : ""));
        b.disabled = onWall.has(g.url);
        b.title = "Put on tile " + (+$("tileI").value + 1);
        b.onclick = () => propose({ op: "tile", i: pickTile(), url: g.url, label: g.label });
        box.append(b);
      }
    }
  }
  let liveT = null;
  const startLive = () => { if (!liveT) { loadLive(); liveT = setInterval(loadLive, 30000); } };   // only once we're in (no 401s)
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
  const beat = () => { if (!(me && me.public)) send({ t: "hb", hidden: document.hidden }); };
  document.addEventListener("visibilitychange", beat);
  setInterval(beat, 15000);
  setInterval(ping, 15000);
  setInterval(syncTick, 250);

  // ---- boot: the join screen until /me says who you are ----
  function showGate(msg) {
    $("room").hidden = true; $("gate").hidden = false;
    document.body.classList.remove("inroom");
    if (msg) $("gateErr").textContent = msg;
  }
  function showPeek(p) {
    // Logged-out peek: the running wall's name and a head count, nothing else (never the stream before joining).
    const n = (p && p.watching) || 0, name = p && p.name;
    const parts = [];
    if (name) parts.push("Live now: " + name);
    if (n) parts.push(n + (n === 1 ? " person" : " people") + " watching");
    $("peekText").textContent = parts.join(" · ");
    $("gatePeek").classList.toggle("on", !!name);
    $("gatePeek").classList.toggle("empty", !parts.length);   // keeps its space either way: nothing below it jumps
  }
  function nickError(msg) {
    $("nickErr").textContent = msg || "";
    if (msg) $("nick").setAttribute("aria-invalid", "true"); else $("nick").removeAttribute("aria-invalid");
  }
  if (Date.now() < Date.UTC(2026, 10, 5, 12)) {   // US midterms, 3 Nov 2026: say so on the join card
    $("gateTitle").textContent = "Election Night";
    $("gateLede").textContent = "Live map, the news streams, Kalshi odds, chat. Results from 6 PM ET.";
  }
  const qs = new URLSearchParams(location.search), err = qs.get("error"), key = qs.get("k") || "";
  try { const n = localStorage.getItem("wall.nick"); if (n) $("nick").value = n; } catch (e) { /* storage blocked: start empty */ }
  function boot() {
    fetch("/api/v1/wall/me?k=" + encodeURIComponent(key), { credentials: "same-origin" }).then((r) => r.json().then((j) => ({ ok: r.ok, j }))).then(({ ok, j }) => {
      if (!ok) {
        if (j.login) $("loginBtn").href = j.login;
        $("guestForm").hidden = $("gateRoles").hidden = !j.guest_ok;   // demo mode with the keyed link: a nickname is enough
        showPeek(j.peek);
        showGate(err === "link_expired" ? "That link expired — use Get my link again, or log in." : "");
        return;
      }
      if (j.public) {   // the plain page in public mode: live election data only — no stream, no votes, chat read-only
        document.body.classList.add("public");
        $("publicNote").hidden = false;
        $("chatIn").disabled = true;
        const a = el("a", "pub-login", "Member? Log in"); a.href = j.login || "/api/v1/auth/discord/login?next=/wall.html";
        $("navRight").replaceChildren(a);
      } else {
        $("navRight").textContent = j.name + (j.guest ? " (guest)" : "");
        if (j.tier === "member") $("topNav").hidden = false;   // friends and the public see only the page's own name
      }
      window.__wallMe = j;   // wall-bets.js / wall-election.js start from this (a public viewer never calls the Kalshi endpoints)
      window.dispatchEvent(new CustomEvent("wall:me", { detail: j }));
      if (!j.public) startLive();
      $("gate").hidden = true;
      $("room").hidden = false;
      document.body.classList.add("inroom");
      applyMode();
      connect();
    }).catch(() => showGate("Couldn't reach SharpLab."));
  }
  $("nick").oninput = () => nickError("");
  $("guestForm").onsubmit = (e) => {
    e.preventDefault();
    const name = $("nick").value.trim(), btn = $("joinBtn");
    if (btn.disabled) return;
    btn.disabled = true; btn.textContent = "Joining…"; nickError("");
    fetch("/api/v1/wall/guest", { method: "POST", credentials: "same-origin", headers: { "content-type": "application/json" },
                                  body: JSON.stringify({ k: key, name }) })
      .then((r) => r.json().then((j) => ({ ok: r.ok, j, wait: +(r.headers.get("Retry-After") || j.retry_after || 0) })))
      .then(({ ok, j, wait }) => {
        if (!ok && wait > 0) { holdJoin(wait, j.error); return; }
        if (!ok) { nickError(j.error || "Couldn't join."); $("nick").focus(); return; }
        try { localStorage.setItem("wall.nick", name); } catch (e) { /* not remembered next time, still joined */ }
        boot();
      })
      .catch(() => nickError("Couldn't reach SharpLab."))
      .finally(() => { if (!holdT) { btn.disabled = false; btn.textContent = "Join"; } });
  };
  // Too many joins from this network just now: count down on the button, then let them try again.
  let holdT = null;
  function holdJoin(secs, msg) {
    const btn = $("joinBtn"), end = Date.now() + secs * 1000;
    clearInterval(holdT);
    const tick = () => {
      const left = Math.ceil((end - Date.now()) / 1000);
      if (left <= 0) { clearInterval(holdT); holdT = null; btn.disabled = false; btn.textContent = "Join"; nickError(""); return; }
      const t = left >= 60 ? `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}` : `${left} s`;
      btn.disabled = true; btn.textContent = t;
      nickError(`${msg || "Too many joins from this network just now."} Try again in ${t}.`);
    };
    tick(); holdT = setInterval(tick, 1000);
  }
  boot();
})();
