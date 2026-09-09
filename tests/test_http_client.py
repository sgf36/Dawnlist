"""Nothing in `app/` may build its own HTTP request.

THE FAILURE THIS REPLACES A COMMENT WITH
----------------------------------------
Cloudflare refuses urllib's default agent (`Python-urllib/3.x`) with error
1010 and an HTTP **403**. On 2026-09-08 that broke every request from every
shipped build. It was fixed by adding a header at the call site, and a comment
explaining why, and that held for exactly one day:

    2026-09-09  theirstack.py    still sending the default
    2026-09-09  entitlement.py   /v1/licence, /v1/apple and /redeem, all three
    2026-09-09  admin.py         written that morning, never had it

A 403 is not a neutral failure. `licence_details` reads 401 and 403 as "the
server said no", and `check()` treats a refusal as FINAL — before the grace
period, deliberately, so a revoked licence cannot keep working for a
fortnight. So this would have told every paying customer their key was not
accepted, refused every reviewer's override code, and left the Mac build
unable to exchange its App Store receipt.

None of it was visible from inside the project. The suite injects transports,
so the real request is never made, and the manual checks used `curl`, whose
agent is not blocked — the verification passed while no real client could
connect.

So the rule is structural rather than remembered: one constructor, in
`app/core/http.py`, which cannot be called without setting the agent. This
test is what makes that a rule instead of a convention.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"

#: The only module allowed to construct one. Everything else goes through
#: `build_request`.
ALLOWED = {"core/http.py"}


def _requests_built_in(path: Path) -> list[int]:
    """Line numbers where this file constructs a `urllib.request.Request`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = ""
        if isinstance(func, ast.Attribute):
            name = func.attr
        elif isinstance(func, ast.Name):
            name = func.id
        if name == "Request":
            found.append(node.lineno)
    return found


def test_only_the_http_module_builds_a_request():
    offenders = []
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(APP).as_posix()
        if rel in ALLOWED:
            continue
        for line in _requests_built_in(path):
            offenders.append(f"app/{rel}:{line}")
    assert not offenders, (
        "these build a urllib Request directly and will send urllib's default "
        "User-Agent, which Cloudflare refuses with error 1010 and a 403 — use "
        "app.core.http.build_request:\n  " + "\n  ".join(offenders))


def test_the_constructor_always_identifies_the_client():
    from app.core.http import USER_AGENT, build_request

    req = build_request("https://example.invalid/x")
    assert req.get_header("User-agent") == USER_AGENT
    assert "Python-urllib" not in (req.get_header("User-agent") or "")


def test_a_caller_cannot_accidentally_drop_the_agent():
    """Caller headers are added to the agent, never instead of it."""
    from app.core.http import USER_AGENT, build_request

    req = build_request("https://example.invalid/x",
                        headers={"authorization": "Bearer x"})
    assert req.get_header("User-agent") == USER_AGENT
    assert req.get_header("Authorization") == "Bearer x"


def test_the_method_follows_urllibs_own_rule():
    from app.core.http import build_request

    assert build_request("https://example.invalid/x").get_method() == "GET"
    assert build_request("https://example.invalid/x",
                         data=b"{}").get_method() == "POST"
    assert build_request("https://example.invalid/x", data=b"{}",
                         method="PATCH").get_method() == "PATCH"


@pytest.mark.parametrize("module,attr", [
    ("app.core.entitlement", "licence_details"),
    ("app.core.entitlement", "redeem_override_code"),
    ("app.core.entitlement", "exchange_mac_receipt"),
    ("app.core.admin", "_request"),
])
def test_every_worker_caller_still_exists(module, attr):
    """Names the four that were found sending the default, so a rename cannot
    quietly drop one back out of the guard above."""
    import importlib

    assert hasattr(importlib.import_module(module), attr)
