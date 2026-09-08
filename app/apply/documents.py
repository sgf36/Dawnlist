"""Documents that go to an employer, and the guard they all share.

A tailored CV and a covering letter are the same problem as an outreach email:
a model writing about somebody's career will invent a plausible detail unless
something stops it, and the person sending it will not always catch it. The
guard is therefore imported from `app.outreach.drafts` rather than written
again — a second copy drifts, and the direction it drifts in is the one that
ships an invented job title.

WHAT IS DIFFERENT FROM AN EMAIL, AND WHY IT MATTERS MORE HERE
-------------------------------------------------------------
An outreach email makes two or three claims. A CV makes forty, most of them
numeric, and it is read by someone whose job is to check them. So the same
rule — every claim from the factsheet, gaps as visible placeholders, an
unresolved placeholder blocks the export — is not merely reused; it is the
reason this feature is worth having at all. Every competitor that tailors a CV
will happily write "led a team of twelve" from a factsheet that says
"coordinated across three teams".

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not invent structure. A tailored CV is the person's OWN CV
reordered and re-emphasised for one posting; it is not a new document
generated from a factsheet, because that produces a fluent CV describing a
career nobody had. The source CV text is supplied and the model is told to
work from it.

It also does not write a final file the user cannot see. Every document lands
on disk as Markdown, plainly readable, and the caller converts if it wants —
a .docx the user cannot diff is how a wrong claim survives review.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.intelligence.assess import DRAFTING_MODEL
from app.outreach.drafts import (LOOSE_PLACEHOLDER, PLACEHOLDER, NotSendReady,
                                 _is_editorial, safe_stem)

#: Shared by the CV and the letter. Stated once so the two cannot disagree
#: about what may be claimed, which would be the worst possible inconsistency:
#: a letter asserting something the CV beside it refuses to.
EVIDENCE_RULES = """\
Absolute rules. These override every instruction about tone, length or format.

1. EVERY FACTUAL CLAIM COMES FROM THE SUPPLIED EVIDENCE — the background
   factsheet and the person's own curriculum vitae. If the evidence does not
   support a claim, write [[a short description of what is missing]] and move
   on. Never invent an employer, a date, a title, a figure, a team size, a
   qualification or a technology.

2. FIGURES KEEP THEIR QUALIFIERS OR ARE DROPPED. A figure *identified* is
   never "saved" or "delivered". A figure *supported* is never "led" or
   "closed". A portfolio *analysed* is never "managed". This is where a
   truthful record turns into a false one, and it happens during compression.

3. NO LINE MANAGEMENT unless the evidence states it. Coordinating, mentoring
   and chairing are not "managed a team of N".

4. REORDER AND RE-EMPHASISE; DO NOT REWRITE HISTORY. You may choose which
   experience leads, which detail is expanded and which is cut. You may not
   change what happened, when, or for whom.

5. NEVER CLAIM A REQUIREMENT THE PERSON DOES NOT MEET. If the posting asks for
   something the evidence does not show, leave it out entirely. Do not imply
   it, do not gesture at it, and do not write "exposure to" as a way of
   claiming it. Omission is honest; implication is not.

6. GAPS USE DOUBLE SQUARE BRACKETS, always: [[like this]], never [like this].
   The single-bracket form is not recognised everywhere and can be sent by
   accident.
"""


@dataclass
class ApplyDocument:
    """One document written for one posting.

    `kind` is 'cv' or 'letter'. Kept as a plain string rather than an enum
    because it is also the filename stem and the two would have to be mapped
    anyway.
    """

    kind: str
    company: str
    posting_title: str
    body: str
    path: Path | None = None

    @property
    def placeholders(self) -> list[str]:
        """Every unresolved gap, in either bracket form.

        Identical in behaviour to `Draft.placeholders`, and for the same
        reason: the prompt asks for double brackets and the model sometimes
        writes single ones, so a guard that only recognises the form it asked
        for is not a guard.
        """
        found: list[str] = []
        found.extend(PLACEHOLDER.findall(self.body))
        remainder = PLACEHOLDER.sub("", self.body)
        found.extend(m for m in LOOSE_PLACEHOLDER.findall(remainder)
                     if not _is_editorial(m))
        return found

    @property
    def send_ready(self) -> bool:
        return not self.placeholders

    def assert_send_ready(self) -> None:
        if not self.send_ready:
            raise NotSendReady(
                "unresolved placeholders: "
                + ", ".join(repr(p) for p in self.placeholders)
                + ". Every claim must come from your factsheet or your CV; "
                  "fill these from evidence or cut the line.")


def write_document(doc: ApplyDocument, folder: Path, *,
                   allow_placeholders: bool = True) -> Path:
    """Write the document, naming a blocked one so it cannot be mistaken.

    The naming is the whole point. A blocked CV that looks like a finished one
    in a folder listing is a document somebody attaches to an application at
    eleven at night, and the placeholder is then read by a person who can
    check it.
    """
    folder.mkdir(parents=True, exist_ok=True)
    blocked = not doc.send_ready
    if blocked and not allow_placeholders:
        doc.assert_send_ready()

    stem = safe_stem(f"{doc.company}-{doc.posting_title}")
    prefix = "BLOCKED-" if blocked else ""
    path = folder / f"{prefix}{stem}-{doc.kind}.md"

    header = (f"<!-- {doc.kind.upper()} for {doc.posting_title} "
              f"at {doc.company} -->\n")
    if blocked:
        header += ("<!-- NOT READY TO SEND. Unresolved: "
                   + "; ".join(doc.placeholders) + " -->\n")
    path.write_text(header + "\n" + doc.body.strip() + "\n", encoding="utf-8")
    doc.path = path
    return path


def _evidence_block(factsheet: str, cv_text: str) -> str:
    return (f"# Background factsheet\n\n{factsheet.strip()}\n\n"
            f"# The person's own curriculum vitae\n\n{cv_text.strip()}")


def _posting_block(title: str, company: str, description: str,
                   gaps: "object | None") -> str:
    block = (f"# The posting\n\nRole: {title}\nCompany: {company}\n\n"
             f"{description.strip()}")
    if gaps is not None and not getattr(gaps, "is_empty", True):
        covered = [r.phrase for r in gaps.covered]
        missing = [r.phrase for r in gaps.missing_required]
        # The gap analysis is free and already computed, so telling the model
        # what the evidence DOES support keeps it from hunting for something
        # to say about a requirement that is genuinely unmet — which is where
        # rule 5 gets broken.
        block += ("\n\n# Requirements the evidence supports\n"
                  + ("\n".join(f"- {c}" for c in covered) or "- none"))
        block += ("\n\n# Requirements the evidence does NOT support — do not "
                  "claim these, in any form\n"
                  + ("\n".join(f"- {m}" for m in missing) or "- none"))
    return block


CV_INSTRUCTIONS = """\
Produce the person's curriculum vitae, reordered and re-emphasised for this one
posting.

Keep every role, employer and date exactly as the source CV has them. Choose
which roles lead, which achievements are expanded, and which detail is cut for
space. Where the posting names something the evidence genuinely shows, make it
easy to find — do not bury it in the fourth bullet of the third role.

Output Markdown. Keep the structure of a CV a recruiter expects: name, a short
professional summary, experience newest first, then education and
qualifications. No commentary, no explanation of what you changed."""

LETTER_INSTRUCTIONS = """\
Write a covering letter for this posting.

Three or four short paragraphs. Open by saying plainly which role this is for.
Then the two or three things in the evidence that most directly answer what the
posting asks for — specific, with the qualifier intact. Close with a plain
statement of interest and availability.

Do not restate the CV in prose. Do not open with a quotation, a rhetorical
question, or a sentence about the company's reputation. End at the sign-off:
write "Yours sincerely," or "Kind regards," and STOP — the sender signs their
own letters, and a bracketed stand-in for a name is one careless attachment
away from going out exactly like that.

Output Markdown, body only. No address block, no date, no subject line."""


def build_document_request(kind: str, *, title: str, company: str,
                           description: str, factsheet: str, cv_text: str,
                           gaps=None, model: str = DRAFTING_MODEL) -> dict:
    """One Messages request for a CV or a covering letter.

    The strong model, deliberately, for the same reason as outreach: this text
    reaches someone whose job includes checking it, and a mistake is not
    recoverable once the application is in.
    """
    if kind not in ("cv", "letter"):
        raise ValueError(f"kind must be 'cv' or 'letter', not {kind!r}")

    instructions = CV_INSTRUCTIONS if kind == "cv" else LETTER_INSTRUCTIONS
    system = [
        {"type": "text", "text": EVIDENCE_RULES},
        {"type": "text", "text": instructions},
        # The evidence is the large, stable part of the prompt and it is
        # identical for every posting, so the cache breakpoint goes at its end.
        {"type": "text", "text": _evidence_block(factsheet, cv_text),
         "cache_control": {"type": "ephemeral"}},
    ]
    return {
        "model": model,
        "max_tokens": 4000 if kind == "cv" else 1500,
        "system": system,
        "messages": [{"role": "user",
                      "content": _posting_block(title, company, description,
                                                gaps)}],
    }


def parse_document(kind: str, payload, *, company: str,
                   posting_title: str) -> ApplyDocument:
    """Turn a Messages response into a document.

    Accepts either the raw payload or a plain string, so a caller with a stub
    transport does not have to build an API-shaped dictionary to test with.
    """
    if isinstance(payload, str):
        text = payload
    else:
        blocks = payload.get("content", []) if isinstance(payload, dict) else []
        text = "".join(b.get("text", "") for b in blocks
                       if isinstance(b, dict) and b.get("type") == "text")

    # Models occasionally wrap a whole document in a fence despite being asked
    # for Markdown. Unwrapping is safe; leaving it means the first line of the
    # CV is ``` and nobody notices until it is attached.
    text = re.sub(r"^\s*```(?:markdown|md)?\s*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)

    return ApplyDocument(kind=kind, company=company,
                         posting_title=posting_title, body=text.strip())
