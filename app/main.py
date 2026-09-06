"""Dawnlist entry point.

Two ways in, and they share everything below the surface:

    python -m app.main                 # the app
    python -m app.main --run-once      # the morning run, headless
    python -m app.main --board         # open on the board
    python -m app.main --audit         # print the board audit and exit

The headless form exists so a scheduled run and a hand-run produce identical
state — a scheduler that drives a different code path is a second system that
drifts from the first.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.core import db
from app.core.pipeline import (Gate, permanent_reject_gate, persist,
                               posted_within_gate, run_morning)
from app.core.rules import RuleTable
from app.feed.base import SearchQuery
from app.i18n import set_locale

DEFAULT_POSTED_WITHIN_DAYS = 45


def _force_utf8_console() -> None:
    """The Windows console is cp1252 and job titles are not.

    Measured in P0: a run died on the first en-dash AFTER the credits had
    already been spent. `--run-once` prints titles, screen reasons and verdicts,
    so this applies to BOTH streams — the first version of this file fixed only
    stdout and an em-dash on stderr still came out as a replacement character.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001 - never block startup on this
                pass


_force_utf8_console()


class NotConfigured(RuntimeError):
    """Raised when a run is attempted before onboarding has produced a brief.

    Deliberately loud. The calibration gate is the transfer-of-judgement step
    that made the original system work, and a run against an empty brief would
    produce a plausible-looking shortlist built on nothing.
    """


def load_settings(conn) -> dict[str, str]:
    return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}


def load_document(conn, kind: str) -> str:
    """The latest version of the fit brief or factsheet."""
    row = conn.execute(
        "SELECT body FROM documents WHERE kind=? ORDER BY version DESC LIMIT 1",
        (kind,)).fetchone()
    return row["body"] if row else ""


def load_queries(conn) -> list[SearchQuery]:
    import json
    out: list[SearchQuery] = []
    for row in conn.execute("SELECT * FROM queries WHERE enabled=1 ORDER BY id"):
        params = json.loads(row["params_json"])
        since = None
        if row["last_discovered_at"]:
            try:
                since = datetime.fromisoformat(row["last_discovered_at"])
            except ValueError:
                since = None
        out.append(SearchQuery(
            label=row["label"],
            titles=params.get("titles", []),
            countries=params.get("countries", []),
            companies=params.get("companies", []),
            posted_within_days=params.get("posted_within_days",
                                          DEFAULT_POSTED_WITHIN_DAYS),
            discovered_since=since,
            max_results=params.get("max_results", 500),
        ))
    return out


def load_rules(conn) -> RuleTable:
    import json

    from app.core.rules import KillFamily, KillFamilyError

    table = RuleTable()
    for row in conn.execute("SELECT field, term FROM rule_terms"):
        getattr(table, row["field"]).append(row["term"])

    for row in conn.execute("SELECT * FROM kill_families"):
        try:
            table.kill_families.append(KillFamily(
                name=row["name"],
                employers=tuple(json.loads(row["employers_json"])),
                kill_titles=tuple(json.loads(row["kill_json"])),
                saves_titles=tuple(json.loads(row["saves_json"])),
                precedents=tuple(tuple(p) for p in
                                 json.loads(row["precedents_json"])),
                adopted=bool(row["adopted"]),
            ))
        except KillFamilyError as exc:
            # A stored family that no longer satisfies the rules is skipped
            # LOUDLY rather than silently dropped or silently applied.
            print(f"warning: kill family {row['name']!r} is invalid and was "
                  f"not loaded: {exc}", file=sys.stderr)
    return table


def mark_queries_run(conn, when: datetime | None = None) -> None:
    """Advance each query's delta high-water mark.

    handoff 2.1a: billing is per job RETURNED, so re-fetching yesterday's
    postings is re-buying them. This is written only after a run completes.
    """
    when = when or datetime.now(timezone.utc)
    conn.execute("UPDATE queries SET last_discovered_at=? WHERE enabled=1",
                 (when.isoformat(timespec="seconds"),))
    conn.commit()


def build_provider(conn):
    """The feed provider, from the stored settings.

    Kept behind a function so a BYO-keys user, a managed-tier user routed
    through the Worker, and a future dataset adapter all enter the same way.
    """
    import keyring

    from app.feed.theirstack import TheirStackProvider

    key = keyring.get_password("dawnlist-feed", "api-key")
    if not key:
        raise NotConfigured(
            "No feed credential found. Store one under the keyring service "
            "'dawnlist-feed', account 'api-key', or sign in to the managed tier.")
    return TheirStackProvider(key)


def build_send(conn):
    """The assessment transport. Never called during tests."""
    import anthropic
    import keyring

    api_key = keyring.get_password("dawnlist-anthropic", "api-key")
    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    def send(request: dict):
        response = client.messages.create(**request)
        return "".join(b.text for b in response.content if b.type == "text")

    return send


def morning_run(conn, *, provider=None, send=None, today: date | None = None):
    """One morning run, with the gates the stored state implies."""
    from app.ui.adapter import rejected_keys

    from app.onboarding.calibration import is_calibrated

    brief = load_document(conn, "fit_brief")
    factsheet = load_document(conn, "factsheet")
    if not brief.strip():
        raise NotConfigured(
            "No fit brief yet. Run onboarding first — a run against an empty "
            "brief produces a plausible shortlist built on nothing.")
    if not is_calibrated(conn):
        # The gate is checked HERE, at the only door into a run, rather than in
        # the UI. A gate enforced in a screen is a gate the scheduled run walks
        # straight past.
        raise NotConfigured(
            "Calibration has not been completed. The app must show you ~10 "
            "live postings and have you correct its verdicts before it runs "
            "daily — that is the step that transfers your judgement into the "
            "brief, and without it the shortlist is a guess that looks like an "
            "answer.")

    queries = load_queries(conn)
    if not queries:
        raise NotConfigured("No saved queries. Add at least one before running.")

    seen = {(r["provider"], r["provider_job_id"])
            for r in conn.execute("SELECT provider, provider_job_id FROM seen_jobs")}

    gates: list[Gate] = [
        permanent_reject_gate(rejected_keys(conn)),
        posted_within_gate(DEFAULT_POSTED_WITHIN_DAYS, today=today),
    ]

    outcome = run_morning(
        conn, provider or build_provider(conn), queries, load_rules(conn),
        fit_brief=brief, factsheet=factsheet,
        send=send or build_send(conn), gates=gates, already_seen=seen,
    )
    persist(conn, outcome)
    if not outcome.fetch_errors:
        # Only advance the delta mark on a clean fetch. Advancing it after a
        # failure would skip the window the failed run never actually read.
        mark_queries_run(conn)
    return outcome


def print_funnel(outcome) -> None:
    """The whole funnel, never one number without what it excludes."""
    counts = outcome.funnel()
    width = max(len(k) for k in counts)
    for key, value in counts.items():
        print(f"  {key.replace('_', ' '):<{width}}  {value}")
    if outcome.fetch_errors:
        print("\n  FETCH PROBLEMS — this run is incomplete:")
        for err in outcome.fetch_errors:
            print(f"    - {err}")
    if outcome.unscored_queries:
        print(f"\n  no yield rate computed for: "
              f"{', '.join(outcome.unscored_queries)} (fetch failed)")
    loose = outcome.loose_queries()
    if loose:
        print(f"\n  below 10% yield, rewrite rather than widen: {', '.join(loose)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dawnlist")
    parser.add_argument("--run-once", action="store_true",
                        help="run the morning sweep headlessly and exit")
    parser.add_argument("--board", action="store_true",
                        help="open on the board rather than the shortlist")
    parser.add_argument("--audit", action="store_true",
                        help="print the board audit and exit")
    parser.add_argument("--db", type=Path, default=None,
                        help="database path (defaults to the app data dir)")
    parser.add_argument("--locale", default=None, help="UI locale, e.g. fr")
    args = parser.parse_args(argv)

    conn = db.connect(args.db)
    db.migrate(conn)

    settings = load_settings(conn)
    set_locale(args.locale or settings.get("locale", "en"))

    if args.audit:
        from app.core.board_repo import audit_board
        findings = audit_board(conn)
        print(f"{len(findings['scanned'])} opportunities checked")
        for key in ("bounce_corrections", "parity_defects",
                    "duplicate_open_children"):
            for item in findings[key]:
                print(f"  {key}: {item}")
        return 0

    if args.run_once:
        try:
            outcome = morning_run(conn)
        except NotConfigured as exc:
            print(f"not configured: {exc}", file=sys.stderr)
            return 2
        print_funnel(outcome)
        # An incomplete run exits non-zero so a scheduler notices. A run that
        # fetched nothing because the endpoint failed must never look like a
        # quiet morning.
        return 0 if outcome.complete else 1

    return _launch_ui(conn, open_board=args.board)


def _launch_ui(conn, *, open_board: bool) -> int:
    from PySide6.QtWidgets import QApplication

    from app.ui.adapter import connect_window
    from app.ui.board import BoardWindow
    from app.ui.board_adapter import board_rows, connect_board
    from app.ui.review import ReviewWindow

    app = QApplication.instance() or QApplication(sys.argv)

    if open_board:
        window = BoardWindow()
        connect_board(window, conn)
        rows, findings = board_rows(conn)
        window.load(rows, findings)
    else:
        window = ReviewWindow()
        connect_window(window, conn)
        window.load([], {})

    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
