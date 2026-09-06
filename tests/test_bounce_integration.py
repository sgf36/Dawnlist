"""A bounce must mean the same thing in every module.

The two halves were written separately and could drift: the tracker decides
what the Stage becomes, the cadence decides when the next touch is due. If they
disagree, the board says "waiting on them" while the engine chases a mailbox
that does not exist.
"""
from datetime import date

from app.core.cadence import Channel, Direction, Touch, next_step
from app.core.tracker import Opportunity, Stage, bounce_correction, check_parity

TUE = date(2026, 9, 8)
WED = date(2026, 9, 9)


def test_tracker_and_cadence_agree_on_a_bounce():
    opp = Opportunity(id="o1", company="Travelfusion", stage=Stage.CONTACTED,
                      status="waiting", email_bounced=True)
    touches = [Touch(Channel.EMAIL, Direction.OUT, TUE, bounced=True)]

    # The tracker: the STAGE is wrong, not the status.
    correction = bounce_correction(opp)
    assert correction is not None and correction.fix == "stage"
    assert "Identified" in correction.expected

    # ... and the record is held out of the parity batch entirely, so nothing
    # mirrors `waiting` onto it in the meantime.
    assert check_parity(opp) is None

    # The cadence: due now, off the dead channel, ladder unchanged.
    step = next_step(touches, today=WED)
    assert step.due_on == WED
    assert Channel.EMAIL not in step.channels
    assert step.rung == 0


def test_neither_module_treats_a_bounce_as_contact_made():
    """The single fact both halves must share: a bounce is NEVER CONTACTED."""
    touches = [Touch(Channel.EMAIL, Direction.OUT, TUE, bounced=True)]
    assert next_step(touches, today=WED).due_on == WED   # not TUE + 5bd

    opp = Opportunity(id="o1", company="X", stage=Stage.CONTACTED,
                      status="waiting", email_bounced=True)
    assert bounce_correction(opp).expected.endswith("Identified")
