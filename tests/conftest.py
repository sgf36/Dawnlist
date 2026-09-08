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

    `tools/set_build_variant.py` writes a flag file into `app/resources`, and
    `entitlement.require` treats a store or MAS build as entitled by
    possession. So every test that reaches `morning_run` passed on this
    machine only because a `store_build.flag` happened to be sitting there
    from the last packaging run — and the first CI run on a clean checkout
    failed fifteen tests with NotEntitled, because no flag had been written
    yet at the point the suite ran.

    That is a test suite whose result depends on local state that nothing
    declares. Pinning it here makes the outcome the same everywhere; the gate
    itself is still tested directly and deliberately in test_entitlement.py
    and test_provider_selection.py, which override this.
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
