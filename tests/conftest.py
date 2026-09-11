"""Test setup.

Qt widgets shown during tests appear on the developer's actual desktop — every
run flashes windows open and closed, which is intrusive when the suite runs
repeatedly. WA_DontShowOnScreen makes Qt lay out and paint a widget normally
without ever mapping it to the display, so the tests still exercise real
layout, real fonts and real styles while nothing appears.

Patched at QWidget.show rather than at each call site, because a test added
later would otherwise reintroduce the flash silently.
"""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt          # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402

_real_show = QWidget.show


def _hidden_show(self):
    self.setAttribute(Qt.WA_DontShowOnScreen, True)
    _real_show(self)


@pytest.fixture(autouse=True, scope="session")
def _never_show_windows():
    QWidget.show = _hidden_show
    yield
    QWidget.show = _real_show


# ---------------------------------------------------------------------------
# The build variant must not decide whether the suite passes
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _variant_is_not_ambient(monkeypatch):
    """Pin the build variant so tests do not read the developer's tree.

    `tools/set_build_variant.py` writes a flag file into `app/resources`, so
    every test that reaches `morning_run` used to pass on this machine only
    because a `store_build.flag` happened to be sitting there from the last
    packaging run — and the first CI run on a clean checkout failed fifteen
    tests with NotEntitled, because no flag had been written yet.

    That is a test suite whose result depends on local state that nothing
    declares. Pinning it here makes the outcome the same everywhere; the gate
    itself is still tested directly and deliberately in test_entitlement.py
    and test_provider_selection.py, which override this.

    A LICENCE IS PINNED TOO, and that is not the same belt-and-braces it looks
    like. Until 2026-09-08 a `store` build was entitled by POSSESSION, so
    pinning the variant alone was enough to get through the gate. Windows now
    sells through Paddle on both channels, so a Store build needs a key like
    any other — and without one pinned here, ten tests that are about the
    morning run, not about payment, would fail on the licence check.

    Pin the variant WITHOUT the licence and you are testing the gate by
    accident; pin the licence without the variant and you are back to reading
    the developer's tree.
    """
    # BOTH names, and that is not belt-and-braces. `entitlement.py` does
    # `from app.core.build_variant import variant` at module level, so the name
    # is bound at import time and patching the SOURCE module never reaches it —
    # the first version of this fixture patched only the source and six tests
    # still failed, which is the same trap in miniature.
    monkeypatch.setattr("app.core.build_variant.variant", lambda: "store",
                        raising=False)
    monkeypatch.setattr("app.core.entitlement.variant", lambda: "store",
                        raising=False)
    # The Windows shipping path in full: a stored key that verifies. Not a
    # possession shortcut — the same code a real Store customer runs down.
    # Tests that are ABOUT entitlement override both of these.
    monkeypatch.setattr("app.core.entitlement.stored_licence",
                        lambda: "DAWN-TEST-LICENCE", raising=False)
    # `check()` reads through `read_licence`, which tells a missing key from an
    # unreadable credential store; pinning only `stored_licence` would send the
    # gate to the developer's real Credential Manager.
    monkeypatch.setattr("app.core.entitlement.read_licence",
                        lambda: "DAWN-TEST-LICENCE", raising=False)
    # The Mac subscription cache lives in the credential store too. Empty and
    # unwritable-by-accident here, so a test that reaches a `mas` path never
    # reads or overwrites the developer's own entry.
    monkeypatch.setattr("app.core.entitlement.apple_cache", lambda: {},
                        raising=False)
    monkeypatch.setattr("app.core.entitlement.write_apple_cache",
                        lambda cache: None, raising=False)
    monkeypatch.setattr("app.core.entitlement.verify_against_worker",
                        lambda _key: True, raising=False)

    # NO TEST REACHES THE LIVE WORKER BY OPENING A WINDOW. Settings asks the
    # Worker whether the pinned licence above is an administrator every time it
    # is shown, so every test that showed Settings put a real network call on a
    # worker thread — and in CI on 2026-09-11 one outlived its test and crashed
    # the next with an access violation. Only the network path is replaced:
    # a call that passes its own `opener`, as tests of the admin client do, still
    # runs the real function.
    import app.core.admin as admin

    real_is_admin = admin.is_admin

    def offline_is_admin(key, *args, opener=None, **kwargs):
        if opener is None:
            return False
        return real_is_admin(key, *args, opener=opener, **kwargs)

    monkeypatch.setattr(admin, "is_admin", offline_is_admin)


# ---------------------------------------------------------------------------
# Waiting for work that is deliberately no longer synchronous
# ---------------------------------------------------------------------------

@pytest.fixture
def settle():
    """Spin the Qt event loop until `predicate()` is true, or fail loudly.

    The onboarding draft used to run inline on the UI thread, so a test could
    call `run_draft()` and assert on the next line. That inline call is exactly
    what froze the application — the window went "(Not Responding)" for the
    whole of a CV read plus an Anthropic call, which Store Policy 10.4.2
    forbids.

    The work now runs on a worker thread and its result arrives through a
    queued signal, so a test has to let the event loop deliver it. This spins
    the loop rather than sleeping, because a sleep does not deliver signals and
    would simply time out while looking like a hang.

    Do NOT "fix" a test that uses this by making the production code
    synchronous again.
    """
    from PySide6.QtWidgets import QApplication

    def _settle(predicate, timeout=10.0, what="the background task"):
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            QApplication.processEvents()
            time.sleep(0.005)
        raise AssertionError(
            f"{what} did not finish within {timeout}s. If the production code "
            f"was just made synchronous again, that is the bug, not this.")

    return _settle


# ---------------------------------------------------------------------------
# No worker thread outlives the test that started it
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _drain_the_thread_pool():
    """Wait for every `run_in_background` task before the next test starts.

    A task delivers its result through a queued signal to a widget the test
    owns. When the test ends without waiting, the widget is collected while
    C++ still holds a pointer to it, and the crash lands wherever the
    interpreter happens to be — usually at shutdown, in a different test's
    name, or in no test's name at all.

    That is what CI showed on 2026-09-09: `build (windows-latest, direct)`
    printed all 813 dots and then died with exit 1 before pytest could print
    its summary, while the same commit passed in the run beside it. Six local
    repeats did not reproduce it, which is what a race looks like.

    `_Task._emit` already swallows the RuntimeError Qt raises when the
    receiver has gone, but that only covers the case Qt notices in Python. The
    fix for the rest is not to catch it, it is to not have a thread running
    when nobody is left to hear from it.
    """
    yield
    from PySide6.QtCore import QThreadPool
    pool = QThreadPool.globalInstance()
    if not pool.waitForDone(10_000):
        raise AssertionError(
            f"{pool.activeThreadCount()} background task(s) still running "
            f"after this test. A task outliving its test is what makes the "
            f"suite abort at shutdown in someone else's name.")


# ---------------------------------------------------------------------------
# The locale is global, and a test that changes it changes every test after it
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _locale_does_not_leak():
    """Put the language back, whatever a test did to it.

    `set_locale` mutates module state, so the first test to call it — directly
    or through `save_locale`, which is the point of that function — silently
    ran every later test in another language. Twenty-seven of them failed on
    string assertions that had nothing to do with what they were testing, and
    the failures named the wrong culprit entirely.

    Pinned here rather than fixed in each test: the next one to change a
    locale should not have to know this.
    """
    from app.i18n import current_locale, set_locale
    before = current_locale()
    yield
    set_locale(before)
