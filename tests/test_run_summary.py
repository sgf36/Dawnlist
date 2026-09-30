"""A finished run says what it did — the banner that was silent about a quiet run.

Failed and partial runs already had banners. What was missing was a run that
completed cleanly but read little or nothing: it said "up to date" while
having fetched no postings, which is true and useless. These tests pin that a
clean run reports numbers, and that a thin one is flagged as such.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.core import db, diagnostics
from app.core.run_report import COMPLETE, RunReport, report_from_outcome
from app.feed.base import FetchResult
from app.i18n import set_locale
from app.ui.scheduler import describe_report, format_duration
from tests.test_main import Stub, job, ok, seed, strong_send
from tests.test_run_report import PerQuery, add_query, conn, outcome_of  # noqa: F401

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def english():
    set_locale("en")
    diagnostics.disable()
    yield
    diagnostics.disable()


def text(report):
    return describe_report(report, now=NOW)


# -- the report carries what the run measured --------------------------------

def test_a_clean_run_reports_its_own_numbers(conn):  # noqa: F811
    seed(conn)
    report = report_from_outcome(outcome_of(conn, Stub(ok([job("a"), job("b")]))))
    assert report.kind == COMPLETE
    assert report.swept == 2 and report.searches >= 1 and report.assessed >= 1
    assert report.seconds > 0


def test_a_search_that_returned_nothing_is_counted_as_empty(conn):  # noqa: F811
    seed(conn)
    add_query(conn, "zz quiet search")
    report = report_from_outcome(outcome_of(conn, PerQuery({
        "strategy": ok([job("a")]), "zz quiet search": ok([])})))
    assert report.empty_searches == 1 and report.searches == 2


def test_a_failed_search_is_not_counted_as_empty(conn):  # noqa: F811
    """'Returned nothing' and 'could not be asked' are different facts."""
    seed(conn)
    add_query(conn, "zz broken")
    report = report_from_outcome(outcome_of(conn, PerQuery({
        "strategy": ok([job("a")]),
        "zz broken": FetchResult(jobs=[], error="timeout")})))
    assert report.empty_searches == 0


# -- thin runs are named as such ---------------------------------------------

@pytest.mark.parametrize("swept,searches,empty,thin", [
    (0, 10, 10, True),    # the run that finished in seconds having read nothing
    (5, 10, 5, True),     # half the searches came back empty
    (20, 10, 4, False),
    (0, 0, 0, False),     # nothing measured is not the same as nothing found
])
def test_thin_means_nothing_or_mostly_nothing(swept, searches, empty, thin):
    r = RunReport(COMPLETE, swept=swept, searches=searches, empty_searches=empty)
    assert r.thin is thin


def test_only_a_complete_run_can_be_thin():
    assert not RunReport("fetch_failed", swept=0, searches=10, empty_searches=10).thin


# -- the words -----------------------------------------------------------------

def test_a_zero_posting_run_does_not_say_it_is_up_to_date():
    said = text(RunReport(COMPLETE, swept=0, searches=10, empty_searches=10,
                          seconds=19))
    assert "up to date" not in said
    assert "none of your 10 searches" in said and "19 s" in said


def test_a_thin_run_names_the_empty_searches():
    said = text(RunReport(COMPLETE, swept=6, searches=10, empty_searches=6,
                          assessed=6, seconds=83))
    assert "6 of your 10 searches returned nothing" in said and "1 min 23 s" in said


def test_a_healthy_run_reports_counts_and_duration():
    said = text(RunReport(COMPLETE, swept=40, searches=10, empty_searches=1,
                          assessed=38, seconds=83))
    assert "40 postings from 10 searches" in said and "38 of them read" in said


def test_an_unmeasured_report_falls_back_to_the_old_sentence():
    assert "shortlist is up to date" in text(RunReport(COMPLETE))


@pytest.mark.parametrize("seconds,want", [(0, "0 s"), (19.4, "19 s"),
                                          (60, "1 min 0 s"), (83, "1 min 23 s")])
def test_durations(seconds, want):
    assert format_duration(seconds) == want


# -- the pipeline times every stage and the diagnostics log tells the story ----

def test_every_stage_is_timed_on_the_outcome(conn):  # noqa: F811
    seed(conn)
    outcome = outcome_of(conn, Stub(ok([job("a")])))
    assert set(outcome.stage_seconds) >= {
        "fetch", "dedup", "cross_provider", "gates", "screen", "assess"}
    assert outcome.elapsed_seconds >= sum(
        v for k, v in outcome.stage_seconds.items()) * 0.9


def test_diagnostics_record_a_run_end_to_end(conn, tmp_path):  # noqa: F811
    seed(conn)
    diagnostics.enable(tmp_path, hook_network=False, hook_exceptions=False)
    outcome_of(conn, Stub(ok([job("a"), job("b")])))
    events = []
    for f in (tmp_path / diagnostics.SUBDIR).glob("*.jsonl"):
        events += [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()]
    names = [e["event"] for e in events]
    for expected in ("run.start", "fetch.search", "pipeline.fetch",
                     "pipeline.dedup", "pipeline.gates", "pipeline.screen",
                     "pipeline.assess", "run.end"):
        assert expected in names, expected
    end = [e for e in events if e["event"] == "run.end"][-1]
    assert end["funnel"]["swept"] == 2 and "fetch" in end["stages"]
    # a job description is never in the log
    assert "description" not in json.dumps(events).lower() or "[text len=" in json.dumps(events)


# -- a provider that could not be searched is said, not silently missing -------

from app.core.run_report import PROVIDER_LABELS  # noqa: E402
from app.feed.managed import ManagedProvider  # noqa: E402


def _worker_answer(degraded):
    return 200, {"jobs": [{"provider": "theirstack", "provider_job_id": "1", "title": "Hotel Manager",
                           "company": "Acme"}],
                 "counts": {"postings_used": 1, "matched": 1}, "degraded": degraded}


def test_the_app_reads_the_workers_degraded_report(monkeypatch):
    prov = ManagedProvider("DAWN-TEST-KEY", base="https://example.invalid")
    monkeypatch.setattr(prov, "_call", lambda *a, **k: _worker_answer(
        [{"provider": "linkedin", "code": "token_expired", "error": "LinkedIn token expired"}]))
    from app.feed.base import SearchQuery
    result = prov.search(SearchQuery(label="q"))
    assert result.ok and len(result.jobs) == 1
    assert result.degraded == [{"provider": "linkedin", "code": "token_expired",
                                "error": "LinkedIn token expired"}]


def test_no_degraded_field_means_nothing_degraded(monkeypatch):
    prov = ManagedProvider("DAWN-TEST-KEY", base="https://example.invalid")
    payload = _worker_answer(None)[1]
    payload.pop("degraded")
    monkeypatch.setattr(prov, "_call", lambda *a, **k: (200, payload))
    from app.feed.base import SearchQuery
    assert prov.search(SearchQuery(label="q")).degraded == []


class Degraded(Stub):
    def search(self, query):
        r = super().search(query)
        r.degraded = [{"provider": "linkedin", "code": "token_expired", "error": "expired"}]
        return r


def test_a_run_carries_the_failed_provider_once_not_per_search(conn):  # noqa: F811
    seed(conn)
    add_query(conn, "zz second search")
    outcome = outcome_of(conn, Degraded(ok([job("a")])))
    assert [d["provider"] for d in outcome.provider_degraded] == ["linkedin"]
    assert report_from_outcome(outcome).degraded == (PROVIDER_LABELS["linkedin"],)


def test_the_banner_names_the_missing_provider_in_plain_words():
    said = text(RunReport(COMPLETE, swept=40, searches=10, empty_searches=1, assessed=38,
                          seconds=83, degraded=("LinkedIn Jobs",)))
    assert "40 postings from 10 searches" in said
    assert "LinkedIn Jobs could not be searched this time" in said
    assert "token" not in said.lower() and "expired" not in said.lower()


def test_a_run_with_a_failed_provider_is_shown_as_a_problem_not_a_quiet_success():
    """set_run_status(problem=...) is driven by `thin or degraded`."""
    import inspect
    from app.ui import scheduler
    assert "report.degraded" in inspect.getsource(scheduler.RunBinding.finished)


def test_diagnostics_record_the_failed_provider_and_its_reason(conn, tmp_path):  # noqa: F811
    seed(conn)
    diagnostics.enable(tmp_path, hook_network=False, hook_exceptions=False)
    outcome_of(conn, Degraded(ok([job("a")])))
    events = []
    for f in (tmp_path / diagnostics.SUBDIR).glob("*.jsonl"):
        events += [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()]
    fetch = [e for e in events if e["event"] == "fetch.search"][-1]
    assert fetch["degraded"][0]["provider"] == "linkedin"
    assert fetch["degraded"][0]["code"] == "token_expired"
