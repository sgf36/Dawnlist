"""Render the settings panels so a human can LOOK at them."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui.settings import SettingsWindow  # noqa: E402

STORE_SIZE = (1366, 768)
def render_hidden(widget, size):
    """Lay out and paint a widget WITHOUT putting it on screen.

    Qt paints a WA_DontShowOnScreen widget normally — real fonts, real styles —
    but never maps it to the display. Without this every render flashes a
    window on the user's desktop, which is intrusive when these run repeatedly.

    The offscreen platform plugin would also avoid the flash, but it has no
    font configuration and renders every glyph as tofu.
    """
    widget.setAttribute(Qt.WA_DontShowOnScreen, True)
    widget.resize(*size)
    widget.show()
    return widget


app = QApplication(sys.argv)

w = SettingsWindow()
# Show it as a new user sees it: no key, no licence, nothing hidden.
w.key.stored.setText("No key stored yet — Dawnlist cannot run without one.")
w.licence.stored.setText("No licence stored yet.")
render_hidden(w, STORE_SIZE)
for _ in range(8):
    app.processEvents()
out = Path(__file__).resolve().parents[1] / "docs" / "settings.png"
w.grab().save(str(out))
print("wrote", out)
