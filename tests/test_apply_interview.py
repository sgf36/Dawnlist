"""The interview brief — the one document that is allowed to say "you cannot
evidence this".

Its failure mode is the opposite of the CV's. A CV must not make a claim the
evidence cannot support; a brief must not offer comfort the evidence cannot
support. So these tests check that the unmet requirements reach the model, and
that the brief is NOT run through the send-ready guard, which would delete the
most useful paragraph in it.
"""
from app.apply.interview import (InterviewBrief, build_brief_request,
                                 parse_brief)
from app.apply.keywords import analyse

POSTING = ("Requirements\n"
           "- Experience in revenue management required\n"
           "- Experience with feasibility studies required\n"
           "Desirable\n"
           "- Familiarity with Power BI is desirable\n")
FACTSHEET = "Supported feasibility studies at Example Group, 2021-2023."
CV = "Example Group — Senior Associate. Feasibility studies."


def gaps():
    return analyse(POSTING, evidence=FACTSHEET + " " + CV)


def user_text(**kw):
    req = build_brief_request(title="Analyst", company="Example",
                              description=POSTING, factsheet=FACTSHEET,
                              cv_text=CV, **kw)
    return req["messages"][0]["content"]


# ---------------------------------------------------------------------------
# What reaches the model
# ---------------------------------------------------------------------------

def test_the_unmet_requirements_are_handed_over_not_re_derived():
    """Rule 1 is only possible if the model is told what is missing. Left to
    infer exposure from the posting alone, it infers generously."""
    text = user_text(gaps=gaps())
    assert "revenue management" in text.lower()
    assert "does NOT support" in text


def test_what_the_evidence_does_support_is_also_sent():
    text = user_text(gaps=gaps())
    assert "DOES support" in text
    assert "feasibility studies" in text.lower()


def test_a_preference_is_kept_separate_from_a_requirement():
    """Preparing for a nice-to-have as though it were a bar wastes the
    evening before an interview."""
    text = user_text(gaps=gaps())
    # The section itself, not everything before it — the raw posting is quoted
    # above and legitimately contains the word.
    required = text.split("# Stated requirements the evidence does NOT support")[1]
    required = required.split("# Preferences")[0]
    assert "power bi" not in required.lower()
    preferences = text.split("# Preferences the evidence does not support")[1]
    assert "power bi" in preferences.lower()


def test_no_analysis_says_so_rather_than_implying_none_is_needed():
    text = user_text(gaps=None)
    assert "None could be extracted" in text
    assert "Do not invent exposure" in text


def test_the_rules_lead_with_exposure_not_reassurance():
    req = build_brief_request(title="t", company="c", description="d",
                              factsheet="f", cv_text="v")
    rules = req["system"][0]["text"]
    assert "LEAD WITH WHAT IS WEAK" in rules
    assert "NEVER INVENT EXPERIENCE" in rules


def test_the_evidence_is_the_cached_block():
    req = build_brief_request(title="t", company="c", description="d",
                              factsheet="f", cv_text="v")
    assert req["system"][-1].get("cache_control") == {"type": "ephemeral"}


# ---------------------------------------------------------------------------
# The brief itself
# ---------------------------------------------------------------------------

def test_a_brief_is_not_blocked_by_saying_you_cannot_evidence_something():
    """The whole point. Running the send-ready guard over this would delete
    the most useful paragraph in the document."""
    brief = parse_brief("## Where you are exposed\n\n"
                        "You cannot evidence revenue management. Decide now "
                        "what you will say when they ask.")
    assert not hasattr(brief, "send_ready")
    assert "cannot evidence" in brief.body


def test_the_exposure_list_is_carried_on_the_brief():
    brief = parse_brief("body", gaps=gaps())
    assert "revenue management" in [e.casefold() for e in brief.exposed_on]


def test_a_fenced_brief_is_unwrapped():
    brief = parse_brief("```markdown\n## Where you are exposed\n```")
    assert brief.body.startswith("## Where you are exposed")


def test_an_empty_brief_is_distinguishable():
    assert InterviewBrief(body="   ").is_empty
    assert not InterviewBrief(body="text").is_empty


def test_a_real_payload_shape_is_read():
    payload = {"content": [{"type": "text", "text": "Brief."}]}
    assert parse_brief(payload).body == "Brief."
