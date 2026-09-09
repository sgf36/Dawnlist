"""Onboarding: the corpus, the factsheet rules, and the calibration gate."""
import pytest

from app.core import db
from app.onboarding.calibration import (MIN_DECIDED, CalibrationItem,
                                        CalibrationResult, apply_corrections,
                                        complete_calibration, is_calibrated,
                                        mark_calibrated, save_brief)
from app.onboarding.interview import (INGEST_GUIDANCE, Corpus, CVDocument,
                                      build_brief_request,
                                      build_factsheet_request, verb_is_upgrade)


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.sqlite3")
    db.migrate(c)
    yield c
    c.close()


def item(key, app="rejected", user=None, sentence=""):
    return CalibrationItem(job_key=key, title=f"Role {key}", company="Acme",
                           description="d", app_verdict=app,
                           app_reason="because", user_verdict=user,
                           brief_sentence=sentence)


def full_result():
    """Ten items, eight decided, one disagreement carrying a sentence."""
    items = [item(str(i), user="rejected") for i in range(7)]
    items.append(item("7", app="rejected", user="pursue",
                      sentence="Operational real estate roles are in scope."))
    items.extend([item("8"), item("9")])          # left undecided on purpose
    return CalibrationResult(items=items)


# -- the corpus -------------------------------------------------------------
def test_the_ingest_copy_keeps_both_load_bearing_sentences():
    assert "Do not tidy them up first" in INGEST_GUIDANCE
    assert "Early roles" in INGEST_GUIDANCE


def test_a_single_cv_version_is_flagged():
    corpus = Corpus([CVDocument("cv.docx", "x" * 300)])
    assert any("Only one CV version" in w for w in corpus.warnings)


def test_an_unreadable_document_is_named_not_dropped():
    corpus = Corpus([CVDocument("good.docx", "x" * 300),
                     CVDocument("scan.pdf", "")])
    assert any("scan.pdf" in w for w in corpus.warnings)
    assert len(corpus.usable) == 1


def test_a_late_starting_corpus_warns_about_missing_early_career():
    corpus = Corpus([CVDocument("cv.docx", "Started 2019. " + "x" * 300),
                     CVDocument("cv2.docx", "Also 2020. " + "x" * 300)])
    assert corpus.earliest_year() == 2019
    assert any("early operational roles" in w for w in corpus.warnings)


def test_an_early_career_corpus_does_not_warn():
    corpus = Corpus([CVDocument("a.docx", "From 2004 onwards. " + "x" * 300),
                     CVDocument("b.docx", "Since 2008. " + "x" * 300)])
    assert not any("early operational roles" in w for w in corpus.warnings)


# -- the factsheet rules ----------------------------------------------------
def test_the_factsheet_schema_keeps_the_verb_apart_from_the_figure():
    """Compression is where claims distort - the verb must be a field, not
    prose, so it cannot be silently upgraded."""
    req = build_factsheet_request(Corpus([CVDocument("cv", "x" * 300)]))
    claim = (req["output_config"]["format"]["schema"]["properties"]["roles"]
             ["items"]["properties"]["claims"]["items"]["properties"])
    assert "verb" in claim and "figure" in claim
    assert "identified" in claim["verb"]["enum"]


def test_every_claim_must_cite_its_source_document():
    req = build_factsheet_request(Corpus([CVDocument("cv", "x" * 300)]))
    claim = (req["output_config"]["format"]["schema"]["properties"]["roles"]
             ["items"]["properties"]["claims"]["items"])
    assert "source_document" in claim["required"]


def test_the_must_never_claim_list_is_required():
    req = build_factsheet_request(Corpus([CVDocument("cv", "x" * 300)]))
    schema = req["output_config"]["format"]["schema"]
    assert "must_never_claim" in schema["required"]


def test_identified_may_never_become_delivered():
    """The single most damaging failure, and the most tempting."""
    assert verb_is_upgrade("identified", "delivered")
    assert verb_is_upgrade("supported", "led")
    assert verb_is_upgrade("analysed", "managed")


def test_weakening_a_claim_is_always_allowed():
    assert not verb_is_upgrade("delivered", "identified")
    assert not verb_is_upgrade("led", "led")


def test_the_brief_prompt_demands_disqualifiers_and_surfaces_disagreement():
    req = build_brief_request(Corpus([CVDocument("cv", "x" * 300)]), "GM roles")
    rules = req["system"][0]["text"]
    assert "what looks like a fit but is not" in rules
    assert "surface that disagreement" in rules
    assert "RANGE" in rules


# -- the calibration gate ---------------------------------------------------
def test_the_gate_blocks_until_enough_are_decided():
    items = [item(str(i), user="rejected") for i in range(3)]
    items += [item(str(i)) for i in range(3, 10)]
    result = CalibrationResult(items=items)
    assert not result.passed
    assert any(str(MIN_DECIDED) in r for r in result.blocking_reasons())


def test_a_disagreement_without_a_sentence_blocks_the_gate():
    """A correction that does not reach the brief will not persist - the next
    run makes exactly the same mistake."""
    items = [item(str(i), user="rejected") for i in range(8)]
    items[0] = item("0", app="rejected", user="pursue")   # no sentence
    result = CalibrationResult(items=items)
    assert not result.passed
    assert any("would have got this right" in r
               for r in result.blocking_reasons())


def test_all_blocking_reasons_are_reported_at_once():
    """Revealing them one at a time makes the step feel endless."""
    # A FULL sample with two problems in it. A short sample is now a setup
    # failure with a single reason, which is a different case entirely.
    items = [item(str(i)) for i in range(10)]
    items[0] = item("0", app="rejected", user="pursue")   # no sentence
    items[1] = item("1", app="rejected", user="pursue")   # no sentence
    reasons = CalibrationResult(items=items).blocking_reasons()
    assert len(reasons) >= 3   # too few decided, plus both missing sentences


def test_a_sample_too_short_to_calibrate_is_a_setup_failure():
    """Asking for eight decisions out of one is a gate nobody can pass. The
    Finish button simply never enabled, on the last step of onboarding, with
    nothing on screen saying why — and it read as the user's fault."""
    reasons = CalibrationResult(items=[item("0")]).blocking_reasons()
    assert len(reasons) == 1
    assert "setup problem" in reasons[0]
    assert "1 of the 8" in reasons[0]


def test_a_short_sample_does_not_ask_for_decisions(): 
    """It must not be phrased as an instruction the user cannot follow."""
    reasons = CalibrationResult(items=[item("0")]).blocking_reasons()
    assert not any("decide at least" in r for r in reasons)


def test_an_empty_sample_says_the_same_thing():
    reasons = CalibrationResult(items=[]).blocking_reasons()
    assert reasons and "0 of the 8" in reasons[0]


def test_a_complete_calibration_passes():
    assert full_result().passed


def test_unanimous_agreement_is_reported_as_low_signal():
    """Ten postings with no disagreement usually means the sample was easy,
    not that the brief is perfect."""
    result = CalibrationResult(items=[item(str(i), user="rejected")
                                      for i in range(10)])
    assert result.passed and result.low_signal
    assert result.agreement_rate == 1.0


# -- corrections reach the brief -------------------------------------------
def test_corrections_are_appended_with_their_provenance():
    updated = apply_corrections("# Fit brief\n\nRoles in hospitality.",
                                full_result())
    assert "Learned from calibration" in updated
    assert "Operational real estate roles are in scope." in updated
    assert "Roles in hospitality." in updated, "the original must survive"


def test_a_gate_with_no_disagreements_leaves_the_brief_alone():
    result = CalibrationResult(items=[item(str(i), user="rejected")
                                      for i in range(10)])
    assert apply_corrections("# Fit brief", result) == "# Fit brief"


# -- persistence ------------------------------------------------------------
def test_a_run_is_not_calibrated_by_default(conn):
    assert not is_calibrated(conn)


def test_completing_the_gate_saves_a_new_brief_version_and_opens_it(conn):
    save_brief(conn, "# Fit brief\n\nOriginal.")
    complete_calibration(conn, "# Fit brief\n\nOriginal.", full_result())

    assert is_calibrated(conn)
    rows = conn.execute(
        "SELECT version, body FROM documents WHERE kind='fit_brief' "
        "ORDER BY version").fetchall()
    assert len(rows) == 2, "the brief is versioned, never updated in place"
    assert "Learned from calibration" in rows[-1]["body"]


def test_a_failed_gate_can_never_be_recorded_as_passed(conn):
    incomplete = CalibrationResult(items=[item("0", user="rejected")])
    with pytest.raises(ValueError, match="has not passed"):
        mark_calibrated(conn, incomplete)
    assert not is_calibrated(conn)


def test_a_half_finished_calibration_leaves_the_gate_shut(conn):
    incomplete = CalibrationResult(items=[item("0", user="rejected")])
    with pytest.raises(ValueError):
        complete_calibration(conn, "# Fit brief", incomplete)
    assert not is_calibrated(conn), "a partial run must never open the gate"


def test_an_unreachable_feed_lets_the_user_finish_but_is_never_a_calibration(conn):
    """THE BUG THAT SHIPPED, pinned from both sides.

    Seeded searches arrive switched OFF by design, so a fresh install had no
    enabled search, fetched nothing, and `passed` stayed False — leaving Finish
    greyed on the LAST screen of onboarding with nothing the user could do
    about it. Both Windows databases and the Mac one were found with zero
    enabled queries: nobody had ever completed onboarding on any platform.

    The fix must not overshoot. Finishing and RECORDING are different
    questions, and letting an empty sample count as a pass would record a
    calibration against no postings as though it were eight.
    """
    empty = CalibrationResult(items=[])
    assert empty.sample_unavailable
    assert empty.can_finish, "a gate the user cannot act on must not trap them"
    assert not empty.passed, "and must never be recorded as a calibration"
    with pytest.raises(ValueError):
        complete_calibration(conn, "# Fit brief", empty)
    assert not is_calibrated(conn)


def test_a_half_finished_calibration_still_cannot_be_left(conn):
    """NOT the same case, and the distinction is the whole point: the postings
    arrived, so the remaining work is the user's and the gate stays shut."""
    partial = CalibrationResult(items=[item(str(i)) for i in range(MIN_DECIDED)])
    assert not partial.sample_unavailable
    assert not partial.can_finish
    assert not partial.passed
