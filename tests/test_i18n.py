"""i18n: the same 50 languages as the other apps, and safe fallbacks."""
import json
import pathlib

import pytest

from app import i18n

LOCALES = pathlib.Path(i18n.__file__).parent / "resources" / "locales"


@pytest.fixture(autouse=True)
def reset():
    i18n.clear_cache()
    i18n.set_locale("en")
    yield
    i18n.set_locale("en")


def test_the_language_set_matches_the_other_apps():
    assert len(i18n.SUPPORTED_LOCALES) == 50
    assert i18n.LOCALE_CODES[0] == "en"
    assert len(set(i18n.LOCALE_CODES)) == 50, "no duplicate locale codes"


def test_the_rtl_set_is_the_shared_one():
    assert i18n.RTL_LOCALES == {"ar", "ur", "fa", "he"}
    assert i18n.is_rtl("ar") and not i18n.is_rtl("en")


def test_every_locale_has_a_native_name():
    for code, english, native in i18n.SUPPORTED_LOCALES:
        assert code and english and native
        assert len(code) == 2


def test_english_catalogue_is_valid_json_and_complete():
    data = json.loads((LOCALES / "en.json").read_text(encoding="utf-8"))
    assert data and i18n.missing_keys("en") == []


def test_every_shipped_catalogue_parses():
    """A broken catalogue must be caught here, not at runtime."""
    for path in LOCALES.glob("*.json"):
        json.loads(path.read_text(encoding="utf-8"))
        assert path.stem in i18n.LOCALE_CODES, f"{path.stem} is not in the language set"


def test_a_missing_translation_falls_back_to_english():
    i18n.set_locale("zu")          # catalogue not yet generated
    assert i18n.tr("btn.pursue") == "Pursue"


def test_an_unknown_key_returns_itself_so_the_gap_is_visible():
    assert i18n.tr("no.such.key") == "no.such.key"


def test_an_unknown_locale_falls_back_rather_than_raising():
    assert i18n.set_locale("zz") == "en"


def test_interpolation_works():
    assert i18n.tr("funnel.left_unread", count=6) == "6 left unread"


def test_coverage_is_reported_for_every_locale():
    cov = i18n.coverage()
    assert set(cov) == set(i18n.LOCALE_CODES)
    assert cov["en"] == 1.0
