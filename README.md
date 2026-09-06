# Dawnlist — Job Search

A desktop app that reads the world's job feeds every morning, judges every posting against a fit
brief it interviewed you to build, hands you a ranked shortlist with the rejects visible and their
reasons stated, tracks every company you pursue, and writes your follow-up emails as drafts you
send yourself.

**It never sends anything.** Every outreach path ends in a draft you open and send from your own
mail app. That is the positioning against the auto-apply category and the safety boundary both.

Build handoff: `../Apps/Claude/job-search-desktop-app-build-handoff.md`
Operating spec (the source system's IP): `../Apps/Claude/job-search-automation-handoff-for-sean.md`
Identity: `../Apps/Claude/brand-dawnlist/README.md`

---

## Scope lock — what v1 does NOT ship

Written here, not just in the handoff, because the handoff says to (Part 1) and because scope creep
back toward integrations is named as the single biggest schedule risk.

- **No sending, ever. No SMTP code in the binary.**
- **No mailbox reading** — no IMAP, no Gmail/Outlook OAuth. Revisit in v2, with the CASA line item
  priced in first.
- **No LinkedIn network code, and no scraping of any site.** The endpoints are undocumented and
  non-deterministic, LinkedIn's User Agreement prohibits automated access, and both stores pull an
  app on a rights-holder complaint. `deep_sweep.py` stays personal tooling and the marketing never
  names LinkedIn.
- **No board integrations** — no ClickUp, Notion or Trello. *The app is the board* (`app/core/tracker.py`).
- **No letters or phone-channel automation.** Email first.
- **No CV rewriting.** The CV *review* is in scope as onboarding output; generating new CVs is not.

## Where the state lives

Per-user SQLite in the platform app-data dir via `platformdirs` — **never a cloud-synced folder.**
A sync conflict copy of a half-written SQLite file is a corrupted database with no error message.
Keep this repo off OneDrive too.

---

## The fourteen invariants

Each is encoded as code, schema or prompt — never as documentation, because prose is what failed.
The parenthetical is where it lives.

| # | Invariant | Where |
|---|---|---|
| 1 | Never send; drafts only. No SMTP in the binary. Permanent. | absence of any SMTP dependency |
| 2 | Every factual claim comes from the factsheet; gaps render as blocking placeholders | `drafts.has_placeholder` |
| 3 | Funnel counts always visible; a count is never shown without what it excludes | `runs` columns, `ScreenReport.counts` |
| 4 | Read every `likely` posting; bands order the work, never authorise skipping | `Run.finish(left_unread=…)` |
| 5 | A failed or empty fetch is loud, never "no new jobs" | `FetchResult.error`, `runs.fetch_failed` |
| 6 | Rejections on stated requirements quote the line; unfetchable = "not checked" | `assessments.disqualifying_quote`, `.requirement_checked` |
| 7 | Word boundaries, never substrings; title/company scoping distinct from description | `rules.word_boundary`, screen tiers 3 vs 4 |
| 8 | Kill families require SAVES + two anchored precedents; proposed, never auto-adopted | `KillFamily.__post_init__`, `.adopted` |
| 9 | Dedup by provider job id; near-duplicates flagged, never silently dropped | `dedup.dedup`, `jobs` UNIQUE |
| 10 | One draft per recipient per thread; revise in place, never stack | `idx_one_draft_per_thread` |
| 11 | Cadence from evidenced touches; Tue–Thu only; a bounce pivots, never advances | `touches`, `tracker.bounce_correction` |
| 12 | Incomplete runs are reported incomplete, with counts left unread | `runs.status`, `runs.left_unread` |
| 13 | Every output registered before the run ends | `run_outputs.run_id` FK (a constraint, not a procedure) |
| 14 | Metadata never outranks the description; substance over title | `Job.raw_criteria` is display-only |

Two of these are worth reading the code for, because the obvious implementation is wrong:

**Invariant 7 is not sufficient on its own.** Word boundaries stop `venue` firing inside *revenue*
and `spa` inside *space* — the failure that marked 474 of 550 postings `likely`. They do **not**
stop `\boffice manager\b` matching inside "Assistant Front Office Manager", which is a real,
genuinely in-scope title that a real user pursued. No matcher tweak fixes that safely. The rule is
therefore enforced at **admission**: `rules.assert_no_conflicts()` tests every proposed term and
every proposed kill family against the user's actual pursue history before it can enter the table.

**Invariant 13 is a foreign key.** A run produced a complete 101-row assessment including a strong
match, renamed its output to avoid a clash, never registered the new name, and nothing would ever
have read it. `run_outputs.run_id` is `NOT NULL REFERENCES runs(id)`, so an unregistered output is
not something to remember — it is impossible.

---

## The board

`app/core/tracker.py`. Stage values are ClickUp's 0-based orderindex so an export from the existing
board maps across without a translation table.

| # | Stage | Status mirror | Cadence |
|---|---|---|---|
| 0 | Identified | `open` | live |
| 1 | Contacted | `waiting` | live |
| 2 | In Dialogue | `waiting` | live |
| 3 | Phone Interview | `phone interview` | live |
| 4 | In-Person Interview | `in person interview` | live |
| 5 | Offer | `received offer` | live |
| 6 | Won | `offer accepted` | terminal |
| 7 | Lost | `no offer` | terminal |
| 8 | On Hold | `on hold` | **paused, not dead** — no cadence, still reply-checked |

**Stage is the truth; status is a derived mirror of it.** When they disagree, correct the mirror.
The rules that are easy to get wrong, all ported from production defects:

- **An opportunity is a record that carries a Stage** — not "a record with no parent". The tree is
  deeper than two levels; a has-no-parent test silently skipped an entire opportunity with eight
  live children that hung two levels down.
- **A bounce outranks Stage.** A hard bounce means *never contacted*, not unanswered, so the Stage
  goes back to Identified and the status stays `open`. This is the one case where the fix is the
  Stage, and bounced records are held out of the parity batch entirely.
- **An outbound advances Identified → Contacted and nothing further.** In Dialogue and beyond
  require an inbound reply; inferring them from a send corrupts the pipeline read.
- **`no offer` is forbidden on a child of a live opportunity.** It misstates the pipeline and risks
  tripping the determination cascade. Once the parent is Lost the whole tree is marked uniformly,
  which is why the parent's stage is a parameter.
- **Contacted and In Dialogue deliberately share `waiting`.** Nothing is lost: Stage holds the
  distinction. The status answers only "does this need me now, or am I waiting on them?"
- **Mutual POC records are exempt from the ladder.** Their lifecycle is *intro asked → delivered or
  declined*; forcing one back to `waiting` resurrects a discharged thread.
- **Retiring a task is not closing the opportunity**, and a retirement without evidence is refused —
  the status removes the row from view, the evidence is what a later audit reads.
- **Sort the board by Stage, never by status.** The status vocabulary's own order is scrambled
  relative to the pipeline.

---

## Layout

```
app/core/rules.py      the ONE rule table, shared by screen and scorer (spec 5.5)
app/core/screen.py     the four-tier deterministic screen — zero tokens
app/core/dedup.py      exact drops, near-duplicate flags, stranded-file recovery
app/core/tracker.py    the board: stages, status mirror, action tasks
app/core/db.py         SQLite schema; the constraints that replace procedures
app/feed/models.py     the normalised Job — the only shape the app ever sees
app/feed/base.py       provider adapter contract + rate limiting
app/feed/theirstack.py the P0-validated adapter, with delta pulls
tests/                 the golden set: every case cites the failure it encodes
```

## Running the tests

```bash
.venv/Scripts/python -m pytest -q
```

The suite is the golden set. Every kill is anchored to a real rejection and every save to a real
pursue, so a regression in the screen shows up as a named historical failure returning rather than
as an abstract assertion.

---

## Languages

Dawnlist ships the **same 50 languages as Easy-Post Desktop and Wren** — the list and the RTL set
(`ar`, `ur`, `fa`, `he`) are ported verbatim from `EasyPost-Desktop-App/app/i18n.py`. Keeping the
three apps on one list means a locale added to one is addable to all, and the store listings stay
comparable. **If the list changes there, change it here too.**

`app/i18n.py` is the same JSON-catalogue pattern: `tr(key)` resolves against the active locale, then
English, then returns the key itself — so a missing translation is visibly obvious rather than
silently blank, and a broken catalogue file can never crash the UI.

RTL locales mirror the **whole window**, not just the strings (`docs/review-window-ar.png` is the
rendered proof).

**Catalogue status: `en` and `ar` are written; the other 48 are not.** They fall back to English
cleanly in the meantime. `tools/translate_catalog.py` generates them — it is deliberately **not**
run automatically because it calls the API and spends money:

```bash
python tools/translate_catalog.py --dry-run
```

It never overwrites a hand-corrected catalogue without `--force`, never invents keys, and **verifies
placeholder parity before writing** — a translation that drops `{count}` renders as a runtime error
in front of the user, in a language the developer cannot read, so a catalogue that fails the check
is not written at all. A missing file degrades to English; a corrupt one does not.

## Tone of voice

Outreach drafts are written in the user's chosen language **and in their own habitual register**,
measured from messages they wrote themselves.

**This does not read anyone's mailbox.** v1 ships no IMAP, no OAuth and no app passwords, and that
is not negotiable — it is what keeps the product out of Google's restricted-scope rules and their
annual paid CASA assessment. So the samples arrive exactly the way job-alert emails already do: the
user **drags their own sent mail onto the app** (`.eml` files, or an `.mbox` export — every
mainstream client can produce both). Zero credentials, every provider, and it is the user handling
their own mail.

`app/outreach/voice.py` measures **style only**: greeting and sign-off habits, sentence length,
contraction rate, hedging, exclamation frequency, paragraph shape. Quoted reply text and signature
blocks are stripped first, so the profile reflects what the user actually composed.

**It never derives content.** No employers, no figures, no claims. A tone profile that carried
biography would be a route around the one rule protecting the user from inventing their own career,
so the extractor works on shape and frequency, the prompt block says `STYLE ONLY` in as many words,
and a test asserts that content planted in the sample messages does not appear in the profile.
Every factual claim still comes from the factsheet, and gaps are still blocking `[[placeholders]]`.

Below five sample messages the profile reports itself unusable and drafting falls back to a neutral
professional register — imitating noise is worse than not imitating.
