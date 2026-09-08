"""A capped run must SAY it was capped, in those words, with the numbers.

spec 6.2 forbids presenting a truncated fetch as "no new jobs". A daily cap is
the most ordinary way a fetch gets truncated in the managed tier, and it is the
one case where the truncation is expected — which is exactly why it needs its
own wording. "Results are partial" invites the user to assume a bug; "your
plan's daily limit was reached, 290 postings not fetched" is a fact they can
act on, by waiting a day or by raising the cap.
"""
import pytest

from app.core import db
from app.core.pipeline import run_morning
from app.core.rules import RuleTable
from app.feed.base import FeedProvider, FetchResult, SearchQuery
from app.feed.models import Job


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.sqlite3")
    db.migrate(c)
    yield c
    c.close()


def job(jid):
    return Job(provider="theirstack", provider_job_id=jid,
               title="Head of Strategy", company="Acme",
               description_text="A strategy role.")


class StubProvider(FeedProvider):
    name = "theirstack"

    def __init__(self, result):
        self.result = result

    def search(self, query):
        return self.result

    def credits_used(self):
        return 0


RULES = RuleTable(strong_terms=["strategy"])
Q = SearchQuery(label="strategy", titles=["strategy"])


def _send(request):
    import re
    refs = re.findall(r'ref="([^"]+)"', request["messages"][0]["content"])
    return {"verdicts": [{"job_ref": r, "bucket": "strong", "reason": "fits",
                          "disqualifying_quote": None,
                          "requirement_checked": True} for r in refs]}


# --- the shortfall sentence -------------------------------------------------

def test_a_complete_fetch_has_no_shortfall():
    r = FetchResult(jobs=[job("a")], pages_fetched=1, exhausted=True)
    assert r.shortfall is None


def test_capped_names_the_cap_and_the_numbers():
    r = FetchResult(jobs=[job(str(i)) for i in range(50)], pages_fetched=1,
                    exhausted=False, matched=340, not_fetched=290, capped=True)
    s = r.shortfall
    assert "capped" in s
    assert "340" in s and "50" in s and "290" in s
    assert "daily limit" in s


def test_partial_without_a_cap_does_not_blame_the_plan():
    r = FetchResult(jobs=[job("a")], pages_fetched=1, exhausted=False,
                    matched=90, not_fetched=89, capped=False)
    s = r.shortfall
    assert "capped" not in s
    assert "89" in s and "90" in s


def test_unpaginated_still_reports_even_when_unsized():
    r = FetchResult(jobs=[job("a")], pages_fetched=1, exhausted=False)
    assert r.shortfall == "partial: pagination did not reach exhaustion"


def test_matched_none_is_not_treated_as_zero():
    """`None` means the provider did not size the query. A run that prints 0
    there would claim the query matched nothing, which is the exact error
    spec 6.2 exists to stop."""
    r = FetchResult(jobs=[job("a")], pages_fetched=1, exhausted=False,
                    not_fetched=5, capped=True, matched=None)
    assert "?" in r.shortfall
    assert " 0 postings matched" not in r.shortfall


# --- and it has to reach the run --------------------------------------------

def test_a_capped_run_is_not_reported_as_clean(conn):
    provider = StubProvider(FetchResult(
        jobs=[job("a")], pages_fetched=1, exhausted=False,
        matched=340, not_fetched=339, capped=True, credits_estimate=1))

    out = run_morning(conn, provider, [Q], RULES, fit_brief="b", factsheet="f",
                      send=_send)

    assert out.fetch_errors, "a capped run must carry a reported shortfall"
    assert any("capped" in e and "339" in e for e in out.fetch_errors), \
        f"the cap must be named with its numbers, got {out.fetch_errors}"
    assert not out.complete, "a capped run is never a complete run"


def test_an_uncapped_complete_run_stays_clean(conn):
    provider = StubProvider(FetchResult(
        jobs=[job("a")], pages_fetched=1, exhausted=True, credits_estimate=1))

    out = run_morning(conn, provider, [Q], RULES, fit_brief="b", factsheet="f",
                      send=_send)

    assert out.fetch_errors == []
    assert out.complete
