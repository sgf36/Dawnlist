"""Schema tests — the constraints that replace documented procedures."""
import sqlite3

import pytest

from app.core import db


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.sqlite3")
    db.migrate(c)
    yield c
    c.close()


def _job(conn, jid="1", company="Acme", title="Strategy Lead"):
    return conn.execute(
        "INSERT INTO jobs(provider, provider_job_id, title, company) "
        "VALUES('theirstack',?,?,?)", (jid, title, company)).lastrowid


# -- invariant 13 / spec 6.5 ------------------------------------------------
def test_output_cannot_exist_without_a_run(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO run_outputs(run_id, path, kind, created_at) "
            "VALUES(9999, 'x.xlsx', 'review', '2026-09-06')")


def test_orphan_check_uses_all_outputs_not_the_pending_list(conn, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    registered = out / "review-2026-09-06.xlsx"
    stranded = out / "review-2026-09-06 (1).xlsx"
    registered.write_text("x")
    stranded.write_text("x")

    with db.run(conn) as r:
        r.register_output(registered)

    orphans = db.orphan_outputs(conn, out)
    assert orphans == [stranded]


# -- spec 6.2 / 6.4 ---------------------------------------------------------
def test_a_crashed_run_is_recorded_as_failed_never_as_normal(conn):
    with pytest.raises(ValueError):
        with db.run(conn) as r:
            run_id = r.id
            raise ValueError("boom")
    row = conn.execute("SELECT status, incomplete_note FROM runs WHERE id=?",
                       (run_id,)).fetchone()
    assert row["status"] == "failed"
    assert "boom" in row["incomplete_note"]


def test_empty_fetch_is_flagged_loudly(conn):
    with db.run(conn) as r:
        r.record_fetch_failure("provider returned 0 rows and HTTP 502")
        r.record_counts(swept=0)
    row = conn.execute("SELECT fetch_failed, fetch_error, swept FROM runs").fetchone()
    assert row["fetch_failed"] == 1 and row["swept"] == 0
    assert "502" in row["fetch_error"]


def test_incomplete_run_reports_counts_left_unread(conn):
    with db.run(conn) as r:
        r.finish(left_unread=17, note="context exhausted")
    row = conn.execute("SELECT status, left_unread FROM runs").fetchone()
    assert row["status"] == "incomplete" and row["left_unread"] == 17


def test_unknown_funnel_counter_is_rejected(conn):
    with db.run(conn) as r:
        with pytest.raises(ValueError, match="unknown funnel counters"):
            r.record_counts(swept=10, shortlisted=3)


# -- spec 6.6 ---------------------------------------------------------------
def test_dedup_is_by_provider_job_id(conn):
    _job(conn, "abc")
    with pytest.raises(sqlite3.IntegrityError):
        _job(conn, "abc")


def test_same_company_different_role_both_survive(conn):
    _job(conn, "a", company="Acme", title="Strategy Lead")
    _job(conn, "b", company="Acme", title="Revenue Lead")
    assert conn.execute("SELECT count(*) c FROM jobs").fetchone()["c"] == 2


# -- spec 9.3 ---------------------------------------------------------------
def test_only_one_live_draft_per_recipient_per_thread(conn):
    opp = conn.execute(
        "INSERT INTO opportunities(company, created_at) VALUES('Acme','x')").lastrowid
    c = conn.execute(
        "INSERT INTO contacts(opportunity_id, name, created_at) "
        "VALUES(?, 'Jo', 'x')", (opp,)).lastrowid
    conn.execute("INSERT INTO drafts(opportunity_id, contact_id, thread_key, path,"
                 " created_at) VALUES(?,?,'t1','a.eml','x')", (opp, c))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO drafts(opportunity_id, contact_id, thread_key,"
                     " path, created_at) VALUES(?,?,'t1','b.eml','x')", (opp, c))


def test_superseding_a_draft_frees_the_slot(conn):
    opp = conn.execute(
        "INSERT INTO opportunities(company, created_at) VALUES('Acme','x')").lastrowid
    c = conn.execute("INSERT INTO contacts(opportunity_id, name, created_at) "
                     "VALUES(?, 'Jo', 'x')", (opp,)).lastrowid
    conn.execute("INSERT INTO drafts(opportunity_id, contact_id, thread_key, path,"
                 " created_at) VALUES(?,?,'t1','a.eml','x')", (opp, c))
    conn.execute("UPDATE drafts SET superseded=1 WHERE thread_key='t1'")
    conn.execute("INSERT INTO drafts(opportunity_id, contact_id, thread_key, path,"
                 " created_at) VALUES(?,?,'t1','b.eml','x')", (opp, c))
    live = conn.execute("SELECT count(*) c FROM drafts WHERE superseded=0").fetchone()
    assert live["c"] == 1


# -- spec 9.5 ---------------------------------------------------------------
def test_one_live_opportunity_per_employer(conn):
    conn.execute("INSERT INTO opportunities(company, created_at) VALUES('Acme','x')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO opportunities(company, created_at) VALUES('Acme','x')")


def test_a_closed_opportunity_frees_the_employer(conn):
    conn.execute("INSERT INTO opportunities(company, created_at, closed_at) "
                 "VALUES('Acme','x','2026-01-01')")
    conn.execute("INSERT INTO opportunities(company, created_at) VALUES('Acme','x')")
    assert conn.execute("SELECT count(*) c FROM opportunities").fetchone()["c"] == 2


# -- spec 4 / rejections are permanent --------------------------------------
def test_rejections_have_no_expiry_column(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(decisions)")}
    assert "expires_at" not in cols


def test_seen_jobs_roll_off_but_decisions_do_not(conn):
    conn.execute("INSERT INTO seen_jobs VALUES('theirstack','old','2020-01-01T00:00:00+00:00')")
    conn.execute("INSERT INTO seen_jobs VALUES('theirstack','new','2999-01-01T00:00:00+00:00')")
    jid = _job(conn, "z")
    conn.execute("INSERT INTO decisions(job_id, kind, decided_at) "
                 "VALUES(?, 'reject', '2020-01-01')", (jid,))
    conn.commit()
    assert db.prune_seen(conn) == 1
    assert conn.execute("SELECT count(*) c FROM seen_jobs").fetchone()["c"] == 1
    assert conn.execute("SELECT count(*) c FROM decisions").fetchone()["c"] == 1


# -- spec 6.7 ---------------------------------------------------------------
def test_assessment_can_record_unchecked_requirement(conn):
    jid = _job(conn, "q")
    with db.run(conn) as r:
        conn.execute(
            "INSERT INTO assessments(job_id, run_id, bucket, reason,"
            " requirement_checked, created_at) VALUES(?,?,?,?,0,'x')",
            (jid, r.id, "possible", "requirement could not be fetched"))
    row = conn.execute("SELECT requirement_checked, bucket FROM assessments").fetchone()
    assert row["requirement_checked"] == 0
    assert row["bucket"] != "rejected", "unfetchable is 'not checked', never a fail"


def test_bucket_is_constrained(conn):
    jid = _job(conn, "w")
    with db.run(conn) as r:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO assessments(job_id, run_id, bucket, reason,"
                         " created_at) VALUES(?,?, 'maybe', 'x', 'x')", (jid, r.id))


# -- the board (tracker persistence) ----------------------------------------
def _opp(conn, company="Acme", stage=0, **kw):
    cols = "company, stage, created_at" + ("".join(f", {k}" for k in kw))
    marks = "?, ?, 'x'" + ("".join(", ?" for _ in kw))
    return conn.execute(f"INSERT INTO opportunities({cols}) VALUES({marks})",
                        (company, stage, *kw.values())).lastrowid


def test_one_live_opportunity_per_employer(conn):
    _opp(conn, "Acme", 1)
    with pytest.raises(sqlite3.IntegrityError):
        _opp(conn, "Acme", 0)


def test_a_lost_opportunity_frees_the_employer(conn):
    _opp(conn, "Acme", 7)          # Lost
    _opp(conn, "Acme", 0)          # a fresh one is allowed
    assert conn.execute("SELECT count(*) c FROM opportunities").fetchone()["c"] == 2


def test_on_hold_does_not_free_the_employer(conn):
    """On Hold is paused, not dead - it still occupies the employer slot."""
    _opp(conn, "Acme", 8)
    with pytest.raises(sqlite3.IntegrityError):
        _opp(conn, "Acme", 0)


def test_stage_is_range_checked(conn):
    with pytest.raises(sqlite3.IntegrityError):
        _opp(conn, "Bad", 9)


def test_opportunity_may_be_nested_at_any_depth(conn):
    top = _opp(conn, "Highgate", 1)
    mid = _opp(conn, "Highgate Mid", 1, parent_id=top)
    deep = _opp(conn, "Mandarin Oriental", 1, parent_id=mid)
    row = conn.execute("SELECT stage, parent_id FROM opportunities WHERE id=?",
                       (deep,)).fetchone()
    assert row["stage"] == 1 and row["parent_id"] == mid


def test_at_most_one_open_task_per_opportunity(conn):
    o = _opp(conn)
    conn.execute("INSERT INTO tasks(opportunity_id, title, status, created_at)"
                 " VALUES(?, 'chase', 'open', 'x')", (o,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO tasks(opportunity_id, title, status, created_at)"
                     " VALUES(?, 'chase again', 'waiting', 'x')", (o,))


def test_completing_a_task_frees_the_slot_and_needs_evidence(conn):
    o = _opp(conn)
    conn.execute("INSERT INTO tasks(opportunity_id, title, status, created_at)"
                 " VALUES(?, 'chase', 'open', 'x')", (o,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE tasks SET status='complete' WHERE opportunity_id=?", (o,))
    conn.execute("UPDATE tasks SET status='complete', closed_evidence='letter posted'"
                 " WHERE opportunity_id=?", (o,))
    conn.execute("INSERT INTO tasks(opportunity_id, title, status, created_at)"
                 " VALUES(?, 'next step', 'open', 'x')", (o,))
    live = conn.execute("SELECT count(*) c FROM tasks WHERE status IN"
                        " ('open','waiting')").fetchone()
    assert live["c"] == 1
