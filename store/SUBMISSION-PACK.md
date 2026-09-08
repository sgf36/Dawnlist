# Microsoft Store submission — everything Partner Center will ask for

**Written 2026-09-08.** One page, in the order the Partner Center forms ask.
`microsoft-store-listing.md` holds the copy and the reasoning; this is the
walk-through, so you are not reading a 460-line document with a browser open.

---

## Before you start: why this can be submitted before the terms are settled

The solicitor question and the fourteen-day cancellation right gate the **first
sale**. They do not gate this submission, and the difference is worth being
precise about rather than cautious about:

**The Store app is FREE.** Nothing is bought in it. Commerce happens on the
website, through Paddle, under terms that are not yet in force — and until they
are, nothing is sold anywhere. A free listing that hands a reviewer a trial
code sells nobody anything.

Store certification takes days. Running it in parallel with the legal work
costs nothing and saves a week. **What must NOT happen is the listing going
live while purchasing is open and the terms are still draft** — and that cannot
happen by accident, because purchasing opens on the website, which is a
separate deliberate act.

---

## 1. The package

`dist/Dawnlist.msix`, built from current `master` and signed.

Signed with a throwaway self-signed certificate whose subject matches the
manifest's `Publisher` exactly. That is correct rather than a shortcut: the
Store strips the signature and re-signs on publish, so a purchased certificate
would pay for a signature no customer ever sees. What must be exact is the
subject, and `sign_msix.ps1` reads it from the manifest rather than repeating
it, then verifies it afterwards.

**Run WACK before uploading.** Elevated PowerShell:

```powershell
pwsh -File C:\Users\SpencerFields\dawnlist\packaging\run_wack.ps1
```

It must say `OVERALL RESULT: PASS` **and** the report must be newer than the
run. The script now enforces that itself — on 2026-09-08 it reported a PASS
from a stale report for a run that never happened, and three guards were added
so it cannot do that again.

---

## 2. Identity — do not change any of this

| Field | Value |
|---|---|
| Package identity | `SFields.DawnlistJobSearch` |
| Publisher | `CN=A7D4B6C0-27D4-4F66-82EB-82F5DD466788` |
| Publisher display name | `SFields` |
| Store ID | `9PF25H395BB8` |
| Package family name | `SFields.DawnlistJobSearch_qhnp6qavahs3g` |

Confirmed live against Partner Center on 2026-09-08. **The identity is the one
irreversible field in this whole process**: a display name can be changed after
publish, an identity cannot, and getting it wrong means the app can only ever be
replaced, never updated.

---

## 3. Pricing and availability

**Base price: FREE.**

**Do NOT create a subscription add-on.** This is the decision that unblocked the
submission: the subscription is bought on the website through Paddle, and the
licence a Store customer holds is the same one a direct-download customer
holds. Microsoft permits third-party commerce and takes **0%** on it.

**Markets:** all, unless there is a reason to exclude one.

---

## 4. Properties

| Field | Value |
|---|---|
| Category | Productivity |
| Privacy policy URL | `https://dawnlist.spencerfields.com/privacy.html` |
| Website | `https://dawnlist.spencerfields.com` |
| Support contact | `Apps@spencerfields.com` |

**The privacy policy URL is not optional and Partner Center checks it
resolves.** It is live, returns 200 and is indexed.

### The declaration that is easy to miss

Under **Properties → Product declarations**, tick the box declaring that the
product uses **a third-party commerce platform / sells through a
non-Microsoft purchase mechanism**.

Microsoft permits third-party commerce at 0%. **Using it without declaring it
is the compliance failure — the commerce itself is fine.** This is the single
most likely cause of a rejection on this submission, because everything else is
either mechanical or already checked.

---

## 5. Age ratings

Complete the IARC questionnaire. Dawnlist has no user-generated content shared
between users, no chat, no advertising, no gambling, no in-app purchase. It
should land at the lowest rating.

---

## 6. Store listing

Copy is in `store/microsoft-store-listing.md` — short description, full
description, features and search terms, all written and audited against the
claims the product can actually support.

**Screenshots:** six, in `store/screenshots/`, captions in `CAPTIONS.md`.

---

## 7. Submission notes to the certification team

**This is the field that decides whether review passes.** A reviewer installs a
free app which then asks for a licence key. Without a code they see a product
that does nothing, and that is a rejection.

Paste this:

> Dawnlist is free to install. The subscription that unlocks the job feed is
> purchased on our website through Paddle, our third-party commerce provider;
> this is declared under Product declarations. Nothing is purchased inside the
> application.
>
> To review the full product, please use this licence key:
>
>     DL-XXXX-XXXX-XXXX-XXXX
>
> Open the app, go to Settings, and paste it into the licence field. The code
> may be used up to 25 times, so it will still work if you test on more than
> one machine or re-test after a rejection.
>
> Two things worth knowing before you test:
>
> 1. The application also asks for an Anthropic API key. This is deliberate and
>    is stated before purchase on our website and in the app: reading job
>    postings and drafting messages happen on the customer's own Anthropic
>    account, so their CV and correspondence never reach our servers. Without a
>    key the application will explain what it needs rather than fail.
> 2. The application never sends email or submits applications on anyone's
>    behalf. It writes draft files which the person opens and sends themselves.
>
> Support: Apps@spencerfields.com

**The code is `DL-XXXX-XXXX-XXXX-XXXX`** — minted 2026-09-08, 25 uses, standard
plan, 700 postings a day. Deliberately not single-use: a reviewer may test on
several machines or re-test after a rejection, and a spent code turns that into
a failed review.

---

## 8. After submitting

- **Do not publish the Store link anywhere** until the listing is live. A badge
  pointing at nothing is worse than no badge. The website session is holding
  it.
- Certification usually takes 24–72 hours. A rejection comes with a reason and
  is usually a form field rather than the package.
- **When it passes, do not open purchasing until the terms are in force and
  acceptance is being recorded.** Those are separate acts on the website, and
  they are what the solicitor question actually gates.
