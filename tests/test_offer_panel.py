"""The Mac offer-code console: what it hands out, and what it refuses to claim.

This screen exists because Dawnlist's own override codes were removed from the
Mac build under guideline 3.1.1, so an App Store offer code is the ONLY way to
give somebody free access to the Mac subscription. A mistake here is a friend
holding a string that does not work, with no way to tell that from a broken app.
"""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


class FakeAPI:
    """The Worker, as this screen sees it.

    `assign` returns a code the SERVER chose, because that is the real
    contract: the panel never picks one. A fake that let the caller name a
    code would let a bug through that hands the same string to two people.
    """

    def __init__(self, codes=None):
        self.codes = codes if codes is not None else [
            {"code": "AAAA1111", "batch": "friends-2026", "assigned_to": None,
             "assigned_at": None, "note": None, "void": 0},
            {"code": "BBBB2222", "batch": "friends-2026", "assigned_to": None,
             "assigned_at": None, "note": None, "void": 0},
        ]
        self.assigned, self.voided = [], []

    def apple_codes(self, key, *, state="all"):
        return {"codes": self.codes,
                "batches": [{"batch": "friends-2026", "total": len(self.codes),
                             "assigned": 0, "redemptions": 0}]}

    def assign_apple_code(self, key, *, assigned_to, note="", batch=""):
        free = [c for c in self.codes if not c["void"] and not c["assigned_at"]]
        if not free:
            raise RuntimeError("No unassigned code left")
        row = free[0]
        row.update(assigned_to=assigned_to, assigned_at="2026-09-12T12:00:00Z",
                   note=note)
        self.assigned.append((row["code"], assigned_to))
        return dict(row)

    def void_apple_code(self, key, code):
        for row in self.codes:
            if row["code"] == code:
                row["void"] = 1
        self.voided.append(code)
        return {"ok": True, "code": code, "still_redeemable_at_apple": True}


def panel(qapp, api=None, key="ADMIN-KEY", confirm=lambda code: True):
    from app.ui.settings import AppleOfferPanel
    return AppleOfferPanel(api=api or FakeAPI(), key_source=lambda: key,
                           confirm=confirm)


def idle(p, timeout=10.0):
    import time

    deadline = time.monotonic() + timeout
    while not p.btn_assign.isEnabled():
        if time.monotonic() > deadline:
            raise AssertionError("the console action never finished")
        QApplication.processEvents()
        time.sleep(0.005)


def test_the_code_is_shown_in_full_so_it_can_be_copied(qapp):
    """The whole point of the screen is to get a string into a message. A
    truncated or masked code is one that gets retyped wrongly, and a mistyped
    offer code is indistinguishable from a broken app to whoever received it."""
    api = FakeAPI()
    p = panel(qapp, api)
    p.who.setText("Sean")
    p.btn_assign.click()
    idle(p)

    assert api.assigned == [("AAAA1111", "Sean")]
    assert "AAAA1111" in p.result.text()
    assert "Sean" in p.result.text()


def test_the_code_can_actually_be_selected(qapp):
    from PySide6.QtCore import Qt

    p = panel(qapp)
    assert p.result.textInteractionFlags() & Qt.TextSelectableByMouse


def test_an_unnamed_recipient_is_refused_without_a_round_trip(qapp):
    """Refused next to the box it is about, and before the Worker is asked."""
    api = FakeAPI()
    p = panel(qapp, api)
    p.who.setText("   ")
    p.btn_assign.click()
    idle(p)

    assert api.assigned == []
    assert p.result.text()


def test_an_empty_ledger_says_what_to_do_rather_than_reading_as_an_error(qapp):
    """Nothing to hand out is not a fault, and the fix happens somewhere else
    entirely — in App Store Connect — so the screen has to name it."""
    p = panel(qapp, FakeAPI(codes=[]))
    p.refresh()
    idle(p)

    assert p.codes.count() == 0
    assert "mint" in p.result.text().lower()


def test_voiding_never_claims_apple_withdrew_the_code(qapp):
    """Apple has no API to withdraw a minted one-time code. A console that
    reported this as revoked would be lying about the one thing its user needs
    to know, so both the confirmation and the result say it still works."""
    from app.i18n import tr

    api = FakeAPI()
    asked = {}
    p = panel(qapp, api, confirm=lambda code: asked.setdefault("code", code) or True)
    p.refresh()
    idle(p)
    p.codes.setCurrentRow(0)
    p.btn_void.click()
    idle(p)

    assert api.voided == ["AAAA1111"]
    assert "still" in p.result.text().lower() or "redeem" in p.result.text().lower()
    assert "REMAINS REDEEMABLE" in tr("settings.offer_confirm_void", code="X")


def test_voiding_asks_first_and_a_no_changes_nothing(qapp):
    api = FakeAPI()
    p = panel(qapp, api, confirm=lambda code: False)
    p.refresh()
    idle(p)
    p.codes.setCurrentRow(0)
    p.btn_void.click()
    idle(p)

    assert api.voided == []


def test_voiding_with_nothing_selected_says_so(qapp):
    api = FakeAPI()
    p = panel(qapp, api)
    p.refresh()
    idle(p)
    p.codes.setCurrentRow(-1)
    p.btn_void.click()
    idle(p)

    assert api.voided == []
    assert p.result.text()


def test_every_action_runs_off_the_ui_thread_with_the_buttons_held(qapp):
    """Each is a round trip to the Worker. A void pressed while a list is
    still arriving would act on the row indexes of the list it is replacing."""
    import threading

    release = threading.Event()

    class Slow(FakeAPI):
        def apple_codes(self, key, *, state="all"):
            release.wait(5)
            return super().apple_codes(key, state=state)

    p = panel(qapp, Slow())
    p.refresh()
    QApplication.processEvents()
    assert not p.btn_assign.isEnabled()
    assert not p.btn_void.isEnabled()
    release.set()
    idle(p)
    assert p.btn_assign.isEnabled()


def test_the_panel_is_hidden_until_the_server_says_administrator(qapp):
    """Same answer, same moment as the console above it. An administrator who
    could see one and not the other would think offer codes were a Mac-only
    screen, which is exactly backwards.

    And, as for the console: the check must not run at CONSTRUCTION. Building a
    window that reaches the live Worker makes the suite depend on the network.
    """
    from app.ui.settings import SettingsWindow

    asked = []
    w = SettingsWindow(is_admin=lambda key: asked.append(key) or False)
    assert w.offers.isHidden()
    assert w.admin.isHidden(), 'the two reveal together or the pairing is broken'
    assert asked == [], 'constructing a window must not reach the network'
    w.close()
