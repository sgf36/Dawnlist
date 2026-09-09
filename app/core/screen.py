"""The deterministic screen — zero tokens, runs before any model call.

spec 5.1, four tiers IN ORDER. The order is load-bearing, not stylistic:
unsupported titles are checked FIRST so that a junior role at a great employer
cannot pass on the employer's name.

Nothing here is ever deleted. Screened-out rows are returned with a per-row
reason and counted every run (spec 5.4) — that is what makes an over-aggressive
filter visible rather than showing up as months of silence.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.core.rules import CompiledRules, RuleTable, is_contained_match
from app.feed.models import Job


class Verdict(str, Enum):
    LIKELY = "likely"
    UNLIKELY = "unlikely"


class Tier(str, Enum):
    UNSUPPORTED_TITLE = "1:unsupported-title"
    KILL_FAMILY = "1b:kill-family"
    KNOWN_EMPLOYER = "2:known-employer"
    STRONG_TERM = "3:strong-term"
    CONTEXTUAL_TERM = "4:contextual-term"
    NO_SIGNAL = "5:no-signal"


@dataclass(frozen=True)
class ScreenResult:
    job: Job
    verdict: Verdict
    tier: Tier
    reason: str
    #: The kill term matched INSIDE a longer role name — "office manager"
    #: firing on "Assistant Front Office Manager". Still killed, so the screen
    #: stays cheap and predictable, but surfaced for review: this is the
    #: failure mode that removes roles the user wanted, and it is invisible
    #: unless something names it.
    contained: bool = False

    @property
    def is_likely(self) -> bool:
        return self.verdict is Verdict.LIKELY

    @property
    def needs_review(self) -> bool:
        return self.contained and not self.is_likely


def screen_one(job: Job, rules: CompiledRules) -> ScreenResult:
    title = job.title or ""
    company = job.company or ""
    description = job.description_text or ""

    # --- Tier 1: unsupported title, regardless of employer -------------------
    if rules.unsupported:
        m = rules.unsupported.search(title)
        if m:
            contained = is_contained_match(title, m.start())
            reason = f"unsupported title term {m.group(0)!r}"
            if contained:
                reason += (" — matched INSIDE a longer role name; "
                           "review before trusting this kill")
            return ScreenResult(job, Verdict.UNLIKELY, Tier.UNSUPPORTED_TITLE,
                                reason, contained=contained)

    # --- Tier 1b: kill families (employer + wrong function) ------------------
    # SAVES is evaluated inside the family, before KILL.
    for fam in rules.families:
        hit = fam.match(company, title)
        if hit:
            why, start = hit
            contained = is_contained_match(title, start)
            if contained:
                why += (" — matched INSIDE a longer role name; "
                        "review before trusting this kill")
            return ScreenResult(job, Verdict.UNLIKELY, Tier.KILL_FAMILY, why,
                                contained=contained)

    # --- Tier 2: known employer ---------------------------------------------
    hit = rules.known_employer(company)
    if hit:
        return ScreenResult(
            job, Verdict.LIKELY, Tier.KNOWN_EMPLOYER, f"known employer {hit!r}",
        )

    # --- Tier 3: strong term anywhere, description included ------------------
    if rules.strong:
        m = rules.strong.search(f"{title}\n{company}\n{description}")
        if m:
            return ScreenResult(
                job, Verdict.LIKELY, Tier.STRONG_TERM, f"strong term {m.group(0)!r}",
            )

    # --- Tier 4: contextual term in TITLE or COMPANY only --------------------
    # Deliberately NOT the description: "travel" or "portfolio" is real signal
    # in a title and pure noise in a benefits paragraph. Tiers 3 and 4 must
    # differ, or tier 4 collapses into tier 3 and the screen over-fires.
    if rules.contextual:
        m = rules.contextual.search(f"{title}\n{company}")
        if m:
            return ScreenResult(
                job, Verdict.LIKELY, Tier.CONTEXTUAL_TERM,
                f"contextual term {m.group(0)!r} in title/company",
            )

    # --- nothing matched -----------------------------------------------------
    # An UNCONFIGURED table has no opinion, so the posting is read rather than
    # killed by an allowlist that could never have matched anything. This is
    # the expensive direction deliberately: paying to assess a posting is
    # recoverable, and a role lost to a rule the user was never shown is not.
    if not rules.has_positive_signal:
        return ScreenResult(
            job, Verdict.LIKELY, Tier.NO_SIGNAL,
            "no screening rules yet — every posting is read until enough "
            "decisions exist for the screen to have a pattern to apply")

    return ScreenResult(job, Verdict.UNLIKELY, Tier.NO_SIGNAL, "no matching term")


@dataclass
class ScreenReport:
    """Every row, both verdicts, plus the counts. Never just the survivors."""
    results: list[ScreenResult]

    @property
    def likely(self) -> list[ScreenResult]:
        return [r for r in self.results if r.is_likely]

    @property
    def unlikely(self) -> list[ScreenResult]:
        """The browsable `unlikely` pile (spec 5.4). Kept, never erased."""
        return [r for r in self.results if not r.is_likely]

    @property
    def contained(self) -> list[ScreenResult]:
        """Kills where the term matched inside a longer role name.

        Surface these every run. A term that keeps landing here is reaching
        further than the user meant it to, and this is the only signal before
        it costs them a role they wanted."""
        return [r for r in self.results if r.needs_review]

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {"screened": len(self.results),
                               "likely": len(self.likely),
                               "unlikely": len(self.unlikely),
                               "contained_needs_review": len(self.contained)}
        for r in self.results:
            out[f"tier:{r.tier.value}"] = out.get(f"tier:{r.tier.value}", 0) + 1
        return out

    @property
    def unlikely_share(self) -> float:
        """Watch this between runs. spec 5.4: if it moves sharply, say so."""
        return unlikely_share_of(len(self.likely), len(self.unlikely))


def unlikely_share_of(likely: int, unlikely: int) -> float:
    """The share of a run the screen removed, from two counts.

    A function rather than only a property because the same number has to be
    computed from the `runs` table long after the report is out of memory —
    watching it BETWEEN runs is the whole of spec 5.4, and a second expression
    of the same ratio is a second thing to get wrong.
    """
    total = likely + unlikely
    return unlikely / total if total else 0.0


#: How far the share may move between runs before it is worth saying so.
#: Deliberately blunt: the point is to catch a rule edit that quietly started
#: removing half the feed, not to report noise on a twenty-row morning.
SHARE_DRIFT = 0.15

#: Below this many screened postings the share is not a measurement. Three
#: rows going the wrong way is 100% drift and means nothing.
DRIFT_MIN_SAMPLE = 20


def share_drift(current: float, previous: float | None) -> float | None:
    """Signed change in the unlikely share, or None when there is nothing to
    compare against. A first run has no drift — it has a starting point."""
    if previous is None:
        return None
    return current - previous


def screen_all(jobs: list[Job], table: RuleTable) -> ScreenReport:
    rules = table.compiled()
    return ScreenReport([screen_one(j, rules) for j in jobs])


def yield_rate(swept: int, likely: int) -> float:
    """spec 3 / handoff 2.1a: `likely ÷ swept`, per query.

    Below ~10% the query is fetching mostly noise. Under per-job feed billing
    that is a direct cash cost per wasted posting, not just a token cost — so
    the remedy is to rewrite the query, never to raise a page cap.
    """
    return (likely / swept) if swept else 0.0


LOOSE_QUERY_THRESHOLD = 0.10
