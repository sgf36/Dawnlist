"""Bringing in a posting found somewhere the feed does not reach.

The contract these tests pin down is the important one: extract what can be
read, and REPORT what cannot. A silently wrong company name is worse than an
empty one, because the empty one gets corrected and the wrong one gets applied
to.
"""
from app.feed.ingest import (MIN_DESCRIPTION_CHARS, PROVIDER, ParsedPosting,
                             parse_pasted, stable_id)

BODY = ("We are seeking a commercially minded analyst to join the team. "
        "You will own the annual budget, support the feasibility pipeline "
        "and work with the operations directors across the estate. "
        "Requirements: experience in feasibility studies; a degree in a "
        "numerate discipline; the right to work in the United Kingdom. "
        "This is a hybrid role based in London with occasional travel.")

PASTED = f"""\
Skip to main content
Senior Analyst at Example Group
Location: London
Salary: £55,000 - £65,000

{BODY}
"""


# ---------------------------------------------------------------------------
# Reading what is there
# ---------------------------------------------------------------------------

def test_the_title_and_company_split_on_at():
    p = parse_pasted(PASTED)
    assert p.title == "Senior Analyst"
    assert p.company == "Example Group"


def test_labelled_fields_are_read():
    p = parse_pasted(PASTED)
    assert p.location == "London"
    assert "55,000" in p.salary


def test_page_furniture_is_not_mistaken_for_the_title():
    """"Skip to main content" is the first line of a great many copied pages
    and would otherwise become the job title."""
    assert parse_pasted(PASTED).title != "Skip to main content"


def test_a_label_beats_an_inference():
    text = f"Job title: Revenue Manager\nSenior Analyst at Example Group\n{BODY}"
    assert parse_pasted(text).title == "Revenue Manager"


def test_the_description_keeps_the_heading_and_the_labels():
    """Removing lines to be tidy is how the salary or the closing date goes
    missing from what the model reads."""
    p = parse_pasted(PASTED)
    assert "Senior Analyst" in p.description
    assert "Salary" in p.description


# ---------------------------------------------------------------------------
# Reporting what is not there
# ---------------------------------------------------------------------------

def test_a_missing_company_is_reported_not_guessed():
    p = parse_pasted(f"Senior Analyst\n{BODY}")
    assert p.company == ""
    assert "company" in p.unresolved


def test_the_url_host_stands_in_for_a_company_when_there_is_one():
    p = parse_pasted(f"Senior Analyst\n{BODY}",
                     url="https://www.examplegroup.com/careers/123")
    assert p.company == "examplegroup.com"


def test_a_headline_alone_is_not_usable():
    p = parse_pasted("Senior Analyst at Example Group")
    assert not p.is_usable
    assert "description" in p.unresolved


def test_usable_needs_a_title_and_a_real_description():
    p = parse_pasted(PASTED)
    assert p.is_usable
    assert len(p.description) >= MIN_DESCRIPTION_CHARS


def test_a_missing_company_does_not_make_it_unusable():
    """Somebody pasting a recruiter's listing often has no company name.
    Refusing the posting would lose the role over a field the screen never
    reads."""
    p = parse_pasted(f"Senior Analyst\n{BODY}")
    assert p.is_usable
    assert "company" in p.unresolved


# ---------------------------------------------------------------------------
# Becoming a Job
# ---------------------------------------------------------------------------

def test_it_becomes_a_job_marked_as_hand_entered():
    job = parse_pasted(PASTED, url="https://x.test/1").to_job()
    assert job.provider == PROVIDER
    assert job.raw_criteria["source"] == "pasted"
    assert job.locations == ("London",)


def test_the_same_url_pasted_twice_is_one_job():
    """The same advert copied twice picks up different whitespace, so an id
    derived from the text alone would arrive as two jobs."""
    a = parse_pasted(PASTED, url="https://x.test/1").to_job()
    b = parse_pasted(PASTED + "\n\n ", url="https://x.test/1").to_job()
    assert a.dedup_key == b.dedup_key


def test_without_a_url_it_still_deduplicates_against_itself():
    a = parse_pasted(PASTED).to_job()
    b = parse_pasted(PASTED).to_job()
    assert a.dedup_key == b.dedup_key


def test_two_different_postings_are_two_jobs():
    a = parse_pasted(PASTED, url="https://x.test/1").to_job()
    b = parse_pasted(PASTED, url="https://x.test/2").to_job()
    assert a.dedup_key != b.dedup_key


def test_an_empty_paste_is_empty_rather_than_an_exception():
    p = parse_pasted("")
    assert p == ParsedPosting(unresolved=("title", "company", "description"))


# ---------------------------------------------------------------------------
# The deliberate refusal
# ---------------------------------------------------------------------------

def test_ingestion_never_fetches_the_url(monkeypatch):
    """A desktop app requesting arbitrary job pages on somebody's behalf, from
    their address, against sites whose terms often forbid it, is a decision
    that is not the app's to make. Pasting also works behind a login."""
    import urllib.request

    def refuse(*a, **k):
        raise AssertionError("ingestion must never fetch a URL")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    parse_pasted(PASTED, url="https://x.test/1").to_job()


def test_the_id_is_stable_across_whitespace_and_case_in_the_url():
    assert stable_id("https://X.test/1 ", "") == stable_id("https://x.test/1", "")
