import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui

// Bar button and popup for the iPhone webcam bridge. All state lives in the
// daemon: the panel reads `iphone-camera status --json` and writes through the
// same CLI, so the terminal and the bar never disagree.
Panel {
  id: root
  moduleName: "iphone-camera"
  ipcTarget: "iphone-camera"

  readonly property string cli: String(Qt.resolvedUrl("bin/iphone-camera")).replace(/^file:\/\//, "")

  property var status: ({ state: "stopped", config: {} })
  readonly property var cfg: status.config || {}
  readonly property string bridgeState: status.state || "stopped"
  readonly property bool serviceOn: bridgeState !== "stopped"
  readonly property bool live: bridgeState === "active"
  readonly property bool needsSetup: status.installed === false

  readonly property var rows: ["service", "qr", "copy", "r540", "r720", "r1080"]
  property int rowIndex: 0
  property bool cursorActive: false

  readonly property string statusText: {
    if (needsSetup) return "Setup needed"
    if (bridgeState === "active") {
      var rate = status.kbps > 1000
        ? (status.kbps / 1000).toFixed(1) + " Mbit/s"
        : (status.kbps || 0) + " kbit/s"
      return "Live · " + (status.fps || 0) + " fps · " + rate
    }
    if (bridgeState === "connected") return "Phone connected · waiting for video"
    if (bridgeState === "error") return "Error · " + (status.error || "")
    if (status.near === true) return "Ready · iPhone nearby"
    return "Off"
  }

  function refresh() {
    if (!statusProc.running) statusProc.running = true
  }

  function run(args) {
    actionProc.command = [root.cli].concat(args)
    actionProc.running = true
  }

  function resolutionIs(w, h) {
    return root.cfg.width === w && root.cfg.height === h
  }

  function activate(row) {
    if (row === "service" && root.needsSetup) {
      Quickshell.execDetached(["omarchy-launch-floating-terminal-with-presentation", root.cli + " setup"])
      root.close()
    }
    else if (row === "service") run([root.serviceOn ? "stop" : "start"])
    else if (row === "qr") {
      Quickshell.execDetached(["omarchy-launch-floating-terminal-with-presentation", root.cli + " qr"])
      root.close()
    }
    else if (row === "copy") { run(["copy"]); root.close() }
    else if (row === "r540") run(["resolution", "540p"])
    else if (row === "r720") run(["resolution", "720p"])
    else if (row === "r1080") run(["resolution", "1080p"])
  }

  function hover(row) {
    root.cursorActive = true
    root.rowIndex = root.rows.indexOf(row)
  }

  function cursorOn(row) {
    return root.cursorActive && root.rows[root.rowIndex] === row
  }

  onOpenedChanged: if (opened) { cursorActive = false; refresh() }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  Process {
    id: statusProc
    command: [root.cli, "status", "--json"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try { root.status = JSON.parse(text) } catch (e) {}
      }
    }
  }

  Process {
    id: actionProc
    onExited: root.refresh()
  }

  Timer {
    interval: root.opened ? 1000 : 4000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.serviceOn ? "󰄀" : "󱜷"
    active: root.live
    dimmed: !root.live && !root.opened
    tooltipText: "iPhone Camera · " + root.statusText
    onPressed: function(b) {
      if (b === Qt.RightButton) root.run([root.serviceOn ? "stop" : "start"])
      else root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(380))
    contentHeight: panel.fittedContentHeight(column.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onMoveRequested: function(dx, dy) {
        if (!root.cursorActive) { root.cursorActive = true; return }
        var d = dy !== 0 ? dy : dx
        root.rowIndex = Math.max(0, Math.min(root.rows.length - 1, root.rowIndex + d))
      }
      onActivateRequested: if (root.cursorActive) root.activate(root.rows[root.rowIndex])
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      Column {
        id: column
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        spacing: Style.space(12)

        // ---------- Hero ----------
        Item {
          width: parent.width
          implicitHeight: Math.max(heroIcon.implicitHeight, heroLabels.implicitHeight)

          Text {
            id: heroIcon
            text: button.text
            color: root.live ? Commons.Color.accent : root.bar.foreground
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.display
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            Behavior on color { ColorAnimation { duration: 200 } }
          }

          Column {
            id: heroLabels
            anchors.left: heroIcon.right
            anchors.leftMargin: Style.space(14)
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(2)

            Text {
              text: "iPhone Camera"
              color: root.bar.foreground
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.title
              font.bold: true
              elide: Text.ElideRight
              width: parent.width
            }

            Text {
              text: root.statusText.toUpperCase()
              color: Qt.darker(root.bar.foreground, 1.4)
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 1.2
              elide: Text.ElideRight
              width: parent.width
            }
          }
        }

        EffectToggle {
          row: "service"
          label: root.needsSetup ? "Set up the camera bridge" : "Camera bridge"
          description: root.needsSetup
            ? "Creates the virtual camera device. Asks for your password."
            : "Then pick \"" + (root.status.device || "/dev/video43") + "\" in your call app."
          checked: root.serviceOn
        }

        ActionButton {
          row: "qr"
          iconText: "󰐲"
          text: "Scan with iPhone"
          enabled: root.serviceOn
        }

        ActionButton {
          row: "copy"
          iconText: "󰆏"
          text: "Copy phone link"
          enabled: root.serviceOn
        }

        PanelSeparator { foreground: root.bar.foreground }

        PanelSectionHeader {
          text: "RESOLUTION"
          foreground: root.bar.foreground
          fontFamily: root.bar.fontFamily
        }

        Row {
          id: resRow
          width: parent.width
          spacing: Style.space(6)
          readonly property real cellWidth: (width - spacing * 2) / 3

          ResButton { row: "r540"; text: "540p"; resW: 960; resH: 540 }
          ResButton { row: "r720"; text: "720p"; resW: 1280; resH: 720 }
          ResButton { row: "r1080"; text: "1080p"; resW: 1920; resH: 1080 }
        }

        Text {
          width: parent.width
          text: "Video travels over Wi-Fi or USB. Bluetooth only notices that the phone is nearby — it is far too slow for video."
          color: Qt.darker(root.bar.foreground, 1.4)
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.caption
          wrapMode: Text.WordWrap
        }
      }
    }
  }

  component EffectToggle: Toggle {
    property string row: ""
    width: parent.width
    foreground: root.bar.foreground
    accent: Commons.Color.accent
    fontFamily: root.bar.fontFamily
    enabled: row === "service" || root.serviceOn
    opacity: enabled ? 1 : 0.5
    hasCursor: root.cursorOn(row)
    onHovered: function(h) { if (h) root.hover(row) }
    onClicked: root.activate(row)
  }

  component ActionButton: Button {
    property string row: ""
    width: parent.width
    iconSize: Style.font.title
    fontSize: Style.font.bodySmall
    foreground: root.bar.foreground
    fontFamily: root.bar.fontFamily
    horizontalPadding: Style.spacing.controlPaddingX
    verticalPadding: Style.spacing.controlPaddingY + Style.space(2)
    bordered: true
    hasCursor: root.cursorOn(row)
    onClicked: root.activate(row)
    onHovered: function(h) { if (h) root.hover(row) }
  }

  component ResButton: Button {
    property string row: ""
    property int resW: 1280
    property int resH: 720
    width: resRow.cellWidth
    iconSize: Style.font.title
    fontSize: Style.font.bodySmall
    foreground: root.bar.foreground
    fontFamily: root.bar.fontFamily
    horizontalPadding: Style.spacing.controlPaddingX
    verticalPadding: Style.spacing.controlPaddingY + Style.space(2)
    bordered: true
    active: root.resolutionIs(resW, resH)
    enabled: root.serviceOn
    opacity: enabled ? 1 : 0.5
    hasCursor: root.cursorOn(row)
    onClicked: root.activate(row)
    onHovered: function(h) { if (h) root.hover(row) }
  }
}
