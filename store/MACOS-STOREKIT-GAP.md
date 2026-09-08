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

**Nothing in the application sells the subscription.**

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
