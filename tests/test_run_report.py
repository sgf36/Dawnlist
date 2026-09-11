"""One door into a run, and a report the window can put into words."""
import threading

import pytest

from app.core import db
from app.core.api_key import KeyProblem
from app.core.entitlement import NotEntitled
from app.core.run_report import (COMPLETE, ERROR, FETCH_FAILED, KEY, LIMIT,
                                 NOT_CONFIGURED, NOT_ENTITLED, PARTIAL,
                                 report_from_error, report_from_outcome)
from app.feed.base import FetchResult
from app.feed.managed import ManagedProvider
from app.main import NotConfigured, main, run_daily_search_on_worker
from tests.test_main import Stub, job, ok, seed, strong_send


class PerQuery(Stub):
    """A different answer for each saved search, by label."""

    def __init__(self, results):
        super().__init__(None)
        self.results = results

    def search(self, query):
        return self.results[query.label]


def add_query(conn, label):
    import json
    conn.execute("INSERT INTO queries(label, params_json, created_at) "
                 "VALUES(?,?,'x')",
                 (label, json.dumps({"titles": [label], "countries": ["GB"]})))
    conn.commit()


def refused(message="Daily refresh cap reached (3)"):
    return FetchResult(jobs=[], error=message, refusal="refresh_cap")


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.sqlite3")
    db.migrate(c)
    yield c
    c.close()


def outcome_of(conn, provider):
    from app.main import morning_run
    return morning_run(conn, provider=provider, send=strong_send)


# -- the Worker's refusal travels as a code, not only as prose --------------

def test_the_worker_refusal_code_is_kept(monkeypatch):
    prov = ManagedProvider("DAWN-TEST-KEY", base="https://example.invalid")
    monkeypatch.setattr(prov, "_call", lambda *a, **k: (429, {
        "error": "refresh_cap", "message": "Daily refresh cap reached (3)"}))
    from app.feed.base import SearchQuery
    assert prov.search(SearchQuery(label="q")).refusal == "refresh_cap"


# -- outcomes ---------------------------------------------------------------

def test_a_clean_run_is_complete(conn):
    seed(conn)
    assert report_from_outcome(outcome_of(conn, Stub(ok([job("a")])))).kind == COMPLETE


def test_the_refresh_limit_is_named_with_its_number(conn):
    seed(conn)
    report = report_from_outcome(outcome_of(conn, Stub(refused(
        "Daily refresh cap reached (6)"))))
    assert report.kind == LIMIT
    assert report.refreshes_per_day == 6


def test_the_limit_is_named_even_when_an_earlier_search_got_through(conn):
    """The fourth search on a three-refresh plan is refused while the first
    three succeed. That is still the limit, not an ordinary partial run."""
    seed(conn)
    add_query(conn, "zz later search")
    report = report_from_outcome(outcome_of(conn, PerQuery({
        "strategy": ok([job("a")]), "zz later search": refused()})))
    assert report.kind == LIMIT
    assert report.refreshes_per_day == 3


def test_an_unstated_limit_falls_back_to_the_workers_default(conn):
    seed(conn)
    report = report_from_outcome(outcome_of(conn, Stub(refused("Daily limit reached"))))
    assert (report.kind, report.refreshes_per_day) == (LIMIT, 3)


def test_a_different_fetch_failure_is_not_called_the_limit(conn):
    """Positive control for the one above: an outage is not a spent allowance,
    and telling someone to wait until midnight UTC for an outage is wrong."""
    seed(conn)
    report = report_from_outcome(outcome_of(conn, Stub(FetchResult(
        jobs=[], error="Could not reach the Dawnlist feed service."))))
    assert report.kind == FETCH_FAILED
    assert "Could not reach" in report.detail


def test_a_partial_fetch_is_partial_and_says_why(conn):
    seed(conn)
    add_query(conn, "zz later search")
    report = report_from_outcome(outcome_of(conn, PerQuery({
        "strategy": ok([job("a")]),
        "zz later search": FetchResult(jobs=[], error="provider timed out")})))
    assert report.kind == PARTIAL
    assert "provider timed out" in report.detail


# -- refusals ---------------------------------------------------------------

@pytest.mark.parametrize("exc, kind", [
    (KeyProblem("no key"), KEY),
    (NotEntitled("no licence"), NOT_ENTITLED),
    (NotConfigured("no brief"), NOT_CONFIGURED),
    (ValueError("boom"), ERROR),
])
def test_each_refusal_is_its_own_kind(exc, kind):
    report = report_from_error(exc)
    assert report.kind == kind
    assert str(exc) in report.detail


# -- the worker-thread door -------------------------------------------------

def test_the_worker_opens_its_own_connection(tmp_path, monkeypatch):
    """sqlite3 refuses a connection used from a thread that did not open it,
    which is exactly what handing the window's connection to a worker does."""
    import app.main as main_mod

    path = tmp_path / "t.sqlite3"
    ui_conn = db.connect(path)
    db.migrate(ui_conn)
    seed(ui_conn)
    monkeypatch.setattr(main_mod, "build_provider", lambda c: Stub(ok([job("a")])))
    monkeypatch.setattr(main_mod, "build_send", lambda c: strong_send)

    results = {}

    def on_worker(fn, key):
        try:
            results[key] = fn()
        except Exception as exc:  # noqa: BLE001
            results[key] = exc

    # Positive control: the window's own connection fails on a worker.
    t = threading.Thread(target=on_worker, args=(
        lambda: main_mod.run_daily_search(ui_conn), "shared"))
    t.start()
    t.join()
    assert "thread" in str(results["shared"]).lower()

    # Read on the UI thread, as the window does: only the path crosses over.
    path_text = main_mod.database_path(ui_conn)
    t = threading.Thread(target=on_worker, args=(
        lambda: run_daily_search_on_worker(path_text), "own"))
    t.start()
    t.join()
    assert getattr(results["own"], "kind", results["own"]) == COMPLETE
    assert ui_conn.execute("SELECT COUNT(*) FROM runs WHERE kind='sweep'"
                           ).fetchone()[0] == 1
    ui_conn.close()


# -- the command line uses the same door ------------------------------------

def _cli(tmp_path, monkeypatch, provider, capsys):
    import app.main as main_mod

    path = tmp_path / "cli.sqlite3"
    c = db.connect(path)
    db.migrate(c)
    seed(c)
    c.close()
    monkeypatch.setattr(main_mod, "build_provider", lambda conn: provider)
    monkeypatch.setattr(main_mod, "build_send", lambda conn: strong_send)
    code = main(["--run-once", "--db", str(path)])
    return code, capsys.readouterr().out


def test_run_once_names_the_daily_limit(tmp_path, monkeypatch, capsys):
    code, out = _cli(tmp_path, monkeypatch, Stub(refused()), capsys)
    assert code == 1
    assert "DAILY LIMIT" in out and "3 refreshes per UTC day" in out


def test_run_once_says_nothing_about_a_limit_it_did_not_hit(tmp_path, monkeypatch, capsys):
    code, out = _cli(tmp_path, monkeypatch, Stub(ok([job("a")])), capsys)
    assert code == 0
    assert "DAILY LIMIT" not in out
