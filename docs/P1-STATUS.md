# Build status

**Repo:** `C:\Users\SpencerFields\dawnlist`, deliberately **off OneDrive** per handoff Part 9.
**Tests:** 349, all passing — `.venv/Scripts/python -m pytest -q`
**Last updated:** 2026-09-06 — P1 engine complete; a frozen build runs

## Done

| Module | What it holds |
|---|---|
| `app/core/rules.py` | the one shared rule table (spec 5.5), word-boundary matcher, `KillFamily` with mandatory SAVES + two anchored precedents, admission-time conflict guard, containment detection |
| `app/core/screen.py` | four-tier screen, tier order enforced, screened-out rows kept with reasons, contained-match bucket, yield-rate diagnostic |
| `app/core/dedup.py` | exact drops by `(provider, provider_job_id)`, near-duplicate flagging, stranded-file recovery |
| `app/core/tracker.py` | the board — 9-stage ladder, status mirror, action tasks, parity/bounce audit |
| `app/core/cadence.py` | ladder from evidenced touches, Tue–Thu rule, OOO override, bounce pivot |
| `app/core/db.py` | full SQLite schema; invariant 13 as a foreign key; partial unique indexes for one-live-opportunity, one-open-task, one-live-draft |
| `app/core/pipeline.py` | the morning run: feed → dedup → gates → screen → assessment, every narrowing counted onto the run as it happens |
| `app/feed/` | normalised `Job`, provider contract, P0-validated TheirStack adapter with mandatory delta pulls |
| `app/intelligence/` | cached prompt prefix, verdict schema, batching, resumability, the three assessment guards |
| `app/outreach/drafts.py` | `.eml` output, placeholder blocking, revise-in-place, the plain-ask check |
| `app/ui/review.py` | review window: always-visible funnel bar, browsable rejects, "Needs review" containment tab, fully localised + RTL |
| `app/i18n.py` | the same 50 locales as Easy-Post Desktop and Wren, ported verbatim |
| `app/outreach/voice.py` | tone of voice measured from the user's own sent mail — style only, never content |
| `app/outreach/compose.py` | language + voice + factsheet assembled into the drafting request |
| `app/ui/adapter.py` | run → rows, and the user's click → `decisions`; rejections compound into the next run's gate |
| `app/core/board_repo.py` | board load/save; stage and mirror move in one statement |
| `app/ui/board.py` | the board: stage-grouped, audit on screen, two separate repairs |
| `app/outreach/run.py` | what is due, who to write to, and the draft — blocked items named |
| `app/onboarding/` | CV corpus, factsheet/brief drafting rules, the calibration gate |
| `app/main.py` | entry point: `--run-once`, `--board`, `--audit`; headless and windowed share every path |
| `app/onboarding/extract.py` | CV corpus off disk; a scanned PDF is diagnosed by name, never treated as empty |
| `app/ui/onboarding.py` | ingest with drag-drop, and the calibration gate |
| `app/feed/alert_email.py` | job-alert digests dragged in; tracking stripped so one posting is one row |
| `server/dawnlist-feed-worker/` | search proxy, D1 metering, cross-user cache, provider failover as data |

Both P1 findings are resolved structurally — see below. Rendered proof of the UI is in
`docs/review-window.png` and `docs/review-window-contained.png`; `tools/render_ui.py` regenerates
them.

## The two findings, and how they were closed

**1. Containment.** Word boundaries stop `venue`/*revenue* and `spa`/*space*, but not
`\boffice manager\b` matching inside "Assistant Front Office Manager". Two layers now cover it:

- `rules.assert_no_conflicts()` — an **admission** guard testing every proposed term and every
  proposed kill family against the user's real pursue history before it can enter the table;
- `rules.is_contained_match()` — a structural check needing **no history**, so it works on day one.
  The discriminator is linguistic: *"X of Y"* means the role **is** Y (so an `operations` kill on
  "Head of Operations" is correct and is **not** flagged), while *"A B Y"* means Y modified (so
  "Assistant Front Office Manager" **is** flagged).

The kill still happens — the screen stays cheap and predictable — but the row lands in the review
window's **Needs review** tab with its explanation and a Pursue button. The failure that was silent
is now an actionable row.

**2. Bounces.** Beyond `Touch.is_evidenced_outbound` excluding bounces from the cadence:

- `contacts` carries `CHECK (email_bounced = 0 OR email IS NULL)`, so a bounced contact **cannot
  retain an address**. "Cleared" is enforced, not remembered;
- `db.record_bounce()` clears and flags in one statement;
- `tests/test_bounce_integration.py` pins the tracker and the cadence to the same meaning, so the
  board cannot read "waiting on them" while the engine chases a dead mailbox.

## Next

Everything buildable without your credentials or spend is done. What remains needs a decision
from you:

1. **Generate the 48 locale catalogues** — `tools/translate_catalog.py`. One API run; it spends
   money, so it is not automatic. `en` and `ar` are written and the rest fall back to English
   cleanly meanwhile.
2. **Worker deploy** — needs Cloudflare credentials. `server/dawnlist-feed-worker/README.md` has
   the sequence; verify by behaviour, never by the deploy message.
3. **MSIX, notarisation, MAS** — the PyInstaller build works and produces a running 124MB onedir.
   Store packaging and signing are outward-facing and need certificates. Reuse EasyPost's
   `build_msix.py`, `sign_msix.ps1`, `run_wack.ps1` and `CI-MAS-SETUP.md`.
4. **TheirStack tier** — blocked on the licensing reply. Do not purchase before it lands.
5. **Trademark clearance on "Dawnlist"** — free first-pass across UKIPO, EUIPO, USPTO and both
   stores, re-run immediately before submission. September's search found nothing, but registers
   move and a clean result has a shelf life.

Smaller things worth doing when convenient: wiring the onboarding pages into a wizard flow in
`main.py` (the pages exist and are tested; nothing routes to them yet), and a drop target for
alert emails on the review window (the parser is done).

## Blocked, deliberately

- **Worker deploy.** Needs Cloudflare credentials and is outward-facing infrastructure; not run.
  `wrangler.jsonc` carries a placeholder `database_id`, and `server/dawnlist-feed-worker/README.md`
  has the sequence. Verify by behaviour, never by the deploy message.
- **TheirStack tier purchase.** The licensing/product-fit answer
  (`theirstack-licensing-enquiry.md`) decides API vs self-hosted dataset index. **Do not purchase
  any tier before that reply.** The provider abstraction means a dataset becomes another adapter
  behind the same normalised `Job`, so nothing built so far is wasted either way.

## Environment note (corrects an earlier claim)

**PySide6 does have a Python 3.14 wheel** — 6.11.1 was already running in the EasyPost venv; it was
simply absent from the system interpreter. 6.11.2 is installed in this repo's `.venv`. No version
pin is needed.

Two rendering traps worth keeping: `QT_QPA_PLATFORM=offscreen` renders every glyph as tofu (a
font-config artefact, not an app fault — render natively for screenshots), and a UI smoke test
proves the window *built*, never that it is *readable*. Rendering and looking at it caught a
truncated "Why" column and a dangling em-dash that no test would have.
