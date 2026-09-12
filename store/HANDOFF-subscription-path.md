# Dawnlist subscription path — state as at 9 September, evening

From the app/build session (`claude-fe`). Everything below was verified
tonight against the live service or the shipped artefact, not read off the
source. Where something is unproven it says so.

---

## 1. The licence check was accepting anything, then refusing everything

Until this evening `verify_against_worker` called **`/health`** — a route that
takes no request, never reads the Authorization header, and answers 200 to
anybody. So every string typed into the licence box verified.

It was replaced with **`/v1/licence`**, which authenticates. That route was
written, committed — and **not deployed** until tonight. It is deployed now:

    fabricated key : 403 {"error":"unknown_licence"}
    no key         : 401 {"error":"no_licence"}
    real key       : 200 {"ok":true,"plan":"owner","role":"admin",...}

## 2. Cloudflare refuses urllib's default user-agent, and that shipped

`Python-urllib/3.x` is refused with **error 1010 and an HTTP 403**.

    default UA  : 403 'error code: 1010'
    Dawnlist UA : 200 {"ok":true,...}

`licence_details` reads 401/403 as "the server said no", and `check()` treats
a refusal as **final** — deliberately checked *before* the grace period, so a
revoked licence cannot keep working for a fortnight. The consequence:

> **The published Microsoft Store build (1.0.0) rejects every licence key ever
> typed into it.** Purchasing being closed is the only reason that reached
> nobody.

It was fixed in `managed.py` on 8 September, with a comment. Four more places
were found sending the default two days later: `/v1/licence`, `/v1/apple`,
`/redeem` and the whole admin console. There is now ONE constructor
(`app/core/http.py`) and a test that walks the AST of `app/` and fails if
anything else builds a `urllib.request.Request`.

## 3. What a MAS build will and will not honour

`build_provider` honours a licence on a Mac App Store build **only** when the
server says:

    granted_by_code == true   AND   purchased == false

A free grant is not a purchase, so it is the right side of guideline 3.1.1. A
Paddle licence sitting in the keychain from the Windows edition is refused,
which matters because the keychain is per USER and not per application.

## 4. Roles decide the feed

| role | gets the metered feed |
|---|---|
| `byo` | no — supplies their own provider key |
| `managed` | **yes** |
| `admin` | yes, plus the console |

A reviewer or friends-and-family code must be `managed` or the feed stays shut
and the code looks broken.

**ON A MAC BUILD THERE IS NOWHERE TO TYPE ONE, SINCE 2026-09-12.** Apple
rejected 1.1.0 (75) under 3.1.1 — "the app uses access codes to unlock app
features" — so the redemption box is gone from `SubscribePanel` and no `mas`
screen offers redemption at all. A grant already in the keychain is still
honoured (section 3 is unchanged), and Windows keeps its box in
`LicencePanel`. The consequence to plan for: an App Review reviewer can no
longer let themselves in with a code and has to complete a sandbox
subscription, which makes the Worker's Apple route a prerequisite for
resubmission rather than a nice-to-have.

## 5. The Mac purchase path had NEVER worked

PyObjC was never a declared dependency, so `import StoreKit` failed in every
package ever built. The only trace was two lines inside PyInstaller's output:

    ERROR: Hidden import 'CoreFoundation' not found
    ERROR: Hidden import 'objc' not found

after which the build succeeded, signed, uploaded and reached TestFlight. The
app ran; the Subscribe button was permanently dead and said "The App Store
cannot take a purchase on this Mac right now", which reads as an App Store
hiccup. Declared now, and `build_macos.py` REFUSES a mas package whose bundle
carries no StoreKit.

**Still unproven: nobody has completed a sandbox purchase.** A built binary
that imports StoreKit is not a payment that worked.

## 6. Restore reported success to Macs that had never subscribed

`paymentQueueRestoreCompletedTransactionsFinished_` fires when the restore
OPERATION finishes, including with nothing found. The code then asked only
whether a receipt file existed — true for every Mac App Store app,
subscription or not. Pressing Restore before subscribing answered
**"Subscribed. The feed reads for you from tomorrow morning."**

Restore now counts what it restored. Whether a restored subscription is still
ACTIVE remains the server's question: a restored transaction can be expired or
refunded.

## 7. Which build to trust

| build | verdict |
|---|---|
| 53, 61 | no StoreKit; licence check refuses everything |
| 66 | StoreKit present; Restore lies; purchase screen can dead-end |
| 69 | Restore still lies |
| **72** | **the one to test** — in TestFlight, attached to version 1.1.0 |

## 8. Where the subscription is offered (1.1.0)

Setup asks for the Anthropic key, then the subscription — it used to ask for
neither, and both panels lived only in Settings, which onboarding never opens.
Also reachable from the "⋯" menu and Settings. It never blocks: the board and
the brief are useful unpaid; it is the live feed that is bought.

---

## What would help from your side

1. Anything you change in the Worker's licence or entitlement logic, tell me —
   `app/core/entitlement.py` and `app/core/admin.py` are the only clients.
2. If you mint codes, `managed` is the role that works for a tester.
3. Do not reintroduce a licence-key field on the MAS build in any form.
