"""Opt-in diagnostics: a structured record of what the app did, for review.

OFF BY DEFAULT, and the same build either way. A separate "logging build" would
differ from the shipped one in exactly the ways that matter (package identity,
edition flag, signing), so a fault seen there proves little about what
customers run. Instead the shipped build carries this module dormant, and it
is switched on by ANY of:

  * ``--diagnostics`` on the command line;
  * ``DAWNLIST_DIAGNOSTICS=1`` in the environment;
  * an empty file named ``diagnostics.on`` in the app-data directory — the only
    one of the three that works for a packaged (MSIX) launch a developer cannot
    add arguments to.

Output is JSON lines, one file per UTC day, in ``<app data>/diagnostics/``,
pruned after `KEEP_DAYS` and capped at `MAX_BYTES` a file. Nothing is ever sent
anywhere.

THE REDACTION RULE — one chokepoint, not a convention
-----------------------------------------------------
Every value passes through `redact` inside `event()`, the ONLY function that
writes. There is deliberately no other way to put a line in the file, and
`tests/test_diagnostics.py` walks the AST of `app/` and fails if any other
module touches the diagnostics directory or a log file handler. A rule every
call site has to remember is the failure recorded in `app/core/http.py`.

What is removed, whatever the caller passed:
  * anything whose KEY names a credential (token, key, secret, password,
    licence, authorization, cookie ...);
  * anything that LOOKS like one (Bearer values, sk-ant- keys, 32+ hex chars,
    long unbroken base64-ish runs);
  * email addresses;
  * free text keys (description, body, cv, letter, brief, prompt ...) — logged
    as a length and a short hash only. Job descriptions are third-party data
    held under the feed's terms and a CV is the user's own; neither belongs in
    a file that gets attached to a bug report.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

KEEP_DAYS = 7
MAX_BYTES = 20 * 1024 * 1024
MAX_STRING = 300
SWITCH_ENV = "DAWNLIST_DIAGNOSTICS"
MARKER_NAME = "diagnostics.on"
SUBDIR = "diagnostics"

_SECRET_KEY = re.compile(
    r"(token|secret|passw|api[-_]?key|apikey|licen[cs]e|authoriz|cookie|"
    r"credential|bearer|signature|(^|[-_])key($|[-_]))", re.I)
_TEXT_KEY = re.compile(
    r"^(description|description_text|body|text|content|cv|letter|brief|"
    r"prompt|fit_brief|factsheet|reply|message)$", re.I)
_SECRET_VALUE = [
    re.compile(r"Bearer\s+[A-Za-z0-9._\-~+/=]+", re.I),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]+"),
    re.compile(r"\b[A-Fa-f0-9]{32,}\b"),
    re.compile(r"\b[A-Za-z0-9_\-+/=]{40,}\b"),
]
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_REDACTED = "[redacted]"

_lock = threading.Lock()
_state: dict[str, Any] = {"dir": None, "written": 0, "day": None,
                          "truncated": False}


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

def _scrub_string(value: str) -> str:
    for pattern in _SECRET_VALUE:
        value = pattern.sub(_REDACTED, value)
    value = _EMAIL.sub("[email]", value)
    if len(value) > MAX_STRING:
        value = value[:MAX_STRING] + f"...[+{len(value) - MAX_STRING} chars]"
    return value


def scrub_url(url: str) -> str:
    """Keep the path and the names of query parameters, drop sensitive values.

    A search keyword is kept: it is what the user asked for and is the thing a
    reviewer needs to reproduce a request.
    """
    try:
        parts = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        kept = [(k, _REDACTED if _SECRET_KEY.search(k) else v)
                for k, v in query]
        clean = parts._replace(query=urllib.parse.urlencode(kept),
                               netloc=parts.hostname or "")
        return _scrub_string(urllib.parse.urlunsplit(clean))
    except Exception:  # noqa: BLE001 - a log line must never raise
        return _REDACTED


def redact(value: Any, key: str = "") -> Any:
    """Return a JSON-safe copy of `value` with secrets and free text removed."""
    if key and _SECRET_KEY.search(key):
        return _REDACTED
    if isinstance(value, dict):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact(v, key) for v in list(value)[:200]]
    if isinstance(value, str):
        if key and _TEXT_KEY.match(key):
            digest = hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()
            return f"[text len={len(value)} sha={digest[:8]}]"
        return _scrub_string(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, Path):
        return _scrub_string(str(value))
    if isinstance(value, BaseException):
        return _scrub_string(f"{type(value).__name__}: {value}")
    return _scrub_string(repr(value))


# ---------------------------------------------------------------------------
# Switching on
# ---------------------------------------------------------------------------

def is_enabled() -> bool:
    return _state["dir"] is not None


def requested(app_dir: Path, *, flag: bool = False,
              environ: dict | None = None) -> bool:
    env = os.environ if environ is None else environ
    return bool(flag or env.get(SWITCH_ENV) == "1"
                or (app_dir / MARKER_NAME).exists())


def enable(app_dir: Path, *, hook_network: bool = True,
           hook_exceptions: bool = True) -> Path:
    """Start writing. Returns the directory. Safe to call twice."""
    directory = Path(app_dir) / SUBDIR
    directory.mkdir(parents=True, exist_ok=True)
    with _lock:
        _state.update(dir=directory, written=0, day=None, truncated=False)
    _prune(directory)
    if hook_exceptions:
        _hook_exceptions()
    if hook_network:
        _hook_network()
    from app.version import marketing_version
    event("app.start", version=marketing_version(),
          python=sys.version.split()[0], platform=sys.platform,
          frozen=bool(getattr(sys, "frozen", False)),
          argv=[a for a in sys.argv[1:]])
    return directory


def disable() -> None:
    """Stop writing and remove the network hook (tests, and a clean exit)."""
    with _lock:
        _state.update(dir=None)
    _unhook_network()


def _prune(directory: Path) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)
    for path in directory.glob("dawnlist-*.jsonl"):
        try:
            if datetime.fromtimestamp(path.stat().st_mtime,
                                      timezone.utc) < cutoff:
                path.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# The one writer
# ---------------------------------------------------------------------------

def event(name: str, **fields: Any) -> None:
    """Write one line. THE ONLY WRITER; a no-op when diagnostics are off.

    Never raises: a diagnostic that can break the run it describes is worse
    than none.
    """
    directory = _state["dir"]
    if directory is None:
        return
    try:
        now = datetime.now(timezone.utc)
        record = {"ts": now.isoformat(timespec="milliseconds"),
                  "thread": threading.current_thread().name,
                  "event": name}
        record.update({k: redact(v, k) for k, v in fields.items()})
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        with _lock:
            if _state["truncated"]:
                return
            day = now.strftime("%Y-%m-%d")
            if _state["day"] != day:
                _state.update(day=day, written=0)
            if _state["written"] + len(line) > MAX_BYTES:
                _state["truncated"] = True
                line = json.dumps({"ts": record["ts"], "event": "log.truncated",
                                   "limit_bytes": MAX_BYTES}) + "\n"
            path = directory / f"dawnlist-{day}.jsonl"
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line)
            _state["written"] += len(line)
    except Exception:  # noqa: BLE001
        pass


@contextlib.contextmanager
def span(name: str, **fields: Any) -> Iterator[dict]:
    """Time a block. The yielded dict collects fields to log at the end, so a
    stage can report what it produced, not only how long it took."""
    out: dict[str, Any] = {}
    started = time.perf_counter()
    try:
        yield out
    except BaseException as exc:
        event(name, ok=False, ms=round((time.perf_counter() - started) * 1000),
              error=exc, **fields, **out)
        raise
    else:
        event(name, ok=True, ms=round((time.perf_counter() - started) * 1000),
              **fields, **out)


# ---------------------------------------------------------------------------
# Hooks: uncaught exceptions and every urllib request
# ---------------------------------------------------------------------------

_hooked = {"exceptions": False}


def _hook_exceptions() -> None:
    if _hooked["exceptions"]:
        return
    _hooked["exceptions"] = True
    previous = sys.excepthook

    def hook(exc_type, exc, tb):
        import traceback
        event("exception.uncaught", type=exc_type.__name__, error=exc,
              trace="".join(traceback.format_tb(tb))[-1500:])
        previous(exc_type, exc, tb)

    sys.excepthook = hook
    previous_thread = threading.excepthook

    def thread_hook(args):
        import traceback
        event("exception.thread", type=args.exc_type.__name__,
              error=args.exc_value,
              trace="".join(traceback.format_tb(args.exc_traceback))[-1500:])
        previous_thread(args)

    threading.excepthook = thread_hook


class _Replay:
    """The response a caller would have got, after the hook has read its body.

    Everything (status, headers, `msg`, `info()`) is delegated to the original,
    which urllib's error processing needs; only the body methods replay the
    bytes already read. A bare `addinfourl` lacks `.msg` and crashed inside
    urllib on the first real HTTP error.
    """

    def __init__(self, response, body: bytes):
        import io
        self._response = response
        self._buf = io.BytesIO(body)

    def __getattr__(self, name):
        return getattr(self._response, name)

    def read(self, amt=None):
        return self._buf.read() if amt is None else self._buf.read(amt)

    def readline(self, *a):
        return self._buf.readline(*a)

    def readlines(self, *a):
        return self._buf.readlines(*a)

    def __iter__(self):
        return iter(self._buf)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self._buf.close()
        self._response.close()


def _make_handlers():
    import http.client

    def wrap(base, connection):
        class Logged(base):
            def do_open(self, http_class, req, **kwargs):
                started = time.perf_counter()
                url = scrub_url(req.full_url)
                try:
                    response = super().do_open(http_class, req, **kwargs)
                except Exception as exc:  # DNS, TLS, timeout: no response at all
                    event("http", method=req.get_method(), url=url, ok=False,
                          ms=round((time.perf_counter() - started) * 1000),
                          error=exc)
                    raise
                status = getattr(response, "status", None)
                fields = dict(method=req.get_method(), url=url, status=status,
                              ms=round((time.perf_counter() - started) * 1000))
                if status is not None and status >= 400:
                    # urllib raises AFTER this returns, and the body is where
                    # the API says WHAT was wrong ("Invalid query parameters"
                    # was the only clue to a bug that made every search fail).
                    # Read it once and hand the caller an identical response.
                    body = response.read()
                    fields["error_body"] = body[:MAX_STRING].decode(
                        "utf-8", "replace")
                    response = _Replay(response, body)
                event("http", ok=status is not None and status < 400, **fields)
                return response
        return Logged

    class HTTPH(wrap(urllib.request.HTTPHandler, http.client.HTTPConnection)):
        def http_open(self, req):
            return self.do_open(http.client.HTTPConnection, req)

    class HTTPSH(wrap(urllib.request.HTTPSHandler, http.client.HTTPSConnection)):
        def https_open(self, req):
            return self.do_open(http.client.HTTPSConnection, req,
                                context=self._context)

    return HTTPH, HTTPSH


def _hook_network() -> None:
    HTTPH, HTTPSH = _make_handlers()
    urllib.request.install_opener(
        urllib.request.build_opener(HTTPH(), HTTPSH()))


def _unhook_network() -> None:
    urllib.request.install_opener(urllib.request.build_opener())


# ---------------------------------------------------------------------------
# The window: clicks and screens, not what the user typed
# ---------------------------------------------------------------------------

def install_qt_hooks(app) -> None:
    """Log button presses and window/dialog appearances.

    Widget class, object name and a button's own label are logged; NOTHING a
    user typed is, because an event filter cannot tell a search box from a
    password field and guessing wrong would defeat the redaction rule.
    """
    if not is_enabled():
        return
    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtWidgets import (QAbstractButton, QDialog, QMainWindow,
                                   QTabBar)

    class Filter(QObject):
        def eventFilter(self, obj, ev):  # noqa: N802 - Qt naming
            try:
                kind = ev.type()
                if kind == QEvent.MouseButtonRelease and isinstance(
                        obj, QAbstractButton):
                    event("ui.click", widget=type(obj).__name__,
                          name=obj.objectName(), label=obj.text())
                elif kind == QEvent.Show and isinstance(
                        obj, (QDialog, QMainWindow)):
                    event("ui.show", widget=type(obj).__name__,
                          name=obj.objectName(), title=obj.windowTitle())
                elif kind == QEvent.MouseButtonRelease and isinstance(
                        obj, QTabBar):
                    event("ui.tab", name=obj.objectName(),
                          current=obj.tabText(obj.currentIndex()))
            except Exception:  # noqa: BLE001
                pass
            return False

    hook = Filter(app)
    app.installEventFilter(hook)
    app._diagnostics_filter = hook  # keep a reference alive
