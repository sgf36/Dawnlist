"""In-app review prompt: when to ask and when to stay quiet."""
from datetime import date, timedelta

import pytest

from app.core import db
from app.core import review_prompt as rp


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.sqlite3")
    db.migrate(c)
    yield c
    c.close()


def _insert_completed_sweeps(conn, n):
    for _ in range(n):
        conn.execute(
            "INSERT INTO runs (started_at, finished_at, status, kind) "
            "VALUES ('2026-09-01T07:00:00Z', '2026-09-01T07:05:00Z', "
            "'complete', 'sweep')")
    conn.commit()


def _insert_opportunity(conn):
    conn.execute(
        "INSERT INTO opportunities (company, stage, category, created_at) "
        "VALUES ('Acme', 0, 'opportunity', '2026-09-01T07:00:00Z')")
    conn.commit()


class TestShouldAsk:
    def test_refuses_on_wrong_variant(self, conn, monkeypatch):
        monkeypatch.setattr("app.core.review_prompt.variant", lambda: "direct")
        _insert_completed_sweeps(conn, 5)
        _insert_opportunity(conn)
        assert not rp.should_ask(conn)

    def test_refuses_when_too_few_sweeps(self, conn, monkeypatch):
        monkeypatch.setattr("app.core.review_prompt.variant", lambda: "mas")
        _insert_completed_sweeps(conn, 2)
        _insert_opportunity(conn)
        assert not rp.should_ask(conn)

    def test_refuses_when_no_opportunity(self, conn, monkeypatch):
        monkeypatch.setattr("app.core.review_prompt.variant", lambda: "mas")
        _insert_completed_sweeps(conn, 5)
        assert not rp.should_ask(conn)

    def test_asks_when_criteria_met_mas(self, conn, monkeypatch):
        monkeypatch.setattr("app.core.review_prompt.variant", lambda: "mas")
        _insert_completed_sweeps(conn, 3)
        _insert_opportunity(conn)
        assert rp.should_ask(conn)

    def test_asks_when_criteria_met_store_iap(self, conn, monkeypatch):
        monkeypatch.setattr("app.core.review_prompt.variant", lambda: "store_iap")
        _insert_completed_sweeps(conn, 5)
        _insert_opportunity(conn)
        assert rp.should_ask(conn)

    def test_refuses_after_recently_asked(self, conn, monkeypatch):
        monkeypatch.setattr("app.core.review_prompt.variant", lambda: "mas")
        _insert_completed_sweeps(conn, 5)
        _insert_opportunity(conn)
        today = date(2026, 9, 26)
        rp.record_asked(conn, today=today)
        assert not rp.should_ask(conn, today=today)

    def test_asks_again_after_cooldown(self, conn, monkeypatch):
        monkeypatch.setattr("app.core.review_prompt.variant", lambda: "mas")
        _insert_completed_sweeps(conn, 5)
        _insert_opportunity(conn)
        asked = date(2025, 9, 1)
        rp.record_asked(conn, today=asked)
        today = asked + timedelta(days=366)
        assert rp.should_ask(conn, today=today)


class TestRequest:
    def test_noop_when_criteria_not_met(self, conn, monkeypatch):
        monkeypatch.setattr("app.core.review_prompt.variant", lambda: "direct")
        rp.request(conn)
        assert rp._get(conn, rp._SETTINGS_KEY) is None

    def test_records_on_mas(self, conn, monkeypatch):
        monkeypatch.setattr("app.core.review_prompt.variant", lambda: "mas")
        monkeypatch.setattr(rp, "_request_mac", lambda: True)
        _insert_completed_sweeps(conn, 3)
        _insert_opportunity(conn)
        rp.request(conn)
        assert rp._get(conn, rp._SETTINGS_KEY) is not None

    def test_records_on_store_iap(self, conn, monkeypatch):
        monkeypatch.setattr("app.core.review_prompt.variant", lambda: "store_iap")
        monkeypatch.setattr(rp, "_request_windows", lambda hwnd=None: True)
        _insert_completed_sweeps(conn, 3)
        _insert_opportunity(conn)
        rp.request(conn)
        assert rp._get(conn, rp._SETTINGS_KEY) is not None

    def test_does_not_record_when_platform_fails(self, conn, monkeypatch):
        monkeypatch.setattr("app.core.review_prompt.variant", lambda: "mas")
        monkeypatch.setattr(rp, "_request_mac", lambda: False)
        _insert_completed_sweeps(conn, 3)
        _insert_opportunity(conn)
        rp.request(conn)
        assert rp._get(conn, rp._SETTINGS_KEY) is None
