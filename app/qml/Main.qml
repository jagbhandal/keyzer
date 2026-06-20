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
    readonly property color violet: "#9b8cff"       // Hypershift second-layer accent

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
    property bool listening: false
    property int litStep: 0
    // last lighting applied per device (drives the TRUTHFUL on-device preview);
    // OpenRazer can't read the active effect back, so this is KEYZER's own record.
    // Hydrated from backend.lightState() on startup, so a chosen look persists across
    // sessions (and the backend watcher re-applies it on device reconnect).
    property var lightState: ({})
    function effectLabel(tok) {
        return ({ "static": "Solid", "breath_single": "Pulse", "breath_dual": "Pulse",
                  "breath_random": "Pulse", "spectrum": "Rainbow", "reactive": "React to keys",
                  "wave": "Wave", "none": "Off" })[tok] || tok
    }
    function uniqueEffects(list) {   // collapse duplicate labels (the 3 breath_* -> one "Pulse")
        var seen = ({}), out = []
        for (var i = 0; i < (list || []).length; i++) {
            var lbl = effectLabel(list[i])
            if (!seen[lbl]) { seen[lbl] = 1; out.push(list[i]) }
        }
        return out
    }
    // lightState is keyed by "device|zone" — OpenRazer can't read effects back, so it's
    // KEYZER's own record of the applied look, blank for a zone until it's ever been set.
    function lightKey() { return curDev + "|" + lightZone }
    function curLight() {
        return lightState[lightKey()] || { effect: "", r: 68, g: 214, b: 44, direction: "left", react_ms: 1000 }
    }
    // which contextual controls the current effect actually uses (the rest stay hidden)
    function lightUsesColour() { var e = curLight().effect   // Solid, React, and any single-colour Pulse (not random)
        return e === "static" || e === "reactive" || (e.indexOf("breath") === 0 && e !== "breath_random") }
    function lightUsesSpeed() { return curLight().effect === "reactive" }
    function lightUsesDirection() { return curLight().effect === "wave" }
    function speedLabel(ms) { return ms <= 500 ? "Fast" : (ms >= 1500 ? "Slow" : "Medium") }
    function applyLighting(patch) {   // merge one change into the current look, push it live, record it
        var s = Object.assign({}, curLight(), patch)
        if (!s.effect) s.effect = "static"   // a colour/param change before an effect is picked -> Solid
        // optimistic: update the preview + record now; the hardware call runs off the
        // GUI thread and a failure comes back via onLightingOpFailed (a toast).
        backend.setLightEffect(curDev, s.effect, s.r, s.g, s.b, lightZone, s.direction, s.react_ms)
        var m = Object.assign({}, lightState); m[lightKey()] = s; lightState = m
        showToast(lightLabel() + " → " + effectLabel(s.effect))
    }
    // colour the on-device glow with the current zone's applied look (approximate
    // preview). ONE shared binding rather than a per-hotspot function call, so for a
    // breath/spectrum effect it re-evaluates once per frame instead of once per
    // visible hotspot (up to 22) — the value is identical across all of them.
    readonly property color liveGlow: {
        var s = lightState[lightKey()]
        if (!s || !s.effect || s.effect === "none") return Qt.rgba(0, 0, 0, 0)
        if (s.effect === "spectrum") return Qt.hsla(((litStep * 8) % 360) / 360, 0.9, 0.55, 0.9)
        var a = (s.effect && s.effect.indexOf("breath") === 0) ? (0.3 + 0.55 * pulse) : 0.85
        return Qt.rgba(s.r / 255, s.g / 255, s.b / 255, a)
    }
    // ---- canvas-native lighting mode (operates on the on-stage device, curDev) ----
    property var lightInfo: ({ error: null, devices: [] })   // backend.lightingDevices() snapshot
    property string lightZone: ""                            // selected zone ("" = whole device)
    property real lightBright: 100
    property bool lightSync: false                           // OpenRazer: mirror effects across all devices
    property bool lcWheel: false                             // custom-colour wheel disclosed?
    property real lcH: 0.33
    property real lcS: 1.0
    property real lcV: 1.0
    readonly property color lcColor: Qt.hsva(lcH, lcS, lcV, 1)
    readonly property var lightSwatches: [["Razer Green", 68, 214, 44], ["White", 255, 255, 255],
        ["Red", 230, 40, 40], ["Blue", 40, 120, 255], ["Cyan", 34, 200, 255], ["Pink", 220, 40, 200],
        ["Amber", 230, 170, 40]]
    function lightDev() {           // the lightInfo entry for the on-stage device, or null
        var ds = lightInfo.devices || []
        for (var i = 0; i < ds.length; i++) if (ds[i].id === curDev) return ds[i]
        return null
    }
    function lightZonesFor() {      // [{name,label,effects}] incl. "Whole device", for curDev
        var d = lightDev(); if (!d) return []
        return [{ name: "", label: "Whole device", effects: (d.effects || []) }].concat(d.zones || [])
    }
    function zoneLabel(z) { var zs = lightZonesFor(); for (var i = 0; i < zs.length; i++) if (zs[i].name === z) return zs[i].label; return z }
    function curZoneEffects() { var zs = lightZonesFor(); for (var i = 0; i < zs.length; i++) if (zs[i].name === lightZone) return zs[i].effects || []; return [] }
    function lightLabel() { var d = lightDev(); return (d ? d.name : curDev) + (lightZone ? " · " + zoneLabel(lightZone) : "") }
    // Querying OpenRazer can block ~25s when the daemon is down, so the panel asks
    // the backend worker (off the GUI thread) and fills in on lightingDevicesReady.
    function enterLighting() { lightInfo = { error: null, devices: [], checking: true }; lightZone = ""; lcWheel = false; syncWheelToLook(); backend.requestLightingDevices() }
    function onLightingReady(snap) {   // the async query came back — populate the panel
        lightInfo = snap || { error: null, devices: [] }
        lightSync = snap && snap.sync === true
        var d = lightDev(); lightBright = d ? d.brightness : 100
        syncWheelToLook()
    }
    function syncLightDevice() { if (lighting) { lightZone = ""; var d = lightDev(); lightBright = d ? d.brightness : 100; syncWheelToLook() } }
    function selectLightZone(z) { lightZone = z; syncWheelToLook() }   // pick a zone + show its saved colour
    // move the custom-colour wheel + value to a given colour (greys keep the wheel angle)
    function setWheelRGB(r, g, b) {
        var c = Qt.rgba((r || 0) / 255, (g || 0) / 255, (b || 0) / 255, 1)
        if (c.hsvHue >= 0) lcH = c.hsvHue   // hue is -1 for greys; keep the wheel angle then
        lcS = c.hsvSaturation; lcV = c.hsvValue
    }
    // move the wheel to the current look's colour, so the picker shows the saved
    // colour (not a fixed default) after a restart / device / zone switch
    function syncWheelToLook() { var s = curLight(); setWheelRGB(s.r, s.g, s.b) }
    function setLightBright(pct) { backend.setLightBrightness(curDev, Math.round(pct)) }   // async; errors -> onLightingOpFailed
    function pickLightWheel(mx, my) { var dx = mx - 75, dy = my - 75; lcS = Math.max(0, Math.min(1, Math.sqrt(dx * dx + dy * dy) / 72)); lcH = (Math.atan2(dy, dx) / (2 * Math.PI) + 1) % 1 }
    function hex2(c) { var h = Math.round(c * 255).toString(16); return h.length < 2 ? "0" + h : h }
    function curHex() { return "#" + hex2(lcColor.r) + hex2(lcColor.g) + hex2(lcColor.b) }
    function setLightHex(s) {   // parse a typed/pasted hex, move the wheel + value to match, apply it live
        s = (s || "").trim().replace(/^#/, "")
        if (s.length === 3) s = s[0] + s[0] + s[1] + s[1] + s[2] + s[2]   // #abc -> #aabbcc
        if (!/^[0-9a-fA-F]{6}$/.test(s)) { showToast("Enter a hex colour like #44d62c"); return false }
        var r = parseInt(s.substr(0, 2), 16), g = parseInt(s.substr(2, 2), 16), b = parseInt(s.substr(4, 2), 16)
        setWheelRGB(r, g, b)
        applyLighting({ r: r, g: g, b: b })
        return true
    }
    function openLightingDemo() {   // offscreen QA: drive the lighting inspector with sample devices
        lightInfo = { error: null, devices: [
            { id: "tartarus", name: "Razer Tartarus Pro", brightness: 80, effects: ["static", "reactive", "none"] },
            { id: "naga", name: "Razer Naga Pro", brightness: 100, effects: ["static", "spectrum", "breath_single", "wave", "none"],
              zones: [{ name: "logo", label: "Logo", effects: ["static", "spectrum", "breath_single", "none"] },
                      { name: "scroll_wheel", label: "Scroll wheel", effects: ["static", "spectrum", "reactive", "none"] }] } ] }
        lightState = { "naga|": { effect: "breath_single", r: 40, g: 120, b: 255, direction: "left", react_ms: 1000 } }
        switchDevice("naga"); lighting = true; lcWheel = true; lightSync = false
        syncWheelToLook()   // the disclosed wheel must show the seeded look's colour, not the default
    }
    property string dirtyText: "All changes saved"
    property bool applying: false   // an Apply is running on the worker thread
    property var applyResult: null          // last Apply-to-hardware report
    property string compareProfile: ""      // profile-diff: compare the active profile against this one
    readonly property var applyHealth: {    // honest footer numbers from the last apply
        var r = root.applyResult
        if (!r || !r.devices || !r.devices.length) return null
        var live = 0, dropped = 0, failed = 0
        for (var i = 0; i < r.devices.length; i++) {
            var d = r.devices[i]
            if (d.ok) live += (d.count || 0)
            else if (d.error && d.error !== "no applicable binds") failed++   // a device that didn't apply
            dropped += (d.warnings ? d.warnings.length : 0)
        }
        return { live: live, dropped: dropped, failed: failed }
    }
    property var capSummary: ({})            // per-device captured-key counts
    property bool qaLive: false              // offscreen QA: force the LIVE pill visible
    property string capSource: "none"        // 'user' | 'default' | 'none' — drives the calibrate hint
    property bool hintDismissed: false
    property bool shotMode: false            // a screenshot is being captured — hide demo chrome
    // ---- live in-app calibration ----
    property bool calibrating: false         // calibrate mode active
    property string armedKey: ""             // hotspot awaiting a physical press (pulses)
    property var capturedIds: []             // hotspots the user has captured (this device)
    property var calBindable: []             // hotspots that need a code, in walk order
    readonly property int calDone: {         // captured keys that actually need calibrating
        var n = 0
        for (var i = 0; i < calBindable.length; i++)
            if (capturedIds.indexOf(calBindable[i]) >= 0) n++
        return n
    }

    // ---------- derived ----------
    readonly property var device: backend.layouts[curDev]
    readonly property var viewObj: (device && curView && device.views[curView]) ? device.views[curView] : null
    readonly property var viewNames: backend.viewNames(curDev)
    property string bindLayer: "base"            // "base" | "shift" (Hypershift layer 2)
    property bool pickingHoldKey: false      // next hotspot click designates the hold key
    readonly property var bindMap: {
        var src = root.bindLayer === "shift" ? backend.shiftBindings : backend.bindings
        var p = src[curProfile]
        return (p && p[curDev]) ? p[curDev] : ({})
    }
    readonly property string holdKey: {      // the hold-to-shift key for this profile+device
        var m = backend.shiftKeysMap[curProfile]
        return (m && m[curDev]) ? m[curDev] : ""
    }
    readonly property var compareMap: {      // the compared profile's binds for this device+layer
        if (root.compareProfile === "") return ({})
        var src = root.bindLayer === "shift" ? backend.shiftBindings : backend.bindings
        var p = src[root.compareProfile]
        return (p && p[curDev]) ? p[curDev] : ({})
    }
    // profile-diff state for a hotspot vs the compared profile (same layer)
    function diffState(id) {
        if (root.compareProfile === "" || root.compareProfile === root.curProfile) return ""
        var a = root.bindMap[id], b = root.compareMap[id]
        if (a === b) return a === undefined ? "" : "same"
        if (a !== undefined && b === undefined) return "here"
        if (a === undefined) return "there"
        return "changed"
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
    function deselect() { selKey = ""; capValue = ""; listening = false; pickingHoldKey = false }
    function setLayer(l) { root.bindLayer = l; root.pickingHoldKey = false; root.deselect() }
    function setHoldKeyTo(id) {   // designate a hotspot as the shift hold key, then re-apply
        var clearing = id === root.holdKey
        backend.setShiftKey(root.curProfile, root.curDev, clearing ? "" : id)
        root.pickingHoldKey = false
        root.applyActiveProfile()
        if (clearing) { showToast("Hold key cleared"); return }
        var base = backend.bindings[curProfile]
        var baseBound = (base && base[curDev]) ? base[curDev][id] : undefined
        if (baseBound !== undefined && baseBound !== "")
            showToast(id.replace(/_/g, " ") + " is the hold key — clear its base bind (" + baseBound + ") so it doesn't fire when held")
        else
            showToast(id.replace(/_/g, " ") + " is now the hold key")
    }
    function switchDevice(dev) {
        if (root.calibrating && dev !== curDev) {   // follow calibration to the new device
            backend.endCalibration()
            curDev = dev; curView = firstView(dev); deselect(); syncLightDevice()
            if (!enterCalibrate()) { root.calibrating = false; applyActiveProfile() }   // restore binds on failure
            return
        }
        curDev = dev; curView = firstView(dev); deselect(); syncLightDevice()
    }
    function markDirty() { dirtyText = "● Unsaved → autosaving…"; dirtyTimer.restart() }
    function showToast(m) { toast.msg = m; toast.show() }
    function curBinding() { return bindMap[selKey] !== undefined ? bindMap[selKey] : "" }
    property var ov: ({})   // drag-align position overrides, keyed "dev|view|id"
    function ovKey(id) { return curDev + "|" + curView + "|" + id }
    function setCoord(id, nx, ny) { var m = ov; m[ovKey(id)] = { x: Math.round(nx), y: Math.round(ny) }; ov = m }
    function alpha(c, a) { return Qt.rgba(c.r, c.g, c.b, a) }   // theme color at alpha
    function mergeApplyResult(r) {   // fold a per-device apply into the whole-profile health
        if (!r || !r.devices) return
        if (!root.applyResult || !root.applyResult.devices) { root.applyResult = r; return }
        var idx = {}, devs = root.applyResult.devices.slice()
        for (var i = 0; i < devs.length; i++) idx[devs[i].dev] = i
        for (var j = 0; j < r.devices.length; j++) {
            var d = r.devices[j]
            if (idx[d.dev] !== undefined) devs[idx[d.dev]] = d
            else devs.push(d)
        }
        root.applyResult = { ok: devs.every(function (x) { return x.ok }), message: root.applyResult.message, devices: devs }
    }

    function applyBinding() {
        if (selKey === "") return
        listening = false                                     // committing ends listen mode
        var v = capValue !== "" ? capValue : curBinding()
        if (v === "" || v === "—") { showToast("Pick a binding first"); return }
        backend.setBinding(curProfile, curDev, selKey, v, root.bindLayer)
        markDirty()
        // set AND push live, off the GUI thread (tag=selKey so the result handler can
        // tell if THIS bind was dropped). Optimistic toast now; problems surface on apply.
        root.applying = true
        backend.applyToHardware(curProfile, curDev, selKey)
        if (root.bindLayer === "shift" && root.holdKey === "")
            showToast(selKey.replace(/_/g, " ") + " → " + v + "  · set a hold key to activate")
        else
            showToast(selKey.replace(/_/g, " ") + " → " + v)
    }
    // The skip warning for `key` in an apply report (the daemon drops binds it
    // can't express), or "" if it applied cleanly. Warnings are prefixed
    // "<hotspot>: …" or "<hotspot> = …"; match the exact token so a shorter id
    // (TAR_TPAD_N) can't swallow a longer one's warning (TAR_TPAD_NE).
    function bindWarning(report, key) {
        return (report.devices || [])
            .reduce(function (all, d) { return all.concat(d.warnings || []) }, [])
            .find(function (w) {
                var s = w.indexOf("shift ") === 0 ? w.slice(6) : w   // shift-layer warnings are 'shift '-prefixed
                return s.indexOf(key + ":") === 0 || s.indexOf(key + " ") === 0
            }) || ""
    }
    function clearBinding() {
        if (selKey === "") return
        listening = false
        backend.clearBinding(curProfile, curDev, selKey, root.bindLayer)
        capValue = ""; markDirty()
        root.applying = true
        backend.applyToHardware(curProfile, curDev, selKey)   // async; result -> onApplyFinished
        showToast("Cleared")
    }
    function applyActiveProfile() {   // the active profile is always what's live — push it
        if (!backend.deps.inputRemapper) { showToast("Switched to " + curProfile); return }
        applyTimer.restart()   // defer so the profile change paints before the (blocking) call
    }
    function hasApplyIssue(r) {   // a genuine problem worth the detail overlay, not just "empty"
        return (r.devices || []).some(function (d) {
            return (d.error && d.error !== "no applicable binds") || (d.warnings && d.warnings.length > 0)
        })
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
    // ---- in-app calibration: click a key, press it, it locks in ----
    function refreshCaptured() { root.capturedIds = backend.capturedIds(root.curDev) }
    function armKey(id) {
        if (!root.calibrating) return
        if (root.calBindable.indexOf(id) < 0) return   // combos / unavailable can't be calibrated
        root.armedKey = id; backend.armCalibration(id)
    }
    function armFrom(start) {   // arm the first uncaptured bindable at/after `start`
        for (var i = start; i < root.calBindable.length; i++)
            if (root.capturedIds.indexOf(root.calBindable[i]) < 0) { root.armKey(root.calBindable[i]); return true }
        return false
    }
    function armNextUncaptured() { if (!armFrom(0)) { root.armedKey = ""; backend.disarmCalibration() } }
    function skipArmed() {
        var start = root.armedKey === "" ? 0 : root.calBindable.indexOf(root.armedKey) + 1
        if (!armFrom(start)) armNextUncaptured()   // none after — wrap to any remaining
    }
    function enterCalibrate() {
        var r = backend.beginCalibration(root.curDev)
        if (!r.ok) { showToast(r.error); return false }
        root.aligning = false; root.lighting = false; root.deselect()
        root.calBindable = backend.bindableIds(root.curDev)
        root.calibrating = true
        refreshCaptured()
        if (!backend.demo) armNextUncaptured()   // demo: click a key to calibrate it (no auto-advance)
        return true
    }
    function exitCalibrate() {
        backend.endCalibration()
        root.calibrating = false; root.armedKey = ""
        root.capSummary = backend.captureSummary(); root.capSource = backend.capturesSource()
        applyActiveProfile()   // restore live binds (calibration had stopped them)
    }
    function toggleCalibrate() { if (root.calibrating) exitCalibrate(); else enterCalibrate() }
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
        root.lightState = backend.lightState()   // restore the saved look(s) across sessions
        // offscreen QA: drive initial state from env vars
        var q = backend.qaState()
        if (q.KEYZER_DEV) switchDevice(q.KEYZER_DEV)
        if (q.KEYZER_VIEW) curView = q.KEYZER_VIEW
        if (q.KEYZER_PROFILE) curProfile = q.KEYZER_PROFILE
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
        if (q.KEYZER_LIGHTPANEL) root.openLightingDemo()
        if (q.KEYZER_LIGHTFX) {   // QA/screenshots: seed a specific effect (+ optional zone) so each contextual state renders
            if (q.KEYZER_LIGHTZONE) root.lightZone = q.KEYZER_LIGHTZONE
            var lseed = Object.assign({}, root.curLight(), { effect: q.KEYZER_LIGHTFX })
            var lmap = Object.assign({}, root.lightState); lmap[root.lightKey()] = lseed; root.lightState = lmap
            root.syncWheelToLook()   // keep the wheel on the seeded colour, not the default
        }
        if (q.KEYZER_SHOT) root.shotMode = true   // screenshot render — drop the demo badge
        if (q.KEYZER_SHIFT === "1") root.bindLayer = "shift"   // QA: render the Hypershift layer
        if (q.KEYZER_COMPARE) root.compareProfile = q.KEYZER_COMPARE   // QA: render profile-diff
        if (q.KEYZER_CALIBRATE === "1") {   // QA: render calibrate mode without a live capture session
            root.calBindable = backend.bindableIds(root.curDev)
            root.capturedIds = root.calBindable.slice(0, 5)   // a few already set
            root.armedKey = root.calBindable.length > 5 ? root.calBindable[5] : ""
            root.calibrating = true
        }
        // restore the lighting view-mode across restarts (persisted pref)
        if (backend.getSetting("lighting", false) === true && !root.lighting) { root.lighting = true; root.enterLighting() }
    }

    Timer { id: dirtyTimer; interval: 1400; onTriggered: root.dirtyText = "All changes saved" }
    Timer {
        id: applyTimer; interval: 60
        // a full-profile apply (tag=""); the result lands in onApplyFinished
        onTriggered: { root.applying = true; backend.applyToHardware(root.curProfile, "", "") }
    }
    Timer { running: root.lighting; interval: 220; repeat: true; onTriggered: root.litStep++ }

    // live calibration: each physical press the worker captures lands here
    Connections {
        target: backend
        function onLightingDevicesReady(snap) { root.onLightingReady(snap) }
        function onLightingOpFailed(msg) { root.showToast(msg || "lighting failed") }
        function onApplyFinished(report, tag) {
            root.applying = false
            if (tag !== "") {                        // a per-bind apply (applyBinding/clearBinding)
                root.mergeApplyResult(report)        // keep the footer health current
                var warn = root.bindWarning(report, tag)   // did THIS bind get dropped server-side?
                if (warn) root.showToast("⚠ not applied — " + warn)
                return
            }
            // a full-profile apply (profile switch)
            if (report.devices && report.devices.length) root.applyResult = report
            if (report.ok) { root.showToast(root.curProfile + " — " + report.message); return }
            if (root.hasApplyIssue(report)) { resultOverlay.visible = true; return }
            root.showToast((report.devices && report.devices.length) ? ("Switched to " + root.curProfile)
                                                                     : (root.curProfile + " — " + report.message))
        }
        function onKeyCaptured(dev, hotspot, label) {
            if (!root.calibrating || dev !== root.curDev) return
            if (backend.demo) {   // simulated capture — track in memory (no disk), no auto-advance
                if (root.capturedIds.indexOf(hotspot) < 0) {
                    var c = root.capturedIds.slice(); c.push(hotspot); root.capturedIds = c
                }
                root.armedKey = ""
            } else {
                root.refreshCaptured()
                root.armNextUncaptured()
            }
            root.showToast("Got it — " + hotspot.replace(/_/g, " ") + (label ? "  (" + label + ")" : ""))
        }
        function onCalibrationError(msg) { root.showToast("Calibrate — " + msg) }
    }

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
    component PulseDot: Rectangle {   // a small breathing status dot (live/recording)
        id: pd
        property bool active: true
        property real lo: 0.3
        property int half: 600
        radius: width / 2
        SequentialAnimation on opacity {
            running: pd.active; loops: Animation.Infinite
            NumberAnimation { to: pd.lo; duration: pd.half }
            NumberAnimation { to: 1.0; duration: pd.half }
        }
    }

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

    // a selectable pill — the shared shape for the lighting EFFECT / SPEED / DIRECTION choices.
    // hero=true is the larger, accent-bordered EFFECT pill; the rest are the compact parameter pills.
    component SelPill: Rectangle {
        id: pill
        property string label: ""
        property bool selected: false
        property bool hero: false
        signal picked()
        implicitWidth: pillTxt.implicitWidth + (hero ? 22 : 20); implicitHeight: hero ? 30 : 28; radius: 7
        color: selected ? root.greenDim : root.panel2
        border.width: 1; border.color: selected ? (hero ? root.greenHot : root.green) : root.lineC
        Text { id: pillTxt; anchors.centerIn: parent; text: pill.label; font.bold: true
            font.pixelSize: hero ? 12 : 11; color: pill.selected ? root.greenTxt : root.muted }
        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: pill.picked() }
    }

    component Hotspot: Item {
        id: hs
        property var k
        property string binding: ""
        property bool selected: false
        property bool conflict: false
        property string unavailable: ""
        property int litIndex: 0
        property bool calibrating: false   // calibrate mode on
        property bool captured: false      // user has a recorded code for this key
        property bool armed: false         // awaiting a press for this key right now
        property bool combo: false         // derived 8-way diagonal — not directly captured
        property bool isHold: false        // the Hypershift hold key (shown in the shift layer)
        property string diff: ""           // profile-diff: ""|"same"|"changed"|"here"|"there"
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
        // ---- calibrate states: armed (press it now) is the one bold moment ----
        Rectangle {   // armed bloom
            visible: hs.armed
            anchors.fill: parent; anchors.margins: -16; radius: 22
            color: root.alpha(root.amber, 0.10 + 0.10 * root.pulse)
        }
        Rectangle {   // armed ring — pulsing amber "press it"
            visible: hs.armed
            anchors.fill: parent; radius: 9
            color: root.alpha(root.amber, 0.14 + 0.08 * root.pulse)
            border.width: 2; border.color: root.alpha(root.amber, 0.6 + 0.4 * root.pulse)
        }
        Rectangle {   // captured ✓ — quiet green confirmation
            visible: hs.calibrating && hs.captured && !hs.armed
            anchors.fill: parent; radius: 9
            color: root.alpha(root.green, 0.07)
            border.width: 1.5; border.color: root.alpha(root.green, 0.55)
        }
        Rectangle {   // still-to-do — a faint outline so the work left is legible, not loud
            visible: hs.calibrating && !hs.captured && !hs.armed && !hs.combo && hs.unavailable === ""
            anchors.fill: parent; radius: 9
            color: "transparent"
            border.width: 1; border.color: root.alpha(root.muted2, 0.45)
        }
        Rectangle {   // Hypershift hold-key marker (violet)
            visible: hs.isHold
            anchors.fill: parent; radius: 9
            color: root.alpha(root.violet, 0.12)
            border.width: 2; border.color: root.violet
        }
        Rectangle {   // profile-diff tint (compare mode): green=same, amber=changed, cyan=only-here, violet=only-there
            visible: hs.diff !== ""
            anchors.fill: parent; radius: 9
            color: hs.diff === "changed" ? root.alpha(root.amber, 0.14)
                 : hs.diff === "here" ? root.alpha(root.cyan, 0.12)
                 : hs.diff === "there" ? root.alpha(root.violet, 0.10)
                 : root.alpha(root.green, 0.05)
            border.width: hs.diff === "same" ? 1 : 2
            border.color: hs.diff === "changed" ? root.amber
                        : hs.diff === "here" ? root.cyan
                        : hs.diff === "there" ? root.violet
                        : root.alpha(root.green, 0.4)
        }
        Rectangle {
            id: pill
            visible: hs.calibrating ? (hs.armed || hs.captured || hs.unavailable !== "")
                                    : (hs.binding !== "" || hs.selected || hs.unavailable !== "" || hs.isHold)
            anchors.centerIn: parent
            width: Math.max(26, pillTxt.implicitWidth + 14); height: 24; radius: 6
            color: Qt.rgba(0.03, 0.035, 0.024, 0.86)
            border.width: hs.selected ? 1.5 : 1
            border.color: hs.unavailable !== "" ? root.line2
                        : hs.calibrating ? (hs.armed ? root.amber : root.green)
                        : hs.isHold ? root.violet
                        : hs.conflict ? root.amber
                        : hs.selected ? root.greenHot : root.alpha(root.green, 0.45)
            Text {
                id: pillTxt; anchors.centerIn: parent
                text: hs.unavailable !== "" ? "n/a"
                    : hs.calibrating ? (hs.armed ? "●" : hs.captured ? "✓" : "")
                    : hs.isHold ? "HOLD"
                    : (hs.binding !== "" ? hs.binding : (hs.selected ? "·" : ""))
                color: hs.unavailable !== "" ? root.muted2
                     : hs.calibrating ? (hs.armed ? root.amber : root.green)
                     : hs.isHold ? root.violet
                     : hs.conflict ? root.amber : (hs.selected ? root.greenHot : root.green)
                font.pixelSize: 14; font.bold: true
            }
        }
        Rectangle {
            id: glow
            visible: root.lighting
            anchors.fill: parent; radius: 9
            color: "transparent"
            border.width: 2
            border.color: root.liveGlow   // the real applied colour/effect, not a fake rainbow
        }
        HoverHandler { id: hov; cursorShape: root.aligning ? Qt.SizeAllCursor : Qt.PointingHandCursor }
        TapHandler {
            enabled: !root.aligning && !root.lighting
            onTapped: {
                if (hs.unavailable !== "") { root.showToast(hs.unavailable); return }
                if (root.pickingHoldKey) {
                    if (hs.combo) root.showToast("Pick a single key as the hold key")
                    else root.setHoldKeyTo(hs.k.id)
                    return
                }
                if (root.calibrating) {
                    if (hs.combo) root.showToast("8-way diagonals are set from their two keys — no need to calibrate")
                    else root.armKey(hs.k.id)
                    return
                }
                root.selectKey(hs.k.id)
            }
        }
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
            Rectangle {   // demo-mode badge — for interactive demo users; hidden in screenshots
                visible: backend.demo && !root.shotMode
                anchors.verticalCenter: parent.verticalCenter
                width: demoRow.implicitWidth + 16; height: 22; radius: 6
                color: root.panel2; border.width: 1; border.color: root.alpha(root.cyan, 0.55)
                Row {
                    id: demoRow; anchors.centerIn: parent; spacing: 6
                    Text { text: "DEMO"; color: root.cyan; font.pixelSize: 10; font.bold: true; font.letterSpacing: 1.5; anchors.verticalCenter: parent.verticalCenter }
                    Text { text: "no hardware · simulated"; color: root.muted2; font.pixelSize: 9; anchors.verticalCenter: parent.verticalCenter }
                }
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
                                onTriggered: { backend.setActiveProfile(modelData); root.syncProfile(); root.deselect(); if (root.compareProfile === root.curProfile) root.compareProfile = ""; if (root.calibrating) root.exitCalibrate(); else root.applyActiveProfile() }
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
            // profile-diff: compare the active profile against another
            Rectangle {
                id: cmpDd
                anchors.verticalCenter: parent.verticalCenter
                width: cmpRow.implicitWidth + 24; height: 34; radius: 9
                color: root.compareProfile !== "" ? root.alpha(root.cyan, 0.14) : root.panel2
                border.width: 1; border.color: (cmpMa.containsMouse || root.compareProfile !== "") ? root.cyan : root.lineC
                Row {
                    id: cmpRow; anchors.centerIn: parent; spacing: 6
                    Text { anchors.verticalCenter: parent.verticalCenter; font.pixelSize: 12; font.bold: true
                           text: root.compareProfile !== "" ? ("vs " + root.compareProfile) : "Compare"
                           color: root.compareProfile !== "" ? root.cyan : root.muted }
                    Text { anchors.verticalCenter: parent.verticalCenter; text: "▾"; color: root.muted; font.pixelSize: 11 }
                }
                MouseArea { id: cmpMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: cmpMenu.open() }
                Menu {
                    id: cmpMenu; y: cmpDd.height + 4
                    MenuItem { text: "Off"; onTriggered: root.compareProfile = "" }
                    MenuSeparator {}
                    Repeater {
                        model: backend.profileList
                        MenuItem {
                            enabled: modelData !== root.curProfile
                            text: (modelData === root.compareProfile ? "●  " : "      ") + modelData
                            onTriggered: root.compareProfile = modelData
                        }
                    }
                }
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
                    PulseDot {
                        width: 8; height: 8; anchors.verticalCenter: parent.verticalCenter
                        color: lpMa.containsMouse ? root.danger : root.green
                        active: livePill.visible; lo: 0.35; half: 700
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter; font.pixelSize: 11; font.bold: true; font.letterSpacing: 1
                        text: lpMa.containsMouse ? "STOP" : "LIVE"
                        color: lpMa.containsMouse ? root.danger : root.green
                    }
                }
                MouseArea { id: lpMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.stopHardware() }
            }
            FlatSwitch {
                anchors.verticalCenter: parent.verticalCenter; label: "CALIBRATE"
                enabled: backend.deps.inputRemapper; on: root.calibrating
                accent: root.alpha(root.amber, 0.5); accentBorder: root.amber
                onToggled: root.toggleCalibrate()
            }
            FlatSwitch {
                anchors.verticalCenter: parent.verticalCenter; label: "LIGHTING"
                enabled: backend.deps.openrazer; on: root.lighting
                onToggled: { root.lighting = !root.lighting; backend.setSetting("lighting", root.lighting); if (root.lighting) { root.aligning = false; if (root.calibrating) root.exitCalibrate(); root.deselect(); root.enterLighting() } }
            }
            FlatSwitch { anchors.verticalCenter: parent.verticalCenter; label: "ALIGN"; on: root.aligning; accent: "#1d7fa6"; accentBorder: root.cyan; onToggled: { root.aligning = !root.aligning; if (root.aligning) { root.lighting = false; if (root.calibrating) root.exitCalibrate() } root.deselect() } }
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
            Text {   // honest health from the last apply: what's live, what got dropped
                visible: root.applyHealth !== null && !root.lighting
                anchors.verticalCenter: parent.verticalCenter; font.pixelSize: 12
                text: root.applyHealth ? ("✓ " + root.applyHealth.live + " live"
                      + (root.applyHealth.dropped > 0 ? " · ⚠ " + root.applyHealth.dropped + " dropped" : "")
                      + (root.applyHealth.failed > 0 ? " · ✕ " + root.applyHealth.failed + " not applied" : "")) : ""
                color: (root.applyHealth && (root.applyHealth.dropped > 0 || root.applyHealth.failed > 0)) ? root.amber : root.green
            }
            Text { visible: !root.lighting; text: "Preset: " + backend.presetNameFor(root.curProfile); color: root.muted; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter }
            Text { visible: root.lighting; text: "Lighting: " + root.lightLabel() + " · " + Math.round(root.lightBright) + "%"; color: root.green; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter }
        }
        Text { anchors { right: parent.right; rightMargin: 18; verticalCenter: parent.verticalCenter }text: root.applying ? "Applying…" : root.dirtyText; color: root.muted; font.pixelSize: 12 }
    }

    // ================= first-run calibration hint =================
    Item {
        id: hintBar
        anchors { top: header.bottom; left: parent.left; right: parent.right }
        height: (root.capSource === "default" && !root.hintDismissed && !root.calibrating && !backend.demo) ? 34 : 0
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
                Text {
                    width: parent.width; wrapMode: Text.WordWrap; topPadding: 16
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
                spacing: 14; visible: !root.lighting && !root.calibrating && root.selKey === ""
                Text { anchors.horizontalCenter: parent.horizontalCenter; text: "⊕"; color: root.green; font.pixelSize: 40; opacity: 0.6 }
                Text { horizontalAlignment: Text.AlignHCenter; text: "Select a key on the device\nto map it."; color: root.muted2; font.pixelSize: 13; lineHeight: 1.4 }
            }

            // ---------- calibrate help (replaces the assign panel while calibrating) ----------
            Column {
                anchors { top: parent.top; left: parent.left; right: parent.right; topMargin: 54; leftMargin: 22; rightMargin: 22 }
                spacing: 13; visible: root.calibrating
                Row {
                    spacing: 9
                    PulseDot { width: 10; height: 10; color: root.amber; active: root.calibrating; anchors.verticalCenter: parent.verticalCenter }
                    Text { text: "Calibrating"; color: root.txt; font.pixelSize: 16; font.bold: true; anchors.verticalCenter: parent.verticalCenter }
                }
                Text { text: root.device ? root.device.name : root.curDev; color: root.amber; font.pixelSize: 13; font.bold: true }
                Text {
                    width: parent.width; wrapMode: Text.WordWrap; color: root.muted; font.pixelSize: 12; lineHeight: 1.4
                    text: "Press each key on your hardware as it lights up amber. Every press is saved to your device map instantly."
                }
                Rectangle {   // progress bar
                    width: parent.width; height: 8; radius: 4; color: root.alpha(root.amber, 0.14)
                    Rectangle {
                        height: parent.height; radius: 4; color: root.amber
                        width: parent.width * (root.calBindable.length ? root.calDone / root.calBindable.length : 0)
                    }
                }
                Text { text: root.calDone + " / " + root.calBindable.length + " keys set"
                       color: root.amber; font.pixelSize: 12; font.bold: true }
                Column {
                    spacing: 6; topPadding: 6
                    Text { text: "•  Click any key to jump to it"; color: root.muted2; font.pixelSize: 12 }
                    Text { text: "•  Space — skip the current key"; color: root.muted2; font.pixelSize: 12 }
                    Text { text: "•  Esc or Done — finish"; color: root.muted2; font.pixelSize: 12 }
                }
            }

            // ---------- lighting inspector (canvas-native mode) ----------
            Flickable {
                anchors { fill: parent; margins: 20 }
                visible: root.lighting; clip: true
                contentHeight: liCol.implicitHeight
                Column {
                    id: liCol
                    width: parent.width; spacing: 14
                    Text { text: "Lighting"; color: root.txt; font.pixelSize: 15; font.bold: true }
                    Text { text: root.lightLabel(); color: root.green; font.pixelSize: 13; font.bold: true; visible: root.lightDev() !== null }
                    Text {
                        visible: root.lightDev() === null
                        width: parent.width; wrapMode: Text.WordWrap; color: root.muted; font.pixelSize: 12; lineHeight: 1.35
                        text: root.lightInfo.checking
                              ? "Checking OpenRazer…"
                              : root.lightInfo.error
                                ? ("OpenRazer: " + root.lightInfo.error)
                                : ("No lighting for " + (root.device ? root.device.name : root.curDev) + " — is the OpenRazer daemon running and are you in the 'plugdev' group?")
                    }
                    Rectangle {   // Recheck (re-query OpenRazer)
                        visible: root.lightDev() === null && !root.lightInfo.checking
                        width: rcT.implicitWidth + 24; height: 28; radius: 7; color: root.panel2; border.width: 1; border.color: root.lineC
                        Text { id: rcT; anchors.centerIn: parent; text: "↻ Recheck"; color: root.txt; font.pixelSize: 12 }
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.enterLighting() }
                    }
                    Column {
                        visible: root.lightDev() !== null
                        width: parent.width; spacing: 13
                        // current state — what's applied to this device/zone right now
                        Rectangle {
                            width: parent.width; height: nowCol.implicitHeight + 18; radius: 9
                            color: root.panel2; border.width: 1; border.color: root.lineC
                            Column {
                                id: nowCol
                                anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 11 }
                                spacing: 3
                                Text { text: "NOW"; color: root.muted2; font.pixelSize: 9; font.letterSpacing: 1.6 }
                                Row {
                                    spacing: 8
                                    Rectangle {
                                        visible: root.lightUsesColour()
                                        width: 14; height: 14; radius: 4; anchors.verticalCenter: parent.verticalCenter
                                        color: Qt.rgba(root.curLight().r / 255, root.curLight().g / 255, root.curLight().b / 255, 1)
                                        border.width: 1; border.color: root.line2
                                    }
                                    Text {
                                        anchors.verticalCenter: parent.verticalCenter; font.pixelSize: 13; font.bold: true; color: root.txt
                                        text: root.curLight().effect === "" ? "No look set yet — pick an effect"
                                            : (root.effectLabel(root.curLight().effect)
                                               + (root.lightUsesDirection() ? " · " + (root.curLight().direction === "left" ? "← Left" : "Right →") : "")
                                               + (root.lightUsesSpeed() ? " · " + root.speedLabel(root.curLight().react_ms) : "")
                                               + " · " + Math.round(root.lightBright) + "% bright")
                                    }
                                }
                            }
                        }
                        // zones (only when the device exposes them; Tartarus shows none)
                        Flow {
                            visible: root.lightDev() && (root.lightDev().zones || []).length > 0
                            width: parent.width; spacing: 6
                            Repeater {
                                model: root.lightZonesFor()
                                Rectangle {
                                    height: 26; width: zlt.implicitWidth + 20; radius: 7
                                    color: root.lightZone === modelData.name ? root.greenDim : root.panel2
                                    border.width: 1; border.color: root.lightZone === modelData.name ? root.green : root.lineC
                                    Text { id: zlt; anchors.centerIn: parent; text: modelData.label; font.pixelSize: 11; font.bold: true
                                        color: root.lightZone === modelData.name ? root.greenTxt : root.muted }
                                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.selectLightZone(modelData.name) }
                                }
                            }
                        }
                        Text { text: "Brightness " + Math.round(root.lightBright) + "%"; color: root.muted2; font.pixelSize: 10; font.letterSpacing: 1 }
                        Rectangle {
                            width: parent.width; height: 16; radius: 8; color: root.panel2; border.width: 1; border.color: root.line2
                            Rectangle { anchors { left: parent.left; top: parent.top; bottom: parent.bottom }
                                width: Math.max(8, root.lightBright / 100 * parent.width); radius: 8; color: root.greenDim }
                            Rectangle { width: 4; height: parent.height + 6; radius: 2; color: "white"
                                y: -3; x: Math.min(parent.width - 4, root.lightBright / 100 * (parent.width - 4)) }
                            MouseArea {
                                anchors.fill: parent; cursorShape: Qt.PointingHandCursor; preventStealing: true
                                function setFrom(mx) { root.lightBright = Math.max(0, Math.min(100, Math.round(mx / width * 100))) }
                                onPressed: function (m) { setFrom(m.x) }
                                onPositionChanged: function (m) { if (pressed) setFrom(m.x) }
                                onReleased: root.setLightBright(root.lightBright)
                            }
                        }
                        // EFFECT — pick the look first (the primary choice), set off by its own rule
                        Rectangle { width: parent.width; height: 1; color: root.lineC }
                        Text { text: "EFFECT"; color: root.muted2; font.pixelSize: 10; font.letterSpacing: 1.6 }
                        Flow {
                            width: parent.width; spacing: 6
                            Repeater {
                                model: root.uniqueEffects(root.curZoneEffects())
                                // compare by label so the single "Pulse" pill stays selected whichever breath_* the device advertised
                                SelPill { hero: true; label: root.effectLabel(modelData)
                                    selected: root.effectLabel(root.curLight().effect) === root.effectLabel(modelData)
                                    onPicked: root.applyLighting({ effect: modelData }) }
                            }
                        }
                        // contextual controls — only what the chosen effect actually uses
                        Rectangle { visible: root.lightUsesColour() || root.lightUsesSpeed() || root.lightUsesDirection()
                            width: parent.width; height: 1; color: root.lineC }
                        Text { visible: root.lightUsesColour(); text: "COLOUR"; color: root.muted2; font.pixelSize: 10; font.letterSpacing: 1.6 }
                        Flow {
                            visible: root.lightUsesColour(); width: parent.width; spacing: 8
                            Repeater {
                                model: root.lightSwatches
                                Rectangle {
                                    property bool sel: root.curLight().r === modelData[1] && root.curLight().g === modelData[2] && root.curLight().b === modelData[3]
                                    width: 30; height: 30; radius: 7
                                    color: Qt.rgba(modelData[1] / 255, modelData[2] / 255, modelData[3] / 255, 1)
                                    border.width: sel ? 3 : 1; border.color: sel ? root.greenHot : (lsw.containsMouse ? root.txt : root.line2)
                                    MouseArea { id: lsw; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                        onClicked: root.applyLighting({ r: modelData[1], g: modelData[2], b: modelData[3] }) }
                                }
                            }
                        }
                        Rectangle {   // custom-colour disclosure
                            visible: root.lightUsesColour()
                            width: lcpT.implicitWidth + 26; height: 24; radius: 6
                            color: root.lcWheel ? root.greenDim : root.panel2
                            border.width: 1; border.color: root.lcWheel ? root.green : root.lineC
                            Text { id: lcpT; anchors.centerIn: parent; text: "🎨 Custom colour"; font.pixelSize: 11; font.bold: true
                                color: root.lcWheel ? root.greenTxt : root.muted }
                            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.lcWheel = !root.lcWheel }
                        }
                        Column {
                            visible: root.lightUsesColour() && root.lcWheel; width: parent.width; spacing: 8
                            Item {
                                width: 150; height: 150
                                Canvas {
                                    anchors.fill: parent
                                    onVisibleChanged: if (visible) requestPaint()
                                    Component.onCompleted: requestPaint()
                                    onPaint: {
                                        var ctx = getContext("2d"), cx = width / 2, cy = height / 2, R = Math.min(cx, cy) - 3
                                        ctx.clearRect(0, 0, width, height)
                                        for (var a = 0; a < 360; a += 2) {
                                            ctx.beginPath(); ctx.moveTo(cx, cy); ctx.arc(cx, cy, R, (a - 1) * Math.PI / 180, (a + 2) * Math.PI / 180); ctx.closePath()
                                            ctx.fillStyle = Qt.hsva(a / 360, 1, 1, 1); ctx.fill()
                                        }
                                        var g = ctx.createRadialGradient(cx, cy, 0, cx, cy, R)
                                        g.addColorStop(0, Qt.rgba(1, 1, 1, 1)); g.addColorStop(1, Qt.rgba(1, 1, 1, 0))
                                        ctx.fillStyle = g; ctx.beginPath(); ctx.arc(cx, cy, R, 0, 2 * Math.PI); ctx.fill()
                                    }
                                }
                                Rectangle { width: 12; height: 12; radius: 6; color: "transparent"; border.width: 2; border.color: "white"
                                    x: 75 + Math.cos(root.lcH * 2 * Math.PI) * root.lcS * 72 - 6
                                    y: 75 + Math.sin(root.lcH * 2 * Math.PI) * root.lcS * 72 - 6 }
                                MouseArea { anchors.fill: parent; cursorShape: Qt.CrossCursor; preventStealing: true
                                    onPressed: function (m) { root.pickLightWheel(m.x, m.y) }
                                    onPositionChanged: function (m) { if (pressed) root.pickLightWheel(m.x, m.y) }
                                    onReleased: root.applyLighting({ r: Math.round(root.lcColor.r * 255), g: Math.round(root.lcColor.g * 255), b: Math.round(root.lcColor.b * 255) }) }
                            }
                            Rectangle { width: parent.width; height: 30; radius: 7; color: root.lcColor; border.width: 1; border.color: hexField.activeFocus ? root.green : root.line2
                                // editable hex — select to copy, type/paste a value + Enter to apply
                                TextInput {
                                    id: hexField
                                    anchors.centerIn: parent; width: parent.width - 16
                                    text: root.curHex()
                                    color: root.lcV > 0.55 ? "#101010" : "#f0f0f0"; font.pixelSize: 12; font.bold: true
                                    horizontalAlignment: TextInput.AlignHCenter
                                    selectByMouse: true; maximumLength: 9   // room for a space-padded paste that trim() cleans
                                    inputMethodHints: Qt.ImhPreferLatin | Qt.ImhNoAutoUppercase | Qt.ImhNoPredictiveText
                                    // single commit site: Enter just drops focus, so both Enter and click-away
                                    // go through the one guard below (commit once if changed, else snap back).
                                    onActiveFocusChanged: {
                                        if (activeFocus) selectAll()
                                        else {
                                            if (text.replace("#", "").toLowerCase() !== root.curHex().replace("#", "").toLowerCase()) root.setLightHex(text)
                                            text = Qt.binding(function () { return root.curHex() })
                                        }
                                    }
                                    onAccepted: focus = false
                                } }
                            Rectangle {   // value slider
                                width: parent.width; height: 16; radius: 8
                                gradient: Gradient { orientation: Gradient.Horizontal
                                    GradientStop { position: 0; color: "#000000" }
                                    GradientStop { position: 1; color: Qt.hsva(root.lcH, root.lcS, 1, 1) } }
                                Rectangle { width: 4; height: parent.height + 6; radius: 2; color: "white"; y: -3; x: root.lcV * (parent.width - 4) }
                                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; preventStealing: true
                                    onPressed: function (m) { root.lcV = Math.max(0, Math.min(1, m.x / width)) }
                                    onPositionChanged: function (m) { if (pressed) root.lcV = Math.max(0, Math.min(1, m.x / width)) }
                                    onReleased: root.applyLighting({ r: Math.round(root.lcColor.r * 255), g: Math.round(root.lcColor.g * 255), b: Math.round(root.lcColor.b * 255) }) }
                            }
                            Text { width: parent.width; wrapMode: Text.WordWrap; color: root.muted2; font.pixelSize: 10
                                text: "Drag to preview · release to apply · or type/paste a hex above + Enter" }
                        }
                        // SPEED — React only
                        Text { visible: root.lightUsesSpeed(); text: "SPEED"; color: root.muted2; font.pixelSize: 10; font.letterSpacing: 1.6 }
                        Row {
                            visible: root.lightUsesSpeed(); spacing: 6
                            Repeater {
                                model: [{ ms: 1500, t: "Slow" }, { ms: 1000, t: "Medium" }, { ms: 500, t: "Fast" }]
                                SelPill { label: modelData.t; selected: root.curLight().react_ms === modelData.ms
                                    onPicked: root.applyLighting({ react_ms: modelData.ms }) }
                            }
                        }
                        // DIRECTION — Wave only
                        Text { visible: root.lightUsesDirection(); text: "DIRECTION"; color: root.muted2; font.pixelSize: 10; font.letterSpacing: 1.6 }
                        Row {
                            visible: root.lightUsesDirection(); spacing: 6
                            Repeater {
                                model: [{ d: "left", t: "←  Left" }, { d: "right", t: "Right  →" }]
                                SelPill { label: modelData.t; selected: root.curLight().direction === modelData.d
                                    onPicked: root.applyLighting({ direction: modelData.d }) }
                            }
                        }
                        // sync across devices (a global OpenRazer setting) — kept at the bottom
                        Rectangle { width: parent.width; height: 1; color: root.lineC }
                        FlatSwitch {
                            label: "Sync all devices"; on: root.lightSync
                            onToggled: { root.lightSync = !root.lightSync   // optimistic; errors -> onLightingOpFailed
                                backend.setLightingSync(root.lightSync) }
                        }
                        Text { width: parent.width; wrapMode: Text.WordWrap; color: root.muted2; font.pixelSize: 10
                            text: root.lightSync ? "One look mirrors to every Razer device." : "Each device is controlled on its own." }
                    }
                }
            }

            // assign body
            Column {
                anchors { fill: parent; margins: 20 }
                spacing: 16; visible: !root.lighting && root.selKey !== ""

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
                                border.width: 1; border.color: (root.listening || bindField.activeFocus) ? root.green : root.line2
                                TextField {
                                    id: bindField
                                    anchors.fill: parent; anchors.margins: 2; leftPadding: 14
                                    verticalAlignment: TextInput.AlignVCenter
                                    font.pixelSize: 17; font.bold: true
                                    color: root.listening ? root.green : root.txt
                                    placeholderText: root.curBinding() !== "" ? root.curBinding()
                                                     : (root.listening ? "press a key…" : "press Listen, or type — e.g. W+A, Ctrl+C")
                                    selectByMouse: true
                                    background: Item {}
                                    onTextEdited: root.capValue = text                 // typing IS the binding (e.g. W+A held together)
                                    onAccepted: root.applyBinding()
                                    Connections { target: root; function onCapValueChanged() {
                                        if (bindField.text !== root.capValue) bindField.text = root.capValue } }   // reflect Listen / key-select
                                    Component.onCompleted: text = root.capValue
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
                                    "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12", "Disable"]
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

            // Hypershift: layer selector + hold-key control (top-right of the stage)
            Row {
                anchors { top: parent.top; right: parent.right; margins: 14 }
                z: 4; spacing: 8
                visible: !root.aligning && !root.calibrating && !root.lighting
                Rectangle {   // [ Base | Shift ] segmented
                    width: segRow.implicitWidth + 6; height: 30; radius: 9
                    color: root.panel2; border.width: 1; border.color: root.lineC
                    anchors.verticalCenter: parent.verticalCenter
                    Row {
                        id: segRow; anchors.centerIn: parent; spacing: 3
                        Repeater {
                            model: [{ k: "base", t: "Base" }, { k: "shift", t: "Shift" }]
                            Rectangle {
                                property bool sel: root.bindLayer === modelData.k
                                property bool isShift: modelData.k === "shift"
                                width: segTxt.implicitWidth + 26; height: 24; radius: 6
                                color: sel ? (isShift ? root.alpha(root.violet, 0.22) : root.greenDim) : "transparent"
                                border.width: sel ? 1 : 0
                                border.color: isShift ? root.violet : root.green
                                Text {
                                    id: segTxt; anchors.centerIn: parent; text: modelData.t
                                    color: sel ? (isShift ? root.violet : root.greenTxt) : root.muted
                                    font.pixelSize: 12; font.bold: true
                                }
                                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.setLayer(modelData.k) }
                            }
                        }
                    }
                }
                Rectangle {   // hold-key control — only in the Shift layer
                    visible: root.bindLayer === "shift"
                    anchors.verticalCenter: parent.verticalCenter
                    width: holdRow.implicitWidth + 18; height: 30; radius: 9
                    color: root.pickingHoldKey ? root.alpha(root.violet, 0.22) : root.panel2
                    border.width: 1; border.color: root.violet
                    Row {
                        id: holdRow; anchors.centerIn: parent; spacing: 7
                        Text { anchors.verticalCenter: parent.verticalCenter; font.pixelSize: 10; font.letterSpacing: 1; color: root.muted2; text: "HOLD" }
                        Text {
                            anchors.verticalCenter: parent.verticalCenter; font.pixelSize: 12; font.bold: true; color: root.violet
                            text: root.pickingHoldKey ? "click a key…"
                                : (root.holdKey ? root.holdKey.replace(/_/g, " ") : "set a key")
                        }
                    }
                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.pickingHoldKey = !root.pickingHoldKey }
                }
            }

            // shift-layer banner — what this layer is, while it's active
            Rectangle {
                visible: root.bindLayer === "shift" && !root.aligning && !root.calibrating && !root.lighting
                anchors { top: parent.top; horizontalCenter: parent.horizontalCenter; topMargin: 14 }
                z: 3
                width: shiftRow.implicitWidth + 26; height: 34; radius: 10
                color: Qt.rgba(0.07, 0.065, 0.1, 0.94); border.width: 1; border.color: root.violet
                Row {
                    id: shiftRow; anchors.centerIn: parent; spacing: 9
                    Text { text: "⇧ SHIFT LAYER"; color: root.violet; font.pixelSize: 11; font.bold: true; font.letterSpacing: 1; anchors.verticalCenter: parent.verticalCenter }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter; font.pixelSize: 12; color: "#d9d2ff"
                        text: root.holdKey ? ("fires while you hold " + root.holdKey.replace(/_/g, " "))
                                           : "set a hold key, then bind the second layer"
                    }
                }
            }

            // profile-diff legend — colour key while comparing (drops below the shift banner if both show)
            Rectangle {
                visible: root.compareProfile !== "" && root.compareProfile !== root.curProfile
                         && !root.aligning && !root.calibrating && !root.lighting
                anchors { top: parent.top; horizontalCenter: parent.horizontalCenter
                          topMargin: root.bindLayer === "shift" ? 56 : 14 }
                z: 3
                width: cmpLegend.implicitWidth + 26; height: 34; radius: 10
                color: Qt.rgba(0.05, 0.07, 0.086, 0.94); border.width: 1; border.color: root.cyan
                Row {
                    id: cmpLegend; anchors.centerIn: parent; spacing: 13
                    Text { text: "vs " + root.compareProfile; color: "#bfe9fb"; font.pixelSize: 12; font.bold: true; anchors.verticalCenter: parent.verticalCenter }
                    Repeater {
                        model: [{ c: root.amber, t: "changed" }, { c: root.cyan, t: "only here" }, { c: root.violet, t: "only there" }]
                        Row {
                            spacing: 5; anchors.verticalCenter: parent.verticalCenter
                            Rectangle { width: 8; height: 8; radius: 2; color: modelData.c; anchors.verticalCenter: parent.verticalCenter }
                            Text { text: modelData.t; color: root.muted2; font.pixelSize: 11; anchors.verticalCenter: parent.verticalCenter }
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

            // calibrate bar — amber "recording" banner: instruction + progress + Done
            Rectangle {
                visible: root.calibrating
                anchors { top: parent.top; horizontalCenter: parent.horizontalCenter; topMargin: 14 }
                z: 4
                width: calRow.implicitWidth + 28; height: 40; radius: 11
                color: Qt.rgba(0.08, 0.06, 0.03, 0.95); border.width: 1; border.color: root.amber
                Rectangle {   // soft amber halo so it reads as "live/recording"
                    anchors.fill: parent; anchors.margins: -6; radius: 16; z: -1
                    color: root.alpha(root.amber, 0.06 + 0.05 * root.pulse)
                }
                Row {
                    id: calRow; anchors.centerIn: parent; spacing: 13
                    PulseDot { width: 9; height: 9; color: root.amber; active: root.calibrating; anchors.verticalCenter: parent.verticalCenter }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: root.calBindable.length === 0 ? "Nothing to calibrate on this device"
                            : root.armedKey !== "" ? "Press the highlighted key on your device"
                            : (root.calDone >= root.calBindable.length ? "All keys set — press Done"
                               : "Click a key to calibrate it")
                        color: "#ffe6b8"; font.pixelSize: 12; font.bold: true
                    }
                    Rectangle { visible: root.calBindable.length > 0; width: 1; height: 18; color: root.alpha(root.amber, 0.4); anchors.verticalCenter: parent.verticalCenter }
                    Text {
                        visible: root.calBindable.length > 0
                        anchors.verticalCenter: parent.verticalCenter
                        text: root.calDone + " / " + root.calBindable.length
                        color: root.amber; font.pixelSize: 12; font.bold: true
                    }
                    Rectangle {   // Done
                        width: calDoneTxt.implicitWidth + 22; height: 26; radius: 7
                        anchors.verticalCenter: parent.verticalCenter
                        color: calDoneMa.containsMouse ? root.amber : root.alpha(root.amber, 0.18)
                        border.width: 1; border.color: root.amber
                        Text { id: calDoneTxt; anchors.centerIn: parent; text: "Done"
                               color: calDoneMa.containsMouse ? "#1a1306" : root.amber; font.pixelSize: 12; font.bold: true }
                        MouseArea { id: calDoneMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.exitCalibrate() }
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
                    Rectangle {   // calibrate focus scrim — mute the device's own RGB so
                        anchors.fill: parent          // the amber/green capture state reads clearly
                        visible: root.calibrating; radius: 8
                        color: Qt.rgba(0, 0, 0, 0.5)
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
                            calibrating: root.calibrating
                            captured: root.calibrating && root.capturedIds.indexOf(modelData.id) >= 0
                            armed: root.armedKey === modelData.id
                            combo: modelData.combo ? true : false
                            isHold: root.bindLayer === "shift" && root.holdKey === modelData.id
                            diff: root.diffState(modelData.id)
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
                if (root.calibrating) root.exitCalibrate()   // a profile change ends calibration (and re-applies)
                else root.applyActiveProfile()               // the new active profile becomes live immediately
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
    Shortcut { sequence: "Escape"; onActivated: { if (root.calibrating) root.exitCalibrate(); else if (!root.listening) root.deselect() } }
    Shortcut { sequence: "Space"; enabled: root.calibrating; onActivated: root.skipArmed() }
}
