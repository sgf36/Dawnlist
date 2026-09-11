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


# -- spec 8.2: a bounce clears the address, structurally --------------------
def test_a_bounced_contact_cannot_retain_an_address(conn):
    o = _opp(conn)
    conn.execute("INSERT INTO contacts(opportunity_id, name, email, created_at)"
                 " VALUES(?, 'Jo', 'jo@dead.example', 'x')", (o,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE contacts SET email_bounced = 1")


def test_record_bounce_clears_and_flags_together(conn):
    o = _opp(conn)
    cid = conn.execute("INSERT INTO contacts(opportunity_id, name, email,"
                       " created_at) VALUES(?, 'Jo', 'jo@dead.example', 'x')",
                       (o,)).lastrowid
    db.record_bounce(conn, cid)
    row = conn.execute("SELECT email, email_bounced FROM contacts WHERE id=?",
                       (cid,)).fetchone()
    assert row["email"] is None and row["email_bounced"] == 1


# -- numbered migrations ----------------------------------------------------
def _version_one(path):
    """An install from before migrations existed: the shipped schema and the
    version number it wrote, and nothing else."""
    c = db.connect(path)
    c.executescript(db.SCHEMA)
    c.execute("INSERT INTO settings(key, value) VALUES('schema_version', '1')")
    c.commit()
    return c


def _columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_an_installed_version_one_database_is_brought_up_to_date(tmp_path):
    """`SCHEMA` is CREATE IF NOT EXISTS, which cannot add a column to a table
    someone already has, and `schema_version` was written and never read — so
    no installed database could ever change shape."""
    c = _version_one(tmp_path / "old.sqlite3")
    assert "description_pruned_at" not in _columns(c, "jobs"), "positive control"

    db.migrate(c)
    assert "description_pruned_at" in _columns(c, "jobs")
    assert db.schema_version(c) == db.SCHEMA_VERSION

    db.migrate(c)                       # a second start changes nothing
    assert db.schema_version(c) == db.SCHEMA_VERSION
    c.close()


def test_a_database_newer_than_the_build_is_not_lowered(conn):
    """The old migrate wrote its own version on every start, so an older build
    opening a newer database quietly claimed the newer steps had not run."""
    conn.execute("UPDATE settings SET value='999' WHERE key='schema_version'")
    conn.commit()
    db.migrate(conn)
    assert db.schema_version(conn) == 999


def test_a_failed_step_leaves_neither_a_half_change_nor_a_new_version(tmp_path,
                                                                    monkeypatch):
    c = _version_one(tmp_path / "old.sqlite3")
    monkeypatch.setattr(db, "MIGRATIONS", ((2, (
        "ALTER TABLE jobs ADD COLUMN half_done TEXT",
        "ALTER TABLE no_such_table ADD COLUMN x TEXT",
    )),))
    with pytest.raises(sqlite3.OperationalError):
        db.migrate(c)
    assert "half_done" not in _columns(c, "jobs")
    assert db.schema_version(c) == 1, "the next start must retry the step"
    c.close()


# -- old descriptions nobody decided on are cleared, the rows kept ----------
def test_old_undecided_descriptions_are_cleared_and_everything_else_kept(conn):
    """Every swept posting's full description was kept for the life of the
    install, although only one the user decides on is read again."""
    old = conn.execute("INSERT INTO runs(started_at) "
                       "VALUES('2020-01-01T00:00:00+00:00')").lastrowid
    recent = conn.execute("INSERT INTO runs(started_at) VALUES(?)",
                          (db._now(),)).lastrowid

    def posting(jid, run, verdict="likely"):
        return conn.execute(
            "INSERT INTO jobs(provider, provider_job_id, title, company, "
            "description_text, first_seen_run, screen_verdict) "
            "VALUES('theirstack', ?, 't', 'c', 'The full text.', ?, ?)",
            (jid, run, verdict)).lastrowid

    posting("old-out", old, "unlikely")
    posting("old-undecided", old)
    posting("recent", recent)
    decided = posting("old-decided", old)
    on_board = posting("old-on-board", old)
    posting("pasted", None)
    conn.execute("INSERT INTO decisions(job_id, kind, decided_at) "
                 "VALUES(?, 'reject', 'x')", (decided,))
    conn.execute("INSERT INTO opportunities(company, job_id, created_at) "
                 "VALUES('c', ?, 'x')", (on_board,))
    conn.commit()

    assert db.prune_descriptions(conn) == 2
    rows = {r["provider_job_id"]: r for r in conn.execute(
        "SELECT provider_job_id, description_text, description_pruned_at "
        "FROM jobs")}
    assert len(rows) == 6, "rows are never erased"
    for cleared in ("old-out", "old-undecided"):
        assert rows[cleared]["description_text"] == ""
        assert rows[cleared]["description_pruned_at"]
    # Positive controls: everything the user may still need keeps its text.
    for kept in ("recent", "old-decided", "old-on-board", "pasted"):
        assert rows[kept]["description_text"] == "The full text."
        assert rows[kept]["description_pruned_at"] is None


def test_an_installed_database_gains_the_model_calls_table(tmp_path):
    """Usage has somewhere to go on an install that predates it."""
    c = _version_one(tmp_path / "old.sqlite3")

    def tables():
        return {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}

    assert "model_calls" not in tables(), "positive control"
    db.migrate(c)
    assert "model_calls" in tables()
    c.close()
