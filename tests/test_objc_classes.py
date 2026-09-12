"""No Objective-C class may be defined inside a function.

THE RULE NAME, for `--doctor` and for the cloud Mac session that asked:

    objc-classes-are-module-level

WHAT IT PREVENTS
----------------
Objective-C's class registry is **global and per-process**. A class defined
inside a function is registered on the first call and raises on every call
after it, for the life of the process:

    objc.error: _Delegate is overriding existing Objective-C class

Dawnlist shipped four of them — `_Delegate`, `_Observer`, `_Restorer`,
`_Refresh` — each inside the function that used it, because the callbacks
wanted the caller's `on_finished`, its `threading.Event` and its result box. A
closure is the natural way to write that in Python and it is the one thing
PyObjC will not allow.

WHAT IT COST. Diagnosed on the cloud Mac against build 72, from the shipped
bytecode run against the app's own bundled CPython and PyObjC. The daemon log
across four launches in one evening:

    4  Requesting Media API product batch    <- one per launch, from price()
    0  addPayment                            <- never, not once

**No purchase had ever reached StoreKit.** Not a failed one, not a cancelled
one, in any build ever made.

WHY NOBODY SAW IT. `price()` runs once when the panel is built, so the FIRST
`_fetch_product()` succeeds and the price renders — the screen looks perfectly
healthy. Pressing Subscribe is the SECOND call, which raises on a worker
thread where nothing is catching it, so `on_finished` is never called and the
panel sits on "Talking to the App Store…". Every symptom pointed at Apple.

THE TRAP THIS TEST EXISTS FOR: **the failure needs a second call.** A build
where Subscribe happens to be the first StoreKit action in the process works
perfectly. No amount of trying it once will find this.
"""
from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"

#: Bases that put a class into the Objective-C runtime. Matched by NAME, since
#: the import is lazy and the symbol is not resolvable off a Mac.
OBJC_BASES = {"NSObject", "NSView", "NSWindow", "NSApplication", "NSDocument",
              "NSViewController", "NSWindowController", "NSResponder"}


def _objc_bases(node: ast.ClassDef) -> list[str]:
    names = []
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id in OBJC_BASES:
            names.append(base.id)
        elif isinstance(base, ast.Attribute) and base.attr in OBJC_BASES:
            names.append(base.attr)
    return names


def test_no_objc_class_is_defined_inside_a_function():
    offenders = []
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(func):
                if isinstance(node, ast.ClassDef) and _objc_bases(node):
                    offenders.append(
                        f"app/{path.relative_to(APP).as_posix()}:{node.lineno} "
                        f"{node.name}({', '.join(_objc_bases(node))}) inside "
                        f"{func.name}()")
    assert not offenders, (
        "an Objective-C class defined inside a function registers on the "
        "first call and raises on every call after it, for the life of the "
        "process — move it to module level:\n  " + "\n  ".join(offenders))


def test_the_storekit_classes_are_module_level_and_prefixed():
    """They are global to the Objective-C runtime in a process that also loads
    PySide6, PIL and anthropic. `_Delegate` and `_Observer` are exactly the
    names something else would pick."""
    source = (APP / "core" / "mac_storekit_objc.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    classes = {n.name for n in tree.body if isinstance(n, ast.ClassDef)}
    # Two, where there were four: one observer per purchase and one per
    # restore stacked observers on every press, and the receipt delegate went
    # with the receipt.
    assert classes == {"DawnlistProductsDelegate",
                       "DawnlistTransactionObserver"}, classes
    for name in classes:
        assert name.startswith("Dawnlist"), (
            f"{name} is unprefixed and shares a global namespace with every "
            f"other framework in the process")


def test_the_objc_module_is_imported_lazily_everywhere():
    """It imports Foundation at module level, so importing it on Windows or in
    a source run would raise. Every use has to sit behind a try/except."""
    for path in sorted(APP.rglob("*.py")):
        if path.name == "mac_storekit_objc.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:              # module level only
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                text = ast.unparse(node)
                assert "mac_storekit_objc" not in text, (
                    f"app/{path.relative_to(APP).as_posix()} imports the "
                    f"Objective-C module at module level; it pulls in "
                    f"Foundation and cannot be imported off a Mac")


def test_a_second_call_does_not_raise():
    """The real thing, on a real Mac. Skipped elsewhere.

    This is the assertion that fails today and passes after the fix, and it is
    the ONLY one that exercises the Objective-C runtime rather than the shape
    of the source. It runs in the macOS CI job, where PyObjC is now declared.
    """
    import pytest

    pytest.importorskip("StoreKit")

    from app.core import mac_storekit

    # Twice in one process. The first registers the class; the second is the
    # call that used to raise `objc.error: ... is overriding existing
    # Objective-C class`.
    for _ in range(2):
        mac_storekit._fetch_product()      # returns None off a mas build
