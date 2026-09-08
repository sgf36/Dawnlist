"""Build, sign and package Dawnlist for macOS.

    python packaging/build_macos.py --variant mas      # Mac App Store .pkg
    python packaging/build_macos.py --variant direct   # notarised .dmg

THIS SCRIPT ONLY RUNS ON macOS, and it refuses rather than pretending.

PyInstaller does not cross-compile: it bundles the interpreter and the
extension modules of the machine it runs on, so there is no way to produce a
Mac .app from Windows. Neither `codesign`, `productbuild`, `hdiutil` nor
`notarytool` exists off Darwin either. A script that limped along and emitted
something unusable would be worse than one that stops, because the artefact
would look finished.

WHAT THIS SHARES WITH THE WINDOWS PATH, DELIBERATELY
----------------------------------------------------
The same two bundle guards as `build_msix.py`, for the same reasons and with
the same failure behaviour: the wrong build variant and a short locale set
both certify cleanly and fail in front of a paying customer. They are
duplicated as behaviour, not as code, because the two packagers inspect
different directory layouts — `_internal/` on Windows, `Contents/Resources/`
or `Contents/Frameworks/` in an .app — and a shared helper that guessed
between them would be the thing that breaks.

THE TWO VARIANTS ARE NOT THE SAME BUILD
---------------------------------------
  * `mas` — App Sandbox, signed with the 3rd Party Mac Developer certificates,
    packaged as a .pkg, uploaded to App Store Connect. Reviewed by Apple, so
    it is NOT notarised separately.
  * `direct` — hardened runtime, signed with Developer ID, packaged as a .dmg,
    and notarised. Notarisation is what stops Gatekeeper refusing to open it.

Signing the .app is not enough on either path: every nested dylib and
framework must be signed too, innermost first, or the outer signature is
invalid the moment it is checked.
"""
from __future__ import annotations

import argparse
import platform
import plistlib
import subprocess
import sys
from pathlib import Path

# The Windows console is cp1252 and this script's refusal message contains an
# em dash. Without this the one message a Windows user will ever see from this
# file prints mojibake. Same guard as build_msix.py, for the same reason.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

project_root = Path(__file__).parent.parent
dist_dir = project_root / "dist"
app_path = dist_dir / "Dawnlist.app"

ENTITLEMENTS = {
    "direct": project_root / "packaging" / "macos" / "direct.entitlements",
    "mas": project_root / "packaging" / "macos" / "mas.entitlements",
}

#: The variant each package target requires INSIDE the bundle. `mas` and
#: `direct` are the two macOS variants; `store` is the Windows one and is not
#: packageable here. The flag names themselves come from the app, via
#: VARIANT_FLAGS, so this cannot drift from what the app reads.


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("  $ " + " ".join(str(c) for c in cmd))
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)


def require_macos() -> None:
    if platform.system() != "Darwin":
        fail(
            "this builds a macOS application and must run on macOS.\n"
            f"       Detected: {platform.system()} {platform.release()}.\n"
            "\n"
            "       PyInstaller bundles the interpreter and extension modules of\n"
            "       the machine it runs on, so a Mac .app cannot be produced from\n"
            "       Windows or Linux — and codesign, productbuild, hdiutil and\n"
            "       notarytool do not exist here either.\n"
            "\n"
            "       Everything this script needs is already committed. On a Mac\n"
            "       with Xcode command line tools and the signing identities\n"
            "       installed, clone the repository and run this same command."
        )


def find_resources(app: Path) -> Path:
    """Where PyInstaller put `app/resources` inside the bundle.

    It has moved between PyInstaller versions — Resources/ in some, the
    Frameworks/ tree in others — so this looks rather than assumes. Guessing
    wrong would make the guards below silently pass on an empty directory,
    which is the failure they exist to prevent.
    """
    for candidate in app.rglob("app/resources"):
        if candidate.is_dir():
            return candidate
    fail(f"app/resources not found anywhere inside {app.name} — the bundle is "
         f"not laid out as expected and the guards below cannot be trusted")


def verify_bundle(app: Path, variant: str) -> None:
    if not app.exists():
        fail(f"{app} not found. Run PyInstaller first.")

    resources = find_resources(app)

    locales = resources / "locales"
    bundled = {p.stem for p in locales.glob("*.json")} if locales.exists() else set()
    expected = set(LOCALE_CODES)
    missing = sorted(expected - bundled)
    if missing:
        fail(f"{len(bundled)} of {len(expected)} locale catalogues in the "
             f"bundle — missing {', '.join(missing)}. The app declares these "
             f"in app/i18n.py and would silently fall back to English.")
    print(f"  {len(bundled)} of {len(expected)} locale catalogues in the bundle")

    present = [name for name, flag in VARIANT_FLAGS.items()
               if (resources / flag).exists()]
    want = variant
    if present == [want]:
        print(f"  variant: {want}")
    elif not present:
        fail(f"no build-variant flag in the bundle. Run "
             f"`python tools/set_build_variant.py {want}` and rebuild — the "
             f"flag is read at BUILD time, so setting it now is not enough.")
    elif len(present) > 1:
        fail(f"ambiguous build variant — {', '.join(present)} are all in the "
             f"bundle. Run `python tools/set_build_variant.py {want}`, which "
             f"deletes the others, and rebuild.")
    else:
        fail(f"this is the {present[0].upper()} build, but you asked to "
             f"package {want.upper()}. Run "
             f"`python tools/set_build_variant.py {want}` and rebuild.")


def check_plist(app: Path) -> None:
    """The keys whose absence is found by App Store Connect, not by a build."""
    plist_path = app / "Contents" / "Info.plist"
    if not plist_path.exists():
        fail(f"{plist_path} missing — this is not a valid .app")
    with plist_path.open("rb") as fh:
        info = plistlib.load(fh)
    for key in ("CFBundleShortVersionString", "CFBundleVersion",
                "CFBundleIdentifier", "LSApplicationCategoryType"):
        if not info.get(key):
            fail(f"Info.plist is missing {key}. The upload would be rejected "
                 f"after the build appeared to succeed.")
    print(f"  version {info['CFBundleShortVersionString']} "
          f"(build {info['CFBundleVersion']}), id {info['CFBundleIdentifier']}")


def sign(app: Path, variant: str, identity: str) -> None:
    """Sign every nested binary, innermost first, then the bundle itself.

    Signing only the .app leaves an invalid signature: macOS verifies the
    nested Mach-O files too, and one unsigned dylib inside Frameworks/
    invalidates the outer seal at the moment Gatekeeper checks it — which is
    on the user's machine, not here.
    """
    entitlements = ENTITLEMENTS[variant]
    if not entitlements.exists():
        fail(f"{entitlements} not found")

    inner = sorted(
        (p for p in app.rglob("*")
         if p.is_file() and not p.is_symlink()
         and (p.suffix in {".dylib", ".so"} or ".framework/" in str(p))),
        key=lambda p: len(p.parts), reverse=True,      # deepest first
    )
    print(f"  signing {len(inner)} nested binaries")
    for target in inner:
        res = run(["codesign", "--force", "--timestamp", "--options", "runtime",
                   "--sign", identity, target])
        if res.returncode != 0:
            fail(f"codesign failed on {target}: {res.stderr.strip()[:300]}")

    res = run(["codesign", "--force", "--timestamp", "--options", "runtime",
               "--entitlements", entitlements, "--sign", identity, app])
    if res.returncode != 0:
        fail(f"codesign failed on the bundle: {res.stderr.strip()[:400]}")

    # Verify rather than trust the exit code above.
    res = run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", app])
    if res.returncode != 0:
        fail(f"the signature did not verify: {res.stderr.strip()[:400]}")
    print("  signature verifies")


def package_direct(app: Path, dmg: Path) -> None:
    if dmg.exists():
        dmg.unlink()
    res = run(["hdiutil", "create", "-volname", "Dawnlist", "-srcfolder", app,
               "-ov", "-format", "UDZO", dmg])
    if res.returncode != 0:
        fail(f"hdiutil failed: {res.stderr.strip()[:300]}")
    print(f"  wrote {dmg}")


def package_mas(app: Path, pkg: Path, installer_identity: str) -> None:
    if pkg.exists():
        pkg.unlink()
    res = run(["productbuild", "--component", app, "/Applications",
               "--sign", installer_identity, pkg])
    if res.returncode != 0:
        fail(f"productbuild failed: {res.stderr.strip()[:300]}")
    print(f"  wrote {pkg}")


def notarise(artefact: Path, profile: str) -> None:
    """Submit and wait, then staple.

    Stapling matters: without it the app needs to reach Apple on first launch,
    so a user opening it offline sees Gatekeeper refuse a perfectly notarised
    build.
    """
    res = run(["xcrun", "notarytool", "submit", artefact,
               "--keychain-profile", profile, "--wait"])
    print(res.stdout[-1500:])
    if res.returncode != 0:
        fail(f"notarisation failed: {res.stderr.strip()[:400]}")
    if "status: Accepted" not in res.stdout:
        # --wait exits 0 for an Invalid submission too, so the exit code alone
        # is not evidence that it passed.
        fail("notarytool did not report 'status: Accepted'. Read the log with "
             "`xcrun notarytool log <id> --keychain-profile " + profile + "`")
    res = run(["xcrun", "stapler", "staple", artefact])
    if res.returncode != 0:
        fail(f"stapling failed: {res.stderr.strip()[:300]}")
    print("  notarised and stapled")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--variant", required=True, choices=["direct", "mas"])
    ap.add_argument("--identity", help="codesign identity; defaults to the "
                                       "variant's usual certificate name")
    ap.add_argument("--installer-identity",
                    help="3rd Party Mac Developer Installer identity (mas only)")
    ap.add_argument("--notary-profile", default="dawnlist",
                    help="notarytool keychain profile (direct only)")
    ap.add_argument("--skip-notarise", action="store_true")
    args = ap.parse_args()

    require_macos()

    identity = args.identity or (
        "Developer ID Application" if args.variant == "direct"
        else "3rd Party Mac Developer Application")

    print("verifying the PyInstaller output...")
    verify_bundle(app_path, args.variant)
    check_plist(app_path)

    print("signing...")
    sign(app_path, args.variant, identity)

    if args.variant == "direct":
        dmg = dist_dir / "Dawnlist.dmg"
        print("packaging...")
        package_direct(app_path, dmg)
        if args.skip_notarise:
            print("\nSKIPPED notarisation. Gatekeeper will refuse this build "
                  "on any machine but this one.")
        else:
            print("notarising...")
            notarise(dmg, args.notary_profile)
        print(f"\nDone: {dmg}")
    else:
        installer = args.installer_identity or "3rd Party Mac Developer Installer"
        pkg = dist_dir / "Dawnlist.pkg"
        print("packaging...")
        package_mas(app_path, pkg, installer)
        print(f"\nDone: {pkg}")
        print("Upload with:")
        print(f"  xcrun altool --upload-app -f {pkg} -t macos "
              f"--apiKey <KEY_ID> --apiIssuer <ISSUER_ID>")
        print("App Review notarises store builds, so do NOT notarise this.")
    return 0


# Imported late so `--help` and the macOS refusal work without the app's deps.
if __name__ == "__main__":
    sys.path.insert(0, str(project_root))
    try:
        from app.i18n import LOCALE_CODES
        from app.core.build_variant import FLAGS as VARIANT_FLAGS
    except Exception as exc:  # noqa: BLE001
        # On Windows this is reached only after require_macos() has already
        # stopped us, so it never masks the real message.
        LOCALE_CODES, VARIANT_FLAGS = [], {}
        _import_error = exc
    raise SystemExit(main())
