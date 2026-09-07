"""Every command reaches the code it claims to.

These are the tests that were missing when `tools/audit_wiring.py` found two
whole capabilities with no route into them: the review window opened with a
hard-coded empty list however much the last run had found, and outreach had no
entry point at all — `due_today` and `prepare_drafts` were complete and tested
and unreachable from anywhere a user could get to.

Neither was a logic fault, so no unit test could have caught either. What
catches this class is asking, of each capability, *how does a person start it*.
"""
from datetime import date
from pathlib import Path

import pytest

from app.core import db
from app.core.board_repo import create_opportunity
from app.core.pipeline import persist, run_morning
from app.core.rules import RuleTable
from app.feed.base import FeedProvider, FetchResult, SearchQuery
from app.feed.models import Job
from app.main import NotConfigured, load_voice, main, outreach_run, voice_dir
from app.onboarding.interview import save_document
from app.ui.adapter import latest_run_id, record_decision, rows_from_db

TUE = date(2026, 9, 8)

FACTS = "Spencer Fields. Cornell SHA 2019."
BODY = ("I am an individual exploring roles and I am not selling anything. "
        "Could we speak briefly?")


@pytest.fixture(scope="module")
def qapp_or_skip():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.sqlite3")
    db.migrate(c)
    yield c
    c.close()


class Stub(FeedProvider):
    name = "theirstack"

    def __init__(self, jobs):
        self.jobs = jobs

    def search(self, query):
        return FetchResult(jobs=self.jobs, pages_fetched=1, exhausted=True,
                           credits_estimate=len(self.jobs))

    def credits_used(self):
        return 0


def verdicts(bucket):
    import re

    def send(request):
        refs = re.findall(r'ref="([^"]+)"', request["messages"][0]["content"])
        return {"verdicts": [{"job_ref": r, "bucket": bucket,
                              "reason": "fits", "disqualifying_quote": None,
                              "requirement_checked": True} for r in refs]}
    return send


def a_run(conn, jobs):
    outcome = run_morning(
        conn, Stub(jobs), [SearchQuery(label="q", titles=["strategy"])],
        RuleTable(strong_terms=["strategy"]), fit_brief="b", factsheet=FACTS,
        send=verdicts("strong"))
    persist(conn, outcome)
    return outcome


# -- the shortlist survives the process that found it -----------------------
def test_the_last_runs_rows_come_back_from_the_database(conn):
    """The window used to be loaded with a literal empty list. Every posting
    the run assessed was on disk and nothing put it on screen."""
    a_run(conn, [Job(provider="theirstack", provider_job_id="a",
                     title="Head of Strategy", company="Acme",
                     description_text="Strategy.")])

    rows = rows_from_db(conn)
    assert len(rows) == 1
    assert rows[0].title == "Head of Strategy"
    assert rows[0].bucket == "strong"
    assert rows[0].job_id == "theirstack:a"


def test_no_run_yet_is_no_rows_rather_than_an_error(conn):
    assert latest_run_id(conn) is None
    assert rows_from_db(conn) == []


def test_a_decided_posting_leaves_the_pile(conn):
    """A list that does not shrink as it is worked is one people stop working."""
    a_run(conn, [Job(provider="theirstack", provider_job_id="a",
                     title="Head of Strategy", company="Acme",
                     description_text="Strategy."),
                 Job(provider="theirstack", provider_job_id="b",
                     title="Strategy Lead", company="Beta",
                     description_text="Strategy.")])
    assert len(rows_from_db(conn)) == 2

    record_decision(conn, "theirstack:a", "pursue")
    assert [r.job_id for r in rows_from_db(conn)] == ["theirstack:b"]
    assert len(rows_from_db(conn, include_decided=True)) == 2


def test_a_screened_out_posting_is_still_a_row(conn):
    """spec 5.4 — the unlikely pile is browsable, or an over-aggressive rule
    shows up as months of silence rather than as rows to disagree with."""
    a_run(conn, [Job(provider="theirstack", provider_job_id="n",
                     title="Night Auditor", company="Acme",
                     description_text="Front desk, overnight.")])
    rows = rows_from_db(conn)
    assert len(rows) == 1 and rows[0].bucket == "screened-out"


# -- outreach has a way in --------------------------------------------------
def test_draft_is_a_command(capsys, tmp_path, monkeypatch):
    """`--draft` existed nowhere. The app could not produce its own output."""
    assert main(["--draft", "--db", str(tmp_path / "x.sqlite3")]) == 2
    assert "not configured" in capsys.readouterr().err


def test_outreach_refuses_without_a_factsheet(conn):
    """Every claim in a draft has to come from it. Without one the draft is
    either empty or invented, and inventing is the worse failure."""
    with pytest.raises(NotConfigured, match="factsheet"):
        outreach_run(conn, send=lambda _r: BODY, today=TUE)


def test_outreach_writes_a_file_and_sends_nothing(conn, tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.entitlement.require", lambda c: None)
    save_document(conn, "factsheet", FACTS)
    oid = create_opportunity(conn, "Acme")
    conn.execute("INSERT INTO contacts(opportunity_id, name, email, created_at)"
                 " VALUES(?,?,?,?)", (oid, "Jo", "jo@example.com", "x"))

    report = outreach_run(conn, send=lambda _r: BODY, today=TUE,
                          folder=tmp_path / "drafts")

    assert report.counts["drafted"] == 1
    assert report.counts["send_ready"] == 1
    written = list((tmp_path / "drafts").glob("*.eml"))
    assert len(written) == 1


def test_the_drafts_folder_is_not_a_synced_one():
    """A draft is unsent correspondence and has no business in OneDrive."""
    from app.main import drafts_dir
    assert "OneDrive" not in str(drafts_dir())


# -- the voice corpus round-trips ------------------------------------------
def test_a_missing_voice_corpus_still_yields_a_profile(conn):
    """In nobody's particular voice is a fine answer. No drafts is not."""
    assert load_voice(conn) is not None


def test_sent_mail_in_the_voice_folder_is_measured(conn, tmp_path):
    folder = tmp_path / "voice"
    folder.mkdir()
    (folder / "one.txt").write_text(
        "Hi Jo,\n\nThanks for the introduction. Best, Spencer\n",
        encoding="utf-8")
    assert load_voice(conn, folder) is not None


def test_an_empty_voice_folder_is_not_an_error(conn, tmp_path):
    (tmp_path / "voice").mkdir()
    assert load_voice(conn, tmp_path / "voice") is not None


def test_the_voice_folder_is_not_a_synced_one():
    """Sent correspondence, sitting on disk. Not in OneDrive."""
    assert "OneDrive" not in str(voice_dir())


# -- deciding actually does something ---------------------------------------
def test_pursue_puts_the_employer_on_the_board(conn):
    """Pursue used to write a `decisions` row and stop. Nothing reached the
    board, so nothing came due, so no draft was ever written."""
    from app.core.board_repo import load_board
    a_run(conn, [Job(provider="theirstack", provider_job_id="a",
                     title="Head of Strategy", company="Meridian Group",
                     description_text="Strategy.")])
    assert load_board(conn) == []

    record_decision(conn, "theirstack:a", "pursue")
    board = load_board(conn)
    assert len(board) == 1
    assert board[0].company == "Meridian Group"


def test_rejecting_puts_nothing_on_the_board(conn):
    from app.core.board_repo import load_board
    a_run(conn, [Job(provider="theirstack", provider_job_id="a",
                     title="Head of Strategy", company="Acme",
                     description_text="Strategy.")])
    record_decision(conn, "theirstack:a", "reject")
    assert load_board(conn) == []


def test_changing_your_mind_does_not_open_a_second_record(conn):
    from app.core.board_repo import load_board
    a_run(conn, [Job(provider="theirstack", provider_job_id="a",
                     title="Head of Strategy", company="Acme",
                     description_text="Strategy.")])
    record_decision(conn, "theirstack:a", "pursue")
    record_decision(conn, "theirstack:a", "later")
    record_decision(conn, "theirstack:a", "pursue")
    assert len(load_board(conn)) == 1


def test_two_postings_at_one_employer_share_one_pursuit(conn):
    """spec 9.5. Two live records for one employer is how the same person gets
    written to twice in a week."""
    from app.core.board_repo import load_board
    a_run(conn, [Job(provider="theirstack", provider_job_id="a",
                     title="Head of Strategy", company="Acme",
                     description_text="Strategy."),
                 Job(provider="theirstack", provider_job_id="b",
                     title="Strategy Director", company="Acme",
                     description_text="Strategy.")])
    record_decision(conn, "theirstack:a", "pursue")
    record_decision(conn, "theirstack:b", "pursue")
    assert len(load_board(conn)) == 1


# -- settings is reachable --------------------------------------------------
def test_the_review_window_offers_settings(qapp_or_skip):
    """SettingsWindow existed with no way to open it. On a bring-your-own-key
    app that means a rotated or mistyped key leaves the whole thing inert with
    nothing on screen that could fix it."""
    from app.ui.review import ReviewWindow
    w = ReviewWindow()
    assert w.menuBar().actions(), "no menu bar"
    labels = [a.text() for menu in w.menuBar().actions()
              for a in (menu.menu().actions() if menu.menu() else [])]
    assert any("etting" in l for l in labels), labels
    w.close()


def test_asking_for_settings_is_a_signal_not_a_window(qapp_or_skip):
    """The window knows nothing about the keyring — main.py wires that. Opening
    it here would put a credential store behind a Qt widget."""
    from app.ui.review import ReviewWindow
    w = ReviewWindow()
    seen = []
    w.settings_requested.connect(lambda: seen.append(True))
    w.act_settings.trigger()
    assert seen == [True]
    w.close()


def test_settings_is_reachable_without_a_working_key(qapp_or_skip):
    """The state it is most needed in is the one where nothing else starts, so
    it must not sit behind onboarding or the entitlement gate."""
    from app.main import open_settings
    window = open_settings()
    assert window.key is not None and window.licence is not None
    window.close()


# -- rules can now be earned ------------------------------------------------
def test_a_pursued_employer_becomes_a_known_employer(conn):
    """`rule_terms` and `kill_families` had NO writer anywhere in the app, so
    the rule table was empty forever rather than only on day one — and "every
    entry is earned" was a design with no way to earn one."""
    from app.main import load_rules
    a_run(conn, [Job(provider="theirstack", provider_job_id="a",
                     title="Head of Strategy", company="Meridian Group",
                     description_text="Strategy.")])
    assert load_rules(conn).known_employers == []

    record_decision(conn, "theirstack:a", "pursue")
    assert load_rules(conn).known_employers == ["Meridian Group"]


def test_a_rejected_employer_is_not_learned(conn):
    from app.main import load_rules
    a_run(conn, [Job(provider="theirstack", provider_job_id="a",
                     title="Head of Strategy", company="Acme",
                     description_text="Strategy.")])
    record_decision(conn, "theirstack:a", "reject")
    assert load_rules(conn).known_employers == []


def test_revising_a_decision_unlearns_the_employer(conn):
    """Derived, never copied: a stored duplicate would keep an employer known
    after the decision that made it known was withdrawn."""
    from app.main import load_rules
    a_run(conn, [Job(provider="theirstack", provider_job_id="a",
                     title="Head of Strategy", company="Acme",
                     description_text="Strategy.")])
    record_decision(conn, "theirstack:a", "pursue")
    assert load_rules(conn).known_employers == ["Acme"]
    record_decision(conn, "theirstack:a", "reject")
    assert load_rules(conn).known_employers == []


def test_a_saved_term_survives_a_reload(conn):
    from app.main import load_rules, save_rule_term
    save_rule_term(conn, "strong_terms", "asset management")
    assert load_rules(conn).strong_terms == ["asset management"]


def test_a_term_that_would_kill_a_pursued_role_is_refused(conn):
    """spec 5.3. A kill term that would have removed a role the user actually
    chased is not a rule — it is a mistake about to repeat itself."""
    from app.core.rules import RuleConflictError
    from app.main import load_rules, save_rule_term

    a_run(conn, [Job(provider="theirstack", provider_job_id="a",
                     title="Head of Operations", company="Acme",
                     description_text="Strategy.")])
    record_decision(conn, "theirstack:a", "pursue")

    with pytest.raises(RuleConflictError):
        save_rule_term(conn, "unsupported_titles", "operations")
    assert load_rules(conn).unsupported_titles == [], "nothing was stored"


def test_known_employers_cannot_be_typed_in(conn):
    """It is derived from decisions. A hand-typed copy would drift the moment
    one was revised."""
    from app.main import save_rule_term
    with pytest.raises(ValueError, match="unknown rule field"):
        save_rule_term(conn, "known_employers", "Acme")


def test_a_term_can_be_taken_back(conn):
    from app.main import forget_rule_term, load_rules, save_rule_term
    save_rule_term(conn, "contextual_terms", "portfolio")
    forget_rule_term(conn, "contextual_terms", "portfolio")
    assert load_rules(conn).contextual_terms == []


def test_an_empty_term_is_refused(conn):
    from app.main import save_rule_term
    with pytest.raises(ValueError, match="cannot be empty"):
        save_rule_term(conn, "strong_terms", "   ")


def test_settings_carries_the_rules_panel_when_it_has_a_database(conn, qapp_or_skip):
    """Wired to the real loader and saver, not a stub — a panel that offers to
    save terms and drops them is worse than no panel."""
    from app.main import open_settings, save_rule_term
    window = open_settings(conn=conn)
    assert window.rules is not None

    save_rule_term(conn, "strong_terms", "asset management")
    window.rules.refresh()
    assert window.rules._lists["strong_terms"].item(0).text() == "asset management"
    window.close()


def test_adding_a_term_through_the_panel_reaches_the_database(conn, qapp_or_skip):
    from app.main import load_rules, open_settings
    window = open_settings(conn=conn)
    window.rules._fields["contextual_terms"].setText("hospitality")
    window.rules.add("contextual_terms")
    assert load_rules(conn).contextual_terms == ["hospitality"]
    window.close()


def test_the_panel_refuses_a_term_that_would_hide_a_pursued_role(conn, qapp_or_skip):
    """The whole admission guard, reaching a person for the first time."""
    from app.main import load_rules, open_settings

    a_run(conn, [Job(provider="theirstack", provider_job_id="a",
                     title="Head of Operations", company="Round Hill Capital",
                     description_text="Strategy.")])
    record_decision(conn, "theirstack:a", "pursue")

    window = open_settings(conn=conn)
    window.rules._fields["unsupported_titles"].setText("operations")
    window.rules.add("unsupported_titles")

    assert load_rules(conn).unsupported_titles == [], "nothing was stored"
    assert "Head of Operations" in window.rules.result.text()
    assert "Round Hill Capital" in window.rules.result.text()
    window.close()


# -- near-duplicates are flagged, and the flag survives ---------------------
def test_a_near_duplicate_is_stored_and_shown(conn):
    """`dedup` has always found these. Nothing wrote them down, so nothing
    could ever show them — a flag that is computed and dropped is not a flag."""
    from app.ui.adapter import near_duplicate_notes
    a_run(conn, [Job(provider="theirstack", provider_job_id="a",
                     title="Head of Strategy", company="Acme Hotels",
                     description_text="Strategy."),
                 Job(provider="theirstack", provider_job_id="b",
                     title="Head of Strategy", company="Acme Hotels",
                     description_text="Strategy, relisted.")])

    notes = near_duplicate_notes(conn)
    assert notes, "nothing flagged"
    # Both sides carry it, so whichever the user opens names the other.
    assert set(notes) == {"a", "b"}
    assert "Acme Hotels" in notes["a"] and "Head of Strategy" in notes["a"]
    assert "Acme Hotels" in notes["b"]


def test_the_note_reaches_the_review_row(conn):
    a_run(conn, [Job(provider="theirstack", provider_job_id="a",
                     title="Head of Strategy", company="Acme Hotels",
                     description_text="Strategy."),
                 Job(provider="theirstack", provider_job_id="b",
                     title="Head of Strategy", company="Acme Hotels",
                     description_text="Strategy, relisted.")])
    rows = {r.job_id: r for r in rows_from_db(conn)}
    assert rows["theirstack:a"].near_duplicate
    assert rows["theirstack:b"].near_duplicate


def test_both_postings_survive(conn):
    """Never merged (spec 6.6): two postings that look like one may be two real
    vacancies, and merging them loses one."""
    a_run(conn, [Job(provider="theirstack", provider_job_id="a",
                     title="Head of Strategy", company="Acme Hotels",
                     description_text="Strategy."),
                 Job(provider="theirstack", provider_job_id="b",
                     title="Head of Strategy", company="Acme Hotels",
                     description_text="Strategy, relisted.")])
    assert len(rows_from_db(conn)) == 2


def test_nothing_flagged_is_no_notes(conn):
    from app.ui.adapter import near_duplicate_notes
    a_run(conn, [Job(provider="theirstack", provider_job_id="a",
                     title="Head of Strategy", company="Acme",
                     description_text="Strategy.")])
    assert near_duplicate_notes(conn) == {}


# -- kill families: proposed by evidence, adopted by the user ---------------
def reject_all(conn, jobs):
    """Run and reject everything, so the proposer has a shape to find."""
    a_run(conn, jobs)
    for j in jobs:
        record_decision(conn, f"theirstack:{j.provider_job_id}", "reject")


def kier(n, title):
    return Job(provider="theirstack", provider_job_id=n, title=title,
               company="Kier", description_text="Strategy.")


def test_two_rejections_of_a_shape_propose_a_family(conn):
    """`kill_families` was the last table the app read and never wrote, so
    tier 1b could only be populated by editing SQLite by hand."""
    from app.main import load_rules, refresh_kill_family_proposals, save_rule_term

    save_rule_term(conn, "strong_terms", "asset management")
    reject_all(conn, [kier("a", "Site Engineer"), kier("b", "Senior Site Engineer")])

    assert refresh_kill_family_proposals(conn) == 1
    families = load_rules(conn).kill_families
    assert len(families) == 1
    assert families[0].name == "Kier"
    assert "engineer" in families[0].kill_titles
    assert families[0].saves_titles, "SAVES is required (spec 5.3 rule 2)"
    assert len(families[0].precedents) == 2


def test_one_rejection_proposes_nothing(conn):
    """Rule 1: one rejection is a decision, two is a shape."""
    from app.main import load_rules, refresh_kill_family_proposals, save_rule_term
    save_rule_term(conn, "strong_terms", "asset management")
    reject_all(conn, [kier("a", "Site Engineer")])
    refresh_kill_family_proposals(conn)
    assert load_rules(conn).kill_families == []


def test_no_basis_for_saves_proposes_nothing(conn):
    """Rule 2. A family that would kill every posting at an employer is what
    the required SAVES exists to prevent, and proposing one would make the app
    the author of that mistake."""
    from app.main import load_rules, refresh_kill_family_proposals
    reject_all(conn, [kier("a", "Site Engineer"), kier("b", "Senior Site Engineer")])
    refresh_kill_family_proposals(conn)
    assert load_rules(conn).kill_families == []


def test_a_proposed_family_does_not_fire(conn):
    """Rule 8: adoption is the user's decision, so a proposal screens nothing."""
    from app.core.screen import screen_all
    from app.main import load_rules, refresh_kill_family_proposals, save_rule_term

    save_rule_term(conn, "strong_terms", "strategy")
    reject_all(conn, [kier("a", "Site Engineer"), kier("b", "Senior Site Engineer")])
    refresh_kill_family_proposals(conn)

    fresh = kier("c", "Site Engineer")
    report = screen_all([fresh], load_rules(conn))
    assert report.likely, "an unadopted family must never screen anything out"


def test_adopting_arms_it(conn):
    from app.core.screen import screen_all
    from app.main import (adopt_kill_family, load_rules,
                          refresh_kill_family_proposals, save_rule_term)

    save_rule_term(conn, "strong_terms", "strategy")
    reject_all(conn, [kier("a", "Site Engineer"), kier("b", "Senior Site Engineer")])
    refresh_kill_family_proposals(conn)
    adopt_kill_family(conn, "Kier")

    report = screen_all([kier("c", "Site Engineer")], load_rules(conn))
    assert report.unlikely, "an adopted family must fire"
    assert "kill family" in report.unlikely[0].reason


def test_saves_survives_at_the_same_employer(conn):
    """spec 5.2: the brand does not disqualify a posting, the brand plus the
    wrong function does."""
    from app.core.screen import screen_all
    from app.main import (adopt_kill_family, load_rules,
                          refresh_kill_family_proposals, save_rule_term)

    save_rule_term(conn, "strong_terms", "strategy")
    reject_all(conn, [kier("a", "Site Engineer"), kier("b", "Senior Site Engineer")])
    refresh_kill_family_proposals(conn)
    adopt_kill_family(conn, "Kier")

    survivor = kier("c", "Head of Strategy")
    assert screen_all([survivor], load_rules(conn)).likely


def test_adoption_is_refused_when_it_would_hide_a_pursued_role(conn):
    """The same guard as a tier-1 term, and the moment to find out is before it
    fires rather than after a role goes missing."""
    from app.core.rules import RuleConflictError
    from app.main import (adopt_kill_family, load_rules,
                          refresh_kill_family_proposals, save_rule_term)

    save_rule_term(conn, "strong_terms", "asset management")
    reject_all(conn, [kier("a", "Site Engineer"), kier("b", "Senior Site Engineer")])
    refresh_kill_family_proposals(conn)

    # Now pursue an engineering role at the same employer.
    a_run(conn, [kier("z", "Site Engineer, Strategy")])
    record_decision(conn, "theirstack:z", "pursue")

    with pytest.raises(RuleConflictError):
        adopt_kill_family(conn, "Kier")
    assert not load_rules(conn).kill_families[0].adopted


def test_re_proposing_does_not_re_arm_a_stood_down_family(conn):
    """Otherwise every refresh quietly undoes the user's decision."""
    from app.main import (adopt_kill_family, load_rules,
                          refresh_kill_family_proposals, save_rule_term)

    save_rule_term(conn, "strong_terms", "strategy")
    reject_all(conn, [kier("a", "Site Engineer"), kier("b", "Senior Site Engineer")])
    refresh_kill_family_proposals(conn)
    adopt_kill_family(conn, "Kier")
    adopt_kill_family(conn, "Kier", adopted=False)

    refresh_kill_family_proposals(conn)
    assert not load_rules(conn).kill_families[0].adopted


def test_a_term_that_both_kills_and_saves_is_not_proposed(conn):
    """SAVES is checked first, so such a family would decline to kill the very
    titles it was built from — armed, and doing nothing."""
    from app.main import load_rules, refresh_kill_family_proposals, save_rule_term
    save_rule_term(conn, "strong_terms", "engineer")
    reject_all(conn, [kier("a", "Site Engineer"), kier("b", "Senior Site Engineer")])
    refresh_kill_family_proposals(conn)
    assert load_rules(conn).kill_families == []


# -- housekeeping that never ran --------------------------------------------
def test_a_run_prunes_the_rolling_seen_window(conn, monkeypatch):
    """`seen_jobs` is a rolling window, not a record: rejections live in
    `decisions`, which never expires. Nothing pruned it, so the table grew for
    the life of the install and every run rebuilt a larger already-seen set."""
    from datetime import datetime, timedelta, timezone
    from app.core import db
    from app.main import morning_run

    old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat(timespec="seconds")
    conn.execute("INSERT INTO seen_jobs(provider, provider_job_id, seen_at) "
                 "VALUES('theirstack','ancient',?)", (old,))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM seen_jobs").fetchone()[0] == 1

    save_document(conn, "fit_brief", "b")
    save_document(conn, "factsheet", FACTS)
    from app.onboarding.calibration import CALIBRATION_KEY
    conn.execute("INSERT INTO settings(key, value) VALUES(?, 'done')",
                 (CALIBRATION_KEY,))
    conn.execute("INSERT INTO queries(label, params_json, enabled, created_at) "
                 "VALUES('q','{\"titles\":[\"strategy\"]}',1,'x')")
    conn.commit()
    monkeypatch.setattr("app.core.entitlement.require", lambda c: None)

    morning_run(conn, provider=Stub([]), send=verdicts("strong"))
    rows = conn.execute("SELECT provider_job_id FROM seen_jobs").fetchall()
    assert "ancient" not in [r[0] for r in rows], "the stale entry survived"


def test_a_draft_is_registered_against_a_run(conn, tmp_path, monkeypatch):
    """Invariant 13: an output cannot exist without the run that produced it.
    The FK was there and nothing ever opened a run around the writing."""
    from app.core.board_repo import create_opportunity
    from app.main import outreach_run

    monkeypatch.setattr("app.core.entitlement.require", lambda c: None)
    save_document(conn, "factsheet", FACTS)
    oid = create_opportunity(conn, "Acme")
    conn.execute("INSERT INTO contacts(opportunity_id,name,email,created_at) "
                 "VALUES(?,?,?,?)", (oid, "Jo", "jo@example.com", "x"))
    conn.commit()

    outreach_run(conn, send=lambda r: BODY, today=TUE, folder=tmp_path)
    registered = conn.execute(
        "SELECT path, kind FROM run_outputs").fetchall()
    assert len(registered) == 1
    assert registered[0]["kind"] == "draft"
    assert registered[0]["path"].endswith(".eml")


def test_an_unregistered_file_is_reported_as_an_orphan(conn, tmp_path):
    from app.core import db
    folder = tmp_path / "drafts"          # not tmp_path: the db lives there
    folder.mkdir()
    (folder / "stray.eml").write_text("hand-written", encoding="utf-8")
    orphans = db.orphan_outputs(conn, folder)
    assert [p.name for p in orphans] == ["stray.eml"]


def test_drafting_does_not_empty_the_review_window(conn, tmp_path, monkeypatch):
    """An outreach run opens a run row and sweeps nothing. Taking the newest
    row regardless would empty the shortlist every time the user drafted."""
    from app.core.board_repo import create_opportunity
    from app.main import outreach_run

    a_run(conn, [Job(provider="theirstack", provider_job_id="a",
                     title="Head of Strategy", company="Acme",
                     description_text="Strategy.")])
    assert len(rows_from_db(conn)) == 1

    monkeypatch.setattr("app.core.entitlement.require", lambda c: None)
    save_document(conn, "factsheet", FACTS)
    oid = create_opportunity(conn, "Beta")
    conn.execute("INSERT INTO contacts(opportunity_id,name,email,created_at) "
                 "VALUES(?,?,?,?)", (oid, "Jo", "jo@example.com", "x"))
    conn.commit()
    outreach_run(conn, send=lambda r: BODY, today=TUE, folder=tmp_path)

    assert len(rows_from_db(conn)) == 1, "the shortlist vanished after drafting"


# -- job-alert emails, which the listing promises ---------------------------
DIGEST = """From: LinkedIn Job Alerts <jobs-noreply@example.com>
Subject: 3 new jobs for asset manager
Content-Type: text/html; charset="utf-8"

<html><body>
<a href="https://example.com/jobs/view/111/?trk=x">Head of Asset Management</a>
<p>Meridian Group &middot; London</p>
<a href="https://example.com/jobs/view/222/?trk=y">Night Auditor</a>
<p>Acme Hotels &middot; London</p>
</body></html>
"""


def a_configured_db(conn, monkeypatch):
    from app.onboarding.calibration import CALIBRATION_KEY
    save_document(conn, "fit_brief", "Asset management in London.")
    save_document(conn, "factsheet", FACTS)
    conn.execute("INSERT INTO settings(key, value) VALUES(?, 'done')",
                 (CALIBRATION_KEY,))
    conn.commit()
    monkeypatch.setattr("app.core.entitlement.require", lambda c: None)


def test_a_dropped_digest_becomes_shortlist_rows(conn, tmp_path, monkeypatch):
    """The listing promises "add job-alert emails yourself for anything the
    feeds miss". `parse_many` was complete, tested, and reachable from
    nowhere."""
    from app.main import ingest_alerts

    a_configured_db(conn, monkeypatch)
    path = tmp_path / "alert.eml"
    path.write_text(DIGEST, encoding="utf-8")

    outcome, problems = ingest_alerts(conn, [path], send=verdicts("strong"))
    assert outcome is not None, problems
    titles = {r.title for r in rows_from_db(conn)}
    assert "Head of Asset Management" in titles


def test_alerts_need_no_saved_queries(conn, tmp_path, monkeypatch):
    """These postings did not come from a query, so requiring one would block
    exactly the case the feature exists for."""
    from app.main import ingest_alerts, load_queries

    a_configured_db(conn, monkeypatch)
    assert load_queries(conn) == []
    path = tmp_path / "alert.eml"
    path.write_text(DIGEST, encoding="utf-8")
    outcome, _ = ingest_alerts(conn, [path], send=verdicts("strong"))
    assert outcome is not None


def test_alerts_do_not_walk_round_the_calibration_gate(conn, tmp_path, monkeypatch):
    """A gate the user can skip by dragging a file is not a gate."""
    from app.main import NotConfigured, ingest_alerts

    save_document(conn, "fit_brief", "b")
    monkeypatch.setattr("app.core.entitlement.require", lambda c: None)
    path = tmp_path / "alert.eml"
    path.write_text(DIGEST, encoding="utf-8")
    with pytest.raises(NotConfigured, match="[Cc]alibration"):
        ingest_alerts(conn, [path], send=verdicts("strong"))


def test_a_previously_rejected_posting_is_not_re_added(conn, tmp_path, monkeypatch):
    """The permanent-reject gate applies to a dragged posting exactly as it
    does to a swept one — the whole point of routing both through one path."""
    from app.main import ingest_alerts

    a_configured_db(conn, monkeypatch)
    path = tmp_path / "alert.eml"
    path.write_text(DIGEST, encoding="utf-8")
    ingest_alerts(conn, [path], send=verdicts("strong"))

    rows = rows_from_db(conn)
    rejected = rows[0].job_id
    record_decision(conn, rejected, "reject")
    assert rejected not in {r.job_id for r in rows_from_db(conn)}

    # Dropping the same digest again must not resurrect it. Suppressed here by
    # the already-seen set rather than the permanent-reject gate — either is
    # correct, and asserting the OUTCOME rather than which one fired is what
    # keeps the test true if the order of the two ever changes.
    ingest_alerts(conn, [path], send=verdicts("strong"))
    assert rejected not in {r.job_id for r in rows_from_db(conn)}, (
        "a rejected posting came back")


def test_a_file_with_no_jobs_says_so(conn, tmp_path, monkeypatch):
    from app.main import ingest_alerts
    a_configured_db(conn, monkeypatch)
    path = tmp_path / "not-an-alert.eml"
    path.write_text("From: a@b\nSubject: hello\n\nplain text", encoding="utf-8")
    outcome, problems = ingest_alerts(conn, [path], send=verdicts("strong"))
    assert outcome is None
    assert problems and "not-an-alert" in problems[0]


def test_the_review_window_accepts_only_alert_files(qapp_or_skip):
    """A window that lights up for any file and then refuses it has already
    told the user the wrong thing."""
    from pathlib import Path
    from PySide6.QtCore import QMimeData, QUrl
    from app.ui.review import ReviewWindow

    w = ReviewWindow()
    assert w.acceptDrops()

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(Path("a.eml").resolve())),
                  QUrl.fromLocalFile(str(Path("cv.docx").resolve()))])
    assert [p.suffix for p in w._alert_paths(mime)] == [".eml"]
    w.close()


def test_the_two_row_builders_agree(conn):
    """`rows_from_outcome` reads a run still in memory; `rows_from_db` rebuilds
    it from disk. A field added to one and not the other shows the user
    different things depending on whether they are looking at the run that just
    happened or the one they opened this morning."""
    from app.ui.adapter import rows_from_outcome

    jobs = [Job(provider="theirstack", provider_job_id="a",
                title="Head of Strategy", company="Acme",
                description_text="Strategy work."),
            Job(provider="theirstack", provider_job_id="n",
                title="Night Auditor", company="Acme",
                description_text="Front desk, overnight.")]
    outcome = a_run(conn, jobs)

    live = {r.job_id: r for r in rows_from_outcome(outcome)}
    stored = {r.job_id: r for r in rows_from_db(conn)}
    assert set(live) == set(stored)

    # `downgrade_reason` and `contained` are deliberately absent from the
    # stored form — `persist` folds the downgrade into the reason text and does
    # not record containment — so they are excluded rather than pretended.
    compared = ("title", "company", "url", "description", "bucket",
                "requirement_checked", "near_duplicate")
    for job_id, row in live.items():
        for field in compared:
            assert getattr(row, field) == getattr(stored[job_id], field), (
                f"{job_id}.{field}: in-memory {getattr(row, field)!r} vs "
                f"stored {getattr(stored[job_id], field)!r}")


# -- searches: the run had nothing to sweep ---------------------------------
def test_a_search_can_be_saved_and_swept(conn):
    """Nothing created a `queries` row. `load_queries` returned an empty list,
    `morning_run` refused with "No saved queries", and there was no way to add
    one — so the run could never happen at all."""
    from app.main import load_queries, save_query
    assert load_queries(conn) == []
    save_query(conn, "asset management", ["asset manager", "asset management"])
    labels = [q.label for q in load_queries(conn)]
    assert labels == ["asset management"]


def test_a_search_needs_a_title_to_look_for(conn):
    from app.main import save_query
    with pytest.raises(ValueError, match="job title"):
        save_query(conn, "empty", [])
    with pytest.raises(ValueError, match="needs a name"):
        save_query(conn, "  ", ["asset manager"])


def test_seeded_searches_arrive_switched_off(conn):
    """Billing is per job returned, so a seed nobody read is a seed nobody
    should be charged for — "the South East" is a location no extractor can
    tell from a job title."""
    from app.main import all_queries, load_queries, seed_queries_from_aim

    added = seed_queries_from_aim(
        conn, "Hotel asset management in London, and general management roles.")
    assert added >= 1
    assert all(not on for _label, _titles, on in all_queries(conn))
    assert load_queries(conn) == [], "a seed must not sweep until switched on"


def test_switching_a_search_on_makes_a_run_sweep_it(conn):
    from app.main import all_queries, enable_query, load_queries, seed_queries_from_aim
    seed_queries_from_aim(conn, "Hotel asset management in London.")
    label = all_queries(conn)[0][0]
    enable_query(conn, label)
    assert [q.label for q in load_queries(conn)] == [label]


def test_seeding_twice_does_not_duplicate(conn):
    from app.main import all_queries, seed_queries_from_aim
    aim = "Hotel asset management in London."
    seed_queries_from_aim(conn, aim)
    before = len(all_queries(conn))
    seed_queries_from_aim(conn, aim)
    assert len(all_queries(conn)) == before


def test_a_search_can_be_removed(conn):
    from app.main import all_queries, forget_query, save_query
    save_query(conn, "asset management", ["asset manager"])
    forget_query(conn, "asset management")
    assert all_queries(conn) == []


# -- the calibration sample was a stub --------------------------------------
def test_the_calibration_sample_is_a_real_pull(conn, monkeypatch):
    """It was one hard-coded placeholder against a gate needing eight
    decisions, so onboarding could never be completed by anyone."""
    from app.main import CALIBRATION_SAMPLE, calibration_sample, save_query

    save_query(conn, "strategy", ["strategy"])
    jobs = [Job(provider="theirstack", provider_job_id=str(i),
                title=f"Head of Strategy {i}", company="Acme",
                description_text="Strategy work.")
            for i in range(CALIBRATION_SAMPLE)]

    items = calibration_sample(conn, provider=Stub(jobs),
                               send=verdicts("strong"))
    assert len(items) == CALIBRATION_SAMPLE
    assert all(i.app_verdict for i in items)
    assert items[0].title.startswith("Head of Strategy")


def test_no_searches_means_no_sample(conn, tmp_path, monkeypatch):
    """And the gate then reports a setup failure rather than an impossible ask.

    `alerts_dir` is redirected because the default is the REAL app-data folder:
    without this the test reads whatever alert emails the developer happens to
    have on disk, and passes or fails on their mail rather than on the code.
    """
    import app.main as main
    from app.main import calibration_sample
    from app.onboarding.calibration import CalibrationResult

    monkeypatch.setattr(main, "alerts_dir", lambda: tmp_path / "no-alerts-here")
    items = calibration_sample(conn, provider=Stub([]), send=verdicts("strong"))
    assert items == []
    reasons = CalibrationResult(items=items).blocking_reasons()
    assert "setup problem" in reasons[0]


def test_a_screened_out_posting_still_reaches_the_gate(conn):
    """An over-reaching rule is exactly what calibration should catch, so
    hiding those rows would hide the failure the gate exists to surface."""
    from app.main import calibration_sample, save_query, save_rule_term

    save_query(conn, "strategy", ["strategy"])
    save_rule_term(conn, "strong_terms", "strategy")
    jobs = [Job(provider="theirstack", provider_job_id="a",
                title="Head of Strategy", company="Acme",
                description_text="Strategy."),
            Job(provider="theirstack", provider_job_id="n",
                title="Night Auditor", company="Acme",
                description_text="Front desk.")]

    items = calibration_sample(conn, provider=Stub(jobs), send=verdicts("strong"))
    titles = {i.title for i in items}
    assert "Night Auditor" in titles, "the screened-out row was hidden"


# -- onboarding without a feed credential (the beta blocker) ----------------
ALERT_CARD = ('<a href="https://example.com/jobs/view/{i}/?trk=x">{title}</a>'
              '<p>{company} &middot; London</p>')


def an_alerts_folder(tmp_path, n=10):
    folder = tmp_path / "alerts"
    folder.mkdir()
    cards = "\n".join(
        ALERT_CARD.format(i=200 + i, title=f"Asset Manager {i}", company=f"Co {i}")
        for i in range(n))
    (folder / "alert.eml").write_text(
        'From: LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>\n'
        'Subject: new jobs\nContent-Type: text/html; charset="utf-8"\n\n'
        f"<html><body>{cards}</body></html>\n", encoding="utf-8")
    return folder


def test_calibration_works_with_no_feed_credential(conn, tmp_path, monkeypatch):
    """Without this a user with no feed credential could not finish onboarding
    AT ALL: calibration needs ten live postings, the gate needs eight
    decisions, and there was no third way to get them. It blocked every beta
    tester, on the last screen of setup."""
    import app.main as main
    from app.main import calibration_sample
    from app.onboarding.calibration import CalibrationResult

    save_document(conn, "fit_brief", "Asset management in London.")
    save_document(conn, "factsheet", FACTS)
    monkeypatch.setattr(main, "alerts_dir", lambda: an_alerts_folder(tmp_path))

    items = calibration_sample(conn, send=verdicts("possible"))
    assert len(items) == 10
    reasons = CalibrationResult(items=items).blocking_reasons()
    assert "decide at least 8" in reasons[0], (
        "the gate must now be reachable, not a setup failure")


def test_the_feed_is_still_preferred_when_present(conn, tmp_path, monkeypatch):
    """Alert emails are the fallback, not the default — a configured feed is
    live and current where a saved digest is neither."""
    import app.main as main
    from app.main import calibration_sample, save_query

    save_document(conn, "fit_brief", "b")
    save_document(conn, "factsheet", FACTS)
    save_query(conn, "strategy", ["strategy"])
    monkeypatch.setattr(main, "alerts_dir", lambda: an_alerts_folder(tmp_path))

    feed_jobs = [Job(provider="theirstack", provider_job_id=f"f{i}",
                     title=f"Head of Strategy {i}", company="Acme",
                     description_text="Strategy.") for i in range(10)]
    items = calibration_sample(conn, provider=Stub(feed_jobs),
                               send=verdicts("strong"))
    assert all(i.job_key.startswith("theirstack:") for i in items)


def test_a_short_feed_is_topped_up_from_alerts(conn, tmp_path, monkeypatch):
    """Three from the feed plus seven from alerts is a usable sample; three
    alone is a setup failure the user cannot act on."""
    import app.main as main
    from app.main import calibration_sample, save_query

    save_document(conn, "fit_brief", "b")
    save_document(conn, "factsheet", FACTS)
    save_query(conn, "strategy", ["strategy"])
    monkeypatch.setattr(main, "alerts_dir", lambda: an_alerts_folder(tmp_path))

    feed_jobs = [Job(provider="theirstack", provider_job_id=f"f{i}",
                     title=f"Head of Strategy {i}", company="Acme",
                     description_text="Strategy.") for i in range(3)]
    items = calibration_sample(conn, provider=Stub(feed_jobs),
                               send=verdicts("strong"))
    assert len(items) == 10
    providers = {i.job_key.split(":")[0] for i in items}
    assert providers == {"theirstack", "alert-email"}


def test_no_feed_and_no_alerts_is_still_a_named_setup_failure(conn, tmp_path, monkeypatch):
    import app.main as main
    from app.main import calibration_sample
    from app.onboarding.calibration import CalibrationResult

    save_document(conn, "fit_brief", "b")
    monkeypatch.setattr(main, "alerts_dir", lambda: tmp_path / "nothing-here")
    items = calibration_sample(conn)
    assert items == []
    assert "setup problem" in CalibrationResult(items=items).blocking_reasons()[0]


def test_the_alerts_folder_is_not_a_synced_one():
    from app.main import alerts_dir
    assert "OneDrive" not in str(alerts_dir())
