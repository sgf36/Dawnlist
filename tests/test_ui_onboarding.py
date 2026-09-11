"""The onboarding screens, and above all the gate."""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.onboarding.calibration import CalibrationItem  # noqa: E402
from app.ui.onboarding import CalibrationPage, IngestPage  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture()
def page(qapp):
    p = CalibrationPage()
    yield p
    p.close()


def items(n=10, app_verdict="rejected"):
    return [CalibrationItem(job_key=str(i), title=f"Role {i}", company="Acme",
                            description="d", app_verdict=app_verdict,
                            app_reason="outside the brief")
            for i in range(n)]


def choose(page, index, verdict):
    widgets = page._widgets[index]
    for btn in widgets.group.buttons():
        if btn.property("verdict") == verdict:
            btn.setChecked(True)
            return
    raise AssertionError(f"no radio for {verdict}")


def type_sentence(page, index, text):
    page._widgets[index].sentence.setText(text)


# -- ingest -----------------------------------------------------------------
def test_the_ingest_copy_is_shown_verbatim(qapp):
    p = IngestPage()
    body = [c.text() for c in p.findChildren(type(p.warnings_label))]
    joined = " ".join(body)
    assert "Do not tidy them up first" in joined
    assert "Early roles" in joined
    p.close()


def test_unreadable_files_are_named_on_screen(qapp):
    p = IngestPage()
    p.show_corpus(["cv.docx"], ["scan.pdf: this looks like a scanned PDF"])
    assert p.warnings.isVisibleTo(p)
    assert "scan.pdf" in p.warnings_label.text()
    p.close()


def test_no_warnings_hides_the_panel(qapp):
    p = IngestPage()
    p.show_corpus(["a.docx", "b.docx"], [])
    assert p.warnings.isHidden()
    p.close()


# -- the gate ---------------------------------------------------------------
def test_finish_is_disabled_on_an_untouched_gate(page):
    page.load(items())
    assert not page.btn_finish.isEnabled()


def test_deciding_enough_enables_finish(page):
    page.load(items())
    for i in range(8):
        choose(page, i, "rejected")
    assert page.btn_finish.isEnabled()


def test_a_disagreement_without_a_sentence_disables_finish(page):
    """The whole point of the gate: a correction that does not reach the brief
    will not persist."""
    page.load(items())
    for i in range(8):
        choose(page, i, "rejected")
    assert page.btn_finish.isEnabled()

    choose(page, 0, "strong")          # now disagreeing, no sentence yet
    assert not page.btn_finish.isEnabled()
    assert "would have got this right" in page.blockers_label.text()

    type_sentence(page, 0, "Operational real estate roles are in scope.")
    assert page.btn_finish.isEnabled()


def test_the_sentence_box_only_appears_on_a_disagreement(page):
    page.load(items())
    assert not page._widgets[0].sentence.isVisibleTo(page)
    choose(page, 0, "strong")
    assert page._widgets[0].sentence.isVisibleTo(page)
    choose(page, 0, "rejected")
    assert not page._widgets[0].sentence.isVisibleTo(page)


def test_every_blocking_reason_is_listed_at_once(page):
    """Revealing them one at a time makes a five-minute step feel endless."""
    page.load(items())
    choose(page, 0, "strong")
    choose(page, 1, "strong")
    text = page.blockers_label.text()
    assert text.count("•") >= 3    # too few decided, plus both sentences


def test_agreeing_with_everything_passes_but_is_called_out(page):
    page.load(items())
    for i in range(10):
        choose(page, i, "rejected")
    assert page.btn_finish.isEnabled()
    assert "usually means this sample was easy" in page.blockers_label.text()


def test_the_result_carries_the_users_corrections(page):
    page.load(items())
    for i in range(8):
        choose(page, i, "rejected")
    choose(page, 0, "strong")
    type_sentence(page, 0, "Operational real estate is in scope.")

    result = page.result()
    assert result.passed
    assert len(result.disagreements) == 1
    assert result.disagreements[0].brief_sentence == (
        "Operational real estate is in scope.")


def test_finishing_emits_only_once_it_can(page, qapp):
    page.load(items())
    seen = []
    page.finished.connect(lambda: seen.append(True))
    page.btn_finish.click()            # disabled, so nothing happens
    assert seen == []

    for i in range(8):
        choose(page, i, "rejected")
    page.btn_finish.click()
    assert seen == [True]


def test_reloading_clears_the_previous_sample(page):
    page.load(items())
    for i in range(8):
        choose(page, i, "rejected")
    assert page.btn_finish.isEnabled()
    page.load(items())
    assert len(page._widgets) == 10
    assert not page.btn_finish.isEnabled()


def test_reflow_undoes_source_line_breaks_but_keeps_paragraphs():
    """The source strings are hard-wrapped at ~78 chars for readability, and
    QLabel honours those newlines — so the text renders at the source's width
    and ignores the pane. That looks like a word-wrap bug and is not one."""
    from app.ui.onboarding import reflow
    out = reflow("one line\nsame paragraph\n\nsecond paragraph\nstill second")
    assert out == "one line same paragraph\n\nsecond paragraph still second"


def test_reflow_preserves_the_wording(qapp):
    from app.onboarding.interview import INGEST_GUIDANCE
    from app.ui.onboarding import reflow
    out = reflow(INGEST_GUIDANCE)
    assert "Do not tidy them up first" in out
    assert "Early roles are often cut from a senior CV" in out
    assert "\n" not in out.split("\n\n")[0], "no hard breaks inside a paragraph"


def test_the_disagreement_is_named_so_the_sentence_box_makes_sense(page):
    """Without this the box reads as 'justify your answer'. It is not that:
    the app and the user disagree, and the sentence is how the brief learns
    the difference."""
    page.load(items())                      # every card: app said "rejected"
    assert not page._widgets[0].disagreement.isVisibleTo(page)

    choose(page, 0, "strong")
    w = page._widgets[0]
    assert w.disagreement.isVisibleTo(page)
    assert "You disagree" in w.disagreement.text()
    assert "not a fit" in w.disagreement.text()


def test_the_disagreement_uses_the_label_not_the_stored_value(page):
    """'you say strong' leaks the storage vocabulary; they clicked a button
    that said 'Strong fit'."""
    page.load(items())
    choose(page, 0, "strong")
    text = page._widgets[0].disagreement.text()
    assert "strong fit" in text.lower()


def test_the_verdict_row_is_labelled_as_the_users_answer(page, qapp):
    """Unlabelled radios read as a restatement of the app's verdict."""
    from PySide6.QtWidgets import QLabel
    page.load(items())
    labels = [l.text() for l in page.findChildren(QLabel)
              if l.objectName() == "yourVerdict"]
    assert labels and labels[0] == "You say:"


def test_agreeing_shows_no_disagreement_line(page):
    page.load(items())
    choose(page, 0, "rejected")
    assert not page._widgets[0].disagreement.isVisibleTo(page)


def test_the_app_verdict_has_its_own_vocabulary(page):
    """The user's buttons are in their voice ("Not for me"). Putting that in
    the app's mouth - "Dawnlist said not for me" - reads as nonsense."""
    from app.ui.onboarding import app_verdict_label, verdict_label
    assert verdict_label("rejected") == "Not for me"
    assert app_verdict_label("rejected") == "not a fit"
    assert app_verdict_label("strong") == "a strong fit"


# -- the key step -----------------------------------------------------------
def test_onboarding_has_a_key_step_before_calibration(qapp, monkeypatch):
    """Calibration fetches live postings and assesses them — the first thing
    that spends the user's money. They must have supplied a key first."""
    from app.core import api_key
    from app.ui.onboarding import STEP_KEY, OnboardingWizard
    from app.ui.settings import KeyPanel

    monkeypatch.setattr(api_key, "get", lambda: None)
    w = OnboardingWizard(extract=lambda p: ([], []), sample=lambda: items(1))
    assert w.stack.count() == 6, (
        "ingest, key, ENTITLEMENT, interview, searches, calibration — the "
        "entitlement step is where the user subscribes, and its absence is "
        "why a new install reached calibration having paid for nothing")
    assert isinstance(w.stack.widget(STEP_KEY), KeyPanel)
    w.close()


def test_the_interview_runs_after_the_key_and_before_calibration(qapp, monkeypatch):
    """It is a model call, so it needs the key. Calibration corrects verdicts
    made against the brief this step produces, so without it there is nothing
    to correct."""
    from app.core import api_key
    from app.ui.onboarding import (STEP_CALIBRATION, STEP_INTERVIEW, STEP_KEY,
                                   InterviewPage, OnboardingWizard)

    monkeypatch.setattr(api_key, "get", lambda: "sk-ant-stored")
    w = OnboardingWizard(extract=lambda p: (["cv.docx"], []), sample=lambda: items(1))
    assert isinstance(w.stack.widget(STEP_INTERVIEW), InterviewPage)
    assert STEP_KEY < STEP_INTERVIEW < STEP_CALIBRATION
    w.close()


def test_the_interview_drafts_both_documents(qapp, monkeypatch, settle):
    from app.core import api_key
    from app.ui.onboarding import InterviewPage

    monkeypatch.setattr(api_key, "get", lambda: "sk-ant-stored")
    page = InterviewPage(drafter=lambda corpus, aim: (
        "# Background factsheet\n\nAcme Hotels.",
        "# Fit brief\n\nHospitality strategy.",
        ["Did the 2019 role include line management?"]))
    page.run_draft(["cv.docx"])
    settle(lambda: page.btn_draft.isEnabled(), what="the draft")

    assert "Acme Hotels" in page.factsheet.toPlainText()
    assert "Hospitality strategy" in page.brief.toPlainText()
    assert page.has_content
    # The question is now a row with a box under it, not a line in a wall of
    # bullets that nothing ever read back.
    asked = [q for q, _ in page._answer_rows]
    assert any("line management" in q for q in asked)

    # AN UNANSWERED QUESTION MUST NOT REACH THE FACTSHEET. It is not a fact,
    # and the factsheet is the only thing outreach is allowed to claim.
    assert "Clarifications" not in page.documents()[0]

    # An answered one must, because a correction that governs nothing is not a
    # correction — which is exactly what the display-only version was.
    page._answer_rows[0][1].setText("Yes, four direct reports.")
    factsheet = page.documents()[0]
    assert "Clarifications" in factsheet
    assert "four direct reports" in factsheet
    page.close()


def test_a_failed_draft_is_not_a_dead_end(qapp, settle):
    """It must not look like an empty draft either."""
    from app.ui.onboarding import InterviewPage

    def boom(corpus, aim):
        raise RuntimeError("Anthropic rejected that key")

    page = InterviewPage(drafter=boom)
    page.run_draft(["cv.docx"])
    settle(lambda: page.btn_draft.isEnabled(), what="the draft")
    assert "Could not draft" in page.status.text()
    assert "rejected that key" in page.status.text()
    assert not page.has_content
    page.close()


def test_the_two_documents_are_kept_apart(qapp, settle):
    """The factsheet governs what may be SAID; the brief what gets SURFACED.
    Merging them is how an ambition quietly becomes a claim."""
    from app.ui.onboarding import InterviewPage
    page = InterviewPage(drafter=lambda c, aim: ("FACTS", "BRIEF", []))
    page.run_draft(["cv"])
    settle(lambda: page.btn_draft.isEnabled(), what="the draft")
    factsheet, brief = page.documents()
    assert factsheet == "FACTS" and brief == "BRIEF"
    page.close()


def test_next_is_disabled_on_the_key_step_without_a_key(qapp, monkeypatch):
    from app.core import api_key
    from app.ui.onboarding import OnboardingWizard

    monkeypatch.setattr(api_key, "get", lambda: None)
    w = OnboardingWizard(extract=lambda p: (["cv.docx"], []), sample=lambda: items(1))
    w._show_step(1)
    assert not w.btn_next.isEnabled(), "an app with no key is inert"
    w.close()


def test_next_is_enabled_once_a_key_is_present(qapp, monkeypatch):
    from app.core import api_key
    from app.ui.onboarding import OnboardingWizard

    monkeypatch.setattr(api_key, "get", lambda: "sk-ant-stored")
    w = OnboardingWizard(extract=lambda p: (["cv.docx"], []), sample=lambda: items(1))
    w._show_step(1)
    assert w.btn_next.isEnabled()
    w.close()


# -- the aim ----------------------------------------------------------------
def test_the_user_is_asked_what_they_are_looking_for(qapp, settle):
    """`stated_aim` was a parameter nothing ever filled. Drafted from CVs
    alone against a real corpus, the brief guessed at the target, the seniority
    direction, the location, permanent versus contract and the salary floor,
    then closed with six questions — five of which are one sentence from the
    person sitting in front of it."""
    from app.ui.onboarding import InterviewPage
    seen = {}

    def drafter(corpus, aim):
        seen["aim"] = aim
        return "FACTS", "BRIEF", []

    page = InterviewPage(drafter=drafter)
    page.set_corpus(["cv.docx"])
    page.aim.setPlainText("Hotel asset management in London, senior manager "
                          "band, permanent, not below £85k.")
    page.run_draft()
    settle(lambda: page.btn_draft.isEnabled(), what="the draft")
    assert "£85k" in seen["aim"]
    page.close()


def test_the_aim_box_sits_above_the_draft_button(qapp):
    """Drafting on arrival would spend the user's money on a brief written
    before they said anything, so the box has to come first on screen."""
    from app.ui.onboarding import InterviewPage
    page = InterviewPage(drafter=lambda c, a: ("F", "B", []))
    page.resize(1200, 700)
    page.show()                     # geometry is 0,0 until a layout pass runs
    for _ in range(4):
        qapp.processEvents()
    assert page.aim.y() < page.btn_draft.y()
    page.close()


def test_entering_the_step_does_not_draft(qapp, monkeypatch):
    from app.core import api_key
    from app.ui.onboarding import OnboardingWizard

    monkeypatch.setattr(api_key, "get", lambda: "sk-ant-stored")
    from app.ui.onboarding import STEP_ENTITLEMENT

    calls = []
    w = OnboardingWizard(extract=lambda p: (["cv.docx"], []),
                         sample=lambda: items(1),
                         drafter=lambda c, a: calls.append(1) or ("F", "B", []))
    w._corpus = ["cv.docx"]
    w._show_step(STEP_ENTITLEMENT)
    w._next()                       # arrives at the interview
    assert calls == [], "drafted before the user said anything"
    w.close()


def test_the_draft_can_be_re_run_with_a_corrected_aim(qapp, settle):
    """A first attempt whose brief guessed wrong should be one sentence away
    from a better one, not a restart."""
    from app.ui.onboarding import InterviewPage
    aims = []
    page = InterviewPage(
        drafter=lambda c, a: (aims.append(a) or ("F", f"BRIEF for {a}", [])))
    page.set_corpus(["cv.docx"])
    page.run_draft()
    settle(lambda: page.btn_draft.isEnabled(), what="the draft")
    page.aim.setPlainText("Actually, London only.")
    page.btn_draft.click()
    # The SECOND draft is asynchronous too, and waiting on the button here
    # would be useless: it is re-enabled from the first run at the moment the
    # click lands, so the predicate is already true. Wait for the new brief.
    settle(lambda: "London only" in page.brief.toPlainText(),
           what="the re-run draft")
    assert aims == ["", "Actually, London only."]
    assert "London only" in page.brief.toPlainText()
    page.close()


def test_an_empty_aim_still_drafts(qapp, settle):
    """Someone who does not want to type should still get a draft to correct."""
    from app.ui.onboarding import InterviewPage
    page = InterviewPage(drafter=lambda c, a: ("F", "B", []))
    page.set_corpus(["cv.docx"])
    page.run_draft()
    settle(lambda: page.btn_draft.isEnabled(), what="the draft")
    assert page.has_content
    page.close()


def test_the_searches_step_switches_a_seed_on(qapp):
    """The step whose absence was the bug. Seeds arrive off; this is the only
    place in onboarding that can turn one on."""
    from app.ui.onboarding import SearchesPage

    page = SearchesPage()
    page.load([("hotel asset management", ["hotel asset management"], False),
               ("three kinds investment", ["three kinds investment"], False)])
    assert not page.any_enabled
    assert "quiet" in page.note.text(), "it must say what nothing-on means"

    page._rows[0][1].setChecked(True)
    assert page.any_enabled
    assert page.selections() == [("hotel asset management", True),
                                 ("three kinds investment", False)]
    page.close()


def test_no_location_means_no_search_can_be_switched_on(qapp):
    """A search with no location looks across the whole world, and every
    posting it returns is paid for."""
    from app.ui.onboarding import SearchesPage

    page = SearchesPage()
    page.load([("hotel asset manager", ["hotel asset manager"], False)], where="")
    assert not page._rows[0][1].isEnabled()
    assert "whole world" in page.note.text()

    page.load([("hotel asset manager", ["hotel asset manager"], False)],
              where="London, GB")
    assert page._rows[0][1].isEnabled(), "positive control"
    page._rows[0][1].setChecked(True)
    assert "London, GB" in page.note.text()
    page.close()


def test_searches_sit_at_the_top_rather_than_spreading_down_the_window(qapp):
    """Five searches were spread down the whole height of the setup window."""
    from app.ui.onboarding import SearchesPage

    page = SearchesPage()
    page.load([("a", ["a"], False), ("b", ["b"], False)], where="London, GB")
    page.resize(900, 780)
    page.show()
    qapp.processEvents()
    first, second = page._rows[0][1], page._rows[1][1]
    assert second.y() - first.y() < 3 * first.height(), (
        f"rows {second.y() - first.y()}px apart")
    page.close()


def test_leaving_the_searches_step_saves_what_was_switched_on(qapp, monkeypatch):
    """A tick that is not written back is the same bug wearing a hat."""
    from app.core import api_key
    from app.ui.onboarding import (STEP_CALIBRATION, STEP_SEARCHES,
                                   OnboardingWizard)

    monkeypatch.setattr(api_key, "get", lambda: "sk-ant-stored")
    saved = []
    w = OnboardingWizard(
        extract=lambda p: (["cv.docx"], []),
        sample=lambda: items(0),
        searches=lambda: [("hotels", ["hotels"], False),
                          ("junk phrase", ["junk phrase"], False)],
        set_search=lambda label, enabled: saved.append((label, enabled)))

    w.searches.load(w._searches())
    w.searches._rows[0][1].setChecked(True)
    w.stack.setCurrentIndex(STEP_SEARCHES)
    w._next()

    assert ("hotels", True) in saved
    assert ("junk phrase", False) in saved
    assert w.stack.currentIndex() == STEP_CALIBRATION, "and on to calibration"
    w.close()


def test_an_unreachable_feed_does_not_trap_the_user_on_the_last_screen(qapp):
    """Finish must enable even with nothing fetched — the shipped dead end."""
    from app.ui.onboarding import CalibrationPage

    page = CalibrationPage()
    page.load([])
    assert page.btn_finish.isEnabled(), "nothing here for the user to act on"
    assert not page.result().passed, "but it is still not a calibration"
    page.close()


# ---------------------------------------------------------------------------
# When does a user actually subscribe?
#
# Until this step existed: never. Both panels were written and correct, and
# both lived only in Settings, which onboarding does not open and does not
# mention. A new user finished setting up having paid for nothing, reached
# calibration, and was told there were "not enough live postings to calibrate
# against" — true, and a description of an empty market rather than of an
# unpaid subscription.
# ---------------------------------------------------------------------------

def test_the_wizard_asks_for_the_subscription_before_it_spends_anything(
        qapp, monkeypatch):
    from app.ui.onboarding import (STEP_ENTITLEMENT, STEP_INTERVIEW,
                                   OnboardingWizard)
    from PySide6.QtWidgets import QLabel

    panel = QLabel("subscribe here")
    w = OnboardingWizard(extract=lambda p: ([], []), sample=lambda: items(1),
                         entitlement_panel=panel)
    assert w.stack.widget(STEP_ENTITLEMENT) is panel
    assert STEP_ENTITLEMENT < STEP_INTERVIEW, (
        "the interview is the first screen that spends the user's money")
    w.close()


def test_the_subscription_step_never_traps_anyone(qapp, monkeypatch):
    """The app is deliberately useful unpaid — the board and the brief stay
    open, and it is the live feed that is bought. Requiring payment to finish
    setting up would trap someone who wants to look first."""
    from app.core import api_key
    from app.ui.onboarding import STEP_ENTITLEMENT, OnboardingWizard
    from PySide6.QtWidgets import QLabel

    monkeypatch.setattr(api_key, "get", lambda: "sk-ant-stored")
    w = OnboardingWizard(extract=lambda p: ([], []), sample=lambda: items(1),
                         entitlement_panel=QLabel("subscribe"))
    w._show_step(STEP_ENTITLEMENT)
    assert w.btn_next.isEnabled()
    w.close()


def test_a_build_with_no_variant_flag_shows_no_purchase_panel():
    """Guessing is worse than showing nothing: a Windows key box on a Mac
    build is the exact shape guideline 3.1.1 forbids."""
    from app.main import onboarding_entitlement_panel
    import app.core.build_variant as bv

    real = bv.variant
    try:
        bv.variant = lambda: "none"
        assert onboarding_entitlement_panel() is None
    finally:
        bv.variant = real
