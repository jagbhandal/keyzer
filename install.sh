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
