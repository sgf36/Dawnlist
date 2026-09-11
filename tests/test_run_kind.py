"""Which runs are the daily search, and which one the window reports.

Outreach runs and job-alert imports open `runs` rows too. Before `runs.kind`
nothing could tell them from a search, so "has a run started today" would have
counted drafting follow-ups as the morning's search, and a failed search — which
sweeps nothing — was skipped in favour of yesterday's shortlist (PIPELINE-P6).
"""
import sqlite3

import pytest

from app.core import db
from app.core.schedule import SWEEP
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


# -- the migration ----------------------------------------------------------

def test_an_existing_database_gains_the_column(tmp_path):
    """Every install from before this change has a `runs` table already, and
    CREATE TABLE IF NOT EXISTS leaves it exactly as it was."""
    path = tmp_path / "old.sqlite3"
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, started_at TEXT "
                "NOT NULL, status TEXT NOT NULL DEFAULT 'running', swept "
                "INTEGER NOT NULL DEFAULT 0)")
    raw.execute("INSERT INTO runs(started_at, swept) VALUES('x', 4)")
    raw.commit()
    raw.close()

    conn = db.connect(path)
    db.migrate(conn)
    db.migrate(conn)          # and a second launch does not try to add it again
    columns = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
    assert "kind" in columns
    assert conn.execute("SELECT kind FROM runs").fetchone()[0] is None, (
        "an old row cannot honestly be called a search")
    conn.close()


# -- tagging ----------------------------------------------------------------

def test_a_search_tags_its_own_row(conn):
    seed(conn)
    morning_run(conn, provider=Stub(ok([job("a")])), send=strong_send)
    assert kinds(conn) == [("complete", SWEEP)]


class Exploding(Stub):
    def search(self, query):
        raise RuntimeError("the feed fell over mid-run")


def test_a_search_that_crashes_is_still_todays_search(conn):
    """The refreshes it spent are gone, so it must count."""
    seed(conn)
    with pytest.raises(RuntimeError):
        morning_run(conn, provider=Exploding(ok([])), send=strong_send)
    assert kinds(conn) == [("failed", SWEEP)]


def test_a_refused_run_opens_no_row(conn):
    """Nothing was spent, so Run now must stay offered once it is fixed."""
    seed(conn, calibrated=False)
    with pytest.raises(NotConfigured):
        morning_run(conn, provider=Stub(ok([job("a")])), send=strong_send)
    assert kinds(conn) == []


def test_other_runs_are_left_untagged(conn):
    seed(conn)
    with db.run(conn):             # the shape of an outreach run
        pass
    morning_run(conn, provider=Stub(ok([job("a")])), send=strong_send)
    with db.run(conn):
        pass
    assert kinds(conn) == [("complete", None), ("complete", SWEEP),
                           ("complete", None)]


# -- which run the window reports -------------------------------------------

def add(conn, *, swept, kind, status="complete", note=None, error=None):
    cur = conn.execute(
        "INSERT INTO runs(started_at, status, swept, kind, incomplete_note, "
        "fetch_error) VALUES('2026-09-11T06:00:00+00:00',?,?,?,?,?)",
        (status, swept, kind, note, error))
    conn.commit()
    return cur.lastrowid


def test_a_failed_search_is_reported_rather_than_skipped(conn):
    add(conn, swept=40, kind=SWEEP)
    failed = add(conn, swept=0, kind=SWEEP, status="incomplete",
                 error="strategy: Could not reach the Dawnlist feed service.")
    assert latest_run_id(conn) == failed


def test_the_old_rule_would_have_hidden_it(conn):
    """Positive control: `swept > 0` alone picks yesterday's run."""
    yesterday = add(conn, swept=40, kind=SWEEP)
    add(conn, swept=0, kind=SWEEP, status="failed")
    old = conn.execute("SELECT MAX(id) FROM runs WHERE swept > 0").fetchone()[0]
    assert old == yesterday


def test_a_later_draft_run_still_does_not_empty_the_window(conn):
    search = add(conn, swept=40, kind=SWEEP)
    add(conn, swept=0, kind=None)
    assert latest_run_id(conn) == search


def test_job_alert_imports_and_old_rows_still_count_when_they_swept(conn):
    add(conn, swept=40, kind=SWEEP)
    alerts = add(conn, swept=3, kind=None)
    assert latest_run_id(conn) == alerts


def test_the_status_carries_what_went_wrong(conn):
    run_id = add(conn, swept=0, kind=SWEEP, status="incomplete",
                 note="strategy: Daily refresh cap reached (3)",
                 error="strategy: Daily refresh cap reached (3)")
    status = run_status(conn, run_id)
    assert status.went_wrong
    assert status.status == "incomplete"
    assert "refresh cap" in status.incomplete_note
    assert "refresh cap" in status.fetch_error

    clean = run_status(conn, add(conn, swept=5, kind=SWEEP))
    assert not clean.went_wrong
    assert run_status(conn, None) is None
