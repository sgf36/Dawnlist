"""Every place that states a version must state the same one.

WHAT THIS CAUGHT
----------------
On 2026-09-09 the app, `build_exe.spec` and `build_windows_direct.py` were all
moved to 1.1.0. `packaging/msix/AppxManifest.xml` was not, because it is XML
and nothing pointed at it. CI then built a correct, signed Store package
stamped **1.0.2.0** — the version already published.

Partner Center refuses a package whose version is not higher than the live
one. So the package carrying the fix for a release-blocking defect would have
been rejected at upload, days after that defect went live, and the rejection
would have read as a Partner Center problem rather than a versioning one.

`build_msix.py` now stamps the manifest at package time, so the XML cannot
lag. This pins the rest of them together, and pins the SHAPE of the MSIX
version: four parts, revision zero.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.version import VERSION, marketing_version, msix_version

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "packaging" / "msix" / "AppxManifest.xml"


def manifest_version() -> str:
    text = MANIFEST.read_text(encoding="utf-8")
    found = re.search(r"<Identity[^>]*?Version=\"([^\"]*)\"", text, re.DOTALL)
    assert found, "no Identity Version in AppxManifest.xml"
    return found.group(1)


def test_the_marketing_version_is_three_parts():
    assert re.fullmatch(r"\d+\.\d+\.\d+", VERSION), VERSION


def test_the_msix_version_is_four_parts_with_a_zero_revision():
    """Partner Center reserves the revision field for its own re-signing and
    rejects a package that sets it."""
    assert msix_version() == VERSION + ".0"
    assert re.fullmatch(r"\d+\.\d+\.\d+\.0", msix_version())


def test_the_committed_manifest_matches_the_app():
    """The stamper rewrites this at package time, so a mismatch here cannot
    ship — but a manifest that disagrees with the app is still a manifest
    nobody has read, and this is the file a person opens to check."""
    assert manifest_version() == msix_version(), (
        f"AppxManifest.xml says {manifest_version()}, the app says "
        f"{msix_version()}. build_msix.py would correct it; fix it here too "
        f"so the committed file is not a lie.")


def test_the_manifest_is_ahead_of_what_is_published():
    """1.0.2.0 is live on the Microsoft Store (9PF25H395BB8). A package at or
    below it is refused at upload."""
    published = (1, 0, 2, 0)
    current = tuple(int(p) for p in manifest_version().split("."))
    assert current > published, (
        f"package version {manifest_version()} is not above the published "
        f"1.0.2.0 — Partner Center will refuse it")


def test_the_packaging_scripts_read_the_app_rather_than_repeating_it():
    """A literal version in a build script is the drift this module exists to
    stop. Both producers must import it."""
    for name in ("build_exe.spec", "build_windows_direct.py"):
        text = (ROOT / "packaging" / name).read_text(encoding="utf-8")
        assert "marketing_version" in text, f"{name} does not read app.version"
        literals = re.findall(r'"(\d+\.\d+\.\d+)"', text)
        assert not literals, (
            f"{name} still carries hardcoded version(s) {literals}; the "
            f"version belongs in app/version.py alone")


def test_an_override_flows_through_to_the_package(monkeypatch):
    monkeypatch.setenv("DAWNLIST_VERSION", "2.3.4")
    assert marketing_version() == "2.3.4"
    assert msix_version() == "2.3.4.0"


def test_a_short_override_is_padded_rather_than_producing_a_bad_manifest(
        monkeypatch):
    monkeypatch.setenv("DAWNLIST_VERSION", "2.3")
    assert msix_version() == "2.3.0.0"


# ---------------------------------------------------------------------------
# The stamper itself, run for real.
#
# `build_msix.py` refused a CI build with "could not find the Identity Version"
# because the regex contained two literal BACKSPACE characters (0x08) — a
# heredoc had turned `\b` into a control character. It was invisible in the
# editor, in `sed` and in the file viewer; only `cat -A` showed it, and only
# after a six-minute build had been spent finding out.
#
# The guard did its job: it refused rather than shipping a package whose
# version had silently not been set. But a test costs a second.
# ---------------------------------------------------------------------------

def _stamper():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_msix", ROOT / "packaging" / "build_msix.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_stamper_rewrites_a_real_manifest(tmp_path, monkeypatch):
    import shutil

    staged = tmp_path / "AppxManifest.xml"
    shutil.copy2(MANIFEST, staged)
    monkeypatch.setenv("DAWNLIST_VERSION", "3.4.5")

    _stamper().stamp_version(staged)

    text = staged.read_text(encoding="utf-8")
    found = re.search(r"<Identity[^>]*?Version=\"([^\"]*)\"", text, re.DOTALL)
    assert found and found.group(1) == "3.4.5.0"


def test_the_stamper_refuses_a_manifest_it_cannot_stamp(tmp_path):
    import pytest

    broken = tmp_path / "AppxManifest.xml"
    broken.write_text("<Package><Identity Name=\"x\" /></Package>",
                      encoding="utf-8")
    with pytest.raises(SystemExit):
        _stamper().stamp_version(broken)


def test_no_packaging_script_carries_a_control_character():
    """How the backspaces got in, and why nothing showed them.

    A control character inside a regex literal changes what it matches while
    looking identical in every tool that renders the file.
    """
    offenders = []
    for path in sorted((ROOT / "packaging").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for i, ch in enumerate(text):
            if ord(ch) < 32 and ch not in "\n\r\t":
                line = text.count("\n", 0, i) + 1
                offenders.append(f"{path.name}:{line} contains \\x{ord(ch):02x}")
    assert not offenders, offenders
