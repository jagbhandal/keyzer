#!/usr/bin/env python3
"""Build the self-contained KEYZER prototype.

Inlines the device images as base64 data-URIs into the HTML template,
producing a single openable file at dist/keyzer.html.

    python3 prototype/build.py
"""
import base64
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "prototype" / "keyzer.template.html"
OUTPUT = ROOT / "dist" / "keyzer.html"

ASSETS = {
    "__IMG_TARTARUS__":  ROOT / "prototype" / "assets" / "tartarus-top.png",
    "__IMG_NAGA_TOP__":  ROOT / "prototype" / "assets" / "naga-top.png",
    "__IMG_NAGA_SIDE__": ROOT / "prototype" / "assets" / "naga-side.png",
}


def data_uri(path: pathlib.Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def main() -> None:
    html = TEMPLATE.read_text()
    for token, path in ASSETS.items():
        html = html.replace(token, data_uri(path))
    if "__IMG_" in html:
        raise SystemExit("error: unreplaced image placeholder remains")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(html)
    print(f"wrote {OUTPUT.relative_to(ROOT)} ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
