// Dev preview of the overlay without layer-shell (any desktop, or headless): renders the screen as the TV would show it
// in a plain window — for lbar the four tiles (stand-ins, with their size) plus the sidebar and the band — and, with
// --out, saves a PNG after a few seconds and quits.
//   QT_QPA_PLATFORM=offscreen qml6 Preview.qml -- --layout lbar|full|matchup --board nfl --size 2560x1440 --out /tmp/ov.png
//   --tiles a.png,b.png,c.png = real frames on the tiles (laid out like the wall); --bets <url> = the bets (sidebar, ticker, bands) + delays
//   --n 2 = that many stand-in tiles (lbar.js); --bands <url> (default: --bets) = the band layer around them (BandLayer.qml);
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
    // "none" + the service bringing the L-bar up for bets on 1 / 4 tiles (bets.auto_lbar) = the L-bar, like Overlay.qml
    readonly property bool lbar: layout === "lbar" || (layout === "none" && !!bets && !!bets.auto_lbar && !!bets.show)
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
    readonly property var tileRects: G.wallTiles(w.bands && w.bands.n > 0 ? w.bands.n : Number(arg("--n", "4")), G.wallArea(width, height, w.bands && w.bands.n > 0 ? !!w.bands.lbar : lbar))
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
    Timer {   // the bets + delays, like Overlay.qml
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
    readonly property string spotApi: arg("--spot", arg("--bets", ""))   // --spot <url> = the state spotlight (default: --bets)
    property var spot: null
    Timer {
        interval: 1000; running: w.spotApi !== ""; repeat: true; triggeredOnStart: true
        onTriggered: {
            var x = new XMLHttpRequest()
            x.onreadystatechange = function () {
                if (x.readyState !== XMLHttpRequest.DONE || x.status !== 200) return
                try { w.spot = JSON.parse(x.responseText).spot || null } catch (e) { }
            }
            x.open("GET", w.spotApi + "/api/overlay/spot")
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
    readonly property var bandGeo: w.bands && w.bands.n > 0 ? G.wallBands(w.bands.n, size[0], size[1], w.bands.lbar, w.lbar) : null
    BandLayer {
        visible: w.bandGeo !== null && w.layout !== "full" && w.layout !== "matchup" && (w.bands.n === 2 || w.bands.n === 3)
        x: w.bandGeo ? w.bandGeo.outer.x : 0; y: w.bandGeo ? w.bandGeo.outer.y : 0
        width: w.bandGeo ? w.bandGeo.outer.w : 0; height: w.bandGeo ? w.bandGeo.outer.h : 0
        b: w.bands; geo: w.bandGeo; unit: w.width; lbarShown: w.lbar; bets: w.bets
        boardData: (w.lbar ? side : whole).last ? (w.lbar ? side : whole).last.data : null
    }
    SpeakerBadge {   // the badge on the sound tile, like Overlay.qml's speaker surface
        visible: w.layout !== "full" && w.layout !== "matchup"; width: w.width; height: w.height; b: w.bands; unit: w.width
        area: w.bands && w.bands.n > 0 ? G.wallArea(w.width, w.height, !!w.bands.lbar) : null
    }
    Board {
        id: side; visible: w.lbar
        x: w.geo.gridW; width: w.geo.side; height: w.height; unit: w.width
        layout: "side"; board: w.arg("--board", ""); api: w.arg("--api", "http://127.0.0.1:7790"); polling: w.lbar
        dflt: w.arg("--bets", "") === "" ? 0 : w.bets ? w.bets["default"] : 45; delays: (w.bets && w.bets.delays) || ({})
        bets: w.bets; spot: w.spot
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
        layout: "band"; polling: false; v: side.v; bets: w.bets
    }
    Board {
        id: whole; visible: w.layout === "full" || w.layout === "matchup"; anchors.fill: parent
        layout: w.layout === "none" ? "full" : w.layout; board: w.arg("--board", ""); api: w.arg("--api", "http://127.0.0.1:7790"); polling: !w.lbar
        dflt: w.arg("--bets", "") === "" ? 0 : w.bets ? w.bets["default"] : 45; delays: (w.bets && w.bets.delays) || ({})
    }
    Timer {
        interval: Number(w.arg("--wait", "4000")); running: w.arg("--out", "") !== ""
        onTriggered: w.contentItem.grabToImage(function (r) { r.saveToFile(w.arg("--out", "")); Qt.quit() })
    }
}
