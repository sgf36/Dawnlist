"""Assessment — the model half of the funnel.

Three things here are not negotiable, and all three are enforced in code rather
than asked for in the prompt, because a prompt is a request and a check is a
guarantee:

  * **The quote is verified.** spec 6.7 says a rejection on a stated requirement
    must quote the line verbatim. Asking for that catches most hallucinated
    rejections; VERIFYING that the quoted text actually occurs in the posting
    catches the rest. A reject whose quote is not in the source is downgraded,
    never trusted.

  * **Volume is never bought by skipping reads.** There is no "assess the top
    N". The set either completes or the run is reported incomplete with the
    counts left unread (spec 3, 6.1, 6.4).

  * **Batch results are keyed by custom_id.** They come back in ANY order.
    Reading them positionally silently attaches every verdict to the wrong job.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from app.feed.models import Job
from app.intelligence.prompts import (VERDICT_SCHEMA, render_posting,
                                      system_prefix)

# handoff Part 3.3. Assessment runs on the cheap model by design; the strong
# model is spent where a mistake is unrecoverable, which is outreach drafting.
ASSESSMENT_MODEL = "claude-haiku-4-5"
DRAFTING_MODEL = "claude-sonnet-5"

#: spec 7: ~25 postings per batch.
BATCH_SIZE = 25

BUCKETS = ("strong", "possible", "rejected", "judgement-call")


def _normalise(text: str) -> str:
    """Collapse whitespace and unify quote characters for quote matching.

    A model reproducing a line verbatim may still normalise a curly quote or a
    line break. That is not a hallucination, so it must not be treated as one.
    """
    text = (text or "").replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text).strip().casefold()


def quote_is_verbatim(quote: str, description: str) -> bool:
    """Does the quoted line actually occur in the source text?"""
    if not quote or not description:
        return False
    return _normalise(quote) in _normalise(description)


def render_job(job: Job, *, full: bool = False) -> str:
    """The posting block exactly as the model is shown it.

    One function builds it for the request AND for quote verification. The
    block carries field lines — salary, contract type, country — that the
    description does not, and a rejection may rest on one of them. Verified
    against the description alone, a true quote from a field line reads as a
    hallucination and a correct rejection is thrown away.
    """
    raw = job.raw_criteria or {}
    return render_posting(
        job.provider_job_id, job.title, job.company, ", ".join(job.locations),
        job.description_text, full=full, salary=job.salary,
        employment=", ".join(str(s) for s in raw.get("employment_statuses") or ()),
        posted=job.posted_at.isoformat() if job.posted_at else "",
        country=", ".join(str(c) for c in raw.get("country_codes") or ()))


@dataclass
class Verdict:
    job: Job
    bucket: str
    reason: str
    disqualifying_quote: str | None = None
    requirement_checked: bool = True
    full_read: bool = False
    #: Set when a guard changed the model's verdict. Always surfaced.
    downgraded_from: str | None = None
    downgrade_reason: str | None = None

    @property
    def is_positive(self) -> bool:
        return self.bucket in ("strong", "possible")

    @property
    def needs_full_read(self) -> bool:
        """spec 7.6: re-read the full description before any STRONG verdict."""
        return self.bucket == "strong" and not self.full_read


def enforce_quote_rule(raw: dict, job: Job, rendered: str | None = None) -> Verdict:
    """Turn one model verdict into a trusted one, or downgrade it.

    Two guards, in order:

      1. requirement_checked=false may not co-exist with a rejection. An
         unfetchable or absent requirement is "not checked", never a failure.
      2. a rejection carrying a quote that is NOT in the posting block the
         model was shown is a hallucinated rejection. Downgrade it and say so.

    `rendered` is that block. Without one the full block is rendered, which is
    the most the model could have seen.
    """
    bucket = raw.get("bucket") or "judgement-call"
    if bucket not in BUCKETS:
        bucket = "judgement-call"
    reason = (raw.get("reason") or "").strip() or "no reason given"
    quote = raw.get("disqualifying_quote") or None
    checked = bool(raw.get("requirement_checked", True))

    v = Verdict(job=job, bucket=bucket, reason=reason,
                disqualifying_quote=quote, requirement_checked=checked)

    if bucket == "rejected" and not checked:
        v.downgraded_from, v.bucket = "rejected", "possible"
        v.downgrade_reason = (
            "requirement could not be checked; an unfetchable requirement is "
            "'not checked', never a failure")
        return v

    source = rendered if rendered is not None else render_job(job, full=True)
    if bucket == "rejected" and quote and not quote_is_verbatim(quote, source):
        v.downgraded_from, v.bucket = "rejected", "judgement-call"
        v.downgrade_reason = (
            "the quoted disqualifying line does not appear in the description "
            "or the posting's fields — treating this rejection as unverified")
        return v

    return v


@dataclass
class AssessmentReport:
    verdicts: list[Verdict] = field(default_factory=list)
    #: Jobs that were in the likely set but never judged. MUST be reported.
    unread: list[Job] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.unread and not self.errors

    @property
    def downgraded(self) -> list[Verdict]:
        """Report the demotion count every run so the filter's aggressiveness
        stays visible (spec 6.7)."""
        return [v for v in self.verdicts if v.downgraded_from]

    @property
    def counts(self) -> dict[str, int]:
        out = {b: 0 for b in BUCKETS}
        for v in self.verdicts:
            out[v.bucket] += 1
        out["assessed"] = len(self.verdicts)
        out["left_unread"] = len(self.unread)
        out["downgraded"] = len(self.downgraded)
        return out


def batches(seq: Sequence[Job], size: int = BATCH_SIZE) -> Iterable[list[Job]]:
    for i in range(0, len(seq), size):
        yield list(seq[i:i + size])


def build_request(jobs: list[Job], fit_brief: str, factsheet: str, *,
                  model: str = ASSESSMENT_MODEL, full: bool = False) -> dict:
    """One Messages request. `custom_id` keying is the caller's job."""
    postings = "\n\n".join(render_job(j, full=full) for j in jobs)
    return {
        "model": model,
        "max_tokens": 8000,
        "system": system_prefix(fit_brief, factsheet),
        "messages": [{"role": "user", "content": postings}],
        "output_config": {
            "format": {
                "type": "json_schema",
                "schema": VERDICT_SCHEMA,
            }
        },
    }


def parse_verdicts(payload: Any, jobs_by_ref: dict[str, Job],
                   rendered_by_ref: dict[str, str] | None = None,
                   ) -> tuple[list[Verdict], list[str]]:
    """Map a structured response back onto jobs BY REF, never by position.

    `rendered_by_ref` holds the blocks as sent, so a quote is checked against
    what the model actually read — a first-pass block is truncated.
    """
    errors: list[str] = []
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as e:
            return [], [f"unparseable verdict payload: {e}"]

    rendered_by_ref = rendered_by_ref or {}
    rows = (payload or {}).get("verdicts") or []
    out: list[Verdict] = []
    seen: set[str] = set()
    for row in rows:
        ref = str(row.get("job_ref", ""))
        job = jobs_by_ref.get(ref)
        if job is None:
            errors.append(f"verdict for unknown ref {ref!r} — discarded")
            continue
        if ref in seen:
            errors.append(f"duplicate verdict for ref {ref!r} — first kept")
            continue
        seen.add(ref)
        out.append(enforce_quote_rule(row, job, rendered_by_ref.get(ref)))
    return out, errors


def assess(jobs: list[Job], fit_brief: str, factsheet: str, *,
           send,
           already_judged: set[str] | None = None,
           model: str = ASSESSMENT_MODEL) -> AssessmentReport:
    """Assess every job in `jobs`. Resumable, and honest about what it missed.

    `send(request) -> payload` is injected so the caller owns transport: a live
    Anthropic client, the Batch API, or a stub in tests. Nothing here spends
    money by itself.

    `already_judged` carries provider_job_ids from a crashed run's scratch
    state; those are skipped, which is what halves the cost of a bad day
    (spec 7.7).
    """
    already_judged = already_judged or set()
    todo = [j for j in jobs if j.provider_job_id not in already_judged]
    report = AssessmentReport()

    for chunk in batches(todo):
        by_ref = {j.provider_job_id: j for j in chunk}
        rendered = {ref: render_job(j) for ref, j in by_ref.items()}
        try:
            payload = send(build_request(chunk, fit_brief, factsheet, model=model))
        except Exception as exc:  # noqa: BLE001
            # The batch is unread, not rejected. Never silently dropped.
            report.errors.append(f"batch failed: {type(exc).__name__}: {exc}")
            report.unread.extend(chunk)
            continue

        verdicts, errs = parse_verdicts(payload, by_ref, rendered)
        report.verdicts.extend(verdicts)
        report.errors.extend(errs)

        judged = {v.job.provider_job_id for v in verdicts}
        missing = [j for j in chunk if j.provider_job_id not in judged]
        report.unread.extend(missing)

    _second_pass(report, fit_brief, factsheet, send=send, model=model)
    return report


def _second_pass(report: "AssessmentReport", fit_brief: str, factsheet: str, *,
                 send, model: str) -> None:
    """spec 7.6 — re-read the FULL description before trusting a strong verdict.

    The first pass truncates to FIRST_PASS_CHARS (1,200) and says so in the
    prompt. Descriptions average about 7,400 characters, so a first-pass
    verdict is formed on roughly a sixth of the posting. That is fine for
    setting most of them aside; it is not enough to put one at the top of
    someone's shortlist, which is exactly what a STRONG verdict does.

    This existed only as an unasked question until 2026-09-08: `needs_full_read`
    computed the answer, `render_posting` honoured `full=True`, and nothing
    joined them — so every strong verdict was issued on a truncated read while
    the product's stated differentiator was that it reads every posting in
    full.

    Cost note: this is CHEAPER than sending full text on the first pass, not
    more expensive. Only strong candidates are re-read, and they are a small
    fraction of what is assessed.

    A failed re-read does NOT silently promote or demote anything. The verdict
    stands as the model gave it, `full_read` stays False so `needs_full_read`
    still reports True, and the failure is recorded on the report — a run that
    could not complete its second pass must not be reportable as a clean one.
    """
    pending = [v for v in report.verdicts if v.needs_full_read]
    if not pending:
        return

    by_ref = {v.job.provider_job_id: v for v in pending}

    for chunk in batches([v.job for v in pending]):
        try:
            payload = send(build_request(chunk, fit_brief, factsheet,
                                         model=model, full=True))
        except Exception as exc:  # noqa: BLE001
            report.errors.append(
                f"full re-read failed for {len(chunk)} strong verdict(s) "
                f"({type(exc).__name__}: {exc}) — they were judged on a "
                f"truncated description")
            continue

        chunk_refs = {j.provider_job_id: j for j in chunk}
        rereads, errs = parse_verdicts(
            payload, chunk_refs,
            {ref: render_job(j, full=True) for ref, j in chunk_refs.items()})
        report.errors.extend(errs)

        for fresh in rereads:
            ref = fresh.job.provider_job_id
            original = by_ref.get(ref)
            if original is None:
                continue
            fresh.full_read = True
            # A re-read that CHANGES the verdict is the whole point, and the
            # change is surfaced rather than quietly applied: the first answer
            # was formed on a sixth of the text, and the user is entitled to
            # know the fuller read disagreed.
            if fresh.bucket != original.bucket:
                fresh.downgraded_from = original.bucket
                fresh.downgrade_reason = (
                    "re-read in full: the complete description changed the "
                    f"verdict from {original.bucket}")
            report.verdicts[report.verdicts.index(original)] = fresh


def merge_batch_results(results: Iterable[Any],
                        jobs_by_custom_id: dict[str, Job]) -> tuple[list[Verdict], list[str]]:
    """Batch API results arrive in ANY order — key by custom_id, never position.

    Reading them positionally attaches every verdict to the wrong job, and the
    output looks entirely plausible, which is what makes it dangerous.
    """
    verdicts: list[Verdict] = []
    errors: list[str] = []
    for result in results:
        custom_id = getattr(result, "custom_id", None) or (
            result.get("custom_id") if isinstance(result, dict) else None)
        if custom_id is None:
            errors.append("batch result with no custom_id — discarded")
            continue
        job = jobs_by_custom_id.get(custom_id)
        if job is None:
            errors.append(f"batch result for unknown custom_id {custom_id!r}")
            continue

        body = getattr(result, "result", None) or (
            result.get("result") if isinstance(result, dict) else None)
        kind = getattr(body, "type", None) or (
            body.get("type") if isinstance(body, dict) else None)
        if kind != "succeeded":
            errors.append(f"{custom_id}: batch entry {kind!r}")
            continue

        message = getattr(body, "message", None) or body.get("message")
        content = getattr(message, "content", None) or message.get("content")
        text = ""
        for block in content or []:
            btype = getattr(block, "type", None) or (
                block.get("type") if isinstance(block, dict) else None)
            if btype == "text":
                text += getattr(block, "text", None) or block.get("text", "")

        parsed, errs = parse_verdicts(text, {job.provider_job_id: job})
        verdicts.extend(parsed)
        errors.extend(errs)
    return verdicts, errors
