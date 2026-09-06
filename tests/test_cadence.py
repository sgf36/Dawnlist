"""spec 8.1 / 8.2 — the cadence, computed from evidence."""
from datetime import date

from app.core.cadence import (Channel, Direction, Touch, add_business_days,
                              has_genuine_reply, is_warm_route, next_step,
                              rung_from_touches, shift_to_tue_thu)

# 2026-09-07 is a Monday.
MON = date(2026, 9, 7)
TUE = date(2026, 9, 8)
WED = date(2026, 9, 9)
THU = date(2026, 9, 10)
FRI = date(2026, 9, 11)
SAT = date(2026, 9, 12)


def out(day, channel=Channel.EMAIL, **kw):
    return Touch(channel, Direction.OUT, day, **kw)


def inbound(day, **kw):
    return Touch(Channel.EMAIL, Direction.IN, day, **kw)


# -- the Tue-Thu rule -------------------------------------------------------
def test_no_touch_lands_on_monday_or_friday():
    for d in (MON, FRI, SAT):
        assert shift_to_tue_thu(d).weekday() not in (0, 4, 5, 6)


def test_friday_moves_forward_never_back():
    """Shifting Friday back to Thursday would move a follow-up EARLIER than
    its computed due date."""
    assert shift_to_tue_thu(FRI) > FRI
    assert shift_to_tue_thu(FRI).weekday() == 1     # the following Tuesday


def test_a_tuesday_is_left_alone():
    assert shift_to_tue_thu(TUE) == TUE


def test_business_days_skip_the_weekend():
    assert add_business_days(MON, 5) == MON.replace(day=14)   # next Monday


# -- the interval comes from evidence ---------------------------------------
def test_no_outbound_yet_means_initial_email():
    step = next_step([], today=TUE)
    assert step.channels == (Channel.EMAIL,) and step.rung == 0


def test_interval_is_measured_from_the_actual_last_outbound():
    step = next_step([out(TUE)], today=date(2026, 9, 30))
    # +5 business days from Tue 8 Sep = Tue 15 Sep. Already Tue, so unshifted.
    assert step.due_on == date(2026, 9, 15)


def test_a_dual_touch_on_one_day_is_one_rung_not_two():
    touches = [out(TUE, Channel.EMAIL), out(TUE, Channel.LETTER)]
    assert rung_from_touches(touches) == 1


def test_ladder_order_two_dual_touches_before_the_call():
    t1 = [out(TUE)]
    assert next_step(t1, today=TUE).channels == (Channel.EMAIL, Channel.LETTER)

    t2 = t1 + [out(date(2026, 9, 15)), out(date(2026, 9, 15), Channel.LETTER)]
    assert next_step(t2, today=TUE).channels == (Channel.EMAIL, Channel.LETTER), \
        "a SECOND dual touch comes before escalating to a call"

    t3 = t2 + [out(date(2026, 9, 22)), out(date(2026, 9, 22), Channel.LETTER)]
    assert next_step(t3, today=TUE).channels == (Channel.CALL,)


def test_ladder_exhausted_pivots_to_an_alternate_contact():
    days = [TUE, date(2026, 9, 15), date(2026, 9, 22), date(2026, 9, 29)]
    step = next_step([out(d) for d in days], today=TUE)
    assert step.pivot_to_alternate_contact
    assert "warm mutual intro" in step.reason


# -- replies ----------------------------------------------------------------
def test_a_genuine_reply_supersedes_the_cadence():
    step = next_step([out(TUE), inbound(WED)], today=THU)
    assert step.due_on is None
    assert has_genuine_reply([out(TUE), inbound(WED)])


def test_an_auto_reply_is_never_a_genuine_reply():
    touches = [out(TUE), inbound(WED, is_auto_reply=True)]
    assert not has_genuine_reply(touches)
    assert next_step(touches, today=THU).due_on is not None, \
        "an auto-reply is a scheduling override, never a reply"


def test_ooo_with_a_return_date_pushes_to_return_plus_seven():
    touches = [out(TUE), inbound(WED, is_auto_reply=True,
                                 ooo_return_on=date(2026, 10, 1))]
    step = next_step(touches, today=THU)
    assert step.due_on >= date(2026, 10, 8)
    assert "out-of-office" in step.reason


def test_ooo_without_a_return_date_is_no_override():
    plain = [out(TUE), inbound(WED, is_auto_reply=True)]
    dated = [out(TUE)]
    assert next_step(plain, today=THU).due_on == next_step(dated, today=THU).due_on


# -- bounces ----------------------------------------------------------------
def test_a_bounce_makes_the_pivot_due_now_and_does_not_advance_the_ladder():
    touches = [out(TUE, bounced=True)]
    step = next_step(touches, today=WED)
    assert step.due_on == WED, "the failed send is what makes the alternative due now"
    assert step.channels == (Channel.LETTER,), "pivot off the dead channel"
    assert step.rung == 0, "the pivot counts as the touch the bounce stood in for"


def test_a_bounced_send_is_not_an_evidenced_touch():
    """A bounced email reached nobody, so the cadence may not count down
    five business days from it as though contact had been made."""
    step = next_step([out(TUE, bounced=True)], today=WED)
    assert step.due_on == WED and step.due_on != date(2026, 9, 15)


def test_a_later_successful_send_supersedes_the_bounce():
    touches = [out(TUE, bounced=True), out(WED)]
    step = next_step(touches, today=THU)
    assert step.due_on > WED


# -- warm routes ------------------------------------------------------------
def test_an_unresponsive_mutual_is_not_a_warm_route():
    assert not is_warm_route(contact_ever_replied=False)
    assert is_warm_route(contact_ever_replied=True)
