"""A very small RFC 6455 server-side WebSocket implementation.

The daemon needs exactly three things from WebSockets: accept the handshake,
read binary frames (JPEG images) and answer pings. Pulling in a dependency for
that would make the plugin harder to install than it is to write.
"""

from __future__ import annotations

import base64
import hashlib
import os
import struct

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_FRAME = 8 * 1024 * 1024

OP_CONTINUATION = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA


class Closed(Exception):
    """The peer went away or sent a close frame."""


class ProtocolError(Exception):
    """The peer sent something that is not WebSocket."""


def accept_key(client_key: str) -> str:
    digest = hashlib.sha1((client_key + GUID).encode()).digest()
    return base64.b64encode(digest).decode()


def _unmask(data: bytes, mask: bytes) -> bytes:
    # xor in C-sized chunks instead of a per-byte Python loop; frames are
    # 100-300 KB JPEGs, so this matters.
    length = len(data)
    if length == 0:
        return b""
    key = (mask * (length // 4 + 1))[:length]
    return (int.from_bytes(data, "big") ^ int.from_bytes(key, "big")).to_bytes(length, "big")


class WebSocket:
    def __init__(self, sock):
        self.sock = sock
        self._buffer = b""
        self.closed = False

    # -- reading ----------------------------------------------------------

    def _read(self, count: int) -> bytes:
        while len(self._buffer) < count:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise Closed()
            self._buffer += chunk
        head, self._buffer = self._buffer[:count], self._buffer[count:]
        return head

    def receive(self):
        """Yield complete (opcode, payload) messages."""
        pending_opcode = None
        pending = bytearray()

        while True:
            first, second = self._read(2)
            fin = bool(first & 0x80)
            opcode = first & 0x0F
            masked = bool(second & 0x80)
            length = second & 0x7F

            if length == 126:
                (length,) = struct.unpack("!H", self._read(2))
            elif length == 127:
                (length,) = struct.unpack("!Q", self._read(8))

            if length > MAX_FRAME:
                raise ProtocolError(f"frame of {length} bytes is too large")

            mask = self._read(4) if masked else None
            payload = self._read(length)
            if mask:
                payload = _unmask(payload, mask)

            if opcode == OP_CLOSE:
                self.close()
                raise Closed()
            if opcode == OP_PING:
                self.send(payload, OP_PONG)
                continue
            if opcode == OP_PONG:
                continue

            if opcode == OP_CONTINUATION:
                if pending_opcode is None:
                    raise ProtocolError("continuation without a start frame")
                pending += payload
            else:
                pending_opcode = opcode
                pending = bytearray(payload)

            if fin:
                message = (pending_opcode, bytes(pending))
                pending_opcode = None
                pending = bytearray()
                yield message

    # -- writing ----------------------------------------------------------

    def send(self, payload: bytes, opcode: int = OP_BINARY) -> None:
        if self.closed:
            return
        length = len(payload)
        if length < 126:
            header = struct.pack("!BB", 0x80 | opcode, length)
        elif length < (1 << 16):
            header = struct.pack("!BBH", 0x80 | opcode, 126, length)
        else:
            header = struct.pack("!BBQ", 0x80 | opcode, 127, length)
        try:
            self.sock.sendall(header + payload)
        except OSError as exc:
            raise Closed() from exc

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.sock.sendall(struct.pack("!BB", 0x80 | OP_CLOSE, 0))
        except OSError:
            pass


def handshake_headers(key: str) -> dict:
    return {
        "Upgrade": "websocket",
        "Connection": "Upgrade",
        "Sec-WebSocket-Accept": accept_key(key),
    }


def random_mask() -> bytes:
    return os.urandom(4)
