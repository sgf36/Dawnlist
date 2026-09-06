# Build status

**Repo:** `C:\Users\SpencerFields\dawnlist`, deliberately **off OneDrive** per handoff Part 9.
**Tests:** 190, all passing — `.venv/Scripts/python -m pytest -q`
**Last updated:** 2026-09-06

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

0. **Generate the 48 outstanding locale catalogues** — `tools/translate_catalog.py`. One API run,
   needs your go-ahead because it spends money. `en` and `ar` are written; the rest fall back to
   English cleanly until then.
1. **Onboarding (P2)** — CV ingest, the Sessions 1–3 interview, factsheet/brief builders, and the
   **calibration gate**. Not started. The gate is the transfer-of-judgement step that made the
   original system work: ~10 live postings, the user corrects the verdicts, and each correction
   rewrites the brief. Do not let a user into daily runs without it.
2. **Wire the UI to the pipeline** — the review window currently takes `ReviewRow`s directly;
   it needs the `RunOutcome` → rows adapter and decision persistence into `decisions`.
3. **Tracker UI** — the board exists in `tracker.py` and the schema; it has no screen yet.
4. **Cadence → drafts** — `next_step()` and the draft writer exist but are not joined up.
5. **Packaging (P4)** — PyInstaller onedir, MSIX, notarised macOS, MAS. Reuse the EasyPost specs.

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
