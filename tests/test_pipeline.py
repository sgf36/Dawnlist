"""End-to-end funnel. A stub provider and a stub `send`: no network, no spend."""
from datetime import date, timedelta

import pytest

from app.core import db
from app.core.pipeline import (Gate, permanent_reject_gate, persist,
                               posted_within_gate, run_morning)
from app.core.rules import RuleTable
from app.feed.base import FeedProvider, FetchResult, SearchQuery
from app.feed.models import Job


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.sqlite3")
    db.migrate(c)
    yield c
    c.close()


def job(jid, title="Head of Strategy", company="Acme", desc="A strategy role.",
        posted=None):
    return Job(provider="theirstack", provider_job_id=jid, title=title,
               company=company, description_text=desc, posted_at=posted)


class StubProvider(FeedProvider):
    name = "theirstack"

    def __init__(self, results):
        self.results = results

    def search(self, query):
        return self.results[query.label]

    def credits_used(self):
        return 0


def ok(jobs):
    return FetchResult(jobs=jobs, pages_fetched=1, exhausted=True,
                       credits_estimate=len(jobs))


def strong_send(request):
    import re
    refs = re.findall(r'ref="([^"]+)"', request["messages"][0]["content"])
    return {"verdicts": [{"job_ref": r, "bucket": "strong", "reason": "fits",
                          "disqualifying_quote": None,
                          "requirement_checked": True} for r in refs]}


RULES = RuleTable(strong_terms=["strategy"])
Q = SearchQuery(label="strategy", titles=["strategy"])


def test_happy_path_records_the_whole_funnel(conn):
    provider = StubProvider({"strategy": ok([job("a"), job("b")])})
    out = run_morning(conn, provider, [Q], RULES, fit_brief="b", factsheet="f",
                      send=strong_send)

    assert out.complete
    f = out.funnel()
    assert f["swept"] == 2 and f["screened_likely"] == 2 and f["assessed"] == 2

    row = conn.execute("SELECT * FROM runs WHERE id=?", (out.run_id,)).fetchone()
    assert row["status"] == "complete"
    assert row["swept"] == 2 and row["assessed"] == 2


def test_a_fetch_failure_is_never_reported_as_a_clean_run(conn):
    provider = StubProvider({"strategy": FetchResult(jobs=[], error="HTTP 502")})
    out = run_morning(conn, provider, [Q], RULES, fit_brief="b", factsheet="f",
                      send=strong_send)

    assert not out.complete
    assert out.fetch_errors
    row = conn.execute("SELECT * FROM runs WHERE id=?", (out.run_id,)).fetchone()
    assert row["fetch_failed"] == 1
    assert row["status"] == "incomplete", (
        "zero jobs plus an error is not a quiet morning")


def test_unexhausted_pagination_is_surfaced(conn):
    """A page cap hit silently looks exactly like 'nothing more there'."""
    provider = StubProvider({"strategy": FetchResult(
        jobs=[job("a")], pages_fetched=1, exhausted=False)})
    out = run_morning(conn, provider, [Q], RULES, fit_brief="b", factsheet="f",
                      send=strong_send)
    assert any("exhaustion" in e for e in out.fetch_errors)
    assert not out.complete


def test_screened_out_rows_are_counted_and_kept(conn):
    provider = StubProvider({"strategy": ok([job("a"), job("b", title="Kitchen Porter",
                                                          desc="washing up")])})
    out = run_morning(conn, provider, [Q], RULES, fit_brief="b", factsheet="f",
                      send=strong_send)
    f = out.funnel()
    assert f["screened_likely"] == 1 and f["screened_out"] == 1
    assert f["assessed"] == 1, "only survivors reach the model"

    persist(conn, out)
    kept = conn.execute("SELECT screen_verdict, screen_reason FROM jobs "
                        "WHERE provider_job_id='b'").fetchone()
    assert kept["screen_verdict"] == "unlikely" and kept["screen_reason"]


def test_permanent_rejects_are_gated_before_the_model(conn):
    provider = StubProvider({"strategy": ok([job("a"), job("b")])})
    gate = permanent_reject_gate({("theirstack", "a")})
    out = run_morning(conn, provider, [Q], RULES, fit_brief="b", factsheet="f",
                      send=strong_send, gates=[gate])
    assert out.funnel()["gated_out"] == 1
    assert out.funnel()["assessed"] == 1


def test_stale_postings_are_gated(conn):
    old = date.today() - timedelta(days=90)
    provider = StubProvider({"strategy": ok([job("a", posted=old), job("b")])})
    out = run_morning(conn, provider, [Q], RULES, fit_brief="b", factsheet="f",
                      send=strong_send, gates=[posted_within_gate(30)])
    assert out.funnel()["gated_out"] == 1


def test_duplicates_are_dropped_and_counted(conn):
    provider = StubProvider({"strategy": ok([job("a"), job("a")])})
    out = run_morning(conn, provider, [Q], RULES, fit_brief="b", factsheet="f",
                      send=strong_send)
    f = out.funnel()
    assert f["swept"] == 2 and f["deduped"] == 1


def test_yield_rate_identifies_the_loose_query(conn):
    noise = [job(f"n{i}", title="Kitchen Porter", desc="washing up")
             for i in range(19)]
    provider = StubProvider({"strategy": ok([job("a")] + noise)})
    out = run_morning(conn, provider, [Q], RULES, fit_brief="b", factsheet="f",
                      send=strong_send)
    assert out.per_query_yield["strategy"] == pytest.approx(0.05)
    assert "strategy" in out.loose_queries()


def test_an_unread_posting_makes_the_run_incomplete(conn):
    provider = StubProvider({"strategy": ok([job("a"), job("b")])})

    def skips_one(_request):
        return {"verdicts": [{"job_ref": "a", "bucket": "strong", "reason": "x",
                              "disqualifying_quote": None,
                              "requirement_checked": True}]}

    out = run_morning(conn, provider, [Q], RULES, fit_brief="b", factsheet="f",
                      send=skips_one)
    assert not out.complete
    row = conn.execute("SELECT status, left_unread FROM runs").fetchone()
    assert row["status"] == "incomplete" and row["left_unread"] == 1


def test_a_crash_mid_run_still_records_the_run_as_failed(conn):
    provider = StubProvider({"strategy": ok([job("a")])})

    class Boom(FeedProvider):
        name = "theirstack"

        def search(self, q):
            raise RuntimeError("network gone")

        def credits_used(self):
            return None

    with pytest.raises(RuntimeError):
        run_morning(conn, Boom(), [Q], RULES, fit_brief="b", factsheet="f",
                    send=strong_send)
    row = conn.execute("SELECT status, incomplete_note FROM runs").fetchone()
    assert row["status"] == "failed" and "network gone" in row["incomplete_note"]


def test_persist_writes_verdicts_and_marks_downgrades(conn):
    desc = "Must have a minimum of 10 years' experience."
    provider = StubProvider({"strategy": ok([job("a", desc=desc + " Strategy role.")])})

    def hallucinating(_r):
        return {"verdicts": [{"job_ref": "a", "bucket": "rejected",
                              "reason": "needs an MBA",
                              "disqualifying_quote": "must hold an MBA",
                              "requirement_checked": True}]}

    out = run_morning(conn, provider, [Q], RULES, fit_brief="b", factsheet="f",
                      send=hallucinating)
    persist(conn, out)
    row = conn.execute("SELECT bucket, reason FROM assessments").fetchone()
    assert row["bucket"] == "judgement-call"
    assert "downgraded" in row["reason"]


def test_a_run_output_written_but_not_registered_is_caught(conn, tmp_path):
    """The 101-row stranded-file failure, reproduced against the schema.

    The output directory must be dedicated to run outputs — the check reports
    every unregistered file in it, which is the point.
    """
    out_dir = tmp_path / "outputs"
    out_dir.mkdir()
    provider = StubProvider({"strategy": ok([job("a")])})
    outcome = run_morning(conn, provider, [Q], RULES, fit_brief="b",
                          factsheet="f", send=strong_send)

    registered = out_dir / "shortlist-2026-09-06.xlsx"
    registered.write_text("rows")
    conn.execute("INSERT INTO run_outputs(run_id, path, kind, created_at) "
                 "VALUES(?,?,?,?)",
                 (outcome.run_id, str(registered), "review", "now"))
    conn.commit()
    assert db.orphan_outputs(conn, out_dir) == []

    # The real failure: renamed to avoid a clash, never re-registered.
    stranded = out_dir / "shortlist-2026-09-06 (1).xlsx"
    stranded.write_text("rows including a strong match")
    assert db.orphan_outputs(conn, out_dir) == [stranded]


def test_a_failed_query_gets_no_yield_rate(conn):
    """The 58.1% bug: a P0 run counted rate-limit failures as coverage misses.

    A query whose fetch failed has partial rows. Scoring it would report a
    transport fault as a loose query — "not measured" is not "0%".
    """
    provider = StubProvider({
        "strategy": ok([job("a")]),
        "revenue": FetchResult(jobs=[job("b")], pages_fetched=1,
                               exhausted=False, error="HTTP 429 rate limited"),
    })
    qs = [Q, SearchQuery(label="revenue", titles=["revenue"])]
    out = run_morning(conn, provider, qs, RULES, fit_brief="b", factsheet="f",
                      send=strong_send)

    assert "revenue" not in out.per_query_yield
    assert "revenue" in out.unscored_queries
    assert "revenue" not in out.loose_queries(), (
        "a rate-limited query must never be reported as a loose query")
    assert "strategy" in out.per_query_yield


# ---------------------------------------------------------------------------
# Delta marks: per query, from the start of that query's own fetch
# ---------------------------------------------------------------------------

REVENUE = SearchQuery(label="revenue", titles=["revenue"])


def test_a_failing_query_does_not_freeze_the_others(conn):
    """One run-wide mark, advanced only when nothing went wrong, meant a
    single broken search re-requested every search's window every morning."""
    provider = StubProvider({"strategy": ok([job("a")]),
                             "revenue": FetchResult(jobs=[], error="HTTP 502")})
    out = run_morning(conn, provider, [Q, REVENUE], RULES, fit_brief="b",
                      factsheet="f", send=strong_send)
    assert "strategy" in out.marks, "the search that fetched moves on"
    assert "revenue" not in out.marks, "the one that failed keeps its window"


def test_an_oversize_query_advances_and_is_named_to_narrow(conn):
    """A search matching more than a page held its mark forever: every run
    asked for the same window again and never read past it."""
    wide = FetchResult(jobs=[job("a")], pages_fetched=1, exhausted=False,
                       matched=250, not_fetched=249)
    out = run_morning(conn, StubProvider({"strategy": wide}), [Q], RULES,
                      fit_brief="b", factsheet="f", send=strong_send)
    assert "strategy" in out.marks
    assert out.unfetched == {"strategy": 249}
    assert out.funnel()["not_fetched"] == 249
    assert any("strategy" in e and "narrow" in e for e in out.fetch_errors)
    assert not out.complete, "a search read only in part is still said to be"


def test_a_capped_query_keeps_its_mark(conn):
    """The plan's ceiling stopped it, not the search. Tomorrow's allowance
    reads the rest; advancing would have skipped it for good."""
    capped = FetchResult(jobs=[job("a")], pages_fetched=1, exhausted=False,
                         matched=80, not_fetched=79, capped=True)
    out = run_morning(conn, StubProvider({"strategy": capped}), [Q], RULES,
                      fit_brief="b", factsheet="f", send=strong_send)
    assert "strategy" not in out.marks
    assert out.unfetched == {}, "a cap is not a search to narrow"


def test_the_mark_is_when_that_querys_fetch_began(conn):
    """Stamped at the end of the run, the mark skipped every posting indexed
    while the run was still fetching other searches and assessing."""
    from datetime import datetime, timezone

    t = [datetime(2026, 9, 11, 7, 0, tzinfo=timezone.utc)]

    def later(minutes):
        t[0] = t[0].replace(minute=t[0].minute + minutes)

    class Slow(FeedProvider):
        name = "theirstack"

        def search(self, query):
            later(5)                   # each fetch takes a while
            return ok([job(query.label)])

        def credits_used(self):
            return 0

    def slow_send(request):
        later(20)                      # and so does the assessment
        return strong_send(request)

    out = run_morning(conn, Slow(), [Q, REVENUE], RULES, fit_brief="b",
                      factsheet="f", send=slow_send, clock=lambda: t[0])
    assert out.marks["strategy"] == datetime(2026, 9, 11, 7, 0, tzinfo=timezone.utc)
    assert out.marks["revenue"] == datetime(2026, 9, 11, 7, 5, tzinfo=timezone.utc)
    assert t[0] > out.marks["revenue"], (
        "positive control: the run really did go on after the fetch began")
