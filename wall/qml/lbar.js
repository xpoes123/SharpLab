.pragma library
// Wall geometry, the one copy: Overlay.qml sizes its sidebar + band with lbar(), wall.py's KWin tiling script embeds this
// file (minus the pragma line) to place the tiles with wallTiles(), and wall.py reads TILES for the phone's tile map.
//
// L-bar: the 2×2 grid keeps every tile exactly 16:9 (no pillarbox); the sidebar takes what is left on the right, the band
// what is left at the bottom. The tile width is a multiple of 16, so its height (w · 9/16) is a whole number of pixels.
// 2560×1440 → tiles 1040×585, grid 2080×1170, sidebar 480, band 270. The L-bar is the same for any tile count: the
// tiles are laid out inside the corner it leaves (or the whole screen when there is no L-bar).
var SIDE = 0.1875;   // the sidebar's target share of the screen width

function lbar(sw, sh) {
    var w = Math.min(Math.floor(sw * (1 - SIDE) / 32) * 16, Math.floor(sh * 8 / 9 / 16) * 16);
    var h = w * 9 / 16;
    return { tileW: w, tileH: h, gridW: 2 * w, gridH: 2 * h, side: sw - 2 * w, band: sh - 2 * h };
}

// Tile arrangements for 1–4 tiles, in 16:9 cells: [col, row, span] — a tile covers span × span cells (so it is 16:9 too).
// 1 = one big tile; 2 = side by side; 3 = broadcast: one big + two small stacked on its right; 4 = 2×2. Tile 0 is the
// first URL. Keep this one JSON line: wall.py parses it.
var TILES = {"1": [[0, 0, 1]], "2": [[0, 0, 1], [1, 0, 1]], "3": [[0, 0, 2], [2, 0, 1], [2, 1, 1]], "4": [[0, 0, 1], [1, 0, 1], [0, 1, 1], [1, 1, 1]]};

// The area the tiles may use: the corner the L-bar leaves, or the whole screen (no overlay, or Full which covers it all).
function wallArea(sw, sh, useL) {
    if (!useL) return { x: 0, y: 0, w: sw, h: sh };
    var L = lbar(sw, sh);
    return { x: 0, y: 0, w: L.gridW, h: L.gridH };
}

// n tiles in the area: one cell = 16k × 9k px with k as big as fits (so every tile is an exact 16:9 integer rect), the
// whole arrangement centred in the area. → [{x, y, w, h}] in tile order.
function wallTiles(n, area) {
    var t = TILES[String(Math.max(1, Math.min(4, n)))], cols = 0, rows = 0;
    for (var i = 0; i < t.length; i++) { cols = Math.max(cols, t[i][0] + t[i][2]); rows = Math.max(rows, t[i][1] + t[i][2]); }
    var k = Math.max(1, Math.min(Math.floor(area.w / (16 * cols)), Math.floor(area.h / (9 * rows))));
    var cw = 16 * k, ch = 9 * k;
    var x0 = area.x + Math.floor((area.w - cols * cw) / 2), y0 = area.y + Math.floor((area.h - rows * ch) / 2);
    var out = [];
    for (var j = 0; j < t.length; j++) out.push({ x: x0 + t[j][0] * cw, y: y0 + t[j][1] * ch, w: t[j][2] * cw, h: t[j][2] * ch });
    return out;
}
