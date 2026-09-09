"""Package the PyInstaller onedir build as the Windows direct download.

    .venv\\Scripts\\python.exe tools\\set_build_variant.py direct
    .venv\\Scripts\\python.exe -m PyInstaller packaging\\build_exe.spec --noconfirm
    .venv\\Scripts\\python.exe packaging\\build_windows_direct.py

Produces dist/Dawnlist-windows-<version>.zip and a .sha256 beside it.

WHY THIS FILE HAD TO EXIST
--------------------------
Until it did, Windows had exactly one packager — `build_msix.py` — and the CI
workflow uploaded only `dist/*.msix`, `dist/*.dmg` and `dist/*.pkg`. So the
Microsoft Store had an artefact, macOS had one, and the WINDOWS DIRECT
DOWNLOAD had none at all. Nothing failed; the build was green and the download
simply did not exist. "Direct download and the stores, shipped together" was
not achievable from the repository as it stood.

A ZIP rather than an installer, deliberately. The build is already onedir
(see build_exe.spec: onefile self-extraction is an antivirus heuristic), the
app writes its state to the user profile rather than to Program Files, and it
needs no registry keys, no services and no elevation. An installer would add a
code-signing surface, an uninstaller to maintain and an elevation prompt, and
would buy nothing a folder does not already give.

THE GUARDS, AND WHY EACH ONE REFUSES RATHER THAN WARNS
------------------------------------------------------
All three failures below produce a package that looks completely normal and
goes wrong later, in front of somebody who paid:

  * WRONG VARIANT. A direct-download ZIP cut from a `store` build expects a
    Microsoft Store entitlement the buyer does not have, so the app they just
    paid Paddle for never unlocks. Read from the BUNDLE, not the source tree:
    the flag is baked in at PyInstaller time, so setting it afterwards changes
    nothing about what is in `dist/`.
  * INCOMPLETE LOCALES. A partial catalogue set falls back to English silently
    for every locale it is missing. Non-empty is not the bar.
  * UNSIGNED BINARY. This is the one specific to this path. The Store re-signs
    on publish, so an unsigned MSIX is fine; a ZIP from a website is run
    exactly as shipped, and an unsigned executable gets a SmartScreen wall.
    Worse, publishing a SHA-256 for an unsigned build teaches buyers to verify
    a hash that proves nothing about origin. Signing must happen BEFORE the
    checksum, or the published hash belongs to a file nobody will download —
    the same ordering mistake the sibling project made.

`--allow-unsigned` exists for local testing and says so in the output. CI does
not pass it.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import zipfile
from pathlib import Path

# The Windows console is cp1252 and this script prints em dashes. Third file in
# this repository to need it; cheap to prevent, confusing to debug.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

project_root = Path(__file__).parent.parent

# The declared locale list and the variant flag names both come from the app,
# so this script cannot drift from what the app actually ships.
sys.path.insert(0, str(project_root))
from app.i18n import LOCALE_CODES  # noqa: E402
from app.core.build_variant import FLAGS as VARIANT_FLAGS  # noqa: E402

dist_dir = project_root / "dist"
pyinstaller_output = dist_dir / "Dawnlist"
EXE_NAME = "Dawnlist.exe"


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def require_windows() -> None:
    """Refuse off Windows rather than emitting something unusable.

    PyInstaller does not cross-compile, so `dist/Dawnlist` on a Linux or macOS
    runner is not a Windows build even when the directory exists. Zipping it
    would produce a file named like the download and containing an ELF binary.
    """
    if not sys.platform.startswith("win"):
        fail(f"this builds the Windows package and cannot run on "
             f"{sys.platform}. PyInstaller bundles the interpreter of the "
             f"machine it runs on; use the windows-latest CI job.")


def verify_bundle() -> None:
    """Check the PyInstaller output before packaging it, not after."""
    if not pyinstaller_output.exists():
        fail(f"{pyinstaller_output} not found — run PyInstaller first")

    exe = pyinstaller_output / EXE_NAME
    if not exe.exists():
        fail(f"{exe} not found in the PyInstaller output")

    bundled_resources = pyinstaller_output / "_internal" / "app" / "resources"

    locales = bundled_resources / "locales"
    catalogues = sorted(locales.glob("*.json")) if locales.exists() else []
    if not catalogues:
        fail("no locale catalogues in the bundle — the app would be silently "
             "monolingual. Check the `datas` entry in build_exe.spec.")

    expected = set(LOCALE_CODES)
    bundled = {path.stem for path in catalogues}
    missing = sorted(expected - bundled)
    if missing:
        fail(f"{len(bundled)} of {len(expected)} locale catalogues in the "
             f"bundle — missing {', '.join(missing)}. The app declares these "
             f"in app/i18n.py and would silently fall back to English.")
    print(f"  {len(bundled)} of {len(expected)} locale catalogues in the bundle")

    present = [name for name, flag in VARIANT_FLAGS.items()
               if (bundled_resources / flag).exists()]
    if present == ["direct"]:
        print("  variant: direct")
    elif not present:
        fail("no build-variant flag in the bundle. Run "
             "`python tools/set_build_variant.py direct` and rebuild — the "
             "flag is read at BUILD time, so setting it now is not enough.")
    elif len(present) > 1:
        fail(f"ambiguous build variant — {', '.join(present)} are all in the "
             f"bundle. Run `python tools/set_build_variant.py direct`, which "
             f"deletes the others, and rebuild.")
    else:
        fail(f"this is the {present[0].upper()} build, not the direct-download "
             f"build. A ZIP cut from it would expect a store entitlement the "
             f"buyer does not have, so the app they paid for would never "
             f"unlock. Run `python tools/set_build_variant.py direct` and "
             f"rebuild.")


def signature_state(exe: Path) -> tuple[str, str]:
    """(status, signer) from Authenticode, as PowerShell reports it.

    Returns the status verbatim rather than a boolean, because the three
    failure modes need different answers: `NotSigned` means nobody signed it,
    `UnknownError` usually means the certificate chain is not trusted on this
    machine, and `HashMismatch` means the file was modified after signing.
    Collapsing them to False would hide the last one, which is the serious one.
    """
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         f"$s = Get-AuthenticodeSignature -LiteralPath '{exe}'; "
         f"Write-Output $s.Status; Write-Output $s.SignerCertificate.Subject"],
        capture_output=True, text=True)
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    status = lines[0] if lines else "Unknown"
    signer = lines[1] if len(lines) > 1 else ""
    return status, signer


def require_signed(allow_unsigned: bool) -> None:
    exe = pyinstaller_output / EXE_NAME
    status, signer = signature_state(exe)

    if status == "Valid":
        print(f"  signature: valid — {signer or 'signer not reported'}")
        return

    if allow_unsigned:
        print(f"  signature: {status}  [--allow-unsigned given]")
        print("  NOT FIT TO PUBLISH. The checksum written below identifies an "
              "unsigned binary; sign first, then re-run without the flag.")
        return

    fail(f"{EXE_NAME} signature is '{status}', not 'Valid'. A ZIP served from "
         f"the website is run exactly as shipped, so an unsigned executable "
         f"means a SmartScreen wall for every buyer, and a published SHA-256 "
         f"for it proves nothing about where the file came from. Sign the exe "
         f"inside dist/Dawnlist/ FIRST, then run this again. Use "
         f"--allow-unsigned only for a local smoke test.")


def version() -> str:
    """The marketing version, from the same env var the spec reads.

    Kept identical to build_exe.spec's default so the ZIP name and the file
    version can never disagree about which release this is.
    """
    from app.version import marketing_version

    return marketing_version()


def write_zip(target: Path) -> None:
    """Zip the onedir output with `Dawnlist/` as the top-level folder.

    The top-level folder is not cosmetic: without it, a buyer who extracts to
    their Downloads folder gets several hundred loose files, and the one they
    need to double-click is somewhere in the middle of them.
    """
    if target.exists():
        target.unlink()

    files = sorted(p for p in pyinstaller_output.rglob("*") if p.is_file())
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=9) as archive:
        for path in files:
            archive.write(path, Path("Dawnlist") / path.relative_to(
                pyinstaller_output))
    print(f"  {len(files)} files")


def write_checksum(target: Path) -> Path:
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    checksum = target.with_suffix(target.suffix + ".sha256")
    # The two-space form `sha256sum -c` expects, so a buyer on any platform can
    # verify it without being told how.
    checksum.write_text(f"{digest}  {target.name}\n", encoding="utf-8")
    print(f"  sha256 {digest}")
    return checksum


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--allow-unsigned", action="store_true",
                    help="package an unsigned build for local testing. The "
                         "result must not be published.")
    ap.add_argument("--verify-only", action="store_true",
                    help="run the guards and stop, without writing anything")
    args = ap.parse_args()

    require_windows()

    print("verifying the PyInstaller output...")
    verify_bundle()
    require_signed(args.allow_unsigned)

    if args.verify_only:
        print("\nverify-only: nothing written.")
        return 0

    target = dist_dir / f"Dawnlist-windows-{version()}.zip"
    print(f"packing {target.name}...")
    write_zip(target)
    checksum = write_checksum(target)

    size_mb = target.stat().st_size / (1024 * 1024)
    print(f"\nwrote {target} ({size_mb:.0f} MB)")
    print(f"wrote {checksum}")
    print("\nPublish both. The checksum is only meaningful alongside the "
          "signature — it proves the download is intact, the signature proves "
          "who made it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
