"""The update check: what it must never do, and what it must not say.

The assertions that matter here are the NEGATIVE ones. An update checker that
works is pleasant; an update checker that fires in a Mac App Store build is a
rejection, and one that shouts on a network blip is an error box in front of
somebody at 7am. Both of those are tested first.
"""
from __future__ import annotations

import json
import urllib.error

import pytest

from app.core import updates


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _opener(payload, *, status=200):
    """A stand-in for urllib.request.urlopen.

    Injected at the SAME layer the real call uses, so `build_request` still
    runs and the User-Agent is still attached. Mocking higher than this is how
    the Cloudflare 1010 fault stayed invisible through a green suite.
    """
    class _Response:
        def __init__(self, raw):
            self._raw = raw

        def read(self, size=-1):
            return self._raw if size < 0 else self._raw[:size]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def opener(request, timeout=None):
        opener.seen = request
        return _Response(raw)

    opener.seen = None
    return opener


def _manifest(version="9.9.9", **over):
    m = {"version": version,
         "url": f"https://dawnlist.spencerfields.com/downloads/Dawnlist-windows-{version}.zip",
         "sha256": "0" * 64,
         "notes_url": "https://dawnlist.spencerfields.com/whats-new.html",
         "minimum": "1.0.0"}
    m.update(over)
    return m


@pytest.fixture
def state(tmp_path):
    """A state file that already claims a check long ago, so `due` is true."""
    p = tmp_path / "update-check.json"
    p.write_text(json.dumps({"last": 0.0}), encoding="utf-8")
    return p


@pytest.fixture
def direct(monkeypatch):
    # Windows too: the manifest is the Windows download, and the check is
    # gated on the platform as well as the variant. Pinned so the suite
    # means the same on the macOS and Linux runners.
    monkeypatch.setattr(updates, "variant", lambda: "direct")
    monkeypatch.setattr(updates.sys, "platform", "win32")


# ---------------------------------------------------------------------------
# Windows only, one host, and the checksum shown
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_a_direct_build_off_windows_never_checks(monkeypatch, state, platform):
    """windows.json describes a Windows zip. A direct build anywhere else
    would be told to download it."""
    monkeypatch.setattr(updates, "variant", lambda: "direct")
    monkeypatch.setattr(updates.sys, "platform", platform)
    opener = _opener(_manifest("9.9.9"))
    assert updates.check(current="1.0.0", opener=opener, state_path=state) is None
    assert opener.seen is None


def test_a_direct_build_on_windows_does_check(direct, state):
    """The positive control for the platform gate."""
    opener = _opener(_manifest("9.9.9"))
    assert updates.check(current="1.0.0", opener=opener, state_path=state) is not None
    assert opener.seen is not None


@pytest.mark.parametrize("bad", [
    "https://evil.example/Dawnlist-windows-9.9.9.zip",
    "https://dawnlist.spencerfields.com.evil.example/x.zip",   # starts right
    "https://dawnlist.spencerfields.com@evil.example/x.zip",   # user-info
    "https://evil.example/dawnlist.spencerfields.com/x.zip",   # host in path
    "https://dawnlist.spencerfields.com:8443/x.zip",           # another port
    "https://spencerfields.com/x.zip",                         # parent domain
])
def test_a_download_on_any_other_host_is_refused(direct, state, bad):
    """"https://" alone let a manifest point at anybody's binary."""
    assert updates.check(current="1.1.0", opener=_opener(_manifest(url=bad)),
                         state_path=state) is None


def test_the_checksum_and_size_are_carried_to_the_prompt(direct, state):
    found = updates.check(current="1.1.0", state_path=state, opener=_opener(
        _manifest("1.2.0", sha256="AB" * 32, size=48_123_456)))
    assert found.sha256 == "ab" * 32
    assert found.size == 48_123_456


@pytest.mark.parametrize("digest", ["abc", "z" * 64, 123, "0" * 63])
def test_a_malformed_checksum_refuses_the_update(direct, state, digest):
    """It would be shown as the thing to check the download against."""
    assert updates.check(current="1.1.0", state_path=state,
                         opener=_opener(_manifest("1.2.0", sha256=digest))) is None


def test_an_unpublished_checksum_still_offers_the_update(direct, state):
    manifest = _manifest("1.2.0")
    del manifest["sha256"]
    found = updates.check(current="1.1.0", state_path=state, opener=_opener(manifest))
    assert found is not None and found.sha256 is None


@pytest.mark.parametrize("size", ["big", -1, 0, True, 1.5])
def test_a_nonsense_size_is_dropped_not_fatal(direct, state, size):
    found = updates.check(current="1.1.0", state_path=state,
                          opener=_opener(_manifest("1.2.0", size=size)))
    assert found is not None and found.size is None


# ---------------------------------------------------------------------------
# The variant gate. This is the one that would get the Mac app rejected.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("build", ["store", "mas", "none", "ambiguous"])
def test_only_the_direct_build_ever_checks(monkeypatch, state, build):
    """A Store build is updated by the Store; a Mac App Store build that
    shipped its own update path would be rejected on review."""
    monkeypatch.setattr(updates, "variant", lambda: build)
    opener = _opener(_manifest())
    assert updates.check(current="1.0.0", opener=opener, state_path=state) is None
    assert opener.seen is None, f"a {build} build reached the network"


def test_force_does_not_skip_the_variant_gate(monkeypatch, state):
    """`force` exists for a "check now" button. It must not become a way to
    make a Store build phone home."""
    monkeypatch.setattr(updates, "variant", lambda: "mas")
    opener = _opener(_manifest())
    assert updates.check(current="1.0.0", opener=opener,
                         state_path=state, force=True) is None
    assert opener.seen is None


# ---------------------------------------------------------------------------
# Silence on failure
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("boom", [
    urllib.error.URLError("no network"),
    urllib.error.HTTPError("u", 404, "Not Found", {}, None),
    urllib.error.HTTPError("u", 406, "Not Acceptable", {}, None),
    TimeoutError("slow"),
    ValueError("nonsense"),
])
def test_every_transport_failure_is_silent(direct, state, boom):
    def opener(request, timeout=None):
        raise boom
    assert updates.check(current="1.0.0", opener=opener, state_path=state) is None


@pytest.mark.parametrize("body", [
    b"<html>hello</html>",          # a captive portal or an error page
    b"",                            # nothing at all
    b"[1, 2, 3]",                   # valid JSON, wrong shape
    b'{"version": 110}',            # right key, wrong type
    b'{"url": "https://x/y.zip"}',  # no version
])
def test_a_manifest_that_is_not_one_is_silent(direct, state, body):
    assert updates.check(current="1.0.0", opener=_opener(body),
                         state_path=state) is None


def test_an_absurdly_large_body_is_refused_without_parsing(direct, state):
    huge = b'{"version": "9.9.9", "pad": "' + b"x" * (updates.MAX_MANIFEST_BYTES + 10) + b'"}'
    assert updates.check(current="1.0.0", opener=_opener(huge),
                         state_path=state) is None


# ---------------------------------------------------------------------------
# Version comparison
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("candidate,current,expected", [
    ("1.1.1", "1.1.0", True),
    ("1.2.0", "1.1.9", True),
    ("2.0.0", "1.9.9", True),
    ("1.1.0", "1.1.0", False),
    ("1.0.9", "1.1.0", False),
    ("1.1", "1.1.0", False),      # zero-padded: the same version
    ("1.1.0.1", "1.1.0", True),   # four parts, for a hotfix respin
])
def test_is_newer(candidate, current, expected):
    assert updates.is_newer(candidate, current) is expected


@pytest.mark.parametrize("text", ["latest", "v2.0", "2.0-beta", "", "1.2.x",
                                  "-1.0.0", "1.2.3.4.5", None])
def test_an_unparseable_version_never_prompts(text):
    """Coercing "latest" into something orderable would prompt on every launch
    for an update that does not exist."""
    assert updates.parse_version(text) is None
    assert updates.is_newer(text, "1.1.0") is False


def test_the_same_version_does_not_prompt(direct, state):
    assert updates.check(current="1.1.0", opener=_opener(_manifest("1.1.0")),
                         state_path=state) is None


def test_a_newer_version_is_reported(direct, state):
    found = updates.check(current="1.1.0", opener=_opener(_manifest("1.2.0")),
                          state_path=state)
    assert found is not None
    assert found.version == "1.2.0"
    assert found.url.endswith("Dawnlist-windows-1.2.0.zip")
    assert found.notes_url == "https://dawnlist.spencerfields.com/whats-new.html"


# ---------------------------------------------------------------------------
# It sends people somewhere safe, or nowhere
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "http://dawnlist.spencerfields.com/x.zip",   # plaintext
    "ftp://example.com/x.zip",
    "file:///C:/Windows/System32/x.zip",
    "javascript:alert(1)",
    "",
])
def test_a_download_url_that_is_not_https_is_refused(direct, state, bad):
    """The artefact is signed precisely so its origin can be trusted. Fetching
    it over a channel that can be rewritten in flight throws that away."""
    assert updates.check(current="1.1.0", opener=_opener(_manifest(url=bad)),
                         state_path=state) is None


def test_a_non_https_notes_url_is_dropped_not_fatal(direct, state):
    found = updates.check(current="1.1.0", state_path=state,
                          opener=_opener(_manifest("1.2.0",
                                                   notes_url="http://x/n.html")))
    assert found is not None and found.notes_url is None


# ---------------------------------------------------------------------------
# Cadence
# ---------------------------------------------------------------------------

def test_the_first_run_never_checks(direct, tmp_path):
    """First launch already asks for an API key, a subscription and ten
    calibration verdicts, and the build was downloaded minutes ago."""
    path = tmp_path / "update-check.json"
    opener = _opener(_manifest("9.9.9"))
    assert updates.check(current="1.1.0", opener=opener, state_path=path) is None
    assert opener.seen is None
    assert path.exists(), "the first run must record the time so the next one checks"


def test_a_second_check_the_same_day_does_not_reach_the_network(direct, tmp_path):
    path = tmp_path / "update-check.json"
    updates.check(current="1.1.0", opener=_opener(_manifest()),
                  state_path=path, now=1000.0)          # first run: stamps only
    opener = _opener(_manifest("9.9.9"))
    assert updates.check(current="1.1.0", opener=opener, state_path=path,
                         now=1000.0 + 60) is None
    assert opener.seen is None


def test_it_checks_again_after_a_day(direct, tmp_path):
    path = tmp_path / "update-check.json"
    updates.check(current="1.1.0", opener=_opener(_manifest()),
                  state_path=path, now=1000.0)
    later = 1000.0 + updates.CHECK_INTERVAL_SECONDS + 1
    found = updates.check(current="1.1.0", opener=_opener(_manifest("1.2.0")),
                          state_path=path, now=later)
    assert found is not None and found.version == "1.2.0"


def test_force_checks_immediately(direct, tmp_path):
    path = tmp_path / "update-check.json"
    updates.check(current="1.1.0", opener=_opener(_manifest()),
                  state_path=path, now=1000.0)
    found = updates.check(current="1.1.0", opener=_opener(_manifest("1.2.0")),
                          state_path=path, now=1000.0 + 1, force=True)
    assert found is not None


def test_an_unwritable_state_file_does_not_break_the_check(direct, tmp_path):
    """A read-only profile must not stop the app launching."""
    path = tmp_path / "nope" / "deep" / "update-check.json"
    path.parent.mkdir(parents=True)
    path.parent.chmod(0o500)
    try:
        updates.check(current="1.1.0", opener=_opener(_manifest()),
                      state_path=path)
    finally:
        path.parent.chmod(0o700)


# ---------------------------------------------------------------------------
# The User-Agent, which is the whole reason this can work at all
# ---------------------------------------------------------------------------

def test_the_request_carries_the_dawnlist_agent(direct, state):
    """The live host answers 406 Not Acceptable to urllib's default agent.
    Measured 2026-09-10. Without this the check returns None forever, which is
    indistinguishable from "you are up to date"."""
    opener = _opener(_manifest("1.2.0"))
    updates.check(current="1.1.0", opener=opener, state_path=state)
    agent = opener.seen.get_header("User-agent") or ""
    assert agent.startswith("Dawnlist/")
    assert "urllib" not in agent.lower()


def test_the_manifest_url_is_https(direct):
    assert updates.MANIFEST_URL.startswith("https://")


# ---------------------------------------------------------------------------
# The wiring. A tested checker nothing calls is a missing capability, not a
# finished one — this project has shipped that failure before.
# ---------------------------------------------------------------------------

def test_the_launch_path_actually_calls_the_checker():
    """`_offer_update` must be invoked from `main`, after the window is shown.

    Asserted against the source because the alternative is running the whole
    Qt application. Before this, `updates.check` had 46 green tests and no
    production caller at all.
    """
    import ast
    import inspect

    from app import main as main_module

    tree = ast.parse(inspect.getsource(main_module))

    def calls_of(fn):
        return {n.func.id for n in ast.walk(fn)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}

    def attr_calls_of(fn):
        return {n.func.attr for n in ast.walk(fn)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}

    # Whichever function shows the window is the one that must offer the
    # update. Naming `main` here was wrong: the launch path lives in a helper,
    # and a test pinned to the wrong function passes for the wrong reason.
    showing = [n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and "show" in attr_calls_of(n)]
    assert showing, "no function in main.py shows a window"
    assert any("_offer_update" in calls_of(n) for n in showing), (
        "the function that shows the window never calls the update check")

    body = inspect.getsource(main_module)
    assert body.index("window.show()") < body.rindex("_offer_update(window)"), (
        "the prompt must come after the window is shown, or it is a modal "
        "with nothing behind it")


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


def test_the_prompt_is_a_no_op_when_there_is_no_update(qapp, settle, monkeypatch):
    from app import main as main_module

    shown = []
    monkeypatch.setattr("app.core.updates.check", lambda *a, **k: None)
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.exec",
                        lambda self: shown.append(self))
    main_module._offer_update(None, delay_ms=0)       # must not raise
    settle(lambda: not main_module._UPDATE_TASKS and _spun(qapp), what="the check")
    main_module._show_update(None, None)
    assert shown == [], "prompted with nothing to offer"


def test_a_failure_inside_the_check_never_blocks_the_launch(qapp, settle, monkeypatch):
    """An update check must never be the reason the application did not open."""
    from app import main as main_module

    def boom(*a, **k):
        raise RuntimeError("catalogue on fire")

    monkeypatch.setattr("app.core.updates.check", boom)
    main_module._offer_update(None, delay_ms=0)       # swallowed
    settle(lambda: not main_module._UPDATE_TASKS and _spun(qapp), what="the check")


def test_the_daily_check_waits_for_the_window_and_runs_off_the_ui_thread(
        qapp, settle, monkeypatch):
    """Inline after `window.show()`, a slow manifest host held the first paint
    of the shortlist for up to its ten-second timeout."""
    import threading

    from app import main as main_module

    seen, shown = {}, []

    def checker():
        seen["thread"] = threading.current_thread()
        return updates.Update(version="9.9.9",
                              url="https://dawnlist.spencerfields.com/x.zip")

    monkeypatch.setattr(main_module, "_show_update",
                        lambda parent, found: shown.append((found, threading.current_thread())))
    main_module._offer_update(None, checker=checker, delay_ms=0)
    assert seen == {}, "the check ran inside the launch call"

    settle(lambda: shown, what="the update check")
    assert seen["thread"] is not threading.main_thread()
    found, shown_on = shown[0]
    assert found.version == "9.9.9"
    assert shown_on is threading.main_thread(), "a dialog belongs on the UI thread"


def test_the_prompt_shows_the_checksum_and_size_to_verify_against():
    from app import main as main_module

    found = updates.Update(version="1.2.0",
                           url="https://dawnlist.spencerfields.com/x.zip",
                           sha256="ab" * 32, size=48_123_456)
    headline, body = main_module._update_texts(found)
    assert "1.2.0" in headline
    assert "ab" * 32 in body
    assert "45.9 MB" in body

    bare = updates.Update(version="1.2.0",
                          url="https://dawnlist.spencerfields.com/x.zip")
    assert "SHA-256" not in main_module._update_texts(bare)[1]


def _spun(qapp):
    """True once the event loop has had a turn, so a zero-delay timer fires."""
    qapp.processEvents()
    return True
