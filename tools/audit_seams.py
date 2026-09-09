"""Find real implementations that no test ever runs, because a double stands in.

`audit_wiring.py` finds code NOTHING calls. This finds the opposite and more
dangerous shape: code production DOES call, wrapped in an injection seam, whose
real implementation every test replaces with a fake. The suite is green, the
function is "covered" by the caller's tests, and nobody has ever executed it.

Two shipped bugs had exactly this shape, found by hand on 2026-09-09:

  * `entitlement.verify_against_worker` asked `/health`, which takes no request
    and returns 200 to anybody — so EVERY string typed into the licence box
    verified. Every test injects `verifier=`, so the real one never ran.

  * `main.calibration_sample` needs an ENABLED search, and seeded searches are
    created disabled — so a fresh install fetched nothing and onboarding could
    not be completed. Every test injects `sample=`, so the real one never ran.

WHAT IT REPORTS, and why each matters:

  NETWORK, UNTESTED   a function that reaches the network and is named by no
                      test. Nobody has asserted what it does with a real
                      response shape, a 403, or an outage.

  SEAM                `x or real_thing` — an injectable default. If the default
                      is also never named by a test, production runs a code
                      path the suite has never executed.

A hit is not automatically a bug. It is a place where "the tests pass" carries
no information about the thing that actually runs in front of a customer.

    python tools/audit_seams.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
TESTS = ROOT / "tests"

#: Names whose appearance means the function talks to something outside itself.
NETWORK = {"urlopen", "Request", "urlretrieve", "httpx", "requests",
           "HTTPSConnection", "HTTPConnection", "socket"}


def names_in(node) -> set[str]:
    found = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            found.add(child.id)
        elif isinstance(child, ast.Attribute):
            found.add(child.attr)
    return found


def tested_names() -> set[str]:
    """Every identifier any test mentions. Deliberately generous: a name that
    appears anywhere in the suite is given the benefit of the doubt, so a hit
    here means the suite does not mention it AT ALL."""
    out: set[str] = set()
    for path in TESTS.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text("utf-8"))
        except SyntaxError:
            continue
        out |= names_in(tree)
    return out


def main() -> int:
    tested = tested_names()
    network_hits: list[str] = []
    seam_hits: list[str] = []

    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text("utf-8"))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                used = names_in(node)
                if used & NETWORK and node.name not in tested:
                    network_hits.append(
                        f"  {rel}:{node.lineno}  {node.name}()  "
                        f"reaches the network, no test names it")

            # `x or real_default` — the injection seam itself.
            if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
                for value in node.values[1:]:
                    target = None
                    if isinstance(value, ast.Name):
                        target = value.id
                    elif isinstance(value, ast.Attribute):
                        target = value.attr
                    if (target and target[0].islower() and "_" in target
                            and target not in tested):
                        seam_hits.append(
                            f"  {rel}:{node.lineno}  falls back to "
                            f"{target}(), which no test names")

    print("NETWORK PATHS NO TEST EXERCISES")
    print("\n".join(sorted(set(network_hits))) or "  (none)")
    print()
    print("INJECTION SEAMS WHOSE REAL DEFAULT NO TEST EXERCISES")
    print("\n".join(sorted(set(seam_hits))) or "  (none)")
    print()
    total = len(set(network_hits)) + len(set(seam_hits))
    print(f"{total} place(s) where a green suite says nothing about what runs.")
    print("Each needs a judgement: some are thin wrappers worth leaving.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
