// Dev preview of the overlay without layer-shell (any desktop, or headless): renders the screen as the TV would show it
// in a plain window — for lbar the four tiles (stand-ins, with their size) plus the sidebar and the band — and, with
// --out, saves a PNG after a few seconds and quits.
//   QT_QPA_PLATFORM=offscreen qml6 Preview.qml -- --layout lbar|full|matchup --board nfl --size 2560x1440 --out /tmp/ov.png
//   --tiles a.png,b.png,c.png = real frames on the tiles (laid out like the wall); --bets <url> = the bets badges + delays
//   --n 2 = that many stand-in tiles (lbar.js); --bands <url> (default: --bets) = the band layer around them (Bands.qml);
//   --layout none = no L-bar / board (the tiles over the whole screen)
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
    color: w.layout === "full" || w.layout === "matchup" ? "#2a2f45" : "#000000"   // video under a full overlay; else the kiosk's black
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
    readonly property var tileRects: G.wallTiles(Number(arg("--n", "4")), G.wallArea(width, height, lbar))
    Repeater {   // stand-in tiles (lbar.js): in the L-bar's corner, or over the whole screen
        model: w.frames.length ? [] : w.tileRects
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
    readonly property string bandsApi: arg("--bands", arg("--bets", ""))
    property var bands: null
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
            boardData: (w.lbar ? side : whole).last ? (w.lbar ? side : whole).last.data : null
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
        id: whole; visible: w.layout === "full" || w.layout === "matchup"; anchors.fill: parent
        layout: w.layout === "none" ? "full" : w.layout; board: w.arg("--board", ""); api: w.arg("--api", "http://127.0.0.1:7790"); polling: !w.lbar
        dflt: w.arg("--bets", "") === "" ? 0 : w.bets ? w.bets["default"] : 45; delays: (w.bets && w.bets.delays) || ({})
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
