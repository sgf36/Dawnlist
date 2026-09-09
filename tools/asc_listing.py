"""Fill the Mac App Store listing metadata. Idempotent — safe to re-run.

    .venv/Scripts/python tools/asc_listing.py

THE MAC COPY IS NOT THE MICROSOFT COPY, and reusing it would be a mistake with
teeth. The Store description says "Dawnlist is a subscription, bought on our
website" — true on Windows, where Microsoft permits third-party commerce.
Saying that on the Mac App Store would describe a licence-key purchase outside
Apple's commerce, which guideline 3.1.1 forbids and which this build cannot do
anyway: the MAS variant has no way to accept a key and buys through StoreKit.

So the two descriptions differ at the commerce paragraph, deliberately, and
neither should be copied over the other.

WHAT IS DELIBERATELY ABSENT
---------------------------
whatsNew. Apple REJECTS release notes on a version that has never been
released — the Wren tooling records the same thing, and its push script drops
the field for exactly this reason.

Keywords are 100 characters INCLUDING commas and Apple counts them strictly.
Do not pad with spaces after commas; each one costs a character that could
have been part of a term.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.asc import APP, call, errs  # noqa: E402

SUPPORT_URL = "https://dawnlist.spencerfields.com"
MARKETING_URL = "https://dawnlist.spencerfields.com"
PRIVACY_URL = "https://dawnlist.spencerfields.com/privacy.html"

#: 30 characters maximum. Shown under the name on the product page.
SUBTITLE = "Judged job leads, every day"

#: 100 characters INCLUDING the commas.
KEYWORDS = ("job search,jobs,career,job tracker,applications,job alerts,"
            "hiring,recruitment,CV,resume")

#: 170 characters maximum.
PROMOTIONAL = ("Reads the world's job feeds each morning, judges every posting "
               "against a brief it builds with you, and drafts your follow-ups "
               "for you to send yourself.")

DESCRIPTION = """BEFORE YOU SUBSCRIBE — WHAT DAWNLIST DEPENDS ON

Dawnlist uses generative AI to read job postings, to judge them against your brief, and to draft your messages. It runs on your own Anthropic API key. You will need one, and Anthropic bills you directly for what Dawnlist reads — typically a few pounds a month at ordinary use. Creating a key takes a couple of minutes at console.anthropic.com. Dawnlist takes no cut and adds no markup. If you would rather not hold an API key, this app is not for you.

Dawnlist reads job feeds every morning, judges every posting against a fit brief it builds with you, and hands you a ranked shortlist before you have finished your coffee.

It never sends anything. Every message it writes is a draft you open and send from your own mail app. That is a deliberate boundary, not a limitation: you stay the person who decides what goes out under your name.


WHAT IT DOES

A shortlist every morning
Dawnlist sweeps job feeds, removes what you have already seen or already turned down, and reads what is left. You get a ranked shortlist with reasons — and, unusually, the rejections stay visible too, each with the reason it was set aside. Nothing disappears quietly.

It shows its working
Every run shows the whole funnel: how many postings were swept, deduplicated, filtered, screened and assessed. A count is never shown without what it excludes, so you can always see how a shortlist of four came from a sweep of a thousand.

It learns your judgement, not just your keywords
Before the first run, Dawnlist shows you ten live postings and its verdict on each. Where you disagree, you tell it what sentence would have got it right, and that sentence goes into your brief. Every correction afterwards does the same. This is the part that makes it yours rather than generic.

A tracker that reflects reality
Every company you pursue, with its stage, an evidence log of what was actually sent, and what is due next. Follow-up dates are computed from the touches you have actually recorded — never from a stale field — and never land on a Monday or a Friday.

Drafts in your voice, in your language
Dawnlist writes follow-ups in the register you actually use, learned from your own sent messages that you add yourself. It works in fifty languages. And every factual claim comes from a background factsheet built from your own CVs; where the evidence does not support a claim, it leaves a visible gap rather than inventing something.


WHAT IT DOES NOT DO

It does not send. There is no sending code in the app at all.
It does not read your mailbox. No passwords, no inbox access, nothing to connect.
It does not apply on your behalf, or fill in forms, or message anyone for you.
It does not scrape job boards.


HOW YOUR DATA IS HANDLED

Your CVs, your brief and your tracker live on your own Mac.

Job descriptions and your fit brief are sent to Anthropic's API to be assessed, under YOUR OWN API key — so that traffic is between you and Anthropic, and Dawnlist is not a party to it. Job searches go through Dawnlist's feed service, which records usage counts and never content.


REPORTING WHAT DAWNLIST WRITES

Because Dawnlist's verdicts and drafts are generated by AI, there is a route to tell us when it produces something inappropriate, offensive or plainly wrong. Open Settings and use "Report AI-generated content". A report is written by a person, not sent by the app — describing what happened is enough.


WHAT YOU PAY FOR

One subscription, and the whole app. Nothing is held back and no feature is locked behind a second payment. The job feed is included, and searching costs real money every day Dawnlist runs, which is what the subscription pays for.

The reading and drafting are separate, and you pay Anthropic directly on your own API key. You hold the key, you see the usage, and you can revoke it at any moment. Dawnlist never sees that bill and takes no share of it."""


def put(kind, rid, attrs):
    st, d = call("PATCH", f"{kind}/{rid}",
                 {"data": {"type": kind, "id": rid, "attributes": attrs}})
    if st not in (200, 201):
        sys.exit(f"{kind} -> {st}: {errs(d)}")
    return d["data"]["attributes"]


def main() -> int:
    if len(DESCRIPTION) > 4000:
        sys.exit(f"description is {len(DESCRIPTION)}; Apple's limit is 4000")
    if len(KEYWORDS) > 100:
        sys.exit(f"keywords are {len(KEYWORDS)}; Apple's limit is 100")
    if len(SUBTITLE) > 30:
        sys.exit(f"subtitle is {len(SUBTITLE)}; Apple's limit is 30")
    if len(PROMOTIONAL) > 170:
        sys.exit(f"promotional text is {len(PROMOTIONAL)}; limit is 170")

    st, d = call("GET", f"apps/{APP}/appStoreVersions?limit=5")
    vid = d["data"][0]["id"]
    st, loc = call("GET", f"appStoreVersions/{vid}/appStoreVersionLocalizations")
    lid = loc["data"][0]["id"]
    a = put("appStoreVersionLocalizations", lid, {
        "description": DESCRIPTION,
        "keywords": KEYWORDS,
        "promotionalText": PROMOTIONAL,
        "supportUrl": SUPPORT_URL,
        "marketingUrl": MARKETING_URL,
    })
    print(f"version localisation  {a.get('locale')}")
    print(f"  description   {len(a.get('description') or '')} chars")
    print(f"  keywords      {a.get('keywords')}")
    print(f"  support       {a.get('supportUrl')}")

    st, ai = call("GET", f"apps/{APP}/appInfos")
    aid = ai["data"][0]["id"]
    st, ail = call("GET", f"appInfos/{aid}/appInfoLocalizations")
    ailid = ail["data"][0]["id"]
    b = put("appInfoLocalizations", ailid, {
        "subtitle": SUBTITLE,
        "privacyPolicyUrl": PRIVACY_URL,
    })
    print(f"app info              {b.get('locale')}")
    print(f"  name          {b.get('name')}")
    print(f"  subtitle      {b.get('subtitle')}")
    print(f"  privacy       {b.get('privacyPolicyUrl')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
