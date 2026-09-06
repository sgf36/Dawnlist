"""Find code that exists but nothing runs.

The missing interview step was exactly this: `build_factsheet_request` was
written, tested, and called by nothing, so a user finished onboarding with an
empty brief. Nothing failed — the app just quietly did less than it claimed.
It was found by reading, which is not a method.

A definition is reported when NOTHING in `app/` names it — not another module,
not even its own. Tests do not count as callers: a well-tested function that
production never reaches is the precise shape of this bug, and counting the
test would hide exactly the case worth seeing.

This is a reading aid, not a gate. Dynamic dispatch, Qt signal connections by
name, and the CLI's own entry points all look unreferenced from here, so every
hit needs a human decision. It is run by hand, not in CI.
"""
from __future__ import annotations

import argparse
import ast
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
TESTS = ROOT / "tests"


def defined_names(tree: ast.Module) -> dict[str, int]:
    """Top-level functions and classes, plus methods, with their line numbers."""
    out: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name.startswith("_"):
                continue        # private by convention; not part of any contract
            out.setdefault(node.name, node.lineno)
    return out


def used_names(tree: ast.Module) -> set[str]:
    """Every way one name can reach another, which is more than call sites.

    Four node kinds, and leaving any of them out produces a report too noisy to
    read. Attributes: `interview.render_factsheet(...)` yields no Name node for
    `render_factsheet`. Import aliases: `from x import create_opportunity` is
    how most of this codebase reaches most of itself, and counting only Names
    reported every imported function as dead. Bare Names: the ordinary case.
    String constants: a Qt connection or a `getattr` by name is still a use.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add(node.asname or node.name.rsplit(".", 1)[-1])
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.add(node.value)
    return names


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--include-tests", action="store_true",
                    help="count a test as a caller (hides the bug this looks for)")
    args = ap.parse_args()

    app_files = sorted(APP.rglob("*.py"))
    test_files = sorted(TESTS.rglob("*.py"))

    definitions: dict[str, list[tuple[Path, int]]] = defaultdict(list)
    per_file_uses: dict[Path, set[str]] = {}

    for path in app_files:
        tree = parse(path)
        per_file_uses[path] = used_names(tree)
        for name, line in defined_names(tree).items():
            definitions[name].append((path, line))

    test_uses: set[str] = set()
    for path in test_files:
        test_uses |= used_names(parse(path))

    # A name counts as used if ANY app file references it, its own included:
    # a helper called only by its own module is wired, as long as something
    # eventually reaches that module. What this is looking for is a definition
    # the running app never reaches by any route at all.
    all_app_uses: set[str] = set()
    for uses in per_file_uses.values():
        all_app_uses |= uses

    unwired: list[tuple[str, Path, int, bool]] = []
    for name, sites in sorted(definitions.items()):
        if name in all_app_uses:
            continue
        if args.include_tests and name in test_uses:
            continue
        for path, line in sites:
            unwired.append((name, path, line, name in test_uses))

    if not unwired:
        print("nothing unwired")
        return 0

    print(f"{len(unwired)} definitions nothing in `app/` reaches:\n")
    for name, path, line, tested in sorted(unwired, key=lambda r: (not r[3], str(r[1]))):
        mark = "TESTED but uncalled" if tested else "no caller, no test"
        rel = path.relative_to(ROOT).as_posix()
        print(f"  {rel}:{line}  {name}  - {mark}")

    print("\nEvery line needs a judgement. Expected to appear here and be fine:\n"
          "entry points the CLI calls by name, Qt slots connected as strings,\n"
          "and anything reached only through a subclass.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
