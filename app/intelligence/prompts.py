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

3. QUOTE THE DISQUALIFYING LINE. If you reject on a STATED requirement — a
   years floor, a credential, a hard skill — you must quote that line verbatim
   from the description, exactly as written. If you cannot quote it, you may not
   reject on it.

4. NEVER INFER A BAR THE POSTING DOES NOT STATE. Do not assume a requirement is
   implied by seniority or sector. One senior posting explicitly said the
   obvious prerequisite was not required.

5. AN UNFETCHABLE OR ABSENT REQUIREMENT IS "NOT CHECKED", NEVER A FAILURE. If
   the description is truncated or silent on something material, set
   requirement_checked to false and bucket it as possible, not rejected. An
   unread posting is an unknown; hiding it is the same failure as presenting an
   unqualified one.

6. GENUINELY AMBIGUOUS CALLS GO TO judgement-call, not to a silent decision.

Buckets:
  strong          — clearly fits the brief; the reader should look at this today
  possible        — a real stretch or a partial fit, worth their attention
  rejected        — does not fit, with a stated reason (and a quote if rule 3 applies)
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
                            "REQUIRED when rejecting on a stated requirement: the "
                            "line from the description, verbatim and unedited. "
                            "Null when the rejection is not based on a stated "
                            "requirement."
                        ),
                    },
                    "requirement_checked": {
                        "type": "boolean",
                        "description": (
                            "False when the description was truncated or silent on "
                            "something material. False forces the verdict out of "
                            "'rejected'."
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


FIRST_PASS_CHARS = 1200


def render_posting(ref: str, title: str, company: str, locations: str,
                   description: str, *, full: bool = False) -> str:
    """One posting block.

    First pass truncates to ~1,200 characters; a posting heading for a strong
    verdict is re-read in full before the verdict is trusted. The truncation is
    ANNOUNCED, so the model can set requirement_checked=false rather than
    treating a cut-off description as a complete one.
    """
    body = description or ""
    if not full and len(body) > FIRST_PASS_CHARS:
        body = body[:FIRST_PASS_CHARS] + "\n[TRUNCATED — description continues]"
    return (
        f"<posting ref=\"{ref}\">\n"
        f"title: {title}\n"
        f"company: {company}\n"
        f"location: {locations}\n"
        f"description:\n{body}\n"
        f"</posting>"
    )
