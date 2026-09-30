"""Opt-in diagnostics: off by default, redacted at ONE chokepoint, honest.

What these protect, in one line each:
  - nothing is written, and no directory is made, unless diagnostics are on;
  - a credential, a key-shaped value, an email or a job description never
    reaches the file, whichever call site passed it;
  - the network hook records status, timing and the API's own error body — the
    thing that made a bug ("Invalid query parameters") findable — while the
    caller still receives an intact error;
  - no other module can write into the diagnostics directory, so the redaction
    rule cannot be walked around.
"""
from __future__ import annotations

import ast
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from app.core import diagnostics as diag

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"


@pytest.fixture(autouse=True)
def _reset():
    diag.disable()
    yield
    diag.disable()


def lines(directory: Path) -> list[dict]:
    out = []
    for path in sorted(directory.glob("dawnlist-*.jsonl")):
        out += [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    return out


def enabled(tmp_path, **kw) -> Path:
    diag.enable(tmp_path, hook_exceptions=False, **kw)
    return tmp_path / diag.SUBDIR


# -- off by default ----------------------------------------------------------

def test_off_by_default_writes_nothing_and_makes_no_directory(tmp_path):
    diag.event("anything", token="secret")
    with diag.span("stage"):
        pass
    assert not diag.is_enabled()
    assert not (tmp_path / diag.SUBDIR).exists()


def test_requested_by_flag_env_or_marker_file(tmp_path):
    assert not diag.requested(tmp_path, environ={})
    assert diag.requested(tmp_path, flag=True, environ={})
    assert diag.requested(tmp_path, environ={diag.SWITCH_ENV: "1"})
    assert not diag.requested(tmp_path, environ={diag.SWITCH_ENV: "0"})
    (tmp_path / diag.MARKER_NAME).write_text("")
    assert diag.requested(tmp_path, environ={})  # the only route for a packaged launch


def test_enabling_records_the_start_and_prunes_nothing_recent(tmp_path):
    out = enabled(tmp_path, hook_network=False)
    first = lines(out)[0]
    assert first["event"] == "app.start" and "version" in first


# -- redaction ---------------------------------------------------------------

SECRETS = {
    "authorization": "Bearer abcDEF123456",
    "api_key": "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    "licence_key": "DAWN-AAAA-BBBB",
    "password": "hunter2",
    "access_token": "AQXt" + "x" * 60,
    "Cookie": "session=abc",
}


@pytest.mark.parametrize("key,value", SECRETS.items())
def test_a_credential_named_key_is_never_written(tmp_path, key, value):
    out = enabled(tmp_path, hook_network=False)
    diag.event("t", **{key: value})
    blob = json.dumps(lines(out))
    assert value not in blob and diag._REDACTED in blob


def test_a_key_shaped_value_is_redacted_even_under_an_innocent_name(tmp_path):
    out = enabled(tmp_path, hook_network=False)
    diag.event("t", note="sk-ant-api03-ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ",
               other="Bearer abcdef.ghijkl", digest="a" * 64,
               who="someone@example.com")
    blob = json.dumps(lines(out))
    for leak in ("sk-ant", "abcdef.ghijkl", "a" * 64, "someone@example.com"):
        assert leak not in blob


def test_free_text_is_logged_as_a_length_and_hash_only(tmp_path):
    out = enabled(tmp_path, hook_network=False)
    diag.event("t", description="A very private job description " * 20,
               cv="my whole CV")
    rec = lines(out)[-1]
    assert rec["description"].startswith("[text len=")
    assert "private" not in json.dumps(rec) and "whole CV" not in json.dumps(rec)


def test_nested_structures_and_long_strings_are_handled(tmp_path):
    out = enabled(tmp_path, hook_network=False)
    diag.event("t", nested={"a": [{"token": "x"}, "word " * 200]})
    blob = lines(out)[-1]["nested"]
    assert blob["a"][0]["token"] == diag._REDACTED
    assert "[+" in blob["a"][1]  # truncated, with how much was cut


def test_url_query_values_named_like_secrets_are_scrubbed():
    url = diag.scrub_url("https://api.example.com/x?token=abc123&keyword=Hotel%20Manager")
    assert "abc123" not in url and "Hotel" in url


def test_a_log_write_can_never_raise_into_the_run(tmp_path, monkeypatch):
    enabled(tmp_path, hook_network=False)
    monkeypatch.setattr(diag, "redact", lambda *a, **k: 1 / 0)
    diag.event("t", x=1)  # must not raise


# -- spans -------------------------------------------------------------------

def test_a_span_records_duration_and_what_the_stage_produced(tmp_path):
    out = enabled(tmp_path, hook_network=False)
    with diag.span("pipeline.fetch", run_id=3) as extra:
        extra["postings"] = 20
    rec = lines(out)[-1]
    assert rec["event"] == "pipeline.fetch" and rec["ok"] is True
    assert rec["postings"] == 20 and rec["run_id"] == 3 and rec["ms"] >= 0


def test_a_failing_span_records_the_error_and_reraises(tmp_path):
    out = enabled(tmp_path, hook_network=False)
    with pytest.raises(ValueError):
        with diag.span("pipeline.assess"):
            raise ValueError("boom")
    rec = lines(out)[-1]
    assert rec["ok"] is False and "ValueError: boom" in rec["error"]


def test_the_file_is_capped_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(diag, "MAX_BYTES", 600)
    out = enabled(tmp_path, hook_network=False)
    for i in range(50):
        diag.event("spam", i=i, pad="x" * 40)
    events = [r["event"] for r in lines(out)]
    assert "log.truncated" in events and events.count("log.truncated") == 1


# -- the network hook --------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/bad"):
            body = b'{"message":"Invalid query parameters passed to request"}'
            self.send_response(400)
        else:
            body = b'{"ok":true}'
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # keep test output quiet
        pass


@pytest.fixture
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_http_success_is_logged_without_the_authorization_header(tmp_path, server):
    out = enabled(tmp_path)
    req = urllib.request.Request(server + "/ok?token=SECRETVALUE&keyword=hotel",
                                 headers={"Authorization": "Bearer TOPSECRET123"})
    with urllib.request.urlopen(req) as r:
        assert json.load(r) == {"ok": True}
    blob = json.dumps(lines(out))
    rec = [x for x in lines(out) if x["event"] == "http"][-1]
    assert rec["status"] == 200 and rec["ok"] is True and rec["ms"] >= 0
    assert "TOPSECRET123" not in blob and "SECRETVALUE" not in blob
    assert "keyword=hotel" in rec["url"]


def test_http_error_body_is_logged_and_the_caller_still_gets_it(tmp_path, server):
    out = enabled(tmp_path)
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(server + "/bad")
    # the caller's copy is intact: the hook read the body and handed it back
    assert b"Invalid query parameters" in caught.value.read()
    rec = [x for x in lines(out) if x["event"] == "http"][-1]
    assert rec["status"] == 400 and rec["ok"] is False
    assert "Invalid query parameters" in rec["error_body"]


def test_a_connection_failure_is_logged_as_an_error(tmp_path):
    out = enabled(tmp_path)
    with pytest.raises(Exception):
        urllib.request.urlopen("http://127.0.0.1:1/", timeout=2)
    rec = [x for x in lines(out) if x["event"] == "http"][-1]
    assert rec["ok"] is False and rec["error"]


def test_disabling_removes_the_network_hook(tmp_path, server):
    out = enabled(tmp_path)
    diag.disable()
    n = len(lines(out))
    urllib.request.urlopen(server + "/ok").read()
    assert len(lines(out)) == n


# -- the chokepoint ----------------------------------------------------------

def test_only_the_diagnostics_module_writes_the_log_or_configures_file_logging():
    """`event()` is the only writer. Another module writing the file directly
    would bypass redaction — the failure recorded in `app/core/http.py`."""
    offenders = []
    for path in APP.rglob("*.py"):
        if path.name == "diagnostics.py":
            continue
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in (
                    "FileHandler", "RotatingFileHandler", "basicConfig"):
                offenders.append(f"{path.relative_to(ROOT)}: logging.{node.attr}")
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and (
                    ".jsonl" in node.value or node.value == "diagnostics"):
                if "help" not in text.split(node.value)[0][-80:]:
                    offenders.append(f"{path.relative_to(ROOT)}: {node.value!r}")
    assert not offenders, offenders
