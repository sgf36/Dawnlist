# Launch runbook — everything only Spencer can do

**Written 2026-09-08.** Ordered. Each step says what it is for and what proves
it worked, because several of the ones below report success without having done
anything.

This is the whole-product list. `server/dawnlist-feed-worker/LAUNCH-READINESS.md`
keeps the reasoning behind the Worker's deployment; where the two describe the
same command, this file is the order to do it in and that one is the why.

---

## What ships, and what does not

| Surface | State | Artefact |
|---|---|---|
| Microsoft Store (Windows) | Ready to submit | `dist/Dawnlist.msix` |
| Direct download, Windows | Ready to submit | `dist/Dawnlist-windows-1.0.0.zip` |
| Direct download, macOS | Ready to submit | `dist/Dawnlist-1.0.0.dmg` |
| **Mac App Store** | **Cannot be submitted** | — |

**The Mac App Store is not a packaging step away.** Apple's guideline 3.1.1
forbids unlocking an app with a licence key, which is exactly how the
entitlement works. The compliant route — 3.1.3(b), Multiplatform Services —
requires a StoreKit in-app subscription *and* account sign-in, and the product
has neither, having deliberately avoided holding user records. The receipt
plumbing in `app/core/mac_receipt.py` is the start of that work, not the end of
it: the Worker has no `/v1/apple` endpoint and there is no StoreKit purchase to
produce a receipt in the first place.

macOS buyers are properly served by the notarised `.dmg`, which is outside
Apple's commerce rules entirely and keeps 100% of revenue less Paddle. It is
the same application, not a lesser one.

---

## Step 1 — Buy the feed credits

> **CORRECTED 2026-09-08, and it is the correction that matters most in this
> file.** This step previously said "buy the $199 one-time pack, 5,000
> credits". **That buys the wrong currency and Dawnlist could not spend a
> penny of it.** Spencer challenged the figure and was right to.

### There are two credit pools, and only one of them reaches the API

| Pool | How it is sold | What spends it |
|---|---|---|
| **Company credits** | **One-time packs** — $109/1,000 up to $999/200,000, rolling over twelve months | Revealing or exporting a company **in the web app** at `app.theirstack.com` |
| **API credits** | **Monthly subscription only** — $49/1,500 up to $5,500/5M | **Jobs returned by the API or a webhook** (1 each); companies (3 each); technographics (3 each) |

Dawnlist's Worker calls the API. It spends **API credits** and nothing else.
A one-time pack would leave $199 spent and a store reviewer still watching a
search fail. Spencer's own balance is the proof the two pools are separate:
50 web-app credits unused alongside 195 of 200 API credits spent.

The twelve-month rollover — the appealing half of the old reasoning — belongs
entirely to the pool that cannot be used.

### What to buy before submission

**The $100/month tier: 5,000 API credits. Time it to submission, not before.**

An API subscription bills every month whether anyone searches or not, so
buying early to sit in a review queue is money for nothing. Buy it the week
the first package is submitted.

$49/1,500 is the entry tier and it is too tight to be safe: one comprehensive
search is ~513 credits, so a single unscoped test run costs a third of the
month's allowance. 5,000 covers end-to-end verification, both store reviews and
the first trials, with room for a mistake. **Reviewer workload has not been
measured** — this is sized for headroom, not from data. A trial code caps a
reviewer at 30 postings a day, which bounds the risk.

### What to buy afterwards, and when

One credit is one job returned. A subscriber running a comprehensive daily
search consumes roughly **15,400 credits a month** (~513/day). Against $79 on
Paddle, which nets ~$74.55 — **all API subscription tiers**, the only ones that
can serve the product:

| Tier | Credits | $/credit | Subscribers it serves | Feed cost each | Contribution |
|---|---|---|---|---|---|
| $240/mo | 20,000 | $0.0120 | 1 | $240 | heavily negative |
| $400/mo | 50,000 | $0.0080 | 3 | $123 | negative |
| $600/mo | 100,000 | $0.0060 | 6 | $92 | negative |
| **$900/mo** | **200,000** | **$0.0045** | **13** | **$69.30** | **+$5.25** |
| $1,200/mo | 500,000 | $0.0024 | 32 | $37 | +$37 |
| $1,500/mo | 1,000,000 | $0.0015 | 64 | $23 | +$51 |

**Per-subscriber contribution only turns positive at the 200,000 tier**, and it
does not become comfortable until 500,000. That is not a pricing failure, it is
block granularity: credits are sold in blocks, so a block must be bought before
there are users to spread it over. Below roughly **18 subscribers** the product
loses money whatever you buy.

So:

1. **At submission:** $100/month, 5,000 credits.
2. **First paying subscribers:** step up the tier to match measured burn. Each
   step is a downgrade away if it was premature.
3. **At about 13 subscribers:** the **$900/month, 200,000** tier, which is where
   the product stops losing money per head. Read `usage_daily.postings` to
   decide — the number is instrumented precisely so this is a measurement and
   not an argument.
4. **Above roughly 65 daily-active users:** re-open the Datasets question. Bulk
   delivery costs the same whether there is one subscriber or a hundred, so
   there is a crossover, and it is around 1M records a month.

> Two reasons real burn may come in **below** 15,400. The app now sends
> `excludeJobIds`, so postings already paid for are not re-fetched — steady
> state after the first sweep should be well under 513/day. And searches are
> now required to be specific (at least one country, named titles), which was
> added for exactly this reason. Do not raise a tier on the model when the
> table can be read.

---

## A note on running these commands

**Every command here works from anywhere, including `C:\Windows\System32`.**
That is deliberate. `npx wrangler secret put THEIRSTACK_API_KEY` run from a
console that happened to open in System32 fails with *"Required Worker name
missing"* — which reads like a broken configuration and is only a wrong
directory. Passing `--config` removes the question entirely, so the commands
below carry it.

For brevity these define the path once:

```powershell
$W = "C:\Users\SpencerFields\dawnlist\server\dawnlist-feed-worker\wrangler.jsonc"
```

Everything is verified on **PowerShell 7.6.5 (Core)**. The three `.ps1` scripts
resolve their own paths from `$PSScriptRoot`, so they too may be launched from
anywhere, and all three parse and run under 7.x — `Import-Certificate`, the
`Cert:` drive and `Get-AuthenticodeSignature` are all present in PowerShell 7
on Windows.

---

## Step 2 — Migrate the database

> **DONE 2026-09-08, and verified.** Both migrations were applied to the
> live database. All three existing licences and all five override codes
> backfilled to `standard` with 700/3/10 caps — the allowance they already
> had by default, so nothing about anybody's behaviour changed. Kept here
> because a second run FAILS, and somebody working down this list needs to
> know that is expected rather than a fault.

Once each, in order. SQLite has no `ADD COLUMN IF NOT EXISTS`, so a second run
stops at "duplicate column name" — which is the documented behaviour, not a
fault.

```powershell
npx wrangler d1 execute dawnlist --remote --config $W --file "C:\Users\SpencerFields\dawnlist\server\dawnlist-feed-worker\migrations\001-plans.sql"
```

```powershell
npx wrangler d1 execute dawnlist --remote --config $W --file "C:\Users\SpencerFields\dawnlist\server\dawnlist-feed-worker\migrations\002-code-plans.sql"
```

001 adds the plan columns to `licences` and backfills existing rows to
`standard`, whose 700/day ceiling is what they already had, so nothing about
anybody's behaviour changes. 002 does the same for override codes.

**Proves it worked:**

```powershell
npx wrangler d1 execute dawnlist --remote --config $W --command "SELECT plan, COUNT(*) FROM licences GROUP BY plan;"
```

Every row should have a plan. A NULL means the backfill did not run.

---

## Step 3 — Configure the Worker

```powershell
npx wrangler secret put THEIRSTACK_API_KEY --config $W
```

```powershell
npx wrangler secret put PADDLE_WEBHOOK_SECRET --config $W
```

**Two more, added 2026-09-08 when licence delivery was built.** Without
them a customer pays, a licence is created, and nothing reaches them:

```powershell
npx wrangler secret put RESEND_API_KEY --config $W
```

```powershell
npx wrangler secret put PADDLE_API_KEY --config $W
```

`RESEND_API_KEY` sends the mail. `PADDLE_API_KEY` looks the buyer up:
`subscription.created` carries a customer id and no address, so without it
the Worker knows a licence is owed to somebody it cannot name. A read-only
key is enough. Both failures are logged by cause rather than swallowed —
`licence issued but NOT delivered` is the line to search for.

**`wrangler secret put` takes the secret's NAME.** The value goes at the
interactive prompt and nowhere else. Typing it into the command creates a
secret whose *name* is your credential, leaves the real one unset, and writes
the whole thing into Wrangler's logs, which record full command lines. This has
already happened once on the sibling project.

Price ids are **not** secrets. Set them as plain vars so a misconfiguration is
visible rather than hidden:

```
PADDLE_PRICE_STANDARD = pri_...        # every id for the $79 plan
```

Leave `PADDLE_PRICE_GLOBAL` unset — see step 4.

**Delete `ANTHROPIC_API_KEY` if it is still there.** Nothing reads it. It
belonged to an inference proxy built and removed on 2026-09-06, because
proxying job descriptions plus the fit brief would have made Spencer a
processor of every buyer's career record. A credential kept for a capability
that was deliberately removed is pure exposure.

```bash
npx wrangler secret delete ANTHROPIC_API_KEY
npx wrangler deploy
curl https://dawnlist-feed-worker.sgf36.workers.dev/health
```

---

## Step 4 — Create the Paddle product

> **DONE 2026-09-08.** Product `pro_01m217zsvqcexs9atdswvja3sf`, price
> `pri_01m217zszynjmwmcx6xqpewxc1`, $79/month recurring, quantity locked
> to 1, **tax exclusive** so the same $79 arrives from every country.
> The price id is committed in `wrangler.jsonc` and the Worker is
> deployed with it. `PADDLE_PRICE_GLOBAL` remains deliberately unset.

**One product, one price: $79 per month, recurring.**

Do **not** create the Global plan yet. Its 2,500/day ceiling is the one number
in `src/plans.js` that has never been measured against a real multi-region
sweep, and selling an allowance nobody has tested is how a cap quietly becomes
the thing that decides coverage. Run a genuine multi-region search, read
`usage_daily.postings`, set the ceiling from what comes back, and only then
list it. It is a D1 column, so correcting it is an `UPDATE`, not a release.

Copy the price id — and every currency and billing-period variant of it, comma
separated — into `PADDLE_PRICE_STANDARD`.

**Sandbox and production issue different ids for the same product.** An id that
matches nothing makes the webhook fall back to Standard and set
`plan_unmatched = 1`. Today that fallback is harmless because Standard is the
only plan; the moment Global exists it means a customer paid for one thing and
received another.

> **MEASURED 2026-09-11 — CORRECTS THE 2026-09-10 BLOCK THAT STOOD HERE.
> THE LIVE DAWNLIST DESTINATION IS ACTIVE. NOTHING NEEDS SWITCHING ON.**
>
> Read by id with `notificationSettings.get`, on the live account:
>
>     "Dawnlist Worker (USE ME)"  ntfset_01m21hag...
>       -> https://dawnlist-feed-worker.sgf36.workers.dev/paddle/webhook
>       active: TRUE, traffic all, subscribed to subscription.created
>     "Dawnlist Feed Worker"      ntfset_01m218j2...
>       -> the same URL, active: false — the pre-rotation destination that the
>          2026-09-08 secret rotation retired
>     "USE ME!"                   ntfset_01m06589...
>       -> easypost-license-webhook.sgf36.workers.dev — EasyPost's, active
>
> Paddle refuses two active destinations on one URL, so exactly one Dawnlist
> destination is ever live, and this is it.
>
> **WHAT THE 2026-09-10 BLOCK GOT WRONG, AND WHY.** It said the Dawnlist
> destination was switched off and that only Spencer could turn it on. It had
> found the *retired* destination through `notifications.list()`, which only
> surfaces destinations that have received platform events. The live one had
> only ever received a simulation, so it never appeared. The block wrote that
> coverage limit down ("two seen, not two exist") and still reported a state.
> It also said the names were crossed. They were not: there are two "USE ME"
> destinations, EasyPost's and Dawnlist's, and the 2026-09-09 note naming
> "Dawnlist Worker (USE ME)" was right.
>
> **HOW TO MEASURE.** Get each known destination BY ID with
> `notificationSettings.get(<id>)` and read `active`. Never infer the live one
> from `notifications.list()`, and never from a name — both Dawnlist
> destinations share one URL.
>
> **DELIVERY IS STILL UNPROVEN, AND NOTHING IN THE ACCOUNT BLOCKS PROVING IT.**
> A key being *issued* is not a key *received*. The gate is a
> `subscription.created` carrying a real customer email, and the email seen to
> arrive. Paddle refused the Dawnlist checkout domain on 2026-09-11, so this
> means a simulation built on a real customer, not a sale. Spencer runs it.
>
> The shape to keep: a note corrected on better evidence can be wrong in a new
> way. This block replaces one that read as a blocker, exactly as the one
> before it read as clearance.

> **BLOCKER FOUND 2026-09-08. THERE IS NO DAWNLIST WEBHOOK DESTINATION.**
>
> Every notification destination on the live Paddle account points at
> **Easy-Post's** Worker:
>
>     ntfset_REDACTED  "USE ME!"
>     -> https://easypost-license-webhook.sgf36.workers.dev/paddle/webhook
>     subscribed to subscription.created, subscription.activated,
>     transaction.completed — everything
>
> Nothing points at `dawnlist-feed-worker`. So a Dawnlist purchase today would
> charge the customer, deliver `subscription.created` to **Easy-Post's**
> Worker, and deliver nothing to Dawnlist's. The buyer would receive no key,
> and the wrong service would be asked to issue one.
>
> **This also puts the secret in doubt.** `PADDLE_WEBHOOK_SECRET` is set on the
> Dawnlist Worker, and a destination for Dawnlist does not exist — so whatever
> that value is, it did not come from a Dawnlist destination. A Paddle secret
> embeds its own destination id; a secret from the wrong destination fails
> signature verification and reads as a wrong secret rather than a wrong
> destination. That is the failure the Easy-Post runbook records breaking four
> times.
>
> **`notificationSettings.list()` returns `[]` on this account** while those
> destinations demonstrably exist — confirmed empirically today, not assumed.
> Do not enumerate destinations that way and do not read an empty list as
> "none". Read them in the dashboard, or `get` them by id.
>
> **"USE ME!" is the hazard.** It is the most convincingly-named destination
> and it belongs to a different product.

Then point a Paddle notification destination at
`https://dawnlist-feed-worker.sgf36.workers.dev/paddle/webhook` and put its
signing secret in `PADDLE_WEBHOOK_SECRET`.

**Verify by replay, never by inspection.** "Wrangler said success" proves a
value was stored, not that it was the right one:

- Replay `subscription.created`; confirm a new `licences` row with
  `plan = 'standard'` and `max_postings_per_day = 700`.
- `GET /v1/plan` with that licence key; confirm the numbers match D1.
- `SELECT licence_key FROM licences WHERE plan_unmatched = 1;` must be empty.

---

## Step 5 — GitHub secrets, so CI can sign

> **Status, 2026-09-08 17:11 — macOS is DONE and PROVEN.** Run `34255301944`
> imported the certificate, validated the notarisation credentials against
> Apple, signed 15 nested binaries plus the bundle, verified the signature,
> built the `.dmg`, notarised it (submission `d50f9f9a-f515-4a6f-a781-
> e7f6f4837f45`) and stapled the ticket — in 2m58s. All three artefacts now
> exist on every run.
>
> **Only Windows direct-download signing is outstanding**, and only the
> federated credential and the `AZURE_SIGNING_READY` variable — the three
> `AZURE_*` secrets are set. Until then the ZIP is produced but named
> `UNSIGNED-DO-NOT-PUBLISH-…`, with no checksum, exactly as intended.

### 5a. Windows signing — reuse the existing certificate

Azure Artifact Signing is already live for Easy-Post Desktop, and **the
certificate profile is named for the publisher, not the product** — one profile
signs everything the business ships. So there is nothing to buy and no new
identity validation. The endpoint, signing account `EasyPostDesktop` and
profile `SpencerFieldsSoftware` are already hard-coded in the workflow.

What is missing is permission for *this repository*, because the federated
credential is bound to a repository subject:

1. In the Entra app registration **EasyPostDesktop-GitHubActions**, add a second
   federated credential with subject

   ```
   repo:sgf36/Dawnlist:ref:refs/heads/master
   ```

   **`master`, not `main`.** Easy-Post's credential says `main` because that is
   Easy-Post's default branch; this repository's is `master`, and the subject
   is matched literally. A one-word mismatch fails the OIDC exchange with a
   message about the credential rather than about the branch.
2. Confirm the *Artifact Signing Certificate Profile Signer* role is scoped to
   the signing account rather than to the EasyPost repo. If it is scoped
   correctly it already covers this.

```bash
gh secret   set AZURE_CLIENT_ID       -R sgf36/Dawnlist --body "3bc64444-7f28-46d6-9ee6-e90ed9b56649"
gh secret   set AZURE_TENANT_ID       -R sgf36/Dawnlist --body "9b52d991-eac1-46ce-a0ee-158ce7579674"
gh secret   set AZURE_SUBSCRIPTION_ID -R sgf36/Dawnlist --body "96d4ff81-7f5c-4028-bde2-92e1e24e057f"
gh variable set AZURE_SIGNING_READY   -R sgf36/Dawnlist --body "true"
```

None of the three is a credential — they are directory identifiers, and the
OIDC exchange is what proves the workflow may use them. They are secrets only
because that is where `azure/login` reads them from.

Until `AZURE_SIGNING_READY` is `true`, CI still builds the ZIP and still runs
every guard, but names it `UNSIGNED-DO-NOT-PUBLISH-…` and writes no checksum.

### 5b. macOS signing and notarisation

> **The distribution provisioning profile is not used here, and this was
> checked rather than assumed.** The certificate embedded inside
> `Dawnlist_Job_Search.provisionprofile` is **`Apple Distribution: Spencer
> Fields (7WA4F8P743)`** — Apple Distribution is the App Store certificate, so
> this is a Mac App Store profile. The direct-download `.dmg` is signed with
> Developer ID and cleared by **notarisation**, which reads the signature and
> the hardened runtime and knows nothing about profiles.
>
> It is filed at `Apps\Claude MacOS\signing\Dawnlist_Job_Search.provisionprofile`,
> beside `EasyPost_Desktop.provisionprofile`. Nothing in this launch consumes
> it and there is no secret to create from it yet; when the Mac App Store
> project starts it becomes `MAS_PROVISION_PROFILE_BASE64`, matching Easy-Post.
>
> Two facts from it worth having now: the App ID
> **`7WA4F8P743.com.spencerfields.dawnlist` is registered** — which matches the
> bundle identifier in `build_exe.spec`, so that step is already done — and both
> the profile and its Apple Distribution certificate **expire 4 August 2027**.

Direct download needs a **Developer ID Application** certificate — the same one
Easy-Post Desktop already uses, because a Developer ID certificate belongs to
the team and not to an app. It needs no App ID registration, no provisioning
profile and no App Store Connect record.

GitHub cannot read a secret back out, so these have to come from the original
material rather than by copying from `sgf36/EasyPost`:

| Secret | What it is |
|---|---|
| `MACOS_CERTIFICATE_P12_BASE64` | The Developer ID Application cert and key, exported as .p12, base64-encoded |
| `MACOS_CERTIFICATE_PASSWORD` | The password set on that export |
| `MACOS_SIGN_IDENTITY` | The identity string, e.g. `Developer ID Application: Spencer Fields (TEAMID)` |
| `APPLE_ID` | The Apple ID that owns the team |
| `APPLE_APP_PASSWORD` | An **app-specific** password from appleid.apple.com, not the account password |
| `APPLE_TEAM_ID` | The ten-character team id |

The names are identical to Easy-Post Desktop's on purpose, so the two projects
do not drift into two conventions.

**Two traps that cost real time on the sibling project.** Both produce an error
that names the wrong cause:

- **Export the `.p12` with `-legacy -macalg sha1`.** OpenSSL 3.x writes PKCS#12
  with AES/PBKDF2 and a SHA-256 MAC, which macOS `security import` cannot read.
  It fails with `MAC verification failed (wrong password?)` when the password is
  perfectly correct, so the natural next move — retyping the password — never
  works.
  ```
  openssl pkcs12 -export -legacy -macalg sha1 -inkey key.pem -in cert.pem -out DeveloperID.p12
  ```
- **`APPLE_APP_PASSWORD` is an app-specific password** from appleid.apple.com,
  not the Apple ID password. The account password authenticates and then
  notarisation fails later with an unrelated-looking error.

**I cannot do this step and will not ask for the values.** Certificates and
passwords go from you into GitHub directly.

### 5c. Then push

Push to `main`. One run produces:

- `dawnlist-windows-latest-store` — the signed MSIX
- `dawnlist-windows-latest-direct` — the signed ZIP and its `.sha256`
- `dawnlist-macos-latest-direct` — the notarised `.dmg`

The `worker` job runs the Cloudflare test suite, which CI was not running at
all until today.

---

## Step 5c — Prove the shipped client can reach the live service

**Run this before every release. It costs nothing.**

```powershell
python C:\Users\SpencerFields\dawnlist\tools\smoke_live.py DAWN-YOUR-LICENCE-KEY
```

It calls `/v1/plan` through `ManagedProvider` — the same class, transport,
headers, TLS and edge the application uses — and fetches no postings, so it
spends no credits. A check that costs money is a check nobody runs, and a check
nobody runs does not exist.

**Why it exists.** On 2026-09-08 the application could not reach its own service
at all: Cloudflare refused urllib's default user-agent with error 1010, and
every request from every shipped build would have failed. Both halves of the
verification were structurally blind to it — every unit test injects the
transport, so none makes a real request; and every manual check used `curl`,
whose user-agent is not blocked, so the manual pass proved nothing about the
product. The suite was green while the application could not connect.

A unit test now pins the user-agent and catches that exact regression. It cannot
catch the next one, because the failure was never really about a header: it was
about nobody running the shipped client against the real service.

It also asserts two things that have been wrong in production within a day: that
the upgrade ladder is not advertising a plan nobody can buy, and that the licence
has an allowance at all.

---

## Step 6 — Microsoft Store

The package identity is already correct and verified against Partner Center:
`SFields.DawnlistJobSearch`, publisher `CN=A7D4B6C0-27D4-4F66-82EB-82F5DD466788`,
Store ID `9PF25H395BB8`. **Do not change it.** A display name can be changed
after publish; the identity cannot, and getting it wrong means the app can only
ever be replaced, never updated.

**List it as a FREE app. Do not create a subscription add-on.**

The subscription is bought on the website through Paddle, and the licence a
Store customer holds is the same one a direct-download customer holds. That is
expressly permitted — Microsoft allows third-party commerce and takes **0%** —
and it avoids building a `StoreContext` entitlement exchange that does not
exist. The reasoning is in `store/microsoft-store-listing.md`.

1. **Tick the third-party-purchase declaration.** Using third-party commerce
   without declaring it is the compliance failure; the commerce itself is fine.
2. **Mint a trial override code and put it in the submission notes.** A
   reviewer installs a free app which then asks for a licence key, and without
   a code they see a product that does nothing. That is a rejection. One line
   in the notes saying where to paste it is enough.
3. **Run WACK** against `dist/Dawnlist.msix`. This needs an elevated shell,
   which is why it is yours and not CI's.
4. Upload the MSIX, complete the listing, submit.

---

## Step 6a — Getting the artefacts to the website

**Corrected 2026-09-11.** This step used to say the repository was private, that
fetching needed your GitHub login, and that four files ship — a Windows ZIP and
a macOS disk image, both 1.0.0, under `/download/`. All four were wrong by the
time anyone needed them: `gh repo view` reads `visibility: PUBLIC`, the signed
1.1.0 ZIP was fetched without your login, and macOS is Mac App Store only by
your decision of 2026-09-08, so no `.dmg` is ever published.

Artefacts come from a **push to `master`** and never from a pull request —
signing is bound to the default branch, so a PR build is green and unsigned:

```bash
gh run download <master-run-id> -R sgf36/Dawnlist -n dawnlist-windows-latest-direct -D dist-release
```

Two files, and only two:

| From | Publish |
|---|---|
| `Dawnlist-windows-<version>.zip` | under the site's download folder |
| `Dawnlist-windows-<version>.zip.sha256` | beside it |

The folder name is **not settled as at 2026-09-11** — the held page links
`/download/`, the agreed layout said `/downloads/`, and neither exists on the
host. Whatever the site settles, the page link and the `url` in
`/updates/windows.json` must name the same path.

**Before handing any filename over, observe three things on the file itself,
not the CI tick:** `Get-AuthenticodeSignature` reads `Valid`; a SHA-256 you
compute matches the `.sha256`; and `_internal/app/resources/` holds
`license_required.flag` with NO `store_build.flag` and NO `mas_build.flag`.

**If any filename begins `UNSIGNED-DO-NOT-PUBLISH-`, stop.** Signing did not
run. There is no checksum beside it, deliberately.

`/updates/windows.json` names the version actually hosted and nothing higher.
A manifest ahead of the hosted build sends every direct-download user to a 404,
silently, because the update check is quiet on failure by design.

---

## Step 7 — Website

Owned by the other session, listed here so nothing falls between the two.

The one item that is a legal obligation rather than a nicety: **the terms must
be accepted at purchase and the acceptance recorded.** The data provider's terms
require every downstream recipient — every subscriber — to be bound by written
terms at least as restrictive as their section 4, Spencer is responsible for
subscribers' acts as if they were his own, and the licence to retain any data at
all terminates automatically on breach of that clause. A page nobody agreed to
binds nobody.

The application links to `https://dawnlist.spencerfields.com/terms.html` from
Settings on every build, in all fifty languages, and the URL is compiled in. If
it has to move, that is a code change and it has to happen before release.

**A MAC APP STORE SUBSCRIBER IS NOT BOUND BY THESE TERMS, AS IT STANDS
(found 2026-09-11).** The Mac listing's description cites Apple's *standard*
EULA (`apple.com/legal/internet-services/itunes/dev/stdeula/`), and a
subscriber on the Mac is bound by that and nothing else — a Settings link to
`terms.html` is not acceptance. Against the clause above, that is a subscriber
the data licence says must be bound and is not. The Mac version is
`WAITING_FOR_REVIEW`, so approval can create such subscribers without anyone
acting. **Your decision, and it wants making before approval rather than
after.** The usual route is a custom licence agreement in App Store Connect
carrying the section 4 restrictions. Resolved only when the Mac app's licence
agreement in App Store Connect can be read and shows them.

---

## Step 8 — After submission

- **Measure a multi-region sweep** and set the Global ceiling from the result,
  before that plan is ever sold.
- **Ticket #5008: CLOSED ON OUR SIDE. Do not chase.** Spencer's decision,
  2026-09-08. The drafted chase was deleted and nothing further will be sent.

  This is not "awaiting a reply" and must not be recorded as one. The licensing
  position is settled on the published terms, and the **§4.5-versus-§4.12
  tension is an ACCEPTED RISK** rather than a pending question. What that means
  concretely: if the provider ever reads §4.5 broadly, the remedy is injunctive
  — stop using the feed — not damages. That is the risk being accepted, and it
  is a reason to keep the first credit tier small, which step 1 already does.
- **Do not name the data provider publicly.** Still holds. It was tied to the
  ticket; it now stands on its own, because naming them invites the question
  the terms leave open.

---

## Waiting on a working Anthropic key

**The key in this machine's Credential Manager (`dawnlist-anthropic`) is
rejected: 401 "API key is invalid", well-formed, so rotated or revoked since
2026-09-10 morning.** Enter a working one in Dawnlist's Settings — the
sessions will not store a credential for you. Resolved when a one-token call on
the stored key returns text rather than 401.

Everything below is blocked on it, and goes in ONE pass, because each is
fifty languages and a partial pass is worse than none — `tests/test_i18n.py`
fails a catalogue that is behind, and a half-translated file looks finished.

1. **Licence email, `to_use_body`.** It told buyers a key works "on both the
   Windows and macOS editions"; a Mac App Store build carries no licence field.
   English is corrected in `tools/gen_email_strings.py`; then
   `python tools/gen_email_strings.py --keys to_use_body,allowance`. The `allowance`
   sentence was reworded on 2026-09-11 to call 700 a day a fair-use limit, matching
   pricing.html, so both keys go in the same run.
2. **Nine entitlement refusals** in `app/core/entitlement.py` are raw English
   shown under a translated title (`app/main.py`). Move them into the catalogue
   and translate all fifty in the same commit.
3. **Anthropic cost in both store listings.** The measured figure is "usually a
   pound or two a month". The Microsoft listing contradicts itself — "typically
   a few pounds" before-you-buy, "a pound or two" further down — and is live in
   47 languages, so this is a new Partner Center submission. The Mac listing says
   "typically a few pounds"; change it only once Apple's review has finished,
   because editing a version in review pulls it from the queue.
4. **The app half of "purchasing is not open yet"**, in the exact words you
   settle, identical to the Store listing's.

---

## The accepted risk

Not an open question any more — **Spencer closed it on 2026-09-08 and decided
to proceed.** Recorded here so the decision is legible later, and so nobody
reopens it as though it were still pending.

The published terms answered five of the seven questions the ticket asked, and
answered them favourably: job postings are the most permissively treated data
class in the whole agreement, and reselling them partially through your own
platform is expressly authorised. What they do not settle is §4.5 against
§4.12, above.

§4.12 is the better reading. But it is a judgement and it is the provider's to
make, so the downside is worth stating plainly: worldwide injunctive relief is
available for a §4.5 breach, and the remedy would be to **stop using the feed**
rather than to pay damages. A product built on a feed it can be ordered to stop
reading is the shape of the risk.

Two things make it a sensible risk rather than a reckless one. The first credit
tier is $100/month and cancellable, so being wrong costs a month rather than a
year. And Dawnlist does considerably less than §4.3 expressly permits — it
shows postings privately, to the one person who searched for them, against a
licence that allows publishing them on an indexed public page.
