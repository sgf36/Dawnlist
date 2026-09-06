"""Render the settings panels so a human can LOOK at them."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui.settings import SettingsWindow  # noqa: E402

STORE_SIZE = (1366, 768)
app = QApplication(sys.argv)

w = SettingsWindow()
# Show it as a new user sees it: no key, no licence, nothing hidden.
w.key.stored.setText("No key stored yet — Dawnlist cannot run without one.")
w.licence.stored.setText("No licence stored yet.")
w.resize(*STORE_SIZE)
w.show()
for _ in range(8):
    app.processEvents()
out = Path(__file__).resolve().parents[1] / "docs" / "settings.png"
w.grab().save(str(out))
print("wrote", out)
