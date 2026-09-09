"""What the two transports actually send, and what they do when refused.

WHY THIS FILE EXISTS. Both `_call` methods were flagged by
`tools/audit_seams.py` as network paths no test named. That is the same shape
as three bugs already found by hand: the suite injects the transport
everywhere, so the code that really talks to the internet had never run under
test.

The User-Agent assertions are the point. urllib sends "Python-urllib/3.x" by
default; Cloudflare refuses that signature with error 1010, and on 2026-09-08
that meant every request from every shipped build failed. It was invisible from
both directions — the suite injected the transport, and the manual checks used
`curl`, whose agent is not blocked. A test that pins the header is the only
thing that catches its removal.
"""
import json
import urllib.error

import pytest

from app.feed.base import USER_AGENT, SearchQuery


class _Response:
    def __init__(self, payload, status=200):
        self._body = json.dumps(payload).encode()
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _capture(monkeypatch, module, payload=None, raises=None):
    """Record the Request the transport builds, without letting it out."""
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["request"] = request
        if raises is not None:
            raise raises
        return _Response(payload if payload is not None else {"ok": True})

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    return seen


# -- the managed transport (the customer path) -------------------------------
def test_managed_sends_a_named_user_agent(monkeypatch):
    """Not urllib's default. Cloudflare 1010 refuses that outright."""
    from app.feed import managed

    seen = _capture(monkeypatch, managed, {"ok": True})
    provider = managed.ManagedProvider("DAWN-KEY")
    status, payload = provider._call("/v1/anything")

    request = seen["request"]
    assert request.get_header("User-agent") == USER_AGENT
    assert "Python-urllib" not in (request.get_header("User-agent") or "")
    assert request.get_header("Authorization") == "Bearer DAWN-KEY"
    assert (status, payload) == (200, {"ok": True})


def test_managed_returns_a_refusal_rather_than_raising(monkeypatch):
    """spec 6.2: surfaced, never swallowed — and never an exception through
    the middle of a morning run."""
    from app.feed import managed

    err = urllib.error.HTTPError(
        "https://x/y", 403, "Forbidden", {},
        __import__("io").BytesIO(json.dumps({"error": "licence_inactive"}).encode()))
    _capture(monkeypatch, managed, raises=err)

    status, payload = managed.ManagedProvider("DAWN-KEY")._call("/v1/anything")
    assert status == 403
    assert payload["error"] == "licence_inactive"


def test_managed_reports_unreachable_as_no_status(monkeypatch):
    """A `None` status is 'the network said nothing', which is not a refusal."""
    from app.feed import managed

    _capture(monkeypatch, managed, raises=OSError("dns went away"))
    status, payload = managed.ManagedProvider("DAWN-KEY")._call("/v1/anything")
    assert status is None
    assert payload["error"] == "unreachable"


# -- the theirstack transport (the developer path) ---------------------------
def test_theirstack_sends_a_named_user_agent(monkeypatch):
    """This transport shipped WITHOUT one until 2026-09-09, while managed.py
    carried the fix and the comment explaining why."""
    from app.feed import theirstack

    seen = _capture(monkeypatch, theirstack, {"data": []})
    provider = theirstack.TheirStackProvider("TS-KEY")
    monkeypatch.setattr(provider._limiter, "wait", lambda: None)
    status, payload = provider._call("/v1/jobs/search", {"limit": 1}, "POST")

    request = seen["request"]
    assert request.get_header("User-agent") == USER_AGENT
    assert "Python-urllib" not in (request.get_header("User-agent") or "")
    assert request.get_header("Authorization") == "Bearer TS-KEY"
    assert status == 200


def test_theirstack_gives_up_rather_than_raising(monkeypatch):
    """Three attempts, then a reported failure. A morning run must not die on
    a provider outage."""
    from app.feed import theirstack

    _capture(monkeypatch, theirstack, raises=OSError("connection reset"))
    provider = theirstack.TheirStackProvider("TS-KEY")
    monkeypatch.setattr(provider._limiter, "wait", lambda: None)

    status, payload = provider._call("/v1/jobs/search", {"limit": 1}, "POST")
    assert status is None
    assert "connection reset" in str(payload)
