"""HTTPS + WebSocket server the iPhone connects to."""

from __future__ import annotations

import json
import ssl
import threading
import time
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import config, ws

WEB_ROOT = Path(__file__).resolve().parent.parent.parent / "share" / "web"
INDEX = WEB_ROOT / "index.html"


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.frames = 0
        self.bytes = 0
        self.clients = 0
        self.last_frame = 0.0
        self.phone: dict = {}
        self._mark = (0, 0)

    def add_frame(self, size: int) -> None:
        with self.lock:
            self.frames += 1
            self.bytes += size
            self.last_frame = time.monotonic()

    def client(self, delta: int) -> None:
        with self.lock:
            self.clients = max(0, self.clients + delta)

    def set_phone(self, info: dict) -> None:
        with self.lock:
            self.phone = dict(info)

    def snapshot(self) -> dict:
        with self.lock:
            frames, total = self.frames, self.bytes
            previous_frames, previous_bytes = self._mark
            self._mark = (frames, total)
            return {
                "frames": frames - previous_frames,
                "bytes": total - previous_bytes,
                "clients": self.clients,
                "lastFrame": self.last_frame,
                "phone": dict(self.phone),
            }


class CameraServer:
    def __init__(self, cfg: dict, sink, token: str = ""):
        self.cfg = cfg
        self.sink = sink
        self.token = token
        self.stats = Stats()
        self.httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.error = ""

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        handler = partial(_Handler, app=self)
        self.httpd = _Server(("0.0.0.0", int(self.cfg["port"])), handler)
        self.httpd.app = self
        self.httpd.daemon_threads = True
        if self.cfg.get("tls", True):
            cert, key = config.ensure_cert(self.cfg)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(certfile=str(cert), keyfile=str(key))
            self.httpd.socket = context.wrap_socket(self.httpd.socket, server_side=True)
        self._thread = threading.Thread(
            target=self.httpd.serve_forever, name="http", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    @property
    def port(self) -> int:
        return self.httpd.server_address[1] if self.httpd else int(self.cfg["port"])

    # -- request handling -------------------------------------------------

    def authorized(self, query: dict) -> bool:
        if not self.token:
            return True
        return self.token in (query.get("t") or [""])

    def on_frame(self, payload: bytes) -> None:
        if len(payload) < 4 or payload[:2] != b"\xff\xd8":
            return
        self.stats.add_frame(len(payload))
        try:
            self.sink.write(payload)
        except Exception as exc:  # noqa: BLE001 - surfaced through status
            self.error = str(exc)

    def on_control(self, payload: bytes) -> None:
        try:
            message = json.loads(payload)
        except ValueError:
            return
        if isinstance(message, dict) and message.get("type") == "hello":
            self.stats.set_phone(message)


class _Server(ThreadingHTTPServer):
    app: CameraServer
    allow_reuse_address = True
    daemon_threads = True

    def handle_error(self, request, client_address):  # noqa: D401 - quiet on purpose
        """A phone that walks out of Wi-Fi range is normal; stay silent."""
        pass


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "iphone-camera"

    def __init__(self, *args, app: CameraServer, **kwargs):
        self.app = app
        super().__init__(*args, **kwargs)

    def log_message(self, fmt, *args):  # noqa: D401 - keep the journal clean
        pass

    # -- routes -----------------------------------------------------------

    def do_GET(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/health":
            return self._json({"ok": True})

        if path == "/ws":
            if not self.app.authorized(query):
                return self._text(403, "invalid link")
            return self._websocket()

        if path in ("/", "/index.html"):
            return self._page()

        if path == "/config.json":
            if not self.app.authorized(query):
                return self._text(403, "invalid link")
            cfg = self.app.cfg
            return self._json(
                {
                    "width": cfg["width"],
                    "height": cfg["height"],
                    "fps": cfg["fps"],
                    "jpegQuality": cfg["jpegQuality"],
                }
            )

        return self._text(404, "not found")

    # -- responses --------------------------------------------------------

    def _page(self) -> None:
        try:
            body = INDEX.read_bytes()
        except OSError:
            return self._text(500, "index.html missing from the plugin")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _text(self, status: int, text: str) -> None:
        body = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- websocket --------------------------------------------------------

    def _websocket(self) -> None:
        key = self.headers.get("Sec-WebSocket-Key")
        if not key or "websocket" not in (self.headers.get("Upgrade") or "").lower():
            return self._text(400, "expected a websocket upgrade")

        self.send_response(101, "Switching Protocols")
        for name, value in ws.handshake_headers(key).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.flush()

        self.close_connection = True
        connection = ws.WebSocket(self.connection)
        self.app.stats.client(+1)
        print(
            f"iphone-camera: phone connected from {self.client_address[0]}",
            flush=True,
        )
        try:
            for opcode, payload in connection.receive():
                if opcode == ws.OP_BINARY:
                    self.app.on_frame(payload)
                elif opcode == ws.OP_TEXT:
                    self.app.on_control(payload)
        except (ws.Closed, ws.ProtocolError, OSError):
            pass
        finally:
            self.app.stats.client(-1)
            connection.close()
