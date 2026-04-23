"""BRender v1.x pixelmap (.pix / .pal) parser and decoder.

Handles modern PIXELMAP (0x3D) and legacy OLD_PIXELMAP (0x03), the PIXELS
(0x21) data chunk with per-element big-endian swapping, and the ADD_MAP
(0x22) marker which attaches a child pixelmap (palette) to its parent.

Decodes common BR_PMT_* pixel types to RGBA8 numpy arrays.
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .chunks import FID, BEReader

log = logging.getLogger(__name__)

UNKNOWN_CHUNKS: set[int] = set()
UNKNOWN_PIXEL_TYPES: set[int] = set()


# BR_PMT_* values (subset we handle)
PMT_INDEX_8 = 3
PMT_RGB_555 = 4
PMT_RGB_565 = 5
PMT_RGB_888 = 6
PMT_RGBX_888 = 7
PMT_RGBA_8888 = 8
PMT_BGR_555 = 17
PMT_RGBA_4444 = 18
PMT_ARGB_8888 = 23
PMT_RGBA_5551 = 30
PMT_ARGB_1555 = 31
PMT_ARGB_4444 = 32

_ELEM_SIZE = {
    PMT_INDEX_8: 1,
    PMT_RGB_555: 2,
    PMT_RGB_565: 2,
    PMT_BGR_555: 2,
    PMT_RGBA_4444: 2,
    PMT_RGBA_5551: 2,
    PMT_ARGB_1555: 2,
    PMT_ARGB_4444: 2,
    PMT_RGB_888: 3,
    PMT_RGBX_888: 4,
    PMT_RGBA_8888: 4,
    PMT_ARGB_8888: 4,
}


@dataclass
class Pixmap:
    name: str
    type: int
    row_bytes: int
    width: int
    height: int
    origin_x: int
    origin_y: int
    mip_offset: int
    pixels: bytes | None = None
    disk_elem_size: int = 0  # elem_size from the PIXELS chunk
    palette: "Pixmap | None" = None  # attached via ADD_MAP

    @property
    def bytes_per_pixel(self) -> int:
        return _ELEM_SIZE.get(self.type, 0)


# ---------------------------------------------------------------------------
# Header decode
# ---------------------------------------------------------------------------

def _decode_pixelmap_header(payload: bytes, has_mip: bool) -> Pixmap:
    r = BEReader(payload)
    typ = r.read_u8()
    row_bytes = r.read_u16_be()
    w = r.read_u16_be()
    h = r.read_u16_be()
    ox = r.read_u16_be()
    oy = r.read_u16_be()
    mip = r.read_u16_be() if has_mip else 0
    name = r.read_asciz()
    return Pixmap(
        name=name,
        type=typ,
        row_bytes=row_bytes,
        width=w,
        height=h,
        origin_x=ox,
        origin_y=oy,
        mip_offset=mip,
    )


def _decode_pixels(payload: bytes) -> tuple[bytes, int]:
    """PIXELS (0x21): u32 block_count, u32 elem_size, block_count × elem_size bytes.

    For 16-bit pixel types (elem_size == 2), each pair is stored big-endian
    and must be byte-swapped to native LE. For byte-array types (elem_size
    1, 3, 4 where each byte is an independent R/G/B/A channel) the bytes
    are already in natural order on disk - no swap is required.

    Returns (pixel_bytes, elem_size) so higher-level decoders know the stride
    used on disk (which may differ from `bytes_per_pixel` for odd cases).
    """
    r = BEReader(payload)
    block_count = r.read_u32_be()
    elem_size = r.read_u32_be()
    need = block_count * elem_size
    if need > r.remaining():
        raise ValueError(f"PIXELS truncated (need {need}, have {r.remaining()})")
    raw = r.read(need)
    if elem_size == 2:
        arr = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 2)
        arr = arr[:, ::-1]
        return arr.tobytes(), elem_size
    return raw, elem_size


# ---------------------------------------------------------------------------
# File parser (stack-based to honour ADD_MAP)
# ---------------------------------------------------------------------------

def parse_pixmap_file(path: str | Path) -> list[Pixmap]:
    """Parse a .pix or .pal file. Returns top-level pixmaps (palette attached).

    Grammar (empirical, matches BRender writer):

        FILE_INFO
        (PIXELMAP ( PIXELMAP PIXELS ADD_MAP )? PIXELS END)*
    """
    path = Path(path)
    try:
        r = BEReader.from_path(path)
    except OSError as exc:
        log.warning("pixmap %s: cannot open: %s", path.name, exc)
        return []

    # FILE_INFO
    try:
        cid, plen = r.read_chunk_header()
        r.skip_chunk(plen)
    except EOFError:
        return []

    top: list[Pixmap] = []
    stack: list[Pixmap] = []

    while not r.eof():
        try:
            cid, plen = r.read_chunk_header()
        except EOFError:
            break
        if plen > r.remaining():
            log.warning("%s: truncated chunk 0x%02X plen=%d", path.name, cid, plen)
            break
        try:
            payload = r.read(plen)
        except EOFError:
            break

        try:
            if cid == FID.PIXELMAP:
                pm = _decode_pixelmap_header(payload, has_mip=True)
                stack.append(pm)
            elif cid == FID.OLD_PIXELMAP:
                pm = _decode_pixelmap_header(payload, has_mip=False)
                stack.append(pm)
            elif cid == FID.PIXELS:
                if not stack:
                    log.warning("%s: PIXELS with no pixelmap on stack", path.name)
                    continue
                pixel_bytes, disk_es = _decode_pixels(payload)
                stack[-1].pixels = pixel_bytes
                stack[-1].disk_elem_size = disk_es
            elif cid == FID.ADD_MAP:
                # Pop top (= palette) and attach to new top (= image)
                if len(stack) >= 2:
                    child = stack.pop()
                    stack[-1].palette = child
                else:
                    log.warning("%s: ADD_MAP with <2 on stack", path.name)
            elif cid == FID.END:
                if stack:
                    pm = stack.pop()
                    if not stack:
                        top.append(pm)
            else:
                UNKNOWN_CHUNKS.add(cid)
                log.debug("%s: skipping chunk 0x%02X (plen=%d)", path.name, cid, plen)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: error parsing chunk 0x%02X: %s", path.name, cid, exc)

    # Catch trailing (no-END) pixmaps
    while stack:
        top.append(stack.pop())

    return top


# ---------------------------------------------------------------------------
# Palette helpers
# ---------------------------------------------------------------------------

def parse_palette_file(path: str | Path) -> np.ndarray | None:
    """Parse a .pal file (or .pix palette) and return an (N,3) uint8 RGB array,
    or None on failure. N is typically 256. Returns None if the file is not
    a BRender palette."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError:
        return None

    # Detect BRender vs RAW 768-byte RGB palette
    if len(data) >= 4 and data[:4] == b"\x00\x00\x00\x12":
        # BRender palette file
        pixmaps = parse_pixmap_file(path)
        for pm in pixmaps:
            pal = _pixmap_to_palette(pm)
            if pal is not None:
                return pal
        return None

    if len(data) == 768:
        arr = np.frombuffer(data, dtype=np.uint8).reshape(256, 3)
        return arr.copy()

    if len(data) == 1024:
        arr = np.frombuffer(data, dtype=np.uint8).reshape(256, 4)
        return arr[:, :3].copy()

    return None


def _pixmap_to_palette(pm: Pixmap) -> np.ndarray | None:
    if pm.pixels is None:
        return None
    if pm.type == PMT_RGBX_888:
        n = len(pm.pixels) // 4
        if n == 0:
            return None
        arr = np.frombuffer(pm.pixels[: n * 4], dtype=np.uint8).reshape(n, 4)
        # On-disk layout per entry (no byte-swap applied for elem_size 4): X, R, G, B.
        rgb = arr[:, [1, 2, 3]].copy()
        return rgb
    if pm.type == PMT_RGB_888:
        n = len(pm.pixels) // 3
        if n == 0:
            return None
        arr = np.frombuffer(pm.pixels[: n * 3], dtype=np.uint8).reshape(n, 3)
        return arr.copy()
    return None


# ---------------------------------------------------------------------------
# Decoder
# ---------------------------------------------------------------------------

def _rows(pm: Pixmap) -> np.ndarray | None:
    """Return an (h, w*bpp) uint8 array of pixel bytes, honouring row_bytes padding."""
    if pm.pixels is None:
        return None
    bpp = pm.bytes_per_pixel
    if bpp == 0 or pm.width == 0 or pm.height == 0:
        return None
    row_bytes = pm.row_bytes if pm.row_bytes else pm.width * bpp
    data = pm.pixels
    rows: list[np.ndarray] = []
    for y in range(pm.height):
        start = y * row_bytes
        end = start + pm.width * bpp
        if end > len(data):
            # truncated
            break
        rows.append(np.frombuffer(data[start:end], dtype=np.uint8))
    if not rows:
        return None
    return np.stack(rows, axis=0)


def decode_to_rgba(pm: Pixmap, palette: np.ndarray | None = None) -> np.ndarray | None:
    """Decode a pixelmap to an (H, W, 4) uint8 RGBA array.

    Uses `palette` (N×3 uint8) for INDEX_8; falls back to `pm.palette` if
    present; returns None if a paletted image has no palette available or
    the pixel type is not supported.
    """
    if pm.type not in _ELEM_SIZE:
        UNKNOWN_PIXEL_TYPES.add(pm.type)
    rows = _rows(pm)
    if rows is None:
        return None

    h, _ = rows.shape
    w = pm.width

    if pm.type == PMT_INDEX_8:
        # Prefer an embedded palette if present; fall back to the caller-supplied one.
        pal = None
        if pm.palette is not None:
            pal = _pixmap_to_palette(pm.palette)
        if pal is None:
            pal = palette
        if pal is None:
            return None
        idx = rows.astype(np.int32)
        # clip indices that exceed palette size
        pal_n = pal.shape[0]
        idx = np.clip(idx, 0, pal_n - 1)
        rgb = pal[idx]  # (H, W, 3)
        rgba = np.concatenate([rgb, np.full((h, w, 1), 255, dtype=np.uint8)], axis=2)
        return rgba.astype(np.uint8)

    if pm.type == PMT_RGB_555:
        words = rows.view(np.uint16).reshape(h, w)
        r = ((words >> 10) & 0x1F).astype(np.uint16)
        g = ((words >> 5) & 0x1F).astype(np.uint16)
        b = (words & 0x1F).astype(np.uint16)
        rgba = np.empty((h, w, 4), dtype=np.uint8)
        rgba[..., 0] = ((r * 255 + 15) // 31).astype(np.uint8)
        rgba[..., 1] = ((g * 255 + 15) // 31).astype(np.uint8)
        rgba[..., 2] = ((b * 255 + 15) // 31).astype(np.uint8)
        rgba[..., 3] = 255
        return rgba

    if pm.type == PMT_BGR_555:
        words = rows.view(np.uint16).reshape(h, w)
        b = ((words >> 10) & 0x1F).astype(np.uint16)
        g = ((words >> 5) & 0x1F).astype(np.uint16)
        r = (words & 0x1F).astype(np.uint16)
        rgba = np.empty((h, w, 4), dtype=np.uint8)
        rgba[..., 0] = ((r * 255 + 15) // 31).astype(np.uint8)
        rgba[..., 1] = ((g * 255 + 15) // 31).astype(np.uint8)
        rgba[..., 2] = ((b * 255 + 15) // 31).astype(np.uint8)
        rgba[..., 3] = 255
        return rgba

    if pm.type == PMT_RGB_565:
        words = rows.view(np.uint16).reshape(h, w)
        r = ((words >> 11) & 0x1F).astype(np.uint16)
        g = ((words >> 5) & 0x3F).astype(np.uint16)
        b = (words & 0x1F).astype(np.uint16)
        rgba = np.empty((h, w, 4), dtype=np.uint8)
        rgba[..., 0] = ((r * 255 + 15) // 31).astype(np.uint8)
        rgba[..., 1] = ((g * 255 + 31) // 63).astype(np.uint8)
        rgba[..., 2] = ((b * 255 + 15) // 31).astype(np.uint8)
        rgba[..., 3] = 255
        return rgba

    if pm.type == PMT_RGB_888:
        arr = rows.reshape(h, w, 3)
        rgba = np.empty((h, w, 4), dtype=np.uint8)
        # On-disk order per pixel: R, G, B
        rgba[..., 0] = arr[..., 0]
        rgba[..., 1] = arr[..., 1]
        rgba[..., 2] = arr[..., 2]
        rgba[..., 3] = 255
        return rgba

    if pm.type == PMT_RGBX_888:
        arr = rows.reshape(h, w, 4)
        rgba = np.empty((h, w, 4), dtype=np.uint8)
        # On-disk order per entry: X, R, G, B
        rgba[..., 0] = arr[..., 1]
        rgba[..., 1] = arr[..., 2]
        rgba[..., 2] = arr[..., 3]
        rgba[..., 3] = 255
        return rgba

    if pm.type == PMT_RGBA_8888:
        arr = rows.reshape(h, w, 4)
        rgba = np.empty((h, w, 4), dtype=np.uint8)
        # On-disk order per entry: A, R, G, B
        rgba[..., 0] = arr[..., 1]
        rgba[..., 1] = arr[..., 2]
        rgba[..., 2] = arr[..., 3]
        rgba[..., 3] = arr[..., 0]
        return rgba

    if pm.type == PMT_ARGB_8888:
        arr = rows.reshape(h, w, 4)
        rgba = np.empty((h, w, 4), dtype=np.uint8)
        rgba[..., 0] = arr[..., 1]
        rgba[..., 1] = arr[..., 2]
        rgba[..., 2] = arr[..., 3]
        rgba[..., 3] = arr[..., 0]
        return rgba

    if pm.type == PMT_RGBA_4444:
        words = rows.view(np.uint16).reshape(h, w)
        rgba = np.empty((h, w, 4), dtype=np.uint8)
        rgba[..., 0] = (((words >> 12) & 0xF) * 17).astype(np.uint8)
        rgba[..., 1] = (((words >> 8) & 0xF) * 17).astype(np.uint8)
        rgba[..., 2] = (((words >> 4) & 0xF) * 17).astype(np.uint8)
        rgba[..., 3] = ((words & 0xF) * 17).astype(np.uint8)
        return rgba

    if pm.type == PMT_ARGB_4444:
        words = rows.view(np.uint16).reshape(h, w)
        rgba = np.empty((h, w, 4), dtype=np.uint8)
        rgba[..., 0] = (((words >> 8) & 0xF) * 17).astype(np.uint8)
        rgba[..., 1] = (((words >> 4) & 0xF) * 17).astype(np.uint8)
        rgba[..., 2] = ((words & 0xF) * 17).astype(np.uint8)
        rgba[..., 3] = (((words >> 12) & 0xF) * 17).astype(np.uint8)
        return rgba

    if pm.type == PMT_RGBA_5551:
        words = rows.view(np.uint16).reshape(h, w)
        rgba = np.empty((h, w, 4), dtype=np.uint8)
        rgba[..., 0] = ((((words >> 11) & 0x1F) * 255 + 15) // 31).astype(np.uint8)
        rgba[..., 1] = ((((words >> 6) & 0x1F) * 255 + 15) // 31).astype(np.uint8)
        rgba[..., 2] = ((((words >> 1) & 0x1F) * 255 + 15) // 31).astype(np.uint8)
        rgba[..., 3] = ((words & 0x1) * 255).astype(np.uint8)
        return rgba

    if pm.type == PMT_ARGB_1555:
        words = rows.view(np.uint16).reshape(h, w)
        rgba = np.empty((h, w, 4), dtype=np.uint8)
        rgba[..., 0] = ((((words >> 10) & 0x1F) * 255 + 15) // 31).astype(np.uint8)
        rgba[..., 1] = ((((words >> 5) & 0x1F) * 255 + 15) // 31).astype(np.uint8)
        rgba[..., 2] = (((words & 0x1F) * 255 + 15) // 31).astype(np.uint8)
        rgba[..., 3] = (((words >> 15) & 0x1) * 255).astype(np.uint8)
        return rgba

    UNKNOWN_PIXEL_TYPES.add(pm.type)
    log.warning("pixmap %s: unsupported type %d", pm.name, pm.type)
    return None
