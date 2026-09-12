"""Agreeing to the end-user terms, and recording that it happened.

WHY THIS IS IN THE APPLICATION AND NOT AT THE CHECKOUT
-----------------------------------------------------
The data licence Dawnlist's feed comes under requires every downstream
recipient — which is every subscriber — to be bound by written terms at least
as restrictive as the provider's own, and the licence to hold any of that data
terminates on breach of that clause. Neither store's checkout shows Dawnlist's
terms: Apple's shows Apple's standard EULA, and the Microsoft Store listing is
free, so on both routes a subscriber arrived bound by nothing. A link in
Settings makes the terms findable, which is not the same as agreed.

So acceptance happens here, before first use, in every edition.

WHAT IS RECORDED, AND WHY THE DATE IS PART OF IT
------------------------------------------------
The terms' own "Last updated" date, and the moment the user agreed. Recording
only "accepted: yes" would make a later revision invisible — everyone would
stay bound to whatever they happened to read first, and the terms themselves
promise to ask again when they change. Comparing the stored date with
TERMS_LAST_UPDATED is what asks again, so that constant is the trigger: bump it
when the published page changes, and every user is asked once more.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

#: The "Last updated" date on https://dawnlist.spencerfields.com/terms.html.
#:
#: THIS MUST EQUAL THE DATE ON THE PUBLISHED PAGE. It is not a version number
#: this app is free to choose: it is shown to the user as the date of the
#: document they are agreeing to, and it is what decides whether somebody who
#: agreed before is asked again. A stale value here means people are recorded
#: as having accepted terms they were never shown.
TERMS_LAST_UPDATED = "2026-09-11"

ACCEPTED_VERSION_KEY = "terms_accepted_version"
ACCEPTED_AT_KEY = "terms_accepted_at"


def _get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key=?",
                       (key,)).fetchone()
    return row["value"] if row else None


def accepted_version(conn: sqlite3.Connection) -> str | None:
    """The terms date this machine agreed to, or None if it never has."""
    return _get(conn, ACCEPTED_VERSION_KEY)


def accepted_at(conn: sqlite3.Connection) -> str | None:
    return _get(conn, ACCEPTED_AT_KEY)


def is_accepted(conn: sqlite3.Connection) -> bool:
    """Whether the CURRENT terms have been agreed to.

    An older acceptance is not this one. Treating any acceptance as sufficient
    is how a revision reaches nobody.
    """
    return accepted_version(conn) == TERMS_LAST_UPDATED


def has_changed_since_acceptance(conn: sqlite3.Connection) -> bool:
    """True only for somebody who agreed to an EARLIER version.

    Kept apart from `is_accepted` because the two cases want different words:
    a first-time user is being asked, and this one is being asked again.
    """
    version = accepted_version(conn)
    return bool(version) and version != TERMS_LAST_UPDATED


def record_acceptance(conn: sqlite3.Connection, *,
                      now: datetime | None = None) -> None:
    """Record agreement to the terms as they stand today."""
    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    for key, value in ((ACCEPTED_VERSION_KEY, TERMS_LAST_UPDATED),
                       (ACCEPTED_AT_KEY, stamp)):
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    conn.commit()
