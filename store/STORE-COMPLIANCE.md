# Store compliance — what each storefront allows, and what it costs

**Written 2026-09-08**, after the move from a one-time purchase to a $79/month
subscription. That change is what makes this document necessary: a paid-upfront
app raises almost none of the questions below, and a subscription raises all of
them.

---

## The finding that drives everything

Apple's App Review Guideline **3.1.1** names the mechanism Dawnlist uses:

> "Apps may not use their own mechanisms to unlock content or functionality,
> such as **license keys**, augmented reality markers, QR codes,
> cryptocurrencies and cryptocurrency wallets, etc."

Dawnlist's entire non-store entitlement is a licence key, and the trial system
added on 2026-09-08 issues licence keys from override codes. **In a Mac App
Store build, both are prohibited.**

Windows is not the same, and the difference is not cosmetic. Microsoft permits
third-party commerce, so the identical build is compliant there. Do not
generalise either rule to the other store.

---

## What is already done

**The Settings screen hides the licence panel** on `store` and `mas` builds, so
there is no way to type a key in.

**That was not sufficient, and the gap is now closed.** The keyring is per
USER, not per application. Anyone who ran the direct-download build and later
installed from the Mac App Store still has a licence in their credential store,
and `build_provider` would have found it and used it — unlocking a subscription
bought outside Apple's commerce without anybody typing anything.

That is not a hypothetical path. It is what happens to exactly the people most
likely to buy from the store: the ones who tried the direct build first. A MAS
build now refuses a stored licence outright, and is tested for it.

---

## The three routes that are actually allowed

### A. Direct download only — no Apple commerce rules at all

A notarised `.dmg` distributed from the website is outside the App Store, so
3.1.1 does not apply. Licence keys, Paddle, trials by override code: all fine.

- **Available today.** The CI workflow already builds and notarises it; it
  needs certificates, not code.
- **Keeps 100% of revenue** less Paddle's ~5.6%.
- **Costs discovery.** Nobody browses a website the way they browse a store.

### B. Mac App Store with StoreKit, plus sign-in for web subscribers

Guideline **3.1.3(b), Multiplatform Services**, permits an app to let users
access a subscription bought on your website — **provided the same subscription
is also available as an in-app purchase.** So this is not a workaround; it is
the documented provision, and it is the only compliant way to serve both.

Two things it requires, and the second is a design change rather than a
setting:

1. **A StoreKit subscription** at the same price, sold inside the app.
2. **Account sign-in, not a licence key.** The prohibited thing is the
   *mechanism*. An account the user signs into is accepted; a key they paste is
   named in the guideline. Serving web subscribers on macOS therefore means
   building accounts — which the product does not currently have, by design,
   because it has deliberately avoided holding user records.

- Apple takes **15%** under the Small Business Programme (under $1M/year).
- At $79 that is $67.15 net against Paddle's $74.55 — break-even moves from
  **18 subscribers to 20**. Affordable, not free.

### C. US storefront external links

Since May 2025, apps on the **United States storefront** may include buttons or
links to external payment. Useful, and not a solution: it is one storefront,
and the product is sold in fifty locales.

---

## Microsoft: allowed, but it must be declared

Microsoft permits third-party commerce and takes **0%** on it. Two obligations
come with it:

- The product must **identify the commerce provider, authenticate the user, and
  obtain confirmation at the time of the transaction** or when payment details
  are collected.
- The developer must **tick the third-party-purchase box in Partner Center**.
  Using third-party commerce without declaring it is the compliance failure,
  not the commerce itself.

Dawnlist does not transact inside the app at all — the subscription is bought
on the website and the licence arrives by email — so the first obligation is
largely moot. **The Partner Center declaration is still required.** Add it to
the submission checklist.

---

## Two risks that are not about payment

**The app is unusable without a paid third-party account.** Dawnlist requires
the buyer's own Anthropic key. App Review has historically been uneasy about
apps that cannot function without an external paid service, and this is a
genuine rejection risk independent of anything above. Mitigations: state it
plainly in the description (done), on the site before signup (done), and in the
app before the key is requested (done). It cannot be engineered away without
abandoning bring-your-own-key, which exists for data-protection reasons.

**The trial mechanism is a licence key.** Override codes are fine on Windows
and on direct download, and are prohibited in a MAS build for the same 3.1.1
reason. A Mac App Store trial must be a StoreKit introductory offer.

---

## Recommendation

**Ship macOS as direct download first.** It is already built, it is compliant
by construction, and it needs no accounts, no StoreKit and no 15%.

**Treat the Mac App Store as a later, separate project** — because route B is
not a packaging change, it is accounts plus StoreKit plus a second entitlement
path. Doing it badly is worse than not doing it.

**Windows can ship as designed**, with the Partner Center declaration ticked
and the store-subscription blocker in `microsoft-store-listing.md` resolved
first.
