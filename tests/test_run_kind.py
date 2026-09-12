"""What each run was for, and which one the review window reports.

`runs.kind` is written when the row is opened. Everything that produces output
opens one — a daily search, a job-alert import, the calibration sample, an
outreach drafting run — and only the first two are postings the user works
through. Telling them apart is what stops drafting follow-ups at 06:50 from
cancelling the 07:00 search, and what lets a failed search be reported instead
of skipped (PIPELINE-P6).
"""
import ast
import pathlib

import pytest

from app.core import db
from app.main import NotConfigured, morning_run
from app.ui.adapter import latest_run_id, run_status
from tests.test_main import Stub, job, ok, seed, strong_send


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.sqlite3")
    db.migrate(c)
    yield c
    c.close()


def kinds(conn):
    return [(r["status"], r["kind"]) for r in
            conn.execute("SELECT status, kind FROM runs ORDER BY id")]


# -- tagging ----------------------------------------------------------------

def test_a_search_tags_its_own_row(conn):
    seed(conn)
    morning_run(conn, provider=Stub(ok([job("a")])), send=strong_send)
    assert kinds(conn) == [("complete", db.SWEEP)]


class Exploding(Stub):
    def search(self, query):
        raise RuntimeError("the feed fell over mid-run")


def test_a_search_that_crashes_is_still_todays_search(conn):
    """Tagged when the row opens, so the refreshes it spent are accounted for
    even though the run never reached its own end."""
    seed(conn)
    with pytest.raises(RuntimeError):
        morning_run(conn, provider=Exploding(ok([])), send=strong_send)
    assert kinds(conn) == [("failed", db.SWEEP)]


def test_a_refused_run_opens_no_row(conn):
    """Nothing was spent, so Run now must stay offered once it is fixed."""
    seed(conn, calibrated=False)
    with pytest.raises(NotConfigured):
        morning_run(conn, provider=Stub(ok([job("a")])), send=strong_send)
    assert kinds(conn) == []


def test_drafting_outreach_is_not_the_days_search(conn, tmp_path):
    """The real call site, not a hand-written row: `outreach/run.py` took
    `db.run`'s default and every drafting run was filed as a sweep — which
    would have cancelled that morning's search."""
    from app.outreach.run import prepare_drafts
    from app.outreach.voice import build_profile

    prepare_drafts(conn, [], folder=tmp_path, factsheet="A factsheet.",
                   voice=build_profile([]), send=lambda request: "")
    assert kinds(conn) == [("complete", db.OUTREACH)]

    from app.core.schedule import run_started_today
    from datetime import datetime, timezone
    assert not run_started_today(conn, datetime.now(timezone.utc),
                                 lambda m: m.astimezone(timezone.utc))


APP = pathlib.Path(__file__).resolve().parent.parent / "app"


def _runs_opened_without_a_kind():
    """(file, enclosing function) for every call that opens a run silently."""
    found = []
    for path in APP.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                name = getattr(call.func, "attr", getattr(call.func, "id", ""))
                if name not in ("run", "run_morning"):
                    continue
                if name == "run" and getattr(call.func, "value", None) is None:
                    continue        # a bare run(), not db.run()
                if name == "run" and getattr(call.func.value, "id", "") != "db":
                    continue
                if not any(kw.arg == "kind" for kw in call.keywords):
                    found.append((path.name, node.name))
    return set(found)


def test_every_run_says_what_it_was_for():
    """The one exception is the daily search itself, which `db.run` defaults
    to. Any other caller that forgets is a run filed as a search — the defect
    this test exists to catch, found in outreach drafting."""
    assert _runs_opened_without_a_kind() == {("main.py", "morning_run")}


# -- which run the window reports -------------------------------------------

def add(conn, *, swept, kind, status="complete", note=None, error=None):
    cur = conn.execute(
        "INSERT INTO runs(started_at, status, swept, kind, incomplete_note, "
        "fetch_error) VALUES('2026-09-11T06:00:00+00:00',?,?,?,?,?)",
        (status, swept, kind, note, error))
    conn.commit()
    return cur.lastrowid


def test_a_failed_search_is_reported_rather_than_skipped(conn):
    add(conn, swept=40, kind=db.SWEEP)
    failed = add(conn, swept=0, kind=db.SWEEP, status="incomplete",
                 error="strategy: Could not reach the Dawnlist feed service.")
    assert latest_run_id(conn) == failed


def test_the_old_rule_would_have_hidden_it(conn):
    """Positive control: `swept > 0` alone picks the run before."""
    before = add(conn, swept=40, kind=db.SWEEP)
    add(conn, swept=0, kind=db.SWEEP, status="failed")
    old = conn.execute("SELECT MAX(id) FROM runs WHERE swept > 0").fetchone()[0]
    assert old == before


def test_a_later_outreach_run_does_not_empty_the_window(conn):
    search = add(conn, swept=40, kind=db.SWEEP)
    add(conn, swept=0, kind=db.OUTREACH)
    assert latest_run_id(conn) == search


def test_a_job_alert_import_is_what_the_window_shows(conn):
    add(conn, swept=40, kind=db.SWEEP)
    alerts = add(conn, swept=3, kind=db.ALERTS)
    assert latest_run_id(conn) == alerts


def test_the_calibration_sample_is_not_the_mornings_shortlist(conn):
    """It sweeps postings like a search does, and the user has already ruled on
    every one of them in the wizard."""
    add(conn, swept=10, kind=db.CALIBRATION)
    assert latest_run_id(conn) is None
    search = add(conn, swept=0, kind=db.SWEEP)
    assert latest_run_id(conn) == search


def test_the_status_carries_what_went_wrong(conn):
    run_id = add(conn, swept=0, kind=db.SWEEP, status="incomplete",
                 note="strategy: Daily refresh cap reached (3)",
                 error="strategy: Daily refresh cap reached (3)")
    status = run_status(conn, run_id)
    assert status.went_wrong
    assert status.status == "incomplete"
    assert "refresh cap" in status.incomplete_note
    assert "refresh cap" in status.fetch_error

    clean = run_status(conn, add(conn, swept=5, kind=db.SWEEP))
    assert not clean.went_wrong
    assert run_status(conn, None) is None


def test_a_first_launch_after_setup_is_not_an_empty_window(conn):
    """Onboarding assesses ten live postings and stores them as a calibration
    run. That run is not the morning's shortlist, but its postings are judged
    and undecided — and until the first search they are all there is to show.
    """
    from app.ui.adapter import rows_from_db

    calibration = add(conn, swept=1, kind=db.CALIBRATION)
    conn.execute(
        "INSERT INTO jobs(provider, provider_job_id, title, company, "
        "first_seen_run, screen_verdict) VALUES('theirstack','c1','Head of "
        "Strategy','Acme',?,'likely')", (calibration,))
    job_row = conn.execute("SELECT id FROM jobs").fetchone()[0]
    conn.execute(
        "INSERT INTO assessments(job_id, run_id, bucket, reason, created_at) "
        "VALUES(?,?,'strong','fits','x')", (job_row, calibration))
    conn.commit()

    assert latest_run_id(conn) is None, "a calibration run is not a search"
    rows = rows_from_db(conn)
    assert [r.title for r in rows] == ["Head of Strategy"]
    assert rows[0].carried_forward, "and it says where it came from"


def test_a_posting_the_user_already_ruled_on_stays_gone(conn):
    """The positive control: recovery must not re-open decided postings."""
    from app.ui.adapter import rows_from_db

    calibration = add(conn, swept=1, kind=db.CALIBRATION)
    conn.execute(
        "INSERT INTO jobs(provider, provider_job_id, title, company, "
        "first_seen_run, screen_verdict) VALUES('theirstack','c1','Head of "
        "Strategy','Acme',?,'likely')", (calibration,))
    job_row = conn.execute("SELECT id FROM jobs").fetchone()[0]
    conn.execute(
        "INSERT INTO assessments(job_id, run_id, bucket, reason, created_at) "
        "VALUES(?,?,'strong','fits','x')", (job_row, calibration))
    conn.execute("INSERT INTO decisions(job_id, kind, decided_at) "
                 "VALUES(?,'reject','x')", (job_row,))
    conn.commit()

    assert rows_from_db(conn) == []
