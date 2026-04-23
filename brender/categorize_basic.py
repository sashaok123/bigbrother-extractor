"""Organize extracted Big Brother meshes/textures into semantic categories.

Reconstructs the same pid assignment used by :func:`brender.extract.run`
(deterministic walk over sorted .dat files, counting MODEL chunks) and
distributes the OBJ+MTL pairs into per-category folders, copying referenced
textures alongside each category's Mesh/ folder.

Categories:
    Characters/             MEDIA/Creatures/*  +  MEDIA/Proleq.dat
    Items/                  MEDIA/Objects/<X>.dat with inventory icon sibling
    Props/                  MEDIA/Objects/<X>.dat without inventory icon
    Environment/<level>/    MEDIA/<level>.dat at MEDIA root
    Misc/                   anything else

Each category folder has the shape:

    <Category>/
        Mesh/*.obj + *.mtl
        Texture/*.png
        mesh_texture_map.json
"""
from __future__ import annotations

import json
import logging
import re
import shutil
from collections import defaultdict
from pathlib import Path

from .model import parse_model_file

log = logging.getLogger("categorize_basic")

_SAFE = re.compile(r"[^A-Za-z0-9_\-]+")


def sanitize(name: str, fallback: str = "mesh") -> str:
    out = _SAFE.sub("_", name).strip("_")
    return out or fallback


def build_inventory_index(iso_root: Path) -> set[str]:
    """Return the set of lower-case stems that have an inventory icon."""
    icons: set[str] = set()
    for sub in ("Objects/Inventory", "Objects/Inventory2"):
        d = iso_root / "MEDIA" / sub
        if not d.is_dir():
            continue
        for p in d.iterdir():
            if p.suffix.lower() in (".pix", ".tga", ".bmp"):
                icons.add(p.stem.lower())
    return icons


def classify(dat_path: Path, iso_root: Path, inventory_icons: set[str]) -> tuple[str, str | None]:
    """Return (category, subcategory_or_None)."""
    try:
        rel = dat_path.relative_to(iso_root / "MEDIA")
    except ValueError:
        return ("Misc", None)

    parts = rel.parts
    stem = dat_path.stem.lower()

    if len(parts) >= 2 and parts[0].lower() == "creatures":
        return ("Characters", None)

    if len(parts) >= 2 and parts[0].lower() == "objects":
        if stem in inventory_icons:
            return ("Items", None)
        return ("Props", None)

    if len(parts) == 1:
        # MEDIA root .dat -> level geometry
        return ("Environment", dat_path.stem)

    return ("Misc", None)


# MTL rewrites: `map_Kd X.png` -> `map_Kd ../Texture/X.png`
_MAP_KD_RE = re.compile(r"^(\s*map_Kd\s+)(\S+)", re.MULTILINE)


def rewrite_and_copy_mtl(src: Path, dst: Path) -> set[str]:
    """Copy MTL rewriting texture paths. Returns the set of bare texture names."""
    text = src.read_text(encoding="latin-1")
    refs: set[str] = set()

    def repl(m: re.Match[str]) -> str:
        tex = m.group(2)
        refs.add(tex)
        if tex.startswith("../Texture/"):
            return m.group(0)
        return f"{m.group(1)}../Texture/{tex}"

    new_text = _MAP_KD_RE.sub(repl, text)
    dst.write_text(new_text, encoding="latin-1")
    return refs


def run(
    iso: Path,
    mesh: Path,
    texture: Path,
    texmap: Path,
    out: Path,
) -> list[tuple[str, int, int]]:
    """Rebuild per-category folders under ``out``. Returns a summary list of
    ``(label, meshes, textures)`` tuples."""
    iso = Path(iso)
    mesh = Path(mesh)
    texture = Path(texture)
    texmap = Path(texmap)
    out = Path(out)

    texmap_data = json.loads(texmap.read_text(encoding="utf-8"))
    inventory_icons = build_inventory_index(iso)
    log.info("inventory icons indexed: %d", len(inventory_icons))

    # Per-category buckets
    cat_pids: dict[tuple[str, str | None], list[int]] = defaultdict(list)
    pid_to_cat: dict[int, tuple[str, str | None]] = {}

    # Reproduce brender.extract.run's walk order and pid assignment
    pid = 0
    dat_paths = sorted(p for p in iso.rglob("*") if p.suffix.lower() == ".dat")
    for dat in dat_paths:
        try:
            models = parse_model_file(dat)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: parse failed: %s", dat.name, exc)
            continue
        cat = classify(dat, iso, inventory_icons)
        for _ in models:
            pid += 1
            pid_to_cat[pid] = cat
            cat_pids[cat].append(pid)

    log.info("total pids: %d", pid)
    for (cat, sub), pids in sorted(cat_pids.items()):
        label = f"{cat}/{sub}" if sub else cat
        log.info("  %-30s %5d models", label, len(pids))

    # Build pid -> OBJ filename index from Mesh/
    obj_by_pid: dict[int, Path] = {}
    for p in mesh.iterdir():
        if p.suffix.lower() != ".obj":
            continue
        m = re.search(r"@(\d+)\.obj$", p.name)
        if m:
            obj_by_pid[int(m.group(1))] = p

    missing = [pid for pid in pid_to_cat if pid not in obj_by_pid]
    if missing:
        log.warning("%d pids have no matching OBJ in Mesh/ (first 10: %s)", len(missing), missing[:10])

    # Copy per category
    out.mkdir(parents=True, exist_ok=True)
    per_cat_stats: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for cat_key, pids in cat_pids.items():
        cat, sub = cat_key
        cat_dir = out / cat / (sub or "")
        mesh_dst = cat_dir / "Mesh"
        tex_dst = cat_dir / "Texture"
        mesh_dst.mkdir(parents=True, exist_ok=True)
        tex_dst.mkdir(parents=True, exist_ok=True)

        needed_textures: set[str] = set()
        per_texmap: dict[str, list[str]] = {}

        for pid in pids:
            obj_src = obj_by_pid.get(pid)
            if not obj_src:
                continue
            mtl_src = obj_src.with_suffix(".mtl")
            obj_dst = mesh_dst / obj_src.name
            mtl_dst = mesh_dst / mtl_src.name

            shutil.copy2(obj_src, obj_dst)
            if mtl_src.exists():
                needed_textures.update(rewrite_and_copy_mtl(mtl_src, mtl_dst))
            per_cat_stats[f"{cat}/{sub}" if sub else cat]["meshes"] += 1

            # Carry the pid entry into this category's JSON
            per_texmap[str(pid)] = texmap_data.get(str(pid), [])
            # Also add names referenced in json (they may differ from MTL bare refs)
            for tname in per_texmap[str(pid)]:
                needed_textures.add(sanitize(tname) + ".png")
                needed_textures.add(tname + ".png")

        # Copy only textures that actually exist on disk (from either MTL or JSON)
        copied = 0
        for tex_name in sorted(needed_textures):
            bn = tex_name.split("/")[-1]
            src = texture / bn
            if src.exists():
                dst = tex_dst / bn
                if not dst.exists():
                    shutil.copy2(src, dst)
                    copied += 1
        per_cat_stats[f"{cat}/{sub}" if sub else cat]["textures"] = copied

        (cat_dir / "mesh_texture_map.json").write_text(
            json.dumps(per_texmap, indent=1),
            encoding="utf-8",
        )

    # Build summary list
    summary: list[tuple[str, int, int]] = []
    for label, stats in sorted(per_cat_stats.items()):
        summary.append((label, stats["meshes"], stats["textures"]))
    return summary
