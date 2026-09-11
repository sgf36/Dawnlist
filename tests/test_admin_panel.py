"""The admin console screen, and the two defects wiring it up exposed."""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


class FakeAPI:
    from app.core.admin import (DEFAULT_EXPIRY_DAYS, PLANS, ROLES, REVIEW_USES,
                                expiry_after, may_be_open_ended)
    expiry_after = staticmethod(expiry_after)
    may_be_open_ended = staticmethod(may_be_open_ended)

    def __init__(self):
        self.issued, self.revoked, self.expiries = [], [], []
        self.codes = [{"code": "DL-1", "note": "Sean", "role": "managed",
                       "plan": "standard", "max_uses": 25, "uses": 3,
                       "revoked": 0}]

    def list_codes(self, key):
        return self.codes

    def issue_code(self, key, *, note, role, plan, max_uses, expires_at=None):
        if not note.strip():
            raise RuntimeError("Say who this code is for")
        self.issued.append((note, role, plan, max_uses))
        self.expiries.append(expires_at)
        return {"ok": True, "code": "DL-NEW"}

    def revoke_code(self, key, code):
        self.revoked.append(code)
        return {"ok": True}


def panel(qapp, api=None, key="ADMIN-KEY"):
    from app.ui.settings import AdminPanel
    return AdminPanel(api=api or FakeAPI(), key_source=lambda: key)


def idle(p, timeout=10.0):
    """Wait for a console action. They run off the UI thread now, so the
    next line after a click would otherwise read the state before the answer."""
    import time

    deadline = time.monotonic() + timeout
    while not p.btn_issue.isEnabled():
        if time.monotonic() > deadline:
            raise AssertionError("the console action never finished")
        QApplication.processEvents()
        time.sleep(0.005)


def test_console_actions_run_off_the_ui_thread_with_the_buttons_held(qapp):
    """Each is a round trip to the Worker. A revoke pressed while a list is
    still arriving would act on row indexes about to be replaced."""
    import threading

    release, seen = threading.Event(), {}

    class Slow(FakeAPI):
        def list_codes(self, key):
            seen["thread"] = threading.current_thread()
            release.wait(5)
            return self.codes

    p = panel(qapp, Slow())
    p.refresh()
    assert not (p.btn_issue.isEnabled() or p.btn_refresh.isEnabled()
                or p.btn_revoke.isEnabled())
    release.set()
    idle(p)
    assert seen["thread"] is not threading.main_thread()
    assert p.codes.count() == 1
    p.close()


def test_existing_codes_show_their_use_count(qapp):
    """A code's usage is the thing you need to see before withdrawing it."""
    p = panel(qapp)
    p.refresh()
    idle(p)
    assert p.codes.count() == 1
    assert "3/25" in p.codes.item(0).text()
    assert "Sean" in p.codes.item(0).text()
    p.close()


def test_a_code_without_a_note_is_refused(qapp):
    """An unlabelled code cannot be accounted for later."""
    api = FakeAPI()
    p = panel(qapp, api)
    p.note.setText("   ")
    p.btn_issue.click()
    idle(p)
    assert api.issued == []
    assert p.result.text()
    p.close()


def test_choosing_managed_stops_a_reviewer_code_being_single_use(qapp):
    """THE WREN SCAR, in the interface. A reviewer may test on several
    machines, or re-test after a rejection; a spent code fails the review."""
    p = panel(qapp)
    assert p.uses.value() == 1
    p.role.setCurrentText("managed")
    assert p.uses.value() == FakeAPI.REVIEW_USES
    p.close()


def test_issuing_passes_the_note_role_and_uses(qapp):
    api = FakeAPI()
    p = panel(qapp, api)
    p.note.setText("App Review")
    p.role.setCurrentText("managed")
    p.btn_issue.click()
    idle(p)
    assert api.issued and api.issued[0][0] == "App Review"
    assert api.issued[0][1] == "managed"
    assert "DL-NEW" in p.result.text()
    p.close()


def test_revoking_without_a_selection_says_so(qapp):
    api = FakeAPI()
    p = panel(qapp, api)
    p.refresh()
    idle(p)
    p.codes.setCurrentRow(-1)
    p.btn_revoke.click()
    assert api.revoked == []
    p.close()


# -- expiry, and asking before a withdrawal ----------------------------------

def _days_from_now(stamp):
    from datetime import datetime, timezone

    when = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S.000Z").replace(
        tzinfo=timezone.utc)
    return (when - datetime.now(timezone.utc)).total_seconds() / 86_400


def test_a_managed_code_expires_in_about_a_month_by_default(qapp):
    """A reviewer's or friend's code that never expires is a standing grant
    nobody remembers issuing."""
    api = FakeAPI()
    p = panel(qapp, api)
    p.note.setText("App Review")
    p.role.setCurrentText("managed")
    assert p.expires.value() == 30
    p.btn_issue.click()
    idle(p)
    assert 29.9 < _days_from_now(api.expiries[0]) <= 30.0
    p.close()


def test_an_ordinary_code_cannot_be_set_never_to_expire(qapp):
    p = panel(qapp)
    p.role.setCurrentText("managed")
    p.expires.setValue(0)
    assert p.expires.value() >= 1
    p.close()


@pytest.mark.parametrize("role,plan", [("admin", "standard"), ("managed", "owner")])
def test_an_admin_or_owner_code_may_be_left_open_ended(qapp, role, plan):
    """The positive control for the refusal above."""
    api = FakeAPI()
    p = panel(qapp, api)
    p.note.setText("Spencer")
    p.role.setCurrentText(role)
    p.plan.setCurrentText(plan)
    p.expires.setValue(0)
    assert p.expires.value() == 0
    p.btn_issue.click()
    idle(p)
    assert api.expiries == [None]
    p.close()


def test_the_expiry_matches_the_workers_text_comparison():
    """The Worker compares `expires_at` with `toISOString()` as TEXT."""
    from datetime import datetime, timezone

    from app.core import admin

    stamp = admin.expiry_after(30, now=datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc))
    assert stamp == "2026-10-11T12:00:00.000Z"
    assert admin.expiry_after(0) is None


def test_revoking_asks_first_and_no_withdraws_nothing(qapp):
    from app.ui.settings import AdminPanel

    for answer, expected in ((False, []), (True, ["DL-1"])):
        api, asked = FakeAPI(), []
        p = AdminPanel(api=api, key_source=lambda: "ADMIN-KEY",
                       confirm=lambda code: asked.append(code) or answer)
        p.refresh()
        idle(p)
        p.codes.setCurrentRow(0)
        p.btn_revoke.click()
        idle(p)
        assert asked == ["DL-1"], "withdrew without asking"
        assert api.revoked == expected
        p.close()


def test_the_plans_offered_are_the_workers_plans():
    """This offered "pro", which the Worker has never had: a "pro" code
    silently became a trial."""
    import pathlib
    import re

    from app.core import admin

    plans_js = (pathlib.Path(__file__).resolve().parents[1] / "server"
                / "dawnlist-feed-worker" / "src" / "plans.js").read_text(encoding="utf-8")
    block = plans_js.split("export const PLANS = {", 1)[1].split("\n};", 1)[0]
    worker = set(re.findall(r"^  (\w+): \{", block, re.M))
    assert set(admin.PLANS) == worker


def test_the_console_stays_hidden_unless_the_server_says_admin(qapp):
    """And the check must not run at CONSTRUCTION: building a window reached
    the live Worker and made the suite depend on the network."""
    from app.ui.settings import SettingsWindow

    asked = []
    w = SettingsWindow(is_admin=lambda key: asked.append(key) or False)
    assert w.admin.isHidden()
    assert asked == [], "constructing a window must not reach the network"
    w.close()


def test_a_closed_window_does_not_crash_a_finishing_task(qapp):
    """`RuntimeError: Signal source has been deleted`, raised from the worker
    thread where nothing catches it. The answer is unwanted, not fatal."""
    from app.ui.background import _Task

    class Dead:
        def emit(self, _payload):
            raise RuntimeError("Signal source has been deleted")

    _Task._emit(Dead(), "result")      # must not raise
