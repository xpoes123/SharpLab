// SharpLab — Unlock (Android pattern) renderer. Registers window.DailyRenderers["unlock"]:
//   mount(boardEl, board, { onSolved, onMove })
//   getMoves()   -> selected dots in order (pass-over auto-selects included)
//   teardown()
//
// Board from the server: { n, edges: [[a, b], ...] }, dots indexed r*n + c, edges are PRIMITIVE
// (adjacent collinear lattice dots). Rules mirror shared/daily_games/unlock.py: you can only land
// on an unused dot; a stroke may cross used dots, and crossing an unused dot selects it. Solved =
// the drawn primitive-edge set equals the target set exactly.
(function () {
  "use strict";

  window.DailyRenderers = window.DailyRenderers || {};

  const S = 72; // px between dots
  const HIT = 0.38 * S; // pointer radius that "touches" a dot

  let n = 3, target = new Set(), svgEl = null, cbs = {}, done = false, dragging = false;
  let taps = []; // [{dots: [auto..., t]}] — one entry per tap, so Undo removes a whole stroke
  const seq = () => taps.flatMap((t) => t.dots);

  const gcd = (a, b) => (b ? gcd(b, a % b) : a);
  const key = (a, b) => (a < b ? `${a}-${b}` : `${b}-${a}`);
  const xy = (d) => [S / 2 + (d % n) * S, S / 2 + Math.floor(d / n) * S];

  function step(a, b) {
    const r0 = Math.floor(a / n), c0 = a % n, r1 = Math.floor(b / n), c1 = b % n;
    const k = gcd(Math.abs(r1 - r0), Math.abs(c1 - c0));
    const dr = (r1 - r0) / k, dc = (c1 - c0) / k, out = [];
    for (let i = 0; i <= k; i++) out.push((r0 + i * dr) * n + (c0 + i * dc));
    return out;
  }

  function drawnEdges() {
    const s = seq(), out = new Set();
    for (let i = 1; i < s.length; i++) {
      const p = step(s[i - 1], s[i]);
      for (let j = 1; j < p.length; j++) out.add(key(p[j - 1], p[j]));
    }
    return out;
  }

  function select(t) {
    if (done) return;
    const s = seq();
    if (s.includes(t)) return; // can't land on a used dot (crossing it is fine)
    const dots = [];
    if (s.length) for (const p of step(s[s.length - 1], t).slice(1, -1)) if (!s.includes(p)) dots.push(p);
    dots.push(t);
    taps.push({ dots });
    afterChange();
  }

  function undo() {
    if (done || !taps.length) return;
    taps.pop();
    afterChange();
  }

  function afterChange() {
    draw();
    if (cbs.onMove) cbs.onMove(seq().length);
    const drawn = drawnEdges();
    if (drawn.size === target.size && [...drawn].every((e) => target.has(e))) {
      done = true;
      draw();
      if (cbs.onSolved) cbs.onSolved();
    }
  }

  function line(a, b, cls) {
    const [x1, y1] = xy(a), [x2, y2] = xy(b);
    return `<line class="${cls}" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/>`;
  }

  function draw() {
    const s = seq(), drawn = drawnEdges(), order = new Map(s.map((d, i) => [d, i + 1]));
    let h = "";
    for (const e of target) { const [a, b] = e.split("-").map(Number); h += line(a, b, "ul-target"); }
    for (const e of drawn) {
      const [a, b] = e.split("-").map(Number);
      h += line(a, b, target.has(e) ? "ul-drawn" : "ul-wrong");
    }
    for (let d = 0; d < n * n; d++) {
      const [x, y] = xy(d), o = order.get(d), last = d === s[s.length - 1];
      h += `<circle class="ul-dot${o ? " on" : ""}${last ? " last" : ""}" cx="${x}" cy="${y}" r="${o ? 13 : 8}"/>`;
      if (o) h += `<text class="ul-num" x="${x}" y="${y + 4}">${o}</text>`;
    }
    svgEl.innerHTML = h;
    svgEl.classList.toggle("solved", done);
  }

  function dotAt(ev) {
    const r = svgEl.getBoundingClientRect(), k = (n * S) / r.width;
    const x = (ev.clientX - r.left) * k, y = (ev.clientY - r.top) * k;
    const c = Math.round((x - S / 2) / S), rr = Math.round((y - S / 2) / S);
    if (c < 0 || rr < 0 || c >= n || rr >= n) return null;
    const d = rr * n + c, [dx, dy] = xy(d);
    return Math.hypot(x - dx, y - dy) <= HIT ? d : null;
  }

  function onDown(ev) { dragging = true; const d = dotAt(ev); if (d != null) select(d); }
  function onMoveEv(ev) { if (dragging) { const d = dotAt(ev); if (d != null) select(d); } }
  function onUp() { dragging = false; }

  window.DailyRenderers["unlock"] = {
    mount(boardEl, board, callbacks) {
      cbs = callbacks || {};
      n = Math.max(2, Math.min(8, Number(board.n) | 0)) || 3;
      target = new Set((board.edges || []).map(([a, b]) => key(Number(a) | 0, Number(b) | 0)));
      taps = []; done = false; dragging = false;
      boardEl.innerHTML = `
        <style>
          .ul-wrap { display:flex; flex-direction:column; align-items:center; gap:10px; width:100%; }
          .ul-board { width:100%; max-width:${Math.min(380, n * 84)}px; touch-action:none; user-select:none; }
          .ul-target { stroke:var(--line-2, #3b4261); stroke-width:14; stroke-linecap:round; }
          .ul-drawn { stroke:var(--accent); stroke-width:6; stroke-linecap:round; }
          .ul-wrong { stroke:var(--bad, #f7768e); stroke-width:6; stroke-linecap:round; stroke-dasharray:6 6; }
          .ul-dot { fill:var(--muted); }
          .ul-dot.on { fill:var(--accent); }
          .ul-dot.last { stroke:var(--text); stroke-width:3; }
          .ul-num { font:700 11px var(--font-mono, monospace); fill:var(--bg, #0b0e1a); text-anchor:middle; pointer-events:none; }
          .ul-board.solved .ul-drawn { stroke:var(--ok, #9ece6a); }
        </style>
        <div class="ul-wrap">
          <svg class="ul-board" viewBox="0 0 ${n * S} ${n * S}"></svg>
          <button class="btn ghost" type="button" data-ul-undo>↶ Undo</button>
        </div>`;
      svgEl = boardEl.querySelector("svg");
      svgEl.addEventListener("pointerdown", onDown);
      window.addEventListener("pointermove", onMoveEv);
      window.addEventListener("pointerup", onUp);
      window.addEventListener("pointercancel", onUp);
      boardEl.querySelector("[data-ul-undo]").onclick = undo;
      draw();
      if (cbs.onMove) cbs.onMove(0);
    },
    getMoves() {
      return seq();
    },
    teardown() {
      if (svgEl) svgEl.removeEventListener("pointerdown", onDown);
      window.removeEventListener("pointermove", onMoveEv);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      svgEl = null; taps = []; done = true;
    },
  };
})();
