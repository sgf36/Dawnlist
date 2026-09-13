"""The Microsoft Store-billed build (`store_iap`) gets what the other three get.

Every one of these was a check written as `build in ("store", "direct")` or
`build == "store"` before `store_iap` existed, and none raised an error: the
branch was simply skipped. Found by audit on 2026-09-13, from a screenshot of
the live Store build whose menu offered no way to subscribe.

  * the daily run read only the Paddle licence slot, so a PAYING Store
    subscriber passed the entitlement check and was then told "No licence key
    found. Enter the key from your purchase email";
  * setup offered no purchase step at all;
  * the menu offered no Subscribe;
  * Launch at sign-in reported the build as unsupported, so the 07:00 run could
    not start by itself.

Each test also pins the nuance of the other builds, so a fix for one cannot
quietly change another.
"""
from __future__ import annotations

import pytest

from app.core.entitlement import AppleExchange


@pytest.fixture
def store_iap(monkeypatch):
    monkeypatch.setattr("app.core.build_variant.variant", lambda: "store_iap")
    monkeypatch.setattr("app.core.entitlement.stored_licence", lambda: None)


def _ms(monkeypatch, result, cache=None):
    monkeypatch.setattr("app.core.entitlement.ms_exchange_and_cache",
                        lambda *a, **k: result)
    monkeypatch.setattr("app.core.entitlement.ms_cache", lambda: dict(cache or {}))


# -- the feed ------------------------------------------------------------------

def test_a_store_subscriber_gets_the_feed(store_iap, monkeypatch):
    from app.main import build_provider

    _ms(monkeypatch, AppleExchange("licence", licence_key="DAWN-MS"))
    assert build_provider(conn=None)._licence == "DAWN-MS"


def test_a_code_or_paddle_licence_still_works_on_the_store_build(monkeypatch):
    """Microsoft permits third-party commerce, so unlike the Mac this build
    honours any Dawnlist licence first — matching `_check_ms_store`."""
    from app.main import build_provider

    monkeypatch.setattr("app.core.build_variant.variant", lambda: "store_iap")
    monkeypatch.setattr("app.core.entitlement.stored_licence", lambda: "DAWN-CODE")

    def explode(*a, **k):
        raise AssertionError("a licence in hand needs no Store round trip")

    monkeypatch.setattr("app.core.entitlement.ms_exchange_and_cache", explode)
    assert build_provider(conn=None)._licence == "DAWN-CODE"


def test_an_unreachable_store_runs_on_the_last_licence(store_iap, monkeypatch):
    from app.main import build_provider

    _ms(monkeypatch, AppleExchange("unreachable"), cache={"licence_key": "DAWN-LAST"})
    assert build_provider(conn=None)._licence == "DAWN-LAST"


@pytest.mark.parametrize("outcome,key", [
    ("none", "entitlement.ms_not_subscribed"),
    ("refused", "entitlement.ms_lapsed"),
    ("unreachable", "entitlement.ms_unreachable"),
])
def test_no_feed_says_the_store_thing_never_the_licence_key_thing(
        store_iap, monkeypatch, outcome, key):
    from app.i18n import tr
    from app.main import NotConfigured, build_provider

    _ms(monkeypatch, AppleExchange(outcome))
    with pytest.raises(NotConfigured) as e:
        build_provider(conn=None)
    assert str(e.value) == tr(key)
    assert "licence key" not in str(e.value).lower()


def test_a_refusal_is_never_softened_by_a_kept_store_licence(store_iap, monkeypatch):
    from app.main import NotConfigured, build_provider

    _ms(monkeypatch, AppleExchange("refused"), cache={"licence_key": "DAWN-OLD"})
    with pytest.raises(NotConfigured):
        build_provider(conn=None)


# -- setup, menu, sign-in -------------------------------------------------------

def test_setup_offers_the_store_purchase(monkeypatch, qapp):
    from app import main
    from app.ui.settings import LicencePanel, StoreSubscribePanel, SubscribePanel

    monkeypatch.setattr("app.core.build_variant.variant", lambda: "store_iap")
    monkeypatch.setattr("app.core.msstore.offer",
                        lambda: type("O", (), {"available": False, "title": "", "price": ""})())
    assert isinstance(main.onboarding_entitlement_panel(), StoreSubscribePanel)
    monkeypatch.setattr("app.core.build_variant.variant", lambda: "mas")
    assert isinstance(main.onboarding_entitlement_panel(), SubscribePanel)
    for build in ("store", "direct"):
        monkeypatch.setattr("app.core.build_variant.variant", lambda b=build: b)
        assert isinstance(main.onboarding_entitlement_panel(), LicencePanel)


def _menu_texts(build, qapp):
    from PySide6.QtWidgets import QMenu

    from app.ui.quickmenu import populate

    menu = QMenu()
    populate(menu, build=build, on_language=lambda c: None,
             on_subscribe=lambda: None, on_restore=lambda: None,
             on_settings=lambda: None)
    return [a.text() for a in menu.actions() if a.text()]


def test_the_menu_offers_subscribe_on_the_store_build(qapp):
    from app.i18n import tr

    texts = _menu_texts("store_iap", qapp)
    assert tr("menu.subscribe") in texts
    # Restore is Apple's; the Store entitlement follows the Microsoft account.
    assert tr("menu.restore") not in texts
    assert tr("menu.licence") not in texts
    # The other builds keep their own routes.
    assert tr("menu.licence") in _menu_texts("store", qapp)
    assert tr("menu.restore") in _menu_texts("mas", qapp)
    assert tr("menu.licence") not in _menu_texts("mas", qapp)


def test_launch_at_sign_in_works_on_both_store_builds():
    from app.core import sign_in

    assert sign_in.mechanism("store_iap", "win32") == "store"
    assert sign_in.mechanism("store", "win32") == "store"
    assert sign_in.mechanism("store_iap", "darwin") is None


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])
