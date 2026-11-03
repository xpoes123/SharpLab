// Dev preview of the overlay without layer-shell (any desktop, or headless): renders the screen as the TV would show it
// in a plain window — for lbar the four tiles (stand-ins, with their size) plus the sidebar and the band — and, with
// --out, saves a PNG after a few seconds and quits.
//   QT_QPA_PLATFORM=offscreen qml6 Preview.qml -- --layout lbar|full|matchup --board nfl --size 2560x1440 --out /tmp/ov.png
//   --tiles a.png,b.png,c.png = real frames on the tiles (laid out like the wall); --bets <url> = the bets badges + delays
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

    readonly property var frames: arg("--tiles", "") ? arg("--tiles", "").split(",") : []
    readonly property var frameRects: G.wallTiles(Math.max(1, frames.length), G.wallArea(width, height, lbar))
    Repeater {   // real frames on the tiles (--tiles)
        model: w.frames
        Image {
            required property string modelData
            required property int index
            readonly property var r: w.frameRects[index]
            x: r.x; y: r.y; width: r.w; height: r.h; source: "file://" + modelData; fillMode: Image.PreserveAspectCrop
        }
    }
    Repeater {   // the wall's tiles: L-bar corner, or quarters
        model: w.frames.length ? 0 : 4
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
    property var bets: null
    Timer {   // the bets badges + delays, like Overlay.qml
        interval: 1000; running: w.arg("--bets", "") !== ""; repeat: true; triggeredOnStart: true
        onTriggered: {
            var x = new XMLHttpRequest()
            x.onreadystatechange = function () {
                if (x.readyState !== XMLHttpRequest.DONE || x.status !== 200) return
                try { w.bets = JSON.parse(x.responseText) } catch (e) { }
            }
            x.open("GET", w.arg("--bets", "") + "/api/overlay/bets")
            x.send()
        }
    }
    Board {
        id: side; visible: w.lbar
        x: w.geo.gridW; width: w.geo.side; height: w.height; unit: w.width
        layout: "side"; board: w.arg("--board", ""); api: w.arg("--api", "http://127.0.0.1:7790"); polling: w.lbar
        dflt: w.arg("--bets", "") === "" ? 0 : w.bets ? w.bets["default"] : 45; delays: (w.bets && w.bets.delays) || ({})
    }
    Timer {   // the watch party's chat, from the view poll like Overlay.qml
        interval: 500; running: w.lbar; repeat: true; triggeredOnStart: true
        onTriggered: {
            var x = new XMLHttpRequest()
            x.onreadystatechange = function () {
                if (x.readyState !== XMLHttpRequest.DONE || x.status !== 200) return
                try { var c = JSON.parse(x.responseText).chat || null } catch (e) { return }
                if (JSON.stringify(c) !== JSON.stringify(side.chat)) side.chat = c
            }
            x.open("GET", w.arg("--api", "http://127.0.0.1:7790") + "/api/overlay/view")
            x.send()
        }
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
    Bets {
        visible: !!w.bets && (w.layout === "lbar" || w.layout === "")
        width: w.lbar ? w.geo.gridW : w.width; height: w.lbar ? w.geo.gridH : w.height; unit: w.width; lbar: false; bets: w.bets
    }
    Timer {
        interval: Number(w.arg("--wait", "4000")); running: w.arg("--out", "") !== ""
        onTriggered: w.contentItem.grabToImage(function (r) { r.saveToFile(w.arg("--out", "")); Qt.quit() })
    }
}
