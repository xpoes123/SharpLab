// SharpLab — Unlock (Android pattern) renderer. Registers window.DailyRenderers["unlock"]:
//   mount(boardEl, board, { onSolved, onMove })
//   getMoves()   -> selected dots in order (pass-over auto-selects included)
//   teardown()
//
// Board from the server: { n, edges: [[a, b], ...] }, dots indexed r*n + c, edges are PRIMITIVE
// (adjacent collinear lattice dots). Rules mirror shared/daily_games/unlock.py: you can only land
// on an unused dot; a stroke may cross used dots, and crossing an unused dot selects it. Solved =
// the drawn primitive-edge set equals the target set exactly.
//
// Input: tap/drag dots (a rubber band follows the pointer; hovering previews the stroke, green if
// it fits the pattern, red if not). Keys: arrows move a cursor, Enter/Space picks it, 1-9 pick
// directly on 3×3 (phone layout), Z/Backspace undo, Esc/C clear.
(function () {
  "use strict";

  window.DailyRenderers = window.DailyRenderers || {};

  const S = 72; // px between dots
  const HIT = 0.38 * S; // tap radius
  // Drag radius: a long diagonal stroke (e.g. 1×4) passes 0.24·S from the dots beside it, so a
  // drag only grabs a dot near its centre — otherwise strokes snag neighbours they never meant.
  const DRAG_HIT = 0.2 * S;

  let n = 3, target = new Set(), patternDots = new Set(), svgEl = null, rootEl = null, cbs = {};
  let done = false, dragging = false, cursor = 0, aim = null, pointer = null, keyNav = false;
  let taps = []; // [{dots: [auto..., t]}] — one entry per tap, so Undo removes a whole stroke
  const seq = () => taps.flatMap((t) => t.dots);
  const last = () => { const s = seq(); return s.length ? s[s.length - 1] : null; };

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

  const strokeEdges = (a, b) => { const p = step(a, b), out = []; for (let j = 1; j < p.length; j++) out.push(key(p[j - 1], p[j])); return out; };

  function drawnEdges() {
    const s = seq(), out = new Set();
    for (let i = 1; i < s.length; i++) for (const e of strokeEdges(s[i - 1], s[i])) out.add(e);
    return out;
  }

  function select(t) {
    if (done || t == null) return;
    const s = seq();
    if (s.includes(t)) return; // can't land on a used dot (crossing it is fine)
    const dots = [];
    if (s.length) for (const p of step(s[s.length - 1], t).slice(1, -1)) if (!s.includes(p)) dots.push(p);
    dots.push(t);
    taps.push({ dots });
    cursor = t;
    if (navigator.vibrate) try { navigator.vibrate(8); } catch (_) {}
    afterChange();
  }

  function undo() {
    if (done || !taps.length) return;
    taps.pop();
    const l = last();
    if (l != null) cursor = l;
    afterChange();
  }

  function clear() {
    if (done || !taps.length) return;
    taps = [];
    afterChange();
  }

  function afterChange() {
    draw();
    if (cbs.onMove) cbs.onMove(seq().length);
    const drawn = drawnEdges();
    if (drawn.size === target.size && [...drawn].every((e) => target.has(e))) {
      done = true;
      aim = null;
      draw();
      if (cbs.onSolved) cbs.onSolved();
    }
  }

  function line(a, b, cls) {
    const [x1, y1] = xy(a), [x2, y2] = xy(b);
    return `<line class="${cls}" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/>`;
  }

  function draw() {
    if (!svgEl) return;
    const s = seq(), drawn = drawnEdges(), order = new Map(s.map((d, i) => [d, i + 1])), l = last();
    let h = "";
    for (const e of target) { const [a, b] = e.split("-").map(Number); h += line(a, b, "ul-target"); }
    for (const e of drawn) {
      const [a, b] = e.split("-").map(Number);
      h += line(a, b, target.has(e) ? "ul-drawn" : "ul-wrong");
    }
    // Stroke preview toward the aimed dot (hover or keyboard cursor): would it fit the pattern?
    if (!done && aim != null && l != null && !order.has(aim)) {
      const fits = strokeEdges(l, aim).every((e) => target.has(e) && !drawn.has(e));
      h += line(l, aim, fits ? "ul-prev ok" : "ul-prev bad");
    }
    // Rubber band from the last dot to the finger while dragging.
    if (!done && dragging && pointer && l != null) {
      const [x1, y1] = xy(l);
      h += `<line class="ul-band" x1="${x1}" y1="${y1}" x2="${pointer[0]}" y2="${pointer[1]}"/>`;
    }
    for (let d = 0; d < n * n; d++) {
      const [x, y] = xy(d), o = order.get(d);
      const cls = ["ul-dot", o && "on", d === l && "last", patternDots.has(d) && !o && "pat"].filter(Boolean).join(" ");
      h += `<circle class="${cls}" cx="${x}" cy="${y}" r="${o ? 13 : patternDots.has(d) ? 9 : 6}"/>`;
      if (o) h += `<text class="ul-num" x="${x}" y="${y + 4}">${o}</text>`;
    }
    if (keyNav && !done) { const [x, y] = xy(cursor); h += `<circle class="ul-cursor" cx="${x}" cy="${y}" r="22"/>`; }
    svgEl.innerHTML = h;
    svgEl.classList.toggle("solved", done);
    const prog = rootEl && rootEl.querySelector("[data-ul-prog]");
    if (prog) {
      const good = [...drawn].filter((e) => target.has(e)).length, bad = drawn.size - good;
      prog.innerHTML = `${good}/${target.size} lines${bad ? ` · <span class="ul-badtxt">${bad} extra</span>` : ""}`;
    }
  }

  function svgPoint(ev) {
    const r = svgEl.getBoundingClientRect(), k = (n * S) / r.width;
    return [(ev.clientX - r.left) * k, (ev.clientY - r.top) * k];
  }

  function dotAt(ev, radius) {
    const [x, y] = svgPoint(ev);
    const c = Math.round((x - S / 2) / S), rr = Math.round((y - S / 2) / S);
    if (c < 0 || rr < 0 || c >= n || rr >= n) return null;
    const d = rr * n + c, [dx, dy] = xy(d);
    return Math.hypot(x - dx, y - dy) <= radius ? d : null;
  }

  function onDown(ev) {
    if (done) return;
    keyNav = false;
    dragging = true;
    pointer = svgPoint(ev);
    const d = dotAt(ev, HIT);
    if (d != null) select(d); else draw();
  }
  function onMoveEv(ev) {
    if (!svgEl || done) return;
    pointer = svgPoint(ev);
    if (dragging) { const d = dotAt(ev, DRAG_HIT); if (d != null) select(d); }
    const a = ev.pointerType === "mouse" ? dotAt(ev, S / 2) : null; // hover preview is mouse-only
    if (a !== aim || dragging) { aim = a; draw(); }
  }
  function onUp() { if (dragging) { dragging = false; pointer = null; draw(); } }
  function onLeave() { if (!dragging && aim != null) { aim = null; draw(); } }

  function onKey(ev) {
    if (!svgEl || done || ev.altKey || ev.metaKey) return;
    const tag = (ev.target && ev.target.tagName) || "";
    if (tag === "INPUT" || tag === "TEXTAREA" || (ev.target && ev.target.isContentEditable)) return;
    const k = ev.key;
    const r = Math.floor(cursor / n), c = cursor % n;
    const moves = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] };
    let handled = true;
    if (moves[k]) {
      const [dr, dc] = moves[k];
      cursor = Math.min(n - 1, Math.max(0, r + dr)) * n + Math.min(n - 1, Math.max(0, c + dc));
      keyNav = true; aim = cursor; draw();
    } else if (k === "Enter" || k === " ") {
      keyNav = true; select(cursor); aim = null; draw();
    } else if (n === 3 && /^[1-9]$/.test(k) && !ev.ctrlKey) {
      select(Number(k) - 1);
    } else if (k === "Backspace" || k === "z" || k === "Z" || k === "u") {
      undo();
    } else if (k === "Escape" || k === "c" || k === "C") {
      clear();
    } else handled = false;
    if (handled) ev.preventDefault();
  }

  window.DailyRenderers["unlock"] = {
    mount(boardEl, board, callbacks) {
      cbs = callbacks || {};
      n = Math.max(2, Math.min(8, Number(board.n) | 0)) || 3;
      target = new Set((board.edges || []).map(([a, b]) => key(Number(a) | 0, Number(b) | 0)));
      patternDots = new Set([...target].flatMap((e) => e.split("-").map(Number)));
      taps = []; done = false; dragging = false; aim = null; pointer = null; keyNav = false;
      cursor = Math.min(...patternDots);
      const keys = n === 3 ? "<kbd>1</kbd>–<kbd>9</kbd> or " : "";
      boardEl.innerHTML = `
        <style>
          .ul-wrap { display:flex; flex-direction:column; align-items:center; gap:10px; width:100%; }
          .ul-board { width:100%; max-width:${Math.min(400, n * 88)}px; touch-action:none; user-select:none; -webkit-user-select:none; cursor:pointer; }
          .ul-target { stroke:var(--line-2, #3b4261); stroke-width:14; stroke-linecap:round; }
          .ul-drawn { stroke:var(--accent); stroke-width:6; stroke-linecap:round; }
          .ul-wrong { stroke:var(--bad, #f7768e); stroke-width:6; stroke-linecap:round; stroke-dasharray:6 6; }
          .ul-prev { stroke-width:4; stroke-linecap:round; stroke-dasharray:3 7; opacity:.85; }
          .ul-prev.ok { stroke:var(--ok, #9ece6a); }
          .ul-prev.bad { stroke:var(--bad, #f7768e); }
          .ul-band { stroke:var(--accent); stroke-width:4; stroke-linecap:round; opacity:.55; }
          .ul-dot { fill:var(--line-2, #3b4261); }
          .ul-dot.pat { fill:var(--muted); }
          .ul-dot.on { fill:var(--accent); }
          .ul-dot.last { stroke:var(--text); stroke-width:3; }
          .ul-cursor { fill:none; stroke:var(--info, #7dcfff); stroke-width:2.5; stroke-dasharray:4 4; }
          .ul-num { font:700 12px var(--font-mono, monospace); fill:var(--bg, #0b0e1a); text-anchor:middle; pointer-events:none; }
          .ul-board.solved .ul-drawn { stroke:var(--ok, #9ece6a); }
          .ul-bar .btn { white-space:nowrap; }
          .ul-bar { display:flex; gap:8px; align-items:center; justify-content:center; flex-wrap:wrap; }
          .ul-prog { font:600 13px var(--font-mono, monospace); color:var(--muted); min-width:9ch; text-align:center; }
          .ul-badtxt { color:var(--bad, #f7768e); }
          .ul-keys { font-size:12px; color:var(--muted); text-align:center; }
          .ul-keys kbd { font:600 11px var(--font-mono, monospace); border:1px solid var(--line); border-bottom-width:2px; border-radius:4px; padding:0 4px; }
          @media (hover: none) { .ul-keys { display:none; } }
        </style>
        <div class="ul-wrap">
          <svg class="ul-board" viewBox="0 0 ${n * S} ${n * S}"></svg>
          <div class="ul-bar">
            <button class="btn ghost" type="button" data-ul-undo>↶ Undo</button>
            <span class="ul-prog" data-ul-prog></span>
            <button class="btn ghost" type="button" data-ul-clear>✕ Clear</button>
          </div>
          <div class="ul-keys">${keys}<kbd>←↑↓→</kbd> + <kbd>Enter</kbd> pick · <kbd>Z</kbd> undo · <kbd>Esc</kbd> clear</div>
        </div>`;
      rootEl = boardEl;
      svgEl = boardEl.querySelector("svg");
      svgEl.addEventListener("pointerdown", onDown);
      svgEl.addEventListener("pointerleave", onLeave);
      window.addEventListener("pointermove", onMoveEv);
      window.addEventListener("pointerup", onUp);
      window.addEventListener("pointercancel", onUp);
      window.addEventListener("keydown", onKey);
      boardEl.querySelector("[data-ul-undo]").onclick = undo;
      boardEl.querySelector("[data-ul-clear]").onclick = clear;
      draw();
      if (cbs.onMove) cbs.onMove(0);
    },
    getMoves() {
      return seq();
    },
    teardown() {
      if (svgEl) {
        svgEl.removeEventListener("pointerdown", onDown);
        svgEl.removeEventListener("pointerleave", onLeave);
      }
      window.removeEventListener("pointermove", onMoveEv);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      window.removeEventListener("keydown", onKey);
      svgEl = null; rootEl = null; taps = []; done = true;
    },
  };
})();
