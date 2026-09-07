"""Settings: the Anthropic key, the licence, and an override code.

This screen exists because the app cannot work without it. Dawnlist is
bring-your-own-key, so a buyer with no way to enter a key has bought something
inert. It is therefore part of onboarding, not a preferences pane people find
later.

Three decisions about handling the key:

  * **It is verified when entered, not on first use.** A typo caught here is a
    typo the user fixes while looking at the box they typed it into. The same
    typo caught during tomorrow's run is an error message about assessment,
    hours later, with nothing on screen to connect it to.

  * **It is never shown back.** Once stored, the field displays a masked
    summary. Re-displaying a secret so the user can check it is how it ends up
    in a screenshot, and they can always paste a new one.

  * **The cost is stated before the field, not after.** Someone is about to
    attach their own billing account to an app. Telling them what it typically
    costs, and that Dawnlist takes no cut, belongs above the input rather than
    in a help page they will not open.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QPushButton,
                               QScrollArea, QSizePolicy, QVBoxLayout,
                               QWidget)

from app.core import api_key
from app.i18n import tr
from app.ui.onboarding import ONBOARDING_STYLESHEET, reflow
from app.ui.review import CREAM, GOLD_DEEP, TEAL

SETTINGS_STYLESHEET = ONBOARDING_STYLESHEET + f"""
QLabel#ok {{ color: {TEAL}; font-weight: 600; }}
QLabel#bad {{ color: {GOLD_DEEP}; font-weight: 600; }}
QLabel#costNote {{
    background: #f4f1ea;
    border: 1px solid #ddd8cc;
    border-radius: 6px;
    padding: 10px 12px;
    color: #45505a;
}}
QLabel#storedKey {{ color: #6b7480; font-family: Consolas, monospace; }}
QListWidget#ruleList {{
    background: #ffffff;
    border: 1px solid #cfcabf;
    border-radius: 6px;
    padding: 4px;
}}
QListWidget#ruleList::item {{ padding: 3px 4px; }}
QListWidget#ruleList::item:selected {{ background: {TEAL}; color: {CREAM}; }}
QListWidget#ruleList:disabled {{ background: #f4f1ea; color: #45505a; }}
"""


def masked(key: str) -> str:
    """Enough to recognise, not enough to use.

    Shown so a user can tell WHICH key is stored — they may have several — but
    never enough to reconstruct it from a screenshot.
    """
    if not key:
        return ""
    if len(key) < 16:
        return "•" * len(key)
    return f"{key[:11]}…{key[-4:]}"


class KeyPanel(QWidget):
    """Enter and verify the user's own Anthropic key."""

    key_changed = Signal(bool)      # True when a verified key is stored

    def __init__(self, *, verifier=None, storer=None, reader=None,
                 forgetter=None, parent=None):
        super().__init__(parent)
        self._verify = verifier or api_key.verify
        self._store = storer or api_key.store
        self._read = reader or api_key.get
        self._forget = forgetter or api_key.forget

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        heading = QLabel(tr("settings.key_heading"))
        heading.setObjectName("stepHeading")
        layout.addWidget(heading)

        body = QLabel(reflow(tr("settings.key_body")))
        body.setObjectName("stepBody")
        body.setWordWrap(True)
        layout.addWidget(body)

        # Above the input, deliberately. Someone is about to attach their own
        # billing account to this.
        cost = QLabel(reflow(api_key.COST_GUIDANCE))
        cost.setObjectName("costNote")
        cost.setWordWrap(True)
        layout.addWidget(cost)

        self.stored = QLabel()
        self.stored.setObjectName("storedKey")
        layout.addWidget(self.stored)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.field = QLineEdit()
        self.field.setObjectName("sentence")
        self.field.setEchoMode(QLineEdit.Password)
        self.field.setPlaceholderText(tr("settings.key_placeholder"))
        self.button = QPushButton(tr("settings.key_save"))
        self.button.setObjectName("primary")
        self.button.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        # Only shown once there is something to remove. Someone who revokes
        # the key at Anthropic, or hands the machine on, otherwise has no way
        # to clear it from here — the app would keep a dead credential
        # indefinitely and never say so.
        self.button_forget = QPushButton(tr("settings.key_forget"))
        # `secondary`, not a new danger style: pasting the key back takes ten
        # seconds, and styling a reversible action as destructive spends alarm
        # that the genuinely irreversible ones then have less of.
        self.button_forget.setObjectName("secondary")
        self.button_forget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        row.addWidget(self.field, 1)
        row.addWidget(self.button)
        row.addWidget(self.button_forget)
        layout.addLayout(row)

        self.result = QLabel()
        self.result.setWordWrap(True)
        layout.addWidget(self.result)
        layout.addStretch(1)

        self.setStyleSheet(SETTINGS_STYLESHEET)
        self.button.clicked.connect(self.save)
        self.button_forget.clicked.connect(self.forget)
        self.field.returnPressed.connect(self.save)
        self.refresh()

    def refresh(self) -> None:
        existing = self._read()
        self.stored.setText(
            tr("settings.key_stored", key=masked(existing)) if existing
            else tr("settings.key_none"))
        self.button_forget.setVisible(bool(existing))
        self.key_changed.emit(bool(existing))

    def forget(self) -> None:
        """Remove the stored key.

        Not confirmed: nothing is lost that cannot be pasted back in ten
        seconds, and a confirmation dialog on a reversible action teaches
        people to dismiss dialogs.
        """
        self._forget()
        self.field.clear()
        self.result.setObjectName("")
        self.result.setText(tr("settings.key_forgotten"))
        self.result.style().unpolish(self.result)
        self.result.style().polish(self.result)
        self.refresh()

    def save(self) -> None:
        entered = self.field.text().strip()
        if not entered:
            return

        self.button.setEnabled(False)
        self.result.setObjectName("")
        self.result.setText(tr("settings.key_checking"))
        self.result.repaint()
        try:
            ok, message = self._verify(entered)
        finally:
            self.button.setEnabled(True)

        self.result.setObjectName("ok" if ok else "bad")
        self.result.setText(message)
        self.result.style().unpolish(self.result)
        self.result.style().polish(self.result)

        if ok:
            self._store(entered)
            # Cleared on success only. A rejected key stays in the box so the
            # user can see what they pasted and fix it, rather than starting
            # again from nothing.
            self.field.clear()
            self.refresh()


class LicencePanel(QWidget):
    """Enter a licence key, or redeem an override code for one."""

    licence_changed = Signal(bool)

    def __init__(self, *, redeemer=None, storer=None, reader=None, parent=None):
        super().__init__(parent)
        from app.core import entitlement

        self._redeem = redeemer or entitlement.redeem_override_code
        self._store = storer or entitlement.store_licence
        self._read = reader or entitlement.stored_licence

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        heading = QLabel(tr("settings.licence_heading"))
        heading.setObjectName("stepHeading")
        layout.addWidget(heading)

        body = QLabel(reflow(tr("settings.licence_body")))
        body.setObjectName("stepBody")
        body.setWordWrap(True)
        layout.addWidget(body)

        self.stored = QLabel()
        self.stored.setObjectName("storedKey")
        layout.addWidget(self.stored)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.field = QLineEdit()
        self.field.setObjectName("sentence")
        self.field.setPlaceholderText(tr("settings.licence_placeholder"))
        self.button = QPushButton(tr("settings.licence_save"))
        self.button.setObjectName("primary")
        self.button.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        row.addWidget(self.field, 1)
        row.addWidget(self.button)
        layout.addLayout(row)

        self.result = QLabel()
        self.result.setWordWrap(True)
        layout.addWidget(self.result)
        layout.addStretch(1)

        self.setStyleSheet(SETTINGS_STYLESHEET)
        self.button.clicked.connect(self.save)
        self.field.returnPressed.connect(self.save)
        self.refresh()

    def refresh(self) -> None:
        existing = self._read()
        self.stored.setText(
            tr("settings.licence_stored", key=masked(existing)) if existing
            else tr("settings.licence_none"))
        self.licence_changed.emit(bool(existing))

    def save(self) -> None:
        entered = self.field.text().strip()
        if not entered:
            return

        self.button.setEnabled(False)
        self.result.setObjectName("")
        self.result.setText(tr("settings.licence_checking"))
        self.result.repaint()

        # One field for both, because a user does not care which they were
        # given. A licence key starts DAWN-; anything else is tried as a code.
        try:
            if entered.upper().startswith("DAWN-"):
                self._store(entered)
                ok, message = True, tr("settings.licence_saved")
            else:
                licence = self._redeem(entered)
                self._store(licence)
                ok, message = True, tr("settings.code_redeemed")
        except Exception as exc:  # noqa: BLE001 - the reason is the message
            ok, message = False, str(exc)
        finally:
            self.button.setEnabled(True)

        self.result.setObjectName("ok" if ok else "bad")
        self.result.setText(message)
        self.result.style().unpolish(self.result)
        self.result.style().polish(self.result)

        if ok:
            self.field.clear()
            self.refresh()


def _divider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setStyleSheet("color:#ddd8cc;")
    return line


class SettingsWindow(QWidget):
    """The panels, for the Settings menu and for onboarding.

    In a store build the licence panel is not shown. Entitlement there is by
    possession — the storefront already took the money and there is no licence
    key in existence — so a box asking for one sends a paying user hunting
    through their email for something nobody ever sent them.
    """

    def __init__(self, parent=None, *, variant=None, rules=None):
        super().__init__(parent)
        from app.core.build_variant import variant as read_variant

        self.setWindowTitle(tr("settings.title"))

        # Scrolled, because three stacked panels want ~916px and a 768-tall
        # laptop screen is ordinary. Without this the licence box sits below
        # the bottom of the display on the one build that needs it, with no
        # way to reach it — and a user who cannot enter their licence key has
        # bought something inert.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll)

        content = QWidget()
        self.scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.key = KeyPanel()
        layout.addWidget(self.key)

        # Only when a database is available: the rules live per-user in the
        # database, and a panel wired to nothing would offer to save terms and
        # silently drop them.
        self.rules = rules
        if rules is not None:
            layout.addWidget(_divider())
            layout.addWidget(rules, 1)

        self.licence = LicencePanel()
        build = variant if variant is not None else read_variant()
        self.shows_licence = build not in ("store", "mas")
        if self.shows_licence:
            layout.addWidget(_divider())
            layout.addWidget(self.licence)
        else:
            # Constructed but not laid out, so `window.licence` stays a stable
            # attribute for callers and tests rather than sometimes-missing.
            self.licence.hide()
        self.setStyleSheet(SETTINGS_STYLESHEET)

        # A scroll area reports a tiny minimum, so the window would otherwise
        # be resizable down to a stub. The floor keeps the four rule columns
        # legible; the default opens shorter than a 768-tall laptop screen and
        # lets the scroll cover the rest.
        self.setMinimumSize(760, 440)
        self.resize(1120, 700)


#: The three writable tiers, in the order they fire during a screen. Tier 2
#: (`known_employers`) is absent on purpose: it is derived from pursue
#: decisions, and a hand-typed copy would drift the moment one was revised.
RULE_TIERS = (
    ("unsupported_titles", "rules.unsupported", "rules.unsupported_help"),
    ("strong_terms", "rules.strong", "rules.strong_help"),
    ("contextual_terms", "rules.contextual", "rules.contextual_help"),
)


class RulesPanel(QWidget):
    """The screening rules: what gets read, and what gets killed for nothing.

    Two things make this more than a list of words.

    **A refused term is the most useful thing this screen does.** When a term
    would have removed a role the user actually pursued, the refusal names the
    role — because the useful information is not "invalid", it is "you chased
    this exact job in March". That is the failure the whole admission guard was
    built for, and reducing it to a red border throws the finding away.

    **Tier 2 is shown but not editable.** Employers are earned by pursuing
    them. Showing the earned list makes the mechanism visible; letting someone
    type into it would create a second, drifting copy of the decisions.
    """

    changed = Signal()

    def __init__(self, *, loader=None, saver=None, forgetter=None, parent=None):
        super().__init__(parent)
        self._load = loader or (lambda: None)
        self._save = saver or (lambda field, term: None)
        self._forget = forgetter or (lambda field, term: None)
        self._lists: dict[str, QListWidget] = {}
        self._fields: dict[str, QLineEdit] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        heading = QLabel(tr("rules.heading"))
        heading.setObjectName("stepHeading")
        layout.addWidget(heading)

        body = QLabel(reflow(tr("rules.body")))
        body.setObjectName("stepBody")
        body.setWordWrap(True)
        layout.addWidget(body)

        self.result = QLabel()
        self.result.setWordWrap(True)
        self.result.hide()
        layout.addWidget(self.result)

        # A grid, not four stacked columns: the help text runs to two lines in
        # one tier and three in the others, so side-by-side layouts start each
        # list box at a different height and the row of panels reads as
        # misaligned. Grid rows are shared, so the boxes line up whatever the
        # translation does to the text length.
        columns = QGridLayout()
        columns.setHorizontalSpacing(12)
        columns.setVerticalSpacing(6)
        for i, (field, label_key, help_key) in enumerate(RULE_TIERS):
            self._tier(columns, i, field, label_key, help_key)
        self._earned_column(columns, len(RULE_TIERS))
        columns.setRowStretch(2, 1)
        for i in range(len(RULE_TIERS) + 1):
            columns.setColumnStretch(i, 1)
        layout.addLayout(columns, 1)

        self.setStyleSheet(SETTINGS_STYLESHEET)
        self.refresh()

    def _head(self, grid: QGridLayout, column: int, label_key: str,
              help_key: str) -> None:
        title = QLabel(tr(label_key))
        f = QFont()
        f.setBold(True)
        title.setFont(f)
        grid.addWidget(title, 0, column)

        note = QLabel(reflow(tr(help_key)))
        note.setObjectName("stepBody")
        note.setWordWrap(True)
        note.setAlignment(Qt.AlignTop)
        grid.addWidget(note, 1, column)

    def _tier(self, grid: QGridLayout, column: int, field: str,
              label_key: str, help_key: str) -> None:
        self._head(grid, column, label_key, help_key)

        listing = QListWidget()
        listing.setObjectName("ruleList")
        grid.addWidget(listing, 2, column)
        self._lists[field] = listing

        controls = QWidget()
        row = QHBoxLayout(controls)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        entry = QLineEdit()
        entry.setObjectName("sentence")
        entry.setPlaceholderText(tr("rules.add_placeholder"))
        entry.returnPressed.connect(lambda f=field: self.add(f))
        add = QPushButton(tr("rules.add"))
        remove = QPushButton(tr("rules.remove"))
        for b, slot in ((add, self.add), (remove, self.remove)):
            b.setObjectName("secondary")
            b.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            b.clicked.connect(lambda _=False, f=field, s=slot: s(f))
        row.addWidget(entry, 1)
        row.addWidget(add)
        row.addWidget(remove)
        grid.addWidget(controls, 3, column)
        self._fields[field] = entry

    def _earned_column(self, grid: QGridLayout, column: int) -> None:
        self._head(grid, column, "rules.employers", "rules.employers_help")
        self.employers = QListWidget()
        self.employers.setObjectName("ruleList")
        self.employers.setEnabled(False)      # earned, never typed
        grid.addWidget(self.employers, 2, column)
        # Row 3 is left empty rather than spaced by hand: the grid gives this
        # column the same height as the others, so the list boxes end level.

    # -- state -------------------------------------------------------------
    def refresh(self) -> None:
        table = self._load()
        for field, listing in self._lists.items():
            listing.clear()
            listing.addItems(getattr(table, field, []) if table else [])
        self.employers.clear()
        self.employers.addItems(getattr(table, "known_employers", []) if table
                                else [])

    def _say(self, text: str, ok: bool) -> None:
        self.result.setObjectName("ok" if ok else "bad")
        self.result.setText(text)
        self.result.style().unpolish(self.result)
        self.result.style().polish(self.result)
        self.result.setVisible(bool(text))

    def add(self, field: str) -> None:
        from app.core.rules import RuleConflictError

        term = self._fields[field].text().strip()
        if not term:
            return
        try:
            self._save(field, term)
        except RuleConflictError as exc:
            # Name the role. "Invalid term" throws away the only fact the user
            # needs, which is which job this would have cost them.
            self._say(tr("rules.conflict") + "\n• " + "\n• ".join(
                tr("rules.conflict_line", term=c.term, title=c.pursued_title,
                   company=c.company) for c in exc.conflicts), ok=False)
            return
        except ValueError as exc:
            self._say(str(exc), ok=False)
            return

        self._fields[field].clear()
        self._say(tr("rules.added", term=term), ok=True)
        self.refresh()
        self.changed.emit()

    def remove(self, field: str) -> None:
        listing = self._lists[field]
        item = listing.currentItem()
        if item is None:
            return
        self._forget(field, item.text())
        self._say(tr("rules.removed", term=item.text()), ok=True)
        self.refresh()
        self.changed.emit()
