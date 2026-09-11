"""The morning run: feed -> dedup -> gates -> screen -> assessment.

Every narrowing between the feed and the shortlist is counted and recorded on
the run, because a single surviving figure hides every narrowing that produced
it (spec 6.3). The counts are written as the run proceeds, so even a crashed
run leaves an honest partial record rather than nothing.

The order is deliberate and the reason is money as well as correctness: the
deterministic screen costs nothing and runs BEFORE any model call, so the only
postings that reach the assessment are the ones the rules already believe in.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Callable, Sequence

from app.core import db
from app.core.dedup import DedupResult, dedup
from app.core.rules import RuleTable
from app.core.screen import ScreenReport, screen_all, yield_rate
from app.feed.base import FeedProvider, FetchResult, SearchQuery
from app.feed.models import Job, name_key
from app.i18n import tr
from app.intelligence.assess import AssessmentReport, assess


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Gate:
    """A mechanical gate: cheap, explainable, applied before the screen."""
    name: str
    predicate: Callable[[Job], bool]
    reason: str



def cap_remedy(provider) -> str:
    """The sentence that follows "you were capped": what would lift it.

    Duck-typed on purpose. Only the managed provider knows about plans, and
    the developer provider has no plan to report — asking it should be a
    no-op, not an AttributeError in the middle of a morning run.

    Returns "" whenever it cannot say something true: no plan support, the
    lookup failed, or the licence is already on the largest plan. Offering an
    upgrade that does not exist is worse than saying nothing.
    """
    ask = getattr(provider, "plan", None)
    if not callable(ask):
        return ""
    try:
        status = ask()
    except Exception:  # noqa: BLE001 - a failed lookup must not fail the run
        return ""
    nxt = getattr(status, "next_plan_up", None)
    if not nxt:
        return ""
    return (f"the {status.plan or 'current'} plan covers "
            f"{status.postings_per_day} postings a day; "
            f"{nxt['key']} covers {nxt['postings_per_day']}")


def permanent_reject_gate(rejected_ids: set[tuple[str, str]]) -> Gate:
    """Rejections are permanent and never expire (spec 4)."""
    return Gate("already-rejected",
                lambda j: j.dedup_key not in rejected_ids,
                "previously rejected by the user")


def posted_within_gate(days: int, today: date | None = None) -> Gate:
    today = today or date.today()
    return Gate(f"posted-within-{days}d",
                lambda j: j.posted_at is None or (today - j.posted_at).days <= days,
                f"posted more than {days} days ago")


@dataclass
class RunOutcome:
    run_id: int
    fetch: dict[str, FetchResult] = field(default_factory=dict)
    deduped: DedupResult | None = None
    gated_out: list[tuple[Job, str]] = field(default_factory=list)
    screen: ScreenReport | None = None
    assessment: AssessmentReport | None = None
    fetch_errors: list[str] = field(default_factory=list)
    per_query_yield: dict[str, float] = field(default_factory=dict)
    #: Queries whose fetch failed, so no yield rate was computed for them.
    #: Named rather than silently absent - "not measured" is not "0%".
    unscored_queries: list[str] = field(default_factory=list)
    #: label -> when that query's fetch BEGAN, for each query whose delta mark
    #: may advance. Per query, because one run-wide mark meant a single
    #: failing or oversize search froze every other search's window with it.
    marks: dict[str, datetime] = field(default_factory=dict)
    #: label -> postings the query matched but did not fetch. Kept apart from
    #: the errors so a search too wide to read can be named and narrowed.
    unfetched: dict[str, int] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return (not self.fetch_errors
                and self.assessment is not None
                and self.assessment.complete)

    def funnel(self) -> dict[str, int]:
        """The whole funnel. Never one number without what it excludes."""
        swept = sum(len(f.jobs) for f in self.fetch.values())
        out = {
            "swept": swept,
            "deduped": len(self.deduped.unique) if self.deduped else swept,
            "gated_out": len(self.gated_out),
            "screened_likely": len(self.screen.likely) if self.screen else 0,
            "screened_out": len(self.screen.unlikely) if self.screen else 0,
            "assessed": len(self.assessment.verdicts) if self.assessment else 0,
            "left_unread": len(self.assessment.unread) if self.assessment else 0,
        }
        if self.screen:
            out["contained_needs_review"] = len(self.screen.contained)
        if self.deduped:
            out["near_duplicates_flagged"] = len(self.deduped.near_duplicates)
        if self.unfetched:
            out["not_fetched"] = sum(self.unfetched.values())
        return out

    def loose_queries(self, threshold: float = 0.10) -> list[str]:
        """Below ~10% the query is fetching mostly noise — and under per-job
        feed billing that is a cash cost per wasted posting, so the remedy is
        to rewrite the query, never to raise a page cap."""
        return [q for q, y in self.per_query_yield.items() if y < threshold]


def run_morning(
    conn: sqlite3.Connection,
    provider: FeedProvider,
    queries: Sequence[SearchQuery],
    rules: RuleTable,
    *,
    fit_brief: str,
    factsheet: str,
    send,
    gates: Sequence[Gate] = (),
    already_seen: set[tuple[str, str]] | None = None,
    already_judged: set[str] | None = None,
    clock: Callable[[], datetime] = _utcnow,
) -> RunOutcome:
    """One morning run, recorded end to end."""
    with db.run(conn) as run:
        outcome = RunOutcome(run_id=run.id)

        # --- fetch ---------------------------------------------------------
        all_jobs: list[Job] = []
        per_query: dict[str, list[Job]] = {}
        for q in queries:
            # Taken BEFORE this query's fetch. A mark stamped when the whole
            # run ended skipped every posting indexed while earlier searches
            # were fetching and the batch was being assessed: none of them was
            # in what this fetch read, and the next run started after them.
            started = clock()
            result = provider.search(q)
            outcome.fetch[q.label] = result
            if not result.ok:
                # spec 6.2: never "no new jobs". Recorded on the run, and the
                # run cannot later be reported as a clean one.
                outcome.fetch_errors.append(f"{q.label}: {result.error}")
                run.record_fetch_failure(f"{q.label}: {result.error}")
            else:
                if result.shortfall:
                    # spec 6.2 again, and the wording carries weight. A capped
                    # run and an under-paginated one are both partial, but only
                    # one of them is the user's own plan doing what it was
                    # bought to do — so the reason is named, with its numbers,
                    # rather than flattened into "results are partial".
                    message = f"{q.label}: {result.shortfall}"
                    if result.not_fetched and not result.capped:
                        outcome.unfetched[q.label] = result.not_fetched
                        message += f" — {tr('funnel.narrow_search')}"
                    outcome.fetch_errors.append(message)
                # The window this fetch read is done, even when it matched
                # more than one page: holding the mark back re-requested the
                # same oversize window every morning and never moved on. A
                # CAPPED fetch keeps its mark, because the plan's daily ceiling
                # stopped it rather than the search, and tomorrow's allowance
                # reads the rest while the exclusion list stops a re-buy.
                if not result.capped:
                    outcome.marks[q.label] = started
            per_query[q.label] = result.jobs
            all_jobs.extend(result.jobs)

        # Once per run, not per query: if the plan's ceiling is what stopped
        # any of them, say what would lift it. A cap message without a remedy
        # is a dead end, and the user cannot act on a number alone.
        if any(r.capped for r in outcome.fetch.values()):
            remedy = cap_remedy(provider)
            if remedy:
                outcome.fetch_errors.append(remedy)

        run.record_counts(swept=len(all_jobs))

        # --- dedup ---------------------------------------------------------
        outcome.deduped = dedup(all_jobs, already_seen=already_seen)
        survivors = outcome.deduped.unique
        run.record_counts(swept=len(all_jobs), deduped=len(survivors))

        # --- mechanical gates ----------------------------------------------
        kept: list[Job] = []
        for job in survivors:
            for gate in gates:
                if not gate.predicate(job):
                    outcome.gated_out.append((job, gate.reason))
                    break
            else:
                kept.append(job)
        run.record_counts(gated=len(outcome.gated_out))

        # --- deterministic screen (zero tokens) ----------------------------
        outcome.screen = screen_all(kept, rules)
        likely = [r.job for r in outcome.screen.likely]
        run.record_counts(screened_likely=len(likely),
                          screened_out=len(outcome.screen.unlikely))

        # Per-query yield, measured on what each query actually contributed.
        #
        # A query whose fetch FAILED is excluded entirely rather than scored on
        # its partial rows. Scoring it would report a transport fault as a
        # loose query - the same error that made a P0 run print 58.1% coverage
        # by counting rate-limit failures as misses (spec 6.2). A yield rate
        # that silently mixes the two is worse than no yield rate.
        likely_ids = {j.dedup_key for j in likely}
        failed = {q.label for q in queries
                  if outcome.fetch.get(q.label) and not outcome.fetch[q.label].ok}
        for label, jobs in per_query.items():
            if label in failed:
                outcome.unscored_queries.append(label)
                continue
            hits = sum(1 for j in jobs if j.dedup_key in likely_ids)
            outcome.per_query_yield[label] = yield_rate(len(jobs), hits)

        # --- assessment ----------------------------------------------------
        outcome.assessment = assess(likely, fit_brief, factsheet, send=send,
                                    already_judged=already_judged)
        run.record_counts(assessed=len(outcome.assessment.verdicts))

        # --- close it honestly ---------------------------------------------
        note = None
        if outcome.fetch_errors:
            note = "; ".join(outcome.fetch_errors)[:500]
        elif outcome.assessment.errors:
            note = "; ".join(outcome.assessment.errors)[:500]
        run.finish(left_unread=len(outcome.assessment.unread), note=note)

        # A run with a fetch failure is never 'complete', even when everything
        # it did manage to fetch was assessed.
        if outcome.fetch_errors:
            conn.execute("UPDATE runs SET status='incomplete' WHERE id=?", (run.id,))
            conn.commit()

    return outcome


def persist(conn: sqlite3.Connection, outcome: RunOutcome) -> None:
    """Write the run's jobs and verdicts. Screened-out rows are kept."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if outcome.screen:
        for result in outcome.screen.results:
            j = result.job
            conn.execute(
                """INSERT INTO jobs(provider, provider_job_id, title, company,
                       description_text, url, name_key, first_seen_run,
                       funnel_status, screen_verdict, screen_tier, screen_reason)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(provider, provider_job_id) DO UPDATE SET
                       screen_verdict=excluded.screen_verdict,
                       screen_tier=excluded.screen_tier,
                       screen_reason=excluded.screen_reason""",
                (j.provider, j.provider_job_id, j.title, j.company,
                 j.description_text, j.url, name_key(j.company, j.title),
                 outcome.run_id, "screened", result.verdict.value,
                 result.tier.value, result.reason))
            conn.execute(
                "INSERT INTO seen_jobs(provider, provider_job_id, seen_at) "
                "VALUES(?,?,?) ON CONFLICT DO NOTHING",
                (j.provider, j.provider_job_id, now))

    if outcome.assessment:
        for v in outcome.assessment.verdicts:
            row = conn.execute(
                "SELECT id FROM jobs WHERE provider=? AND provider_job_id=?",
                (v.job.provider, v.job.provider_job_id)).fetchone()
            if row is None:
                continue
            reason = v.reason
            if v.downgrade_reason:
                reason = f"{reason} [downgraded: {v.downgrade_reason}]"
            conn.execute(
                """INSERT INTO assessments(job_id, run_id, bucket, reason,
                       disqualifying_quote, requirement_checked, full_read,
                       model, created_at)
                   VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(job_id, run_id) DO NOTHING""",
                (row["id"], outcome.run_id, v.bucket, reason,
                 v.disqualifying_quote, int(v.requirement_checked),
                 int(v.full_read), "", now))

    # Near-duplicates are FLAGGED, never merged (spec 6.6) — and a flag that is
    # computed and then dropped is not a flag. `dedup` has always found these;
    # nothing ever wrote them down, so nothing could show them. Written last
    # because both jobs have to be stored before they can be referenced.
    if outcome.deduped:
        for nd in outcome.deduped.near_duplicates:
            rows = [conn.execute(
                "SELECT id FROM jobs WHERE provider=? AND provider_job_id=?",
                (j.provider, j.provider_job_id)).fetchone()
                for j in (nd.job, nd.other)]
            if any(r is None for r in rows):
                continue
            # Ordered, so the same pair found the other way round is one row.
            a, b = sorted(r["id"] for r in rows)
            conn.execute(
                "INSERT INTO near_duplicates(job_id, other_id, reason) "
                "VALUES(?,?,?) ON CONFLICT DO NOTHING", (a, b, nd.reason))
    conn.commit()
