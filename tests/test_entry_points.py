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
