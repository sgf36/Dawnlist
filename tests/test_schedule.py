"""The daily run's timing rules, at the boundaries where they break.

Every clock here is injected. Waiting for a real midnight or a real change to
British Summer Time is how these rules would otherwise go untested until a user
in some other time zone met the bug first.
"""
from datetime import date, datetime, time, timedelta, timezone, tzinfo

import pytest

from app.core import db, schedule
from app.core.schedule import (DEFAULT_RUN_TIME, claim_scheduled_run, is_due,
                               local_day_of, next_utc_midnight,
                               parse_run_time, run_started_today,
                               should_offer_run_now)

UTC = timezone.utc


class _London(tzinfo):
    """Europe/London without tzdata, which this Windows venv does not have.

    Written out rather than skipped when `zoneinfo` cannot find the zone: a
    skipped DST test is exactly the test that never runs on the machine the
    app ships from. The UK rule is the EU one — summer time from 01:00 UTC on
    the last Sunday of March to 01:00 UTC on the last Sunday of October.
    """

    @staticmethod
    def _bounds(year):
        def last_sunday(month):
            end = date(year, month, 31)
            return end - timedelta(days=(end.weekday() - 6) % 7)
        return (datetime.combine(last_sunday(3), time(1), tzinfo=UTC),
                datetime.combine(last_sunday(10), time(1), tzinfo=UTC))

    def fromutc(self, dt):
        utc = dt.replace(tzinfo=UTC)
        start, end = self._bounds(dt.year)
        summer = start <= utc < end
        local = (dt + (timedelta(hours=1) if summer else timedelta(0)))
        # The hour after the clocks go back happens twice; the second pass is
        # fold=1, as `zoneinfo` would mark it.
        repeated = end <= utc < end + timedelta(hours=1)
        return local.replace(tzinfo=self, fold=1 if repeated else 0)

    def utcoffset(self, dt):
        start, end = self._bounds(dt.year)
        wall = dt.replace(tzinfo=None)
        start_wall = start.replace(tzinfo=None)                 # 01:00 GMT
        end_wall = end.replace(tzinfo=None) + timedelta(hours=1)  # 02:00 BST
        if dt.fold == 0 and start_wall <= wall < end_wall:
            return timedelta(hours=1)
        if dt.fold == 1 and start_wall <= wall < end_wall - timedelta(hours=1):
            return timedelta(hours=1)
        return timedelta(0)

    def dst(self, dt):
        return self.utcoffset(dt)

    def tzname(self, dt):
        return "BST" if self.utcoffset(dt) else "GMT"


LONDON = _London()


def london(moment_utc: datetime) -> datetime:
    return moment_utc.astimezone(LONDON)


def at(y, mo, d, h, mi) -> datetime:
    """A London wall-clock reading, given as the UTC instant it happened."""
    return london(datetime(y, mo, d, h, mi, tzinfo=UTC))


def test_the_test_zone_is_itself_right():
    """A home-made zone that is wrong would make every test below agree with
    it. Pinned against dates whose offsets are not in doubt."""
    assert at(2026, 9, 11, 6, 0).hour == 7          # BST in September
    assert at(2026, 12, 1, 6, 0).hour == 6          # GMT in December
    assert at(2026, 3, 29, 0, 59).hour == 0         # the last GMT minute
    assert at(2026, 3, 29, 1, 0).hour == 2          # 01:00 never happens
    first = at(2026, 10, 25, 0, 30)
    second = at(2026, 10, 25, 1, 30)
    assert (first.hour, first.fold) == (1, 0)
    assert (second.hour, second.fold) == (1, 1)     # 01:30 happens twice
    assert first.astimezone(UTC) != second.astimezone(UTC)


# ---------------------------------------------------------------------------
# The setting
# ---------------------------------------------------------------------------

def test_the_run_time_reads_hours_and_minutes():
    assert parse_run_time("08:45") == time(8, 45)
    assert parse_run_time(" 6:05 ") == time(6, 5)


@pytest.mark.parametrize("bad", [None, "", "seven", "25:00", "07:60", "7"])
def test_an_unreadable_run_time_falls_back_rather_than_stopping_runs(bad):
    assert parse_run_time(bad) == DEFAULT_RUN_TIME == time(7, 0)


def test_settings_round_trip(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite3")
    db.migrate(conn)
    assert schedule.load_run_time(conn) == time(7, 0)
    assert schedule.load_flag(conn, schedule.KEEP_RUNNING_KEY) is False

    schedule.save_run_time(conn, time(9, 30))
    schedule.save_flag(conn, schedule.KEEP_RUNNING_KEY, True)
    assert schedule.load_run_time(conn) == time(9, 30)
    assert schedule.load_flag(conn, schedule.KEEP_RUNNING_KEY) is True

    schedule.save_flag(conn, schedule.KEEP_RUNNING_KEY, False)
    assert schedule.load_flag(conn, schedule.KEEP_RUNNING_KEY) is False
    conn.close()


# ---------------------------------------------------------------------------
# "Today" is the user's date, not the stored UTC date
# ---------------------------------------------------------------------------

def test_a_run_just_after_local_midnight_belongs_to_the_new_day():
    # 23:30 UTC on the 10th is 00:30 BST on the 11th.
    stored = "2026-09-10T23:30:00+00:00"
    assert local_day_of(stored, london) == date(2026, 9, 11)
    # Positive control: read as UTC it is still the 10th, which is the bug
    # this conversion exists to prevent.
    assert local_day_of(stored, lambda m: m.astimezone(UTC)) == date(2026, 9, 10)


def test_a_naive_stored_time_is_read_as_utc():
    assert local_day_of("2026-09-10T23:30:00", london) == date(2026, 9, 11)


def test_an_unreadable_stored_time_is_no_day_at_all():
    assert local_day_of("not a time", london) is None
    assert local_day_of(None, london) is None


def test_the_midnight_boundary_separates_yesterday_from_today():
    now = at(2026, 9, 10, 23, 10)        # 00:10 BST on the 11th
    just_before = "2026-09-10T22:55:00+00:00"   # 23:55 BST on the 10th
    just_after = "2026-09-10T23:05:00+00:00"    # 00:05 BST on the 11th
    assert not schedule.started_on([just_before], now.date(), london)
    assert schedule.started_on([just_after], now.date(), london)


# ---------------------------------------------------------------------------
# Run now: the owner's rule
# ---------------------------------------------------------------------------

def test_run_now_is_not_offered_before_the_run_time():
    assert not should_offer_run_now(at(2026, 9, 11, 5, 59), time(7, 0),
                                    started_today=False)   # 06:59 BST


def test_run_now_is_offered_from_the_run_time_itself():
    assert should_offer_run_now(at(2026, 9, 11, 6, 0), time(7, 0),
                                started_today=False)       # 07:00 BST


def test_run_now_is_not_offered_once_a_run_has_started_today():
    now = at(2026, 9, 11, 11, 0)
    assert should_offer_run_now(now, time(7, 0), started_today=False)
    assert not should_offer_run_now(now, time(7, 0), started_today=True)


def test_spring_forward_a_run_time_that_never_occurs_still_passes():
    # 01:30 does not exist in London on 29 March 2026: 00:59 GMT is followed
    # by 02:00 BST. The run must not wait until 01:30 tomorrow.
    assert not should_offer_run_now(at(2026, 3, 29, 0, 59), time(1, 30),
                                    started_today=False)
    assert should_offer_run_now(at(2026, 3, 29, 1, 0), time(1, 30),
                                started_today=False)


def test_fall_back_the_repeated_hour_cannot_run_twice(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite3")
    db.migrate(conn)
    first = at(2026, 10, 25, 0, 30)      # 01:30 BST
    second = at(2026, 10, 25, 1, 30)     # 01:30 GMT, an hour later

    assert should_offer_run_now(first, time(1, 30), started_today=False)
    conn.execute("INSERT INTO runs(started_at, status, kind) "
                 "VALUES('2026-10-25T00:30:00+00:00', 'complete', 'sweep')")
    conn.commit()

    started = run_started_today(conn, second, london)
    assert started, "the run at the first 01:30 is today's run"
    assert not should_offer_run_now(second, time(1, 30), started_today=started)
    assert not is_due(second, time(1, 30), started_today=started,
                      watching_since=at(2026, 10, 24, 20, 0), claimed_on=None)
    conn.close()


# ---------------------------------------------------------------------------
# The automatic run
# ---------------------------------------------------------------------------

def test_the_run_time_arriving_while_open_makes_the_run_due():
    assert is_due(at(2026, 9, 11, 6, 0), time(7, 0), started_today=False,
                  watching_since=at(2026, 9, 11, 5, 0), claimed_on=None)


def test_opening_the_app_after_the_run_time_does_not_spend_anything():
    """Launched at 09:00 with no run today: offered, never started."""
    launched = at(2026, 9, 11, 8, 0)
    now = launched + timedelta(minutes=5)
    assert not is_due(now, time(7, 0), started_today=False,
                      watching_since=launched, claimed_on=None)
    assert should_offer_run_now(now, time(7, 0), started_today=False)


def test_left_open_overnight_it_runs_the_next_morning():
    assert is_due(at(2026, 9, 12, 6, 1), time(7, 0), started_today=False,
                  watching_since=at(2026, 9, 11, 21, 0), claimed_on=None)


def test_it_is_not_due_before_the_run_time_even_when_left_open_overnight():
    assert not is_due(at(2026, 9, 12, 5, 59), time(7, 0), started_today=False,
                      watching_since=at(2026, 9, 11, 21, 0), claimed_on=None)


def test_a_claimed_day_is_not_run_again():
    now = at(2026, 9, 11, 6, 0)
    kw = dict(started_today=False, watching_since=at(2026, 9, 11, 5, 0))
    assert is_due(now, time(7, 0), claimed_on=date(2026, 9, 10), **kw)
    assert not is_due(now, time(7, 0), claimed_on=date(2026, 9, 11), **kw)


def test_a_run_already_started_today_is_not_due():
    assert not is_due(at(2026, 9, 11, 6, 0), time(7, 0), started_today=True,
                      watching_since=at(2026, 9, 11, 5, 0), claimed_on=None)


def test_the_refreshes_come_back_at_utc_midnight_in_local_time():
    summer = next_utc_midnight(at(2026, 9, 11, 9, 0), london)
    assert (summer.date(), summer.hour) == (date(2026, 9, 12), 1)
    winter = next_utc_midnight(at(2026, 12, 1, 9, 0), london)
    assert (winter.date(), winter.hour) == (date(2026, 12, 2), 0)


def test_the_refresh_reset_is_the_next_utc_day_even_late_in_the_local_evening():
    # 00:30 BST on the 12th is still the 11th in UTC, so the reset is 01:00
    # BST that same night — not the night after.
    reset = next_utc_midnight(at(2026, 9, 11, 23, 30), london)
    assert (reset.date(), reset.hour) == (date(2026, 9, 12), 1)


# ---------------------------------------------------------------------------
# Against the database
# ---------------------------------------------------------------------------

@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.sqlite3")
    db.migrate(c)
    yield c
    c.close()


def add_run(conn, started_at, *, status="complete", kind="sweep"):
    conn.execute("INSERT INTO runs(started_at, status, kind) VALUES(?,?,?)",
                 (started_at, status, kind))
    conn.commit()


def test_a_search_started_today_counts(conn):
    now = at(2026, 9, 11, 9, 0)
    assert not run_started_today(conn, now, london)
    add_run(conn, "2026-09-11T06:02:00+00:00")
    assert run_started_today(conn, now, london)


def test_a_draft_run_today_is_not_the_daily_search(conn):
    """Drafting follow-ups at 06:50 must not cancel the 07:00 search."""
    now = at(2026, 9, 11, 9, 0)
    add_run(conn, "2026-09-11T05:50:00+00:00", kind=None)
    assert not run_started_today(conn, now, london)


def test_a_run_in_progress_counts_before_it_is_tagged(conn):
    """`morning_run` tags its row when it ends. Another copy of the app must
    not start a second run while the first is still fetching."""
    now = at(2026, 9, 11, 9, 0)
    add_run(conn, "2026-09-11T07:58:00+00:00", status="running", kind=None)
    assert run_started_today(conn, now, london)


def test_yesterdays_search_does_not_count_today(conn):
    add_run(conn, "2026-09-10T06:00:00+00:00")
    assert not run_started_today(conn, at(2026, 9, 11, 9, 0), london)


def test_only_one_copy_can_claim_the_days_automatic_run(conn):
    assert claim_scheduled_run(conn, date(2026, 9, 11))
    assert not claim_scheduled_run(conn, date(2026, 9, 11))
    assert schedule.claimed_on(conn) == date(2026, 9, 11)
    assert claim_scheduled_run(conn, date(2026, 9, 12)), "and the next day is free"


def test_a_second_connection_sees_the_claim(tmp_path):
    """The claim is only worth having if another process honours it."""
    path = tmp_path / "shared.sqlite3"
    one = db.connect(path)
    db.migrate(one)
    two = db.connect(path)
    assert claim_scheduled_run(one, date(2026, 9, 11))
    assert not claim_scheduled_run(two, date(2026, 9, 11))
    one.close()
    two.close()
