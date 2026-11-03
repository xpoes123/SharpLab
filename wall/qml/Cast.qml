// Asks KWin for a PipeWire screencast of one output (KDE's own ScreencastingRequest, zkde_screencast_unstable_v1) and
// keeps it alive for as long as this process runs. headless.py finds the node with pw-dump (owned by our nested kwin).
//   qml6 Cast.qml -- Virtual-0
import QtQuick
import org.kde.taskmanager
Item {
    ScreencastingRequest { outputName: Qt.application.arguments[Qt.application.arguments.length - 1] }
}
