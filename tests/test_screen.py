"""Golden-set tests for the deterministic screen.

spec 5.3 rule 4: every kill is anchored to a real rejection and every save to a
real pursue. Nothing here is invented — each case cites the failure it encodes.
"""
import pytest

from app.core.rules import (KillFamily, KillFamilyError, RuleConflictError,
                            RuleTable, assert_no_conflicts)
from app.core.screen import Tier, Verdict, screen_all, screen_one, yield_rate
from app.feed.models import Job, name_key


def job(title, company="Acme Hotels", description="", **kw):
    return Job(provider="theirstack", provider_job_id=kw.pop("jid", "1"),
               title=title, company=company, description_text=description, **kw)


# --------------------------------------------------------------------------
# spec 5.1 — word boundaries, never substrings.
# The measured failure: a naive substring screen marked 474 of 550 postings
# `likely` because "venue" fired on re*venue* and "spa" on *spa*ce.
# --------------------------------------------------------------------------
def test_substring_false_positives_do_not_fire():
    table = RuleTable(strong_terms=["venue", "spa"])
    r = screen_one(job("Financial Analyst", description="drove revenue growth"),
                   table.compiled())
    assert r.verdict is Verdict.UNLIKELY, "'venue' must not fire inside 'revenue'"

    r = screen_one(job("Office Lead", description="a modern space to work"),
                   table.compiled())
    assert r.verdict is Verdict.UNLIKELY, "'spa' must not fire inside 'space'"


def test_real_term_still_fires():
    table = RuleTable(strong_terms=["venue", "spa"])
    r = screen_one(job("Venue Director"), table.compiled())
    assert r.verdict is Verdict.LIKELY and r.tier is Tier.STRONG_TERM


# --------------------------------------------------------------------------
# spec 5.1 — tier ORDER. Unsupported title is checked first, so a junior role
# at a great employer cannot pass on the employer's name.
# --------------------------------------------------------------------------
def test_unsupported_title_beats_known_employer():
    table = RuleTable(unsupported_titles=["kitchen porter"],
                      known_employers=["Acme Hotels"])
    r = screen_one(job("Kitchen Porter", company="Acme Hotels"), table.compiled())
    assert r.verdict is Verdict.UNLIKELY
    assert r.tier is Tier.UNSUPPORTED_TITLE


# --------------------------------------------------------------------------
# spec 5.1 — tiers 3 and 4 must DIFFER. A contextual term is signal in a title
# and noise in a benefits paragraph.
# --------------------------------------------------------------------------
def test_contextual_term_ignores_the_description():
    table = RuleTable(contextual_terms=["travel"])
    noise = "Benefits include a travel allowance and gym membership."
    assert screen_one(job("Data Analyst", description=noise),
                      table.compiled()).verdict is Verdict.UNLIKELY
    assert screen_one(job("Travel Product Manager"),
                      table.compiled()).verdict is Verdict.LIKELY


# --------------------------------------------------------------------------
# spec 5.2 / 5.3 — kill families.
# --------------------------------------------------------------------------
QSR = KillFamily(
    name="qsr-operations",
    employers=("Burgerly", "Chicken Hut"),
    kill_titles=("general manager", "assistant manager", "operations", "shift"),
    saves_titles=("strategy", "commercial", "revenue", "analytics", "data",
                  "technology", "property", "finance"),
    precedents=(("Burgerly", "General Manager"), ("Chicken Hut", "Shift Manager")),
    adopted=True,
)


def test_kill_family_kills_the_wrong_function():
    table = RuleTable(kill_families=[QSR], strong_terms=["manager"])
    r = screen_one(job("General Manager", company="Burgerly Ltd"), table.compiled())
    assert r.verdict is Verdict.UNLIKELY and r.tier is Tier.KILL_FAMILY


def test_saves_protects_the_right_function_at_the_same_employer():
    """spec 5.2: the brand does not disqualify — the brand plus the wrong
    function does. A strategy role at a rejected employer must survive."""
    table = RuleTable(kill_families=[QSR], strong_terms=["strategy"])
    r = screen_one(job("Head of Commercial Strategy", company="Burgerly Ltd"),
                   table.compiled())
    assert r.verdict is Verdict.LIKELY


def test_unadopted_family_never_fires():
    """Rule 8: families are proposed, never auto-adopted."""
    proposed = KillFamily(
        name=QSR.name, employers=QSR.employers, kill_titles=QSR.kill_titles,
        saves_titles=QSR.saves_titles, precedents=QSR.precedents, adopted=False)
    table = RuleTable(kill_families=[proposed], strong_terms=["manager"])
    r = screen_one(job("General Manager", company="Burgerly"), table.compiled())
    assert r.verdict is Verdict.LIKELY


def test_saves_is_a_required_field():
    with pytest.raises(KillFamilyError, match="SAVES is a required field"):
        KillFamily(name="x", employers=("A",), kill_titles=("ops",),
                   saves_titles=(), precedents=(("A", "Ops"), ("A", "Ops 2")))


def test_family_needs_two_anchored_precedents():
    with pytest.raises(KillFamilyError, match="at least two real rejections"):
        KillFamily(name="x", employers=("A",), kill_titles=("ops",),
                   saves_titles=("data",), precedents=(("A", "Ops"),))


# --------------------------------------------------------------------------
# spec 5.3 rule 3 — never admit a term that can appear inside an in-scope title.
# The real case: "office manager" was added for admin roles and immediately
# killed a genuine "Assistant Front Office Manager", which the user later
# marked pursue.
# --------------------------------------------------------------------------
GOLDEN_PURSUED = [
    ("Grand Hotel", "Assistant Front Office Manager"),
    ("Burgerly", "Head of Commercial Strategy"),
]


def test_office_manager_is_refused_admission():
    """The term IS admissible-looking and word boundaries do not save you:
    `\\boffice manager\\b` matches inside 'Assistant Front Office Manager'.
    The guard is at admission, tested against real decisions."""
    table = RuleTable(unsupported_titles=["office manager"])
    with pytest.raises(RuleConflictError, match="Assistant Front Office Manager"):
        assert_no_conflicts(table, GOLDEN_PURSUED)


def test_word_boundaries_alone_would_not_have_caught_it():
    """Documents WHY the guard exists, so nobody 'simplifies' it away later."""
    table = RuleTable(unsupported_titles=["office manager"])
    r = screen_one(job("Assistant Front Office Manager", company="Grand Hotel"),
                   table.compiled())
    assert r.verdict is Verdict.UNLIKELY
    assert r.tier is Tier.UNSUPPORTED_TITLE


def test_a_clean_table_passes_the_golden_set():
    table = RuleTable(unsupported_titles=["kitchen porter"],
                      kill_families=[QSR])
    assert_no_conflicts(table, GOLDEN_PURSUED)   # must not raise


def test_kill_family_is_checked_before_adoption():
    """A proposed family is tested as if adopted — the user must never be
    asked to adopt a family that would kill something they pursued."""
    bad = KillFamily(
        name="too-broad", employers=("Burgerly",),
        kill_titles=("commercial",), saves_titles=("data",),
        precedents=(("Burgerly", "Ops"), ("Burgerly", "Shift")), adopted=False)
    with pytest.raises(RuleConflictError, match="Head of Commercial Strategy"):
        assert_no_conflicts(RuleTable(kill_families=[bad]), GOLDEN_PURSUED)


# --------------------------------------------------------------------------
# spec 5.4 — never erase, always log.
# --------------------------------------------------------------------------
def test_screened_out_rows_are_kept_with_reasons():
    table = RuleTable(strong_terms=["strategy"])
    report = screen_all([job("Strategy Lead", jid="a"), job("Kitchen Porter", jid="b")],
                        table)
    assert report.counts["screened"] == 2
    assert len(report.unlikely) == 1
    assert report.unlikely[0].reason, "every screened-out row carries a reason"
    assert report.unlikely_share == 0.5


# --------------------------------------------------------------------------
# spec 3 / handoff 2.1a — the yield-rate diagnostic.
# --------------------------------------------------------------------------
def test_yield_rate_flags_the_loose_query():
    # The measured production spread: hospitality tech was 35/663 = 5.3%.
    assert yield_rate(663, 35) < 0.10
    assert yield_rate(193, 51) > 0.10   # GM / front-of-house, 26.4%


# --------------------------------------------------------------------------
# spec 6.6 — name keys FLAG near-duplicates; same company different role is
# NOT a duplicate.
# --------------------------------------------------------------------------
def test_same_company_different_role_is_not_a_duplicate():
    assert name_key("Acme", "Strategy Lead") != name_key("Acme", "Revenue Lead")


def test_name_key_collapses_whitespace_and_ampersands():
    assert name_key("Rocco  Forte  &  Co", "Head of  Strategy") == \
           name_key("Rocco Forte & Co", "Head of Strategy")


# --------------------------------------------------------------------------
# Containment — the second layer under the admission guard.
#
# `assert_no_conflicts` protects a user who already HAS pursue history. On day
# one that history is empty, so containment must be detectable structurally.
# --------------------------------------------------------------------------
def test_containment_is_flagged_for_review_on_a_brand_new_user():
    table = RuleTable(unsupported_titles=["office manager"])
    r = screen_one(job("Assistant Front Office Manager", company="Grand Hotel"),
                   table.compiled())
    assert r.verdict is Verdict.UNLIKELY      # still killed: cheap + predictable
    assert r.contained and r.needs_review
    assert "INSIDE a longer role name" in r.reason


def test_a_clean_kill_is_not_flagged():
    table = RuleTable(unsupported_titles=["office manager"])
    r = screen_one(job("Office Manager"), table.compiled())
    assert r.verdict is Verdict.UNLIKELY and not r.contained


def test_a_trailing_segment_does_not_look_like_containment():
    table = RuleTable(unsupported_titles=["kitchen porter"])
    r = screen_one(job("Kitchen Porter - Full Time - Central London"),
                   table.compiled())
    assert not r.contained, "words AFTER the role name are not modifiers"


def test_a_linking_word_means_the_term_is_the_head_noun():
    """'Head of Operations' IS an operations role, so an `operations` kill
    there is correct and must not be flagged as noise."""
    fam = KillFamily(name="qsr", employers=("Burgerly",),
                     kill_titles=("operations",), saves_titles=("strategy",),
                     precedents=(("Burgerly", "Ops"), ("Burgerly", "Shift")),
                     adopted=True)
    r = screen_one(job("Head of Operations", company="Burgerly"),
                   RuleTable(kill_families=[fam]).compiled())
    assert r.verdict is Verdict.UNLIKELY and not r.contained


def test_kill_families_are_containment_checked_too():
    fam = KillFamily(name="qsr", employers=("Burgerly",),
                     kill_titles=("office manager",), saves_titles=("strategy",),
                     precedents=(("Burgerly", "A"), ("Burgerly", "B")),
                     adopted=True)
    r = screen_one(job("Assistant Front Office Manager", company="Burgerly"),
                   RuleTable(kill_families=[fam]).compiled())
    assert r.contained


def test_report_surfaces_contained_kills_in_the_counts():
    table = RuleTable(unsupported_titles=["office manager"])
    rep = screen_all([job("Office Manager", jid="a"),
                      job("Assistant Front Office Manager", jid="b")], table)
    assert rep.counts["contained_needs_review"] == 1
    assert len(rep.contained) == 1


# -- the unconfigured table -------------------------------------------------
def test_an_empty_table_reads_everything_rather_than_killing_it():
    """Tiers 2-4 are an allowlist, so an EMPTY allowlist used to reject the
    whole world. The shipped app screened out 100% of every sweep, assessed
    nothing, and showed an empty shortlist each morning with no error — the
    funnel read swept N, screened out N, assessed 0, which is exactly what a
    quiet day in the market looks like."""
    jobs = [Job(provider="t", provider_job_id=str(i), title=t, company="Acme",
                description_text="A role.")
            for i, t in enumerate(["Head of Strategy", "Night Auditor",
                                   "Sous Chef"])]
    report = screen_all(jobs, RuleTable())
    assert len(report.likely) == 3, "an unconfigured screen has no opinion"
    assert report.unlikely == []


def test_the_reason_says_why_everything_got_through():
    """Otherwise a 100% pass rate reads as a broken screen."""
    job = Job(provider="t", provider_job_id="a", title="Anything",
              company="Acme", description_text="")
    result = screen_all([job], RuleTable()).results[0]
    assert "no screening rules yet" in result.reason


def test_one_earned_term_switches_the_screen_back_on():
    """The moment the table can say yes to anything, it can say no to the
    rest. This is the boundary the empty case turns on."""
    jobs = [Job(provider="t", provider_job_id=str(i), title=t, company="Acme",
                description_text="A role.")
            for i, t in enumerate(["Head of Strategy", "Night Auditor"])]
    report = screen_all(jobs, RuleTable(strong_terms=["strategy"]))
    assert [r.job.title for r in report.likely] == ["Head of Strategy"]
    assert [r.job.title for r in report.unlikely] == ["Night Auditor"]


def test_a_pursued_employer_never_gates_the_rest_of_the_market_out():
    """The auditor's case. Known employers are derived from pursue decisions,
    and counting them as a positive signal turned the first pursue into a
    one-employer allowlist: every posting anywhere else became "no matching
    term" and was never read."""
    jobs = [Job(provider="t", provider_job_id="fs", title="Director of Rooms",
                company="Four Seasons", description_text="A role."),
            Job(provider="t", provider_job_id="rw", title="General Manager",
                company="Rosewood Hotels", description_text="A role."),
            Job(provider="t", provider_job_id="mo", title="Hotel Manager",
                company="Mandarin Oriental", description_text="A role.")]
    report = screen_all(jobs, RuleTable(known_employers=["Four Seasons"]))
    by_id = {r.job.provider_job_id: r for r in report.results}

    assert by_id["rw"].is_likely and by_id["mo"].is_likely
    # The employer still upgrades its own postings; it just gates nothing.
    assert by_id["fs"].tier is Tier.KNOWN_EMPLOYER

    # Positive control: a term the user TYPED still switches the allowlist on,
    # so the fall-through above is the fix and not a screen that stopped
    # rejecting anything.
    typed = screen_all(jobs, RuleTable(known_employers=["Four Seasons"],
                                       strong_terms=["general manager"]))
    kept = {r.job.provider_job_id for r in typed.likely}
    assert kept == {"fs", "rw"}
    assert [r.job.provider_job_id for r in typed.unlikely] == ["mo"]


def test_employers_match_by_whole_name_never_by_substring():
    """"EY" is inside "The Walt Disney Company" and "Bentley". Matched as a
    substring, one pursued EY role made every posting at either a known
    employer, and a kill family against EY fired on their postings too."""
    rules = RuleTable(known_employers=["EY"]).compiled()
    assert rules.known_employer("The Walt Disney Company") is None
    assert rules.known_employer("Bentley") is None

    family = KillFamily(name="EY", employers=("EY",), kill_titles=("auditor",),
                        saves_titles=("strategy",),
                        precedents=(("EY", "Auditor"), ("EY", "Senior Auditor")),
                        adopted=True)
    assert family.verdict("Bentley", "Auditor") is None
    assert family.verdict("The Walt Disney Company", "Auditor") is None

    # Positive controls: the employer itself, however a source spells it.
    assert rules.known_employer("EY") == "EY"
    assert rules.known_employer("EY Limited") == "EY"
    assert family.verdict("E.Y", "Auditor") is None, "different letters, not EY"
    assert family.verdict("EY LIMITED", "Auditor")


def test_one_employer_spelt_two_ways_is_one_shape():
    """Rejections were grouped on the exact casefolded name, so "Kier Ltd" and
    "Kier Limited" were two employers with one rejection each and no family
    was ever proposed."""
    from app.core.rules import propose_families

    families = propose_families(
        rejected=[("Kier Ltd", "Site Engineer"),
                  ("Kier Limited", "Senior Site Engineer")],
        pursued=[], saves_terms=["strategy"])
    assert len(families) == 1
    assert len(families[0].precedents) == 2


def test_a_kill_term_alone_is_not_a_positive_signal():
    """`unsupported_titles` can only say NO. A table holding nothing but kill
    terms still cannot say yes to anything, so it must not start rejecting
    everything it fails to match."""
    jobs = [Job(provider="t", provider_job_id=str(i), title=t, company="Acme",
                description_text="A role.")
            for i, t in enumerate(["Head of Strategy", "Night Auditor"])]
    report = screen_all(jobs, RuleTable(unsupported_titles=["night auditor"]))
    assert [r.job.title for r in report.likely] == ["Head of Strategy"]
    assert [r.job.title for r in report.unlikely] == ["Night Auditor"]


# ---------------------------------------------------------------------------
# spec 5.4 — "watch this between runs; if it moves sharply, say so". The share
# was computed on every report and read by nothing, so a rule edit that
# started removing half the feed looked exactly like a quiet week.
# ---------------------------------------------------------------------------
import sqlite3  # noqa: E402

from app.core import db  # noqa: E402
from app.core.screen import (DRIFT_MIN_SAMPLE, share_drift,  # noqa: E402
                             unlikely_share_of)
from app.main import _screen_drift_notes  # noqa: E402


def test_the_share_is_one_expression_not_two():
    """The property and the database path must agree, or the warning fires on
    a number the bar never showed."""
    assert unlikely_share_of(3, 1) == 0.25
    assert unlikely_share_of(0, 0) == 0.0


def test_a_first_run_has_no_drift():
    assert share_drift(0.9, None) is None


def _runs(conn, pairs):
    """Each pair is (screened_likely, screened_out) for one run."""
    for likely, out in pairs:
        conn.execute(
            "INSERT INTO runs(started_at, status, swept, screened_likely, "
            "screened_out) VALUES('t','complete',?,?,?)",
            (likely + out, likely, out))
    conn.commit()
    return conn.execute("SELECT MAX(id) AS id FROM runs").fetchone()["id"]


def _conn(tmp_path):
    c = db.connect(tmp_path / "d.sqlite3")
    db.migrate(c)
    return c


def test_a_sharp_move_in_the_screens_reach_is_said_out_loud(tmp_path):
    conn = _conn(tmp_path)
    run_id = _runs(conn, [(80, 20), (20, 80)])
    notes = _screen_drift_notes(conn, run_id)
    assert len(notes) == 1
    assert "80%" in notes[0] and "20%" in notes[0]
    conn.close()


def test_a_steady_screen_says_nothing(tmp_path):
    conn = _conn(tmp_path)
    run_id = _runs(conn, [(50, 50), (48, 52)])
    assert _screen_drift_notes(conn, run_id) == []
    conn.close()


def test_a_run_too_small_to_measure_is_not_reported(tmp_path):
    """Three rows going the wrong way is 100% drift and means nothing."""
    conn = _conn(tmp_path)
    run_id = _runs(conn, [(3, 0), (0, 3)])
    assert _screen_drift_notes(conn, run_id) == []
    conn.close()


def test_a_run_that_screened_nothing_is_not_a_collapse_to_zero(tmp_path):
    """An outreach run screens nothing. Comparing against the previous ROW
    rather than the previous SCREENING run reports a total collapse every
    time the user drafts their post."""
    conn = _conn(tmp_path)
    _runs(conn, [(80, 20), (20, 80)])
    run_id = _runs(conn, [(0, 0)])          # an outreach run
    notes = _screen_drift_notes(conn, run_id)
    assert len(notes) == 1 and "80%" in notes[0]
    conn.close()


def test_the_command_line_prints_the_share_every_run(capsys):
    """There is no funnel bar on the command line, so the standing figure is
    what a CLI user watches move. It was computed and printed nowhere."""
    from app.core.pipeline import RunOutcome
    from app.core.screen import ScreenReport, ScreenResult, Tier, Verdict
    from app.feed.models import Job
    from app.main import print_funnel

    def result(i):
        v = Verdict.UNLIKELY if i < 15 else Verdict.LIKELY
        return ScreenResult(Job(provider="p", provider_job_id=str(i),
                                title="t", company="c"),
                            v, Tier.NO_SIGNAL, "")

    outcome = RunOutcome(run_id=1)
    outcome.screen = ScreenReport([result(i) for i in range(20)])
    print_funnel(outcome)
    assert "screening rules removed 75%" in capsys.readouterr().out


def test_a_sample_too_small_to_measure_prints_no_share(capsys):
    from app.core.pipeline import RunOutcome
    from app.core.screen import ScreenReport, ScreenResult, Tier, Verdict
    from app.feed.models import Job
    from app.main import print_funnel

    outcome = RunOutcome(run_id=1)
    outcome.screen = ScreenReport([
        ScreenResult(Job(provider="p", provider_job_id="1", title="t",
                         company="c"), Verdict.UNLIKELY, Tier.NO_SIGNAL, "")])
    print_funnel(outcome)
    assert "screening rules removed" not in capsys.readouterr().out
