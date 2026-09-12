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


# ---------------------------------------------------------------------------
# Dependency floors, which CI installs from
# ---------------------------------------------------------------------------

def _floor(name: str) -> tuple[int, ...]:
    """The `>=` version stated for a package in requirements.txt."""
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    found = re.search(rf"^{name}>=([0-9.]+)", text, re.M)
    assert found, f"{name} is not pinned in requirements.txt"
    return tuple(int(p) for p in found.group(1).split("."))


def test_every_key_the_app_sends_is_a_parameter_the_sdk_accepts():
    """The floor is a claim about behaviour, so it is tested as one.

    Every assessment and interview request carries `output_config`, and it
    reaches the SDK as a keyword argument. An SDK below the floor raises a
    TypeError naming the keyword, which reads like a fault in the caller.
    """
    import inspect
    import sys

    sys.path.insert(0, str(ROOT))
    from anthropic.resources.messages import Messages
    from app.intelligence.assess import build_request

    sent = set(build_request([], "", "")) | {"stream"}
    accepted = set(inspect.signature(Messages.create).parameters)
    assert sent <= accepted, f"the installed SDK rejects {sent - accepted}"


def test_the_anthropic_floor_is_a_release_that_has_output_config():
    """0.76.0 was measured to have no output_config parameter anywhere;
    0.77.0 has it on create and on stream."""
    assert _floor("anthropic") >= (0, 77, 0)


# ---------------------------------------------------------------------------
# The lock files CI installs, against the floors a person wrote
# ---------------------------------------------------------------------------

#: input -> the hashed lock compiled from it.
LOCKS = {
    "requirements.txt": "requirements.lock.txt",
    "requirements-dev.txt": "requirements-dev.lock.txt",
    "requirements-audit.txt": "requirements-audit.lock.txt",
}

FLOOR = re.compile("([A-Za-z0-9._-]+)>=([0-9][0-9A-Za-z.]*)")
PIN = re.compile("([A-Za-z0-9._-]+)==([0-9][^ ;]*)")


def _normalise(name: str) -> str:
    return re.sub("[-_.]+", "-", name).lower()


def _version(text: str) -> tuple[int, ...]:
    """Enough of a version to compare two of them. A non-numeric segment ends
    the comparison rather than inventing an ordering for it."""
    parts = []
    for segment in text.split("."):
        digits = re.match("[0-9]+", segment)
        if not digits:
            break
        parts.append(int(digits.group()))
    return tuple(parts)


def _floors(rel: str) -> dict[str, str]:
    floors = {}
    for line in (ROOT / rel).read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if not line or line.startswith("-r"):
            continue
        found = FLOOR.match(line)
        assert found, f"{rel} states {line!r}, which is not a floor"
        floors[_normalise(found.group(1))] = found.group(2)
    return floors


def _pins(rel: str) -> dict[str, str]:
    pins = {}
    for line in (ROOT / rel).read_text(encoding="utf-8").splitlines():
        found = PIN.match(line)
        if found:
            pins[_normalise(found.group(1))] = found.group(2)
    return pins


@pytest.mark.parametrize("source,lock", sorted(LOCKS.items()))
def test_every_floor_is_pinned_in_the_lock_beside_it(source, lock):
    """A lock nobody recompiled after an edit installs the OLD set, and says
    nothing: the run is green and the package CI builds is not the one the
    input file describes."""
    floors = _floors(source)
    assert floors, f"nothing parsed out of {source} — suspect the parser"
    missing = sorted(set(floors) - set(_pins(lock)))
    assert not missing, (
        f"{lock} does not pin {missing}. Recompile it — the command is in the "
        f"header of {source}.")


@pytest.mark.parametrize("source,lock", sorted(LOCKS.items()))
def test_every_pin_satisfies_the_floor_that_was_chosen(source, lock):
    """The floors are load-bearing — anthropic>=0.77.0 is the release that
    accepts output_config — so a lock below one installs a build that cannot
    work, on every runner at once."""
    pins = _pins(lock)
    for name, floor in _floors(source).items():
        assert _version(pins[name]) >= _version(floor), (
            f"{lock} pins {name}=={pins[name]}, below the {floor} that "
            f"{source} asks for")


def _unhashed(lines: list[str]) -> tuple[list[str], int]:
    """The pins carrying no hash, and how many pins were read at all."""
    offenders, seen = [], 0
    for index, line in enumerate(lines):
        if not PIN.match(line):
            continue
        seen += 1
        block = [line]
        for following in lines[index + 1:]:
            if not following.startswith(" "):
                break
            block.append(following)
        if not any("--hash=" in item for item in block):
            offenders.append(line)
    return offenders, seen


def test_the_hash_check_can_see_a_pin_with_no_hash():
    """The control. Every assertion below is that a list came back empty, and
    an empty list is what a broken reader returns too."""
    offenders, seen = _unhashed(["pytest==8.0.0", "colorama==0.4.6 ; sys_platform == 'win32'",
                                 "    --hash=sha256:beef"])
    assert seen == 2 and offenders == ["pytest==8.0.0"]


@pytest.mark.parametrize("lock", sorted(LOCKS.values()))
def test_every_pin_in_a_lock_carries_a_hash(lock):
    """pip refuses a --require-hashes install whose file has an unhashed
    requirement. This fails the same build a runner would, in a second rather
    than a queue slot on three operating systems."""
    offenders, seen = _unhashed(
        (ROOT / lock).read_text(encoding="utf-8").splitlines())
    assert not offenders, f"{lock}: {offenders}"
    assert seen > 5, f"{lock} barely parsed: {seen} pins"


def test_dependabot_watches_both_things_that_cannot_update_themselves():
    """A hashed lock and a SHA-pinned action are both deliberately frozen, so
    a published fix reaches neither without somebody opening a pull request."""
    config = yaml.safe_load(
        (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    watched = {entry["package-ecosystem"] for entry in config["updates"]}
    assert {"pip", "github-actions"} <= watched


# ---------------------------------------------------------------------------
# What actually runs, evaluated rather than read
#
# Every test above this line asks whether a string appears in a condition. That
# catches a clause being deleted and nothing else: the conditions here decide
# whether a branch can sign with the release certificate and upload to the App
# Store, and reading them by substring cannot tell a gate that holds from one
# that is merely present. So the expressions are evaluated, under the contexts
# GitHub would supply — including the one nobody thought about, a
# workflow_dispatch from a branch.
# ---------------------------------------------------------------------------

#: Every secret the workflow names. Present or absent as a set, which is how
#: they exist in practice.
SECRET_NAMES = (
    "MACOS_CERTIFICATE_P12_BASE64", "MACOS_CERTIFICATE_PASSWORD",
    "MACOS_SIGN_IDENTITY", "APPLE_ID", "APPLE_APP_PASSWORD", "APPLE_TEAM_ID",
    "AZURE_CLIENT_ID", "AZURE_TENANT_ID", "AZURE_SUBSCRIPTION_ID",
    "MAS_CERTIFICATE_P12_BASE64", "MAS_INSTALLER_P12_BASE64",
    "MAS_CERTIFICATE_PASSWORD", "MAS_PROVISION_PROFILE_BASE64",
    "MAS_SIGN_APP_IDENTITY", "MAS_SIGN_INSTALLER_IDENTITY",
)

SIGNING_STEPS = (
    "Import the signing certificate",
    "Store the notarisation credentials",
    "Sign, package and notarise (macOS)",
    "Azure login (OIDC) for signing",
    "Sign the Windows build (Azure Artifact Signing)",
)


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _tokens(text: str) -> list[str]:
    """A GitHub expression, split. Hand-written because the alternative is a
    regular expression nobody can check by eye."""
    out, index = [], 0
    while index < len(text):
        char = text[index]
        if char == " ":
            index += 1
        elif text[index:index + 2] in ("&&", "||", "==", "!="):
            out.append(text[index:index + 2])
            index += 2
        elif char in "()!":
            out.append(char)
            index += 1
        elif char == "'":
            end = text.index("'", index + 1)
            out.append(text[index:end + 1])
            index = end + 1
        else:
            end = index
            while end < len(text) and (text[end].isalnum() or text[end] in "._-/"):
                end += 1
            assert end > index, f"cannot read {text[index:]!r}"
            out.append(text[index:end])
            index = end
    return out


def _truthy(value) -> bool:
    return value not in (None, False, "", 0)


def _equal(left, right) -> bool:
    left = "" if left is None else left
    right = "" if right is None else right
    if isinstance(left, str) and isinstance(right, str):
        return left.lower() == right.lower()
    if isinstance(left, bool) or isinstance(right, bool):
        return _truthy(left) == _truthy(right)
    return left == right


def _lookup(path: str, context: dict):
    value = context
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


class _Reader:
    def __init__(self, tokens: list[str]):
        self.tokens, self.at = tokens, 0

    def peek(self):
        return self.tokens[self.at] if self.at < len(self.tokens) else None

    def take(self):
        token = self.peek()
        self.at += 1
        return token


def _or(reader: _Reader, context: dict):
    value = _and(reader, context)
    while reader.peek() == "||":
        reader.take()
        alternative = _and(reader, context)
        # GitHub returns the OPERAND, not a boolean; `x && 'error' || 'warn'`
        # depends on that.
        value = value if _truthy(value) else alternative
    return value


def _and(reader: _Reader, context: dict):
    value = _comparison(reader, context)
    while reader.peek() == "&&":
        reader.take()
        right = _comparison(reader, context)
        value = right if _truthy(value) else value
    return value


def _comparison(reader: _Reader, context: dict):
    left = _unary(reader, context)
    if reader.peek() in ("==", "!="):
        operator = reader.take()
        right = _unary(reader, context)
        same = _equal(left, right)
        return same if operator == "==" else not same
    return left


def _unary(reader: _Reader, context: dict):
    token = reader.take()
    assert token is not None, "expression ended early"
    if token == "!":
        return not _truthy(_unary(reader, context))
    if token == "(":
        value = _or(reader, context)
        assert reader.take() == ")", "unbalanced parentheses"
        return value
    if token.startswith("'"):
        return token[1:-1]
    if token == "true":
        return True
    if token == "false":
        return False
    if token.isdigit():
        return int(token)
    return _lookup(token, context)


def _evaluate(text: str, context: dict):
    text = text.strip()
    if text.startswith("${{") and text.endswith("}}"):
        text = text[3:-2].strip()
    reader = _Reader(_tokens(text))
    value = _or(reader, context)
    assert reader.peek() is None, f"unparsed tail in {text!r}"
    return value


def _interpolate(text: str, context: dict) -> str:
    """A field that mixes literal text with expressions, such as an artefact
    name."""
    out, rest = "", text
    while "${{" in rest:
        before, rest = rest.split("${{", 1)
        expression, rest = rest.split("}}", 1)
        out += before + _as_text(_evaluate(expression, context))
    return out + rest


def _as_text(value) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return ""
    return str(value)


def _context(*, event: str, ref: str, os: str = "windows-latest",
             variant: str = "direct", secrets: bool = True,
             azure_ready: bool = True, macos_direct: bool = False) -> dict:
    """One run, as the workflow would see it."""
    head = "abc1234def5678" + "0" * 26
    return {
        "github": {
            "event_name": event,
            "ref": ref,
            "sha": head,
            "workspace": "D:/a/Dawnlist/Dawnlist",
            "run_number": "42",
            "event": {"pull_request": {"head": {"sha": head}}},
        },
        "matrix": {"os": os, "variant": variant},
        "secrets": {name: ("x" if secrets else "") for name in SECRET_NAMES},
        "vars": {"AZURE_SIGNING_READY": "true" if azure_ready else ""},
        # `inputs` is empty on anything but a dispatch, so a boolean input
        # reads as null there — which is exactly what gates the .dmg off a
        # push to master.
        "inputs": {"macos_direct": macos_direct} if event == "workflow_dispatch" else {},
        "steps": {"label": {"outputs": {
            "suffix": "" if ref == "refs/heads/master" else f"-{event}-abc1234"}}},
    }


def _step_context(step: dict, context: dict) -> dict:
    resolved = dict(context.get("env") or {})
    for key, value in (step.get("env") or {}).items():
        resolved[key] = (_as_text(_evaluate(value, context))
                         if isinstance(value, str) and "${{" in value else value)
    return dict(context, env=resolved)


def _will_run(step: dict, context: dict) -> bool:
    condition = step.get("if")
    if condition is None:
        return True
    return _truthy(_evaluate(condition, _step_context(step, context)))


def _job_runs(job: dict, context: dict) -> bool:
    condition = job.get("if")
    return condition is None or _truthy(_evaluate(condition, context))


# -- the evaluator itself, before anything is concluded from it -------------

def test_the_expression_evaluator_agrees_with_github_on_known_cases():
    """Without this, every "does not run" assertion below could be satisfied by
    an evaluator that returns false for everything."""
    on_master = _context(event="push", ref="refs/heads/master")
    assert _evaluate("github.ref == 'refs/heads/master'", on_master) is True
    assert _evaluate("github.ref == 'refs/heads/other'", on_master) is False
    assert _evaluate("${{ true && 'error' || 'warn' }}", on_master) == "error"
    assert _evaluate("${{ false && 'error' || 'warn' }}", on_master) == "warn"
    assert _evaluate("!(github.event_name == 'push')", on_master) is False
    assert _evaluate("secrets.APPLE_ID != ''", on_master) is True
    # An input a push never supplies.
    assert not _truthy(_evaluate("inputs.macos_direct", on_master))
    assert _interpolate("a-${{ matrix.os }}-b", on_master) == "a-windows-latest-b"


# -- the manual run from a branch, which nothing used to stop ---------------

def test_a_manual_run_from_a_branch_signs_nothing(workflow):
    """workflow_dispatch can be started from ANY branch. With every secret
    present, the signing account ready and the .dmg explicitly asked for, a
    branch must still reach no signing identity at all."""
    build = workflow["jobs"]["build"]
    for name in SIGNING_STEPS:
        step = _step(build, name)
        for os_name, variant in (("macos-latest", "direct"),
                                 ("windows-latest", "direct"),
                                 ("windows-latest", "store")):
            context = _context(event="workflow_dispatch",
                               ref="refs/heads/some-branch",
                               os=os_name, variant=variant, macos_direct=True)
            assert not _will_run(step, context), (
                f"{name!r} runs on a manual run from a branch")


def test_a_manual_run_from_a_branch_does_not_reach_app_store_connect(workflow):
    mas = workflow["jobs"]["mas"]
    context = _context(event="workflow_dispatch", ref="refs/heads/some-branch",
                       os="macos-latest")
    assert not _job_runs(mas, context)
    upload = _step(mas, "Build, sign and upload the Mac App Store package")
    assert not _will_run(upload, context), (
        "the upload step would run if the job's own condition were edited away")


def test_a_pull_request_signs_nothing(workflow):
    build = workflow["jobs"]["build"]
    for name in SIGNING_STEPS:
        step = _step(build, name)
        context = _context(event="pull_request", ref="refs/pull/7/merge",
                           os="macos-latest", macos_direct=True)
        assert not _will_run(step, context)


# -- the positive controls: master still ships ------------------------------

def test_a_push_to_master_still_signs_the_windows_download(workflow):
    build = workflow["jobs"]["build"]
    context = _context(event="push", ref="refs/heads/master",
                       os="windows-latest", variant="direct")
    assert _will_run(_step(build, "Azure login (OIDC) for signing"), context)
    assert _will_run(
        _step(build, "Sign the Windows build (Azure Artifact Signing)"), context)


def test_the_dmg_is_signed_only_when_a_run_asks_for_one(workflow):
    """macOS ships through the Mac App Store, so a push to master must not
    spend the Developer ID certificate and a notarisation round trip on a file
    nobody publishes — while the bundle guards still run on every push."""
    build = workflow["jobs"]["build"]
    packaging = _step(build, MAC_PACKAGING_STEP)
    guards = _step(build, "Verify the bundle (macOS)")

    pushed = _context(event="push", ref="refs/heads/master", os="macos-latest")
    assert not _will_run(packaging, pushed)
    assert _will_run(guards, pushed), (
        "the guards are the reason the macOS leg still runs at all")

    asked = _context(event="workflow_dispatch", ref="refs/heads/master",
                     os="macos-latest", macos_direct=True)
    assert _will_run(packaging, asked)


def test_the_mas_job_runs_on_a_dispatch_from_master(workflow):
    context = _context(event="workflow_dispatch", ref="refs/heads/master",
                       os="macos-latest")
    assert _job_runs(workflow["jobs"]["mas"], context)


# -- the two expressions that must agree with a third -----------------------

SCENARIOS = [
    ("push to master", dict(event="push", ref="refs/heads/master")),
    ("push to a branch", dict(event="push", ref="refs/heads/main")),
    ("pull request", dict(event="pull_request", ref="refs/pull/7/merge")),
    ("dispatch from master", dict(event="workflow_dispatch",
                                  ref="refs/heads/master")),
    ("dispatch from a branch", dict(event="workflow_dispatch",
                                    ref="refs/heads/wip")),
    ("no signing account", dict(event="push", ref="refs/heads/master",
                                azure_ready=False)),
    ("no secrets", dict(event="push", ref="refs/heads/master", secrets=False)),
]


@pytest.mark.parametrize("label,scenario", SCENARIOS)
def test_the_packaging_step_agrees_with_the_signing_step(workflow, label,
                                                         scenario):
    """The packaging step decides whether to pass --allow-unsigned, and it used
    to decide that by re-typing the signing step's condition in shell. Two
    copies of one condition is how a green job produced an unsigned ZIP under a
    release name."""
    build = workflow["jobs"]["build"]
    context = _context(os="windows-latest", variant="direct", **scenario)
    signing = _will_run(
        _step(build, "Sign the Windows build (Azure Artifact Signing)"), context)
    packaging = _step(build, WINDOWS_PACKAGING_STEP)
    signed = _as_text(_evaluate(packaging["env"]["SIGNED"], context)) == "true"
    assert signed is signing, f"{label}: signing={signing}, SIGNED={signed}"


@pytest.mark.parametrize("label,scenario", SCENARIOS)
def test_the_upload_demands_an_artefact_only_when_one_will_exist(workflow,
                                                                 label,
                                                                 scenario):
    """The failure this file was written for, now measured rather than read:
    on a macOS leg that correctly builds nothing, the upload must not insist on
    a .dmg. Both Windows legs always produce a package."""
    build = workflow["jobs"]["build"]
    upload = _step(build, "Upload")
    for os_name, variant in (("macos-latest", "direct"),
                             ("windows-latest", "direct"),
                             ("windows-latest", "store")):
        context = _context(os=os_name, variant=variant, macos_direct=True,
                           **scenario)
        expectation = _evaluate(upload["with"]["if-no-files-found"], context)
        produced = (os_name == "windows-latest"
                    or _will_run(_step(build, MAC_PACKAGING_STEP), context))
        assert expectation == ("error" if produced else "warn"), (
            f"{label} on {os_name}/{variant}: expectation {expectation!r} with "
            f"produced={produced}")


# -- a branch artefact must not read like a release -------------------------

def test_a_branch_artefact_is_named_for_its_event_and_commit(workflow):
    upload = _step(workflow["jobs"]["build"], "Upload")
    name = upload["with"]["name"]

    released = _interpolate(name, _context(event="push",
                                           ref="refs/heads/master",
                                           os="windows-latest",
                                           variant="store"))
    assert released == "dawnlist-windows-latest-store"

    branch = _interpolate(name, _context(event="pull_request",
                                         ref="refs/pull/7/merge",
                                         os="windows-latest", variant="store"))
    assert branch.startswith("dawnlist-windows-latest-store-")
    assert "pull_request" in branch and "abc1234" in branch


def test_the_label_step_reads_the_commit_somebody_pushed(workflow):
    """Asserted on the script because the naming happens in shell. A pull
    request's own SHA is the merge commit, which exists in no branch."""
    run = _step(workflow["jobs"]["build"], "Name this build")["run"]
    assert "pull_request.head.sha" in run
    assert "sha:0:7" in run
    assert "github.event_name" in run


def test_a_non_release_msix_is_renamed_as_well_as_relabelled(workflow):
    """The artefact name is lost the moment somebody extracts the ZIP, and the
    MSIX inside carries the same throwaway signature as a release build."""
    build = workflow["jobs"]["build"]
    rename = _step(build, "Mark a non-release MSIX in its own filename")
    assert _index(build, "Sign the MSIX for upload") < _index(
        build, "Mark a non-release MSIX in its own filename"), (
        "renaming before signing would leave sign_msix.ps1 looking for a file "
        "that is no longer there")
    assert _will_run(rename, _context(event="pull_request",
                                      ref="refs/pull/7/merge",
                                      os="windows-latest", variant="store"))
    assert not _will_run(rename, _context(event="push",
                                          ref="refs/heads/master",
                                          os="windows-latest",
                                          variant="store"))


# -- what every job installs, and what it waits for -------------------------

def test_no_job_installs_anything_unpinned(workflow):
    """An ad-hoc `pip install pyinstaller` in the job that signs with the Apple
    Distribution certificate resolves whatever PyPI offers that minute."""
    installs = []
    for name, job in workflow["jobs"].items():
        for step in job["steps"]:
            for line in (step.get("run") or "").splitlines():
                if "pip install" in line and not line.lstrip().startswith("#"):
                    installs.append((name, line.strip()))
    assert len(installs) >= 5, f"only found {installs} — suspect the reader"
    for job_name, line in installs:
        assert "--require-hashes" in line and ".lock.txt" in line, (
            f"{job_name} installs with {line!r}")


def test_the_build_and_upload_jobs_wait_for_the_tests(workflow):
    """The Worker is half the product, and the mas job used to upload to App
    Store Connect without a single test having run in the same workflow."""
    assert workflow["jobs"]["build"]["needs"] == "worker"
    assert set(workflow["jobs"]["mas"]["needs"]) == {"worker", "build"}


def test_the_app_store_job_names_an_environment(workflow):
    """A condition in this file is only as good as this file: anyone who can
    push a branch can edit it. A GitHub environment is where a branch
    restriction or a required reviewer is enforced instead."""
    assert workflow["jobs"]["mas"]["environment"] == "app-store"


def test_the_public_scrub_check_runs_in_ci(workflow):
    commands = [step.get("run", "") for step in workflow["jobs"]["audit"]["steps"]]
    assert any("scrub_for_public.py --check" in command for command in commands)


def test_known_vulnerabilities_fail_the_run(workflow):
    """pip-audit exits non-zero on a finding, so the assertion is that it runs
    at all and reads the locks CI installs from."""
    commands = "\n".join(step.get("run", "")
                         for step in workflow["jobs"]["audit"]["steps"])
    assert "pip-audit" in commands
    for lock in LOCKS.values():
        assert lock in commands, f"{lock} is installed but never audited"


def test_the_suite_runs_on_both_interpreters(workflow):
    """3.12 is what the packages are frozen with; 3.14 is what the app is
    developed on. A suite run on one says nothing about the other."""
    versions = set()
    for job in workflow["jobs"].values():
        runs_tests = any("pytest" in (step.get("run") or "")
                         for step in job["steps"])
        if not runs_tests:
            continue
        for step in job["steps"]:
            if "setup-python" in (step.get("uses") or ""):
                versions.add(str(step["with"]["python-version"]))
    assert {"3.12", "3.14"} <= versions, versions


def test_the_tests_run_headless_everywhere(workflow):
    """The local command and the CI command must set the same Qt platform.
    They did not, and tests/test_extract.py failed here for weeks while
    passing on a desktop — which teaches everyone to ignore a red result."""
    assert workflow["env"]["QT_QPA_PLATFORM"] == "offscreen"


# -- actions are pinned, because a tag is a mutable pointer -----------------

USES = re.compile("uses: *([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@([A-Za-z0-9]+)")


def test_every_action_is_pinned_to_a_commit_with_its_tag_beside_it():
    """azure/login and azure/artifact-signing-action run in a job holding an
    OIDC token and the signing profile. A retagged release would run there with
    nothing changed in this repository and nothing to see in a diff."""
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    pinned = 0
    for line in lines:
        found = USES.search(line)
        if not found:
            continue
        pinned += 1
        action, reference = found.group(1), found.group(2)
        assert len(reference) == 40 and all(c in "0123456789abcdef" for c in reference), (
            f"{action} is used at {reference!r} rather than a commit SHA")
        assert "# v" in line, (
            f"{action} is pinned with no tag in the comment, so nobody can "
            f"tell which release it is")
    assert pinned >= 8, f"only {pinned} actions seen — suspect the reader"
