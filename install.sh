#!/bin/bash
# Install iPhone Camera: loopback device, CLI, user service and bar widget.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
plugin_id="iphone-camera"
device="${IPHONE_CAMERA_DEVICE:-/dev/video43}"

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
mkdir -p "$plugins"
# Installed with `omarchy plugin add`, the checkout already is the plugin.
if [[ "$(realpath "$here")" != "$(realpath -m "$plugins/$plugin_id")" ]]; then
  [[ -e "$plugins/$plugin_id" ]] || ln -s "$here" "$plugins/$plugin_id"
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
