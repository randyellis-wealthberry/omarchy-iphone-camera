#!/bin/bash
# Remove iPhone Camera. Keeps settings and the virtual camera unless --purge.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
plugin_id="iphone-camera"
purge=0
[[ "${1:-}" == "--purge" ]] && purge=1

echo "==> Stopping the service"
systemctl --user disable --now iphone-camera.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/iphone-camera.service"
systemctl --user daemon-reload

echo "==> Command"
[[ -L "$HOME/.local/bin/iphone-camera" ]] && rm -f "$HOME/.local/bin/iphone-camera"

echo "==> Bar widget"
link="$HOME/.config/omarchy/plugins/$plugin_id"
if [[ -L "$link" ]]; then
  rm -f "$link"
elif [[ -d "$link" && "$(realpath "$link")" == "$(realpath "$here")" ]]; then
  rm -rf "$link"
fi
command -v omarchy-shell >/dev/null && omarchy-shell shell rescanPlugins >/dev/null 2>&1 || true

if ((purge)); then
  echo "==> Settings and state"
  rm -rf "$HOME/.config/iphone-camera" "$HOME/.local/state/iphone-camera"
  echo "==> Loopback configuration (needs root)"
  if [[ -t 0 ]]; then
    sudo rm -f /etc/modprobe.d/zz-iphone-camera.conf /etc/modules-load.d/iphone-camera.conf
  else
    pkexec rm -f /etc/modprobe.d/zz-iphone-camera.conf /etc/modules-load.d/iphone-camera.conf
  fi
  echo "    A reboot removes /dev/video43."
fi

echo "Removed."
