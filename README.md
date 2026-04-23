# Big Brother (1999) — Asset Extractor

A Python tool that extracts 3D models and textures from the 1999 MediaX prototype
*Big Brother* directly from its original CD-ROM image (`.BIN` / `.CUE`).
The game was never released; the only public build is the January 4, 1999
alpha uploaded to the Internet Archive by `shedtroll1984`.

The tool decodes the original Argonaut **BRender** `v1.x` asset format
(`.DAT` models, `.PIX` textures, `.PAL` palettes, `.MAT` materials) from
scratch and writes standard `.obj` / `.mtl` / `.png` files that any modern 3D
application can open.

**Output stats** for the `990104_1516.BIN` build: **11,713 models**,
**2,286 textures**, no parser errors, no unsupported pixel formats.

---

## For everyone — just the tool

### What it does

You give it the `.BIN` / `.CUE` disc image and it gives you back a folder
with every 3D model from the game as an `.obj` file plus all textures as
`.png`. It also organises the output into rough categories (Characters,
Items, Props, and one subfolder per level).

### Requirements

- Windows, macOS, or Linux
- **Python 3.8 or newer** with Tkinter
  - Windows and macOS installers from [python.org](https://www.python.org/downloads/) already include Tkinter.
  - On Debian/Ubuntu Linux: `sudo apt install python3 python3-tk`.
- Internet connection the first time you run it (so it can install three small packages — `pycdlib`, `numpy`, `Pillow`).

### Install & run

```
git clone https://github.com/sashaok123/bigbrother-extractor.git
cd bigbrother-extractor
python extract_gui.py
```

Or download the zip from the **Code** button on GitHub, unpack it, and
double-click `extract_gui.py` on Windows.

Obtain the disc image yourself (see
[archive.org/details/BigBrother19990401](https://archive.org/details/BigBrother19990401))
and point the GUI at the `.BIN` file.

### Using the GUI

1. **BIN/CUE file** — pick the `.BIN` (the extractor auto-detects one if it sits next to `extract_gui.py`).
2. **Output folder** — defaults to `Extracted/` in the project directory.
3. Click **Run all (1 → 4)**. That's it.

The four steps it runs, in case you want to do them one at a time:

| Step | What happens |
|---|---|
| 1. BIN → ISO        | Rips the `MODE2/2352` CD image into a plain ISO9660. |
| 2. Unpack ISO tree  | Extracts every file from the ISO into `Extracted/ISO/`. |
| 3. Extract BRender assets | Parses every `.dat` / `.pix` / `.pal` / `.mat` into `.obj` / `.mtl` / `.png`. |
| 4. Sort into basic categories | Organises the output into `Categorized/Characters/`, `Categorized/Items/`, `Categorized/Props/`, `Categorized/Environment/<level>/`. |

Each step is idempotent — you can rerun safely.

### Output layout

```
Extracted/
    disc.iso                     raw ISO9660 image
    ISO/                         full file tree from the disc
    Mesh/*.obj + *.mtl           11,713 models
    Texture/*.png                2,286 textures (RGBA)
    mesh_texture_map.json        pid -> [texture names]
    extraction.log               per-format counters, zero errors expected
    Categorized/
        Characters/              NPCs (rat, bat, bird, gyrobot, macbot, ...)
        Items/                   pickups with an inventory icon
        Props/                   pickups without an inventory icon
        Environment/<level>/     world geometry per level
            catacomb/ dmuseum/ end/ harbor/ miniluvb/ numbers/
            prison/ Proleq/ river/ room101/ subway/ zerog/ zerog_d/
```

### Legal note

Big Brother was never released and was abandoned by MediaX in 1999. The
surviving build is circulated as a preservation artefact. This tool does not
include or distribute any copyrighted assets — it only operates on a disc
image you provide. The MediaX / Orwell Estate copyright on the underlying
assets is unaffected.

---

## For developers — how it works inside

### Repository layout

```
extract_gui.py                 Tkinter front-end (the only user-facing script)
brender/                       on-disk format parser (pure Python, NumPy)
    __init__.py                re-exports of the public API
    chunks.py                  big-endian byte reader + chunk-ID enum
    model.py                   parse_model_file()   -> list[Model]
    material.py                parse_material_file() -> list[Material]
    pixelmap.py                parse_pixmap_file(), parse_palette_file(), decode_to_rgba()
    iso.py                     rip(), extract_tree(), strip_version()
    extract.py                 full pipeline run(iso_root, out_root)
    categorize_basic.py        folder-based sort into semantic subfolders
requirements.txt               pycdlib, numpy, Pillow
```

The GUI is a thin Tk wrapper — all the real work lives in `brender/` and is
usable from any Python script.

### BRender format summary

On-disk **everything is big-endian**: chunk headers, struct fields, floats,
and every pixel word. Each file begins with a `FILE_INFO` chunk
(`id=0x12`, payload `type:u32, version:u32`). Chunks that group sub-chunks
(`MODEL`, `MATERIAL`, `PIXELMAP`, `ACTOR`) are terminated by an `END` chunk
(`id=0x00`, payload length 0).

The extractor understands both the v1.2+ chunk set and every legacy
(`OLD_*`) variant, so older BRender titles should work too with little or
no modification. Concretely:

| Area | Supported chunks |
|---|---|
| Models     | `MODEL 0x40`, `OLD_MODEL 0x0D`, `OLD_MODEL_1 0x1B`, `OLD_MODEL_2 0x36`, `VERTICES 0x17` + `OLD_VERTICES 0x0A`, `VERTEX_UV 0x18` + `OLD_VERTICES_UV 0x0B`, `VERTEX_NORMAL 0x42`, `VERTEX_COLOUR 0x41`, `FACES 0x35` + `OLD_FACES 0x0C` + `OLD_FACES_1 0x19`, `MATERIAL_INDEX 0x16` + `OLD_MATERIAL_INDEX 0x09`, `FACE_MATERIAL 0x1A`, `FACE_COLOUR 0x43`, `PIVOT 0x15` |
| Materials  | `MATERIAL 0x3E`, `MATERIAL_OLD 0x3C`, `MATERIAL_OLDEST 0x04`, `COLOUR_MAP_REF 0x1C`, `INDEX_SHADE_REF 0x1F`, `INDEX_BLEND_REF 0x1E`, `SCREENDOOR_REF 0x20`, `INDEX_FOG_REF 0x3B` |
| Pixelmaps  | `PIXELMAP 0x3D`, `OLD_PIXELMAP 0x03`, `PIXELS 0x21`, `ADD_MAP 0x22` |

Pixel types decoded to RGBA8:

| `BR_PMT_*` | Bits | Notes |
|---:|---:|---|
| 3 INDEX_8     | 8  | needs companion palette (`.PAL` next to `.PIX`, or `Winstd.pal` fallback) |
| 4 RGB_555     | 16 | big-endian word, top bit unused |
| 5 RGB_565     | 16 | |
| 6 RGB_888     | 24 | |
| 7 RGBX_888    | 32 | used by palettes (256 × RGBX) |
| 8 RGBA_8888   | 32 | disk order A,R,G,B |
| 17 BGR_555    | 16 | byte-swapped RGB_555 |
| 18 RGBA_4444  | 16 | `RRRRGGGGBBBBAAAA`, alpha=0 is valid for additive particles |
| 23 ARGB_8888  | 32 | |
| 30 RGBA_5551  | 16 | |
| 31 ARGB_1555  | 16 | |
| 32 ARGB_4444  | 16 | |

### Gotchas worth knowing if you adapt this to another BRender game

- `FSM_COLOUR` writes **3 bytes** (R, G, B) on disk — no alpha byte.
- `FID_FACES` (0x35) is **9 bytes per face**: `u16 v0, u16 v1, u16 v2, u16 smoothing, u8 flags`. There is **no material index inside the face** — that lives in a separate `FID_FACE_MATERIAL` (0x1A) block, with **1-based** indices (0 = NULL material).
- `FID_VERTEX_UV` (0x18) is a separate chunk with the same count as `FID_VERTICES`; merge them into one vertex array.
- `FID_PIXELS` (0x21) element bytes are **big-endian on disk** — the writer byte-swaps each `elem_size`-byte group before emitting. On a little-endian host, swap back when reading 16/32-bit pixel types.
- `PIXELMAP.row_bytes` can exceed `width × bytes_per_pixel` (row padding) — read one row at a time, not the whole blob in one go.
- `.PAL` files are just regular pixelmaps of type `RGBX_888` (7) with `width=256, height=1` and a 1032-byte `PIXELS` payload. Entry layout on disk is **R, G, B, X** (the X byte is padding).
- `.MAT` / `.BRM` files write `FILE_INFO.type = 5` (FILE_TYPE_MATERIAL_OLD) even when the body uses the current `FID_MATERIAL` (0x3E) — do not branch on `FILE_INFO.type`; detect by chunk IDs instead.

### Using the library directly

```python
from pathlib import Path
from brender.iso import rip, extract_tree, strip_version
from brender.extract import run as extract_run

# 1. BIN -> ISO
rip(Path("990104_1516.BIN"), Path("out/disc.iso"))

# 2. ISO -> file tree (Joliet)
extract_tree(Path("out/disc.iso"), Path("out/ISO"))
strip_version(Path("out/ISO"))   # strip ';1' version suffix

# 3. BRender assets -> OBJ + PNG + JSON
extract_run(iso=Path("out/ISO"), out=Path("out"))
```

Or at an even lower level:

```python
from pathlib import Path
from brender.model import parse_model_file
from brender.pixelmap import parse_pixmap_file, parse_palette_file, decode_to_rgba
from brender.material import parse_material_file

models     = parse_model_file(Path("catacomb.dat"))
pixmaps    = parse_pixmap_file(Path("catacomb.pix"))
palette    = parse_palette_file(Path("catacomb.pal"))  # -> numpy (256, 3) uint8
materials  = parse_material_file(Path("catacomb.mat"))

rgba = decode_to_rgba(pixmaps[0], palette)             # -> numpy (H, W, 4) uint8
```

### Running on older Python

Everything in `brender/` uses `from __future__ import annotations`, so
modern typing syntax (`int | None`, `list[str]`) is just strings at runtime.
The minimum supported Python is **3.8**, limited only by the dependencies
(`pycdlib`, `numpy`, `Pillow`) and Tkinter. On Linux distributions where
Tkinter is a separate package, install `python3-tk` before running the GUI.

---

## Credits and references

This tool exists because Argonaut **open-sourced the BRender v1.3.2 source
code** in 2021. Every chunk ID and struct layout in `brender/` is a direct
port of the canonical C writers/readers.

**Primary source material**
- [foone/BRender-v1.3.2](https://github.com/foone/BRender-v1.3.2) — the canonical BRender 1.3.2 source release (Argonaut, 2021). Specifically `core/fw/datafile.[ch]`, `core/v1db/v1dbfile.c`, `core/pixelmap/pmfile.c`, `core/pixelmap/pmmem.[ch]`, `inc/pixelmap.h`, `inc/material.h`, `inc/model.h`, `inc/actor.h`.
- [foone/BRender-1997](https://github.com/foone/BRender-1997), [foone/BRender-v1.1.2](https://github.com/foone/BRender-v1.1.2) — earlier releases, useful for understanding the evolution of the legacy `OLD_*` chunks.
- [FFmpeg `libavcodec/brender_pix.c`](https://ffmpeg.org/doxygen/2.0/brender__pix_8c_source.html) — third-party PIX decoder that corroborates the pixel chunk IDs and big-endian encoding.
- [Archiveteam — BRender PIX format page](http://fileformats.archiveteam.org/wiki/BRender_PIX).
- [Carmageddon Wiki — BRender](https://wiki.cwaboard.co.uk/wiki/BRender), [CWA Board: BRender pixelmap types](https://www.cwaboard.co.uk/viewtopic.php?t=19755), [Toshiba-3/BRender-pixelmap-types](https://github.com/Toshiba-3/BRender-pixelmap-types) — community reference samples across all `BR_PMT_*` types.
- [dethrace-labs/dethrace](https://github.com/dethrace-labs/dethrace) — Carmageddon 1 reverse-engineering project, an excellent case study of a BRender-based game.

**Game**
- [Big Brother (Jan 4, 1999 build) on archive.org](https://archive.org/details/BigBrother19990401) — the disc image this tool was developed against. Uploaded by `shedtroll1984@gmail.com`, preservation item hosted by the Internet Archive.
- [*Big Brother* at Games That Weren't](https://www.gamesthatwerent.com/2025/03/big-brother/) — background on the project and its cancellation.
- *Big Brother* © 1996–1999 MediaX, with a publishing arrangement involving the estate of George Orwell.

**Runtime dependencies**
- [pycdlib](https://github.com/clalancette/pycdlib) — ISO9660 reader.
- [NumPy](https://numpy.org/) — pixel arithmetic.
- [Pillow](https://python-pillow.org/) — PNG encoding.

**License**
The tool itself is released under the MIT License (see `LICENSE`). Big
Brother's assets are copyright their respective owners and are not
redistributed by this repository.
