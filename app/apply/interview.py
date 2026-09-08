"""A briefing for the person, not a claim to an employer.

    request = build_brief_request(job=..., factsheet=..., cv_text=..., gaps=...)
    brief   = parse_brief(payload)

WHY THIS ONE IS NOT GUARDED LIKE THE CV
---------------------------------------
Everything else in this package refuses to write a claim the evidence does not
support, and blocks the export if one survives. This document does the
opposite on purpose: its most useful paragraph is the one that says "the
posting asks for X, you cannot evidence X, decide now what you will say when
they ask."

Applying the send-ready guard here would delete exactly that. A brief the
person reads before a conversation is not a document anybody else sees, so the
failure mode it has to avoid is different: not a false claim, but false
comfort.

For the same reason it names the weak spots FIRST. A brief that opens with
three paragraphs of what went well, and buries the unmet requirement at the
bottom, is read as reassurance and closed.

WHAT IT COSTS
-------------
One request on the user's own key, against a posting already fetched and a
factsheet already held. Nothing is sent to Dawnlist's servers, and the gap
analysis it builds on costs nothing at all.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.intelligence.assess import DRAFTING_MODEL

BRIEF_RULES = """\
You are briefing one person before an interview. They will read this alone,
and nobody else will see it. Be direct rather than encouraging.

Rules:

1. LEAD WITH WHAT IS WEAK. Open with the requirements the posting states that
   their evidence does not support, and say plainly for each one what they
   should decide to say. A brief that opens with reassurance gets skimmed and
   closed, and the unprepared answer is the one that costs the role.

2. NEVER INVENT EXPERIENCE, EVEN AS A SUGGESTION. Do not propose that they
   describe something they have not done, and do not offer a form of words
   that implies it. Where the honest answer is "I have not done that", say so
   and help them say what they have done instead.

3. QUALIFIERS SURVIVE. A figure they identified is not one they saved. If you
   suggest they mention it, keep the qualifier in the suggested wording.

4. BE SPECIFIC TO THIS POSTING. Generic interview advice is worthless and they
   can find it anywhere. Every point must be traceable to something the
   posting actually says or to something on their record.

5. NO PREAMBLE, NO PEP TALK, NO CLOSING ENCOURAGEMENT.

Structure, in this order and with these headings:

## Where you are exposed
## What to lead with
## Likely questions
## What to ask them
## Practicalities to confirm"""


@dataclass(frozen=True)
class InterviewBrief:
    body: str
    #: Verbatim from the posting, so a wrong emphasis is visibly wrong rather
    #: than merely surprising — the same reasoning as quoting the
    #: disqualifying line in an assessment.
    exposed_on: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.body.strip()


def _gap_block(gaps) -> str:
    """What the analysis already knows, handed over rather than re-derived.

    Passing this in is what makes rule 1 possible. Without it the model has to
    guess at exposure from the posting alone, and it guesses generously.
    """
    if gaps is None or getattr(gaps, "is_empty", True):
        return ("# Requirement analysis\n\nNone could be extracted from this "
                "posting. Do not invent exposure; work from the description.")
    missing = [r.phrase for r in gaps.missing_required]
    preferred = [r.phrase for r in gaps.missing_preferred]
    covered = [r.phrase for r in gaps.covered]
    return (
        "# Stated requirements the evidence does NOT support\n"
        + ("\n".join(f"- {m}" for m in missing) or "- none")
        + "\n\n# Preferences the evidence does not support\n"
        + ("\n".join(f"- {p}" for p in preferred) or "- none")
        + "\n\n# Requirements the evidence DOES support\n"
        + ("\n".join(f"- {c}" for c in covered) or "- none"))


def build_brief_request(*, title: str, company: str, description: str,
                        factsheet: str, cv_text: str, gaps=None,
                        model: str = DRAFTING_MODEL) -> dict:
    system = [
        {"type": "text", "text": BRIEF_RULES},
        {"type": "text",
         "text": (f"# Background factsheet\n\n{factsheet.strip()}\n\n"
                  f"# Their curriculum vitae\n\n{cv_text.strip()}"),
         "cache_control": {"type": "ephemeral"}},
    ]
    user = (f"# The posting\n\nRole: {title}\nCompany: {company}\n\n"
            f"{description.strip()}\n\n{_gap_block(gaps)}")
    return {
        "model": model,
        "max_tokens": 2500,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }


def parse_brief(payload, gaps=None) -> InterviewBrief:
    if isinstance(payload, str):
        text = payload
    else:
        blocks = payload.get("content", []) if isinstance(payload, dict) else []
        text = "".join(b.get("text", "") for b in blocks
                       if isinstance(b, dict) and b.get("type") == "text")

    text = re.sub(r"^\s*```(?:markdown|md)?\s*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)

    exposed = ()
    if gaps is not None and not getattr(gaps, "is_empty", True):
        exposed = tuple(r.phrase for r in gaps.missing_required)
    return InterviewBrief(body=text.strip(), exposed_on=exposed)
