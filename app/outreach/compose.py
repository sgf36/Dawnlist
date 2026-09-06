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
from datetime import date

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

6. END AT THE SIGN-OFF. Write "Best regards," or the sender's usual closing and
   STOP. Do not write their name and do not leave a bracketed stand-in for it —
   they sign their own messages. Observed live: a draft ended "Best regards,
   [Your name]", which is one careless send away from reaching a real person
   exactly like that.

7. GAPS USE DOUBLE SQUARE BRACKETS, always: [[like this]], never [like this].
   The single-bracket form is not recognised as a gap and can be sent by
   accident.

Write only the email body. No subject line, no preamble, no commentary."""


@dataclass
class DraftBrief:
    recipient_name: str
    recipient_role: str
    company: str
    posting_title: str
    why_this_company: str = ""
    locale: str = "en"
    #: Which rung of the cadence this is. 0 is first contact; anything above is
    #: a follow-up to a message this person has already been sent.
    #:
    #: Without it every follow-up was drafted as a cold approach, so a real
    #: recipient got "I am an individual exploring roles, and I am not selling
    #: anything" a second and a third time — which reads as though the sender
    #: had forgotten writing, and is worse than not following up at all.
    rung: int = 0
    #: When the previous message actually went out, so a follow-up can refer to
    #: it instead of gesturing vaguely at "my earlier email".
    last_contacted_on: date | None = None

    @property
    def is_follow_up(self) -> bool:
        return self.rung > 0


def _contact_history(brief: DraftBrief) -> str:
    """Tell the model what this person has already received.

    Stated as fact plus an instruction, because the model cannot infer either:
    nothing else in the request distinguishes a first approach from a third,
    and the default shape of "write a cold outreach email" is to reintroduce.
    """
    if not brief.is_follow_up:
        return "This is the FIRST message to this person.\n"

    when = (f" on {brief.last_contacted_on.isoformat()}"
            if brief.last_contacted_on else "")
    ordinal = {1: "second", 2: "third", 3: "fourth"}.get(brief.rung,
                                                        f"{brief.rung + 1}th")
    return (
        f"This is a FOLLOW-UP — the {ordinal} message to this person. They "
        f"were already written to{when} and have not replied.\n"
        "Do NOT reintroduce the sender or restate who they are: they have read "
        "that already, and repeating it reads as though the sender has "
        "forgotten writing. Refer briefly to the earlier message, add one new "
        "and specific reason for writing, and keep it shorter than the first. "
        "Do not express disappointment and do not ask why they have not "
        "replied.\n")


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
        + _contact_history(brief)
        + f"\nWrite the email body in {locale_name}."
    )
    return {
        "model": model,
        "max_tokens": 2000,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
