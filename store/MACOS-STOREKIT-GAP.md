# macOS is blocked on a purchase flow that does not exist

**Written 2026-09-09.** Decision that day: **ship Windows first, macOS when this
is built.** Windows is one package upload from submission; macOS needs the work
below. Holding Windows for macOS would cost a week for nothing.

---

## What is already done, so nobody rebuilds it

App Store Connect is fully configured. Confirmed live 2026-09-09:

| Thing | Value |
|---|---|
| App | `6809836433`, `com.spencerfields.dawnlist`, primary locale `en-GB` |
| Version | MAC_OS 1.0, `PREPARE_FOR_SUBMISSION` |
| Subscription group | `22370180` "Dawnlist" |
| Subscription | `6809984690`, `com.spencerfields.dawnlist.monthly`, ONE_MONTH |
| Price | $79.00 — Apple reports proceeds **$67.15** (15% Small Business rate) |
| Availability | 175 territories, new territories on |
| Localisation | en-GB, "Dawnlist" / "The job feed, read and judged daily" |
| Review note | Set |
| **State** | **`MISSING_METADATA`** |

Rebuild any of that with `tools/asc_subscription.py` and
`tools/asc_subscription_price.py`. Both are idempotent.

The app side is also built: `app/core/mac_receipt.py` reads the App Store
receipt, `entitlement.exchange_mac_receipt` trades it with the Worker, and
`build_provider` refuses when Apple reports no active subscription. That path
is correct and is not what is missing.

---

## What is missing

> **UPDATED 2026-09-09, LATER THE SAME DAY.** The purchase flow described below
> as missing has since been WRITTEN — `app/core/mac_storekit.py` and
> `SubscribePanel` in `app/ui/settings.py`. It has **never run on a Mac**.
> Read the section under "What is still missing" at the bottom before treating
> any of this as done; the original text is kept because the reasoning for why
> it blocks submission is unchanged.

**Nothing in the application sold the subscription.**

`mac_receipt.py` only READS a receipt. On a `mas` build with no active
subscription, `app/main.py` raises `NotConfigured` — "The App Store could not
confirm an active subscription" — and stops. `app/ui/settings.py` hides the
licence panel on `mas` (correctly, per guideline 3.1.1). So there is no
Subscribe button anywhere in the product.

Two consequences, and the second is the one that matters:

1. **The subscription cannot leave `MISSING_METADATA`.** Its last unmet
   requirement is an App Review screenshot, and there is no screen to
   photograph.
2. **App Review would reject it anyway.** An app carrying a subscription
   product has to let a customer buy it in the app. A build that reports it
   cannot confirm a subscription, and offers no way to get one, is a dead end
   in front of a reviewer.

This is not a settings problem. It is a missing feature.

---

## Why it is more than an afternoon

Dawnlist is PySide6/Python. There is no StoreKit binding for it.

- **StoreKit 2 is Swift-only** and cannot be reached from Python.
- **StoreKit 1 via PyObjC** is the realistic route: `SKProductsRequest`,
  `SKPaymentQueue`, and an observer for transaction updates. It works, it is
  well documented, and it is deprecated-but-supported.
- Whatever is built must **refresh the receipt after purchase**
  (`SKReceiptRefreshRequest`), because the existing `read_receipt()` reads a
  file that does not exist until the App Store writes one.
- A MAS build must **exit 173** when the receipt is missing or invalid;
  `mac_receipt.py` already documents this. Do not break it while adding the
  purchase path.

Scope, roughly: a purchase view, a PyObjC StoreKit bridge, receipt refresh,
restore-purchases (Apple requires it), and a macOS screenshot set. None of it
can be tested on Windows.

---

## What NOT to do

**Do not add a licence-key field to the MAS build to get around this.**
Guideline 3.1.1 names licence keys explicitly. `build_provider` deliberately
refuses to consult a stored licence on `mas` even when one exists in the
keyring, because the keyring is per user and anyone who ran the direct build
first still has one. That refusal is load-bearing.

**Do not ship the notarised `.dmg` as a substitute.**
`packaging/build_macos.py --variant direct` still builds one and it must not be
published — macOS is Mac App Store only (Spencer, 2026-09-08). See
`STORE-COMPLIANCE.md`, Route A, marked WITHDRAWN.

---

## Order of work when macOS is picked up

1. PyObjC StoreKit bridge and a purchase view.
2. Receipt refresh after purchase, and restore purchases.
3. Render the macOS screenshot set (the Store set is Windows-sized).
4. App Review screenshot for the subscription — then it leaves
   `MISSING_METADATA`.
5. Age rating declaration.
6. Build and upload the `.pkg` from a Mac or CI runner.

---

## What is STILL missing, as of 2026-09-09

Written, not verified:

* `app/core/mac_storekit.py` — product fetch, payment, transaction observer,
  receipt refresh, restore. StoreKit 1 through PyObjC.
* `SubscribePanel` in `app/ui/settings.py` — shown on `mas` builds only, as the
  mirror of the licence panel that Windows builds get.
* 12 tests proving it degrades safely off macOS, and that a DIRECT-DOWNLOAD
  build on a Mac can never sell through Apple — it already sells through
  Paddle, and both live at once means a customer pays twice.

**NONE OF THE STOREKIT CALLS HAVE EVER EXECUTED.** They were written on Windows
from Apple's documented API. CI can compile them. CI cannot exercise a
purchase: that needs a signed build, a sandbox tester account and a person
clicking. The passing tests are evidence about the guards, not the feature.

**THE CRITICAL PATH IS NOW MAC ACCESS, NOT CODE.** Everything below waits on
somebody being able to run a signed build on a Mac:

1. Verify the purchase and restore flows against a sandbox tester.
2. Screenshot the SubscribePanel — that is the review screenshot the
   subscription needs, and the only reason it still reads MISSING_METADATA.
3. Render the macOS screenshot set (the Store set is Windows-sized).
4. Add the `mas` leg to `.github/workflows/build.yml`. Deliberately absent
   until there is something submittable; see the comment in that file.
5. Build and upload the `.pkg`.

**Age rating is DONE** (2026-09-09) — the declaration existed with all 29 fields
null and is now complete.


---

## RESOLVED, 2026-09-09 (evening) — read this before believing anything above

Most of this document is now out of date. It is kept because the reasoning is
still worth reading, but the status lines below supersede every "outstanding"
claim earlier in the file.

### The Mac App Store build ships, and no Mac was needed to make it

The certificates are team-wide and the private keys were regenerated on Windows
(`secrets/Dawnlist-MAS-*.key`), so a cloud Mac was never required for the build.
Three red runs, each a different lesson:

1. **"1 identity imported."** A single `.p12` built off a Mac collapses to ONE
   identity on `security import` — OpenSSL gives both key/cert pairs the same
   localKeyID. The app signed and `productbuild` then failed with "Could not
   find appropriate signing identity". Fixed by shipping the app and installer
   identities as TWO single-identity p12s, plus a `find-identity` check that
   fails in seconds rather than twenty minutes later.
2. **altool 90236, missing icon.** `BUNDLE(icon=...)` was conditional on a
   `dawnlist.icns` that never existed, so the `.app` shipped iconless. The icon
   is now generated and `_require_icon` makes it a hard failure, matching the
   `.ico` guard. This is also why App Store Connect showed no logo: the icon was
   fine once build 53 existed, but **the build was not attached to the version**.
3. **altool 90886, TestFlight ineligible.** The signed entitlements lacked
   `com.apple.application-identifier`. Added to `packaging/macos/mas.entitlements`
   along with `com.apple.developer.team-identifier`.

### The subscription's MISSING_METADATA was NOT the review screenshot

This is the correction that matters most. Section 4 of "Order of work" above
says the review screenshot is what holds MISSING_METADATA. **It is not.** The
screenshot was uploaded and reached `COMPLETE`, and the state did not move.

The subscription was **available in 175 territories and priced in exactly ONE**.
App Store Connect's web interface silently generates the equalised schedule when
a base price is chosen; `POST /v1/subscriptionPrices` does not. Nothing reads as
absent — localisation, availability, review note and screenshot all return 200
with data — so it survived repeated inspection. Filling the other 174 from
`subscriptionPricePoints/{id}/equalizations` moved it to `READY_TO_SUBMIT`
immediately. Tool: `tools/asc_subscription_price_fill.py`.

### The review screenshot was RENDERED, because macOS will not let it be captured

Every capture route on a machine nobody is sitting at is closed: `screencapture`
over SSH fails with "could not create image from display", `launchctl asuser`
needs root, Screen Recording is grantable only by a human in System Settings,
and SIP puts the TCC database out of reach.

Qt does not need any of it. `QWidget.grab()` paints the widget into a pixmap
in-process. `tools/render_subscribe.py` renders the real `SubscribePanel`, and
the `review-screenshot` job runs it on macOS so the fonts and controls are
genuinely macOS. The price is passed in and must match App Store Connect.

### Status of the "STILL missing" list above

* macOS screenshot set — **DONE**, 5 images, all COMPLETE.
* Review screenshot — **DONE**, uploaded and COMPLETE.
* `mas` leg in `build.yml` — **DONE**, and green: build 53 uploaded.
* Age rating — **DONE** (FOUR_PLUS, on the app record, not the version).
* App Privacy labels — **DONE** 2026-09-09: Email Address, Search History,
  User ID, Product Interaction, Other Diagnostic Data; each App Functionality,
  linked to the user, none used for tracking. Browser-only, no API exists.
* Also newly done, none of which announced itself as missing: primary category
  (BUSINESS), content rights declaration, the **app price schedule** (free) and
  the **App Review contact block** — the last two had never been created at all.

`tools/asc_readiness.py` now checks all of the above in one read-only pass, and
counts price rows against available territories rather than trusting presence.

### What genuinely remains

1. **A real sandbox purchase has still never executed.** Every StoreKit call was
   written on Windows from Apple's documentation. Build 53 is installed via
   TestFlight; someone must press Subscribe and then Restore. This is the ONLY
   remaining item that needs a Mac, and the passing tests are evidence about the
   guards, not the feature.
2. **Submit the subscription WITH the version, in one draft.** Open the
   subscription, press **Add for Review**, add version 1.0 to the same draft,
   submit once. Never `POST /v1/inAppPurchaseSubmissions` — that creates a
   review that cannot be listed, read or recalled, and it cost Wren a cancelled
   release.
3. The privacy policy's Payment section describes **Paddle** as merchant of
   record, which is true for Windows and wrong for the Mac App Store, where
   Apple takes the payment. Worth a sentence on the site.
