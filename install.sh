#!/bin/bash
# Install iPhone Camera: loopback device, CLI, user service and bar widget.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
plugin_id="iphone-camera"
device="${IPHONE_CAMERA_DEVICE:-/dev/video43}"

# The daemon port, so the firewall rule matches a customised setting.
settings="${XDG_CONFIG_HOME:-$HOME/.config}/iphone-camera/settings.json"
IPHONE_CAMERA_PORT="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["port"])' "$settings" 2>/dev/null || echo 4711)"
export IPHONE_CAMERA_PORT

if [[ ! -e "$device" ]]; then
  echo "==> Creating the virtual camera ($device, needs root)"
  if [[ -t 0 ]]; then
    sudo bash "$here/share/install-root.sh"
  else
    pkexec bash "$here/share/install-root.sh"
  fi
fi

if ! command -v ffmpeg >/dev/null; then
  echo "!! ffmpeg is missing; run: sudo pacman -S ffmpeg" >&2
fi

echo "==> Command and service"
mkdir -p "$HOME/.local/bin" "$HOME/.config/systemd/user"
chmod +x "$here/bin/iphone-camera" "$here/share/install-root.sh" "$here/uninstall.sh"
ln -sfn "$here/bin/iphone-camera" "$HOME/.local/bin/iphone-camera"
install -m644 "$here/share/iphone-camera.service" "$HOME/.config/systemd/user/iphone-camera.service"
systemctl --user daemon-reload
systemctl --user enable iphone-camera.service
systemctl --user restart iphone-camera.service

echo "==> Bar widget"
plugins="$HOME/.config/omarchy/plugins"
dest="$plugins/$plugin_id"
mkdir -p "$plugins"
# Quickshell refuses to load a plugin whose module path is outside the config
# folder, so a symlink to ~/Projects does not work. Install a copy instead and
# record where it came from, for uninstall.
copied=0
if [[ -L "$dest" ]]; then
  # A symlink resolves to the same path as $here, so test for it explicitly.
  rm -f "$dest"
fi
if [[ "$(realpath "$here")" != "$(realpath -m "$dest")" ]]; then
  mkdir -p "$dest"
  if command -v rsync >/dev/null; then
    rsync -a --delete \
      --exclude '.git' --exclude 'tests' --exclude '__pycache__' \
      "$here/" "$dest/"
  else
    rm -rf "$dest"
    mkdir -p "$dest"
    cp -a "$here/manifest.json" "$here/Panel.qml" "$here/bin" "$here/lib" "$here/share" "$dest/"
  fi
  printf '%s\n' "$here" > "$dest/.installed-from"
  copied=1
fi

# The command and service should use the installed copy, not the checkout, so
# the bar and the daemon never run different code.
if (( copied )); then
  ln -sfn "$dest/bin/iphone-camera" "$HOME/.local/bin/iphone-camera"
  install -m644 "$dest/share/iphone-camera.service" "$HOME/.config/systemd/user/iphone-camera.service"
  systemctl --user daemon-reload
  systemctl --user restart iphone-camera.service
fi

if command -v omarchy >/dev/null; then
  omarchy-shell shell rescanPlugins >/dev/null 2>&1 || true
  omarchy plugin enable "$plugin_id" >/dev/null 2>&1 || true
fi

echo
"$HOME/.local/bin/iphone-camera" status || true
echo
echo "Done. The bar shows a camera icon: click it, scan the QR code, tap Start."
echo "Pick \"$device\" in Zoom / Meet / Teams / Discord / OBS."
