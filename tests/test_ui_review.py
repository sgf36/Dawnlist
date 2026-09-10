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


def bar_text(win):
    """Every label in the funnel bar, at any depth.

    The stats are QWidget columns of two plain QLabels rather than one
    rich-text label, so this walks rather than reading the top level.
    """
    from PySide6.QtWidgets import QLabel
    return " ".join(lbl.text() for lbl in win.funnel.findChildren(QLabel))


def test_the_funnel_bar_shows_every_stage(win):
    counts = {"swept": 1143, "deduped": 1088, "gated_out": 213,
              "screened_likely": 118, "screened_out": 757, "assessed": 112}
    win.load([], counts)
    joined = bar_text(win)
    for value in counts.values():
        assert str(value) in joined, "a count is never shown without its siblings"


def test_every_funnel_caption_is_labelled(win):
    """A bare number with no caption is a count without what it excludes."""
    win.load([], {"swept": 10, "deduped": 9, "gated_out": 1,
                  "screened_likely": 5, "screened_out": 3, "assessed": 5})
    joined = bar_text(win)
    for caption in ("Swept", "Deduped", "Gated", "Screened in",
                    "Screened out", "Assessed"):
        assert caption in joined


def test_an_incomplete_run_is_announced_in_the_bar(win):
    win.load([], {"swept": 10, "assessed": 4, "left_unread": 6},
             incomplete_note="context exhausted")
    assert "6 left unread" in bar_text(win)
    # The visible text is elided to fit the chip, so the FULL note lives on the
    # tooltip - it must never be lost, only shortened.
    from PySide6.QtWidgets import QLabel
    tips = " ".join(l.toolTip() for l in win.funnel.findChildren(QLabel))
    assert "context exhausted" in tips


def test_a_very_long_note_is_elided_not_allowed_to_overflow(win):
    from PySide6.QtWidgets import QLabel
    note = "context exhausted after " + ("a very long explanation " * 12)
    win.load([], {"swept": 10, "assessed": 4, "left_unread": 6},
             incomplete_note=note)
    warn = [l for l in win.funnel.findChildren(QLabel)
            if l.objectName() == "funnelWarning"][0]
    assert warn.width() <= 420 or warn.sizeHint().width() <= 420
    assert warn.text() != note, "the label must elide rather than clip"
    assert note in warn.toolTip(), "the whole note is still available"



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


# --------------------------------------------------------------------------
# Layout regressions. Both of these shipped once and were only visible by
# rendering the window and looking at it.
# --------------------------------------------------------------------------
def test_all_three_decision_buttons_share_a_height(win):
    """Styling ONE QPushButton drops Qt's native metrics for that one only.

    btn_later had no stylesheet while the other two did, so the row of buttons
    came out at mismatched heights with the text tight against the edges.
    All three are now styled together by object name.
    """
    win.show()
    heights = {b.sizeHint().height() for b in
               (win.btn_pursue, win.btn_later, win.btn_reject)}
    assert len(heights) == 1, f"button heights disagree: {heights}"
    assert heights.pop() >= 34, "buttons need room for their padding"


def test_no_widget_paints_its_own_background(win):
    """Qt stylesheets CASCADE to children.

    A `background` set on a container repaints every label inside it, and a
    `border-radius` turns each one into its own box with no padding — which is
    how the funnel bar became a row of chips with clipped text. The window owns
    the one stylesheet; nothing below it sets its own.
    """
    import pathlib
    import re

    src = pathlib.Path(win.__class__.__module__.replace(".", "/") + ".py")
    if not src.exists():
        import app.ui.review as mod
        src = pathlib.Path(mod.__file__)
    text = src.read_text(encoding="utf-8")
    calls = re.findall(r"(\w+)\.setStyleSheet\(", text)
    assert calls == ["self"], (
        f"only the window may set a stylesheet; found {calls}")


def test_the_funnel_captions_fit_their_labels(win):
    """The captions clipped when they were one rich-text label: an HTML
    line-height renders taller than the sizeHint Qt reports. They are two
    plain-text labels now, so Qt measures both lines exactly."""
    from PySide6.QtWidgets import QLabel
    win.load([], {"swept": 1143, "deduped": 1088, "gated_out": 213,
                  "screened_likely": 118, "screened_out": 757, "assessed": 112})
    win.show()
    for label in win.funnel.findChildren(QLabel):
        if label.objectName() == "funnelWarning":
            continue
        hint = label.sizeHint().height()
        assert hint > 0
        assert label.height() == 0 or label.height() >= hint, (
            f"{label.text()!r} is allotted less height than it needs")


# ---------------------------------------------------------------------------
# The gap analysis, in the pane where the decision is actually made
# ---------------------------------------------------------------------------

REQUIREMENTS = ("Requirements\n"
                "- Experience in revenue management required\n"
                "- Experience in feasibility studies required\n"
                "Desirable\n"
                "- Familiarity with Power BI is desirable\n")


def test_no_evidence_means_no_gap_section(win):
    """Before onboarding every requirement is unsupported, and a list of forty
    missing things teaches the reader to skip the section."""
    win.load([row("1", bucket="strong", description=REQUIREMENTS)], {})
    win.tabs.setCurrentIndex(0)
    win.shortlist.setCurrentItem(win.shortlist.topLevelItem(0))
    assert "What this posting asks for" not in win.detail.toPlainText()


def test_an_unsupported_requirement_is_named(win):
    win.evidence = "Feasibility studies across a portfolio."
    win.load([row("1", bucket="strong", description=REQUIREMENTS)], {})
    win.tabs.setCurrentIndex(0)
    win.shortlist.setCurrentItem(win.shortlist.topLevelItem(0))
    text = win.detail.toPlainText()
    assert "does not support" in text
    assert "revenue management" in text


def test_a_preference_is_not_shown_as_a_bar(win):
    """Presenting a nice-to-have as a requirement costs somebody an
    afternoon."""
    win.evidence = "Feasibility studies across a portfolio."
    win.load([row("1", bucket="strong", description=REQUIREMENTS)], {})
    win.tabs.setCurrentIndex(0)
    win.shortlist.setCurrentItem(win.shortlist.topLevelItem(0))
    text = win.detail.toPlainText()
    unsupported = text.split("does not support")[1].split("Preferred")[0]
    assert "power bi" not in unsupported.lower()
    assert "Preferred, not required" in text


def test_full_coverage_says_so_rather_than_showing_nothing(win):
    win.evidence = "Revenue management and feasibility studies, ten years."
    win.load([row("1", bucket="strong", description=REQUIREMENTS)], {})
    win.tabs.setCurrentIndex(0)
    win.shortlist.setCurrentItem(win.shortlist.topLevelItem(0))
    assert "covers every stated requirement" in win.detail.toPlainText()


def test_a_posting_stating_nothing_shows_no_section(win):
    """An empty gap list must never render as though the person qualified."""
    win.evidence = "Anything at all."
    win.load([row("1", bucket="strong",
                  description="We want someone great to join the team.")], {})
    win.tabs.setCurrentIndex(0)
    win.shortlist.setCurrentItem(win.shortlist.topLevelItem(0))
    assert "What this posting asks for" not in win.detail.toPlainText()


def test_the_description_is_still_shown_below_the_gaps(win):
    win.evidence = "Feasibility studies."
    win.load([row("1", bucket="strong", description=REQUIREMENTS)], {})
    win.tabs.setCurrentIndex(0)
    win.shortlist.setCurrentItem(win.shortlist.topLevelItem(0))
    text = win.detail.toPlainText()
    assert text.index("What this posting asks for") < text.index("Desirable")


# ---------------------------------------------------------------------------
# The detail pane must describe the row the buttons will act on
#
# `_decide` reads `_current_row()`, which resolves against the CURRENT TAB.
# `_show_detail` was wired only to `currentItemChanged` inside a tree, so
# switching tabs left the pane describing the posting from the tab you left
# while every button acted on the one selected in the tab you arrived at.
#
# A person reads posting A and presses "Reject - permanently" on posting B,
# and rejections have no expiry by design. Found because a store screenshot
# showed a detail pane naming a different job from the row beside it.
# ---------------------------------------------------------------------------

def _two_tab_window(qapp):
    from app.ui.review import ReviewRow, ReviewWindow

    w = ReviewWindow()
    w.load([
        ReviewRow(job_id="a", title="Head of Strategy", company="Acme",
                  location="London", url="", description="d",
                  bucket="strong", reason="fits"),
        ReviewRow(job_id="b", title="Night Auditor", company="Beta",
                  location="Leeds", url="", description="d",
                  bucket="screened-out", reason="", screen_reason="night"),
    ], {})
    return w


def test_switching_tabs_updates_the_detail_pane(qapp):
    w = _two_tab_window(qapp)

    # Select in the shortlist, then move to the screened-out tab.
    shortlist = w.shortlist
    shortlist.setCurrentItem(shortlist.topLevelItem(0))
    assert "Head of Strategy" in w.detail.toHtml()

    for i in range(w.tabs.count()):
        if w.tabs.widget(i) is w.screened_out:
            w.tabs.setCurrentIndex(i)
            break
    w.screened_out.setCurrentItem(w.screened_out.topLevelItem(0))

    shown = w.detail.toHtml()
    assert "Night Auditor" in shown, (
        "the pane still describes the posting from the tab that was left")
    assert "Head of Strategy" not in shown
    w.close()


def test_the_pane_and_the_buttons_never_disagree(qapp):
    """The invariant, stated directly: whatever `_decide` would act on is
    what the reader is looking at."""
    w = _two_tab_window(qapp)
    decided = []
    w.decided.connect(lambda job_id, d: decided.append(job_id))

    for i in range(w.tabs.count()):
        if w.tabs.widget(i) is w.screened_out:
            w.tabs.setCurrentIndex(i)
            break
    w.screened_out.setCurrentItem(w.screened_out.topLevelItem(0))

    row = w._current_row()
    assert row is not None
    assert row.title in w.detail.toHtml(), (
        "the buttons would act on a posting the pane is not showing")
    w.close()


def test_a_tab_with_nothing_selected_clears_the_pane(qapp):
    """Better an empty pane than a stale one: an empty pane cannot be read as
    a description of whatever the buttons are about to do."""
    w = _two_tab_window(qapp)
    w.shortlist.setCurrentItem(w.shortlist.topLevelItem(0))
    assert "Head of Strategy" in w.detail.toHtml()

    for i in range(w.tabs.count()):
        if w.tabs.widget(i) is w.rejected:      # empty in this fixture
            w.tabs.setCurrentIndex(i)
            break
    assert "Head of Strategy" not in w.detail.toHtml()
    w.close()
