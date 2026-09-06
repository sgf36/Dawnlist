"""Draft output: .eml only, placeholders block, one draft per thread."""
import email
import email.policy

import pytest

from app.outreach.drafts import (Draft, DraftSet, NotSendReady, build_eml,
                                 mailto_url, revise_in_place, states_the_ask,
                                 write_draft)


def draft(body="I am an individual exploring roles in hospitality. "
               "I am not selling anything. Could we speak?", **kw):
    base = dict(to_name="Jo Bloggs", to_email="jo@example.com",
                subject="Exploring roles", body=body, thread_key="acme-jo")
    base.update(kw)
    return Draft(**base)


# -- invariant 2: placeholders block ----------------------------------------
def test_a_placeholder_blocks_send_ready_export():
    d = draft(body="I led a team of [[headcount]] at [[employer]].")
    assert not d.send_ready
    assert set(d.placeholders) == {"headcount", "employer"}
    with pytest.raises(NotSendReady, match="headcount"):
        d.assert_send_ready()


def test_a_placeholder_in_the_subject_also_blocks():
    assert not draft(subject="Role at [[employer]]").send_ready


def test_a_fully_evidenced_draft_is_send_ready():
    assert draft().send_ready


def test_a_blocked_draft_is_named_so_it_cannot_be_mistaken(tmp_path):
    p = write_draft(draft(body="I identified [[figure]] in savings."), tmp_path)
    assert p.name.endswith(".NEEDS-EVIDENCE.eml")


def test_writing_a_blocked_draft_can_be_refused_outright(tmp_path):
    with pytest.raises(NotSendReady):
        write_draft(draft(body="[[gap]]"), tmp_path, allow_placeholders=False)


# -- spec 9.4: state the ask ------------------------------------------------
def test_a_plain_ask_is_recognised():
    assert states_the_ask("I am an individual exploring roles. Not selling.")


def test_a_consulting_flavoured_opening_is_not_an_ask():
    """Two recipients read exactly this shape as a vendor pitch."""
    body = ("Your recent expansion caught my eye. I have been thinking about "
            "how operators approach asset strategy. I'd value your perspective.")
    assert not states_the_ask(body)


def test_the_ask_must_be_early():
    late = ("Filler one. Filler two. Filler three. Filler four. "
            "I am an individual exploring roles.")
    assert not states_the_ask(late)


# -- RFC 5322 output --------------------------------------------------------
def test_the_eml_parses_and_carries_the_headers(tmp_path):
    p = write_draft(draft(from_name="Spencer Fields",
                          from_email="s@example.com"), tmp_path)
    msg = email.message_from_bytes(p.read_bytes(),
                                   policy=email.policy.default)
    assert msg["To"] == "Jo Bloggs <jo@example.com>"
    assert msg["From"] == "Spencer Fields <s@example.com>"
    assert msg["Subject"] == "Exploring roles"
    assert msg["X-Unsent"] == "1", "clients must open this as an unsent draft"
    assert "individual exploring roles" in msg.get_content()


def test_a_reply_threads_correctly():
    msg = build_eml(draft(in_reply_to="<abc@mail>", references=("<a@m>", "<abc@mail>")))
    assert msg["In-Reply-To"] == "<abc@mail>"
    assert "<a@m>" in msg["References"]


def test_mailto_is_escaped():
    url = mailto_url(draft(subject="Roles & strategy"))
    assert url.startswith("mailto:jo%40example.com?") and "%26" in url


# -- spec 9.3: revise in place, never stack ---------------------------------
def test_revising_replaces_rather_than_stacks(tmp_path):
    first = write_draft(draft(body="First version. I am an individual exploring roles."),
                        tmp_path)
    second = revise_in_place(
        draft(body="Second version. I am an individual exploring roles."),
        tmp_path, previous=first)
    assert list(tmp_path.glob("*.eml")) == [second]
    assert "Second version" in second.read_text(encoding="utf-8")


def test_a_thread_that_becomes_blocked_does_not_leave_the_old_file(tmp_path):
    first = write_draft(draft(), tmp_path)
    second = revise_in_place(draft(body="Now with [[gap]]."), tmp_path, previous=first)
    assert not first.exists()
    assert second.name.endswith(".NEEDS-EVIDENCE.eml")


# -- reporting --------------------------------------------------------------
def test_blocked_drafts_are_counted_and_named():
    s = DraftSet([draft(), draft(body="[[gap]]", thread_key="b")])
    assert s.counts == {"drafted": 2, "send_ready": 1, "needs_evidence": 1}
    assert s.blocked[0].thread_key == "b"
