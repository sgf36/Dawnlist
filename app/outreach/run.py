"""The outreach run: what is due today, and the drafts for it.

This joins the two halves that already existed separately — `cadence.next_step`
knows WHEN a touch is due, and `drafts.write_draft` knows how to produce one —
and it is where the safety rules actually bite, because this is the only part
of the product that produces something aimed at a real person.

What it will not do, in order of consequence:

  * **It never sends.** It writes `.eml` files. The human's click in their own
    mail client is the authorisation, and that is a structural boundary, not a
    preference.
  * **It never stacks drafts.** One per recipient per thread, revised in place.
  * **It never writes past a missing fact.** A claim the factsheet cannot
    support stays a visible `[[placeholder]]` and the draft is marked as
    needing evidence.
  * **It never contacts a barred person**, and it never opens a second thread
    at an employer already in the pipeline.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from app.core import db
from app.core.board_repo import load_board, load_touches
from app.core.cadence import Channel, NextStep, is_warm_route, next_step
from app.core.tracker import JobCategory, Opportunity
from app.outreach.compose import DraftBrief, build_drafting_request
from app.outreach.drafts import Draft, DraftSet, revise_in_place, states_the_ask
from app.outreach.voice import VoiceProfile


@dataclass
class Contact:
    id: int
    name: str
    email: str | None
    bounced: bool = False
    ever_replied: bool = False
    do_not_contact: bool = False

    @property
    def reachable_by_email(self) -> bool:
        return bool(self.email) and not self.bounced and not self.do_not_contact


@dataclass
class DueItem:
    opportunity: Opportunity
    step: NextStep
    contact: Contact | None
    #: Why this one was skipped, if it was. Never silently dropped.
    blocked: str = ""

    @property
    def actionable(self) -> bool:
        return not self.blocked and self.contact is not None


@dataclass
class OutreachReport:
    due: list[DueItem] = field(default_factory=list)
    drafts: DraftSet = field(default_factory=DraftSet)
    blocked: list[DueItem] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        out = {"due": len(self.due), "blocked": len(self.blocked)}
        out.update(self.drafts.counts)
        return out


def _contacts_for(conn: sqlite3.Connection, opportunity_id: str) -> list[Contact]:
    rows = conn.execute(
        "SELECT * FROM contacts WHERE opportunity_id = ? ORDER BY id",
        (int(opportunity_id),)).fetchall()
    return [Contact(id=r["id"], name=r["name"], email=r["email"],
                    bounced=bool(r["email_bounced"]),
                    ever_replied=bool(r["ever_replied"]),
                    do_not_contact=bool(r["do_not_contact"]))
            for r in rows]


#: The three routes to a person, best first. spec 9.6.
WARM, UNTRIED, UNRESPONSIVE = 0, 1, 2


def route_rank(contact: Contact, tried_ids) -> int:
    """Which of the three routes this contact is, best first.

    `ever_replied` on its own answers only "warm or not". It cannot tell an
    UNTRIED contact from an UNRESPONSIVE one, because both have never replied
    — and the whole point of spec 9.6 is that those two are not the same
    person. The evidence log is what separates them: an outbound touch already
    recorded against this contact means the ask has been made and ignored.

    Ranking without that evidence collapsed the two, and the collapse ran the
    wrong way round: `pick_contact` took the first contact who had never
    replied, which put a person who had ignored two letters ahead of a person
    who had written back.
    """
    if is_warm_route(contact.ever_replied):
        return WARM
    return UNRESPONSIVE if contact.id in tried_ids else UNTRIED


def pick_contact(contacts: list[Contact], *,
                 tried_ids=frozenset()) -> tuple[Contact | None, str]:
    """Choose who to write to, and say why when nobody qualifies.

    spec 9.6: a mutual connection who has never replied is not a warm route.
    Connection counts measure graph proximity, not willingness, so an untried
    contact ranks ABOVE one who ignored a previous ask — re-asking someone who
    did not answer is a cost, not a free retry. Someone who HAS answered
    outranks both: that is what a warm route is.

    `tried_ids` is the set of contacts an outbound touch has already been
    recorded against. Left empty, every non-replier reads as untried, which is
    the safe direction to be wrong in — it never demotes a contact who has not
    earned it — but a caller with the evidence log in hand should pass it.
    """
    allowed = [c for c in contacts if not c.do_not_contact]
    if not allowed:
        if contacts:
            return None, "every contact is marked do-not-contact"
        return None, "no contact on record"

    reachable = [c for c in allowed if c.reachable_by_email]
    if not reachable:
        return None, "no working email address (bounced or missing)"

    return min(reachable, key=lambda c: (route_rank(c, tried_ids), c.id)), ""


def tried_contact_ids(conn: sqlite3.Connection, opportunity_id: str) -> set[int]:
    """Contacts an outbound touch has already gone to, from the evidence log.

    Outbound only, and read rather than cached: spec 8.1 computes the cadence
    from actual evidenced touches for the same reason.
    """
    return {r["contact_id"] for r in conn.execute(
        "SELECT DISTINCT contact_id FROM touches "
        " WHERE opportunity_id = ? AND direction = 'out' "
        "   AND contact_id IS NOT NULL", (int(opportunity_id),))}


def due_today(conn: sqlite3.Connection, *, today: date | None = None) -> list[DueItem]:
    """Everything whose next touch has come due, with the blocked ones named.

    spec 8.4: an unactionable step is SURFACED as blocked, naming the missing
    field — never left sitting as due, and never pattern-guessed.
    """
    today = today or date.today()
    items: list[DueItem] = []

    for opp in load_board(conn):
        if opp.category == JobCategory.MUTUAL_POC:
            continue
        if not opp.stage.is_live:
            # On Hold and the terminal stages carry no cadence. On Hold is
            # still reply-checked elsewhere; it is not dead.
            continue

        step = next_step(load_touches(conn, opp.id), today=today)
        if step.due_on is None or step.due_on > today:
            continue

        contact, why = pick_contact(
            _contacts_for(conn, opp.id),
            tried_ids=tried_contact_ids(conn, opp.id))
        items.append(DueItem(opportunity=opp, step=step, contact=contact,
                             blocked=why))
    return items


def live_draft_path(conn: sqlite3.Connection, thread_key: str,
                    contact_id: int) -> Path | None:
    row = conn.execute(
        "SELECT path FROM drafts WHERE thread_key=? AND contact_id=? "
        "AND superseded=0", (thread_key, contact_id)).fetchone()
    return Path(row["path"]) if row else None


def thread_key_for(opp: Opportunity, contact: Contact) -> str:
    return f"{opp.company}-{contact.name}".lower().replace(" ", "-")


def prepare_drafts(conn: sqlite3.Connection, items: list[DueItem], *,
                   folder: Path, factsheet: str, voice: VoiceProfile,
                   send, locale: str = "en",
                   today: date | None = None) -> OutreachReport:
    """Draft everything actionable. Writes files; sends nothing.

    `send(request) -> text` is injected, so the caller owns transport and the
    tests spend nothing.
    """
    report = OutreachReport()
    today = today or date.today()

    # Invariant 13: an output cannot exist without the run that produced it.
    # The FK is what makes an unregistered draft impossible, and it was never
    # exercised because nothing opened a run around the writing.
    with db.run(conn) as run:
        for item in items:
            report.due.append(item)
            if not item.actionable:
                report.blocked.append(item)
                continue

            opp, contact = item.opportunity, item.contact
            touches = load_touches(conn, opp.id)
            sent = [t.occurred_on for t in touches if t.is_evidenced_outbound]
            brief = DraftBrief(
                recipient_name=contact.name,
                recipient_role="",
                company=opp.company,
                posting_title="",
                locale=locale,
                # The rung was already computed and carried this far, and was then
                # dropped on the floor — so a third approach was drafted with the
                # same instructions as the first.
                rung=item.step.rung,
                last_contacted_on=max(sent) if sent else None,
            )
            request = build_drafting_request(brief, factsheet, voice)

            try:
                body = send(request)
            except Exception as exc:  # noqa: BLE001
                item.blocked = f"drafting failed: {type(exc).__name__}: {exc}"
                report.blocked.append(item)
                continue

            if not states_the_ask(body):
                # spec 9.4. An abstract, commentary-led opening reads as a
                # consulting pitch — two real recipients read one that way. Rather
                # than silently sending a draft that misrepresents what he wants,
                # mark it as needing work.
                body = ("[[the opening must say plainly that this is an individual "
                        "exploring roles, not a vendor]]\n\n") + body

            thread = thread_key_for(opp, contact)
            draft = Draft(
                to_name=contact.name,
                to_email=contact.email,
                subject=f"{opp.company}",
                body=body,
                thread_key=thread,
            )
            previous = live_draft_path(conn, thread, contact.id)
            path = revise_in_place(draft, folder, previous)
            _record_draft(conn, opp, contact, thread, path, draft)
            run.register_output(path, kind="draft")
            report.drafts.drafts.append(draft)


        run.record_counts(swept=0)
    return report


def _record_draft(conn: sqlite3.Connection, opp: Opportunity, contact: Contact,
                  thread: str, path: Path, draft: Draft) -> None:
    """Supersede the old row before inserting the new one.

    The unique index allows exactly one live draft per (thread, contact), so
    this order matters: superseding first is what makes a revision a revision
    rather than a constraint violation.
    """
    conn.execute(
        "UPDATE drafts SET superseded=1 WHERE thread_key=? AND contact_id=?",
        (thread, contact.id))
    conn.execute(
        """INSERT INTO drafts(opportunity_id, contact_id, thread_key, path,
               has_placeholder, superseded, created_at)
           VALUES(?,?,?,?,?,0,?)""",
        (int(opp.id), contact.id, thread, str(path),
         int(not draft.send_ready),
         datetime.now(timezone.utc).isoformat(timespec="seconds")))
    conn.commit()
