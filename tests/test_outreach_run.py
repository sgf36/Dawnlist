"""The outreach run: what is due, who to write to, and the draft."""
from datetime import date
from pathlib import Path

import pytest

from app.core import db
from app.core.board_repo import create_opportunity, record_bounce, record_outbound
from app.core.cadence import Channel
from app.core.tracker import JobCategory, Stage
from app.outreach.run import (due_today, pick_contact, prepare_drafts,
                              thread_key_for, Contact)
from app.outreach.voice import build_profile

TUE = date(2026, 9, 8)
LATER = date(2026, 9, 15)

GOOD_BODY = ("I am an individual exploring roles in hospitality, and I am not "
             "selling anything. Could we speak briefly?")
VOICE = build_profile([])
FACTS = "Spencer Fields. Cornell SHA 2019."


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.sqlite3")
    db.migrate(c)
    yield c
    c.close()


def add_contact(conn, oid, name="Jo", email="jo@example.com", **kw):
    cols = "opportunity_id, name, email, created_at"
    vals = [oid, name, email, "x"]
    for k, v in kw.items():
        cols += f", {k}"
        vals.append(v)
    marks = ",".join("?" * len(vals))
    return conn.execute(
        f"INSERT INTO contacts({cols}) VALUES({marks})", vals).lastrowid


def sends(body=GOOD_BODY):
    return lambda _request: body


# -- who is due -------------------------------------------------------------
def test_a_fresh_opportunity_is_due_now(conn):
    oid = create_opportunity(conn, "Acme")
    add_contact(conn, oid)
    items = due_today(conn, today=TUE)
    assert len(items) == 1 and items[0].actionable


def test_an_opportunity_not_yet_due_is_not_listed(conn):
    oid = create_opportunity(conn, "Acme")
    add_contact(conn, oid)
    record_outbound(conn, str(oid), Channel.EMAIL, TUE)
    assert due_today(conn, today=date(2026, 9, 10)) == []


def test_on_hold_and_terminal_stages_are_never_due(conn):
    for stage in (Stage.ON_HOLD, Stage.WON, Stage.LOST):
        oid = create_opportunity(conn, f"Co{stage.value}", stage=stage)
        add_contact(conn, oid)
    assert due_today(conn, today=TUE) == []


def test_mutual_poc_records_are_not_chased(conn):
    oid = create_opportunity(conn, "Intro", category=JobCategory.MUTUAL_POC)
    add_contact(conn, oid)
    assert due_today(conn, today=TUE) == []


# -- who to write to --------------------------------------------------------
def test_an_untried_contact_outranks_an_unresponsive_one():
    """Connection counts measure graph proximity, not willingness."""
    unresponsive = Contact(1, "Old", "old@x.com", ever_replied=False)
    replied = Contact(2, "Replied", "r@x.com", ever_replied=True)
    untried = Contact(3, "New", "new@x.com", ever_replied=False)
    picked, why = pick_contact([replied, untried])
    assert picked.name == "New" and why == ""


def test_a_barred_contact_is_never_chosen():
    picked, why = pick_contact([Contact(1, "Nick", "n@x.com", do_not_contact=True)])
    assert picked is None and "do-not-contact" in why


def test_a_bounced_address_is_not_reachable():
    picked, why = pick_contact([Contact(1, "Jo", None, bounced=True)])
    assert picked is None and "no working email" in why


def test_no_contact_is_surfaced_as_blocked_not_left_due(conn):
    """spec 8.4: an unactionable step names the missing field."""
    create_opportunity(conn, "Acme")
    items = due_today(conn, today=TUE)
    assert len(items) == 1
    assert not items[0].actionable
    assert items[0].blocked == "no contact on record"


# -- drafting ---------------------------------------------------------------
def test_a_draft_is_written_and_recorded(conn, tmp_path):
    oid = create_opportunity(conn, "Acme")
    add_contact(conn, oid)
    report = prepare_drafts(conn, due_today(conn, today=TUE), folder=tmp_path,
                            factsheet=FACTS, voice=VOICE, send=sends())
    assert report.counts["drafted"] == 1
    assert list(tmp_path.glob("*.eml"))
    row = conn.execute("SELECT thread_key, superseded FROM drafts").fetchone()
    assert row["superseded"] == 0


def test_blocked_items_produce_no_draft_but_are_reported(conn, tmp_path):
    create_opportunity(conn, "Acme")          # no contact
    report = prepare_drafts(conn, due_today(conn, today=TUE), folder=tmp_path,
                            factsheet=FACTS, voice=VOICE, send=sends())
    assert report.counts["drafted"] == 0
    assert len(report.blocked) == 1
    assert list(tmp_path.glob("*.eml")) == []


def test_a_second_run_revises_rather_than_stacks(conn, tmp_path):
    """spec 9.3: one draft per recipient per thread, ever."""
    oid = create_opportunity(conn, "Acme")
    add_contact(conn, oid)
    prepare_drafts(conn, due_today(conn, today=TUE), folder=tmp_path,
                   factsheet=FACTS, voice=VOICE, send=sends())
    prepare_drafts(conn, due_today(conn, today=TUE), folder=tmp_path,
                   factsheet=FACTS, voice=VOICE,
                   send=sends(GOOD_BODY + " Revised."))

    assert len(list(tmp_path.glob("*.eml"))) == 1, "never two files for one thread"
    live = conn.execute(
        "SELECT count(*) c FROM drafts WHERE superseded=0").fetchone()
    assert live["c"] == 1
    assert "Revised." in list(tmp_path.glob("*.eml"))[0].read_text(encoding="utf-8")


def test_a_draft_that_does_not_state_the_ask_is_marked_for_work(conn, tmp_path):
    """The real failure: two recipients read a commentary-led opening as a
    consulting pitch."""
    oid = create_opportunity(conn, "Acme")
    add_contact(conn, oid)
    vague = ("Your recent expansion caught my eye. I have been thinking about "
             "how operators approach asset strategy. I'd value your perspective.")
    report = prepare_drafts(conn, due_today(conn, today=TUE), folder=tmp_path,
                            factsheet=FACTS, voice=VOICE, send=sends(vague))
    draft = report.drafts.drafts[0]
    assert not draft.send_ready
    assert any("individual exploring roles" in p for p in draft.placeholders)
    assert draft.path.name.endswith(".NEEDS-EVIDENCE.eml")


def test_a_placeholder_from_the_model_blocks_the_draft(conn, tmp_path):
    oid = create_opportunity(conn, "Acme")
    add_contact(conn, oid)
    body = GOOD_BODY + " I delivered [[figure]] at [[employer]]."
    report = prepare_drafts(conn, due_today(conn, today=TUE), folder=tmp_path,
                            factsheet=FACTS, voice=VOICE, send=sends(body))
    assert report.counts["needs_evidence"] == 1
    row = conn.execute("SELECT has_placeholder FROM drafts").fetchone()
    assert row["has_placeholder"] == 1


def test_a_drafting_failure_blocks_rather_than_writes_a_bad_draft(conn, tmp_path):
    oid = create_opportunity(conn, "Acme")
    add_contact(conn, oid)

    def boom(_r):
        raise RuntimeError("upstream 503")

    report = prepare_drafts(conn, due_today(conn, today=TUE), folder=tmp_path,
                            factsheet=FACTS, voice=VOICE, send=boom)
    assert report.counts["drafted"] == 0
    assert "upstream 503" in report.blocked[0].blocked
    assert list(tmp_path.glob("*.eml")) == []


def test_a_bounce_pivots_off_email_and_blocks_the_email_draft(conn, tmp_path):
    oid = create_opportunity(conn, "Acme")
    cid = add_contact(conn, oid)
    record_bounce(conn, str(oid), cid, TUE)
    items = due_today(conn, today=TUE)
    assert len(items) == 1
    assert not items[0].actionable, "the address is dead; email is not the channel"
    assert Channel.EMAIL not in items[0].step.channels


# -- a follow-up must know it is one -----------------------------------------
def test_a_first_contact_says_so_in_the_request():
    from app.outreach.compose import DraftBrief, build_drafting_request
    req = build_drafting_request(
        DraftBrief(recipient_name="Jo", recipient_role="", company="Acme",
                   posting_title="Asset Manager"), FACTS, VOICE)
    assert "FIRST message" in req["messages"][0]["content"]


def test_a_follow_up_is_told_not_to_reintroduce():
    """Without this every follow-up was drafted as a cold approach, so a real
    recipient got "I am an individual exploring roles" a second and third
    time — which reads as though the sender forgot writing."""
    from app.outreach.compose import DraftBrief, build_drafting_request
    req = build_drafting_request(
        DraftBrief(recipient_name="Jo", recipient_role="", company="Acme",
                   posting_title="Asset Manager", rung=1,
                   last_contacted_on=TUE), FACTS, VOICE)
    content = req["messages"][0]["content"]
    assert "FOLLOW-UP" in content
    assert "Do NOT reintroduce" in content
    assert TUE.isoformat() in content, "the date lets it cite the real message"


def test_the_rung_reaches_the_draft(conn):
    """The rung was computed, carried as far as `prepare_drafts`, and then
    dropped — so a third approach used the same instructions as the first."""
    from app.core.board_repo import record_outbound
    seen = {}

    def capture(request):
        seen["content"] = request["messages"][0]["content"]
        return GOOD_BODY

    oid = create_opportunity(conn, "Acme")
    add_contact(conn, oid)
    record_outbound(conn, str(oid), Channel.EMAIL, TUE)

    items = due_today(conn, today=LATER)
    assert items, "nothing due, so nothing to check"
    prepare_drafts(conn, items, folder=Path("."), factsheet=FACTS,
                   voice=VOICE, send=capture, today=LATER)
    assert "FOLLOW-UP" in seen["content"]
    assert TUE.isoformat() in seen["content"]
