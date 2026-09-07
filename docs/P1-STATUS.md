# Build status

**Repo:** `C:\Users\SpencerFields\dawnlist`, deliberately **off OneDrive** per handoff Part 9.
**Tests:** 533 app + 33 Worker, all passing — `.venv/Scripts/python -m pytest -q`
**Last updated:** 2026-09-07 — P1 engine complete; a frozen build runs

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
| `app/onboarding/` | CV corpus, the interview that turns it into a factsheet and a brief, the calibration gate |
| `app/main.py` | entry point: `--run-once`, `--board`, `--audit`; headless and windowed share every path |
| `app/onboarding/extract.py` | CV corpus off disk; a scanned PDF is diagnosed by name, never treated as empty |
| `app/ui/onboarding.py` | the four-step wizard: ingest → key → interview → calibration |
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

## Done since

- **The interview step is wired.** Onboarding previously collected CVs and dropped them:
  `build_factsheet_request` had no caller, so a user finished the wizard with an empty brief and
  no factsheet, and every later assessment was scored against nothing. The wizard is now
  ingest → key → interview → calibration, the two documents are editable side by side, and what
  is saved is what the user **corrected** — the model has seen only the CVs, so it cannot know
  which of two conflicting titles is the real one. `docs/interview-step.png` shows it.
- **An end-to-end smoke test** — `tests/test_smoke_end_to_end.py` walks one database from the
  saved documents to a written `.eml` with only the two network boundaries stubbed. The pieces
  were each tested; the seams between them were not.
- **Five capabilities that had no way in.** `tools/audit_wiring.py` asks a question no test
  asks — is there any route from a person to this code — by reporting every definition that
  NOTHING in `app/` names, its own module included, and deliberately not counting tests as
  callers. What it found:

  | Was | Now |
  |---|---|
  | the review window opened with a literal empty list, so the morning after a run showed nothing | `rows_from_db` rebuilds the rows from what `persist` wrote, minus anything already decided |
  | outreach had no entry point — the app could not produce its own output | `--draft`, verified through the CLI against a real database |
  | Pursue wrote a `decisions` row and stopped; nothing reached the board, so nothing came due | Pursue opens the opportunity, one live record per employer |
  | Settings had no menu and no flag, so a key could never be changed | a menu on the review window, plus `--settings` reachable without a working key |
  | a stored key could not be removed | a Remove button, shown only when there is one |

  None was a logic fault, so no unit test could have caught any of them. `tests/test_entry_points.py`
  now covers the routes, and the smoke test no longer opens the opportunity on the app's behalf —
  which is how it passed over a severed chain in the first place.

- **Five defects a user would have hit every day.** The same audit run one layer down — which
  tables does the app READ but never WRITE — found that a table nothing writes is the same
  defect as a function nothing calls.

  | Was | Now |
  |---|---|
  | `rule_terms` had no writer, and tiers 2-4 are an allowlist, so an empty table rejected everything: **0 of 8 postings survived** and the shortlist was empty every morning with no error | an unconfigured table has no opinion and says so; the funnel can no longer read "swept N, screened out N, assessed 0" and look like a quiet market |
  | "every entry is earned" had no way to earn one | tier 2 derived from pursue decisions; `save_rule_term` for the rest, finally calling the `assert_no_conflicts` guard written for it |
  | nothing called `record_outbound`, so the cadence sat at rung zero — four weeks simulated, the same first-contact letter redrafted every Tuesday | a board action, attributing the touch to the contact the live draft was addressed to |
  | the rung reached `prepare_drafts` and was dropped, so every follow-up was drafted as a cold approach | the request states which message this is and when the last went out; verified live |
  | invariant 2 caught only `[[double]]` brackets, so a draft ending "Best regards, [Your name]" was send-ready | both forms block; numerics and `[sic]` excluded |

  The last two were findable only by reading real model output — no test written against the
  prompt's own conventions would have caught either.

- **The rule editor.** Terms could be stored and read but only from code, so three of the four
  screening tiers were unreachable and the screen could never be tuned. A refused term now names
  the role it would have cost — *"operations would have killed Head of Operations at Round Hill
  Capital"* — because that finding is what the admission guard exists to produce and a red border
  throws it away. Tier 2 is shown but not editable: employers are earned by pursuing them.
  `docs/screening-rules.png`.

- **Near-duplicate flags are written down.** `dedup` always found them and nothing stored them, so
  `near_duplicates` was the one table the app never touched at all. Both postings still appear —
  flagged, never merged, because merging two real vacancies loses one.

- **A button that said "Applied" in five languages.** "I sent this" gave a translator no
  referent, so German resolved it to "Beworben" (applied), Italian to "Candidatura inviata", and
  French, Portuguese and Romanian likewise — on a follow-up, a claim the user never made, in a
  language they may not be able to check. The English is now "I sent the message", and
  `tools/translation-notes.json` carries context for keys whose English cannot hold it alone.
  Two tests keep it honest: one fails if a note names a key that no longer exists, the other if
  the file ever reaches the build. A spot-check of the other context-dependent keys across eight
  Latin-script locales came back clean — "Also posted as" correctly took the publish sense, not
  the postal one.

- **The frozen build and the MSIX are rebuilt** against current source and verified by
  behaviour, not by the build log: `--doctor` reports 50 of 50 catalogues and the store variant,
  `--draft` and `--settings` are both present, exit code 2 carries the right message, and the
  corrected German and Italian strings are readable inside `dist/Dawnlist.msix` itself.

- **Run against Spencer's own five CVs**, which is what found the next four faults. Two broke it
  outright: `max_tokens` budgets thinking PLUS output, so at 8,000 the model spent the whole
  budget reasoning and returned no text (the real factsheet needs 15,617), and the SDK then
  refused the raised budget on a non-streaming request. A synthetic two-CV corpus fitted
  comfortably, which is why every test passed.

  The output is good: it independently derived five of the hand-built factsheet's most important
  rules — £2.3M *identified* not delivered, $220M+ *supported* not closed, $7B+ Watermark
  *analysed* not managed, no line management of the 12+/13-person groups, and title variants
  unconfirmed. It missed the professional-designation rule, so the rules now require it; re-run,
  it produces the MAI/RICS/CFA guard plus one nobody had written down — that appraisals
  quality-assured for external MAI appraisers may not be claimed as authored.

- **The brief asked six questions it had no way to answer.** `stated_aim` was a parameter nothing
  filled, so the brief was inferred from CVs alone — and it excluded general management and
  front-of-house, one of the three legs actually being searched. One sentence of aim: unknowns 6
  to 2, question marks 11 to 5, and that leg became a named target. The interview step now opens
  with the aim box above a Draft button and no longer drafts on arrival.

- **Store screenshots**, six of them, in `store/screenshots/` with captions. Opening the first
  caught a real fault: the Title column was fixed at 215px, so "Head of Asset Management, UK &
  Ireland" elided — the most important field in the window. The listing had also promised a
  screenshot of a draft; there is no such screen, and the table now says so rather than mocking
  one up.

- **Kill families are proposed and armed.** `kill_families` was the last table the app read and
  never wrote. It is not an editor: spec 5.3 anchors a family to two real rejections and rule 8
  makes arming it the user's call, so the app watches rejections, notices a shape and offers it
  with the precedents attached. The proposer withholds rather than bend a rule — no SAVES, no
  proposal; and a family that would not kill its own precedents is inert and is never offered.
  `docs/kill-families.png`.

- **The housekeeping now runs.** `prune_seen` bounds `seen_jobs`, which had grown for the life of
  an install. `register_output` and `orphan_outputs` are the two halves of invariant 13 and
  neither had ever run, so no draft was registered and the orphan check had nothing to check
  against — the outreach run is a run now, and `--doctor` reports unregistered files without
  deleting them. That change had a trap: `latest_run_id` took MAX(id), so an outreach run (which
  sweeps nothing) would have emptied the review window.

- **All 50 locale catalogues**, at 100% coverage, verified for placeholder parity. `--fill` tops
  up catalogues that fall behind the source, and now falls back to the keyring when
  `ANTHROPIC_API_KEY` is absent from the shell.
- **The Worker is deployed** — https://dawnlist-feed-worker.sgf36.workers.dev with D1 `dawnlist`.
  Inert: no licences exist and no secrets are installed.
- **MSIX builds** — `dist/Dawnlist.msix`, 73MB, validated by opening the package.
- **A new user is routed to onboarding** rather than to an empty shortlist.

## Commercial model — CHANGED 2026-09-06

Paid app, one price, bring-your-own-key. This supersedes handoff Part 7 (free download + licence
+ managed subscription + store add-on). See the README for the full statement. The practical
consequences already in code: `entitlement.py` treats store builds as entitled by possession,
`api_key.py` requires the user's own key with no fallback to ambient credentials, and the store
listing discloses the key requirement in the description rather than the small print.

**BYO is the ONLY route** (decided 2026-09-06, on data risk rather than cost). No career data
crosses Dawnlist's own infrastructure; the Worker handles job searches and licence metering and
nothing else. A managed inference proxy was built, then removed — the endpoint 404s and a test
asserts no path back to one survives in the app.

Two consequences worth holding:
- **The market narrows to people willing to hold an API key.** That is a real cost and it is
  accepted deliberately. The listing says so before the buy button.
- `dawnlist-anthropic-managed` **is now unused.** Revoke it — an unused key with no purpose is
  only a liability.

Still open: the **direct-download price**, and whether a Paddle subscription exists at all now
there is no managed tier to fund.

## What only Spencer can do

Nothing below is blocked on engineering. Each needs an account, a credential, a payment, or a
machine I do not have.

**Before a Store submission**
1. **Reserve the app name in Partner Center.** `packaging/msix/AppxManifest.xml` carries
   `SFields.Dawnlist` as the expected identity; it is **not reserved**. A mismatch fails
   *ingestion*, not certification, so it surfaces late and confusingly.
2. **Sign the MSIX.** A self-signed certificate is enough — the Store re-signs on publish.
   Azure Trusted Signing is already live for the sibling app.
3. **macOS builds** — notarised `.dmg` and the MAS variant. These need a Mac; the spec already
   carries the `BUNDLE` block and the StoreKit hidden imports.
4. **Store listing copy, screenshots and the privacy policy.** Write the labels honestly: CVs and
   career data are processed via the Anthropic API and the feed proxy. Make the Worker keep
   behaving that way — it logs counts, never content.
5. **Trademark clearance on "Dawnlist"** across UKIPO, EUIPO, USPTO and both stores. I attempted
   this and **could not get a trustworthy answer**: TMview returned "No rows found" for a control
   term with known live registrations, so any clean result from it would be a false negative.

**Commercial**
6. **The TheirStack licensing reply**, then the tier decision. Do not purchase before it lands —
   it decides API versus a self-hosted dataset index.
7. **A feed credential**, once the tier is settled: `wrangler secret put THEIRSTACK_API_KEY` for
   the Worker, and a key in Credential Manager under `dawnlist-feed` / `api-key` for the desktop
   app.
8. **Paddle products and prices**, and the licence-issuance path into the Worker's `licences`
   table.
9. **Set the direct-download price.** Store builds are entitled by possession, so this is the
   only price still undecided.

*Two earlier items are gone with the managed tier: a separate Anthropic workspace for a proxy
key, and the two-tier pricing hypothesis. There is no proxy and there is one price.*

**Product**
11. **Your own CVs.** The factsheet and fit brief are built from them, and nothing can stand in
    for that. It is also the fastest way to find out whether the onboarding flow actually works.
12. **Sean's pilot**, if it is doubling as the onboarding test.

## The audits are clean

**Every table in the schema is read and written.** The function audit's remaining entries are
library surface, Qt event overrides and diagnostics — nothing a user could reach for and fail to
find. Closed since the last update:

- **Job-alert digests have a way in**, which the store listing already promised. Dropped files go
  through the same path as a fetched sweep, so dedup, the reject gate, the screen and assessment
  all apply unchanged; `--add-alerts` covers the headless case.
- **Board tasks can be created from the board**, so the "Open task" column is no longer filled
  only by something else writing the row.
- `counts_for_ui` deleted — a one-line wrapper with no caller and no test is rot, not surface.
  `rows_from_outcome` kept and pinned to `rows_from_db` by a test, because two builders for the
  same rows drift invisibly.

## Blocked, deliberately

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
