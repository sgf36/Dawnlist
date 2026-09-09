"""The board — opportunities, stages and action tasks, in-app.

This replaces ClickUp entirely (handoff Part 0.1: the tracking board is in-app
SQLite; Part 1 excludes board integrations because *the app is the board*).

The Stage ladder and every rule below is ported from the production ClickUp
system rather than invented, because each one was paid for by a real defect.
The numbering is deliberately ClickUp's 0-based orderindex, so an export from
the existing board maps across without a translation table.

The governing idea, and the reason this file exists at all:

    STAGE IS THE TRUTH. The visible status is a DERIVED MIRROR of it.
    When they disagree, correct the mirror, never the truth.

That is spec 8.3. The one thing that outranks Stage is physical evidence — a
bounce (spec 8.2) — and that case is handled explicitly rather than by letting
callers edit Stage freely.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import IntEnum


class Stage(IntEnum):
    """The opportunity ladder. Values are ClickUp's 0-based orderindex."""
    IDENTIFIED = 0
    CONTACTED = 1
    IN_DIALOGUE = 2
    PHONE_INTERVIEW = 3
    IN_PERSON_INTERVIEW = 4
    OFFER = 5
    WON = 6
    LOST = 7
    ON_HOLD = 8          # a deliberate pause. NOT closed, and not Lost.

    @property
    def is_live(self) -> bool:
        """Carries an active cadence. On Hold is excluded; so are the two
        terminal stages."""
        return self <= Stage.OFFER

    @property
    def is_terminal(self) -> bool:
        return self in (Stage.WON, Stage.LOST)

    @property
    def is_paused(self) -> bool:
        """Paused is not dead. Skip the cadence, but still check for a reply."""
        return self is Stage.ON_HOLD

    @property
    def label(self) -> str:
        return {
            Stage.IDENTIFIED: "Identified",
            Stage.CONTACTED: "Contacted",
            Stage.IN_DIALOGUE: "In Dialogue",
            Stage.PHONE_INTERVIEW: "Phone Interview",
            Stage.IN_PERSON_INTERVIEW: "In-Person Interview",
            Stage.OFFER: "Offer",
            Stage.WON: "Won",
            Stage.LOST: "Lost",
            Stage.ON_HOLD: "On Hold",
        }[self]


# The derived mirror. Parent status mirrors Stage across the WHOLE ladder —
# the old "live Stages are exempt" carve-out was withdrawn, because it is
# exactly what let every parent sit at `Open` forever regardless of what had
# happened beneath it.
#
# Contacted and In Dialogue deliberately SHARE `waiting`. The distinction is
# not lost: Stage still holds it authoritatively. The status answers a coarser
# question — "does this need me now, or am I waiting on them?" — and both are
# "waiting on them".
STATUS_MIRROR: dict[Stage, str] = {
    Stage.IDENTIFIED: "open",
    Stage.CONTACTED: "waiting",
    Stage.IN_DIALOGUE: "waiting",
    Stage.PHONE_INTERVIEW: "phone interview",
    Stage.IN_PERSON_INTERVIEW: "in person interview",
    Stage.OFFER: "received offer",
    Stage.WON: "offer accepted",
    Stage.LOST: "no offer",
    Stage.ON_HOLD: "on hold",
}

#: Label back to Stage. `apply_determination` returns its writes as
#: `"stage=In Dialogue"` strings so a caller can show the user exactly what is
#: about to happen; something then has to read them back, and parsing a label
#: by hand at the call site is how a typo becomes a silent no-op.
STAGE_BY_LABEL: dict[str, "Stage"] = {}   # filled below, after Stage.label


#: Status values an ACTION TASK (a child) may carry. A child's status is
#: evidence-based and is never a mirror of the parent's Stage.
CHILD_OPEN_STATUSES = {"open", "waiting"}
CHILD_TERMINAL_STATUSES = {"complete", "completed", "closed", "no offer"}


STAGE_BY_LABEL.update({stage.label: stage for stage in Stage})


class TrackerError(ValueError):
    pass


class JobCategory(str):
    """`Job Category` is the only classifier. A record's NAME is never evidence
    of what it is, in either direction."""
    OPPORTUNITY = "opportunity"
    MUTUAL_POC = "mutual-poc"


# ---------------------------------------------------------------------------
# What an opportunity IS
# ---------------------------------------------------------------------------

@dataclass
class Task:
    """An action task — a child. It does NOT carry a Stage."""
    id: str
    title: str
    status: str = "open"
    parent_id: str | None = None
    due_on: date | None = None
    closed_evidence: str | None = None

    @property
    def is_open(self) -> bool:
        return (self.status or "").lower() in CHILD_OPEN_STATUSES


@dataclass
class Opportunity:
    """A record that CARRIES A STAGE VALUE.

    That is the definition — not "a record with no parent". A 560-task sweep
    established that the tree is deeper than two levels, and a has-no-parent
    test silently mis-classified real opportunities nested two levels down and
    skipped them in every audit. The confirmed case was an entire opportunity
    with eight live children hanging two levels beneath its grandparent.
    """
    id: str
    company: str
    stage: Stage
    status: str = ""
    parent_id: str | None = None
    category: str = JobCategory.OPPORTUNITY
    tasks: list[Task] = field(default_factory=list)
    email_bounced: bool = False
    closed_at: date | None = None

    # --- the posting this opportunity came from -----------------------------
    # An opportunity is keyed on the EMPLOYER (one live opportunity per
    # company, spec 9.5), but a job search is about roles, and a tracker that
    # cannot show which role is being pursued is missing the thing the user
    # came for. These are read from the linked `jobs` row via `job_id`; they
    # are display facts, never inputs to the screen or the assessment.
    #
    # All optional: an opportunity can be created by hand with no posting
    # behind it, and an empty column is honest where an invented one is not.
    job_title: str = ""
    job_url: str = ""
    salary: str = ""
    location: str = ""
    posted_at: date | None = None

    #: When this employer was first tracked, and when something last went OUT.
    #: `last_outbound_on` is evidence, not a plan: it is the newest outbound
    #: touch actually recorded, which is what the cadence is computed from.
    created_at: date | None = None
    last_outbound_on: date | None = None

    @property
    def is_opportunity(self) -> bool:
        return True          # carrying a Stage is what makes it one

    @property
    def expected_status(self) -> str:
        return STATUS_MIRROR[self.stage]


def is_opportunity(record: object) -> bool:
    """Classify by the presence of a Stage value, never by tree position."""
    return getattr(record, "stage", None) is not None


def nearest_stage_bearing_ancestor(task_id: str,
                                   records: dict[str, object]) -> Opportunity | None:
    """Walk up to the opportunity an action task belongs to.

    Never assume depth 2, and never assume a "grandchild-shaped" record is
    itself a branch point.
    """
    seen: set[str] = set()
    node = records.get(task_id)
    while node is not None:
        nid = getattr(node, "id", None)
        if nid in seen:              # cycle guard
            return None
        seen.add(nid)
        parent_id = getattr(node, "parent_id", None)
        if parent_id is None:
            return None
        parent = records.get(parent_id)
        if parent is None:
            return None
        if is_opportunity(parent):
            return parent            # type: ignore[return-value]
        node = parent
    return None


# ---------------------------------------------------------------------------
# Parity — the mirror, and only ever the mirror
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ParityDefect:
    opportunity_id: str
    company: str
    found: str
    expected: str
    fix: str = "status"        # 'status' = correct the mirror. Never 'stage'.

    def __str__(self) -> str:
        return (f"{self.company} ({self.opportunity_id}): status {self.found!r} "
                f"should mirror Stage as {self.expected!r}")


def check_parity(opp: Opportunity) -> ParityDefect | None:
    """Return a defect where the visible status has drifted from Stage.

    Two exemptions, both real and both easy to get wrong:

      * A Mutual POC record is not a job opportunity and the ladder does not
        fit it — its real lifecycle is *intro asked → intro delivered or
        declined*. Its status may legitimately read `completed` while Stage
        reads `Contacted`, and "correcting" that resurrects a discharged
        thread as waiting-on-them and invites a pointless chase of someone who
        already did what was asked.

      * A bounced opportunity is held out entirely: there the STAGE is wrong,
        not the status, so mirroring would cement a contact that never
        happened. See `bounce_correction`.
    """
    if opp.category == JobCategory.MUTUAL_POC:
        return None
    if opp.email_bounced and opp.stage >= Stage.CONTACTED:
        return None
    expected = opp.expected_status
    if (opp.status or "").lower() != expected:
        return ParityDefect(opp.id, opp.company, opp.status, expected)
    return None


def bounce_correction(opp: Opportunity) -> ParityDefect | None:
    """spec 8.2: a bounce OUTRANKS the Stage field.

    A hard bounce means NEVER CONTACTED, not unanswered. So the Stage goes
    back to Identified and the status stays `open`, which now correctly reads
    "not yet contacted". This is the one case where the fix is the Stage.
    """
    if not opp.email_bounced or opp.stage < Stage.CONTACTED:
        return None
    if not opp.stage.is_live:
        return None
    return ParityDefect(
        opp.id, opp.company,
        found=f"Stage {opp.stage.label}",
        expected=f"Stage {Stage.IDENTIFIED.label}",
        fix="stage",
    )


# ---------------------------------------------------------------------------
# Advancing a stage — the two rules that stop the pipeline being corrupted
# ---------------------------------------------------------------------------

def advance_for_outbound(stage: Stage) -> Stage:
    """A successful outbound advances Identified → Contacted, and NOTHING else.

    Never infer In Dialogue or beyond from a send: those require an inbound
    reply. Inferring them from outbound alone corrupts the pipeline read. The
    same ceiling applies to a posted letter — a letter going out never implies
    a reply.
    """
    return Stage.CONTACTED if stage is Stage.IDENTIFIED else stage


@dataclass(frozen=True)
class Write:
    """One pending change, and WHICH RECORD it is against.

    The record id alone is not enough, and that is not a hypothetical. In the
    ClickUp system these rules were ported from every id was globally unique.
    Here `opportunities` and `tasks` are both `INTEGER PRIMARY KEY` and their
    ids collide from the very first row, so a caller routing on the id alone
    applies the parent's write and silently skips its children's: the cascade
    reads as committed while every subtask underneath it stays open.
    """
    kind: str          #: 'opportunity' or 'task'
    record_id: str
    field: str         #: 'stage' or 'status'
    value: str

    def __str__(self) -> str:
        return f"{self.kind}:{self.record_id} {self.field}={self.value}"


def apply_determination(opp: Opportunity, positive: bool,
                        advance_to: Stage | None = None) -> list[Write]:
    """Apply a header-verified determination from an employer.

    Negative → the parent goes Lost and EVERY subtask is rendered `no offer`.
    That cascade is legitimate precisely because the parent is going Lost at
    the same time, so the whole tree is uniformly marked with the outcome that
    actually happened.

    Returns the writes rather than performing them: a determination is
    pre-filled for the user's confirmation, never committed autonomously.
    """
    writes: list[Write] = []
    if positive:
        target = advance_to or Stage.PHONE_INTERVIEW
        if target.is_terminal or not target.is_live:
            raise TrackerError("a positive determination must advance to a live stage")
        writes.append(Write("opportunity", opp.id, "stage", target.label))
        writes.append(Write("opportunity", opp.id, "status", STATUS_MIRROR[target]))
        return writes

    writes.append(Write("opportunity", opp.id, "stage", Stage.LOST.label))
    writes.append(Write("opportunity", opp.id, "status", "no offer"))
    for t in opp.tasks:
        writes.append(Write("task", t.id, "status", "no offer"))
    return writes


def validate_child_status(child_status: str, parent_stage: Stage) -> None:
    """`no offer` on a child of a LIVE opportunity is forbidden.

    It both misstates the pipeline and risks tripping the determination
    cascade, which keys on `no offer` + Stage Lost. Two action tasks under a
    live in-person-interview opportunity would have been corrupted exactly
    this way. Once the opportunity itself is Lost the scoping no longer
    applies, which is why the parent stage is a parameter.
    """
    if (child_status or "").lower() == "no offer" and parent_stage is not Stage.LOST:
        raise TrackerError(
            "`no offer` on a child of a live opportunity misstates the pipeline "
            f"(parent stage is {parent_stage.label}). Retire the task with an "
            "evidence comment instead."
        )


def open_children(opp: Opportunity) -> list[Task]:
    """Dedup tests Open OR waiting — a non-terminal child is either."""
    return [t for t in opp.tasks if t.is_open]


def assert_at_most_one_open_child(opp: Opportunity) -> None:
    """At most one non-terminal action task per opportunity.

    More than one means a duplicate next-step was created, which is how the
    same chase goes out twice.
    """
    live = open_children(opp)
    if len(live) > 1:
        raise TrackerError(
            f"{opp.company}: {len(live)} open action tasks "
            f"({', '.join(t.id for t in live)}); at most one is allowed")


def retire_task(task: Task, evidence: str) -> Task:
    """Retiring a task is not closing the opportunity.

    Every retirement is paired with the evidence that discharged it: the
    status removes the row from view, the comment is what a later audit
    actually reads. Refusing to retire without evidence is the whole point.
    """
    if not (evidence or "").strip():
        raise TrackerError("a retirement needs evidence; the status alone is not a record")
    task.status = "complete"
    task.closed_evidence = evidence
    return task


# ---------------------------------------------------------------------------
# The board view
# ---------------------------------------------------------------------------

def pipeline_order(opps: list[Opportunity]) -> list[Opportunity]:
    """Sort by STAGE, never by status.

    The status vocabulary's own order is scrambled relative to the pipeline, so
    no status-sorted board is coherent regardless.
    """
    return sorted(opps, key=lambda o: (o.stage.value, o.company.casefold()))


def cadence_scope(opps: list[Opportunity]) -> list[Opportunity]:
    """Opportunities an active follow-up cadence applies to.

    On Hold is skipped here but is NOT dead — it still gets a reply check.
    """
    return [o for o in opps
            if o.stage.is_live and o.category != JobCategory.MUTUAL_POC]


def reply_check_scope(opps: list[Opportunity]) -> list[Opportunity]:
    """Paused is not dead: On Hold still gets checked for an inbound reply."""
    return [o for o in opps if o.stage.is_live or o.stage.is_paused]


def audit(opps: list[Opportunity]) -> dict[str, list]:
    """One pass over EVERY stage, including the closed ones.

    Auditing status-slice by status-slice leaves a different hole each time,
    and every hole looks like "nothing due".
    """
    parity: list[ParityDefect] = []
    bounces: list[ParityDefect] = []
    duplicate_children: list[str] = []
    for o in opps:
        b = bounce_correction(o)
        if b:
            bounces.append(b)
            continue
        d = check_parity(o)
        if d:
            parity.append(d)
        try:
            assert_at_most_one_open_child(o)
        except TrackerError as e:
            duplicate_children.append(str(e))
    return {
        "scanned": list(opps),
        "parity_defects": parity,
        "bounce_corrections": bounces,
        "duplicate_open_children": duplicate_children,
    }
