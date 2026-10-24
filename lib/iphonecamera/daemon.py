"""The long-running bridge: HTTPS server, V4L2 sink, state file."""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time

from . import config, proximity, server, sink

IDLE_STOP_SECONDS = 15


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
            idle_for = time.monotonic() - pipe.last_frame if pipe.last_frame else 1e9

            # Release the loopback node when nobody is watching, so a stale
            # last frame does not sit frozen in every camera picker.
            if not clients and pipe.running and idle_for > IDLE_STOP_SECONDS:
                pipe.stop()

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
        bridge.stop()
        pipe.stop()
        config.write_state({"state": "stopped", "device": device})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
