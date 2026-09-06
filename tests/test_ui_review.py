"""Review window smoke tests.

These prove the window builds and routes rows to the right tab. They do NOT
prove it is readable — that needs `tools/render_ui.py` and a human looking at
the PNG. A green test here plus an unopened image is exactly the failure mode
that ships a broken screen.
"""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui.review import ReviewRow, ReviewWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def win(qapp):
    w = ReviewWindow()
    yield w
    w.close()


def row(jid, **kw):
    base = dict(job_id=jid, title="T", company="C", location="London",
                url="", description="d")
    base.update(kw)
    return ReviewRow(**base)


def test_rows_route_to_the_right_tab(win):
    win.load([
        row("1", bucket="strong", reason="fits"),
        row("2", bucket="rejected", reason="no"),
        row("3", bucket="screened-out", screen_reason="unsupported title"),
        row("4", bucket="screened-out", screen_reason="contained", contained=True),
    ], {"swept": 4, "assessed": 2})

    assert win.shortlist.topLevelItemCount() == 1
    assert win.rejected.topLevelItemCount() == 1
    assert win.screened_out.topLevelItemCount() == 1
    assert win.contained.topLevelItemCount() == 1


def test_screened_out_rows_are_browsable_not_deleted(win):
    win.load([row(str(i), bucket="screened-out", screen_reason="x")
              for i in range(9)], {"swept": 9})
    assert win.screened_out.topLevelItemCount() == 9
    assert "9" in win.tabs.tabText(2)


def test_the_funnel_bar_shows_every_stage(win):
    counts = {"swept": 1143, "deduped": 1088, "gated_out": 213,
              "screened_likely": 118, "screened_out": 757, "assessed": 112}
    win.load([], counts)
    texts = [win.funnel._layout.itemAt(i).widget().text()
             for i in range(win.funnel._layout.count())
             if win.funnel._layout.itemAt(i).widget()]
    joined = " ".join(texts)
    for value in counts.values():
        assert str(value) in joined, "a count is never shown without its siblings"


def test_an_incomplete_run_is_announced_in_the_bar(win):
    win.load([], {"swept": 10, "assessed": 4, "left_unread": 6},
             incomplete_note="context exhausted")
    texts = " ".join(
        win.funnel._layout.itemAt(i).widget().text()
        for i in range(win.funnel._layout.count())
        if win.funnel._layout.itemAt(i).widget())
    assert "6 left unread" in texts and "context exhausted" in texts


def test_strong_verdicts_sort_above_rejections(win):
    win.load([row("1", bucket="judgement-call", company="A"),
              row("2", bucket="strong", company="Z")], {})
    assert win.shortlist.topLevelItem(0).data(0, 0x0100) == "2"


def test_deciding_emits_the_job_id_and_decision(win):
    win.load([row("42", bucket="strong", reason="fits")], {})
    win.tabs.setCurrentIndex(0)
    win.shortlist.setCurrentItem(win.shortlist.topLevelItem(0))
    seen = []
    win.decided.connect(lambda j, d: seen.append((j, d)))
    win.btn_pursue.click()
    win.btn_reject.click()
    assert seen == [("42", "pursue"), ("42", "reject")]


def test_the_full_why_text_is_available_on_hover(win):
    long_reason = ("Tagged Entertainment Providers, but the substance is "
                   "premium hospitality at a landmark venue")
    win.load([row("1", bucket="strong", reason=long_reason)], {})
    assert win.shortlist.topLevelItem(0).toolTip(2) == long_reason


def test_an_unchecked_requirement_is_explained_not_hidden(win):
    win.load([row("1", bucket="possible", reason="unavailable",
                  requirement_checked=False)], {})
    win.tabs.setCurrentIndex(0)
    win.shortlist.setCurrentItem(win.shortlist.topLevelItem(0))
    assert "never as a failure" in win.detail.toHtml()


def test_no_dangling_dash_when_there_is_no_reason(win):
    win.load([row("1", bucket="screened-out", screen_reason="x")], {})
    win.tabs.setCurrentIndex(2)
    win.screened_out.setCurrentItem(win.screened_out.topLevelItem(0))
    assert "screened-out —" not in win.detail.toPlainText()
