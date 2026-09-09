"""The "⋯" button: language, subscription, restore — reachable from anywhere.

WHY THIS EXISTS
---------------
Two holes, and the second one is embarrassing.

**Nothing could change the language.** `settings["locale"]` is READ in three
places and was written in none. The only way to pick a language was
`--locale` on the command line, which no Store customer has ever typed. Fifty
catalogues and a hundred and ninety keys were translated, paid for, bundled
and shipped, and every user saw English — including the ones who cannot read
it, who therefore could not find the setting that would have fixed it even if
one had existed.

**Nothing could reach the subscription.** `SubscribePanel` and `LicencePanel`
were both complete and both lived only in Settings, which onboarding never
opens. A user part-way through setup — exactly the person who has just been
told there is no feed — had no route to buying one, and no route to Restore
either. Apple requires Restore to be reachable, and "reachable" cannot mean a
screen the flow never offers.

So: one small control, on every window including the setup wizard, holding
the three things a person needs before the app works at all.

WHY A RESTART FOR THE LANGUAGE, AND WHY THAT IS SAID PLAINLY
------------------------------------------------------------
Qt does not retranslate a built widget tree. Doing it live means rebuilding
every window, and a half-rebuilt wizard loses whatever the user has typed
into it. So the choice is stored, applied at once to everything built after
it, and the menu SAYS the rest of the app catches up when Dawnlist is next
opened. A promise the code does not keep is worse than a smaller promise.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QMenu, QPushButton, QWidget

from app.core.build_variant import variant
from app.i18n import SUPPORTED_LOCALES, current_locale, tr


def populate(menu: QMenu, *, build: str, on_language, on_subscribe,
             on_restore, on_settings) -> None:
    """Fill a menu with the same three things, wherever it is shown.

    The wizard has no menu bar and uses a "⋯" button; the main window has a
    real menu bar and should use it, because a second floating button beside
    a native menu is how an app starts feeling like a port of something else.
    Both call THIS, so the two can never offer different things — which is
    exactly what would happen to a second copy the first time one was edited.
    """
    languages = menu.addMenu(tr("menu.language"))
    here = current_locale()
    for code, _english, native in SUPPORTED_LOCALES:
        # The NATIVE name, always. A person looking for their own language is
        # looking for the word they call it — "Deutsch", not "German".
        action = languages.addAction(native)
        action.setCheckable(True)
        action.setChecked(code == here)
        action.triggered.connect(
            lambda _checked=False, c=code: on_language(c))

    menu.addSeparator()

    # WHAT IS OFFERED DEPENDS ON THE BUILD, and getting it wrong is not
    # cosmetic: guideline 3.1.1 forbids a Mac App Store build from unlocking
    # anything with a key, and Restore is meaningless on Windows because
    # Paddle issues a key rather than an account entitlement.
    if build == "mas":
        menu.addAction(tr("menu.subscribe"), on_subscribe)
        menu.addAction(tr("menu.restore"), on_restore)
    elif build in ("store", "direct"):
        menu.addAction(tr("menu.licence"), on_subscribe)

    menu.addSeparator()
    menu.addAction(tr("menu.settings"), on_settings)


class QuickMenu(QPushButton):
    """One button, three jobs. Emits intent; writes nothing itself."""

    #: A locale code the user picked. The host stores it.
    language_chosen = Signal(str)
    #: Open the purchase screen — StoreKit on a Mac build, the licence and
    #: access-code panel on Windows.
    subscribe_requested = Signal()
    #: Apple requires this to exist and to be findable. Somebody who paid on
    #: another Mac has already paid, and must not pay twice to prove it.
    restore_requested = Signal()
    settings_requested = Signal()

    def __init__(self, *, build: str | None = None, parent=None):
        super().__init__("⋯", parent)          # MIDLINE HORIZONTAL ELLIPSIS
        self.setObjectName("quickMenu")
        self.setFlat(True)
        self.setFixedWidth(38)
        self.setToolTip(tr("menu.tooltip"))
        # Named so a screen reader says something better than "three dots".
        self.setAccessibleName(tr("menu.tooltip"))
        self._build = build if build is not None else variant()
        self.clicked.connect(self._open)

    def _open(self) -> None:
        menu = QMenu(self)
        populate(menu, build=self._build,
                 on_language=self.language_chosen.emit,
                 on_subscribe=self.subscribe_requested.emit,
                 on_restore=self.restore_requested.emit,
                 on_settings=self.settings_requested.emit)
        menu.exec(self.mapToGlobal(self.rect().bottomLeft()))
