"""Settings. The app cannot be used without this screen, so it earns real tests."""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui.settings import KeyPanel, LicencePanel, SettingsWindow, masked  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def saved(panel, timeout=10.0):
    """Press save and wait for the answer.

    The check and the redemption run off the UI thread now, so asserting on
    the next line would read the "Checking…" state. Do not make them
    synchronous again to shorten this.
    """
    import time

    panel.save()
    deadline = time.monotonic() + timeout
    while not panel.button.isEnabled():
        if time.monotonic() > deadline:
            raise AssertionError("the save never finished")
        QApplication.processEvents()
        time.sleep(0.005)


def _wait_until(predicate, timeout=10.0):
    import time

    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("did not finish")
        QApplication.processEvents()
        time.sleep(0.005)


def test_the_key_check_runs_off_the_ui_thread_with_the_button_held(qapp):
    """The check is a round trip to Anthropic. On the UI thread the window
    stopped answering for its whole length; with the button live, a second
    press would start a second check."""
    import threading

    release, seen = threading.Event(), {}

    def slow(key):
        seen["thread"] = threading.current_thread()
        release.wait(5)
        return True, "Verified — 1 models available."

    panel = KeyPanel(verifier=slow, storer=lambda k: None, reader=lambda: None)
    panel.field.setText("sk-ant-api03-" + "x" * 40)
    panel.button.click()
    assert not panel.button.isEnabled()
    assert "Checking" in panel.result.text()

    release.set()
    _wait_until(panel.button.isEnabled)
    assert seen["thread"] is not threading.main_thread()
    assert "Verified" in panel.result.text()
    panel.close()


def test_redeeming_a_code_runs_off_the_ui_thread_with_the_button_held(qapp):
    import threading

    release, seen = threading.Event(), {}

    def slow(code):
        seen["thread"] = threading.current_thread()
        release.wait(5)
        return "DAWN-FROM-CODE"

    stored = []
    panel = LicencePanel(redeemer=slow, storer=stored.append, reader=lambda: None)
    panel.field.setText("DL-SLOW")
    panel.button.click()
    assert not panel.button.isEnabled()

    release.set()
    _wait_until(panel.button.isEnabled)
    assert seen["thread"] is not threading.main_thread()
    assert stored == ["DAWN-FROM-CODE"]
    panel.close()


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
    saved(panel)
    assert panel._saved["key"].startswith("sk-ant-")
    assert "Verified" in panel.result.text()


def test_a_rejected_key_is_NOT_stored(qapp):
    panel = key_panel(qapp, verify=lambda k: (False, "Anthropic rejected that key."))
    panel.field.setText("sk-ant-api03-wrong")
    saved(panel)
    assert "key" not in panel._saved
    assert "rejected" in panel.result.text().lower()


def test_a_rejected_key_stays_in_the_box(qapp):
    """So the user can see what they pasted and fix it, rather than starting
    again from nothing."""
    panel = key_panel(qapp, verify=lambda k: (False, "no"))
    panel.field.setText("sk-ant-typo")
    saved(panel)
    assert panel.field.text() == "sk-ant-typo"


def test_a_verified_key_is_cleared_from_the_box(qapp):
    panel = key_panel(qapp)
    panel.field.setText("sk-ant-api03-" + "x" * 40)
    saved(panel)
    assert panel.field.text() == ""


def test_the_key_field_is_masked_while_typing(qapp):
    from PySide6.QtWidgets import QLineEdit
    panel = key_panel(qapp)
    assert panel.field.echoMode() == QLineEdit.Password


def test_an_empty_save_does_nothing(qapp):
    calls = []
    panel = key_panel(qapp, verify=lambda k: calls.append(k) or (True, "ok"))
    panel.field.setText("   ")
    saved(panel)
    assert calls == []


def test_the_cost_is_stated_before_the_field(qapp):
    """Someone is about to attach their own billing account to this."""
    from PySide6.QtWidgets import QLabel
    panel = key_panel(qapp)
    cost = [l for l in panel.findChildren(QLabel) if l.objectName() == "costNote"]
    assert cost, "no cost note on the key panel"
    text = cost[0].text()
    # A magnitude, not a precise figure — see the reasoning on
    # test_the_cost_guidance_gives_a_magnitude_and_says_who_bills. Demanding a
    # "£" here protected three numbers that were never measured.
    assert "pound" in text.lower() and "no markup" in text


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
    saved(panel)
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
    saved(panel)
    assert seen["code"] == "DL-ABCD-EFGH"
    assert panel._saved["key"] == "DAWN-ISSUED"


def test_a_failed_redemption_reports_the_servers_reason(qapp):
    def refuse(code):
        raise RuntimeError("That code has already been used the maximum number of times.")

    panel = licence_panel(qapp, redeem=refuse)
    panel.field.setText("DL-SPENT")
    saved(panel)
    assert "already been used" in panel.result.text()
    assert "key" not in panel._saved


def test_a_failed_redemption_keeps_the_code_in_the_box(qapp):
    panel = licence_panel(qapp,
                          redeem=lambda c: (_ for _ in ()).throw(RuntimeError("no")))
    panel.field.setText("DL-BAD")
    saved(panel)
    assert panel.field.text() == "DL-BAD"


# -- what the server says about a saved licence ------------------------------
def test_a_saved_licence_is_verified_off_the_ui_thread_and_its_plan_shown(qapp):
    """"Licence saved." was said for a typo, a refund and a real key alike;
    the truth arrived at the next morning's run."""
    import threading

    seen, stored = {}, {}

    def check(key):
        seen["thread"], seen["key"] = threading.current_thread(), key
        return "ok", {"ok": True, "status": "active", "plan": "standard"}

    panel = LicencePanel(storer=lambda k: stored.update(key=k),
                         reader=lambda: stored.get("key"), checker=check)
    panel.field.setText("DAWN-AAAA-BBBB")
    saved(panel)
    _wait_until(lambda: "Verified" in panel.verdict.text())
    assert "standard" in panel.verdict.text()
    assert seen["key"] == "DAWN-AAAA-BBBB"
    assert seen["thread"] is not threading.main_thread()
    panel.close()


@pytest.mark.parametrize("answer,expected", [
    (("ok", {"ok": True, "plan": "global"}), "Verified"),
    (("refused", {"error": "licence_inactive"}), "no longer active"),
    (("refused", {"error": "unknown_licence"}), "does not recognise"),
    (("unreachable", {}), "not yet verified"),
])
def test_valid_inactive_unknown_and_unreachable_are_each_said(qapp, answer, expected):
    panel = LicencePanel(storer=lambda k: None, reader=lambda: None,
                         checker=lambda key: answer)
    panel.field.setText("DAWN-AAAA-BBBB")
    saved(panel)
    _wait_until(lambda: expected in panel.verdict.text())
    others = {"Verified", "no longer active", "does not recognise",
              "not yet verified"} - {expected}
    assert not any(o in panel.verdict.text() for o in others)
    panel.close()


def test_the_stored_licence_is_verified_when_the_panel_opens_not_when_built(qapp):
    asked = []
    panel = LicencePanel(reader=lambda: "DAWN-STORED",
                         checker=lambda key: asked.append(key) or (
                             "ok", {"ok": True, "plan": "standard"}))
    QApplication.processEvents()
    assert asked == [], "building a window must not reach the network"

    panel.show()
    _wait_until(lambda: "Verified" in panel.verdict.text())
    assert asked == ["DAWN-STORED"]
    panel.close()


# -- a credential store that will not save ----------------------------------
def _refusing_store(_key):
    from app.core.credentials import KeyringUnavailable
    raise KeyringUnavailable("locked")


def test_a_verified_key_that_cannot_be_saved_is_not_called_verified(qapp):
    """The user would close Settings believing they were set up.
    `test_a_verified_key_is_stored` is the positive control."""
    panel = KeyPanel(verifier=lambda k: (True, "Verified — 3 models available."),
                     storer=_refusing_store, reader=lambda: None)
    panel.field.setText("sk-ant-api03-" + "x" * 40)
    saved(panel)
    assert "Verified" not in panel.result.text()
    assert "could not save" in panel.result.text().lower()
    assert panel.field.text(), "left in the box so it can be saved again"
    panel.close()


def test_a_redeemed_code_that_cannot_be_saved_shows_the_licence(qapp):
    """The code may be single-use and is already spent, so the licence on
    screen is the only copy there is."""
    from PySide6.QtCore import Qt

    panel = LicencePanel(redeemer=lambda c: "DAWN-ONLY-COPY",
                         storer=_refusing_store, reader=lambda: None)
    panel.field.setText("DL-ONCE")
    saved(panel)
    assert "DAWN-ONLY-COPY" in panel.result.text()
    assert panel.result.textInteractionFlags() & Qt.TextSelectableByMouse
    panel.close()


def test_a_licence_key_that_cannot_be_saved_says_so(qapp):
    panel = LicencePanel(storer=_refusing_store, reader=lambda: None)
    panel.field.setText("DAWN-AAAA-BBBB")
    saved(panel)
    assert "could not save" in panel.result.text().lower()
    assert "Licence saved." not in panel.result.text()
    panel.close()


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


# -- the licence panel is build-dependent, and the two stores DIFFER --------
def test_a_windows_store_build_DOES_show_the_licence_box(qapp):
    """Microsoft permits third-party commerce, subject to declaring it.

    This was hidden on Windows too, by applying Apple's rule to both stores.
    That is what left a paying Store customer with no route to the feed: the
    subscription is bought on the website, the licence arrives by email, and
    there was nowhere to put it.
    """
    w = SettingsWindow(variant="store")
    assert w.shows_licence
    w.close()


def test_a_direct_download_build_shows_the_licence_box(qapp):
    w = SettingsWindow(variant="direct")
    assert w.shows_licence
    w.close()


def test_the_mac_app_store_build_shows_NO_licence_box(qapp):
    """Apple guideline 3.1.1 names licence keys as a prohibited mechanism, so
    a MAS build must sell through StoreKit and never accept a key. This is the
    one place the two stores genuinely diverge."""
    w = SettingsWindow(variant="mas")
    assert not w.shows_licence
    assert w.licence is not None, "the attribute must stay stable for callers"
    w.close()


@pytest.mark.parametrize("build", ["none", "ambiguous"])
def test_a_badly_packaged_build_shows_no_licence_box(qapp, build):
    """It was "anything but mas", so a copy with no flag — which may well be a
    Mac build — offered the key box guideline 3.1.1 forbids. The store and
    direct tests above are the positive controls."""
    w = SettingsWindow(variant=build)
    assert not w.shows_licence
    assert not w.shows_subscribe
    w.close()


# -- the rules panel --------------------------------------------------------
def rules_panel(qapp, table=None, *, saver=None):
    from app.core.rules import RuleTable
    from app.ui.settings import RulesPanel

    state = table if table is not None else RuleTable()
    saved = []

    def default_saver(field, term):
        getattr(state, field).append(term)
        saved.append((field, term))

    panel = RulesPanel(loader=lambda: state,
                       saver=saver or default_saver,
                       forgetter=lambda field, term: (
                           getattr(state, field).remove(term),
                           saved.append(("-", term))))
    panel._state, panel._log = state, saved
    return panel


def test_the_three_writable_tiers_are_offered(qapp):
    """Not four: `known_employers` is derived from pursue decisions, and a
    hand-typed copy would drift the moment one was revised."""
    from app.ui.settings import RULE_TIERS
    assert [f for f, _, _ in RULE_TIERS] == [
        "unsupported_titles", "strong_terms", "contextual_terms"]

    panel = rules_panel(qapp)
    assert set(panel._lists) == set(f for f, _, _ in RULE_TIERS)
    panel.close()


def test_the_earned_employers_are_shown_but_not_editable(qapp):
    """Showing them makes the earning mechanism visible; letting someone type
    into the list would create a second, drifting copy of the decisions."""
    from app.core.rules import RuleTable
    panel = rules_panel(qapp, RuleTable(known_employers=["Meridian Group"]))
    assert panel.employers.count() == 1
    assert panel.employers.item(0).text() == "Meridian Group"
    assert not panel.employers.isEnabled()
    panel.close()


def test_a_term_is_added_and_the_list_reloads(qapp):
    panel = rules_panel(qapp)
    panel._fields["strong_terms"].setText("asset management")
    panel.add("strong_terms")
    assert panel._log == [("strong_terms", "asset management")]
    assert panel._lists["strong_terms"].item(0).text() == "asset management"
    assert panel._fields["strong_terms"].text() == ""
    panel.close()


def test_an_empty_add_does_nothing(qapp):
    panel = rules_panel(qapp)
    panel._fields["strong_terms"].setText("   ")
    panel.add("strong_terms")
    assert panel._log == []
    panel.close()


def test_a_refusal_names_the_role_it_would_have_cost(qapp):
    """The useful information is not "invalid term", it is "you chased this
    exact job". Reducing the conflict to a red border throws away the only
    finding the admission guard exists to produce."""
    from app.core.rules import RuleConflict, RuleConflictError

    def refuse(field, term):
        raise RuleConflictError([RuleConflict(
            term="operations", field="unsupported_titles",
            pursued_title="Head of Operations", company="Meridian Group")])

    panel = rules_panel(qapp, saver=refuse)
    panel._fields["unsupported_titles"].setText("operations")
    panel.add("unsupported_titles")

    text = panel.result.text()
    assert "Head of Operations" in text
    assert "Meridian Group" in text
    assert panel.result.isVisibleTo(panel)
    panel.close()


def test_a_refused_term_stays_in_the_box(qapp):
    """So it can be edited into something narrower, rather than retyped."""
    from app.core.rules import RuleConflict, RuleConflictError

    def refuse(field, term):
        raise RuleConflictError([RuleConflict("operations", field, "Head of "
                                              "Operations", "Acme")])

    panel = rules_panel(qapp, saver=refuse)
    panel._fields["unsupported_titles"].setText("operations")
    panel.add("unsupported_titles")
    assert panel._fields["unsupported_titles"].text() == "operations"
    panel.close()


def test_removing_takes_the_selected_term(qapp):
    from app.core.rules import RuleTable
    panel = rules_panel(qapp, RuleTable(strong_terms=["asset", "strategy"]))
    panel._lists["strong_terms"].setCurrentRow(1)
    panel.remove("strong_terms")
    assert panel._log == [("-", "strategy")]
    assert panel._lists["strong_terms"].count() == 1
    panel.close()


def test_removing_nothing_selected_does_nothing(qapp):
    from app.core.rules import RuleTable
    panel = rules_panel(qapp, RuleTable(strong_terms=["asset"]))
    panel._lists["strong_terms"].setCurrentRow(-1)
    panel.remove("strong_terms")
    assert panel._log == []
    panel.close()


def test_the_window_omits_the_rules_panel_without_a_database(qapp):
    """Rules are per-user and live in the database. A panel wired to nothing
    would offer to save terms and silently drop them."""
    w = SettingsWindow(variant="direct")
    assert w.rules is None
    w.close()


def test_the_window_carries_the_rules_panel_when_given_one(qapp):
    panel = rules_panel(qapp)
    w = SettingsWindow(variant="direct", rules=panel)
    assert w.rules is panel
    assert panel.isVisibleTo(w)
    w.close()


def test_the_window_scrolls(qapp):
    """Three stacked panels want ~916px and a 768-tall laptop is ordinary.
    Without a scroll area the licence box sits below the bottom of the display
    on the one build that needs it, unreachable — and a user who cannot enter
    their licence key has bought something inert."""
    panel = rules_panel(qapp)
    w = SettingsWindow(variant="direct", rules=panel)
    w.resize(1120, 640)
    w.show()
    for _ in range(4):
        qapp.processEvents()
    assert w.scroll.verticalScrollBar().maximum() > 0
    w.close()


def test_the_window_opens_shorter_than_a_laptop_screen(qapp):
    panel = rules_panel(qapp)
    w = SettingsWindow(variant="direct", rules=panel)
    assert w.height() <= 720, f"opens at {w.height()}px tall"
    w.close()


def test_the_window_cannot_be_shrunk_to_a_stub(qapp):
    """A scroll area reports a tiny minimum of its own."""
    panel = rules_panel(qapp)
    w = SettingsWindow(variant="direct", rules=panel)
    assert w.minimumWidth() >= 700 and w.minimumHeight() >= 400
    w.close()


# -- kill families ----------------------------------------------------------
def a_family(name="Kier", adopted=False):
    from app.core.rules import KillFamily
    return KillFamily(name=name, employers=(name,), kill_titles=("engineer",),
                      saves_titles=("strategy",),
                      precedents=((name, "Site Engineer"),
                                  (name, "Senior Site Engineer")),
                      adopted=adopted)


def families_panel(qapp, families=None, *, adopter=None):
    from app.ui.settings import FamiliesPanel
    state = list(families or [])
    log = []

    def default_adopter(name, on):
        log.append((name, on))

    panel = FamiliesPanel(loader=lambda: state,
                          adopter=adopter or default_adopter,
                          refresher=lambda: len(state))
    panel._state, panel._log = state, log
    return panel


def test_a_proposal_shows_the_rejections_it_came_from(qapp):
    """"Kier + engineer" is a rule to agree with in the abstract. "You turned
    down these two" is a decision the user can actually check."""
    panel = families_panel(qapp, [a_family()])
    text = panel.listing.item(0).text()
    assert "Kier" in text
    assert "Site Engineer" in text
    assert "engineer" in text and "strategy" in text
    panel.close()


def test_a_proposal_is_marked_as_not_yet_armed(qapp):
    panel = families_panel(qapp, [a_family(adopted=False)])
    assert "Proposed" in panel.listing.item(0).text()
    panel.close()


def test_an_armed_family_says_so(qapp):
    panel = families_panel(qapp, [a_family(adopted=True)])
    assert "Armed" in panel.listing.item(0).text()
    panel.close()


def test_nothing_proposed_says_why_not(qapp):
    panel = families_panel(qapp, [])
    assert panel.empty.isVisibleTo(panel)
    assert "two similar roles" in panel.empty.text()
    panel.close()


def test_arming_needs_a_selection(qapp):
    panel = families_panel(qapp, [a_family()])
    assert not panel.btn_adopt.isEnabled()
    panel.listing.setCurrentRow(0)
    assert panel.btn_adopt.isEnabled()
    panel.close()


def test_arming_and_standing_down_reach_the_adopter(qapp):
    panel = families_panel(qapp, [a_family()])
    panel.listing.setCurrentRow(0)
    panel.set_adopted(True)
    panel.listing.setCurrentRow(0)
    panel.set_adopted(False)
    assert panel._log == [("Kier", True), ("Kier", False)]
    panel.close()


def test_a_refused_adoption_names_the_role(qapp):
    """The same guard as a tier-1 term, and the same reason for naming it."""
    from app.core.rules import RuleConflict, RuleConflictError

    def refuse(name, on):
        raise RuleConflictError([RuleConflict(
            "engineer", "kill_families", "Site Engineer, Strategy", "Kier")])

    panel = families_panel(qapp, [a_family()], adopter=refuse)
    panel.listing.setCurrentRow(0)
    panel.set_adopted(True)
    assert "Site Engineer, Strategy" in panel.result.text()
    assert panel.result.isVisibleTo(panel)
    panel.close()


def test_the_window_omits_the_families_panel_without_a_database(qapp):
    w = SettingsWindow(variant="direct")
    assert w.families is None
    w.close()


def test_looking_for_proposals_has_a_button(qapp):
    """It was written and reachable from nowhere — the same fault the audit
    that produced this panel exists to find."""
    panel = families_panel(qapp, [a_family()])
    found = []
    panel._refresh = lambda: found.append(1) or 1
    panel.btn_look.click()
    assert found == [1]
    assert panel.result.isVisibleTo(panel)
    panel.close()


def test_finding_nothing_says_so(qapp):
    """Silence after pressing a button reads as a broken button."""
    panel = families_panel(qapp, [])
    panel._refresh = lambda: 0
    panel.btn_look.click()
    assert "Nothing new" in panel.result.text()
    panel.close()


# -- saved searches ---------------------------------------------------------
def searches_panel(qapp, rows=None):
    from app.ui.settings import SearchesPanel
    state = list(rows or [])
    log = []

    def save(label, titles):
        state.append((label, titles, True))
        log.append(("add", label))

    def forget(label):
        state[:] = [r for r in state if r[0] != label]
        log.append(("remove", label))

    def enable(label, on):
        state[:] = [(l, t, on if l == label else e) for l, t, e in state]
        log.append(("enable", label, on))

    panel = SearchesPanel(loader=lambda: state, saver=save,
                          forgetter=forget, enabler=enable)
    panel._state, panel._log = state, log
    return panel


def test_a_search_shows_whether_it_is_switched_on(qapp):
    """A search left on costs money every morning, because the feed bills per
    posting returned."""
    panel = searches_panel(qapp, [("asset management", ["asset manager"], False)])
    assert "off" in panel.listing.item(0).text()
    assert "asset manager" in panel.listing.item(0).text()
    panel.close()


def test_a_search_can_be_added(qapp):
    panel = searches_panel(qapp)
    panel.field.setText("hotel strategy")
    panel.add()
    assert panel._log == [("add", "hotel strategy")]
    assert panel.listing.count() == 1
    assert panel.field.text() == ""
    panel.close()


def test_an_empty_search_is_not_added(qapp):
    panel = searches_panel(qapp)
    panel.field.setText("   ")
    panel.add()
    assert panel._log == []
    panel.close()


def test_a_search_can_be_switched_on(qapp):
    panel = searches_panel(qapp, [("asset management", ["asset manager"], False)])
    panel.listing.setCurrentRow(0)
    panel.toggle()
    assert panel._log == [("enable", "asset management", True)]
    assert "ON" in panel.listing.item(0).text()
    panel.close()


def test_switching_says_what_it_will_cost_you(qapp):
    """Silence would leave the user unsure whether it now costs money."""
    panel = searches_panel(qapp, [("asset management", ["asset manager"], False)])
    panel.listing.setCurrentRow(0)
    panel.toggle()
    assert "swept each morning" in panel.result.text()
    panel.close()


def test_a_search_can_be_removed(qapp):
    panel = searches_panel(qapp, [("asset management", ["asset manager"], True)])
    panel.listing.setCurrentRow(0)
    panel.remove()
    assert panel.listing.count() == 0
    panel.close()


def test_where_you_want_to_work_is_applied_to_every_search(qapp):
    from app.ui.settings import SearchesPanel

    saved = []
    panel = SearchesPanel(where_loader=lambda: saved[-1] if saved else "",
                          where_saver=saved.append)
    panel.where_field.setText("London, GB")
    panel.apply_where()
    assert saved == ["London, GB"]
    assert "London, GB" in panel.result.text()
    panel.close()


def test_a_location_that_cannot_be_used_says_why(qapp):
    from app.ui.settings import SearchesPanel

    def refuse(text):
        raise ValueError("Add the two-letter country code, for example: London, GB")

    panel = SearchesPanel(where_saver=refuse)
    panel.where_field.setText("London")
    panel.apply_where()
    assert "country code" in panel.result.text()
    panel.close()


def test_switching_on_a_search_with_no_location_says_why(qapp):
    """A refusal that raised out of a button handler looked like a dead button."""
    from app.ui.settings import SearchesPanel

    def refuse(label, on):
        raise ValueError(f"{label} cannot be switched on until you set where")

    panel = SearchesPanel(
        loader=lambda: [("asset management", ["asset manager"], False)],
        enabler=refuse)
    panel.listing.setCurrentRow(0)
    panel.toggle()
    assert "cannot be switched on" in panel.result.text()
    assert "off" in panel.listing.item(0).text(), "and it stays off"
    panel.close()


def test_the_window_omits_searches_without_a_database(qapp):
    w = SettingsWindow(variant="direct")
    assert w.searches is None
    w.close()


# ---------------------------------------------------------------------------
# The API-key step is the biggest adoption barrier; make it clickable
# ---------------------------------------------------------------------------

def test_the_console_address_is_a_real_link(qapp):
    """Not decoration. "Go and get a key from a company you have not heard of"
    is the largest single obstacle between a buyer and a working app, and the
    address was plain text they had to retype into a browser."""
    from PySide6.QtWidgets import QLabel
    panel = key_panel(qapp)
    body = [l for l in panel.findChildren(QLabel) if l.objectName() == "stepBody"]
    assert body, "no explanatory body on the key panel"
    html = body[0].text()
    assert "<a href=" in html
    assert "console.anthropic.com" in html
    assert body[0].openExternalLinks(), "a link that does not open is not a link"


def test_it_deep_links_to_the_keys_page_not_the_dashboard():
    """The front page lands on a dashboard several clicks from the keys page."""
    from app.ui.settings import CONSOLE_KEYS_URL
    assert CONSOLE_KEYS_URL.endswith("/settings/keys")
    assert CONSOLE_KEYS_URL.startswith("https://")


def test_linkify_leaves_text_alone_when_the_address_is_absent():
    """A future rewording that drops the address must degrade to plain text,
    not to a broken link or an exception."""
    from app.ui.settings import _linkify_console
    assert _linkify_console("no address here") == "no address here"


def test_linkify_escapes_the_surrounding_text():
    """The label is rich text once linkified, so anything around the anchor
    has to be escaped or a stray angle bracket silently eats the sentence."""
    from app.ui.settings import _linkify_console
    out = _linkify_console("a < b at console.anthropic.com & c")
    assert "&lt;" in out and "&amp;" in out


# ---------------------------------------------------------------------------
# The data-source notice: two obligations from the feed licence
# ---------------------------------------------------------------------------

def test_the_data_notice_is_shown_on_every_build(qapp):
    """Attribution and the flow-through terms apply regardless of storefront."""
    from PySide6.QtWidgets import QLabel
    for build in ("direct", "store", "mas"):
        w = SettingsWindow(variant=build)
        links = [l for l in w.findChildren(QLabel) if l.objectName() == "dataTerms"]
        assert links, f"no data-terms notice on the {build} build"
        w.close()


def test_the_terms_are_reachable_and_open(qapp):
    """A link nobody can follow does not help someone who has already agreed."""
    from PySide6.QtWidgets import QLabel
    w = SettingsWindow(variant="direct")
    link = [l for l in w.findChildren(QLabel) if l.objectName() == "dataTerms"][0]
    assert "<a href=" in link.text()
    assert link.openExternalLinks()
    w.close()


def test_the_notice_states_the_restrictions_it_has_to_pass_on(qapp):
    """The feed licence requires downstream recipients to be bound by terms at
    least as restrictive as its own. The app cannot bind anyone — acceptance
    happens at purchase — but the substance has to be findable afterwards by
    someone who already agreed to it."""
    from PySide6.QtWidgets import QLabel
    w = SettingsWindow(variant="direct")
    body = [l for l in w.findChildren(QLabel)
            if l.objectName() == "stepBody" and "licensed" in l.text().lower()]
    assert body, "no explanation of where the postings come from"
    text = body[0].text().lower()
    for restriction in ("redistribute", "resell", "competing"):
        assert restriction in text, f"the notice does not mention {restriction}"
    w.close()
