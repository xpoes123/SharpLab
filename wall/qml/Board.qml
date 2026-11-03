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
    // spoiler delay (Overlay.qml sets them from the service's bets poll): scores + plays are shown as of (now − the
    // game's tile delay), never newer; dflt for everything else. 0 / {} = live (a preview without --bets).
    property var delays: ({})
    property real dflt: 0
    property var hist: []
    // the watch party's chat while the discord wall runs (the TV router's /api/overlay/view "chat"; null = none):
    // {count, who, lines: [{id, name, guest, text, sys, fresh}]} — the sidebar gives it its lower half. A line kept in
    // chatLines by id, so an old one (fresh false, ~20 s) dims slowly instead of being redrawn. Nothing blinks or pulses.
    property var chat: null
    ListModel { id: chatLines }
    // members' Kalshi bets (the service's /api/overlay/bets, already delayed per tile; null = none): the sidebar's Bets
    // section, and with bets.ticker a bets line in the band's crawl. Never drawn over the video.
    property var bets: null
    // election night: the state spotlight (the service's /api/overlay/spot; null = none) — a card at the top of the sidebar
    property var spot: null
    // the TV chat docked in the sidebar (the service's /bands payload, its tvchat {on, lines}; null = not here: no L-bar,
    // or the host's view hides it). ChatFloat.qml dock; while it's active the election view hides TRADES.
    property var tvchat: null
    readonly property var betRows: B.betRows(bets, bets ? bets.n : 0)
    onChatChanged: {
        var ls = (chat && chat.lines) || [], want = {}
        for (var i = 0; i < ls.length; i++) want[ls[i].id] = true
        for (var j = chatLines.count - 1; j >= 0; j--) if (!want[chatLines.get(j).mid]) chatLines.remove(j)
        for (var k = 0; k < ls.length; k++) {
            var l = ls[k], row = { mid: l.id, who: String(l.name || ""), guest: !!l.guest, body: String(l.text || ""), sys: !!l.sys, fresh: !!l.fresh }
            var at = -1
            for (var n = 0; n < chatLines.count; n++) if (chatLines.get(n).mid === l.id) { at = n; break }
            if (at < 0) chatLines.append(row)
            else chatLines.set(at, row)
        }
    }
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
    function tone(t) { return t === "ok" ? cOk : t === "danger" ? cDanger : t === "accent" ? cAccent : t === "dem" ? cDem : t === "rep" ? cRep : t === "ind" ? cInd : cInk }
    // election night: the parties' blue / red, softened (wall/pages/election/election.css); leading = the same hue dimmed
    readonly property color cDem: "#6C9BE0"
    readonly property color cRep: "#E07A6E"
    readonly property color cInd: "#B39AD9"
    readonly property var segColor: ({ dem: "#6C9BE0", demLead: "#3A5378", ind: "#B39AD9", indLead: "#5E5277", none: "#33342D", repLead: "#7A3F39", rep: "#E07A6E" })
    function hhmm() { var d = new Date(), h = d.getHours(), mi = d.getMinutes(); return ((h + 11) % 12 + 1) + ":" + (mi < 10 ? "0" : "") + mi }

    function tick() {
        now = hhmm()
        if (!polling) return
        var x = new XMLHttpRequest()
        x.onreadystatechange = function () {
            if (x.readyState !== XMLHttpRequest.DONE) return
            var s = null, t = Date.now() / 1000
            if (x.status === 200) { try { s = JSON.parse(x.responseText) } catch (e) { s = null } }
            if (s) {
                if (hist.length && hist[hist.length - 1].s.shown !== s.shown) hist = []   // another board: its own history
                var keep = dflt + 30
                for (var k in delays) keep = Math.max(keep, delays[k] + 30)
                hist = B.trim(hist.concat([{ t: t, s: s }]), t, keep)
                last = B.delayed(hist, t, delays, dflt)
            }
            else if (last) last.error = "router unreachable"
            if (last) { v = B.view(last, t); m = B.matchup(last.matchup) }
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
    // the TV chat in the sidebar's lower part: a rule + CHAT, then the lines bottom-up (ChatFloat.qml dock). Takes room
    // (height > 0) only while `on`: a line in the last 2 minutes; quiet for 2 minutes, it gives the room back.
    component TvDock: Item {
        property bool allowed: true
        property real cap: 1e9
        readonly property bool on: allowed && tdcf.active
        readonly property real room: 8 * 2.4 * tdcf.fontPx + tdhead.y + tdhead.height + 0.6 * root.rem
        visible: on
        height: on ? Math.min(room, cap) : 0
        clip: true
        Rectangle { id: tdrule; width: parent.width; height: root.hair; color: root.cRule }
        Item {
            id: tdhead
            y: tdrule.height + 0.7 * root.rem
            width: parent.width; height: tdtitle.implicitHeight
            Mono { id: tdtitle; text: "CHAT"; weight: 600; color: root.cAccent }
        }
        ChatFloat {
            id: tdcf
            dock: true
            anchors.top: tdhead.bottom; anchors.topMargin: 0.6 * root.rem; anchors.bottom: parent.bottom
            width: parent.width
            b: root.tvchat; unit: root.unit; shown: !!root.tvchat; family: root.fDisplay
        }
    }
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
    Spot {   // over the top of the sidebar only (never the band, never full screen: nothing over the video)
        z: 5; b: root; spot: root.layout === "side" ? root.spot : null
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
        height: Math.min(implicitHeight, parent.height)
    }
    Loader {
        anchors.fill: parent
        sourceComponent: root.layout === "matchup" ? matchupView : root.layout === "band" ? (root.v.kind === "fantasy" ? leaguesBand : bandView)
                         : root.v.kind === "election" ? elecView : root.layout === "full" ? fullView : sideView
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
                    anchors.bottom: betsBox.top; anchors.bottomMargin: betsBox.on || chatBox.on ? root.gap : 0
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
                    anchors.bottom: betsBox.top; anchors.bottomMargin: betsBox.on || chatBox.on ? root.gap : 0
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

                // members' bets on the wall's games: solid rows (tile number, who, side + market, entry → now, ▲/▼ %),
                // above the chat; the row takes a calm green / red tint when its price moved, easing over 1.5 s
                Item {
                    id: betsBox
                    readonly property bool on: root.betRows.length > 0
                    readonly property real rowH: 6.6 * root.rem
                    readonly property real cap: parent.height * (chatBox.on ? 0.3 : 0.45)
                    readonly property int fits: Math.max(1, Math.floor((cap - bhead.height - 0.8 * root.rem) / (rowH + 0.5 * root.rem)))
                    visible: on
                    anchors.bottom: chatBox.top; anchors.bottomMargin: on && chatBox.on ? root.gap : 0
                    width: parent.width
                    height: on ? Math.min(cap, bhead.height + 0.8 * root.rem + Math.min(fits, root.betRows.length) * (rowH + 0.5 * root.rem)) : 0
                    Rectangle { id: brule; width: parent.width; height: root.hair; color: root.cRule }
                    Item {
                        id: bhead
                        y: brule.height + 0.8 * root.rem
                        width: parent.width; height: btitle.implicitHeight
                        Mono { id: btitle; text: "BETS"; weight: 600; color: root.cAccent }
                        Mono {
                            anchors.right: parent.right; color: root.cMuted
                            text: (root.betRows.length > betsBox.fits ? "+" + (root.betRows.length - betsBox.fits) + " more · " : "") + "delayed"
                        }
                    }
                    Column {
                        anchors.top: bhead.bottom; anchors.topMargin: 0.8 * root.rem
                        width: parent.width
                        spacing: 0.5 * root.rem
                        Repeater {
                            model: root.betRows.slice(0, betsBox.fits)
                            delegate: Rectangle {
                                id: br
                                required property var modelData
                                readonly property color tone: modelData.dir === "up" ? root.cOk : modelData.dir === "down" ? root.cDanger : root.cMuted
                                width: parent.width; height: betsBox.rowH
                                color: modelData.dir === "up" ? Qt.tint(root.cSurface, "#228ED07A") : modelData.dir === "down" ? Qt.tint(root.cSurface, "#22FF9580") : root.cSurface
                                Behavior on color { ColorAnimation { duration: 1500; easing.type: Easing.InOutQuad } }
                                Rectangle {   // the tile it's on
                                    id: tno
                                    x: 0.5 * root.rem; anchors.verticalCenter: parent.verticalCenter
                                    width: 2.2 * root.rem; height: 2.2 * root.rem; color: root.cSurface2
                                    border.color: root.cRuleStrong; border.width: root.hair
                                    T { anchors.centerIn: parent; text: String(br.modelData.tile); size: 1.5 * root.rem; weight: 800; wdth: 68 }
                                }
                                T {
                                    id: bwho
                                    x: tno.x + tno.width + 0.6 * root.rem; y: 0.35 * root.rem
                                    width: Math.min(implicitWidth, 7 * root.rem)
                                    text: br.modelData.who; size: 1.75 * root.rem; weight: 700; wdth: 68; color: root.cAccent
                                }
                                T {   // side + market on its own line: the sidebar is narrow, and this is the part that matters
                                    x: bwho.x; anchors.top: bwho.bottom
                                    width: parent.width - x - 0.6 * root.rem
                                    text: br.modelData.what; size: 1.75 * root.rem; weight: 600; wdth: 68
                                }
                                Mono {
                                    id: bpct
                                    anchors.right: parent.right; anchors.rightMargin: 0.6 * root.rem; anchors.baseline: bwho.baseline
                                    text: br.modelData.pct; size: 1.6 * root.rem; weight: 600; color: br.tone
                                    Behavior on color { ColorAnimation { duration: 1500 } }
                                }
                                Mono {
                                    x: bwho.x; anchors.bottom: parent.bottom; anchors.bottomMargin: 0.35 * root.rem
                                    width: parent.width - x - 0.6 * root.rem
                                    text: br.modelData.px + (br.modelData.size ? "  " + br.modelData.size : "") + (br.modelData.pnl ? "  " + br.modelData.pnl : "")
                                    size: 1.45 * root.rem; color: root.cInk
                                }
                            }
                        }
                    }
                }

                // the TV chat at the bottom (the discord wall's chat section below takes its place when that's up)
                TvDock { id: sdock; allowed: !root.chat; anchors.bottom: parent.bottom; width: parent.width; cap: parent.height * 0.45 }

                // the watch party's chat: the lower half while the discord wall runs, newest at the bottom; what doesn't
                // fit scrolls off the top. Lines older than ~20 s dim (a slow 2 s fade) so the new ones stand out.
                Item {
                    id: chatBox
                    readonly property bool on: !!root.chat
                    visible: on
                    anchors.bottom: sdock.top
                    width: parent.width
                    height: on ? Math.round(parent.height * 0.5) : 0
                    Rectangle { id: crule; width: parent.width; height: root.hair; color: root.cRule }
                    Item {
                        id: chead
                        y: crule.height + 0.8 * root.rem
                        width: parent.width; height: ctitle.implicitHeight
                        Mono { id: ctitle; text: "CHAT"; weight: 600; color: root.cAccent; width: parent.width - ccount.implicitWidth - root.rem }
                        Mono { id: ccount; anchors.right: parent.right; text: root.chat ? root.chat.count + " watching" : ""; color: root.cInk }
                    }
                    Mono {
                        id: cwho
                        anchors.top: chead.bottom; anchors.topMargin: 0.2 * root.rem
                        width: parent.width; size: 1.5 * root.rem
                        text: root.chat ? root.chat.who : ""; visible: text !== ""
                        wrapMode: Text.Wrap; maximumLineCount: 2
                    }
                    Item {
                        anchors.top: cwho.visible ? cwho.bottom : chead.bottom; anchors.topMargin: 0.8 * root.rem
                        anchors.bottom: parent.bottom
                        width: parent.width
                        clip: true
                        Caps { visible: chatLines.count === 0; width: parent.width; text: "No messages yet"; size: root.tLabel; color: root.cMuted }
                        Column {
                            id: ccol
                            anchors.bottom: parent.bottom
                            width: parent.width
                            spacing: 0.7 * root.rem
                            Repeater {
                                model: chatLines
                                delegate: Item {
                                    id: cl
                                    required property string who
                                    required property bool guest
                                    required property string body
                                    required property bool sys
                                    required property bool fresh
                                    width: ccol.width
                                    height: cbody.y + cbody.implicitHeight
                                    opacity: fresh ? 1 : 0.5
                                    visible: y + ccol.y >= 0   // a line cut off at the top isn't drawn half
                                    Behavior on opacity { NumberAnimation { duration: 2000; easing.type: Easing.InOutQuad } }
                                    Rectangle { width: root.tally * 0.6; height: parent.height; color: cl.sys ? root.cRuleStrong : cl.guest ? root.cMuted : root.cAccent }
                                    T {
                                        id: cname
                                        x: root.tally * 0.6 + 0.8 * root.rem
                                        visible: !cl.sys
                                        width: Math.min(implicitWidth, cl.width - x - (gtag.visible ? gtag.width + 0.5 * root.rem : 0))
                                        text: cl.who; size: 1.6 * root.rem; weight: 700; wdth: 68; color: cl.guest ? root.cInk : root.cAccent
                                    }
                                    Rectangle {   // the guest marker
                                        id: gtag
                                        visible: cl.guest && !cl.sys
                                        x: cname.x + cname.width + 0.5 * root.rem; anchors.verticalCenter: cname.verticalCenter
                                        width: gtt.implicitWidth + 0.6 * root.rem; height: gtt.implicitHeight
                                        color: "transparent"; border.color: root.cRuleStrong; border.width: root.hair
                                        Mono { id: gtt; anchors.centerIn: parent; text: "GUEST"; size: 1.25 * root.rem }
                                    }
                                    T {
                                        id: cbody
                                        x: cname.x; y: cl.sys ? 0 : cname.height + 0.1 * root.rem
                                        width: cl.width - x
                                        text: cl.body; size: root.tMeta; weight: 500
                                        color: cl.sys ? root.cMuted : root.cInk
                                        wrapMode: Text.Wrap; maximumLineCount: 3
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // ---- election night (the sidebar, or the whole screen in full): balance of power per office + the latest calls ----
    // Calm: the bars ease over 3 s when a seat moves, nothing blinks; a call is just a new line on top.
    component ElecBop: Column {
        property var o
        property bool wideBop: false
        property bool compact: false   // the sidebar with a Trades section: smaller numbers, so the trades get room
        spacing: (compact ? 0.2 : 0.35) * root.rem
        Item {
            width: parent.width; height: bname.implicitHeight
            Caps { id: bname; text: o.name; size: root.tLabel; weight: 800; color: root.cInk }
            Mono { anchors.right: parent.right; anchors.baseline: bname.baseline; size: 1.4 * root.rem; text: o.winner || o.need; color: o.winner ? root.tone(o.winTone) : root.cMuted }
        }
        Item {
            width: parent.width; height: bd.implicitHeight
            Num { id: bd; text: "D " + o.dem; size: (compact ? 2.7 : 4) * root.rem; color: root.cDem }
            Num { anchors.horizontalCenter: parent.horizontalCenter; anchors.baseline: bd.baseline; text: o.ind; size: root.tLabel; color: root.cInd }
            Num { anchors.right: parent.right; anchors.baseline: bd.baseline; text: "R " + o.rep; size: (compact ? 2.7 : 4) * root.rem; color: root.cRep }
        }
        Item {
            width: parent.width; height: (compact ? 0.9 : 1.3) * root.rem
            Row {
                anchors.fill: parent
                Repeater {
                    model: o.segs
                    Rectangle {
                        required property var modelData
                        width: parent.width * modelData.f; height: parent.height; color: root.segColor[modelData.c]
                        Behavior on width { NumberAnimation { duration: 3000; easing.type: Easing.InOutQuad } }
                    }
                }
            }
            Rectangle { visible: o.tick > 0; x: parent.width * o.tick - width / 2; y: -0.35 * root.rem; width: Math.max(2, 0.2 * root.rem); height: parent.height + 0.7 * root.rem; color: root.cInk }
        }
        Item {
            width: parent.width; height: bl.implicitHeight
            Mono { id: bl; size: 1.4 * root.rem; text: o.left + (o.lead && wideBop ? " · " + o.lead : ""); width: parent.width - bn.implicitWidth - root.rem }
            Mono { id: bn; size: 1.4 * root.rem; anchors.right: parent.right; text: o.net; color: o.netTone ? root.tone(o.netTone) : root.cMuted }
        }
    }

    Component {
        id: elecView
        Item {
            readonly property var e: root.v.elec
            readonly property bool wide: root.layout === "full"
            Rectangle { visible: !wide; width: root.hair; height: parent.height; color: root.cAccent }
            Item {
                anchors.fill: parent
                anchors.leftMargin: (wide ? 6 : 1.25) * root.rem + root.hair; anchors.rightMargin: (wide ? 6 : 1.25) * root.rem
                anchors.topMargin: (wide ? 4 : 1.5) * root.rem; anchors.bottomMargin: 1.25 * root.rem
                Column {
                    id: ehead
                    width: parent.width
                    spacing: 0.3 * root.rem
                    Item {
                        width: parent.width; height: ename.implicitHeight
                        Caps { id: ename; text: "Election"; size: root.tTitle; weight: 800; color: root.cAccent; width: parent.width - eclock.implicitWidth - root.rem }
                        Num { id: eclock; anchors.right: parent.right; anchors.baseline: ename.baseline; text: root.now; size: root.tLabel }
                    }
                    Mono { width: parent.width; size: 1.4 * root.rem; color: root.v.error ? root.cDanger : root.cAccent; text: root.v.error || (e ? e.source : ""); wrapMode: Text.Wrap; maximumLineCount: 2 }
                    Mono { width: parent.width; size: 1.4 * root.rem; text: root.v.meta + (e && e.counting ? " · " + e.counting + " races counting" : "") }
                    Item { width: 1; height: 0.4 * root.rem }
                    Rectangle { width: parent.width; height: root.hair; color: root.cRule }
                }
                Caps { visible: !e; anchors.top: ehead.bottom; anchors.topMargin: root.gap; width: parent.width; text: root.v.empty || "Loading…"; size: root.tLabel; color: root.cMuted }
                Grid {   // the three offices: stacked in the sidebar, side by side full screen
                    id: bops
                    visible: !!e
                    anchors.top: ehead.bottom; anchors.topMargin: root.gap
                    width: parent.width
                    columns: wide ? 3 : 1
                    columnSpacing: 3 * root.rem; rowSpacing: (e && ((e.trades && e.trades.length) || echat.on) && !wide ? 0.9 : 1.6) * root.rem
                    Repeater {
                        model: e ? e.offices : []
                        delegate: ElecBop { required property var modelData; o: modelData; wideBop: wide; compact: !wide && ((!!e.trades && e.trades.length > 0) || echat.on); width: wide ? (bops.width - 6 * root.rem) / 3 : bops.width }
                    }
                }
                // members' midterm positions under the balance of power: who, side + race, entry → now, ▲/▼ %, the
                // race's status; once the race is called the row says so ("✓ CALLED · Hansen wins +38%") in a calm
                // green (won) or the muted ink (lost). Live (results aren't a picture), nothing blinks.
                Item {
                    id: tradesBox
                    readonly property var rows: e && e.trades ? e.trades : []
                    readonly property bool on: rows.length > 0 && !echat.on   // the chat has its room while it's active
                    readonly property real rowH: 5.3 * root.rem
                    // as many rows as fit under the offices (the calls get the rest; they're in the ticker anyway)
                    readonly property real room: parent.height - bops.y - bops.height - root.gap - 4 * root.rem
                    readonly property int fits: Math.max(1, Math.min(wide ? 6 : 4, rows.length,
                                                                     (wide ? 2 : 1) * Math.floor(room / (rowH + 0.5 * root.rem))))
                    visible: on
                    anchors.top: bops.bottom; anchors.topMargin: on ? root.gap : 0
                    width: parent.width
                    height: on ? thead.y + thead.height + 0.7 * root.rem + (wide ? Math.ceil(fits / 2) : fits) * (rowH + 0.5 * root.rem) : 0
                    Rectangle { id: trule; width: parent.width; height: root.hair; color: root.cRule }
                    Item {
                        id: thead
                        y: trule.height + 0.7 * root.rem
                        width: parent.width; height: ttitle.implicitHeight
                        Mono { id: ttitle; text: "TRADES"; weight: 600; color: root.cAccent }
                        Mono { anchors.right: parent.right; color: root.cMuted
                               text: tradesBox.rows.length > tradesBox.fits ? "+" + (tradesBox.rows.length - tradesBox.fits) + " more" : "live" }
                    }
                    Grid {
                        id: tgrid
                        anchors.top: thead.bottom; anchors.topMargin: 0.7 * root.rem
                        width: parent.width
                        columns: wide ? 2 : 1
                        columnSpacing: 1.5 * root.rem; rowSpacing: 0.5 * root.rem
                        Repeater {
                            model: tradesBox.rows.slice(0, tradesBox.fits)
                            delegate: Rectangle {
                                id: tr
                                required property var modelData
                                readonly property bool done: modelData.won !== ""
                                readonly property color tone: done ? (modelData.won === "won" ? root.cOk : root.cMuted)
                                                              : modelData.dir === "up" ? root.cOk : modelData.dir === "down" ? root.cDanger : root.cMuted
                                width: wide ? (tgrid.width - 1.5 * root.rem) / 2 : tgrid.width; height: tradesBox.rowH
                                color: done && modelData.won === "won" ? Qt.tint(root.cSurface, "#2A8ED07A")
                                     : !done && modelData.dir === "up" ? Qt.tint(root.cSurface, "#188ED07A")
                                     : !done && modelData.dir === "down" ? Qt.tint(root.cSurface, "#18FF9580") : root.cSurface
                                Behavior on color { ColorAnimation { duration: 1500; easing.type: Easing.InOutQuad } }
                                Rectangle { width: root.tally; height: parent.height; color: tr.done ? tr.tone : root.cAccent }
                                T {
                                    id: twho
                                    x: root.tally + 0.7 * root.rem; y: 0.35 * root.rem
                                    width: parent.width - x - (tr.done ? 0 : tpct.implicitWidth) - 1.2 * root.rem; elide: Text.ElideRight
                                    text: tr.done ? tr.modelData.highlight.replace(/ [+−]\d+%$/, "") : tr.modelData.who + "  " + tr.modelData.side + " " + tr.modelData.what
                                    size: (tr.done ? 1.45 : 1.6) * root.rem; weight: 700; wdth: 68; color: tr.done ? tr.tone : root.cInk
                                }
                                Mono {
                                    id: tpct
                                    anchors.right: parent.right; anchors.rightMargin: 0.6 * root.rem; anchors.baseline: twho.baseline
                                    visible: !tr.done
                                    text: tr.modelData.pct; size: 1.5 * root.rem; weight: 600; color: tr.tone
                                    Behavior on color { ColorAnimation { duration: 1500 } }
                                }
                                Mono {
                                    x: twho.x; anchors.bottom: parent.bottom; anchors.bottomMargin: 0.35 * root.rem
                                    width: parent.width - x - 0.6 * root.rem; elide: Text.ElideRight
                                    text: tr.done ? (tr.modelData.highlight.match(/[+−]\d+%$/) || [""])[0] + "  " + tr.modelData.side + " " + tr.modelData.what
                                                  + (tr.modelData.pnl ? "  " + tr.modelData.pnl : "")
                                          : tr.modelData.status + "  " + tr.modelData.px.replace("@", "") + (tr.modelData.size ? "  " + tr.modelData.size : "")
                                            + (tr.modelData.pnl ? "  " + tr.modelData.pnl : "")
                                    size: 1.35 * root.rem; color: tr.done ? root.cInk : root.cMuted
                                }
                            }
                        }
                    }
                }
                Item {
                    id: callsBox
                    visible: !!e && height > 5 * root.rem   // squeezed out by the trades: the calls are in the ticker
                    anchors.top: tradesBox.bottom; anchors.topMargin: root.gap
                    anchors.bottom: echat.top; anchors.bottomMargin: echat.on ? root.gap : 0
                    width: parent.width
                    clip: true
                    Column {
                        width: parent.width
                        spacing: 0.7 * root.rem
                        Rectangle { width: parent.width; height: root.hair; color: root.cRule }
                        Mono { text: e && e.calls.length ? (e.latest ? "LATEST" : "LATEST CALLS") : "NO CALLS YET"; weight: 600; color: root.cAccent }
                        Mono { visible: !!e && e.calls.length === 0 && e.next !== ""; width: parent.width; text: e ? e.next : ""; color: root.cInk; wrapMode: Text.Wrap; maximumLineCount: 2 }
                        Repeater {
                            model: e ? e.calls : []
                            delegate: Item {
                                required property var modelData
                                width: parent.width
                                height: ct.implicitHeight + cm.implicitHeight + 0.15 * root.rem
                                visible: y + height <= callsBox.height   // a call cut off at the bottom isn't drawn half
                                Rectangle { width: root.tally; height: parent.height; color: root.tone(modelData.tone) }
                                T { id: ct; x: root.tally + 0.8 * root.rem; width: parent.width - x; text: modelData.head + " · " + modelData.text; size: 1.8 * root.rem; weight: 700; wrapMode: Text.Wrap; maximumLineCount: 2; color: root.cInk }
                                Mono { id: cm; x: ct.x; y: ct.implicitHeight + 0.15 * root.rem; width: ct.width; size: 1.4 * root.rem; text: (modelData.flip ? "FLIP · " : "") + (modelData.tail || "earlier"); color: modelData.flip ? root.cAccent : root.cMuted }
                            }
                        }
                    }
                }
                // the TV chat, anchored to the bottom, under the offices (and the calls, when they still fit)
                TvDock {
                    id: echat
                    anchors.bottom: parent.bottom; width: parent.width
                    cap: parent.height - bops.y - bops.height - root.gap
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
            readonly property var items: (root.v.ticker || []).concat(B.betTicker(root.bets, root.bets ? root.bets.n : 0))
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
                    Caps { text: root.v.name; size: root.tMeta; color: root.cMuted; width: parent.width; visible: root.v.kicker !== "" && root.v.kicker.toLowerCase() !== String(root.v.name).toLowerCase() }
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
