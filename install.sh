#!/usr/bin/env bash
# KEYZER setup — installs runtime dependencies, optional OpenRazer, and an
# optional application-menu entry. Safe to re-run.
set -euo pipefail
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
say() { printf '\n\033[1;32m==>\033[0m %s\n' "$*"; }

REQUIRED=(input-remapper x11-xserver-utils python3-evdev
          python3-pyside6.qtquick python3-pyside6.qtquickcontrols2
          python3-pyside6.qtsvg python3-pyside6.qtdbus)

if command -v apt >/dev/null 2>&1; then
  say "Installing required dependencies (input-remapper engine + PySide6 UI)"
  printf '   - %s\n' "${REQUIRED[@]}"
  sudo apt update
  sudo apt install -y "${REQUIRED[@]}"
else
  say "No apt detected — install these manually, then re-run:"
  echo "   - input-remapper    (https://github.com/sezanzeb/input-remapper)"
  echo "   - PySide6           (pip install --user PySide6)"
  echo "   - python-evdev      (pip install --user evdev)"
  echo "   - xmodmap           (your distro's x11-xserver-utils / xorg-xmodmap)"
  exit 1
fi

# Capturing key codes reads /dev/input, which requires the 'input' group.
say "Adding you to the 'input' group (needed to capture key codes)"
sudo gpasswd -a "$USER" input || true
echo "   (log out and back in for the group change to take effect)"

read -r -p $'\nInstall OpenRazer for Chroma lighting? (optional) [y/N] ' ans
if [[ "${ans:-N}" =~ ^[Yy] ]]; then
  say "Installing OpenRazer"
  sudo add-apt-repository -y ppa:openrazer/stable || true
  sudo apt update
  sudo apt install -y openrazer-meta python3-openrazer
  sudo gpasswd -a "$USER" plugdev || true
  echo "   (log out and back in for the plugdev group to take effect)"
fi

read -r -p $'\nEnable Naga Pro wheel-tilt key binding (background shim)? [Y/n] ' tilt
if [[ ! "${tilt:-Y}" =~ ^[Nn] ]]; then
  say "Installing the wheel-tilt shim (udev access rules + per-user service)"
  # Grant the logged-in user hidraw read + uinput write (no root daemon).
  sudo install -m 0644 "$HERE/packaging/99-keyzer-tilt.rules" \
                       /etc/udev/rules.d/99-keyzer-tilt.rules
  echo uinput | sudo tee /etc/modules-load.d/keyzer-uinput.conf >/dev/null
  sudo modprobe uinput || true
  sudo udevadm control --reload-rules && sudo udevadm trigger
  # Per-user systemd service running the shim from this checkout.
  mkdir -p "$HOME/.config/systemd/user"
  # Substitute the checkout path via argv/env (never interpolated into a shell
  # or regex), so a path with special characters can't corrupt the unit file.
  python3 - "$HERE/packaging/keyzer-tilt-shim.service" \
            "$HOME/.config/systemd/user/keyzer-tilt-shim.service" "$HERE" <<'PY'
import sys
src, dst, keyzer_dir = sys.argv[1], sys.argv[2], sys.argv[3]
with open(src) as f:
    unit = f.read().replace("@KEYZER_DIR@", keyzer_dir)
with open(dst, "w") as f:
    f.write(unit)
PY
  systemctl --user daemon-reload || true
  systemctl --user enable --now keyzer-tilt-shim.service || true
  echo "   Enabled. Re-plug the Naga once (or log out/in) so the new access applies,"
  echo "   then calibrate it in KEYZER and bind 'Wheel tilt L/R' like any other key."
fi

read -r -p $'\nAdd KEYZER to your application menu? [Y/n] ' menu
if [[ ! "${menu:-Y}" =~ ^[Nn] ]]; then
  say "Installing launcher, icon and desktop entry (per-user, no root)"
  mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications" \
           "$HOME/.local/share/icons/hicolor/scalable/apps"
  ln -sf "$HERE/keyzer" "$HOME/.local/bin/keyzer"
  cp "$HERE/packaging/keyzer.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/keyzer.svg"
  cat > "$HOME/.local/share/applications/keyzer.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=KEYZER
GenericName=Razer Key Remapper
Comment=Visual key remapping for Razer peripherals
Exec=$HERE/keyzer
Icon=keyzer
Terminal=false
Categories=Utility;Settings;HardwareSettings;
Keywords=razer;remap;keybind;keymap;tartarus;naga;
StartupWMClass=keyzer
EOF
  echo "   Installed. Ensure ~/.local/bin is on your PATH to run 'keyzer'."
fi

say "Done. Run KEYZER:  python3 app/main.py   (or 'keyzer' if you added the menu entry)"
