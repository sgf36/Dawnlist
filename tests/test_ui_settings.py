"""Settings. The app cannot be used without this screen, so it earns real tests."""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui.settings import KeyPanel, LicencePanel, SettingsWindow, masked  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def key_panel(qapp, *, verify=None, stored=None):
    saved = {}
    panel = KeyPanel(
        verifier=verify or (lambda k: (True, "Verified — 3 models available.")),
        storer=lambda k: saved.update(key=k),
        reader=lambda: saved.get("key", stored))
    panel._saved = saved
    return panel


# -- masking ----------------------------------------------------------------
def test_a_stored_key_is_never_shown_in_full():
    """Re-displaying a secret so the user can check it is how it ends up in a
    screenshot."""
    key = "sk-ant-api03-" + "S" * 80 + "TAIL"
    shown = masked(key)
    assert key not in shown
    assert len(shown) < 25
    assert shown.startswith("sk-ant-api")


def test_the_mask_still_identifies_which_key_it_is():
    a = masked("sk-ant-api03-" + "A" * 60 + "1234")
    b = masked("sk-ant-api03-" + "B" * 60 + "9876")
    assert a != b, "a user with several keys must be able to tell them apart"


def test_a_short_value_is_fully_masked():
    assert set(masked("short")) == {"•"}


# -- the key panel ----------------------------------------------------------
def test_it_says_plainly_when_no_key_is_stored(qapp):
    panel = key_panel(qapp)
    assert "cannot run without one" in panel.stored.text()


def test_a_verified_key_is_stored(qapp):
    panel = key_panel(qapp)
    panel.field.setText("sk-ant-api03-" + "x" * 40)
    panel.save()
    assert panel._saved["key"].startswith("sk-ant-")
    assert "Verified" in panel.result.text()


def test_a_rejected_key_is_NOT_stored(qapp):
    panel = key_panel(qapp, verify=lambda k: (False, "Anthropic rejected that key."))
    panel.field.setText("sk-ant-api03-wrong")
    panel.save()
    assert "key" not in panel._saved
    assert "rejected" in panel.result.text().lower()


def test_a_rejected_key_stays_in_the_box(qapp):
    """So the user can see what they pasted and fix it, rather than starting
    again from nothing."""
    panel = key_panel(qapp, verify=lambda k: (False, "no"))
    panel.field.setText("sk-ant-typo")
    panel.save()
    assert panel.field.text() == "sk-ant-typo"


def test_a_verified_key_is_cleared_from_the_box(qapp):
    panel = key_panel(qapp)
    panel.field.setText("sk-ant-api03-" + "x" * 40)
    panel.save()
    assert panel.field.text() == ""


def test_the_key_field_is_masked_while_typing(qapp):
    from PySide6.QtWidgets import QLineEdit
    panel = key_panel(qapp)
    assert panel.field.echoMode() == QLineEdit.Password


def test_an_empty_save_does_nothing(qapp):
    calls = []
    panel = key_panel(qapp, verify=lambda k: calls.append(k) or (True, "ok"))
    panel.field.setText("   ")
    panel.save()
    assert calls == []


def test_the_cost_is_stated_before_the_field(qapp):
    """Someone is about to attach their own billing account to this."""
    from PySide6.QtWidgets import QLabel
    panel = key_panel(qapp)
    cost = [l for l in panel.findChildren(QLabel) if l.objectName() == "costNote"]
    assert cost, "no cost note on the key panel"
    text = cost[0].text()
    assert "£" in text and "no markup" in text


def test_it_says_where_the_key_is_kept(qapp):
    from PySide6.QtWidgets import QLabel
    panel = key_panel(qapp)
    body = " ".join(l.text() for l in panel.findChildren(QLabel))
    assert "credential manager" in body.lower()
    assert "revoke it at any time" in body


# -- the licence panel ------------------------------------------------------
def licence_panel(qapp, *, redeem=None, stored=None):
    saved = {}
    panel = LicencePanel(
        redeemer=redeem or (lambda c: "DAWN-FROM-CODE"),
        storer=lambda k: saved.update(key=k),
        reader=lambda: saved.get("key", stored))
    panel._saved = saved
    return panel


def test_a_licence_key_is_stored_directly(qapp):
    panel = licence_panel(qapp)
    panel.field.setText("DAWN-AAAA-BBBB")
    panel.save()
    assert panel._saved["key"] == "DAWN-AAAA-BBBB"


def test_an_override_code_is_exchanged_for_a_licence(qapp):
    """One field for both: a user does not care which they were given."""
    seen = {}

    def redeem(code):
        # Not a lambda with setdefault: setdefault RETURNS the value, so
        # `setdefault(...) or "DAWN-ISSUED"` short-circuits and returns the code.
        seen["code"] = code
        return "DAWN-ISSUED"

    panel = licence_panel(qapp, redeem=redeem)
    panel.field.setText("DL-ABCD-EFGH")
    panel.save()
    assert seen["code"] == "DL-ABCD-EFGH"
    assert panel._saved["key"] == "DAWN-ISSUED"


def test_a_failed_redemption_reports_the_servers_reason(qapp):
    def refuse(code):
        raise RuntimeError("That code has already been used the maximum number of times.")

    panel = licence_panel(qapp, redeem=refuse)
    panel.field.setText("DL-SPENT")
    panel.save()
    assert "already been used" in panel.result.text()
    assert "key" not in panel._saved


def test_a_failed_redemption_keeps_the_code_in_the_box(qapp):
    panel = licence_panel(qapp,
                          redeem=lambda c: (_ for _ in ()).throw(RuntimeError("no")))
    panel.field.setText("DL-BAD")
    panel.save()
    assert panel.field.text() == "DL-BAD"


# -- the window -------------------------------------------------------------
def test_the_window_carries_both_panels(qapp):
    w = SettingsWindow()
    assert isinstance(w.key, KeyPanel)
    assert isinstance(w.licence, LicencePanel)
    w.close()


# -- removing a key ---------------------------------------------------------
def test_a_stored_key_can_be_removed(qapp):
    """Someone who revokes the key at Anthropic, or hands the machine on, had
    no way to clear it from here. The app kept a dead credential and said
    nothing."""
    panel = key_panel(qapp, stored="sk-ant-api03-" + "x" * 40)
    assert panel.button_forget.isVisibleTo(panel)
    panel._forget = lambda: panel._saved.update(key=None)
    panel.forget()
    assert panel._saved["key"] is None
    assert "cannot run without one" in panel.stored.text()


def test_the_remove_button_is_hidden_with_no_key(qapp):
    """Nothing to remove, so nothing offering to."""
    panel = key_panel(qapp)
    assert not panel.button_forget.isVisibleTo(panel)


def test_removing_says_what_it_means(qapp):
    panel = key_panel(qapp, stored="sk-ant-api03-" + "x" * 40)
    panel._forget = lambda: panel._saved.update(key=None)
    panel.forget()
    assert "cannot run" in panel.result.text()


def test_both_buttons_keep_the_same_metrics(qapp):
    """Styling one QPushButton and not the other drops native metrics for the
    styled one, and they end up different heights on the same row."""
    panel = key_panel(qapp, stored="sk-ant-api03-" + "x" * 40)
    panel.resize(900, 400)
    panel.show()
    assert panel.button.sizeHint().height() == \
        panel.button_forget.sizeHint().height()
    panel.close()


# -- the licence panel is build-dependent -----------------------------------
def test_a_store_build_shows_no_licence_box(qapp):
    """Entitlement there is by possession and no licence key exists. Asking for
    one sends a paying user hunting for something nobody sent them."""
    w = SettingsWindow(variant="store")
    assert not w.shows_licence
    assert w.licence is not None, "the attribute must stay stable for callers"
    w.close()


def test_a_direct_download_build_shows_the_licence_box(qapp):
    w = SettingsWindow(variant="direct")
    assert w.shows_licence
    w.close()


def test_the_mac_app_store_build_matches_the_windows_one(qapp):
    w = SettingsWindow(variant="mas")
    assert not w.shows_licence
    w.close()
