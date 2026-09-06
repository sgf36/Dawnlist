"""spec 6.6 / 6.5 — dedup and stranded-file recovery."""
from app.core.dedup import dedup, recover_stranded
from app.feed.models import Job


def j(jid, company="Acme", title="Strategy Lead", provider="theirstack"):
    return Job(provider=provider, provider_job_id=jid, title=title, company=company)


def test_exact_duplicate_is_dropped():
    r = dedup([j("1"), j("1")])
    assert len(r.unique) == 1 and len(r.exact_duplicates) == 1


def test_same_company_different_role_is_not_a_duplicate():
    """The worse failure. Collapsing these discards real postings."""
    r = dedup([j("1", title="Strategy Lead"), j("2", title="Revenue Lead")])
    assert len(r.unique) == 2
    assert r.near_duplicates == []


def test_near_duplicate_is_flagged_never_dropped():
    r = dedup([j("1", provider="theirstack"), j("2", provider="adzuna")])
    assert len(r.unique) == 2, "a near-duplicate is never silently dropped"
    assert len(r.near_duplicates) == 1
    assert "name key" in r.near_duplicates[0].reason


def test_whitespace_and_ampersand_variants_are_caught_as_near_duplicates():
    a = j("1", company="Rocco  Forte & Co", title="Head of  Strategy")
    b = j("2", company="Rocco Forte & Co", title="Head of Strategy")
    r = dedup([a, b])
    assert len(r.near_duplicates) == 1


def test_already_seen_jobs_are_dropped():
    r = dedup([j("1")], already_seen={("theirstack", "1")})
    assert r.unique == [] and len(r.exact_duplicates) == 1


def test_counts_state_what_was_excluded():
    r = dedup([j("1"), j("1"), j("2", title="Other")])
    assert r.counts == {"in": 3, "unique": 2, "exact_duplicates": 1,
                        "near_duplicates_flagged": 0}


# -- spec 6.5 recovery ------------------------------------------------------
def test_recovery_takes_only_positive_rows():
    rows = [
        {"provider": "theirstack", "provider_job_id": "1", "bucket": "strong"},
        {"provider": "theirstack", "provider_job_id": "2", "bucket": "rejected"},
    ]
    out = recover_stranded(rows, decided_ids=set())
    assert [r["provider_job_id"] for r in out] == ["1"]


def test_recovery_dedups_by_job_id_not_name():
    rows = [
        {"provider": "theirstack", "provider_job_id": "1", "bucket": "strong",
         "company": "Acme  Ltd"},
        {"provider": "theirstack", "provider_job_id": "2", "bucket": "strong",
         "company": "Acme Ltd"},
    ]
    out = recover_stranded(rows, decided_ids={("theirstack", "1")})
    assert [r["provider_job_id"] for r in out] == ["2"]


# --------------------------------------------------------------------------
# Measured in P0: a naive company+title matcher produced at least one false
# negative in six. Because a name key can only ever RAISE a flag, folding these
# variants together is safe — a missed flag is the expensive direction.
# --------------------------------------------------------------------------
from app.feed.models import name_key  # noqa: E402


def test_a_single_plural_no_longer_splits_the_key():
    """The real case: live title 'Analyst, Investment and Portfolio Oversight'
    vs a search for 'analyst investments portfolio'."""
    assert name_key("Round Hill", "Analyst, Investment and Portfolio Oversight") \
        == name_key("Round Hill", "Analyst Investments Portfolio Oversight")


def test_company_suffix_variants_fold_together():
    """A company-name variant returned n=0 for a whole employer over 365 days."""
    assert name_key("Staycity Group Ltd", "Revenue Manager") \
        == name_key("StayCity", "Revenue Manager")


def test_word_order_and_connecting_words_do_not_split_a_role():
    assert name_key("Acme", "Head of Revenue and Strategy") \
        == name_key("Acme", "Strategy & Revenue Head")


def test_over_stemming_is_avoided():
    """'analysis' and 'business' must not be stemmed into something else."""
    assert name_key("Acme", "Business Analysis Lead") \
        != name_key("Acme", "Busines Analysi Lead")


def test_same_company_different_role_still_differs_after_folding():
    """The worse failure must stay impossible."""
    assert name_key("Acme", "Strategy Lead") != name_key("Acme", "Revenue Lead")
    assert name_key("Acme", "Analyst") != name_key("Acme", "Analysts Manager")


def test_plural_variants_are_flagged_as_near_duplicates_not_dropped():
    a = j("1", company="Round Hill", title="Analyst, Investment and Portfolio")
    b = j("2", company="Round Hill Ltd", title="Analyst Investments Portfolio")
    r = dedup([a, b])
    assert len(r.unique) == 2, "a near-duplicate is never silently dropped"
    assert len(r.near_duplicates) == 1, "but it must be FLAGGED for judgement"
