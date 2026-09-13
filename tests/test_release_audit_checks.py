"""The checks behind a release, pinned so each runs on every build.

On 2026-09-13 these were run by hand against the shipped code and reported as
passing. A check run once proves the commit it ran on. Each is here so the next
build proves it too, and each negative check carries a positive control from
the same method, because a checker that cannot see anything also finds nothing.

Two claims from that audit cannot live here and are not pretended to: a real
Microsoft Store purchase, and a Mac run on a real Anthropic key. Start at
sign-in is pinned only as far as the manifest (tests/test_sign_in.py); the
switch itself needs an installed package.
"""
from __future__ import annotations

import ast
import json
import pathlib
import sqlite3
import string
from datetime import timedelta

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOCALES = ROOT / "app" / "resources" / "locales"
_FORMATTER = string.Formatter()


def _fields(text: str) -> set[str]:
    return {name for _, name, _, _ in _FORMATTER.parse(text) if name}


def _english() -> dict:
    return json.loads((LOCALES / "en.json").read_text(encoding="utf-8"))


# -- translations: a placeholder mismatch is a crash in that language only -----

def test_every_locale_keeps_exactly_the_english_placeholders():
    english = _english()
    wrong = []
    for path in sorted(LOCALES.glob("*.json")):
        catalogue = json.loads(path.read_text(encoding="utf-8"))
        for key, text in english.items():
            if not isinstance(text, str) or key not in catalogue:
                continue
            try:
                theirs = _fields(catalogue[key])
            except ValueError as exc:       # an unbalanced brace
                wrong.append(f"{path.stem} {key}: {exc}")
                continue
            if theirs != _fields(text):
                wrong.append(f"{path.stem} {key}: {sorted(theirs)} != {sorted(_fields(text))}")
    assert not wrong, "\n".join(wrong[:40])


def _tr_mismatches(source: str, english: dict, filename="<src>"):
    found, checked = [], 0
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "id", getattr(node.func, "attr", None)) == "tr"
                and node.args and isinstance(node.args[0], ast.Constant)
                and node.args[0].value in english):
            continue
        if any(kw.arg is None for kw in node.keywords):     # tr(key, **values)
            continue
        checked += 1
        need = _fields(english[node.args[0].value])
        given = {kw.arg for kw in node.keywords}
        if need != given:
            found.append(f"{filename}:{node.lineno} {node.args[0].value} "
                         f"needs {sorted(need)}, given {sorted(given)}")
    return found, checked


def test_every_tr_call_passes_exactly_the_placeholders_its_string_needs():
    english = _english()
    found, checked = [], 0
    for path in (ROOT / "app").rglob("*.py"):
        more, n = _tr_mismatches(path.read_text(encoding="utf-8"), english,
                                 path.relative_to(ROOT).as_posix())
        found += more
        checked += n
    assert checked > 300, f"only {checked} calls seen: the scan is broken"
    assert not found, "\n".join(found)


def test_the_tr_check_catches_a_missing_placeholder():
    found, checked = _tr_mismatches('tr("x.y")\ntr("x.y", name=1)\n',
                                    {"x.y": "Hello {name}"})
    assert checked == 2 and len(found) == 1


# -- background work never touches the UI thread's database connection ---------

def _uses_ui_connection(fn: ast.AST) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and node.id == "conn":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "_conn":
            return True
    return False


def _background_offenders(source: str, filename="<src>"):
    tree = ast.parse(source)
    offenders, seen = [], 0
    for scope in ast.walk(tree):
        if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        local_defs = {n.name: n for n in ast.walk(scope)
                      if isinstance(n, ast.FunctionDef) and n is not scope}
        for call in ast.walk(scope):
            if not (isinstance(call, ast.Call)
                    and getattr(call.func, "id", None) == "run_in_background"
                    and call.args):
                continue
            seen += 1
            work = call.args[0]
            body = work if isinstance(work, ast.Lambda) else local_defs.get(
                getattr(work, "id", None))
            if body is not None and _uses_ui_connection(body):
                offenders.append(f"{filename}:{call.lineno}")
    return offenders, seen


def test_no_background_task_uses_the_ui_threads_database_connection():
    """sqlite3 refuses a connection from another thread, and the refusal
    arrives as the task's failure, which reads as a network or model fault."""
    offenders, seen = [], 0
    for path in (ROOT / "app").rglob("*.py"):
        more, n = _background_offenders(path.read_text(encoding="utf-8"),
                                        path.relative_to(ROOT).as_posix())
        offenders += more
        seen += n
    assert seen >= 15, f"only {seen} background tasks seen: the scan is broken"
    assert not offenders, "background work reads the UI connection at " + ", ".join(offenders)


def test_the_background_check_catches_a_captured_connection():
    bad = ("def f(conn):\n"
           "    run_in_background(lambda: all_queries(conn))\n"
           "    def work():\n        return conn.execute('x')\n"
           "    run_in_background(work)\n")
    good = ("def f(path):\n"
            "    def work():\n        c = db.connect(path)\n        return c\n"
            "    run_in_background(work)\n")
    assert len(_background_offenders(bad)[0]) == 2
    assert _background_offenders(good) == ([], 1)


# -- Anthropic: model names and the SDK that ships ----------------------------

def test_the_models_are_current_names_and_the_shipped_sdk_takes_structured_output():
    from app.intelligence import assess

    assert assess.ASSESSMENT_MODEL == "claude-haiku-4-5"
    assert assess.DRAFTING_MODEL == "claude-sonnet-5"

    lock = (ROOT / "requirements.lock.txt").read_text(encoding="utf-8")
    pinned = next(line.split("==")[1].split()[0] for line in lock.splitlines()
                  if line.startswith("anthropic=="))
    assert int(pinned.split(".")[0]) >= 1, f"anthropic {pinned} predates output_config"

    anthropic = pytest.importorskip("anthropic")
    import inspect
    create = anthropic.resources.messages.Messages.create
    assert "output_config" in inspect.signature(create).parameters


# -- updating from 1.1.0 ------------------------------------------------------

def _database_from_1_1_0(tmp_path):
    from app.core import db

    path = tmp_path / "upgraded.sqlite3"
    raw = sqlite3.connect(path)
    raw.executescript((ROOT / "tests" / "fixtures" /
                       "database-as-shipped-1.1.0.sql").read_text(encoding="utf-8"))
    raw.close()
    conn = db.connect(path)
    db.migrate(conn)
    return conn


def test_a_1_1_0_database_migrates_and_every_reader_the_window_uses_works(tmp_path):
    from app import main
    from app.core import db, schedule
    from app.ui.adapter import latest_run_id, rows_from_db

    conn = _database_from_1_1_0(tmp_path)
    version = conn.execute("SELECT value FROM settings WHERE key='schema_version'").fetchone()
    assert int(version["value"]) == db.SCHEMA_VERSION
    db.migrate(conn)                      # a second launch changes nothing
    rows_from_db(conn, latest_run_id(conn))
    main.all_queries(conn)
    main.load_scope(conn)
    schedule.load_run_time(conn)


def test_somebody_who_finished_setup_on_1_1_0_is_not_sent_back_into_it(tmp_path):
    from app.onboarding import state

    conn = _database_from_1_1_0(tmp_path)
    assert state.is_setup_finished(conn)
    assert state.calibration_prompt(conn) is None


# -- renewals: the app asks again rather than trusting one answer for ever -----

def test_a_mac_licence_is_reconfirmed_with_apple_after_ten_minutes():
    from app.core import entitlement

    now = entitlement._now()
    cache = {"licence_key": "DL-1", "checked_at": (now - timedelta(minutes=9)).isoformat()}
    assert entitlement.fresh_apple_licence(cache, now) == "DL-1"
    cache["checked_at"] = (now - timedelta(minutes=11)).isoformat()
    assert entitlement.fresh_apple_licence(cache, now) is None
