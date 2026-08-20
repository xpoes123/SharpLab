// SharpLab — Rush Hour board renderer for the Daily page. Registers itself on
// window.DailyRenderers["rushhour"] and implements the daily renderer interface:
//   mount(boardEl, board, { onSolved, onEscaped, onMove })
//   getMoves()   -> ordered [[car_id, delta], ...] (delta = signed cells slid)
//   teardown()
//
// Board schema from /api/v1/daily/start:
//   { size: 6, exit_row: 2, target: "X",
//     cars: [{ id, r, c, len, orient: "h" | "v" }, ...] }
// A horizontal car occupies (r, c .. c+len-1); a vertical car (r .. r+len-1, c).
// The exit is a gap on the RIGHT edge of `exit_row`. Solved = the target car's
// right end reaches the last column (X.c + X.len - 1 === size - 1). A slide of
// any distance in one gesture counts as exactly ONE move (matches the server),
// recorded as [car_id, new_var - old_var] where var = column for horizontal
// cars, row for vertical cars.
(function () {
  "use strict";

  window.DailyRenderers = window.DailyRenderers || {};

  const CELL = 56; // px per grid cell
  const PAD = 5; // inset of a car rect inside its cells
  const ARROW = 26; // px of exit gutter drawn to the right of the board

  // Tokyo-Night-friendly palette for the non-target cars (the target is red).
  const PALETTE = [
    "#7aa2f7", // blue
    "#9ece6a", // green
    "#bb9af7", // purple
    "#7dcfff", // cyan
    "#ff9e64", // orange
    "#e0af68", // yellow
    "#73daca", // teal
    "#b4f9f8", // light cyan
    "#c0caf5", // foreground blue
    "#ff007c", // hot pink
  ];

  // ── Per-mount state ──
  let svgEl = null;
  let size = 6;
  let exitRow = 2;
  let target = "X";
  let cars = []; // live copy: {id, r, c, len, orient}
  let colorOf = {}; // id -> fill
  let moves = []; // [[id, delta], ...]
  let cbs = {};
  let done = false;
  let drag = null; // active drag context

  const carVar = (car) => (car.orient === "h" ? car.c : car.r); // the sliding axis coord

  // Attribute-safe escape for the (server-controlled) car id.
  const escAttr = (s) =>
    String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // Occupancy grid of car ids (or null), optionally excluding one car.
  function occupancy(exceptId) {
    const grid = [];
    for (let r = 0; r < size; r++) grid.push(new Array(size).fill(null));
    for (const car of cars) {
      if (car.id === exceptId) continue;
      for (let i = 0; i < car.len; i++) {
        const rr = car.orient === "v" ? car.r + i : car.r;
        const cc = car.orient === "h" ? car.c + i : car.c;
        grid[rr][cc] = car.id;
      }
    }
    return grid;
  }

  // Contiguous legal [min, max] leftmost/topmost coord reachable by sliding
  // `car` along its axis (can't pass through another car or leave the grid).
  function slideRange(car) {
    const grid = occupancy(car.id);
    let lo = carVar(car);
    let hi = carVar(car);
    if (car.orient === "h") {
      while (lo - 1 >= 0 && grid[car.r][lo - 1] === null) lo--;
      while (hi + car.len <= size - 1 && grid[car.r][hi + car.len] === null) hi++;
    } else {
      while (lo - 1 >= 0 && grid[lo - 1][car.c] === null) lo--;
      while (hi + car.len <= size - 1 && grid[hi + car.len][car.c] === null) hi++;
    }
    return [lo, hi];
  }

  // ── SVG geometry ──
  const boardW = () => size * CELL;
  const boardH = () => size * CELL;

  function carRectAttrs(car) {
    const x = car.c * CELL + PAD;
    const y = car.r * CELL + PAD;
    const w = (car.orient === "h" ? car.len : 1) * CELL - 2 * PAD;
    const h = (car.orient === "v" ? car.len : 1) * CELL - 2 * PAD;
    return { x, y, w, h };
  }

  function draw() {
    const W = boardW() + ARROW;
    const H = boardH();
    svgEl.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svgEl.setAttribute("width", W);
    svgEl.setAttribute("xmlns", "http://www.w3.org/2000/svg");

    let s = "";
    // Board backing + grid lines.
    s += `<rect x="0" y="0" width="${boardW()}" height="${H}" rx="12" fill="var(--panel2)" stroke="var(--line)" stroke-width="1.5"/>`;
    for (let i = 1; i < size; i++) {
      const p = i * CELL;
      s += `<line x1="${p}" y1="0" x2="${p}" y2="${H}" stroke="var(--line)" stroke-width="1" opacity="0.5"/>`;
      s += `<line x1="0" y1="${p}" x2="${boardW()}" y2="${p}" stroke="var(--line)" stroke-width="1" opacity="0.5"/>`;
    }
    // Exit gap + arrow on the right edge of exit_row.
    const ey = exitRow * CELL;
    s += `<rect x="${boardW() - 2}" y="${ey + 3}" width="4" height="${CELL - 6}" fill="var(--panel2)"/>`;
    s += `<text x="${boardW() + ARROW / 2}" y="${ey + CELL * 0.66}" font-size="${CELL * 0.6}" text-anchor="middle" fill="var(--green)">▶</text>`;

    // Cars.
    for (const car of cars) {
      const { x, y, w, h } = carRectAttrs(car);
      const fill = colorOf[car.id];
      const isTarget = car.id === target;
      const rad = Math.min(w, h) * 0.28;
      s += `<rect class="rh-car${isTarget ? " target" : ""}" data-id="${escAttr(car.id)}" ` +
        `x="${x}" y="${y}" width="${w}" height="${h}" rx="${rad}" ry="${rad}" ` +
        `fill="${fill}" stroke="rgba(0,0,0,.35)" stroke-width="1.5"/>`;
    }
    svgEl.innerHTML = s;
  }

  // Move only the active car's rect during a drag (no full redraw).
  function moveDragRect(px) {
    const rect = drag && drag.rect;
    if (!rect) return;
    if (drag.car.orient === "h") rect.setAttribute("x", px + PAD);
    else rect.setAttribute("y", px + PAD);
  }

  // ── Pointer drag ──
  function pointerCoord(e) {
    // Map client px to SVG px along the drag axis.
    const rect = svgEl.getBoundingClientRect();
    const scaleX = (boardW() + ARROW) / rect.width;
    const scaleY = boardH() / rect.height;
    return drag.car.orient === "h"
      ? (e.clientX - rect.left) * scaleX
      : (e.clientY - rect.top) * scaleY;
  }

  function onPointerDown(e) {
    if (done) return;
    const t = e.target.closest("rect[data-id]");
    if (!t) return;
    const id = t.getAttribute("data-id");
    const car = cars.find((c) => c.id === id);
    if (!car) return;
    const [lo, hi] = slideRange(car);
    drag = {
      car,
      rect: t,
      startVar: carVar(car), // axis coord before this gesture (for delta)
      minPx: lo * CELL,
      maxPx: hi * CELL,
      curPx: carVar(car) * CELL,
    };
    drag.pointerStart = pointerCoord(e);
    drag.originPx = drag.car[car.orient === "h" ? "c" : "r"] * CELL;
    try { svgEl.setPointerCapture(e.pointerId); } catch (_) {}
    e.preventDefault();
  }

  function onPointerMove(e) {
    if (!drag) return;
    const delta = pointerCoord(e) - drag.pointerStart;
    let px = drag.originPx + delta;
    px = Math.max(drag.minPx, Math.min(drag.maxPx, px));
    drag.curPx = px;
    moveDragRect(px);
    e.preventDefault();
  }

  function onPointerUp(e) {
    if (!drag) return;
    const car = drag.car;
    // Snap to nearest legal cell.
    let cell = Math.round(drag.curPx / CELL);
    const minCell = Math.round(drag.minPx / CELL);
    const maxCell = Math.round(drag.maxPx / CELL);
    cell = Math.max(minCell, Math.min(maxCell, cell));
    if (car.orient === "h") car.c = cell;
    else car.r = cell;

    const delta = cell - drag.startVar;
    drag = null;
    draw(); // snap the rect to its final grid cell

    if (delta !== 0) {
      moves.push([car.id, delta]);
      if (cbs.onMove) cbs.onMove(moves.length);
    }
    if (isSolved()) {
      done = true;
      if (cbs.onSolved) cbs.onSolved();
    }
  }

  function isSolved() {
    const x = cars.find((c) => c.id === target);
    return !!x && x.orient === "h" && x.c + x.len - 1 === size - 1;
  }

  function wire() {
    svgEl.style.touchAction = "none";
    svgEl.addEventListener("pointerdown", onPointerDown);
    svgEl.addEventListener("pointermove", onPointerMove);
    svgEl.addEventListener("pointerup", onPointerUp);
    svgEl.addEventListener("pointercancel", onPointerUp);
  }

  window.DailyRenderers["rushhour"] = {
    mount(boardEl, board, callbacks) {
      cbs = callbacks || {};
      moves = [];
      done = false;
      drag = null;
      size = board.size || 6;
      exitRow = board.exit_row != null ? board.exit_row : 2;
      target = String(board.target != null ? board.target : "X");
      // Deep-copy the cars so internal state is fully reset on every (re)mount.
      cars = (board.cars || []).map((c) => ({
        id: String(c.id),
        r: c.r,
        c: c.c,
        len: c.len,
        orient: c.orient,
      }));
      // Assign stable colours: red for the target, palette for the rest.
      colorOf = {};
      let i = 0;
      for (const car of cars) {
        if (car.id === target) colorOf[car.id] = "var(--red)";
        else colorOf[car.id] = PALETTE[i++ % PALETTE.length];
      }

      boardEl.innerHTML = `<svg class="rh-board"></svg>`;
      svgEl = boardEl.querySelector("svg");
      draw();
      wire();
      if (cbs.onMove) cbs.onMove(0);
    },
    getMoves() {
      return moves;
    },
    teardown() {
      if (svgEl) {
        svgEl.removeEventListener("pointerdown", onPointerDown);
        svgEl.removeEventListener("pointermove", onPointerMove);
        svgEl.removeEventListener("pointerup", onPointerUp);
        svgEl.removeEventListener("pointercancel", onPointerUp);
      }
      svgEl = null;
      cars = [];
      moves = [];
      drag = null;
      done = true;
    },
  };
})();
