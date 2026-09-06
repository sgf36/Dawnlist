"""The whole chain, once, against one database.

Every piece below is covered by its own test file. This one exists because the
seams between them are not: onboarding writes a brief that the pipeline reads,
the pipeline's shortlist becomes a decision, the decision opens an opportunity,
and only then does drafting have a factsheet to be truthful against. Each of
those hand-offs is a place where two correct modules can still disagree about
who owns a field, and no unit test looks at a hand-off.

Two boundaries are stubbed, both of them network: `provider.search` and `send`.
Nothing else is. In particular the database is real and migrated, so a column
this chain forgets to write shows up here as a failure rather than as an empty
draft in front of a user.
"""
import re
from datetime import date
from email import message_from_bytes, policy
from pathlib import Path

import pytest

from app.core import db
from app.core.board_repo import add_task, load_board, set_stage
from app.core.pipeline import permanent_reject_gate, persist, run_morning
from app.core.rules import RuleTable
from app.core.tracker import Stage
from app.feed.base import FeedProvider, FetchResult, SearchQuery
from app.feed.models import Job
from app.main import load_document
from app.onboarding.calibration import (CalibrationItem, CalibrationResult,
                                        complete_calibration, is_calibrated)
from app.onboarding.interview import save_document
from app.outreach.run import due_today, prepare_drafts
from app.outreach.voice import build_profile
from app.ui.adapter import record_decision, rejected_keys, rows_from_outcome

TUE = date(2026, 9, 8)

FACTSHEET = """# Background factsheet

## Acme Hotels
**Asset Manager** - 2019 to 2023
- Reviewed a 14-property portfolio and identified GBP 4.2m in cost opportunities.

## Must never be claimed
- Do not claim the GBP 4.2m was saved or delivered. It was identified only.
"""

DRAFT_BRIEF = """# Fit brief

Operational real estate and hospitality strategy roles in London.
"""

BODY = ("I am an individual exploring roles in hospitality strategy, and I am "
        "not selling anything. Your portfolio work is why I am writing. Could "
        "we speak briefly?")


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "chain.sqlite3")
    db.migrate(c)
    yield c
    c.close()


class StubProvider(FeedProvider):
    name = "theirstack"

    def __init__(self, jobs):
        self.jobs = jobs
        self.calls = 0

    def search(self, query):
        self.calls += 1
        return FetchResult(jobs=self.jobs, pages_fetched=1, exhausted=True,
                           credits_estimate=len(self.jobs))

    def credits_used(self):
        return len(self.jobs)


def job(jid, title, desc="Strategy work across an operational portfolio."):
    return Job(provider="theirstack", provider_job_id=jid, title=title,
               company="Meridian Group", description_text=desc)


def assessor(buckets):
    """Stands in for the model. Keyed by ref so a reordered batch is caught."""

    def send(request):
        refs = re.findall(r'ref="([^"]+)"', request["messages"][0]["content"])
        return {"verdicts": [
            {"job_ref": r, "bucket": buckets.get(r, "rejected"),
             "reason": "matches the brief", "disqualifying_quote": None,
             "requirement_checked": True}
            for r in reversed(refs)]}       # deliberately out of order

    return send


def add_contact(conn, oid, name="Jo Bennett", email="jo@meridian.example"):
    return conn.execute(
        "INSERT INTO contacts(opportunity_id, name, email, created_at) "
        "VALUES(?,?,?,?)", (oid, name, email, "x")).lastrowid


def test_a_user_goes_from_documents_to_a_draft(conn, tmp_path):
    # -- 0. the interview writes the two documents -------------------------
    # Three modules write and read these rows and each names the kind as a
    # string literal. A disagreement about that string does not raise: it
    # returns "" and the user gets a shortlist scored against nothing.
    save_document(conn, "factsheet", FACTSHEET)
    save_document(conn, "fit_brief", DRAFT_BRIEF)
    assert load_document(conn, "factsheet") == FACTSHEET
    assert load_document(conn, "fit_brief") == DRAFT_BRIEF

    # -- 1. onboarding: the brief the rest of the chain reads ---------------
    assert not is_calibrated(conn), "the gate starts shut"

    sample = [CalibrationItem(job_key=str(i), title=f"Role {i}",
                              company="Acme", description="d",
                              app_verdict="rejected",
                              app_reason="outside the brief")
              for i in range(10)]
    for item in sample[:8]:
        item.user_verdict = "rejected"
    sample[0].user_verdict = "strong"
    sample[0].brief_sentence = "Operational real estate is in scope."

    result = CalibrationResult(items=sample)
    assert result.passed, result.blocking_reasons()
    brief = complete_calibration(conn, load_document(conn, "fit_brief"), result)

    assert is_calibrated(conn), "the gate opens only here"
    assert "Operational real estate is in scope." in brief, (
        "the user's correction must reach the brief the run is scored against")
    assert load_document(conn, "fit_brief") == brief, (
        "calibration saves the brief through its own writer, not "
        "`save_document`. If the two ever name the kind differently the "
        "correction is written somewhere nothing reads.")

    # -- 2. the morning run, scored against THAT brief ----------------------
    jobs = [job("m-1", "Head of Asset Strategy"),
            job("m-2", "Night Auditor", desc="Front desk, overnight.")]
    provider = StubProvider(jobs)
    outcome = run_morning(
        conn, provider, [SearchQuery(label="strategy", titles=["strategy"])],
        RuleTable(strong_terms=["strategy"]),
        fit_brief=brief, factsheet=FACTSHEET,
        send=assessor({"m-1": "strong"}),
        gates=[permanent_reject_gate(rejected_keys(conn))])

    assert outcome.complete, outcome.fetch_errors
    persist(conn, outcome)

    rows = rows_from_outcome(outcome)
    assert rows, "nothing reached the review screen"
    shortlisted = [r for r in rows if r.bucket == "strong"]
    assert len(shortlisted) == 1, [(r.title, r.bucket) for r in rows]
    assert shortlisted[0].title == "Head of Asset Strategy", (
        "verdicts came back reordered; they must be matched by ref, not index")

    # The night-audit role never reached the model: the rule table killed it
    # for nothing. That saving is the whole point of a four-tier screen, and it
    # is only observable from here — `assess` alone never sees the job.
    screened = [r for r in rows if r.bucket == "screened-out"]
    assert [r.title for r in screened] == ["Night Auditor"]

    # -- 3. the decision, and its two consequences --------------------------
    record_decision(conn, shortlisted[0].job_id, "pursue")
    rejected = [r for r in rows if r.bucket != "strong"]
    record_decision(conn, rejected[0].job_id, "reject")

    assert ("theirstack", "m-2") in rejected_keys(conn), (
        "a rejection that does not suppress tomorrow's sweep is not a rejection")

    # -- 4. the board, opened by the decision itself ------------------------
    # This step used to call `create_opportunity` here, which is exactly how a
    # smoke test can pass over a severed chain: the app did not open anything
    # on a Pursue, and the test did it on the app's behalf.
    board = load_board(conn)
    assert len(board) == 1, "Pursue must put the employer on the board"
    assert board[0].company == "Meridian Group"
    assert board[0].stage is Stage.IDENTIFIED

    oid = board[0].id
    add_contact(conn, oid)
    add_task(conn, str(oid), "Write to Jo Bennett", due_on=TUE)
    set_stage(conn, str(oid), Stage.CONTACTED)
    assert load_board(conn)[0].stage is Stage.CONTACTED

    # -- 5. drafting, on the factsheet the interview produced --------------
    due = due_today(conn, today=TUE)
    assert len(due) == 1 and due[0].actionable, [d.blocked for d in due]

    report = prepare_drafts(conn, due, folder=tmp_path / "drafts",
                            factsheet=FACTSHEET, voice=build_profile([]),
                            send=lambda _r: BODY, today=TUE)

    assert not report.blocked, [i.blocked for i in report.blocked]
    assert report.counts["drafted"] == 1
    assert report.counts["send_ready"] == 1, (
        "an unresolved placeholder here means the factsheet did not reach the "
        "drafter")

    draft = report.drafts.drafts[0]
    assert draft.to_name == "Jo Bennett"
    assert draft.send_ready

    path = Path(draft.path)
    assert path.exists() and path.suffix == ".eml"

    # Read it the way a mail client would. Grepping the raw file finds nothing:
    # quoted-printable inserts a soft break mid-word, so `not sell=\ning`.
    written = message_from_bytes(path.read_bytes(), policy=policy.default)
    assert written["To"] == "Jo Bennett <jo@meridian.example>"
    body = written.get_body(("plain",)).get_content()
    assert "not selling anything" in body, "the ask must survive to the file"

    # Nothing was sent. The file is the whole output, and the row that records
    # it is what makes tomorrow's run a revision rather than a second letter.
    live = conn.execute(
        "SELECT COUNT(*) FROM drafts WHERE superseded=0").fetchone()[0]
    assert live == 1


def test_the_chain_refuses_to_run_before_calibration(conn):
    """Every step above is reachable only through the gate. If the gate can be
    bypassed the user is scored against a brief they never corrected."""
    assert not is_calibrated(conn)

    sample = [CalibrationItem(job_key=str(i), title=f"R{i}", company="A",
                              description="d", app_verdict="rejected",
                              app_reason="r") for i in range(10)]
    for item in sample[:8]:
        item.user_verdict = "rejected"
    sample[0].user_verdict = "strong"          # disagreed, no sentence

    result = CalibrationResult(items=sample)
    assert not result.passed
    with pytest.raises(Exception):
        complete_calibration(conn, DRAFT_BRIEF, result)
    assert not is_calibrated(conn), "a failed calibration must not open the gate"
