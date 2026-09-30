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

import contextlib
import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Callable, Sequence

from app.core import db, diagnostics
from app.core.dedup import (DedupResult, cross_provider_merge, dedup,
                            merge_same_run)
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



_MAX_REBALANCE_PASSES = 5


def _run_budget(provider, queries: Sequence[SearchQuery]) -> int:
    """The global fetch budget for this run.

    Derived from the plan's remaining daily allowance when the provider
    reports one, otherwise the sum of all queries' individual caps.  The
    plan is the natural constraint: it is the number of postings the user
    has paid for today and has not yet used.
    """
    plan_fn = getattr(provider, "plan", None)
    if callable(plan_fn):
        try:
            status = plan_fn()
            if status.postings_remaining > 0:
                return status.postings_remaining
        except Exception:  # noqa: BLE001 - a failed lookup must not fail the run
            pass
    return sum(q.max_results for q in queries)


def _balanced_fetch(
    provider: FeedProvider,
    queries: Sequence[SearchQuery],
    outcome: RunOutcome,
    run,
    clock: Callable[[], datetime] = _utcnow,
) -> tuple[list[Job], dict[str, list[Job]]]:
    """Fetch with multi-pass equal-share budget balancing.

    Pass 1 divides the budget equally across queries.  Queries that return
    fewer than their share release the surplus.  Surplus is redistributed
    to queries that hit their cap and still have matches available
    upstream.  Repeats until the budget is exhausted or every query is
    satisfied.

    Re-fetch passes use ``exclude_job_ids`` to avoid re-buying jobs the
    run has already received — the same billing-control mechanism the
    delta pull uses between runs.
    """
    budget = _run_budget(provider, queries)

    all_jobs: list[Job] = []
    per_query: dict[str, list[Job]] = {}
    fetched_ids: dict[str, set[str]] = {}
    pass_results: dict[str, list[FetchResult]] = {}
    started_at: dict[str, datetime] = {}

    for q in queries:
        per_query[q.label] = []
        fetched_ids[q.label] = set()
        pass_results[q.label] = []

    active = list(queries)
    remaining = budget

    for _ in range(_MAX_REBALANCE_PASSES):
        if not active or remaining <= 0:
            break

        share = remaining // len(active)
        if share == 0:
            active = active[:remaining]
            share = 1
            if not active:
                break

        surplus = 0
        hungry: list[SearchQuery] = []

        for q in active:
            cap = q.max_results - len(per_query[q.label])
            pass_limit = min(share, cap) if cap > 0 else 0
            if pass_limit <= 0:
                surplus += share
                continue

            if q.label not in started_at:
                started_at[q.label] = clock()

            pass_query = SearchQuery(
                label=q.label,
                titles=list(q.titles),
                countries=list(q.countries),
                companies=list(q.companies),
                posted_within_days=q.posted_within_days,
                discovered_since=q.discovered_since,
                exclude_job_ids=tuple(
                    set(q.exclude_job_ids) | fetched_ids[q.label]),
                max_results=pass_limit,
                cities=list(q.cities),
                exclude_title_terms=list(q.exclude_title_terms),
                exclude_companies=list(q.exclude_companies),
                description_keywords=list(q.description_keywords),
                search_type=q.search_type,
            )

            result = provider.search(pass_query)
            pass_results[q.label].append(result)

            if result.ok:
                per_query[q.label].extend(result.jobs)
                all_jobs.extend(result.jobs)
                for j in result.jobs:
                    fetched_ids[q.label].add(j.provider_job_id)

                returned = len(result.jobs)
                if returned < pass_limit:
                    surplus += pass_limit - returned
                elif result.not_fetched > 0 and not result.capped:
                    hungry.append(q)
            else:
                surplus += pass_limit

        remaining = surplus
        active = hungry

    for q in queries:
        passes = pass_results[q.label]
        if not passes:
            continue

        first_ok = next((r for r in passes if r.ok), None)
        last_ok = next((r for r in reversed(passes) if r.ok), None)

        combined = FetchResult(
            jobs=per_query[q.label],
            pages_fetched=sum(r.pages_fetched for r in passes),
            exhausted=(last_ok.exhausted if last_ok else False),
            credits_estimate=len(per_query[q.label]),
            error=next((r.error for r in passes if not r.ok), None),
            refusal=next((r.refusal for r in passes if r.refusal), None),
            matched=(first_ok.matched if first_ok else None),
            not_fetched=(last_ok.not_fetched if last_ok else 0),
            capped=any(r.capped for r in passes),
            scanned=sum(r.scanned for r in passes),
            degraded=_unique_degraded(passes),
        )

        outcome.fetch[q.label] = combined
        for d in combined.degraded:
            if not any(x["provider"] == d["provider"]
                       for x in outcome.provider_degraded):
                outcome.provider_degraded.append(d)
        if not combined.ok:
            error_text = combined.error
            if combined.refusal:
                error_text = "today's search limit was reached"
            msg = f"{q.label}: {error_text}"
            outcome.fetch_errors.append(msg)
            run.record_fetch_failure(msg)
        else:
            if combined.shortfall:
                message = f"{q.label}: {combined.shortfall}"
                if combined.not_fetched and not combined.capped:
                    outcome.unfetched[q.label] = combined.not_fetched
                    message += f" — {tr('funnel.narrow_search')}"
                outcome.fetch_errors.append(message)
            if not combined.capped and q.label in started_at:
                outcome.marks[q.label] = started_at[q.label]

    return all_jobs, per_query


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
    #: Postings an earlier run bought and screened in but never judged, put
    #: back in front of the gates and the screen at the start of this one.
    requeued: list[Job] = field(default_factory=list)
    #: Cross-provider near-duplicates resolved by richness score.
    cross_provider_superseded: list[tuple[Job, str]] = field(default_factory=list)
    #: Wall-clock seconds per stage and for the whole run, so "it finished very
    #: fast" can be answered with a number instead of a feeling, and so a run
    #: that read nothing can say how long it spent reading nothing.
    stage_seconds: dict[str, float] = field(default_factory=dict)
    elapsed_seconds: float = 0.0
    #: Providers that could not be searched although others answered, once each.
    provider_degraded: list[dict] = field(default_factory=list)

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
        if self.requeued:
            out["requeued"] = len(self.requeued)
        return out

    def loose_queries(self, threshold: float = 0.10) -> list[str]:
        """Below ~10% the query is fetching mostly noise — and under per-job
        feed billing that is a cash cost per wasted posting, so the remedy is
        to rewrite the query, never to raise a page cap."""
        return [q for q, y in self.per_query_yield.items() if y < threshold]


def _unique_degraded(results) -> list[dict]:
    """The providers that failed in ANY pass of one search, once each."""
    out: list[dict] = []
    for r in results:
        for d in getattr(r, "degraded", []) or []:
            if not any(x["provider"] == d["provider"] for x in out):
                out.append(d)
    return out


@contextlib.contextmanager
def _stage(outcome: "RunOutcome", name: str):
    """Time one stage onto the outcome and, when diagnostics are on, the log."""
    started = time.perf_counter()
    try:
        with diagnostics.span(f"pipeline.{name}", run_id=outcome.run_id) as extra:
            yield extra
    finally:
        outcome.stage_seconds[name] = round(time.perf_counter() - started, 3)


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
    requeued: Sequence[Job] = (),
    kind: str = "sweep",
    seniority_table: str = "",
) -> RunOutcome:
    """One morning run, recorded end to end."""
    run_started = time.perf_counter()
    with db.run(conn, kind=kind) as run:
        outcome = RunOutcome(run_id=run.id)
        diagnostics.event(
            "run.start", run_id=run.id, kind=kind, provider=provider.name,
            searches=[q.label for q in queries], requeued=len(requeued))

        # --- fetch (budget-balanced) ------------------------------------------
        with _stage(outcome, "fetch") as extra:
            all_jobs, per_query = _balanced_fetch(
                provider, queries, outcome, run, clock=clock)
            extra["postings"] = len(all_jobs)
        for label, result in outcome.fetch.items():
            diagnostics.event(
                "fetch.search", run_id=run.id, search=label, ok=result.ok,
                matched=result.matched, kept=len(result.jobs),
                scanned=result.scanned, pages=result.pages_fetched,
                exhausted=result.exhausted, not_fetched=result.not_fetched,
                capped=result.capped, refusal=result.refusal,
                error=result.error, degraded=result.degraded)

        # Once per run, not per query: if the plan's ceiling is what stopped
        # any of them, say what would lift it. A cap message without a remedy
        # is a dead end, and the user cannot act on a number alone.
        if any(r.capped for r in outcome.fetch.values()):
            remedy = cap_remedy(provider)
            if remedy:
                outcome.fetch_errors.append(remedy)

        run.record_counts(swept=len(all_jobs))

        # --- dedup ---------------------------------------------------------
        with _stage(outcome, "dedup") as extra:
            outcome.deduped = dedup(all_jobs, already_seen=already_seen)
            survivors = outcome.deduped.unique
            extra.update(before=len(all_jobs), after=len(survivors))
        run.record_counts(swept=len(all_jobs), deduped=len(survivors))

        # --- cross-provider merge -----------------------------------------
        with _stage(outcome, "cross_provider") as extra:
            # Two providers returning the same role in THIS run first, then the
            # survivors against what earlier runs stored.
            same_run = merge_same_run(survivors)
            xp = cross_provider_merge(same_run.kept, conn)
            survivors = xp.kept
            outcome.cross_provider_superseded = same_run.superseded + xp.superseded
            extra.update(same_run=len(same_run.superseded),
                         vs_stored=len(xp.superseded))

        # Postings already held, so dedup would drop them, and never judged:
        # without this a posting whose batch failed, whose run was stopped, or
        # that the model skipped stayed unread for good. They skip dedup and
        # still pass the gates and the screen, so a rejection or a rule added
        # since applies to them too.
        fresh = {j.dedup_key for j in survivors}
        outcome.requeued = [j for j in requeued if j.dedup_key not in fresh]

        # --- mechanical gates ----------------------------------------------
        kept: list[Job] = []
        with _stage(outcome, "gates") as extra:
            for job in survivors + outcome.requeued:
                for gate in gates:
                    if not gate.predicate(job):
                        outcome.gated_out.append((job, gate.reason))
                        break
                else:
                    kept.append(job)
            reasons: dict[str, int] = {}
            for _job, reason in outcome.gated_out:
                reasons[reason] = reasons.get(reason, 0) + 1
            extra.update(kept=len(kept), removed=len(outcome.gated_out),
                         reasons=reasons)
        run.record_counts(gated=len(outcome.gated_out))

        # --- deterministic screen (zero tokens) ----------------------------
        with _stage(outcome, "screen") as extra:
            outcome.screen = screen_all(kept, rules)
            extra.update(likely=len(outcome.screen.likely),
                         unlikely=len(outcome.screen.unlikely))
        likely = [r.job for r in outcome.screen.likely]
        run.record_counts(screened_likely=len(likely),
                          screened_out=len(outcome.screen.unlikely))

        # Stored BEFORE any model call. Nothing used to be written until
        # `persist` ran after the whole run returned, so a run stopped during
        # assessment kept none of the postings it had bought. The marks move
        # only once the postings their windows returned are on disk.
        persist_screen(conn, run.id, outcome.screen.results)
        persist_near_duplicates(conn, outcome.deduped)
        db.advance_query_marks(conn, outcome.marks)

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
        with _stage(outcome, "assess") as extra:
            outcome.assessment = assess(
                likely, fit_brief, factsheet, send=send,
                already_judged=already_judged,
                on_batch=lambda verdicts: persist_verdicts(conn, run.id, verdicts),
                on_call=lambda call: persist_call(conn, run.id, call),
                seniority_table=seniority_table)
            extra.update(sent=len(likely),
                         verdicts=len(outcome.assessment.verdicts),
                         unread=len(outcome.assessment.unread),
                         errors=list(outcome.assessment.errors))
        run.record_counts(assessed=len(outcome.assessment.verdicts))

        # --- close it honestly ---------------------------------------------
        note = None
        if outcome.fetch_errors:
            note = "; ".join(outcome.fetch_errors)[:500]
        elif outcome.assessment.errors:
            note = "; ".join(outcome.assessment.errors)[:500]
        run.finish(left_unread=len(outcome.assessment.unread), note=note)

        # A run with a fetch failure OR an assessment error is never
        # 'complete', even when everything it did manage to fetch was judged.
        # `finish` counts only postings left unread, so a batch that returned
        # an unusable verdict, or a re-read that never came back, was filed as
        # a clean run.
        if outcome.fetch_errors or outcome.assessment.errors:
            conn.execute("UPDATE runs SET status='incomplete' WHERE id=?", (run.id,))
            conn.commit()

    outcome.elapsed_seconds = round(time.perf_counter() - run_started, 3)
    diagnostics.event(
        "run.end", run_id=outcome.run_id, complete=outcome.complete,
        seconds=outcome.elapsed_seconds, stages=outcome.stage_seconds,
        funnel=outcome.funnel(), fetch_errors=outcome.fetch_errors)
    return outcome


def persist(conn: sqlite3.Connection, outcome: RunOutcome) -> None:
    """Write the run's jobs and verdicts. Screened-out rows are kept.

    `run_morning` now writes each part as it happens. This writes them all
    again, and harmlessly, for a caller holding an outcome of its own.
    """
    if outcome.screen:
        persist_screen(conn, outcome.run_id, outcome.screen.results)
    if outcome.assessment:
        persist_verdicts(conn, outcome.run_id, outcome.assessment.verdicts)
    persist_near_duplicates(conn, outcome.deduped)


def persist_screen(conn: sqlite3.Connection, run_id: int, results) -> None:
    """Every screened posting, both piles, with why it landed where it did."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for result in results:
        j = result.job
        # Salary, posted date, place and the feed's tags were written for a
        # PASTED posting only. A fed one read back from its row had none of
        # them, so anything rebuilt from the database showed the model "not
        # stated" for fields the feed did state — and those are the fields a
        # brief's hard constraints are written in.
        #
        # On a later write of the same posting a value is updated, never
        # blanked: an emptier copy (a cached row, an alert email) must not
        # erase what an earlier, fuller one recorded.
        conn.execute(
            """INSERT INTO jobs(provider, provider_job_id, title, company,
                   locations_json, description_text, posted_at, salary, url,
                   raw_criteria_json, name_key, first_seen_run,
                   funnel_status, screen_verdict, screen_tier, screen_reason)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(provider, provider_job_id) DO UPDATE SET
                   locations_json=CASE WHEN excluded.locations_json <> '[]'
                       THEN excluded.locations_json ELSE jobs.locations_json END,
                   posted_at=COALESCE(excluded.posted_at, jobs.posted_at),
                   salary=COALESCE(excluded.salary, jobs.salary),
                   raw_criteria_json=CASE WHEN excluded.raw_criteria_json <> '{}'
                       THEN excluded.raw_criteria_json ELSE jobs.raw_criteria_json END,
                   screen_verdict=excluded.screen_verdict,
                   screen_tier=excluded.screen_tier,
                   screen_reason=excluded.screen_reason""",
            (j.provider, j.provider_job_id, j.title, j.company,
             json.dumps(list(j.locations)), j.description_text,
             j.posted_at.isoformat() if j.posted_at else None, j.salary,
             j.url, json.dumps(j.raw_criteria or {}, default=str),
             name_key(j.company, j.title), run_id, "screened",
             result.verdict.value, result.tier.value, result.reason))
        conn.execute(
            "INSERT INTO seen_jobs(provider, provider_job_id, seen_at) "
            "VALUES(?,?,?) ON CONFLICT DO NOTHING",
            (j.provider, j.provider_job_id, now))
    conn.commit()


def persist_verdicts(conn: sqlite3.Connection, run_id: int, verdicts) -> None:
    """This run's verdicts, as they arrive.

    A second verdict for the same posting in the same run REPLACES the first.
    Verdicts are written batch by batch, so the full re-read's answer lands
    after the first pass's has been stored — and keeping the first would keep
    the answer formed on the cut-off text.
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for v in verdicts:
        row = conn.execute(
            "SELECT id FROM jobs WHERE provider=? AND provider_job_id=?",
            (v.job.provider, v.job.provider_job_id)).fetchone()
        if row is None:
            continue
        reason = v.reason
        conn.execute(
            """INSERT INTO assessments(job_id, run_id, bucket, reason,
                   disqualifying_quote, requirement_checked, full_read,
                   model, created_at)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(job_id, run_id) DO UPDATE SET
                   bucket=excluded.bucket,
                   reason=excluded.reason,
                   disqualifying_quote=excluded.disqualifying_quote,
                   requirement_checked=excluded.requirement_checked,
                   full_read=excluded.full_read,
                   model=excluded.model""",
            (row["id"], run_id, v.bucket, reason,
             v.disqualifying_quote, int(v.requirement_checked),
             int(v.full_read), v.model, now))
    conn.commit()


def persist_call(conn: sqlite3.Connection, run_id: int, call) -> None:
    """One model request's usage, stored as it returns.

    Every token is the user's own money on their own key, and none of it was
    written anywhere, so neither what a run cost nor whether the cached prefix
    ever cached could be read back.
    """
    diagnostics.event(
        "model.call", run_id=run_id, model=call.model,
        stop_reason=call.stop_reason, full_read=bool(call.full_read),
        input_tokens=call.input_tokens, output_tokens=call.output_tokens,
        cache_read_tokens=call.cache_read_tokens)
    conn.execute(
        """INSERT INTO model_calls(run_id, model, stop_reason, full_read,
               input_tokens, output_tokens, cache_read_tokens,
               cache_write_tokens, created_at)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (run_id, call.model, call.stop_reason, int(call.full_read),
         call.input_tokens, call.output_tokens, call.cache_read_tokens,
         call.cache_write_tokens,
         datetime.now(timezone.utc).isoformat(timespec="seconds")))
    conn.commit()


def persist_near_duplicates(conn: sqlite3.Connection,
                            deduped: DedupResult | None) -> None:
    # Near-duplicates are FLAGGED, never merged (spec 6.6) — and a flag that is
    # computed and then dropped is not a flag. `dedup` has always found these;
    # nothing ever wrote them down, so nothing could show them. Written after
    # the screen because both jobs have to be stored before they can be
    # referenced.
    if deduped:
        for nd in deduped.near_duplicates:
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


def judged_refs(conn: sqlite3.Connection) -> set[str]:
    """Every posting already assessed, keyed as `assess` skips them.

    Read from the stored verdicts rather than kept in memory, so it survives
    the run that produced them: a run resumed after an interruption skips what
    the interrupted one had already paid the model to read.
    """
    return {f"{r['provider']}:{r['provider_job_id']}" for r in conn.execute(
        "SELECT DISTINCT j.provider, j.provider_job_id FROM assessments a "
        "JOIN jobs j ON j.id = a.job_id")}


def stored_job(row) -> Job:
    """A posting rebuilt from its `jobs` row, with the fields its assessment
    renders and its gates read."""
    posted = None
    if row["posted_at"]:
        try:
            posted = date.fromisoformat(str(row["posted_at"])[:10])
        except ValueError:
            posted = None
    return Job(
        provider=row["provider"], provider_job_id=row["provider_job_id"],
        title=row["title"], company=row["company"],
        locations=tuple(json.loads(row["locations_json"] or "[]")),
        description_text=row["description_text"] or "",
        posted_at=posted, salary=row["salary"], url=row["url"] or "",
        raw_criteria=json.loads(row["raw_criteria_json"] or "{}"))


def unassessed_likely(conn: sqlite3.Connection) -> list[Job]:
    """Stored postings the screen let through that nothing has judged.

    Only undecided ones with a description: a decided posting needs no verdict,
    and one whose description is gone would be assessed on nothing.
    """
    return [stored_job(r) for r in conn.execute(
        "SELECT j.* FROM jobs j"
        " WHERE j.screen_verdict = 'likely' AND j.description_text <> ''"
        "   AND NOT EXISTS (SELECT 1 FROM assessments a WHERE a.job_id = j.id)"
        "   AND NOT EXISTS (SELECT 1 FROM decisions d WHERE d.job_id = j.id)"
        " ORDER BY j.id")]
