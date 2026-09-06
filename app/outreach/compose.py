"""Assembling an outreach drafting request.

This is where the three constraints meet:

  * **language** — the draft is written in the user's chosen locale, stated
    explicitly rather than left for the model to infer;
  * **voice** — the measured style of the user's own sent messages;
  * **truth** — every factual claim from the factsheet, gaps left as visible
    `[[placeholders]]`.

The ordering of the system blocks is fixed and the cache breakpoint sits on the
last one, for the same reason as the assessment prefix: caching is a prefix
match, so anything volatile above the breakpoint silently destroys the saving.
The voice profile sits INSIDE the cached region because it changes only when
the user imports more mail — not per draft.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.i18n import LOCALE_NAMES
from app.intelligence.assess import DRAFTING_MODEL
from app.outreach.voice import VoiceProfile, drafting_instructions

DRAFT_RULES = """\
You are drafting one cold outreach email for an individual exploring roles.

Absolute rules:

1. EVERY FACTUAL CLAIM COMES FROM THE FACTSHEET. If the factsheet does not
   support a claim, write it as [[a short description of what is missing]] and
   move on. Inventing a plausible career detail is the worst failure available
   here: the sender will not always catch it, and the recipient may be someone
   who can check.

2. FIGURES KEEP THEIR QUALIFIERS OR ARE DROPPED. A figure *identified* is never
   "saved" or "delivered". A figure *supported* is never "led" or "closed". A
   portfolio *analysed* is never "managed". Compression is where claims
   distort, and a short message is the most dangerous format — never shorten a
   qualifier into something untrue.

3. NO LINE MANAGEMENT unless the factsheet states it. Coordinating and
   mentoring are not "managed a team of N".

4. STATE THE ASK PLAINLY IN THE FIRST TWO OR THREE SENTENCES: that the sender
   is an individual exploring roles, not a vendor, not a consultancy, not
   selling anything. An abstract, commentary-led opening reads as a consulting
   pitch — this has happened to real recipients. "I'd value your perspective"
   is not a substitute for stating the ask.

5. PAST TENSE FOR ENDED ROLES. Never volunteer an employment gap and never
   invent an explanation for one.

Write only the email body. No subject line, no preamble, no commentary."""


@dataclass
class DraftBrief:
    recipient_name: str
    recipient_role: str
    company: str
    posting_title: str
    why_this_company: str = ""
    locale: str = "en"


def build_drafting_request(brief: DraftBrief, factsheet: str,
                           voice: VoiceProfile, *,
                           model: str = DRAFTING_MODEL) -> dict:
    """One Messages request for a single outreach draft.

    The strong model is used here by design: this is the text where a mistake
    is unrecoverable, because it goes to a real person who may be able to check
    it (handoff Part 3.3).
    """
    locale_name = LOCALE_NAMES.get(brief.locale, "English")
    system = [
        {"type": "text", "text": DRAFT_RULES},
        {"type": "text",
         "text": drafting_instructions(voice, brief.locale, locale_name)},
        {"type": "text",
         "text": f"# Background factsheet\n\n{factsheet.strip()}",
         "cache_control": {"type": "ephemeral"}},
    ]
    user = (
        f"Recipient: {brief.recipient_name}"
        + (f", {brief.recipient_role}" if brief.recipient_role else "")
        + f"\nCompany: {brief.company}"
        f"\nRole being explored: {brief.posting_title}\n"
        + (f"Why this company: {brief.why_this_company}\n"
           if brief.why_this_company else "")
        + f"\nWrite the email body in {locale_name}."
    )
    return {
        "model": model,
        "max_tokens": 2000,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
