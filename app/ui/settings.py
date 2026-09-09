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
from PySide6.QtWidgets import (QComboBox, QFrame, QGridLayout, QHBoxLayout,
                               QLabel,
                               QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton,
                               QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout,
                               QWidget)

from app.core import api_key
from app.i18n import tr
from app.ui.background import run_in_background
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



#: Deep link, not the front page. `console.anthropic.com` lands on a dashboard
#: from which the keys page is several clicks away; this is the page the user
#: actually needs. Anthropic has kept this path stable, and if it ever moves the
#: worst case is a redirect — strictly better than the plain text it replaces.
CONSOLE_KEYS_URL = "https://console.anthropic.com/settings/keys"


def _linkify_console(text: str) -> str:
    """Make the console address clickable, in every language.

    The domain survives translation intact in all fifty catalogues (checked,
    not assumed), so matching the literal is safe and needs no per-locale
    handling.

    Returns the text UNCHANGED when the domain is absent, so a future
    rewording that drops the address degrades to plain text rather than to a
    broken link or an exception.
    """
    needle = "console.anthropic.com"
    if needle not in text:
        return text
    from html import escape
    before, _, after = text.partition(needle)
    return (escape(before)
            + f'<a href="{CONSOLE_KEYS_URL}">{needle}</a>'
            + escape(after))


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

        body = QLabel(_linkify_console(reflow(tr("settings.key_body"))))
        body.setObjectName("stepBody")
        body.setWordWrap(True)
        # The single biggest adoption barrier in this product is "go and get an
        # API key from a company you have never heard of". Making the address
        # something you can click, rather than something you must copy into a
        # browser by hand, is the cheapest reduction of it available.
        body.setOpenExternalLinks(True)
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

    def __init__(self, parent=None, *, variant=None, rules=None,
                 families=None, searches=None, is_admin=None):
        super().__init__(parent)
        from app.core.build_variant import variant as read_variant

        #: Injectable so a test can answer without a network call, and so
        #: nothing reaches the live Worker merely because a window was built.
        self._is_admin = is_admin
        self._admin_checked = False

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

        # First, because it is the one nothing works without: a run with no
        # saved search refuses outright, and until this panel existed there was
        # nowhere to add one.
        self.searches = searches
        if searches is not None:
            layout.addWidget(_divider())
            layout.addWidget(searches, 1)

        # Only when a database is available: the rules live per-user in the
        # database, and a panel wired to nothing would offer to save terms and
        # silently drop them.
        self.rules = rules
        if rules is not None:
            layout.addWidget(_divider())
            layout.addWidget(rules, 1)

        self.families = families
        if families is not None:
            layout.addWidget(_divider())
            layout.addWidget(families, 1)

        self.licence = LicencePanel()
        build = variant if variant is not None else read_variant()
        # ONLY Apple prohibits this, and the two stores are not the same.
        #
        # Apple's guideline 3.1.1 names licence keys as a mechanism an app may
        # not use to unlock functionality, so a MAS build must sell through
        # StoreKit and must never accept a key.
        #
        # Microsoft permits third-party commerce and takes nothing on it,
        # subject to declaring it in Partner Center. So a Windows Store build
        # MAY accept a licence bought on the website — and hiding the box there
        # was the reason a Store customer could pay and then reach no feed at
        # all. The restriction was Apple's, applied to both by assumption.
        self.shows_licence = build != "mas"
        if self.shows_licence:
            layout.addWidget(_divider())
            layout.addWidget(self.licence)
        else:
            # Constructed but not laid out, so `window.licence` stays a stable
            # attribute for callers and tests rather than sometimes-missing.
            self.licence.hide()

        # THE OTHER HALF, AND EXACTLY ONE OF THE TWO IS EVER SHOWN.
        #
        # Hiding the key box on a MAS build was correct and was also only half
        # a decision: it left that build with no way to pay AT ALL, which is
        # the same shape of hole the Windows Store build had in reverse. A
        # customer met "the App Store could not confirm an active subscription"
        # and had nowhere to go.
        #
        # This is also the screen App Review wants a screenshot of, and the
        # reason the subscription cannot leave MISSING_METADATA without it.
        self.shows_subscribe = build == "mas"
        self.subscribe = SubscribePanel() if self.shows_subscribe else None
        if self.subscribe is not None:
            layout.addWidget(_divider())
            layout.addWidget(self.subscribe)

        # THE ADMIN CONSOLE, on every build and hidden by default.
        #
        # Revealed only when the SERVER says this licence is an administrator,
        # asked fresh each time this screen opens. Caching that answer, or
        # inferring it locally, would leave a withdrawn admin holding a console
        # until they restarted — the decision belongs to the Worker, which
        # re-reads `licence_roles` on every request.
        self.admin = AdminPanel()
        self.admin.hide()
        self._admin_divider = _divider()
        self._admin_divider.hide()
        layout.addWidget(self._admin_divider)
        layout.addWidget(self.admin)

        # Always shown, on every build. Two obligations meet here.
        layout.addWidget(_divider())
        layout.addWidget(DataTermsPanel())

        # Also every build: Store Policy 11.16 wants a reporting route for
        # generated content, and generated content is in all three variants.
        layout.addWidget(_divider())
        self.report = ReportPanel()
        layout.addWidget(self.report)

        self.setStyleSheet(SETTINGS_STYLESHEET)

        # A scroll area reports a tiny minimum, so the window would otherwise
        # be resizable down to a stub. The floor keeps the four rule columns
        # legible; the default opens shorter than a 768-tall laptop screen and
        # lets the scroll cover the rest.
        self.setMinimumSize(760, 440)
        self.resize(1120, 700)

    def showEvent(self, event):
        # ASKED WHEN THE SCREEN OPENS, not when it is built. Constructing a
        # window must not reach the network: it made the test suite call the
        # live Worker, and it asked before the answer could be wanted.
        super().showEvent(event)
        if not self._admin_checked:
            self._admin_checked = True
            self._check_admin()

    def _check_admin(self) -> None:
        """Ask the SERVER whether this licence may administer, never assume.

        Off the UI thread: it is a network call, and a settings screen that
        freezes while it asks is the same defect this codebase already fixed
        in onboarding. A failed check reveals nothing, which is the safe
        direction to fail in.
        """
        from app.core import admin as admin_api
        from app.core import entitlement

        key = entitlement.stored_licence()
        if not key:
            return
        ask = self._is_admin or admin_api.is_admin

        def reveal(is_admin):
            if is_admin:
                self._admin_divider.show()
                self.admin.show()
                self.admin.refresh()

        self._admin_task = run_in_background(
            lambda: ask(key),
            on_done=reveal,
            on_error=lambda exc: None)


#: The three writable tiers, in the order they fire during a screen. Tier 2
#: (`known_employers`) is absent on purpose: it is derived from pursue
#: decisions, and a hand-typed copy would drift the moment one was revised.
RULE_TIERS = (
    ("unsupported_titles", "rules.unsupported", "rules.unsupported_help"),
    ("strong_terms", "rules.strong", "rules.strong_help"),
    ("contextual_terms", "rules.contextual", "rules.contextual_help"),
)



#: Where the end-user terms live. They must be PUBLISHED and ACCEPTED, not
#: merely linked: the data licence requires every downstream recipient to be
#: "bound by written terms", and a page nobody agreed to binds nobody. This
#: link is the app's part of that, not the whole of it — acceptance happens at
#: purchase.
TERMS_URL = "https://dawnlist.spencerfields.com/terms.html"

#: Where a person reports what the model wrote. Microsoft Store Policy 11.16
#: requires products with live generative AI to "provide a means for users to
#: report inappropriate content to the developer" and to act on what comes in.
#:
#: A support address in a footer is not a MEANS. It is an address someone has
#: to guess is the right one, for a purpose it never mentions. This is a
#: mailto with the subject already written, so the route is one click and the
#: reports arrive already labelled — which is what makes acting on them
#: possible rather than notional.
#:
#: Same address the website uses. Deliberately not a second channel: two
#: inboxes for one obligation is how one of them stops being read.
REPORT_EMAIL = "Apps@spencerfields.com"
REPORT_MAILTO = (
    "mailto:" + REPORT_EMAIL
    + "?subject=Dawnlist%20%E2%80%94%20reporting%20AI-generated%20content"
)



class DataTermsPanel(QWidget):
    """Where the postings come from, and what may be done with them.

    TWO OBLIGATIONS, both from the feed provider's licence.

    ATTRIBUTION. Their §4.5 requires materials integrated into a CRM to carry
    identifying information showing they originated with the provider. Whether
    Dawnlist's tracker counts as a CRM is genuinely arguable — it is
    CRM-shaped, and the clause is aimed at leakage through internal systems.
    Attributing anyway costs a line of text and removes the argument entirely,
    which is a good trade against a clause that carries injunctive relief.

    FLOW-THROUGH. Their §4.11 requires every downstream recipient — which is
    every subscriber — to be bound by written terms at least as restrictive as
    their own §4. A link is not binding on its own; acceptance at purchase is
    what binds. This panel exists so the terms are FINDABLE afterwards by
    someone who has already agreed to them, which is the part a purchase flow
    cannot do.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        heading = QLabel(tr("settings.data_heading"))
        heading.setObjectName("stepHeading")
        layout.addWidget(heading)

        body = QLabel(reflow(tr("settings.data_body")))
        body.setObjectName("stepBody")
        body.setWordWrap(True)
        layout.addWidget(body)

        link = QLabel(f'<a href="{TERMS_URL}">{tr("settings.data_terms_link")}</a>')
        link.setObjectName("dataTerms")
        link.setOpenExternalLinks(True)
        link.setWordWrap(True)
        layout.addWidget(link)


class ReportPanel(QWidget):
    """How to report what the model wrote.

    Microsoft Store Policy 11.16 applies to any product whose content is
    generated live by AI in response to user input, which is exactly what a
    verdict on a posting and a drafted follow-up are. It requires the use of
    live generative AI to be disclosed AND a means for users to report
    inappropriate content to the developer.

    Dawnlist declares the generative-AI use in Partner Center. Until this
    panel existed it had NO reporting route anywhere — not in the app, not on
    the website — while telling reviewers to expect one. That is a rejection
    waiting to happen, and worse, a person with a genuine complaint about
    something written under their own name had nowhere to take it.

    ALWAYS SHOWN, ON EVERY BUILD. The obligation follows the generated
    content, and the generated content is in all three variants. It sits
    beside the data terms because both are things the person is owed rather
    than features they chose.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        heading = QLabel(tr("settings.report_heading"))
        heading.setObjectName("stepHeading")
        layout.addWidget(heading)

        body = QLabel(reflow(tr("settings.report_body")))
        body.setObjectName("stepBody")
        body.setWordWrap(True)
        layout.addWidget(body)

        link = QLabel(f'<a href="{REPORT_MAILTO}">{tr("settings.report_link")}</a>')
        link.setObjectName("dataTerms")
        link.setOpenExternalLinks(True)
        link.setWordWrap(True)
        layout.addWidget(link)


class AdminPanel(QWidget):
    """Issue, list and withdraw override codes — the console the Worker was
    already serving and nothing had ever called.

    HIDDEN UNTIL THE SERVER SAYS OTHERWISE, and asked every time this screen
    opens. The role lives in the Worker's `licence_roles` table rather than
    inside the licence, so withdrawing an administrator takes effect at once;
    caching the answer here would hand that decision to the wrong computer and
    leave a former admin holding a console until they happened to restart.

    ON BOTH PLATFORMS. Nothing about administering codes is Apple's business:
    guideline 3.1.1 is about unlocking content with a key, and this screen
    issues free grants rather than selling anything.
    """

    def __init__(self, *, key_source=None, api=None, parent=None):
        super().__init__(parent)
        from app.core import admin as admin_api
        from app.core import entitlement

        self._api = api or admin_api
        self._key_source = key_source or entitlement.stored_licence
        self._codes: list[dict] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        heading = QLabel(tr("settings.admin_heading"))
        heading.setObjectName("stepHeading")
        layout.addWidget(heading)

        body = QLabel(reflow(tr("settings.admin_body")))
        body.setObjectName("stepBody")
        body.setWordWrap(True)
        layout.addWidget(body)

        form = QHBoxLayout()
        form.setSpacing(6)
        self.note = QLineEdit()
        self.note.setPlaceholderText(tr("settings.admin_note_placeholder"))
        form.addWidget(self.note, 2)

        self.role = QComboBox()
        for role in self._api.ROLES:
            self.role.addItem(role)
        form.addWidget(self.role)

        self.plan = QComboBox()
        for plan in self._api.PLANS:
            self.plan.addItem(plan)
        form.addWidget(self.plan)

        self.uses = QSpinBox()
        self.uses.setRange(1, 999)
        self.uses.setValue(1)
        self.uses.setToolTip(tr("settings.admin_uses_tip"))
        form.addWidget(self.uses)

        self.btn_issue = QPushButton(tr("settings.admin_issue"))
        self.btn_issue.setObjectName("primary")
        form.addWidget(self.btn_issue)
        layout.addLayout(form)

        self.result = QLabel()
        self.result.setWordWrap(True)
        # Selectable so a freshly minted code can be copied out. A code you
        # cannot copy is a code you retype wrongly.
        self.result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.result)

        self.codes = QListWidget()
        self.codes.setMaximumHeight(160)
        layout.addWidget(self.codes)

        row = QHBoxLayout()
        self.btn_refresh = QPushButton(tr("settings.admin_refresh"))
        self.btn_revoke = QPushButton(tr("settings.admin_revoke"))
        row.addWidget(self.btn_refresh)
        row.addWidget(self.btn_revoke)
        row.addStretch(1)
        layout.addLayout(row)

        self.btn_issue.clicked.connect(self._issue)
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_revoke.clicked.connect(self._revoke)
        self.role.currentTextChanged.connect(self._role_changed)

    # -- helpers -----------------------------------------------------------
    def _role_changed(self, role: str) -> None:
        """A managed code is the usual shape for a reviewer, and a reviewer
        code must not be single-use. Nudge rather than enforce: an admin may
        still want a one-shot managed code for a friend."""
        if role == "managed" and self.uses.value() == 1:
            self.uses.setValue(self._api.REVIEW_USES)

    def _key(self):
        return self._key_source()

    def refresh(self) -> None:
        key = self._key()
        if not key:
            self.result.setText(tr("settings.admin_no_licence"))
            return
        try:
            self._codes = self._api.list_codes(key)
        except Exception as exc:  # noqa: BLE001
            self.result.setText(str(exc))
            return
        self.codes.clear()
        for row in self._codes:
            used, cap = row.get("uses", 0), row.get("max_uses", 1)
            state = " REVOKED" if row.get("revoked") else ""
            self.codes.addItem(
                f"{row.get('code')}  {row.get('role')}/{row.get('plan')}  "
                f"{used}/{cap}{state}  — {row.get('note') or ''}")
        self.result.setText("")

    def _issue(self) -> None:
        key = self._key()
        if not key:
            self.result.setText(tr("settings.admin_no_licence"))
            return
        try:
            made = self._api.issue_code(
                key, note=self.note.text(), role=self.role.currentText(),
                plan=self.plan.currentText(), max_uses=self.uses.value())
        except Exception as exc:  # noqa: BLE001
            self.result.setText(str(exc))
            return
        # REFRESH FIRST, THEN SAY WHAT HAPPENED. `refresh()` blanks the
        # message, so setting it beforehand wiped the code the operator was
        # told to copy — on a line that reads "Copy it now; it is shown once
        # here". The code is minted either way; only the one chance to read it
        # was lost.
        self.note.clear()
        self.refresh()
        self.result.setText(tr("settings.admin_issued", code=made.get("code", "")))

    def _revoke(self) -> None:
        index = self.codes.currentRow()
        if index < 0 or index >= len(self._codes):
            self.result.setText(tr("settings.admin_pick_one"))
            return
        key = self._key()
        code = self._codes[index].get("code")
        try:
            self._api.revoke_code(key, code)
        except Exception as exc:  # noqa: BLE001
            self.result.setText(str(exc))
            return
        self.refresh()
        self.result.setText(tr("settings.admin_revoked", code=code))


class SubscribePanel(QWidget):
    """Buy the subscription, on a Mac App Store build.

    THE MIRROR OF LicencePanel, AND ONLY ONE OF THE TWO IS EVER SHOWN.
    Windows sells through Paddle on both channels, so a `store` or `direct`
    build shows a box to paste a key into. Apple forbids that (guideline
    3.1.1), so a `mas` build shows this instead.

    Until this existed a Mac App Store build had NO way to buy the subscription
    it required: it reported that it could not confirm one and stopped. App
    Review rejects an app that sells a subscription and offers no way to buy
    it, and the subscription itself cannot leave MISSING_METADATA because the
    review screenshot Apple asks for is of this screen.

    RESTORE IS NOT OPTIONAL. Apple requires it. Somebody who subscribed on
    another Mac, or who reinstalled, has already paid and must be able to get
    their entitlement back without paying twice.

    THE PRICE IS ASKED OF APPLE, NEVER HARDCODED. Apple sets it per storefront
    across 175 territories and formats it for the customer's region. A
    hardcoded price is wrong almost everywhere, and showing one that differs
    from what the App Store charges is a rejection.
    """

    entitlement_changed = Signal(bool)

    def __init__(self, *, storekit=None, redeemer=None, storer=None,
                 parent=None):
        super().__init__(parent)
        from app.core import mac_storekit

        self._redeemer = redeemer
        self._storer = storer
        self._sk = storekit or mac_storekit

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        heading = QLabel(tr("settings.subscribe_heading"))
        heading.setObjectName("stepHeading")
        layout.addWidget(heading)

        body = QLabel(reflow(tr("settings.subscribe_body")))
        body.setObjectName("stepBody")
        body.setWordWrap(True)
        layout.addWidget(body)

        self.price = QLabel()
        self.price.setObjectName("storedKey")
        layout.addWidget(self.price)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.buy = QPushButton(tr("settings.subscribe_button"))
        self.buy.setObjectName("primary")
        self.buy.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.restore = QPushButton(tr("settings.subscribe_restore"))
        self.restore.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        row.addWidget(self.buy)
        row.addWidget(self.restore)
        row.addStretch(1)
        layout.addLayout(row)

        self.result = QLabel()
        self.result.setWordWrap(True)
        layout.addWidget(self.result)

        # AN ACCESS CODE, AND DELIBERATELY NOT A LICENCE KEY BOX.
        #
        # Guideline 3.1.1 forbids unlocking content with a key INSTEAD OF
        # Apple's commerce. This redeems a free grant — a reviewer, a friend,
        # the developer — and sells nothing, which is why it may exist on a MAS
        # build at all. The wording matters as much as the mechanism: "licence
        # key" reads as an alternative way to buy, which is the thing that is
        # actually prohibited, so it is never called that here.
        #
        # `build_provider` will only honour what comes back if the SERVER says
        # it was granted by a code rather than purchased. A Paddle licence
        # already in the keyring is still refused on this build.
        code_row = QHBoxLayout()
        code_row.setSpacing(10)
        self.code = QLineEdit()
        self.code.setObjectName("sentence")
        self.code.setPlaceholderText(tr("settings.subscribe_code_placeholder"))
        self.btn_code = QPushButton(tr("settings.subscribe_code_button"))
        self.btn_code.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        code_row.addWidget(self.code, 1)
        code_row.addWidget(self.btn_code)
        layout.addLayout(code_row)

        self.buy.clicked.connect(self._purchase)
        self.restore.clicked.connect(self._restore)
        self.btn_code.clicked.connect(self._redeem_code)
        self.code.returnPressed.connect(self._redeem_code)
        self.refresh()

    def _redeem_code(self) -> None:
        """Exchange an access code for the grant it names."""
        from app.core import entitlement

        code = self.code.text().strip()
        if not code:
            return
        try:
            key = (self._redeemer or entitlement.redeem_override_code)(code)
            (self._storer or entitlement.store_licence)(key)
        except Exception as exc:  # noqa: BLE001
            self.result.setText(str(exc))
            return
        self.code.clear()
        self.result.setText(tr("settings.code_redeemed"))
        self.entitlement_changed.emit(True)

    def refresh(self) -> None:
        """Ask Apple what to show. On construction, and after a purchase.

        A MISSING PRICE IS NOT AN ERROR STATE. Apple returns nothing for a
        product its storefront does not know yet, which is also what a
        brand-new subscription looks like while it propagates. "Not available
        right now" is true in both cases; "something went wrong" is not.
        """
        shown = self._sk.price() if self._sk.available() else None
        self.price.setText(shown or "")
        can = self._sk.available() and self._sk.can_make_payments()
        self.buy.setEnabled(can)
        self.restore.setEnabled(self._sk.available())
        if not can:
            self.result.setText(tr("settings.subscribe_unavailable"))

    def _busy(self, on: bool) -> None:
        idle = not on
        self.buy.setEnabled(idle and self._sk.available()
                            and self._sk.can_make_payments())
        self.restore.setEnabled(idle and self._sk.available())
        if on:
            self.result.setText(tr("settings.subscribe_working"))

    def _finished(self, result) -> None:
        from app.core.mac_storekit import Outcome
        self._busy(False)
        if result.outcome is Outcome.PURCHASED:
            self.result.setText(tr("settings.subscribe_done"))
            self.refresh()
            self.entitlement_changed.emit(True)
            return
        if result.outcome is Outcome.CANCELLED:
            # Not a failure, and it must not read as one. The person changed
            # their mind, which is a normal thing to do in a payment sheet.
            self.result.setText("")
            return
        self.result.setText(result.detail or tr("settings.subscribe_unavailable"))

    def _purchase(self) -> None:
        self._busy(True)
        self._sk.purchase(self._finished)

    def _restore(self) -> None:
        self._busy(True)
        self._sk.restore(self._finished)


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


class FamiliesPanel(QWidget):
    """Kill families: proposed by the evidence, adopted by the user.

    A family is employer plus wrong function — an operations role at a rejected
    employer dies, a strategy role at the same employer survives. It is never
    typed in: spec 5.3 anchors one to at least two real rejections, so the app
    spots the shape and offers it, and rule 8 makes arming it the user's call.

    The precedents are shown with every proposal, because "Kier + engineer" on
    its own is a rule to agree or disagree with in the abstract, and "you turned
    down these two" is a decision the user can actually check.
    """

    changed = Signal()

    def __init__(self, *, loader=None, adopter=None, refresher=None, parent=None):
        super().__init__(parent)
        self._load = loader or (lambda: [])
        self._adopt = adopter or (lambda name, on: None)
        self._refresh = refresher or (lambda: 0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        heading = QLabel(tr("families.heading"))
        heading.setObjectName("stepHeading")
        layout.addWidget(heading)

        body = QLabel(reflow(tr("families.body")))
        body.setObjectName("stepBody")
        body.setWordWrap(True)
        layout.addWidget(body)

        self.result = QLabel()
        self.result.setWordWrap(True)
        self.result.hide()
        layout.addWidget(self.result)

        self.empty = QLabel(reflow(tr("families.none")))
        self.empty.setObjectName("stepBody")
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty)

        self.listing = QListWidget()
        self.listing.setObjectName("ruleList")
        layout.addWidget(self.listing, 1)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.btn_adopt = QPushButton(tr("families.adopt"))
        self.btn_stand_down = QPushButton(tr("families.stand_down"))
        for b in (self.btn_adopt, self.btn_stand_down):
            b.setObjectName("secondary")
            b.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            b.setEnabled(False)
            row.addWidget(b)
        row.addStretch(1)
        # Proposals are found from the rejections on record, so this is only
        # ever worth pressing after a few decisions. It is a button rather than
        # something that runs on open: re-reading every rejection to find the
        # same proposals is work nobody asked for on a screen opened to change
        # an API key.
        self.btn_look = QPushButton(tr("families.look"))
        self.btn_look.setObjectName("secondary")
        self.btn_look.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        row.addWidget(self.btn_look)
        layout.addLayout(row)

        self.setStyleSheet(SETTINGS_STYLESHEET)
        self.btn_look.clicked.connect(self.look_for_proposals)
        self.listing.currentItemChanged.connect(self._on_select)
        self.btn_adopt.clicked.connect(lambda: self.set_adopted(True))
        self.btn_stand_down.clicked.connect(lambda: self.set_adopted(False))
        self.refresh()

    # -- state -------------------------------------------------------------
    def refresh(self) -> None:
        self.listing.clear()
        families = self._load() or []
        for fam in families:
            state = (tr("families.armed") if fam.adopted
                     else tr("families.proposed"))
            precedents = "; ".join(f"{t}" for _c, t in fam.precedents[:3])
            item = QListWidgetItem(
                f"{state}  {fam.name} — kills {', '.join(fam.kill_titles)}"
                f"  ·  saves {', '.join(fam.saves_titles[:3])}"
                f"  ·  because you rejected: {precedents}")
            item.setData(Qt.UserRole, fam.name)
            self.listing.addItem(item)
        self.empty.setVisible(not families)
        self.listing.setVisible(bool(families))
        self._on_select()

    def look_for_proposals(self) -> None:
        found = self._refresh()
        self.refresh()
        self._say(tr("families.found", count=found) if found
                  else tr("families.none_found"), ok=True)

    def _on_select(self, *_):
        has = self.listing.currentItem() is not None
        self.btn_adopt.setEnabled(has)
        self.btn_stand_down.setEnabled(has)

    def _say(self, text: str, ok: bool) -> None:
        self.result.setObjectName("ok" if ok else "bad")
        self.result.setText(text)
        self.result.style().unpolish(self.result)
        self.result.style().polish(self.result)
        self.result.setVisible(bool(text))

    def set_adopted(self, on: bool) -> None:
        from app.core.rules import RuleConflictError

        item = self.listing.currentItem()
        if item is None:
            return
        name = item.data(Qt.UserRole)
        try:
            self._adopt(name, on)
        except RuleConflictError as exc:
            # Name the role, exactly as the term editor does. "Invalid" throws
            # away the only fact the user needs.
            self._say(tr("rules.conflict") + "\n• " + "\n• ".join(
                tr("rules.conflict_line", term=c.term, title=c.pursued_title,
                   company=c.company) for c in exc.conflicts), ok=False)
            return
        except Exception as exc:  # noqa: BLE001
            self._say(str(exc), ok=False)
            return
        self._say(tr("families.armed_now", name=name) if on
                  else tr("families.stood_down", name=name), ok=True)
        self.refresh()
        self.changed.emit()


class SearchesPanel(QWidget):
    """The saved searches a morning run sweeps.

    Nothing created one before this panel existed, so `load_queries` returned
    an empty list and every run refused with "No saved queries. Add at least
    one before running" — with nowhere to add one.

    Searches carry a switch rather than only existing or not, because billing
    is per posting RETURNED: a search left on that nobody reads costs money
    every morning, and the seeds drawn from the user's stated aim arrive off
    for exactly that reason.
    """

    changed = Signal()

    def __init__(self, *, loader=None, saver=None, forgetter=None,
                 enabler=None, parent=None):
        super().__init__(parent)
        self._load = loader or (lambda: [])
        self._save = saver or (lambda label, titles: None)
        self._forget = forgetter or (lambda label: None)
        self._enable = enabler or (lambda label, on: None)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        heading = QLabel(tr("searches.heading"))
        heading.setObjectName("stepHeading")
        layout.addWidget(heading)

        body = QLabel(reflow(tr("searches.body")))
        body.setObjectName("stepBody")
        body.setWordWrap(True)
        layout.addWidget(body)

        self.result = QLabel()
        self.result.setWordWrap(True)
        self.result.hide()
        layout.addWidget(self.result)

        self.listing = QListWidget()
        self.listing.setObjectName("ruleList")
        layout.addWidget(self.listing, 1)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.field = QLineEdit()
        self.field.setObjectName("sentence")
        self.field.setPlaceholderText(tr("searches.placeholder"))
        self.field.returnPressed.connect(self.add)
        self.btn_add = QPushButton(tr("searches.add"))
        self.btn_toggle = QPushButton(tr("searches.toggle"))
        self.btn_remove = QPushButton(tr("searches.remove"))
        for b in (self.btn_add, self.btn_toggle, self.btn_remove):
            b.setObjectName("secondary")
            b.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        row.addWidget(self.field, 1)
        row.addWidget(self.btn_add)
        row.addWidget(self.btn_toggle)
        row.addWidget(self.btn_remove)
        layout.addLayout(row)

        self.setStyleSheet(SETTINGS_STYLESHEET)
        self.btn_add.clicked.connect(self.add)
        self.btn_toggle.clicked.connect(self.toggle)
        self.btn_remove.clicked.connect(self.remove)
        self.refresh()

    def refresh(self) -> None:
        self.listing.clear()
        for label, titles, on in self._load() or []:
            state = tr("searches.on") if on else tr("searches.off")
            item = QListWidgetItem(f"{state}  {label}  —  {', '.join(titles)}")
            item.setData(Qt.UserRole, (label, on))
            self.listing.addItem(item)

    def _say(self, text: str, ok: bool) -> None:
        self.result.setObjectName("ok" if ok else "bad")
        self.result.setText(text)
        self.result.style().unpolish(self.result)
        self.result.style().polish(self.result)
        self.result.setVisible(bool(text))

    def add(self) -> None:
        text = self.field.text().strip()
        if not text:
            return
        try:
            self._save(text, [text])
        except ValueError as exc:
            self._say(str(exc), ok=False)
            return
        self.field.clear()
        self._say(tr("searches.added", label=text), ok=True)
        self.refresh()
        self.changed.emit()

    def _selected(self):
        item = self.listing.currentItem()
        return item.data(Qt.UserRole) if item else (None, None)

    def toggle(self) -> None:
        label, on = self._selected()
        if label is None:
            return
        self._enable(label, not on)
        self._say(tr("searches.switched_off", label=label) if on
                  else tr("searches.switched_on", label=label), ok=True)
        self.refresh()
        self.changed.emit()

    def remove(self) -> None:
        label, _on = self._selected()
        if label is None:
            return
        self._forget(label)
        self._say(tr("searches.removed", label=label), ok=True)
        self.refresh()
        self.changed.emit()
