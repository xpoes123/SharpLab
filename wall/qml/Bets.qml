// Members' Kalshi positions as a calm badge in the top-left corner of each tile that has some (only members who opted
// in; already delayed to the tile's picture by the service + web). TV distance: 1 rem = screen width / 120, like
// Board.qml. Nothing blinks: a line's background takes a faint green / red tint when its price moved, easing over 1.5 s.
//   "DAVID   YES LAL −3.5   @42¢ → 51¢   ▲ 21%"
import QtQuick
import "board.js" as B
import "lbar.js" as G

Item {
    id: root
    property var bets: null              // GET <bets>/api/overlay/bets
    property bool lbar: true             // tiles in the L-bar's corner (else the whole screen)
    property real unit: width            // the screen width
    readonly property real rem: unit / 120
    readonly property int n: bets ? bets.n : 0
    readonly property var rects: G.wallTiles(Math.max(1, n), G.wallArea(width, height, lbar))
    readonly property var items: B.badges(bets, n)

    readonly property color cBg: "#0E0F0C"
    readonly property color cInk: "#ECE6D6"
    readonly property color cMuted: "#B3AD9D"
    readonly property color cAccent: "#FFB000"
    readonly property color cOk: "#8ED07A"
    readonly property color cDanger: "#FF9580"
    function pick(want) { var have = Qt.fontFamilies(); for (var i = 0; i < want.length; i++) if (have.indexOf(want[i]) >= 0) return want[i]; return "" }
    readonly property string fDisplay: pick(["Archivo", "Fira Sans Condensed", "DejaVu Sans Condensed"]) || "sans-serif"
    readonly property string fMono: pick(["IBM Plex Mono", "DejaVu Sans Mono"]) || "monospace"

    Repeater {
        model: root.items
        delegate: Rectangle {
            id: badge
            required property var modelData
            readonly property var r: root.rects[modelData.i] || { x: 0, y: 0, w: 0, h: 0 }
            x: r.x + 1.2 * root.rem; y: r.y + 1.2 * root.rem
            width: Math.min(Math.max(r.w * 0.5, 22 * root.rem), r.w - 2.4 * root.rem, 30 * root.rem)
            height: col.implicitHeight + 1.4 * root.rem
            radius: 0.4 * root.rem
            color: Qt.rgba(0.055, 0.059, 0.047, 0.84)
            border.color: "#3A3B34"; border.width: Math.max(1, Math.round(0.1 * root.rem))
            Column {
                id: col
                x: 0.9 * root.rem; y: 0.7 * root.rem
                width: badge.width - 1.8 * root.rem
                spacing: 0.35 * root.rem
                Item {
                    width: parent.width; height: head.implicitHeight
                    Text { id: head; text: "BETS"; color: root.cAccent; font.family: root.fMono; font.pixelSize: 1.25 * root.rem; font.weight: Font.DemiBold; font.letterSpacing: 0.1 * root.rem }
                    Text { anchors.right: parent.right; text: "delayed " + badge.modelData.delay; color: root.cMuted; font.family: root.fMono; font.pixelSize: 1.25 * root.rem }
                }
                Repeater {
                    model: badge.modelData.lines
                    delegate: Rectangle {   // two lines a position: who + what (+ ▲/▼ %), then entry → now (+ size, $ if shared)
                        id: ln
                        required property var modelData
                        width: col.width
                        height: top.height + bot.height + 0.5 * root.rem
                        radius: 0.25 * root.rem
                        color: modelData.dir === "up" ? Qt.rgba(0.56, 0.82, 0.48, 0.15) : modelData.dir === "down" ? Qt.rgba(1, 0.58, 0.5, 0.15) : "transparent"
                        Behavior on color { ColorAnimation { duration: 1500; easing.type: Easing.InOutQuad } }
                        readonly property color tone: modelData.dir === "up" ? root.cOk : modelData.dir === "down" ? root.cDanger : root.cMuted
                        Item {
                            id: top
                            x: 0.4 * root.rem; y: 0.2 * root.rem; width: parent.width - 0.8 * root.rem; height: who.implicitHeight
                            Text { id: who; text: ln.modelData.who; color: root.cAccent; font.family: root.fDisplay; font.pixelSize: 1.6 * root.rem; font.weight: Font.Bold
                                   width: Math.min(implicitWidth, 7 * root.rem); elide: Text.ElideRight }
                            Text { anchors.left: who.right; anchors.leftMargin: 0.6 * root.rem; anchors.right: pct.left; anchors.rightMargin: 0.6 * root.rem
                                   anchors.baseline: who.baseline; text: ln.modelData.what; color: root.cInk; font.family: root.fDisplay
                                   font.pixelSize: 1.6 * root.rem; font.weight: Font.DemiBold; elide: Text.ElideRight }
                            Text { id: pct; anchors.right: parent.right; anchors.baseline: who.baseline; text: ln.modelData.pct; color: ln.tone
                                   font.family: root.fMono; font.pixelSize: 1.5 * root.rem; font.weight: Font.DemiBold
                                   Behavior on color { ColorAnimation { duration: 1500 } } }
                        }
                        Item {
                            id: bot
                            x: top.x; anchors.top: top.bottom; width: top.width; height: px.implicitHeight
                            Text { id: px; text: ln.modelData.px; color: root.cInk; font.family: root.fMono; font.pixelSize: 1.4 * root.rem }
                            Text { anchors.right: parent.right; anchors.baseline: px.baseline; color: root.cMuted; font.family: root.fMono; font.pixelSize: 1.25 * root.rem
                                   text: ln.modelData.size + (ln.modelData.pnl ? "  " + ln.modelData.pnl : "") }
                        }
                    }
                }
                Text { visible: badge.modelData.more > 0; text: "+" + badge.modelData.more + " more"; color: root.cMuted; font.family: root.fMono; font.pixelSize: 1.25 * root.rem }
            }
        }
    }
}
