"""The window icon.

THE EXE ICON AND THE WINDOW ICON ARE DIFFERENT THINGS AND ONE DOES NOT IMPLY
THE OTHER. `packaging/build_exe.spec` sets the icon Explorer and the taskbar
show for `Dawnlist.exe`. Qt does not read that: every window it opens uses
whatever `QApplication.windowIcon()` returns, and the default is a generic
placeholder.

Until 2026-09-09 nothing ever set it, so every window in the running
application — onboarding, settings, the board, the review screen — carried
Qt's placeholder in its title bar. It was in every screenshot anyone took and
nobody looked at the title bar.

WHY THIS IS A MODULE AND NOT A LINE AT THE ENTRY POINT
------------------------------------------------------
There are four places that construct a QApplication: the main entry, two
onboarding paths, and the review window's standalone runner. Setting the icon
at one of them fixes one of them. `tests/test_branding.py` asserts that every
file constructing a QApplication also applies the icon, so a fifth call site
cannot quietly go back to the placeholder.
"""
from __future__ import annotations

from pathlib import Path

#: Shipped by `build_exe.spec` into `app/resources`. Read by path rather than
#: imported, so a missing file degrades to the placeholder rather than raising
#: — an icon is not worth crashing over.
ICON = Path(__file__).resolve().parent.parent / "resources" / "icon.png"


def apply_icon(app) -> bool:
    """Set the application-wide window icon. True if it was applied.

    Applied to the QApplication rather than to each window, because Qt uses it
    as the default for every window created afterwards. Setting it per window
    is the version of this that misses one.
    """
    if not ICON.exists():
        return False
    try:
        from PySide6.QtGui import QIcon
        icon = QIcon(str(ICON))
        if icon.isNull():
            return False
        app.setWindowIcon(icon)
        return True
    except Exception:  # noqa: BLE001 - branding must never break a launch
        return False
