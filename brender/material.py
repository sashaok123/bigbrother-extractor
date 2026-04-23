"""BRender v1.x material (.mat / .brm) parser.

Returns a list of `Material` records with the material name and the attached
`COLOUR_MAP_REF` texture base name (if any).

Empirically, MediaX-era .mat files written with MATERIAL_OLD (0x3C) declare
payload lengths that overlap the following chunk by a handful of bytes. To
stay robust, we parse MATERIAL-family bodies leniently: we take the name as
the last printable asciz inside the declared payload, then consume any
trailing `COLOUR_MAP_REF (0x1C)` / `INDEX_*_REF` / `END` sub-chunks. If the
top-level walker lands in garbage, we fall back to a forward scan for known
chunk ids.
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from pathlib import Path

from .chunks import FID, BEReader

log = logging.getLogger(__name__)


@dataclass
class Material:
    name: str
    colour_map: str | None = None
    flags: int = 0
    colour_rgb: tuple[int, int, int] = (255, 255, 255)
    opacity: int = 255
    index_shade: str | None = None
    index_blend: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_KNOWN_CHUNK_IDS = {
    FID.END,
    FID.FILE_INFO,
    FID.MATERIAL,
    FID.MATERIAL_OLD,
    FID.MATERIAL_OLDEST,
    FID.COLOUR_MAP_REF,
    FID.INDEX_BLEND_REF,
    FID.INDEX_SHADE_REF,
    FID.SCREENDOOR_REF,
    FID.INDEX_FOG_REF,
}


def _extract_material_header(cid: int, payload: bytes) -> Material:
    """Extract the material name, base colour and opacity from a MATERIAL-family payload.

    Robust to slight body-length discrepancies in MediaX writers: we scan the
    payload from the end to find the asciz name.
    """
    m = Material(name="")
    if len(payload) < 4:
        return m

    # Colour + opacity are the first 4 bytes for all variants.
    m.colour_rgb = (payload[0], payload[1], payload[2])
    m.opacity = payload[3]

    # Name: trailing printable asciz near the end of the payload.
    end = len(payload)
    while end > 0 and payload[end - 1] == 0:
        end -= 1
    start = end
    while start > 0 and 32 <= payload[start - 1] < 127:
        start -= 1
    if start < end:
        m.name = payload[start:end].decode("latin-1", errors="replace")
    return m


def _scan_chunks(data: bytes, start: int = 0) -> list[tuple[int, int, int]]:
    """Linearly scan forward, yielding (offset, cid, plen) for every header
    whose id is in `_KNOWN_CHUNK_IDS` and whose plen stays in-bounds."""
    out: list[tuple[int, int, int]] = []
    i = start
    n = len(data)
    while i + 8 <= n:
        cid = struct.unpack_from(">I", data, i)[0]
        plen = struct.unpack_from(">I", data, i + 4)[0]
        if cid in _KNOWN_CHUNK_IDS and i + 8 + plen <= n and plen < 0x10000:
            out.append((i, cid, plen))
            i += 1
        else:
            i += 1
    return out


# ---------------------------------------------------------------------------
# Top-level parser
# ---------------------------------------------------------------------------

def parse_material_file(path: str | Path) -> list[Material]:
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        log.warning("material %s: cannot open: %s", path.name, exc)
        return []

    if len(data) < 16:
        return []

    # Must start with FILE_INFO
    hdr_id = struct.unpack_from(">I", data, 0)[0]
    if hdr_id != FID.FILE_INFO:
        log.warning("%s: not a BRender file", path.name)
        return []

    # Robust scan: collect offsets of every material-family chunk and every
    # COLOUR_MAP_REF chunk. Each material claims the refs that appear between
    # it and the next material/END.
    scanned = _scan_chunks(data)
    # De-dup overlaps: keep the first occurrence whose payload doesn't intrude
    # into an earlier chunk's body.
    deduped: list[tuple[int, int, int]] = []
    next_allowed = 16
    for off, cid, plen in scanned:
        if off < next_allowed:
            continue
        # Heuristic: when we see a MATERIAL-family chunk, require the payload
        # to contain a printable asciz near the end (the material name).
        if cid in (FID.MATERIAL, FID.MATERIAL_OLD, FID.MATERIAL_OLDEST):
            body = data[off + 8 : off + 8 + plen]
            if len(body) >= 16:
                # Look for at least one printable char in the tail
                tail = body[-32:]
                if not any(32 <= b < 127 for b in tail):
                    continue
        deduped.append((off, cid, plen))
        next_allowed = off + 8 + plen
        # Allow child chunks that start inside the declared range (they
        # legitimately overlap due to writer quirk).
        # To permit 0x1C immediately following at actual end, relax by
        # letting next_allowed rewind by up to 4 bytes.
        next_allowed -= 4  # tolerate writer off-by-a-few

    materials: list[Material] = []
    current: Material | None = None

    for off, cid, plen in deduped:
        payload = data[off + 8 : off + 8 + plen]
        if cid in (FID.MATERIAL, FID.MATERIAL_OLD, FID.MATERIAL_OLDEST):
            if current is not None:
                materials.append(current)
            current = _extract_material_header(cid, payload)
        elif cid == FID.COLOUR_MAP_REF:
            name = _asciz(payload)
            if current is not None:
                current.colour_map = name
        elif cid == FID.INDEX_SHADE_REF:
            if current is not None:
                current.index_shade = _asciz(payload)
        elif cid == FID.INDEX_BLEND_REF:
            if current is not None:
                current.index_blend = _asciz(payload)
        elif cid == FID.END:
            if current is not None:
                materials.append(current)
                current = None

    if current is not None:
        materials.append(current)

    return materials


def _asciz(payload: bytes) -> str:
    i = 0
    while i < len(payload) and payload[i] != 0:
        i += 1
    return payload[:i].decode("latin-1", errors="replace")
