// SharpLab — Windmill board renderer (shared by the Daily page and the free-play
// /windmill page). Self-registers on window.DailyRenderers["windmill"] and implements
// the daily renderer interface: mount(boardEl, board, {onSolved,onMove}), getMoves()
// -> placements [[x,y,w,h],...], teardown(). Extracted verbatim from daily.js.

// ─────────────────────────────────────────────────────────────────────────────
// Windmill renderer — offline tile-placement puzzle. Place every rectangular
// tile from the bag on the n×n grid so exactly one square in each row AND each
// column is left empty (tiles rotate). Fully client-built; getMoves() hands the
// server the final placements [[x,y,w,h], ...] (x=col, y=row) to validate.
// ─────────────────────────────────────────────────────────────────────────────
(function () {
  window.DailyRenderers = window.DailyRenderers || {};
  const TILE_COLORS = [
    "#4f8cff", "#ff6b6b", "#ffd166", "#06d6a0", "#c77dff", "#ff9f45",
    "#4cc9f0", "#f15bb5", "#80ed99", "#e07a5f", "#9b5de5", "#00bbf9",
  ];
  let cbs = {};
  let n = 0;
  let bag = [];          // {id, w, h, color}
  let placed = {};       // id -> {x, y, w, h}
  let selId = null;      // selected tile id (from tray or picked-up)
  let selRot = false;    // rotation of the current selection
  let root = null, done = false;

  const eff = (t, rot) => (rot ? { w: t.h, h: t.w } : { w: t.w, h: t.h });
  const tileById = (id) => bag.find((t) => t.id === id);

  function grid() {
    // occupancy → tile id or null
    const g = Array.from({ length: n }, () => Array(n).fill(null));
    for (const [id, p] of Object.entries(placed)) {
      for (let y = p.y; y < p.y + p.h; y++)
        for (let x = p.x; x < p.x + p.w; x++) g[y][x] = id;
    }
    return g;
  }

  function fits(p, ignoreId) {
    if (p.x < 0 || p.y < 0 || p.x + p.w > n || p.y + p.h > n) return false;
    const g = grid();
    for (let y = p.y; y < p.y + p.h; y++)
      for (let x = p.x; x < p.x + p.w; x++)
        if (g[y][x] && g[y][x] !== ignoreId) return false;
    return true;
  }

  function lineStatus() {
    const g = grid();
    const rowEmpty = g.map((row) => row.filter((c) => !c).length);
    const colEmpty = Array.from({ length: n }, (_, x) => g.filter((row) => !row[x]).length);
    return { rowEmpty, colEmpty };
  }

  function isWin() {
    if (Object.keys(placed).length !== bag.length) return false;
    const { rowEmpty, colEmpty } = lineStatus();
    return rowEmpty.every((e) => e === 1) && colEmpty.every((e) => e === 1);
  }

  function draw() {
    const cell = Math.max(26, Math.min(56, Math.floor(420 / n)));
    const { rowEmpty, colEmpty } = lineStatus();
    const g = grid();

    // column indicators
    let colBar = '<div class="wm-colbar" style="margin-left:' + (cell) + 'px">';
    for (let x = 0; x < n; x++) {
      const ok = colEmpty[x] === 1;
      colBar += `<div class="wm-ind ${ok ? "ok" : colEmpty[x] === 0 ? "bad" : ""}" style="width:${cell}px">${ok ? "✓" : colEmpty[x]}</div>`;
    }
    colBar += "</div>";

    // grid with a row-indicator column on the left
    let rows = "";
    for (let y = 0; y < n; y++) {
      const ok = rowEmpty[y] === 1;
      rows += `<div class="wm-ind ${ok ? "ok" : rowEmpty[y] === 0 ? "bad" : ""}" style="width:${cell}px;height:${cell}px">${ok ? "✓" : rowEmpty[y]}</div>`;
      for (let x = 0; x < n; x++) {
        const id = g[y][x];
        const t = id ? tileById(id) : null;
        const bg = t ? t.color : "";
        rows += `<div class="wm-cell${id ? " filled" : ""}" data-x="${x}" data-y="${y}" style="width:${cell}px;height:${cell}px;${bg ? "background:" + bg : ""}"></div>`;
      }
    }

    // tray: unplaced tiles
    const trayTiles = bag.filter((t) => !placed[t.id]).map((t) => {
      const d = eff(t, selId === t.id ? selRot : false);
      const sel = selId === t.id ? " sel" : "";
      const u = 12;
      return `<div class="wm-tile${sel}" data-tid="${t.id}">
        <div class="wm-mini" style="width:${d.w * u}px;height:${d.h * u}px;background:${t.color}"></div>
        <span>${d.w}×${d.h}</span></div>`;
    }).join("");

    const left = bag.length - Object.keys(placed).length;
    root.innerHTML = `
      <div class="wm-wrap">
        <div class="wm-controls">
          <button id="wm-rot" class="btn" ${selId ? "" : "disabled"}>⟳ Rotate (R)</button>
          <button id="wm-reset" class="btn">↺ Reset</button>
          <span class="wm-left">tiles left: <b>${left}</b></span>
        </div>
        ${colBar}
        <div class="wm-grid" style="grid-template-columns:repeat(${n + 1}, ${cell}px)">${rows}</div>
        <div class="wm-tray">${trayTiles || '<span class="wm-alldone">all tiles placed</span>'}</div>
        <button id="wm-submit" class="btn primary" ${isWin() ? "" : "disabled"}>Submit ${isWin() ? "✓" : ""}</button>
      </div>`;

    root.querySelectorAll(".wm-tile").forEach((el) =>
      el.addEventListener("click", () => selectTile(el.dataset.tid)));
    root.querySelectorAll(".wm-cell").forEach((el) =>
      el.addEventListener("click", () => onCell(+el.dataset.x, +el.dataset.y)));
    root.querySelector("#wm-rot").addEventListener("click", rotate);
    root.querySelector("#wm-reset").addEventListener("click", reset);
    root.querySelector("#wm-submit").addEventListener("click", () => {
      if (isWin() && !done) { done = true; cbs.onSolved && cbs.onSolved(); }
    });
  }

  function selectTile(id) {
    if (done) return;
    selId = selId === id ? null : id;
    selRot = false;
    draw();
  }

  function rotate() {
    if (!selId || done) return;
    selRot = !selRot;
    draw();
  }

  function onCell(x, y) {
    if (done) return;
    const g = grid();
    const hitId = g[y][x];
    if (hitId) {                       // pick a placed tile back up
      delete placed[hitId];
      selId = hitId;
      selRot = false;
      notifyMove();
      draw();
      return;
    }
    if (!selId) return;                // nothing selected, empty cell → no-op
    const t = tileById(selId);
    const d = eff(t, selRot);
    const p = { x, y, w: d.w, h: d.h };
    if (!fits(p, null)) { nudge(); return; }
    placed[selId] = p;
    selId = null;
    selRot = false;
    notifyMove();
    draw();
  }

  function nudge() {
    const gel = root.querySelector(".wm-grid");
    if (!gel) return;
    gel.classList.remove("wm-nudge");
    void gel.offsetWidth;
    gel.classList.add("wm-nudge");
  }

  function notifyMove() {
    if (cbs.onMove) cbs.onMove(Object.keys(placed).length);
  }

  function reset() {
    if (done) return;
    placed = {};
    selId = null;
    selRot = false;
    notifyMove();
    draw();
  }

  function onKey(e) {
    if (done) return;
    if (e.key === "r" || e.key === "R") { rotate(); }
  }

  window.DailyRenderers["windmill"] = {
    mount(boardEl, board, callbacks) {
      cbs = callbacks || {};
      n = board.n;
      bag = board.tiles.map((t, i) => ({
        id: "t" + i, w: t[0], h: t[1], color: TILE_COLORS[i % TILE_COLORS.length],
      }));
      placed = {};
      selId = null;
      selRot = false;
      done = false;
      boardEl.innerHTML = `<div id="wm-root"></div>`;
      root = boardEl.querySelector("#wm-root");
      document.addEventListener("keydown", onKey);
      draw();
      notifyMove();
    },
    getMoves() {
      // ordered by tile index so the bag multiset lines up; server is order-agnostic anyway
      return bag.filter((t) => placed[t.id]).map((t) => {
        const p = placed[t.id];
        return [p.x, p.y, p.w, p.h];
      });
    },
    teardown() {
      document.removeEventListener("keydown", onKey);
      root = null;
      placed = {};
      done = true;
    },
  };
})();
