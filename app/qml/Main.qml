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
    readonly property color greenHot: "#7dff5e"     // hottest highlight — the "lit filament"
    readonly property color cyan: "#22c8ff"
    readonly property color greenTxt: "#eafbe6"
    readonly property color bg0: "#0c0c11"
    readonly property color danger: "#d65c44"
    readonly property color amber: "#e0a83a"

    // ---------- neon: "powered on" pulse driver ----------
    // one shared low-amplitude oscillation; everything energized reads off this,
    // so the whole UI breathes in sync instead of a dozen unrelated timers.
    property real pulse: 0.0
    SequentialAnimation on pulse {
        running: true; loops: Animation.Infinite
        NumberAnimation { to: 1.0; duration: 1600; easing.type: Easing.InOutSine }
        NumberAnimation { to: 0.0; duration: 1600; easing.type: Easing.InOutSine }
    }
    // load sweep — a single horizontal light bar travels across the stage on start
    property real bootSweep: 0.0
    NumberAnimation on bootSweep { from: 0.0; to: 1.0; duration: 1100; easing.type: Easing.OutCubic; running: true }

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
    property var applyResult: null          // last Apply-to-hardware report
    property var capSummary: ({})            // per-device captured-key counts
    property bool qaLive: false              // offscreen QA: force the LIVE pill visible
    property string capSource: "none"        // 'user' | 'default' | 'none' — drives the calibrate hint
    property bool hintDismissed: false

    // ---------- derived ----------
    readonly property var device: backend.layouts[curDev]
    readonly property var viewObj: (device && curView && device.views[curView]) ? device.views[curView] : null
    readonly property var viewNames: backend.viewNames(curDev)
    readonly property var bindMap: {
        var p = backend.bindings[curProfile]
        return (p && p[curDev]) ? p[curDev] : ({})
    }
    // hotspots in the CURRENT view whose output is shared with another visible
    // hotspot (scoped to the view so cross-view dups, e.g. WASD on keypad + thumb,
    // aren't flagged when the partner is off-screen)
    readonly property var conflictKeys: {
        var m = bindMap
        var bound = ((viewObj && viewObj.keys) ? viewObj.keys : [])
            .map(function (k) { return k.id })
            .filter(function (id) { return m[id] !== undefined })
        var counts = bound.reduce(function (c, id) { c[m[id]] = (c[m[id]] || 0) + 1; return c }, ({}))
        return bound.filter(function (id) { return counts[m[id]] > 1 })
    }
    readonly property int boundCount: Object.keys(bindMap).length

    // ---------- logic ----------
    function firstView(dev) { return backend.viewNames(dev)[0] }
    function selectKey(id) { selKey = id; capValue = ""; listening = false }
    function deselect() { selKey = ""; capValue = ""; listening = false }
    function switchDevice(dev) { curDev = dev; curView = firstView(dev); deselect() }
    function markDirty() { dirtyText = "● Unsaved → autosaving…"; dirtyTimer.restart() }
    function showToast(m) { toast.msg = m; toast.show() }
    function curBinding() { return bindMap[selKey] !== undefined ? bindMap[selKey] : "" }
    property var ov: ({})   // drag-align position overrides, keyed "dev|view|id"
    function ovKey(id) { return curDev + "|" + curView + "|" + id }
    function setCoord(id, nx, ny) { var m = ov; m[ovKey(id)] = { x: Math.round(nx), y: Math.round(ny) }; ov = m }
    function alpha(c, a) { return Qt.rgba(c.r, c.g, c.b, a) }   // theme color at alpha

    function applyBinding() {
        if (selKey === "") return
        listening = false                                     // committing ends listen mode
        var v = capValue !== "" ? capValue : curBinding()
        if (v === "" || v === "—") { showToast("Pick a binding first"); return }
        backend.setBinding(curProfile, curDev, selKey, v)
        markDirty()
        var r = backend.applyToHardware(curProfile, curDev)   // set AND push live, one step
        var warn = bindWarning(r, selKey)                     // did THIS bind get dropped server-side?
        if (warn) showToast("⚠ not applied — " + warn)
        else if (r.ok) showToast(selKey.replace(/_/g, " ") + " → " + v + "  · live")
        else {
            var e = (r.devices && r.devices.length) ? (r.devices[0].error || r.message) : r.message
            showToast("Bound · " + (e || "not pushed live"))
        }
    }
    // The skip warning for `key` in an apply report (the daemon drops binds it
    // can't express), or "" if it applied cleanly. Warnings are prefixed
    // "<hotspot>: …" or "<hotspot> = …"; match the exact token so a shorter id
    // (TAR_TPAD_N) can't swallow a longer one's warning (TAR_TPAD_NE).
    function bindWarning(report, key) {
        return (report.devices || [])
            .reduce(function (all, d) { return all.concat(d.warnings || []) }, [])
            .find(function (w) { return w.indexOf(key + ":") === 0 || w.indexOf(key + " ") === 0 }) || ""
    }
    function clearBinding() {
        if (selKey === "") return
        listening = false
        backend.clearBinding(curProfile, curDev, selKey)
        capValue = ""; markDirty()
        var r = backend.applyToHardware(curProfile, curDev)
        showToast(r.ok ? "Cleared · live" : "Cleared")
    }
    function applyToHardware() {
        if (!backend.deps.inputRemapper) { showToast("input-remapper not found"); return }
        showToast("Applying " + curProfile + " to your hardware…")
        applyTimer.restart()   // defer so the toast paints before the (blocking) call
    }
    function syncProfile() { curProfile = backend.activeProfile }
    function exportProfile() {
        var r = backend.exportProfile(curProfile)
        if (r.ok) { backend.copyToClipboard(r.json); showToast("“" + r.name + "” copied — paste to share") }
        else showToast(r.error)
    }
    function stopHardware() {
        var r = backend.stopAll()
        showToast(r.ok ? "Remapping stopped — devices back to default" : (r.error || "Stop failed"))
    }
    // Keys that are modifiers on their own — a lone press of one isn't a binding.
    readonly property var modifierKeys: [Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta,
                                         Qt.Key_Super_L, Qt.Key_Super_R, Qt.Key_AltGr]
    function isBareModifier(key) { return modifierKeys.indexOf(key) !== -1 }
    function keyLabel(event) {
        var parts = []
        if (event.modifiers & Qt.ControlModifier) parts.push("Ctrl")
        if (event.modifiers & Qt.AltModifier) parts.push("Alt")
        if (event.modifiers & Qt.ShiftModifier) parts.push("Shift")
        var named = ({})
        named[Qt.Key_Escape] = "Esc"; named[Qt.Key_Tab] = "Tab"; named[Qt.Key_Space] = "Space"
        named[Qt.Key_Return] = "Enter"; named[Qt.Key_Enter] = "Enter"; named[Qt.Key_Backspace] = "Bksp"
        named[Qt.Key_Up] = "↑"; named[Qt.Key_Down] = "↓"; named[Qt.Key_Left] = "←"; named[Qt.Key_Right] = "→"
        for (var fn = 1; fn <= 12; fn++) named[Qt.Key_F1 + (fn - 1)] = "F" + fn   // F1..F12 are contiguous
        if (isBareModifier(event.key)) return parts.join("+")
        var k
        if (named[event.key] !== undefined) k = named[event.key]
        else if (event.key >= Qt.Key_A && event.key <= Qt.Key_Z) k = String.fromCharCode(event.key)   // base letter
        else if (event.key >= Qt.Key_0 && event.key <= Qt.Key_9) k = String.fromCharCode(event.key)   // base digit (Shift-proof)
        else if (event.text && event.text.length === 1) k = event.text.toUpperCase()
        else k = ""
        if (k === "+") k = "plus"   // don't clash with the chord delimiter
        if (k && k !== "") parts.push(k)
        return parts.join("+")
    }

    Component.onCompleted: {
        curView = firstView(curDev)
        capSummary = backend.captureSummary()
        capSource = backend.capturesSource()
        curProfile = backend.activeProfile
        // offscreen QA: drive initial state from env vars
        var q = backend.qaState()
        if (q.KEYZER_DEV) switchDevice(q.KEYZER_DEV)
        if (q.KEYZER_VIEW) curView = q.KEYZER_VIEW
        if (q.KEYZER_PROFILE) curProfile = q.KEYZER_PROFILE
        if (q.KEYZER_APPAWARE) appAware = (q.KEYZER_APPAWARE === "1")
        if (q.KEYZER_LIGHTING === "1") lighting = true
        if (q.KEYZER_ALIGN === "1") aligning = true
        if (q.KEYZER_SELECT) selectKey(q.KEYZER_SELECT)
        if (q.KEYZER_LISTEN === "1") { if (selKey === "") selectKey("TAR_08"); listening = true }
        if (q.KEYZER_RESULT) {   // QA: render the apply-result overlay with sample data
            root.applyResult = { ok: false, message: "Applied with issues — see below.", devices: [
                { dev: "tartarus", name: "Razer Tartarus Pro", ok: true, count: 21,
                  warnings: ["TAR_TPAD = 'WASD': directional pads aren't supported yet"], error: null },
                { dev: "naga", name: "Razer Naga Pro", ok: false, count: 0,
                  warnings: [], error: "not captured — run capture.py" } ] }
            resultOverlay.visible = true
        }
        if (q.KEYZER_DIALOG === "name") nameDialog.open("create", "New profile", "")
        if (q.KEYZER_DIALOG === "import") importDialog.open()
        if (q.KEYZER_LIVE) qaLive = true
        if (q.KEYZER_HINT) capSource = "default"
        if (q.KEYZER_LIGHTPANEL) lightingOverlay.openDemo()
    }

    Timer { id: dirtyTimer; interval: 1400; onTriggered: root.dirtyText = "All changes saved" }
    Timer {
        id: applyTimer; interval: 60
        onTriggered: {
            root.applyResult = backend.applyToHardware(root.curProfile, "")
            resultOverlay.visible = true
        }
    }
    Timer { running: root.lighting; interval: 220; repeat: true; onTriggered: root.litStep++ }

    // ================= ambient backdrop (neon) =================
    // a quiet vertical wash + a faint green floor-glow so the whole surface
    // reads as "powered on", not flat black. Sits behind everything (z:-2).
    Rectangle {
        anchors.fill: parent; z: -2
        gradient: Gradient {
            GradientStop { position: 0.0; color: "#101218" }
            GradientStop { position: 0.45; color: root.bg }
            GradientStop { position: 1.0; color: "#08090c" }
        }
    }
    Rectangle {   // ambient green floor-glow, low and restrained
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        height: parent.height * 0.42; z: -2
        gradient: Gradient {
            GradientStop { position: 0.0; color: "transparent" }
            GradientStop { position: 1.0; color: root.alpha(root.green, 0.06 + 0.02 * root.pulse) }
        }
    }

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
        opacity: enabled ? 1 : 0.45
        Rectangle {
            id: track
            width: 40; height: 22; radius: 11
            anchors.verticalCenter: parent.verticalCenter
            color: sw.on ? sw.accent : root.lineC
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
        property bool conflict: false
        property string unavailable: ""
        property int litIndex: 0
        width: k.w; height: k.h
        opacity: hs.unavailable !== "" ? 0.45 : 1
        Component.onCompleted: { var o = root.ov[root.ovKey(k.id)]; x = o ? o.x : k.x; y = o ? o.y : k.y }
        // ---- signature: the lit selected key. layered halos breathing on the
        // shared pulse make the chosen key read like a glowing, powered filament.
        Rectangle {   // outermost soft bloom
            visible: hs.selected
            anchors.fill: parent; anchors.margins: -16; radius: 22
            color: root.alpha(root.green, 0.07 + 0.05 * root.pulse)
        }
        Rectangle {   // mid halo
            visible: hs.selected
            anchors.fill: parent; anchors.margins: -8; radius: 15
            color: root.alpha(root.green, 0.16 + 0.06 * root.pulse)
        }
        Rectangle {
            id: hit
            anchors.fill: parent; radius: 9
            color: hs.selected ? root.alpha(root.green, 0.18 + 0.06 * root.pulse)
                 : root.aligning ? root.alpha(root.cyan, 0.10)
                 : hov.hovered ? root.alpha(root.green, 0.07)
                 : "transparent"
            border.width: (hs.selected || root.aligning || hov.hovered) ? 2 : 0
            border.color: hs.selected ? root.greenHot
                        : root.aligning ? root.cyan
                        : Qt.rgba(1, 1, 1, 0.55)
        }
        Rectangle {   // bright inner rim ring on the selected key — the "on" filament edge
            visible: hs.selected
            anchors.fill: parent; anchors.margins: 1; radius: 8
            color: "transparent"
            border.width: 1
            border.color: root.alpha(root.greenHot, 0.5 + 0.4 * root.pulse)
        }
        Rectangle {
            id: pill
            visible: hs.binding !== "" || hs.selected || hs.unavailable !== ""
            anchors.centerIn: parent
            width: Math.max(26, pillTxt.implicitWidth + 14); height: 24; radius: 6
            color: Qt.rgba(0.03, 0.035, 0.024, 0.86)
            border.width: hs.selected ? 1.5 : 1
            border.color: hs.unavailable !== "" ? root.line2
                        : hs.conflict ? root.amber
                        : hs.selected ? root.greenHot : root.alpha(root.green, 0.45)
            Text {
                id: pillTxt; anchors.centerIn: parent
                text: hs.unavailable !== "" ? "n/a" : (hs.binding !== "" ? hs.binding : (hs.selected ? "·" : ""))
                color: hs.unavailable !== "" ? root.muted2 : (hs.conflict ? root.amber : (hs.selected ? root.greenHot : root.green))
                font.pixelSize: 14; font.bold: true
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
        HoverHandler { id: hov; cursorShape: root.aligning ? Qt.SizeAllCursor : Qt.PointingHandCursor }
        TapHandler { enabled: !root.aligning; onTapped: hs.unavailable !== "" ? root.showToast(hs.unavailable) : root.selectKey(hs.k.id) }
        DragHandler { enabled: root.aligning; target: hs; onActiveChanged: if (!active) root.setCoord(hs.k.id, hs.x, hs.y) }
    }

    component RailDevice: Rectangle {
        id: rd
        property string devName: ""
        property string devType: ""
        property bool active: false
        signal chosen()
        height: 54; radius: 9
        color: active ? root.alpha(root.green, 0.10 + 0.03 * root.pulse)
             : rdMa.containsMouse ? root.panelC : "transparent"
        border.width: 1; border.color: active ? root.alpha(root.green, 0.55 + 0.3 * root.pulse) : "transparent"
        Rectangle {   // left edge "power bar" — lights up on the active device
            visible: rd.active
            anchors { left: parent.left; top: parent.top; bottom: parent.bottom; topMargin: 8; bottomMargin: 8 }
            width: 3; radius: 2
            gradient: Gradient {
                GradientStop { position: 0.0; color: root.alpha(root.greenHot, 0.4) }
                GradientStop { position: 0.5; color: root.greenHot }
                GradientStop { position: 1.0; color: root.alpha(root.greenHot, 0.4) }
            }
        }
        Rectangle {
            id: ico
            anchors { left: parent.left; leftMargin: 11; verticalCenter: parent.verticalCenter }
            width: 32; height: 32; radius: 8; color: rd.active ? root.alpha(root.green, 0.14) : root.panel2
            border.width: 1; border.color: rd.active ? root.green : root.line2
            Column {
                anchors.centerIn: parent; spacing: 3
                Repeater { model: 3; Rectangle { width: 14; height: 2; radius: 1; color: rd.active ? root.greenHot : root.muted } }
            }
        }
        Column {
            anchors { left: ico.right; leftMargin: 12; verticalCenter: parent.verticalCenter }
            spacing: 2
            Text { text: rd.devName; color: root.txt; font.pixelSize: 13; font.bold: true }
            Text { text: rd.devType; color: root.muted; font.pixelSize: 11 }
        }
        Rectangle {   // status LED glow halo (behind the dot)
            visible: rd.active
            anchors { right: parent.right; rightMargin: 8.5; verticalCenter: parent.verticalCenter }
            width: 14; height: 14; radius: 7
            color: root.alpha(root.greenHot, 0.25 + 0.2 * root.pulse)
        }
        Rectangle {   // status LED — glows on the active device
            anchors { right: parent.right; rightMargin: 12; verticalCenter: parent.verticalCenter }
            width: 7; height: 7; radius: 4; color: rd.active ? root.greenHot : "#3a3a45"
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
            Image {
                width: 30; height: 30; anchors.verticalCenter: parent.verticalCenter
                source: backend.imageUrl("packaging/keyzer-mark.svg")
                sourceSize.width: 60; sourceSize.height: 60   // rasterise the SVG at 2x for crisp edges
                smooth: true; fillMode: Image.PreserveAspectFit
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
                    color: root.panel2; border.width: 1; border.color: ddMa.containsMouse ? root.green : root.alpha(root.greenDim, 0.6)
                    Rectangle {   // tiny active-profile LED
                        anchors { left: parent.left; leftMargin: 11; verticalCenter: parent.verticalCenter }
                        width: 5; height: 5; radius: 3; color: root.greenHot
                    }
                    Text { anchors { left: parent.left; leftMargin: 24; verticalCenter: parent.verticalCenter }text: root.curProfile; color: root.txt; font.pixelSize: 13; font.bold: true }
                    Text { anchors { right: parent.right; rightMargin: 11; verticalCenter: parent.verticalCenter }text: "▾"; color: root.muted; font.pixelSize: 11 }
                    MouseArea { id: ddMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: profileMenu.open() }
                    Menu {
                        id: profileMenu; y: profileDd.height + 4
                        Repeater {
                            model: backend.profileList
                            MenuItem {
                                text: (modelData === root.curProfile ? "●  " : "      ") + modelData
                                onTriggered: { backend.setActiveProfile(modelData); root.syncProfile() }
                            }
                        }
                        MenuSeparator {}
                        MenuItem { text: "＋  New profile…"; onTriggered: nameDialog.open("create", "New profile", "") }
                        MenuItem { text: "✎  Rename…"; onTriggered: nameDialog.open("rename", "Rename profile", root.curProfile) }
                        MenuItem { text: "⧉  Duplicate…"; onTriggered: nameDialog.open("duplicate", "Duplicate profile", root.curProfile + " copy") }
                        MenuSeparator {}
                        MenuItem { text: "⤓  Import…"; onTriggered: importDialog.open() }
                        MenuItem { text: "⤴  Export (copy)"; onTriggered: root.exportProfile() }
                        MenuSeparator {}
                        MenuItem { text: "🗑  Delete"; enabled: backend.profileList.length > 1
                            onTriggered: nameDialog.open("delete", "Delete profile", "") }
                    }
                }
            }
            Rectangle {
                id: applyBtn; anchors.verticalCenter: parent.verticalCenter
                width: applyRow.implicitWidth + 28; height: 34; radius: 9
                border.width: 1; border.color: applyMa.containsMouse ? "#74f562" : root.green
                gradient: Gradient {
                    GradientStop { position: 0.0; color: applyMa.containsMouse ? "#5fe245" : root.green }
                    GradientStop { position: 1.0; color: applyMa.containsMouse ? root.green : root.greenDim }
                }
                Rectangle {   // breathing halo — marks the one primary action, "powered"
                    anchors.fill: parent; anchors.margins: -7; radius: 15; z: -1
                    color: root.alpha(root.green, applyMa.containsMouse ? 0.34 : (0.13 + 0.10 * root.pulse))
                }
                Row {
                    id: applyRow; anchors.centerIn: parent; spacing: 6
                    Text { text: "⚡"; color: root.greenTxt; font.pixelSize: 13; anchors.verticalCenter: parent.verticalCenter }
                    Text { text: "Apply to device"; color: root.greenTxt; font.pixelSize: 12; font.bold: true; font.letterSpacing: 0.3; anchors.verticalCenter: parent.verticalCenter }
                }
                MouseArea { id: applyMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.applyToHardware() }
            }
            Rectangle {
                id: livePill; anchors.verticalCenter: parent.verticalCenter
                visible: root.qaLive || Object.keys(backend.liveStatus).length > 0
                width: liveRow.implicitWidth + 20; height: 34; radius: 9
                color: lpMa.containsMouse ? root.alpha(root.danger, 0.18) : root.alpha(root.green, 0.12 + 0.05 * root.pulse)
                border.width: 1; border.color: lpMa.containsMouse ? root.danger : root.alpha(root.green, 0.55 + 0.35 * root.pulse)
                Rectangle {   // breathing live-glow halo (hidden on hover→STOP)
                    visible: !lpMa.containsMouse
                    anchors.fill: parent; anchors.margins: -6; radius: 14; z: -1
                    color: root.alpha(root.green, 0.05 + 0.07 * root.pulse)
                }
                Row {
                    id: liveRow; anchors.centerIn: parent; spacing: 7
                    Rectangle {
                        width: 8; height: 8; radius: 4; anchors.verticalCenter: parent.verticalCenter
                        color: lpMa.containsMouse ? root.danger : root.green
                        SequentialAnimation on opacity {
                            running: livePill.visible; loops: Animation.Infinite
                            NumberAnimation { to: 0.35; duration: 700 }
                            NumberAnimation { to: 1.0; duration: 700 }
                        }
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter; font.pixelSize: 11; font.bold: true; font.letterSpacing: 1
                        text: lpMa.containsMouse ? "STOP" : "LIVE"
                        color: lpMa.containsMouse ? root.danger : root.green
                    }
                }
                MouseArea { id: lpMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.stopHardware() }
            }
            FlatSwitch { anchors.verticalCenter: parent.verticalCenter; label: "APP-AWARE"; on: root.appAware; onToggled: root.appAware = !root.appAware }
            FlatSwitch {
                anchors.verticalCenter: parent.verticalCenter; label: "LIGHTING"
                enabled: backend.deps.openrazer; on: root.lighting
                onToggled: { root.lighting = !root.lighting; if (root.lighting) lightingOverlay.open() }
            }
            FlatSwitch { anchors.verticalCenter: parent.verticalCenter; label: "ALIGN"; on: root.aligning; accent: "#1d7fa6"; accentBorder: root.cyan; onToggled: { root.aligning = !root.aligning; root.deselect() } }
        }
    }

    // ================= footer =================
    Item {
        id: footer
        anchors { bottom: parent.bottom; left: parent.left; right: parent.right }
        height: 30
        Rectangle { anchors.fill: parent; color: root.bg0 }
        Rectangle { anchors { left: parent.left; right: parent.right; top: parent.top }height: 1; color: root.lineC }
        Row {
            anchors { left: parent.left; leftMargin: 18; verticalCenter: parent.verticalCenter }
            spacing: 16
            Row {
                spacing: 6; anchors.verticalCenter: parent.verticalCenter
                Rectangle { width: 7; height: 7; radius: 4; color: backend.deps.inputRemapper ? root.green : root.danger; anchors.verticalCenter: parent.verticalCenter }
                Text { text: "Engine: "; color: root.muted; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter }
            }
            Text { text: backend.deps.inputRemapper ? "input-remapper connected" : "input-remapper not found"; color: backend.deps.inputRemapper ? root.green : root.danger; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter }
            Text { text: "Device: " + (root.device ? root.device.name : ""); color: root.muted; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter }
            Text {
                anchors.verticalCenter: parent.verticalCenter; font.pixelSize: 12
                property var cs: root.capSummary[root.curDev]
                text: cs ? (cs.captured > 0 ? ("● " + cs.captured + "/" + cs.total + " captured")
                                            : "○ not captured — run capture.py") : ""
                color: cs ? (cs.captured > 0 ? root.green : root.danger) : root.muted
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter; font.pixelSize: 12
                text: root.boundCount + " bound" + (root.conflictKeys.length > 0
                      ? " · " + root.conflictKeys.length + " share an output" : "")
                color: root.conflictKeys.length > 0 ? root.amber : root.muted
            }
            Text { text: "Preset: " + backend.presetNameFor(root.curProfile); color: root.muted; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter }
        }
        Text { anchors { right: parent.right; rightMargin: 18; verticalCenter: parent.verticalCenter }text: root.dirtyText; color: root.muted; font.pixelSize: 12 }
    }

    // ================= first-run calibration hint =================
    Item {
        id: hintBar
        anchors { top: header.bottom; left: parent.left; right: parent.right }
        height: (root.capSource === "default" && !root.hintDismissed) ? 34 : 0
        visible: height > 0; clip: true
        Rectangle {
            anchors.fill: parent; color: root.alpha(root.amber, 0.13)
            Rectangle { anchors { left: parent.left; right: parent.right; bottom: parent.bottom } height: 1; color: root.alpha(root.amber, 0.45) }
        }
        Row {
            spacing: 8
            anchors { left: parent.left; leftMargin: 18; verticalCenter: parent.verticalCenter }
            Text { text: "⚠"; color: root.amber; font.pixelSize: 13; anchors.verticalCenter: parent.verticalCenter }
            Text { text: "Using the bundled default key map — not calibrated to this machine. If Apply hits the wrong key or device, run  python3 app/capture.py"
                color: "#e8d9b0"; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter }
        }
        Text {
            anchors { right: parent.right; rightMargin: 16; verticalCenter: parent.verticalCenter }
            text: "✕ dismiss"; color: root.muted; font.pixelSize: 11
            MouseArea { anchors.fill: parent; anchors.margins: -6; cursorShape: Qt.PointingHandCursor; onClicked: root.hintDismissed = true }
        }
    }

    // ================= body =================
    Item {
        id: body
        anchors { top: hintBar.bottom; bottom: footer.top; left: parent.left; right: parent.right }

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
                        devName: backend.layouts[modelData].name.replace("Razer ", "")
                        devType: backend.layouts[modelData].kind + " · " + backend.layouts[modelData].usb
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
                        width: 46; height: 46; radius: 10; color: "#22331c"
                        border.width: 1; border.color: root.alpha(root.greenHot, 0.6 + 0.3 * root.pulse)
                        Rectangle {   // soft lit halo mirroring the selected hotspot's glow
                            anchors.fill: parent; anchors.margins: -4; radius: 14; z: -1
                            color: root.alpha(root.green, 0.08 + 0.06 * root.pulse)
                        }
                        Text { anchors.centerIn: parent; text: root.selKey.split("_").slice(1).join("_").slice(0, 5); color: root.greenHot; font.pixelSize: 14; font.bold: true }
                    }
                    Column {
                        anchors.verticalCenter: parent.verticalCenter; spacing: 2
                        Text { text: root.selKey.replace(/_/g, " "); color: root.txt; font.pixelSize: 15; font.bold: true }
                        Text { text: "hotspot: " + root.selKey; color: root.muted; font.pixelSize: 12 }
                    }
                }

                Column {
                    width: parent.width; spacing: 9
                    Text { text: "OUTPUT TYPE"; color: root.muted2; font.pixelSize: 10; font.letterSpacing: 1.6 }
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
                                        onClicked: { seg.sel = modelData; if (modelData === "Disable") { root.capValue = "Disabled"; root.listening = false } } }
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
                        Text { text: "BINDING"; color: root.muted2; font.pixelSize: 10; font.letterSpacing: 1.6 }
                        Row {
                            width: parent.width; spacing: 10; height: 54
                            Rectangle {
                                width: parent.width - 90; height: 54; radius: 10; color: root.bg0
                                border.width: 1; border.color: root.listening ? root.green : root.line2
                                Text {
                                    anchors.centerIn: parent
                                    text: root.listening
                                          ? (root.capValue !== "" ? root.capValue : "press a key…")
                                          : (root.capValue !== "" ? root.capValue : (root.curBinding() !== "" ? root.curBinding() : "—"))
                                    color: root.listening ? root.green : root.txt; font.pixelSize: 18; font.bold: true
                                }
                            }
                            Rectangle {
                                width: 80; height: 54; radius: 10
                                color: root.listening ? root.green : root.greenDim
                                border.width: 1; border.color: root.green
                                Text { anchors.centerIn: parent; text: root.listening ? "Stop" : "Listen"; color: root.greenTxt; font.pixelSize: 13; font.bold: true }
                                MouseArea {
                                    anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                    onClicked: { root.listening = !root.listening; if (root.listening) keyCatcher.forceActiveFocus() }
                                }
                            }
                        }
                        Text { text: "QUICK PICK"; color: root.muted2; font.pixelSize: 10; font.letterSpacing: 1.6 }
                        Flow {
                            width: parent.width; spacing: 6
                            Repeater {
                                model: ["Esc", "Tab", "Shift", "Ctrl", "Alt", "Space", "Enter", "↑", "↓", "←", "→",
                                    "Q", "W", "E", "R", "F", "1", "2", "3", "LMB", "RMB", "MMB",
                                    "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12"]
                                Chip { label: modelData; onPicked: { root.capValue = modelData; root.listening = false } }
                            }
                        }
                    }
                }

                Row {
                    width: parent.width; spacing: 10; height: 42
                    Rectangle {
                        width: parent.width - 90; height: 42; radius: 10
                        border.width: 1; border.color: bindMa.containsMouse ? root.greenHot : root.green
                        gradient: Gradient {
                            GradientStop { position: 0.0; color: bindMa.containsMouse ? "#4fc036" : root.green }
                            GradientStop { position: 1.0; color: root.greenDim }
                        }
                        Rectangle {   // breathing halo — the panel's primary commit action
                            anchors.fill: parent; anchors.margins: -5; radius: 14; z: -1
                            color: root.alpha(root.green, bindMa.containsMouse ? 0.30 : (0.08 + 0.07 * root.pulse))
                        }
                        Text { anchors.centerIn: parent; text: "Bind"; color: root.greenTxt; font.pixelSize: 13; font.bold: true; font.letterSpacing: 0.3 }
                        MouseArea { id: bindMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.applyBinding() }
                    }
                    Rectangle {
                        width: 80; height: 42; radius: 10; color: root.panel2; border.width: 1; border.color: root.line2
                        Text { anchors.centerIn: parent; text: "Clear"; color: root.txt; font.pixelSize: 13; font.bold: true }
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.clearBinding() }
                    }
                }
                Text {
                    width: parent.width; wrapMode: Text.WordWrap
                    text: "“Bind” sets it and pushes it live instantly · “Clear” removes it"
                    color: root.muted2; font.pixelSize: 11
                }
            }
        }

        // ---------- center stage ----------
        Item {
            id: stage
            anchors { top: parent.top; bottom: parent.bottom; left: rail.right; right: panelArea.left }
            clip: true

            // ambient stage vignette — a faint green top-light so the device looks lit
            Rectangle {
                anchors.fill: parent; z: 0
                gradient: Gradient {
                    GradientStop { position: 0.0; color: root.alpha(root.green, 0.05) }
                    GradientStop { position: 0.55; color: "transparent" }
                }
            }

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
                                border.width: root.curView === modelData ? 1 : 0
                                border.color: root.alpha(root.greenHot, 0.5 + 0.3 * root.pulse)
                                Text { id: tabTxt; anchors.centerIn: parent; text: (root.device && root.device.views[modelData]) ? (root.device.views[modelData].label || modelData) : modelData; color: root.curView === modelData ? root.greenTxt : root.muted; font.pixelSize: 12; font.bold: true }
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
                    // detail views (e.g. Tartarus thumb close-up) get a framed look
                    Rectangle {
                        anchors.fill: parent
                        visible: root.viewObj && root.viewObj.framed === true
                        color: "transparent"; radius: 14
                        border.width: 2; border.color: root.line2
                    }
                    Repeater {
                        model: root.viewObj ? root.viewObj.keys : []
                        Hotspot {
                            k: modelData
                            litIndex: index
                            binding: root.bindMap[modelData.id] !== undefined ? root.bindMap[modelData.id] : ""
                            selected: root.selKey === modelData.id
                            conflict: root.conflictKeys.indexOf(modelData.id) >= 0
                            unavailable: modelData.unavailable || ""
                        }
                    }
                }
            }

            // ---- signature load moment: a single green light bar sweeps across
            // the stage on start, then fades — the UI "powering on".
            Rectangle {
                z: 6
                visible: root.bootSweep < 0.999
                width: 120
                anchors { top: parent.top; bottom: parent.bottom }
                x: -width + (stage.width + width * 2) * root.bootSweep
                opacity: (1.0 - root.bootSweep) * 0.5
                rotation: 8
                transformOrigin: Item.Center
                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: 0.0; color: "transparent" }
                    GradientStop { position: 0.5; color: root.alpha(root.greenHot, 0.5) }
                    GradientStop { position: 1.0; color: "transparent" }
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

    // ================= apply-to-hardware result =================
    Item {
        id: resultOverlay
        anchors.fill: parent
        visible: false
        z: 100
        Rectangle {
            anchors.fill: parent; color: Qt.rgba(0, 0, 0, 0.55)
            MouseArea { anchors.fill: parent; onClicked: resultOverlay.visible = false }
        }
        Rectangle {
            anchors.centerIn: parent
            width: 480
            height: Math.min(parent.height - 80, resCol.implicitHeight + 40)
            radius: 14; color: root.panelC; border.width: 1; border.color: root.line2
            clip: true
            Column {
                id: resCol
                anchors { left: parent.left; right: parent.right; top: parent.top; margins: 20 }
                spacing: 12
                Row {
                    spacing: 10
                    Rectangle { width: 9; height: 9; radius: 4; anchors.verticalCenter: parent.verticalCenter
                        color: (root.applyResult && root.applyResult.ok) ? root.green : root.danger }
                    Text { text: "Apply to hardware"; color: root.txt; font.pixelSize: 15; font.bold: true; anchors.verticalCenter: parent.verticalCenter }
                }
                Text {
                    width: parent.width; wrapMode: Text.WordWrap
                    text: root.applyResult ? root.applyResult.message : ""
                    color: (root.applyResult && root.applyResult.ok) ? root.green : root.danger
                    font.pixelSize: 13
                }
                Repeater {
                    model: root.applyResult ? root.applyResult.devices : []
                    Column {
                        width: resCol.width; spacing: 4; topPadding: 4
                        Row {
                            spacing: 8
                            Text { text: (modelData.ok ? "✓ " : "✕ ") + modelData.name
                                color: modelData.ok ? root.txt : root.danger; font.pixelSize: 13; font.bold: true }
                            Text { text: modelData.ok ? (modelData.count + " keys live") : (modelData.error || "failed")
                                color: root.muted; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter }
                        }
                        Repeater {
                            model: modelData.warnings || []
                            Text { width: resCol.width - 14; wrapMode: Text.WordWrap; leftPadding: 14
                                text: "• " + modelData; color: root.muted2; font.pixelSize: 11 }
                        }
                    }
                }
                Rectangle {
                    width: parent.width; height: 38; radius: 9; color: root.greenDim; border.width: 1; border.color: root.green
                    Text { anchors.centerIn: parent; text: "Done"; color: root.greenTxt; font.bold: true; font.pixelSize: 13 }
                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: resultOverlay.visible = false }
                }
            }
        }
    }

    // ================= profile name dialog (create / rename / duplicate / delete) =================
    Item {
        id: nameDialog
        anchors.fill: parent; visible: false; z: 110
        property string mode: ""
        property string title: ""
        property string error: ""
        function open(m, t, initial) {
            mode = m; title = t; error = ""
            nameField.text = initial || ""
            visible = true
            if (m !== "delete") { nameField.forceActiveFocus(); nameField.selectAll() }
        }
        function submit() {
            var r
            if (mode === "create") r = backend.createProfile(nameField.text)
            else if (mode === "rename") r = backend.renameProfile(root.curProfile, nameField.text)
            else if (mode === "duplicate") r = backend.duplicateProfile(root.curProfile, nameField.text)
            else if (mode === "delete") r = backend.deleteProfile(root.curProfile)
            else r = { ok: false, error: "?" }
            if (r.ok) {
                root.syncProfile(); visible = false
                showToast(mode === "delete" ? "Profile deleted" : ("Saved “" + (r.name || "") + "”"))
            } else { error = r.error }
        }
        Rectangle { anchors.fill: parent; color: Qt.rgba(0, 0, 0, 0.55)
            MouseArea { anchors.fill: parent; onClicked: nameDialog.visible = false } }
        Rectangle {
            anchors.centerIn: parent; width: 380; radius: 14
            height: dCol.implicitHeight + 36; color: root.panelC; border.width: 1; border.color: root.line2
            Column {
                id: dCol; spacing: 14
                anchors { left: parent.left; right: parent.right; top: parent.top; margins: 18 }
                Text { text: nameDialog.title; color: root.txt; font.pixelSize: 15; font.bold: true }
                Text { visible: nameDialog.mode === "delete"; width: parent.width; wrapMode: Text.WordWrap
                    text: "Delete “" + root.curProfile + "”? This can't be undone."; color: root.muted; font.pixelSize: 13 }
                Rectangle {
                    visible: nameDialog.mode !== "delete"
                    width: parent.width; height: 40; radius: 9; color: root.bg0
                    border.width: 1; border.color: nameField.activeFocus ? root.green : root.line2
                    TextField {
                        id: nameField; anchors.fill: parent; anchors.margins: 2; leftPadding: 10
                        verticalAlignment: TextInput.AlignVCenter; color: root.txt; font.pixelSize: 14
                        selectByMouse: true; onAccepted: nameDialog.submit()
                        background: Item {}
                    }
                }
                Text { visible: nameDialog.error !== ""; text: nameDialog.error; color: root.danger; font.pixelSize: 12 }
                Row {
                    width: parent.width; spacing: 10; layoutDirection: Qt.RightToLeft
                    Rectangle {
                        width: 96; height: 36; radius: 9
                        color: nameDialog.mode === "delete" ? root.danger : root.greenDim
                        border.width: 1; border.color: nameDialog.mode === "delete" ? "#e8775f" : root.green
                        Text { anchors.centerIn: parent; text: nameDialog.mode === "delete" ? "Delete" : "Save"; color: root.greenTxt; font.bold: true; font.pixelSize: 13 }
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: nameDialog.submit() }
                    }
                    Rectangle {
                        width: 90; height: 36; radius: 9; color: root.panel2; border.width: 1; border.color: root.line2
                        Text { anchors.centerIn: parent; text: "Cancel"; color: root.txt; font.pixelSize: 13 }
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: nameDialog.visible = false }
                    }
                }
            }
        }
    }

    // ================= import-profile dialog =================
    Item {
        id: importDialog
        anchors.fill: parent; visible: false; z: 110
        property string error: ""
        function open() { error = ""; importField.text = ""; visible = true; importField.forceActiveFocus() }
        function submit() {
            var r = backend.importProfile(importField.text)
            if (r.ok) { root.syncProfile(); visible = false; showToast("Imported “" + r.name + "”") }
            else { error = r.error }
        }
        Rectangle { anchors.fill: parent; color: Qt.rgba(0, 0, 0, 0.55)
            MouseArea { anchors.fill: parent; onClicked: importDialog.visible = false } }
        Rectangle {
            anchors.centerIn: parent; width: 460; radius: 14
            height: iCol.implicitHeight + 36; color: root.panelC; border.width: 1; border.color: root.line2
            Column {
                id: iCol; spacing: 12
                anchors { left: parent.left; right: parent.right; top: parent.top; margins: 18 }
                Text { text: "Import profile"; color: root.txt; font.pixelSize: 15; font.bold: true }
                Text { text: "Paste an exported KEYZER profile (JSON) below."; color: root.muted; font.pixelSize: 12 }
                Rectangle {
                    width: parent.width; height: 150; radius: 9; color: root.bg0; clip: true
                    border.width: 1; border.color: importField.activeFocus ? root.green : root.line2
                    ScrollView {
                        anchors.fill: parent; anchors.margins: 6
                        TextArea {
                            id: importField; color: root.txt; font.pixelSize: 12; font.family: "monospace"
                            wrapMode: TextEdit.WrapAnywhere; selectByMouse: true; background: Item {}
                        }
                    }
                }
                Text { visible: importDialog.error !== ""; text: importDialog.error; color: root.danger; font.pixelSize: 12 }
                Row {
                    width: parent.width; spacing: 10; layoutDirection: Qt.RightToLeft
                    Rectangle {
                        width: 96; height: 36; radius: 9; color: root.greenDim; border.width: 1; border.color: root.green
                        Text { anchors.centerIn: parent; text: "Import"; color: root.greenTxt; font.bold: true; font.pixelSize: 13 }
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: importDialog.submit() }
                    }
                    Rectangle {
                        width: 90; height: 36; radius: 9; color: root.panel2; border.width: 1; border.color: root.line2
                        Text { anchors.centerIn: parent; text: "Cancel"; color: root.txt; font.pixelSize: 13 }
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: importDialog.visible = false }
                    }
                }
            }
        }
    }

    // ================= lighting panel (OpenRazer, optional) =================
    Item {
        id: lightingOverlay
        anchors.fill: parent; visible: false; z: 110
        property var panel: ({ error: null, devices: [] })
        readonly property var swatches: [["Razer", 68, 214, 44], ["White", 255, 255, 255], ["Red", 230, 40, 40],
            ["Blue", 40, 120, 255], ["Cyan", 34, 200, 255], ["Pink", 220, 40, 200], ["Amber", 230, 170, 40], ["Off", 0, 0, 0]]
        function open() { panel = backend.lightingDevices(); visible = true }
        function openDemo() {   // offscreen QA only
            panel = { error: null, devices: [
                { id: "tartarus", name: "Razer Tartarus Pro", brightness: 80, effects: ["static", "reactive", "none"] },
                { id: "naga", name: "Razer Naga Pro", brightness: 100, _wheel: true, effects: ["static", "spectrum", "breath_single", "wave", "none"],
                  zones: [{ name: "logo", label: "Logo", effects: ["static", "spectrum", "breath_single", "none"] },
                          { name: "scroll_wheel", label: "Scroll wheel", effects: ["static", "spectrum", "reactive", "none"] }] } ] }
            visible = true
        }
        function applyColor(devId, sw, zone) {
            var r = backend.setLightEffect(devId, sw[0] === "Off" ? "none" : "static", sw[1], sw[2], sw[3], zone)
            showToast(r.ok ? (devId + (zone ? " " + zone : "") + " → " + sw[0]) : (r.error || "lighting failed"))
        }
        function applyEffect(devId, eff, zone) {
            var r = backend.setLightEffect(devId, eff, 68, 214, 44, zone)
            showToast(r.ok ? (devId + (zone ? " " + zone : "") + " → " + eff) : (r.error || "lighting failed"))
        }
        function setBright(devId, pct) {
            var r = backend.setLightBrightness(devId, pct)
            if (r.ok) open(); else showToast(r.error || "brightness failed")
        }
        Rectangle { anchors.fill: parent; color: Qt.rgba(0, 0, 0, 0.55)
            MouseArea { anchors.fill: parent; onClicked: lightingOverlay.visible = false } }
        Rectangle {
            anchors.centerIn: parent; width: 520
            height: Math.min(parent.height - 80, lCol.implicitHeight + 36)
            radius: 14; color: root.panelC; border.width: 1; border.color: root.line2; clip: true
            Column {
                id: lCol; spacing: 14
                anchors { left: parent.left; right: parent.right; top: parent.top; margins: 20 }
                Text { text: "Lighting"; color: root.txt; font.pixelSize: 15; font.bold: true }
                Text {
                    visible: (lightingOverlay.panel.error || "") !== "" || (lightingOverlay.panel.devices || []).length === 0
                    width: parent.width; wrapMode: Text.WordWrap; font.pixelSize: 12; color: root.muted
                    text: lightingOverlay.panel.error
                          ? ("OpenRazer: " + lightingOverlay.panel.error)
                          : "No lightable Razer devices found — is the OpenRazer daemon running (and are you in the 'plugdev' group)?"
                }
                Repeater {
                    model: lightingOverlay.panel.devices || []
                    Column {
                        id: devRow
                        property string devId: modelData.id
                        property real devBright: modelData.brightness
                        property var devZones: modelData.zones || []
                        property string zone: ""
                        property var curEffects: {
                            if (zone === "") return modelData.effects || []
                            var z = devZones.find(function (zn) { return zn.name === zone })
                            return z ? z.effects : []
                        }
                        // full custom-colour picker (hue/sat wheel + value)
                        property bool showWheel: false
                        property real selH: 0.33   // hue 0..1
                        property real selS: 1.0    // saturation 0..1 (wheel radius)
                        property real selV: 1.0    // value 0..1
                        readonly property color selColor: Qt.hsva(selH, selS, selV, 1)
                        function pickWheel(mx, my) {
                            var dx = mx - 75, dy = my - 75
                            selS = Math.max(0, Math.min(1, Math.sqrt(dx * dx + dy * dy) / 72))
                            selH = (Math.atan2(dy, dx) / (2 * Math.PI) + 1) % 1
                        }
                        function hex2(c) { var h = Math.round(c * 255).toString(16); return h.length < 2 ? "0" + h : h }
                        Component.onCompleted: if (modelData._wheel === true) showWheel = true
                        width: lCol.width; spacing: 8; topPadding: 4
                        Row {
                            spacing: 10
                            Text { text: modelData.name; color: root.txt; font.pixelSize: 13; font.bold: true; anchors.verticalCenter: parent.verticalCenter }
                            Text { text: Math.round(devRow.devBright) + "%"; color: root.muted; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter }
                            Rectangle { width: 26; height: 22; radius: 6; color: root.panel2; border.width: 1; border.color: root.line2; anchors.verticalCenter: parent.verticalCenter
                                Text { anchors.centerIn: parent; text: "−"; color: root.txt; font.pixelSize: 14 }
                                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: lightingOverlay.setBright(devRow.devId, Math.max(0, devRow.devBright - 10)) } }
                            Rectangle { width: 26; height: 22; radius: 6; color: root.panel2; border.width: 1; border.color: root.line2; anchors.verticalCenter: parent.verticalCenter
                                Text { anchors.centerIn: parent; text: "+"; color: root.txt; font.pixelSize: 14 }
                                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: lightingOverlay.setBright(devRow.devId, Math.min(100, devRow.devBright + 10)) } }
                        }
                        Flow {   // zone selector (Naga exposes independent zones)
                            visible: devRow.devZones.length > 0
                            width: parent.width; spacing: 6
                            Repeater {
                                model: [{ name: "", label: "All" }].concat(devRow.devZones)
                                Rectangle {
                                    height: 24; width: zt.implicitWidth + 18; radius: 6
                                    color: devRow.zone === modelData.name ? root.greenDim : root.panel2
                                    border.width: 1; border.color: devRow.zone === modelData.name ? root.green : root.lineC
                                    Text { id: zt; anchors.centerIn: parent; text: modelData.label; font.pixelSize: 11; font.bold: true
                                        color: devRow.zone === modelData.name ? root.greenTxt : root.muted }
                                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: devRow.zone = modelData.name }
                                }
                            }
                        }
                        Flow {
                            width: parent.width; spacing: 8
                            Repeater {
                                model: lightingOverlay.swatches
                                Rectangle {
                                    width: 30; height: 30; radius: 7
                                    color: modelData[0] === "Off" ? root.bg0 : Qt.rgba(modelData[1] / 255, modelData[2] / 255, modelData[3] / 255, 1)
                                    border.width: 1; border.color: swMa.containsMouse ? root.txt : root.line2
                                    Text { visible: modelData[0] === "Off"; anchors.centerIn: parent; text: "∅"; color: root.muted; font.pixelSize: 13 }
                                    MouseArea { id: swMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                        onClicked: lightingOverlay.applyColor(devRow.devId, modelData, devRow.zone) }
                                }
                            }
                        }
                        Flow {
                            width: parent.width; spacing: 6
                            Repeater {
                                model: devRow.curEffects
                                Chip { label: modelData; onPicked: lightingOverlay.applyEffect(devRow.devId, modelData, devRow.zone) }
                            }
                        }
                        // ----- custom colour: hue/sat wheel + value -----
                        Rectangle {
                            width: cpTxt.implicitWidth + 26; height: 24; radius: 6
                            color: devRow.showWheel ? root.greenDim : root.panel2
                            border.width: 1; border.color: devRow.showWheel ? root.green : root.lineC
                            Text { id: cpTxt; anchors.centerIn: parent; text: "🎨 Custom colour"; font.pixelSize: 11; font.bold: true
                                color: devRow.showWheel ? root.greenTxt : root.muted }
                            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: devRow.showWheel = !devRow.showWheel }
                        }
                        Row {
                            visible: devRow.showWheel
                            spacing: 14
                            Item {
                                width: 150; height: 150
                                Canvas {
                                    anchors.fill: parent
                                    onVisibleChanged: if (visible) requestPaint()
                                    Component.onCompleted: requestPaint()
                                    onPaint: {
                                        var ctx = getContext("2d")
                                        var cx = width / 2, cy = height / 2, R = Math.min(cx, cy) - 3
                                        ctx.clearRect(0, 0, width, height)
                                        for (var a = 0; a < 360; a += 2) {
                                            ctx.beginPath(); ctx.moveTo(cx, cy)
                                            ctx.arc(cx, cy, R, (a - 1) * Math.PI / 180, (a + 2) * Math.PI / 180)
                                            ctx.closePath()
                                            ctx.fillStyle = Qt.hsva(a / 360, 1, 1, 1); ctx.fill()
                                        }
                                        var g = ctx.createRadialGradient(cx, cy, 0, cx, cy, R)
                                        g.addColorStop(0, Qt.rgba(1, 1, 1, 1)); g.addColorStop(1, Qt.rgba(1, 1, 1, 0))
                                        ctx.fillStyle = g
                                        ctx.beginPath(); ctx.arc(cx, cy, R, 0, 2 * Math.PI); ctx.fill()
                                    }
                                }
                                Rectangle {   // picked-point marker
                                    width: 12; height: 12; radius: 6; color: "transparent"
                                    border.width: 2; border.color: "white"
                                    x: 75 + Math.cos(devRow.selH * 2 * Math.PI) * devRow.selS * 72 - 6
                                    y: 75 + Math.sin(devRow.selH * 2 * Math.PI) * devRow.selS * 72 - 6
                                }
                                MouseArea {
                                    anchors.fill: parent; cursorShape: Qt.CrossCursor
                                    onPressed: function (m) { devRow.pickWheel(m.x, m.y) }
                                    onPositionChanged: function (m) { if (pressed) devRow.pickWheel(m.x, m.y) }
                                }
                            }
                            Column {
                                spacing: 8; width: 150; anchors.verticalCenter: parent.verticalCenter
                                Rectangle {   // live preview + hex
                                    width: parent.width; height: 34; radius: 7; color: devRow.selColor
                                    border.width: 1; border.color: root.line2
                                    Text { anchors.centerIn: parent
                                        text: "#" + devRow.hex2(devRow.selColor.r) + devRow.hex2(devRow.selColor.g) + devRow.hex2(devRow.selColor.b)
                                        color: devRow.selV > 0.55 ? "#101010" : "#f0f0f0"; font.pixelSize: 12; font.bold: true }
                                }
                                Text { text: "Brightness"; color: root.muted2; font.pixelSize: 10 }
                                Rectangle {   // value slider
                                    width: parent.width; height: 16; radius: 8
                                    gradient: Gradient {
                                        orientation: Gradient.Horizontal
                                        GradientStop { position: 0; color: "#000000" }
                                        GradientStop { position: 1; color: Qt.hsva(devRow.selH, devRow.selS, 1, 1) }
                                    }
                                    Rectangle {
                                        width: 4; height: parent.height + 6; radius: 2; color: "white"
                                        y: -3; x: devRow.selV * (parent.width - 4)
                                    }
                                    MouseArea {
                                        anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                        onPressed: function (m) { devRow.selV = Math.max(0, Math.min(1, m.x / width)) }
                                        onPositionChanged: function (m) { if (pressed) devRow.selV = Math.max(0, Math.min(1, m.x / width)) }
                                    }
                                }
                                Rectangle {   // apply
                                    width: parent.width; height: 30; radius: 8; color: root.greenDim; border.width: 1; border.color: root.green
                                    Text { anchors.centerIn: parent; text: "Apply colour"; color: root.greenTxt; font.pixelSize: 12; font.bold: true }
                                    MouseArea {
                                        anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                        onClicked: lightingOverlay.applyColor(devRow.devId,
                                            ["Custom", Math.round(devRow.selColor.r * 255),
                                                       Math.round(devRow.selColor.g * 255),
                                                       Math.round(devRow.selColor.b * 255)], devRow.zone)
                                    }
                                }
                            }
                        }
                    }
                }
                Rectangle {
                    width: parent.width; height: 38; radius: 9; color: root.greenDim; border.width: 1; border.color: root.green
                    Text { anchors.centerIn: parent; text: "Done"; color: root.greenTxt; font.bold: true; font.pixelSize: 13 }
                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: lightingOverlay.visible = false }
                }
            }
        }
    }

    // key capture for Listen
    Item {
        id: keyCatcher
        focus: false
        Keys.onPressed: function(event) {
            if (!root.listening) return
            event.accepted = true
            if (event.key === Qt.Key_Escape) { root.listening = false; return }  // stop (use the Esc chip to bind Escape)
            // skip bare modifier presses so combos capture fully (Ctrl+1, Ctrl+Shift+1)
            if (root.isBareModifier(event.key)) return
            root.capValue = root.keyLabel(event)
            // stay in listen mode — keep capturing (last press wins) until the
            // user stops via Stop, Esc, Bind, or selecting another hotspot.
        }
    }
    // copy-layout placeholder (drag-align wired in next phase)
    function copyLayout() {
        var out = {}
        var L = backend.layouts
        for (var d in L) {
            out[d] = {}
            var vns = backend.viewNames(d)
            for (var i = 0; i < vns.length; i++) {
                var vn = vns[i]
                out[d][vn] = L[d].views[vn].keys.map(function (kk) {
                    var o = root.ov[d + "|" + vn + "|" + kk.id]
                    return { id: kk.id, x: o ? o.x : kk.x, y: o ? o.y : kk.y, w: kk.w, h: kk.h }
                })
            }
        }
        backend.copyToClipboard(JSON.stringify(out))
        showToast("Layout copied to clipboard")
    }

    // Escape to deselect
    Shortcut { sequence: "Escape"; onActivated: if (!root.listening) root.deselect() }
}
