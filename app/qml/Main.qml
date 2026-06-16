import QtQuick
import QtQuick.Controls

// KEYZER — skeleton milestone: load layouts.json, render the first device's
// image with its hotspot overlay (the core mechanic). Root is an Item so it
// can be hosted in a QQuickView (grab-able for offscreen QA). Backend is read
// in Component.onCompleted (context properties aren't reliable in eager
// top-level bindings during root construction).
Rectangle {
    id: root
    width: 1200
    height: 760
    color: "#0b0b0e"

    property var device: null
    property var view: null
    property string viewName: ""

    Component.onCompleted: {
        const layouts = backend.layouts
        const id = backend.deviceIds()[0]
        device = layouts[id]
        viewName = Object.keys(device.views)[0]
        view = device.views[viewName]
    }

    Text {
        id: brand
        anchors { top: parent.top; left: parent.left; margins: 18 }
        text: "KEY<font color='#44d62c'>ZER</font>"
        textFormat: Text.RichText
        font { pixelSize: 22; bold: true; letterSpacing: 2 }
        color: "#e9e9ee"
    }
    Text {
        id: sub
        anchors { top: brand.bottom; left: parent.left; leftMargin: 18; topMargin: 3 }
        text: root.device
              ? root.device.name + "   ·   " + root.viewName + "   ·   " + root.view.keys.length + " hotspots"
              : "loading…"
        color: "#8b8b97"
        font.pixelSize: 12
    }

    // Device stage: a native-sized item (image px) scaled to fit, with the
    // hotspot rectangles overlaid in the same coordinate space as layouts.json.
    Item {
        anchors { top: sub.bottom; left: parent.left; right: parent.right; bottom: parent.bottom; topMargin: 24 }

        Item {
            id: dev
            visible: root.view !== null
            width: root.view ? root.view.size[0] : 1
            height: root.view ? root.view.size[1] : 1
            anchors.centerIn: parent
            scale: root.view ? Math.min((parent.width - 80) / width, (parent.height - 50) / height) : 1

            Image {
                anchors.fill: parent
                source: root.view ? backend.imageUrl(root.view.image) : ""
                fillMode: Image.PreserveAspectFit
                smooth: true
            }
            Repeater {
                model: root.view ? root.view.keys : []
                Rectangle {
                    x: modelData.x
                    y: modelData.y
                    width: modelData.w
                    height: modelData.h
                    radius: 7
                    color: "#2644d62c"      // translucent green
                    border.color: "#44d62c"
                    border.width: 2
                }
            }
        }
    }
}
