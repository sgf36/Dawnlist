"""The onboarding screens: ingest, then the calibration gate.

The calibration screen is the one that matters, and it is designed against one
temptation: making the gate easy to get past. It is not a form to complete, it
is the step where the user's judgement is transferred into the brief, and a
user who clicks through it without disagreeing with anything has taught the
brief nothing.

So the Finish button stays disabled and **every** blocking reason is listed at
once, permanently visible — not revealed one at a time as each is cleared,
which makes a five-minute step feel endless and trains people to guess.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QPushButton,
                               QPlainTextEdit, QRadioButton, QScrollArea, QSizePolicy,
                               QSplitter, QStackedWidget, QTextBrowser,
                               QVBoxLayout, QWidget)

from app.i18n import tr
from app.onboarding.calibration import CalibrationItem, CalibrationResult
from app.onboarding.interview import INGEST_GUIDANCE
from app.ui.review import CREAM, GOLD, GOLD_DEEP, INK, TEAL, TEAL_LIFTED

def VERDICT_CHOICES() -> list[tuple[str, str]]:
    """(stored value, label the user sees). Called rather than computed once,
    so switching locale re-reads the catalogue."""
    return [("strong", tr("onboarding.v.strong")),
            ("possible", tr("onboarding.v.possible")),
            ("rejected", tr("onboarding.v.rejected"))]


def verdict_label(value: str | None) -> str:
    """The label the user actually clicked, never the internal bucket value.

    "you say strong" leaks the storage vocabulary into a sentence the user
    reads; they clicked a button that said "Strong fit".
    """
    if not value:
        return ""
    return dict(VERDICT_CHOICES()).get(value, value)


def app_verdict_label(value: str | None) -> str:
    """How the APP's verdict is described, which is not how the user's is.

    The user's buttons are written in their voice ("Not for me"), and putting
    that phrasing in the app's mouth — "Dawnlist said not for me" — reads as
    nonsense. The two vocabularies are deliberately separate.
    """
    if not value:
        return ""
    return tr(f"onboarding.app.{value}")


def reflow(text: str) -> str:
    """Undo hard line breaks inside paragraphs, keep the paragraph breaks.

    Source strings are wrapped at ~78 characters for readability in the file.
    QLabel HONOURS those newlines, so the text renders at whatever width the
    source happened to use and ignores the pane it is in — which looks like a
    word-wrap bug and is not one. Blank lines are kept, because they separate
    paragraphs; single newlines are joined.

    This changes whitespace only. The wording is still shown verbatim.
    """
    paragraphs = [" ".join(part.split())
                  for part in re.split(r"\n\s*\n", text.strip())]
    return "\n\n".join(p for p in paragraphs if p).replace("**", "")


ONBOARDING_STYLESHEET = f"""
QLabel#stepHeading {{
    font-size: 19px;
    font-weight: 700;
    color: {TEAL};
}}
QLabel#stepBody {{ color: #45505a; }}

QFrame#dropZone {{
    border: 2px dashed #b9b3a6;
    border-radius: 10px;
    background: #faf8f4;
}}
QFrame#dropZoneActive {{
    border: 2px dashed {TEAL};
    border-radius: 10px;
    background: #eef4f2;
}}
QFrame#dropZone QLabel, QFrame#dropZoneActive QLabel {{
    background: transparent;
    color: #45505a;
}}

QFrame#blockers {{
    background: {GOLD_DEEP};
    border-radius: 6px;
}}
QFrame#blockers QLabel {{
    background: transparent;
    color: {CREAM};
}}
QFrame#blockersClear {{
    background: {TEAL};
    border-radius: 6px;
}}
QFrame#blockersClear QLabel {{
    background: transparent;
    color: {CREAM};
}}

QFrame#warnings {{
    background: #fdf6e9;
    border: 1px solid #e6d3ac;
    border-radius: 6px;
}}
QFrame#warnings QLabel {{ background: transparent; color: #6b5426; }}

QPushButton#primary {{
    min-height: 34px;
    padding: 8px 22px;
    border-radius: 6px;
    border: 1px solid {TEAL};
    background: {TEAL};
    color: {CREAM};
    font-weight: 600;
}}
QPushButton#primary:hover {{ background: {TEAL_LIFTED}; }}
QPushButton#primary:disabled {{
    background: #d5d1c8;
    border-color: #c8c3b8;
    color: #8b8578;
}}
QPushButton#secondary {{
    min-height: 34px;
    padding: 8px 18px;
    border-radius: 6px;
    border: 1px solid #cfcabf;
    background: #ffffff;
    color: {INK};
}}
QPushButton#secondary:hover {{ background: #f4f1ea; }}

QFrame#verdictCard {{
    border: 1px solid #d8d4cc;
    border-radius: 8px;
    background: #ffffff;
}}
QLineEdit#sentence {{
    border: 1px solid #cfcabf;
    border-radius: 5px;
    padding: 7px 9px;
}}
QLineEdit#sentence[needed="true"] {{ border: 1px solid {GOLD_DEEP}; }}
QLabel#yourVerdict {{ color: #6b7480; }}
QLabel#disagreement {{
    color: {GOLD_DEEP};
    font-weight: 600;
}}
"""


class DropZone(QFrame):
    """Drag CVs onto the app. The same gesture the alert-email import uses."""

    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(120)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        label = QLabel(tr("onboarding.drop_hint"))
        label.setAlignment(Qt.AlignCenter)
        label.setWordWrap(True)
        layout.addWidget(label)

    def _set_active(self, active: bool) -> None:
        self.setObjectName("dropZoneActive" if active else "dropZone")
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, event):  # noqa: N802 - Qt naming
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._set_active(True)

    def dragLeaveEvent(self, event):  # noqa: N802
        self._set_active(False)

    def dropEvent(self, event):  # noqa: N802
        self._set_active(False)
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls()
                 if u.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()


class IngestPage(QWidget):
    """Step one: the CV corpus."""

    files_added = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        heading = QLabel(tr("onboarding.ingest_heading"))
        heading.setObjectName("stepHeading")
        layout.addWidget(heading)

        # The guidance is shown VERBATIM. Both sentences are load-bearing:
        # users hand over a single tidied CV and lose exactly the history the
        # screen needs.
        body = QLabel(reflow(INGEST_GUIDANCE))
        body.setObjectName("stepBody")
        body.setWordWrap(True)
        layout.addWidget(body)

        self.drop = DropZone()
        self.drop.files_dropped.connect(self.files_added)
        layout.addWidget(self.drop)

        self.files = QListWidget()
        self.files.setMaximumHeight(140)
        layout.addWidget(self.files)

        self.warnings = QFrame()
        self.warnings.setObjectName("warnings")
        wl = QVBoxLayout(self.warnings)
        wl.setContentsMargins(14, 10, 14, 10)
        self.warnings_label = QLabel()
        self.warnings_label.setWordWrap(True)
        wl.addWidget(self.warnings_label)
        self.warnings.hide()
        layout.addWidget(self.warnings)

        layout.addStretch(1)
        self.setStyleSheet(ONBOARDING_STYLESHEET)

    def show_corpus(self, names: list[str], warnings: list[str]) -> None:
        self.files.clear()
        self.files.addItems(names)
        if warnings:
            # Named files, so the user knows WHICH document was not read.
            self.warnings_label.setText("• " + "\n• ".join(warnings))
            self.warnings.show()
        else:
            self.warnings.hide()


@dataclass
class _ItemWidgets:
    item: CalibrationItem
    group: QButtonGroup
    sentence: QLineEdit
    disagreement: QLabel


class CalibrationPage(QWidget):
    """Step two: the gate.

    Ten live postings, the app's verdict on each, and the user's correction —
    with the sentence that would have got it right.
    """

    changed = Signal()
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._widgets: list[_ItemWidgets] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        heading = QLabel(tr("onboarding.calibration_heading"))
        heading.setObjectName("stepHeading")
        layout.addWidget(heading)

        body = QLabel(reflow(tr("onboarding.calibration_body")))
        body.setObjectName("stepBody")
        body.setWordWrap(True)
        layout.addWidget(body)

        self.blockers = QFrame()
        self.blockers.setObjectName("blockers")
        bl = QVBoxLayout(self.blockers)
        bl.setContentsMargins(14, 10, 14, 10)
        self.blockers_label = QLabel()
        self.blockers_label.setWordWrap(True)
        bl.addWidget(self.blockers_label)
        layout.addWidget(self.blockers)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self._holder = QWidget()
        self._holder_layout = QVBoxLayout(self._holder)
        self._holder_layout.setContentsMargins(0, 0, 8, 0)
        self._holder_layout.setSpacing(10)
        self.scroll.setWidget(self._holder)
        layout.addWidget(self.scroll, 1)

        actions = QHBoxLayout()
        # Separated from the scrolling list, so the button does not read as
        # floating over the last card.
        actions.setContentsMargins(0, 8, 0, 0)
        self.btn_finish = QPushButton(tr("onboarding.finish"))
        self.btn_finish.setObjectName("primary")
        self.btn_finish.setEnabled(False)
        self.btn_finish.clicked.connect(self.finished)
        actions.addStretch(1)
        actions.addWidget(self.btn_finish)
        layout.addLayout(actions)

        self.setStyleSheet(ONBOARDING_STYLESHEET)

    # -- population --------------------------------------------------------
    def load(self, items: list[CalibrationItem]) -> None:
        while self._holder_layout.count():
            entry = self._holder_layout.takeAt(0)
            if entry.widget():
                entry.widget().deleteLater()
        self._widgets.clear()

        for item in items:
            self._holder_layout.addWidget(self._card(item))
        self._holder_layout.addStretch(1)
        self._refresh()

    def _card(self, item: CalibrationItem) -> QFrame:
        card = QFrame()
        card.setObjectName("verdictCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        title = QLabel(f"{item.title} — {item.company}")
        tf = QFont()
        tf.setBold(True)
        title.setFont(tf)
        title.setWordWrap(True)
        layout.addWidget(title)

        verdict = QLabel(tr("onboarding.app_said",
                            verdict=app_verdict_label(item.app_verdict),
                            reason=item.app_reason))
        verdict.setWordWrap(True)
        verdict.setStyleSheet("color:#45505a;")
        layout.addWidget(verdict)

        row = QHBoxLayout()
        row.setSpacing(14)
        yours = QLabel(tr("onboarding.your_verdict"))
        yours.setObjectName("yourVerdict")
        row.addWidget(yours)
        group = QButtonGroup(card)
        for value, label in VERDICT_CHOICES():
            btn = QRadioButton(label)
            btn.setProperty("verdict", value)
            if item.user_verdict == value:
                btn.setChecked(True)
            group.addButton(btn)
            row.addWidget(btn)
        row.addStretch(1)
        layout.addLayout(row)

        disagreement = QLabel()
        disagreement.setObjectName("disagreement")
        disagreement.setWordWrap(True)
        disagreement.hide()
        layout.addWidget(disagreement)

        sentence = QLineEdit(item.brief_sentence)
        sentence.setObjectName("sentence")
        # The spec's own phrasing. It stops the user editing the conversation —
        # a fix that evaporates — and makes them edit the brief, which persists.
        sentence.setPlaceholderText(tr("onboarding.sentence_placeholder"))
        layout.addWidget(sentence)

        widgets = _ItemWidgets(item=item, group=group, sentence=sentence,
                               disagreement=disagreement)
        self._widgets.append(widgets)

        group.buttonToggled.connect(lambda *_: self._sync(widgets))
        sentence.textChanged.connect(lambda *_: self._sync(widgets))
        self._sync(widgets, refresh=False)
        return card

    # -- state -------------------------------------------------------------
    def _sync(self, widgets: _ItemWidgets, *, refresh: bool = True) -> None:
        checked = widgets.group.checkedButton()
        widgets.item.user_verdict = (checked.property("verdict")
                                     if checked else None)
        widgets.item.brief_sentence = widgets.sentence.text()

        # The sentence box only matters when the user has disagreed — showing
        # it as required on every card would be noise.
        needed = widgets.item.needs_sentence
        widgets.sentence.setProperty("needed", "true" if needed else "false")
        widgets.sentence.style().unpolish(widgets.sentence)
        widgets.sentence.style().polish(widgets.sentence)
        widgets.sentence.setVisible(widgets.item.disagreed)

        # Naming the disagreement is what makes the sentence box make sense.
        # Without it the box reads as "justify your answer", which is not what
        # it is for: the app and the user disagree, and the sentence is how the
        # brief learns the difference.
        if widgets.item.disagreed:
            widgets.disagreement.setText(tr(
                "onboarding.disagreement",
                app=app_verdict_label(widgets.item.app_verdict),
                user=verdict_label(widgets.item.user_verdict)))
        widgets.disagreement.setVisible(widgets.item.disagreed)

        if refresh:
            self._refresh()

    def result(self) -> CalibrationResult:
        return CalibrationResult(items=[w.item for w in self._widgets])

    def _refresh(self) -> None:
        result = self.result()
        reasons = result.blocking_reasons()
        if reasons:
            self.blockers.setObjectName("blockers")
            # Every reason at once. Revealing them one at a time makes a
            # five-minute step feel endless and trains people to guess.
            self.blockers_label.setText("• " + "\n• ".join(reasons))
        else:
            self.blockers.setObjectName("blockersClear")
            text = tr("onboarding.ready")
            if result.low_signal:
                text += " " + tr("onboarding.low_signal")
            self.blockers_label.setText(text)
        self.blockers.style().unpolish(self.blockers)
        self.blockers.style().polish(self.blockers)
        self.btn_finish.setEnabled(result.passed)
        self.changed.emit()


class InterviewPage(QWidget):
    """Step three: the factsheet and the fit brief, drafted from the CVs.

    The user CORRECTS a draft rather than composing from a blank page. That is
    the whole design: people under-report their own experience when asked to
    write it out, and over-claim when asked to justify it. A draft they can
    argue with produces a better factsheet than either.

    The two documents are kept apart on screen as well as in storage, because
    they answer different questions. The factsheet governs what may be SAID;
    the brief governs what gets SURFACED. Merging them is how an ambition
    quietly becomes a claim.
    """

    drafted = Signal()

    def __init__(self, *, drafter=None, parent=None):
        super().__init__(parent)
        #: (corpus, aim) -> (factsheet_md, brief_md, questions)
        self._draft = drafter
        self._questions: list[str] = []
        self._corpus = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        heading = QLabel(tr("onboarding.interview_heading"))
        heading.setObjectName("stepHeading")
        layout.addWidget(heading)

        body = QLabel(reflow(tr("onboarding.interview_body")))
        body.setObjectName("stepBody")
        body.setWordWrap(True)
        layout.addWidget(body)

        # The aim, in the user's own words, BEFORE anything is drafted.
        #
        # Without it the brief is inferred from CVs alone, and a CV says what
        # someone has done rather than what they want next. Drafted that way
        # against a real corpus, the brief guessed at the target, the seniority
        # direction, the location, permanent versus contract and the salary
        # floor — and closed with six questions it had no way to answer. Five
        # of the six are one sentence from the person sitting in front of it.
        aim_label = QLabel(tr("onboarding.aim_label"))
        f = QFont()
        f.setBold(True)
        aim_label.setFont(f)
        layout.addWidget(aim_label)

        self.aim = QPlainTextEdit()
        self.aim.setObjectName("aimBox")
        self.aim.setPlaceholderText(tr("onboarding.aim_placeholder"))
        self.aim.setMaximumHeight(96)
        layout.addWidget(self.aim)

        row = QHBoxLayout()
        self.btn_draft = QPushButton(tr("onboarding.draft"))
        self.btn_draft.setObjectName("primary")
        self.btn_draft.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        row.addWidget(self.btn_draft)
        self.status = QLabel()
        self.status.setObjectName("stepBody")
        row.addWidget(self.status, 1)
        layout.addLayout(row)

        self.questions = QLabel()
        self.questions.setObjectName("disagreement")
        self.questions.setWordWrap(True)
        self.questions.hide()
        layout.addWidget(self.questions)

        split = QHBoxLayout()
        split.setSpacing(12)
        for title, attr in ((tr("onboarding.factsheet_label"), "factsheet"),
                            (tr("onboarding.brief_label"), "brief")):
            column = QVBoxLayout()
            label = QLabel(title)
            f = QFont()
            f.setBold(True)
            label.setFont(f)
            column.addWidget(label)
            editor = QPlainTextEdit()
            editor.setObjectName("docEditor")
            column.addWidget(editor, 1)
            setattr(self, attr, editor)
            split.addLayout(column, 1)
        layout.addLayout(split, 1)

        self.setStyleSheet(ONBOARDING_STYLESHEET + """
QPlainTextEdit#docEditor {
    border: 1px solid #cfcabf;
    border-radius: 6px;
    padding: 8px;
    font-family: Consolas, monospace;
}
QPlainTextEdit#aimBox {
    border: 1px solid #cfcabf;
    border-radius: 6px;
    padding: 8px;
}
""")
        self.btn_draft.clicked.connect(lambda: self.run_draft())

    def run_draft(self, corpus=None) -> None:
        """Draft both documents. Failures are shown, never swallowed.

        The corpus is remembered from the first call so the Draft button can
        re-run with a corrected aim — a first attempt whose brief guessed wrong
        should be one sentence away from a better one, not a restart.
        """
        if corpus is not None:
            self._corpus = corpus
        if self._corpus is None:
            return

        self.btn_draft.setEnabled(False)
        self.status.setText(tr("onboarding.drafting"))
        self.status.repaint()
        try:
            factsheet, brief, questions = self._draft(
                self._corpus, self.aim.toPlainText().strip())
        except Exception as exc:  # noqa: BLE001
            # The user can still write their own; a failed draft must not be a
            # dead end, and it must not look like an empty one either.
            self.status.setText(tr("onboarding.draft_failed", reason=str(exc)[:200]))
            return
        finally:
            self.btn_draft.setEnabled(True)

        self.factsheet.setPlainText(factsheet)
        self.brief.setPlainText(brief)
        self._questions = questions
        if questions:
            self.questions.setText(
                tr("onboarding.open_questions") + "\n• " + "\n• ".join(questions))
            self.questions.show()
        self.status.setText(tr("onboarding.drafted"))
        self.drafted.emit()

    def set_corpus(self, corpus) -> None:
        """Hand over the documents without spending anything yet."""
        self._corpus = corpus

    def documents(self) -> tuple[str, str]:
        return self.factsheet.toPlainText(), self.brief.toPlainText()

    @property
    def has_content(self) -> bool:
        return bool(self.factsheet.toPlainText().strip()
                    and self.brief.toPlainText().strip())


class OnboardingWizard(QWidget):
    """Ingest, then the gate.

    The wizard exists so the gate is reachable, but it is NOT what enforces the
    gate — `morning_run` does. A gate enforced only by the screen that presents
    it is a gate the scheduled run walks straight past, so this window can be
    closed at any point and the app simply refuses to run until the gate is
    passed properly.
    """

    completed = Signal(object)          # CalibrationResult
    documents_ready = Signal(str, str)  # factsheet, brief

    def __init__(self, *, extract, sample, drafter=None, parent=None):
        """`extract(paths) -> (names, warnings)` and `sample() -> [items]` are
        injected, so the wizard neither reads disks nor calls a model itself."""
        super().__init__(parent)
        self._extract = extract
        self._sample = sample
        self._corpus = None

        self.setWindowTitle(tr("onboarding.title"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.stack = QStackedWidget()
        self.ingest = IngestPage()
        # The key step sits BETWEEN ingest and calibration, because calibration
        # fetches live postings and assesses them — it is the first thing that
        # actually spends the user's money, so they must have supplied the key
        # before they reach it.
        from app.ui.settings import KeyPanel
        self.keys = KeyPanel()
        # The interview runs AFTER the key, because it is a model call. It runs
        # BEFORE calibration, because calibration corrects verdicts made against
        # the brief this step produces — without it there is nothing to correct.
        self.interview = InterviewPage(drafter=drafter)
        self.calibration = CalibrationPage()
        self.stack.addWidget(self.ingest)
        self.stack.addWidget(self.keys)
        self.stack.addWidget(self.interview)
        self.stack.addWidget(self.calibration)
        layout.addWidget(self.stack, 1)

        nav = QHBoxLayout()
        nav.setContentsMargins(16, 0, 16, 16)
        self.btn_back = QPushButton(tr("onboarding.back"))
        self.btn_back.setObjectName("secondary")
        self.btn_next = QPushButton(tr("onboarding.next"))
        self.btn_next.setObjectName("primary")
        self.btn_back.setEnabled(False)
        self.btn_next.setEnabled(False)      # nothing ingested yet
        nav.addWidget(self.btn_back)
        nav.addStretch(1)
        nav.addWidget(self.btn_next)
        layout.addLayout(nav)

        self.setStyleSheet(ONBOARDING_STYLESHEET)

        self.ingest.files_added.connect(self._on_files)
        self.keys.key_changed.connect(self._on_key)
        self.btn_next.clicked.connect(self._next)
        self.btn_back.clicked.connect(self._back)
        self.calibration.finished.connect(self._finish)
        self._show_step(0)

    def _on_key(self, present: bool) -> None:
        if self.stack.currentIndex() == 1:
            self.btn_next.setEnabled(present)

    def _on_files(self, paths) -> None:
        names, warnings = self._extract(paths)
        self._corpus = paths
        self.ingest.show_corpus(names, warnings)
        # Warnings never block: an unreadable file is the user's to fix or
        # ignore, and refusing to continue over one would strand someone whose
        # only copy of an old CV is a scan.
        self.btn_next.setEnabled(bool(names))

    def _show_step(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.btn_back.setEnabled(index > 0)
        # The gate has its own Finish button, so the wizard's Next is hidden
        # there — two buttons that mean different things is how people click
        # the wrong one.
        self.btn_next.setVisible(index < 3)
        if index == 1:
            # Cannot leave the key step without a key: the next screen spends
            # money on the user's account, and an app with no key is inert.
            from app.core import api_key
            self.btn_next.setEnabled(bool(api_key.get()))

    def _next(self) -> None:
        index = self.stack.currentIndex()
        if index == 0:
            self._show_step(1)
        elif index == 1:
            self._show_step(2)
            # Hand over the corpus but do NOT draft: the aim box is above the
            # button for a reason, and drafting on arrival would spend the
            # user's money on a brief drafted before they said anything.
            if self._corpus is not None and not self.interview.has_content:
                self.interview.set_corpus(self._corpus)
        elif index == 2:
            factsheet, brief = self.interview.documents()
            self.documents_ready.emit(factsheet, brief)
            self.calibration.load(self._sample())
            self._show_step(3)

    def _back(self) -> None:
        self._show_step(max(0, self.stack.currentIndex() - 1))

    def _finish(self) -> None:
        self.completed.emit(self.calibration.result())
