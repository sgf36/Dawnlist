"""Push the Mac App Store listing into EVERY Apple locale we offer. Idempotent.

    .venv/Scripts/python tools/asc_listing_locales.py --dry-run
    .venv/Scripts/python tools/asc_listing_locales.py --only de-DE
    .venv/Scripts/python tools/asc_listing_locales.py

`asc_listing.py` writes the PRIMARY locale only — it reads `data[0]` from each
relationship and PATCHes it, which is right for en-GB and silently does nothing
for the other twenty-nine. This script creates the missing ones and updates the
rest, from `store/listing-mac/<app locale>.json`.

TWO RESOURCES PER LOCALE, AND THEY ARE NOT INTERCHANGEABLE.

  appStoreVersionLocalizations  description, keywords, promotionalText, the
                                two URLs. Attached to the VERSION, so they are
                                re-entered for every release.
  appInfoLocalizations          name, subtitle, privacyPolicyUrl. Attached to
                                the APP, so they persist across releases.

A subtitle PATCHed onto the version localisation is accepted and then ignored,
which looks exactly like success.

whatsNew IS DELIBERATELY ABSENT. Apple rejects release notes on a version that
has never been released, and no Mac version of Dawnlist ever has.

THE NAME IS NOT TRANSLATED. "Dawnlist Job Search" is a product name; it is sent
identically to every locale so the app is findable by the name people are told.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.asc import APP, call, errs  # noqa: E402
from tools.mac_listing import (APPLE_LOCALES, LIMITS, check,  # noqa: E402
                               load)

NAME = "Dawnlist Job Search"
SUPPORT_URL = "https://dawnlist.spencerfields.com"
MARKETING_URL = "https://dawnlist.spencerfields.com"
PRIVACY_URL = "https://dawnlist.spencerfields.com/privacy.html"


def existing(kind: str, parent: str, parent_id: str) -> dict[str, str]:
    """`{locale: id}` for one parent's localisations, following pagination."""
    out: dict[str, str] = {}
    path = f"{parent}/{parent_id}/{kind}?limit=50"
    while path:
        st, d = call("GET", path)
        if st != 200:
            sys.exit(f"GET {path} -> {st}: {errs(d)}")
        for row in d["data"]:
            out[row["attributes"]["locale"]] = row["id"]
        nxt = (d.get("links") or {}).get("next")
        path = nxt.split("/v1/", 1)[1] if nxt else ""
    return out


def write(kind: str, rid: str | None, attrs: dict, rel: dict) -> str:
    """PATCH an existing localisation or POST a new one. Returns its id."""
    if rid:
        st, d = call("PATCH", f"{kind}/{rid}",
                     {"data": {"type": kind, "id": rid, "attributes": attrs}})
        ok = (200, 201)
    else:
        st, d = call("POST", kind,
                     {"data": {"type": kind, "attributes": attrs,
                               "relationships": rel}})
        ok = (200, 201)
    if st not in ok:
        sys.exit(f"{'PATCH' if rid else 'POST'} {kind} "
                 f"[{attrs.get('locale', rid)}] -> {st}: {errs(d)}")
    return d["data"]["id"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated APPLE locales")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    only = set(args.only.split(",")) if args.only else None

    # Every locale's copy is loaded and checked BEFORE anything is written.
    # A half-pushed listing is worse than an unpushed one: the locales that
    # landed look finished, and nothing on the page says which those were.
    copy: dict[str, dict] = {}
    problems: list[str] = []
    missing: list[str] = []
    for apple, app_locale in sorted(APPLE_LOCALES.items()):
        if only and apple not in only:
            continue
        path = ROOT / "store" / "listing-mac" / f"{app_locale}.json"
        if not path.exists():
            missing.append(f"{apple} needs store/listing-mac/{app_locale}.json")
            continue
        c = load(app_locale)
        problems.extend(f"{apple}: {p}" for p in check(app_locale, c))
        copy[apple] = c

    if missing:
        print(f"{len(missing)} locale(s) have no translation yet:",
              file=sys.stderr)
        for m in missing:
            print("  " + m, file=sys.stderr)
    if problems:
        print(f"\n{len(problems)} problem(s) — nothing written:", file=sys.stderr)
        for p in problems:
            print("  " + p, file=sys.stderr)
        return 1
    if missing:
        return 1

    st, d = call("GET", f"apps/{APP}/appStoreVersions?limit=1")
    vid = d["data"][0]["id"]
    print(f"version {d['data'][0]['attributes']['versionString']} "
          f"({d['data'][0]['attributes']['appStoreState']})")
    st, ai = call("GET", f"apps/{APP}/appInfos")
    aid = ai["data"][0]["id"]

    have_ver = existing("appStoreVersionLocalizations", "appStoreVersions", vid)
    have_info = existing("appInfoLocalizations", "appInfos", aid)
    print(f"{len(have_ver)} version localisation(s), "
          f"{len(have_info)} app-info localisation(s) already there")
    print(f"{len(copy)} locale(s) to push\n")

    if args.dry_run:
        for apple in sorted(copy):
            print(f"  {apple:8} {'update' if apple in have_ver else 'CREATE':6} "
                  f"desc {len(copy[apple]['description'])}, "
                  f"sub {len(copy[apple]['subtitle'])}, "
                  f"kw {len(copy[apple]['keywords'])}")
        return 0

    for apple in sorted(copy):
        c = copy[apple]
        write("appStoreVersionLocalizations", have_ver.get(apple),
              {k: v for k, v in {
                  "locale": None if apple in have_ver else apple,
                  "description": c["description"],
                  "keywords": c["keywords"],
                  "promotionalText": c["promotional"],
                  "supportUrl": SUPPORT_URL,
                  "marketingUrl": MARKETING_URL,
              }.items() if v is not None},
              {"appStoreVersion": {"data": {"type": "appStoreVersions",
                                            "id": vid}}})
        # RE-READ, because creating the version localisation above CREATES THE
        # MATCHING appInfoLocalization as a side effect. The id did not exist
        # when the list was taken and does by the time this line runs, so a
        # POST here answers 409 "already exists" and the run dies half way
        # through a locale. Apple documents neither the side effect nor the
        # 409; both were found by doing it.
        have_info = existing("appInfoLocalizations", "appInfos", aid)
        write("appInfoLocalizations", have_info.get(apple),
              {k: v for k, v in {
                  "locale": None if apple in have_info else apple,
                  "name": NAME,
                  "subtitle": c["subtitle"],
                  "privacyPolicyUrl": PRIVACY_URL,
              }.items() if v is not None},
              {"appInfo": {"data": {"type": "appInfos", "id": aid}}})
        print(f"  {apple:8} ok")

    # READ IT BACK. What was sent is not what is stored: Apple silently drops a
    # field it does not accept for a locale, and the POST still returns 201.
    after_ver = existing("appStoreVersionLocalizations", "appStoreVersions", vid)
    after_info = existing("appInfoLocalizations", "appInfos", aid)
    gaps = [a for a in copy if a not in after_ver or a not in after_info]
    if gaps:
        print(f"\n{len(gaps)} locale(s) did not come back: {', '.join(gaps)}",
              file=sys.stderr)
        return 1
    print(f"\n{len(after_ver)} version and {len(after_info)} app-info "
          f"localisations now live")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
