"""Low-level big-endian chunk reader for BRender v1.x files.

Every multi-byte integer and float is big-endian on disk. Chunk headers are
`u32 id, u32 payload_len`. Container chunks (MODEL, MATERIAL, PIXELMAP, ACTOR)
are closed with FID_END (id=0, plen=0).
"""

from __future__ import annotations

import struct
from enum import IntEnum
from io import BytesIO
from pathlib import Path
from typing import BinaryIO


class FID(IntEnum):
    END = 0x00
    OLD_PIXELMAP = 0x03
    MATERIAL_OLDEST = 0x04
    OLD_MATERIAL_INDEX = 0x09
    OLD_VERTICES = 0x0A
    OLD_VERTICES_UV = 0x0B
    OLD_FACES = 0x0C
    OLD_MODEL = 0x0D
    FILE_INFO = 0x12
    PIVOT = 0x15
    MATERIAL_INDEX = 0x16
    VERTICES = 0x17
    VERTEX_UV = 0x18
    OLD_FACES_1 = 0x19
    FACE_MATERIAL = 0x1A
    OLD_MODEL_1 = 0x1B
    COLOUR_MAP_REF = 0x1C
    INDEX_BLEND_REF = 0x1E
    INDEX_SHADE_REF = 0x1F
    SCREENDOOR_REF = 0x20
    PIXELS = 0x21
    ADD_MAP = 0x22
    ACTOR = 0x23
    ACTOR_MODEL = 0x24
    ACTOR_TRANSFORM = 0x25
    ACTOR_MATERIAL = 0x26
    ACTOR_LIGHT = 0x27
    ACTOR_CAMERA = 0x28
    ACTOR_BOUNDS = 0x29
    ACTOR_ADD_CHILD = 0x2A
    TRANSFORM_MATRIX34 = 0x2B
    TRANSFORM_MATRIX34_LP = 0x2C
    TRANSFORM_QUAT = 0x2D
    TRANSFORM_EULER = 0x2E
    TRANSFORM_LOOK_UP = 0x2F
    TRANSFORM_TRANSLATION = 0x30
    TRANSFORM_IDENTITY = 0x31
    BOUNDS = 0x32
    FACES = 0x35
    OLD_MODEL_2 = 0x36
    INDEX_FOG_REF = 0x3B
    MATERIAL_OLD = 0x3C
    PIXELMAP = 0x3D
    MATERIAL = 0x3E
    MODEL = 0x40
    VERTEX_COLOUR = 0x41
    VERTEX_NORMAL = 0x42
    FACE_COLOUR = 0x43


class BEReader:
    """Big-endian binary reader over a bytes buffer or file."""

    __slots__ = ("_buf", "_pos", "_end")

    def __init__(self, data: bytes) -> None:
        self._buf = data
        self._pos = 0
        self._end = len(data)

    @classmethod
    def from_path(cls, path: str | Path) -> "BEReader":
        with open(path, "rb") as fh:
            return cls(fh.read())

    # ---- position management -------------------------------------------------
    @property
    def pos(self) -> int:
        return self._pos

    @property
    def end(self) -> int:
        return self._end

    def remaining(self) -> int:
        return self._end - self._pos

    def eof(self) -> bool:
        return self._pos >= self._end

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self._pos = offset
        elif whence == 1:
            self._pos += offset
        elif whence == 2:
            self._pos = self._end + offset
        self._pos = max(0, min(self._pos, self._end))
        return self._pos

    def read(self, n: int) -> bytes:
        if self._pos + n > self._end:
            raise EOFError(f"read {n} at {self._pos} exceeds end {self._end}")
        out = self._buf[self._pos : self._pos + n]
        self._pos += n
        return out

    # ---- scalar readers ------------------------------------------------------
    def read_u8(self) -> int:
        b = self.read(1)
        return b[0]

    def read_u16_be(self) -> int:
        return struct.unpack(">H", self.read(2))[0]

    def read_u32_be(self) -> int:
        return struct.unpack(">I", self.read(4))[0]

    def read_i32_be(self) -> int:
        return struct.unpack(">i", self.read(4))[0]

    def read_f32_be(self) -> int:
        return struct.unpack(">f", self.read(4))[0]

    def read_asciz(self, max_len: int | None = None) -> str:
        start = self._pos
        limit = self._end if max_len is None else min(self._end, start + max_len)
        i = start
        while i < limit and self._buf[i] != 0:
            i += 1
        data = self._buf[start:i]
        # advance past the zero byte if present
        self._pos = min(i + 1, limit)
        return data.decode("latin-1", errors="replace")

    # ---- chunk helpers -------------------------------------------------------
    def read_chunk_header(self) -> tuple[int, int]:
        """Read (chunk_id, payload_len). Raises EOFError if insufficient bytes."""
        if self._pos + 8 > self._end:
            raise EOFError("chunk header truncated")
        cid = struct.unpack(">I", self._buf[self._pos : self._pos + 4])[0]
        plen = struct.unpack(">I", self._buf[self._pos + 4 : self._pos + 8])[0]
        self._pos += 8
        return cid, plen

    def skip_chunk(self, payload_len: int) -> None:
        if self._pos + payload_len > self._end:
            raise EOFError(
                f"skip payload {payload_len} at {self._pos} exceeds end {self._end}"
            )
        self._pos += payload_len

    def peek_u32_be(self, rel_offset: int = 0) -> int | None:
        p = self._pos + rel_offset
        if p + 4 > self._end:
            return None
        return struct.unpack(">I", self._buf[p : p + 4])[0]

    def sub_reader(self, length: int) -> "BEReader":
        """Return a sub-reader over the next `length` bytes and advance past them."""
        data = self.read(length)
        return BEReader(data)


def iter_chunks(reader: BEReader):
    """Yield (chunk_id, payload_bytes) until the reader is exhausted or a malformed
    header is encountered. Caller is responsible for interpreting END markers.

    Robust against truncation — if a declared payload length overruns the end of
    the buffer, the iterator stops cleanly.
    """
    while not reader.eof():
        if reader.remaining() < 8:
            return
        try:
            cid, plen = reader.read_chunk_header()
        except EOFError:
            return
        if plen > reader.remaining():
            # Truncated — stop cleanly
            return
        try:
            payload = reader.read(plen)
        except EOFError:
            return
        yield cid, payload
