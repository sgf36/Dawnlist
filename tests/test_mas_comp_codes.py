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


def test_the_mac_panel_can_redeem_a_code(monkeypatch):
    """The Mac build had no way to redeem at all: LicencePanel owns the box and
    is never shown on `mas`."""
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from app.ui.settings import SubscribePanel

    QApplication.instance() or QApplication([])
    stored = []

    class SK:
        def available(self): return True
        def can_make_payments(self): return True
        def price(self): return "$79.00"

    panel = SubscribePanel(storekit=SK(),
                           redeemer=lambda code: f"DAWN-FOR-{code}",
                           storer=stored.append)
    panel.code.setText("DL-ABC")
    panel.btn_code.click()

    assert stored == ["DAWN-FOR-DL-ABC"]
    assert panel.code.text() == ""
    panel.close()


def test_a_mac_code_that_cannot_be_saved_shows_the_licence(monkeypatch):
    """The code is spent by the time the store refuses, so the key on screen
    is the only copy. The test above is the positive control."""
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from app.core.credentials import KeyringUnavailable
    from app.ui.settings import SubscribePanel

    QApplication.instance() or QApplication([])

    class SK:
        def available(self): return True
        def can_make_payments(self): return True
        def price(self): return "$79.00"

    def refuse(_key):
        raise KeyringUnavailable("locked")

    panel = SubscribePanel(storekit=SK(), redeemer=lambda code: "DAWN-ONLY-COPY",
                           storer=refuse)
    panel.code.setText("DL-ABC")
    panel.btn_code.click()
    assert "DAWN-ONLY-COPY" in panel.result.text()
    panel.close()
