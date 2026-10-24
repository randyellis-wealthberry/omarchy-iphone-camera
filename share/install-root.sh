#!/bin/bash
# The privileged half of install.sh: make sure v4l2loopback offers a device
# labelled "iPhone Camera", without taking anything away from other plugins
# (camera-effects, OBS virtual camera, ...).
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"

echo "==> Packages"
pacman -S --needed --noconfirm v4l2loopback-dkms v4l-utils ffmpeg qrencode

echo "==> Merging v4l2loopback device configuration"
python3 - "$here/../lib" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from iphonecamera.modprobe import merge_and_write

line = merge_and_write(Path("/etc/modprobe.d"), Path("/etc/modprobe.d/zz-iphone-camera.conf"))
print("    " + line)
PY

mkdir -p /etc/modules-load.d
echo v4l2loopback > /etc/modules-load.d/iphone-camera.conf

if lsmod | grep -q '^v4l2loopback '; then
  echo "==> Reloading v4l2loopback"
  if modprobe -r v4l2loopback 2>/dev/null; then
    modprobe v4l2loopback
  else
    echo "    module is in use right now; reboot (or close camera apps) to add /dev/video43"
  fi
else
  modprobe v4l2loopback
fi

if [[ -e /dev/video43 ]]; then
  echo "==> /dev/video43 is ready"
else
  echo "==> /dev/video43 not present yet; a reboot will create it"
fi
