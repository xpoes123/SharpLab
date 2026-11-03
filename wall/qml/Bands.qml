// One band the wall's tiles leave (above, below or beside them; lbar.js wallBands has the geometry): a calm backdrop tinted by
// the teams on screen with faint court / field lines, a header strip right against each tile it owns (matchup, delayed
// score, period + clock, a speaker on the tile with the sound), and in the bottom band without the L-bar a slim strip
// of the other games (delayed too) + the watch party's chat. The data is GET <--bands>/bands (wall/bands.py).
// Calm on purpose: nothing flashes or pulses. The tint drifts over minutes in 2 s steps too small to see; the strip
// changes page with a slow fade; a new score just appears.
import QtQuick
import "lbar.js" as G
import "bands.js" as BJ

Item {
    id: root
    property var b: null                 // the /bands payload
    property var boardData: null         // the overlay's own delayed board (Board.qml last.data): wins for a game it holds
    property string edge: "top"          // top | bottom | left | right (beside the tiles: the backdrop only)
    property bool lbarShown: false       // the L-bar is on screen: it has the ticker + chat, the strip stays off
    property var geo: null               // G.wallBands(...)
    property real unit: 1920             // the screen width: 1 rem = unit / 120, like Board.qml
    readonly property real rem: unit / 120
    readonly property var band: geo ? geo[edge] : ({ x: 0, y: 0, w: 0, h: 0 })
    readonly property bool strip: edge === "bottom" && b !== null && !lbarShown
    clip: true

    // tokens.css (Board.qml)
    readonly property color cBase: "#0B0C0A"
    readonly property color cSurface: "#161714"
    readonly property color cRule: "#3A3B34"
    readonly property color cInk: "#ECE6D6"
    readonly property color cMuted: "#B3AD9D"
    readonly property color cAccent: "#FFB000"
    function pick(want) { var have = Qt.fontFamilies(); for (var i = 0; i < want.length; i++) if (have.indexOf(want[i]) >= 0) return want[i]; return "" }
    readonly property string fDisplay: pick(["Archivo", "Fira Sans Condensed", "DejaVu Sans Condensed"]) || "sans-serif"
    readonly property string fMono: pick(["IBM Plex Mono", "DejaVu Sans Mono"]) || "monospace"

    readonly property real gap: 0.6 * rem
    readonly property real headH: Math.min(5.5 * rem, band.h - 2 * gap)
    readonly property bool heads: headH >= 3.2 * rem
    // tile i's header lives in this band
    function owns(i) { return geo !== null && heads && geo.head[i] === edge }
    function game(i) { return b && b.tiles && i < b.tiles.length ? BJ.headerGame(b.tiles[i].game, boardData) : null }
    function ownsAny() { if (!geo) return false; for (var i = 0; i < geo.tiles.length; i++) if (owns(i)) return true; return false }

    // ---- the slow drift: a phase stepped every 2 s (period 4 min), never animated per frame ----
    property real phase: 0
    Timer { interval: 2000; running: root.visible; repeat: true; triggeredOnStart: true; onTriggered: root.phase = (Date.now() / 1000 % 240) / 240 * 2 * Math.PI }
    property string clock: ""
    Timer {
        interval: 10000; running: root.visible; repeat: true; triggeredOnStart: true
        onTriggered: { var d = new Date(), h = d.getHours(), m = d.getMinutes(); root.clock = ((h + 11) % 12 + 1) + ":" + (m < 10 ? "0" : "") + m }
    }

    // gentle fade in when the band appears (once; a layout change re-maps the window)
    opacity: 0
    Component.onCompleted: opacity = 1
    Behavior on opacity { NumberAnimation { duration: 1600; easing.type: Easing.InOutQuad } }

    component T: Text {
        property real size: 2 * root.rem
        property int weight: 600
        property real wdth: 84
        property bool mono: false
        textFormat: Text.PlainText
        color: root.cInk
        elide: Text.ElideRight
        maximumLineCount: 1
        font.family: mono ? root.fMono : root.fDisplay
        font.pixelSize: size
        font.weight: weight
        font.variableAxes: mono ? ({}) : ({ "wdth": wdth, "wght": weight })
    }

    // ---- backdrop ----
    Rectangle { anchors.fill: parent; color: root.cBase }
    Repeater {   // each tile touching this band glows faintly in its teams' colours, strongest at the tile's edge
        model: root.geo ? root.geo.tiles : []
        Item {
            id: col
            required property var modelData
            required property int index
            readonly property var g: root.game(index)
            readonly property bool touches: root.edge === "top" ? Math.abs(modelData.y - (root.band.y + root.band.h)) < 2
                                                               : Math.abs(modelData.y + modelData.h - root.band.y) < 2
            visible: touches
            x: modelData.x - root.band.x; width: modelData.w; height: root.height
            opacity: 0.85 + 0.15 * Math.sin(root.phase + index)
            Rectangle {
                id: glow
                anchors.fill: parent
                readonly property color a: col.g ? col.g.away.color : "#2a3350"
                readonly property color h: col.g ? col.g.home.color : "#3a2f4a"
                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: 0.0; color: Qt.alpha(glow.a, 0.26) }
                    GradientStop { position: 0.5 + 0.12 * Math.sin(root.phase); color: Qt.alpha(Qt.tint(glow.a, Qt.alpha(glow.h, 0.5)), 0.10) }
                    GradientStop { position: 1.0; color: Qt.alpha(glow.h, 0.26) }
                }
            }
            Rectangle {   // fade away from the tile into the base
                anchors.fill: parent
                gradient: Gradient {
                    GradientStop { position: 0.0; color: root.edge === "top" ? root.cBase : "transparent" }
                    GradientStop { position: 1.0; color: root.edge === "top" ? "transparent" : root.cBase }
                }
            }
        }
    }
    Canvas {   // faint court lines (NBA: the 3-point arc + the centre circle per tile) or yard lines (NFL), drawn once
        id: art
        anchors.fill: parent
        opacity: 0.07
        readonly property string key: (root.b ? root.b.league : "") + width + "x" + height + JSON.stringify(root.geo ? root.geo.tiles : [])
        onKeyChanged: requestPaint()
        onPaint: {
            var c = getContext("2d"), lg = root.b ? root.b.league : ""
            c.reset()
            c.strokeStyle = root.cInk
            c.lineWidth = Math.max(1, 0.14 * root.rem)
            if (!root.geo) return
            var T = root.geo.tiles, edgeY = root.edge === "top" ? height : 0
            for (var i = 0; i < T.length; i++) {
                var t = T[i], cx = t.x - root.band.x + t.w / 2, x0 = t.x - root.band.x
                var touches = root.edge === "top" ? Math.abs(t.y - (root.band.y + root.band.h)) < 2 : Math.abs(t.y + t.h - root.band.y) < 2
                if (!touches) continue
                if (lg === "nba") {
                    c.beginPath(); c.arc(cx, edgeY, t.w * 0.40, 0, 2 * Math.PI); c.stroke()
                    c.beginPath(); c.arc(cx, edgeY, t.w * 0.12, 0, 2 * Math.PI); c.stroke()
                } else if (lg === "nfl") {
                    for (var k = 1; k < 10; k++) {
                        var x = x0 + t.w * k / 10
                        c.beginPath(); c.moveTo(x, 0); c.lineTo(x, height); c.stroke()
                        for (var m = 1; m < 5; m++) {
                            var hx = x - t.w / 20 + t.w / 10 * m / 5
                            c.beginPath(); c.moveTo(hx, height * 0.33 - root.rem * 0.4); c.lineTo(hx, height * 0.33 + root.rem * 0.4)
                            c.moveTo(hx, height * 0.67 - root.rem * 0.4); c.lineTo(hx, height * 0.67 + root.rem * 0.4); c.stroke()
                        }
                    }
                }
            }
        }
    }

    // ---- top band, room to spare: what's on + the time, quietly ----
    Item {
        visible: root.edge === "top" && root.b !== null && root.height - (root.ownsAny() ? root.headH + 2 * root.gap : 0) >= 3.6 * root.rem
        x: 1.5 * root.rem; y: 1.2 * root.rem; width: root.width - 3 * root.rem; height: 2.4 * root.rem
        T {
            anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter; width: parent.width * 0.6
            text: root.b ? (root.b.title || "Video wall") : ""; size: 1.75 * root.rem; weight: 600; wdth: 68; color: root.cMuted
            font.capitalization: Font.AllUppercase; font.letterSpacing: size * 0.12
        }
        T { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: root.clock; mono: true; size: 1.75 * root.rem; weight: 500; color: root.cMuted }
    }

    // ---- a header strip right against each tile this band owns ----
    Repeater {
        model: root.geo ? root.geo.tiles : []
        Item {
            id: hd
            required property var modelData
            required property int index
            readonly property var tile: root.b && root.b.tiles && index < root.b.tiles.length ? root.b.tiles[index] : null
            readonly property var g: root.game(index)
            readonly property bool sound: root.b !== null && root.b.sound === index
            readonly property real s: height   // the header's own scale
            visible: root.owns(index)
            x: modelData.x - root.band.x + root.gap; width: modelData.w - 2 * root.gap; height: root.headH
            y: root.edge === "top" ? root.height - height - root.gap : root.gap

            Rectangle {   // the strip
                anchors.fill: parent; color: Qt.alpha(root.cSurface, 0.88); radius: 0.25 * root.rem
                border.color: root.cRule; border.width: Math.max(1, Math.round(0.08 * root.rem))
                clip: true
                Rectangle {
                    anchors.fill: parent; visible: hd.g !== null
                    gradient: Gradient {
                        orientation: Gradient.Horizontal
                        GradientStop { position: 0.0; color: hd.g ? Qt.alpha(hd.g.away.color, 0.38) : "transparent" }
                        GradientStop { position: 0.42; color: "transparent" }
                        GradientStop { position: 0.58; color: "transparent" }
                        GradientStop { position: 1.0; color: hd.g ? Qt.alpha(hd.g.home.color, 0.38) : "transparent" }
                    }
                }
                Rectangle { visible: hd.g !== null; width: 0.55 * root.rem; height: parent.height; color: hd.g ? hd.g.away.color : "transparent" }
                Rectangle { visible: hd.g !== null; anchors.right: parent.right; width: 0.55 * root.rem; height: parent.height; color: hd.g ? hd.g.home.color : "transparent" }
                Rectangle {   // the sound tile: an amber rule on the edge that meets the tile
                    visible: hd.sound; width: parent.width; height: Math.max(2, Math.round(0.2 * root.rem)); color: root.cAccent
                    y: root.edge === "top" ? parent.height - height : 0
                }
            }

            // away: abbr + score from the left; home: score + abbr from the right
            Row {
                visible: hd.g !== null
                anchors.left: parent.left; anchors.leftMargin: 1.4 * root.rem; anchors.verticalCenter: parent.verticalCenter
                spacing: 0.9 * root.rem
                T { anchors.verticalCenter: parent.verticalCenter; text: hd.g ? hd.g.away.abbr : ""; size: hd.s * 0.40; weight: 800; wdth: 68 }
                T { anchors.verticalCenter: parent.verticalCenter; visible: hd.g && hd.g.away.poss; text: "●"; size: hd.s * 0.18; color: root.cAccent }
                T {
                    anchors.verticalCenter: parent.verticalCenter; text: hd.g && hd.g.away.score !== null ? String(hd.g.away.score) : ""
                    size: hd.s * 0.48; weight: 800; wdth: 68; font.features: { "tnum": 1 }
                }
            }
            Row {
                visible: hd.g !== null
                anchors.right: parent.right; anchors.rightMargin: 1.4 * root.rem; anchors.verticalCenter: parent.verticalCenter
                spacing: 0.9 * root.rem
                layoutDirection: Qt.RightToLeft
                T { anchors.verticalCenter: parent.verticalCenter; text: hd.g ? hd.g.home.abbr : ""; size: hd.s * 0.40; weight: 800; wdth: 68 }
                T { anchors.verticalCenter: parent.verticalCenter; visible: hd.g && hd.g.home.poss; text: "●"; size: hd.s * 0.18; color: root.cAccent }
                T {
                    anchors.verticalCenter: parent.verticalCenter; text: hd.g && hd.g.home.score !== null ? String(hd.g.home.score) : ""
                    size: hd.s * 0.48; weight: 800; wdth: 68; font.features: { "tnum": 1 }
                }
            }
            // the middle: period + clock (or the page's site), the speaker on the tile with the sound, the delay
            Column {
                anchors.centerIn: parent
                width: parent.width * 0.36
                spacing: 0.1 * hd.s
                Row {
                    anchors.horizontalCenter: parent.horizontalCenter
                    spacing: 0.5 * root.rem
                    Speaker { visible: hd.sound; size: hd.s * 0.28; anchors.verticalCenter: parent.verticalCenter }
                    T {
                        anchors.verticalCenter: parent.verticalCenter
                        width: Math.min(implicitWidth, hd.width * 0.36 - (hd.sound ? hd.s * 0.4 : 0))
                        text: hd.g ? hd.g.status : hd.tile ? (hd.tile.label || hd.tile.host) : ""
                        size: hd.s * 0.27; weight: 700; wdth: 68
                        color: hd.g && hd.g.close && hd.g.state === "in" ? root.cAccent : hd.g && hd.g.status !== "syncing" ? root.cInk : root.cMuted
                        font.capitalization: Font.AllUppercase; font.letterSpacing: size * 0.04
                    }
                }
                T {
                    anchors.horizontalCenter: parent.horizontalCenter
                    visible: hd.g !== null && hd.g.state !== "pre" && hd.s >= 4 * root.rem
                    text: hd.tile ? Math.round(hd.tile.delay) + " s delay" : ""
                    mono: true; size: Math.max(1.1 * root.rem, hd.s * 0.16); weight: 500; color: root.cMuted
                }
            }
        }
    }

    component Speaker: Canvas {   // 🔊 drawn, so it never depends on an emoji font
        property real size: 2 * root.rem
        width: size * 1.25; height: size
        onSizeChanged: requestPaint()
        onPaint: {
            var c = getContext("2d"), s = size
            c.reset()
            c.fillStyle = root.cAccent; c.strokeStyle = root.cAccent; c.lineWidth = Math.max(1.5, s * 0.09); c.lineCap = "round"
            c.beginPath(); c.moveTo(0, s * 0.35); c.lineTo(s * 0.22, s * 0.35); c.lineTo(s * 0.5, s * 0.1); c.lineTo(s * 0.5, s * 0.9)
            c.lineTo(s * 0.22, s * 0.65); c.lineTo(0, s * 0.65); c.closePath(); c.fill()
            c.beginPath(); c.arc(s * 0.55, s * 0.5, s * 0.22, -0.9, 0.9); c.stroke()
            c.beginPath(); c.arc(s * 0.55, s * 0.5, s * 0.42, -0.9, 0.9); c.stroke()
        }
    }

    // ---- the bottom band without the L-bar: the other games + the chat, under whatever headers this band holds ----
    readonly property real infoTop: ownsAny() ? headH + 2 * gap : gap
    readonly property real stripH: 3.4 * rem
    readonly property var chatLines: b && b.chat ? b.chat : []
    readonly property bool roomy: strip && height - infoTop >= stripH + gap

    Item {   // the strip of other games, paged slowly
        id: tick
        clip: true
        visible: root.roomy && root.b && root.b.ticker.length > 0
        x: 1.5 * root.rem; y: root.height - height - root.gap
        width: root.width - 3 * root.rem - (root.chatLines.length ? chat.width + 1.5 * root.rem : 0)
        height: root.stripH
        readonly property real cellW: 20 * root.rem
        readonly property int per: Math.max(1, Math.floor((width - label.width - root.rem) / cellW))
        readonly property int pages: root.b ? Math.max(1, Math.ceil(root.b.ticker.length / per)) : 1
        property int shown: 0
        Timer {
            interval: 14000; running: tick.visible && tick.pages > 1; repeat: true
            onTriggered: turn.restart()
        }
        SequentialAnimation {
            id: turn
            NumberAnimation { target: cells; property: "opacity"; to: 0; duration: 1200; easing.type: Easing.InOutQuad }
            ScriptAction { script: tick.shown = (tick.shown + 1) % tick.pages }
            NumberAnimation { target: cells; property: "opacity"; to: 1; duration: 1200; easing.type: Easing.InOutQuad }
        }
        Rectangle { anchors.fill: parent; color: Qt.alpha(root.cSurface, 0.7); radius: 0.25 * root.rem }
        T {
            id: label
            x: 1 * root.rem; anchors.verticalCenter: parent.verticalCenter
            text: "Elsewhere"; size: 1.5 * root.rem; weight: 600; wdth: 68; color: root.cMuted
            font.capitalization: Font.AllUppercase; font.letterSpacing: size * 0.12
        }
        Row {
            id: cells
            x: label.x + label.width + 1.2 * root.rem; height: parent.height
            Repeater {
                model: root.b ? root.b.ticker.map(function (g) { return BJ.headerGame(g, root.boardData) }).slice((tick.shown % tick.pages) * tick.per, (tick.shown % tick.pages + 1) * tick.per) : []
                Item {
                    required property var modelData
                    width: tick.cellW; height: cells.height
                    Rectangle { width: Math.max(1, 0.08 * root.rem); height: parent.height * 0.5; anchors.verticalCenter: parent.verticalCenter; color: root.cRule }
                    Row {
                        x: 0.9 * root.rem; anchors.verticalCenter: parent.verticalCenter; spacing: 0.45 * root.rem
                        Rectangle { width: 0.3 * root.rem; height: 1.6 * root.rem; color: modelData.away.color; anchors.verticalCenter: parent.verticalCenter }
                        T { text: modelData.away.abbr; size: 1.8 * root.rem; weight: 700; wdth: 68; anchors.verticalCenter: parent.verticalCenter }
                        T {
                            text: modelData.state === "pre" ? "@" : (modelData.away.score ?? "") + "–" + (modelData.home.score ?? "")
                            size: 1.8 * root.rem; weight: 800; wdth: 68; font.features: { "tnum": 1 }; anchors.verticalCenter: parent.verticalCenter
                            color: modelData.state === "pre" ? root.cMuted : root.cInk
                        }
                        T { text: modelData.home.abbr; size: 1.8 * root.rem; weight: 700; wdth: 68; anchors.verticalCenter: parent.verticalCenter }
                        Rectangle { width: 0.3 * root.rem; height: 1.6 * root.rem; color: modelData.home.color; anchors.verticalCenter: parent.verticalCenter }
                        T {
                            text: modelData.status; mono: true; size: 1.35 * root.rem; weight: 500; anchors.verticalCenter: parent.verticalCenter
                            color: modelData.close && modelData.state === "in" ? root.cAccent : root.cMuted
                        }
                    }
                }
            }
        }
    }

    Column {   // the watch party's chat (the L-bar's sidebar has it when the L-bar is up): newest at the bottom
        id: chat
        visible: root.roomy && root.chatLines.length > 0
        readonly property real lineH: 2.7 * root.rem
        readonly property int fits: Math.max(1, Math.floor((root.height - root.infoTop - root.gap) / lineH))
        width: Math.min(root.width * 0.38, 46 * root.rem)
        anchors.right: parent.right; anchors.rightMargin: 1.5 * root.rem
        anchors.bottom: parent.bottom; anchors.bottomMargin: root.gap
        Repeater {
            model: root.chatLines.slice(-chat.fits)
            Item {
                required property var modelData
                width: chat.width; height: chat.lineH
                opacity: modelData.fresh ? 1 : 0.6
                Behavior on opacity { NumberAnimation { duration: 4000 } }
                Row {
                    anchors.verticalCenter: parent.verticalCenter; spacing: 0.6 * root.rem; width: parent.width
                    T {
                        id: who; visible: !modelData.sys; text: modelData.name; size: 1.75 * root.rem; weight: 700; wdth: 68
                        color: modelData.guest ? root.cMuted : root.cAccent; width: Math.min(implicitWidth, chat.width * 0.3)
                    }
                    T {
                        text: modelData.text; size: 1.75 * root.rem; weight: modelData.sys ? 500 : 600
                        color: modelData.sys ? root.cMuted : root.cInk; font.italic: modelData.sys
                        width: chat.width - (who.visible ? who.width + 0.6 * root.rem : 0)
                    }
                }
            }
        }
    }
}
