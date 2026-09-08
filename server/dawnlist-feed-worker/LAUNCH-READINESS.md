# Launch readiness — the API path

**Written 2026-09-08.** Assumes the provider's **API** is the route, not their
bulk Datasets product.

> **The ordered owner checklist now lives in `LAUNCH-RUNBOOK.md` at the
> repository root**, because launch is not only a Worker deployment — it is
> also two store submissions, three signed packages and a website obligation,
> and splitting the order of operations across two files is how a step gets
> skipped. This file keeps the *reasoning* behind the Worker's configuration,
> which the runbook deliberately does not repeat.
>
> **Superseded here, corrected there:** the "buy nothing yet" position below,
> and the "break-even around the mid-teens" figure. Launch now needs a small
> credit pack before submission — a store reviewer runs the application, and a
> feed with no credit is a rejection. Break-even at $79 is **18 subscribers on
> Paddle, 20 through a store's own commerce**, measured on 2026-09-08. "Mid-
> teens" was a conversational round-down and should not be quoted.

---

## Why the API, and when that stops being true

The API bills per credit — 1 credit = 1 job **returned** — so cost tracks use.
Datasets is bulk delivery bought whole, so it costs the same whether there is
one subscriber or a hundred. Below the crossover the API is plainly cheaper,
and above it Datasets is.

**The crossover is roughly 65 daily-active users**, at ~1M records a month
against a measured **~15,400 credits per user per month** (a comprehensive
daily delta is ~513 postings).

> **Do not use the 330 that was in `schema.sql` until 2026-09-08.** It came from
> dividing 1M by the build handoff's *assumed* 3,000 credits per user. Measured
> consumption is five times that, so the crossover arrives five times sooner.
> Believing 330 would keep the product on per-credit pricing long past the point
> where bulk delivery is cheaper.

`usage_daily.postings` is instrumented from day one precisely so this is a
measurement rather than an argument. Read it before switching.

---

## What to buy, and when

**The $100/month API tier — 5,000 API credits — bought at submission.** The
runbook gives the ordering; this is why that shape rather than another.

**THERE ARE TWO CREDIT POOLS AND ONLY ONE REACHES THE API.** One-time packs
($109/1,000 up to $999/200,000, rolling over twelve months) buy **company
credits**, spent revealing companies in the provider's own web app. The API,
webhooks and the MCP server spend **API credits**, which are sold **only** as a
monthly subscription. This file said "one-time packs, not a subscription" until
2026-09-08; that advice would have bought a currency the Worker cannot spend.

The earlier answer here was "nothing yet", on two grounds. Both have moved:

1. **The licence question is closed.** Ticket #5008 is CLOSED ON OUR SIDE by
   Spencer's decision of 2026-09-08 — no chase will be sent. The published
   terms answer five of its seven questions favourably; reselling their data
   partially through your own platform is expressly authorised. The remaining
   §4.5-versus-§4.12 tension is an **accepted risk**, not a pending question.
   Do not record it as awaiting a reply.
2. **"No subscribers and nothing submitted" argued against buying EARLY, and
   still does.** A store reviewer runs the application, and a search that errors
   for want of credit is a rejection — so a subscription is needed, timed to
   submission rather than taken out in advance. There is no rollover to lean
   on: that belongs to the pool that cannot be used.

**When the base is steady**, the first tier worth moving to is **$900/month for
200,000 API credits** ($0.0045/credit). At ~15,400 credits per user that block
serves about **13 users**, which is also where per-subscriber contribution turns
positive — no smaller tier does. It does not become comfortable until
$1,200/500,000, where the cost per subscriber roughly halves to $37.

Break-even at $79 is **18 subscribers on Paddle and 20 through a store's own
commerce**, measured 2026-09-08. Not "mid-teens", which was a conversational
round-down, and certainly not the 66 that belongs to the discarded $19.99
model.

---

## Deploy, in order

### 1. Migrate D1 — once

```
npx wrangler d1 execute dawnlist --remote --file migrations/001-plans.sql
```

Adds `plan` and `plan_unmatched`, and backfills existing licences to
`standard`, whose 700/day ceiling is what they already had — so nothing about
their behaviour changes. **Apply once**: SQLite has no
`ADD COLUMN IF NOT EXISTS` and a second run stops at "duplicate column name".

### 2. Create the Paddle products

Two prices, both recurring monthly:

| Plan | Ceiling | What it is for |
|---|---|---|
| Standard | 700 postings/day | A comprehensive search in one region. Measured need is ~513/day, so a single-region user should never see the cap. |
| Global | 2,500 postings/day | Sweeps spanning many regions. |

> **The Global ceiling is the one number here that has not been measured.**
> 2,500 is roughly five regions' worth and is deliberately generous. Run a real
> multi-region sweep, read `usage_daily.postings`, and set it from what comes
> back. It is a D1 column, so correcting it is an `UPDATE`, not a release.

### 3. Set the configuration

```
npx wrangler secret put THEIRSTACK_API_KEY
npx wrangler secret put PADDLE_WEBHOOK_SECRET
```

`wrangler secret put` takes the secret's **NAME**. The value goes at the
interactive prompt only — typing it into the command creates a secret whose
*name* is your credential and leaves the real one unset, and Wrangler's logs
record full command lines.

Price ids are **not secrets** — set them as plain vars so a misconfiguration is
visible:

```
PADDLE_PRICE_STANDARD = pri_...,pri_...     # monthly, annual, per currency
PADDLE_PRICE_GLOBAL   = pri_...
```

Sandbox and production issue **different ids for the same product**. An id that
matches nothing makes the webhook fall back to Standard and set
`plan_unmatched = 1`, so a Global customer silently lands on Standard caps.

**Delete `ANTHROPIC_API_KEY` if it is still set.** Nothing reads it. It
belonged to an inference proxy built and removed on 2026-09-06, because
proxying job descriptions plus the fit brief and factsheet would make Spencer a
processor of every buyer's career record. A credential kept for a capability
that was deliberately removed is pure exposure:

```
npx wrangler secret delete ANTHROPIC_API_KEY
```

### 4. Deploy and verify

```
npx wrangler deploy
curl https://dawnlist-feed-worker.sgf36.workers.dev/health
```

Then verify the part that was broken, because "Wrangler said success" proves a
value was stored, not that it was the right one:

- Replay a `subscription.created` for a **Global** price and confirm the new
  licence row has `plan = 'global'` and `max_postings_per_day = 2500`.
- Replay a `subscription.updated` moving it to Standard and confirm the caps
  come **down**.
- `GET /v1/plan` with that licence and confirm the numbers match D1.
- `SELECT licence_key FROM licences WHERE plan_unmatched = 1;` should be empty.
  Anything there is a customer on the wrong caps.

---

## Still blocked, and by whom

| Item | Owner | State |
|---|---|---|
| Ticket #5008 — the licence answer | — | **CLOSED 2026-09-08.** Spencer's decision: no chase. Proceeding on the published terms |
| Partner Center **package identity** | — | **DONE.** `SFields.DawnlistJobSearch`, confirmed live 2026-09-08 |
| macOS build | — | **DONE.** Built, signed and notarised in CI, 2026-09-08. It never needed a Mac of our own |
| WACK — needs an elevated shell | Spencer | `pwsh -File packaging\run_wack.ps1`, Run as administrator |
| Windows signing — Entra federated credential | Spencer | The three secrets and the variable are set; the credential subject `repo:sgf36/Dawnlist:ref:refs/heads/master` is missing |
| API credit subscription, at submission | Spencer | $100/month, 5,000 API credits |
| Paddle product and price id | Spencer | One price: $79/month |

The package identity is the irreversible one. A displayed name can change after
publish; the identity cannot, and getting it wrong means the app can only be
replaced, never updated.
