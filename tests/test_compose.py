"""Drafting request: language, voice and truth, assembled together."""
from app.outreach.compose import DraftBrief, build_drafting_request
from app.outreach.voice import build_profile

FORMAL = """Dear Ms Bloggs,

I hope you are well. I am writing regarding the role.
I would be grateful for a short conversation.

Kind regards,
Spencer
"""
VOICE = build_profile([FORMAL] * 6)
FACTS = "Spencer Fields. Cornell SHA 2019. Identified GBP 4.2m in opportunities."


def brief(**kw):
    base = dict(recipient_name="Jo Bloggs", recipient_role="Managing Director",
                company="Acme Hotels", posting_title="Head of Strategy")
    base.update(kw)
    return DraftBrief(**base)


def test_the_target_language_is_stated_explicitly():
    req = build_drafting_request(brief(locale="fr"), FACTS, VOICE)
    blob = " ".join(b["text"] for b in req["system"])
    assert "French (fr)" in blob
    assert "French" in req["messages"][0]["content"]


def test_an_rtl_locale_carries_its_note():
    req = build_drafting_request(brief(locale="ar"), FACTS, VOICE)
    assert "right-to-left" in " ".join(b["text"] for b in req["system"])


def test_an_unknown_locale_falls_back_to_english():
    req = build_drafting_request(brief(locale="zz"), FACTS, VOICE)
    assert "English" in " ".join(b["text"] for b in req["system"])


def test_the_voice_profile_is_included():
    blob = " ".join(b["text"] for b in
                    build_drafting_request(brief(), FACTS, VOICE)["system"])
    assert "kind regards" in blob and "formal" in blob


def test_the_factsheet_rule_survives_the_voice_block():
    """Style guidance must never become permission to invent biography."""
    blob = " ".join(b["text"] for b in
                    build_drafting_request(brief(), FACTS, VOICE)["system"])
    assert "EVERY FACTUAL CLAIM COMES FROM THE FACTSHEET" in blob
    assert "STYLE ONLY" in blob
    assert "[[placeholder]]" in blob or "[[a short description" in blob


def test_the_distortion_rules_are_present():
    blob = " ".join(b["text"] for b in
                    build_drafting_request(brief(), FACTS, VOICE)["system"])
    for rule in ("identified", "supported", "analysed", "line management",
                 "individual exploring roles"):
        assert rule.lower() in blob.lower()


def test_the_cache_breakpoint_is_on_the_last_system_block():
    req = build_drafting_request(brief(), FACTS, VOICE)
    assert "cache_control" not in req["system"][0]
    assert req["system"][-1]["cache_control"] == {"type": "ephemeral"}


def test_the_request_is_byte_stable_for_the_same_inputs():
    a = build_drafting_request(brief(), FACTS, VOICE)
    b = build_drafting_request(brief(), FACTS, VOICE)
    assert a == b, "a volatile prefix silently destroys the cache saving"


def test_drafting_uses_the_strong_model():
    """The one place a mistake is unrecoverable, so not the cheap model."""
    req = build_drafting_request(brief(), FACTS, VOICE)
    assert req["model"] == "claude-sonnet-5"
