"""Minimal JSON-catalog i18n: tr(key) looks up the active locale, falling back
to English so a partially-translated locale file never crashes the UI.

The language set is the SAME 50 as Easy-Post Desktop and Wren — top 50 languages
by combined speaker population, in each language's standard written form (one
Mandarin entry, one Modern Standard Arabic entry) rather than every mutually
unintelligible topolect. Keeping the three apps on one list means a locale added
to one is addable to all, and the store listings stay comparable.

The list and the RTL set are ported verbatim from `EasyPost-Desktop-App/app/i18n.py`.
If either changes there, change it here too.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

LOCALES_DIR = Path(__file__).parent / "resources" / "locales"
DEFAULT_LOCALE = "en"

# (code, English name, native name)
SUPPORTED_LOCALES: list[tuple[str, str, str]] = [
    ("en", "English", "English"),
    ("zh", "Mandarin Chinese", "中文"),
    ("hi", "Hindi", "हिन्दी"),
    ("es", "Spanish", "Español"),
    ("fr", "French", "Français"),
    ("ar", "Arabic", "العربية"),
    ("bn", "Bengali", "বাংলা"),
    ("pt", "Portuguese", "Português"),
    ("ru", "Russian", "Русский"),
    ("ur", "Urdu", "اردو"),
    ("id", "Indonesian", "Bahasa Indonesia"),
    ("de", "German", "Deutsch"),
    ("ja", "Japanese", "日本語"),
    ("mr", "Marathi", "मराठी"),
    ("te", "Telugu", "తెలుగు"),
    ("tr", "Turkish", "Türkçe"),
    ("ta", "Tamil", "தமிழ்"),
    ("vi", "Vietnamese", "Tiếng Việt"),
    ("ko", "Korean", "한국어"),
    ("fa", "Persian", "فارسی"),
    ("ha", "Hausa", "Hausa"),
    ("sw", "Swahili", "Kiswahili"),
    ("jv", "Javanese", "Basa Jawa"),
    ("it", "Italian", "Italiano"),
    ("pa", "Punjabi", "ਪੰਜਾਬੀ"),
    ("gu", "Gujarati", "ગુજરાતી"),
    ("am", "Amharic", "አማርኛ"),
    ("th", "Thai", "ไทย"),
    ("kn", "Kannada", "ಕನ್ನಡ"),
    ("my", "Burmese", "မြန်မာဘာသာ"),
    ("yo", "Yoruba", "Yorùbá"),
    ("uz", "Uzbek", "Oʻzbekcha"),
    ("ml", "Malayalam", "മലയാളം"),
    ("or", "Odia", "ଓଡ଼ିଆ"),
    ("uk", "Ukrainian", "Українська"),
    ("pl", "Polish", "Polski"),
    ("ms", "Malay", "Bahasa Melayu"),
    ("nl", "Dutch", "Nederlands"),
    ("ig", "Igbo", "Igbo"),
    ("si", "Sinhala", "සිංහල"),
    ("ne", "Nepali", "नेपाली"),
    ("ro", "Romanian", "Română"),
    ("zu", "Zulu", "isiZulu"),
    ("so", "Somali", "Soomaali"),
    ("hr", "Croatian", "Hrvatski"),
    ("el", "Greek", "Ελληνικά"),
    ("hu", "Hungarian", "Magyar"),
    ("cs", "Czech", "Čeština"),
    ("he", "Hebrew", "עברית"),
    ("sv", "Swedish", "Svenska"),
]

LOCALE_CODES = [code for code, _, _ in SUPPORTED_LOCALES]
LOCALE_NAMES = {code: english for code, english, _ in SUPPORTED_LOCALES}

RTL_LOCALES = {"ar", "ur", "fa", "he"}

_active_locale = DEFAULT_LOCALE


@lru_cache(maxsize=None)
def _load_catalog(locale: str) -> dict:
    path = LOCALES_DIR / f"{locale}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a broken catalog must never crash the UI
        return {}


@lru_cache(maxsize=1)
def _english_catalog() -> dict:
    return _load_catalog(DEFAULT_LOCALE)


def current_locale() -> str:
    return _active_locale


def set_locale(locale: str) -> str:
    """Set the active locale. An unknown code falls back to English rather than
    raising — a bad settings value must not stop the app opening."""
    global _active_locale
    _active_locale = locale if locale in LOCALE_CODES else DEFAULT_LOCALE
    return _active_locale


def tr(key: str, /, **kwargs) -> str:
    """Active locale, then English, then the key itself.

    Returning the key last means a missing translation is visibly obvious in
    the UI rather than silently blank.

    `key` is POSITIONAL-ONLY. Without that, any catalogue string containing a
    `{key}` placeholder collides with this parameter — `tr("settings.key_stored",
    key=...)` raises "got multiple values for argument 'key'". The placeholder
    name is chosen by whoever writes the catalogue, so the function must not
    reserve plausible words.
    """
    catalog = _load_catalog(current_locale())
    text = catalog.get(key) or _english_catalog().get(key) or key
    return text.format(**kwargs) if kwargs else text


def is_rtl(locale: str | None = None) -> bool:
    return (locale or current_locale()) in RTL_LOCALES


def clear_cache() -> None:
    _load_catalog.cache_clear()
    _english_catalog.cache_clear()


def missing_keys(locale: str) -> list[str]:
    """Keys present in English and absent from `locale`. Used by the catalogue
    check so an untranslated string is a reported gap, never a surprise."""
    english = _english_catalog()
    catalog = _load_catalog(locale)
    return sorted(k for k in english if k not in catalog)


def coverage() -> dict[str, float]:
    """Per-locale translation coverage, 0.0-1.0. Reported, never inferred."""
    total = len(_english_catalog()) or 1
    return {code: 1.0 - (len(missing_keys(code)) / total) for code in LOCALE_CODES}
