from __future__ import annotations

import struct
import zlib
from datetime import datetime
from pathlib import Path


def _chunk(tag: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(tag + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)


def min_png() -> bytes:
    """1x1 opaque gray PNG that opens in ordinary viewers."""
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw = b"\x00\x80\x80\x80"
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )


MIN_PNG = min_png()


class OutputStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def make_run_dir(self) -> Path:
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        run_dir = self.root / stamp
        suffix = 1
        while run_dir.exists():
            run_dir = self.root / f"{stamp}-{suffix}"
            suffix += 1
        run_dir.mkdir(parents=True)
        return run_dir
