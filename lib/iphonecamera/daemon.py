"""The long-running bridge: HTTPS server, V4L2 sink, state file."""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
from pathlib import Path

from . import config, proximity, server, sink

ROOT = Path(__file__).resolve().parents[2]
PLACEHOLDER = ROOT / "share" / "placeholder.jpg"


def _install_signals(stop: threading.Event) -> None:
    def handler(signum, _frame):
        stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, handler)
        except ValueError:  # not the main thread (tests)
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="iphone-camera daemon")
    parser.add_argument("--device", help="v4l2loopback node to write to")
    parser.add_argument("--port", type=int, help="HTTPS port")
    args = parser.parse_args(argv)

    cfg = config.load()
    if args.port:
        cfg["port"] = args.port

    token = config.ensure_token(cfg)
    device = args.device or config.pick_device(cfg)
    if not device:
        message = (
            "no free v4l2loopback device; run `iphone-camera setup` to create "
            "/dev/video43"
        )
        config.write_state({"state": "error", "error": message})
        print(f"iphone-camera: {message}", file=sys.stderr)
        return 2

    pipe = sink.V4L2Sink(device, cfg["width"], cfg["height"], cfg["fps"])
    bridge = server.CameraServer(cfg, pipe, token=token)
    try:
        bridge.start()
    except OSError as exc:
        message = f"cannot listen on port {cfg['port']}: {exc}"
        config.write_state({"state": "error", "error": message})
        print(f"iphone-camera: {message}", file=sys.stderr)
        return 2

    placeholder = PLACEHOLDER.read_bytes() if PLACEHOLDER.exists() else b""
    keepalive = threading.Event()

    def idle_frame() -> bytes | None:
        """A still frame so the loopback device always has a live producer.

        v4l2loopback's exclusive_caps mode only announces CAPTURE (i.e. looks
        like a webcam in apps) while a producer is attached. ffmpeg also needs
        a steady trickle of frames before it opens the device at all, so a
        still is re-sent whenever the phone is not sending anything.
        """
        return placeholder or None

    def prime() -> None:
        if not Path(device).exists():
            return
        try:
            if not pipe.running:
                pipe.start()
        except Exception as exc:  # noqa: BLE001 - retried by the loop
            print(f"iphone-camera: cannot start the sink: {exc}", file=sys.stderr)
            return
        frame = idle_frame()
        if frame:
            pipe.write(frame)

    def keepalive_loop() -> None:
        while not keepalive.is_set():
            if Path(device).exists():
                if not pipe.running:
                    prime()
                elif placeholder and time.monotonic() - bridge.stats.last_frame > 0.4:
                    pipe.write(placeholder)
            keepalive.wait(0.2)

    prime()
    threading.Thread(target=keepalive_loop, name="keepalive", daemon=True).start()

    stop = threading.Event()
    _install_signals(stop)
    started = time.time()
    nearby: bool | None = None

    print(f"iphone-camera: {device} <- https://{config.host(cfg)}:{bridge.port}/", flush=True)

    try:
        while not stop.is_set():
            snapshot = bridge.stats.snapshot()
            fps, kbps = snapshot["frames"], round(snapshot["bytes"] * 8 / 1000, 1)
            clients = snapshot["clients"]

            if cfg.get("bluetoothDevice"):
                nearby = proximity.is_near(str(cfg["bluetoothDevice"]))

            if clients and fps:
                state = "active"
            elif clients and pipe.error_text() and not pipe.running:
                state = "error"
            elif clients:
                state = "connected"
            else:
                state = "waiting"

            config.write_state(
                {
                    "state": state,
                    "device": device,
                    "url": config.url(cfg),
                    "fps": fps,
                    "kbps": kbps,
                    "clients": clients,
                    "near": nearby,
                    "phone": snapshot["phone"],
                    "error": pipe.error_text() or bridge.error,
                    "started": int(started),
                }
            )
            stop.wait(1.0)
    finally:
        keepalive.set()
        bridge.stop()
        pipe.stop()
        config.write_state({"state": "stopped", "device": device})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
