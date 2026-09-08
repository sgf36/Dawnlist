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
