"""Language, subscription and Restore, reachable without a manual.

TWO HOLES THIS CLOSES, BOTH FOUND BY USING THE APP RATHER THAN READING IT

**Nothing could change the language.** `settings["locale"]` was READ in three
places — the app's own strings, the alert-email parser, the voice profile —
and written by nothing at all. The only way to select a language was
`--locale` on the command line. Fifty catalogues and a hundred and ninety-odd
keys were translated, bundled and shipped, and every user saw English,
including the ones who could not have read a setting that did not exist.

**Nothing could reach the subscription.** Both purchase panels were complete
and both lived only in Settings, which the setup wizard never opens and never
mentions. Apple requires Restore to be findable, and a screen the flow never
offers is not findable.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402

from app.core import db  # noqa: E402
from app.i18n import LOCALE_CODES, SUPPORTED_LOCALES  # noqa: E402
from app.ui.quickmenu import QuickMenu, populate  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.sqlite3")
    db.migrate(c)
    yield c
    c.close()


def items(menu: QMenu) -> list[str]:
    return [a.text() for a in menu.actions() if a.text()]


def test_every_shipped_language_can_be_chosen(qapp):
    """Fifty catalogues ship. Fifty must be selectable, or the ones that are
    not were translated for nobody."""
    menu = QMenu()
    populate(menu, build="direct", on_language=lambda c: None,
             on_subscribe=lambda: None, on_restore=lambda: None,
             on_settings=lambda: None)
    languages = [a.menu() for a in menu.actions() if a.menu()][0]
    assert len(languages.actions()) == len(LOCALE_CODES) == 50


def test_languages_are_listed_in_their_own_names(qapp):
    """Somebody looking for their language is looking for the word they call
    it. "German" is no use to a person who reads only Deutsch."""
    menu = QMenu()
    populate(menu, build="direct", on_language=lambda c: None,
             on_subscribe=lambda: None, on_restore=lambda: None,
             on_settings=lambda: None)
    languages = [a.menu() for a in menu.actions() if a.menu()][0]
    shown = {a.text() for a in languages.actions()}
    natives = {native for _c, _e, native in SUPPORTED_LOCALES}
    assert shown == natives


def test_choosing_a_language_reports_the_code(qapp):
    chosen = []
    menu = QMenu()
    populate(menu, build="direct", on_language=chosen.append,
             on_subscribe=lambda: None, on_restore=lambda: None,
             on_settings=lambda: None)
    languages = [a.menu() for a in menu.actions() if a.menu()][0]
    languages.actions()[1].trigger()
    assert chosen == [SUPPORTED_LOCALES[1][0]]


def test_a_mac_build_offers_subscribe_and_restore(qapp):
    """Apple requires Restore: somebody who paid on another Mac has already
    paid, and must not pay twice to prove it."""
    menu = QMenu()
    populate(menu, build="mas", on_language=lambda c: None,
             on_subscribe=lambda: None, on_restore=lambda: None,
             on_settings=lambda: None)
    text = " ".join(items(menu))
    assert "Subscribe" in text and "Restore" in text


def test_a_windows_build_offers_a_key_and_never_restore(qapp):
    """Restore is meaningless where Paddle issues a key rather than an
    account entitlement, and a dead menu item is a support ticket."""
    menu = QMenu()
    populate(menu, build="store", on_language=lambda c: None,
             on_subscribe=lambda: None, on_restore=lambda: None,
             on_settings=lambda: None)
    text = " ".join(items(menu))
    assert "access code" in text
    assert "Restore" not in text


def test_a_mac_build_never_offers_a_licence_key(qapp):
    """Guideline 3.1.1 names licence keys explicitly. Offering one on a MAS
    build is the prohibited shape, whatever the box is labelled."""
    menu = QMenu()
    populate(menu, build="mas", on_language=lambda c: None,
             on_subscribe=lambda: None, on_restore=lambda: None,
             on_settings=lambda: None)
    assert "access code" not in " ".join(items(menu)).lower()


def test_an_unknown_build_offers_neither(qapp):
    """A build with no usable variant flag must not guess: a key box on a Mac
    is a rejection, and a StoreKit button on Windows does nothing."""
    menu = QMenu()
    populate(menu, build="none", on_language=lambda c: None,
             on_subscribe=lambda: None, on_restore=lambda: None,
             on_settings=lambda: None)
    text = " ".join(items(menu))
    assert "Subscribe" not in text and "access code" not in text


# -- the write that did not exist -------------------------------------------

def test_the_chosen_language_is_actually_stored(conn):
    from app.main import load_settings, save_locale

    assert load_settings(conn).get("locale") is None, "nothing has chosen yet"
    save_locale(conn, "de")
    assert load_settings(conn)["locale"] == "de"


def test_choosing_twice_replaces_rather_than_duplicates(conn):
    from app.main import load_settings, save_locale

    save_locale(conn, "de")
    save_locale(conn, "fr")
    assert load_settings(conn)["locale"] == "fr"


def test_a_locale_with_no_catalogue_is_refused(conn):
    """A stored locale nothing can load falls back to English silently, which
    looks like the setting was ignored."""
    from app.main import save_locale

    with pytest.raises(ValueError):
        save_locale(conn, "klingon")


def test_the_setting_the_app_reads_is_the_one_the_menu_writes(conn):
    """`load_settings(conn).get("locale", "en")` is what `main` passes to
    `set_locale` on every launch. Writing a different key would store a
    preference nothing reads — which is the bug this replaces, inverted."""
    from app.main import load_settings, save_locale

    save_locale(conn, "es")
    assert load_settings(conn).get("locale", "en") == "es"


def test_the_button_exists_on_the_setup_wizard(qapp):
    """The person who most needs a language switcher has not finished setting
    up, and the person who most needs Subscribe has just been told there is
    no feed."""
    from app.ui.onboarding import OnboardingWizard

    w = OnboardingWizard(extract=lambda p: ([], []), sample=lambda: [])
    assert isinstance(w.menu, QuickMenu)
    w.close()
