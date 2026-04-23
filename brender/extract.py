"""Big Brother asset extractor.

Walks the ISO tree, exports every BRender model (.dat) as OBJ+MTL, every
pixelmap (.pix) as PNG, and writes `mesh_texture_map.json` + `extraction.log`.
"""

from __future__ import annotations

import json
import logging
import re
import traceback
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

from . import model as _model_mod
from . import pixelmap as _pixmap_mod
from .material import Material, parse_material_file
from .model import Model, parse_model_file
from .pixelmap import (
    Pixmap,
    decode_to_rgba,
    parse_palette_file,
    parse_pixmap_file,
)


log = logging.getLogger("extract")


_SAFE = re.compile(r"[^A-Za-z0-9_\-]+")


def sanitize(name: str, fallback: str = "mesh") -> str:
    out = _SAFE.sub("_", name).strip("_")
    return out or fallback


# ---------------------------------------------------------------------------
# OBJ / MTL writers
# ---------------------------------------------------------------------------

def write_obj(
    path: Path,
    model: Model,
    mtl_name: str | None,
    material_lookup: dict[str, Material],
) -> None:
    V = model.vertices
    F = model.faces
    UV = model.uvs
    N = model.normals

    # Per-face material index maps into model.material_names (1-based, 0=null).
    # Group faces by material so we can emit `usemtl` blocks.
    unique_mats = np.unique(model.face_materials)

    lines: list[str] = []
    lines.append("# Exported from BRender")
    if mtl_name:
        lines.append(f"mtllib {mtl_name}")
    lines.append(f"o {sanitize(model.name)}")

    # Vertices
    for v in V:
        lines.append(f"v {v[0]:.6f} {v[1]:.6f} {v[2]:.6f}")

    if UV is not None:
        for uv in UV:
            lines.append(f"vt {uv[0]:.6f} {1.0 - uv[1]:.6f}")

    if N is not None:
        for n in N:
            lines.append(f"vn {n[0]:.6f} {n[1]:.6f} {n[2]:.6f}")

    # Faces grouped by material
    for mat_idx in unique_mats:
        mask = model.face_materials == mat_idx
        if mat_idx == 0 or mat_idx > len(model.material_names):
            mat_name = None
        else:
            mat_name = model.material_names[mat_idx - 1]
        if mat_name:
            lines.append(f"usemtl {sanitize(mat_name)}")
        for face in F[mask]:
            f_idx = [int(face[0]) + 1, int(face[1]) + 1, int(face[2]) + 1]
            if UV is not None and N is not None:
                lines.append(f"f {f_idx[0]}/{f_idx[0]}/{f_idx[0]} "
                             f"{f_idx[1]}/{f_idx[1]}/{f_idx[1]} "
                             f"{f_idx[2]}/{f_idx[2]}/{f_idx[2]}")
            elif UV is not None:
                lines.append(f"f {f_idx[0]}/{f_idx[0]} "
                             f"{f_idx[1]}/{f_idx[1]} "
                             f"{f_idx[2]}/{f_idx[2]}")
            elif N is not None:
                lines.append(f"f {f_idx[0]}//{f_idx[0]} "
                             f"{f_idx[1]}//{f_idx[1]} "
                             f"{f_idx[2]}//{f_idx[2]}")
            else:
                lines.append(f"f {f_idx[0]} {f_idx[1]} {f_idx[2]}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_mtl(
    path: Path,
    model: Model,
    material_lookup: dict[str, Material],
    texture_names_by_base: set[str],
) -> None:
    lines: list[str] = ["# Exported from BRender"]
    for name in model.material_names:
        mat = material_lookup.get(name) or material_lookup.get(name.lower())
        lines.append(f"newmtl {sanitize(name)}")
        if mat is not None:
            r, g, b = mat.colour_rgb
            lines.append(f"Kd {r / 255:.4f} {g / 255:.4f} {b / 255:.4f}")
            lines.append(f"d {mat.opacity / 255:.4f}")
            if mat.colour_map:
                tex_base = mat.colour_map
                if tex_base.lower() in texture_names_by_base:
                    lines.append(f"map_Kd {tex_base}.png")
        else:
            lines.append("Kd 1.0 1.0 1.0")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Texture pipeline
# ---------------------------------------------------------------------------

def _find_sibling_palette(pix_path: Path, iso_root: Path) -> Path | None:
    stem = pix_path.stem
    parent = pix_path.parent
    for ext in (".pal", ".PAL", ".Pal"):
        cand = parent / f"{stem}{ext}"
        if cand.exists():
            return cand
    # Case-insensitive walk
    lower = stem.lower()
    for entry in parent.iterdir():
        if entry.is_file() and entry.stem.lower() == lower and entry.suffix.lower() == ".pal":
            return entry
    return None


def export_pixmaps(
    iso_root: Path,
    out_texture_dir: Path,
    counters: dict[str, int],
    errors: list[str],
    winstd_palette: np.ndarray | None,
) -> set[str]:
    """Walk the ISO tree and export every .pix as PNG. Returns the set of
    texture base names (lower-case, no extension) that were successfully
    exported."""
    out_texture_dir.mkdir(parents=True, exist_ok=True)
    bases: set[str] = set()
    pix_files = sorted(p for p in iso_root.rglob("*") if p.suffix.lower() == ".pix")
    for pix in pix_files:
        counters["pix_files"] += 1
        try:
            pixmaps = parse_pixmap_file(pix)
            pal_path = _find_sibling_palette(pix, iso_root)
            sibling_pal: np.ndarray | None = None
            if pal_path is not None:
                sibling_pal = parse_palette_file(pal_path)
                counters["pal_files"] += 1

            for idx, pm in enumerate(pixmaps):
                # Skip pixmaps that are palettes themselves — too thin to be useful
                if pm.height == 1 and pm.type in (7, 6):
                    continue
                if pm.pixels is None:
                    continue
                palette = sibling_pal if pm.palette is None else None
                if palette is None and pm.palette is None and pm.type == 3:
                    palette = winstd_palette
                img = decode_to_rgba(pm, palette)
                if img is None:
                    log.warning("pix %s: could not decode pixmap '%s' (type=%d)", pix.name, pm.name, pm.type)
                    counters["pix_decode_fail"] += 1
                    continue
                base = pm.name or pix.stem
                safe = sanitize(base, pix.stem)
                # Disambiguate collisions across pixmaps within a file and across files
                candidate = safe
                suffix = 0
                while candidate.lower() in bases:
                    suffix += 1
                    candidate = f"{safe}__{pix.stem}" if suffix == 1 else f"{safe}__{pix.stem}_{suffix}"
                out_path = out_texture_dir / f"{candidate}.png"
                Image.fromarray(img).save(out_path)
                bases.add(candidate.lower())
                counters["textures_extracted"] += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"pix {pix}: {exc}\n{traceback.format_exc(limit=3)}")
            counters["pix_parse_fail"] += 1
    return bases


# ---------------------------------------------------------------------------
# Material pipeline
# ---------------------------------------------------------------------------

def gather_materials(iso_root: Path, counters: dict[str, int], errors: list[str]) -> dict[str, Material]:
    """Index every material by name across all .mat / .brm files."""
    lookup: dict[str, Material] = {}
    mat_files: list[Path] = []
    for ext in (".mat", ".brm"):
        mat_files.extend(iso_root.rglob(f"*{ext}"))
        mat_files.extend(iso_root.rglob(f"*{ext.upper()}"))
    # Deduplicate on resolved path (case-insensitive file systems may double up).
    seen_paths = set()
    unique = []
    for p in mat_files:
        key = str(p).lower()
        if key in seen_paths:
            continue
        seen_paths.add(key)
        unique.append(p)

    for mat_path in sorted(unique):
        counters["mat_files"] += 1
        try:
            for mat in parse_material_file(mat_path):
                if not mat.name:
                    continue
                lookup.setdefault(mat.name, mat)
                lookup.setdefault(mat.name.lower(), mat)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"mat {mat_path}: {exc}\n{traceback.format_exc(limit=3)}")
            counters["mat_parse_fail"] += 1
    return lookup


# ---------------------------------------------------------------------------
# Model pipeline
# ---------------------------------------------------------------------------

def export_models(
    iso_root: Path,
    out_mesh_dir: Path,
    material_lookup: dict[str, Material],
    texture_bases: set[str],
    counters: dict[str, int],
    errors: list[str],
) -> dict[str, list[str]]:
    """Export OBJ + MTL for every MODEL chunk across all .dat files. Returns
    mesh_texture_map { pid(str) : [texture_base, ...] }.
    """
    out_mesh_dir.mkdir(parents=True, exist_ok=True)
    mesh_tex_map: dict[str, list[str]] = {}
    pid = 0

    dat_paths: list[Path] = sorted(p for p in iso_root.rglob("*") if p.suffix.lower() == ".dat")
    for dat in dat_paths:
        counters["dat_files"] += 1
        try:
            models = parse_model_file(dat)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"dat {dat}: {exc}\n{traceback.format_exc(limit=3)}")
            counters["dat_parse_fail"] += 1
            continue

        for model in models:
            pid += 1
            name = sanitize(model.name, dat.stem)
            obj_name = f"{name}@{pid}"
            obj_path = out_mesh_dir / f"{obj_name}.obj"
            mtl_name = f"{obj_name}.mtl"
            mtl_path = out_mesh_dir / mtl_name
            try:
                write_obj(obj_path, model, mtl_name, material_lookup)
                write_mtl(mtl_path, model, material_lookup, texture_bases)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"obj {dat} [{model.name}]: {exc}\n{traceback.format_exc(limit=3)}")
                counters["obj_write_fail"] += 1
                continue
            counters["models_extracted"] += 1

            # Build texture list for this model
            tex_list: list[str] = []
            for mname in model.material_names:
                mat = material_lookup.get(mname) or material_lookup.get(mname.lower())
                if mat and mat.colour_map:
                    tex = mat.colour_map
                    if tex not in tex_list:
                        tex_list.append(tex)
            mesh_tex_map[str(pid)] = tex_list

    return mesh_tex_map


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(iso: Path, out: Path, winstd: Path | None = None, verbose: bool = False) -> int:
    """Extract BRender assets from an extracted ISO tree.

    Parameters
    ----------
    iso : Path
        Directory containing the extracted ISO9660 tree.
    out : Path
        Output root — will contain ``Mesh/``, ``Texture/``,
        ``mesh_texture_map.json`` and ``extraction.log``.
    winstd : Path | None
        Optional fallback palette (defaults to ``<project>/Dll/Winstd.pal``).
    verbose : bool
        Debug-level logging.
    """
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.DEBUG if verbose else logging.INFO,
            format="%(levelname)s %(name)s: %(message)s",
        )

    iso_root = Path(iso)
    out_root = Path(out)
    if not iso_root.is_dir():
        log.error("ISO root not found: %s", iso_root)
        return 1

    if winstd is None:
        # project root is the parent of the brender/ package
        winstd = Path(__file__).resolve().parent.parent / "Dll" / "Winstd.pal"
    winstd = Path(winstd)

    mesh_dir = out_root / "Mesh"
    tex_dir = out_root / "Texture"
    out_root.mkdir(parents=True, exist_ok=True)
    mesh_dir.mkdir(parents=True, exist_ok=True)
    tex_dir.mkdir(parents=True, exist_ok=True)

    counters: dict[str, int] = defaultdict(int)
    errors: list[str] = []

    winstd_pal = None
    if winstd.exists():
        winstd_pal = parse_palette_file(winstd)
        if winstd_pal is not None:
            log.info("loaded Winstd.pal (%d entries)", winstd_pal.shape[0])

    log.info("scanning materials...")
    material_lookup = gather_materials(iso_root, counters, errors)
    log.info("  %d materials indexed (from %d files)", len(material_lookup) // 2, counters["mat_files"])

    log.info("exporting textures...")
    texture_bases = export_pixmaps(iso_root, tex_dir, counters, errors, winstd_pal)
    log.info("  %d textures exported", counters["textures_extracted"])

    log.info("exporting models...")
    mesh_tex_map = export_models(iso_root, mesh_dir, material_lookup, texture_bases, counters, errors)
    log.info("  %d models exported", counters["models_extracted"])

    # Write JSON map
    json_path = out_root / "mesh_texture_map.json"
    json_path.write_text(json.dumps(mesh_tex_map, indent=2), encoding="utf-8")

    # Write log
    log_path = out_root / "extraction.log"
    with log_path.open("w", encoding="utf-8") as fh:
        fh.write("Big Brother asset extraction log\n")
        fh.write("=" * 40 + "\n\n")
        for key in sorted(counters):
            fh.write(f"{key}: {counters[key]}\n")
        fh.write("\n")
        unknown_chunks = sorted(_model_mod.UNKNOWN_CHUNKS | _pixmap_mod.UNKNOWN_CHUNKS)
        fh.write("unknown_chunk_ids: " + ", ".join(f"0x{c:02X}" for c in unknown_chunks) + "\n")
        unknown_pt = sorted(_pixmap_mod.UNKNOWN_PIXEL_TYPES)
        fh.write("unsupported_pixel_types: " + ", ".join(str(t) for t in unknown_pt) + "\n\n")
        fh.write(f"errors: {len(errors)}\n")
        for e in errors:
            fh.write("- " + e + "\n")
        fh.write("\nnote: actor hierarchy not baked; .act files ignored this pass.\n")

    log.info("done; log at %s", log_path)
    return 0
