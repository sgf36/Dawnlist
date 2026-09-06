"""The follow-up cadence (spec 8.1, 8.2, invariant 11).

The ladder:

    1. initial outreach — email
    2. +5 business days, no reply -> email AND handwritten letter (one dual touch)
    3. +5bd                        -> a SECOND dual touch
    4. +5bd                        -> phone call
    5. then pivot to an alternate contact, preferring a warm mutual intro

Two rules do most of the work, and both exist because a cached field lied:

  * the interval is computed from the ACTUAL last evidenced outbound touch,
    never from a "last contact" field and never from a step number in a task
    name;
  * no touch ever lands on a Monday or Friday. Monday recipients are clearing a
    weekend backlog; Friday starts something that sits over the weekend. This
    applies to EVERY date the system writes, not just the arithmetic.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

BUSINESS_DAY_GAP = 5
OOO_RETURN_BUFFER_DAYS = 7

MONDAY, FRIDAY, SATURDAY, SUNDAY = 0, 4, 5, 6


class Channel(str, Enum):
    EMAIL = "email"
    LETTER = "letter"
    CALL = "call"
    OTHER = "other"


class Direction(str, Enum):
    OUT = "out"
    IN = "in"


@dataclass(frozen=True)
class Touch:
    channel: Channel
    direction: Direction
    occurred_on: date
    #: An auto-reply is NEVER a genuine reply. It is a scheduling override only:
    #: it does not reset the cadence.
    is_auto_reply: bool = False
    ooo_return_on: date | None = None
    bounced: bool = False

    @property
    def is_genuine_reply(self) -> bool:
        return self.direction is Direction.IN and not self.is_auto_reply

    @property
    def is_evidenced_outbound(self) -> bool:
        """A bounced send did not reach anyone, so it is not a touch that the
        cadence may count down from."""
        return self.direction is Direction.OUT and not self.bounced


@dataclass(frozen=True)
class NextStep:
    due_on: date | None
    channels: tuple[Channel, ...]
    rung: int
    reason: str
    pivot_to_alternate_contact: bool = False


def add_business_days(start: date, days: int) -> date:
    d = start
    added = 0
    while added < days:
        d += timedelta(days=1)
        if d.weekday() < SATURDAY:
            added += 1
    return d


def shift_to_tue_thu(d: date) -> date:
    """No touch on a Monday or Friday; weekends move forward too.

    Applies to every date the system writes. Monday and the weekend push
    forward to Tuesday; Friday pushes forward to the following Tuesday, because
    shifting it back to Thursday would move a follow-up EARLIER than its
    computed due date.
    """
    while d.weekday() in (MONDAY, FRIDAY, SATURDAY, SUNDAY):
        d += timedelta(days=1)
    return d


def last_evidenced_outbound(touches: list[Touch]) -> Touch | None:
    outbound = [t for t in touches if t.is_evidenced_outbound]
    return max(outbound, key=lambda t: t.occurred_on) if outbound else None


def has_genuine_reply(touches: list[Touch]) -> bool:
    return any(t.is_genuine_reply for t in touches)


def rung_from_touches(touches: list[Touch]) -> int:
    """Derive the ladder position from what actually happened.

    Counts distinct evidenced outbound DAYS, so an email and a letter sent on
    the same day are one dual touch, not two rungs.
    """
    return len({t.occurred_on for t in touches if t.is_evidenced_outbound})


def _ooo_override(touches: list[Touch]) -> date | None:
    """Out-of-office with a stated return date -> return + 7 days.

    No stated date -> no override, and the caller flags it.
    """
    dates = [t.ooo_return_on for t in touches
             if t.is_auto_reply and t.ooo_return_on is not None]
    if not dates:
        return None
    return max(dates) + timedelta(days=OOO_RETURN_BUFFER_DAYS)


def next_step(touches: list[Touch], *, today: date | None = None) -> NextStep:
    """The next due touch, computed from evidence alone."""
    today = today or date.today()

    if has_genuine_reply(touches):
        return NextStep(None, (), rung_from_touches(touches),
                        "a genuine reply arrived; the cadence is superseded")

    # A confirmed bounce makes the alternative due NOW, and does not advance
    # the ladder: the pivot counts as the touch the bounced email stood in for.
    bounced = [t for t in touches if t.bounced]
    if bounced and not any(t.is_evidenced_outbound
                           and t.occurred_on > max(b.occurred_on for b in bounced)
                           for t in touches):
        return NextStep(
            shift_to_tue_thu(today),
            (Channel.LETTER,),
            rung_from_touches(touches),
            "email bounced: the address is dead, pivot immediately; the "
            "cadence does not advance",
            pivot_to_alternate_contact=False,
        )

    last = last_evidenced_outbound(touches)
    if last is None:
        return NextStep(shift_to_tue_thu(today), (Channel.EMAIL,), 0,
                        "no outbound yet: initial email")

    rung = rung_from_touches(touches)
    due = add_business_days(last.occurred_on, BUSINESS_DAY_GAP)

    override = _ooo_override(touches)
    if override is not None and override > due:
        due = override

    if rung == 1:
        channels, why = (Channel.EMAIL, Channel.LETTER), "first dual touch"
    elif rung == 2:
        channels, why = (Channel.EMAIL, Channel.LETTER), "second dual touch"
    elif rung == 3:
        channels, why = (Channel.CALL,), "phone call"
    else:
        return NextStep(shift_to_tue_thu(due), (), rung,
                        "ladder exhausted: pivot to an alternate contact, "
                        "preferring a warm mutual intro",
                        pivot_to_alternate_contact=True)

    if override is not None:
        why += " (out-of-office: return date + 7 days)"

    return NextStep(shift_to_tue_thu(due), channels, rung, why)


def is_warm_route(contact_ever_replied: bool) -> bool:
    """spec 9.6: a mutual connection who has never replied is not a warm route.

    Connection counts measure graph proximity, not willingness. Rank untried
    contacts above unresponsive ones, and treat re-asking someone who ignored a
    first request as a cost, not a free retry.
    """
    return bool(contact_ever_replied)
