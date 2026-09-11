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
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QFrame, QHBoxLayout,
                               QLabel,
                               QLineEdit, QListWidget, QPushButton,
                               QPlainTextEdit, QRadioButton, QScrollArea, QSizePolicy,
                               QSplitter, QStackedWidget, QTextBrowser,
                               QVBoxLayout, QWidget)

from app.i18n import tr
from app.ui.background import run_in_background
from app.onboarding.calibration import CalibrationItem, CalibrationResult
from app.onboarding.interview import ingest_guidance
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

QPushButton#quickMenu {{
    background: transparent;
    border: none;
    color: {INK};
    font-size: 20px;
    padding: 0 6px;
}}
QPushButton#quickMenu:hover {{ color: {TEAL}; }}
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
        body = QLabel(reflow(ingest_guidance()))
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
        #: Why the feed could not be reached, when that is why the sample is
        #: short. Set from the sample in `load`.
        self._no_feed: str | None = None

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
        # `no_feed` rides on the sample itself (see CalibrationSample), so a
        # caller that hands over a plain list — every test, and the render
        # tools — behaves exactly as before.
        self._no_feed = getattr(items, "no_feed", None)
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
        return CalibrationResult(items=[w.item for w in self._widgets],
                                 no_feed=self._no_feed)

    def _refresh(self) -> None:
        result = self.result()
        reasons = result.blocking_reasons()
        if result.sample_unavailable:
            # Say what happened and let them through. Calibration runs on a
            # later morning once a search is switched on; being unable to
            # finish setup at all is the worse failure by a wide margin.
            #
            # WHICH "what happened" matters: blaming a quiet market for an
            # unpaid subscription sends the user to check searches that were
            # never the problem.
            self.blockers.setObjectName("blockers")
            self.blockers_label.setText(
                tr("onboarding.calibration_no_feed") if result.no_feed
                else tr("onboarding.calibration_skipped"))
        elif reasons:
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
        # `can_finish`, not `passed`: an unreachable feed must not trap anyone
        # on the last screen, but it must not be recorded as a calibration
        # either. See CalibrationResult.can_finish.
        self.btn_finish.setEnabled(result.can_finish)
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

    def __init__(self, *, drafter=None, titler=None, parent=None):
        super().__init__(parent)
        #: `titler(text, brief) -> plan`, where `plan.titles` is the list of
        #: search titles. Injected like the drafter so this widget
        #: neither holds a key nor knows what a model is.
        self._titler = titler
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

        # THE OPEN QUESTIONS, EACH WITH SOMEWHERE TO PUT THE ANSWER.
        #
        # These were one wall of bullets in a single QLabel, and `_questions`
        # was stored and then never read by anything. The app asked eight
        # things about the person sitting in front of it and discarded whatever
        # they would have said — which is worse than not asking, because it
        # reads as a form. A question with nowhere to put the answer is not a
        # question, it is a disclaimer.
        #
        # Answers are appended to the FACTSHEET, deliberately: the factsheet is
        # the only thing outreach may claim, so a correction that lands
        # anywhere else does not govern anything.
        self.questions = QLabel()
        self.questions.setObjectName("disagreement")
        self.questions.setWordWrap(True)
        self.questions.hide()
        layout.addWidget(self.questions)

        self._answer_rows: list[tuple[str, QLineEdit]] = []
        self._questions_form = QVBoxLayout()
        self._questions_form.setContentsMargins(0, 0, 0, 0)
        self._questions_form.setSpacing(4)
        holder = QWidget()
        holder.setLayout(self._questions_form)
        # Capped and scrollable: eight questions each with a box is taller than
        # the documents they are about, and pushing those off-screen would
        # trade one unreadable layout for another.
        self.questions_area = QScrollArea()
        self.questions_area.setObjectName("questionsArea")
        self.questions_area.setWidget(holder)
        self.questions_area.setWidgetResizable(True)
        self.questions_area.setMaximumHeight(210)
        self.questions_area.hide()
        layout.addWidget(self.questions_area)

        # A box you type into and nothing happens is a box you assume was
        # ignored. The answers were in fact read — on Next, invisibly — so the
        # only thing missing was telling the user, which is the whole job of
        # this button: it folds the answers into the factsheet ABOVE, where
        # they can see their own words land in the record that governs what
        # may later be said about them.
        self.btn_answers = QPushButton(tr("onboarding.submit_answers"))
        self.btn_answers.setObjectName("secondary")
        self.btn_answers.hide()
        self.btn_answers.clicked.connect(self._submit_answers)
        layout.addWidget(self.btn_answers)

        #: Questions already folded in, so neither the button nor `documents()`
        #: can add the same answer twice.
        self._merged: set[str] = set()
        #: Job titles for the searches step, computed off the UI thread.
        self.suggested_titles: list[str] = []
        #: The whole plan — titles, where, contract types, exclusions — or
        #: None until the background call has returned.
        self.suggested_plan = None
        self._titles_task = None

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

        # OFF THE UI THREAD. This reads a corpus of CVs and then calls
        # Anthropic, which is tens of seconds. It used to run here, inline,
        # with a `self.status.repaint()` on the line above to force the
        # "reading your CVs" text out before everything stopped — so the
        # window went "(Not Responding)" for the whole draft, every time.
        #
        # Getting the please-wait text painted before you stop answering the OS
        # is not the same as not stopping. Store Policy 10.4.2 requires the
        # product to "continue to run and remain responsive to user input".
        aim = self.aim.toPlainText().strip()
        corpus = self._corpus
        self._draft_task = run_in_background(
            lambda: self._draft(corpus, aim),
            on_done=self._draft_done,
            on_error=self._draft_error,
        )

    def _draft_done(self, result) -> None:
        """Back on the UI thread, with (factsheet, brief, questions)."""
        self.btn_draft.setEnabled(True)
        factsheet, brief, questions = result
        self.factsheet.setPlainText(factsheet)
        self.brief.setPlainText(brief)
        self._questions = questions
        self._show_questions(questions)
        # Start on the titles now, while the user reads the draft. By the time
        # they press Next the searches step has something to show, and nothing
        # had to block to get it.
        self.request_titles()
        self.status.setText(tr("onboarding.drafted"))
        self.drafted.emit()

    def _draft_error(self, exc) -> None:
        """The user can still write their own; a failed draft must not be a
        dead end, and it must not look like an empty one either."""
        self.btn_draft.setEnabled(True)
        self.status.setText(tr("onboarding.draft_failed", reason=str(exc)[:200]))

    def _show_questions(self, questions: list[str]) -> None:
        """One row per question: the question, and a box to answer it in."""
        while self._questions_form.count():
            item = self._questions_form.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._answer_rows = []

        self._merged = set()
        if not questions:
            self.questions.hide()
            self.questions_area.hide()
            self.btn_answers.hide()
            return

        self.questions.setText(tr("onboarding.open_questions"))
        self.questions.show()
        for question in questions:
            label = QLabel("• " + question)
            label.setObjectName("disagreement")
            label.setWordWrap(True)
            self._questions_form.addWidget(label)
            box = QLineEdit()
            box.setPlaceholderText(tr("onboarding.answer_placeholder"))
            self._questions_form.addWidget(box)
            self._answer_rows.append((question, box))
        self.questions_area.show()
        self.btn_answers.show()
        self.btn_answers.setEnabled(True)

    def _submit_answers(self) -> None:
        """Fold the answers into the factsheet, and say what happened.

        Two things the user could not previously tell apart: an answer that
        was taken, and an answer that was ignored. Both looked identical —
        a box with text in it and no acknowledgement anywhere.

        The merge is instant and local. The searches are re-derived in the
        BACKGROUND from the aim plus these answers, because that is a model
        call and a model call on the UI thread is the freeze this module's
        `run_in_background` exists to prevent (Store Policy 10.4.2).
        """
        pending = [(q, a) for q, a in self.question_answers()
                   if q not in self._merged]
        if not pending:
            self.status.setText(tr("onboarding.answers_none"))
            return

        self.factsheet.setPlainText(self._with_answers(
            self.factsheet.toPlainText(), pending))
        self._merged.update(q for q, _a in pending)
        for question, box in self._answer_rows:
            if question in self._merged:
                box.setReadOnly(True)
        self.status.setText(tr("onboarding.answers_added",
                               count=len(pending)))
        self.btn_answers.setEnabled(False)
        self.request_titles()

    def request_titles(self) -> None:
        """Work out the search titles off the UI thread.

        Started when the draft lands and again when answers are submitted, so
        that by the time the user reaches the searches step the list is
        already there and no click has to wait for a network round trip.
        """
        if self._titler is None:
            return
        aim = self.aim.toPlainText()
        answers = "\n".join(f"{q} {a}" for q, a in self.question_answers())
        text = f"{aim}\n{answers}".strip()
        # The brief as well, because corrections such as "roles must be BASED
        # in London" are made in the brief, never in the aim box.
        brief = self.brief.toPlainText()
        if not text and not brief.strip():
            return
        titler = self._titler
        self._titles_task = run_in_background(
            lambda: titler(text, brief),
            on_done=self._titles_ready,
            on_error=lambda _exc: None)   # a failed side errand stays silent

    def _titles_ready(self, plan) -> None:
        self.suggested_plan = plan
        self.suggested_titles = list(getattr(plan, "titles", plan) or [])

    @staticmethod
    def _with_answers(factsheet: str, answered) -> str:
        """The factsheet is the only thing outreach may claim from, so a
        correction that landed anywhere else would govern nothing."""
        lines = [tr("onboarding.clarifications_heading")]
        lines += [f"- {question} — {answer}" for question, answer in answered]
        return factsheet.rstrip() + "\n\n" + "\n".join(lines) + "\n"

    def question_answers(self) -> list[tuple[str, str]]:
        """Only the questions actually answered. A blank box is not an answer,
        and must never reach the factsheet as one."""
        return [(q, box.text().strip())
                for q, box in self._answer_rows if box.text().strip()]

    def set_corpus(self, corpus) -> None:
        """Hand over the documents without spending anything yet."""
        self._corpus = corpus

    def documents(self) -> tuple[str, str]:
        """The factsheet carries any answers, because the factsheet is the only
        thing outreach may claim — a correction that landed anywhere else would
        govern nothing. Unanswered questions are simply dropped: an unanswered
        question is not a fact, and must not read as one later."""
        factsheet = self.factsheet.toPlainText()
        # Only what the button has NOT already folded in. Pressing it and then
        # pressing Next must not record the same clarification twice.
        answered = [(q, a) for q, a in self.question_answers()
                    if q not in self._merged]
        if answered:
            factsheet = self._with_answers(factsheet, answered)
        return factsheet, self.brief.toPlainText()

    @property
    def has_content(self) -> bool:
        return bool(self.factsheet.toPlainText().strip()
                    and self.brief.toPlainText().strip())


class SearchesPage(QWidget):
    """Switch on the searches that are actually searches.

    THE STEP WHOSE ABSENCE WAS THE BUG. Seeds are generated from the stated aim
    and arrive switched OFF on purpose — billing is per posting returned, so a
    seed nobody meant is money spent for nothing. But nothing ever offered to
    switch one ON, so a fresh install arrived at calibration with no enabled
    search, fetched nothing, and could not finish. The design assumed this
    screen existed. It did not, on any platform, in any shipped build.

    IT DOES NOT BLOCK. Requiring a tick here would replace one trap with
    another — a user whose seeds are all junk would be stuck again. Calibration
    no longer blocks either, so the worst case is a quiet first morning and a
    search switched on later from Settings.
    """

    changed = Signal()

    def __init__(self, *, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 8)
        layout.setSpacing(10)

        heading = QLabel(tr("onboarding.searches_heading"))
        heading.setObjectName("stepHeading")
        layout.addWidget(heading)

        body = QLabel(reflow(tr("onboarding.searches_body")))
        body.setObjectName("stepBody")
        body.setWordWrap(True)
        layout.addWidget(body)

        self._rows: list[tuple[str, QCheckBox]] = []
        self._where: str | None = None
        self._form = QVBoxLayout()
        self._form.setContentsMargins(0, 0, 0, 0)
        self._form.setSpacing(4)
        holder = QWidget()
        holder.setLayout(self._form)
        area = QScrollArea()
        area.setWidget(holder)
        area.setWidgetResizable(True)
        layout.addWidget(area, 1)

        self.note = QLabel()
        self.note.setObjectName("stepBody")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

    def load(self, rows, where: str | None = None) -> None:
        """`rows` is (label, titles, enabled), as `all_queries` returns.

        `where` is the place every search looks. Blank means setup found none,
        and then nothing here can be switched on: a search with no location
        looks across the whole world and pays for every posting it returns.
        None means the caller did not say, which only tests do.
        """
        while self._form.count():
            item = self._form.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._rows = []
        self._where = where
        blocked = where is not None and not where.strip()
        for label, _titles, enabled in rows:
            box = QCheckBox(label)
            box.setChecked(bool(enabled) and not blocked)
            box.setEnabled(not blocked)
            box.stateChanged.connect(lambda *_: self._refresh())
            self._form.addWidget(box)
            self._rows.append((label, box))
        # Without this the layout shares the scroll area's spare height out
        # between the rows, and five searches spread down the whole window.
        self._form.addStretch(1)
        self._refresh()

    def selections(self) -> list[tuple[str, bool]]:
        return [(label, box.isChecked()) for label, box in self._rows]

    @property
    def any_enabled(self) -> bool:
        return any(on for _label, on in self.selections())

    def _refresh(self) -> None:
        if not self._rows:
            self.note.setText(tr("onboarding.searches_none"))
        elif self._where is not None and not self._where.strip():
            self.note.setText(tr("onboarding.searches_nowhere"))
        elif not self.any_enabled:
            self.note.setText(tr("onboarding.searches_off"))
        elif self._where:
            self.note.setText(tr("onboarding.searches_where", where=self._where))
        else:
            self.note.setText("")
        self.changed.emit()


#: The steps, by name. They were bare integers compared in four places
#: (`index < 4`, `index == 3`, `index == 1`), which is fine until a step is
#: inserted — and a step was: nothing in this wizard ever asked the user to
#: subscribe, so a new install finished setup, reached a feed it had not paid
#: for, and was told there was nothing to calibrate against.
STEP_INGEST = 0
STEP_KEY = 1
STEP_ENTITLEMENT = 2
STEP_INTERVIEW = 3
STEP_SEARCHES = 4
STEP_CALIBRATION = 5
LAST_STEP = STEP_CALIBRATION


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

    def __init__(self, *, extract, sample, drafter=None, titler=None,
                 searches=None, set_search=None, entitlement_panel=None,
                 where=None, parent=None):
        """`extract(paths) -> (names, warnings)` and `sample() -> [items]` are
        injected, so the wizard neither reads disks nor calls a model itself.

        `searches() -> [(label, titles, enabled)]` and
        `set_search(label, enabled)` are the same idea for the saved searches.
        Both default to None so existing callers and tests keep working; the
        step simply shows nothing to switch on.
        """
        super().__init__(parent)
        self._extract = extract
        self._sample = sample
        self._searches = searches
        self._set_search = set_search
        #: `where() -> str`, the place the seeded searches look ("" if none).
        self._where = where
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
        # WHERE THE USER ACTUALLY SUBSCRIBES, which until now was nowhere.
        #
        # Both panels existed and both lived in Settings, which onboarding
        # never opens and never mentions. So the answer to "when does a user
        # subscribe?" was: they do not. They finish setting up, the feed
        # refuses because nothing has been bought, and calibration reports
        # that there were not enough live postings — which reads as an empty
        # market rather than an unpaid subscription.
        #
        # It sits directly after the Anthropic key because the two questions
        # are the same question — what this app needs before it can work — and
        # before the interview, which is the first screen that spends money.
        self.entitlement = entitlement_panel or QWidget()
        self.interview = InterviewPage(drafter=drafter, titler=titler)
        self.calibration = CalibrationPage()
        self.searches = SearchesPage()
        self.stack.addWidget(self.ingest)
        self.stack.addWidget(self.keys)
        self.stack.addWidget(self.entitlement)
        self.stack.addWidget(self.interview)
        # BETWEEN the interview and calibration, because the seeds are written
        # from the aim when the interview is left, and calibration needs at
        # least one of them switched on to fetch anything at all.
        self.stack.addWidget(self.searches)
        self.stack.addWidget(self.calibration)
        layout.addWidget(self.stack, 1)

        nav = QHBoxLayout()
        nav.setContentsMargins(16, 0, 16, 16)
        # ON THE SETUP WIZARD TOO, and that is the point of it. Someone who
        # cannot read English cannot find a language setting that only exists
        # after setup, and someone who has just been told there is no feed
        # needs the subscription now, not after finishing a flow they cannot
        # use the result of.
        from app.ui.quickmenu import QuickMenu
        self.menu = QuickMenu()
        nav.addWidget(self.menu)
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
        # Subscribe from the menu goes to the step that already does it,
        # rather than opening a second copy of the same panel somewhere else.
        self.menu.subscribe_requested.connect(
            lambda: self._show_step(STEP_ENTITLEMENT))
        self.menu.restore_requested.connect(self._restore)
        # The subscribe panel reports its own outcome; the wizard only needs to
        # stop offering the step once it has succeeded.
        if hasattr(self.entitlement, "entitlement_changed"):
            self.entitlement.entitlement_changed.connect(
                lambda _ok: self.btn_next.setEnabled(True))
        self.btn_next.clicked.connect(self._next)
        self.btn_back.clicked.connect(self._back)
        self.calibration.finished.connect(self._finish)
        self._show_step(0)

    def _on_key(self, present: bool) -> None:
        if self.stack.currentIndex() == STEP_KEY:
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
        self.btn_back.setEnabled(index > STEP_INGEST)
        # The gate has its own Finish button, so the wizard's Next is hidden
        # there — two buttons that mean different things is how people click
        # the wrong one.
        self.btn_next.setVisible(index < LAST_STEP)
        if index in (STEP_ENTITLEMENT, STEP_SEARCHES):
            # NEITHER OF THESE BLOCKS, and for the same reason.
            #
            # Requiring the subscription here would trap someone who wants to
            # look before they pay, and the app is deliberately useful without
            # it — the board, the brief and everything already on the machine
            # stay open; it is the live feed that is bought. Requiring a ticked
            # search would trap someone whose suggestions are all wrong.
            self.btn_next.setEnabled(True)
        if index == STEP_KEY:
            # Cannot leave the key step without a key: the next screen spends
            # money on the user's account, and an app with no key is inert.
            from app.core import api_key
            self.btn_next.setEnabled(bool(api_key.get()))

    def _next(self) -> None:
        index = self.stack.currentIndex()
        if index == STEP_INGEST:
            self._show_step(STEP_KEY)
        elif index == STEP_KEY:
            self._show_step(STEP_ENTITLEMENT)
        elif index == STEP_ENTITLEMENT:
            self._show_step(STEP_INTERVIEW)
            # Hand over the corpus but do NOT draft: the aim box is above the
            # button for a reason, and drafting on arrival would spend the
            # user's money on a brief drafted before they said anything.
            if self._corpus is not None and not self.interview.has_content:
                self.interview.set_corpus(self._corpus)
        elif index == STEP_INTERVIEW:
            factsheet, brief = self.interview.documents()
            # Emitting this is what writes the seed searches, so the next
            # screen has something to show. Order matters here.
            self.documents_ready.emit(factsheet, brief)
            if self._searches is not None:
                self.searches.load(
                    self._searches(),
                    where=self._where() if self._where is not None else None)
            self._show_step(STEP_SEARCHES)
        elif index == STEP_SEARCHES:
            if self._set_search is not None:
                for label, enabled in self.searches.selections():
                    try:
                        self._set_search(label, enabled)
                    except ValueError:
                        # Refused because the search has no location. Leaving
                        # it off is the right outcome, and Settings says how
                        # to fix it; stopping setup here would strand the user.
                        pass
            # Only now is there anything to fetch with.
            self.calibration.load(self._sample())
            self._show_step(STEP_CALIBRATION)

    def _restore(self) -> None:
        """Apple requires Restore to be reachable. The panel owns the actual
        StoreKit call; this only makes sure the user can get to it.

        It looked up `restore`, which on the subscribe panel is the Restore
        BUTTON. A QPushButton is not callable, so the menu item only ever
        turned the page, and nothing was restored.
        """
        self._show_step(STEP_ENTITLEMENT)
        restore = getattr(self.entitlement, "restore_purchases", None)
        if callable(restore):
            restore()

    def _back(self) -> None:
        self._show_step(max(0, self.stack.currentIndex() - 1))

    def _finish(self) -> None:
        self.completed.emit(self.calibration.result())
