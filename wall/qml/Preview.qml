// Dev preview of the overlay without layer-shell (any desktop, or headless): renders the screen as the TV would show it
// in a plain window — for lbar the four tiles (stand-ins, with their size) plus the sidebar and the band — and, with
// --out, saves a PNG after a few seconds and quits.
//   QT_QPA_PLATFORM=offscreen qml6 Preview.qml -- --layout lbar|full|matchup --board nfl --size 2560x1440 --out /tmp/ov.png
import QtQuick
import QtQuick.Window
import "lbar.js" as G

Window {
    id: w
    function arg(name, dflt) {
        var a = Qt.application.arguments, i = a.lastIndexOf(name)
        return i >= 0 && i + 1 < a.length ? a[i + 1] : dflt
    }
    readonly property var size: arg("--size", "1920x1080").split("x").map(Number)
    readonly property string layout: arg("--layout", "lbar")
    readonly property var geo: G.lbar(size[0], size[1])
    readonly property bool lbar: layout === "lbar"
    width: size[0]
    height: size[1]
    color: "#2a2f45"   // stands in for the video under a full overlay
    visible: true
    title: "TV overlay preview"

    Repeater {   // the wall's tiles: L-bar corner, or quarters
        model: 4
        Rectangle {
            readonly property real tw: w.lbar ? w.geo.tileW : w.width / 2
            readonly property real th: w.lbar ? w.geo.tileH : w.height / 2
            x: (index % 2) * tw; y: Math.floor(index / 2) * th; width: tw; height: th
            color: ["#2a2f45", "#30364f", "#343a55", "#2c3248"][index]
            border.color: "#4a5070"; border.width: 2
            Text {
                anchors.centerIn: parent; color: "#8b93b8"; font.pixelSize: parent.height / 10
                text: "tile " + (index + 1) + "\n" + parent.width + "×" + parent.height + (Math.abs(parent.width * 9 - parent.height * 16) < 1 ? " = 16:9" : "")
                horizontalAlignment: Text.AlignHCenter
            }
        }
    }
    Board {
        id: side; visible: w.lbar
        x: w.geo.gridW; width: w.geo.side; height: w.height; unit: w.width
        layout: "side"; board: w.arg("--board", ""); api: w.arg("--api", "http://127.0.0.1:7790"); polling: w.lbar
    }
    Board {
        visible: w.lbar
        y: w.geo.gridH; width: w.geo.gridW; height: w.geo.band; unit: w.width
        layout: "band"; polling: false; v: side.v
    }
    Board {
        visible: !w.lbar; anchors.fill: parent
        layout: w.layout; board: w.arg("--board", ""); api: w.arg("--api", "http://127.0.0.1:7790"); polling: !w.lbar
    }
    Timer {
        interval: Number(w.arg("--wait", "4000")); running: w.arg("--out", "") !== ""
        onTriggered: w.contentItem.grabToImage(function (r) { r.saveToFile(w.arg("--out", "")); Qt.quit() })
    }
}
