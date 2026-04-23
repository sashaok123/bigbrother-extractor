"""Big Brother asset extraction GUI.

One-click pipeline:
    BIN/CUE -> ISO -> file tree -> BRender assets (OBJ/MTL/PNG) -> mesh_texture_map.json

Output layout under the chosen folder:
    Extracted/
        disc.iso
        ISO/                  raw ISO9660 tree
        Mesh/*.obj + *.mtl
        Texture/*.png
        mesh_texture_map.json
        extraction.log
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
import threading
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


def _pip_install(pkg: str) -> bool:
    tries = [
        [sys.executable, "-m", "pip", "install", "--upgrade", pkg],
        [sys.executable, "-m", "pip", "install", "--user", "--upgrade", pkg],
    ]
    for cmd in tries:
        try:
            subprocess.check_call(cmd, stdout=sys.stdout, stderr=sys.stderr)
            return True
        except subprocess.CalledProcessError:
            continue
    return False


def _ensure_deps() -> None:
    py = sys.version_info
    print(f"Python {py.major}.{py.minor}.{py.micro} ({platform.machine()}) at {sys.executable}")
    try:
        subprocess.call(
            [sys.executable, "-m", "pip", "install", "--upgrade", "pip"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass
    required = [("pycdlib", "pycdlib"), ("numpy", "numpy"), ("pillow", "PIL")]
    failed: list[str] = []
    for pkg, imp in required:
        try:
            __import__(imp)
            print(f"  OK  {pkg}")
        except ImportError:
            print(f"  ... installing {pkg}")
            if _pip_install(pkg):
                try:
                    __import__(imp)
                    print(f"  OK  {pkg} installed")
                except ImportError:
                    failed.append(pkg)
            else:
                failed.append(pkg)
    if failed:
        root = tk.Tk(); root.withdraw()
        messagebox.showerror(
            "Dependency install failed",
            f"Failed to install: {', '.join(failed)}\n\n"
            f"Try manually:\n"
            f'   "{sys.executable}" -m pip install {" ".join(failed)}',
        )
        sys.exit(1)


_ensure_deps()

from brender import iso as brender_iso
from brender import extract as brender_extract
from brender import categorize_basic


HERE = Path(__file__).resolve().parent


class ExtractApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("Big Brother — Extract Models & Textures")
        root.geometry("760x560")

        self.bin_var = tk.StringVar(value=self._autodetect_bin())
        self.out_var = tk.StringVar(value=str(HERE / "Extracted"))
        self.running = False
        self.buttons: list[tk.Button] = []

        self._build_ui()

    def _autodetect_bin(self) -> str:
        for p in sorted(HERE.glob("*.BIN")) + sorted(HERE.glob("*.bin")):
            return str(p)
        return ""

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}

        top = ttk.Frame(self.root)
        top.pack(fill=tk.X, **pad)

        ttk.Label(top, text="BIN/CUE file:", width=18, anchor="e").grid(row=0, column=0, sticky="e")
        ttk.Entry(top, textvariable=self.bin_var, width=70).grid(row=0, column=1, sticky="we", padx=4)
        ttk.Button(top, text="Browse...", command=self._pick_bin).grid(row=0, column=2)

        ttk.Label(top, text="Output folder:", width=18, anchor="e").grid(row=1, column=0, sticky="e")
        ttk.Entry(top, textvariable=self.out_var, width=70).grid(row=1, column=1, sticky="we", padx=4)
        ttk.Button(top, text="Browse...", command=self._pick_out).grid(row=1, column=2)

        top.columnconfigure(1, weight=1)

        # Step buttons
        btns = ttk.LabelFrame(self.root, text="Pipeline")
        btns.pack(fill=tk.X, **pad)
        steps = [
            ("1. BIN -> ISO",                self._step_bin_to_iso),
            ("2. Unpack ISO tree",           self._step_unpack_iso),
            ("3. Extract BRender assets",    self._step_extract_brender),
            ("4. Sort into basic categories", self._step_basic_categorize),
            ("Run all (1 -> 4)",             self._step_run_all),
        ]
        for i, (label, fn) in enumerate(steps):
            b = tk.Button(btns, text=label, width=32, command=lambda f=fn: self._spawn(f))
            b.grid(row=i, column=0, padx=6, pady=3, sticky="w")
            self.buttons.append(b)

        # Log
        logfrm = ttk.LabelFrame(self.root, text="Log")
        logfrm.pack(fill=tk.BOTH, expand=True, **pad)
        self.log = tk.Text(logfrm, wrap="word", height=18)
        self.log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(logfrm, command=self.log.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.log.config(yscrollcommand=sb.set)

        self.status = tk.StringVar(value="Ready")
        ttk.Label(self.root, textvariable=self.status, anchor="w", relief="sunken").pack(fill=tk.X, side=tk.BOTTOM)

    def _pick_bin(self) -> None:
        p = filedialog.askopenfilename(
            title="Choose the BIN or CUE file",
            filetypes=[("BIN/CUE", "*.BIN;*.bin;*.cue;*.CUE"), ("All files", "*.*")],
        )
        if p:
            self.bin_var.set(p)

    def _pick_out(self) -> None:
        p = filedialog.askdirectory(title="Choose output folder")
        if p:
            self.out_var.set(p)

    def logln(self, msg: str) -> None:
        self.log.insert(tk.END, msg.rstrip() + "\n")
        self.log.see(tk.END)
        self.root.update_idletasks()

    def set_status(self, msg: str) -> None:
        self.status.set(msg)
        self.root.update_idletasks()

    def _spawn(self, fn) -> None:
        if self.running:
            return
        self.running = True
        for b in self.buttons:
            b.config(state="disabled")

        def wrapper() -> None:
            try:
                fn()
            except Exception as exc:
                self.logln(f"ERROR: {exc}")
                self.logln(traceback.format_exc())
                self.root.after(0, lambda: messagebox.showerror("Step failed", str(exc)))
            finally:
                self.running = False
                self.set_status("Ready")
                for b in self.buttons:
                    self.root.after(0, lambda bb=b: bb.config(state="normal"))

        threading.Thread(target=wrapper, daemon=True).start()

    # --------------------------------------------------------- pipeline steps
    def _paths(self) -> tuple[Path, Path]:
        bin_path = Path(self.bin_var.get())
        if bin_path.suffix.lower() == ".cue":
            # Expect matching .BIN in the same folder
            bin_candidate = bin_path.with_suffix(".BIN")
            if not bin_candidate.exists():
                bin_candidate = bin_path.with_suffix(".bin")
            bin_path = bin_candidate
        out = Path(self.out_var.get())
        out.mkdir(parents=True, exist_ok=True)
        return bin_path, out

    def _step_bin_to_iso(self) -> None:
        bin_path, out = self._paths()
        iso = out / "disc.iso"
        if not bin_path.exists():
            raise FileNotFoundError(f"BIN not found: {bin_path}")
        if iso.exists() and iso.stat().st_size > 0:
            self.logln(f"[1/4] {iso.name} already exists ({iso.stat().st_size:,} B) - skipping")
            return
        self.set_status("Converting BIN -> ISO...")
        self.logln(f"[1/4] Ripping {bin_path.name} -> {iso}")
        n = brender_iso.rip(bin_path, iso)
        self.logln(f"      sectors read: {n}, iso size: {iso.stat().st_size:,} B")

    def _step_unpack_iso(self) -> None:
        _, out = self._paths()
        iso = out / "disc.iso"
        iso_dir = out / "ISO"
        if not iso.exists():
            raise FileNotFoundError(f"Missing {iso}. Run step 1 first.")
        self.set_status("Unpacking ISO...")
        self.logln(f"[2/4] Extracting ISO tree into {iso_dir}")
        iso_dir.mkdir(parents=True, exist_ok=True)
        n = brender_iso.extract_tree(iso, iso_dir)
        self.logln(f"      files extracted: {n}")
        renamed = brender_iso.strip_version(iso_dir)
        self.logln(f"      stripped ;1 version from: {renamed}")

    def _step_extract_brender(self) -> None:
        _, out = self._paths()
        iso_dir = out / "ISO"
        if not iso_dir.is_dir():
            raise FileNotFoundError(f"Missing {iso_dir}. Run step 2 first.")
        self.set_status("Extracting BRender assets...")
        self.logln(f"[3/4] Parsing .dat / .pix / .pal / .mat ...")
        brender_extract.run(iso_dir, out)
        logp = out / "extraction.log"
        if logp.exists():
            self.logln("      --- extraction.log ---")
            for line in logp.read_text(encoding="utf-8").splitlines():
                self.logln(f"      {line}")

    def _step_basic_categorize(self) -> None:
        _, out = self._paths()
        iso_dir = out / "ISO"
        mesh = out / "Mesh"
        tex = out / "Texture"
        tmap = out / "mesh_texture_map.json"
        if not all(p.exists() for p in (iso_dir, mesh, tex, tmap)):
            raise FileNotFoundError("Run steps 2 and 3 before categorizing.")
        self.set_status("Folder categorization...")
        self.logln(f"[4/4] Sorting into Characters / Items / Props / Environment/<level>")
        summary = categorize_basic.run(iso_dir, mesh, tex, tmap, out / "Categorized")
        for label, n_meshes, n_tex in summary:
            self.logln(f"      {label:30s}  {n_meshes:5d} meshes, {n_tex:5d} textures")
        self.logln("      basic categories written under Extracted/Categorized/")

    def _step_run_all(self) -> None:
        self._step_bin_to_iso()
        self._step_unpack_iso()
        self._step_extract_brender()
        self._step_basic_categorize()
        self.logln("=== All steps complete ===")
        self.set_status("Done")


def main() -> None:
    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", 1.2)
    except Exception:
        pass
    ExtractApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
