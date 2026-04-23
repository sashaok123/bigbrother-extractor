"""BRender v1.x model (.dat) parser.

Produces `Model` dataclasses with vertices, UVs, optional normals, triangle
faces and per-face material indices (1-based into `material_names`, 0 = null).

Handles the modern MODEL (0x40) and legacy OLD_MODEL variants
(0x0D / 0x1B / 0x36), FACES (0x35) and legacy OLD_FACES (0x0C, 12 B/face) /
OLD_FACES_1 (0x19, 9 B with u8 smoothing), VERTICES + VERTEX_UV (merged on
count match), plus VERTEX_NORMAL (0x42).
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .chunks import FID, BEReader

log = logging.getLogger(__name__)

# Set of chunk IDs encountered that are not handled by this parser.
UNKNOWN_CHUNKS: set[int] = set()


@dataclass
class Model:
    name: str
    vertices: np.ndarray  # (N, 3) float32
    uvs: np.ndarray | None  # (N, 2) float32 or None
    normals: np.ndarray | None  # (N, 3) float32 or None
    faces: np.ndarray  # (M, 3) uint32 (0-based vertex indices)
    face_materials: np.ndarray  # (M,) uint16 (1-based, 0 = null)
    material_names: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Field-level readers
# ---------------------------------------------------------------------------

def _read_vertices(payload: bytes) -> np.ndarray:
    r = BEReader(payload)
    count = r.read_u32_be()
    need = count * 12
    if need > r.remaining():
        raise ValueError(f"VERTICES count {count} exceeds payload")
    raw = r.read(need)
    arr = np.frombuffer(raw, dtype=">f4").reshape(count, 3).astype(np.float32)
    return arr


def _read_vertex_uvs(payload: bytes) -> np.ndarray:
    r = BEReader(payload)
    count = r.read_u32_be()
    need = count * 8
    if need > r.remaining():
        raise ValueError(f"VERTEX_UV count {count} exceeds payload")
    raw = r.read(need)
    arr = np.frombuffer(raw, dtype=">f4").reshape(count, 2).astype(np.float32)
    return arr


def _read_vertex_normals(payload: bytes) -> np.ndarray:
    r = BEReader(payload)
    count = r.read_u32_be()
    need = count * 12
    if need > r.remaining():
        raise ValueError(f"VERTEX_NORMAL count {count} exceeds payload")
    raw = r.read(need)
    arr = np.frombuffer(raw, dtype=">f4").reshape(count, 3).astype(np.float32)
    return arr


def _read_faces(payload: bytes) -> np.ndarray:
    """FID_FACES (0x35): u32 count, then count × (u16 v0, u16 v1, u16 v2, u16 smoothing, u8 flags) = 9 B."""
    r = BEReader(payload)
    count = r.read_u32_be()
    need = count * 9
    if need > r.remaining():
        raise ValueError(f"FACES count {count} exceeds payload")
    buf = r.read(need)
    faces = np.empty((count, 3), dtype=np.uint32)
    for i in range(count):
        base = i * 9
        v0, v1, v2 = struct.unpack_from(">HHH", buf, base)
        faces[i, 0] = v0
        faces[i, 1] = v1
        faces[i, 2] = v2
    return faces


def _read_old_faces(payload: bytes) -> tuple[np.ndarray, np.ndarray]:
    """OLD_FACES (0x0C): count × 12 B (v0 v1 v2 u16, material u16, smoothing u32)."""
    r = BEReader(payload)
    count = r.read_u32_be()
    need = count * 12
    if need > r.remaining():
        raise ValueError(f"OLD_FACES count {count} exceeds payload")
    buf = r.read(need)
    faces = np.empty((count, 3), dtype=np.uint32)
    mats = np.zeros(count, dtype=np.uint16)
    for i in range(count):
        base = i * 12
        v0, v1, v2, mat = struct.unpack_from(">HHHH", buf, base)
        faces[i, 0] = v0
        faces[i, 1] = v1
        faces[i, 2] = v2
        mats[i] = mat
    return faces, mats


def _read_old_faces_1(payload: bytes) -> np.ndarray:
    """OLD_FACES_1 (0x19): count × 9 B (v0 v1 v2 u16, mat u16, u8 smoothing)."""
    r = BEReader(payload)
    count = r.read_u32_be()
    need = count * 9
    if need > r.remaining():
        raise ValueError(f"OLD_FACES_1 count {count} exceeds payload")
    buf = r.read(need)
    faces = np.empty((count, 3), dtype=np.uint32)
    for i in range(count):
        base = i * 9
        v0, v1, v2 = struct.unpack_from(">HHH", buf, base)
        faces[i, 0] = v0
        faces[i, 1] = v1
        faces[i, 2] = v2
    return faces


def _read_material_index(payload: bytes) -> list[str]:
    """MATERIAL_INDEX (0x16): u32 count, count × asciz names.

    Some legacy writers include a leading u16 padding before each name; we
    tolerate leading zero bytes between names.
    """
    r = BEReader(payload)
    count = r.read_u32_be()
    out: list[str] = []
    while len(out) < count and not r.eof():
        # Skip any leading zero bytes (alignment padding between names)
        while not r.eof() and r._buf[r._pos] == 0 and len(out) < count:
            # If we're at the very start of a name, a leading zero means an
            # empty name. We disambiguate: if the next non-zero byte is
            # printable, the zero was padding.
            lookahead = r._pos
            while lookahead < r._end and r._buf[lookahead] == 0:
                lookahead += 1
            if lookahead - r._pos >= 1 and lookahead < r._end and 32 <= r._buf[lookahead] < 127:
                r._pos = lookahead
                break
            # Otherwise, treat as an empty string entry
            r._pos += 1
            out.append("")
        if len(out) >= count or r.eof():
            break
        out.append(r.read_asciz())
    return out


def _read_face_material(payload: bytes) -> np.ndarray:
    """FACE_MATERIAL (0x1A): block_count u32, elem_size u32, (block_count × u16 BE)."""
    r = BEReader(payload)
    block_count = r.read_u32_be()
    elem_size = r.read_u32_be()
    if elem_size != 2:
        raise ValueError(f"FACE_MATERIAL unsupported elem_size {elem_size}")
    need = block_count * 2
    if need > r.remaining():
        raise ValueError("FACE_MATERIAL truncated")
    raw = r.read(need)
    return np.frombuffer(raw, dtype=">u2").astype(np.uint16)


def _read_old_material_index(payload: bytes) -> list[str]:
    return _read_material_index(payload)


# ---------------------------------------------------------------------------
# Model chunk (header) parsers
# ---------------------------------------------------------------------------

def _parse_model_header(cid: int, payload: bytes) -> str:
    """Return the model name from any MODEL-variant header chunk."""
    r = BEReader(payload)
    try:
        if cid == FID.MODEL:
            # u16 flags, 3 floats pivot, u16 crease_angle, float radius,
            # 6 floats bounds, asciz name
            r.read_u16_be()
            r.read_f32_be(); r.read_f32_be(); r.read_f32_be()
            r.read_u16_be()
            r.read_f32_be()
            for _ in range(6):
                r.read_f32_be()
            return r.read_asciz()
        else:
            # OLD_MODEL variants - name appears near the end.
            # Practical observation: u16 flags (or padding) then asciz.
            # Try reading from the end backwards: find the asciz by scanning
            # for the last ASCII-printable run preceding the terminator.
            data = payload
            # walk back to find the null terminator at the end
            end = len(data)
            # trim trailing zero bytes
            while end > 0 and data[end - 1] == 0:
                end -= 1
            if end == 0:
                return ""
            # find start of name = after the last non-printable byte
            start = end
            while start > 0 and 32 <= data[start - 1] < 127:
                start -= 1
            return data[start:end].decode("latin-1", errors="replace")
    except EOFError:
        return ""


# ---------------------------------------------------------------------------
# Top-level parser
# ---------------------------------------------------------------------------

def parse_model_file(path: str | Path) -> list[Model]:
    """Parse a BRender .DAT file containing zero or more models.

    Returns the list of successfully parsed models. Unknown chunks are logged
    and skipped. If the file is malformed past the FILE_INFO chunk, whatever
    models were parsed before the failure are returned.
    """
    path = Path(path)
    r = BEReader.from_path(path)

    # Consume FILE_INFO
    try:
        cid, plen = r.read_chunk_header()
    except EOFError:
        return []
    if cid != FID.FILE_INFO:
        log.warning("%s: first chunk not FILE_INFO (got 0x%08X) - not a BRender file", path.name, cid)
        return []
    if plen > r.remaining():
        log.warning("%s: FILE_INFO plen=%d exceeds file size", path.name, plen)
        return []
    r.skip_chunk(plen)

    models: list[Model] = []

    # Per-model scratch
    current_name: str | None = None
    vertices: np.ndarray | None = None
    uvs: np.ndarray | None = None
    normals: np.ndarray | None = None
    faces: np.ndarray | None = None
    face_materials: np.ndarray | None = None
    material_names: list[str] = []
    legacy_inline_mats: np.ndarray | None = None  # for OLD_FACES

    def flush() -> None:
        nonlocal current_name, vertices, uvs, normals, faces
        nonlocal face_materials, material_names, legacy_inline_mats
        if vertices is not None and faces is not None:
            name = current_name or path.stem
            if face_materials is None:
                if legacy_inline_mats is not None:
                    face_materials = legacy_inline_mats
                else:
                    face_materials = np.zeros(len(faces), dtype=np.uint16)
            # Normalize face-mat length
            if len(face_materials) < len(faces):
                pad = np.zeros(len(faces) - len(face_materials), dtype=np.uint16)
                face_materials = np.concatenate([face_materials, pad])
            elif len(face_materials) > len(faces):
                face_materials = face_materials[: len(faces)]
            models.append(
                Model(
                    name=name,
                    vertices=vertices,
                    uvs=uvs if uvs is not None and len(uvs) == len(vertices) else None,
                    normals=normals if normals is not None and len(normals) == len(vertices) else None,
                    faces=faces,
                    face_materials=face_materials,
                    material_names=list(material_names),
                )
            )
        current_name = None
        vertices = None
        uvs = None
        normals = None
        faces = None
        face_materials = None
        material_names = []
        legacy_inline_mats = None

    model_header_ids = {
        FID.MODEL,
        FID.OLD_MODEL,
        FID.OLD_MODEL_1,
        FID.OLD_MODEL_2,
    }

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
            if cid in model_header_ids:
                # Starting a new model - flush any previous
                if vertices is not None:
                    flush()
                current_name = _parse_model_header(cid, payload)
            elif cid == FID.VERTICES or cid == FID.OLD_VERTICES:
                vertices = _read_vertices(payload)
            elif cid == FID.OLD_VERTICES_UV:
                # u32 count, count × (3 floats pos + 2 floats uv) = 20 B
                rr = BEReader(payload)
                count = rr.read_u32_be()
                need = count * 20
                if need > rr.remaining():
                    raise ValueError("OLD_VERTICES_UV truncated")
                raw = rr.read(need)
                arr = np.frombuffer(raw, dtype=">f4").reshape(count, 5).astype(np.float32)
                vertices = arr[:, :3].copy()
                uvs = arr[:, 3:5].copy()
            elif cid == FID.VERTEX_UV:
                uvs = _read_vertex_uvs(payload)
            elif cid == FID.VERTEX_NORMAL:
                normals = _read_vertex_normals(payload)
            elif cid == FID.FACES:
                faces = _read_faces(payload)
            elif cid == FID.OLD_FACES:
                faces, legacy_inline_mats = _read_old_faces(payload)
            elif cid == FID.OLD_FACES_1:
                faces = _read_old_faces_1(payload)
            elif cid == FID.MATERIAL_INDEX or cid == FID.OLD_MATERIAL_INDEX:
                material_names = _read_material_index(payload)
            elif cid == FID.FACE_MATERIAL:
                face_materials = _read_face_material(payload)
            elif cid == FID.END:
                flush()
            elif cid == FID.FILE_INFO:
                pass  # ignore stray
            else:
                # Unknown at model scope - track and skip
                UNKNOWN_CHUNKS.add(cid)
                log.debug("%s: skipping chunk 0x%02X (plen=%d)", path.name, cid, plen)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: error parsing chunk 0x%02X: %s", path.name, cid, exc)

    # Catch-all: a file may end without an explicit END
    if vertices is not None and faces is not None:
        flush()

    return models
