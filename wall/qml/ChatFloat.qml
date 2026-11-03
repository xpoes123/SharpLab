// The watch party's chat on the TV, Twitch-style, in one of two places:
//   dock (the L-bar is up): the lower part of the sidebar (Board.qml's election / side views place it), full width,
//     newest at the bottom, at most 8 lines, the sidebar's ink on its dark background with a subtle shadow. `active` =
//     a line in the last 2 minutes: the sidebar hides its TRADES box meanwhile (the trades still scroll in the ticker).
//   float (no L-bar): over the tiles at the top right of the tiles' area (under the tile chips' row — the outlet's name
//     + 🔊, SpeakerBadge.qml — right edges lined up with it), at most 6 lines, white with a dark outline + a soft shadow,
//     no box. Positioned in `area` coordinates (the tiles' area, lbar.js wallArea), like SpeakerBadge.qml.
// Each line = a bold name + the text; Jev's / the wall's notices (sys) in the accent colour. A line fades in when it
// arrives and out ~20 s later. Only fresh lines ever show (bands.py chat_lines: < 20 s old), so nothing old comes up on
// a start or when the owner turns it back on. The data is /bands' tvchat {on, lines} (wall/server.py; the owner's toggle
// on the watch page, op tvchat). The Discord stream captures the TV, so it shows there too.
import QtQuick
import "lbar.js" as G

Item {
    id: cf
    property var b: null           // the /bands payload (n, tvchat {on, lines})
    property var area: null        // G.wallArea(...) the tiles are in
    property real unit: 1920       // the screen width
    property bool shown: true      // the surface is up (the host's view allows it): ingest only then
    property bool dock: false      // in the sidebar (fills this item, bottom-up) instead of floating over the tiles
    property string family: ""     // the font (the sidebar passes its display face); "" = the default
    readonly property int maxLines: dock ? 8 : 6
    readonly property int lifeMs: 20000
    readonly property int activeMs: 120000
    readonly property bool live: shown && !!b && !!b.tvchat && b.tvchat.on === true && b.n > 0 && (dock || !!area)
    // a line in the last 2 minutes (the sidebar gives the chat the TRADES box's room meanwhile)
    property real lastAt: 0
    property real nowMs: 0
    readonly property bool active: live && lastAt > 0 && nowMs - lastAt < activeMs
    Timer { interval: 1000; repeat: true; running: cf.live && cf.lastAt > 0; onTriggered: cf.nowMs = Date.now() }
    // the top-right tile and its name chip's height (SpeakerBadge.qml: d, inset 0.6 d)
    readonly property var tr: {
        if (!area || !b || !(b.n > 0)) return null
        var t = G.wallTiles(b.n, area), y0 = Math.min.apply(null, t.map(function (r) { return r.y })), best = null
        for (var i = 0; i < t.length; i++) if (t[i].y === y0 && (!best || t[i].x + t[i].w > best.x + best.w)) best = t[i]
        return best
    }
    readonly property real d: tr ? G.chipH(tr.w, unit) : 0
    readonly property real fontPx: Math.round(unit * (dock ? 0.0146 : 0.0175))   // dock: the sidebar's body size (1.75 rem)

    ListModel { id: lines }
    property var seen: ({})

    function esc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;") }
    function ingest() {
        if (!live) return
        var ls = b.tvchat.lines || [], ids = {}, now = Date.now()
        for (var i = 0; i < ls.length; i++) ids[ls[i].id] = true
        for (var j = lines.count - 1; j >= 0; j--)   // deleted by a mod, or long gone
            if (!ids[lines.get(j).lid] || now - lines.get(j).born > lifeMs + 2000) lines.remove(j)
        for (var k = 0; k < ls.length; k++) {
            var l = ls[k]
            if (seen[l.id]) continue
            seen[l.id] = true
            if (!l.fresh) continue
            lastAt = nowMs = now
            lines.append({ lid: l.id, born: now, sys: !!l.sys, guest: !!l.guest,
                           html: l.sys ? esc(l.text) : "<b>" + esc(l.name || "?") + "</b>&nbsp; " + esc(l.text) })
        }
        while (lines.count > maxLines) lines.remove(0)
    }
    function trim() { while (lines.count > 1 && col.implicitHeight > col.maxH) { lines.remove(0); col.forceLayout() } }
    function drop(id) { for (var i = 0; i < lines.count; i++) if (lines.get(i).lid === id) { lines.remove(i); return } }
    onBChanged: ingest()
    onLiveChanged: {
        if (live) ingest()
        else { lines.clear(); lastAt = 0 }   // seen stays: a line once shown (or skipped) never comes back
    }

    Item {
        id: stack
        visible: cf.live && lines.count > 0
        width: cf.dock ? cf.width : cf.tr ? Math.min(cf.unit * 0.27, cf.tr.w - 1.2 * cf.d) : 0
        height: col.implicitHeight
        x: cf.dock ? 0 : cf.tr ? cf.tr.x + cf.tr.w - 0.6 * cf.d - width : 0
        y: cf.dock ? cf.height - height : cf.tr ? cf.tr.y + 2.1 * cf.d : 0   // float: under the chip row (0.6 d inset + d) with a 0.5 d gap
        Column {
            id: col
            width: parent.width
            spacing: cf.fontPx * 0.18
            // never taller than the dock / ~half the tiles' area (long wrapped lines): the oldest goes first
            readonly property real maxH: cf.dock ? cf.height : cf.area ? cf.area.h * 0.5 : 1e9
            onImplicitHeightChanged: Qt.callLater(cf.trim)
            Repeater {
                model: lines
                Item {
                    id: line
                    required property int lid
                    required property bool sys
                    required property bool guest
                    required property string html
                    width: col.width
                    height: face.implicitHeight
                    opacity: 0
                    // the soft shadow: the same text in black, a little down + right, twice (no shader effect: plain
                    // Text paints the same on the TV's GPU and in the offscreen test harness); docked, one faint one
                    Repeater {
                        model: cf.dock ? [[0.04, 0.06, 0.5]] : [[0.05, 0.07, 0.55], [0.0, 0.1, 0.35]]
                        Text {
                            required property var modelData
                            x: modelData[0] * cf.fontPx; y: modelData[1] * cf.fontPx
                            width: face.width; textFormat: Text.StyledText; text: line.html
                            wrapMode: face.wrapMode; maximumLineCount: face.maximumLineCount; elide: face.elide
                            font: face.font; color: "#000000"; opacity: modelData[2]
                        }
                    }
                    Text {
                        id: face
                        width: parent.width
                        textFormat: Text.StyledText
                        text: line.html
                        wrapMode: Text.Wrap
                        maximumLineCount: 2
                        elide: Text.ElideRight
                        font.pixelSize: cf.fontPx
                        font.family: cf.family || Qt.application.font.family
                        font.weight: line.sys ? Font.DemiBold : Font.Medium
                        // the accent (tokens.css) for notices; docked, the sidebar's ink (cInk / cMuted)
                        color: line.sys ? "#FFB000" : cf.dock ? (line.guest ? "#B3AD9D" : "#ECE6D6") : line.guest ? "#E8E8E8" : "#FFFFFF"
                        style: cf.dock ? Text.Normal : Text.Outline; styleColor: Qt.rgba(0, 0, 0, 0.9)
                    }
                    SequentialAnimation on opacity {
                        NumberAnimation { to: 1; duration: 250; easing.type: Easing.OutQuad }
                        PauseAnimation { duration: cf.lifeMs - 1250 }
                        NumberAnimation { to: 0; duration: 1000; easing.type: Easing.InQuad }
                        ScriptAction { script: cf.drop(line.lid) }
                    }
                }
            }
        }
    }
}
