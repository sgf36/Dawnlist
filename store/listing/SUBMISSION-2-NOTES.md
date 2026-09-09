# Submission 2 (1.1.0) — what to paste, and what changed

Written 2026-09-09 against the listing export
`listingData-9PF25H395BB8-1152921505701840644 (2).csv`.

**The code values are deliberately not in this file. `sgf36/Dawnlist` is a
public repository.** The reviewer code is the one noted "Microsoft Store
certification reviewers" in the admin console (Settings → Admin); it has 25
uses and none are spent.

---

## 1. Notes for certification — REPLACE the existing text

The previous notes were written for 1.0.0, which had no way to redeem a code
at all. They said to paste the code into the **licence field**. In 1.1.0 that
field verifies Paddle-issued keys and refuses anything else, so a reviewer
following the old notes would be told the code was not accepted — which reads
as a broken product and is a rejection.

Paste this, with the real code substituted:

> Dawnlist is free to install. The subscription that unlocks the daily job
> feed is bought on our website through Paddle, our third-party commerce
> provider; this is declared under Product declarations. Nothing is purchased
> inside the application.
>
> To review the full product, please use this access code:
>
>     <code from the admin console>
>
> Open Dawnlist. Setup asks for two things in order — an Anthropic API key,
> then the subscription. On the subscription step, paste the code above into
> the box labelled **"Access code, if you were given one"** and press Redeem.
> Do not use the licence key field above it: that one is for a key from a
> purchase email and will refuse a code.
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

## 2. What is missing from the listing, found in the export

| Field | State | Consequence |
|---|---|---|
| `ShortDescription` | **empty in all 47 languages** | This is the line shown under the app name in Store search results. Empty means the Store falls back to truncating the long description, which begins "BEFORE YOU BUY — WHAT DAWNLIST DEPENDS ON" — a warning, as the first thing a browsing customer reads. |
| `SearchTerm1`–`7` | **empty in all 47** | Nothing but the title and description is indexed. |
| `DesktopScreenshotCaption1`–`5` | **empty in all 47** | The captions in `store/screenshots/CAPTIONS.md` were written and never uploaded. A screenshot with no caption makes the reader work out what they are looking at. |
| `ReleaseNotes` | **empty** | This is an update whose entire purpose is that the live build refuses every licence key. Users see nothing about it. |

Suggested English copy is in `SHORT-DESCRIPTION.md` and `RELEASE-NOTES.md`
beside this file. Both need translating into the other 46 before import — the
listing is fully translated for Description, Title and every Feature, and a
half-translated listing is worse than a consistent one.

---

## 3. Screenshots

**`DesktopScreenshot1` and `DesktopScreenshot2` are the same asset.**
Asset id `3038302758314086904` is used for both, so five slots show four
pictures and the first two are identical.

The repository has six, in `store/screenshots/`, in the intended order:

1. `01-shortlist.png`
2. `02-needs-review.png`
3. `03-board.png`
4. `04-understood.png`
5. `05-calibration.png`
6. `06-rules.png`

So the listing is one short as well as one duplicated. Re-upload all six in
that order, with the captions from `CAPTIONS.md`.

**None of the six is invalidated by 1.1.0.** They show the shortlist, the
contained-rule notice, the board, the ingest summary, calibration and the
screening rules — none of which changed today. The new screens (the
subscription step and the ⋯ menu) are setup, and the listing deliberately
shows the product rather than its installer.

---

## 4. Languages

The app ships **50** catalogues. The listing covers **47**.

Absent from the listing: **Javanese (`jv`), Burmese (`my`), Somali (`so`)**.

Check whether Partner Center offers those three at all before treating it as a
gap — the Store's language list is finite and shorter than ours. If it does
not offer them, nothing is wrong and no listing claim needs changing:
`Feature11` says "Fifty languages, including right-to-left", which is a
statement about the application and remains true.
