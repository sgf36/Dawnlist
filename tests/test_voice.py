"""Tone of voice: measured from the user's own sent mail, style only."""
from pathlib import Path

from app.outreach.voice import (MIN_SAMPLE, VoiceProfile, build_profile,
                                drafting_instructions, extract_bodies,
                                profile_from_files, strip_quoted)

FORMAL = """Dear Ms Bloggs,

I hope you are well. I am writing regarding the asset management role.
I would be grateful for the opportunity to discuss it.

Kind regards,
Spencer
"""

CASUAL = """Hi Jo,

Thanks for the quick reply! I've had a look and it's a great fit.
Let's catch up soon.

Cheers,
Spencer
"""


def test_quoted_reply_text_is_not_the_users_writing():
    raw = ("Thanks, that works.\n\n"
           "On Tuesday, Jo wrote:\n> the original message\n> more quoted text")
    out = strip_quoted(raw)
    assert "Thanks, that works." in out
    assert "original message" not in out


def test_client_signature_block_is_stripped():
    assert "Sent from my" not in strip_quoted("Real text.\n\nSent from my iPhone")


def test_formal_and_casual_are_distinguished():
    formal = build_profile([FORMAL] * MIN_SAMPLE)
    casual = build_profile([CASUAL] * MIN_SAMPLE)
    assert formal.formality == "formal"
    assert casual.formality == "casual"


def test_greeting_and_signoff_habits_are_learned():
    p = build_profile([FORMAL] * MIN_SAMPLE)
    assert "dear" in p.top_greetings
    assert "kind regards" in p.top_signoffs


def test_contraction_rate_separates_the_two_registers():
    formal = build_profile([FORMAL] * MIN_SAMPLE)
    casual = build_profile([CASUAL] * MIN_SAMPLE)
    assert casual.contractions_per_100w > formal.contractions_per_100w


def test_a_thin_sample_is_not_usable_and_says_so():
    p = build_profile([FORMAL])
    assert not p.is_usable
    assert "No tone profile is available" in p.as_prompt()


def test_an_empty_import_is_not_a_crash():
    p = build_profile([])
    assert p.sample_size == 0 and not p.is_usable


def test_the_profile_carries_style_never_content():
    """The profile must not become a second, unchecked source of biography."""
    body = FORMAL.replace("the asset management role",
                          "my £4.2m savings at Marriott as Director")
    p = build_profile([body] * MIN_SAMPLE)
    blob = p.as_prompt()
    for leak in ("4.2m", "Marriott", "Director", "savings"):
        assert leak not in blob, f"{leak!r} leaked from content into the tone profile"


def test_the_prompt_reasserts_the_factsheet_rule():
    p = build_profile([FORMAL] * MIN_SAMPLE)
    assert "STYLE ONLY" in p.as_prompt()
    assert "factsheet" in p.as_prompt() and "[[placeholder]]" in p.as_prompt()


# -- file import: no mailbox, no credentials --------------------------------
def test_eml_files_are_read(tmp_path):
    p = tmp_path / "a.eml"
    p.write_text("From: s@example.com\r\nTo: j@example.com\r\n"
                 "Subject: Hi\r\n\r\n" + FORMAL, encoding="utf-8")
    bodies = extract_bodies([p])
    assert bodies and "Dear Ms Bloggs" in bodies[0]


def test_a_corrupt_file_does_not_stop_the_import(tmp_path):
    good = tmp_path / "good.eml"
    good.write_text("Subject: Hi\r\n\r\n" + FORMAL, encoding="utf-8")
    bad = tmp_path / "bad.eml"
    bad.write_bytes(b"\x00\xff not an email")
    assert len(extract_bodies([bad, good])) >= 1


def test_profile_from_files_end_to_end(tmp_path):
    for i in range(MIN_SAMPLE):
        (tmp_path / f"m{i}.eml").write_text(
            "Subject: Hi\r\n\r\n" + FORMAL, encoding="utf-8")
    p = profile_from_files(sorted(tmp_path.glob("*.eml")))
    assert p.is_usable and p.formality == "formal"


# -- language ---------------------------------------------------------------
def test_drafting_instructions_name_the_language_explicitly():
    p = build_profile([FORMAL] * MIN_SAMPLE)
    out = drafting_instructions(p, "fr", "French")
    assert "French (fr)" in out
    assert "Do not translate an English draft literally" in out


def test_rtl_languages_get_a_note():
    p = build_profile([FORMAL] * MIN_SAMPLE)
    assert "right-to-left" in drafting_instructions(p, "ar", "Arabic")
    assert "right-to-left" not in drafting_instructions(p, "de", "German")
