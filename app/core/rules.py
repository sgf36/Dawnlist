"""The rule table — ONE table, shared by the screen and the priority scorer.

spec 5.5: the screen decides *whether* a posting is assessed; the scorer decides
*when*. They can silently disagree when a rule is added to one and not the
other — a real case put "Assistant <title>" roles in scope while the scorer
still demoted anything containing "assistant", so newly in-scope roles landed
at the bottom of the pile.

The structural answer is that there is no second table to forget to update.
Both consumers import RuleTable and neither carries terms of its own.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from app.feed.models import company_key


def word_boundary(terms: list[str]) -> re.Pattern | None:
    r"""Compile terms into ONE word-boundary alternation.

    Rule 7 / spec 5.1. A naive substring screen marked 474 of 550 postings
    `likely` because "venue" fired on re*venue* and "spa" on *spa*ce. Word
    boundaries plus title/company scoping cut that to 143.

    Multi-word terms are supported; internal whitespace matches any run of
    whitespace so "front office" matches "front  office".
    """
    if not terms:
        return None
    parts = [r"\s+".join(re.escape(w) for w in t.split()) for t in terms if t.strip()]
    if not parts:
        return None
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b", re.IGNORECASE)


class KillFamilyError(ValueError):
    """Raised when a kill family violates one of the four rules in spec 5.3."""


@dataclass(frozen=True)
class KillFamily:
    """An employer list + a KILL title regex + a SAVES title regex.

    spec 5.2: the brand does not disqualify a posting — the brand plus the
    wrong function does. An operations role at these employers dies; a strategy
    or data role at the same employer survives.

    The four rules of spec 5.3 are enforced in __post_init__ rather than
    documented, because prose is what failed:
      1. anchored to >= 2 real rejections, carried in `precedents`;
      2. a SAVES regex is REQUIRED — a family without one over-fires the
         moment the user's interests widen;
      3. never admit a term that can appear inside an in-scope title (tested
         against the golden set, see tests/test_screen.py);
      4. unit-tested against real decisions.
    """
    name: str
    employers: tuple[str, ...]
    kill_titles: tuple[str, ...]
    saves_titles: tuple[str, ...]
    # Every precedent is a real decision the user made: (company, title).
    precedents: tuple[tuple[str, str], ...] = ()
    # Kill families are PROPOSED by the app after two rejections of the same
    # shape and adopted only when the user accepts them (rule 8). A family
    # loaded from the database with adopted=False never fires.
    adopted: bool = False

    _kill_re: re.Pattern | None = field(default=None, init=False, repr=False, compare=False)
    _saves_re: re.Pattern | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.employers:
            raise KillFamilyError(f"{self.name}: a kill family needs at least one employer")
        if not self.kill_titles:
            raise KillFamilyError(f"{self.name}: a kill family needs KILL terms")
        if not self.saves_titles:
            raise KillFamilyError(
                f"{self.name}: SAVES is a required field (spec 5.3 rule 2). "
                "A family without one over-fires the moment interests widen."
            )
        if len(self.precedents) < 2:
            raise KillFamilyError(
                f"{self.name}: anchor to at least two real rejections "
                f"(spec 5.3 rule 1); got {len(self.precedents)}. "
                "A family invented from intuition removes things the user wanted."
            )
        object.__setattr__(self, "_kill_re", word_boundary(list(self.kill_titles)))
        object.__setattr__(self, "_saves_re", word_boundary(list(self.saves_titles)))

    def covers_employer(self, company: str) -> bool:
        # The whole normalised name, never a substring: "EY" is inside
        # "Bentley", and a family armed against EY killed Bentley's postings.
        key = company_key(company)
        return bool(key) and any(company_key(e) == key for e in self.employers)

    def match(self, company: str, title: str) -> tuple[str, int] | None:
        """Return (reason, match offset), or None if this family does not kill.

        SAVES is checked BEFORE kill: the whole point of the family is that the
        right function at a rejected employer survives.
        """
        if not self.adopted or not self.covers_employer(company):
            return None
        if self._saves_re and self._saves_re.search(title or ""):
            return None
        if self._kill_re:
            m = self._kill_re.search(title or "")
            if m:
                return (f"kill family {self.name!r}: {company} + title term "
                        f"{m.group(0)!r}"), m.start()
        return None

    def verdict(self, company: str, title: str) -> str | None:
        hit = self.match(company, title)
        return hit[0] if hit else None


@dataclass
class RuleTable:
    """Per-user screening rules. Seeded EMPTY — every entry is earned.

    unsupported_titles  tier 1: line-staff grades and excluded functions
    known_employers     tier 2: employers the user has actually pursued
    strong_terms        tier 3: signal anywhere, description included
    contextual_terms    tier 4: signal in TITLE or COMPANY only — a word like
                        "travel" or "portfolio" is real signal in a title and
                        pure noise in a description's benefits boilerplate
    """
    unsupported_titles: list[str] = field(default_factory=list)
    known_employers: list[str] = field(default_factory=list)
    strong_terms: list[str] = field(default_factory=list)
    contextual_terms: list[str] = field(default_factory=list)
    kill_families: list[KillFamily] = field(default_factory=list)

    def compiled(self) -> "CompiledRules":
        return CompiledRules(
            unsupported=word_boundary(self.unsupported_titles),
            strong=word_boundary(self.strong_terms),
            contextual=word_boundary(self.contextual_terms),
            employers=tuple((company_key(e), e) for e in self.known_employers
                            if company_key(e)),
            families=tuple(self.kill_families),
        )


@dataclass(frozen=True)
class CompiledRules:
    unsupported: re.Pattern | None
    strong: re.Pattern | None
    contextual: re.Pattern | None
    #: (normalised key, name as the user's decision recorded it)
    employers: tuple[tuple[str, str], ...]
    families: tuple[KillFamily, ...]

    def known_employer(self, company: str) -> str | None:
        """The pursued employer this posting is at, matched on the whole
        normalised name.

        A substring test made every posting at "The Walt Disney Company" or
        "Bentley" a known employer once the user pursued a role at EY.
        """
        key = company_key(company)
        if not key:
            return None
        for employer_key, name in self.employers:
            if employer_key == key:
                return name
        return None

    @property
    def has_positive_signal(self) -> bool:
        """Whether this table may reject a posting for matching nothing.

        Tiers 3 and 4 are an ALLOWLIST: a posting survives only because
        something matched it. The table is seeded empty and every entry is
        earned — but an empty allowlist rejects the entire world, and entries
        are earned by deciding on postings the user has to be shown first.

        So an empty table means NO OPINION, not a universal no. Without this
        the app screened out 100% of every sweep, assessed nothing, and showed
        an empty shortlist every morning with no error at all: the funnel read
        swept N, screened out N, assessed 0 — indistinguishable from a quiet
        day in the market.

        Known employers are deliberately NOT counted. They are derived from
        pursue decisions, so counting them turned the first pursue into a
        one-employer allowlist: pursue one Four Seasons role and every General
        Manager at Rosewood or Mandarin Oriental became "no matching term",
        unread, for every user from their first decision on. An employer the
        user chased is a reason to read a posting, never a reason to stop
        reading the rest of the market. Only terms the user typed can say that
        what fails to match them is not wanted.
        """
        return bool(self.strong or self.contextual)


# ---------------------------------------------------------------------------
# spec 5.3 rules 3 and 4 — the admission-time guard.
#
# Word boundaries are NOT sufficient here, and assuming they were is how the
# original failure happened. `\boffice manager\b` still matches INSIDE
# "Assistant Front Office Manager" — the term is a well-formed word sequence
# sitting within a longer, genuinely in-scope title. No matcher tweak fixes
# that safely: shortening the match would break real kills.
#
# The spec's actual rule is therefore about ADMISSION, not matching: "never
# admit a term that can appear inside an in-scope title". So a term is tested
# against the user's real decisions BEFORE it can enter the table, and rule 4
# ("unit-test the families against real decisions") becomes runtime code
# instead of a documented intention.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RuleConflict:
    term: str
    field: str
    pursued_title: str
    company: str

    def __str__(self) -> str:
        return (f"{self.field} term {self.term!r} would kill a pursued role: "
                f"{self.pursued_title!r} at {self.company!r}")


class RuleConflictError(ValueError):
    def __init__(self, conflicts: list[RuleConflict]):
        self.conflicts = conflicts
        super().__init__("; ".join(str(c) for c in conflicts))


def find_conflicts(table: "RuleTable",
                   pursued: list[tuple[str, str]]) -> list[RuleConflict]:
    """Every way `table` would screen out a role the user actually pursued.

    `pursued` is the golden set: (company, title) pairs the user marked pursue.
    Returns conflicts rather than raising, so the UI can show them all at once.
    """
    conflicts: list[RuleConflict] = []
    unsupported = word_boundary(table.unsupported_titles)
    for company, title in pursued:
        if unsupported:
            m = unsupported.search(title or "")
            if m:
                conflicts.append(RuleConflict(m.group(0), "unsupported_titles",
                                              title, company))
        for fam in table.kill_families:
            # Test the family as if adopted: a proposed family must be checked
            # before the user is ever asked to adopt it.
            probe = KillFamily(name=fam.name, employers=fam.employers,
                               kill_titles=fam.kill_titles,
                               saves_titles=fam.saves_titles,
                               precedents=fam.precedents, adopted=True)
            why = probe.verdict(company, title)
            if why:
                conflicts.append(RuleConflict(fam.name, "kill_families",
                                              title, company))
    return conflicts


def assert_no_conflicts(table: "RuleTable",
                        pursued: list[tuple[str, str]]) -> None:
    """Raise if any rule in `table` would screen out a real pursue.

    Call this on every rule change — adding a term, adopting a proposed kill
    family — and on app start against the stored decision history.
    """
    conflicts = find_conflicts(table, pursued)
    if conflicts:
        raise RuleConflictError(conflicts)


# ---------------------------------------------------------------------------
# Containment detection — the second layer under the admission guard.
#
# `assert_no_conflicts` only protects a user who ALREADY has pursue history.
# On day one that history is empty, so the "office manager" class of failure
# would be silent again. This makes it visible instead.
#
# The linguistic rule, which is what makes this reliable rather than a guess:
#
#   "X of Y"  -> the role IS Y. "Head of Operations" is an operations role, so
#               a kill term `operations` is firing on the head noun. Correct.
#   "A B Y"   -> Y modified by A B, which is often a DIFFERENT role. "Assistant
#               Front Office Manager" is not an office manager, so a kill term
#               `office manager` is firing inside a longer role name.
#
# So: significant words immediately before the match, not joined by a linking
# word, mean the term matched inside a longer title. The kill still happens —
# the screen stays cheap and predictable — but the row is FLAGGED so an
# over-reaching term shows up in review rather than as months of silence.
# ---------------------------------------------------------------------------

#: Words that link a head noun to its qualifier. "Head **of** Operations".
LINKING_WORDS = frozenset({
    "of", "for", "in", "at", "to", "and", "or", "&", "the", "a", "an",
    "on", "with", "de", "des", "du",
})

#: Characters that end a title segment. "Kitchen Porter - Full Time" is two.
SEGMENT_BREAKS = "-–—,|/()[]:;·"


def is_contained_match(title: str, match_start: int) -> bool:
    """True when the matched term sits INSIDE a longer role name.

    >>> is_contained_match("Office Manager", 0)
    False
    >>> is_contained_match("Assistant Front Office Manager", 16)
    True
    >>> is_contained_match("Head of Operations", 8)      # "of" links
    False
    """
    if match_start <= 0:
        return False

    segment_start = 0
    for i, ch in enumerate(title[:match_start]):
        if ch in SEGMENT_BREAKS:
            segment_start = i + 1

    prefix = title[segment_start:match_start].strip()
    if not prefix:
        return False

    words = prefix.split()
    if not words:
        return False
    return words[-1].strip(".,").casefold() not in LINKING_WORDS


# ---------------------------------------------------------------------------
# Proposing kill families.
#
# A family is never typed in. Spec 5.3 rule 1 anchors one to at least two real
# rejections, and rule 8 makes adoption the user's decision — so the app's job
# is to notice the shape and offer it, never to invent one. `kill_families` was
# the last table the app read and never wrote, which meant tier 1b could only
# ever be populated by hand-editing SQLite.
# ---------------------------------------------------------------------------

#: Words that carry no functional meaning in a job title, so they can never be
#: the thing a family kills on. "Senior Manager" and "Senior Analyst" share
#: "senior" and are not the same function.
TITLE_NOISE = frozenset({
    "senior", "junior", "lead", "head", "chief", "deputy", "assistant",
    "associate", "director", "manager", "executive", "officer", "specialist",
    "coordinator", "analyst", "consultant", "advisor", "trainee", "graduate",
    "i", "ii", "iii", "uk", "eu", "us", "emea",
}) | LINKING_WORDS


def _title_words(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", (title or "").casefold())
            if len(w) > 2 and w not in TITLE_NOISE}


def propose_families(rejected: list[tuple[str, str]],
                     pursued: list[tuple[str, str]],
                     saves_terms: list[str]) -> list[KillFamily]:
    """Kill families the evidence supports, none of them adopted.

    Every returned family satisfies all four rules of spec 5.3 by construction:
    two or more real precedents, a non-empty SAVES, no kill term that appears
    in anything the user pursued, and a shape a test can pin.

    An employer with two rejections but no basis for SAVES yields NOTHING. A
    family that would kill every posting at an employer is precisely what rule
    2 exists to prevent, and proposing one and letting the user adopt it would
    make the app the author of that mistake.
    """
    pursued_words: set[str] = set()
    for _company, title in pursued:
        pursued_words |= _title_words(title)

    # Grouped on the same normalised name the family will match on, so "Kier
    # Ltd" and "Kier Limited" are one employer's shape, not two lone
    # rejections that never reach the two a family needs.
    by_employer: dict[str, list[tuple[str, str]]] = {}
    for company, title in rejected:
        if company_key(company):
            by_employer.setdefault(company_key(company), []).append(
                (company, title))

    out: list[KillFamily] = []
    for _key, group in sorted(by_employer.items()):
        if len(group) < 2:
            continue
        company = group[0][0]

        # A kill term must appear in at least two rejected titles at this
        # employer: one rejection is a decision, two is a shape.
        counts: dict[str, int] = {}
        for _c, title in group:
            for word in _title_words(title):
                counts[word] = counts.get(word, 0) + 1
        kill = sorted(w for w, n in counts.items()
                      if n >= 2 and w not in pursued_words)
        if not kill:
            continue

        # SAVES: what the user has pursued at this employer, plus the terms
        # they told the screen always to read. Rule 2 — required, not optional.
        saves = sorted({w for c, t in pursued if company_key(c) == _key
                        for w in _title_words(t)}
                       | {t.casefold() for t in saves_terms if t.strip()})
        if not saves:
            continue

        family = KillFamily(
            name=company,
            employers=(company,),
            kill_titles=tuple(kill),
            saves_titles=tuple(saves),
            precedents=tuple(group),
            adopted=False,
        )

        # It must actually kill the rejections it was built from. SAVES is
        # checked before KILL, so a family whose saves cover its own precedents
        # is inert: it would sit in the list looking armed and screen nothing.
        # Tested by arming a copy, because an unadopted family never matches
        # and would therefore pass any check made against the real one.
        armed = replace(family, adopted=True)
        if not all(armed.match(c, t) for c, t in group):
            continue

        out.append(family)
    return out
