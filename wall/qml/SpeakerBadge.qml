// The speaker badge: a small dark chip with a 🔊 (drawn, no emoji font) and a faint amber ring, inset in the top-right
// corner of the tile that has the sound, so the room and the Discord stream (it captures this screen) can tell which
// stream they hear. The service decides whether there is one (wall/bands.py speaker: none with < 2 video tiles or on
// our /p/ data pages); this places it with lbar.js wallTiles — the same arrangement the KWin script tiles with.
// When the sound moves it fades out where it was and back in on the new tile (0.4 s each way); nothing pulses.
// Positioned in `area` coordinates (the tiles' area, lbar.js wallArea): Overlay.qml puts it on a transparent surface
// that size at the screen's top-left; Preview.qml at 0,0.
import QtQuick
import "lbar.js" as G

Item {
    id: sb
    property var b: null           // the /bands payload (n, speaker {tile, rect})
    property var area: null        // G.wallArea(...) the tiles are in
    property real unit: 1920       // the screen width (1 rem = unit / 120, like Board.qml)
    readonly property real rem: unit / 120
    readonly property var want: b && b.speaker && area && b.n > 1 ? G.wallTiles(b.n, area)[b.speaker.tile] || null : null
    property var at: null          // the rect the chip is drawn on now (lags `want` by the fade-out)
    readonly property real d: at ? Math.round(Math.max(0.025 * at.w, 1.5 * rem)) : 0

    function key(r) { return r ? [r.x, r.y, r.w, r.h].join(",") : "" }
    onWantChanged: {
        if (key(want) === key(at)) { if (want && chip.opacity < 1 && !move.running) fadeIn.start(); return }
        fadeIn.stop(); move.restart()
    }
    Component.onCompleted: { at = want; if (want) fadeIn.start() }

    SequentialAnimation {
        id: move
        NumberAnimation { target: chip; property: "opacity"; to: 0; duration: chip.opacity > 0 ? 400 : 0; easing.type: Easing.InOutQuad }
        ScriptAction { script: sb.at = sb.want }
        NumberAnimation { target: chip; property: "opacity"; to: sb.want ? 1 : 0; duration: sb.want ? 400 : 0; easing.type: Easing.InOutQuad }
    }
    NumberAnimation { id: fadeIn; target: chip; property: "opacity"; to: 1; duration: 400; easing.type: Easing.InOutQuad }

    Rectangle {
        id: chip
        opacity: 0
        visible: sb.at !== null
        width: sb.d * 1.5; height: sb.d; radius: height / 2
        x: sb.at ? sb.at.x + sb.at.w - width - 0.6 * sb.d : 0
        y: sb.at ? sb.at.y + 0.6 * sb.d : 0
        color: Qt.rgba(0.043, 0.047, 0.039, 0.78)                          // tokens.css base, see-through
        border.color: Qt.rgba(1, 0.69, 0, 0.55); border.width: Math.max(1.5, sb.d * 0.06)   // the accent, faint
        Canvas {
            id: glyph
            readonly property real s: sb.d * 0.56
            anchors.centerIn: parent; anchors.horizontalCenterOffset: s * 0.05
            width: s * 1.05; height: s
            onSChanged: requestPaint()
            onVisibleChanged: requestPaint()
            Component.onCompleted: requestPaint()
            onPaint: {
                var c = getContext("2d"), s = glyph.s
                c.reset()
                c.fillStyle = "#ECE6D6"; c.strokeStyle = "#ECE6D6"; c.lineWidth = Math.max(1.5, s * 0.09); c.lineCap = "round"
                c.beginPath(); c.moveTo(0, s * 0.35); c.lineTo(s * 0.22, s * 0.35); c.lineTo(s * 0.5, s * 0.1); c.lineTo(s * 0.5, s * 0.9)
                c.lineTo(s * 0.22, s * 0.65); c.lineTo(0, s * 0.65); c.closePath(); c.fill()
                c.beginPath(); c.arc(s * 0.55, s * 0.5, s * 0.2, -0.9, 0.9); c.stroke()
                c.beginPath(); c.arc(s * 0.55, s * 0.5, s * 0.4, -0.9, 0.9); c.stroke()
            }
        }
    }
}
