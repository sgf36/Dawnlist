"""Is the Mac App Store version actually submittable? Read-only — changes nothing.

    .venv/Scripts/python tools/asc_readiness.py

WHY THIS EXISTS. Three separate times, a requirement that was simply absent read
as fine, because App Store Connect's API **404s or returns an empty relationship
rather than saying "not set"**:

  * `subscriptionAvailability` 404d before it was ever created (2026-09-07).
  * The subscription sat at MISSING_METADATA for hours with localisation,
    availability, review note and review screenshot all present — it was
    **priced in 1 territory out of 175**, and a price schedule that is merely
    short reads exactly like one that is complete (2026-09-09).
  * `appPriceSchedule` and `appStoreReviewDetail` had never been created at all,
    and neither announces itself anywhere in the interface (2026-09-09).

So this does not ask "is the field present". It **counts things against each
other** — price rows against available territories — and treats a 404 as
MISSING rather than as an error to shrug at.

WHAT IT CANNOT SEE, AND WILL SAY SO. The App Privacy labels (`appDataUsages`)
are not exposed by the API at any role, and the Paid Applications Agreement is
not readable either. Both are browser-only, and a green run here does NOT mean
those are done.
"""
from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tools.asc import APP, call  # noqa: E402

SUBSCRIPTION = "6809984690"

OK, BAD, WARN = "  OK   ", "MISSING", " WARN  "
_bad = 0


def report(state: str, label: str, detail: str = "") -> None:
    global _bad
    if state is BAD:
        _bad += 1
    print(f"[{state}] {label}" + (f" — {detail}" if detail else ""))


def total_of(payload: dict, relationship: str) -> int | None:
    """The paging total Apple hides inside a relationship's meta."""
    rel = (payload.get("data", {}) or {}).get("relationships", {}) or {}
    meta = (rel.get(relationship, {}) or {}).get("meta") or {}
    return (meta.get("paging") or {}).get("total")


def main() -> int:
    st, v = call("GET", f"apps/{APP}/appStoreVersions?limit=1")
    if not v.get("data"):
        sys.exit("no app store version found")
    ver = v["data"][0]
    vid = ver["id"]
    print(f"version {ver['attributes'].get('versionString')} "
          f"({ver['attributes'].get('appStoreState')})\n")

    # WHICH BUILD, NOT WHETHER ONE. This asked only "is a build attached",
    # which on 2026-09-09 reported OK while version 1.1.0 carried build 53 —
    # the morning's build, with no StoreKit in it, no subscription step in
    # setup, and a licence check that refused every key. Five newer builds had
    # been uploaded since; attaching a build is a separate act from uploading
    # one, and nothing had performed it.
    #
    # App Review installs whatever is attached. "Not empty" was not the
    # question and never had been.
    st, b = call("GET", f"appStoreVersions/{vid}/build")
    attached = (b.get("data") or {}).get("id")
    report(OK if attached else BAD, "build attached to the version")

    if attached:
        st, allb = call("GET", f"builds?filter[app]={APP}&limit=20&sort=-version")
        builds = [x for x in allb.get("data", [])
                  if x["attributes"].get("processingState") == "VALID"]
        newest = builds[0] if builds else None
        this = next((x for x in builds if x["id"] == attached), None)
        label = (this or {}).get("attributes", {}).get("version", "?")
        if newest and newest["id"] != attached:
            report(BAD, f"attached build is {label}, but "
                        f"{newest['attributes'].get('version')} is newer and "
                        f"VALID — App Review installs what is attached")
        else:
            report(OK, f"attached build is the newest VALID one ({label})")

    st, loc = call("GET", f"appStoreVersions/{vid}/appStoreVersionLocalizations")
    if loc.get("data"):
        a = loc["data"][0]["attributes"]
        for f in ("description", "keywords", "supportUrl"):
            report(OK if a.get(f) else BAD, f"version localisation: {f}")
        lid = loc["data"][0]["id"]
        st, sets = call("GET", f"appStoreVersionLocalizations/{lid}/appScreenshotSets")
        shots = 0
        for s in sets.get("data", []):
            st2, sh = call("GET", f"appScreenshotSets/{s['id']}/appScreenshots")
            shots += len(sh.get("data", []))
        report(OK if shots else BAD, "screenshots", f"{shots} image(s)")
    else:
        report(BAD, "version localisation")

    st, infos = call("GET", f"apps/{APP}/appInfos")
    if infos.get("data"):
        iid = infos["data"][0]["id"]
        ia = infos["data"][0]["attributes"]
        report(OK if ia.get("appStoreAgeRating") else BAD, "age rating",
               str(ia.get("appStoreAgeRating")))
        st, c = call("GET", f"appInfos/{iid}/primaryCategory")
        cid = (c.get("data") or {}).get("id")
        report(OK if cid else BAD, "primary category", str(cid))
        st, il = call("GET", f"appInfos/{iid}/appInfoLocalizations")
        pp = (il.get("data") or [{}])[0].get("attributes", {}).get("privacyPolicyUrl")
        report(OK if pp else BAD, "privacy policy URL")

    st, ap = call("GET", f"apps/{APP}")
    report(OK if ap["data"]["attributes"].get("contentRightsDeclaration") else BAD,
           "content rights declaration")

    # Never created = 404. Treat that as missing, not as a transport error.
    st, ps = call("GET", f"apps/{APP}/appPriceSchedule")
    report(OK if st == 200 and ps.get("data") else BAD,
           "app price schedule", "404 = never set" if st != 200 else "")

    st, av = call("GET", f"apps/{APP}/appAvailabilityV2"
                         f"?include=territoryAvailabilities&limit[territoryAvailabilities]=1")
    app_terrs = total_of(av, "territoryAvailabilities") if st == 200 else None
    report(OK if app_terrs else BAD, "app territory availability",
           f"{app_terrs} territories" if app_terrs else "404 = never set (Console-only)")

    st, rd = call("GET", f"appStoreVersions/{vid}/appStoreReviewDetail")
    if rd.get("data"):
        ra = rd["data"]["attributes"]
        missing = [f for f in ("contactFirstName", "contactLastName",
                               "contactEmail", "contactPhone") if not ra.get(f)]
        report(OK if not missing else BAD, "App Review contact block",
               "missing " + ", ".join(missing) if missing else "")
        notes = ra.get("notes") or ""
        # The reviewer cannot finish onboarding without an Anthropic key, so a
        # placeholder left in the notes is a guideline 2.1 rejection waiting.
        if "PASTE KEY HERE" in notes:
            report(BAD, "review notes still contain the API key placeholder")
    else:
        report(BAD, "App Review contact block", "never created")

    # -- the subscription, and the count that actually matters ---------------
    st, s = call("GET", f"subscriptions/{SUBSCRIPTION}?fields[subscriptions]=state")
    state = s.get("data", {}).get("attributes", {}).get("state")
    report(OK if state == "READY_TO_SUBMIT" else BAD, "subscription state", str(state))

    st, pr = call("GET", f"subscriptions/{SUBSCRIPTION}/prices?limit=200")
    priced = ((pr.get("meta") or {}).get("paging") or {}).get("total")
    st, sav = call("GET", f"subscriptions/{SUBSCRIPTION}/subscriptionAvailability"
                          f"?include=availableTerritories&limit[availableTerritories]=1")
    sub_terrs = total_of(sav, "availableTerritories") if st == 200 else None
    same = priced is not None and priced == sub_terrs
    report(OK if same else BAD, "subscription priced in every available territory",
           f"{priced} price rows vs {sub_terrs} territories")

    st, scr = call("GET", f"subscriptions/{SUBSCRIPTION}/appStoreReviewScreenshot")
    delivered = ((scr.get("data") or {}).get("attributes", {})
                 .get("assetDeliveryState") or {}).get("state")
    report(OK if delivered == "COMPLETE" else BAD,
           "subscription review screenshot", str(delivered))

    print("\nNOT VISIBLE TO THIS SCRIPT — check them in the browser:")
    print("  * App Privacy labels (appDataUsages is not exposed at any role)")
    print("  * Paid Applications Agreement")
    print("  * Submit the subscription WITH the version in one draft:")
    print("    open the subscription -> Add for Review -> add the version -> Submit.")
    print("    Never POST inAppPurchaseSubmissions; that review cannot be recalled.")

    print(f"\n{_bad} blocking item(s) found." if _bad
          else "\nNothing blocking found in what the API exposes.")
    return 1 if _bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
