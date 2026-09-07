# Microsoft Store listing — Dawnlist — Job Search

Paste-ready copy for Partner Center. British English throughout, no Oxford
commas, no abbreviations, and the title takes no full stop.

**Every claim here is about the product, not about Spencer.** Nothing on this
page asserts anything from the background factsheet, because a store listing is
not outreach and does not need to.

**LinkedIn is not named anywhere, deliberately.** The product contains no
LinkedIn code and the marketing must not imply otherwise.

---

## Identity

| Field | Value |
|---|---|
| Product name | Dawnlist — Job Search |
| Package identity | `SFields.Dawnlist` *(confirm against what Partner Center actually reserved, and correct `packaging/msix/AppxManifest.xml` if it differs — a mismatch fails ingestion, not certification)* |
| Category | Productivity |
| Subcategory | Personal finance & productivity → Productivity |
| Pricing | **Paid, one price, up front.** No in-app purchase and no free tier. **Bring-your-own-key:** the buyer supplies an Anthropic API key and is billed by Anthropic directly. |

---

## Short description
*(Max 200 characters. Used in search results and tiles.)*

> Reads the world's job feeds every morning, judges every posting against a fit brief it builds with you, and writes your follow-up emails as drafts. It never sends anything — you do.

*(190 characters.)*

---

## Description
*(Max 10,000 characters. Plain text; Partner Center strips most markup.)*

```
Dawnlist is a paid app. You buy it once, and it is yours — there is nothing to
unlock inside it and nothing held back.

BEFORE YOU BUY: Dawnlist uses your own Anthropic API key to read job postings
and draft messages. You will need one, and Anthropic bills you directly for
what Dawnlist reads — typically a few pounds a month at normal use. Creating a
key takes a couple of minutes at console.anthropic.com. Dawnlist takes no cut
and adds no markup. If you would rather not hold an API key, this app is not
for you.

Dawnlist reads the world's job feeds every morning, judges every posting against
a fit brief it builds with you, and hands you a ranked shortlist before you have
finished your coffee.

It never sends anything. Every message it writes is a draft you open and send
from your own mail app. That is a deliberate boundary, not a limitation: you
stay the person who decides what goes out under your name.


WHAT IT DOES

A shortlist every morning
Dawnlist sweeps job feeds, removes what you have already seen or already
turned down, and reads what is left. You get a ranked shortlist
with reasons — and, unusually, the rejections are visible too, each with the
reason it was set aside. Nothing disappears quietly.

It shows its working
Every run shows the whole funnel: how many postings were swept, deduplicated,
filtered, screened and assessed. A count is never shown without what it
excludes, so you can always see how a shortlist of four came from a sweep of a
thousand.

It learns your judgement, not just your keywords
Before the first run, Dawnlist shows you ten live postings and its verdict on
each. Where you disagree, you tell it what sentence would have got it right, and
that sentence goes into your brief. Every correction afterwards does the same.
This is the part that makes it yours rather than generic.

A tracker that reflects reality
Every company you pursue, with its stage, an evidence log of what was actually
sent, and what is due next. Follow-up dates are computed from the touches you
have actually recorded — never from a stale field — and never land on a Monday
or a Friday.

Drafts in your voice, in your language
Dawnlist writes follow-ups in the register you actually use, learned from your
own sent messages that you add yourself. It works in fifty languages. And every
factual claim comes from a background factsheet built from your own CVs; where
the evidence does not support a claim, it leaves a visible gap rather than
inventing something.


WHAT IT DOES NOT DO

It does not send. There is no sending code in the app at all.
It does not read your mailbox. No passwords, no inbox access, nothing to connect.
It does not apply on your behalf, or fill in forms, or message anyone for you.
It does not scrape job boards.

If you want an app that fires off a hundred applications while you sleep, this
is not it. Dawnlist is for people who want fewer, better-judged approaches, and
who intend to stand behind every one.


HOW YOUR DATA IS HANDLED

Your CVs, your brief and your tracker live on your own machine.

Job descriptions and your fit brief are sent to Anthropic's API to be assessed,
under YOUR OWN API key — so that traffic is between you and Anthropic, and
Dawnlist is not a party to it. Job searches go through Dawnlist's feed service,
which records usage counts and never content.

Full detail is in the privacy policy.


GETTING STARTED

Setting up takes about half an hour: add your CVs, answer some questions about
what you are looking for, then correct Dawnlist on ten real postings. After
that it runs every morning.


WHAT YOU PAY FOR

One purchase, and the whole app. No subscription inside it, nothing locked
behind a second payment, and no features held back from the version you bought.

Separately, you pay Anthropic for what Dawnlist reads, on your own API key. At
about forty postings a day that is roughly two pounds a month for the reading
and about one for the drafting, plus a one-off two to four pounds when you first
set up your factsheet. You hold the key, you see the usage, and you can revoke
it at any moment. Dawnlist never sees a bill and takes no share of one.

Why it works this way: reading is the cost that grows with how hard you use the
app, and it belongs with the person doing the using. Charging everyone a
subscription to cover the heaviest users would mean the lightest users
subsidised them.
```

---

## Features
*(Partner Center takes up to 20 short bullets.)*

1. A ranked shortlist every morning, with the reasons stated
2. Rejections stay visible, each with why it was set aside
3. The full funnel on screen — swept, deduplicated, filtered, screened, assessed
4. Learns your judgement from corrections, not just keywords
5. Reads job feeds, not scraped pages
6. Add job-alert emails yourself for anything the feeds miss
7. Tracks every company you pursue, with an evidence log
8. Follow-up dates computed from what was actually sent
9. Never schedules a message for a Monday or a Friday
10. Writes follow-ups as drafts in your own register
11. Fifty languages, including right-to-left
12. Claims come from your own CVs, and gaps are shown rather than filled
13. Never sends anything — you send it yourself
14. No mailbox access and no passwords to hand over
15. Your CVs and tracker stay on your machine
16. One purchase, the whole app — no in-app unlocks
17. Uses your own Anthropic key — you hold it, you see the usage

---

## Search terms
*(Max 7, 30 characters each. Not shown to users.)*

```
job search
job tracker
job applications
career search
job alerts
application tracker
job hunt
```

---

## Screenshots

Minimum one, up to ten. **1366 × 768 or larger, 16:9.** Take them from the real
app, not a mockup — and open every one at full size before uploading. A passing
export proves the file was written, nothing more.

| # | File | Caption |
|---|---|---|
| 1 | `01-shortlist.png` | Your shortlist, with the reasons — and the rejections still visible |
| 2 | `02-needs-review.png` | When a rule reaches too far, it tells you |
| 3 | `03-board.png` | Every company you are pursuing, and what is due next |
| 4 | `04-understood.png` | It reads your CVs and shows you what it understood |
| 5 | `05-calibration.png` | It learns your judgement before it runs |
| 6 | `06-rules.png` | Screening you control, and it refuses a rule that would hide a real role |

`tools/render_store.py` produces all six in one pass, into `store/screenshots/`
at **2135×1200** — 16:9, comfortably above the 1366×768 minimum. Captions are
repeated in `store/screenshots/CAPTIONS.md` so the upload order is unambiguous.

**There is deliberately no screenshot of a draft**, though an earlier version of
this table promised one. Drafts are `.eml` files opened in the user's own mail
client, so there is no Dawnlist screen to photograph — and the instruction above
says take them from the real app. A mockup would have advertised a screen that
does not exist.

`review-window-ar.png` is proof that right-to-left mirrors the whole window —
it is not a listing asset, and the listing is en-GB.

---

## Age rating

The questionnaire is short for this one. Every answer is "no": no user-generated
content shared with others, no in-app purchases of chance, no violence, no
profanity, no data shared with third parties for advertising.

Answer the data question honestly: **personal information is collected and
transmitted** — CVs and job preferences go to the assessment API. It does not
raise the rating, and answering it wrongly is a compliance problem rather than
a rating one.

---

## Privacy policy

Required, and the URL must be live before submission. It must state plainly:

- what leaves the machine (job descriptions, the fit brief, the factsheet — to
  Anthropic's API for assessment; search terms — to the feed service);
- what does not (CVs, the tracker, drafts, decisions — all local);
- what is retained (the feed service records usage counts, never content — and
  the Worker must actually behave that way);
- that no data is sold, and none is used for advertising;
- how to export and delete everything.

Host it at `dawnlist.spencerfields.com/privacy` alongside the marketing site.

---

## Claims audit

Every line above was checked against what the app actually does, on
2026-09-07. Two were not true when checked and are fixed in code rather than
softened in copy: "add job-alert emails yourself" had no route into the app,
and "shows you ten live postings" could not happen because the calibration
sample was a placeholder.

**"195 countries" is removed** and not replaced with another number. It came
from a provider's marketing, the feed tier is not bought, and the licensing
answer that decides between an API and a self-hosted index has not landed — so
any figure stated now is a guess about somebody else's product. Put a number
back only when the contract supports it.

One claim to keep an eye on: *"learned from your own sent messages that you add
yourself"*. True — Dawnlist reads `.eml`, `.txt` and `.md` files from the voice
folder beside the database, and `--doctor` prints the path — but there is no
drag-and-drop for it, so in practice only a user who goes looking will find it.
Honest as written; thin as an experience.

---

## Before submitting

- [ ] Confirm the reserved identity matches `AppxManifest.xml` exactly
- [ ] Create `app/resources/store_build.flag`, then rebuild — otherwise you are
      shipping the direct-download variant to the Store
- [ ] Rebuild the MSIX and check `--doctor` reports 50 of 50 catalogues
- [ ] Sign the package (self-signed is enough; the Store re-signs on publish)
- [ ] Run WACK and clear everything it raises
- [ ] Privacy policy live at the URL given
- [ ] Re-run the free trademark first-pass — a clean result has a shelf life
