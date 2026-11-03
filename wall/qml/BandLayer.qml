// Everything the tiles leave uncovered inside `geo.outer` (lbar.js wallBands): the bands above and below the tiles
// (Bands.qml: headers, backdrop, strip + chat) and the slivers / columns beside them (backdrop only). Every rect is
// painted opaque, however thin, so nothing behind the wall (the kiosk's other screens, a panel) ever shows through.
// Positioned in outer's coordinates: Overlay.qml puts it on one layer-shell surface the size of outer at its top-left.
import QtQuick

Item {
    id: bl
    property var b: null
    property var geo: null
    property real unit: 1920
    property bool lbarShown: false
    property var boardData: null
    Repeater {
        model: bl.geo ? ["top", "bottom", "left", "right"] : []
        Bands {
            required property string modelData
            readonly property var r: bl.geo[modelData]
            visible: r.w > 0 && r.h > 0
            x: r.x - bl.geo.outer.x; y: r.y - bl.geo.outer.y; width: r.w; height: r.h
            b: bl.b; edge: modelData; geo: bl.geo; unit: bl.unit; lbarShown: bl.lbarShown; boardData: bl.boardData
        }
    }
}
