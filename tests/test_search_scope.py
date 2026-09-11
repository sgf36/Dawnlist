"""Where a search looks, and the gates that hold it there.

Every search switched on during setup used to look across the whole world, and
the model call meant to choose the searches crashed on every call, so users were
offered fragments of their own sentences instead. Each posting those searches
returned was paid for. These tests exist for all three.
"""
import json
import re

import pytest

from app.core import db
from app.core.search_scope import (SearchScope, employment_gate,
                                   excluded_company_gate, load_scope,
                                   location_gate, parse_where, scope_gates)
from app.feed.base import FetchResult
from app.feed.models import Job

AIM = "Hotel asset management, full-time roles based in London."


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.sqlite3")
    db.migrate(c)
    yield c
    c.close()


def job(jid="1", company="Acme", **raw):
    return Job(provider="theirstack", provider_job_id=jid, title="Role",
               company=company, description_text="A role.", raw_criteria=raw)


def plan_reply(**fields):
    base = {"titles": [], "countries": [], "cities": [], "employment_types": [],
            "exclude_title_terms": [], "exclude_companies": []}
    base.update(fields)
    return json.dumps(base)


def verdicts(bucket):
    def send(request):
        refs = re.findall(r'ref="([^"]+)"', request["messages"][0]["content"])
        return {"verdicts": [{"job_ref": r, "bucket": bucket, "reason": "fits",
                              "disqualifying_quote": None,
                              "requirement_checked": True} for r in refs]}
    return send


# -- what the user types in Settings -------------------------------------------
def test_a_city_and_a_country_are_read_apart():
    scope = parse_where("London, GB")
    assert scope.cities == ("London",)
    assert scope.countries == ("GB",)


def test_uk_becomes_the_code_the_feed_indexes():
    assert parse_where("uk").countries == ("GB",)


def test_a_city_with_no_country_is_refused():
    """The same name exists in several countries; the feed would have to guess."""
    with pytest.raises(ValueError):
        parse_where("London")


def test_nothing_typed_is_refused():
    with pytest.raises(ValueError):
        parse_where("   ")


def test_changing_the_place_keeps_the_exclusions_and_contract_types():
    kept = SearchScope.from_parts(countries=["GB"], exclude_companies=["GIC"],
                                  employment_types=["full_time"])
    moved = parse_where("Manchester, GB", keep=kept)
    assert moved.cities == ("Manchester",)
    assert moved.exclude_companies == ("GIC",)
    assert moved.employment_types == ("full_time",)


# -- the gates only remove what contradicts the user ----------------------------
def test_a_posting_tagged_with_another_country_is_removed():
    gate = location_gate(["GB"])
    assert gate.predicate(job(country_codes=["GB"])), "positive control"
    assert not gate.predicate(job(country_codes=["US"]))


def test_a_posting_with_no_country_is_kept():
    assert location_gate(["GB"]).predicate(job())


def test_blank_and_mixed_contract_types_are_kept():
    gate = employment_gate(["full_time"])
    assert gate.predicate(job()), "the best posting in one sample had no contract type"
    assert gate.predicate(job(employment_statuses=["volunteer", "full_time"])), (
        "the feed reads types out of benefits text")
    assert not gate.predicate(job(employment_statuses=["contract"]))


def test_no_contract_preference_keeps_every_contract_type():
    assert employment_gate([]).predicate(job(employment_statuses=["contract"]))


def test_an_excluded_employer_is_matched_on_its_normalised_name():
    gate = excluded_company_gate(["GIC"])
    assert not gate.predicate(job(company="GIC Pte. Ltd."))
    assert gate.predicate(job(company="Logic Hotels")), "never a substring match"


def test_a_run_over_several_searches_keeps_what_any_of_them_allows():
    gates = scope_gates([
        SearchScope.from_parts(countries=["GB"], employment_types=["full_time"]),
        SearchScope.from_parts(countries=["IE"]),
    ])
    contract_in_ireland = job(country_codes=["IE"], employment_statuses=["contract"])
    assert all(g.predicate(contract_in_ireland) for g in gates)


# -- the model call that chooses the searches -----------------------------------
def test_the_plan_is_read_from_the_text_the_transport_returns():
    """`build_send` returns TEXT. The old code read `.content` from it, raised on
    every call, and silently fell back to splitting the user's sentences."""
    from app.main import search_plan

    reply = plan_reply(titles=["Hotel Asset Manager", "Revenue Manager"],
                       countries=["GB"], cities=["London"],
                       employment_types=["full_time"],
                       exclude_companies=["GIC"])
    plan = search_plan(AIM, send=lambda request: reply)
    assert plan.titles == ["Hotel Asset Manager", "Revenue Manager"]
    assert plan.scope.countries == ("GB",)
    assert plan.scope.cities == ("London",)
    assert plan.scope.employment_types == ("full_time",)
    assert plan.scope.exclude_companies == ("GIC",)


def test_the_brief_reaches_the_model():
    """Corrections like "roles must be BASED in London" are made in the brief."""
    from app.main import search_plan

    seen = {}

    def send(request):
        seen["content"] = request["messages"][0]["content"]
        return plan_reply()

    search_plan("asset management", brief="Roles must be BASED in London.", send=send)
    assert "BASED in London" in seen["content"]


def test_a_failed_model_call_offers_nothing_rather_than_fragments():
    from app.main import search_plan, titles_from_aim

    def broken(request):
        raise RuntimeError("network down")

    assert search_plan(AIM, send=broken).titles == []
    # Positive control: the rule-based split really does produce the fragment
    # users were shown, so the empty list above is a choice, not an accident.
    assert "full-time roles based" in titles_from_aim(AIM)


# -- searches carry a scope, and cannot run without one ---------------------------
def test_seeded_searches_carry_the_plan_scope(conn):
    from app.main import seed_queries_from_aim

    scope = SearchScope.from_parts(countries=["GB"], cities=["London"])
    seed_queries_from_aim(conn, "ignored", titles=["Hotel Asset Manager"], scope=scope)
    params = json.loads(conn.execute("SELECT params_json FROM queries")
                        .fetchone()["params_json"])
    assert params["countries"] == ["GB"]
    assert params["cities"] == ["London"]
    assert load_scope(conn) == scope


def test_a_search_with_no_location_cannot_be_switched_on(conn):
    from app.main import enable_query, save_query

    save_query(conn, "asset manager", ["asset manager"], enabled=False)
    with pytest.raises(ValueError):
        enable_query(conn, "asset manager")


def test_a_search_with_a_location_can_be_switched_on(conn):
    from app.main import enable_query, load_queries, save_query

    save_query(conn, "asset manager", ["asset manager"], countries=["GB"],
               enabled=False)
    enable_query(conn, "asset manager")
    assert [q.label for q in load_queries(conn)] == ["asset manager"]


def test_one_entry_rescopes_every_search(conn):
    from app.main import apply_scope, enable_query, load_queries, save_query

    for label in ("a", "b"):
        save_query(conn, label, [label], enabled=False)
    apply_scope(conn, parse_where("London, GB"))
    for label in ("a", "b"):
        enable_query(conn, label)
    assert {q.label: q.cities for q in load_queries(conn)} == {
        "a": ["London"], "b": ["London"]}


def test_rescoping_clears_the_delta_mark(conn):
    """The mark records how far the OLD area was read; kept, it would skip
    everything already indexed in the new one."""
    from app.main import apply_scope, save_query

    save_query(conn, "a", ["a"], countries=["GB"])
    conn.execute("UPDATE queries SET last_discovered_at='2026-09-01T00:00:00+00:00'")
    apply_scope(conn, parse_where("Dublin, IE"))
    assert conn.execute("SELECT last_discovered_at FROM queries").fetchone()[0] is None


def test_a_search_switched_on_before_locations_were_required_is_never_swept(conn):
    from app.main import load_queries, unscoped_enabled_labels

    conn.execute("INSERT INTO queries(label, params_json, enabled, created_at) "
                 "VALUES('old', ?, 1, 'x')",
                 (json.dumps({"titles": ["manager"], "countries": []}),))
    conn.commit()
    assert load_queries(conn) == []
    assert unscoped_enabled_labels(conn) == ["old"]


def test_a_run_refuses_and_names_a_search_with_no_location(conn):
    from app.main import NotConfigured, morning_run
    from app.onboarding.interview import save_document

    save_document(conn, "fit_brief", "Hotel asset management.")
    conn.execute("INSERT INTO settings(key, value) "
                 "VALUES('calibration_passed_at', '2026-09-06T00:00:00+00:00')")
    conn.execute("INSERT INTO queries(label, params_json, enabled, created_at) "
                 "VALUES('old', ?, 1, 'x')",
                 (json.dumps({"titles": ["manager"], "countries": []}),))
    conn.commit()
    with pytest.raises(NotConfigured, match="old"):
        morning_run(conn)


def test_adding_a_search_in_settings_uses_the_install_scope(conn):
    from app.core.search_scope import save_scope
    from app.main import load_queries, save_new_search

    with pytest.raises(ValueError):
        save_new_search(conn, "revenue manager", ["revenue manager"])
    save_scope(conn, parse_where("London, GB"))
    save_new_search(conn, "revenue manager", ["revenue manager"])
    assert load_queries(conn)[0].cities == ["London"]


# -- calibration pays for what it shows, and no more ------------------------------
class Feed:
    name = "theirstack"

    def __init__(self, jobs):
        self.jobs, self.asked = jobs, []

    def search(self, query):
        self.asked.append(query.max_results)
        return FetchResult(jobs=self.jobs, pages_fetched=1, exhausted=True)


def test_calibration_asks_for_a_bounded_number_of_postings(conn, monkeypatch, tmp_path):
    """It asked the first search alone for 500 (a 100-row page) to show ten."""
    import app.main as main

    monkeypatch.setattr(main, "alerts_dir", lambda: tmp_path / "none")
    for label in ("a", "b"):
        main.save_query(conn, label, [label], countries=["GB"])
    feed = Feed([])
    main.calibration_sample(conn, provider=feed, send=verdicts("strong"))
    assert feed.asked, "positive control: the feed was asked"
    assert all(n <= main.CALIBRATION_FETCH for n in feed.asked)
    assert sum(feed.asked) <= main.CALIBRATION_FETCH + len(feed.asked)


def test_calibration_never_shows_a_posting_from_another_country(conn, monkeypatch, tmp_path):
    import app.main as main
    from app.onboarding.interview import save_document

    monkeypatch.setattr(main, "alerts_dir", lambda: tmp_path / "none")
    save_document(conn, "fit_brief", "b")
    main.save_query(conn, "a", ["a"], countries=["GB"])
    jobs = ([job(f"gb{i}", country_codes=["GB"]) for i in range(10)]
            + [job(f"us{i}", country_codes=["US"]) for i in range(5)])
    items = main.calibration_sample(conn, provider=Feed(jobs), send=verdicts("strong"))
    keys = {i.job_key for i in items}
    assert any(k.endswith(":gb0") for k in keys), "positive control"
    assert not any(":us" in k for k in keys)
