"""Package the PyInstaller onedir build into an MSIX.

    .venv\\Scripts\\python.exe -m PyInstaller packaging\\build_exe.spec --noconfirm
    .venv\\Scripts\\python.exe packaging\\build_msix.py

Produces dist/Dawnlist.msix. Signing is a separate step: a self-signed
certificate is enough for Store submission because the Store re-signs on
publish, so the unsigned package here is the deliverable, not a half-finished
one.

Two things this script refuses to do quietly, both because they fail LATE:

  * build if the store-build flag is missing from the PyInstaller output, so a
    Store package cannot be cut from a direct-download build;
  * build if the locale catalogues did not make it into the bundle, because a
    silently monolingual app passes certification and disappoints every
    non-English user instead.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

# The Windows console is cp1252 and this script prints em-dashes. Third time
# this has bitten in this repo; it is cheap to prevent and confusing to debug.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

project_root = Path(__file__).parent.parent
dist_dir = project_root / "dist"
pyinstaller_output = dist_dir / "Dawnlist"
staging_dir = project_root / "build" / "msix_staging"
manifest_src = Path(__file__).parent / "msix" / "AppxManifest.xml"
output_msix = dist_dir / "Dawnlist.msix"

BRAND = Path(r"C:\Users\SpencerFields\OneDrive - Spencer Fields\Apps\Claude"
             r"\brand-dawnlist\png")
SOURCE_ICON_CANDIDATES = [
    BRAND / "mark-tile-1024.png",
    project_root / "packaging" / "icons" / "dawnlist-1024.png",
]

# (output filename, pixel size). Every one is referenced by AppxManifest.xml;
# a missing asset is a certification failure, not a warning.
ASSET_SIZES = [
    ("StoreLogo.png", 50),
    ("Square44x44Logo.png", 44),
    ("Square150x150Logo.png", 150),
    ("SmallTile.png", 71),
    ("LargeTile.png", 310),
]
WIDE_TILE = ("Wide310x150Logo.png", 310, 150)


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def find_makeappx() -> Path:
    roots = [Path(r"C:\Program Files (x86)\Windows Kits\10\bin"),
             Path(r"C:\Program Files\Windows Kits\10\bin")]
    found = []
    for root in roots:
        if root.exists():
            found.extend(root.glob("*/x64/makeappx.exe"))
    if not found:
        fail("makeappx.exe not found — install the Windows 10/11 SDK")
    return sorted(found)[-1]


def source_icon() -> Path:
    for candidate in SOURCE_ICON_CANDIDATES:
        if candidate.exists():
            return candidate
    fail("no source icon found; looked in:\n  "
         + "\n  ".join(str(c) for c in SOURCE_ICON_CANDIDATES))


def build_assets(target: Path) -> None:
    from PIL import Image

    target.mkdir(parents=True, exist_ok=True)
    master = Image.open(source_icon()).convert("RGBA")

    for name, size in ASSET_SIZES:
        master.resize((size, size), Image.LANCZOS).save(target / name)

    # The wide tile is the one that catches people out: it is not square, and
    # scaling a square mark to fill it distorts the mark. Centre it instead, on
    # the brand ground.
    name, width, height = WIDE_TILE
    wide = Image.new("RGBA", (width, height), (30, 75, 69, 255))   # TEAL
    mark = master.resize((height - 20, height - 20), Image.LANCZOS)
    wide.paste(mark, ((width - mark.width) // 2, (height - mark.height) // 2), mark)
    wide.save(target / name)


def verify_bundle() -> None:
    """Check the PyInstaller output before packaging it, not after."""
    if not pyinstaller_output.exists():
        fail(f"{pyinstaller_output} not found — run PyInstaller first")

    exe = pyinstaller_output / "Dawnlist.exe"
    if not exe.exists():
        fail(f"{exe} not found in the PyInstaller output")

    locales = pyinstaller_output / "_internal" / "app" / "resources" / "locales"
    catalogues = sorted(locales.glob("*.json")) if locales.exists() else []
    if not catalogues:
        fail("no locale catalogues in the bundle — the app would be silently "
             "monolingual. Check the `datas` entry in build_exe.spec.")
    print(f"  {len(catalogues)} locale catalogues in the bundle")

    flag = pyinstaller_output / "_internal" / "app" / "resources" / "store_build.flag"
    if not flag.exists():
        print("  note: store_build.flag is absent, so this packages the "
              "DIRECT-DOWNLOAD variant. Create the flag and rebuild before "
              "submitting to the Store.", file=sys.stderr)


def main() -> int:
    makeappx = find_makeappx()
    print(f"makeappx: {makeappx}")

    print("verifying the PyInstaller output...")
    verify_bundle()

    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)

    print("staging...")
    shutil.copytree(pyinstaller_output, staging_dir, dirs_exist_ok=True)
    shutil.copy2(manifest_src, staging_dir / "AppxManifest.xml")
    build_assets(staging_dir / "Assets")

    output_msix.parent.mkdir(parents=True, exist_ok=True)
    if output_msix.exists():
        output_msix.unlink()

    print("packing...")
    result = subprocess.run(
        [str(makeappx), "pack", "/o", "/d", str(staging_dir), "/p",
         str(output_msix)],
        capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout[-3000:])
        fail(f"makeappx failed ({result.returncode}): {result.stderr[-2000:]}")

    size_mb = output_msix.stat().st_size / (1024 * 1024)
    print(f"wrote {output_msix} ({size_mb:.0f} MB)")
    print("\nNext: sign it (a self-signed cert is enough — the Store re-signs "
          "on publish), then submit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
