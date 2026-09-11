"""The managed feed path: plans, caps, and what the user is told at the ceiling.

The cap tests are the point of this file. A cap that silently returns a short
list is indistinguishable from a quiet day, and that is the exact failure spec
6.2 forbids — so every one of these asserts that the SHORTFALL IS NAMED, not
merely that the row count is right.
"""
from __future__ import annotations

import json

import pytest

from app.feed.base import SearchQuery
from app.feed.managed import ManagedFeedError, ManagedProvider, PlanStatus


def _provider(monkeypatch, responses):
    """A provider whose transport replays canned (status, payload) pairs.

    Keyed by path so a test can answer /v1/plan and /v1/search differently
    without caring about call order.
    """
    prov = ManagedProvider("DAWN-TEST-KEY", base="https://example.invalid")
    calls = []

    def fake_call(path, body=None, method="GET"):
        calls.append((path, body, method))
        return responses[path]

    monkeypatch.setattr(prov, "_call", fake_call)
    prov.calls = calls
    return prov


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

PLAN_OK = (200, {
    "ok": True,
    "plan": "standard",
    "plan_assigned": True,
    "tier": "managed",
    "caps": {"postings_per_day": 700, "refreshes_per_day": 3},
    "used_today": {"postings": 120, "refreshes": 1},
    "remaining_today": {"postings": 580, "refreshes": 2},
    "plans": [
        {"key": "trial", "postings_per_day": 30},
        {"key": "standard", "postings_per_day": 700},
        {"key": "global", "postings_per_day": 2500},
    ],
    "fallback_plan": "standard",
})


def test_plan_reads_the_server_numbers(monkeypatch):
    prov = _provider(monkeypatch, {"/v1/plan": PLAN_OK})
    plan = prov.plan()
    assert plan.plan == "standard"
    assert plan.postings_per_day == 700
    assert plan.postings_used == 120
    assert plan.postings_remaining == 580


def test_next_plan_up_is_the_smallest_bigger_one(monkeypatch):
    prov = _provider(monkeypatch, {"/v1/plan": PLAN_OK})
    nxt = prov.plan().next_plan_up
    assert nxt["key"] == "global"


def test_no_upgrade_is_offered_from_the_largest_plan():
    """The UI must not offer a plan that does not exist."""
    top = PlanStatus(plan="global", plan_assigned=True, tier="managed",
                     postings_per_day=2500, postings_used=0,
                     postings_remaining=2500, refreshes_per_day=6,
                     refreshes_used=0,
                     ladder=({"key": "standard", "postings_per_day": 700},
                             {"key": "global", "postings_per_day": 2500}))
    assert top.next_plan_up is None


def test_unassigned_plan_is_reported_as_unknown_not_guessed(monkeypatch):
    """A licence with no plan must not be shown as Standard.

    Licences issued before plans existed, and those minted by an override
    code, genuinely have no plan. Displaying the fallback would assert
    something about a person's subscription that nobody recorded.
    """
    payload = dict(PLAN_OK[1], plan=None, plan_assigned=False)
    prov = _provider(monkeypatch, {"/v1/plan": (200, payload)})
    plan = prov.plan()
    assert plan.plan is None
    assert plan.plan_assigned is False


def test_plan_failure_raises_rather_than_returning_zeroes(monkeypatch):
    """Zeroes would render as "0 of 0 used", which reads as a spent quota."""
    prov = _provider(monkeypatch, {
        "/v1/plan": (403, {"error": "unknown_licence", "message": "nope"})})
    with pytest.raises(ManagedFeedError) as e:
        prov.plan()
    assert e.value.code == "unknown_licence"


# ---------------------------------------------------------------------------
# Search and the cap
# ---------------------------------------------------------------------------

def _query():
    return SearchQuery(label="hotel gm", titles=["General Manager"],
                       countries=["GB"])


def test_search_maps_rows_and_counts(monkeypatch):
    prov = _provider(monkeypatch, {"/v1/search": (200, {
        "jobs": [{"provider": "theirstack", "provider_job_id": "1",
                  "title": "General Manager", "company": "Claridge's",
                  "locations": ["London"], "description_text": "x",
                  "posted_at": "2026-09-01", "url": "https://e.example"}],
        "counts": {"matched": 1, "returned": 1, "not_fetched": 0,
                   "capped": False, "postings_used": 1, "postings_allowed": 700,
                   "postings_remaining": 699},
    })})
    res = prov.search(_query())
    assert res.ok
    assert len(res.jobs) == 1
    assert res.jobs[0].company == "Claridge's"
    assert res.jobs[0].posted_at is not None
    assert res.capped is False
    assert res.shortfall is None


def test_partial_fetch_names_the_shortfall(monkeypatch):
    """Matched more than returned, but NOT because of the cap."""
    prov = _provider(monkeypatch, {"/v1/search": (200, {
        "jobs": [{"provider": "theirstack", "provider_job_id": str(i),
                  "title": "GM", "company": "C", "locations": [],
                  "description_text": "", "url": ""} for i in range(10)],
        "counts": {"matched": 40, "returned": 10, "not_fetched": 30,
                   "capped": False, "postings_used": 10},
    })})
    res = prov.search(_query())
    assert res.not_fetched == 30
    assert res.shortfall is not None
    assert "30" in res.shortfall


def test_cap_reached_mid_query_is_flagged_and_counted(monkeypatch):
    """The run got rows, but the plan's ceiling stopped it short."""
    prov = _provider(monkeypatch, {"/v1/search": (200, {
        "jobs": [{"provider": "theirstack", "provider_job_id": str(i),
                  "title": "GM", "company": "C", "locations": [],
                  "description_text": "", "url": ""} for i in range(5)],
        "counts": {"matched": 900, "returned": 5, "not_fetched": 895,
                   "capped": True, "postings_used": 700,
                   "postings_allowed": 700, "postings_remaining": 0},
    })})
    res = prov.search(_query())
    assert res.capped is True
    assert res.not_fetched == 895
    shortfall = res.shortfall
    assert shortfall is not None
    # It must say it is the PLAN, with the numbers — not a vague "partial".
    assert "capped" in shortfall
    assert "900" in shortfall
    assert prov.credits_used() == 700


def test_hard_cap_refusal_is_not_an_empty_quiet_day(monkeypatch):
    """429 from the Worker: nothing fetched at all, because nothing is left.

    This is the case most likely to be mistaken for "no new jobs today", so it
    must arrive flagged as a cap and carrying a message.
    """
    prov = _provider(monkeypatch, {"/v1/search": (429, {
        "error": "posting_cap",
        "message": "Daily posting cap reached — 700 of 700 fetched today"})})
    res = prov.search(_query())
    assert res.jobs == []
    assert res.capped is True
    assert res.ok is False
    assert "700" in res.error


def test_refresh_cap_is_distinguished_from_the_posting_cap(monkeypatch):
    """Different limit, different remedy — running again tomorrow vs upgrading."""
    prov = _provider(monkeypatch, {"/v1/search": (429, {
        "error": "refresh_cap", "message": "Daily refresh cap reached (3)"})})
    res = prov.search(_query())
    assert res.capped is False        # not the posting ceiling
    assert "refresh" in res.error.lower()


def test_provider_outage_is_surfaced_not_swallowed(monkeypatch):
    prov = _provider(monkeypatch, {"/v1/search": (502, {
        "error": "provider_error", "message": "theirstack returned 503"})})
    res = prov.search(_query())
    assert res.ok is False
    assert "unavailable" in res.error


def test_unreachable_worker_says_so(monkeypatch):
    prov = _provider(monkeypatch, {"/v1/search": (None, {
        "error": "unreachable", "message": "timeout"})})
    res = prov.search(_query())
    assert res.ok is False
    assert "feed service" in res.error


def test_a_licence_key_is_required():
    with pytest.raises(ValueError):
        ManagedProvider("")


def test_no_personal_content_is_sent_upstream(monkeypatch):
    """The request body carries search terms only.

    Load-bearing for the store privacy labels and for the decision not to be a
    processor of anyone's career record: if a CV, brief or description ever
    appears in this body, that claim becomes false.
    """
    prov = _provider(monkeypatch, {"/v1/search": (200, {"jobs": [], "counts": {}})})
    prov.search(_query())
    _path, body, _method = prov.calls[0]
    sent = json.dumps(body).lower()
    for forbidden in ("cv", "factsheet", "brief", "description_text", "resume"):
        assert forbidden not in sent
    assert set(body) == {"label", "titles", "countries", "companies",
                         "postedWithinDays", "discoveredSince",
                         "excludeJobIds", "maxResults", "cities",
                         "excludeTitleTerms", "excludeCompanies"}
    # excludeJobIds carries PROVIDER IDS ONLY. It is a billing control, and it
    # must never become a channel for anything about the user. They are
    # integers, not strings: the Worker forwards the list untouched to
    # TheirStack's `job_id_not`, which is typed as integers. This line used to
    # assert strings, which pinned the wrong type in place.
    assert all(isinstance(x, int) for x in body["excludeJobIds"])


def test_only_numeric_feed_ids_reach_either_transport(monkeypatch):
    """A pasted advert's id is a hash, and a fallback id can be a URL. Neither
    is anything `job_id_not` can use, and one of them in the list can fail the
    whole search it was meant to make cheaper."""
    from app.feed.theirstack import TheirStackProvider

    query = SearchQuery(label="q", titles=["GM"], countries=["GB"],
                        exclude_job_ids=("4711", "p3f9a0c1d2e4b5a6978c1",
                                         "https://example.com/jobs/9", " 12 "))

    prov = _provider(monkeypatch, {"/v1/search": (200, {"jobs": [], "counts": {}})})
    prov.search(query)
    assert prov.calls[0][1]["excludeJobIds"] == [4711, 12]

    direct = TheirStackProvider("TS-KEY")._body(query, 0, 10)
    assert direct["job_id_not"] == [4711, 12]

    # Nothing usable means the filter is left off, not sent empty.
    bare = SearchQuery(label="q", exclude_job_ids=("p1", "abc"))
    assert "job_id_not" not in TheirStackProvider("TS-KEY")._body(bare, 0, 10)


def test_the_direct_adapter_records_the_country_like_the_worker_does():
    """The location gate and the assessment both read `country_codes`. The
    developer adapter never set it, so its gate kept every posting and the
    model saw no country to hold a "based in" constraint against."""
    from app.feed.theirstack import TheirStackProvider

    prov = TheirStackProvider("TS-KEY")
    single = prov._to_job({"id": 1, "job_title": "GM", "country_code": "GB"})
    listed = prov._to_job({"id": 2, "job_title": "GM", "country_codes": ["IE", "GB"]})
    assert single.raw_criteria["country_codes"] == ["GB"]
    assert listed.raw_criteria["country_codes"] == ["IE", "GB"]
    # Unknown stays absent rather than an empty tag, which the gate reads as
    # "no country recorded" and keeps.
    assert "country_codes" not in prov._to_job({"id": 3, "job_title": "GM"}).raw_criteria


# ---------------------------------------------------------------------------
# The remedy sentence: a cap message must not be a dead end
# ---------------------------------------------------------------------------

from app.core.pipeline import cap_remedy  # noqa: E402


class _FakeStatus:
    def __init__(self, plan, per_day, nxt):
        self.plan, self.postings_per_day, self.next_plan_up = plan, per_day, nxt


class _Provider:
    def __init__(self, status=None, raises=False):
        self._status, self._raises = status, raises

    def plan(self):
        if self._raises:
            raise RuntimeError("worker down")
        return self._status


def test_remedy_names_the_next_plan_and_both_numbers():
    prov = _Provider(_FakeStatus("standard", 700,
                                 {"key": "global", "postings_per_day": 2500}))
    out = cap_remedy(prov)
    assert "standard" in out and "700" in out
    assert "global" in out and "2500" in out


def test_no_remedy_is_offered_from_the_largest_plan():
    assert cap_remedy(_Provider(_FakeStatus("global", 2500, None))) == ""


def test_a_provider_with_no_plans_is_a_no_op_not_a_crash():
    """The developer provider has no plan(). A morning run must not die on it."""
    class NoPlans:
        pass
    assert cap_remedy(NoPlans()) == ""


def test_a_failed_plan_lookup_does_not_fail_the_run():
    assert cap_remedy(_Provider(raises=True)) == ""


# ---------------------------------------------------------------------------
# The user-agent, without which the shipped app cannot reach its own service
# ---------------------------------------------------------------------------

def test_every_request_identifies_itself(monkeypatch):
    """urllib sends "Python-urllib/3.x" and Cloudflare refuses it — error 1010,
    "banned based on your browser's signature".

    Found by the first end-to-end run against the live service on 2026-09-08.
    Nothing else could have found it: every other test injects the transport,
    and the manual checks used curl, whose agent is not blocked. So the manual
    verification passed while the real client could not connect at all.
    """
    from app.feed.managed import USER_AGENT, ManagedProvider

    seen = {}

    class Response:
        status = 200

        def read(self):
            return b'{"ok":true,"plan":"standard","caps":{},"used_today":{},"remaining_today":{}}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        seen["agent"] = req.get_header("User-agent")
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    ManagedProvider("DAWN-TEST").plan()

    assert seen["agent"] == USER_AGENT
    assert "Python-urllib" not in (seen["agent"] or "")
    assert "Dawnlist" in seen["agent"], "name the product, not just any agent"
