"""The entry points, because a tested function with no caller is a missing
capability.

The wiring audit found four modules in this feature set that were fully tested
and reachable from nothing. These tests cover the callers themselves — reading
a pasted advert into the tracker, and producing an application pack for a
posting that is in it.

No network anywhere: `send` is injected, exactly as it is for assessment and
outreach.
"""
import json

import pytest

from app.core import db
from app.main import (NotConfigured, add_posting, apply_run, load_cv_text)

BODY = ("We are seeking a commercially minded analyst. You will own the "
        "annual budget and support the feasibility pipeline across the "
        "estate. Requirements: experience in feasibility studies; experience "
        "in revenue management; a degree in a numerate discipline. This is a "
        "hybrid role based in London with occasional travel to sites.")
ADVERT = f"Senior Analyst at Example Group\nLocation: London\n\n{BODY}\n"


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.sqlite3")
    db.migrate(c)
    c.execute("INSERT INTO documents(kind, version, body, created_at)"
              " VALUES('factsheet', 1, 'Supported feasibility studies.', 'x')")
    c.commit()
    yield c
    c.close()


@pytest.fixture()
def advert(tmp_path):
    path = tmp_path / "advert.txt"
    path.write_text(ADVERT, encoding="utf-8")
    return path


@pytest.fixture()
def cvfolder(tmp_path):
    folder = tmp_path / "cv"
    folder.mkdir()
    # Over 200 characters on purpose: `CVDocument.is_usable` treats anything
    # shorter as a file that did not open properly, which is the right rule
    # for a scanned PDF and a trap for a test fixture.
    (folder / "cv.txt").write_text(
        "Example Group — Senior Associate, 2021-2023. Feasibility studies "
        "across a portfolio of assets, supporting the investment committee "
        "with analysis of trading performance, capital planning and the "
        "annual budget cycle. Earlier roles in operations across branded "
        "hotels in London and the South West.",
        encoding="utf-8")
    return folder


# ---------------------------------------------------------------------------
# Bringing a posting in
# ---------------------------------------------------------------------------

def test_a_pasted_advert_lands_in_the_tracker(conn, advert):
    parsed = add_posting(conn, advert, url="https://x.test/1")
    row = conn.execute("SELECT * FROM jobs").fetchone()
    assert row["title"] == "Senior Analyst"
    assert row["company"] == "Example Group"
    assert parsed.is_usable


def test_a_hand_entered_posting_is_not_marked_screened_out(conn, advert):
    """It was never screened. The screen exists to thin a sweep nobody asked
    for, and this one the person chose deliberately."""
    add_posting(conn, advert)
    row = conn.execute("SELECT funnel_status, screen_verdict FROM jobs").fetchone()
    assert row["funnel_status"] == "likely"
    assert row["screen_verdict"] is None


def test_adding_the_same_advert_twice_updates_rather_than_duplicates(conn, advert):
    add_posting(conn, advert, url="https://x.test/1")
    add_posting(conn, advert, url="https://x.test/1")
    assert conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"] == 1


def test_a_headline_alone_is_refused_with_a_usable_message(conn, tmp_path):
    path = tmp_path / "thin.txt"
    path.write_text("Senior Analyst at Example Group\n", encoding="utf-8")
    with pytest.raises(NotConfigured) as exc:
        add_posting(conn, path)
    assert "whole advert" in str(exc.value)


# ---------------------------------------------------------------------------
# The CV corpus
# ---------------------------------------------------------------------------

def test_the_cv_is_read_from_files_the_person_put_there(cvfolder):
    text = load_cv_text(cvfolder)
    assert "Senior Associate" in text
    assert "cv.txt" in text, "the source file is named, so a wrong one is visible"


def test_a_missing_cv_folder_is_empty_not_an_error(tmp_path):
    assert load_cv_text(tmp_path / "nothing") == ""


# ---------------------------------------------------------------------------
# The application pack
# ---------------------------------------------------------------------------

def test_apply_refuses_without_a_cv_rather_than_inventing_one(
        conn, advert, tmp_path, monkeypatch):
    """A CV written from a factsheet alone is fluent and describes a career
    nobody had."""
    monkeypatch.setattr("app.main.cv_dir", lambda: tmp_path / "none")
    monkeypatch.setattr("app.core.entitlement.require", lambda c: None)
    add_posting(conn, advert)
    with pytest.raises(NotConfigured) as exc:
        apply_run(conn, "pasted:" + _job_id(conn), send=lambda r: "x")
    assert "reorders your own document" in str(exc.value)


def test_apply_refuses_without_a_factsheet(conn, advert, cvfolder, monkeypatch):
    monkeypatch.setattr("app.main.cv_dir", lambda: cvfolder)
    conn.execute("DELETE FROM documents WHERE kind='factsheet'")
    conn.commit()
    add_posting(conn, advert)
    with pytest.raises(NotConfigured) as exc:
        apply_run(conn, "pasted:" + _job_id(conn), send=lambda r: "x")
    assert "factsheet" in str(exc.value)


def test_an_unknown_posting_is_named(conn, cvfolder, monkeypatch):
    monkeypatch.setattr("app.main.cv_dir", lambda: cvfolder)
    monkeypatch.setattr("app.core.entitlement.require", lambda c: None)
    with pytest.raises(NotConfigured) as exc:
        apply_run(conn, "pasted:nope", send=lambda r: "x")
    assert "nope" in str(exc.value)


def test_a_pack_is_produced_and_written(conn, advert, cvfolder, tmp_path,
                                        monkeypatch):
    monkeypatch.setattr("app.main.cv_dir", lambda: cvfolder)
    monkeypatch.setattr("app.core.entitlement.require", lambda c: None)
    add_posting(conn, advert)
    out = tmp_path / "applications"

    sent = []

    def send(request):
        sent.append(request)
        return "Example Group — Senior Associate. Feasibility studies."

    pack = apply_run(conn, "pasted:" + _job_id(conn), send=send, folder=out)

    assert len(sent) == 2, "a CV and a letter, and no brief unless asked"
    assert pack.complete
    assert sorted(p.name.split("-")[-1] for p in out.iterdir()) == [
        "cv.md", "letter.md"]


def test_the_gap_analysis_reaches_the_documents(conn, advert, cvfolder,
                                                tmp_path, monkeypatch):
    monkeypatch.setattr("app.main.cv_dir", lambda: cvfolder)
    monkeypatch.setattr("app.core.entitlement.require", lambda c: None)
    add_posting(conn, advert)

    sent = []
    pack = apply_run(conn, "pasted:" + _job_id(conn),
                     send=lambda r: sent.append(r) or "body",
                     folder=tmp_path)

    user = sent[0]["messages"][0]["content"]
    assert "revenue management" in user.lower(), (
        "the requirement the evidence cannot support must be named as "
        "forbidden, or the CV reaches for 'exposure to'")
    assert "feasibility studies" in [r.phrase.casefold()
                                     for r in pack.gaps.covered]


def _job_id(conn) -> str:
    return conn.execute(
        "SELECT provider_job_id FROM jobs LIMIT 1").fetchone()[0]
