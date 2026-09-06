# Build status

**Repo:** `C:\Users\SpencerFields\dawnlist`, deliberately **off OneDrive** per handoff Part 9.
**Tests:** 445 app + 33 Worker, all passing — `.venv/Scripts/python -m pytest -q`
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
