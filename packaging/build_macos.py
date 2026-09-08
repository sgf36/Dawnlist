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
import hashlib
import json
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


def notarise(artefact: Path, profile: str, *, timeout: str = "30m") -> None:
    """Submit, record the id, wait, then staple.

    SUBMIT AND WAIT ARE SEPARATE CALLS, and that is not tidiness. `notarytool
    submit --wait` prints the submission id only to stdout as it polls, so when
    a poll dies mid-flight the id is buried in a long log and the build looks
    like it simply hung — a sibling project's run sat here for 1h58m against a
    dead runner network before giving up. Capturing the id first means it is
    always recorded and always queryable afterwards, whatever happens to the
    wait.

    Stapling matters too: without it the app must reach Apple on first launch,
    so a user opening it offline sees Gatekeeper refuse a perfectly notarised
    build.
    """
    # BOUNDED, and this is not belt-and-braces. On 2026-09-08 a run reached
    # exactly here and went silent: `notarytool submit` hung, the job's own
    # 45-minute ceiling eventually killed it, and the runner reported
    # "Terminate orphan process: notarytool". Splitting submit from wait was
    # supposed to guarantee the submission id is always captured — and it does
    # not, if the SUBMIT is what hangs, because the id is only printed once it
    # returns. Ten minutes is generous for an upload that normally takes
    # seconds, and failing here leaves a message that names the cause instead
    # of a job that looks stuck.
    try:
        res = run(["xcrun", "notarytool", "submit", artefact,
                   "--keychain-profile", profile, "--no-wait",
                   "--output-format", "json"], timeout=600)
    except subprocess.TimeoutExpired:
        fail("notarytool submit did not return within 10 minutes. Nothing was "
             "recorded, so there is no submission to query — re-run. If it "
             "recurs, check Apple's system status before changing anything "
             "here: this has been a transient every time so far.")
    if res.returncode != 0:
        fail(f"notarisation submit failed: {res.stderr.strip()[:400]}")
    try:
        submission_id = json.loads(res.stdout)["id"]
    except Exception:  # noqa: BLE001
        fail(f"could not read a submission id from notarytool: {res.stdout[:300]}")
    print(f"  submission id: {submission_id}")
    print(f"  query later:   xcrun notarytool info {submission_id} "
          f"--keychain-profile {profile}")

    # Bounded. Apple normally answers in minutes; an unbounded wait is how a
    # stalled poll eats the whole job.
    res = run(["xcrun", "notarytool", "wait", submission_id,
               "--keychain-profile", profile, "--timeout", timeout])
    if res.returncode != 0 or "status: Accepted" not in res.stdout:
        # `wait` exits 0 for an Invalid submission too, so the exit code alone
        # is not evidence that it passed.
        print(res.stdout[-1500:])
        run(["xcrun", "notarytool", "log", submission_id,
             "--keychain-profile", profile])
        fail(f"notarisation did not reach Accepted. Submission {submission_id}")

    res = run(["xcrun", "stapler", "staple", artefact])
    if res.returncode != 0:
        fail(f"stapling failed: {res.stderr.strip()[:300]}")
    print("  notarised and stapled")


def write_checksum(artefact: Path) -> Path:
    """A .sha256 beside the disk image, matching the Windows download.

    AFTER notarisation and stapling, and the ordering is the whole reason this
    is a separate call rather than a line inside `package_direct`: STAPLING
    REWRITES THE FILE. A checksum taken before it describes a disk image nobody
    will ever download — the same mistake the Windows path made until it was
    reordered, and the reason that ordering is written down in two places now.

    It exists at all because the download page publishes a checksum for the
    Windows ZIP. A page offering one for one platform and not the other reads
    as an oversight on the platform that has none.
    """
    digest = hashlib.sha256(artefact.read_bytes()).hexdigest()
    target = artefact.with_suffix(artefact.suffix + ".sha256")
    # The two-space form both `sha256sum -c` and `shasum -c` expect, so a buyer
    # on either platform can verify it without being told how.
    target.write_text(f"{digest}  {artefact.name}\n", encoding="utf-8")
    print(f"  sha256 {digest}")
    return target


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
    ap.add_argument("--verify-only", action="store_true",
                    help="run the bundle and Info.plist guards, then stop. "
                         "No certificate needed, so CI can catch a broken "
                         "bundle on every push before any signing exists.")
    args = ap.parse_args()

    require_macos()

    identity = args.identity or (
        "Developer ID Application" if args.variant == "direct"
        else "3rd Party Mac Developer Application")

    print("verifying the PyInstaller output...")
    verify_bundle(app_path, args.variant)
    check_plist(app_path)

    if args.verify_only:
        print("Verified. Stopping before signing, as asked.")
        return 0

    print("signing...")
    sign(app_path, args.variant, identity)

    if args.variant == "direct":
        # VERSIONED, to match the Windows download. An unversioned Dawnlist.dmg
        # sitting in somebody's Downloads folder cannot be told apart from the
        # last release, and a cached copy of the old file under the same name
        # is indistinguishable from the new one. The version is read from the
        # BUNDLE rather than taken as an argument, so the filename cannot
        # disagree with what the application reports about itself.
        with (app_path / "Contents" / "Info.plist").open("rb") as fh:
            marketing_version = plistlib.load(fh)["CFBundleShortVersionString"]
        dmg = dist_dir / f"Dawnlist-{marketing_version}.dmg"
        print("packaging...")
        package_direct(app_path, dmg)
        if args.skip_notarise:
            print("\nSKIPPED notarisation. Gatekeeper will refuse this build "
                  "on any machine but this one.")
        else:
            print("notarising...")
            notarise(dmg, args.notary_profile)
        # AFTER stapling, always: stapling rewrites the file, so a checksum
        # taken any earlier describes a disk image nobody will download.
        write_checksum(dmg)
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
