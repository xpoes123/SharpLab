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

// What the tiles leave uncovered (the overlay's band layer, Bands.qml): {outer, tiles, top, bottom, left, right, head}.
// outer = the region the band layer owns: the L-bar's corner while the L-bar is drawn (useL and lbarShown), else the
// whole screen — tiles placed for the L-bar with no L-bar on screen leave its sidebar + band to the bands too, so nothing
// behind the wall (the kiosk, a panel) ever shows. top / bottom = full-width rects above / below the tiles' block, left /
// right = beside it (rects {x, y, w, h}, h or w 0 = none; 1 and 4 tiles fill a 16:9 area). head[i] = the band tile i's
// header goes in: "top" when the tile touches the top band, else "bottom" when it touches the bottom one, else "".
function wallBands(n, sw, sh, useL, lbarShown) {
    var a = wallArea(sw, sh, useL), T = wallTiles(n, a);
    var o = useL && lbarShown !== false ? a : { x: 0, y: 0, w: sw, h: sh };
    var x0 = o.x + o.w, x1 = o.x, y0 = o.y + o.h, y1 = o.y;
    for (var i = 0; i < T.length; i++) {
        x0 = Math.min(x0, T[i].x); x1 = Math.max(x1, T[i].x + T[i].w);
        y0 = Math.min(y0, T[i].y); y1 = Math.max(y1, T[i].y + T[i].h);
    }
    var top = { x: o.x, y: o.y, w: o.w, h: y0 - o.y }, bottom = { x: o.x, y: y1, w: o.w, h: o.y + o.h - y1 };
    var left = { x: o.x, y: y0, w: x0 - o.x, h: y1 - y0 }, right = { x: x1, y: y0, w: o.x + o.w - x1, h: y1 - y0 }, head = [];
    for (var j = 0; j < T.length; j++)
        head.push(top.h > 0 && T[j].y === y0 ? "top" : bottom.h > 0 && T[j].y + T[j].h === y1 ? "bottom" : "");
    return { outer: o, tiles: T, top: top, bottom: bottom, left: left, right: right, head: head };
}
