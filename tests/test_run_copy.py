"""The app may not promise a run it will not make.

Until the schedule existed, nothing in Dawnlist ever started a run: no task, no
timer, no button. The interface still said a shortlist arrived every morning,
which was true only of a run nobody could cause. The wording now describes what
actually happens — a run once a day at the time set in Settings, while Dawnlist
is running — and this keeps it that way.
"""
import json
import pathlib

import pytest

from app import i18n

ENGLISH = pathlib.Path(i18n.__file__).parent / "resources" / "locales" / "en.json"
CATALOGUE = json.loads(ENGLISH.read_text(encoding="utf-8"))

#: The words that promised a time of day nothing was keeping.
PROMISES = ("morning", "each day at", "every day at")


@pytest.mark.parametrize("key, text", sorted(CATALOGUE.items()))
def test_no_english_string_promises_a_morning_run(key, text):
    lowered = text.lower()
    for promise in PROMISES:
        assert promise not in lowered, (
            f"{key} promises a run the app does not make on its own: {text!r}")


@pytest.mark.parametrize("key, expected", [
    # The positive control for the rule above: these are the strings that used
    # to make the promise, and each still tells the user when a run happens
    # rather than going silent about it.
    ("onboarding.interview_body", "each day's run"),
    ("onboarding.calibration_body", "the next run"),
    ("onboarding.searches_off", "the daily run"),
    ("searches.body", "daily run"),
    ("searches.switched_on", "each daily run"),
    ("settings.subscribe_done", "the next daily run"),
    ("schedule.body", "once a day"),
])
def test_the_reworded_strings_still_say_when(key, expected):
    assert expected in CATALOGUE[key]


def test_the_run_time_is_findable_from_the_searches_screen():
    """A search "swept in the daily run" is no use without the run time."""
    assert "run time" in CATALOGUE["searches.body"]


def test_the_calibration_gate_no_longer_says_it_runs_daily():
    from app.main import NotConfigured, morning_run
    from app.core import db

    conn = db.connect(":memory:")
    db.migrate(conn)
    conn.execute("INSERT INTO documents(kind, version, body, created_at) "
                 "VALUES('fit_brief', 1, 'A brief.', 'x')")
    conn.commit()
    with pytest.raises(NotConfigured) as refused:
        morning_run(conn)
    assert "before its first run" in str(refused.value)
    assert "runs daily" not in str(refused.value)
    conn.close()
