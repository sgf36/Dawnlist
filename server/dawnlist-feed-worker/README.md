# dawnlist-feed-worker

The feed proxy. Holds the provider keys, meters per licence in D1, caches
across users, and makes the provider a config value rather than a code path.

## Deploy

```bash
npx wrangler d1 create dawnlist
# put the returned database_id into wrangler.jsonc
npx wrangler d1 execute dawnlist --remote --file=./schema.sql
npx wrangler secret put THEIRSTACK_API_KEY
npx wrangler deploy
```

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
refreshes/day, 600 postings/day), so one licence can be raised without a
deploy. The posting cap matches the operating spec's "target 30–60 reaching
assessment" dial, applied at the screen's input.

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
