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


def test_only_the_query_that_fetched_advances(conn):
    """Every enabled query was stamped together, and only when no query had
    any problem — so one failing search froze every other search's window."""
    seed(conn)
    conn.execute(
        "INSERT INTO queries(label, params_json, created_at) VALUES(?,?,?)",
        ("revenue", json.dumps({"titles": ["revenue"], "countries": ["GB"]}), "x"))
    conn.commit()

    class ByLabel(Stub):
        def search(self, query):
            self.seen.append(query)
            if query.label == "revenue":
                return FetchResult(jobs=[], error="HTTP 502")
            return ok([job("a")])

    morning_run(conn, provider=ByLabel(None), send=strong_send)
    marks = {r["label"]: r["last_discovered_at"]
             for r in conn.execute("SELECT label, last_discovered_at FROM queries")}
    assert marks["strategy"], "the search that fetched moves on"
    assert marks["revenue"] is None, "the one that failed keeps its window"


def test_a_posting_left_unjudged_is_read_on_the_next_run(conn):
    """A likely posting whose batch failed or was never sent stayed unread for
    good: it was held, so every later fetch deduped it, and nothing queued it
    again."""
    seed(conn)

    def skips(_request):
        return {"verdicts": []}

    first = morning_run(conn, provider=Stub(ok([job("7")])), send=skips)
    assert [j.provider_job_id for j in first.assessment.unread] == ["7"], (
        "positive control: the first run really did leave it unjudged")

    second = morning_run(conn, provider=Stub(ok([])), send=strong_send)
    assert [v.job.provider_job_id for v in second.assessment.verdicts] == ["7"]
    assert second.funnel()["requeued"] == 1

    # Judged now, so it is not queued (or paid for) a third time.
    third = morning_run(conn, provider=Stub(ok([])), send=strong_send)
    assert third.assessment.verdicts == [] and not third.requeued


def test_a_morning_run_clears_old_undecided_descriptions(conn):
    """Nothing ever removed a swept posting's text, so the table grew for the
    life of the install. The run now clears the old undecided ones."""
    seed(conn)
    old = conn.execute("INSERT INTO runs(started_at) "
                       "VALUES('2020-01-01T00:00:00+00:00')").lastrowid
    conn.execute("INSERT INTO jobs(provider, provider_job_id, title, company, "
                 "description_text, first_seen_run, screen_verdict) "
                 "VALUES('theirstack', 'old', 'Old role', 'Acme', 'Long ago.', "
                 "?, 'unlikely')", (old,))
    conn.commit()

    morning_run(conn, provider=Stub(ok([job("new")])), send=strong_send)
    text = {r["provider_job_id"]: r["description_text"] for r in conn.execute(
        "SELECT provider_job_id, description_text FROM jobs")}
    assert text["old"] == ""
    assert text["new"] == "A strategy role.", (
        "positive control: today's posting keeps its text")


def test_the_morning_run_uses_the_transport_that_reports_stop_reasons(
        conn, monkeypatch):
    """`build_send` returns bare text, so a reply cut off at max_tokens or
    refused reached the assessment looking like a malformed payload."""
    import app.intelligence.assess as assess_mod
    import app.main as main_mod

    seed(conn)
    used = []

    def transport(key=None, **_kw):
        used.append(key)
        return lambda request: assess_mod.ModelReply(
            text=json.dumps(strong_send(request)), stop_reason="end_turn")

    def text_only(_conn):
        raise AssertionError("the text-only transport was used for assessment")

    monkeypatch.setattr("app.core.api_key.require", lambda: "sk-user")
    monkeypatch.setattr(assess_mod, "anthropic_transport", transport)
    monkeypatch.setattr(main_mod, "build_send", text_only)

    outcome = morning_run(conn, provider=Stub(ok([job("a")])))
    assert used == ["sk-user"], "the user's own key reaches the transport"
    assert [v.bucket for v in outcome.assessment.verdicts] == ["strong"]


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


def test_the_first_pursue_does_not_hide_every_other_employer(conn):
    """End to end, on the route every user takes: run, pursue one role, run
    again. Known employers are derived from that pursue, and counting them as
    the screen's positive signal made every later posting at any other
    employer "no matching term" — never assessed."""
    from app.ui.adapter import record_decision

    seed(conn)
    conn.execute("DELETE FROM rule_terms")          # a user who typed no terms
    conn.commit()

    class Sequence(Stub):
        def __init__(self, *results):
            super().__init__(None)
            self.results = list(results)

        def search(self, query):
            self.seen.append(query)
            return self.results.pop(0)

    fs = Job(provider="theirstack", provider_job_id="1", title="Director of Rooms",
             company="Four Seasons", description_text="A hotel role.")
    rosewood = Job(provider="theirstack", provider_job_id="2",
                   title="General Manager", company="Rosewood Hotels",
                   description_text="A hotel role.")
    mandarin = Job(provider="theirstack", provider_job_id="3",
                   title="Hotel Manager", company="Mandarin Oriental",
                   description_text="A hotel role.")

    feed = Sequence(ok([fs]), ok([rosewood, mandarin]))
    morning_run(conn, provider=feed, send=strong_send)
    record_decision(conn, "theirstack:1", "pursue")
    assert load_rules(conn).known_employers == ["Four Seasons"], (
        "positive control: the pursue really did make an employer known")

    outcome = morning_run(conn, provider=feed, send=strong_send)
    assessed = {v.job.company for v in outcome.assessment.verdicts}
    assert assessed == {"Rosewood Hotels", "Mandarin Oriental"}
    assert outcome.funnel()["screened_out"] == 0


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


def test_an_uncalibrated_launch_routes_to_onboarding(dbfile, monkeypatch):
    """An empty shortlist with no explanation reads as a broken app rather
    than an unfinished setup."""
    import app.main as main_mod

    c = db.connect(dbfile)
    db.migrate(c)

    routed = []
    monkeypatch.setattr(main_mod, "_launch_onboarding",
                        lambda app, conn: routed.append("onboarding") or 0)
    monkeypatch.setattr("PySide6.QtWidgets.QApplication.exec", lambda self: 0)
    main_mod._launch_ui(c, open_board=False)
    assert routed == ["onboarding"]
    c.close()


def test_a_calibrated_launch_goes_straight_to_the_shortlist(dbfile, monkeypatch):
    import app.main as main_mod

    c = db.connect(dbfile)
    db.migrate(c)
    c.execute("INSERT INTO settings(key, value) "
              "VALUES('calibration_passed_at','2026-09-06T00:00:00+00:00')")
    c.commit()

    routed = []
    monkeypatch.setattr(main_mod, "_launch_onboarding",
                        lambda app, conn: routed.append("onboarding") or 0)
    monkeypatch.setattr("PySide6.QtWidgets.QApplication.exec", lambda self: 0)
    main_mod._launch_ui(c, open_board=False)
    assert routed == [], "a calibrated user must not be sent back through setup"
    c.close()


# ---------------------------------------------------------------------------
# The billing control: rows we already hold must not be re-bought
# ---------------------------------------------------------------------------

def test_held_job_ids_are_sent_so_they_are_not_re_bought(tmp_path):
    """The provider does not cache: a row we hold is re-bought if it returns.

    The Worker has honoured `excludeJobIds` since it was written and the app
    never sent it, so the control did nothing. It matters most on the day the
    delta mark fails to advance — deliberately, after a failed fetch — because
    the next run then re-requests the same window.
    """
    from app.core import db
    from app.main import RECENT_HELD_IDS, load_queries

    conn = db.connect(":memory:")
    db.migrate(conn)
    conn.execute(
        "INSERT INTO queries(label, params_json, enabled, created_at) "
        "VALUES('q','{\"countries\":[\"GB\"]}',1,datetime('now'))")
    # Numeric, because TheirStack issues integer ids. These tests used strings
    # like "held-0" that no feed ever issues, which is how a list full of ids
    # the feed could not use went unnoticed.
    for i in range(3):
        conn.execute(
            "INSERT INTO jobs(provider, provider_job_id, title, company) "
            "VALUES('theirstack', ?, 't', 'c')", (str(101 + i),))
    conn.commit()

    q = load_queries(conn)[0]
    assert set(q.exclude_job_ids) == {"101", "102", "103"}


def test_only_the_feeds_own_numeric_ids_are_excluded():
    """`job_id_not` is typed as integers, and `jobs` also holds postings the
    feed never issued. A pasted advert's id is a hash, and an alert email's is
    LinkedIn's number — which would quietly exclude whichever TheirStack
    posting shares it."""
    from app.core import db
    from app.main import load_queries

    conn = db.connect(":memory:")
    db.migrate(conn)
    conn.execute(
        "INSERT INTO queries(label, params_json, enabled, created_at) "
        "VALUES('q','{\"countries\":[\"GB\"]}',1,datetime('now'))")
    conn.executemany(
        "INSERT INTO jobs(provider, provider_job_id, title, company) "
        "VALUES(?, ?, 't', 'c')",
        [("theirstack", "4711"),                          # the feed's own
         ("pasted", "p3f9a0c1d2e4b5a6978c1"),             # a hash of a paste
         ("alert-email", "4200"),                         # LinkedIn's number
         ("theirstack", "https://example.com/jobs/9")])   # url fallback id
    conn.commit()

    q = load_queries(conn)[0]
    # Positive control and the fix in one: the feed's id survives, nothing
    # else does.
    assert q.exclude_job_ids == ("4711",)


def test_the_exclusion_list_is_bounded(tmp_path):
    """It travels in every request body, so it cannot grow without limit."""
    from app.core import db
    from app.main import RECENT_HELD_IDS, load_queries

    conn = db.connect(":memory:")
    db.migrate(conn)
    conn.execute(
        "INSERT INTO queries(label, params_json, enabled, created_at) "
        "VALUES('q','{\"countries\":[\"GB\"]}',1,datetime('now'))")
    conn.executemany(
        "INSERT INTO jobs(provider, provider_job_id, title, company) "
        "VALUES('theirstack', ?, 't', 'c')",
        [(str(10_000 + i),) for i in range(RECENT_HELD_IDS + 250)])
    conn.commit()

    q = load_queries(conn)[0]
    assert len(q.exclude_job_ids) == RECENT_HELD_IDS
    # Newest first: the most recently inserted ids are the ones most likely to
    # come back, so those are the ones worth excluding.
    assert str(10_000 + RECENT_HELD_IDS + 249) in q.exclude_job_ids
    assert "10000" not in q.exclude_job_ids
