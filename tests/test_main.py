"""The entry point. No network: provider and send are injected."""
import json
from datetime import date, datetime, timezone

import pytest

from app.core import db
from app.core.board_repo import create_opportunity
from app.core.tracker import Stage
from app.feed.base import FeedProvider, FetchResult
from app.feed.models import Job
from app.main import (NotConfigured, load_queries, load_rules, main,
                      mark_queries_run, morning_run)

TUE = date(2026, 9, 8)


@pytest.fixture()
def dbfile(tmp_path):
    return tmp_path / "t.sqlite3"


@pytest.fixture()
def conn(dbfile):
    c = db.connect(dbfile)
    db.migrate(c)
    yield c
    c.close()


def seed(conn, *, brief="Roles in hospitality strategy.", queries=True,
         calibrated=True):
    if brief:
        conn.execute("INSERT INTO documents(kind, version, body, created_at)"
                     " VALUES('fit_brief', 1, ?, 'x')", (brief,))
        conn.execute("INSERT INTO documents(kind, version, body, created_at)"
                     " VALUES('factsheet', 1, 'Spencer Fields.', 'x')")
    if queries:
        conn.execute(
            "INSERT INTO queries(label, params_json, created_at) VALUES(?,?,?)",
            ("strategy", json.dumps({"titles": ["strategy"],
                                     "countries": ["GB"]}), "x"))
    conn.execute("INSERT INTO rule_terms(field, term, added_at)"
                 " VALUES('strong_terms', 'strategy', 'x')")
    if calibrated:
        conn.execute("INSERT INTO settings(key, value) "
                     "VALUES('calibration_passed_at', '2026-09-06T00:00:00+00:00')")
    conn.commit()


class Stub(FeedProvider):
    name = "theirstack"

    def __init__(self, result):
        self.result = result
        self.seen: list = []

    def search(self, query):
        self.seen.append(query)
        return self.result

    def credits_used(self):
        return 0


def job(jid, title="Head of Strategy"):
    return Job(provider="theirstack", provider_job_id=jid, title=title,
               company="Acme", description_text="A strategy role.")


def strong_send(request):
    import re
    refs = re.findall(r'ref="([^"]+)"', request["messages"][0]["content"])
    return {"verdicts": [{"job_ref": r, "bucket": "strong", "reason": "fits",
                          "disqualifying_quote": None,
                          "requirement_checked": True} for r in refs]}


def ok(jobs):
    return FetchResult(jobs=jobs, pages_fetched=1, exhausted=True)


# -- configuration gates ----------------------------------------------------
def test_a_run_without_a_brief_is_refused_loudly(conn):
    seed(conn, brief="")
    with pytest.raises(NotConfigured, match="No fit brief"):
        morning_run(conn, provider=Stub(ok([job("a")])), send=strong_send)


def test_a_run_without_queries_is_refused(conn):
    seed(conn, queries=False)
    with pytest.raises(NotConfigured, match="No saved queries"):
        morning_run(conn, provider=Stub(ok([job("a")])), send=strong_send)


# -- the run ----------------------------------------------------------------
def test_a_clean_run_persists_and_advances_the_delta_mark(conn):
    seed(conn)
    outcome = morning_run(conn, provider=Stub(ok([job("a")])), send=strong_send)
    assert outcome.complete
    assert conn.execute("SELECT count(*) c FROM jobs").fetchone()["c"] == 1
    row = conn.execute("SELECT last_discovered_at FROM queries").fetchone()
    assert row["last_discovered_at"], "the delta high-water mark must advance"


def test_a_failed_fetch_does_not_advance_the_delta_mark(conn):
    """Advancing it would skip the window the failed run never actually read."""
    seed(conn)
    bad = FetchResult(jobs=[], error="HTTP 502")
    morning_run(conn, provider=Stub(bad), send=strong_send)
    row = conn.execute("SELECT last_discovered_at FROM queries").fetchone()
    assert row["last_discovered_at"] is None


def test_the_delta_mark_is_passed_back_on_the_next_run(conn):
    seed(conn)
    stub = Stub(ok([job("a")]))
    morning_run(conn, provider=stub, send=strong_send)
    morning_run(conn, provider=stub, send=strong_send)
    assert stub.seen[0].discovered_since is None
    assert stub.seen[1].discovered_since is not None, (
        "re-fetching yesterday's postings is re-buying them")


def test_a_seen_posting_is_deduped_on_the_next_run(conn):
    """The short-term layer: seen_jobs stops a recurring alert re-listing."""
    seed(conn)
    morning_run(conn, provider=Stub(ok([job("a")])), send=strong_send)
    outcome = morning_run(conn, provider=Stub(ok([job("a")])), send=strong_send)
    assert outcome.funnel()["deduped"] == 0
    assert outcome.funnel()["assessed"] == 0


def test_a_rejection_still_gates_after_the_seen_window_expires(conn):
    """The two layers are NOT the same thing, and this is where they separate.

    `seen_jobs` rolls off after ~45 days so a recurring alert cannot re-list
    forever and the table cannot grow unbounded. `decisions` never expires. So
    once the rolling window has aged out, the permanent reject gate is what
    keeps a rejected posting from being assessed again — which is what makes
    rejections compound run on run.
    """
    from app.ui.adapter import record_decision
    seed(conn)
    morning_run(conn, provider=Stub(ok([job("a")])), send=strong_send)
    record_decision(conn, "theirstack:a", "reject")

    # Age the rolling dedup out, leaving only the permanent decision.
    conn.execute("UPDATE seen_jobs SET seen_at = '2020-01-01T00:00:00+00:00'")
    conn.commit()
    assert db.prune_seen(conn) == 1

    outcome = morning_run(conn, provider=Stub(ok([job("a")])), send=strong_send)
    assert outcome.funnel()["gated_out"] == 1
    assert outcome.funnel()["assessed"] == 0, (
        "a rejection is a posting never assessed again")


# -- rules loading ----------------------------------------------------------
def test_an_invalid_stored_kill_family_is_skipped_loudly(conn, capsys):
    seed(conn)
    conn.execute(
        """INSERT INTO kill_families(name, employers_json, kill_json,
               saves_json, precedents_json, adopted, created_at)
           VALUES('broken', '["A"]', '["ops"]', '["data"]', '[]', 1, 'x')""")
    conn.commit()
    table = load_rules(conn)
    assert table.kill_families == []
    assert "not loaded" in capsys.readouterr().err


def test_a_valid_stored_kill_family_loads(conn):
    seed(conn)
    conn.execute(
        """INSERT INTO kill_families(name, employers_json, kill_json,
               saves_json, precedents_json, adopted, created_at)
           VALUES('qsr', '["Burgerly"]', '["operations"]', '["strategy"]',
                  '[["Burgerly","Ops"],["Burgerly","Shift"]]', 1, 'x')""")
    conn.commit()
    assert len(load_rules(conn).kill_families) == 1


# -- CLI --------------------------------------------------------------------
def test_audit_prints_and_exits_zero(dbfile, capsys):
    c = db.connect(dbfile)
    db.migrate(c)
    create_opportunity(c, "Acme", stage=Stage.CONTACTED)
    c.close()
    assert main(["--audit", "--db", str(dbfile)]) == 0
    assert "1 opportunities checked" in capsys.readouterr().out


def test_run_once_without_configuration_exits_two(dbfile, capsys):
    c = db.connect(dbfile)
    db.migrate(c)
    c.close()
    assert main(["--run-once", "--db", str(dbfile)]) == 2
    assert "not configured" in capsys.readouterr().err


def test_non_ascii_survives_the_console(dbfile, capsys):
    """The cp1252 trap: a run died on the first en-dash AFTER the credits were
    spent. Both streams are reconfigured, not just stdout."""
    c = db.connect(dbfile)
    db.migrate(c)
    create_opportunity(c, "Rocco Forte — Hôtel de Rome", stage=Stage.CONTACTED)
    c.execute("UPDATE opportunities SET status_mirror='open'")
    c.commit()
    c.close()
    assert main(["--audit", "--db", str(dbfile)]) == 0
    out = capsys.readouterr().out
    assert "Hôtel de Rome" in out and "�" not in out


def test_an_uncalibrated_run_is_refused_at_the_door(conn):
    """The gate is enforced in morning_run, not in a screen.

    A gate enforced in the UI is a gate the scheduled run walks straight past,
    and the handoff is explicit: no daily runs before calibration.
    """
    seed(conn, calibrated=False)
    with pytest.raises(NotConfigured, match="Calibration has not been completed"):
        morning_run(conn, provider=Stub(ok([job("a")])), send=strong_send)


def test_run_once_exits_two_when_uncalibrated(dbfile, capsys):
    c = db.connect(dbfile)
    db.migrate(c)
    seed(c, calibrated=False)
    c.close()
    assert main(["--run-once", "--db", str(dbfile)]) == 2
    assert "Calibration" in capsys.readouterr().err


def test_doctor_reports_the_locale_catalogues(dbfile, capsys):
    """A frozen build fails differently: a resource read by path can simply be
    absent from the bundle and the app degrades quietly. This is the check."""
    c = db.connect(dbfile)
    db.migrate(c)
    c.close()
    assert main(["--doctor", "--db", str(dbfile)]) == 0
    out = capsys.readouterr().out
    assert "locales exist : True" in out
    assert "catalogues" in out and "en" in out
    assert "calibrated    : False" in out
