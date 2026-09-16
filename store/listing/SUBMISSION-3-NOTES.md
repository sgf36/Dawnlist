# Submission 3 (1.2.0) — what to paste, and what changed

Written 2026-09-16. The commerce model has changed since submission 2:
Paddle rejected the Dawnlist domain on 2026-09-11, so this build (`store_iap`)
sells through the **Microsoft Store's own subscription add-on** (9P0THQRBKFPF,
£79/month). The third-party commerce declaration is no longer the primary
purchase mechanism — though the access-code path still exists for reviewer
and complimentary access.

**The code values are deliberately not in this file. `sgf36/Dawnlist` is a
public repository.** The reviewer code is the one noted "Microsoft Store
certification reviewers" in the admin console (Settings → Admin).

---

## 1. Notes for certification — REPLACE the existing text

Paste this, with the real code substituted:

> Dawnlist is free to install. The subscription that unlocks the daily job
> feed is available as an in-app purchase through the Microsoft Store (add-on
> "Dawnlist", priced per month).
>
> To review the full product without purchasing, please use this access code:
>
>     <code from the admin console>
>
> Open Dawnlist. Setup asks for two things in order — an Anthropic API key,
> then the subscription. On the subscription step, scroll down to the box
> labelled **"Access code, if you were given one"** and paste the code above,
> then press Redeem. Do not use the Subscribe button above it unless you wish
> to test the purchase flow.
>
> The code may be used up to 25 times, so it still works if you test on more
> than one machine or re-test after a rejection.
>
> Three things worth knowing before you test:
>
> 1. **Dawnlist runs on the reviewer's own Anthropic API key**, and setup will
>    not continue without one. This is deliberate and is stated before
>    purchase on our website and on the first screen of the app: reading job
>    postings and drafting messages happen on the customer's own Anthropic
>    account, so their CV and correspondence never reach our servers. If
>    obtaining a key is a problem for the review, please contact us and we
>    will arrange one.
> 2. **The application never sends email and never submits an application on
>    anyone's behalf.** It writes draft files which the person opens and sends
>    themselves. There is no mail transport in the product and no credential
>    that could grow one.
> 3. **The first morning may be quiet.** The app fetches job postings from a
>    paid feed and only pulls searches the user has switched on. Switching one
>    on during setup is what makes the shortlist fill.
>
> Support: Apps@spencerfields.com

---

## 2. What changed from the previous submission

- **Commerce model.** The subscription is now bought through the Microsoft
  Store's own add-on, not through a third-party provider. The third-party
  commerce declaration may still be declared (for the access-code path that
  redeems against our own server), but the primary purchase is a Store
  subscription.
- **Version.** 1.2.0.0 (was 1.1.1.0).
- **New features.** CSV import/export, criteria guide export, test connection,
  keyboard shortcuts, last-run dates, search-type selection, description
  keywords, onboarding progress bars.

---

## 3. Release notes

Updated in the listing CSV for all languages. The English text:

> Import and export your searches as a spreadsheet, so you can build a set in
> a CSV file and load them all at once rather than typing each one.
>
> Export your fit criteria as a formatted document, edit it outside the app
> and reimport it — useful when somebody else is helping you shape what you
> are looking for.
>
> Test your connection from Settings without waiting for the morning run. One
> button checks your API key and feed access and tells you immediately
> whether everything is working.
>
> Each search now shows when it last ran, so you can see at a glance which
> ones are active and which have gone quiet.
>
> Keyboard shortcuts on the calibration screen: P to pursue, L for later, R
> to reject, J and K to move between postings, 1 through 4 to switch tabs.
>
> Searches can now match on the job title, the description or both, and you
> can add description keywords to narrow what comes back.
>
> Setup now shows a progress bar, so you know how far through the steps you
> are.

---

## 4. Screenshots

Seven screenshots uploaded — the six from submission 2 plus a new
settings screenshot (07-settings) showing CSV import/export, search types,
description keywords and the test-connection button. Localised sets rendered
for de, es, fr, hi, ja, zh.
