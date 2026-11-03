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
//   bets:    (with --bets <service>/i/<wall>) a transparent surface over the tiles' corner with members' Kalshi badges, and
//            the spoiler delays the boards are held back by (each game as of now − its tile's delay; Board.qml)
//   bands:   (any view but full / matchup; the same service as --bets) up to two more surfaces, exactly the bands the
//            tiles leave above and below them (lbar.js wallBands; 2 and 3 tiles leave some, 1 and 4 fill their 16:9
//            area): Bands.qml, from GET <bets>/bands every 2 s — a header per tile (its game as of now − its delay; the
//            board's own delayed copy wins when it has the game, so header and sidebar agree), a calm backdrop, and
//            without the L-bar the other games + the chat. Exclusive zone 0: the bottom one sits on the L-bar's band.
//   qml6 Overlay.qml -- --api http://127.0.0.1:7790 --bets http://127.0.0.1:7795/i/tv
import QtQuick
import QtQuick.Window
import org.kde.layershell 1.0 as LayerShell
import "lbar.js" as G
import "board.js" as B

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
    property var chat: null               // the watch party's chat, from the same view poll (the sidebar's lower part)
    readonly property string betsApi: arg("--bets", "")
    property var bets: null               // GET <bets>/api/overlay/bets: badges + delays
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
            var c = s.chat || null   // only on a change: the sidebar keeps its lines (and their fades) across polls
            if (JSON.stringify(c) !== JSON.stringify(app.chat)) app.chat = c
        }
        x.open("GET", app.api + "/api/overlay/view")
        x.send()
    }
    Timer { interval: 500; running: true; repeat: true; triggeredOnStart: true; onTriggered: app.pollView() }

    function pollBets() {
        var x = new XMLHttpRequest()
        x.onreadystatechange = function () {
            if (x.readyState !== XMLHttpRequest.DONE) return
            var b = null
            if (x.status === 200) { try { b = JSON.parse(x.responseText) } catch (e) { b = null } }
            if (JSON.stringify(b) !== JSON.stringify(app.bets)) app.bets = b
        }
        x.open("GET", app.betsApi + "/api/overlay/bets")
        x.send()
    }
    Timer { interval: 2000; running: app.betsApi !== ""; repeat: true; triggeredOnStart: true; onTriggered: app.pollBets() }
    // no answer from the service = the default delay still holds (never live by accident)
    readonly property real dflt: app.betsApi === "" ? 0 : app.bets ? app.bets["default"] : 45

    // the bands around the tiles: their own poll of the same service
    readonly property string bandsApi: arg("--bands", app.betsApi)
    property var bands: null
    property string bandsJson: ""
    function pollBands() {
        var x = new XMLHttpRequest()
        x.onreadystatechange = function () {
            if (x.readyState !== XMLHttpRequest.DONE) return
            if (x.status !== 200) { if (app.bandsJson !== "") { app.bandsJson = ""; app.bands = null }; return }
            if (x.responseText === app.bandsJson) return   // unchanged: no re-render
            try { app.bands = JSON.parse(x.responseText); app.bandsJson = x.responseText } catch (e) {}
        }
        x.open("GET", app.bandsApi + "/bands")
        x.send()
    }
    Timer { interval: 2000; running: app.bandsApi !== ""; repeat: true; triggeredOnStart: true; onTriggered: app.pollBands() }
    readonly property var bandGeo: app.bands && app.bands.n > 0 ? G.wallBands(app.bands.n, Screen.width, Screen.height, app.bands.lbar) : null
    readonly property bool bandsOn: app.bandGeo !== null && app.layout !== "full" && app.layout !== "matchup"
    readonly property real bandMin: Screen.width / 120 * 3   // thinner than 3 rem: not worth a surface

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
    Board { id: main; anchors.fill: parent; layout: app.layout === "matchup" ? "matchup" : "full"; api: app.api; polling: true
            dflt: app.dflt; delays: (app.bets && app.bets.delays) || ({}) }

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
        Board { anchors.fill: parent; unit: Screen.width; layout: "side"; polling: false; v: main.v; chat: app.chat }
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

    Window {   // bets: badges on the tiles (over the L-bar's corner, or the whole screen without one); not over a full board
        transientParent: null
        title: "TV overlay bets"
        color: "transparent"
        flags: Qt.FramelessWindowHint | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus
        readonly property bool lb: app.layout === "lbar"
        width: lb ? app.geo.gridW : Screen.width
        height: lb ? app.geo.gridH : Screen.height
        visible: !!app.bets && app.bets.show && B.badges(app.bets, app.bets.n).length > 0 && (app.layout === "" || app.layout === "lbar")
        LayerShell.Window.scope: "tv-overlay"
        LayerShell.Window.layer: LayerShell.Window.LayerOverlay
        LayerShell.Window.keyboardInteractivity: LayerShell.Window.KeyboardInteractivityNone
        LayerShell.Window.activateOnShow: false
        LayerShell.Window.anchors: LayerShell.Window.AnchorTop | LayerShell.Window.AnchorLeft
        LayerShell.Window.exclusionZone: -1   // ignore the sidebar's / band's zones: the tiles start at 0,0
        Bets { anchors.fill: parent; unit: Screen.width; lbar: false; bets: app.bets }
    }

    component BandWindow: Window {   // one band (top / bottom): exactly its rect, over nothing but the kiosk's black
        id: bw
        property string edge: "top"
        readonly property var r: app.bandGeo ? app.bandGeo[edge] : null
        transientParent: null
        title: "TV overlay bands " + edge
        color: "transparent"
        flags: Qt.FramelessWindowHint | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus
        width: r ? r.w : 1
        height: r ? Math.max(1, r.h) : 1
        visible: app.bandsOn && r !== null && r.h >= app.bandMin
        LayerShell.Window.scope: "tv-overlay"
        LayerShell.Window.layer: LayerShell.Window.LayerOverlay
        LayerShell.Window.keyboardInteractivity: LayerShell.Window.KeyboardInteractivityNone
        LayerShell.Window.activateOnShow: false
        LayerShell.Window.exclusionZone: 0
        Loader {   // a fresh Bands each time it maps, so it fades in again
            anchors.fill: parent
            active: bw.visible
            sourceComponent: Bands { b: app.bands; edge: bw.edge; geo: app.bandGeo; unit: Screen.width; boardData: main.last ? main.last.data : null }
        }
    }
    BandWindow { edge: "top"; LayerShell.Window.anchors: LayerShell.Window.AnchorTop | LayerShell.Window.AnchorLeft }
    BandWindow { edge: "bottom"; LayerShell.Window.anchors: LayerShell.Window.AnchorBottom | LayerShell.Window.AnchorLeft }
}
