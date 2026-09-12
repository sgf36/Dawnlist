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
    from app.onboarding.interview import ingest_guidance
    from app.ui.onboarding import reflow
    out = reflow(ingest_guidance())
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
    assert w.stack.count() == 7, (
        "TERMS, ingest, key, entitlement, interview, searches, calibration — "
        "the entitlement step is where the user subscribes, and its absence "
        "was why a new install reached calibration having paid for nothing; "
        "the terms step is what binds a subscriber to the licence the feed's "
        "own supplier requires")
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
    from app.ui.onboarding import STEP_KEY, OnboardingWizard

    monkeypatch.setattr(api_key, "get", lambda: None)
    w = OnboardingWizard(extract=lambda p: (["cv.docx"], []), sample=lambda: items(1))
    w._show_step(STEP_KEY)
    assert not w.btn_next.isEnabled(), "an app with no key is inert"
    w.close()


def test_next_is_enabled_once_a_key_is_present(qapp, monkeypatch):
    from app.core import api_key
    from app.ui.onboarding import STEP_KEY, OnboardingWizard

    monkeypatch.setattr(api_key, "get", lambda: "sk-ant-stored")
    w = OnboardingWizard(extract=lambda p: (["cv.docx"], []), sample=lambda: items(1))
    w._show_step(STEP_KEY)
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


def test_the_gate_names_an_unpaid_subscription_rather_than_a_quiet_market(qapp):
    """The screen said the same thing whether the market was quiet or nothing
    had been bought, and on a fresh install the second is far commoner."""
    from app.onboarding.calibration import CalibrationSample
    from app.ui.onboarding import CalibrationPage

    page = CalibrationPage()
    page.load(CalibrationSample([], no_feed="No licence key found."))
    assert "no subscription or access code" in page.blockers_label.text()
    assert page.btn_finish.isEnabled(), "and it still must not trap anyone"

    # POSITIVE CONTROL: a sample that is simply short still reads as one.
    page.load(CalibrationSample([]))
    assert "no subscription" not in page.blockers_label.text()
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


def test_restore_from_the_menu_actually_restores(qapp):
    """The wizard looked up `restore`, which on the subscribe panel is the
    Restore BUTTON rather than a method, so the menu item only turned the
    page."""
    from app.ui.onboarding import STEP_ENTITLEMENT, OnboardingWizard
    from app.ui.settings import StoreKitEvents, SubscribePanel

    class SK:
        def __init__(self):
            self.restores = 0

        def available(self):
            return True

        def can_make_payments(self):
            return True

        def price(self):
            return "$79.00"

        def has_product(self):
            return True

        def restore(self):
            self.restores += 1
            return None

    sk = SK()
    panel = SubscribePanel(storekit=sk, events=StoreKitEvents())
    w = OnboardingWizard(extract=lambda p: ([], []), sample=lambda: items(1),
                         entitlement_panel=panel)
    w.menu.restore_requested.emit()
    assert w.stack.currentIndex() == STEP_ENTITLEMENT
    assert sk.restores == 1, "the menu turned the page and restored nothing"
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


# ---------------------------------------------------------------------------
# The interview step cannot be walked past empty
#
# Next was enabled from the moment the step was reached, so setup could be
# finished having drafted nothing: calibration then recorded the placeholder
# "# Fit brief" as the document every later verdict is scored against.
# ---------------------------------------------------------------------------

def a_wizard(monkeypatch, **kwargs):
    from app.core import api_key
    from app.ui.onboarding import OnboardingWizard

    monkeypatch.setattr(api_key, "get", lambda: "sk-ant-stored")
    kwargs.setdefault("extract", lambda p: (["cv.docx"], []))
    kwargs.setdefault("sample", lambda: items(1))
    return OnboardingWizard(**kwargs)


def test_next_needs_both_documents_on_the_interview_step(qapp, monkeypatch):
    from app.ui.onboarding import STEP_INTERVIEW

    w = a_wizard(monkeypatch, drafter=lambda c, a: ("F", "B", []))
    w._show_step(STEP_INTERVIEW)
    assert not w.btn_next.isEnabled(), "nothing has been drafted or written"

    w.interview.factsheet.setPlainText("Acme Hotels, 2019 to 2023.")
    assert not w.btn_next.isEnabled(), "a factsheet alone is not the brief"

    w.interview.brief.setPlainText("Hotel asset management in London.")
    assert w.btn_next.isEnabled(), "written by hand counts — a failed draft "\
        "must not be a wall"
    w.close()


def test_next_is_off_while_the_draft_is_still_running(qapp, monkeypatch, settle):
    """Leaving mid-draft records the empty boxes on screen and throws away the
    answer the user is paying for."""
    from app.ui.onboarding import STEP_INTERVIEW

    w = a_wizard(monkeypatch, drafter=lambda c, a: ("FACTS", "BRIEF", []))
    w._show_step(STEP_INTERVIEW)
    w.interview.factsheet.setPlainText("typed earlier")
    w.interview.brief.setPlainText("typed earlier")
    assert w.btn_next.isEnabled(), "positive control"

    w.interview.run_draft(["cv.docx"])
    assert not w.btn_next.isEnabled(), "left mid-draft"
    settle(lambda: not w.interview.drafting, what="the draft")
    assert w.btn_next.isEnabled(), "and available again once it lands"
    w.close()


def test_drafting_with_no_cvs_says_so_rather_than_doing_nothing(qapp):
    """The one primary button on the screen did nothing at all, and the user
    could not see that it was waiting on files they never added."""
    from app.ui.onboarding import InterviewPage

    page = InterviewPage(drafter=lambda c, a: ("F", "B", []))
    page.run_draft()
    assert "Add your CVs first" in page.status.text()
    page.close()


# ---------------------------------------------------------------------------
# The terms step
# ---------------------------------------------------------------------------

def test_setup_starts_on_the_terms_and_will_not_move_until_they_are_agreed(
        qapp, monkeypatch):
    from app.ui.onboarding import STEP_INGEST, STEP_TERMS

    w = a_wizard(monkeypatch)
    assert w.stack.currentIndex() == STEP_TERMS, "before anything is read"
    assert not w.btn_next.isEnabled()

    w.terms.agree.setChecked(True)
    assert w.btn_next.isEnabled()
    w._next()
    assert w.stack.currentIndex() == STEP_INGEST
    w.close()


def test_the_terms_step_links_the_published_page_and_shows_its_date(qapp,
                                                                    monkeypatch):
    """The date is what the user is agreeing to, and what decides whether they
    are asked again."""
    from app.onboarding.terms import TERMS_LAST_UPDATED
    from app.ui.settings import TERMS_URL

    w = a_wizard(monkeypatch)
    assert TERMS_URL in w.terms.link.text()
    assert TERMS_LAST_UPDATED in w.terms.link.text()
    w.close()


def test_the_acceptance_is_recorded_on_leaving_the_step(qapp, monkeypatch):
    recorded = []
    w = a_wizard(monkeypatch, accept_terms=lambda: recorded.append(True))
    w.terms.agree.setChecked(True)
    assert recorded == [], "a tick is not yet the deliberate action"
    w._next()
    assert recorded == [True]
    w.close()


def test_somebody_who_has_already_agreed_is_not_asked_again(qapp, monkeypatch):
    from app.ui.onboarding import STEP_INGEST

    w = a_wizard(monkeypatch, terms_accepted=True)
    assert w.stack.currentIndex() == STEP_INGEST
    w.close()


def test_back_from_the_first_real_step_does_not_fall_out_of_the_flow(qapp,
                                                                     monkeypatch):
    from app.ui.onboarding import STEP_INGEST, STEP_TERMS

    w = a_wizard(monkeypatch, terms_accepted=True)
    w._back()
    assert w.stack.currentIndex() in (STEP_TERMS, STEP_INGEST)
    assert w.stack.currentIndex() >= 0
    w.close()


def test_the_standalone_gate_agrees_only_once_the_box_is_ticked(qapp):
    """The terms can be revised after setup. Sending that person back through
    the whole wizard to tick one box would be absurd."""
    from app.ui.onboarding import TermsWindow

    gate = TermsWindow(again=True)
    assert not gate.btn_accept.isEnabled()
    agreed = []
    gate.accepted.connect(lambda: agreed.append(True))
    gate.btn_accept.click()
    assert agreed == [], "disabled, so nothing happened"

    gate.page.agree.setChecked(True)
    assert gate.btn_accept.isEnabled()
    gate.btn_accept.click()
    assert agreed == [True]
    gate.close()


# ---------------------------------------------------------------------------
# Adding CVs: by button as well as by drag, merged rather than replaced, and
# read off the UI thread
# ---------------------------------------------------------------------------

def test_files_can_be_chosen_without_dragging_anything(qapp):
    """Drag and drop needs the file manager and the window visible at once,
    which rules out a maximised window, a remote session, and anyone who
    cannot drag."""
    from app.ui.onboarding import IngestPage

    page = IngestPage(picker=lambda parent, title, filt: ["/tmp/cv-2019.docx"])
    added = []
    page.files_added.connect(added.append)
    page.btn_choose.click()
    assert [p.name for p in added[0]] == ["cv-2019.docx"]
    page.close()


def test_the_file_dialog_offers_exactly_what_can_be_read(qapp):
    """A dialog offering a type the extractor refuses is a trap the user walks
    into one file at a time."""
    from app.onboarding.extract import SUPPORTED_SUFFIXES
    from app.ui.onboarding import _cv_file_filter

    shown = _cv_file_filter()
    for suffix in SUPPORTED_SUFFIXES:
        assert "*" + suffix in shown
    assert "*.doc " not in shown and not shown.endswith("*.doc)")


def test_a_second_batch_is_added_to_the_first(qapp, monkeypatch, settle):
    """Dropping again used to replace, so somebody adding their old CVs a
    folder at a time lost everything before — which is what the guidance on
    this very screen asks them to do."""
    from pathlib import Path

    read = []

    def extract(paths):
        read.append(list(paths))
        return [Path(p).name for p in paths], []

    w = a_wizard(monkeypatch, extract=extract)
    w._on_files([Path("/cvs/one.docx")])
    settle(lambda: not w._reading, what="the first read")
    w._on_files([Path("/cvs/two.docx")])
    settle(lambda: not w._reading, what="the second read")

    assert [p.name for p in w._paths] == ["one.docx", "two.docx"]
    assert [Path(p).name for p in read[-1]] == ["one.docx", "two.docx"], (
        "the whole set is re-read, so 'only one CV version' stops being said")
    assert w.ingest.files.count() == 2
    w.close()


def test_the_same_file_twice_is_one_file(qapp, monkeypatch, settle):
    from pathlib import Path

    w = a_wizard(monkeypatch,
                 extract=lambda paths: ([Path(p).name for p in paths], []))
    w._on_files([Path("/cvs/CV.docx")])
    settle(lambda: not w._reading, what="the read")
    w._on_files([Path("/cvs/CV.docx")])
    settle(lambda: not w._reading, what="the re-read")
    assert len(w._paths) == 1, "the corpus would hold it twice over"
    w.close()


def test_a_file_added_by_mistake_can_be_taken_out(qapp, monkeypatch, settle):
    """The wrong Jones, or a colleague's CV. Without this the only way out is
    to start setup again."""
    from pathlib import Path

    w = a_wizard(monkeypatch, terms_accepted=True,
                 extract=lambda paths: ([Path(p).name for p in paths], []))
    w._on_files([Path("/cvs/mine.docx"), Path("/cvs/someone-else.docx")])
    settle(lambda: not w._reading, what="the read")
    assert w.btn_next.isEnabled(), "positive control"

    w._on_files_removed([Path("/cvs/someone-else.docx")])
    settle(lambda: not w._reading, what="the re-read")
    assert [p.name for p in w._paths] == ["mine.docx"]

    w._on_files_removed([Path("/cvs/mine.docx")])
    settle(lambda: not w._reading, what="the final read")
    assert not w.btn_next.isEnabled(), "nothing left to work on"
    w.close()


def test_reading_the_files_does_not_happen_on_the_ui_thread(qapp, monkeypatch,
                                                            settle):
    """It opens and parses every file added. Inline, the window stopped
    answering the OS for the whole of it — which Store Policy 10.4.2 forbids."""
    import threading
    from pathlib import Path

    ui_thread = threading.current_thread().ident
    where = []

    def extract(paths):
        where.append(threading.current_thread().ident)
        return [Path(p).name for p in paths], []

    w = a_wizard(monkeypatch, terms_accepted=True, extract=extract)
    w._on_files([Path("/cvs/one.docx")])
    assert w.ingest.status.text(), "the screen must say it is reading"
    assert not w.btn_next.isEnabled(), "and not let the step be left mid-read"
    settle(lambda: not w._reading, what="the read")

    assert where and where[0] != ui_thread
    assert w.btn_next.isEnabled()
    assert not w.ingest.status.text()
    w.close()


def test_fetching_the_calibration_sample_does_not_happen_on_the_ui_thread(
        qapp, monkeypatch, settle):
    """A request per switched-on search and an assessment of everything that
    comes back — the slowest thing in setup, and it ran on the UI thread."""
    import threading

    from app.ui.onboarding import STEP_CALIBRATION, STEP_SEARCHES

    ui_thread = threading.current_thread().ident
    where = []

    def sample():
        where.append(threading.current_thread().ident)
        return items(10)

    w = a_wizard(monkeypatch, sample=sample)
    w.stack.setCurrentIndex(STEP_SEARCHES)
    w._next()

    assert w.stack.currentIndex() == STEP_CALIBRATION
    assert "Fetching" in w.calibration.blockers_label.text()
    assert not w.btn_back.isEnabled(), "Back mid-fetch lands the answer nowhere"
    settle(lambda: not w._sampling, what="the fetch")

    assert where and where[0] != ui_thread
    assert len(w.calibration._widgets) == 10
    assert w.btn_back.isEnabled()
    w.close()


def test_a_fetch_that_fails_is_shown_and_does_not_trap_anyone(qapp, monkeypatch,
                                                              settle):
    from app.ui.onboarding import STEP_SEARCHES

    def boom():
        raise RuntimeError("the feed returned HTTP 502")

    w = a_wizard(monkeypatch, sample=boom)
    w.stack.setCurrentIndex(STEP_SEARCHES)
    w._next()
    settle(lambda: not w._sampling, what="the failed fetch")

    assert "502" in w.calibration.blockers_label.text()
    assert w.calibration.btn_finish.isEnabled(), (
        "nothing on this screen for the user to act on, so they may leave")
    w.close()


# ---------------------------------------------------------------------------
# Subscribing from the ⋯ menu, and not paying twice for the same postings
# ---------------------------------------------------------------------------

def test_subscribing_from_the_menu_returns_to_where_it_was_opened(qapp,
                                                                  monkeypatch):
    """It was a plain jump to the subscribe step, and Next from there goes on
    to the interview — so somebody who opened the menu from the CV screen
    walked out past both the CVs and the key."""
    from PySide6.QtWidgets import QLabel

    from app.ui.onboarding import (STEP_ENTITLEMENT, STEP_INGEST, STEP_KEY,
                                   STEP_INTERVIEW)

    w = a_wizard(monkeypatch, terms_accepted=True,
                 entitlement_panel=QLabel("subscribe"))
    assert w.stack.currentIndex() == STEP_INGEST

    w.menu.subscribe_requested.emit()
    assert w.stack.currentIndex() == STEP_ENTITLEMENT
    w._next()
    assert w.stack.currentIndex() == STEP_INGEST, (
        "walked forward past the CVs and the key")

    # And from the key step, back to the key step — not onward.
    w._show_step(STEP_KEY)
    w.menu.subscribe_requested.emit()
    w._back()
    assert w.stack.currentIndex() == STEP_KEY

    # POSITIVE CONTROL: reached in the ordinary way, the subscribe step still
    # leads to the interview.
    w._show_step(STEP_ENTITLEMENT)
    w._next()
    assert w.stack.currentIndex() == STEP_INTERVIEW
    w.close()


def test_subscribing_does_not_enable_next_on_a_step_that_is_not_done(qapp,
                                                                     monkeypatch):
    """The panel's own signal used to enable Next unconditionally."""
    from app.core import api_key
    from app.ui.onboarding import STEP_KEY

    w = a_wizard(monkeypatch, terms_accepted=True)
    monkeypatch.setattr(api_key, "get", lambda: None)
    w._show_step(STEP_KEY)
    assert not w.btn_next.isEnabled(), "positive control"

    if hasattr(w.entitlement, "entitlement_changed"):
        w.entitlement.entitlement_changed.emit(True)
    w._refresh_next()
    assert not w.btn_next.isEnabled(), "an app with no key is still inert"
    w.close()


def test_going_back_and_forward_does_not_buy_the_postings_again(qapp,
                                                                monkeypatch,
                                                                settle):
    """Every posting a fetch returns is paid for, and the user's decisions on
    the ones already fetched were thrown away with them."""
    from app.ui.onboarding import STEP_SEARCHES

    fetches = []

    def sample():
        fetches.append(1)
        return items(10)

    w = a_wizard(monkeypatch, terms_accepted=True, sample=sample,
                 searches=lambda: [("hotels", ["hotels"], True)],
                 set_search=lambda label, on: None)
    w.searches.load(w._searches())
    w.stack.setCurrentIndex(STEP_SEARCHES)
    w._next()
    settle(lambda: not w._sampling, what="the first fetch")
    assert len(fetches) == 1

    choose(w.calibration, 0, "strong")
    type_sentence(w.calibration, 0, "Operational real estate is in scope.")

    w._back()
    assert w.stack.currentIndex() == STEP_SEARCHES
    w._next()
    assert len(fetches) == 1, "the same rows were bought a second time"
    assert w.calibration._widgets[0].item.brief_sentence == (
        "Operational real estate is in scope."), "the correction was discarded"
    w.close()


def test_switching_a_different_search_on_asks_before_discarding(qapp,
                                                                monkeypatch,
                                                                settle):
    from app.ui.onboarding import STEP_CALIBRATION, STEP_SEARCHES

    fetches = []
    answers = []

    def sample():
        fetches.append(1)
        return items(10)

    w = a_wizard(monkeypatch, terms_accepted=True, sample=sample,
                 confirm=lambda: answers.pop(0),
                 searches=lambda: [("hotels", ["hotels"], True),
                                   ("asset management", ["asset"], False)],
                 set_search=lambda label, on: None)
    w.searches.load(w._searches())
    w.stack.setCurrentIndex(STEP_SEARCHES)
    w._next()
    settle(lambda: not w._sampling, what="the first fetch")
    choose(w.calibration, 0, "strong")

    # Switch a second search on, then decline the re-fetch.
    w._back()
    w.searches._rows[1][1].setChecked(True)
    answers.append(False)
    w._next()
    assert len(fetches) == 1, "fetched despite the user saying no"
    assert w.stack.currentIndex() == STEP_CALIBRATION
    assert w.calibration._widgets[0].item.decided, "their answers survived"

    # Ask again and accept: now it fetches.
    w._back()
    answers.append(True)
    w._next()
    settle(lambda: not w._sampling, what="the second fetch")
    assert len(fetches) == 2
    w.close()


def test_an_empty_first_fetch_is_retried_without_asking(qapp, monkeypatch,
                                                        settle):
    """Nothing was bought and nothing was decided, so there is nothing to
    confirm — and somebody who has just subscribed from the ⋯ menu is exactly
    who arrives here."""
    from app.ui.onboarding import STEP_SEARCHES

    results = [[], items(10)]
    asked = []

    w = a_wizard(monkeypatch, terms_accepted=True,
                 sample=lambda: results.pop(0),
                 confirm=lambda: asked.append(True) or True,
                 searches=lambda: [("hotels", ["hotels"], True)],
                 set_search=lambda label, on: None)
    w.searches.load(w._searches())
    w.stack.setCurrentIndex(STEP_SEARCHES)
    w._next()
    settle(lambda: not w._sampling, what="the empty fetch")

    w._back()
    w._next()
    settle(lambda: not w._sampling, what="the retry")
    assert asked == [], "asked about discarding decisions that do not exist"
    assert len(w.calibration._widgets) == 10
    w.close()


def test_setup_fits_the_screen_it_opens_on_and_keeps_its_buttons(qapp,
                                                                 monkeypatch):
    """It opened at a fixed 900x780. The Store's stated minimum screen is
    1366x768, and its working area is shorter still — so setup opened taller
    than the desktop with Back and Next below the bottom edge."""
    from PySide6.QtCore import QRect

    small = QRect(0, 0, 1366, 728)          # 768 less a taskbar
    w = a_wizard(monkeypatch, terms_accepted=True)
    placed = w.fit_to_screen(small)

    assert w.width() <= int(small.width() * 0.9)
    assert w.height() <= int(small.height() * 0.9)
    assert placed.center().x() == small.center().x() or abs(
        placed.center().x() - small.center().x()) <= 2, "centred on the screen"

    w.show()
    for _ in range(4):
        qapp.processEvents()
    assert w.btn_next.isVisible() and w.btn_back.isVisible()
    bottom = w.btn_next.mapTo(w, w.btn_next.rect().bottomLeft()).y()
    assert bottom <= w.height(), (
        f"the navigation is {bottom - w.height()}px below the window")
    w.close()


def test_a_large_screen_does_not_get_a_stretched_window(qapp, monkeypatch):
    """POSITIVE CONTROL: the cap is a ceiling, not a size."""
    from PySide6.QtCore import QRect
    from app.ui.onboarding import PREFERRED_SIZE

    w = a_wizard(monkeypatch, terms_accepted=True)
    w.fit_to_screen(QRect(0, 0, 3840, 2160))
    assert (w.width(), w.height()) == PREFERRED_SIZE
    w.close()
