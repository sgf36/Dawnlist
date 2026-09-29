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
PROMOTIONAL = ("Reads job feeds each morning, judges every posting against a "
               "brief it builds with you, and drafts follow-ups for you to "
               "send yourself.")

DESCRIPTION = """BEFORE YOU SUBSCRIBE — WHAT DAWNLIST DEPENDS ON

Dawnlist uses AI to read job postings, judge them against your brief, and draft messages. It runs on your own Anthropic API key — you will need one. Anthropic bills you directly, typically a few pounds a month. Create a key at console.anthropic.com. If you would rather not hold an API key, this app is not for you.

It never sends anything. Every message is a draft you send from your own mail app. You decide what goes out under your name.


WHAT IT DOES

A shortlist every morning
Dawnlist sweeps job feeds, removes what you have seen or turned down, and reads the rest. You get a ranked shortlist with reasons. Rejections stay visible too, each with its reason. Nothing disappears quietly.

More sources than feeds alone
Paste a LinkedIn URL and Dawnlist pulls in the posting. The query generator builds boolean search strings — combine keywords, titles and exclusions into one expression.

It shows its working
Every run shows the whole funnel: swept, deduplicated, filtered, screened, assessed. A count is never shown without what it excludes.

It learns your judgement, not just your keywords
Before the first run, Dawnlist shows ten live postings and its verdict on each. Where you disagree, you say what sentence would have got it right, and it goes into your brief. Every correction afterwards does the same.

A tracker that reflects reality
Every company you pursue, with its stage, what was sent, and what is due next. Follow-up dates come from recorded touches, never a stale field, and never land on a Monday or a Friday.

Drafts in your voice, in your language
Dawnlist writes follow-ups in your own register, learned from sent messages you add yourself. It works in fifty languages. Every claim comes from a factsheet built from your CVs; where evidence is lacking, it leaves a gap rather than inventing.


WHAT IT DOES NOT DO

It does not send. There is no sending code in the app.
It does not read your mailbox. No passwords, no inbox access.
It does not apply on your behalf or fill in forms.
It does not scrape job boards.


HOW YOUR DATA IS HANDLED

Your CVs, brief and tracker live on your own Mac. Job descriptions go to Anthropic's API under YOUR OWN key — traffic is between you and Anthropic. Job searches go through Dawnlist's feed service, which records usage counts, never content.


REPORTING WHAT DAWNLIST WRITES

Dawnlist's verdicts and drafts are AI-generated. To report something inappropriate or wrong, open Settings and use "Report AI-generated content".


WHAT YOU PAY FOR

One subscription, the whole app. Nothing held back, no second payment. The job feed is included.

Reading and drafting are separate — you pay Anthropic on your own key. You hold it, see the usage, and can revoke it at any moment. Dawnlist never sees that bill."""


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
