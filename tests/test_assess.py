"""Assessment guards. No network: `send` is injected, so nothing here spends money."""
import json

import pytest

from app.feed.models import Job
from app.intelligence.assess import (AssessmentReport, assess, build_request,
                                     enforce_quote_rule, merge_batch_results,
                                     parse_verdicts, quote_is_verbatim)
from app.intelligence.prompts import FIRST_PASS_CHARS, render_posting, system_prefix

DESC = ("We are seeking a Director of Asset Management. "
        "Candidates must have a minimum of 10 years' experience in real estate. "
        "RICS qualification is required.")


def job(jid="1", title="Director of Asset Management", company="Acme",
        description=DESC):
    return Job(provider="theirstack", provider_job_id=jid, title=title,
               company=company, description_text=description)


# -- spec 6.7: the quote is VERIFIED, not merely requested ------------------
def test_a_real_quote_verifies():
    assert quote_is_verbatim("minimum of 10 years' experience", DESC)


def test_quote_matching_tolerates_curly_quotes_and_whitespace():
    """A model normalising a smart quote is not hallucinating."""
    assert quote_is_verbatim("minimum of 10 years’  experience", DESC)


def test_a_hallucinated_quote_does_not_verify():
    assert not quote_is_verbatim("must hold an MBA", DESC)


def test_rejection_with_a_hallucinated_quote_is_downgraded():
    v = enforce_quote_rule(
        {"bucket": "rejected", "reason": "needs an MBA",
         "disqualifying_quote": "must hold an MBA", "requirement_checked": True},
        job())
    assert v.bucket == "judgement-call"
    assert v.downgraded_from == "rejected"
    assert "does not appear in the description" in v.downgrade_reason


def test_rejection_with_a_real_quote_stands():
    v = enforce_quote_rule(
        {"bucket": "rejected", "reason": "10 year floor",
         "disqualifying_quote": "minimum of 10 years' experience",
         "requirement_checked": True},
        job())
    assert v.bucket == "rejected" and v.downgraded_from is None


def test_unchecked_requirement_can_never_be_a_rejection():
    v = enforce_quote_rule(
        {"bucket": "rejected", "reason": "description truncated",
         "disqualifying_quote": None, "requirement_checked": False},
        job())
    assert v.bucket == "possible"
    assert "never a failure" in v.downgrade_reason


def test_an_unknown_bucket_becomes_a_judgement_call():
    v = enforce_quote_rule({"bucket": "maybe", "reason": "x"}, job())
    assert v.bucket == "judgement-call"


# -- hard constraints: the model must be SHOWN what the brief constrains ----
def _constrained(**raw):
    from datetime import date
    return Job(provider="theirstack", provider_job_id="hc", title="Hotel Manager",
               company="Acme", description_text="Run a boutique hotel.",
               salary="£30,000 a year", posted_at=date(2026, 9, 1),
               raw_criteria=raw)


def test_the_fields_a_brief_constrains_reach_the_model():
    """A brief says "full-time only", "based in the UK", "£60k floor". The
    posting block carried title, company, location and description, so none
    of those could be applied — the model could only guess or reject on an
    assumption rule 4 forbids."""
    from app.intelligence.prompts import ASSESSMENT_RULES

    content = build_request(
        [_constrained(employment_statuses=["part_time"], country_codes=["US"])],
        "brief", "facts")["messages"][0]["content"]
    assert "salary: £30,000 a year" in content
    assert "employment type: part_time" in content
    assert "country: US" in content
    assert "posted: 2026-09-01" in content
    assert "HARD CONSTRAINT" in ASSESSMENT_RULES

    # An unknown field is shown as unknown, not left out, so rule 5 can apply.
    bare = build_request([job()], "brief", "facts")["messages"][0]["content"]
    assert "employment type: not stated" in bare
    assert "salary: not stated" in bare


def test_a_rejection_quoting_a_field_line_is_verified_against_the_block():
    """The quote was checked against the description alone, so a true quote
    from a field line — the one a hard-constraint rejection must cite — read
    as a hallucination and the correct rejection was thrown away."""
    part_time = _constrained(employment_statuses=["part_time"])

    def rejects(quote):
        def send(request):
            return {"verdicts": [{"job_ref": "theirstack:hc", "bucket": "rejected",
                                  "reason": "brief: full-time only",
                                  "disqualifying_quote": quote,
                                  "requirement_checked": True}]}
        return send

    report = assess([part_time], "Full-time roles only.", "facts",
                    send=rejects("employment type: part_time"))
    assert report.verdicts[0].bucket == "rejected"
    assert report.verdicts[0].downgraded_from is None

    # Positive control: a field line the posting does NOT carry is still
    # caught, so the block is a wider source and not a looser check.
    report = assess([part_time], "Full-time roles only.", "facts",
                    send=rejects("employment type: contract"))
    assert report.verdicts[0].bucket == "judgement-call"


# -- mapping is by ref, never by position -----------------------------------
def test_verdicts_map_by_ref_not_position():
    a, b = job("a", title="Strategy Lead"), job("b", title="Revenue Lead")
    payload = {"verdicts": [
        {"job_ref": "b", "bucket": "strong", "reason": "fits",
         "disqualifying_quote": None, "requirement_checked": True},
        {"job_ref": "a", "bucket": "rejected", "reason": "no",
         "disqualifying_quote": "minimum of 10 years' experience",
         "requirement_checked": True},
    ]}
    verdicts, errors = parse_verdicts(payload, {"a": a, "b": b})
    by_id = {v.job.provider_job_id: v.bucket for v in verdicts}
    assert by_id == {"a": "rejected", "b": "strong"}
    assert errors == []


def test_a_verdict_for_an_unknown_ref_is_discarded_loudly():
    verdicts, errors = parse_verdicts(
        {"verdicts": [{"job_ref": "ghost", "bucket": "strong", "reason": "x"}]},
        {"a": job("a")})
    assert verdicts == [] and "unknown ref" in errors[0]


def test_unparseable_payload_is_an_error_not_a_silent_empty():
    verdicts, errors = parse_verdicts("not json", {"a": job("a")})
    assert verdicts == [] and errors


# -- never buy budget by skipping reads -------------------------------------
def test_a_failed_batch_leaves_jobs_unread_never_rejected():
    jobs = [job(str(i)) for i in range(3)]

    def boom(_request):
        raise RuntimeError("503 from upstream")

    report = assess(jobs, "brief", "facts", send=boom)
    assert report.verdicts == []
    assert len(report.unread) == 3, "a failed batch is unread, not rejected"
    assert not report.complete
    assert report.counts["left_unread"] == 3


def test_a_posting_the_model_skipped_is_reported_unread():
    jobs = [job("a"), job("b")]

    def partial(_request):
        return {"verdicts": [{"job_ref": "theirstack:a", "bucket": "strong", "reason": "fits",
                              "disqualifying_quote": None,
                              "requirement_checked": True}]}

    report = assess(jobs, "brief", "facts", send=partial)
    assert [j.provider_job_id for j in report.unread] == ["b"]
    assert not report.complete


def test_resume_skips_already_judged_ids():
    jobs = [job("a"), job("b")]
    seen = []

    def send(request):
        seen.append(request)
        return {"verdicts": [{"job_ref": "theirstack:b", "bucket": "strong",
                              "reason": "x", "disqualifying_quote": None,
                              "requirement_checked": True}]}

    report = assess(jobs, "brief", "facts", send=send,
                    already_judged={"theirstack:a"})
    assert report.complete
    assert "ref=\"theirstack:a\"" not in seen[0]["messages"][0]["content"]


def test_downgrades_are_counted_every_run():
    jobs = [job("a")]

    def send(_r):
        return {"verdicts": [{"job_ref": "theirstack:a", "bucket": "rejected",
                              "reason": "invented", "requirement_checked": True,
                              "disqualifying_quote": "must hold an MBA"}]}

    report = assess(jobs, "brief", "facts", send=send)
    assert report.counts["downgraded"] == 1


# -- request shape ----------------------------------------------------------
def test_request_uses_structured_outputs_not_the_deprecated_parameter():
    req = build_request([job()], "brief", "facts")
    assert "output_config" in req and "output_format" not in req
    assert req["output_config"]["format"]["type"] == "json_schema"


def test_the_cache_breakpoint_is_on_the_last_system_block():
    blocks = system_prefix("brief", "facts")
    assert "cache_control" not in blocks[0]
    assert blocks[-1]["cache_control"] == {"type": "ephemeral"}


def test_no_volatile_content_in_the_cached_prefix():
    """Two calls with the same inputs must be byte-identical, or the cache
    never hits and the saving silently disappears."""
    assert system_prefix("brief", "facts") == system_prefix("brief", "facts")


def test_first_pass_truncation_is_announced():
    long = "x" * (FIRST_PASS_CHARS + 500)
    block = render_posting("1", "T", "C", "London", long)
    assert "[TRUNCATED" in block, (
        "an unannounced truncation reads as a complete description, so the "
        "model cannot know to set requirement_checked=false")
    assert "TRUNCATED" not in render_posting("1", "T", "C", "L", long, full=True)


# -- Batch API: results arrive in ANY order ---------------------------------
def test_batch_results_are_keyed_by_custom_id():
    a, b = job("a"), job("b")
    results = [
        {"custom_id": "b", "result": {"type": "succeeded", "message": {"content": [
            {"type": "text", "text": json.dumps({"verdicts": [
                {"job_ref": "theirstack:b", "bucket": "strong", "reason": "fits",
                 "disqualifying_quote": None, "requirement_checked": True}]})}]}}},
        {"custom_id": "a", "result": {"type": "succeeded", "message": {"content": [
            {"type": "text", "text": json.dumps({"verdicts": [
                {"job_ref": "theirstack:a", "bucket": "possible", "reason": "stretch",
                 "disqualifying_quote": None, "requirement_checked": True}]})}]}}},
    ]
    verdicts, errors = merge_batch_results(results, {"a": a, "b": b})
    assert {v.job.provider_job_id: v.bucket for v in verdicts} == {
        "a": "possible", "b": "strong"}
    assert errors == []


def test_an_errored_batch_entry_is_reported_not_dropped():
    verdicts, errors = merge_batch_results(
        [{"custom_id": "a", "result": {"type": "errored"}}], {"a": job("a")})
    assert verdicts == [] and "errored" in errors[0]


# -- strong verdicts need the full read -------------------------------------
def test_a_strong_verdict_from_a_truncated_pass_is_flagged_for_re_read():
    v = enforce_quote_rule({"bucket": "strong", "reason": "fits",
                            "requirement_checked": True}, job())
    assert v.needs_full_read, "spec 7.6: re-read in full before a strong verdict"


# ---------------------------------------------------------------------------
# spec 7.6 — the full re-read before a strong verdict
# ---------------------------------------------------------------------------
#
# The first pass reads a description whole up to FIRST_PASS_CHARS and cuts
# beyond it, so a verdict on an outlier has seen only part of the posting.
# These assert that the SECOND request actually happens and actually carries
# the full text — the mechanism existed for weeks with nothing calling it, and
# every test passed throughout.

def _job(ref, description):
    return Job(provider="theirstack", provider_job_id=ref, title="GM",
               company="Co", locations=("London",),
               description_text=description, url="")


# The marker sits BEYOND FIRST_PASS_CHARS on purpose: if it fell inside the
# truncation window the test would pass whether or not the re-read happened.
LONG = "A" * (FIRST_PASS_CHARS + 100) + " unique-marker-deep-in-the-text " + "B" * 6000


def _payload(ref, bucket, reason="because", quote=None):
    # The ref the model is shown carries the provider, because provider ids
    # are only unique within a provider. `_job` always builds a TheirStack one.
    return {"verdicts": [{"job_ref": f"theirstack:{ref}", "bucket": bucket,
                          "reason": reason, "disqualifying_quote": quote,
                          "requirement_checked": True}]}


def test_a_strong_verdict_triggers_a_second_request_with_the_full_text():
    sent = []

    def send(request):
        sent.append(request)
        return _payload("j1", "strong")

    report = assess([_job("j1", LONG)], "brief", "facts", send=send)

    assert len(sent) == 2, "a strong verdict must be re-read in full"
    first = str(sent[0])
    second = str(sent[1])
    assert "TRUNCATED" in first, "the first pass must truncate, and say so"
    assert "unique-marker-deep-in-the-text" not in first
    assert "unique-marker-deep-in-the-text" in second, "the re-read must carry the whole description"
    assert report.verdicts[0].full_read is True
    assert report.verdicts[0].needs_full_read is False


def test_a_rejection_is_not_re_read():
    """A rejection stands on what was read. Re-reading those too would repeat
    the whole first pass for the postings that are least likely to matter."""
    sent = []

    def send(request):
        sent.append(request)
        # Every rejection now quotes the line it rests on; one that quotes
        # nothing is a judgement call, and those ARE re-read.
        return _payload("j1", "rejected", quote="A" * 30)

    assess([_job("j1", LONG)], "brief", "facts", send=send)
    assert len(sent) == 1


def test_a_re_read_that_changes_its_mind_says_so():
    calls = []

    def send(request):
        calls.append(request)
        return _payload("j1", "strong" if len(calls) == 1 else "possible")

    report = assess([_job("j1", LONG)], "brief", "facts", send=send)
    v = report.verdicts[0]
    assert v.bucket == "possible"
    assert v.downgraded_from == "strong"
    assert "re-read in full" in v.downgrade_reason


def test_a_failed_re_read_is_recorded_and_never_hidden():
    """The verdict stands, but the run cannot be reported as clean."""
    calls = []

    def send(request):
        calls.append(request)
        if len(calls) > 1:
            raise RuntimeError("upstream down")
        return _payload("j1", "strong")

    report = assess([_job("j1", LONG)], "brief", "facts", send=send)
    assert report.verdicts[0].bucket == "strong"
    assert report.verdicts[0].full_read is False
    assert report.verdicts[0].needs_full_read is True
    assert any("truncated" in e.lower() for e in report.errors)


# ---------------------------------------------------------------------------
# The first pass reads the posting, not a sixth of it
# ---------------------------------------------------------------------------

# An average description, with the marker where a 1,200-character cut lost it.
AVERAGE = "A" * 7000 + " marker-in-the-last-part " + "B" * 400


def test_an_average_posting_is_read_whole_in_one_request():
    """Descriptions average ~7,400 characters and the first pass cut them at
    1,200, so every first verdict was formed on about a sixth of the posting."""
    sent = []

    def send(request):
        sent.append(request)
        return _payload("j1", "strong")

    report = assess([_job("j1", AVERAGE)], "brief", "facts", send=send)
    assert len(sent) == 1, "a posting read whole needs no second request"
    assert "marker-in-the-last-part" in str(sent[0])
    assert "TRUNCATED" not in str(sent[0])
    assert report.verdicts[0].full_read is True


def test_a_possible_formed_on_a_cut_off_read_is_re_read_in_full():
    """Only strong verdicts were re-read, so a `possible` given for want of
    the rest of the text went to the user as it was."""
    sent = []

    def send(request):
        sent.append(request)
        return {"verdicts": [{"job_ref": "theirstack:j1", "bucket": "possible",
                              "reason": "the rest is cut off",
                              "disqualifying_quote": None,
                              "requirement_checked": False}]}

    report = assess([_job("j1", LONG)], "brief", "facts", send=send)
    assert len(sent) == 2
    assert "unique-marker-deep-in-the-text" in str(sent[1])
    assert report.verdicts[0].full_read is True

    # Positive control: the same verdict on a posting read whole is final.
    sent.clear()
    assess([_job("j2", AVERAGE)], "brief", "facts",
           send=lambda r: sent.append(r) or {"verdicts": [
               {"job_ref": "theirstack:j2", "bucket": "possible", "reason": "stretch",
                "disqualifying_quote": None, "requirement_checked": True}]})
    assert len(sent) == 1


def test_truncation_alone_no_longer_forces_possible():
    """Rule 5 said a truncated description must be bucketed `possible`, which
    flooded the pile with verdicts formed on a sixth of each posting."""
    from app.intelligence.prompts import ASSESSMENT_RULES

    rules = " ".join(ASSESSMENT_RULES.split())
    assert "truncated or silent on something material, set requirement_checked " \
           "to false and bucket it as possible" not in rules
    assert "A truncated description is not by itself a reason to choose " \
           "possible" in rules
    # Positive control: an absent requirement is still never a failure.
    assert 'AN ABSENT REQUIREMENT IS "NOT CHECKED", NEVER A FAILURE' in rules


def test_long_postings_are_split_so_a_request_stays_inside_the_context():
    """Read whole, twenty-five long postings can approach the model's context
    window, and a request that overflows it leaves the whole batch unread."""
    from app.intelligence.assess import BATCH_CHARS, batches

    long_ones = [_job(str(i), "x" * 15_000) for i in range(25)]
    chunks = list(batches(long_ones))
    assert len(chunks) > 1
    assert all(sum(min(len(j.description_text), FIRST_PASS_CHARS) for j in c)
               <= BATCH_CHARS for c in chunks)
    assert sum(len(c) for c in chunks) == 25, "nothing is dropped by splitting"

    # Positive control: short postings still travel twenty-five at a time.
    assert [len(c) for c in batches([_job(str(i), "x" * 500)
                                     for i in range(25)])] == [25]


def test_a_re_read_that_never_comes_back_is_an_error():
    """The second pass replaced what came back and said nothing about what did
    not, so a verdict still standing on a cut-off read left a clean report."""
    calls = []

    def send(request):
        calls.append(request)
        if len(calls) == 1:
            return _payload("j1", "strong")
        return {"verdicts": []}            # the re-read answered for nobody

    report = assess([_job("j1", LONG)], "brief", "facts", send=send)
    assert len(calls) == 2, "positive control: the re-read was asked for"
    assert report.verdicts[0].needs_full_read
    assert any("j1" in e and "truncated" in e for e in report.errors)
    assert not report.complete

    # Positive control: a re-read that does come back leaves no error.
    calls.clear()
    clean = assess([_job("j1", LONG)], "brief", "facts",
                   send=lambda r: calls.append(r) or _payload("j1", "strong"))
    assert len(calls) == 2
    assert clean.errors == [] and clean.complete


def test_two_sources_sharing_an_id_are_never_confused():
    """Refs were bare provider ids, and ids are only unique within a provider:
    an alert email's LinkedIn number and a TheirStack id can be the same
    digits. In one batch the second overwrote the first in the ref map, so one
    posting took the other's verdict and the other was never judged."""
    import re

    fed = Job(provider="theirstack", provider_job_id="42", title="General Manager",
              company="Rosewood", description_text="A hotel role.")
    alert = Job(provider="alert-email", provider_job_id="42", title="Porter",
                company="Elsewhere", description_text="A portering role.")
    buckets = {"theirstack:42": "strong", "alert-email:42": "possible"}

    def send(request):
        refs = re.findall(r'ref="([^"]+)"', request["messages"][0]["content"])
        return {"verdicts": [{"job_ref": r, "bucket": buckets[r], "reason": "x",
                              "disqualifying_quote": None,
                              "requirement_checked": True} for r in refs]}

    report = assess([fed, alert], "brief", "facts", send=send)
    assert {v.job.provider: v.bucket for v in report.verdicts} == {
        "theirstack": "strong", "alert-email": "possible"}
    assert report.unread == [] and report.errors == []

    # Resuming skips exactly the posting already judged, not both.
    sent = []
    assess([fed, alert], "brief", "facts", already_judged={"theirstack:42"},
           send=lambda r: sent.append(r) or send(r))
    assert re.findall(r'ref="([^"]+)"', sent[0]["messages"][0]["content"]) \
        == ["alert-email:42"]


# -- a rejection proves itself with a real line, not a fragment or nothing ---
def test_a_rejection_that_quotes_nothing_is_not_trusted():
    """The quote was verified only when there was one. A rejection with no
    quote at all stood as given, so the check caught a model that quoted the
    wrong line and trusted one that quoted none."""
    v = enforce_quote_rule({"bucket": "rejected", "reason": "not senior enough",
                            "disqualifying_quote": None,
                            "requirement_checked": True}, job())
    assert v.bucket == "judgement-call" and v.downgraded_from == "rejected"
    assert "quoted nothing" in v.downgrade_reason

    # Positive control: the same rejection citing its line stands.
    cited = enforce_quote_rule(
        {"bucket": "rejected", "reason": "not senior enough",
         "disqualifying_quote": "minimum of 10 years' experience in real estate",
         "requirement_checked": True}, job())
    assert cited.bucket == "rejected" and cited.downgraded_from is None


def test_a_fragment_too_short_to_prove_anything_is_not_a_quote():
    """"10 years" sits in postings that set no such bar, so a two-word
    fragment that happened to be in the text verified a rejection it did not
    support."""
    fragment = enforce_quote_rule({"bucket": "rejected", "reason": "years floor",
                                   "disqualifying_quote": "10 years",
                                   "requirement_checked": True}, job())
    assert quote_is_verbatim("10 years", DESC), "positive control: it IS in the text"
    assert fragment.bucket == "judgement-call"
    assert "too short" in fragment.downgrade_reason

    # Positive controls: a four-word clause, and a whole field line however
    # short, are enough.
    clause = enforce_quote_rule({"bucket": "rejected", "reason": "credential",
                                 "disqualifying_quote": "RICS qualification is required",
                                 "requirement_checked": True}, job())
    assert clause.bucket == "rejected"
    abroad = Job(provider="theirstack", provider_job_id="us", title="GM",
                 company="Acme", description_text="A hotel role.",
                 raw_criteria={"country_codes": ["US"]})
    field_line = enforce_quote_rule({"bucket": "rejected", "reason": "brief: UK only",
                                     "disqualifying_quote": "country: US",
                                     "requirement_checked": True}, abroad)
    assert field_line.bucket == "rejected"


# -- a reply that stopped short is named, and a dead account stops the run ---
from app.intelligence.assess import (ModelReply,  # noqa: E402
                                     anthropic_transport)


def _reply_for(request, stop_reason="end_turn"):
    import re
    refs = re.findall(r'ref="([^"]+)"', request["messages"][0]["content"])
    return ModelReply(text=json.dumps({"verdicts": [
        {"job_ref": r, "bucket": "possible", "reason": "stretch",
         "disqualifying_quote": None, "requirement_checked": True}
        for r in refs]}), stop_reason=stop_reason, model="claude-haiku-4-5")


def test_a_reply_cut_off_at_max_tokens_is_named_not_parsed():
    """A reply that stopped at max_tokens went to the JSON parser, so the run
    said "unparseable verdict payload" and never that the verdicts ran out of
    room."""
    report = assess([job("a")], "b", "f",
                    send=lambda r: _reply_for(r, "max_tokens"))
    assert report.verdicts == [] and len(report.unread) == 1
    assert any("ReplyTruncated" in e for e in report.errors)

    # Positive control: the same reply with a normal stop is read as verdicts.
    fine = assess([job("a")], "b", "f", send=_reply_for)
    assert [v.bucket for v in fine.verdicts] == ["possible"]
    assert fine.errors == []


def test_a_refused_reply_is_named_not_parsed():
    report = assess([job("a")], "b", "f", send=lambda r: _reply_for(r, "refusal"))
    assert report.verdicts == [] and len(report.unread) == 1
    assert any("ReplyRefused" in e for e in report.errors)


class _ApiFault(Exception):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


def test_an_account_fault_stops_the_run_instead_of_repeating_it():
    """A bad key, a revoked permission or an empty balance failed every batch
    the same way: each batch was sent, each was refused, and the run recorded
    the same error once per batch."""
    thirty = [job(str(i)) for i in range(30)]

    for fault in (_ApiFault(401, "invalid x-api-key"),
                  _ApiFault(403, "permission denied"),
                  _ApiFault(400, "Your credit balance is too low")):
        calls = []

        def refused(request, fault=fault):
            calls.append(request)
            raise fault

        report = assess(thirty, "b", "f", send=refused)
        assert len(calls) == 1, f"{fault}: the second batch must not be sent"
        assert len(report.unread) == 30
        assert len(report.errors) == 1 and str(fault) in report.errors[0]

    # Positive control: an overloaded API may answer next time, so every batch
    # is still tried.
    calls = []

    def overloaded(request):
        calls.append(request)
        raise _ApiFault(529, "overloaded")

    report = assess(thirty, "b", "f", send=overloaded)
    assert len(calls) == 2 and len(report.errors) == 2


def test_the_live_transport_reports_why_a_reply_ended():
    from types import SimpleNamespace

    seen = {}

    class Messages:
        def create(self, **request):
            seen.update(request)
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text='{"verdicts": []}')],
                stop_reason="max_tokens", model="claude-haiku-4-5-20251001")

    send = anthropic_transport(client=SimpleNamespace(messages=Messages()))
    reply = send(build_request([job()], "b", "f"))
    assert isinstance(reply, ModelReply)
    assert reply.text == '{"verdicts": []}'
    assert reply.stop_reason == "max_tokens"
    assert reply.model == "claude-haiku-4-5-20251001"
    assert seen["model"] == "claude-haiku-4-5", "the request went through as built"


# -- what each request cost, and whether the prefix cached ------------------
def test_every_model_call_records_what_it_cost():
    """No request's usage was kept, so the user's spend on a run could not be
    read back, and nothing showed whether the cached prefix ever cached."""
    recorded = []

    def send(request):
        return ModelReply(text=_reply_for(request).text, stop_reason="end_turn",
                          model="claude-haiku-4-5", input_tokens=5200,
                          output_tokens=180, cache_read_tokens=4300,
                          cache_write_tokens=0)

    report = assess([job("a")], "b", "f", send=send, on_call=recorded.append)
    assert len(report.calls) == 1 and recorded == report.calls
    call = report.calls[0]
    assert (call.input_tokens, call.output_tokens, call.cache_read_tokens,
            call.cache_write_tokens) == (5200, 180, 4300, 0)
    assert report.cached_prefix_tokens == 4300

    # A prefix that never cached measures as zero, not as unknown.
    uncached = assess([job("a")], "b", "f", send=_reply_for)
    assert uncached.calls and uncached.cached_prefix_tokens == 0


def test_a_call_that_stopped_short_is_still_recorded():
    """It was billed all the same."""
    def cut_off(_request):
        return ModelReply(text="{", stop_reason="max_tokens",
                          input_tokens=900, output_tokens=8000)

    report = assess([job("a")], "b", "f", send=cut_off)
    assert [c.output_tokens for c in report.calls] == [8000]
    assert report.unread, "positive control: the reply itself was not used"


def test_the_live_transport_reads_the_usage_including_the_cache():
    from types import SimpleNamespace

    class Messages:
        def create(self, **request):
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text='{"verdicts": []}')],
                stop_reason="end_turn", model="claude-haiku-4-5",
                usage=SimpleNamespace(input_tokens=812, output_tokens=40,
                                      cache_read_input_tokens=None,
                                      cache_creation_input_tokens=4200))

    reply = anthropic_transport(client=SimpleNamespace(messages=Messages()))(
        build_request([job()], "b", "f"))
    assert (reply.input_tokens, reply.output_tokens, reply.cache_read_tokens,
            reply.cache_write_tokens) == (812, 40, 0, 4200)
