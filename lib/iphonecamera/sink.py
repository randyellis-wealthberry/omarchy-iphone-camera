"""Feeding a v4l2loopback device from JPEG frames.

The phone sends JPEG because it is what a browser can produce cheaply and what
survives Wi-Fi without a codec negotiation. ffmpeg does the decode and the
conversion into the YUV420P buffers a loopback device expects, so the rest of
the desktop sees an ordinary camera.
"""

from __future__ import annotations

import collections
import queue
import shutil
import subprocess
import threading
import time


class SinkError(RuntimeError):
    pass


class V4L2Sink:
    def __init__(self, device: str, width: int, height: int, fps: int = 30, on_error=None):
        self.device = device
        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)
        self.on_error = on_error

        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=2)
        self._process: subprocess.Popen | None = None
        self._writer: threading.Thread | None = None
        self._stopping = threading.Event()
        self._lock = threading.Lock()
        self._log: collections.deque[str] = collections.deque(maxlen=25)

        # Stats, read by the daemon's state loop.
        self.frames = 0
        self.bytes = 0
        self.last_frame = 0.0

    # -- lifecycle --------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> None:
        with self._lock:
            if self.running:
                return
            self._stopping.clear()
            self._queue = queue.Queue(maxsize=2)
            self._process = subprocess.Popen(
                self._command(),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
            self._writer = threading.Thread(
                target=self._drain, name="v4l2-writer", daemon=True
            )
            self._writer.start()
            threading.Thread(
                target=self._read_stderr, name="ffmpeg-stderr", daemon=True
            ).start()

    def stop(self) -> None:
        self._stopping.set()
        with self._lock:
            process, self._process = self._process, None
        if process is None:
            return
        try:
            if process.stdin:
                process.stdin.close()
        except OSError:
            pass
        try:
            process.terminate()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass

    def restart(self) -> None:
        self.stop()
        time.sleep(0.5)
        self.start()

    # -- data path --------------------------------------------------------

    def write(self, jpeg: bytes) -> None:
        """Queue one encoded frame, dropping the oldest if ffmpeg lags."""
        if not self.running:
            self.start()
        try:
            self._queue.put_nowait(jpeg)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(jpeg)
            except queue.Full:
                return
        self.frames += 1
        self.bytes += len(jpeg)
        self.last_frame = time.monotonic()

    def _drain(self) -> None:
        while not self._stopping.is_set():
            try:
                frame = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            process = self._process
            if process is None or process.stdin is None:
                continue
            try:
                process.stdin.write(frame)
                process.stdin.flush()
            except (BrokenPipeError, OSError):
                self._log.append("ffmpeg closed its input")
                if self.on_error:
                    self.on_error("ffmpeg stopped accepting frames")
                return

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for raw in process.stderr:
            line = raw.decode("utf-8", "replace").strip()
            if line:
                self._log.append(line)

    def error_text(self) -> str:
        return self._log[-1] if self._log else ""

    # -- command ----------------------------------------------------------

    def _command(self) -> list[str]:
        if not shutil.which("ffmpeg"):
            raise SinkError("ffmpeg is not installed (run setup)")

        width, height = _even(self.width), _even(self.height)
        filtergraph = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
            "format=yuv420p"
        )
        return [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            # image2pipe + a 32-byte probe makes ffmpeg open the v4l2 device on
            # the first frame. With "-f mjpeg" and default probing it buffers
            # for seconds (or until EOF), so the device never goes live and
            # meeting apps cannot see it.
            "-probesize", "32",
            "-analyzeduration", "0",
            "-f", "image2pipe",
            "-vcodec", "mjpeg",
            "-i", "pipe:0",
            "-vf", filtergraph,
            "-pix_fmt", "yuv420p",
            "-r", str(self.fps),
            "-f", "v4l2",
            self.device,
        ]


def _even(value: int) -> int:
    return value - (value % 2)
