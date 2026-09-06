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


def test_a_missing_translation_falls_back_to_english(monkeypatch):
    """Tests the MECHANISM, not the coincidence that a catalogue is absent.

    The first version of this used a locale that had not been generated yet,
    and started failing the moment it was — which proved nothing about the
    fallback and everything about the test.
    """
    import json

    real_english = json.loads((LOCALES / "en.json").read_text(encoding="utf-8"))
    i18n.clear_cache()
    # Read English straight from disk: routing it back through the module
    # would recurse, because _english_catalog() calls _load_catalog().
    monkeypatch.setattr(i18n, "_load_catalog",
                        lambda locale: {} if locale == "zu" else real_english)
    monkeypatch.setattr(i18n, "_english_catalog", lambda: real_english)
    i18n.set_locale("zu")
    assert i18n.tr("btn.pursue") == "Pursue"
    # No clear_cache() here: the patched loader is a plain lambda with no
    # .cache_clear(). monkeypatch restores the real one, and the autouse
    # fixture clears it for the next test.


def test_every_catalogue_is_complete():
    """A catalogue behind the source shows up as an English string in the
    middle of a translated screen, which reads as a broken translation rather
    than a missing key. `translate_catalog.py --fill` is the fix."""
    i18n.clear_cache()
    behind = {code: sorted(i18n.missing_keys(code))
              for code in i18n.LOCALE_CODES
              if (LOCALES / f"{code}.json").exists() and i18n.missing_keys(code)}
    assert not behind, f"catalogues behind the source: {behind}"


def test_all_fifty_catalogues_are_present():
    present = {p.stem for p in LOCALES.glob("*.json")}
    assert present == set(i18n.LOCALE_CODES)


def test_no_catalogue_invents_a_key():
    """A catalogue can never drift AHEAD: an extra key is either a typo or a
    hallucinated string that no code will ever read."""
    import json
    english = set(json.loads((LOCALES / "en.json").read_text(encoding="utf-8")))
    for path in LOCALES.glob("*.json"):
        keys = set(json.loads(path.read_text(encoding="utf-8")))
        assert not (keys - english), f"{path.stem} has invented {keys - english}"


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


def test_a_placeholder_may_be_called_key():
    """The placeholder name is chosen by whoever writes the catalogue, so tr()
    must not reserve plausible words for its own parameters."""
    assert i18n.tr("settings.key_stored", key="sk-ant-…abcd") == \
        "Stored: sk-ant-…abcd"


def test_a_placeholder_may_be_called_anything_else_awkward():
    import inspect
    sig = inspect.signature(i18n.tr)
    first = list(sig.parameters.values())[0]
    assert first.kind is inspect.Parameter.POSITIONAL_ONLY
