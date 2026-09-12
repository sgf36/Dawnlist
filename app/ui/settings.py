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

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QComboBox, QFrame, QGridLayout, QHBoxLayout,
                               QLabel,
                               QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton,
                               QScrollArea, QSizePolicy, QSpinBox, QStyle,
                               QVBoxLayout, QWidget)

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
        # Ignored while a check runs: Return in the field would otherwise
        # start a second one behind the disabled button.
        if not entered or not self.button.isEnabled():
            return

        self.button.setEnabled(False)
        self.result.setObjectName("")
        self.result.setText(tr("settings.key_checking"))
        verify = self._verify
        # OFF THE UI THREAD. The check is a round trip to Anthropic, and the
        # `repaint()` that stood here only got "Checking…" on screen before
        # the window stopped answering.
        self._verify_task = run_in_background(
            lambda: verify(entered),
            on_done=lambda outcome: self._verified(entered, outcome),
            on_error=lambda exc: self._verified(entered, (False, str(exc))))

    def _verified(self, entered: str, outcome) -> None:
        ok, message = outcome
        self.button.setEnabled(True)

        if ok:
            try:
                self._store(entered)
            except Exception:  # noqa: BLE001 - any failure means it was not kept
                # Never "Verified" over a key that was not kept: the user would
                # close Settings believing they were set up.
                ok, message = False, tr("settings.key_store_failed")

        self.result.setObjectName("ok" if ok else "bad")
        self.result.setText(message)
        self.result.style().unpolish(self.result)
        self.result.style().polish(self.result)

        if ok:
            # Cleared on success only. A rejected key stays in the box so the
            # user can see what they pasted and fix it, rather than starting
            # again from nothing.
            self.field.clear()
            self.refresh()


class LicencePanel(QWidget):
    """Enter a licence key, or redeem an override code for one."""

    licence_changed = Signal(bool)

    def __init__(self, *, redeemer=None, storer=None, reader=None,
                 checker=None, parent=None):
        super().__init__(parent)
        from app.core import entitlement

        self._redeem = redeemer or entitlement.redeem_override_code
        self._store = storer or entitlement.store_licence
        self._read = reader or entitlement.stored_licence
        # Looked up when called, so the transport can be replaced after the
        # panel is built.
        self._check = checker or (lambda key: entitlement.licence_check(key))
        self._verify_seq = 0

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

        # What the SERVER says about the stored licence. "Licence saved." was
        # the only feedback, and it was shown for a mistyped key, a refunded
        # one and a real one alike; the truth arrived at tomorrow's run.
        self.verdict = QLabel()
        self.verdict.setWordWrap(True)
        layout.addWidget(self.verdict)

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
        # Selectable, because a licence the credential store refused is shown
        # here, and a key that cannot be copied is a key retyped wrongly.
        self.result.setTextInteractionFlags(Qt.TextSelectableByMouse)
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
        if not entered or not self.button.isEnabled():
            return

        self.button.setEnabled(False)
        self._show(None, tr("settings.licence_checking"))

        # One field for both, because a user does not care which they were
        # given. A licence key starts DAWN-; anything else is tried as a code.
        if entered.upper().startswith("DAWN-"):
            self._keep(entered, tr("settings.licence_saved"),
                       tr("settings.licence_store_failed"))
            return

        redeem = self._redeem
        # Off the UI thread: redeeming is a round trip to the Worker.
        self._redeem_task = run_in_background(
            lambda: redeem(entered),
            # The code may be single-use and is spent by the time the store is
            # asked, so a failed save shows the licence: the only copy of it.
            on_done=lambda licence: self._keep(
                licence, tr("settings.code_redeemed"),
                tr("settings.code_store_failed", key=licence)),
            on_error=lambda exc: self._finish(False, str(exc)))

    def _keep(self, key: str, saved_text: str, failed_text: str) -> None:
        try:
            self._store(key)
        except Exception:  # noqa: BLE001 - any failure means it was not kept
            self._finish(False, failed_text)
            return
        self._finish(True, saved_text)
        self.verify_licence(key)

    def showEvent(self, event):
        # Asked when the panel is SEEN, never when it is built: constructing
        # a window must not reach the network.
        super().showEvent(event)
        existing = self._read()
        if existing:
            self.verify_licence(existing)

    def verify_licence(self, key: str) -> None:
        """Ask the Worker about `key`, off the UI thread, and say what it said."""
        self._verify_seq += 1
        seq = self._verify_seq
        self.verdict.setText(tr("settings.licence_checking"))
        check = self._check

        def answered(answer, seq=seq):
            # A slow answer about the previous key must not overwrite the
            # answer about the one just saved.
            if seq == self._verify_seq:
                self._show_verdict(answer)

        self._check_task = run_in_background(
            lambda: check(key), on_done=answered,
            on_error=lambda _exc: answered(("unreachable", {})))

    def _show_verdict(self, answer) -> None:
        outcome, body = answer
        if outcome == "ok" and body.get("ok"):
            text = tr("settings.licence_valid", plan=str(body.get("plan") or "—"))
        elif outcome == "refused" and body.get("error") == "licence_inactive":
            text = tr("settings.licence_inactive")
        elif outcome in ("refused", "ok"):
            text = tr("settings.licence_unknown")
        else:
            # Kept, and honestly unconfirmed: the key may be perfectly good.
            text = tr("settings.licence_unverified")
        self.verdict.setText(text)

    def _finish(self, ok: bool, message: str) -> None:
        self.button.setEnabled(True)
        self._show(ok, message)
        if ok:
            self.field.clear()
            self.refresh()

    def _show(self, ok: bool | None, message: str) -> None:
        self.result.setObjectName("" if ok is None else ("ok" if ok else "bad"))
        self.result.setText(message)
        self.result.style().unpolish(self.result)
        self.result.style().polish(self.result)


def _divider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setStyleSheet("color:#ddd8cc;")
    return line


class SettingsWindow(QWidget):
    """The panels, for the Settings menu and for onboarding.

    Which purchase panel appears follows the build, because the rules do: both
    Windows channels sell a Paddle licence, so `store` and `direct` show the
    licence box; guideline 3.1.1 forbids a key on the Mac, so `mas` shows the
    StoreKit subscription instead; and a build that does not know which it is
    shows neither rather than guessing.
    """

    def __init__(self, parent=None, *, variant=None, rules=None,
                 families=None, searches=None, is_admin=None, home=None):
        super().__init__(parent)
        from app.core.build_variant import variant as read_variant

        #: Injectable so a test can answer without a network call, and so
        #: nothing reaches the live Worker merely because a window was built.
        self._is_admin = is_admin
        self._admin_checked = False
        #: The window Settings was opened from. Not the Qt parent: parenting
        #: would embed this screen inside that window instead of opening it.
        self._home = home

        self.setWindowTitle(tr("settings.title"))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # THE WAY BACK. This window opens at almost the size of the one beneath
        # and covers it, so to the person using it Settings is a page they have
        # gone to, and a page needs a visible way back. The title-bar close
        # button is not one: nothing about it says the shortlist is still there.
        # Above the scroll area, so it cannot scroll out of reach.
        #
        # `onboarding.back` rather than a new key: it is the same word, already
        # translated in every locale, and a new key would ship in English only
        # until the next translation run.
        bar = QHBoxLayout()
        bar.setContentsMargins(16, 10, 16, 6)
        self.btn_back = QPushButton(tr("onboarding.back"))
        self.btn_back.setObjectName("secondary")
        # SP_ArrowBack follows the layout direction, so it points the right
        # way in the right-to-left locales too.
        self.btn_back.setIcon(self.style().standardIcon(QStyle.SP_ArrowBack))
        self.btn_back.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.btn_back.clicked.connect(self.go_home)
        bar.addWidget(self.btn_back)
        bar.addStretch(1)
        outer.addLayout(bar)

        # Scrolled, because three stacked panels want ~916px and a 768-tall
        # laptop screen is ordinary. Without this the licence box sits below
        # the bottom of the display on the one build that needs it, with no
        # way to reach it — and a user who cannot enter their licence key has
        # bought something inert.
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
        #
        # NAMED, not "anything but mas". A build with no variant flag, or two,
        # was made wrong and may well be a Mac build; offering it a key box is
        # the shape guideline 3.1.1 forbids, and guessing is worse than
        # showing nothing.
        self.shows_licence = build in ("store", "direct")
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

    def go_home(self) -> None:
        """Leave Settings for the window it was opened from.

        No Escape shortcut on purpose: Escape pressed in the key or licence
        field would close the screen and discard what was just pasted.
        """
        self.close()
        home = self._home
        if home is not None:
            if home.isMinimized():
                home.showNormal()
            home.raise_()
            home.activateWindow()

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
#: "bound by written terms", and a page nobody agreed to binds nobody.
#:
#: ACCEPTANCE HAPPENS IN THE APPLICATION, never at a checkout: Apple's and
#: Microsoft's never show these terms. The step is the first screen of setup
#: and it records the terms' "Last updated" date, so a revision asks again —
#: see `app/onboarding/terms.py`, which owns that date. This link is what
#: keeps the terms findable afterwards by somebody who has agreed to them.
TERMS_URL = "https://dawnlist.spencerfields.com/terms.html"

#: The two documents Apple requires an auto-renewing subscription to link to
#: from INSIDE the app, alongside the title, the length and the price
#: (guideline 3.1.2(c) — version 1.1.0 (75) was rejected for their absence).
#:
#: The EULA is Apple's standard one, which is what the Mac listing's
#: description already cites. A Mac subscriber is bound by that document and by
#: no other, so the app and the listing have to name the SAME url: two
#: plausible links to two different agreements is worse than one, and
#: `test_the_app_and_the_mac_listing_cite_the_same_eula` holds them together.
PRIVACY_URL = "https://dawnlist.spencerfields.com/privacy.html"
APPLE_EULA_URL = (
    "https://www.apple.com/legal/internet-services/itunes/dev/stdeula/")

#: What Apple's storefront calls this subscription. Mirrors DISPLAY_NAME in
#: `tools/asc_subscription.py`, as `settings.subscribe_length` mirrors its
#: ONE_MONTH period: a title or a term shown here that differs from the one on
#: Apple's payment sheet reads as a second product rather than the one being
#: bought.
SUBSCRIPTION_TITLE = "Dawnlist"

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
    their own §4. A link is not binding on its own, and no store checkout shows
    these terms, so acceptance is recorded in the application before first use:
    the first screen of setup, gating the morning run exactly as calibration
    does (`app/onboarding/terms.py`). This panel exists so the terms stay
    FINDABLE afterwards by someone who has agreed to them.
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

    def __init__(self, *, key_source=None, api=None, confirm=None, parent=None):
        super().__init__(parent)
        from app.core import admin as admin_api
        from app.core import entitlement

        self._api = api or admin_api
        self._key_source = key_source or entitlement.stored_licence
        #: `confirm(code) -> bool`, asked before a withdrawal. Injectable so a
        #: test can answer without a modal dialog.
        self._confirm = confirm or self._ask_before_revoking
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

        # Days rather than a date: "how long should this work?" is the question
        # the issuer can answer. Codes went out with no expiry at all, so every
        # reviewer's and friend's grant stood for ever.
        self.expires = QSpinBox()
        self.expires.setRange(1, 3650)
        self.expires.setValue(self._api.DEFAULT_EXPIRY_DAYS)
        self.expires.setSuffix(tr("settings.admin_expires_suffix"))
        self.expires.setToolTip(tr("settings.admin_expires_tip"))
        form.addWidget(self.expires)

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
        self.role.currentTextChanged.connect(self._limit_expiry)
        self.plan.currentTextChanged.connect(self._limit_expiry)
        self._limit_expiry()

    # -- helpers -----------------------------------------------------------
    def _role_changed(self, role: str) -> None:
        """A managed code is the usual shape for a reviewer, and a reviewer
        code must not be single-use. Nudge rather than enforce: an admin may
        still want a one-shot managed code for a friend."""
        if role == "managed" and self.uses.value() == 1:
            self.uses.setValue(self._api.REVIEW_USES)

    def _limit_expiry(self, *_) -> None:
        """Zero — never — only for an administrator's own code or the owner plan.

        A floor of one day for everything else, so an open-ended grant cannot
        be issued by leaving the box at its lowest value. The "never" wording
        is shown only where zero is allowed: a spin box shows its special text
        at its minimum, which would otherwise label one day "never".
        """
        if self._api.may_be_open_ended(self.role.currentText(),
                                       self.plan.currentText()):
            self.expires.setMinimum(0)
            self.expires.setSpecialValueText(tr("settings.admin_never"))
        else:
            self.expires.setSpecialValueText("")
            self.expires.setMinimum(1)

    def _ask_before_revoking(self, code: str) -> bool:
        """Withdrawing also expires every licence already redeemed from the
        code, and nothing reverses it — one click on the wrong row would cut a
        reviewer off mid-review."""
        from PySide6.QtWidgets import QMessageBox

        answer = QMessageBox.question(
            self, tr("settings.admin_confirm_revoke_title"),
            tr("settings.admin_confirm_revoke", code=code),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return answer == QMessageBox.Yes

    def _key(self):
        return self._key_source()

    def _busy(self, on: bool) -> None:
        # All three together: a revoke started while a list is still arriving
        # would act on the row indexes of the list it is about to replace.
        for button in (self.btn_issue, self.btn_refresh, self.btn_revoke):
            button.setEnabled(not on)

    def _run(self, work, done) -> None:
        """Every console action is a round trip to the Worker, so off the UI
        thread, with the buttons disabled until it answers."""
        self._busy(True)
        self._task = run_in_background(work, on_done=done,
                                       on_error=self._failed)

    def _failed(self, exc) -> None:
        self._busy(False)
        self.result.setText(str(exc))

    def refresh(self) -> None:
        key = self._key()
        if not key:
            self.result.setText(tr("settings.admin_no_licence"))
            return
        api = self._api
        self._run(lambda: api.list_codes(key), lambda codes: self._listed(codes))

    def _listed(self, codes, message: str = "") -> None:
        self._busy(False)
        self._codes = codes
        self.codes.clear()
        for row in self._codes:
            used, cap = row.get("uses", 0), row.get("max_uses", 1)
            state = " REVOKED" if row.get("revoked") else ""
            expires = row.get("expires_at")
            when = (f"  {tr('settings.admin_expires_on', date=str(expires)[:10])}"
                    if expires else "")
            self.codes.addItem(
                f"{row.get('code')}  {row.get('role')}/{row.get('plan')}  "
                f"{used}/{cap}{state}{when}  — {row.get('note') or ''}")
        # LISTED FIRST, THEN SAY WHAT HAPPENED. Listing blanked the message,
        # so setting it beforehand wiped the code the operator was told to
        # copy — on a line that reads "Copy it now; it is shown once here".
        self.result.setText(message)

    def _issue(self) -> None:
        key = self._key()
        if not key:
            self.result.setText(tr("settings.admin_no_licence"))
            return
        api = self._api
        note, role = self.note.text(), self.role.currentText()
        plan, uses = self.plan.currentText(), self.uses.value()
        expires_at = api.expiry_after(self.expires.value())

        def work():
            made = api.issue_code(key, note=note, role=role, plan=plan,
                                  max_uses=uses, expires_at=expires_at)
            return made, api.list_codes(key)

        def done(pair):
            made, codes = pair
            self.note.clear()
            self._listed(codes, tr("settings.admin_issued",
                                   code=made.get("code", "")))

        self._run(work, done)

    def _revoke(self) -> None:
        index = self.codes.currentRow()
        if index < 0 or index >= len(self._codes):
            self.result.setText(tr("settings.admin_pick_one"))
            return
        key = self._key()
        code = self._codes[index].get("code")
        if not self._confirm(code):
            return
        api = self._api

        def work():
            api.revoke_code(key, code)
            return api.list_codes(key)

        self._run(work, lambda codes: self._listed(
            codes, tr("settings.admin_revoked", code=code)))


class StoreKitEvents(QObject):
    """Where the one transaction observer reports, for whichever panel is open.

    A SIGNAL, not a callback handed to StoreKit by the panel that pressed the
    button. That panel may be closed by the time Apple answers, and a
    transaction Apple redelivers at launch has no panel at all.
    """

    finished = Signal(object)


_STOREKIT_EVENTS: StoreKitEvents | None = None


def storekit_events() -> StoreKitEvents:
    global _STOREKIT_EVENTS
    if _STOREKIT_EVENTS is None:
        _STOREKIT_EVENTS = StoreKitEvents()
    return _STOREKIT_EVENTS


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

    WHAT A PURCHASE SCREEN HAS TO SAY, AND WHY IT IS HERE RATHER THAN ONLY IN
    THE METADATA. Guideline 3.1.2(c) wants the title, the length, the price and
    working links to the privacy policy and the EULA in the app's own purchase
    flow. Version 1.1.0 (75) had all of it in App Store Connect and none of it
    on this screen, and was rejected for exactly that.

    AND NO ACCESS-CODE BOX. It stood here until Apple rejected the same build
    under 3.1.1: "the app uses access codes to unlock app features". A comp
    code is a grant rather than a purchase, which is why it was defensible, but
    the reviewer's reading is the one that ships — so a Mac build now offers no
    redemption at all, and this panel deliberately has no text field of any
    kind. Windows keeps it, in `LicencePanel`, which no `mas` build lays out.
    The Worker's /v1/redeem route and the code machinery are untouched: this is
    about what the Mac build OFFERS, not about what exists. The cost is that an
    App Review reviewer can no longer let themselves in with a code and must
    complete a sandbox subscription instead.
    """

    entitlement_changed = Signal(bool)

    def __init__(self, *, storekit=None, events=None, parent=None):
        super().__init__(parent)
        from app.core import mac_storekit

        self._sk = storekit or mac_storekit
        (events or storekit_events()).finished.connect(self._finished)

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

        # The title and the length, which 3.1.2(c) asks for beside the price.
        # One label rather than three: a customer reads "Dawnlist — 1 month"
        # as a sentence, and Apple's requirement is that the terms be visible,
        # not that they be itemised.
        self.terms = QLabel(tr("settings.subscribe_terms",
                               title=SUBSCRIPTION_TITLE,
                               length=tr("settings.subscribe_length")))
        self.terms.setObjectName("stepBody")
        self.terms.setWordWrap(True)
        layout.addWidget(self.terms)

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

        # THE TWO LINKS 3.1.2(c) REQUIRES, AND THEY HAVE TO WORK. A link is
        # only evidence of an agreement if a person can read what it points at,
        # so these open in the browser rather than describing a document.
        links = QHBoxLayout()
        links.setSpacing(16)
        self.link_privacy = QLabel(
            f'<a href="{PRIVACY_URL}">{tr("settings.privacy_link")}</a>')
        self.link_eula = QLabel(
            f'<a href="{APPLE_EULA_URL}">{tr("settings.eula_link")}</a>')
        for link in (self.link_privacy, self.link_eula):
            link.setObjectName("dataTerms")
            link.setOpenExternalLinks(True)
            links.addWidget(link)
        links.addStretch(1)
        layout.addLayout(links)

        self.buy.clicked.connect(self._purchase)
        self.restore.clicked.connect(self._restore)
        self.refresh()

    def refresh(self) -> None:
        """Ask Apple what to show. On construction, and after a purchase.

        A MISSING PRICE IS NOT AN ERROR STATE. Apple returns nothing for a
        product its storefront does not know yet, which is also what a
        brand-new subscription looks like while it propagates. "Not available
        right now" is true in both cases; "something went wrong" is not.

        OFF THE UI THREAD, and that stopped being optional the moment this
        panel moved into the setup wizard. `price()` is a StoreKit product
        request — a network round trip to Apple — and it used to run only when
        somebody opened Settings, where a pause is survivable. The wizard
        builds this panel at launch, so on a Mac the same call would have held
        the very first screen of the app before it painted.
        """
        self.buy.setEnabled(False)
        self.restore.setEnabled(False)
        sk = self._sk
        self._price_task = run_in_background(
            lambda: (sk.available(), sk.price() if sk.available() else None,
                     sk.can_make_payments()),
            on_done=self._show_availability,
            # Apple being unreachable is "not available right now", which the
            # method above already says is the honest reading. It is not an
            # error to put in front of somebody mid-setup.
            on_error=lambda _exc: self._show_availability((False, None, False)))

    def _show_availability(self, state) -> None:
        available, shown, can_pay = state
        self.price.setText(shown or "")
        can = bool(available and can_pay)
        self.buy.setEnabled(can)
        self.restore.setEnabled(bool(available))
        if not can:
            self.result.setText(tr("settings.subscribe_unavailable"))

    def _busy(self, on: bool) -> None:
        idle = not on
        self.buy.setEnabled(idle and self._sk.available()
                            and self._sk.can_make_payments())
        self.restore.setEnabled(idle and self._sk.available())
        if on:
            self.result.setText(tr("settings.subscribe_working"))
            self._start_waiting()
        else:
            waiting = getattr(self, "_waiting", None)
            if waiting is not None:
                waiting.stop()

    def _finished(self, result) -> None:
        from app.core.mac_storekit import Outcome
        # Stops the "still waiting" timer as well, so a late arrival does not
        # get overwritten a moment later by a message about waiting for it.
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
        if result.outcome is Outcome.IN_PROGRESS:
            # A second press while Apple's queue still holds the first. Adding
            # another payment would stack purchase sheets; saying nothing new
            # is the honest answer.
            self.result.setText(tr("settings.subscribe_working"))
            return
        if result.outcome is Outcome.DEFERRED:
            self.result.setText(tr("settings.subscribe_deferred"))
            return
        self.result.setText(result.detail or tr("settings.subscribe_unavailable"))

    #: How long to wait before saying something, in milliseconds. Not a
    #: cancel and not a failure — a purchase waits on a human and may take as
    #: long as it takes. This is only the point at which silence stops being
    #: informative.
    STILL_WAITING_MS = 40_000

    def _purchase(self) -> None:
        self._busy(True)
        sk = self._sk
        if sk.has_product():
            self._start_purchase()
            return
        # FETCHED OFF THE UI THREAD, then bought from the answer on it. The
        # fetch waits for a delegate that fires on the main run loop, so doing
        # it here waited for itself for thirty seconds and then failed.
        self._product_task = run_in_background(
            sk.load_product,
            on_done=lambda _product: self._start_purchase(),
            on_error=lambda _exc: self._start_purchase())

    def _start_purchase(self) -> None:
        started = self._sk.purchase()
        if started is not None:
            self._finished(started)

    def restore_purchases(self) -> None:
        """Start a restore, exactly as the Restore button does.

        Public because the setup wizard's menu restores through this panel,
        where the answer is shown, rather than firing StoreKit from somewhere
        with nothing to display it.
        """
        self._restore()

    def _restore(self) -> None:
        self._busy(True)
        started = self._sk.restore()
        if started is not None:
            self._finished(started)

    def _start_waiting(self) -> None:
        """Arm the "still waiting" message.

        THE DEAD END THIS REMOVES. `_busy(True)` disabled both buttons and
        showed "Talking to the App Store…", and every route out of that state
        ran through a StoreKit callback. When the callback does not arrive —
        no sandbox account signed in, Apple's sheet opening behind the window,
        a screen-shared session where it never appears — the screen stays like
        that for ever, with the two things that could fix it greyed out.

        Nothing is cancelled here. A purchase genuinely in flight still lands,
        and `_finished` still runs. This only stops silence being the whole of
        the interface.
        """
        from PySide6.QtCore import QTimer

        self._waiting = QTimer(self)
        self._waiting.setSingleShot(True)
        self._waiting.timeout.connect(self._still_waiting)
        self._waiting.start(self.STILL_WAITING_MS)

    def _still_waiting(self) -> None:
        # Re-enabled, not reset: pressing Subscribe again is a legitimate
        # thing to want, and Restore is the answer for somebody who already
        # paid and is watching a sheet that never opened.
        self.result.setText(tr("settings.subscribe_still_waiting"))
        self.buy.setEnabled(True)
        self.restore.setEnabled(True)


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
                 enabler=None, where_loader=None, where_saver=None,
                 parent=None):
        super().__init__(parent)
        self._load = loader or (lambda: [])
        self._save = saver or (lambda label, titles: None)
        self._forget = forgetter or (lambda label: None)
        self._enable = enabler or (lambda label, on: None)
        self._where_load = where_loader or (lambda: "")
        self._where_save = where_saver or (lambda text: None)

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

        # ONE PLACE FOR EVERY SEARCH, above the list. It is also the repair for
        # installs whose searches were switched on with no location: those are
        # refused at the run, and this is the single entry that fixes them all.
        where_row = QHBoxLayout()
        where_row.setSpacing(6)
        where_row.addWidget(QLabel(tr("searches.where_label")))
        self.where_field = QLineEdit()
        self.where_field.setObjectName("sentence")
        self.where_field.setPlaceholderText(tr("searches.where_placeholder"))
        self.where_field.setText(self._where_load() or "")
        self.where_field.returnPressed.connect(self.apply_where)
        self.btn_where = QPushButton(tr("searches.where_apply"))
        self.btn_where.setObjectName("secondary")
        self.btn_where.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.btn_where.clicked.connect(self.apply_where)
        where_row.addWidget(self.where_field, 1)
        where_row.addWidget(self.btn_where)
        layout.addLayout(where_row)

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

    def apply_where(self) -> None:
        text = self.where_field.text().strip()
        try:
            self._where_save(text)
        except ValueError as exc:
            self._say(str(exc), ok=False)
            return
        shown = self._where_load() or text
        self.where_field.setText(shown)
        self._say(tr("searches.where_applied", where=shown), ok=True)
        self.refresh()
        self.changed.emit()

    def toggle(self) -> None:
        label, on = self._selected()
        if label is None:
            return
        try:
            self._enable(label, not on)
        except ValueError as exc:
            # Switching on a search with no location is refused; saying why is
            # the difference between a fix and a button that seems broken.
            self._say(str(exc), ok=False)
            return
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
