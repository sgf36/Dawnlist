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

**A $199 one-time pack — 5,000 credits — and nothing else.** The runbook gives
the ordering; this is why that shape rather than another.

The earlier answer here was "nothing yet", on two grounds. One has gone and one
has changed:

1. **The licence question is no longer a hard blocker.** The provider's
   published terms answer five of the seven questions ticket #5008 asked, and
   answer them favourably — reselling their data partially through your own
   platform is expressly authorised. What is left is one clause tension, and it
   is confirmation of a favourable reading rather than discovery of an unknown
   one. Chase no earlier than Tue 2026-09-15, and never a Monday or a Friday.
2. **"No subscribers and nothing submitted" argued against a SUBSCRIPTION, and
   still does — but it does not argue against buying anything at all.** A store
   reviewer runs the application. A reviewer whose search returns an error
   because the feed has no credit is a rejection, so *something* has to be
   bought before submission. A one-time pack is the instrument that fits: its
   credits roll over twelve months and survive cancellation, so an idle month
   in a review queue costs nothing.

**When the base is steady**, the first tier worth moving to is **$900/month for
200,000 credits** ($0.0045/credit). At ~15,400 credits per user that block
serves about **13 users**, which is also roughly where per-subscriber
contribution turns positive — no smaller tier does, because $0.0050/credit
against $74.55 net is already the whole margin.

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

| Item | Owner |
|---|---|
| Ticket #5008 — the licence answer | TheirStack |
| Partner Center **package identity** confirmed against the manifest | Spencer |
| WACK — needs an elevated shell | Spencer |
| macOS build — cannot be produced from Windows | Spencer, on a Mac |
| Paddle products and price ids | Spencer |

The package identity is the irreversible one. A displayed name can change after
publish; the identity cannot, and getting it wrong means the app can only be
replaced, never updated.
