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
