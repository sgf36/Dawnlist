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

from app.intelligence.assess import ASSESSMENT_MODEL, DRAFTING_MODEL

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
- **A qualification absent from the corpus does not exist.** Where the work
  described sits near a licensed or chartered profession — valuation, appraisal,
  accountancy, surveying, financial advice — say so explicitly in
  `must_never_claim`: that no such designation is evidenced and none may be
  implied. Proximity to the work is how a credential gets implied without ever
  being stated, and this is the one distortion the recipient is most likely to
  be qualified to catch.

Also produce a `must_never_claim` list: things that look supportable from the
corpus but are not — a title held only briefly, a figure whose verb is weaker
than it appears, a responsibility that was shared, a credential implied by
adjacency. This list is as valuable as the claims themselves."""

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


#: `max_tokens` budgets THINKING PLUS OUTPUT, not output alone.
#:
#: Measured against Spencer's real five-CV corpus (13,469 input tokens): at
#: 8,000 the model spent the whole budget reasoning and returned one thinking
#: block with no text at all — `stop_reason: max_tokens`, zero output, and a
#: user who could never finish onboarding. A two-CV synthetic corpus fitted
#: comfortably, which is precisely why every test passed. A real corpus is
#: several times the size of a plausible fixture, and the factsheet it produces
#: is long by design: one entry per claim, plus the whole never-claim list.
FACTSHEET_MAX_TOKENS = 32000
BRIEF_MAX_TOKENS = 16000


def build_factsheet_request(corpus: Corpus, *,
                            model: str = DRAFTING_MODEL) -> dict:
    """The factsheet draft. Uses the strong model: this is the record every
    later claim is checked against, so an error here propagates everywhere."""
    return {
        "model": model,
        "max_tokens": FACTSHEET_MAX_TOKENS,
        "system": [{"type": "text", "text": FACTSHEET_RULES}],
        "messages": [{"role": "user", "content": corpus.as_prompt()}],
        "output_config": {"format": {"type": "json_schema",
                                     "schema": FACTSHEET_SCHEMA}},
    }


def build_brief_request(corpus: Corpus, stated_aim: str, *,
                        model: str = DRAFTING_MODEL) -> dict:
    return {
        "model": model,
        "max_tokens": BRIEF_MAX_TOKENS,
        "system": [{"type": "text", "text": FIT_BRIEF_RULES}],
        "messages": [{"role": "user", "content":
                      f"{corpus.as_prompt()}\n\n<stated_aim>\n{stated_aim}\n"
                      f"</stated_aim>"}],
    }


#: Small, cheap and entirely mechanical: pull role titles out of prose. The
#: strong model is for the factsheet, where an error propagates into every
#: later claim; this is a list of search terms the user then edits.
SEARCH_TITLES_MAX_TOKENS = 1000

SEARCH_TITLES_RULES = """\
Read what this person says they are looking for and return the JOB TITLES a \
job board would list those roles under.

Rules, all of which matter because each title is billed per posting it \
returns:

- Return TITLES ONLY. Not skills, not industries, not locations, not \
  sentences. "Revenue Manager" is a title; "the underwriting-to-property \
  seam", "three kinds of investment" and "London" are not.
- Two to four words each. A title nobody writes on a job advert matches \
  nothing and costs nothing but tells the user the app misunderstood them.
- Use the ordinary market wording, not the person's own phrasing. If they \
  describe running a hotel, that is "General Manager" and "Hotel Manager".
- Between three and eight of them. Prefer the obvious ones; the user can add \
  their own.
- If the text says nothing about what work they want, return an empty list \
  rather than inventing titles."""

SEARCH_TITLES_SCHEMA = {
    "type": "object",
    "properties": {
        "titles": {"type": "array", "items": {"type": "string"},
                   "maxItems": 8},
    },
    "required": ["titles"],
}


def build_search_titles_request(stated_aim: str, *,
                                model: str = ASSESSMENT_MODEL) -> dict:
    """Job titles to seed the searches with, from what the user typed.

    WHY A MODEL CALL AND NOT A SPLIT. The rule-based version split the text on
    punctuation and kept any run of two to five words, which offered a new
    user "including the underwriting-to-property-implementation seam" and
    "three kinds investment" as searches to switch on — on a screen that says,
    two lines above, that each one costs money per posting it returns.

    A search the user did not write and would not recognise is worse than no
    search: it teaches them the app did not understand them, at the first
    screen where they could have found that out.
    """
    return {
        "model": model,
        "max_tokens": SEARCH_TITLES_MAX_TOKENS,
        "system": [{"type": "text", "text": SEARCH_TITLES_RULES}],
        "messages": [{"role": "user", "content": stated_aim}],
        "output_config": {"format": {"type": "json_schema",
                                     "schema": SEARCH_TITLES_SCHEMA}},
    }


def text_of(response) -> str:
    """The text of a response, or a readable failure.

    A truncated response carries no text at all, so the caller's `json.loads`
    fails with "Expecting value: line 1 column 1" — a message that describes
    the symptom, names neither the cause nor anything the user could do about
    it, and is what would otherwise reach the interview screen.
    """
    text = "".join(b.text for b in response.content if b.type == "text")
    if text.strip():
        return text
    if getattr(response, "stop_reason", None) == "max_tokens":
        raise RuntimeError(
            "the model ran out of room before writing anything — these CVs are "
            "long enough that the reasoning used the whole budget. Try fewer "
            "documents, or report it: the limit needs raising.")
    raise RuntimeError(
        f"the model returned no text (stop reason: "
        f"{getattr(response, 'stop_reason', 'unknown')})")


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


# ---------------------------------------------------------------------------
# From the model's structured draft to something a person can correct
# ---------------------------------------------------------------------------

def render_factsheet(data: dict) -> str:
    """Turn the structured factsheet into markdown the user edits.

    Rendered rather than shown as JSON, because the user is being asked to
    CORRECT it, and nobody corrects JSON carefully. The verb stays attached to
    its figure in the rendered line — "identified £4.2m" — so the distinction
    the schema protects survives into the document people actually read.
    """
    lines = ["# Background factsheet", ""]

    for role in data.get("roles", []):
        titles = role.get("title_variants", [])
        primary = titles[0]["title"] if titles else "[[title]]"
        started = role.get("started") or "[[start date]]"
        ended = role.get("ended") or "present"
        lines.append(f"## {role.get('employer', '[[employer]]')}")
        lines.append(f"**{primary}** · {started} to {ended}")

        if len(titles) > 1:
            # Kept because they are evidence, not noise: a title may only be
            # claimed verbatim, paired with the variant it came from.
            others = ", ".join(f"{t['title']} ({t['source_document']})"
                               for t in titles[1:])
            lines.append(f"*Also appears as:* {others}")
        lines.append("")

        for claim in role.get("claims", []):
            figure = claim.get("figure")
            verb = claim.get("verb", "")
            statement = claim.get("statement", "")
            bullet = f"- {statement}"
            if figure:
                bullet += f"  \n  *{verb} {figure}* — this verb may not be strengthened"
            lines.append(bullet)
        lines.append("")

    never = data.get("must_never_claim", [])
    if never:
        lines += ["## Must never be claimed", "",
                  "*As valuable as the claims themselves: these look supportable "
                  "and are not.*", ""]
        lines += [f"- {item}" for item in never]
        lines.append("")

    questions = data.get("open_questions", [])
    if questions:
        lines += ["## Open questions", "",
                  "*Answer these in the text above; each one is a gap that would "
                  "otherwise become a [[placeholder]] in a real message.*", ""]
        lines += [f"- {q}" for q in questions]
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def open_questions(data: dict) -> list[str]:
    return list(data.get("open_questions", []))


def save_document(conn, kind: str, body: str) -> int:
    """Store a new version. Never updates in place.

    The history is what makes a later correction traceable: being able to see
    when a sentence arrived is how a wrong one gets found again.
    """
    from datetime import datetime, timezone

    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) v FROM documents WHERE kind=?",
        (kind,)).fetchone()
    version = row["v"] + 1
    conn.execute(
        "INSERT INTO documents(kind, version, body, created_at) VALUES(?,?,?,?)",
        (kind, version, body,
         datetime.now(timezone.utc).isoformat(timespec="seconds")))
    conn.commit()
    return version
