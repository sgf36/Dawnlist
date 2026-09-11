"""Prompts and the verdict schema.

The system prefix is assembled in a fixed order — rules, then fit brief, then
factsheet — because it is prompt-cached and caching is a PREFIX match: any byte
change anywhere invalidates everything after it. Nothing volatile (a date, a
run id, a posting count) may appear in here. The postings go in the user turn,
after the cache breakpoint.
"""
from __future__ import annotations

# Cheap to state, and it stops the two failure modes that cost the most:
# a confident rejection on a requirement the posting never made, and a title
# read instead of a description.
ASSESSMENT_RULES = """\
You are screening job postings against one person's fit brief. You are the last
step before a human reads the shortlist, so both directions of error are costly:
surfacing an unqualified role wastes their morning, and hiding a good one costs
them the role entirely.

Rules, in order of force:

1. SUBSTANCE OVER TITLE. A title promising analysis can hide a single-system
   administration job; a generic title can hide exactly the right role. Judge
   the description. Where title and substance diverge, say so by name in your
   reason so the reader can overrule you in one word.

2. NEVER TRUST THE METADATA. Seniority tags, industry tags and location strings
   are employer-entered and wrong often enough to be useless: a flagship GM role
   tagged "Not Applicable", hotel development tagged "Construction", a posting
   labelled "London Area" whose description places it two hours away. If the
   description contradicts a tag, the description wins.

3. QUOTE THE LINE EVERY REJECTION RESTS ON. A rejection must quote, verbatim
   from the posting block and exactly as written, the line it rests on: a
   stated requirement (a years floor, a credential, a hard skill), a line
   showing the role is a different function, or a whole field line such as
   "salary: ...". Quote a clause, not a word or two — at least about twenty
   characters, or the whole field line. If you cannot quote it, you may not
   reject.

4. NEVER INFER A BAR THE POSTING DOES NOT STATE. Do not assume a requirement is
   implied by seniority or sector. One senior posting explicitly said the
   obvious prerequisite was not required.

5. AN ABSENT REQUIREMENT IS "NOT CHECKED", NEVER A FAILURE. If the posting is
   silent on something the brief treats as material, set requirement_checked to
   false and do not reject on it. A truncated description is not by itself a
   reason to choose possible: judge what you can read, and set
   requirement_checked to false only when what is missing could change the
   verdict. An unread posting is an unknown; hiding it is the same failure as
   presenting an unqualified one.

6. GENUINELY AMBIGUOUS CALLS GO TO judgement-call, not to a silent decision.

7. A HARD CONSTRAINT IN THE BRIEF IS A REJECTION WHEN THE POSTING BREAKS IT. If
   the brief states a hard constraint — where the person will work, the contract
   type, a salary floor, how recent a posting must be — and the posting plainly
   breaks it, reject it. Quote the posting line that breaks it, and name the
   brief's line in your reason. A field that says "not stated" breaks nothing
   (rule 5), and where the description contradicts a field, the description
   wins (rule 2).

Buckets:
  strong          — clearly fits the brief; the reader should look at this today
  possible        — a real stretch or a partial fit, worth their attention
  rejected        — does not fit, with a stated reason and the quoted line it rests on (rule 3)
  judgement-call  — you could argue it either way; the human decides

Return one entry per posting. Be terse: one sentence of reason, no preamble."""


VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "job_ref": {
                        "type": "string",
                        "description": "The exact ref given in the posting block.",
                    },
                    "bucket": {
                        "type": "string",
                        "enum": ["strong", "possible", "rejected", "judgement-call"],
                    },
                    "reason": {
                        "type": "string",
                        "description": "One sentence. Name a title/substance divergence if there is one.",
                    },
                    "disqualifying_quote": {
                        "type": ["string", "null"],
                        "description": (
                            "REQUIRED for every rejection: the line of the posting "
                            "block the rejection rests on, verbatim and unedited — "
                            "at least a clause (about twenty characters) or a "
                            "whole field line. Null when not rejecting."
                        ),
                    },
                    "requirement_checked": {
                        "type": "boolean",
                        "description": (
                            "False when the posting is silent, or cut off, on "
                            "something that could change the verdict. A "
                            "truncation alone is not a reason. False forces the "
                            "verdict out of 'rejected'."
                        ),
                    },
                },
                "required": ["job_ref", "bucket", "reason", "disqualifying_quote",
                             "requirement_checked"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}


def system_prefix(fit_brief: str, factsheet: str) -> list[dict]:
    """The cached prefix. Order is fixed; contents must be byte-stable.

    The cache breakpoint goes on the LAST block, so everything above it is
    reused across every batch and every run until the user edits their brief.

    The factsheet is included because assessment genuinely uses it — a stated
    years floor is only judgeable against how long the person has actually
    worked. It travels to the user's OWN Anthropic account, on their own key,
    because Dawnlist is bring-your-own-key and no career data crosses Spencer's
    infrastructure at all.
    """
    return [
        {"type": "text", "text": ASSESSMENT_RULES},
        {"type": "text", "text": f"# Fit brief\n\n{fit_brief.strip()}"},
        {
            "type": "text",
            "text": f"# Background factsheet\n\n{factsheet.strip()}",
            "cache_control": {"type": "ephemeral"},
        },
    ]


#: The first pass reads a description whole up to here.
#:
#: It was 1,200. Descriptions average about 7,400 characters, so every first
#: verdict was formed on roughly a sixth of the posting, and the old rule 5
#: told the model to bucket what it could not see as `possible` — so the pile
#: filled with possibles nobody could act on, while rejections were formed on
#: the same sixth. The assessment model is the cheap one: a whole average
#: posting is about 1,900 input tokens. Several times the average, so only an
#: outlier is cut, and a cut verdict is re-read alone in full before it is
#: trusted (`assess._second_pass`).
FIRST_PASS_CHARS = 20_000

#: Written out rather than leaving the line off. An omitted line reads as an
#: oversight in the prompt; "not stated" reads as the absence it is, which is
#: what rule 5 needs the model to see before it rejects on a missing field.
NOT_STATED = "not stated"


def render_posting(ref: str, title: str, company: str, locations: str,
                   description: str, *, full: bool = False,
                   salary: str | None = None, employment: str = "",
                   posted: str = "", country: str = "") -> str:
    """One posting block.

    The first pass sends the description whole up to FIRST_PASS_CHARS; beyond
    that it is cut, and the cut is ANNOUNCED so the model can tell a cut-off
    description from a complete one. `full=True` sends everything.

    Salary, contract type, posting date and country are here because a brief's
    hard constraints are stated in exactly those terms — full-time only, a pay
    floor, a country — and the model was never shown them. It could only guess
    from the description or reject on an assumption rule 4 forbids, so a
    constraint the user wrote down was one nothing could apply.
    """
    body = description or ""
    if not full and len(body) > FIRST_PASS_CHARS:
        body = body[:FIRST_PASS_CHARS] + "\n[TRUNCATED — description continues]"
    return (
        f"<posting ref=\"{ref}\">\n"
        f"title: {title}\n"
        f"company: {company}\n"
        f"location: {locations}\n"
        f"country: {country or NOT_STATED}\n"
        f"employment type: {employment or NOT_STATED}\n"
        f"salary: {salary or NOT_STATED}\n"
        f"posted: {posted or NOT_STATED}\n"
        f"description:\n{body}\n"
        f"</posting>"
    )
