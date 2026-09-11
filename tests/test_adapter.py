"""The run -> screen -> decision loop, end to end."""
import pytest

pytest.importorskip("PySide6")

from app.core import db  # noqa: E402
from app.core.pipeline import persist, run_morning  # noqa: E402
from app.core.rules import RuleTable  # noqa: E402
from app.feed.base import FeedProvider, FetchResult, SearchQuery  # noqa: E402
from app.feed.models import Job  # noqa: E402
from app.ui.adapter import (SCREENED_OUT, incomplete_note, record_decision,  # noqa: E402
                            rejected_keys, rows_from_outcome, split_job_id)


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.sqlite3")
    db.migrate(c)
    yield c
    c.close()


def job(jid, title="Head of Strategy", company="Acme", desc="A strategy role."):
    return Job(provider="theirstack", provider_job_id=jid, title=title,
               company=company, description_text=desc, url=f"https://ats/{jid}")


class Stub(FeedProvider):
    name = "theirstack"

    def __init__(self, results):
        self.results = results

    def search(self, q):
        return self.results[q.label]

    def credits_used(self):
        return 0


def ok(jobs):
    return FetchResult(jobs=jobs, pages_fetched=1, exhausted=True)


def strong_send(request):
    import re
    refs = re.findall(r'ref="([^"]+)"', request["messages"][0]["content"])
    return {"verdicts": [{"job_ref": r, "bucket": "strong", "reason": "fits",
                          "disqualifying_quote": None,
                          "requirement_checked": True} for r in refs]}


RULES = RuleTable(strong_terms=["strategy"])
Q = SearchQuery(label="strategy", titles=["strategy"])


def run(conn, jobs, send=strong_send, **kw):
    return run_morning(conn, Stub({"strategy": ok(jobs)}), [Q], RULES,
                       fit_brief="b", factsheet="f", send=send, **kw)


# -- rows -------------------------------------------------------------------
def test_assessed_rows_carry_the_verdict(conn):
    out = run(conn, [job("a")])
    rows = rows_from_outcome(out)
    assert len(rows) == 1
    assert rows[0].bucket == "strong" and rows[0].reason == "fits"
    assert rows[0].url == "https://ats/a"


def test_screened_out_postings_still_become_rows(conn):
    """spec 5.4: the unlikely pile is browsable, never erased."""
    out = run(conn, [job("a"), job("b", title="Kitchen Porter", desc="washing up")])
    rows = rows_from_outcome(out)
    screened = [r for r in rows if r.bucket == SCREENED_OUT]
    assert len(screened) == 1
    assert screened[0].screen_reason, "a screened-out row must say why"


def test_an_unassessed_survivor_is_a_judgement_call_not_a_rejection(conn):
    """An unread posting is an unknown. Hiding it is the same failure as
    presenting an unqualified one."""
    def skips_everything(_r):
        return {"verdicts": []}

    out = run(conn, [job("a")], send=skips_everything)
    row = rows_from_outcome(out)[0]
    assert row.bucket == "judgement-call"
    assert row.requirement_checked is False
    assert "not assessed" in row.reason


def test_a_downgrade_reaches_the_user(conn):
    desc = "Must have a minimum of 10 years' experience. Strategy role."

    def hallucinating(_r):
        return {"verdicts": [{"job_ref": "theirstack:a", "bucket": "rejected",
                              "reason": "needs an MBA",
                              "disqualifying_quote": "must hold an MBA",
                              "requirement_checked": True}]}

    out = run(conn, [job("a", desc=desc)], send=hallucinating)
    row = rows_from_outcome(out)[0]
    assert row.bucket == "judgement-call"
    assert "does not appear in the description" in row.downgrade_reason


def test_a_contained_match_is_marked_for_review(conn):
    rules = RuleTable(unsupported_titles=["office manager"])
    out = run_morning(conn, Stub({"strategy": ok(
        [job("a", title="Assistant Front Office Manager")])}), [Q], rules,
        fit_brief="b", factsheet="f", send=strong_send)
    row = rows_from_outcome(out)[0]
    assert row.contained and row.bucket == SCREENED_OUT


# -- the warning ------------------------------------------------------------
def test_a_clean_run_shows_no_warning(conn):
    assert incomplete_note(run(conn, [job("a")])) == ""


def test_a_fetch_failure_outranks_an_assessment_gap(conn):
    out = run_morning(conn, Stub({"strategy": FetchResult(jobs=[], error="HTTP 502")}),
                      [Q], RULES, fit_brief="b", factsheet="f", send=strong_send)
    assert "502" in incomplete_note(out), (
        "a partial morning must not be reported as a complete one with a gap")


# -- decisions --------------------------------------------------------------
def test_a_decision_is_persisted(conn):
    out = run(conn, [job("a")])
    persist(conn, out)
    record_decision(conn, "theirstack:a", "pursue")
    row = conn.execute("SELECT kind FROM decisions").fetchone()
    assert row["kind"] == "pursue"


def test_a_decision_can_be_revised(conn):
    out = run(conn, [job("a")])
    persist(conn, out)
    record_decision(conn, "theirstack:a", "later")
    record_decision(conn, "theirstack:a", "pursue")
    rows = conn.execute("SELECT kind FROM decisions").fetchall()
    assert len(rows) == 1 and rows[0]["kind"] == "pursue"


def test_an_unknown_decision_is_refused(conn):
    out = run(conn, [job("a")])
    persist(conn, out)
    with pytest.raises(ValueError, match="unknown decision"):
        record_decision(conn, "theirstack:a", "maybe")


def test_deciding_on_an_unpersisted_job_is_a_loud_error(conn):
    with pytest.raises(LookupError, match="persist the run first"):
        record_decision(conn, "theirstack:ghost", "pursue")


def test_rejections_compound_into_the_next_runs_gate(conn):
    """Each rejection is a posting never assessed again - that is the saving."""
    out = run(conn, [job("a"), job("b", title="Strategy Lead")])
    persist(conn, out)
    record_decision(conn, "theirstack:a", "reject")
    record_decision(conn, "theirstack:b", "pursue")
    assert rejected_keys(conn) == {("theirstack", "a")}


def test_job_id_round_trips():
    assert split_job_id("theirstack:abc-123") == ("theirstack", "abc-123")


# -- the whole loop ---------------------------------------------------------
def test_clicking_pursue_in_the_window_reaches_the_database(conn, qtbot=None):
    from PySide6.QtWidgets import QApplication

    from app.ui.adapter import connect_window
    from app.ui.review import ReviewWindow

    app = QApplication.instance() or QApplication([])
    out = run(conn, [job("a")])
    persist(conn, out)

    win = ReviewWindow()
    connect_window(win, conn)
    win.load(rows_from_outcome(out), out.funnel())
    win.tabs.setCurrentIndex(0)
    win.shortlist.setCurrentItem(win.shortlist.topLevelItem(0))
    win.btn_pursue.click()

    row = conn.execute("SELECT kind FROM decisions").fetchone()
    assert row["kind"] == "pursue"
    win.close()
