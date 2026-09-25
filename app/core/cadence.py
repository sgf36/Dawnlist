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

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum

BUSINESS_DAY_GAP = 5
OOO_RETURN_BUFFER_DAYS = 7

DEFAULT_LADDER: list[list[str]] = [
    ["email"],
    ["email", "letter"],
    ["email", "letter"],
    ["call"],
]

CADENCE_KEY = "cadence_config"


@dataclass
class CadenceConfig:
    """User-editable cadence parameters, persisted in the settings table."""
    business_day_gap: int = BUSINESS_DAY_GAP
    ooo_buffer_days: int = OOO_RETURN_BUFFER_DAYS
    ladder: list[list[str]] = field(default_factory=lambda: [
        ["email"],
        ["email", "letter"],
        ["email", "letter"],
        ["call"],
    ])
    tue_thu_only: bool = True

    def to_json(self) -> str:
        return json.dumps({
            "business_day_gap": self.business_day_gap,
            "ooo_buffer_days": self.ooo_buffer_days,
            "ladder": self.ladder,
            "tue_thu_only": self.tue_thu_only,
        })

    @classmethod
    def from_json(cls, text: str) -> CadenceConfig:
        d = json.loads(text)
        return cls(
            business_day_gap=int(d.get("business_day_gap", BUSINESS_DAY_GAP)),
            ooo_buffer_days=int(d.get("ooo_buffer_days", OOO_RETURN_BUFFER_DAYS)),
            ladder=d.get("ladder", DEFAULT_LADDER),
            tue_thu_only=bool(d.get("tue_thu_only", True)),
        )


def load_cadence_config(conn: sqlite3.Connection) -> CadenceConfig:
    row = conn.execute(
        "SELECT value FROM settings WHERE key=?", (CADENCE_KEY,)).fetchone()
    if row:
        return CadenceConfig.from_json(row[0])
    return CadenceConfig()


def save_cadence_config(conn: sqlite3.Connection, cfg: CadenceConfig) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (CADENCE_KEY, cfg.to_json()))
    conn.commit()

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


def _ooo_override(touches: list[Touch], *,
                   buffer_days: int = OOO_RETURN_BUFFER_DAYS) -> date | None:
    """Out-of-office with a stated return date -> return + buffer days.

    No stated date -> no override, and the caller flags it.
    """
    dates = [t.ooo_return_on for t in touches
             if t.is_auto_reply and t.ooo_return_on is not None]
    if not dates:
        return None
    return max(dates) + timedelta(days=buffer_days)


def _shift(d: date, cfg: CadenceConfig | None) -> date:
    if cfg is None or cfg.tue_thu_only:
        return shift_to_tue_thu(d)
    return d


def _channels_for_rung(rung: int, cfg: CadenceConfig | None
                        ) -> tuple[tuple[Channel, ...], str, bool]:
    """Return (channels, reason, pivot) for a given rung from the ladder."""
    ladder = cfg.ladder if cfg else DEFAULT_LADDER
    idx = rung
    if idx < 0 or idx >= len(ladder):
        return ((), "ladder exhausted: pivot to an alternate contact, "
                "preferring a warm mutual intro", True)
    names = ladder[idx]
    channels = tuple(Channel(n) for n in names)
    label = ", ".join(names)
    why = f"rung {rung}: {label}" if rung > 0 else label
    return channels, why, False


def next_step(touches: list[Touch], *, today: date | None = None,
              config: CadenceConfig | None = None) -> NextStep:
    """The next due touch, computed from evidence alone."""
    today = today or date.today()
    gap = config.business_day_gap if config else BUSINESS_DAY_GAP
    ooo_buf = config.ooo_buffer_days if config else OOO_RETURN_BUFFER_DAYS

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
            _shift(today, config),
            (Channel.LETTER,),
            rung_from_touches(touches),
            "email bounced: the address is dead, pivot immediately; the "
            "cadence does not advance",
            pivot_to_alternate_contact=False,
        )

    last = last_evidenced_outbound(touches)
    if last is None:
        return NextStep(_shift(today, config), (Channel.EMAIL,), 0,
                        "no outbound yet: initial email")

    rung = rung_from_touches(touches)
    due = add_business_days(last.occurred_on, gap)

    override = _ooo_override(touches, buffer_days=ooo_buf)
    if override is not None and override > due:
        due = override

    channels, why, pivot = _channels_for_rung(rung, config)
    if pivot:
        return NextStep(_shift(due, config), (), rung, why,
                        pivot_to_alternate_contact=True)

    if override is not None:
        why += " (out-of-office: return date + buffer)"

    return NextStep(_shift(due, config), channels, rung, why)


def is_warm_route(contact_ever_replied: bool) -> bool:
    """spec 9.6: a mutual connection who has never replied is not a warm route.

    Connection counts measure graph proximity, not willingness. Rank untried
    contacts above unresponsive ones, and treat re-asking someone who ignored a
    first request as a cost, not a free retry.
    """
    return bool(contact_ever_replied)
