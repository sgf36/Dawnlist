"""The board. Every case is a defect the production ClickUp system actually hit."""
import pytest

from app.core.tracker import (JobCategory, Opportunity, Stage, Task, TrackerError,
                              advance_for_outbound, apply_determination,
                              assert_at_most_one_open_child, audit,
                              bounce_correction, cadence_scope, check_parity,
                              Write,
                              is_opportunity, nearest_stage_bearing_ancestor,
                              pipeline_order, reply_check_scope, retire_task,
                              validate_child_status)


def opp(stage=Stage.IDENTIFIED, status=None, **kw):
    o = Opportunity(id=kw.pop("id", "o1"), company=kw.pop("company", "Acme"),
                    stage=stage, **kw)
    o.status = status if status is not None else o.expected_status
    return o


# -- the ladder -------------------------------------------------------------
def test_stage_classes():
    assert Stage.IDENTIFIED.is_live and Stage.OFFER.is_live
    assert Stage.WON.is_terminal and Stage.LOST.is_terminal
    assert Stage.ON_HOLD.is_paused
    assert not Stage.ON_HOLD.is_live, "On Hold carries no active cadence"
    assert not Stage.ON_HOLD.is_terminal, "On Hold is paused, not dead"


def test_orderindex_matches_the_production_board():
    assert Stage.CONTACTED == 1 and Stage.WON == 6 and Stage.ON_HOLD == 8


# -- status is a mirror -----------------------------------------------------
def test_contacted_and_in_dialogue_share_waiting():
    """The distinction is not lost — Stage holds it authoritatively."""
    assert opp(Stage.CONTACTED).expected_status == "waiting"
    assert opp(Stage.IN_DIALOGUE).expected_status == "waiting"


def test_parity_defect_is_fixed_on_the_status_never_the_stage():
    o = opp(Stage.CONTACTED, status="open")
    d = check_parity(o)
    assert d is not None and d.expected == "waiting"
    assert d.fix == "status", "correct the mirror, never the truth"


def test_the_whole_ladder_is_mirrored_including_live_stages():
    """The live-Stage exemption was withdrawn: it is what let every parent sit
    at `open` forever regardless of what had happened beneath it."""
    for stage in (Stage.CONTACTED, Stage.PHONE_INTERVIEW, Stage.OFFER):
        assert check_parity(opp(stage, status="open")) is not None


def test_mutual_poc_is_exempt_from_parity():
    o = opp(Stage.CONTACTED, status="completed", category=JobCategory.MUTUAL_POC)
    assert check_parity(o) is None, (
        "forcing a discharged intro back to 'waiting' invites a pointless chase")


# -- a bounce outranks the stage field --------------------------------------
def test_bounce_corrects_the_stage_not_the_status():
    o = opp(Stage.CONTACTED, status="waiting", email_bounced=True)
    c = bounce_correction(o)
    assert c is not None and c.fix == "stage"
    assert "Identified" in c.expected


def test_bounced_opportunity_is_held_out_of_the_parity_batch():
    o = opp(Stage.CONTACTED, status="waiting", email_bounced=True)
    assert check_parity(o) is None, (
        "mirroring 'waiting' onto a bounce cements a contact that never happened")


# -- advancing ---------------------------------------------------------------
def test_outbound_advances_only_identified_to_contacted():
    assert advance_for_outbound(Stage.IDENTIFIED) is Stage.CONTACTED
    # A send never implies a reply, so it never reaches In Dialogue or beyond.
    assert advance_for_outbound(Stage.CONTACTED) is Stage.CONTACTED
    assert advance_for_outbound(Stage.IN_DIALOGUE) is Stage.IN_DIALOGUE


def test_negative_determination_cascades_no_offer_over_the_whole_tree():
    o = opp(Stage.IN_PERSON_INTERVIEW,
            tasks=[Task(id="t1", title="follow up"), Task(id="t2", title="letter")])
    writes = apply_determination(o, positive=False)
    assert Write("opportunity", "o1", "stage", "Lost") in writes
    # Qualified by RECORD KIND, not just id: opportunity ids and task ids
    # collide in the app's schema, and a caller that routed on the id alone
    # applied the parent's write and skipped every child's.
    assert Write("task", "t1", "status", "no offer") in writes
    assert Write("task", "t2", "status", "no offer") in writes


def test_positive_determination_never_marks_no_offer():
    o = opp(Stage.CONTACTED)
    writes = apply_determination(o, positive=True, advance_to=Stage.PHONE_INTERVIEW)
    assert all(w.value != "no offer" for w in writes)
    assert Write("opportunity", "o1", "stage", "Phone Interview") in writes


def test_positive_determination_cannot_advance_to_a_terminal_stage():
    with pytest.raises(TrackerError):
        apply_determination(opp(), positive=True, advance_to=Stage.LOST)


# -- children ---------------------------------------------------------------
def test_no_offer_forbidden_on_a_child_of_a_live_opportunity():
    with pytest.raises(TrackerError, match="misstates the pipeline"):
        validate_child_status("no offer", Stage.IN_PERSON_INTERVIEW)


def test_no_offer_allowed_once_the_parent_is_lost():
    validate_child_status("no offer", Stage.LOST)   # must not raise


def test_at_most_one_open_child():
    o = opp(tasks=[Task(id="t1", title="a", status="open"),
                   Task(id="t2", title="b", status="waiting")])
    with pytest.raises(TrackerError, match="2 open action tasks"):
        assert_at_most_one_open_child(o)


def test_a_completed_child_does_not_count_against_the_limit():
    o = opp(tasks=[Task(id="t1", title="a", status="open"),
                   Task(id="t2", title="b", status="complete")])
    assert_at_most_one_open_child(o)   # must not raise


def test_retirement_requires_evidence():
    t = Task(id="t1", title="a")
    with pytest.raises(TrackerError, match="needs evidence"):
        retire_task(t, "")
    retire_task(t, "Letter posted 2026-09-01, Royal Mail tracking ABC")
    assert t.status == "complete" and t.closed_evidence


# -- classification ---------------------------------------------------------
def test_an_opportunity_is_defined_by_carrying_a_stage():
    assert is_opportunity(opp())
    assert not is_opportunity(Task(id="t1", title="a"))


def test_opportunity_nested_two_levels_down_is_still_an_opportunity():
    """The confirmed live case: an entire opportunity with eight live children
    hanging two levels beneath its grandparent. A has-no-parent test skips it."""
    deep = opp(id="deep", company="Mandarin Oriental", parent_id="mid")
    assert is_opportunity(deep)
    assert deep.parent_id is not None


def test_walk_to_the_nearest_stage_bearing_ancestor():
    grandparent = opp(id="g", company="Highgate")
    middle = Task(id="mid", title="branch", parent_id="g")
    child = Task(id="c", title="action", parent_id="mid")
    records = {"g": grandparent, "mid": middle, "c": child}
    assert nearest_stage_bearing_ancestor("c", records) is grandparent


def test_ancestor_walk_survives_a_cycle():
    a = Task(id="a", title="a", parent_id="b")
    b = Task(id="b", title="b", parent_id="a")
    assert nearest_stage_bearing_ancestor("a", {"a": a, "b": b}) is None


# -- scopes and ordering ----------------------------------------------------
def test_on_hold_skips_the_cadence_but_still_gets_a_reply_check():
    held = opp(Stage.ON_HOLD, id="h")
    assert held not in cadence_scope([held])
    assert held in reply_check_scope([held]), "paused is not dead"


def test_terminal_stages_are_out_of_both_scopes():
    lost = opp(Stage.LOST, id="l")
    assert cadence_scope([lost]) == [] and reply_check_scope([lost]) == []


def test_board_sorts_by_stage_not_status():
    a = opp(Stage.OFFER, id="a", company="Zeta")
    b = opp(Stage.IDENTIFIED, id="b", company="Alpha")
    assert [o.id for o in pipeline_order([a, b])] == ["b", "a"]


def test_audit_covers_every_stage_in_one_pass():
    records = [opp(Stage.CONTACTED, status="open", id="a"),
               opp(Stage.LOST, status="open", id="b", company="B"),
               opp(Stage.ON_HOLD, status="open", id="c", company="C")]
    result = audit(records)
    assert len(result["scanned"]) == 3
    assert len(result["parity_defects"]) == 3, (
        "auditing status-slice by status-slice leaves a different hole each time")
