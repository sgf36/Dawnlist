"""No name in the app is used without being defined.

The Microsoft Store-billed build's subscription path used `json`, `urllib`,
`credentials` and `build_request` without importing them. It raised NameError
the first time it ran, in the shipped package, and the suite was green because
no test ever called those functions. Found by audit, 2026-09-13.

Coverage cannot catch this; a static check over every file can.
"""
from __future__ import annotations

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _undefined(paths):
    from pyflakes import api, reporter

    class Collect(reporter.Reporter):
        def __init__(self):
            self.found = []

        def flake(self, message):
            if type(message).__name__ in ("UndefinedName", "UndefinedLocal",
                                          "UndefinedExport"):
                self.found.append(str(message))

        def syntaxError(self, filename, msg, lineno, offset, text):
            self.found.append(f"{filename}:{lineno}: {msg}")

        def unexpectedError(self, filename, msg):
            self.found.append(f"{filename}: {msg}")

    collect = Collect()
    for path in paths:
        api.checkPath(str(path), collect)
    return collect.found


def test_nothing_in_the_app_uses_an_undefined_name():
    pytest.importorskip("pyflakes")
    found = _undefined(sorted((ROOT / "app").rglob("*.py")))
    assert not found, "names used without being defined:\n" + "\n".join(found)


def test_the_check_reports_an_undefined_name(tmp_path):
    """The positive control: the same function on a file with the fault."""
    pytest.importorskip("pyflakes")
    bad = tmp_path / "bad.py"
    bad.write_text("def f():\n    return json.dumps({})\n")
    assert any("json" in line for line in _undefined([bad]))
