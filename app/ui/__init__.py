from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractScrollArea, QScroller


def enable_touch_scroll(widget: QAbstractScrollArea) -> None:
    """Enable kinetic touch-screen scrolling on a scrollable widget."""
    widget.setAttribute(Qt.WA_AcceptTouchEvents, True)
    QScroller.grabGesture(
        widget.viewport(), QScroller.ScrollerGestureType.TouchGesture,
    )
