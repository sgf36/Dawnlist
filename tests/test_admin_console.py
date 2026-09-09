"""The admin console client: issuing, listing and withdrawing override codes.

Every one of these runs the REAL function with a fake opener, rather than
injecting a fake client — the distinction `tools/audit_seams.py` exists to
enforce, and the one that let three bugs ship.
"""
import io
import json
import urllib.error

import pytest

from app.core import admin


def opener_for(payload, status=200):
    def opener(request, timeout=None):
        class R:
            def read(self_inner):
                return json.dumps(payload).encode()

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False
        opener.seen = request
        return R()
    return opener


def failing_opener(code, payload):
    def opener(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, code, "refused", {},
            io.BytesIO(json.dumps(payload).encode()))
    return opener


def test_issuing_a_code_sends_the_note_and_the_role():
    op = opener_for({"ok": True, "code": "DL-ABC", "role": "managed"})
    out = admin.issue_code("ADMIN-KEY", note="Sean, Mac tester",
                           role="managed", plan="standard", opener=op)
    assert out["code"] == "DL-ABC"
    body = json.loads(op.seen.data.decode())
    assert body["note"] == "Sean, Mac tester"
    assert body["role"] == "managed"
    assert op.seen.get_header("Authorization") == "Bearer ADMIN-KEY"


def test_an_unlabelled_code_is_refused_before_the_round_trip():
    """The server refuses it too, but a code nobody can account for should not
    cost a request to find out."""
    def explode(request, timeout=None):      # must never be called
        raise AssertionError("should not have sent anything")

    with pytest.raises(admin.AdminError, match="who this code is for"):
        admin.issue_code("ADMIN-KEY", note="   ", opener=explode)


def test_a_review_code_is_not_single_use():
    """THE WREN SCAR. A reviewer may test on several machines, or re-test after
    a rejection; a spent code turns that into a failed review."""
    assert admin.REVIEW_USES > 1
    op = opener_for({"ok": True, "code": "DL-REV", "max_uses": admin.REVIEW_USES})
    admin.issue_code("ADMIN-KEY", note="App Review", role="managed",
                     max_uses=admin.REVIEW_USES, opener=op)
    assert json.loads(op.seen.data.decode())["max_uses"] == admin.REVIEW_USES


def test_a_non_admin_is_told_plainly():
    op = failing_opener(403, {"error": "not_permitted"})
    with pytest.raises(admin.AdminError, match="not an administrator"):
        admin.list_codes("ORDINARY-KEY", opener=op)


def test_an_outage_is_not_reported_as_a_refusal():
    def opener(request, timeout=None):
        raise OSError("no route to host")

    with pytest.raises(admin.AdminError, match="Could not reach"):
        admin.list_codes("ADMIN-KEY", opener=opener)


def test_admin_status_is_asked_of_the_server_not_assumed():
    """A withdrawn administrator must lose the console immediately, so this
    can never be cached or inferred locally."""
    assert admin.is_admin("K", opener=opener_for({"ok": True, "role": "admin"}))
    assert not admin.is_admin("K", opener=opener_for({"ok": True, "role": "byo"}))
    assert not admin.is_admin("K", opener=failing_opener(403, {"error": "x"}))


def test_revoking_names_the_code():
    op = opener_for({"ok": True})
    admin.revoke_code("ADMIN-KEY", "DL-ABC", opener=op)
    assert json.loads(op.seen.data.decode())["code"] == "DL-ABC"
