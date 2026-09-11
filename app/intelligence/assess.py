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
from typing import Any, Callable, Iterable, Sequence

from app.feed.models import Job
from app.intelligence.prompts import (FIRST_PASS_CHARS, VERDICT_SCHEMA,
                                      render_posting, system_prefix)

# handoff Part 3.3. Assessment runs on the cheap model by design; the strong
# model is spent where a mistake is unrecoverable, which is outreach drafting.
ASSESSMENT_MODEL = "claude-haiku-4-5"
DRAFTING_MODEL = "claude-sonnet-5"

#: spec 7: ~25 postings per batch.
BATCH_SIZE = 25

#: Description characters per request, as well as a count.
#:
#: Twenty-five postings cut at 1,200 characters were small whatever they said.
#: Read whole, twenty-five long ones can approach the assessment model's
#: 200,000-token context, and a request that overflows it fails outright and
#: leaves the whole batch unread. Roughly 50,000 tokens keeps a request well
#: inside the window and quick to come back.
BATCH_CHARS = 200_000

BUCKETS = ("strong", "possible", "rejected", "judgement-call")


@dataclass(frozen=True)
class ModelReply:
    """What the live transport returns: the text, why the reply ended, and
    what it cost.

    The text alone cannot say it was cut off or refused, and once parsed both
    look like a malformed payload with the cause lost. The token counts are
    the user's own money on their own key, and nothing recorded any of them.
    """
    text: str
    stop_reason: str | None = None
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    #: Read from and written to the prompt cache. Both zero on every call means
    #: the prefix never cached: under the model's minimum, or changing between
    #: requests.
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True)
class ModelCall:
    """One request's usage, as stored with its run."""
    model: str
    stop_reason: str | None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    full_read: bool = False

    @property
    def cached_prefix_tokens(self) -> int:
        return self.cache_read_tokens + self.cache_write_tokens


class ModelStopped(RuntimeError):
    """A reply that ended before it could be a verdict payload."""


class ReplyTruncated(ModelStopped):
    """stop_reason max_tokens: the verdicts did not fit in the reply."""


class ReplyRefused(ModelStopped):
    """stop_reason refusal: the model declined to answer this batch."""


#: The account cannot be used — a bad key, a missing permission, no balance —
#: as opposed to a request that may work if sent again.
ACCOUNT_FAULT_STATUSES = frozenset({401, 402, 403})
ACCOUNT_FAULT_TYPES = frozenset({"authentication_error", "permission_error",
                                 "billing_error"})


def is_account_fault(exc: BaseException) -> bool:
    """Would every later request fail the same way?

    Judged on the status and the API's error type rather than the SDK's class
    names, so a stub and the real client are read alike. An empty balance can
    arrive as a 400 whose message names the credit balance, so that counts.
    """
    if getattr(exc, "status_code", None) in ACCOUNT_FAULT_STATUSES:
        return True
    body = getattr(exc, "body", None)
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict) and error.get("type") in ACCOUNT_FAULT_TYPES:
        return True
    return "credit balance" in str(exc).lower()


def _reply_payload(reply: Any) -> Any:
    """What `parse_verdicts` reads, from whatever the transport returned.

    A `ModelReply` is checked before it is parsed. A reply cut off at
    max_tokens went to the JSON parser and was reported as an "unparseable
    verdict payload", and a refusal as whatever its text happened to be — the
    run recorded a symptom and never the cause.
    """
    if isinstance(reply, ModelReply):
        if reply.stop_reason == "max_tokens":
            raise ReplyTruncated(
                "the reply reached max_tokens before the verdicts were complete")
        if reply.stop_reason == "refusal":
            raise ReplyRefused("the model refused to assess this batch")
        return reply.text
    return reply


def _record_call(report: "AssessmentReport", reply: Any, model: str,
                 full_read: bool, on_call) -> None:
    """Keep what a request cost, before anything decides the reply is usable:
    a reply cut off at max_tokens was billed all the same."""
    if not isinstance(reply, ModelReply):
        return
    call = ModelCall(model=reply.model or model, stop_reason=reply.stop_reason,
                     input_tokens=reply.input_tokens,
                     output_tokens=reply.output_tokens,
                     cache_read_tokens=reply.cache_read_tokens,
                     cache_write_tokens=reply.cache_write_tokens,
                     full_read=full_read)
    report.calls.append(call)
    if on_call:
        on_call(call)


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


def job_ref(job: Job) -> str:
    """The ref a posting is shown to the model under, and matched back by.

    Qualified by provider, because a provider's ids are unique only within
    that provider. An alert email's LinkedIn number and a TheirStack id can be
    the same digits, and keyed by the bare id the second posting in a batch
    replaced the first: one took the other's verdict, and the other was never
    judged and never reported unread.
    """
    return f"{job.provider}:{job.provider_job_id}"


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
        job_ref(job), job.title, job.company, ", ".join(job.locations),
        job.description_text, full=full, salary=job.salary,
        employment=", ".join(str(s) for s in raw.get("employment_statuses") or ()),
        posted=job.posted_at.isoformat() if job.posted_at else "",
        country=", ".join(str(c) for c in raw.get("country_codes") or ()))


def read_whole_on_first_pass(job: Job) -> bool:
    return len(job.description_text or "") <= FIRST_PASS_CHARS


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
    #: The model that formed this verdict. Stored with it, because a verdict
    #: whose model is unknown cannot be compared with one given later, and
    #: the assessment model is the one setting that changes what every
    #: verdict means.
    model: str = ""

    @property
    def is_positive(self) -> bool:
        return self.bucket in ("strong", "possible")

    @property
    def needs_full_read(self) -> bool:
        """spec 7.6: nothing reaches the user on a cut-off read.

        Not only strong. A `possible` or a `judgement-call` also puts a posting
        in front of the user and asks them to act on it, and a verdict formed
        on a cut-off description is exactly the one the old rule 5 pushed into
        `possible` for want of the rest. A rejection is not re-read: it stands
        on what was read, which at this ceiling is several postings' worth.
        """
        return self.bucket != "rejected" and not self.full_read


#: Below both of these a "quote" proves nothing. "10 years" or "London" sits in
#: countless postings that set no such bar, so a fragment that happened to be
#: in the text verified a rejection it did not support.
MIN_QUOTE_CHARS = 20
MIN_QUOTE_WORDS = 4


def _field_lines(rendered: str) -> set[str]:
    """The block's labelled field lines, normalised. A whole field line is a
    complete statement however short — "country: US" — so the length floor
    does not apply to one."""
    head = (rendered or "").split("\ndescription:", 1)[0]
    return {_normalise(line) for line in head.splitlines()[1:] if ":" in line}


def quote_is_substantial(quote: str, rendered: str) -> bool:
    text = _normalise(quote)
    return (len(text) >= MIN_QUOTE_CHARS
            or len(text.split()) >= MIN_QUOTE_WORDS
            or text in _field_lines(rendered))


def enforce_quote_rule(raw: dict, job: Job, rendered: str | None = None) -> Verdict:
    """Turn one model verdict into a trusted one, or downgrade it.

    Four guards, in order:

      1. requirement_checked=false may not co-exist with a rejection. An
         unfetchable or absent requirement is "not checked", never a failure.
      2. a checked rejection must quote the line it rests on. The quote used
         to be verified only when there was one, so the check caught a model
         that quoted the wrong line and trusted one that quoted nothing.
      3. the quote must be a clause or a whole field line, not a fragment
         that occurs by coincidence.
      4. a quote that is NOT in the posting block the model was shown is a
         hallucinated rejection.

    Each downgrade says why. `rendered` is the block as sent; without one the
    full block is rendered, which is the most the model could have seen.
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

    if bucket != "rejected":
        return v

    source = rendered if rendered is not None else render_job(job, full=True)
    if not quote:
        v.downgraded_from, v.bucket = "rejected", "judgement-call"
        v.downgrade_reason = (
            "a rejection must quote the line it rests on, and this one quoted "
            "nothing — treating it as unverified")
    elif not quote_is_substantial(quote, source):
        v.downgraded_from, v.bucket = "rejected", "judgement-call"
        v.downgrade_reason = (
            f"the quote {quote!r} is too short to show what the rejection "
            "rests on — treating it as unverified")
    elif not quote_is_verbatim(quote, source):
        v.downgraded_from, v.bucket = "rejected", "judgement-call"
        v.downgrade_reason = (
            "the quoted disqualifying line does not appear in the description "
            "or the posting's fields — treating this rejection as unverified")
    return v


@dataclass
class AssessmentReport:
    verdicts: list[Verdict] = field(default_factory=list)
    #: Jobs that were in the likely set but never judged. MUST be reported.
    unread: list[Job] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    #: Every request's usage, in the order sent.
    calls: list[ModelCall] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.unread and not self.errors

    @property
    def cached_prefix_tokens(self) -> int:
        """The cached prefix as the API measured it; 0 means it never cached.

        Measured rather than assumed, because a prefix under the model's
        caching minimum is ignored without any error (`prompts.system_prefix`).
        """
        return max((c.cached_prefix_tokens for c in self.calls), default=0)

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


def batches(seq: Sequence[Job], size: int = BATCH_SIZE, *,
            max_chars: int = BATCH_CHARS, full: bool = False,
            ) -> Iterable[list[Job]]:
    """Consecutive batches of at most `size` postings and `max_chars` of
    description as sent. A single posting larger than that goes alone rather
    than being refused, because an unread posting is the failure."""
    chunk: list[Job] = []
    used = 0
    for job in seq:
        n = len(job.description_text or "")
        if not full:
            n = min(n, FIRST_PASS_CHARS)
        if chunk and (len(chunk) >= size or used + n > max_chars):
            yield chunk
            chunk, used = [], 0
        chunk.append(job)
        used += n
    if chunk:
        yield chunk


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
    what the model actually read — a first-pass block may be cut.
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
           model: str = ASSESSMENT_MODEL,
           on_batch: Callable[[list[Verdict]], None] | None = None,
           on_call: Callable[[ModelCall], None] | None = None,
           ) -> AssessmentReport:
    """Assess every job in `jobs`. Resumable, and honest about what it missed.

    `on_call(call)` receives each request's usage as it returns, so it can be
    stored with the run while the run is still going.

    `send(request) -> payload` is injected so the caller owns transport: a live
    Anthropic client, the Batch API, or a stub in tests. Nothing here spends
    money by itself.

    `already_judged` carries the refs (`job_ref`) of postings already
    assessed; those are skipped, which is what halves the cost of a bad day
    (spec 7.7).

    `on_batch(verdicts)` is called as each batch's verdicts arrive, and again
    with any verdict the full re-read replaced. The report exists only in
    memory until the run ends, so without it an interrupted run lost every
    verdict it had already paid for.
    """
    already_judged = already_judged or set()
    todo = [j for j in jobs if job_ref(j) not in already_judged]
    report = AssessmentReport()

    chunks = list(batches(todo))
    for index, chunk in enumerate(chunks):
        by_ref = {job_ref(j): j for j in chunk}
        rendered = {ref: render_job(j) for ref, j in by_ref.items()}
        try:
            reply = send(build_request(chunk, fit_brief, factsheet, model=model))
            _record_call(report, reply, model, False, on_call)
            payload = _reply_payload(reply)
        except Exception as exc:  # noqa: BLE001
            if is_account_fault(exc):
                # Every later batch would be refused the same way. Sending them
                # recorded one identical error per batch, and on a billing
                # fault kept knocking on an account with nothing in it.
                left = chunks[index:]
                report.errors.append(
                    f"assessment stopped: {type(exc).__name__}: {exc} — "
                    f"{sum(len(c) for c in left)} posting(s) left unread "
                    f"rather than sent again on the same account")
                for rest in left:
                    report.unread.extend(rest)
                return report
            # The batch is unread, not rejected. Never silently dropped.
            report.errors.append(f"batch failed: {type(exc).__name__}: {exc}")
            report.unread.extend(chunk)
            continue

        verdicts, errs = parse_verdicts(payload, by_ref, rendered)
        for v in verdicts:
            v.full_read = read_whole_on_first_pass(v.job)
            v.model = model
        report.verdicts.extend(verdicts)
        report.errors.extend(errs)
        if on_batch and verdicts:
            on_batch(verdicts)

        judged = {job_ref(v.job) for v in verdicts}
        missing = [j for j in chunk if job_ref(j) not in judged]
        report.unread.extend(missing)

    _second_pass(report, fit_brief, factsheet, send=send, model=model,
                 on_batch=on_batch, on_call=on_call)
    return report


def _second_pass(report: "AssessmentReport", fit_brief: str, factsheet: str, *,
                 send, model: str,
                 on_batch: Callable[[list[Verdict]], None] | None = None,
                 on_call: Callable[[ModelCall], None] | None = None) -> None:
    """spec 7.6 — re-read in full any verdict formed on a cut-off description.

    The first pass reads a description whole up to FIRST_PASS_CHARS, which is
    several times the average posting, so this runs for outliers rather than
    for most of the pile. It covers every verdict that would reach the user —
    strong, possible, judgement-call — because each asks them to act on it.

    This existed only as an unasked question until 2026-09-08: `needs_full_read`
    computed the answer, `render_posting` honoured `full=True`, and nothing
    joined them — so every strong verdict was issued on a truncated read while
    the product's stated differentiator was that it reads every posting in
    full.

    Batched by character count with no ceiling on the description, so an
    outlier long enough to have been cut is read alone rather than cut again.

    A failed re-read does NOT silently promote or demote anything. The verdict
    stands as the model gave it, `full_read` stays False so `needs_full_read`
    still reports True, and the failure is recorded on the report — a run that
    could not complete its second pass must not be reportable as a clean one.
    """
    pending = [v for v in report.verdicts if v.needs_full_read]
    if not pending:
        return

    by_ref = {job_ref(v.job): v for v in pending}

    chunks = list(batches([v.job for v in pending], full=True))
    for index, chunk in enumerate(chunks):
        try:
            reply = send(build_request(chunk, fit_brief, factsheet,
                                       model=model, full=True))
            _record_call(report, reply, model, True, on_call)
            payload = _reply_payload(reply)
        except Exception as exc:  # noqa: BLE001
            if is_account_fault(exc):
                left = sum(len(c) for c in chunks[index:])
                report.errors.append(
                    f"full re-read stopped: {type(exc).__name__}: {exc} — "
                    f"{left} verdict(s) were judged on a truncated description")
                return
            report.errors.append(
                f"full re-read failed for {len(chunk)} verdict(s) "
                f"({type(exc).__name__}: {exc}) — they were judged on a "
                f"truncated description")
            continue

        chunk_refs = {job_ref(j): j for j in chunk}
        rereads, errs = parse_verdicts(
            payload, chunk_refs,
            {ref: render_job(j, full=True) for ref, j in chunk_refs.items()})
        report.errors.extend(errs)

        # A re-read that does not come back is not a re-read. The verdict
        # still stands on the cut-off text, and with nothing recorded the run
        # was reported clean while `needs_full_read` stayed true unseen.
        returned = {job_ref(v.job) for v in rereads}
        for ref in chunk_refs:
            if ref not in returned:
                report.errors.append(
                    f"no full re-read came back for {ref} — its verdict was "
                    f"formed on a truncated description")

        replaced: list[Verdict] = []
        for fresh in rereads:
            original = by_ref.get(job_ref(fresh.job))
            if original is None:
                continue
            replaced.append(fresh)
            fresh.full_read = True
            fresh.model = model
            # A re-read that CHANGES the verdict is the whole point, and the
            # change is surfaced rather than quietly applied: the first answer
            # was formed on a cut-off description, and the user is entitled to
            # know the fuller read disagreed.
            if fresh.bucket != original.bucket:
                fresh.downgraded_from = original.bucket
                fresh.downgrade_reason = (
                    "re-read in full: the complete description changed the "
                    f"verdict from {original.bucket}")
            report.verdicts[report.verdicts.index(original)] = fresh
        if on_batch and replaced:
            on_batch(replaced)


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

        parsed, errs = parse_verdicts(text, {job_ref(job): job})
        verdicts.extend(parsed)
        errors.extend(errs)
    return verdicts, errors


def anthropic_transport(api_key: str | None = None, *, client=None):
    """The live assessment transport: straight to Anthropic on the user's key.

    `main.build_send` returns only text, which suits the app's other model
    calls. The assessment has to know why a reply ended — max_tokens and
    refusal are not verdict payloads — so this returns a `ModelReply`. The
    data route is the same one: the user's machine to Anthropic, nothing in
    between. `client` is injectable so a test never reaches the network.
    """
    if client is None:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

    def send(request: dict) -> ModelReply:
        response = client.messages.create(**request)
        usage = getattr(response, "usage", None)

        def count(name: str) -> int:
            # The cache counts are absent or None on a request that used no
            # cache, which is a zero and not a missing figure.
            return int(getattr(usage, name, 0) or 0)

        return ModelReply(
            text="".join(getattr(b, "text", "") for b in response.content
                         if getattr(b, "type", None) == "text"),
            stop_reason=getattr(response, "stop_reason", None),
            model=getattr(response, "model", "") or request.get("model", ""),
            input_tokens=count("input_tokens"),
            output_tokens=count("output_tokens"),
            cache_read_tokens=count("cache_read_input_tokens"),
            cache_write_tokens=count("cache_creation_input_tokens"))

    return send
