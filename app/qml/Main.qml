import QtQuick
import QtQuick.Controls

// KEYZER — full UI ported from the validated HTML prototype. Single file,
// inline components, data from layouts.json (geometry) + backend bindings.
Rectangle {
    id: root
    width: 1280
    height: 800
    color: bg

    // ---------- theme ----------
    readonly property color bg: "#0b0b0e"
    readonly property color bg2: "#101015"
    readonly property color panelC: "#16161d"
    readonly property color panel2: "#1c1c25"
    readonly property color lineC: "#2a2a35"
    readonly property color line2: "#363645"
    readonly property color txt: "#e9e9ee"
    readonly property color muted: "#8b8b97"
    readonly property color muted2: "#5e5e6b"
    readonly property color green: "#44d62c"
    readonly property color greenDim: "#2f9a1f"
    readonly property color cyan: "#22c8ff"
    readonly property color greenTxt: "#eafbe6"

    // ---------- ui state ----------
    property string curDev: "tartarus"
    property string curView: ""
    property string curProfile: "Gaming"
    property string selKey: ""
    property string capValue: ""
    property bool aligning: false
    property bool lighting: false
    property bool appAware: true
    property bool listening: false
    property int litStep: 0
    property string dirtyText: "All changes saved"

    // ---------- derived ----------
    readonly property var device: backend.layouts[curDev]
    readonly property var viewObj: (device && curView && device.views[curView]) ? device.views[curView] : null
    readonly property var viewNames: backend.viewNames(curDev)
    readonly property var bindMap: {
        var p = backend.bindings[curProfile]
        return (p && p[curDev]) ? p[curDev] : ({})
    }

    // ---------- logic ----------
    function firstView(dev) { return backend.viewNames(dev)[0] }
    function selectKey(id) { selKey = id; capValue = "" }
    function deselect() { selKey = ""; capValue = "" }
    function switchDevice(dev) { curDev = dev; curView = firstView(dev); deselect() }
    function markDirty() { dirtyText = "● Unsaved → autosaving…"; dirtyTimer.restart() }
    function showToast(m) { toast.msg = m; toast.show() }
    function curBinding() { return bindMap[selKey] !== undefined ? bindMap[selKey] : "" }

    function applyBinding() {
        if (selKey === "") return
        var v = capValue !== "" ? capValue : curBinding()
        if (v === "" || v === "—") { showToast("Pick a binding first"); return }
        backend.setBinding(curProfile, curDev, selKey, v)
        markDirty(); showToast("Saved to " + curProfile + ".json")
    }
    function clearBinding() {
        if (selKey === "") return
        backend.clearBinding(curProfile, curDev, selKey)
        capValue = ""; markDirty(); showToast("Cleared")
    }
    function keyLabel(event) {
        var parts = []
        if (event.modifiers & Qt.ControlModifier) parts.push("Ctrl")
        if (event.modifiers & Qt.AltModifier) parts.push("Alt")
        if (event.modifiers & Qt.ShiftModifier) parts.push("Shift")
        var named = ({})
        named[Qt.Key_Escape] = "Esc"; named[Qt.Key_Tab] = "Tab"; named[Qt.Key_Space] = "Space"
        named[Qt.Key_Return] = "Enter"; named[Qt.Key_Enter] = "Enter"; named[Qt.Key_Backspace] = "Bksp"
        named[Qt.Key_Up] = "↑"; named[Qt.Key_Down] = "↓"; named[Qt.Key_Left] = "←"; named[Qt.Key_Right] = "→"
        var mods = [Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta]
        if (mods.indexOf(event.key) !== -1) return parts.join("+")
        var k
        if (named[event.key] !== undefined) k = named[event.key]
        else if (event.text && event.text.length === 1) k = event.text.toUpperCase()
        else k = event.text
        if (k && k !== "") parts.push(k)
        return parts.join("+")
    }

    Component.onCompleted: {
        curView = firstView(curDev)
        // offscreen QA: drive initial state from env vars
        var q = backend.qaState()
        if (q.KEYZER_DEV) switchDevice(q.KEYZER_DEV)
        if (q.KEYZER_VIEW) curView = q.KEYZER_VIEW
        if (q.KEYZER_PROFILE) curProfile = q.KEYZER_PROFILE
        if (q.KEYZER_APPAWARE) appAware = (q.KEYZER_APPAWARE === "1")
        if (q.KEYZER_LIGHTING === "1") lighting = true
        if (q.KEYZER_ALIGN === "1") aligning = true
        if (q.KEYZER_SELECT) selectKey(q.KEYZER_SELECT)
    }

    Timer { id: dirtyTimer; interval: 1400; onTriggered: root.dirtyText = "All changes saved" }
    Timer { running: root.lighting; interval: 220; repeat: true; onTriggered: root.litStep++ }

    // ================= inline components =================
    component FlatSwitch: Item {
        id: sw
        property bool on: false
        property string label: ""
        property color accent: root.greenDim
        property color accentBorder: root.green
        signal toggled()
        implicitWidth: track.width + 8 + lbl.implicitWidth
        implicitHeight: 22
        Rectangle {
            id: track
            width: 40; height: 22; radius: 11
            anchors.verticalCenter: parent.verticalCenter
            color: sw.on ? sw.accent : "#2a2a35"
            border.width: 1; border.color: sw.on ? sw.accentBorder : root.line2
            Rectangle {
                width: 16; height: 16; radius: 8; y: 2
                x: sw.on ? 21 : 2
                color: sw.on ? root.greenTxt : "#9a9aa6"
                Behavior on x { NumberAnimation { duration: 120 } }
            }
        }
        Text {
            id: lbl
            anchors { left: track.right; leftMargin: 8; verticalCenter: parent.verticalCenter }
            text: sw.label; color: root.muted; font.pixelSize: 11; font.letterSpacing: 1
        }
        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: sw.toggled() }
    }

    component Chip: Rectangle {
        id: chip
        property string label: ""
        signal picked()
        implicitWidth: ctxt.implicitWidth + 20; implicitHeight: 28; radius: 7
        color: root.panel2; border.width: 1; border.color: chipMa.containsMouse ? root.greenDim : root.lineC
        Text { id: ctxt; anchors.centerIn: parent; text: chip.label; color: chipMa.containsMouse ? root.txt : root.muted; font.pixelSize: 12; font.bold: true }
        MouseArea { id: chipMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: chip.picked() }
    }

    component Hotspot: Item {
        id: hs
        property var k
        property string binding: ""
        property bool selected: false
        property int litIndex: 0
        x: k.x; y: k.y; width: k.w; height: k.h
        Rectangle {
            id: hit
            anchors.fill: parent; radius: 9
            color: hs.selected ? Qt.rgba(root.green.r, root.green.g, root.green.b, 0.12)
                 : root.aligning ? Qt.rgba(root.cyan.r, root.cyan.g, root.cyan.b, 0.10)
                 : "transparent"
            border.width: (hs.selected || root.aligning || hsMa.containsMouse) ? 2 : 0
            border.color: hs.selected ? root.green
                        : root.aligning ? root.cyan
                        : Qt.rgba(1, 1, 1, 0.55)
        }
        Rectangle {
            id: pill
            visible: hs.binding !== "" || hs.selected
            anchors.centerIn: parent
            width: Math.max(26, pillTxt.implicitWidth + 14); height: 24; radius: 6
            color: Qt.rgba(0.03, 0.035, 0.024, 0.86)
            border.width: 1
            border.color: hs.selected ? root.green : Qt.rgba(root.green.r, root.green.g, root.green.b, 0.45)
            Text {
                id: pillTxt; anchors.centerIn: parent
                text: hs.binding !== "" ? hs.binding : (hs.selected ? "·" : "")
                color: root.green; font.pixelSize: 14; font.bold: true
            }
        }
        Rectangle {
            id: glow
            visible: root.lighting
            anchors.fill: parent; radius: 9
            color: "transparent"
            border.width: 2
            border.color: Qt.hsla((((hs.litIndex * 22) + (root.litStep * 8)) % 360) / 360, 0.9, 0.55, 0.9)
        }
        MouseArea {
            id: hsMa
            anchors.fill: parent; hoverEnabled: true
            cursorShape: root.aligning ? Qt.SizeAllCursor : Qt.PointingHandCursor
            onClicked: if (!root.aligning) root.selectKey(hs.k.id)
        }
    }

    component RailDevice: Rectangle {
        id: rd
        property string devId: ""
        property string devName: ""
        property string devType: ""
        property bool active: false
        signal chosen()
        height: 54; radius: 9
        color: active ? Qt.rgba(root.green.r, root.green.g, root.green.b, 0.10)
             : rdMa.containsMouse ? root.panelC : "transparent"
        border.width: 1; border.color: active ? root.greenDim : "transparent"
        Rectangle {
            id: ico
            anchors { left: parent.left; leftMargin: 11; verticalCenter: parent.verticalCenter }
            width: 32; height: 32; radius: 8; color: root.panel2
            border.width: 1; border.color: rd.active ? root.greenDim : root.line2
            Column {
                anchors.centerIn: parent; spacing: 3
                Repeater { model: 3; Rectangle { width: 14; height: 2; radius: 1; color: rd.active ? root.green : root.muted } }
            }
        }
        Column {
            anchors { left: ico.right; leftMargin: 12; verticalCenter: parent.verticalCenter }
            spacing: 2
            Text { text: rd.devName; color: root.txt; font.pixelSize: 13; font.bold: true }
            Text { text: rd.devType; color: root.muted; font.pixelSize: 11 }
        }
        Rectangle {
            anchors { right: parent.right; rightMargin: 12; verticalCenter: parent.verticalCenter }
            width: 7; height: 7; radius: 4; color: rd.active ? root.green : "#3a3a45"
        }
        MouseArea { id: rdMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: rd.chosen() }
    }

    // ================= header =================
    Item {
        id: header
        anchors { top: parent.top; left: parent.left; right: parent.right }
        height: 58
        Rectangle { anchors.fill: parent; color: "#131318" }
        Rectangle { anchors { left: parent.left; right: parent.right; bottom: parent.bottom }height: 1; color: root.lineC }

        Row {
            anchors { left: parent.left; leftMargin: 18; verticalCenter: parent.verticalCenter }
            spacing: 11
            Canvas {
                width: 30; height: 30; anchors.verticalCenter: parent.verticalCenter
                onPaint: {
                    var c = getContext("2d"); c.reset()
                    c.strokeStyle = "#44d62c"; c.lineWidth = 3; c.lineJoin = "round"; c.lineCap = "round"
                    c.beginPath(); c.moveTo(5, 23); c.lineTo(11, 7); c.lineTo(14, 17); c.lineTo(15, 12)
                    c.lineTo(16, 17); c.lineTo(20, 7); c.lineTo(26, 23); c.stroke()
                }
            }
            Column {
                anchors.verticalCenter: parent.verticalCenter; spacing: 0
                Text { textFormat: Text.RichText; text: "KEY<font color='#44d62c'>ZER</font>"; color: root.txt; font.pixelSize: 16; font.bold: true; font.letterSpacing: 2 }
                Text { text: "VISUAL REMAPPING · LINUX"; color: root.muted2; font.pixelSize: 9; font.letterSpacing: 0.5 }
            }
        }

        Row {
            anchors { right: parent.right; rightMargin: 18; verticalCenter: parent.verticalCenter }
            spacing: 16
            // profile dropdown
            Row {
                spacing: 9; anchors.verticalCenter: parent.verticalCenter
                Text { text: "PROFILE"; color: root.muted; font.pixelSize: 11; font.letterSpacing: 1; anchors.verticalCenter: parent.verticalCenter }
                Rectangle {
                    id: profileDd
                    width: 128; height: 34; radius: 9; anchors.verticalCenter: parent.verticalCenter
                    color: root.panel2; border.width: 1; border.color: ddMa.containsMouse ? root.greenDim : root.line2
                    Text { anchors { left: parent.left; leftMargin: 12; verticalCenter: parent.verticalCenter }text: root.curProfile; color: root.txt; font.pixelSize: 13; font.bold: true }
                    Text { anchors { right: parent.right; rightMargin: 11; verticalCenter: parent.verticalCenter }text: "▾"; color: root.muted; font.pixelSize: 11 }
                    MouseArea { id: ddMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: profileMenu.open() }
                    Menu {
                        id: profileMenu; y: profileDd.height + 4
                        Repeater { model: backend.profileNames(); MenuItem { text: modelData; onTriggered: root.curProfile = text } }
                    }
                }
            }
            FlatSwitch { anchors.verticalCenter: parent.verticalCenter; label: "APP-AWARE"; on: root.appAware; onToggled: root.appAware = !root.appAware }
            FlatSwitch { anchors.verticalCenter: parent.verticalCenter; label: "LIGHTING"; on: root.lighting; onToggled: root.lighting = !root.lighting }
            FlatSwitch { anchors.verticalCenter: parent.verticalCenter; label: "ALIGN"; on: root.aligning; accent: "#1d7fa6"; accentBorder: root.cyan; onToggled: { root.aligning = !root.aligning; root.deselect() } }
        }
    }

    // ================= footer =================
    Item {
        id: footer
        anchors { bottom: parent.bottom; left: parent.left; right: parent.right }
        height: 30
        Rectangle { anchors.fill: parent; color: "#0c0c11" }
        Rectangle { anchors { left: parent.left; right: parent.right; top: parent.top }height: 1; color: root.lineC }
        Row {
            anchors { left: parent.left; leftMargin: 18; verticalCenter: parent.verticalCenter }
            spacing: 16
            Row {
                spacing: 6; anchors.verticalCenter: parent.verticalCenter
                Rectangle { width: 7; height: 7; radius: 4; color: root.green; anchors.verticalCenter: parent.verticalCenter }
                Text { text: "Engine: "; color: root.muted; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter }
            }
            Text { text: "input-remapper 2.2.1 connected"; color: root.green; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter }
            Text { text: "Device: " + (root.device ? root.device.name : ""); color: root.muted; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter }
            Text { text: "Preset: " + root.curProfile + ".json"; color: root.muted; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter }
        }
        Text { anchors { right: parent.right; rightMargin: 18; verticalCenter: parent.verticalCenter }text: root.dirtyText; color: root.muted; font.pixelSize: 12 }
    }

    // ================= body =================
    Item {
        id: body
        anchors { top: header.bottom; bottom: footer.top; left: parent.left; right: parent.right }

        // ---------- left rail ----------
        Rectangle {
            id: rail
            anchors { top: parent.top; bottom: parent.bottom; left: parent.left }
            width: 210; color: root.bg2
            Rectangle { anchors { right: parent.right; top: parent.top; bottom: parent.bottom }width: 1; color: root.lineC }
            Column {
                anchors { fill: parent; margins: 12 }
                spacing: 8
                Text { text: "DEVICES"; color: root.muted2; font.pixelSize: 10; font.letterSpacing: 1.5; bottomPadding: 6 }
                Repeater {
                    model: backend.deviceIds()
                    RailDevice {
                        width: parent.width
                        devId: modelData
                        devName: backend.layouts[modelData].name.replace("Razer ", "")
                        devType: (modelData === "tartarus" ? "Keypad · " : "Mouse · ") + backend.layouts[modelData].usb
                        active: root.curDev === modelData
                        onChosen: root.switchDevice(modelData)
                    }
                }
                Item { width: 1; height: 12 }
                Text { visible: root.appAware; text: "APP-AWARE RULES"; color: root.muted2; font.pixelSize: 10; font.letterSpacing: 1.5; bottomPadding: 4 }
                Column {
                    visible: root.appAware; width: parent.width; spacing: 6
                    Repeater {
                        model: [["blender", "Work"], ["steam_app_*", "Gaming"], ["default →", "Gaming"]]
                        Rectangle {
                            width: parent.width; height: 30; radius: 8; color: root.panelC; border.width: 1; border.color: root.lineC
                            Row {
                                anchors { left: parent.left; leftMargin: 9; verticalCenter: parent.verticalCenter }spacing: 8
                                Text { text: modelData[0]; color: (index === 2 ? root.muted2 : root.green); font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter }
                                Text { visible: index < 2; text: "→"; color: root.muted2; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter }
                                Text { text: modelData[1]; color: root.txt; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter }
                            }
                        }
                    }
                }
                Text {
                    width: parent.width; wrapMode: Text.WordWrap; topPadding: 10
                    text: root.aligning
                          ? "Drag each hotspot onto its real key (switch views too), then Copy layout."
                          : "Click a key on the device, then assign a binding — it writes to your input-remapper preset."
                    color: root.muted2; font.pixelSize: 11; lineHeight: 1.3
                }
            }
        }

        // ---------- right assign panel ----------
        Rectangle {
            id: panelArea
            anchors { top: parent.top; bottom: parent.bottom; right: parent.right }
            width: 340; color: root.bg2
            Rectangle { anchors { left: parent.left; top: parent.top; bottom: parent.bottom }width: 1; color: root.lineC }

            // empty state
            Column {
                anchors { top: parent.top; topMargin: 60; horizontalCenter: parent.horizontalCenter }
                spacing: 14; visible: root.selKey === ""
                Text { anchors.horizontalCenter: parent.horizontalCenter; text: "⊕"; color: root.green; font.pixelSize: 40; opacity: 0.6 }
                Text { horizontalAlignment: Text.AlignHCenter; text: "Select a key on the device\nto map it."; color: root.muted2; font.pixelSize: 13; lineHeight: 1.4 }
            }

            // assign body
            Column {
                anchors { fill: parent; margins: 20 }
                spacing: 16; visible: root.selKey !== ""

                Row {
                    spacing: 13; width: parent.width
                    Rectangle {
                        width: 46; height: 46; radius: 10; color: "#22331c"; border.width: 1; border.color: root.greenDim
                        Text { anchors.centerIn: parent; text: root.selKey.replace("TAR_", "").replace("NAGA_", "").slice(0, 5); color: root.green; font.pixelSize: 14; font.bold: true }
                    }
                    Column {
                        anchors.verticalCenter: parent.verticalCenter; spacing: 2
                        Text { text: root.selKey.replace(/_/g, " "); color: root.txt; font.pixelSize: 15; font.bold: true }
                        Text { text: "hotspot: " + root.selKey; color: root.muted; font.pixelSize: 12 }
                    }
                }

                Column {
                    width: parent.width; spacing: 9
                    Text { text: "OUTPUT TYPE"; color: root.muted; font.pixelSize: 11; font.letterSpacing: 1.2 }
                    Item {
                        id: seg
                        width: parent.width; height: 34
                        property string sel: "Key"
                        Rectangle { anchors.fill: parent; radius: 10; color: root.panel2; border.width: 1; border.color: root.lineC }
                        Row {
                            anchors { fill: parent; margins: 3 }spacing: 2
                            Repeater {
                                model: ["Key", "Combo", "Macro", "Mouse", "Disable"]
                                Rectangle {
                                    width: (seg.width - 6 - 8) / 5; height: parent.height; radius: 7
                                    color: seg.sel === modelData ? root.greenDim : "transparent"
                                    Text { anchors.centerIn: parent; text: modelData; font.pixelSize: 11; font.bold: true; color: seg.sel === modelData ? root.greenTxt : root.muted }
                                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                        onClicked: { seg.sel = modelData; if (modelData === "Disable") root.capValue = "Disabled" } }
                                }
                            }
                        }
                    }
                }

                Rectangle {
                    width: parent.width; radius: 14; color: root.panelC; border.width: 1; border.color: root.lineC
                    height: cardCol.implicitHeight + 32
                    Column {
                        id: cardCol
                        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 16 }
                        spacing: 13
                        Text { text: "BINDING"; color: root.muted; font.pixelSize: 11; font.letterSpacing: 1.2 }
                        Row {
                            width: parent.width; spacing: 10; height: 54
                            Rectangle {
                                width: parent.width - 90; height: 54; radius: 10; color: "#0c0c11"
                                border.width: 1; border.color: root.listening ? root.green : root.line2
                                Text {
                                    anchors.centerIn: parent
                                    text: root.listening ? "press…" : (root.capValue !== "" ? root.capValue : (root.curBinding() !== "" ? root.curBinding() : "—"))
                                    color: root.listening ? root.green : root.txt; font.pixelSize: 18; font.bold: true
                                }
                            }
                            Rectangle {
                                width: 80; height: 54; radius: 10; color: root.greenDim; border.width: 1; border.color: root.green
                                Text { anchors.centerIn: parent; text: "Listen"; color: root.greenTxt; font.pixelSize: 13; font.bold: true }
                                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: { root.listening = true; keyCatcher.forceActiveFocus() } }
                            }
                        }
                        Text { text: "QUICK PICK"; color: root.muted; font.pixelSize: 11; font.letterSpacing: 1.2 }
                        Flow {
                            width: parent.width; spacing: 6
                            Repeater {
                                model: ["Esc", "Tab", "Shift", "Ctrl", "Alt", "Space", "Enter", "↑", "↓", "←", "→", "Q", "W", "E", "R", "F", "1", "2", "3", "LMB", "RMB", "MMB"]
                                Chip { label: modelData; onPicked: root.capValue = modelData }
                            }
                        }
                    }
                }

                Row {
                    width: parent.width; spacing: 10; height: 42
                    Rectangle {
                        width: parent.width - 90; height: 42; radius: 10; color: root.greenDim; border.width: 1; border.color: root.green
                        Text { anchors.centerIn: parent; text: "Apply mapping"; color: root.greenTxt; font.pixelSize: 13; font.bold: true }
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.applyBinding() }
                    }
                    Rectangle {
                        width: 80; height: 42; radius: 10; color: root.panel2; border.width: 1; border.color: root.line2
                        Text { anchors.centerIn: parent; text: "Clear"; color: root.txt; font.pixelSize: 13; font.bold: true }
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.clearBinding() }
                    }
                }
                Text {
                    width: parent.width; wrapMode: Text.WordWrap
                    text: "Writes " + root.selKey + " → output into " + root.curProfile + ".json · reloads engine"
                    color: root.muted2; font.pixelSize: 11
                }
            }
        }

        // ---------- center stage ----------
        Item {
            id: stage
            anchors { top: parent.top; bottom: parent.bottom; left: rail.right; right: panelArea.left }
            clip: true

            // title
            Column {
                anchors { top: parent.top; left: parent.left; margins: 18 }
                spacing: 3; z: 3
                Text { text: root.device ? root.device.name : ""; color: root.txt; font.pixelSize: 18; font.bold: true }
                Text { text: root.viewObj ? (root.viewObj.sub || "") : ""; color: root.muted; font.pixelSize: 12 }
            }

            // view tabs (Naga top/side)
            Row {
                visible: root.viewNames.length > 1
                anchors { top: parent.top; topMargin: 14; horizontalCenter: parent.horizontalCenter }
                z: 3
                Rectangle {
                    width: tabRow.implicitWidth + 6; height: tabRow.implicitHeight + 6; radius: 10
                    color: root.panel2; border.width: 1; border.color: root.lineC
                    Row {
                        id: tabRow; anchors.centerIn: parent; spacing: 3
                        Repeater {
                            model: root.viewNames
                            Rectangle {
                                width: tabTxt.implicitWidth + 36; height: 30; radius: 7
                                color: root.curView === modelData ? root.greenDim : "transparent"
                                Text { id: tabTxt; anchors.centerIn: parent; text: root.viewObj && root.device.views[modelData].label ? root.device.views[modelData].label : modelData; color: root.curView === modelData ? root.greenTxt : root.muted; font.pixelSize: 12; font.bold: true }
                                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: { root.curView = modelData; root.deselect() } }
                            }
                        }
                    }
                }
            }

            // align bar
            Rectangle {
                visible: root.aligning
                anchors { top: parent.top; right: parent.right; margins: 14 }
                z: 3
                width: alignRow.implicitWidth + 24; height: 36; radius: 10
                color: Qt.rgba(0.05, 0.07, 0.086, 0.92); border.width: 1; border.color: root.cyan
                Row {
                    id: alignRow; anchors.centerIn: parent; spacing: 10
                    Text { text: "🛠 Align — drag hotspots onto keys"; color: "#bfe9fb"; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter }
                    Rectangle {
                        width: copyTxt.implicitWidth + 22; height: 26; radius: 7; color: "#1d7fa6"; border.width: 1; border.color: root.cyan; anchors.verticalCenter: parent.verticalCenter
                        Text { id: copyTxt; anchors.centerIn: parent; text: "Copy layout"; color: "#eafaff"; font.pixelSize: 12; font.bold: true }
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.copyLayout() }
                    }
                }
            }

            // device + hotspots
            Item {
                id: devHolder
                anchors.fill: parent
                Item {
                    id: dev
                    visible: root.viewObj !== null
                    width: root.viewObj ? root.viewObj.size[0] : 1
                    height: root.viewObj ? root.viewObj.size[1] : 1
                    anchors.centerIn: parent
                    scale: root.viewObj ? Math.min((parent.width - 90) / width, (parent.height - 70) / height) : 1

                    Image {
                        anchors.fill: parent
                        source: root.viewObj ? backend.imageUrl(root.viewObj.image) : ""
                        fillMode: Image.PreserveAspectFit; smooth: true
                    }
                    Repeater {
                        model: root.viewObj ? root.viewObj.keys : []
                        Hotspot {
                            k: modelData
                            litIndex: index
                            binding: root.bindMap[modelData.id] !== undefined ? root.bindMap[modelData.id] : ""
                            selected: root.selKey === modelData.id
                        }
                    }
                }
            }
        }
    }

    // ================= toast =================
    Rectangle {
        id: toast
        property string msg: ""
        function show() { opacity = 1; toastTimer.restart() }
        anchors { bottom: parent.bottom; bottomMargin: 46; horizontalCenter: parent.horizontalCenter }
        width: toastTxt.implicitWidth + 36; height: 42; radius: 10
        color: Qt.rgba(0.13, 0.2, 0.11, 0.95); border.width: 1; border.color: root.greenDim
        opacity: 0
        Behavior on opacity { NumberAnimation { duration: 220 } }
        Text { id: toastTxt; anchors.centerIn: parent; text: toast.msg; color: root.greenTxt; font.pixelSize: 13; font.bold: true }
        Timer { id: toastTimer; interval: 1900; onTriggered: toast.opacity = 0 }
    }

    // key capture for Listen
    Item {
        id: keyCatcher
        focus: false
        Keys.onPressed: function(event) {
            if (!root.listening) return
            event.accepted = true
            root.capValue = root.keyLabel(event)
            root.listening = false
        }
    }
    // copy-layout placeholder (drag-align wired in next phase)
    function copyLayout() {
        var out = {}
        for (var d in backend.layouts) {
            out[d] = {}
            var views = backend.layouts[d].views
            for (var v in views) out[d][v] = views[v].keys
        }
        backend.copyToClipboard(JSON.stringify(out))
        showToast("Layout copied")
    }

    // Escape to deselect
    Shortcut { sequence: "Escape"; onActivated: if (!root.listening) root.deselect() }
}
