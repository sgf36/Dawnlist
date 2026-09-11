# dawnlist-feed-worker

The feed proxy. Holds the provider keys, meters per licence in D1, caches
across users within each Cloudflare data centre (the Cache API is not global),
and makes the provider a config value rather than a code path.

## Deploy

### A new database

```bash
npx wrangler d1 create dawnlist
# put the returned database_id into wrangler.jsonc
npx wrangler d1 execute dawnlist --remote --file=./schema.sql
npx wrangler secret put THEIRSTACK_API_KEY
npx wrangler deploy
```

`schema.sql` is the whole schema as it stands after every migration, so a
database created from it needs none of them — applying one would stop at
"duplicate column name".

### The existing database — migrations, never schema.sql

Apply each file in `migrations/` that the database has not had, **in filename
order, once each, before deploying the code that needs it**:

```bash
npx wrangler d1 execute dawnlist --remote --file=./migrations/<NNN-name>.sql
```

Two ways to get this wrong, both silent:

- **Running `schema.sql` against it succeeds and changes nothing.** Every table
  already exists, so `CREATE TABLE IF NOT EXISTS` skips it along with every
  column added since. The code then fails at the first query that names one.
- **`wrangler d1 migrations apply` would re-run 001 and 002.** It records what
  it applied in its own table, and 001 and 002 were applied to the live
  database by `d1 execute` on 2026-09-08, so that table has no record of them.

`test/schema.test.mjs` rebuilds the schema the live database was created from,
applies every migration, and fails if the result differs from `schema.sql`.

## Two things that have bitten this pattern before

1. **`wrangler secret put <KEY>` takes the secret's NAME, not its value.**
   Typing the value in the command creates a secret whose *name* is the
   credential and leaves the real one untouched. Type the command literally;
   the value goes at the interactive prompt only. If `wrangler secret list`
   ever shows a name that looks like a credential, it is one — delete it and
   clean the Wrangler logs, which record full command lines.

2. **"Wrangler said success" proves a value was stored, not that it was the
   right one.** Verify behaviour, not the deploy message.

## Caps

Per-licence columns override the Worker defaults (10 saved queries, 3
refreshes/day, 700 postings/day), so one licence can be raised without a
deploy.

**The cap is counted in postings FETCHED, and that is not interchangeable with
postings assessed.** The billable event is the fetch — 1 credit = 1 job
RETURNED — and it happens before the free local screen runs. Capping the
assessed count instead would govern roughly a tenth of variable cost and leave
the rest open: a licence could spend an unbounded amount of credit without ever
tripping it. The spec's "target 30–60 reaching assessment" dial is a
*consequence* of this cap, not the cap itself — measured 2026-09-07, ~513
fetched/day yields ~68–93 reaching assessment.

Three properties the tests in `test/caps.test.mjs` hold in place:

1. **The cap cannot be overshot.** The page size is clamped to the licence's
   remaining headroom before the upstream call, so the request that crosses the
   cap lands exactly on it rather than a full page past it.
2. **Being capped is always reported.** `include_total_results` rides along on
   the page we were fetching anyway — free — so the response can say how many
   postings matched and how many were skipped. A separate `limit=1` sizing call
   would cost a credit ("a count costs one record"); do not add one.
3. **A cache hit spends the receiving licence's allowance.** Metering the
   upstream fetch instead would make the cache an unmetered bypass, and would
   make two users' caps depend on who ran the query first. Spencer still pays
   only once **per data centre**: the Cache API is local to each Cloudflare
   location, so the same search from users served by two locations is fetched,
   and billed, twice. Rows the user already holds (`excludeJobIds`) are removed
   from a cache hit before it is cut and metered, so nobody pays twice for a
   posting they have.

The default of 700/day is an **anti-abuse ceiling, not a product tier**. A
comprehensive UK user measures ~513 fetched/day, so the default sits above
normal use: it stops a runaway query, it does not decide coverage. If the cap
is ever set low enough to shape what the user sees, that is a pricing decision
and belongs in the licence row, not here. Full working:
`Apps/Claude/dawnlist-credit-cap-assessment.md`.

## Paddle notification destination — the events it must send

The webhook acts only on what it is sent. A destination subscribed to
`subscription.created` alone — the state read on 2026-09-11 — issues licences
and does nothing else: cancellations, pauses, refunds and plan changes never
arrive, and a customer who cancelled keeps a working key.

Subscribe the Dawnlist destination
(`https://dawnlist-feed-worker.sgf36.workers.dev/paddle/webhook`) to **all** of
these. Anything else it sends is acknowledged and ignored.

| Event | Needed for | What the Worker does |
|---|---|---|
| `subscription.created` | grant | Issues the licence and emails it — only if a price matches a configured plan |
| `subscription.activated` | grant | Issues if none exists; restores access |
| `transaction.completed` | grant, refund | Issues if none exists (a one-time purchase has no subscription events); records which subscription the payment was for, which is how a refund finds its licence |
| `subscription.updated` | update | A plan change moves the caps; its `status` also carries pauses, cancellations and past-due renewals |
| `subscription.canceled` | cancel | Access ends (`expired`) |
| `subscription.paused` | pause | Access ends (`expired`) |
| `subscription.resumed` | resume | Access restored (`active`) |
| `subscription.past_due` | past_due | Access **continues** while Paddle retries the payment; `past_due` is recorded in `paddle_subscriptions.paddle_status` |
| `subscription.trialing` | trial | Access continues |
| `adjustment.created` | refund, chargeback | An approved **full** refund or any chargeback sets the licence `refunded`; a partial refund changes nothing |
| `adjustment.updated` | refund approval | A refund created awaiting approval takes effect when it is approved |

Why the overlap is deliberate: Paddle does not promise delivery or order. Every
subscription event is applied by its `occurred_at` — an older one arriving late
is recorded as `stale_ignored` and changes nothing — so receiving both
`subscription.canceled` and a `subscription.updated` carrying `canceled` is
harmless, and losing either one is not.

Two statuses no webhook overrides: `suspended` (support's decision) and
`refunded`.

**Proves it:** open the destination and compare its event list with the table.
Then, for each event, send a simulation and read what the Worker did:

```bash
npx wrangler d1 execute dawnlist --remote --command \
  "SELECT event_type, action, received_at FROM webhook_events ORDER BY received_at DESC LIMIT 20"
```

An event type missing from those rows after its simulation was sent is an event
the destination is not subscribed to.

## Deployed

Live at **https://dawnlist-feed-worker.sgf36.workers.dev** (deployed 2026-09-06,
account `spencer@spencerfields.com`, D1 `dawnlist` = `8c272845-1e82-4660-ab71-01bef14c897e`,
region WEUR).

**It is inert.** The `licences` table is empty, so every request is refused before
it can reach a provider. Verified by behaviour rather than by the deploy message:

| Request | Expected | Actual |
|---|---|---|
| `GET /health` | reads D1, lists providers | `{"ok":true,"providers":["theirstack"]}` |
| `POST /v1/search`, no auth | 401 | 401 `no_licence` |
| `POST /v1/search`, bogus licence | 403 | 403 `unknown_licence` |
| `GET /nope` | 404 | 404 `not_found` |

**No secrets are installed yet**, deliberately — a live feed key on a public
endpoint is not something to add before it is needed. To make search work:

```bash
npx wrangler secret put THEIRSTACK_API_KEY
```

Type that literally. The command takes the secret's **name**; the value goes at
the interactive prompt only. Putting the value in the command creates a secret
whose *name* is the credential and leaves the real one unset — that exact
mistake cost four broken deploys on the EasyPost webhook Worker.

Issuing a licence (nothing works until one exists):

```bash
npx wrangler d1 execute dawnlist --remote --command \
  "INSERT INTO licences (licence_key, tier) VALUES ('<key>', 'managed')"
```
