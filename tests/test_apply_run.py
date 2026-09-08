"""The application pack — the production caller that makes the rest reachable.

The repository's own lesson applies here: a tested function with no production
caller is a whole missing capability. These tests exist to pin the ORDER (the
free gap analysis first, handed to everything else) and the failure behaviour
(reported, never substituted).
"""
from pathlib import Path

from app.apply.run import ApplicationPack, prepare_application, summarise
from app.feed.models import Job

FACTSHEET = "Supported feasibility studies at Example Group, 2021-2023."
CV = "Example Group — Senior Associate. Feasibility studies."
DESCRIPTION = ("Requirements\n"
               "- Experience in feasibility studies required\n"
               "- Experience in revenue management required\n")

JOB = Job(provider="pasted", provider_job_id="p1", title="Analyst",
          company="Example Group", description_text=DESCRIPTION)


def recorder(reply="Body."):
    seen = []

    def send(request):
        seen.append(request)
        return reply

    return send, seen


# ---------------------------------------------------------------------------
# Order and inputs
# ---------------------------------------------------------------------------

def test_the_gap_analysis_runs_first_and_reaches_both_documents():
    """A CV written without knowing which requirements are unmet is the one
    that reaches for "exposure to" as a way of implying one."""
    send, seen = recorder()
    prepare_application(JOB, factsheet=FACTSHEET, cv_text=CV, send=send)
    assert len(seen) == 2
    for request in seen:
        user = request["messages"][0]["content"]
        assert "do not claim these" in user.lower()
        assert "revenue management" in user.lower()


def test_what_the_evidence_supports_is_recognised():
    pack = prepare_application(JOB, factsheet=FACTSHEET, cv_text=CV,
                               send=recorder()[0])
    covered = [r.phrase.casefold() for r in pack.gaps.covered]
    assert "feasibility studies" in covered


def test_the_brief_is_not_produced_unless_asked_for():
    """It is for an interview nobody has offered yet, and it spends the
    user's own tokens."""
    send, seen = recorder()
    pack = prepare_application(JOB, factsheet=FACTSHEET, cv_text=CV, send=send)
    assert pack.brief is None
    assert len(seen) == 2


def test_asking_for_the_brief_adds_exactly_one_request():
    send, seen = recorder()
    pack = prepare_application(JOB, factsheet=FACTSHEET, cv_text=CV,
                               send=send, want_brief=True)
    assert len(seen) == 3
    assert pack.brief is not None
    assert "revenue management" in [e.casefold() for e in pack.brief.exposed_on]


def test_a_caller_can_ask_for_the_letter_alone():
    send, seen = recorder()
    pack = prepare_application(JOB, factsheet=FACTSHEET, cv_text=CV,
                               send=send, want_cv=False)
    assert len(seen) == 1
    assert pack.cv is None and pack.letter is not None


# ---------------------------------------------------------------------------
# Failure is reported, never substituted
# ---------------------------------------------------------------------------

def test_a_failed_request_is_named_and_the_document_is_absent():
    """A covering letter that quietly became a template is worse than none:
    it goes out looking finished."""
    def send(request):
        if "covering letter" in str(request).lower():
            raise RuntimeError("upstream down")
        return "Body."

    pack = prepare_application(JOB, factsheet=FACTSHEET, cv_text=CV, send=send)
    assert pack.letter is None
    assert any("letter failed" in e for e in pack.errors)
    assert pack.cv is not None, "one failure must not lose the other document"


def test_a_pack_with_an_error_is_not_complete():
    def send(request):
        raise RuntimeError("nope")

    pack = prepare_application(JOB, factsheet=FACTSHEET, cv_text=CV, send=send)
    assert not pack.complete
    assert len(pack.errors) == 2


# ---------------------------------------------------------------------------
# The guard survives the round trip
# ---------------------------------------------------------------------------

def test_an_unsupported_claim_marks_the_pack_blocked():
    send, _ = recorder("Led a team of [[how many?]] people.")
    pack = prepare_application(JOB, factsheet=FACTSHEET, cv_text=CV, send=send)
    assert len(pack.blocked) == 2
    assert not pack.complete


def test_blocked_documents_are_written_under_a_name_that_says_so(tmp_path):
    send, _ = recorder("Led [[how many?]] people.")
    prepare_application(JOB, factsheet=FACTSHEET, cv_text=CV, send=send,
                        folder=tmp_path)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert all(n.startswith("BLOCKED-") for n in names), names


def test_a_clean_pack_writes_two_plain_files(tmp_path):
    send, _ = recorder("Identified savings at Example Group.")
    pack = prepare_application(JOB, factsheet=FACTSHEET, cv_text=CV,
                               send=send, folder=tmp_path)
    assert pack.complete
    assert sorted(p.suffix for p in tmp_path.iterdir()) == [".md", ".md"]


def test_the_brief_is_written_beside_the_documents(tmp_path):
    send, _ = recorder("## Where you are exposed\n\nRevenue management.")
    prepare_application(JOB, factsheet=FACTSHEET, cv_text=CV, send=send,
                        folder=tmp_path, want_cv=False, want_letter=False,
                        want_brief=True)
    written = list(tmp_path.iterdir())
    assert len(written) == 1
    assert written[0].name.endswith("-interview-brief.md")


# ---------------------------------------------------------------------------
# The summary
# ---------------------------------------------------------------------------

def test_the_summary_leads_with_what_is_missing():
    """"Two documents written" is not the useful fact."""
    send, _ = recorder()
    pack = prepare_application(JOB, factsheet=FACTSHEET, cv_text=CV, send=send)
    first = summarise(pack).splitlines()[0]
    assert "Not supported" in first
    assert "revenue management" in first.lower()


def test_the_summary_names_a_blocked_document():
    send, _ = recorder("Led [[how many?]] people.")
    pack = prepare_application(JOB, factsheet=FACTSHEET, cv_text=CV, send=send)
    assert "BLOCKED" in summarise(pack)


def test_a_posting_with_nothing_stated_says_so_rather_than_claiming_a_match():
    job = Job(provider="pasted", provider_job_id="p2", title="t", company="c",
              description_text="We want someone great.")
    send, _ = recorder()
    pack = prepare_application(job, factsheet=FACTSHEET, cv_text=CV, send=send)
    assert "No stated requirements" in summarise(pack)


def test_nothing_reaches_dawnlists_servers(monkeypatch):
    """Every request goes to Anthropic on the user's own key. If this ever
    grows a transport of its own, this fails."""
    import urllib.request

    def refuse(*a, **k):
        raise AssertionError("the pack must not make a network call itself")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    prepare_application(JOB, factsheet=FACTSHEET, cv_text=CV,
                        send=recorder()[0])


# ---------------------------------------------------------------------------
# The source CV, which the first real run proved was not actually required
# ---------------------------------------------------------------------------

def test_a_cv_cannot_be_written_without_a_cv_to_reorder():
    """The guard existed only at the caller, so the first live verification
    walked straight past it and produced a fluent CV out of the factsheet
    alone. Every line was true and the document was not the person's.

    A guard that exists at one entry point is not a guard.
    """
    import pytest

    with pytest.raises(ValueError) as exc:
        prepare_application(JOB, factsheet=FACTSHEET, cv_text="   ",
                            send=recorder()[0])
    assert "reorders the person's own document" in str(exc.value)


def test_a_letter_alone_does_not_need_the_cv():
    """A covering letter is written FROM the evidence, not reordered from a
    document, so the same requirement would be an invented obstacle."""
    send, seen = recorder()
    pack = prepare_application(JOB, factsheet=FACTSHEET, cv_text="",
                               send=send, want_cv=False)
    assert pack.letter is not None
    assert len(seen) == 1
