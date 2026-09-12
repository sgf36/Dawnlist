"""Comp codes on the Mac App Store build, and the 3.1.1 line they must not cross.

A PURCHASE made outside Apple's commerce must never unlock a MAS build — that
is what guideline 3.1.1 forbids and what the keyring guard in `build_provider`
was written for. A GRANT is not a purchase: nothing was bought, so nothing was
bought outside Apple's commerce. The distinction is the server's to make,
because the client cannot tell one licence string from another.
"""
import pytest

from app.core import db


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.sqlite3")
    db.migrate(c)
    yield c
    c.close()


def _mas(monkeypatch):
    monkeypatch.setattr("app.core.build_variant.variant", lambda: "mas")


def test_a_code_granted_licence_is_honoured_on_a_mac_build(conn, monkeypatch):
    import app.main as main

    _mas(monkeypatch)
    monkeypatch.setattr("app.core.entitlement.stored_licence",
                        lambda: "DAWN-GRANT")
    monkeypatch.setattr(
        "app.core.entitlement.licence_details",
        lambda key, **kw: {"ok": True, "granted_by_code": True,
                           "purchased": False, "role": "managed"})

    provider = main.build_provider(conn)
    assert provider.name == "managed"


def test_a_purchased_licence_is_still_refused_on_a_mac_build(conn, monkeypatch):
    """THE LINE. Someone who ran the direct build first has a Paddle licence in
    the keyring; honouring it here is the prohibited shape, and relaxing the
    guard for comp codes must not relax it for purchases."""
    import app.main as main

    _mas(monkeypatch)
    monkeypatch.setattr("app.core.entitlement.stored_licence",
                        lambda: "DAWN-BOUGHT")
    monkeypatch.setattr(
        "app.core.entitlement.licence_details",
        lambda key, **kw: {"ok": True, "granted_by_code": False,
                           "purchased": True, "role": "byo"})
    monkeypatch.setattr("app.core.mac_receipt.read_receipt", lambda: None)

    with pytest.raises(main.NotConfigured):
        main.build_provider(conn)


def test_an_unverifiable_licence_is_refused_rather_than_assumed(conn, monkeypatch):
    """If the server cannot be reached, the answer is not 'probably a grant'."""
    import app.main as main

    _mas(monkeypatch)
    monkeypatch.setattr("app.core.entitlement.stored_licence",
                        lambda: "DAWN-UNKNOWN")
    monkeypatch.setattr("app.core.entitlement.licence_details",
                        lambda key, **kw: None)
    monkeypatch.setattr("app.core.mac_receipt.read_receipt", lambda: None)

    with pytest.raises(main.NotConfigured):
        main.build_provider(conn)




# -- what the Mac build may OFFER, which is not the same question ------------
#
# Apple rejected 1.1.0 (75) under 3.1.1: "the app uses access codes to unlock
# app features". The keyring guard above is unchanged — a grant found on the
# machine is still honoured, and a purchase is still refused — but no Mac
# screen may offer to redeem one. Windows is untouched: Microsoft has no such
# rule, and the direct build has nothing else to sell through.

def code_boxes(window):
    """Every box in this window a person could paste an access code into.

    By panel rather than by placeholder text: the panels that accept a code are
    the ones that own the redemption call, and a test that matched on wording
    would go quiet the first time the wording was translated or reworded.
    """
    from PySide6.QtWidgets import QLineEdit

    from app.ui.settings import LicencePanel, SubscribePanel

    boxes = []
    for kind in (LicencePanel, SubscribePanel):
        for panel in window.findChildren(kind):
            boxes += [f for f in panel.findChildren(QLineEdit)
                      if f.isVisibleTo(window)]
    return boxes


def _settings_window(build):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from app.ui.settings import SettingsWindow

    QApplication.instance() or QApplication([])
    return SettingsWindow(variant=build)


def test_a_mac_build_offers_nowhere_to_redeem_a_code():
    window = _settings_window("mas")
    assert code_boxes(window) == [], (
        "a Mac build offers a box an access code can be typed into")
    # The purchase panel has no text field of ANY kind, so there is nothing to
    # re-wire a redemption onto later without noticing.
    from PySide6.QtWidgets import QLineEdit
    assert window.subscribe is not None, "the purchase panel must still build"
    assert window.subscribe.findChildren(QLineEdit) == []
    window.close()


@pytest.mark.parametrize("build", ["store", "direct"])
def test_the_windows_builds_still_offer_redemption(build):
    """The positive control. A test that only asserts absence passes just as
    happily when the whole panel failed to build."""
    window = _settings_window(build)
    assert code_boxes(window), f"{build} lost its redemption box"
    window.close()
