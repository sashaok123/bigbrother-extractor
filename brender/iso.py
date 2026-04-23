"""ISO-level helpers: BIN/CUE -> ISO9660, ISO tree extraction, ;1 version strip.

Combines the former standalone scripts ``bin_to_iso.py``, ``iso_extract.py``
and ``strip_iso_version.py``.

BIN conversion notes:
    For MODE2 Form 1 XA sectors: 12 B sync + 4 B header + 8 B subheader + 2048 B
    user data + 4 B EDC + 276 B ECC = 2352 B. User data starts at offset 24.
    For MODE1/2352: same offset 24 (12 sync + 4 header + 8-byte sub not present,
    but 2048 B user data still starts at 16, then 288 B of EDC/ECC). To keep
    this robust, we detect the sector mode from the header byte.
"""
from __future__ import annotations

from pathlib import Path

import pycdlib

SECTOR = 2352
USER_DATA = 2048


def rip(bin_path: Path, iso_path: Path) -> int:
    """Convert a MODE2/2352 .BIN into a plain ISO9660 image (2048 B/sector)."""
    size = bin_path.stat().st_size
    if size % SECTOR != 0:
        print(f"[warn] {bin_path.name} size {size} not a multiple of {SECTOR}")
    sectors = size // SECTOR
    with bin_path.open("rb") as fi, iso_path.open("wb") as fo:
        for _ in range(sectors):
            s = fi.read(SECTOR)
            if len(s) < SECTOR:
                break
            mode = s[15]
            if mode == 1:
                user = s[16:16 + USER_DATA]
            elif mode == 2:
                # Form 1: subheader at [16:24], data at [24:24+2048]
                user = s[24:24 + USER_DATA]
            else:
                # Unknown/audio — skip
                continue
            fo.write(user)
    return sectors


def extract_tree(iso_path: Path, dst: Path) -> int:
    """Extract an ISO9660 tree (Joliet/Rock Ridge aware) to a directory."""
    iso = pycdlib.PyCdlib()
    iso.open(str(iso_path))
    facade = None
    facade_kind = "iso9660"
    try:
        facade = iso.get_joliet_facade(); facade_kind = "joliet"
    except Exception:
        try:
            facade = iso.get_rock_ridge_facade(); facade_kind = "rockridge"
        except Exception:
            facade = iso.get_iso9660_facade()
    print(f"[iso] using {facade_kind} facade")
    dst.mkdir(parents=True, exist_ok=True)
    count = 0
    # facade-specific kwarg name
    path_kw = {
        "joliet": "joliet_path",
        "rockridge": "rr_path",
        "iso9660": "iso_path",
    }[facade_kind]
    for root, dirs, files in facade.walk("/"):
        rel = root.lstrip("/")
        (dst / rel).mkdir(parents=True, exist_ok=True)
        for name in files:
            src = f"{root.rstrip('/')}/{name}"
            out = dst / rel / name.lstrip("/")
            out.parent.mkdir(parents=True, exist_ok=True)
            with out.open("wb") as fo:
                facade.get_file_from_iso_fp(fo, **{path_kw: src})
            count += 1
    iso.close()
    return count


def strip_version(root: Path) -> int:
    """Rename 'FOO.EXT;1' -> 'FOO.EXT' recursively. Returns count renamed."""
    n = 0
    for p in root.rglob("*"):
        if p.is_file() and ";" in p.name:
            new = p.with_name(p.name.split(";", 1)[0])
            if new != p:
                if new.exists():
                    p.unlink()
                else:
                    p.rename(new)
                n += 1
    return n
