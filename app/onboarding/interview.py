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

from app.i18n import tr
from app.intelligence.assess import ASSESSMENT_MODEL, DRAFTING_MODEL


def ingest_guidance() -> str:
    """Shown verbatim on the first screen of setup. Both sentences are
    load-bearing: users routinely hand over a tidied CV and lose exactly the
    history the screen needs.

    A FUNCTION RATHER THAN A CONSTANT. A module-level `tr()` is resolved at
    import time, which happens before `main` applies the stored locale — so the
    one screen every user starts on would have stayed English in all fifty
    languages.
    """
    return tr("onboarding.ingest_guidance")

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
            out.append(tr("onboarding.corpus_none"))
        elif len(self.usable) == 1:
            out.append(tr("onboarding.corpus_one_version"))
        unreadable = [d.name for d in self.documents if not d.is_usable]
        if unreadable:
            out.append(tr("onboarding.corpus_unreadable",
                          files=", ".join(unreadable)))
        if self.earliest_year() and self.earliest_year() > 2010:
            out.append(tr("onboarding.corpus_earliest_year",
                          year=self.earliest_year()))
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


#: Small, cheap and entirely mechanical: pull the search out of prose. The
#: strong model is for the factsheet, where an error propagates into every
#: later claim; this is a set of search terms the user then reviews.
SEARCH_PLAN_MAX_TOKENS = 1500

#: Titles ALONE were what this used to return, and location was deliberately
#: left out — so every search set up from it looked across the whole world, and
#: every posting that returned was paid for. Where the role is based, the
#: contract types accepted and the employers ruled out are decided by the
#: person, not by judgement, so they can be applied before anything is fetched.
SEARCH_PLAN_RULES = """\
Read what this person says they are looking for, and the fit brief drafted \
with them, and return what a job feed needs in order to search for those roles.

Every posting a search returns is paid for, and a search with no location \
looks across the whole world, so each rule matters:

- titles: JOB TITLES ONLY, as a job advert would write them. Not skills, \
  industries, locations or sentences. "Revenue Manager" is a title; "the \
  underwriting-to-property seam" and "London" are not. Two to four words \
  each, in ordinary market wording rather than the person's own phrasing. \
  Between three and eight. Empty if nothing says what work they want.
- countries: ISO 3166-1 alpha-2 codes ("GB", never "UK") for the countries \
  the ROLE must be based in. Only countries the person states, or that follow \
  from a city they name. Never infer one from where they used to work, where \
  an employer is headquartered, or a language they speak.
- cities: only when the person limits the search to particular cities, named \
  in English as a map would name them. Empty when anywhere in the country will do.
- employment_types: only contract types the person says they accept. \
  "Permanent" means full_time. Empty when they say nothing about it.
- exclude_title_terms: a word or short phrase that, in a job title, always \
  means a role the person has explicitly ruled out, and could never appear in \
  a title they would want. Leave it out whenever there is doubt: an exclusion \
  hides a posting before anyone has read it.
- exclude_companies: employers the person says must never be suggested or \
  contacted, exactly as written. Empty when none are named."""

SEARCH_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "titles": {"type": "array", "items": {"type": "string"},
                   "maxItems": 8},
        "countries": {"type": "array", "items": {"type": "string"}},
        "cities": {"type": "array", "items": {"type": "string"}},
        "employment_types": {"type": "array", "items": {
            "type": "string",
            "enum": ["full_time", "part_time", "contract", "temporary",
                     "internship", "freelance"]}},
        "exclude_title_terms": {"type": "array", "items": {"type": "string"}},
        "exclude_companies": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["titles", "countries", "cities", "employment_types",
                 "exclude_title_terms", "exclude_companies"],
    "additionalProperties": False,
}


def build_search_plan_request(stated_aim: str, brief: str = "", *,
                              model: str = ASSESSMENT_MODEL) -> dict:
    """The search to seed setup with, from what the user typed and the brief.

    WHY A MODEL CALL AND NOT A SPLIT. The rule-based version split the text on
    punctuation and kept any run of two to five words, which offered a new
    user "including the underwriting-to-property-implementation seam" and
    "three kinds investment" as searches to switch on — on a screen that says,
    two lines above, that each one costs money per posting it returns.

    The brief is included because that is where the person's corrections land:
    "roles must be BASED in London" was written into a brief, never into the
    aim box, and a search built from the aim alone could not know it.
    """
    return {
        "model": model,
        "max_tokens": SEARCH_PLAN_MAX_TOKENS,
        "system": [{"type": "text", "text": SEARCH_PLAN_RULES}],
        "messages": [{"role": "user", "content":
                      f"<stated_aim>\n{stated_aim}\n</stated_aim>\n\n"
                      f"<fit_brief>\n{brief}\n</fit_brief>"}],
        "output_config": {"format": {"type": "json_schema",
                                     "schema": SEARCH_PLAN_SCHEMA}},
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
