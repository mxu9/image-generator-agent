from __future__ import annotations

import sys
import threading
import time
from typing import TextIO

CIRCLE_FRAMES = "◐◓◑◒"
ASCII_FRAMES = "|/-\\"
DEFAULT_INTERVAL = 0.1


class Spinner:
    """TTY-only wait indicator. Writes to stderr and erases itself on exit."""

    def __init__(
        self,
        label: str,
        stream: TextIO | None = None,
        interval: float = DEFAULT_INTERVAL,
    ) -> None:
        self.label = label
        self.stream = sys.stderr if stream is None else stream
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._frames = ASCII_FRAMES
        self._width = 0

    def __enter__(self) -> Spinner:
        if not _is_tty(self.stream):
            return self
        self._frames = _frames_for(self.stream)
        self._stop.clear()
        self._width = 0
        self._thread = threading.Thread(target=self._run, name="spinner", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=1)
        self._clear()

    def _run(self) -> None:
        started = time.monotonic()
        index = 0
        while not self._stop.is_set():
            elapsed = int(time.monotonic() - started)
            frame = self._frames[index % len(self._frames)]
            self._write(f"\r{frame} {self.label} {elapsed}s")
            index += 1
            if self._stop.wait(self.interval):
                return

    def _write(self, text: str) -> None:
        try:
            self.stream.write(text)
            self.stream.flush()
        except (OSError, UnicodeError):
            return
        self._width = max(self._width, _columns(text))

    def _clear(self) -> None:
        if self._width <= 0:
            return
        blank = "\r" + (" " * self._width) + "\r"
        try:
            self.stream.write(blank)
            self.stream.flush()
        except OSError:
            return
        self._width = 0


def _is_tty(stream: TextIO) -> bool:
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty):
        return False
    try:
        return bool(isatty())
    except Exception:
        return False


def _frames_for(stream: TextIO) -> str:
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        CIRCLE_FRAMES.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return ASCII_FRAMES
    return CIRCLE_FRAMES


def _columns(text: str) -> int:
    width = 0
    for char in text:
        if char == "\r":
            continue
        width += 2 if ord(char) > 127 else 1
    return width
