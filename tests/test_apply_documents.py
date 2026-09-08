"""Tailored CVs and covering letters, and the guard that stops them lying.

The reason these tests exist in this shape: a CV makes roughly forty claims,
most of them numeric, and it is read by somebody whose job includes checking
them. The tests therefore assert that an unsupported claim BLOCKS the export
rather than that the prompt asks nicely.
"""
import pytest

from app.apply.documents import (ApplyDocument, build_document_request,
                                 parse_document, write_document)
from app.apply.keywords import analyse
from app.outreach.drafts import NotSendReady

FACTSHEET = "Identified £2.1m of savings at Example Group, 2021-2023."
CV = "Example Group — Senior Associate, 2021-2023. Analysis and feasibility."
POSTING = ("Requirements\n- Experience in revenue management\n"
           "- Experience with feasibility studies required\n")


def doc(body, kind="cv"):
    return ApplyDocument(kind=kind, company="Example", posting_title="Analyst",
                         body=body)


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------

def test_a_supported_document_is_send_ready():
    assert doc("Identified £2.1m of savings.").send_ready


def test_a_double_bracket_gap_blocks_it():
    d = doc("Led a team of [[how many?]] people.")
    assert not d.send_ready
    assert d.placeholders == ["how many?"]


def test_a_single_bracket_gap_blocks_it_too():
    """The prompt asks for double brackets; models write single ones anyway.

    A guard that only recognises the form it requested is not a guard — this
    is the exact failure that once marked a draft ending "Best regards,
    [Your name]" as ready to send.
    """
    d = doc("Managed [team size] direct reports.")
    assert not d.send_ready


def test_a_citation_or_sic_does_not_block():
    assert doc("Revenue rose [sic] by a third.").send_ready
    assert doc("See appendix [3].").send_ready


def test_asserting_send_ready_names_what_is_missing():
    with pytest.raises(NotSendReady) as exc:
        doc("Delivered [[what exactly?]].").assert_send_ready()
    assert "what exactly?" in str(exc.value)


# ---------------------------------------------------------------------------
# Writing to disk — the naming is the safety feature
# ---------------------------------------------------------------------------

def test_a_blocked_document_is_named_so_it_cannot_be_mistaken(tmp_path):
    path = write_document(doc("Led [[how many?]] people."), tmp_path)
    assert path.name.startswith("BLOCKED-"), (
        "a blocked CV that looks finished in a folder listing is one that "
        "gets attached at eleven at night")
    assert "NOT READY TO SEND" in path.read_text(encoding="utf-8")


def test_a_clean_document_is_not_prefixed(tmp_path):
    path = write_document(doc("Identified £2.1m of savings."), tmp_path)
    assert not path.name.startswith("BLOCKED-")
    assert path.name.endswith("-cv.md")


def test_writing_a_blocked_document_can_be_refused_outright(tmp_path):
    with pytest.raises(NotSendReady):
        write_document(doc("Led [[how many?]]."), tmp_path,
                       allow_placeholders=False)


def test_the_letter_and_the_cv_land_on_different_files(tmp_path):
    a = write_document(doc("x", kind="cv"), tmp_path)
    b = write_document(doc("x", kind="letter"), tmp_path)
    assert a != b


# ---------------------------------------------------------------------------
# The request
# ---------------------------------------------------------------------------

def test_the_rules_come_first_and_the_evidence_is_cached():
    req = build_document_request("cv", title="Analyst", company="Example",
                                 description=POSTING, factsheet=FACTSHEET,
                                 cv_text=CV)
    system = req["system"]
    assert "EVERY FACTUAL CLAIM" in system[0]["text"]
    assert system[-1].get("cache_control") == {"type": "ephemeral"}, (
        "the evidence is identical for every posting and is the whole reason "
        "caching is worth anything here")


def test_the_source_cv_is_supplied_not_invented():
    """A CV generated from a factsheet alone is fluent and describes a career
    nobody had. The person's own document has to be in the prompt."""
    req = build_document_request("cv", title="Analyst", company="Example",
                                 description=POSTING, factsheet=FACTSHEET,
                                 cv_text=CV)
    assert CV in "".join(b["text"] for b in req["system"])


def test_an_unknown_kind_is_refused():
    with pytest.raises(ValueError):
        build_document_request("essay", title="t", company="c",
                               description="d", factsheet="f", cv_text="v")


def test_unmet_requirements_are_named_as_forbidden_not_omitted():
    """The gap analysis is free and already computed. Telling the model what
    is NOT supported is what stops it reaching for "exposure to" as a way of
    implying a requirement the person does not meet."""
    gaps = analyse(POSTING, evidence=FACTSHEET + " " + CV)
    req = build_document_request("cv", title="Analyst", company="Example",
                                 description=POSTING, factsheet=FACTSHEET,
                                 cv_text=CV, gaps=gaps)
    user = req["messages"][0]["content"]
    assert "do not claim these" in user.lower()
    assert "revenue management" in user.lower()


def test_a_letter_asks_for_fewer_tokens_than_a_cv():
    cv = build_document_request("cv", title="t", company="c", description="d",
                                factsheet="f", cv_text="v")
    letter = build_document_request("letter", title="t", company="c",
                                    description="d", factsheet="f",
                                    cv_text="v")
    assert letter["max_tokens"] < cv["max_tokens"]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def test_a_fenced_document_is_unwrapped():
    """Asked for Markdown, a model sometimes returns it inside a fence. Left
    alone, the first line of the CV is ``` and nobody notices until it is
    attached to an application."""
    d = parse_document("cv", "```markdown\n# Spencer Fields\n```",
                       company="Example", posting_title="Analyst")
    assert d.body.startswith("# Spencer Fields")
    assert "```" not in d.body


def test_a_plain_string_is_accepted_so_tests_need_no_api_shape():
    d = parse_document("letter", "Dear Sir,", company="c", posting_title="t")
    assert d.body == "Dear Sir,"


def test_a_real_payload_shape_is_read():
    payload = {"content": [{"type": "text", "text": "Body."},
                           {"type": "thinking", "thinking": "ignore me"}]}
    d = parse_document("cv", payload, company="c", posting_title="t")
    assert d.body == "Body."
