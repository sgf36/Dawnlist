# Deploying the Worker — the order, and why it is this order

**STATUS, VERIFIED 2026-09-30: every migration below (001 to 014, including 008,
which the list in section 2 omits) is ALREADY APPLIED to the live `dawnlist`
database.** This was checked against the live schema, not inferred from the
deployment history: each migration's table, column or index was found, and the
`providers` table holds `theirstack` (enabled, priority 10) and `linkedin`
(enabled, priority 20). The Worker deployed on 2026-09-30 (`6d6ae972`) needs
nothing applied first.

Sections 1 and 2 therefore matter only for a **fresh or restored database**.
Do not run them against the live one; `d1 migrations apply` and re-running
`ALTER TABLE ... ADD COLUMN` both fail loudly, but a runbook that reads as
"nothing is applied" invites someone to try.

**Re-verify before trusting this, or any status paragraph, again.** Read-only,
and each answers one migration (`--command`, not `--file`, for the reason below):

```
npx wrangler d1 execute dawnlist --remote --command "SELECT name FROM sqlite_master WHERE type IN ('table','index') ORDER BY name"
npx wrangler d1 execute dawnlist --remote --command "PRAGMA table_info(licences)"
npx wrangler d1 execute dawnlist --remote --command "PRAGMA table_info(apple_transactions)"
npx wrangler d1 execute dawnlist --remote --command "SELECT name, enabled, priority FROM providers ORDER BY priority"
```

Expected: tables `paddle_subscriptions`, `paddle_transactions`, `admin_audit`,
`apple_transactions`, `apple_offer_codes`, `apple_offer_batches`,
`microsoft_transactions`, `providers`; indexes `licences_by_subscription`,
`codes_by_normalised`, `admin_audit_by_actor`; `licences` with `plan`,
`plan_unmatched`, `delivery_status`, `delivery_error_code`;
`apple_transactions` with `offer_identifier`, `offer_type`, `comp`;
`code_attempts` with `client_hash` and no `ip`.

**Deploy from a commit, not from a checkout.** `wrangler deploy` publishes the
tree it runs in, and a stale branch can hold the right files and the wrong
source. Deploy from a clean worktree of the exact commit that is on the
release branch (`git worktree add --detach <path> <sha>`), run the Worker
tests there, `wrangler deploy --dry-run`, then deploy, and remove the
worktree. Then PROVE it with a real search: a fix is not deployed until a
query that used to fail returns the new answer.

**Results are cached six hours per data centre and the cache key is the
query.** A change that alters what the same query returns must bump `v` in
`cacheKeyFor`, or the change is invisible for up to six hours. The first probe
after a deploy can also land on the previous version while it rolls out:
re-run it before believing it.

**The Worker's `linkedin` provider is a fallback that does not work.** It runs
only when TheirStack throws, and it sends `countries=(value:List(...))` and
`sortBy.*`, both of which the LinkedIn API rejects (400), so today it fails and
the request ends in `all_providers_failed`, as it did before the provider
existed. Do not "fix" it by correcting the request alone: with no city, title
or age filter it would answer a London search with UK-wide, keyword-loose
sponsored postings, and the result would be cached for six hours for
everyone. The Python adapter (`app/linkedin/job_library.py`) shows what the
filtering needs.

Everything runs from this directory:

```
cd C:/Users/SpencerFields/dawnlist/server/dawnlist-feed-worker
```

If `npx` fails with `ENOENT ... _cacache ... Invalid response body`, that is a
corrupt npm download cache, not Cloudflare and not authentication. `npm cache
verify`, then re-run.

**Never pass a licence key, or any other credential, with `--command`.**
Wrangler's logs record whole command lines, so anything typed inline lands on
disk. Every statement that names a key is in a file.

**But run the `SELECT`s with `--command`, not `--file`.** `--file` goes through
D1's import endpoint, which reports only "N queries, N rows written" and
DISCARDS the rows a `SELECT` returns. Measured 2026-09-12: a file containing an
`UPDATE` and a verifying `SELECT` printed the summary and nothing else, so the
change was made and could not be confirmed. The pre-checks below therefore go on
the command line — none of them names a key, so none of them is a credential in
a log.

**A failure on the import endpoint is about the credential TYPE, not its
permissions.** `Authentication error [code: 10000]` on `/d1/database/.../import`
was returned to a Super Administrator holding `d1 (write)`: an OAuth login is
refused there. Set a scoped API token in `CLOUDFLARE_API_TOKEN` (Account → D1 →
Edit) and every `--file` below works. Reading the scope list proves nothing.

---

## 1. The pre-checks, before anything is applied (fresh or restored database only)

Two migrations refuse to apply if the data underneath them is not what they
assume, and both fail *without changing anything* — so running them blind is
safe, but finding out first is faster than reading a failure.

Migration 003 builds a unique index on `paddle_subscription_id`. This must
return **no rows**:

```sql
SELECT paddle_subscription_id, COUNT(*) FROM licences
 WHERE paddle_subscription_id IS NOT NULL
 GROUP BY paddle_subscription_id HAVING COUNT(*) > 1;
```

If it returns any, a customer has two licences for one subscription. Find which
key they are actually using (`usage_daily` shows it), expire the others and
clear their `paddle_subscription_id`, then continue.

Migration 007 builds a unique index on the normalised code. Both of these must
return **no rows**:

```sql
SELECT code FROM codes WHERE code GLOB '*[^A-Za-z0-9 -]*';

SELECT UPPER(REPLACE(REPLACE(code, '-', ''), ' ', '')) AS n, COUNT(*)
  FROM codes GROUP BY n HAVING COUNT(*) > 1;
```

## 2. Migrations 003 to 007, and 009 to 014 (fresh or restored database only)

`001-plans.sql` and `002-code-plans.sql` are ALREADY APPLIED — by `d1 execute`
on 2026-09-08. They are listed here so that nobody reading "apply every file in
migrations/" applies them a second time; both add columns, so a second run stops
at "duplicate column name" and changes nothing, but the failure looks like a
broken deploy.

Apply each one once, in order:

```
npx wrangler d1 execute dawnlist --remote --file migrations/003-one-licence-per-subscription.sql
npx wrangler d1 execute dawnlist --remote --file migrations/004-subscription-state.sql
npx wrangler d1 execute dawnlist --remote --file migrations/005-paddle-transactions.sql
npx wrangler d1 execute dawnlist --remote --file migrations/006-licence-delivery.sql
npx wrangler d1 execute dawnlist --remote --file migrations/007-normalised-codes.sql
npx wrangler d1 execute dawnlist --remote --file migrations/009-admin-audit.sql
npx wrangler d1 execute dawnlist --remote --file migrations/010-apple-transactions.sql
npx wrangler d1 execute dawnlist --remote --file migrations/011-apple-offer-codes.sql
npx wrangler d1 execute dawnlist --remote --file migrations/012-apple-comp.sql
npx wrangler d1 execute dawnlist --remote --file migrations/013-microsoft-transactions.sql
npx wrangler d1 execute dawnlist --remote --file migrations/014-linkedin-provider.sql
```

**Do not use `wrangler d1 migrations apply`.** It tracks its own state in a
table this database does not have, and would try to run 001 and 002 again.

006 and 007 add columns, and SQLite has no `ADD COLUMN IF NOT EXISTS`, so a
second run stops at "duplicate column name" and changes nothing. That is a safe
failure, not a broken database.

011 creates two tables and is safe to re-run. It is the ledger behind the
console's Mac offer codes, and it holds no Apple credential: codes are minted
on a machine with the App Store Connect key by `tools/asc_offer_codes.py` and
only the resulting strings are uploaded. The migration says why that split is
not negotiable.

## 3. The secrets, before the deploy

`wrangler secret put` takes the secret's **NAME**. The value goes at the
interactive prompt only — never on the command line, which is logged.

### Already set — confirm, do not re-enter

These are on the live Worker now and the deploy does not change them. Confirm
they are still listed (`npx wrangler secret list`) before deploying, because a
deploy against a Worker missing one of them fails at the first request that
needs it, not at deploy time:

```
THEIRSTACK_API_KEY      the feed credential; the ONLY place it lives
PADDLE_WEBHOOK_SECRET   signing key, separate from EasyPost's
PADDLE_API_KEY          read access, for /admin/selftest and refund lookups
RESEND_API_KEY          send-only; see the selftest note in step 7
```

`PADDLE_API_BASE` is a plain var and only moves the Worker to Paddle's sandbox.
Leave it unset for live.

If `secret list` ever shows a NAME that looks like a credential, it is one:
somebody typed the value where the name goes. Delete it and clean the Wrangler
logs, which record whole command lines.

### Optional, and worth setting

```
npx wrangler secret put CLIENT_HASH_SECRET
```

Unset, failed code attempts are keyed by a SHA-256 with a salt fixed in
`src/codes.js`. That still keeps addresses out of the table, but anybody holding
the source and a copy of the table can hash candidate addresses and match them.
Setting this closes that.

```
npx wrangler secret put LINKEDIN_ACCESS_TOKEN
```

3-legged OAuth member token for the LinkedIn Job Library API (Ad Library
product, App ID 266550517). Expires in ~60 days, cannot be refreshed after
expiry — regenerate at developers.linkedin.com and re-push. The `linkedin`
provider row is inserted disabled (migration 014); enable it in D1 to
activate:

```sql
UPDATE providers SET enabled = 1 WHERE name = 'linkedin';
```

### Required for Mac purchases to work at all — four of them

```
npx wrangler secret put APPLE_SHARED_SECRET
npx wrangler secret put APPLE_IAP_KEY_ID
npx wrangler secret put APPLE_IAP_ISSUER_ID
npx wrangler secret put APPLE_IAP_PRIVATE_KEY
```

`MS_PRODUCT_ID` is a plain var — it is the Store ID printed on the listing
page. The other three are SECRETS, including the two that are only identifiers:
this repository is public, and a tenant id published in it names the directory
to anyone looking for one to phish.

```
npx wrangler secret put MS_TENANT_ID
npx wrangler secret put MS_CLIENT_ID
npx wrangler secret put MS_CLIENT_SECRET
```

`Set-DawnlistAzureSecrets.ps1` in the home folder does all three with one
prompt each.

They verify a Microsoft Store subscription through Microsoft's collections API,
for the `store_iap` build. Paddle declined the Dawnlist domain on 2026-09-11
and an appeal is open, so the Store sells its own subscription ALONGSIDE Paddle
rather than instead of it — both tills stay built and provable. 013 adds the
table. Unlike the App Store Connect key, this credential reads Store
collections and nothing else: it cannot touch the listing, pricing or a build,
which is why it may live here at all. Setup steps are in
`DAWNLIST-AZURE-AD-SETUP.md`.

**Without these, `/v1/microsoft` answers 503 `microsoft_not_configured`** and
every `store_iap` customer runs on grace until it lapses. They paid; they get
nothing.

`APPLE_COMP_OFFERS` is a plain var too, and names which offers may be comped:
a subscription that began with one of those offers may be kept alive past
Apple's free period by an administrator, and one that began any other way never
can. 012 adds the columns it reads. Changing the list is a var change, not a
deploy of new logic.

`APPLE_BUNDLE_ID` and `APPLE_PRODUCT_ID` are already plain vars in
`wrangler.jsonc` and need nothing.

With any of the four unset, `/v1/apple` answers 503 `apple_not_configured`,
which the Mac app reads as *unreachable* rather than *not subscribed* — so a
subscriber runs on grace instead of being locked out. That is the safe failure,
and it is still a failure: the customer paid and has no licence.

## 4. Migration 008, then deploy IMMEDIATELY

This is the one step with a window in it. 008 renames `code_attempts.ip` to
`client_hash`. The Worker deployed today reads and writes `ip`, so **every code
redemption between this migration and the deploy fails**. Run them back to back:

```
npx wrangler d1 execute dawnlist --remote --file migrations/008-hashed-code-attempts.sql
npx wrangler deploy
```

## 5. Re-run 007's backfill after the deploy

Any code minted between step 2 and the deploy was written by the old Worker,
which does not know about `code_normalised` — so those codes have NULL there and
cannot be redeemed. The backfill is idempotent (`WHERE code_normalised IS NULL`),
so re-running it costs nothing and fixes exactly those:

```sql
UPDATE codes
   SET code_normalised = UPPER(REPLACE(REPLACE(code, '-', ''), ' ', ''))
 WHERE code_normalised IS NULL;
```

## 6. Subscribe the Paddle destination to all eleven events

In Paddle, on the destination whose id contains `01kyfxwd` (the runbook in
`EasyPost-Desktop-App/server/paddle-license-webhook-worker/WEBHOOK-RUNBOOK.md`
§9 says why that one and not the convincingly-named dead one):

```
transaction.completed
subscription.created      subscription.activated    subscription.trialing
subscription.updated      subscription.resumed      subscription.paused
subscription.past_due     subscription.canceled
adjustment.created        adjustment.updated
```

An event the destination does not send is not "ignored" — it never arrives, and
the refund handling, the ordering guard and the state table all go quiet with
nothing saying so.

## 7. Verify — and verify the artefact, not the checker

```
GET /admin/selftest        # Resend and Paddle credentials, each isolated
GET /admin/undelivered     # Paddle licences whose email never landed
```

`selftest` reporting `restricted: true` for Resend is **healthy**: a send-only
key cannot list domains, and that is the least privilege this Worker should
hold. Nobody should "fix" it by issuing a full-access key.

Then replay a `transaction.updated` no-op from Paddle and confirm a **200**.
"Wrangler said success" proves a value was stored, not that it was the right
one.

For the Mac path, the proof is a real purchase in the sandbox reaching a
licence: `/v1/apple` returning a key, and one row in `apple_transactions`. A 503
means a secret is missing; a 500 means migration 010 did not apply.
