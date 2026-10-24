"""Paths, settings, certificates and device discovery for iphone-camera.

Everything here is stdlib only: the daemon must run on a stock Omarchy
install without a venv.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

APP = "iphone-camera"
UNIT = "iphone-camera.service"
LABEL = "iPhone Camera"
DEFAULT_PORT = 4711
DEFAULT_DEVICE = "/dev/video43"

_StateHome = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
_ConfigHome = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))

STATE_DIR = _StateHome / APP
CONFIG_DIR = _ConfigHome / APP
SETTINGS = CONFIG_DIR / "settings.json"
STATE = STATE_DIR / "state.json"
CERT = STATE_DIR / "cert.pem"
KEY = STATE_DIR / "key.pem"
CERT_HOST = STATE_DIR / "cert.host"

DEFAULTS = {
    "port": DEFAULT_PORT,
    "width": 1280,
    "height": 720,
    "fps": 30,
    "jpegQuality": 0.75,
    # Empty means "find a free v4l2loopback device labelled iPhone Camera".
    "device": "",
    # Only used to show "phone nearby" in the bar. Video never travels over
    # Bluetooth; see README.
    "bluetoothAutoStart": False,
    "bluetoothDevice": "",
    "advertiseHost": "",
    # Random suffix in the phone URL, so a neighbour on the same Wi-Fi cannot
    # push video into your webcam just by guessing the port.
    "token": "",
}

TYPES = {key: type(value) for key, value in DEFAULTS.items()}

RESOLUTIONS = {
    "540p": (960, 540),
    "720p": (1280, 720),
    "1080p": (1920, 1080),
}


def ensure_dirs() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


def load() -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(SETTINGS.read_text()))
    except (OSError, ValueError):
        pass
    cfg["port"] = int(cfg["port"])
    cfg["width"] = int(cfg["width"])
    cfg["height"] = int(cfg["height"])
    cfg["fps"] = max(1, min(60, int(cfg["fps"])))
    cfg["jpegQuality"] = max(0.3, min(0.95, float(cfg["jpegQuality"])))
    return cfg


def save(cfg: dict) -> dict:
    ensure_dirs()
    merged = dict(DEFAULTS)
    merged.update(cfg)
    _atomic_write(SETTINGS, json.dumps(merged, indent=2, sort_keys=True) + "\n")
    return merged


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# --------------------------------------------------------------------------
# Daemon state (read by the bar widget and `iphone-camera status`)
# --------------------------------------------------------------------------


def read_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {"state": "stopped"}


def write_state(state: dict) -> None:
    ensure_dirs()
    _atomic_write(STATE, json.dumps(state, sort_keys=True) + "\n")


# --------------------------------------------------------------------------
# Network identity
# --------------------------------------------------------------------------


def lan_ip() -> str:
    """Best guess at the address the phone should dial."""
    try:
        out = subprocess.run(
            ["ip", "-4", "-o", "route", "get", "1.1.1.1"],
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout
        match = re.search(r"\bsrc\s+(\d+\.\d+\.\d+\.\d+)", out)
        if match:
            return match.group(1)
    except (OSError, subprocess.SubprocessError):
        pass
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("1.1.1.1", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def host(cfg: dict | None = None) -> str:
    cfg = cfg or load()
    configured = str(cfg.get("advertiseHost") or "").strip()
    return configured or lan_ip()


def ensure_token(cfg: dict | None = None) -> str:
    """Create (once) the short secret that scopes the phone URL."""
    import secrets

    cfg = cfg or load()
    token = str(cfg.get("token") or "").strip()
    if not token:
        token = secrets.token_urlsafe(6)
        cfg["token"] = token
        save(cfg)
    return token


def url(cfg: dict | None = None, token: str | None = None) -> str:
    cfg = cfg or load()
    token = ensure_token(cfg) if token is None else token
    suffix = f"?t={token}" if token else ""
    return f"https://{host(cfg)}:{cfg['port']}/{suffix}"


# --------------------------------------------------------------------------
# TLS
# --------------------------------------------------------------------------


def ensure_cert(cfg: dict | None = None) -> tuple[Path, Path]:
    """Self-signed certificate with a SAN for the current LAN address.

    iOS only allows getUserMedia on a secure origin. A self-signed cert that
    the user accepts once counts as secure, but it must match the address the
    phone dials, otherwise Safari refuses before it can even show the warning.
    """
    ensure_dirs()
    ip = host(cfg)
    if CERT.exists() and KEY.exists():
        try:
            if CERT_HOST.read_text().strip() == ip:
                return CERT, KEY
        except OSError:
            pass

    if not shutil.which("openssl"):
        raise RuntimeError("openssl is required to create the TLS certificate")

    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256",
            "-days", "825", "-nodes",
            "-keyout", str(KEY),
            "-out", str(CERT),
            "-subj", "/CN=iphone-camera.local",
            "-addext", f"subjectAltName=DNS:localhost,IP:127.0.0.1,IP:{ip}",
            "-addext", "basicConstraints=critical,CA:FALSE",
            "-addext", "keyUsage=critical,digitalSignature,keyEncipherment",
            "-addext", "extendedKeyUsage=serverAuth",
        ],
        check=True,
        capture_output=True,
    )
    os.chmod(KEY, 0o600)
    CERT_HOST.write_text(ip + "\n")
    return CERT, KEY


# --------------------------------------------------------------------------
# V4L2 loopback discovery
# --------------------------------------------------------------------------


def video_nodes() -> list[dict]:
    """Every /dev/video* node with its driver name and whether it is virtual."""
    nodes = []
    for sysfs in sorted(Path("/sys/class/video4linux").glob("video*")):
        try:
            name = (sysfs / "name").read_text().strip()
        except OSError:
            continue
        virtual = os.path.realpath(sysfs).startswith("/sys/devices/virtual/")
        nodes.append(
            {
                "device": f"/dev/{sysfs.name}",
                "name": name,
                "virtual": virtual,
            }
        )
    return nodes


def loopback_devices() -> list[dict]:
    return [
        node for node in video_nodes()
        if node["virtual"] and is_loopback(_sysfs(node["device"]))
    ]


def _sysfs(device: str) -> Path:
    return Path("/sys/class/video4linux") / Path(device).name


def is_loopback(sysfs: Path) -> bool:
    """Identify v4l2loopback without opening the node.

    Opening a loopback device matters: in exclusive_caps mode the first opener
    makes it announce either OUTPUT or CAPTURE, and a nosy `v4l2-ctl --info`
    would latch it to the wrong side. `max_openers` is a v4l2loopback-only
    sysfs attribute, so this stays hands-off.
    """
    return (sysfs / "max_openers").exists()


def pick_device(cfg: dict | None = None) -> str | None:
    """Choose the loopback device this daemon may write to.

    Never borrow the camera-effects device: two writers on one loopback node
    produce a torn picture, so an unlabelled virtual device is only used when
    the user explicitly configured it.
    """
    cfg = cfg or load()
    configured = str(cfg.get("device") or "").strip()
    if configured:
        return configured if Path(configured).exists() else None

    for node in loopback_devices():
        if _label(node["name"]) == LABEL:
            return node["device"]

    # Nothing labelled yet: take a virtual device that is clearly not in use by
    # camera-effects, and only if it is not the only virtual node around.
    camera_effects = [n for n in loopback_devices() if "FaceTime" in n["name"]]
    free = [n for n in loopback_devices() if n not in camera_effects]
    if free:
        return free[0]["device"]
    return None


def _label(name: str) -> str:
    """Normalise a card label.

    A module that was loaded before a config change can carry stray quotes in
    its label ("iPhone Camera). Matching should survive that until a reboot
    reloads v4l2loopback with the merged options.
    """
    return name.strip().strip('"').strip()
