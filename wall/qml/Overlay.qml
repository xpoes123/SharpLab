// The video wall's overlay as native layer-shell surfaces (wall.py starts it with qml6 — no Chromium, no KWin rules).
// One process for the wall's whole run: it polls GET /api/overlay/view twice a second and shows the surfaces that view
// needs ("" = none; hidden windows unmap but the process, its data and its image cache stay), refetching the board data at
// once when the view's rev changes — so Show, Hide, a board / layout switch or a matchup opening never waits on a relaunch.
// OVERLAY layer = above the tiles whatever they do; keyboard NONE + Qt.WindowTransparentForInput (empty input region) =
// never takes focus or clicks from the tiles.
//   lbar:    two surfaces, a sidebar on the right and a band along the bottom (what goes where: Board.qml), each with an
//            exclusive zone on its own edge; sizes from lbar.js, so the corner left over is exactly the wall's 16:9 2×2
//            grid (wall.py's KWin script tiles with the same function). One poll: the band reads the sidebar's data.
//   full:    the whole screen, per-pixel 90 % background.
//   matchup: the whole screen, opaque: the league the phone opened.
//   qml6 Overlay.qml -- --api http://127.0.0.1:7790
import QtQuick
import QtQuick.Window
import org.kde.layershell 1.0 as LayerShell
import "lbar.js" as G

Window {   // the root is the full / matchup surface (the qml tool needs a window or an item as its root)
    id: app
    function arg(name, dflt) {
        var a = Qt.application.arguments, i = a.lastIndexOf(name)
        return i >= 0 && i + 1 < a.length ? a[i + 1] : dflt
    }
    property string layout: ""            // the view: "" (nothing shown), lbar, full or matchup
    property int rev: -1
    readonly property string api: arg("--api", "http://127.0.0.1:7790")
    readonly property var geo: G.lbar(Screen.width, Screen.height)
    property bool bandReady: false
    // the band maps 400 ms after the sidebar, every time the L-bar comes back (see the sidebar's anchors)
    onLayoutChanged: { bandReady = false; if (layout === "lbar") bandTimer.restart() }
    Timer { id: bandTimer; interval: 400; onTriggered: app.bandReady = app.layout === "lbar" }

    function pollView() {
        var x = new XMLHttpRequest()
        x.onreadystatechange = function () {
            if (x.readyState !== XMLHttpRequest.DONE || x.status !== 200) return
            var s = null
            try { s = JSON.parse(x.responseText) } catch (e) { return }
            if (s.rev !== app.rev) { app.rev = s.rev; main.tick() }   // new board / matchup: its data now, not in ≤ 5 s
            if (["", "lbar", "full", "matchup"].indexOf(s.view) >= 0) app.layout = s.view
        }
        x.open("GET", app.api + "/api/overlay/view")
        x.send()
    }
    Timer { interval: 500; running: true; repeat: true; triggeredOnStart: true; onTriggered: app.pollView() }

    // every picture the matchup panel can show, kept decoded (same source size / fill as Board.qml's Pic) so a matchup
    // opens with its headshots already there; the list only changes when the lineups do
    property var pics: []
    Connections {
        target: main
        function onLastChanged() {
            var p = (main.last && main.last.prefetch) || []
            if (JSON.stringify(p) !== JSON.stringify(app.pics)) app.pics = p
        }
    }
    Item {
        visible: false
        Repeater {
            model: app.pics
            Image { required property string modelData; source: modelData; sourceSize.width: 240; sourceSize.height: 240; fillMode: Image.PreserveAspectFit; asynchronous: true; cache: true }
        }
    }

    // full / matchup: the whole screen, over panels too, reserving nothing
    title: "TV overlay"
    color: "transparent"
    flags: Qt.FramelessWindowHint | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus
    width: Screen.width
    height: Screen.height
    visible: app.layout === "full" || app.layout === "matchup"
    LayerShell.Window.scope: "tv-overlay"
    LayerShell.Window.layer: LayerShell.Window.LayerOverlay
    LayerShell.Window.keyboardInteractivity: LayerShell.Window.KeyboardInteractivityNone
    LayerShell.Window.activateOnShow: false
    LayerShell.Window.anchors: LayerShell.Window.AnchorTop | LayerShell.Window.AnchorBottom | LayerShell.Window.AnchorLeft | LayerShell.Window.AnchorRight
    LayerShell.Window.exclusionZone: -1
    // the one poll (it runs while its window is hidden too); the L-bar's two boards draw from it
    Board { id: main; anchors.fill: parent; layout: app.layout === "matchup" ? "matchup" : "full"; api: app.api; polling: true }

    Window {   // lbar: the sidebar (full height, right edge)
        id: side
        transientParent: null   // its own surface, not a child of the (hidden) root
        title: "TV overlay side"
        color: "transparent"
        flags: Qt.FramelessWindowHint | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus
        width: app.geo.side
        height: Screen.height
        visible: app.layout === "lbar"
        LayerShell.Window.scope: "tv-overlay"
        LayerShell.Window.layer: LayerShell.Window.LayerOverlay
        LayerShell.Window.keyboardInteractivity: LayerShell.Window.KeyboardInteractivityNone
        LayerShell.Window.activateOnShow: false
        // an edge + both its neighbours (a corner anchor would make the exclusive zone ignored; layer-shell-qt 6.8 has no
        // exclusiveEdge in QML). Shown first, so KWin gives it the full height and the band the width left of it.
        LayerShell.Window.anchors: LayerShell.Window.AnchorTop | LayerShell.Window.AnchorBottom | LayerShell.Window.AnchorRight
        LayerShell.Window.exclusionZone: app.geo.side
        Board { anchors.fill: parent; unit: Screen.width; layout: "side"; polling: false; v: main.v }
    }

    Window {   // lbar: the band (bottom edge, under the tiles only)
        transientParent: null
        title: "TV overlay band"
        color: "transparent"
        flags: Qt.FramelessWindowHint | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus
        width: app.geo.gridW
        height: app.geo.band
        visible: app.layout === "lbar" && app.bandReady
        LayerShell.Window.scope: "tv-overlay"
        LayerShell.Window.layer: LayerShell.Window.LayerOverlay
        LayerShell.Window.keyboardInteractivity: LayerShell.Window.KeyboardInteractivityNone
        LayerShell.Window.activateOnShow: false
        // stretched by KWin to the width left of the sidebar (= geo.gridW); if it ever came first it would span the whole
        // bottom with the sidebar above it — still no video covered (the tiles are placed by wall.py), just a wider band
        LayerShell.Window.anchors: LayerShell.Window.AnchorBottom | LayerShell.Window.AnchorLeft | LayerShell.Window.AnchorRight
        LayerShell.Window.exclusionZone: app.geo.band
        Board { anchors.fill: parent; unit: Screen.width; layout: "band"; polling: false; v: main.v }
    }
}
