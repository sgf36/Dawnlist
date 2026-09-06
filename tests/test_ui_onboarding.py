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
