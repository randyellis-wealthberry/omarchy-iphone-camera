"""Best-effort Bluetooth presence detection.

Bluetooth cannot carry video (no iOS profile, ~1-2 Mbit/s, see README), but it
is excellent at answering "is the phone in the room?". The bar uses this for a
"nearby" hint; the stream itself always goes over Wi-Fi or USB.
"""

from __future__ import annotations

import re
import subprocess

MAC = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$", re.IGNORECASE)


def _run(*args: str) -> str:
    try:
        return subprocess.run(
            ["bluetoothctl", *args], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def resolve(address: str) -> str:
    """Accept either a MAC or a friendly name and return the MAC."""
    address = (address or "").strip()
    if not address:
        return ""
    if MAC.match(address):
        return address.upper()
    for line in _run("devices").splitlines():
        # "Device AA:BB:CC:DD:EE:FF Randy's iPhone"
        parts = line.split(maxsplit=2)
        if len(parts) == 3 and address.lower() in parts[2].lower():
            return parts[1].upper()
    return ""


def is_near(address: str) -> bool | None:
    """True/False when we can ask BlueZ, None when Bluetooth is unavailable."""
    mac = resolve(address)
    if not mac:
        return None
    info = _run("info", mac)
    if not info:
        return None
    return "Connected: yes" in info
