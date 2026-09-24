"""Two faults the 2026-09-13 audit found, pinned so they cannot return.

1. Write application failed for EVERY user on every build: setup kept only the
   text of the chosen CVs, `apply_run` reads files from `cv_dir()`, and nothing
   ever put a file there.
2. The Microsoft Store build imported a WinRT namespace no requirement
   installed, so its whole purchase path raised ImportError in the shipped
   package. A green suite could not see it, because nothing imported the real
   module. This checks every third-party import against the lock instead.
"""
from __future__ import annotations

import ast
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Modules vendored inside the repo (not under ``app/``) that the AST scanner
#: would otherwise flag as unresolved third-party imports.
VENDORED_LOCAL = {"text_unicode", "common"}

#: Import name -> the distribution that provides it, as named in the lock.
#: A NEW third-party import fails the test below until it is added here AND to
#: requirements.txt, which is the point: it cannot be forgotten.
PROVIDERS = {
    "PySide6": "pyside6",
    "anthropic": "anthropic",
    "keyring": "keyring",
    "platformdirs": "platformdirs",
    "openpyxl": "openpyxl",
    "docx": "python-docx",
    "pypdf": "pypdf",
    "truststore": "truststore",
    "Foundation": "pyobjc-framework-cocoa",
    "AppKit": "pyobjc-framework-cocoa",
    "objc": "pyobjc-core",
    "StoreKit": "pyobjc-framework-storekit",
    "ServiceManagement": "pyobjc-framework-servicemanagement",
    "winrt.runtime": "winrt-runtime",
    "winrt.runtime.interop": "winrt-runtime",
    "winrt.system": "winrt-runtime",
    "winrt.windows.foundation": "winrt-windows-foundation",
    "winrt.windows.applicationmodel": "winrt-windows-applicationmodel",
    "winrt.windows.applicationmodel.activation": "winrt-windows-applicationmodel-activation",
    "winrt.windows.services.store": "winrt-windows-services-store",
}


def _third_party_imports():
    std = set(sys.stdlib_module_names)
    found = {}
    for path in (ROOT / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if top in std or top == "app" or top in VENDORED_LOCAL:
                    continue
                found.setdefault(name, path.relative_to(ROOT).as_posix())
    return found


def _provider(name):
    if name in PROVIDERS:
        return PROVIDERS[name]
    if name.startswith("winrt."):
        return None           # every winrt namespace must be named exactly
    return PROVIDERS.get(name.split(".")[0])


def test_every_third_party_import_is_installed_by_the_lock():
    lock = (ROOT / "requirements.lock.txt").read_text(encoding="utf-8").lower()
    missing = []
    for name, where in sorted(_third_party_imports().items()):
        dist = _provider(name)
        if dist is None or f"\n{dist}==" not in "\n" + lock:
            missing.append(f"{name} (imported in {where}) -> {dist or 'no provider named'}")
    assert not missing, "imports the shipped build cannot satisfy:\n" + "\n".join(missing)


def test_the_check_catches_a_missing_namespace():
    """The positive control: the same function reports an unnamed winrt module."""
    assert _provider("winrt.windows.services.store") == "winrt-windows-services-store"
    assert _provider("winrt.windows.media.capture") is None


def test_setup_keeps_the_cvs_it_read(tmp_path, monkeypatch):
    from app import main

    monkeypatch.setattr(main, "cv_dir", lambda: tmp_path / "cv")
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    (chosen / "Spencer CV.txt").write_text("Experience\nSenior Analyst 2019-2024\n" * 20)
    (chosen / "~$lock.docx").write_text("x")
    (chosen / "notes.xlsx").write_text("x")

    kept = main.keep_cvs(sorted(chosen.iterdir()))

    assert [p.name for p in kept] == ["Spencer CV.txt"]
    assert "Senior Analyst" in main.load_cv_text(tmp_path / "cv")


def test_no_cv_is_its_own_refusal_and_says_what_to_do(tmp_path, monkeypatch):
    from app import main
    from app.core import db
    from app.i18n import tr
    from app.onboarding.interview import save_document

    conn = db.connect(tmp_path / "d.sqlite3")
    db.migrate(conn)
    save_document(conn, "factsheet", "Senior Analyst at a firm, 2019-2024.")
    monkeypatch.setattr(main, "cv_dir", lambda: tmp_path / "empty")
    with pytest.raises(main.NoCVs) as caught:
        main.apply_run(conn, "theirstack:1")
    assert str(caught.value) == tr("refusal.no_cv")
    assert str(tmp_path) not in str(caught.value), "never a hidden app-data path"


def test_refusals_are_translated_not_hard_coded_english():
    src = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    english = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
                and getattr(node.exc.func, "id", "") in ("NotConfigured", "NoCVs")
                and node.exc.args
                and isinstance(node.exc.args[0], (ast.Constant, ast.JoinedStr))):
            english.append(node.lineno)
    # One deliberate exception: the developer-only message for a build with no
    # store variant, which no customer can reach.
    assert len(english) <= 2, f"hard-coded refusals at lines {english}"
