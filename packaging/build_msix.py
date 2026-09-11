"""Package the PyInstaller onedir build into an MSIX.

    .venv\\Scripts\\python.exe -m PyInstaller packaging\\build_exe.spec --noconfirm
    .venv\\Scripts\\python.exe packaging\\build_msix.py

Produces dist/Dawnlist.msix. Signing is a separate step: a self-signed
certificate is enough for Store submission because the Store re-signs on
publish, so the unsigned package here is the deliverable, not a half-finished
one.

Two things this script refuses to do, both because they otherwise fail LATE —
after certification, in front of a paying customer:

  * build unless the bundle carries the STORE variant flag and only that one,
    so a Store package cannot be cut from a direct-download build that would
    demand a licence key the buyer was never issued;
  * build unless every locale catalogue the app declares in app/i18n.py is in
    the bundle. Non-empty is not the bar: a partial set certifies cleanly and
    then falls back to English for the locales it is missing.

Both guards read the BUNDLE, not the source tree. The source tree is where the
flag was set; the bundle is what ships, and the two diverge whenever someone
sets a variant and forgets that the flag is read at build time.
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

# The declared locale list and the variant flag names both live in the app, so
# that this script cannot drift from what the app actually ships.
sys.path.insert(0, str(project_root))
from app.i18n import LOCALE_CODES  # noqa: E402
from app.core.build_variant import FLAGS as VARIANT_FLAGS  # noqa: E402
from app.version import msix_version  # noqa: E402

dist_dir = project_root / "dist"
pyinstaller_output = dist_dir / "Dawnlist"
staging_dir = project_root / "build" / "msix_staging"
manifest_src = Path(__file__).parent / "msix" / "AppxManifest.xml"
output_msix = dist_dir / "Dawnlist.msix"

# THE COMMITTED MASTER, AND NOTHING ELSE.
#
# A second candidate under one developer's OneDrive used to follow this one.
# What that bought was a package whose tiles depend on the machine that built
# it: a stale export in that folder is preferred to no file at all, the
# artefact records nothing about which it took, and two machines building the
# same commit produce different tiles. A build that can reach outside the
# repository is not reproducible, and the divergence is silent.
#
# If this file is missing, export it from the brand pack and COMMIT it.
SOURCE_ICON = project_root / "packaging" / "icons" / "dawnlist-1024.png"

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
    if not SOURCE_ICON.exists():
        fail(f"{SOURCE_ICON} is missing. Every Store tile is resized from it, "
             f"and there is no fallback on purpose — export the 1024px master "
             f"from the brand pack and commit it.")
    return SOURCE_ICON


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

    bundled_resources = pyinstaller_output / "_internal" / "app" / "resources"

    locales = bundled_resources / "locales"
    catalogues = sorted(locales.glob("*.json")) if locales.exists() else []
    if not catalogues:
        fail("no locale catalogues in the bundle — the app would be silently "
             "monolingual. Check the `datas` entry in build_exe.spec.")

    # Non-empty is not the bar. A partial set passes certification and then
    # falls back to English for every locale it is missing, which nobody
    # notices until a user in that locale complains. Compare against the
    # declared list, not against zero.
    expected = set(LOCALE_CODES)
    bundled = {path.stem for path in catalogues}
    missing = sorted(expected - bundled)
    if missing:
        fail(f"{len(bundled)} of {len(expected)} locale catalogues in the "
             f"bundle — missing {', '.join(missing)}. The app declares these "
             f"in app/i18n.py and would silently fall back to English.")
    print(f"  {len(bundled)} of {len(expected)} locale catalogues in the bundle")

    # The variant is read from the BUNDLE, not the source tree: the source
    # tree is what the flag was set in, the bundle is what ships. Refusing
    # here is the whole point — an MSIX is a Store artefact, and one cut from
    # a direct-download build would ask a paying customer for a licence key
    # they were never given.
    present = [name for name, flag in VARIANT_FLAGS.items()
               if (bundled_resources / flag).exists()]
    if present == ["store"]:
        print("  variant: store")
    elif not present:
        fail("no build-variant flag in the bundle. Run "
             "`python tools/set_build_variant.py store` and rebuild — the "
             "flag is read at BUILD time, so setting it now is not enough.")
    elif len(present) > 1:
        fail(f"ambiguous build variant — {', '.join(present)} are all in the "
             f"bundle. Run `python tools/set_build_variant.py store`, which "
             f"deletes the others, and rebuild.")
    else:
        fail(f"this is the {present[0].upper()} build, not the store build. "
             f"An MSIX cut from it would gate a Store purchase behind a "
             f"licence key. Run `python tools/set_build_variant.py store` "
             f"and rebuild.")


def stamp_version(manifest: Path) -> None:
    """Write the app's version into the staged manifest, and prove it landed.

    The manifest is XML, so nothing pointed at it when the version moved: on
    2026-09-09 the app, the spec and the direct builder all went to 1.1.0 and
    this file stayed at 1.0.2.0 — the version ALREADY PUBLISHED. Partner
    Center refuses a package whose version is not higher than the live one, so
    the package fixing a release-blocking defect would have been rejected at
    upload, days after the defect went live.

    Rewritten at package time rather than trusted, and then READ BACK: a
    regex that matches nothing is silent, and silence here looks exactly like
    success.
    """
    import re

    wanted = msix_version()
    text = manifest.read_text(encoding="utf-8")
    stamped, count = re.subn(r'(<Identity[^>]*?Version=")[^"]*(")',
                             rf'\g<1>{wanted}\g<2>', text, count=1,
                             flags=re.DOTALL)
    if count != 1:
        sys.exit("could not find the Identity Version in AppxManifest.xml — "
                 "refusing to ship a package whose version was not set.")
    manifest.write_text(stamped, encoding="utf-8")

    found = re.search(r'<Identity[^>]*?Version="([^"]*)"', stamped,
                      flags=re.DOTALL)
    if not found or found.group(1) != wanted:
        sys.exit(f"manifest version is {found and found.group(1)!r}, wanted "
                 f"{wanted!r}")
    print(f"  package version: {wanted}")


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
    stamp_version(staging_dir / "AppxManifest.xml")
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
