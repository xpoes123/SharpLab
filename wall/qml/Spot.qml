// Election night: the state spotlight card at the top of the L-bar sidebar (wall/spotlight.py; ~30 s, or until closed).
// The state's main races (status, top two candidates), their Kalshi prices + 24 h change, a sparkline of the favourite.
// Never over the video: Board.qml places it inside the sidebar. Calm: it fades in and out over 0.6 s; the bar under it
// shrinks with the time left; nothing blinks.
import QtQuick

Rectangle {
    id: s
    property var b                     // the Board (sizes, colours, fonts)
    property var spot: null            // the service's /api/overlay/spot "spot" (null = none)
    readonly property var card: spot ? spot.card : null
    readonly property real rem: b.rem
    color: b.cBg
    implicitHeight: col.implicitHeight + 2.4 * rem
    opacity: spot ? 1 : 0
    visible: opacity > 0
    Behavior on opacity { NumberAnimation { duration: 600; easing.type: Easing.InOutQuad } }
    // keep drawing the last card while it fades out
    property var shown: null
    onSpotChanged: if (spot) shown = spot
    readonly property var d: shown ? shown.card : null

    function party(p) { return p === "D" ? b.cDem : p === "R" ? b.cRep : p === "I" ? b.cInd : b.cMuted }
    function pct(v) { return v === null || v === undefined ? "–" : Math.round(v) + "%" }
    function chg(v) { return v === null || v === undefined || Math.abs(v) < 0.5 ? "" : (v > 0 ? " ▲" : " ▼") + Math.abs(Math.round(v)) }
    function office(r) { return r.office === "S" ? "Senate" : r.office === "G" ? "Governor" : r.label + " House" }

    component T: Text {
        property real size: s.b.tBody
        property int weight: 600
        property real wdth: 84
        property bool mono: false
        textFormat: Text.PlainText
        color: s.b.cInk
        elide: Text.ElideRight
        maximumLineCount: 1
        font.family: mono ? s.b.fMono : s.b.fDisplay
        font.pixelSize: size
        font.weight: weight
        font.variableAxes: mono ? ({}) : ({ "wdth": wdth, "wght": weight })
    }

    Rectangle { width: s.b.hair; height: parent.height; color: s.b.cAccent }
    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: s.b.hair; color: s.b.cRule }
    Rectangle {   // the time left, shrinking smoothly (0 when it's closing)
        anchors.bottom: parent.bottom; height: Math.max(2, 0.25 * s.rem); color: s.b.cAccentDim
        width: s.spot ? parent.width * Math.max(0, s.spot.left) / (s.spot.show_s || 30) : 0
        Behavior on width { NumberAnimation { duration: 2000 } }
    }

    Column {
        id: col
        x: 1.25 * s.rem + s.b.hair; y: 1.2 * s.rem
        width: parent.width - x - 1.25 * s.rem
        spacing: 0.5 * s.rem
        Item {
            width: parent.width; height: kick.implicitHeight
            T { id: kick; text: "SPOTLIGHT"; size: 1.5 * s.rem; weight: 800; wdth: 68; color: s.b.cAccent; font.letterSpacing: 0.1 * s.rem }
            T { anchors.right: parent.right; anchors.baseline: kick.baseline; mono: true; size: 1.3 * s.rem; weight: 500; color: s.b.cMuted
                text: s.shown && s.shown.by ? "via " + s.shown.by : ""; width: parent.width - kick.implicitWidth - s.rem; horizontalAlignment: Text.AlignRight }
        }
        T { width: parent.width; text: s.shown ? s.shown.name : ""; size: s.b.tTitle; weight: 800; wdth: 68 }
        T { visible: !s.d; width: parent.width; mono: true; size: 1.4 * s.rem; color: s.shown && s.shown.error ? s.b.cDanger : s.b.cMuted
            text: s.shown && s.shown.error ? s.shown.error : "Loading races and prices…"; wrapMode: Text.Wrap; maximumLineCount: 2 }
        Repeater {
            model: s.d ? s.d.races : []
            delegate: Column {
                id: rc
                required property var modelData
                readonly property var r: modelData
                width: col.width
                spacing: 0.25 * s.rem
                Rectangle { width: parent.width; height: s.b.hair; color: s.b.cRule }
                Item { width: 1; height: 0.2 * s.rem }
                Item {
                    width: parent.width; height: on.implicitHeight
                    T { id: on; text: s.office(rc.r).toUpperCase(); size: 1.6 * s.rem; weight: 800; wdth: 68; width: parent.width * 0.55 }
                    T { anchors.right: parent.right; anchors.baseline: on.baseline; mono: true; size: 1.3 * s.rem; weight: 500
                        width: parent.width * 0.45; horizontalAlignment: Text.AlignRight
                        text: rc.r.short || rc.r.status; color: rc.r.called ? s.party(rc.r.called) : s.b.cMuted }
                }
                Repeater {   // the top two candidates, with their share once votes are in
                    model: rc.r.cands
                    delegate: Item {
                        required property var modelData
                        width: rc.width; height: cn.implicitHeight
                        Rectangle { width: 0.8 * s.rem; height: width; radius: width / 2; color: s.party(modelData.party); anchors.verticalCenter: cn.verticalCenter }
                        T { id: cn; x: 1.4 * s.rem; width: parent.width - x - cp.implicitWidth - s.rem; size: 1.9 * s.rem
                            text: modelData.name + (modelData.winner ? " ✓" : "") }
                        T { id: cp; anchors.right: parent.right; anchors.baseline: cn.baseline; size: 1.9 * s.rem; weight: 800; wdth: 68
                            text: rc.r.pct > 0 ? s.pct(modelData.pct) : "" }
                    }
                }
                Item {   // Kalshi: the two picks' implied % (+ 24 h change) beside the favourite's sparkline
                    visible: rc.r.markets.length > 0
                    width: parent.width; height: Math.max(mk.implicitHeight, 3.2 * s.rem)
                    Column {
                        id: mk
                        width: parent.width * 0.62
                        Repeater {
                            model: rc.r.markets
                            delegate: T {
                                required property var modelData
                                width: mk.width; mono: true; size: 1.4 * s.rem; weight: 600; color: s.party(modelData.party)
                                text: modelData.who.split(" ").slice(-1)[0] + " " + s.pct(modelData.prob) + s.chg(modelData.chg24)
                            }
                        }
                    }
                    Canvas {
                        id: sp
                        anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                        width: parent.width * 0.34; height: 2.8 * s.rem
                        readonly property var vals: rc.r.spark || []
                        onValsChanged: requestPaint()
                        onPaint: {
                            var c = getContext("2d"); c.reset()
                            var v = vals; if (v.length < 2) return
                            var lo = Math.min.apply(null, v), hi = Math.max.apply(null, v), span = Math.max(10, hi - lo), mid = (hi + lo) / 2; lo = Math.min(lo, mid - span / 2)
                            c.strokeStyle = String(s.party(rc.r.spark_party)); c.lineWidth = Math.max(2, 0.22 * s.rem); c.lineJoin = "round"
                            c.beginPath()
                            for (var i = 0; i < v.length; i++) {
                                var px = i / (v.length - 1) * (width - 2) + 1, py = height - 2 - (v[i] - lo) / span * (height - 4)
                                if (i === 0) c.moveTo(px, py); else c.lineTo(px, py)
                            }
                            c.stroke()
                        }
                    }
                }
                T { visible: rc.r.markets.length === 0; width: parent.width; mono: true; size: 1.3 * s.rem; weight: 500; color: s.b.cMuted; text: "no Kalshi market" }
            }
        }
        T { visible: !!s.d; width: parent.width; mono: true; size: 1.2 * s.rem; weight: 500; color: s.b.cMuted
            text: "Kalshi implied % (24 h ▲▼) · unofficial" + (s.d && s.d.more ? " · +" + s.d.more + " races" : ""); wrapMode: Text.Wrap; maximumLineCount: 2 }
    }
}
