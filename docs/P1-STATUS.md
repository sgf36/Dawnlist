# P1 — status

**Started 2026-09-06**, after the P0 gate passed. Repo is at `C:\Users\SpencerFields\dawnlist`,
deliberately **off OneDrive** per handoff Part 9.

## Done

The engine core — the parts handoff Part 11 calls the product's actual IP, ported as code, schema
and constraints rather than documentation.

| Module | What it holds |
|---|---|
| `app/core/rules.py` | the one shared rule table (spec 5.5), word-boundary matcher, `KillFamily` with mandatory SAVES + two anchored precedents, and the admission-time conflict guard |
| `app/core/screen.py` | the four-tier screen with tier order enforced, `ScreenReport` retaining screened-out rows and reasons, yield-rate diagnostic |
| `app/core/dedup.py` | exact drops by `(provider, provider_job_id)`, near-duplicate flagging, stranded-file recovery |
| `app/core/tracker.py` | the board — 9-stage ladder, status mirror, action tasks, parity/bounce audit |
| `app/core/cadence.py` | ladder from evidenced touches, Tue–Thu rule, OOO override, bounce pivot |
| `app/core/db.py` | full SQLite schema; invariant 13 as a foreign key |
| `app/feed/` | normalised `Job`, provider contract, P0-validated TheirStack adapter with delta pulls |
| `server/dawnlist-feed-worker/` | search proxy, D1 metering, cross-user cache, provider failover as data |

**89 tests, all passing.** `.venv/Scripts/python -m pytest -q`

## Two findings from the port

**1. Word boundaries do not satisfy invariant 7 on their own.** They stop `venue` firing inside
*revenue* and `spa` inside *space* — the 474-of-550 failure. They do **not** stop
`\boffice manager\b` matching inside "Assistant Front Office Manager", because that is a
well-formed word sequence inside a longer, genuinely in-scope title. The golden set reproduced the
original defect against the new code. No matcher tweak fixes it safely — shortening the match
breaks real kills. The spec's rule is an **admission** guard, so `rules.assert_no_conflicts()` now
tests every proposed term and every proposed kill family against the user's pursue history before
it can enter the table. `test_word_boundaries_alone_would_not_have_caught_it` exists specifically
to stop someone "simplifying" the guard away later.

**2. A bounced send is not an evidenced touch.** Counting five business days from a bounce
schedules a chase against contact that never happened — the same class of error as mirroring
`waiting` onto a bounced opportunity. `Touch.is_evidenced_outbound` excludes bounces, and the
bounce path returns "due now, pivot channel, ladder unchanged".

## Next, in order

1. **Assessment via the Batch API** (`app/intelligence/`). Structured outputs for the verdict line;
   the disqualifying quote enforced *in the schema* so a reject on a stated requirement cannot be
   recorded without the verbatim line. Prompt-cache the fit brief + factsheet + rules prefix; keep
   volatile content after the cache breakpoint. Resumable: verdicts append per batch so a crashed
   run resumes by skipping judged ids.
2. **The run pipeline** wiring feed → dedup → gates → screen → assessment through `db.run()`, so
   the funnel counts land in `runs` and every output is registered.
3. **Minimal review UI** (PySide6) — the ranked list with Pursue / Reject / Later, the funnel
   counts always on screen, and the `unlikely` pile browsable.
4. **Worker deploy** — `d1 create`, schema, secrets, then verify by behaviour rather than by the
   deploy message.

`.eml` drafting, the onboarding interview and the calibration gate are P2/P3 and are not started.

## Blocked, and it is not an engineering blocker

The **TheirStack licensing/product-fit answer** (`theirstack-licensing-enquiry.md`) is still
outstanding. It decides whether this runs on the API or on a self-hosted dataset index, which is a
genuine infrastructure step change. **Do not purchase any tier before that reply.** The provider
abstraction means a dataset becomes another adapter behind the same normalised `Job`, so nothing
built so far is wasted either way.

## Environment note

PySide6 is not installed for Python 3.14 in this environment and the engine core does not need it —
everything above is stdlib-only and testable now. Install `requirements-dev.txt` into `.venv` before
starting the UI, and check PySide6 has a 3.14 wheel; if not, pin the venv to 3.12/3.13 as EasyPost
does.
