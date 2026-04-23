# Big Brother (1999) Asset Extractor

Pulls 3D models and textures out of the 1999 MediaX game *Big Brother*. The game was cancelled and never shipped. The only public build is the January 4, 1999 alpha on the Internet Archive.

The tool reads the original Argonaut BRender v1.x files (`.DAT` models, `.PIX` textures, `.PAL` palettes, `.MAT` materials) and writes plain `.obj`, `.mtl`, and `.png` that any 3D app can open.

On the `990104_1516.BIN` disc image it produces **11,713 models** and **2,286 textures** with no parser errors.

---

## Quick start

You need Python 3.8 or newer with Tkinter. On Windows and macOS the installer from [python.org](https://www.python.org/downloads/) already includes Tkinter. On Debian/Ubuntu: `sudo apt install python3 python3-tk`.

```
git clone https://github.com/sashaok123/bigbrother-extractor.git
cd bigbrother-extractor
python extract_gui.py
```

Or grab the zip from the Releases page and run `extract_gui.py`.

Get the disc image from [archive.org/details/BigBrother19990401](https://archive.org/details/BigBrother19990401). Point the GUI at the `.BIN` file and click **Run all**.

### Using the GUI

1. Pick the `.BIN` (the GUI auto-fills it if one sits next to `extract_gui.py`).
2. Pick an output folder (default: `Extracted/` in the project).
3. Click **Run all (1 -> 4)**.

The four steps, if you want to do them one at a time:

| Step | What happens |
|---|---|
| 1. BIN -> ISO | Rips the MODE2/2352 CD image into a plain ISO9660. |
| 2. Unpack ISO tree | Extracts all files from the ISO into `Extracted/ISO/`. |
| 3. Extract BRender assets | Parses every `.dat`, `.pix`, `.pal`, `.mat` into `.obj`, `.mtl`, `.png`. |
| 4. Sort into categories | Groups output into `Categorized/Characters/`, `Items/`, `Props/`, `Environment/<level>/`. |

Rerunning any step is safe. It just overwrites.

The first launch installs `pycdlib`, `numpy`, and `Pillow` via pip.

### Output

```
Extracted/
    disc.iso                     raw ISO9660
    ISO/                         full file tree from the disc
    Mesh/*.obj + *.mtl           11,713 models
    Texture/*.png                2,286 textures (RGBA)
    mesh_texture_map.json        pid -> [texture names]
    extraction.log               per-format counters
    Categorized/
        Characters/              NPCs (rat, bat, bird, gyrobot, macbot, ...)
        Items/                   pickups that have an inventory icon
        Props/                   pickups without an icon
        Environment/<level>/     world geometry, one folder per level
            catacomb/ dmuseum/ end/ harbor/ miniluvb/ numbers/
            prison/ Proleq/ river/ room101/ subway/ zerog/ zerog_d/
```

### Legal

Big Brother was never released. MediaX stopped updating the game in 1999 and shut down in 2005. The surviving build is circulated for preservation. This repo does not ship any game assets. It only operates on a disc image that you supply. Copyright on the game content stays with MediaX and the Orwell estate.

---

## How the code works

### Layout

```
extract_gui.py                 Tkinter GUI, the only user-facing script
brender/                       on-disk format parser (pure Python + NumPy)
    __init__.py                re-exports
    chunks.py                  big-endian reader + chunk-ID enum
    model.py                   parse_model_file()   -> list[Model]
    material.py                parse_material_file() -> list[Material]
    pixelmap.py                parse_pixmap_file(), parse_palette_file(), decode_to_rgba()
    iso.py                     rip(), extract_tree(), strip_version()
    extract.py                 full pipeline: run(iso_root, out_root)
    categorize_basic.py        folder-based sort into category subfolders
requirements.txt               pycdlib, numpy, Pillow
```

The GUI is a thin Tk wrapper. The real work sits in `brender/` and is usable from any Python script.

### BRender format, short version

Everything on disk is big-endian: chunk headers, struct fields, floats, pixel words. Every file starts with a `FILE_INFO` chunk (`id=0x12`, payload `type:u32, version:u32`). Grouping chunks (`MODEL`, `MATERIAL`, `PIXELMAP`, `ACTOR`) close with an `END` chunk (`id=0x00`, payload length 0).

The parser handles both the v1.2+ chunk set and every legacy `OLD_*` variant, so other BRender games should work with little or no change.

| Area | Chunk IDs |
|---|---|
| Models | `MODEL 0x40`, `OLD_MODEL 0x0D`, `OLD_MODEL_1 0x1B`, `OLD_MODEL_2 0x36`, `VERTICES 0x17`, `OLD_VERTICES 0x0A`, `VERTEX_UV 0x18`, `OLD_VERTICES_UV 0x0B`, `VERTEX_NORMAL 0x42`, `VERTEX_COLOUR 0x41`, `FACES 0x35`, `OLD_FACES 0x0C`, `OLD_FACES_1 0x19`, `MATERIAL_INDEX 0x16`, `OLD_MATERIAL_INDEX 0x09`, `FACE_MATERIAL 0x1A`, `FACE_COLOUR 0x43`, `PIVOT 0x15` |
| Materials | `MATERIAL 0x3E`, `MATERIAL_OLD 0x3C`, `MATERIAL_OLDEST 0x04`, `COLOUR_MAP_REF 0x1C`, `INDEX_SHADE_REF 0x1F`, `INDEX_BLEND_REF 0x1E`, `SCREENDOOR_REF 0x20`, `INDEX_FOG_REF 0x3B` |
| Pixelmaps | `PIXELMAP 0x3D`, `OLD_PIXELMAP 0x03`, `PIXELS 0x21`, `ADD_MAP 0x22` |

Pixel types decoded to RGBA8:

| `BR_PMT_*` | Bits | Notes |
|---:|---:|---|
| 3 INDEX_8    | 8  | needs a palette (`.PAL` sibling, or `Winstd.pal` fallback) |
| 4 RGB_555    | 16 | big-endian word, top bit unused |
| 5 RGB_565    | 16 | |
| 6 RGB_888    | 24 | |
| 7 RGBX_888   | 32 | palette format: 256 entries of RGBX |
| 8 RGBA_8888  | 32 | on-disk order A, R, G, B |
| 17 BGR_555   | 16 | byte-swapped RGB_555 |
| 18 RGBA_4444 | 16 | `RRRRGGGGBBBBAAAA`; alpha=0 is valid for additive particles |
| 23 ARGB_8888 | 32 | |
| 30 RGBA_5551 | 16 | |
| 31 ARGB_1555 | 16 | |
| 32 ARGB_4444 | 16 | |

### Things that catch people out

Useful if you port this to another BRender game:

- `FSM_COLOUR` writes 3 bytes (R, G, B). No alpha byte.
- `FID_FACES` (0x35) is 9 bytes per face: `u16 v0, u16 v1, u16 v2, u16 smoothing, u8 flags`. No material index inside the face. Material comes from a separate `FID_FACE_MATERIAL` (0x1A) block with 1-based indices (0 means NULL material).
- `FID_VERTEX_UV` (0x18) is a separate chunk. Its count matches the preceding `FID_VERTICES`. Merge them into one vertex array.
- `FID_PIXELS` (0x21) element bytes are big-endian on disk. The writer byte-swaps each `elem_size`-byte group before emitting. On a little-endian host, swap back when reading 16 or 32-bit pixel types.
- `PIXELMAP.row_bytes` can be larger than `width * bytes_per_pixel` (row padding). Read one row at a time.
- `.PAL` files are ordinary pixelmaps of type `RGBX_888` (7) with `width=256, height=1`. The `PIXELS` payload is 1032 bytes. Each entry on disk is R, G, B, X (X is padding).
- `.MAT` and `.BRM` files write `FILE_INFO.type = 5` (`FILE_TYPE_MATERIAL_OLD`) even when the body uses the current `FID_MATERIAL` (0x3E). Don't branch on `FILE_INFO.type`. Detect format by chunk IDs.

### Using the library

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

Lower level:

```python
from pathlib import Path
from brender.model import parse_model_file
from brender.pixelmap import parse_pixmap_file, parse_palette_file, decode_to_rgba
from brender.material import parse_material_file

models    = parse_model_file(Path("catacomb.dat"))
pixmaps   = parse_pixmap_file(Path("catacomb.pix"))
palette   = parse_palette_file(Path("catacomb.pal"))   # numpy (256, 3) uint8
materials = parse_material_file(Path("catacomb.mat"))

rgba = decode_to_rgba(pixmaps[0], palette)             # numpy (H, W, 4) uint8
```

### Python version

`brender/` uses `from __future__ import annotations`, so modern typing (`int | None`, `list[str]`) stays as strings at runtime. Minimum is Python 3.8, limited by the deps (`pycdlib`, `numpy`, `Pillow`) and Tkinter. On Linux where Tkinter ships separately, install `python3-tk` first.

---

## Credits

The BRender code in this repo is a port of Argonaut's canonical C source, which foone published in 2021. Every chunk ID and struct layout came from reading that source.

Main references:

- [foone/BRender-v1.3.2](https://github.com/foone/BRender-v1.3.2). The 2021 Argonaut source release. Specifically `core/fw/datafile.[ch]`, `core/v1db/v1dbfile.c`, `core/pixelmap/pmfile.c`, `core/pixelmap/pmmem.[ch]`, `inc/pixelmap.h`, `inc/material.h`, `inc/model.h`, `inc/actor.h`.
- [foone/BRender-1997](https://github.com/foone/BRender-1997) and [foone/BRender-v1.1.2](https://github.com/foone/BRender-v1.1.2). Earlier releases, helpful for the legacy `OLD_*` chunks.
- [FFmpeg `libavcodec/brender_pix.c`](https://ffmpeg.org/doxygen/2.0/brender__pix_8c_source.html). Independent PIX decoder that confirms the pixel chunk IDs and big-endian layout.
- [Archiveteam BRender PIX page](http://fileformats.archiveteam.org/wiki/BRender_PIX).
- [Carmageddon Wiki: BRender](https://wiki.cwaboard.co.uk/wiki/BRender), [CWA Board thread on pixelmap types](https://www.cwaboard.co.uk/viewtopic.php?t=19755), [Toshiba-3/BRender-pixelmap-types](https://github.com/Toshiba-3/BRender-pixelmap-types) for sample files across all `BR_PMT_*` types.
- [dethrace-labs/dethrace](https://github.com/dethrace-labs/dethrace). Carmageddon 1 reverse-engineering project. Good real-world case study of a BRender game.

The game:

- [Big Brother (Jan 4, 1999 build) on archive.org](https://archive.org/details/BigBrother19990401). The disc image this tool was written against. Uploaded by `shedtroll1984@gmail.com`.
- [Big Brother writeup at Games That Weren't](https://www.gamesthatwerent.com/2025/03/big-brother/). History of the project.
- *Big Brother* (c) 1996-1999 MediaX. Published with the cooperation of the George Orwell estate.

Runtime deps:

- [pycdlib](https://github.com/clalancette/pycdlib) for ISO9660 reading.
- [NumPy](https://numpy.org/) for pixel math.
- [Pillow](https://python-pillow.org/) for PNG encoding.

License is MIT, see `LICENSE`. The game assets stay under their original copyright and are not redistributed here.
