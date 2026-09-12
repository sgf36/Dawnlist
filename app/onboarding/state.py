"""What setup has got through, kept apart from what calibration recorded.

WHY THESE ARE TWO QUESTIONS AND NOT ONE
---------------------------------------
`is_calibrated` was doing both jobs, and the second one badly. The launch path
asked it "has this person finished setting up?", and for anybody whose
calibration had been skipped — which is every user with no feed, by design, see
`CalibrationResult.can_finish` — the answer was no, for ever. They were sent
back to the first screen of setup on every launch, with their CVs, their aim,
their corrected factsheet and their brief all already stored and none of it
visible. Setup was the only screen they could ever reach.

So finishing setup is recorded here, calibration stays recorded where it was,
and the launch path reads this one. An uncalibrated user reaches their
shortlist and is offered calibration afterwards — `calibration_prompt` is the
sentence for that offer.

A DATABASE THAT PREDATES THIS FLAG IS NOT UNFINISHED. Anybody already
calibrated had, by definition, been all the way through; treating the missing
row as "not finished" would push every existing user back into setup on the
first launch after an update, which is the same bug wearing the fix's clothes.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from app.i18n import tr
from app.onboarding.calibration import is_calibrated

SETUP_FINISHED_KEY = "setup_finished_at"

#: Everything the user has typed into setup but not yet finished. One row, one
#: JSON object: this is a scratchpad for an unfinished flow, not a record, and
#: giving it columns would make a schema out of something whose shape follows
#: whatever the wizard happens to ask next.
DRAFT_KEY = "onboarding_draft"


def _get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key=?",
                       (key,)).fetchone()
    return row["value"] if row else None


def _set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    conn.commit()


def is_setup_finished(conn: sqlite3.Connection) -> bool:
    """Has this person been through setup — whether or not it calibrated."""
    return bool(_get(conn, SETUP_FINISHED_KEY)) or is_calibrated(conn)


def mark_setup_finished(conn: sqlite3.Connection) -> None:
    """Recorded when the user leaves the last screen, calibrated or not.

    Deliberately NOT a claim that calibration happened. `mark_calibrated` still
    refuses a gate that did not pass, and nothing here relaxes that.
    """
    _set(conn, SETUP_FINISHED_KEY,
         datetime.now(timezone.utc).isoformat(timespec="seconds"))


def calibration_prompt(conn: sqlite3.Connection) -> str | None:
    """What to tell somebody whose setup finished without calibrating.

    None when there is nothing to say — setup unfinished, or already
    calibrated — so a caller can put this straight on screen without forming
    its own view about the state.

    Exists as a function rather than a screen because the window that should
    show it is not this module's to edit, and a sentence hard-coded into that
    window would be the fifty-first place this fact is decided.
    """
    if not is_setup_finished(conn) or is_calibrated(conn):
        return None
    return tr("onboarding.calibration_pending")


# ---------------------------------------------------------------------------
# The unfinished flow itself
# ---------------------------------------------------------------------------

def load_draft(conn: sqlite3.Connection) -> dict:
    """What setup was part-way through last time. `{}` when there is nothing.

    A corrupt value is treated as nothing, never raised: this is a convenience,
    and refusing to open the application because a scratchpad would not parse
    would be a far worse failure than losing a half-typed aim.
    """
    raw = _get(conn, DRAFT_KEY)
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def save_draft(conn: sqlite3.Connection, draft: dict) -> None:
    _set(conn, DRAFT_KEY, json.dumps(draft))


def clear_draft(conn: sqlite3.Connection) -> None:
    """Dropped when setup finishes: the documents are saved properly by then,
    and a stale scratchpad would reopen a flow the user has completed."""
    conn.execute("DELETE FROM settings WHERE key=?", (DRAFT_KEY,))
    conn.commit()
