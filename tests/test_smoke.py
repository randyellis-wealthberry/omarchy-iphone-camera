"""Smoke tests: no camera, no phone, no root required.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import base64
import http.client
import os
import socket
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from iphonecamera import config, modprobe, server, sink, ws  # noqa: E402

# Smallest possible valid JPEG (1x1, grey).
JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
    "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAA"
    "AAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q=="
)


class FakeSink:
    def __init__(self):
        self.frames: list[bytes] = []
        self.running = False
        self.last_frame = 0.0

    def write(self, jpeg: bytes) -> None:
        self.frames.append(jpeg)
        self.last_frame = time.monotonic()

    def stop(self) -> None:
        self.running = False


def start_bridge(token: str = "") -> server.CameraServer:
    cfg = dict(config.DEFAULTS)
    cfg.update({"port": 0, "tls": False})
    bridge = server.CameraServer(cfg, FakeSink(), token=token)
    bridge.start()
    return bridge


def ws_upgrade(port: int, path: str = "/ws"):
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    key = base64.b64encode(os.urandom(16)).decode()
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.sendall(request.encode())
    raw = b""
    while b"\r\n\r\n" not in raw:
        chunk = sock.recv(4096)
        if not chunk:
            break
        raw += chunk
    head, _, tail = raw.partition(b"\r\n\r\n")
    return sock, head.decode("latin-1"), tail


def send_client_frame(sock: socket.socket, payload: bytes, opcode: int = ws.OP_BINARY) -> None:
    mask = os.urandom(4)
    masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
    length = len(payload)
    if length < 126:
        header = struct.pack("!BB", 0x80 | opcode, 0x80 | length)
    elif length < (1 << 16):
        header = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, length)
    else:
        header = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, length)
    sock.sendall(header + mask + masked)


class WebSocketTests(unittest.TestCase):
    def test_accept_key_matches_rfc6455(self):
        self.assertEqual(
            ws.accept_key("dGhlIHNhbXBsZSBub25jZQ=="),
            "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=",
        )

    def test_unmask_round_trip(self):
        mask = b"\x01\x02\x03\x04"
        data = bytes(range(64))
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(data))
        self.assertEqual(ws._unmask(masked, mask), data)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.bridge = start_bridge()
        self.addCleanup(self.bridge.stop)

    def test_health_and_page(self):
        port = self.bridge.port
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("GET", "/health")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.read(), b'{"ok": true}')

        connection.request("GET", "/")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        body = response.read()
        self.assertIn(b"iPhone Camera", body)
        self.assertIn(b"getUserMedia", body)

        connection.request("GET", "/config.json")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertIn(b"jpegQuality", response.read())
        connection.close()

    def test_frame_reaches_the_sink(self):
        sock, head, _ = ws_upgrade(self.bridge.port)
        self.assertIn("101", head)
        self.assertIn("Sec-WebSocket-Accept", head)

        send_client_frame(sock, JPEG)
        deadline = time.time() + 3
        while not self.bridge.sink.frames and time.time() < deadline:
            time.sleep(0.02)

        self.assertEqual(self.bridge.sink.frames, [JPEG])
        sock.close()

    def test_control_message_is_recorded(self):
        sock, head, _ = ws_upgrade(self.bridge.port)
        send_client_frame(sock, b'{"type":"hello","width":1280,"height":720}', ws.OP_TEXT)

        deadline = time.time() + 3
        while not self.bridge.stats.snapshot()["phone"] and time.time() < deadline:
            time.sleep(0.02)

        self.assertEqual(self.bridge.stats.snapshot()["phone"]["width"], 1280)
        sock.close()

    def test_garbage_is_ignored(self):
        sock, _, _ = ws_upgrade(self.bridge.port)
        send_client_frame(sock, b"not a jpeg")
        time.sleep(0.2)
        self.assertEqual(self.bridge.sink.frames, [])
        sock.close()


class TokenTests(unittest.TestCase):
    def setUp(self):
        self.bridge = start_bridge(token="secret")
        self.addCleanup(self.bridge.stop)

    def test_wrong_token_is_rejected(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.bridge.port, timeout=5)
        connection.request("GET", "/ws?t=nope")
        self.assertEqual(connection.getresponse().status, 403)
        connection.close()

    def test_right_token_is_accepted(self):
        sock, head, _ = ws_upgrade(self.bridge.port, "/ws?t=secret")
        self.assertIn("101", head)
        sock.close()


class ConfigTests(unittest.TestCase):
    def test_url_carries_the_token(self):
        url = config.url({"port": 4711, "advertiseHost": "192.0.2.10"}, token="abc123")
        self.assertEqual(url, "https://192.0.2.10:4711/?t=abc123")

    def test_resolutions_are_even(self):
        for width, height in config.RESOLUTIONS.values():
            self.assertEqual(width % 2, 0)
            self.assertEqual(height % 2, 0)

    def test_defaults_have_types(self):
        self.assertTrue(config.TYPES)
        self.assertIs(config.TYPES["fps"], int)


class ModprobeTests(unittest.TestCase):
    def test_merges_with_an_existing_plugin(self):
        with tempfile.TemporaryDirectory() as tmp:
            confdir = Path(tmp)
            (confdir / "camera-effects.conf").write_text(
                "# Camera Effects\n"
                'options v4l2loopback devices=1 video_nr=42 '
                'card_label="FaceTime HD Camera (Effects)" exclusive_caps=1 max_buffers=2\n'
            )
            out = confdir / "zz-iphone-camera.conf"
            line = modprobe.merge_and_write(confdir, out)

            self.assertIn("devices=2", line)
            self.assertIn("video_nr=42,43", line)
            self.assertIn('"FaceTime HD Camera (Effects)"', line)
            self.assertIn('"iPhone Camera"', line)
            self.assertIn("exclusive_caps=1,1", line)
            self.assertTrue(out.read_text().startswith("# Written by omarchy-iphone-camera"))

    def test_fresh_machine_gets_one_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            confdir = Path(tmp)
            line = modprobe.merge_and_write(confdir, confdir / "zz-iphone-camera.conf")
            self.assertIn("devices=1", line)
            self.assertIn("video_nr=43", line)
            self.assertIn("card_label=\"iPhone Camera\"", line)

    def test_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            confdir = Path(tmp)
            out = confdir / "zz-iphone-camera.conf"
            first = modprobe.merge_and_write(confdir, out)
            second = modprobe.merge_and_write(confdir, out)
            self.assertEqual(first, second)
            self.assertIn("devices=1", second)


class SinkTests(unittest.TestCase):
    def test_command_targets_the_device(self):
        pipe = sink.V4L2Sink("/dev/video43", 1280, 720, 30)
        command = pipe._command()
        self.assertIn("/dev/video43", command)
        self.assertIn("yuv420p", command)
        self.assertIn("mjpeg", command)
        self.assertIn("scale=1280:720", " ".join(command))

    def test_odd_sizes_are_rounded_down(self):
        pipe = sink.V4L2Sink("/dev/video43", 1281, 721, 30)
        self.assertIn("scale=1280:720", " ".join(pipe._command()))


if __name__ == "__main__":
    unittest.main()
