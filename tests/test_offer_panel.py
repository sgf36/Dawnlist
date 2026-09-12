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

    def apple_subscribers(self, key):
        # Nobody has redeemed. The base fake is about CODES; the people list is
        # exercised by FakePeopleAPI below.
        return {"subscribers": [], "comp_offers": ["Dawnlist Comps"]}

    def set_apple_comp(self, key, *, original_transaction_id, comp):
        raise AssertionError("the code tests must never comp anybody")

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


# ---------------------------------------------------------------------------
# Comping the people who redeemed
# ---------------------------------------------------------------------------

class FakePeopleAPI(FakeAPI):
    """`comp_eligible` is supplied by the server, exactly as the real one does.

    A fake that computed it from the offer name would be a second copy of the
    rule, and the tests would then prove the panel agrees with the fake rather
    than with the Worker.
    """

    def __init__(self, people=None, **kw):
        super().__init__(**kw)
        self.people = people if people is not None else [
            {"original_transaction_id": "2000000111", "licence": "…0001",
             "offer_identifier": "Dawnlist Comps", "comp": 0,
             "comp_eligible": True, "status": "active"},
            {"original_transaction_id": "2000000222", "licence": "…0002",
             "offer_identifier": None, "comp": 0,
             "comp_eligible": False, "status": "active"},
        ]
        self.comped = []

    def apple_subscribers(self, key):
        return {"subscribers": self.people, "comp_offers": ["Dawnlist Comps"]}

    def set_apple_comp(self, key, *, original_transaction_id, comp):
        for row in self.people:
            if row["original_transaction_id"] == original_transaction_id:
                row["comp"] = 1 if comp else 0
        self.comped.append((original_transaction_id, comp))
        return {"ok": True}


def people_panel(qapp, api=None):
    from app.ui.settings import AppleOfferPanel
    p = AppleOfferPanel(api=api or FakePeopleAPI(), key_source=lambda: "ADMIN-KEY",
                        confirm=lambda code: True)
    p.refresh()
    idle(p)
    return p


def test_a_guest_can_be_comped_and_a_customer_cannot(qapp):
    api = FakePeopleAPI()
    p = people_panel(qapp, api)

    p.people.setCurrentRow(0)                      # came in on a comp offer
    assert p.btn_comp.isEnabled()
    p.people.setCurrentRow(1)                      # paid
    assert not p.btn_comp.isEnabled()
    assert p.people_result.text()


def test_the_panel_trusts_the_servers_answer_rather_than_re_deriving_it(qapp):
    """The row NAMES a comp offer but the server says it is not eligible.

    A panel that worked eligibility out for itself would enable the button and
    then be refused — which reads as a broken console rather than a deliberate
    refusal. One rule, decided in one place.
    """
    api = FakePeopleAPI(people=[
        {"original_transaction_id": "2000000333", "licence": "…0003",
         "offer_identifier": "Dawnlist Comps", "comp": 0,
         "comp_eligible": False, "status": "active"}])
    p = people_panel(qapp, api)
    p.people.setCurrentRow(0)
    assert not p.btn_comp.isEnabled()


def test_comping_sends_the_transaction_id_and_reports_what_changed(qapp):
    api = FakePeopleAPI()
    p = people_panel(qapp, api)
    p.people.setCurrentRow(0)
    p.btn_comp.click()
    idle(p)

    assert api.comped == [("2000000111", True)]
    assert p.people_result.text()


def test_the_button_says_what_pressing_it_will_do(qapp):
    from app.i18n import tr

    api = FakePeopleAPI()
    p = people_panel(qapp, api)
    p.people.setCurrentRow(0)
    assert p.btn_comp.text() == tr("settings.offer_comp_on")

    p.btn_comp.click()
    idle(p)
    p.people.setCurrentRow(0)
    assert p.btn_comp.text() == tr("settings.offer_comp_off")


def test_un_comping_is_never_blocked(qapp):
    """Withdrawing access must not be gated by the rule that controls granting
    it — otherwise somebody comped before the gate existed can never be undone."""
    api = FakePeopleAPI(people=[
        {"original_transaction_id": "2000000444", "licence": "…0004",
         "offer_identifier": None, "comp": 1,
         "comp_eligible": False, "status": "active"}])
    p = people_panel(qapp, api)
    p.people.setCurrentRow(0)
    assert p.btn_comp.isEnabled(), "a comped row can always be un-comped"
    p.btn_comp.click()
    idle(p)
    assert api.comped == [("2000000444", False)]


def test_nobody_yet_is_not_an_error(qapp):
    p = people_panel(qapp, FakePeopleAPI(people=[]))
    assert p.people.count() == 0
    assert p.people_result.text()
    assert not p.btn_comp.isEnabled()
