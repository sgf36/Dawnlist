"""The Mac App Store listing copy, and which Apple locale gets which of it.

ONE DEFINITION, because three scripts need the same answer: the translator that
writes `store/listing-mac/<loc>.json`, the pusher that PATCHes App Store
Connect, and any readiness check that wants to know what is missing.

WHY THE MAC COPY IS NOT THE MICROSOFT COPY
------------------------------------------
`store/listing/<loc>.json` is the Microsoft Store listing and says "Dawnlist is
a subscription, bought on our website". That is true on Windows, where
Microsoft permits third-party commerce. On the Mac App Store it would describe
a purchase outside Apple's commerce, which guideline 3.1.1 forbids and which
the MAS build cannot do anyway — it has no way to accept a licence key and buys
through StoreKit. So the two descriptions differ at the commerce paragraphs and
neither may be copied over the other. `store/listing-mac/` is the Apple set.

WHY THESE APPLE LOCALES AND NOT OTHERS
--------------------------------------
An App Store locale is only offered where **the app itself is translated**, so
the product page and the application the reader downloads are in the same
language. Apple falls back to the primary locale (en-GB) for anything not
listed here, which is the honest outcome for a language the app does not speak.

The regional pairs — es-ES/es-MX, fr-FR/fr-CA, pt-BR/pt-PT — carry the same
base translation, and that is not laziness: `app/i18n.py::detect_locale` maps a
full OS tag onto its base language, so a reader in Mexico runs the `es`
catalogue. Giving them a Castilian product page and a Castilian app is
consistent; giving them an English product page and a Spanish app is not.

DELIBERATELY ABSENT: ca, da, fi, no, sk, zh-Hant. The app has no catalogue for
any of them. en-AU, en-CA and en-US are absent because they would be a
character-for-character copy of en-GB, which is what the fallback already does.

THE LIMITS ARE COUNTED IN CHARACTERS AND APPLE COUNTS STRICTLY. Keywords
include the commas. A subtitle of 30 is tight in English and brutal in German,
so the translator retries rather than truncating — a subtitle cut mid-word is
worse than a shorter phrase that means the same thing.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAC_LISTING = ROOT / "store" / "listing-mac"
CATALOGUE = ROOT / "app" / "resources" / "locales"

#: Apple App Store locale -> the app locale whose catalogue it ships with.
#: Read `app/i18n.py::SUPPORTED_LOCALES` before adding a row; a locale with no
#: catalogue must not appear here.
APPLE_LOCALES = {
    # THE PRIMARY IS IN THIS MAP ON PURPOSE. It is the one locale that already
    # existed in App Store Connect, so it is the one that silently drifts: the
    # English description was condensed from 3,917 to 3,633 characters so a
    # German translation could fit Apple's 4,000 limit, and en-GB kept the old
    # text because nothing was pushing it. Every other locale is a translation
    # OF `store/listing-mac/en.json`, so that file and en-GB must be the same
    # thing or the English page is the only one saying something different.
    "en-GB": "en",
    "ar-SA": "ar",
    "cs": "cs",
    "de-DE": "de",
    "el": "el",
    "es-ES": "es",
    "es-MX": "es",
    "fr-CA": "fr",
    "fr-FR": "fr",
    "he": "he",
    "hi": "hi",
    "hr": "hr",
    "hu": "hu",
    "id": "id",
    "it": "it",
    "ja": "ja",
    "ko": "ko",
    "ms": "ms",
    "nl-NL": "nl",
    "pl": "pl",
    "pt-BR": "pt",
    "pt-PT": "pt",
    "ro": "ro",
    "ru": "ru",
    "sv": "sv",
    "th": "th",
    "tr": "tr",
    "uk": "uk",
    "vi": "vi",
    "zh-Hans": "zh",
}

#: The primary locale, which already exists on the record and is English.
PRIMARY = "en-GB"

#: Apple's character limits. Exceeding one is a 409 that names the field but
#: not the locale, on a request that carries all of them.
LIMITS = {
    "subtitle": 30,
    "promotional": 170,
    "keywords": 100,
    "description": 4000,
}

#: Every field a locale must carry before it can be pushed.
FIELDS = tuple(LIMITS)


def app_locales() -> list[str]:
    """The app locales that at least one Apple locale needs, deduplicated."""
    return sorted(set(APPLE_LOCALES.values()))


def report_button(locale: str) -> str:
    """The app's OWN translation of the button the description quotes.

    The description tells the reader to open Settings and use "Report
    AI-generated content". That button is translated in the catalogue. If the
    listing translated the quotation independently it would name a button that
    does not exist in that language, and the reporting route Apple requires for
    generative content becomes unfindable for the people who need it most.
    """
    path = CATALOGUE / f"{locale}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["settings.report_link"]      # the catalogues are FLAT


def load(locale: str) -> dict:
    """The Mac listing copy for an APP locale ("de", not "de-DE")."""
    return json.loads((MAC_LISTING / f"{locale}.json").read_text(encoding="utf-8"))


def check(locale: str, copy: dict) -> list[str]:
    """Everything wrong with one locale's copy. Empty means it can be pushed."""
    bad = []
    for field in FIELDS:
        value = copy.get(field)
        if not value or not value.strip():
            bad.append(f"{locale}: {field} is empty")
            continue
        if len(value) > LIMITS[field]:
            bad.append(f"{locale}: {field} is {len(value)}, "
                       f"limit {LIMITS[field]}")
    quoted = report_button(locale)
    if quoted not in (copy.get("description") or ""):
        bad.append(f"{locale}: the description does not quote the app's own "
                   f"report button, {quoted!r}")
    return bad
