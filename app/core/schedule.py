"""When the daily run happens, and when a person may start one by hand.

THE PROMISE THIS KEEPS
----------------------
The app and both store listings say a shortlist arrives every morning, and
until this module nothing ever started a run: no scheduler entry, no startup
task, no timer and no button. Only `--run-once` on a command line reached
`morning_run`, and no customer has a command line.

TWO RULES, AND THEY ARE DIFFERENT ON PURPOSE
--------------------------------------------
  * `is_due` — the app has been open while the run time arrived, so it runs
    by itself. A person who left Dawnlist running asked for exactly that.

  * `should_offer_run_now` — the run time has passed today and no run has
    started today. The window OFFERS a run; it does not start one. Opening
    the app at lunchtime must never spend the day's feed allowance and the
    user's own Anthropic tokens as a side effect of looking at the shortlist.

"TODAY" IS THE USER'S DAY, NOT UTC'S
------------------------------------
`runs.started_at` is stored in UTC. A run at 00:30 in London during British
Summer Time is stored as 23:30 the previous UTC day, so comparing stored dates
as written would offer a second run just after midnight and refuse one on the
evening before. Every comparison converts to the local date first.

Wall-clock comparisons (`now.time() >= run_time`) are what make the clock
changes harmless: on the spring-forward day a 01:30 run time that never occurs
is simply passed at 02:00, and on the fall-back day the repeated hour cannot
fire twice because the first run has already started today.

Everything here is a pure function of its arguments, with the clock and the
local-time conversion passed in, so the midnight and DST boundaries are tested
directly rather than waited for.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable, Iterable

# The daily search's own kind, as `db.run` writes it. Imported rather than
# repeated: a second spelling of "sweep" here would go unnoticed until the day
# the scheduler stopped recognising the runs it had started itself.
from app.core.db import SWEEP

#: Early enough that the shortlist is waiting before a working day starts,
#: late enough that a laptop left asleep overnight has usually been opened.
DEFAULT_RUN_TIME = time(7, 0)

RUN_TIME_KEY = "run_time"
KEEP_RUNNING_KEY = "keep_running"
#: There is deliberately no stored flag for starting at sign-in. Windows and
#: macOS own that state, the user can change it in Task Manager or System
#: Settings without opening Dawnlist, and a copy kept here would be wrong from
#: the moment they did — see `app.core.sign_in.is_enabled`.
#:
#: Set once the "still running in the background" notice has been shown. Said
#: once, because a notice repeated on every close is one people learn to
#: dismiss unread.
TRAY_NOTICE_KEY = "tray_notice_shown"
#: The local date the automatic run last claimed. Shared by every running copy
#: through the database, so two copies left open cannot both run at 07:00.
SCHEDULED_CLAIM_KEY = "scheduled_run_claimed_on"

ToLocal = Callable[[datetime], datetime]


def system_local(moment: datetime) -> datetime:
    """The machine's own time zone, with its DST rules for that instant."""
    return moment.astimezone()


def local_now() -> datetime:
    return datetime.now().astimezone()


# ---------------------------------------------------------------------------
# The run time setting
# ---------------------------------------------------------------------------

def parse_run_time(text: str | None) -> time:
    """"HH:MM", or the default when the stored value is unusable.

    A bad value falls back rather than raising: a setting nobody can read must
    not be the reason the daily run never happens.
    """
    try:
        hours, minutes = (int(part) for part in (text or "").strip().split(":"))
        return time(hours, minutes)
    except (ValueError, TypeError):
        return DEFAULT_RUN_TIME


def format_run_time(value: time) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"


def _get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def _set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    conn.commit()


def load_run_time(conn: sqlite3.Connection) -> time:
    return parse_run_time(_get(conn, RUN_TIME_KEY))


def save_run_time(conn: sqlite3.Connection, value: time) -> None:
    _set(conn, RUN_TIME_KEY, format_run_time(value))


def load_flag(conn: sqlite3.Connection, key: str) -> bool:
    """Off unless switched on. Both flags this reads change what happens when
    the window closes or the user signs in, and neither may surprise anyone."""
    return _get(conn, key) == "1"


def save_flag(conn: sqlite3.Connection, key: str, on: bool) -> None:
    _set(conn, key, "1" if on else "0")


# ---------------------------------------------------------------------------
# "Has a run started today?"
# ---------------------------------------------------------------------------

def local_day_of(started_at: str | None, to_local: ToLocal = system_local
                 ) -> date | None:
    """The user's calendar date on which a stored UTC instant fell."""
    if not started_at:
        return None
    try:
        moment = datetime.fromisoformat(started_at)
    except ValueError:
        return None
    if moment.tzinfo is None:
        # Written by `db._now`, which is always UTC; a naive value is the same
        # instant with its offset lost, never a local time.
        moment = moment.replace(tzinfo=timezone.utc)
    return to_local(moment).date()


def started_on(started_ats: Iterable[str | None], day: date,
               to_local: ToLocal = system_local) -> bool:
    return any(local_day_of(s, to_local) == day for s in started_ats)


def run_time_passed(now: datetime, run_time: time) -> bool:
    # The wall clock, not an instant: see the module docstring on DST.
    return now.time().replace(tzinfo=None, fold=0) >= run_time


def should_offer_run_now(now: datetime, run_time: time, *,
                         started_today: bool) -> bool:
    """The owner's rule for Run now, and nothing else.

    Before the run time a hand-started run would leave the scheduled one with
    nothing to do and the feed's daily refreshes partly spent; after a run has
    started today a second one re-buys the same window.
    """
    return run_time_passed(now, run_time) and not started_today


def is_due(now: datetime, run_time: time, *, started_today: bool,
           watching_since: datetime, claimed_on: date | None) -> bool:
    """Whether the automatic run should start now.

    `watching_since` is when this copy began keeping time. A run time that had
    already passed by then did not pass WHILE the app was open, so it is
    offered rather than started — the launch rule, not the scheduler's.
    Crossing midnight while open re-arms it: left open overnight, the app runs
    at the next morning's run time.

    `claimed_on` is the local date the automatic run last claimed, so a run
    that refused to start (no key yet, say) is not retried every tick.
    """
    if started_today or claimed_on == now.date():
        return False
    if not run_time_passed(now, run_time):
        return False
    if watching_since.date() < now.date():
        return True
    return watching_since.time().replace(tzinfo=None, fold=0) < run_time


def next_utc_midnight(now: datetime, to_local: ToLocal = system_local) -> datetime:
    """When the feed's daily refreshes come back, in the user's own time.

    The Worker counts refreshes per UTC day (`today()` in its index.js), which
    in London is 01:00 for half the year. Saying "tomorrow" would be wrong for
    an hour every summer night.
    """
    utc = now.astimezone(timezone.utc)
    midnight = datetime.combine(utc.date() + timedelta(days=1), time(0),
                                tzinfo=timezone.utc)
    return to_local(midnight)


# ---------------------------------------------------------------------------
# Reading and claiming, against the database
# ---------------------------------------------------------------------------

def run_started_today(conn: sqlite3.Connection, now: datetime,
                      to_local: ToLocal = system_local) -> bool:
    """A daily search started on the user's current local date.

    A run still `running` counts: it is tagged when its row is opened, so a
    search another copy of the app is part-way through is already visible here
    and must not be offered again. Only rows from the last two UTC days are
    read, which covers every offset from UTC-12 to UTC+14.
    """
    since = (now.astimezone(timezone.utc) - timedelta(days=2)).isoformat(
        timespec="seconds")
    rows = conn.execute(
        "SELECT started_at FROM runs WHERE started_at >= ? AND kind = ?",
        (since, SWEEP)).fetchall()
    return started_on((r[0] for r in rows), now.date(), to_local)


def last_search_started_at(conn: sqlite3.Connection) -> str | None:
    """When the most recent search started, for the "Last run" line.

    Searches only. A calibration sample or an outreach run is not something the
    line is reporting, and neither is what the next one will be compared against.
    """
    row = conn.execute(
        "SELECT started_at FROM runs WHERE kind = ? ORDER BY id DESC LIMIT 1",
        (SWEEP,)).fetchone()
    return row[0] if row else None


def claimed_on(conn: sqlite3.Connection) -> date | None:
    value = _get(conn, SCHEDULED_CLAIM_KEY)
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def claim_scheduled_run(conn: sqlite3.Connection, day: date) -> bool:
    """Take today's automatic run, atomically. False if something else has.

    One statement, so two copies of the app ticking in the same second cannot
    both read "unclaimed" and both run: SQLite applies the upsert's WHERE under
    its write lock, and only one of them changes the row.
    """
    cur = conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value "
        "WHERE settings.value <> excluded.value",
        (SCHEDULED_CLAIM_KEY, day.isoformat()))
    conn.commit()
    return cur.rowcount == 1
