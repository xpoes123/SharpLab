// The tile chips: a small dark chip with a faint amber ring inset in the top-right corner of every stream tile, with the
// outlet's name ("NBC News", "ABC 33/40 Birmingham": wall/bands.py stream_name, the tile's `name`) and, on the tile that
// has the sound, a 🔊 (drawn, no emoji font) before it — so the room and the Discord stream (it captures this screen) can
// tell which outlet is which and which one they hear. Our /p/ data pages get no chip; a tile with the sound but no name
// (a sports stream) gets the 🔊 alone, as before. The service decides whether there is a speaker (bands.speaker: none
// with < 2 video tiles or on our /p/ pages); this places the chips with lbar.js wallTiles — the same arrangement the
// KWin script tiles with. When the sound moves the 🔊 fades out on the old chip and in on the new one (0.4 s); nothing
// pulses. Positioned in `area` coordinates (the tiles' area, lbar.js wallArea): Overlay.qml puts it on a transparent
// surface that size at the screen's top-left; Preview.qml at 0,0.
import QtQuick
import "lbar.js" as G

Item {
    id: sb
    property var b: null           // the /bands payload (n, tiles [{name}], speaker {tile, rect})
    property var area: null        // G.wallArea(...) the tiles are in
    property real unit: 1920       // the screen width
    readonly property var rects: b && area && b.n > 0 ? G.wallTiles(b.n, area) : []
    readonly property int sound: b && b.speaker && b.n > 1 ? b.speaker.tile : -1

    Repeater {
        model: sb.rects.length
        Rectangle {
            id: chip
            required property int index
            readonly property var r: sb.rects[index]
            readonly property var tile: sb.b && sb.b.tiles ? sb.b.tiles[index] || null : null
            readonly property string name: tile && tile.name ? tile.name : ""
            readonly property bool hasSound: sb.sound === index
            readonly property real d: G.chipH(r.w, sb.unit)
            readonly property real pad: d * 0.42
            readonly property real gap: name !== "" && hasSound ? d * 0.22 : 0
            visible: opacity > 0
            opacity: name !== "" || hasSound ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: 400; easing.type: Easing.InOutQuad } }
            height: d; radius: height / 2
            width: name === "" ? d * 1.5 : 2 * pad + (hasSound ? glyph.width + gap : 0) + label.width
            Behavior on width { NumberAnimation { duration: 400; easing.type: Easing.InOutQuad } }
            x: r.x + r.w - width - 0.6 * d
            y: r.y + 0.6 * d
            color: Qt.rgba(0.043, 0.047, 0.039, 0.78)                          // tokens.css base, see-through
            border.color: Qt.rgba(1, 0.69, 0, 0.55); border.width: Math.max(1.5, d * 0.06)   // the accent, faint
            Canvas {
                id: glyph
                readonly property real s: chip.d * 0.56
                x: chip.name === "" ? (chip.width - width) / 2 + s * 0.05 : chip.pad
                anchors.verticalCenter: parent.verticalCenter
                width: s * 1.05; height: s
                opacity: chip.hasSound ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: 400; easing.type: Easing.InOutQuad } }
                onSChanged: requestPaint()
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
            Text {
                id: label
                visible: chip.name !== ""
                text: chip.name
                anchors.verticalCenter: parent.verticalCenter
                anchors.right: parent.right; anchors.rightMargin: chip.pad
                // never wider than the tile leaves (a long station name elides)
                width: Math.min(implicitWidth, chip.r.w * 0.6)
                elide: Text.ElideRight
                font.pixelSize: Math.round(chip.d * 0.5); font.weight: Font.DemiBold
                color: "#ECE6D6"
            }
        }
    }
}
