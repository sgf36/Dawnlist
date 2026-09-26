"""Ask the store for a rating, once, after the product has proved itself.

The prompt fires after at least three completed morning runs AND at least one
company on the board — which means the person has used the core loop (sweep,
read, pursue) enough to have an opinion worth stating. Asking earlier wastes
the one chance per year the system gives us.

ONCE means ONCE. Apple allows three prompts per 365 days but shows them at its
discretion; Microsoft's dialog has no documented cap but nagging is nagging.
This module asks once, records the date, and never asks again unless 365 days
have passed.

DEGRADATION, NOT FAILURE. Off-platform, without the framework, or on a build
variant that has no store: ``should_ask`` returns False and ``request`` is a
silent no-op. Nothing here raises.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date, timedelta

from app.core.build_variant import variant

log = logging.getLogger(__name__)

_SETTINGS_KEY = "review_prompt_last_asked"
_MIN_COMPLETED_SWEEPS = 3
_COOLDOWN_DAYS = 365


def _get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute(
        "SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def _set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    conn.commit()


def _completed_sweep_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM runs WHERE kind='sweep' AND status='complete'"
    ).fetchone()
    return row[0] if row else 0


def _has_opportunity(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM opportunities LIMIT 1"
    ).fetchone()
    return row is not None


def _recently_asked(conn: sqlite3.Connection, today: date) -> bool:
    last = _get(conn, _SETTINGS_KEY)
    if not last:
        return False
    try:
        asked_on = date.fromisoformat(last)
    except ValueError:
        return False
    return (today - asked_on) < timedelta(days=_COOLDOWN_DAYS)


def should_ask(conn: sqlite3.Connection, *, today: date | None = None) -> bool:
    """True when the user has earned an opinion and we have not asked recently."""
    today = today or date.today()
    v = variant()
    if v not in ("mas", "store_iap"):
        return False
    if _recently_asked(conn, today):
        return False
    if _completed_sweep_count(conn) < _MIN_COMPLETED_SWEEPS:
        return False
    if not _has_opportunity(conn):
        return False
    return True


def record_asked(conn: sqlite3.Connection, *, today: date | None = None) -> None:
    """Mark today as the last time the prompt was shown."""
    _set(conn, _SETTINGS_KEY, (today or date.today()).isoformat())


def request(conn: sqlite3.Connection, *, hwnd: int | None = None) -> None:
    """Ask the platform for a review dialog, if appropriate.

    Records the attempt regardless of whether the platform actually showed
    anything — we cannot know, and asking twice is worse than asking zero.
    """
    if not should_ask(conn):
        return

    v = variant()
    shown = False

    if v == "mas":
        shown = _request_mac()
    elif v == "store_iap":
        shown = _request_windows(hwnd)

    if shown:
        record_asked(conn)
        log.info("review prompt requested")


def _request_mac() -> bool:
    """macOS: SKStoreReviewController.requestReview()."""
    try:
        import StoreKit  # noqa: F811
        from AppKit import NSApplication
    except Exception:
        log.debug("StoreKit or AppKit unavailable — skipping review prompt")
        return False
    try:
        window = NSApplication.sharedApplication().keyWindow()
        if window is not None:
            StoreKit.SKStoreReviewController.requestReviewInScene_(
                window)
        else:
            StoreKit.SKStoreReviewController.requestReview()
        return True
    except Exception:
        log.debug("review prompt failed", exc_info=True)
        return False


def _request_windows(hwnd: int | None = None) -> bool:
    """Windows: StoreContext.RequestRateAndReviewAppAsync()."""
    try:
        from winrt.windows.services.store import StoreContext
    except Exception:
        log.debug("Windows.Services.Store unavailable — skipping review prompt")
        return False
    try:
        context = StoreContext.get_default()
        if hwnd:
            from winrt.runtime.interop import initialize_with_window
            initialize_with_window(context, int(hwnd))
        context.request_rate_and_review_app_async()
        return True
    except Exception:
        log.debug("review prompt failed", exc_info=True)
        return False
