"""Find what works on THIS machine and would break on a clean one.

    python tools/audit_portability.py

Written 2026-09-08 after the first CI run failed on two faults that were both
invisible here, and for the same underlying reason: the development machine
carries state that a fresh checkout does not.

  * `pypdf` and `python-docx` were imported by the app and absent from
    requirements.txt. Installed here by hand, so nothing noticed — and a clean
    install produced an app that could not read a PDF or a Word CV.
  * Fifteen tests passed only because a `store_build.flag` was lying in
    app/resources from the last packaging run, which opens the entitlement gate
    by possession.

Both are the same class, and iterating through CI to find them one at a time is
slow and expensive. This looks for the whole class at once, locally, in a
second.

Every finding needs a judgement — this reports, it does not fail a build. The
point is a short list a person can read, not a gate.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: A real backslash. Written this way because a heredoc turns a typed
#: backslash-b into a BACKSPACE BYTE, which is how this detector silently
#: stopped matching anything at all on 2026-09-08.
BS = chr(92)

#: Modules that ship with CPython or are the project itself. Anything else that
#: is imported has to be declared.
STDLIB = set(sys.stdlib_module_names) | {"app", "tools", "tests"}

#: Modules that live in this repository and are imported by adding their
#: directory to sys.path, so they are local rather than third-party. Without
#: this, every render_* helper is reported as an undeclared dependency and the
#: real one (Pillow) is lost in the noise.
LOCAL_MODULES = {p.stem for p in (ROOT / "tools").glob("*.py")} | {
    p.stem for p in (ROOT / "packaging").glob("*.py")}

#: Paths that are the developer's, not the product's.
LOCAL_PATH = re.compile(r"[A-Za-z]:\\Users\\[A-Za-z]|/Users/[a-z]|/home/[a-z]",
                        re.IGNORECASE)

#: Calls that only exist, or only behave, on one platform.
PLATFORM_CALLS = {
    "os.startfile": "Windows only — raises AttributeError elsewhere",
    "winreg": "Windows only",
    "win32": "Windows only",
    "powershell": "Windows only",
    # NOT "NUL" as a bare substring — that matches SQL's NOT NULL, which
    # appears 94 times in db.py alone and drowns the real findings.
    # A REGEX, not a substring. Plain "NUL" matches SQL's NOT NULL, which
    # appears 94 times in db.py alone and buried the real findings. Any key
    # here beginning with a backslash is treated as a pattern.
    r"\bNUL\b": "Windows device name; /dev/null elsewhere",
}

#: Things a test must not reach out to.
TEST_AMBIENT = {
    "keyring.get_password": "reads the developer's real credential store",
    "keyring.set_password": "WRITES to the developer's real credential store",
    "Path.home()": "reads the developer's home directory",
    "urllib.request.urlopen": "makes a real network call",
    "requests.get": "makes a real network call",
    "requests.post": "makes a real network call",
    "anthropic.Anthropic(": "constructs a real API client",
}


def tracked_files(suffix: str) -> list[Path]:
    out = subprocess.run(["git", "ls-files", f"*{suffix}"], cwd=ROOT,
                         capture_output=True, text=True)
    return [ROOT / line for line in out.stdout.splitlines() if line.strip()]


def third_party_imports(paths: list[Path]) -> dict[str, set[str]]:
    """Top-level package name -> the files importing it."""
    found: dict[str, set[str]] = {}
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:            # relative import, always local
                    continue
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            for name in names:
                if name and name not in STDLIB and name not in LOCAL_MODULES:
                    found.setdefault(name, set()).add(
                        str(path.relative_to(ROOT)).replace("\\", "/"))
    return found


def declared() -> set[str]:
    req = ROOT / "requirements.txt"
    if not req.exists():
        return set()
    names = set()
    for line in req.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        name = re.split(r"[><=!\[;]", line)[0].strip().lower()
        if name:
            names.add(name)
    return names


#: pip name -> import name, where they differ.
ALIASES = {"python-docx": "docx", "pyyaml": "yaml", "pillow": "PIL",
           "pyside6": "PySide6", "beautifulsoup4": "bs4"}


def main() -> int:
    app_files = [p for p in tracked_files(".py")
                 if not str(p.relative_to(ROOT)).startswith("tests")]
    test_files = tracked_files(".py")
    test_files = [p for p in test_files
                  if str(p.relative_to(ROOT)).replace("\\", "/").startswith("tests/")]

    findings = 0

    # ---- 1. imports the app makes that nothing declares --------------------
    print("1. UNDECLARED DEPENDENCIES")
    print("   An import with no entry in requirements.txt works here and")
    print("   fails on a clean install, usually as a missing FEATURE rather")
    print("   than a crash.\n")
    decl = declared()
    decl_import_names = {ALIASES.get(d, d).lower() for d in decl} | decl
    for pkg, files in sorted(third_party_imports(app_files).items()):
        if pkg.lower() not in decl_import_names:
            print(f"   MISSING  {pkg:16} imported by {', '.join(sorted(files)[:3])}")
            findings += 1
    if not findings:
        print("   none\n")
    else:
        print()

    # ---- 2. tests that read the machine ------------------------------------
    print("2. TESTS THAT READ THE MACHINE")
    print("   A test touching the real keyring, home directory or network")
    print("   passes or fails according to who runs it.\n")
    before = findings
    for path in test_files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for needle, why in TEST_AMBIENT.items():
            if needle in text:
                # A monkeypatch OF the thing is the fix, not the fault.
                if f'monkeypatch' in text and needle.split("(")[0] in text:
                    continue
                rel = str(path.relative_to(ROOT)).replace("\\", "/")
                print(f"   {rel}: {needle} — {why}")
                findings += 1
    if findings == before:
        print("   none\n")
    else:
        print()

    # ---- 3. state in the tree that decides behaviour -----------------------
    print("3. UNTRACKED STATE THAT CHANGES BEHAVIOUR")
    print("   A gitignored file present here and absent on a fresh checkout.")
    print("   This is what made fifteen tests pass locally and fail in CI.\n")
    before = findings
    ignored_present = subprocess.run(
        ["git", "ls-files", "--others", "--ignored", "--exclude-standard",
         "--directory"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    interesting = [f for f in ignored_present
                   if f.endswith(".flag") or "/resources/" in f]
    for f in interesting:
        print(f"   {f}  — present here, absent on a clean checkout")
        findings += 1
    if findings == before:
        print("   none (no flags or resource files are currently untracked)\n")
    else:
        print()

    # ---- 4. the developer's own paths --------------------------------------
    print("4. HARDCODED LOCAL PATHS")
    before = findings
    for path in tracked_files(".py") + tracked_files(".yml") + tracked_files(".md"):
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        if rel.startswith(("docs/", "store/")):
            continue          # documentation may legitimately quote a path
        for i, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if LOCAL_PATH.search(line) and "example" not in line.lower():
                print(f"   {rel}:{i}  {line.strip()[:90]}")
                findings += 1
    if findings == before:
        print("   none\n")
    else:
        print()

    # ---- 5. platform-only calls --------------------------------------------
    print("5. PLATFORM-SPECIFIC CALLS IN SHIPPED CODE")
    before = findings
    for path in app_files:
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        for needle, why in PLATFORM_CALLS.items():
            hit = (re.search(needle, text) if needle.startswith(r"")
                   else needle in text)
            if hit:
                guarded = ("sys.platform" in text or "platform.system" in text
                           or "os.name" in text)
                mark = "guarded" if guarded else "UNGUARDED"
                print(f"   {rel}: {needle} — {why}  [{mark}]")
                if not guarded:
                    findings += 1
    if findings == before:
        print("   none\n")
    else:
        print()

    print(f"{findings} thing(s) worth a look.")
    print("\nEvery line needs a judgement. A guarded platform call is fine; a")
    print("test that monkeypatches the thing it names is fine. What is never")
    print("fine is a dependency nothing declares, or behaviour that changes")
    print("with a file the repository does not carry.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
