"""The onboarding interview — CV corpus in, factsheet and fit brief out.

The user corrects a DRAFT rather than composing from a blank page. That is the
whole design: people under-report their own experience when asked to write it
out, and over-claim when asked to justify it. A draft they can argue with
produces a better factsheet than either.

The factsheet governs **what may be said**; the fit brief governs **what gets
surfaced**. They are separate on purpose and must not be merged — one is about
truth, the other about taste, and collapsing them lets an ambition quietly
become a claim.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.intelligence.assess import DRAFTING_MODEL

#: Shown verbatim in the UI. Both sentences are load-bearing: users routinely
#: hand over a tidied CV and lose exactly the history the screen needs.
INGEST_GUIDANCE = """\
Drag in every version of your CV you still have — including the old ones, the
ones tailored for a specific job, and the ones you think are out of date.

**Do not tidy them up first.** Different versions describe the same role in
different words, and those differences are the evidence. A single polished CV
is the least useful thing you can give this step.

If your CV starts part-way through your career, say so. Early roles are often
cut from a senior CV, and the screen needs them: they are what tell it which
operational jobs you have actually done, rather than only the ones you have
managed."""

#: The distortion rules, stated to the model as a schema for the factsheet.
#: Each one is a real, recurring failure, not a hypothetical.
FACTSHEET_RULES = """\
You are drafting a background factsheet: the single record of what this person's
outreach is permitted to assert about their career. It will be used to write
messages to real people who may be able to check the claims.

Draft from the CV corpus provided. Where the corpus does not support something,
leave a [[placeholder]] describing what is missing rather than filling the gap.

The recurring distortions, all of which must be avoided:

- **Figures are directional, not decorative.** A figure *identified* is never
  "saved" or "delivered". A figure *supported* is never "led" or "closed". A
  portfolio *analysed* is never "managed". Record the verb the evidence
  supports, and record the figure with that verb attached.
- **No line management unless it happened.** Coordinating, mentoring and
  chairing are not "managed a team of N". If a headcount appears, it must be
  traceable to a specific line in the corpus.
- **Titles differ between CV variants for the same role.** Record every variant
  you find, each tagged with the document it came from. Never blend them into
  one canonical title, and never invent a seniority the corpus does not show.
- **Past tense for ended roles.** Record start and end dates as the corpus
  gives them. Never volunteer an employment gap and never invent an explanation
  for one.

Also produce a `must_never_claim` list: things that look supportable from the
corpus but are not — a title held only briefly, a figure whose verb is weaker
than it appears, a responsibility that was shared. This list is as valuable as
the claims themselves."""

FIT_BRIEF_RULES = """\
You are drafting a fit brief: what kinds of role should be surfaced to this
person every morning.

It must contain, explicitly:
- role types, in their words;
- the seniority band as an explicit RANGE, not a single level;
- hard constraints: location, permanent versus contract, salary floor;
- named target employers;
- **what looks like a fit but is not, by name.** This is the section people
  skip and the one that does most of the work.

Two rules for drafting it:

1. **Draft from the evidence, not from the ambition.** Where the CV corpus and
   the person's stated aim disagree — a band they have not yet worked at, a
   function they have supported but not owned — surface that disagreement
   plainly as a question for them, rather than quietly picking one. It is their
   call, but it must be a call they make knowingly.
2. **Demand the disqualifiers.** A brief with no explicit exclusions cannot
   screen anything out, and a screen that excludes nothing is a list of every
   job in the country."""


@dataclass
class CVDocument:
    """One document from the corpus, with where it came from."""
    name: str
    text: str

    @property
    def is_usable(self) -> bool:
        return len(self.text.strip()) > 200


@dataclass
class Corpus:
    documents: list[CVDocument] = field(default_factory=list)

    @property
    def usable(self) -> list[CVDocument]:
        return [d for d in self.documents if d.is_usable]

    @property
    def warnings(self) -> list[str]:
        """Everything worth telling the user before the interview starts."""
        out: list[str] = []
        if not self.usable:
            out.append("No readable CV text was found — check the files opened "
                       "correctly before continuing.")
        elif len(self.usable) == 1:
            out.append("Only one CV version was read. Different versions "
                       "describe the same role in different words, and those "
                       "differences are the evidence — add the older ones if "
                       "you still have them.")
        unreadable = [d.name for d in self.documents if not d.is_usable]
        if unreadable:
            out.append("Could not read enough text from: "
                       + ", ".join(unreadable))
        if self.earliest_year() and self.earliest_year() > 2010:
            out.append(
                f"The earliest role found starts in {self.earliest_year()}. If "
                "you worked before then, add it — early operational roles are "
                "what tell the screen which jobs you have actually done.")
        return out

    def earliest_year(self) -> int | None:
        years = [int(y) for d in self.usable
                 for y in re.findall(r"\b(19[89]\d|20[0-4]\d)\b", d.text)]
        return min(years) if years else None

    def as_prompt(self) -> str:
        return "\n\n".join(
            f"<document name=\"{d.name}\">\n{d.text}\n</document>"
            for d in self.usable)


FACTSHEET_SCHEMA = {
    "type": "object",
    "properties": {
        "roles": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "employer": {"type": "string"},
                    "title_variants": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "source_document": {"type": "string"},
                            },
                            "required": ["title", "source_document"],
                            "additionalProperties": False,
                        },
                    },
                    "started": {"type": "string"},
                    "ended": {"type": ["string", "null"]},
                    "claims": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "statement": {"type": "string"},
                                # The verb the evidence supports, kept apart
                                # from the figure so compression cannot
                                # silently upgrade it.
                                "verb": {
                                    "type": "string",
                                    "enum": ["identified", "supported",
                                             "analysed", "delivered", "led",
                                             "managed", "owned"],
                                },
                                "figure": {"type": ["string", "null"]},
                                "source_document": {"type": "string"},
                            },
                            "required": ["statement", "verb", "figure",
                                         "source_document"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["employer", "title_variants", "started", "ended",
                             "claims"],
                "additionalProperties": False,
            },
        },
        "must_never_claim": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["roles", "must_never_claim", "open_questions"],
    "additionalProperties": False,
}


def build_factsheet_request(corpus: Corpus, *,
                            model: str = DRAFTING_MODEL) -> dict:
    """The factsheet draft. Uses the strong model: this is the record every
    later claim is checked against, so an error here propagates everywhere."""
    return {
        "model": model,
        "max_tokens": 8000,
        "system": [{"type": "text", "text": FACTSHEET_RULES}],
        "messages": [{"role": "user", "content": corpus.as_prompt()}],
        "output_config": {"format": {"type": "json_schema",
                                     "name": "factsheet",
                                     "schema": FACTSHEET_SCHEMA}},
    }


def build_brief_request(corpus: Corpus, stated_aim: str, *,
                        model: str = DRAFTING_MODEL) -> dict:
    return {
        "model": model,
        "max_tokens": 4000,
        "system": [{"type": "text", "text": FIT_BRIEF_RULES}],
        "messages": [{"role": "user", "content":
                      f"{corpus.as_prompt()}\n\n<stated_aim>\n{stated_aim}\n"
                      f"</stated_aim>"}],
    }


#: Verbs the factsheet may record, ordered weakest to strongest. Outreach may
#: only ever weaken a claim, never strengthen it.
VERB_STRENGTH = {"identified": 0, "supported": 1, "analysed": 1,
                 "delivered": 2, "led": 3, "managed": 3, "owned": 4}


def verb_is_upgrade(recorded: str, used: str) -> bool:
    """True when outreach used a stronger verb than the factsheet supports.

    This is the single most damaging failure in the product: "delivered £Xm"
    where "identified £Xm in opportunities" is what happened. It is also the
    most tempting one, because the short form reads better.
    """
    if recorded not in VERB_STRENGTH or used not in VERB_STRENGTH:
        return False
    return VERB_STRENGTH[used] > VERB_STRENGTH[recorded]
