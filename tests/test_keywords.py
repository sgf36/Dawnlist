"""The gap analysis: what a posting asks for, and what the evidence supports.

Every test here is about ADMISSION rather than matching. Matching is a regex
with word boundaries and it was already solved on the screen; the way this
feature fails is by telling somebody they are missing "a fast-paced
environment", and after three of those nobody reads the list again.
"""
from app.apply.keywords import GapReport, analyse, extract

POSTING = """\
Revenue Manager — London

About us
We are a fast-growing group with a fast-paced environment and a great team.

What you will do
Own the commercial performance of two properties and support the annual budget.

Requirements
- Proven experience in revenue management within branded hotels
- Experience with IDeaS or Duetto required
- Degree in hospitality management or a related field
- Must have at least 5 years in a similar role
- Fluent in English

Desirable
- Experience in cluster revenue roles is desirable
- Familiarity with Power BI would be an advantage
"""


def phrases(description=POSTING):
    return [p for p, _, _ in extract(description)]


# ---------------------------------------------------------------------------
# Admission
# ---------------------------------------------------------------------------

def test_a_stated_requirement_is_extracted():
    assert "revenue management" in [p.casefold() for p in phrases()]


def test_a_tool_named_as_required_is_extracted():
    assert any("ideas" in p.casefold() for p in phrases())


def test_prose_about_the_company_contributes_nothing():
    """The failure that makes the list unreadable.

    "a fast-paced environment" and "a great team" sit in a paragraph with no
    requirement marker, and even if one crept in they are on the noise list.
    """
    lowered = [p.casefold() for p in phrases()]
    assert not any("fast" in p for p in lowered)
    assert not any("environment" in p for p in lowered)
    assert "team" not in lowered


def test_a_bare_stopword_is_never_a_requirement():
    assert "experience" not in [p.casefold() for p in phrases()]
    assert "years" not in [p.casefold() for p in phrases()]


def test_a_phrase_stops_at_the_next_clause():
    """"experience in revenue management within branded hotels" must not
    become one seven-word phrase nobody would recognise as a skill."""
    got = [p.casefold() for p in phrases()]
    assert "revenue management" in got
    assert not any(len(p.split()) > 6 for p in got)


def test_a_leading_article_is_dropped():
    got = [p.casefold() for p in phrases()]
    assert not any(p.startswith(("a ", "an ", "the ")) for p in got)


def test_the_same_phrase_twice_is_reported_once():
    text = ("Requirements\n- Experience in revenue management\n"
            "- Proven experience in revenue management\n")
    got = [p.casefold() for p in phrases(text)]
    assert got.count("revenue management") == 1


# ---------------------------------------------------------------------------
# Required against preferred — the distinction the whole feature rests on
# ---------------------------------------------------------------------------

def test_a_desirable_is_not_reported_as_a_bar():
    report = analyse(POSTING, evidence="")
    preferred = [r.phrase.casefold() for r in report.missing_preferred]
    assert any("cluster revenue" in p for p in preferred)
    required = [r.phrase.casefold() for r in report.missing_required]
    assert not any("cluster revenue" in p for p in required)


def test_a_line_marked_both_ways_counts_as_a_preference():
    """"Experience in X is desirable" carries a requirement marker AND a
    preference marker. Reading it as a bar is the false positive that costs
    somebody an afternoon."""
    text = "Experience in Python is desirable\n"
    report = analyse(text, evidence="")
    assert report.missing_required == ()
    assert [r.phrase for r in report.missing_preferred] == ["Python"]


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def test_evidence_that_covers_a_requirement_marks_it_covered():
    report = analyse(POSTING, evidence="Ten years of revenue management "
                                       "across branded hotels.")
    assert "revenue management" in [r.phrase.casefold() for r in report.covered]


def test_a_word_boundary_stops_a_substring_match():
    """The screen's own lesson: "venue" must not fire on "revenue"."""
    report = analyse("Requirements\n- Experience in venue operations\n",
                     evidence="Grew revenue by a third.")
    assert report.covered == ()
    assert len(report.missing_required) == 1


def test_no_evidence_reports_everything_missing_rather_than_nothing():
    report = analyse(POSTING, evidence="")
    assert report.covered == ()
    assert report.missing_required, "before onboarding, everything is missing"


# ---------------------------------------------------------------------------
# The empty case, which must not read as success
# ---------------------------------------------------------------------------

def test_a_posting_with_no_stated_requirements_says_so():
    report = analyse("We are looking for someone great to join our team.",
                     evidence="anything")
    assert report.is_empty, "an empty report must be distinguishable"
    assert report.covered == ()
    assert report.missing_required == ()
    assert report.missing_preferred == ()


def test_an_empty_report_is_not_a_full_match():
    """`is_empty` exists so the UI can say "nothing stated" rather than
    rendering an empty missing-list as though the person qualified."""
    assert GapReport().is_empty
    assert not GapReport().covered


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------

def test_the_analysis_makes_no_network_call(monkeypatch):
    """Zero tokens is the whole reason this can be given away.

    If anything here ever reaches for a model, this fails rather than quietly
    adding a per-posting cost to every run.
    """
    import urllib.request

    def refuse(*a, **k):
        raise AssertionError("the gap analysis must not call anything")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    analyse(POSTING, evidence="revenue management")
