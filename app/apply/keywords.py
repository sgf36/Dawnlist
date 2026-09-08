"""What this posting asks for that your evidence does not support.

    gaps = analyse(job.description_text, evidence)

Zero tokens. Zero API calls. Runs entirely on text already fetched, which is
why this can be given away against a competitor whose whole product it is.

WHY THIS IS NOT AN "ATS KEYWORD SCORE"
--------------------------------------
The tools that sell this compute bag-of-words overlap between a posting and a
CV and render it as a percentage. That number is close to meaningless: it
rewards stuffing, it counts "management" in "management of expectations", and
a person cannot act on "68%".

What a person can act on is a list of the things the posting STATES it wants
which their evidence does not mention — and, separately, the things it merely
prefers. So this extracts requirement-bearing phrases and reports them
individually, and it deliberately produces no score.

WHY EXTRACTION IS CONSERVATIVE, AND WHY THAT DIRECTION
------------------------------------------------------
A false "you are missing X" is worse than a missed X. The first sends someone
to rewrite a CV around a requirement that was never made, and it erodes trust
in every other line; the second leaves them where they already were. So a
phrase is admitted only from a line that actually reads as a requirement, and
only in a shape that survives being read back — not every capitalised word.

The matching lesson is the repository's own, learned on the screen: word
boundaries stop "venue" firing on "revenue", but they do NOT stop a term
matching inside a longer phrase that means something else. Admission is where
the guard belongs, so most of this file is about what gets in.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.rules import word_boundary

#: A line that reads as a requirement rather than as a description of the team,
#: the company or the benefits. Anchored to the phrases employers actually use.
#: Ordered longest-first only for readability; the alternation is unordered.
REQUIREMENT_MARKERS = re.compile(
    r"\b("
    r"must have|must be|required|requirement|essential|you will need|"
    r"you should have|we require|proven (?:track record|experience)|"
    r"demonstrable|experience (?:in|with|of)|proficien(?:t|cy) (?:in|with)|"
    r"working knowledge of|familiarity with|qualified in|"
    r"degree in|qualification in|certifi(?:ed|cation) in|"
    r"fluent in|native|minimum of|at least"
    r")\b", re.IGNORECASE)

#: The same idea for things the posting says it would LIKE. Kept apart because
#: telling somebody they are missing a nice-to-have as though it were a bar is
#: exactly the false positive this module exists to avoid.
PREFERENCE_MARKERS = re.compile(
    r"\b("
    r"desirable|preferred|nice to have|bonus|advantage|"
    r"ideally|would be a plus|welcome|an asset"
    r")\b", re.IGNORECASE)

#: Where a requirement phrase begins, once a line is admitted. The phrase is
#: what follows one of these, which is why they are separate from the markers
#: above: "experience in revenue management" contributes "revenue management",
#: not "experience".
PHRASE_LEAD = re.compile(
    r"\b(?:experience (?:in|with|of)|proficien(?:t|cy) (?:in|with)|"
    r"working knowledge of|familiarity with|degree in|qualification in|"
    r"certifi(?:ed|cation) in|qualified in|fluent in|knowledge of|"
    r"background in|track record (?:in|of)|expertise in|skills? in)\b",
    re.IGNORECASE)

#: Cut a phrase here. Everything after the first of these belongs to the next
#: clause, and keeping it produces phrases nobody would recognise as a skill.
PHRASE_END = re.compile(
    r"\s*(?:[,.;:()\[\]/|]|\band\b|\bor\b|\bwith\b|\bfor\b|\bto\b|"
    r"\bincluding\b|\bsuch as\b|\bwithin\b|\bacross\b|\bplus\b|"
    r"\bwho\b|\bwhich\b|\bthat\b|\bideally\b|\bpreferably\b|"
    # The trailing VERB and the trailing marker. Without these,
    # "Experience in Python is desirable" yields the phrase
    # "Python is desirable" — not a skill, and it reads as though the
    # extractor did not understand the sentence, which it did not.
    r"\bis\b|\bare\b|\bwas\b|\bwill\b|\bwould\b|\bshould\b|\bmust\b|"
    r"\bdesirable\b|\bpreferred\b|\bessential\b|\brequired\b)",
    re.IGNORECASE)

#: Phrases that pass every structural test and mean nothing as a gap. Reporting
#: "you are missing: a fast-paced environment" is the failure that makes a
#: person stop reading the list.
NOISE_PHRASES = frozenset({
    "a fast paced environment", "a fast-paced environment", "a team",
    "the team", "a similar role", "a related field", "a relevant field",
    "this role", "the role", "the industry", "our industry", "the business",
    "a similar position", "the position", "a comparable role", "the company",
    "a related discipline", "the above", "the following", "english",
    "communication", "communication skills", "teamwork", "attention to detail",
    "problem solving", "time management", "a can do attitude",
})

#: Below this, a phrase is an abbreviation or a fragment rather than a
#: requirement. "P&L" survives because the check is on letters, not length.
MIN_PHRASE_WORDS = 1
MAX_PHRASE_WORDS = 6


@dataclass(frozen=True)
class Requirement:
    """One thing the posting asks for, and whether the evidence supports it."""

    phrase: str
    #: The line it came from, verbatim. Shown to the reader so a wrong call is
    #: visibly wrong rather than merely surprising — the same reasoning as
    #: quoting the disqualifying line in an assessment.
    source_line: str
    required: bool
    matched: bool

    @property
    def kind(self) -> str:
        if self.matched:
            return "covered"
        return "missing-required" if self.required else "missing-preferred"


@dataclass(frozen=True)
class GapReport:
    requirements: tuple[Requirement, ...] = ()

    @property
    def covered(self) -> tuple[Requirement, ...]:
        return tuple(r for r in self.requirements if r.matched)

    @property
    def missing_required(self) -> tuple[Requirement, ...]:
        return tuple(r for r in self.requirements
                     if not r.matched and r.required)

    @property
    def missing_preferred(self) -> tuple[Requirement, ...]:
        return tuple(r for r in self.requirements
                     if not r.matched and not r.required)

    @property
    def is_empty(self) -> bool:
        """True when nothing could be extracted.

        Reported rather than hidden. A posting written as prose with no stated
        requirements yields nothing here, and showing an empty list with no
        explanation reads as "you match everything", which is the opposite of
        what happened.
        """
        return not self.requirements


def _lines(description: str) -> list[str]:
    """Split into candidate requirement lines.

    Bullets arrive as newlines from most providers, but some flatten a list
    into one paragraph separated by semicolons or bullet glyphs, and a single
    1,400-character line yields nothing useful. Splitting on those recovers it.
    """
    rough = re.split(r"[\n\r]+|(?<=[a-z0-9])\s*[•·‣▪]\s*|;\s+", description)
    return [ln.strip(" \t-–—*•·‣▪") for ln in rough if ln and ln.strip()]


def _clean(phrase: str) -> str:
    phrase = re.sub(r"\s+", " ", phrase).strip(" \t-–—,.;:/")
    # A leading article carries no meaning in a skill name and makes two
    # identical requirements look different.
    phrase = re.sub(r"^(?:a|an|the|your|our|their)\s+", "", phrase,
                    flags=re.IGNORECASE)
    return phrase.strip()


def _admissible(phrase: str) -> bool:
    """The admission guard. Most of the value of this module is here."""
    if not phrase:
        return False
    normalised = re.sub(r"[^a-z0-9 &+]", "", phrase.casefold()).strip()
    if not normalised or normalised in NOISE_PHRASES:
        return False
    words = normalised.split()
    if not (MIN_PHRASE_WORDS <= len(words) <= MAX_PHRASE_WORDS):
        return False
    # Must contain a letter. A bare "5" or "2026" from a years-of-experience
    # clause is a number, not a requirement anybody can act on.
    if not re.search(r"[a-z]", normalised):
        return False
    # A single stopword is never a requirement, however it was reached.
    if len(words) == 1 and words[0] in {
            "experience", "knowledge", "skills", "role", "work", "team",
            "years", "field", "area", "level", "ability", "environment"}:
        return False
    return True


def extract(description: str) -> list[tuple[str, str, bool]]:
    """(phrase, source line, required) for everything the posting asks for.

    Separated from matching so the extraction can be tested on its own, and so
    a caller that only wants "what does this job want" — an interview brief,
    say — does not have to supply any evidence at all.
    """
    found: list[tuple[str, str, bool]] = []
    seen: set[str] = set()

    for line in _lines(description):
        is_requirement = bool(REQUIREMENT_MARKERS.search(line))
        is_preference = bool(PREFERENCE_MARKERS.search(line))
        if not (is_requirement or is_preference):
            continue

        # A line marked BOTH — "experience in X desirable" — is a preference.
        # Reading it as a requirement is the false positive that matters.
        required = is_requirement and not is_preference

        for lead in PHRASE_LEAD.finditer(line):
            tail = line[lead.end():]
            cut = PHRASE_END.search(tail)
            phrase = _clean(tail[:cut.start()] if cut else tail)
            if not _admissible(phrase):
                continue
            key = phrase.casefold()
            if key in seen:
                continue
            seen.add(key)
            found.append((phrase, line.strip(), required))

    return found


def analyse(description: str, evidence: str) -> GapReport:
    """Compare what the posting asks for against what the evidence supports.

    `evidence` is the factsheet and CV text joined — whatever the person can
    actually stand behind. Passing an empty string is legitimate and reports
    everything as missing, which is the truthful answer before onboarding.
    """
    extracted = extract(description)
    if not extracted:
        return GapReport()

    phrases = [p for p, _, _ in extracted]
    pattern = word_boundary(phrases)
    present: set[str] = set()
    if pattern is not None and evidence.strip():
        for hit in pattern.finditer(evidence):
            present.add(re.sub(r"\s+", " ", hit.group(0)).casefold())

    return GapReport(tuple(
        Requirement(phrase=phrase, source_line=line, required=required,
                    matched=re.sub(r"\s+", " ", phrase).casefold() in present)
        for phrase, line, required in extracted))
