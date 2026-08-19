// SharpLab — Trap the Pig board engine + renderer (shared by the free-play
// arcade page and the Daily page). The pig AI here is copied VERBATIM from the
// prototype and MUST stay bit-identical to the server (shared/daily_games/
// trappig.py): the same odd/even-row neighbour delta arrays, the same
// multi-source BFS, and the same tie-break (first neighbour in order with a
// strictly-smaller distance wins). If this diverges from the server, a
// client-computed trap would be rejected by /api/v1/daily/submit.
//
// Exposed via the global `TrapPigBoard`:
//   neighbours(r, c, rows, cols)        -> [[r,c], ...] in-bounds neighbours
//   isBorder(r, c, rows, cols)          -> bool
//   distField(fences, rows, cols)       -> { "r,c": dist }  (fences: Set of "r,c")
//   pigStep(pig, fences, rows, cols)    -> [r,c] next cell, or null if trapped
//   renderInto(svgEl, state, onPlace)   -> draw the board; state = {rows, cols,
//                                          pig:[r,c], fences:Set|[[r,c],...]}
//   key(r, c)                           -> "r,c"
//   toKeySet(fences)                    -> Set of "r,c" from a Set or [[r,c],...]
(function () {
  "use strict";

  const SIZE = 17; // hex circumradius (px)
  const key = (r, c) => r + "," + c;

  // ── VERBATIM from prototype / server: offset (odd-r, pointy-top) neighbours ──
  // odd rows : [[0,1],[0,-1],[-1,0],[-1,1],[1,0],[1,1]]
  // even rows: [[0,1],[0,-1],[-1,-1],[-1,0],[1,-1],[1,0]]
  function neighbours(r, c, rows, cols) {
    const odd = r & 1;
    const d = odd
      ? [[0, 1], [0, -1], [-1, 0], [-1, 1], [1, 0], [1, 1]]
      : [[0, 1], [0, -1], [-1, -1], [-1, 0], [1, -1], [1, 0]];
    return d
      .map(([dr, dc]) => [r + dr, c + dc])
      .filter(([nr, nc]) => nr >= 0 && nr < rows && nc >= 0 && nc < cols);
  }

  const isBorder = (r, c, rows, cols) =>
    r === 0 || c === 0 || r === rows - 1 || c === cols - 1;

  // ── VERBATIM: multi-source BFS from open border cells → distance-to-escape ──
  // `fences` is a Set of "r,c" keys.
  function distField(fences, rows, cols) {
    const dist = {};
    const q = [];
    for (let r = 0; r < rows; r++)
      for (let c = 0; c < cols; c++) {
        if (isBorder(r, c, rows, cols) && !fences.has(key(r, c))) {
          dist[key(r, c)] = 0;
          q.push([r, c]);
        }
      }
    for (let i = 0; i < q.length; i++) {
      const [r, c] = q[i];
      const dc = dist[key(r, c)];
      for (const [nr, nc] of neighbours(r, c, rows, cols)) {
        const k = key(nr, nc);
        if (fences.has(k) || k in dist) continue;
        dist[k] = dc + 1;
        q.push([nr, nc]);
      }
    }
    return dist;
  }

  // ── VERBATIM tie-break: pig steps to the open neighbour nearest an escape;
  // the FIRST neighbour (in the fixed delta order) with a strictly-smaller
  // distance wins ties. Returns next cell, or null if trapped (no path out).
  // Mirrors the server's _pig_step, including the "already on a border =
  // escaping" early return. `fences` is a Set of "r,c" keys.
  function pigStep(pig, fences, rows, cols) {
    if (isBorder(pig[0], pig[1], rows, cols)) return pig; // already escaping
    const dist = distField(fences, rows, cols);
    let best = null,
      bd = Infinity;
    for (const [nr, nc] of neighbours(pig[0], pig[1], rows, cols)) {
      const k = key(nr, nc);
      if (fences.has(k)) continue;
      const d = k in dist ? dist[k] : Infinity;
      if (d < bd) {
        bd = d;
        best = [nr, nc];
      }
    }
    if (best === null || bd === Infinity) return null; // no path out → trapped
    return best;
  }

  // Accept either a Set of "r,c" keys or an array of [r,c] pairs.
  function toKeySet(fences) {
    if (fences instanceof Set) return fences;
    const s = new Set();
    if (fences) for (const [r, c] of fences) s.add(key(r, c));
    return s;
  }

  // ── Renderer — Tokyo-Night SVG hex board, copied from the prototype ──
  const W = Math.sqrt(3) * SIZE,
    H = 2 * SIZE,
    VS = 1.5 * SIZE;
  const px = (r, c) => ({ x: W * (c + 0.5 * (r & 1)) + W / 2 + 2, y: VS * r + SIZE + 2 });
  function hexPts(cx, cy) {
    let p = "";
    for (let i = 0; i < 6; i++) {
      const a = (Math.PI / 180) * (60 * i - 90);
      p += `${(cx + SIZE * Math.cos(a)).toFixed(1)},${(cy + SIZE * Math.sin(a)).toFixed(1)} `;
    }
    return p.trim();
  }

  function renderInto(svgEl, state, onPlace) {
    const rows = state.rows,
      cols = state.cols;
    const fences = toKeySet(state.fences);
    const pig = state.pig;
    const width = W * (cols + 0.5) + 4,
      height = VS * (rows - 1) + H + 4;
    svgEl.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svgEl.setAttribute("width", width);
    svgEl.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    let s = "";
    for (let r = 0; r < rows; r++)
      for (let c = 0; c < cols; c++) {
        const { x, y } = px(r, c);
        const k = key(r, c);
        const isPig = pig[0] === r && pig[1] === c;
        const isF = fences.has(k);
        const fill = isF ? "var(--fence)" : isBorder(r, c, rows, cols) ? "#2c3350" : "var(--open)";
        const cls = !isF && !isPig ? "hex open" : "hex";
        s += `<polygon class="${cls}" points="${hexPts(x, y)}" fill="${fill}" stroke="${
          isF ? "var(--fence2)" : "var(--line)"
        }" stroke-width="1.5" data-r="${r}" data-c="${c}"/>`;
        if (isPig)
          s += `<text x="${x}" y="${y + SIZE * 0.55}" font-size="${
            SIZE * 1.3
          }" text-anchor="middle">🐷</text>`;
      }
    svgEl.innerHTML = s;

    // Wire the click handler ONCE; re-renders just swap the innerHTML and the
    // stored callback so we never stack listeners.
    if (onPlace) svgEl.__onPlace = onPlace;
    if (!svgEl.__wired) {
      svgEl.__wired = true;
      svgEl.addEventListener("click", (e) => {
        const t = e.target.closest("polygon");
        if (!t) return;
        const cb = svgEl.__onPlace;
        if (cb) cb(+t.dataset.r, +t.dataset.c);
      });
    }
  }

  window.TrapPigBoard = {
    SIZE,
    key,
    toKeySet,
    neighbours,
    isBorder,
    distField,
    pigStep,
    renderInto,
  };
})();
