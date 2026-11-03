// The overlay's content: polls the router's GET /api/overlay and draws one part of it (board.js maps the JSON).
//   side    = the L-bar's right sidebar: fantasy → his plays feed (every scoring change of his starters, big ones highlighted); NFL/NBA → only the games in progress, stacked
//             (live-close-late first), paging every 8 s; none live → "No games live · next: …" + the next few dimmed
//   band    = the L-bar's bottom band: fantasy → every league's score in one row; NFL/NBA → the ticker (live plays /
//             finals / what's next), a featured item + a crawl
//   full    = header + a grid over the tiles at 90 %
//   matchup = the open league's matchup, opaque: both starting lineups (headshot, game, actual vs live projection) under
//             the win % (Sleeper projections; none → actuals only, no odds), or his lineup + the guillotine table
// In the wall's overlay one Board polls and the L-bar's two bind their `v` to it (polling: false).
// Look = src/tv/static/design/tokens.css ("Master Control"): 1 rem = screen width / 120, Archivo condensed + IBM Plex Mono.
import QtQuick
import "board.js" as B

Item {
    id: root
    property string api: "http://127.0.0.1:7790"
    property string layout: "side"
    property string board: ""            // "" = whatever the router shows; set = preview that board
    property bool polling: true
    property real unit: width            // the SCREEN width: a sidebar is narrow but its type is TV-sized
    property real rem: unit / 120
    readonly property bool full: layout === "full"
    property var last: null
    property var v: B.view(null, 0)
    property var m: null                 // B.matchup(...) while a matchup is open
    property int pageNo: 0
    property string now: ""

    // tokens.css
    readonly property color cBg: "#0E0F0C"
    readonly property color cSurface: "#161714"
    readonly property color cSurface2: "#1F201C"
    readonly property color cRule: "#3A3B34"
    readonly property color cRuleStrong: "#5C5C51"
    readonly property color cInk: "#ECE6D6"
    readonly property color cMuted: "#B3AD9D"
    readonly property color cInkInv: "#0E0F0C"
    readonly property color cAccent: "#FFB000"
    readonly property color cAccentDim: "#6B4A00"
    readonly property color cDanger: "#FF9580"
    readonly property color cDangerFill: "#FF8670"
    readonly property color cOk: "#8ED07A"
    readonly property real tMeta: 1.75 * rem
    readonly property real tBody: 2 * rem
    readonly property real tLabel: 2.25 * rem
    readonly property real tTitle: 3.5 * rem
    readonly property real hair: Math.max(1, Math.round(0.125 * rem))
    readonly property real tally: 0.75 * rem
    readonly property real gap: 1.5 * rem

    // deploy/install-overlay-fonts.sh installs these; without them a condensed face so the cells still fit
    function pick(want) { var have = Qt.fontFamilies(); for (var i = 0; i < want.length; i++) if (have.indexOf(want[i]) >= 0) return want[i]; return "" }
    readonly property string fDisplay: pick(["Archivo", "Fira Sans Condensed", "DejaVu Sans Condensed"]) || "sans-serif"
    readonly property string fMono: pick(["IBM Plex Mono", "DejaVu Sans Mono"]) || "monospace"

    function res(r) { return r === "win" ? cOk : r === "lose" ? cDanger : cMuted }
    function tone(t) { return t === "ok" ? cOk : t === "danger" ? cDanger : t === "accent" ? cAccent : cInk }
    function hhmm() { var d = new Date(), h = d.getHours(), mi = d.getMinutes(); return ((h + 11) % 12 + 1) + ":" + (mi < 10 ? "0" : "") + mi }

    function tick() {
        now = hhmm()
        if (!polling) return
        var x = new XMLHttpRequest()
        x.onreadystatechange = function () {
            if (x.readyState !== XMLHttpRequest.DONE) return
            var s = null
            if (x.status === 200) { try { s = JSON.parse(x.responseText) } catch (e) { s = null } }
            if (s) last = s
            else if (last) last.error = "router unreachable"
            if (last) { v = B.view(last, Date.now() / 1000); m = B.matchup(last.matchup) }
        }
        x.open("GET", api + "/api/overlay" + (board ? "?board=" + encodeURIComponent(board) : ""))
        x.send()
    }
    Timer { interval: 5000; running: true; repeat: true; triggeredOnStart: true; onTriggered: root.tick() }
    Timer { interval: 8000; running: !root.full; repeat: true; onTriggered: root.pageNo++ }

    // ---- text: always plain (team / league / player names come from the internet) ----
    component T: Text {
        property real size: root.tBody
        property int weight: 600
        property real wdth: 84               // Archivo width axis; tokens: 68 condensed, 84 semi
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
    component Mono: T { mono: true; size: root.tMeta; weight: 500; color: root.cMuted }
    component Caps: T { wdth: 68; font.capitalization: Font.AllUppercase; font.letterSpacing: size * 0.06 }
    component Num: T { weight: 800; wdth: 68; font.features: { "tnum": 1 } }

    // ---- cells ----
    component TeamRow: Item {
        property var t
        width: parent.width
        height: 2.4 * root.rem
        Rectangle { id: chip; width: 0.45 * root.rem; height: 1.8 * root.rem; anchors.verticalCenter: parent.verticalCenter; color: t.color }
        Image {
            id: logo; x: chip.width + 0.5 * root.rem; width: 2.4 * root.rem; height: width; anchors.verticalCenter: parent.verticalCenter
            source: t.logo; sourceSize.width: 96; sourceSize.height: 96; fillMode: Image.PreserveAspectFit; asynchronous: true
        }
        T {
            id: abbr; x: logo.x + logo.width + 0.5 * root.rem; width: 4.6 * root.rem; anchors.verticalCenter: parent.verticalCenter
            text: t.abbr; weight: 700; wdth: 68; color: t.lost ? root.cMuted : root.cInk
        }
        T {   // possession ball
            x: abbr.x + abbr.contentWidth + 0.35 * root.rem; anchors.verticalCenter: parent.verticalCenter
            visible: t.poss; text: "●"; size: root.tMeta; color: root.cAccent
        }
        Mono { x: abbr.x + abbr.width + 0.5 * root.rem; anchors.verticalCenter: parent.verticalCenter; text: t.record; size: 1.5 * root.rem }
        T {
            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
            text: t.score; weight: 700; wdth: 68; font.features: { "tnum": 1 }; color: t.lost ? root.cMuted : root.cInk
        }
    }

    component Tally: Rectangle {
        property color bar: root.cRuleStrong
        color: root.cSurface
        clip: true
        Rectangle { width: root.tally; height: parent.height; color: parent.bar }
    }

    component RedZone: Rectangle {
        color: root.cDangerFill; width: rzt.implicitWidth + 0.6 * root.rem; height: rzt.implicitHeight
        Mono { id: rzt; anchors.centerIn: parent; text: "RZ"; color: root.cInkInv }
    }

    component GameCell: Tally {
        property var g
        property bool wrap: false            // full: status and down & distance on lines of their own
        bar: g.close ? root.cAccent : g.live ? root.cAccentDim : root.cRuleStrong
        implicitHeight: col.implicitHeight + (wrap ? 2 * root.rem : 0)
        Column {
            id: col
            x: root.tally + (wrap ? 1.25 : 1) * root.rem
            width: parent.width - x - root.rem
            anchors.verticalCenter: parent.verticalCenter
            spacing: 0.4 * root.rem
            TeamRow { t: g.away }
            TeamRow { t: g.home }
            Row {
                visible: !wrap
                width: parent.width
                spacing: 0.4 * root.rem
                RedZone { id: rzBox; visible: g.redzone }
                Mono {
                    width: parent.width - (rzBox.visible ? rzBox.width + parent.spacing : 0)
                    text: g.status + (g.situation ? " · " + g.situation : ""); color: g.close ? root.cAccent : root.cMuted
                }
            }
            Mono { visible: wrap; width: parent.width; text: g.status; color: g.close ? root.cAccent : root.cMuted }
            Row {
                visible: wrap && g.situation !== ""
                spacing: 0.4 * root.rem
                RedZone { visible: g.redzone }
                Mono { text: g.situation; color: g.close ? root.cAccent : root.cMuted }
            }
        }
    }

    component LeagueCell: Tally {   // the L-bar's band (fantasy)
        property var l
        property bool big: false
        bar: root.res(l.result)
        Column {
            x: root.tally + 1.1 * root.rem
            width: parent.width - x - root.rem
            anchors.verticalCenter: parent.verticalCenter
            spacing: 0.3 * root.rem
            Item {   // league · win % (when there are projections)
                width: parent.width; height: Math.max(lshort.implicitHeight, lwin.implicitHeight)
                Caps {
                    id: lshort; anchors.verticalCenter: parent.verticalCenter
                    width: parent.width - (lwin.visible ? lwin.implicitWidth + 0.6 * root.rem : 0); text: l.short
                    size: big ? root.tLabel : root.tMeta; color: big ? root.cInk : root.cMuted; weight: big ? 800 : 600
                    fontSizeMode: Text.HorizontalFit; minimumPixelSize: 1.4 * root.rem
                }
                Num {
                    id: lwin; visible: l.win !== ""; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                    text: l.win; size: big ? root.tLabel : root.tMeta; color: root.tone(l.winTone); elide: Text.ElideNone
                }
            }
            T {   // one line that shrinks to fit the cell, never clipped
                width: parent.width; size: (big ? 4.5 : 3.25) * root.rem; weight: 800; wdth: 68; font.features: { "tnum": 1 }
                fontSizeMode: Text.HorizontalFit; minimumPixelSize: (big ? 2.6 : 1.4) * root.rem; elide: Text.ElideNone
                textFormat: Text.StyledText
                text: "<font color='" + root.res(l.result) + "'>" + l.me + "</font> <font color='" + root.cMuted + "'>– " + (l.opp || "bye") + "</font>"
            }
            Mono { visible: !big; width: parent.width; text: l.vs }
            T {   // the band's cells are narrower than the old sidebar: condensed, shrinking rather than eliding
                visible: big; width: parent.width; text: l.vs; size: root.tLabel; weight: 600; wdth: 68; color: root.cMuted
                fontSizeMode: Text.HorizontalFit; minimumPixelSize: 1.4 * root.rem
            }
        }
    }

    component PlayLine: Item {
        property var p
        width: parent.width
        implicitHeight: who.y + who.implicitHeight
        Row {
            id: line
            width: parent.width
            spacing: 0.4 * root.rem
            T { id: dl; text: p.delta; color: p.big ? root.cAccent : p.neg ? root.cDanger : root.cOk; weight: p.big ? 800 : 600; font.features: { "tnum": 1 } }
            T { text: p.player; color: p.big ? root.cAccent : root.cInk; weight: p.big ? 800 : 600; width: Math.min(implicitWidth, parent.width - dl.width - 0.4 * root.rem) }
        }
        Mono { id: who; y: line.implicitHeight + 0.2 * root.rem; text: p.where }
    }

    // ---- frame ----
    Rectangle {
        anchors.fill: parent
        color: root.layout === "full" ? Qt.rgba(0.055, 0.059, 0.047, 0.9) : root.cBg
    }
    Loader {
        anchors.fill: parent
        sourceComponent: root.layout === "full" ? fullView : root.layout === "band" ? (root.v.kind === "fantasy" ? leaguesBand : bandView) : root.layout === "matchup" ? matchupView : sideView
    }

    // ---- L-bar: the sidebar ----
    // fantasy: his plays feed, newest on top; scores: the games in progress (none live → the next few, dimmed)
    // his feed: every scoring change of his starters; a big one (≥ his threshold) is larger, in the accent, and pulses
    // a glow the first time it shows up
    property var seenPlays: ({})
    component FeedPlay: Item {
        id: fpi
        property var p
        readonly property color tone: p.big ? root.cAccent : p.neg ? root.cDanger : root.cOk
        readonly property real pad: p.big ? 0.6 * root.rem : 0
        width: parent.width
        implicitHeight: fp.implicitHeight + 2 * pad + 0.9 * root.rem
        Rectangle {   // a big play's card + its glow
            visible: p.big
            width: parent.width; height: fp.implicitHeight + 2 * fpi.pad
            color: Qt.rgba(1, 0.69, 0, 0.08); border.color: root.cAccent; border.width: root.hair
            Rectangle {
                id: glow; anchors.fill: parent; anchors.margins: -0.4 * root.rem; radius: 0.4 * root.rem
                color: "transparent"; border.color: root.cAccent; border.width: 0.4 * root.rem; opacity: 0
            }
            SequentialAnimation {
                id: pulse; loops: 3
                NumberAnimation { target: glow; property: "opacity"; from: 0; to: 0.9; duration: 380; easing.type: Easing.OutQuad }
                NumberAnimation { target: glow; property: "opacity"; to: 0; duration: 620; easing.type: Easing.InQuad }
            }
        }
        Component.onCompleted: {
            if (p.big && !root.seenPlays[p.key] && Date.now() / 1000 - p.t < 180) pulse.start()
            root.seenPlays[p.key] = true
        }
        Rectangle { y: fpi.pad; width: root.tally; height: fp.implicitHeight; color: fpi.tone }
        Column {
            id: fp
            x: root.tally + root.rem; y: fpi.pad; width: parent.width - x - fpi.pad
            spacing: 0.1 * root.rem
            Row {   // small: delta + name on one line; big: the delta alone, large
                width: parent.width
                spacing: 0.8 * root.rem
                Num { id: fd; text: p.delta; size: (p.big ? 4.4 : 2.6) * root.rem; color: fpi.tone }
                T {
                    visible: !p.big; anchors.baseline: fd.baseline
                    width: parent.width - fd.width - parent.spacing; text: p.player; size: root.tLabel
                    fontSizeMode: Text.HorizontalFit; minimumPixelSize: 1.5 * root.rem
                }
            }
            T {
                visible: p.big; width: parent.width; text: p.player; size: root.tTitle * 0.8; weight: 800; color: root.cAccent
                fontSizeMode: Text.HorizontalFit; minimumPixelSize: 1.6 * root.rem
            }
            T {   // league(s) · time: condensed and shrinking, so the time is never elided away
                width: parent.width; text: p.where; size: root.tMeta; weight: 500; wdth: 68; color: root.cMuted
                fontSizeMode: Text.HorizontalFit; minimumPixelSize: 1.3 * root.rem
            }
            T {   // ESPN's play text, when it named him
                visible: p.detail !== ""; width: parent.width; text: p.detail; size: 1.6 * root.rem; weight: 500; color: root.cMuted
                wrapMode: Text.WordWrap; maximumLineCount: 2
            }
        }
    }

    Component {
        id: sideView
        Item {
            readonly property bool fan: root.v.kind === "fantasy"
            readonly property bool idle: !fan && root.v.empty === "" && root.v.liveCells.length === 0
            readonly property var games: idle ? root.v.upNext : root.v.liveCells
            Rectangle { width: root.hair; height: parent.height; color: root.cAccent }
            Item {
                anchors.fill: parent
                anchors.leftMargin: 1.25 * root.rem + root.hair; anchors.rightMargin: 1.25 * root.rem
                anchors.topMargin: 1.5 * root.rem; anchors.bottomMargin: 1.25 * root.rem

                Column {
                    id: shead
                    width: parent.width
                    spacing: 0.3 * root.rem
                    Item {
                        width: parent.width; height: sname.implicitHeight
                        Caps {
                            id: sname; text: root.v.name; size: root.tTitle; weight: 800; color: root.cAccent; width: parent.width - sclock.implicitWidth - root.rem
                            fontSizeMode: Text.HorizontalFit; minimumPixelSize: root.tLabel
                        }
                        Num { id: sclock; anchors.right: parent.right; anchors.baseline: sname.baseline; text: root.now; size: root.tLabel }
                    }
                    T {
                        width: parent.width; size: root.tLabel; color: root.cInk
                        text: root.v.sub + (root.v.live > 0 ? (root.v.sub ? " · " : "") + root.v.live + " live" : "")
                        visible: text !== ""
                    }
                    Mono {
                        width: parent.width; color: root.v.error ? root.cDanger : root.cMuted
                        text: (root.v.error || root.v.meta) + (!fan && cells.pg.pages > 1 ? " · " + (cells.pg.start / Math.max(1, cells.per) + 1) + "/" + cells.pg.pages : "")
                    }
                    Item { width: 1; height: 0.6 * root.rem }
                    Rectangle { width: parent.width; height: root.hair; color: root.cRule }
                }

                Item {
                    id: cells
                    anchors.top: shead.bottom; anchors.topMargin: root.gap
                    anchors.bottom: parent.bottom
                    width: parent.width
                    clip: true
                    visible: !fan
                    readonly property real cellH: 10.4 * root.rem
                    readonly property real sp: 0.8 * root.rem
                    readonly property real headH: idleHead.visible ? idleHead.height + root.gap : 0
                    readonly property int per: Math.max(1, Math.floor((height - headH + sp) / (cellH + sp)))
                    readonly property var pg: B.page(games.length, height - headH, cellH, sp, root.pageNo)
                    Column {
                        id: idleHead
                        visible: idle
                        width: parent.width
                        spacing: 0.2 * root.rem
                        Caps { width: parent.width; text: "No games live"; size: root.tTitle; weight: 800; color: root.cMuted; fontSizeMode: Text.HorizontalFit; minimumPixelSize: root.tLabel }
                        T { width: parent.width; visible: root.v.nextAt !== ""; text: "next: " + root.v.nextAt; size: root.tLabel; color: root.cMuted }
                    }
                    Column {
                        y: cells.headH
                        width: parent.width
                        spacing: cells.sp
                        opacity: idle ? 0.5 : 1
                        Repeater {
                            model: games.slice(cells.pg.start, cells.pg.end)
                            delegate: GameCell { required property var modelData; g: modelData; wrap: true; width: cells.width; height: cells.cellH }
                        }
                    }
                    Caps { visible: root.v.empty !== ""; width: parent.width; text: root.v.empty; size: root.tLabel; color: root.cMuted }
                }

                Item {
                    anchors.top: shead.bottom; anchors.topMargin: root.gap
                    anchors.bottom: parent.bottom
                    width: parent.width
                    clip: true
                    visible: fan
                    Column {
                        width: parent.width
                        spacing: 0.6 * root.rem
                        Mono { text: "MY PLAYS"; weight: 600; color: root.cAccent }
                        Repeater {
                            model: root.v.plays
                            delegate: FeedPlay { required property var modelData; required property int index; p: modelData; opacity: Math.max(0.45, 1 - index * 0.05) }
                        }
                        Caps { visible: root.v.plays.length === 0; width: parent.width; text: root.v.empty || "No plays yet"; size: root.tLabel; color: root.cMuted }
                    }
                }
            }
        }
    }

    // ---- L-bar: the bottom band for fantasy: every league in one row (pages only past what fits) ----
    Component {
        id: leaguesBand
        Item {
            id: lb
            readonly property real sp: root.rem
            readonly property real minW: 17 * root.rem
            readonly property real avail: width - 3 * root.rem
            readonly property var pg: B.page(root.v.cells.length, avail, minW, sp, root.pageNo)
            readonly property int n: Math.max(1, pg.end - pg.start)
            Rectangle { width: parent.width; height: root.hair; color: root.cAccent }
            Row {
                x: 1.5 * root.rem; y: root.hair + 1.25 * root.rem
                spacing: lb.sp
                Repeater {
                    model: root.v.cells.slice(lb.pg.start, lb.pg.end)
                    delegate: LeagueCell {
                        required property var modelData
                        l: modelData; big: true
                        width: (lb.avail - (lb.n - 1) * lb.sp) / lb.n
                        height: lb.height - root.hair - 2.5 * root.rem
                    }
                }
            }
            Caps { visible: root.v.empty !== ""; x: 1.5 * root.rem; anchors.verticalCenter: parent.verticalCenter; text: root.v.empty; size: root.tTitle; color: root.cMuted }
        }
    }

    // ---- L-bar: the bottom band (the ticker) ----
    Component {
        id: bandView
        Item {
            id: band
            readonly property var items: root.v.ticker || []
            readonly property var feat: items.length ? items[root.pageNo % Math.min(items.length, 3)] : null
            Rectangle { width: parent.width; height: root.hair; color: root.cAccent }
            Item {
                id: kick
                x: 1.5 * root.rem; y: root.hair; width: 15 * root.rem; height: parent.height - root.hair
                Column {
                    anchors.verticalCenter: parent.verticalCenter
                    width: parent.width
                    spacing: 0.3 * root.rem
                    Caps { text: root.v.kicker || root.v.name; size: root.tTitle; weight: 800; color: root.cAccent; width: parent.width; fontSizeMode: Text.HorizontalFit; minimumPixelSize: root.tLabel }
                    Caps { text: root.v.name; size: root.tMeta; color: root.cMuted; width: parent.width; visible: root.v.kicker !== "" }
                    T { text: root.now; size: root.tLabel; weight: 700; wdth: 68; font.features: { "tnum": 1 } }
                }
            }
            Rectangle { id: krule; x: kick.x + kick.width + root.rem; y: 1.25 * root.rem; width: root.hair; height: parent.height - 2.5 * root.rem; color: root.cRule }
            Item {
                id: lane
                x: krule.x + 1.5 * root.rem; y: root.hair
                width: parent.width - x - 1.5 * root.rem; height: parent.height - root.hair
                // the featured item, rotating through the top three every 8 s
                Row {
                    id: featRow
                    y: 1.5 * root.rem
                    width: parent.width
                    spacing: 1.25 * root.rem
                    visible: band.feat !== null
                    Num { id: fh; text: band.feat ? band.feat.head : ""; size: 4 * root.rem; color: band.feat ? root.tone(band.feat.tone) : root.cInk }
                    T {
                        anchors.baseline: fh.baseline
                        width: Math.max(0, parent.width - fh.width - ft.width - 2.5 * root.rem)
                        text: band.feat ? band.feat.text : ""; size: 3 * root.rem
                    }
                    Mono { id: ft; anchors.baseline: fh.baseline; text: band.feat ? band.feat.tail : ""; size: root.tLabel; width: Math.min(implicitWidth, 24 * root.rem) }
                }
                Caps {
                    visible: band.feat === null; y: 1.5 * root.rem; size: root.tTitle; color: root.cMuted
                    text: root.v.empty || (root.v.kind === "fantasy" ? "No plays yet" : "Nothing on the wire")
                }
                // the crawl: every item, endlessly, right to left
                Rectangle {
                    id: crawlBox
                    anchors.bottom: parent.bottom; anchors.bottomMargin: 1.25 * root.rem
                    width: parent.width; height: 3.6 * root.rem
                    color: root.cSurface
                    clip: true
                    visible: band.items.length > 1
                    Row {
                        id: crawl
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 0
                        Repeater {
                            model: band.items.length > 1 ? band.items.concat(band.items) : []
                            delegate: Row {
                                required property var modelData
                                spacing: 0.6 * root.rem
                                Item { width: 0.9 * root.rem; height: 1 }
                                T { id: ch; text: modelData.head; weight: 800; wdth: 68; size: root.tLabel; color: root.tone(modelData.tone); font.features: { "tnum": 1 }; elide: Text.ElideNone }
                                T { text: modelData.text; size: root.tLabel; visible: text !== ""; elide: Text.ElideNone }
                                Mono { text: modelData.tail; anchors.baseline: ch.baseline; visible: text !== ""; elide: Text.ElideNone }
                                T { text: "◆"; size: root.tMeta; color: root.cAccentDim; anchors.verticalCenter: parent.verticalCenter; elide: Text.ElideNone }
                            }
                        }
                        NumberAnimation on x {
                            id: crawlAnim
                            from: 0; to: -crawl.width / 2
                            duration: Math.max(8000, crawl.width / 2 / (5 * root.rem) * 1000)   // 5 rem a second
                            loops: Animation.Infinite
                            running: crawlBox.visible && crawl.width > 0
                        }
                        onWidthChanged: crawlAnim.restart()
                    }
                }
            }
        }
    }

    // ---- full ----
    Component {
        id: fullView
        Item {
            Item {
                anchors.fill: parent
                anchors.leftMargin: 6 * root.rem; anchors.rightMargin: 6 * root.rem
                anchors.topMargin: 3.375 * root.rem; anchors.bottomMargin: 3.375 * root.rem

                Item {
                    id: head
                    width: parent.width; height: htitle.implicitHeight + root.rem
                    Row {
                        id: htitle
                        spacing: 0.6 * root.rem
                        Caps { text: root.v.title; size: root.tTitle; weight: 800 }
                        Caps { visible: root.v.live > 0; text: "· " + root.v.live + " live"; size: root.tTitle; weight: 800; color: root.cAccent }
                    }
                    Mono {
                        anchors.right: parent.right; anchors.baseline: htitle.bottom; anchors.baselineOffset: -0.4 * root.rem
                        text: root.v.error || root.v.meta; color: root.v.error ? root.cDanger : root.cMuted
                    }
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: root.hair; color: root.cRule }
                }

                Item {
                    id: body
                    anchors.top: head.bottom; anchors.topMargin: root.gap
                    width: parent.width; anchors.bottom: parent.bottom
                    clip: true
                    readonly property bool fan: root.v.kind === "fantasy"
                    readonly property int cols: fan ? (root.v.cells.length > 4 ? 3 : 2) : 4
                    readonly property real gridW: fan ? width - 34 * root.rem - root.gap : width
                    readonly property real cellW: (gridW - (cols - 1) * root.gap) / cols

                    Caps {
                        anchors.centerIn: parent; visible: root.v.empty !== ""
                        text: root.v.empty; size: root.tTitle; color: root.cMuted
                    }
                    Grid {
                        visible: root.v.empty === ""
                        columns: body.cols; spacing: root.gap
                        Repeater {
                            model: root.v.cells
                            delegate: Loader {
                                required property var modelData
                                width: body.cellW
                                sourceComponent: modelData.type === "game" ? gameF : leagueF
                                Component { id: gameF; GameCell { g: modelData; wrap: true; width: body.cellW } }
                                Component { id: leagueF; LeagueCard { l: modelData; width: body.cellW } }
                            }
                        }
                    }
                    Rectangle {   // his plays
                        visible: body.fan && root.v.empty === ""
                        anchors.right: parent.right
                        width: 34 * root.rem; height: parent.height
                        color: root.cSurface
                        clip: true
                        Column {
                            x: 1.5 * root.rem; y: 1.25 * root.rem; width: parent.width - 3 * root.rem
                            spacing: 0.9 * root.rem
                            Mono { text: "MY PLAYS"; weight: 600; color: root.cAccent }
                            Repeater { model: root.v.plays; delegate: PlayLine { required property var modelData; p: modelData } }
                            T { visible: root.v.plays.length === 0; text: "No plays yet"; color: root.cMuted }
                        }
                    }
                }
            }
        }
    }

    component LeagueCard: Tally {   // full
        property var l
        bar: root.res(l.result)
        implicitHeight: lc.implicitHeight + 2.5 * root.rem
        Column {
            id: lc
            x: root.tally + 1.5 * root.rem; y: 1.25 * root.rem
            width: parent.width - x - 1.5 * root.rem
            spacing: 0.5 * root.rem
            Caps { width: parent.width; text: l.name; size: root.tLabel; color: root.cMuted }
            Item {
                width: parent.width; height: mp.implicitHeight
                T { anchors.baseline: mp.baseline; width: parent.width - mp.implicitWidth - root.rem; text: l.meTeam }
                Num { id: mp; anchors.right: parent.right; text: l.me; size: 4.5 * root.rem; color: root.res(l.result) }
            }
            Mono { visible: l.chop !== ""; text: "🪓 " + l.chop; color: root.res(l.result); width: parent.width }
            Item {
                width: parent.width; height: op.implicitHeight
                T { anchors.baseline: op.baseline; width: parent.width - op.implicitWidth - root.rem; text: l.oppTeam; color: root.cMuted }
                Num { id: op; anchors.right: parent.right; text: l.opp; size: 4.5 * root.rem; color: root.cMuted }
            }
        }
    }

    // ---- matchup (opaque, full screen) ----
    // a picture from the internet (Sleeper headshot / logo / avatar); a neutral silhouette until (or unless) it loads.
    // sourceSize + fillMode match Overlay.qml's prefetch, so the decoded copy there is the one shown here
    component Pic: Rectangle {
        property string src: ""
        property bool silhouette: true
        color: silhouette ? root.cSurface2 : "transparent"
        clip: true
        Image {
            id: im; anchors.fill: parent; source: parent.src; asynchronous: true; cache: true
            sourceSize.width: 240; sourceSize.height: 240; fillMode: Image.PreserveAspectFit; smooth: true
        }
        Item {
            anchors.fill: parent; visible: parent.silhouette && im.status !== Image.Ready
            Rectangle { width: parent.height * 0.34; height: width; radius: width / 2; color: root.cRuleStrong; anchors.horizontalCenter: parent.horizontalCenter; y: parent.height * 0.16 }
            Rectangle { width: parent.height * 0.7; height: parent.height * 0.36; radius: parent.height * 0.18; color: root.cRuleStrong; anchors.horizontalCenter: parent.horizontalCenter; y: parent.height * 0.62 }
        }
    }

    component PlayerRow: Item {
        id: pr
        property var r
        width: parent.width
        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: root.hair; color: root.cRule; opacity: 0.6 }
        Mono { id: slot; width: 3.8 * root.rem; anchors.verticalCenter: parent.verticalCenter; text: r.slot; color: root.cMuted; elide: Text.ElideNone; fontSizeMode: Text.HorizontalFit; minimumPixelSize: root.rem }
        Pic {
            id: face; x: slot.width; anchors.verticalCenter: parent.verticalCenter
            height: pr.height - 0.7 * root.rem; width: height * 1.38   // Sleeper's thumbs are 350×254
            src: r.img; color: r.def ? "transparent" : root.cSurface2
        }
        Column {
            x: face.x + face.width + root.rem; anchors.verticalCenter: parent.verticalCenter
            width: score.x - x - root.rem
            spacing: Math.min(0.25 * root.rem, pr.height * 0.04)
            T { width: parent.width; text: r.name; size: Math.min(root.tLabel, pr.height * 0.42); weight: 650; color: r.empty ? root.cDanger : root.cInk }
            Row {
                width: parent.width; spacing: 0.6 * root.rem
                Mono { id: rmeta; text: r.meta; size: Math.min(root.tMeta, pr.height * 0.34); elide: Text.ElideNone; anchors.verticalCenter: parent.verticalCenter }
                Pic { visible: r.logo !== ""; src: r.logo; silhouette: false; width: 2.2 * root.rem; height: width; anchors.verticalCenter: parent.verticalCenter }
                Mono {
                    anchors.verticalCenter: parent.verticalCenter; width: parent.width - rmeta.width - (r.logo !== "" ? 2.2 * root.rem + 1.2 * root.rem : 0.6 * root.rem)
                    text: r.game; size: rmeta.size; color: r.live ? root.cAccent : root.cMuted
                }
            }
        }
        Item {   // actual (big) over the live projection (small) and a thin bar: how much of it is in
            id: score
            anchors.right: parent.right; width: 12.5 * root.rem; height: parent.height
            Num {
                id: act; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; anchors.verticalCenterOffset: -0.25 * root.rem
                text: r.pts; size: 3 * root.rem; color: r.live ? root.cAccent : r.empty ? root.cMuted : root.cInk
            }
            Mono { anchors.right: act.left; anchors.rightMargin: 0.8 * root.rem; anchors.baseline: act.baseline; text: r.proj; size: 1.5 * root.rem; elide: Text.ElideNone }
            Rectangle {
                visible: r.proj !== ""; anchors.bottom: parent.bottom; anchors.bottomMargin: 0.35 * root.rem
                width: parent.width; height: 0.3 * root.rem; color: root.cSurface2
                Rectangle { width: parent.width * r.frac; height: parent.height; color: r.live ? root.cAccent : r.done ? root.cOk : root.cRuleStrong }
            }
        }
    }

    component Lineup: Column {
        id: lu
        property var s
        property color total: root.cInk
        property real rowH: 4.6 * root.rem
        spacing: 0
        Item {
            width: parent.width; height: 6.4 * root.rem
            Pic { id: av; width: 5 * root.rem; height: width; anchors.verticalCenter: parent.verticalCenter; src: s ? s.avatar : "" }
            Column {
                x: av.width + root.rem; anchors.verticalCenter: parent.verticalCenter
                width: parent.width - x - tp.implicitWidth - root.rem
                spacing: 0.2 * root.rem
                Caps { width: parent.width; text: s ? s.team : ""; size: root.tLabel; weight: 800 }
                Mono { width: parent.width; text: s ? s.proj : ""; visible: text !== "" }
            }
            Num { id: tp; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: s ? s.pts : ""; size: 5 * root.rem; color: lu.total }
        }
        Rectangle { width: parent.width; height: root.hair; color: root.cRuleStrong }
        Repeater { model: s ? s.starters : []; delegate: PlayerRow { required property var modelData; r: modelData; height: lu.rowH } }
    }

    Component {
        id: matchupView
        Item {
            readonly property var mm: root.m
            Item {
                anchors.fill: parent
                anchors.leftMargin: 4 * root.rem; anchors.rightMargin: 4 * root.rem
                anchors.topMargin: 2.5 * root.rem; anchors.bottomMargin: 2 * root.rem

                Caps { anchors.centerIn: parent; visible: !mm; text: "Loading matchup…"; size: root.tTitle; color: root.cMuted }

                Item {
                    id: mhead
                    visible: !!mm
                    width: parent.width; height: 10 * root.rem
                    Column {
                        width: parent.width - odds.width - 3 * root.rem
                        spacing: 0.4 * root.rem
                        Caps { width: parent.width; text: mm ? mm.name : ""; size: root.tTitle; weight: 800 }
                        Mono { width: parent.width; text: (mm ? mm.where : "") + "  ·  ◀ ▶ on the phone" + (root.v.error ? "  ·  " + root.v.error : ""); color: root.v.error ? root.cDanger : root.cMuted }
                        Mono { width: parent.width; text: mm ? mm.source : ""; color: mm && mm.win !== "" ? root.cMuted : root.cDanger; size: 1.5 * root.rem }
                    }
                    Column {   // the odds: "62% to win" + a bar, then where the score stands
                        id: odds
                        anchors.right: parent.right
                        width: 34 * root.rem
                        spacing: 0.35 * root.rem
                        Row {
                            visible: mm && mm.win !== ""
                            anchors.right: parent.right
                            spacing: 0.8 * root.rem
                            Num { id: wp; text: mm ? mm.win : ""; size: 5.5 * root.rem; color: mm ? root.tone(mm.winTone) : root.cInk }
                            Caps { anchors.baseline: wp.baseline; text: mm ? mm.winLabel : ""; size: root.tLabel; weight: 800; color: root.cMuted }
                        }
                        Rectangle {
                            visible: mm && mm.win !== ""
                            width: parent.width; height: 0.8 * root.rem; color: root.cSurface2
                            Rectangle { width: parent.width * (mm ? mm.winFrac : 0); height: parent.height; color: mm ? root.tone(mm.winTone) : root.cInk }
                            Rectangle { x: parent.width / 2; width: root.hair; height: parent.height; color: root.cBg }
                        }
                        T {
                            anchors.right: parent.right; text: mm ? (mm.guillotine ? "🪓 " : "") + mm.status : ""
                            size: mm && mm.win === "" ? 4 * root.rem : root.tLabel; weight: mm && mm.win === "" ? 800 : 600
                            color: mm ? root.res(mm.result) : root.cInk
                        }
                    }
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: root.hair; color: root.cRule }
                }

                Item {
                    id: cols
                    visible: !!mm
                    anchors.top: mhead.bottom; anchors.topMargin: root.gap
                    anchors.bottom: parent.bottom
                    width: parent.width
                    clip: true
                    readonly property real colW: (width - 4 * root.rem) / 2
                    readonly property int n: mm ? Math.max(mm.me.starters.length, mm.opp ? mm.opp.starters.length : 0, 9) : 9
                    readonly property real rowH: Math.min(5.4 * root.rem, (height - 6.4 * root.rem - root.hair) / n)
                    Lineup { width: parent.colW; s: mm ? mm.me : null; total: mm ? root.res(mm.result) : root.cInk; rowH: cols.rowH }
                    Lineup { x: parent.colW + 4 * root.rem; width: parent.colW; visible: mm && !!mm.opp; s: mm ? mm.opp : null; total: root.cMuted; rowH: cols.rowH }
                    Column {   // guillotine: everyone still alive, the chop line above the last
                        x: parent.colW + 4 * root.rem; width: parent.colW
                        visible: mm && mm.guillotine
                        spacing: 0
                        Item {
                            width: parent.width; height: 6.4 * root.rem
                            Caps { id: gh; anchors.verticalCenter: parent.verticalCenter; text: "Still alive · " + (mm ? mm.standings.length : 0); size: root.tLabel; weight: 800 }
                            Mono { anchors.right: parent.right; anchors.baseline: gh.baseline; text: "proj · now" }
                        }
                        Rectangle { width: parent.width; height: root.hair; color: root.cRuleStrong }
                        Item { width: 1; height: 0.6 * root.rem }
                        Repeater {
                            model: mm && mm.guillotine ? mm.standings : []
                            delegate: Column {
                                required property var modelData
                                width: parent.width
                                Item {   // the chop line
                                    visible: modelData.chop; width: parent.width; height: visible ? 2.6 * root.rem : 0
                                    Rectangle { anchors.verticalCenter: parent.verticalCenter; width: parent.width; height: 0.25 * root.rem; color: root.cDangerFill }
                                    Rectangle {
                                        anchors.centerIn: parent; color: root.cBg; width: cl.implicitWidth + 1.2 * root.rem; height: cl.implicitHeight
                                        Mono { id: cl; anchors.centerIn: parent; text: "🪓 CHOP LINE"; color: root.cDanger; weight: 600 }
                                    }
                                }
                                Tally {
                                    width: parent.width; height: 5 * root.rem
                                    color: modelData.me ? root.cSurface2 : root.cSurface
                                    bar: modelData.me ? root.cAccent : modelData.chop ? root.cDangerFill : root.cRuleStrong
                                    Num { x: root.tally + root.rem; anchors.verticalCenter: parent.verticalCenter; text: modelData.rank; size: root.tLabel; color: root.cMuted; width: 2.5 * root.rem }
                                    Pic { id: tav; x: root.tally + 3.6 * root.rem; anchors.verticalCenter: parent.verticalCenter; width: 3.6 * root.rem; height: width; src: modelData.avatar }
                                    T {
                                        x: tav.x + tav.width + root.rem; anchors.verticalCenter: parent.verticalCenter; width: tpj.x - x - root.rem
                                        text: modelData.team + (modelData.me ? "  (you)" : ""); size: root.tLabel; weight: modelData.me ? 800 : 600
                                        color: modelData.chop ? root.cDanger : root.cInk
                                    }
                                    Mono { id: tpj; anchors.right: tpts.left; anchors.rightMargin: 1.5 * root.rem; anchors.verticalCenter: parent.verticalCenter; text: modelData.proj; size: root.tBody; width: 7.5 * root.rem; horizontalAlignment: Text.AlignRight; elide: Text.ElideNone }
                                    Num { id: tpts; anchors.right: parent.right; anchors.rightMargin: root.rem; anchors.verticalCenter: parent.verticalCenter; text: modelData.pts; size: root.tTitle; color: modelData.chop ? root.cDanger : modelData.me ? root.cAccent : root.cInk }
                                }
                                Item { width: 1; height: 0.4 * root.rem }
                            }
                        }
                    }
                }
            }
        }
    }
}
