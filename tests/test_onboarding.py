"""Onboarding: the corpus, the factsheet rules, and the calibration gate."""
import pytest

from app.core import db
from app.onboarding.calibration import (MIN_DECIDED, CalibrationItem,
                                        CalibrationResult, apply_corrections,
                                        complete_calibration, is_calibrated,
                                        mark_calibrated, save_brief)
from app.onboarding.interview import (Corpus, CVDocument, build_brief_request,
                                      build_factsheet_request,
                                      ingest_guidance, verb_is_upgrade)


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
    assert "Do not tidy them up first" in ingest_guidance()
    assert "Early roles" in ingest_guidance()


def test_the_setup_copy_follows_the_language_the_user_chose():
    """Every one of these sentences was compiled into the source, so the whole
    of setup — the first thing a new user sees — stayed English in all fifty
    languages. Read through `tr` at CALL time, never at import: a module-level
    constant is bound before `main` applies the stored locale."""
    from app import i18n

    catalogue = {"onboarding.ingest_guidance": "GUIDANCE IN OTHER WORDS",
                 "onboarding.corpus_one_version": "ONE VERSION ONLY",
                 "onboarding.blocker_decide": "DECIDE {decided}/{needed}"}
    real = i18n._load_catalog
    try:
        i18n._load_catalog = lambda locale: catalogue
        assert ingest_guidance() == "GUIDANCE IN OTHER WORDS"
        assert Corpus([CVDocument("cv.docx", "x" * 300)]).warnings == [
            "ONE VERSION ONLY"]
        reasons = CalibrationResult(
            items=[item(str(i)) for i in range(10)]).blocking_reasons()
        assert reasons[0] == "DECIDE 0/8"
    finally:
        i18n._load_catalog = real


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


def test_an_unpaid_copy_is_told_that_and_not_that_the_market_was_quiet():
    """"Check a search is switched on and the feed is reachable" describes a
    quiet market. Shown to somebody who has not subscribed it is an instruction
    to fix the one thing that was never wrong."""
    reasons = CalibrationResult(
        items=[], no_feed="No licence key found.").blocking_reasons()
    assert len(reasons) == 1
    assert "no subscription or access code" in reasons[0]
    assert "check a search is switched on" not in reasons[0]

    # POSITIVE CONTROL: with nothing wrong at the feed, the old sentence is
    # still the right one — a short sample really can be a quiet morning.
    assert "check a search is switched on" in (
        CalibrationResult(items=[]).blocking_reasons()[0])


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


# ---------------------------------------------------------------------------
# The terms, which nothing used to record
#
# The feed's data licence requires every subscriber to be bound by written
# terms, and neither store checkout shows Dawnlist's: Apple's shows Apple's own
# EULA and the Microsoft listing is free. A link in Settings makes the terms
# findable, which is not the same as agreed.
# ---------------------------------------------------------------------------

def test_a_fresh_install_has_not_agreed_to_anything(conn):
    from app.onboarding import terms
    assert not terms.is_accepted(conn)
    assert terms.accepted_version(conn) is None
    assert not terms.has_changed_since_acceptance(conn), (
        "never having agreed is not the same as having agreed to an old copy")


def test_agreeing_records_the_date_of_the_terms_and_the_moment(conn):
    from app.onboarding import terms

    terms.record_acceptance(conn)
    assert terms.is_accepted(conn)
    assert terms.accepted_version(conn) == terms.TERMS_LAST_UPDATED
    assert terms.accepted_at(conn), "the moment is part of the record"


def test_a_revision_asks_again(conn, monkeypatch):
    """Recording only "accepted: yes" would leave everybody bound to whatever
    they happened to read first, which is the opposite of what the terms
    themselves promise."""
    from app.onboarding import terms

    terms.record_acceptance(conn)
    assert terms.is_accepted(conn), "positive control"

    monkeypatch.setattr(terms, "TERMS_LAST_UPDATED", "2027-01-30")
    assert not terms.is_accepted(conn)
    assert terms.has_changed_since_acceptance(conn), (
        "and this person is being asked AGAIN, which reads differently")

    terms.record_acceptance(conn)
    assert terms.is_accepted(conn) and not terms.has_changed_since_acceptance(conn)


def test_the_recorded_date_is_a_real_date_and_says_where_it_comes_from():
    """The constant is what the user is shown, what is stored as the version
    they agreed to, and what decides whether they are asked again. If it drifts
    from the published page, people are recorded as having accepted terms they
    were never shown — so the requirement lives beside the constant, where
    whoever revises terms.html will meet it, and this fails if either the
    value or that explanation goes missing.
    """
    import datetime
    import inspect

    from app.onboarding import terms

    assert datetime.date.fromisoformat(terms.TERMS_LAST_UPDATED)

    source = inspect.getsource(terms)
    line = source.index("TERMS_LAST_UPDATED =")
    preamble = source[:line]
    assert "terms.html" in preamble, (
        "nothing beside the constant says which page it must match")
    assert "published page" in preamble, (
        "nothing beside the constant says it must equal the published date")


def test_a_run_will_not_start_until_the_terms_are_agreed(conn, monkeypatch):
    """Gated at the door into a run, beside calibration: a gate enforced in a
    screen is one the scheduled run walks straight past."""
    from app.main import NotConfigured, morning_run
    from app.onboarding.calibration import CALIBRATION_KEY
    from app.onboarding.interview import save_document
    from app.onboarding import terms

    save_document(conn, "fit_brief", "Hotel asset management in London.")
    conn.execute("INSERT INTO settings(key, value) VALUES(?, 'done')",
                 (CALIBRATION_KEY,))
    conn.execute("INSERT INTO queries(label, params_json, enabled, created_at) "
                 "VALUES('q','{\"titles\":[\"strategy\"],\"countries\":[\"GB\"]}',1,'x')")
    conn.commit()
    monkeypatch.setattr("app.core.entitlement.require", lambda c: None)

    with pytest.raises(NotConfigured, match="terms"):
        morning_run(conn, provider=None, send=lambda r: {})

    # POSITIVE CONTROL: with the terms agreed the same run gets past this gate
    # and fails, if at all, on something else entirely.
    terms.record_acceptance(conn)
    try:
        morning_run(conn, provider=None, send=lambda r: {})
    except NotConfigured as exc:
        assert "terms" not in str(exc)


def test_dragging_postings_in_does_not_skip_the_terms(conn, tmp_path, monkeypatch):
    """A gate the user can walk round by dragging a file is not a gate."""
    from app.main import NotConfigured, ingest_alerts
    from app.onboarding.calibration import CALIBRATION_KEY
    from app.onboarding.interview import save_document

    save_document(conn, "fit_brief", "Asset management in London.")
    conn.execute("INSERT INTO settings(key, value) VALUES(?, 'done')",
                 (CALIBRATION_KEY,))
    conn.commit()
    monkeypatch.setattr("app.core.entitlement.require", lambda c: None)
    path = tmp_path / "alert.eml"
    path.write_text("From: a@b\nSubject: jobs\n\nnothing", encoding="utf-8")

    with pytest.raises(NotConfigured, match="terms"):
        ingest_alerts(conn, [path])


# ---------------------------------------------------------------------------
# What the sample is made of
#
# The gate transfers judgement, and it can only do that where the user is able
# to DISAGREE. A sample everyone agrees with ten times over teaches the brief
# nothing — `low_signal` reports that after the fact, having already spent the
# user's half hour finding out.
# ---------------------------------------------------------------------------

def candidate(key, *, verdict="rejected", company="Acme", title=None):
    return CalibrationItem(job_key=key, title=title or f"Role {key}",
                           company=company, description="d",
                           app_verdict=verdict, app_reason="because")


def lopsided_pool():
    """What a feed actually returns: one employer dominating, one verdict
    dominating, and a couple of near-duplicate titles."""
    pool = [candidate(f"acme-{i}", company="Acme Hotels",
                      title=f"Night Auditor {i}") for i in range(9)]
    pool += [candidate("dup-1", company="Meridian", title="Asset Manager"),
             candidate("dup-2", company="Oakmere", title="asset manager!"),
             candidate("strong-1", verdict="strong", company="Calderwood",
                       title="Head of Commercial Strategy"),
             candidate("possible-1", verdict="possible", company="Thornfield",
                       title="Director of Asset Management")]
    return pool


def test_the_sample_spans_the_app_s_verdicts_rather_than_one_of_them():
    from app.onboarding.calibration import worth_calibrating

    pool = lopsided_pool()
    # POSITIVE CONTROL, and the reason this test can fail: taken in the order
    # they arrived, the first ten are nine rejections at one employer plus one
    # more — which is what shipped, and what this assertion forbids.
    naive = pool[:10]
    assert len({i.app_verdict for i in naive}) == 1
    assert sum(1 for i in naive if i.company == "Acme Hotels") == 9

    chosen = worth_calibrating(pool)
    assert len({i.app_verdict for i in chosen}) >= 3, (
        "nothing on this screen to disagree with")


def test_no_employer_takes_more_than_two_slots():
    from app.onboarding.calibration import MAX_PER_COMPANY, worth_calibrating

    chosen = worth_calibrating(lopsided_pool())
    counts = {}
    for item in chosen:
        counts[item.company] = counts.get(item.company, 0) + 1
    assert max(counts.values()) <= MAX_PER_COMPANY, counts


def test_the_same_title_twice_is_one_slot():
    """Near-duplicates teach the brief the same thing and cost a slot each."""
    from app.onboarding.calibration import worth_calibrating

    chosen = worth_calibrating(lopsided_pool())
    titles = [i.title.lower().strip("!") for i in chosen]
    assert titles.count("asset manager") <= 1, titles


def test_a_pool_with_nothing_to_learn_from_makes_a_short_sample_not_a_fake_one():
    """Padding it back to ten would hide a useless sample behind a full-looking
    screen. The gate already says what a short sample means and lets the user
    finish."""
    from app.onboarding.calibration import CalibrationResult, worth_calibrating

    clones = [candidate(str(i), company="Acme", title="Night Auditor")
              for i in range(12)]
    chosen = worth_calibrating(clones)
    assert len(chosen) == 1
    assert CalibrationResult(items=chosen).sample_unavailable


def test_a_healthy_pool_still_fills_the_sample():
    """POSITIVE CONTROL: the rules bite only on a lopsided pool."""
    from app.onboarding.calibration import SAMPLE_SIZE, worth_calibrating

    healthy = [candidate(str(i), company=f"Employer {i}",
                         title=f"Asset Manager {i}",
                         verdict=["strong", "possible", "rejected"][i % 3])
               for i in range(12)]
    assert len(worth_calibrating(healthy)) == SAMPLE_SIZE


def test_titles_differing_only_in_punctuation_are_the_same_title():
    from app.onboarding.calibration import normalised_title

    assert normalised_title("Asset Manager (London)") == normalised_title(
        "asset manager, london")
    assert normalised_title("Head of Strategy") != normalised_title(
        "Head of Revenue")
