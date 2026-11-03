// Dev preview of the overlay without layer-shell (any desktop, or headless): renders the screen as the TV would show it
// in a plain window — for lbar the four tiles (stand-ins, with their size) plus the sidebar and the band — and, with
// --out, saves a PNG after a few seconds and quits. --tiles n lays out n tiles (lbar.js); --bands <wall /i/name> adds the
// band layer around them (Bands.qml), --layout none = no L-bar / board (the tiles over the whole screen).
//   QT_QPA_PLATFORM=offscreen qml6 Preview.qml -- --layout lbar|full|matchup|none --board nfl --size 2560x1440 --out /tmp/ov.png
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
    readonly property int tiles: Number(arg("--tiles", "4"))
    readonly property var tileRects: G.wallTiles(tiles, G.wallArea(size[0], size[1], lbar))
    readonly property string bandsApi: arg("--bands", "")
    property var bands: null
    width: size[0]
    height: size[1]
    color: w.layout === "full" || w.layout === "matchup" ? "#2a2f45" : "#000000"   // video under a full overlay; else the kiosk's black
    visible: true
    title: "TV overlay preview"

    Repeater {   // the wall's tiles (lbar.js): in the L-bar's corner, or over the whole screen
        model: w.tileRects
        Rectangle {
            required property var modelData
            required property int index
            x: modelData.x; y: modelData.y; width: modelData.w; height: modelData.h
            color: ["#2a2f45", "#30364f", "#343a55", "#2c3248"][index]
            border.color: "#4a5070"; border.width: 2
            Text {
                anchors.centerIn: parent; color: "#8b93b8"; font.pixelSize: parent.height / 10
                text: "tile " + (index + 1) + "\n" + parent.width + "×" + parent.height + (Math.abs(parent.width * 9 - parent.height * 16) < 1 ? " = 16:9" : "")
                horizontalAlignment: Text.AlignHCenter
            }
        }
    }
    Timer {   // the band layer's data, like Overlay.qml
        interval: 500; running: w.bandsApi !== ""; repeat: true; triggeredOnStart: true
        onTriggered: {
            var x = new XMLHttpRequest()
            x.onreadystatechange = function () {
                if (x.readyState !== XMLHttpRequest.DONE || x.status !== 200) return
                try { w.bands = JSON.parse(x.responseText) } catch (e) {}
            }
            x.open("GET", w.bandsApi + "/bands")
            x.send()
        }
    }
    readonly property var bandGeo: w.bands && w.bands.n > 0 ? G.wallBands(w.bands.n, size[0], size[1], w.bands.lbar) : null
    Repeater {
        model: w.bandGeo && w.layout !== "full" && w.layout !== "matchup" ? ["top", "bottom"] : []
        Bands {
            required property string modelData
            visible: w.bandGeo[modelData].h >= w.width / 40
            x: w.bandGeo[modelData].x; y: w.bandGeo[modelData].y; width: w.bandGeo[modelData].w; height: w.bandGeo[modelData].h
            b: w.bands; edge: modelData; geo: w.bandGeo; unit: w.width
        }
    }
    Board {
        id: side; visible: w.lbar
        x: w.geo.gridW; width: w.geo.side; height: w.height; unit: w.width
        layout: "side"; board: w.arg("--board", ""); api: w.arg("--api", "http://127.0.0.1:7790"); polling: w.lbar
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
        visible: w.layout === "full" || w.layout === "matchup"; anchors.fill: parent
        layout: w.layout; board: w.arg("--board", ""); api: w.arg("--api", "http://127.0.0.1:7790"); polling: w.layout === "full" || w.layout === "matchup"
    }
    Timer {
        interval: Number(w.arg("--wait", "4000")); running: w.arg("--out", "") !== ""
        onTriggered: w.contentItem.grabToImage(function (r) { r.saveToFile(w.arg("--out", "")); Qt.quit() })
    }
}
