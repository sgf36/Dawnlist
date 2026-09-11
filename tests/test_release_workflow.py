"""What the release workflow promises about signing, asserted against the file.

This exists because of a failure that was invisible in exactly the way the
worst ones are. On this branch every CI run failed, and the failing step was
`Upload` on the macOS leg — not a build error, not a test error, an artefact
upload complaining there was nothing to upload. The cause:

    if-no-files-found: ${{ (... || secrets.MACOS_CERTIFICATE_P12_BASE64 != '')
                            && 'error' || 'warn' }}

The macOS signing steps are gated on the secret AND on
`github.event_name != 'pull_request'`. The upload's expectation copied the
first half of that and not the second, so on a pull request the secret was
present, the signing was skipped anyway, and the run demanded a .dmg that
nothing had been asked to build. "Has the secret" and "will actually sign" are
not the same question, and one line here read them as one.

Meanwhile the Windows leg was GREEN and had signed nothing — Azure signing is
gated the same way. A green job producing an unsigned ZIP that is byte-for-byte
indistinguishable from a signed one, at the moment somebody is waiting for a
filename to publish, is the fault this file is here to make loud.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"


@pytest.fixture(scope="module")
def build_job() -> dict:
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return loaded["jobs"]["build"]


def _step(job: dict, name: str) -> dict:
    for step in job["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"no step named {name!r} — the workflow was renamed "
                         f"under this test, which is the same as deleting it")


def _index(job: dict, name: str) -> int:
    return next(i for i, s in enumerate(job["steps"]) if s.get("name") == name)


def _code(run: str) -> str:
    """A shell block with its comments removed.

    Without this, a test asserting that the signed branch does not pass
    `--allow-unsigned` is satisfied by the comment that explains why it does
    not — reading the documentation instead of the command.
    """
    kept = [line for line in run.splitlines() if not line.lstrip().startswith("#")]
    return "\n".join(kept)


# ---------------------------------------------------------------------------
# The expectation must mirror the gates, not half of them
# ---------------------------------------------------------------------------

MAC_PACKAGING_STEP = "Sign, package and notarise (macOS)"


def test_the_macos_packaging_step_is_gated_at_all(build_job):
    """Positive control. Every assertion below is of the form "the upload
    repeats these gates"; if the gates vanished, that would pass vacuously."""
    gate = _step(build_job, MAC_PACKAGING_STEP)["if"]
    assert "macos-latest" in gate
    assert "pull_request" in gate, (
        "the macOS packaging step no longer skips pull requests — if that is "
        "deliberate, the upload expectation below must change with it")


def test_the_upload_expects_a_macos_artefact_only_when_one_will_be_built(build_job):
    """The bug, stated as a test: every clause gating the step that PRODUCES
    the macOS package must also appear in the expression that DEMANDS it."""
    gate = _step(build_job, MAC_PACKAGING_STEP)["if"]
    expectation = _step(build_job, "Upload")["with"]["if-no-files-found"]

    clauses = [c.strip() for c in gate.split("&&")]
    macos_only = [c for c in clauses if "macos-latest" not in c]
    assert macos_only, "no gates left to compare — the control above should have caught this"

    for clause in macos_only:
        # Compare on the operands, not the spacing: `env.HAS_SIGNING == 'true'`
        # in the step is the same question as the raw secret test inline here.
        key = "pull_request" if "pull_request" in clause else "MACOS_CERTIFICATE_P12_BASE64"
        assert key in expectation, (
            f"the macOS package is gated on {clause!r} but the Upload step "
            f"demands one without asking about {key}. This is the exact shape "
            f"that failed every run on 2026-09-10: a job that correctly built "
            f"nothing, failed for having built nothing.")


# ---------------------------------------------------------------------------
# An unsigned Windows build must be impossible to mistake for a signed one
# ---------------------------------------------------------------------------

WINDOWS_PACKAGING_STEP = "Package the Windows download"


def test_an_unsigned_windows_zip_is_renamed_so_nobody_can_publish_it(build_job):
    """The direct download is signed precisely so a stranger's SmartScreen
    stays quiet. A PR build signs nothing, and the ZIP it produces looks
    identical once it has left the runner."""
    run = _step(build_job, WINDOWS_PACKAGING_STEP)["run"]
    assert "--allow-unsigned" in run, "the unsigned branch has gone"
    assert "UNSIGNED-DO-NOT-PUBLISH" in run, (
        "an unsigned ZIP leaves CI under a name that reads exactly like a "
        "release artefact")


def test_no_checksum_is_published_beside_an_unsigned_build(build_job):
    """A .sha256 next to an unsigned binary teaches somebody to verify a hash
    that says nothing at all about where the file came from."""
    run = _code(_step(build_job, WINDOWS_PACKAGING_STEP)["run"])
    unsigned = run.split("else", 1)[1]
    assert re.search(r"rm\s+-f\s+dist/Dawnlist-windows-\*\.zip\.sha256", unsigned), (
        "the unsigned branch no longer removes the checksum")


def test_the_signed_branch_does_not_pass_allow_unsigned(build_job):
    """The guard inside build_windows_direct.py is the last thing standing
    between a silently-skipped signing step and a published binary. A blanket
    --allow-unsigned would disarm it for real releases too."""
    run = _code(_step(build_job, WINDOWS_PACKAGING_STEP)["run"])
    signed = run.split("else", 1)[0]
    assert "--allow-unsigned" not in signed


def test_signing_happens_before_packaging(build_job):
    """Signing rewrites the executable in place. A ZIP or a checksum made
    first describes a file nobody will ever download."""
    assert (_index(build_job, "Sign the Windows build (Azure Artifact Signing)")
            < _index(build_job, WINDOWS_PACKAGING_STEP))


def test_windows_signing_is_gated_on_the_signing_account_being_ready(build_job):
    gate = _step(build_job, "Sign the Windows build (Azure Artifact Signing)")["if"]
    assert "AZURE_SIGNING_READY" in gate
    assert "pull_request" in gate, (
        "a pull request from a fork would reach the signing credentials")


# ---------------------------------------------------------------------------
# One list of build-variant flags, not three
# ---------------------------------------------------------------------------

VARIANT_PRODUCERS = ("tools/set_build_variant.py", "packaging/build_exe.spec")


def test_the_variant_setter_writes_the_flags_the_app_reads():
    """The setter and the app used to hold separate copies of the same three
    names. They agreed, so nothing failed — but a variant added to one and not
    the other produces a build that is silently the wrong variant, which is the
    failure the flags exist to prevent."""
    import importlib
    import sys

    sys.path.insert(0, str(ROOT))
    from app.core.build_variant import FLAGS, RESOURCES

    setter = importlib.import_module("tools.set_build_variant")
    assert setter.VARIANTS is FLAGS
    assert setter.RESOURCES == RESOURCES, (
        "the setter writes flags somewhere the app does not look")


def test_no_producer_quotes_a_flag_name_of_its_own():
    """Reading the import is not enough: a producer can import FLAGS and still
    carry a stale literal beside it. Quoted is the test, because the prose in
    both files names the flags deliberately."""
    import sys

    sys.path.insert(0, str(ROOT))
    from app.core.build_variant import FLAGS

    for rel in VARIANT_PRODUCERS:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "build_variant import FLAGS" in text, (
            f"{rel} no longer reads the flag names from the app")
        for flag in FLAGS.values():
            for literal in (f'"{flag}"', f"'{flag}'"):
                assert literal not in text, f"{rel} repeats {flag} as a literal"


# ---------------------------------------------------------------------------
# The package is built from the repository, not from a machine
# ---------------------------------------------------------------------------

def _packaging_module(name: str):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        name, ROOT / "packaging" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_store_tile_is_resized_from_a_committed_master():
    msix = _packaging_module("build_msix")
    assert msix.SOURCE_ICON.is_relative_to(ROOT), (
        "the tile master lives outside the repository, so two machines "
        "building this commit can produce different tiles")
    assert msix.source_icon() == msix.SOURCE_ICON


def test_a_missing_tile_master_stops_the_build_rather_than_finding_another(
        monkeypatch, tmp_path):
    """The control. This fell back to a folder in one developer's OneDrive,
    which is worse than failing: a stale export there is preferred to nothing
    and the package records nowhere which file it took."""
    msix = _packaging_module("build_msix")
    monkeypatch.setattr(msix, "SOURCE_ICON", tmp_path / "not-here.png")
    with pytest.raises(SystemExit):
        msix.source_icon()


# ---------------------------------------------------------------------------
# Release tooling runs on a machine that is not Spencer's
# ---------------------------------------------------------------------------

RELEASE_TOOLS = ("tools/asc.py", "tools/scrub_for_public.py",
                 "tools/set_build_variant.py")

#: A path under somebody's user directory. `C:\Program Files` is not one.
HOME_PATH = re.compile(r"[A-Za-z]:[\\/]Users[\\/]|/home/[A-Za-z]")


def _asc():
    import importlib
    import sys

    sys.path.insert(0, str(ROOT))
    return importlib.import_module("tools.asc")


def test_the_app_store_key_location_comes_from_the_environment(
        monkeypatch, tmp_path):
    key = tmp_path / "AuthKey.p8"
    key.write_text("not a real key", encoding="utf-8")
    asc = _asc()
    monkeypatch.setenv(asc.KEY_ENV, str(key))
    assert asc.key_path() == key


def test_an_unconfigured_app_store_key_refuses_rather_than_401ing(monkeypatch):
    """Apple answers a missing key and a wrong key with the same 401, so the
    refusal has to happen here to be legible at all."""
    import keyring

    asc = _asc()
    monkeypatch.delenv(asc.KEY_ENV, raising=False)
    monkeypatch.setattr(keyring, "get_password", lambda *a, **k: None)
    with pytest.raises(SystemExit):
        asc.key_path()


def test_no_release_tool_carries_a_path_into_somebodys_home_directory():
    """A literal path into one machine makes a script unrunnable everywhere
    else, and on a PUBLIC repository it also publishes that machine's filing."""
    assert HOME_PATH.search(r'KEY = Path(r"C:\Users\Someone\signing\key.p8")'), (
        "the pattern cannot match the line it exists to catch")

    paths = [ROOT / rel for rel in RELEASE_TOOLS]
    paths += sorted(p for p in (ROOT / "packaging").rglob("*")
                    if p.suffix in {".py", ".spec", ".ps1"})
    for path in paths:
        found = HOME_PATH.findall(path.read_text(encoding="utf-8"))
        assert not found, f"{path.relative_to(ROOT)} names {found}"
