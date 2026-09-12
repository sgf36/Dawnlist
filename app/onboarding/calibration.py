"""The calibration gate.

Before the first real run, the app fetches ~10 live postings across the user's
queries, shows its own verdicts, and asks the user to correct them. Each
correction rewrites the fit brief.

**This is the transfer-of-judgement step that made the original system work.**
Not a tutorial and not a nicety: the brief is the single source of truth for
every later verdict, and a brief that has never been tested against a real
posting is a guess. A user let into daily runs without passing this gets a
plausible-looking shortlist built on nothing, which is worse than no shortlist
because it is believable.

The question a correction must answer is the one from the operating spec:

    "What sentence, added here, would have got this right?"

That phrasing matters. It stops the user editing the conversation — a fix that
evaporates — and makes them edit the brief, which persists. The same loop runs
weekly afterwards, because every override is a sentence the brief is missing.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.i18n import tr

#: How many live postings the gate shows. Enough to disagree with, few enough
#: to finish in one sitting — the product's promise is 30-45 minutes to first
#: shortlist, and this is the largest single step inside it.
SAMPLE_SIZE = 10

#: How many the user must actually decide before the gate can pass. Not all of
#: them: some will be genuinely ambiguous, and forcing a verdict on those
#: teaches the brief noise.
MIN_DECIDED = 8

#: A gate that passes with no disagreement has taught the brief nothing. It is
#: still a pass — the brief may simply be good — but it is reported, because
#: unanimous agreement on ten postings more often means the sample was too easy.
LOW_SIGNAL_THRESHOLD = 1

#: Postings from any one employer. Ten roles at one hotel group teaches the
#: brief a single rule, badly, and spends every slot in the sample doing it.
MAX_PER_COMPANY = 2


def normalised_title(title: str) -> str:
    """A title stripped to what makes two postings the same job.

    Case and punctuation only: "Asset Manager", "asset manager" and "Asset
    Manager (London)" would each take a slot in a ten-posting sample and teach
    the brief the same thing three times.
    """
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", (title or "").lower()).split())


def worth_calibrating(items, *, size: int = SAMPLE_SIZE):
    """Choose the postings actually put to the user, from a larger pool.

    WHY NOT THE FIRST TEN THAT ARRIVED. The gate exists to transfer judgement,
    and it only does that where the user can DISAGREE with something. A sample
    the app is obviously right about ten times over teaches the brief nothing —
    which `low_signal` already reports after the fact, having spent the user's
    half hour finding out.

    So the sample is drawn ACROSS the app's own verdicts, round by round: a
    strong fit, a possible, a rejection, and again. That puts the decision
    boundary in front of the user, which is the only place their judgement is
    worth more than the brief's.

    Two structural rules go with it, both of which exist because a feed returns
    clusters: at most `MAX_PER_COMPANY` from one employer, and never two
    postings with the same normalised title.

    A LOPSIDED POOL PRODUCES A SHORT SAMPLE, deliberately. Ten near-identical
    roles at one company cannot teach the brief ten things, and padding the
    sample back to ten with them would hide that behind a full-looking screen.
    The gate already knows what to do with a short sample: it says so and lets
    the user finish (`sample_unavailable`).
    """
    buckets: dict[str, list] = {}
    for item in items:
        buckets.setdefault(item.app_verdict, []).append(item)

    chosen: list = []
    companies: dict[str, int] = {}
    titles: set[str] = set()
    # Round-robin over the buckets in the order they first appeared, so the
    # result is deterministic and does not depend on dict ordering luck.
    queues = list(buckets.values())
    while len(chosen) < size and any(queues):
        progressed = False
        for queue in queues:
            if len(chosen) >= size:
                break
            while queue:
                item = queue.pop(0)
                company = (item.company or "").strip().casefold()
                title = normalised_title(item.title)
                if companies.get(company, 0) >= MAX_PER_COMPANY and company:
                    continue
                if title and title in titles:
                    continue
                chosen.append(item)
                companies[company] = companies.get(company, 0) + 1
                titles.add(title)
                progressed = True
                break
        if not progressed:
            break
    return chosen


@dataclass
class CalibrationItem:
    """One posting put to the user, with the app's verdict and theirs."""
    job_key: str
    title: str
    company: str
    description: str
    app_verdict: str
    app_reason: str
    user_verdict: str | None = None
    #: The sentence the user says would have got this right. This is what
    #: actually changes behaviour; the verdict alone changes nothing.
    brief_sentence: str = ""

    @property
    def decided(self) -> bool:
        return self.user_verdict is not None

    @property
    def disagreed(self) -> bool:
        return self.decided and self.user_verdict != self.app_verdict

    @property
    def needs_sentence(self) -> bool:
        """A disagreement without a sentence is a correction that will not
        persist — the next run makes exactly the same mistake."""
        return self.disagreed and not self.brief_sentence.strip()


class CalibrationSample(list):
    """The postings the gate will show, and why there were not enough of them.

    A list, so every existing caller and test keeps working. The extra field is
    what the screen could not previously say: "not enough live postings" was
    reported identically whether the market was quiet or whether nothing had
    been paid for, and the second is by far the commoner cause on a fresh
    install. `no_feed` carries the provider's own refusal through instead of
    swallowing it at the `except`.
    """

    def __init__(self, items=(), *, no_feed: str | None = None):
        super().__init__(items)
        #: The reason the feed could not be built, verbatim, or None.
        self.no_feed = no_feed


@dataclass
class CalibrationResult:
    items: list[CalibrationItem] = field(default_factory=list)
    #: Why there was no feed to fetch from, when that is what went wrong.
    #: Kept beside the items because the blocking reason has to name the cause
    #: the user can act on, and "switch a search on" is the wrong instruction
    #: for someone who has not subscribed.
    no_feed: str | None = None

    @property
    def decided(self) -> list[CalibrationItem]:
        return [i for i in self.items if i.decided]

    @property
    def disagreements(self) -> list[CalibrationItem]:
        return [i for i in self.items if i.disagreed]

    @property
    def missing_sentences(self) -> list[CalibrationItem]:
        return [i for i in self.items if i.needs_sentence]

    @property
    def agreement_rate(self) -> float:
        if not self.decided:
            return 0.0
        return 1 - (len(self.disagreements) / len(self.decided))

    @property
    def low_signal(self) -> bool:
        return len(self.disagreements) < LOW_SIGNAL_THRESHOLD

    @property
    def sample_unavailable(self) -> bool:
        """Too few live postings to calibrate against.

        A SETUP failure, never the user's — and therefore NEVER a blocker.

        This used to return a blocking reason, and it trapped every new user on
        the last screen of onboarding. Seeded searches are created switched OFF
        by design, so a fresh install has no enabled search, fetches nothing,
        and `passed` stayed False for ever. The screen then told the user to
        "check a search is switched on" — which onboarding gave them no way to
        do, because no such step existed. Both Windows databases and the Mac
        one were found with zero enabled queries and Finish permanently greyed:
        nobody had ever completed onboarding on any platform.

        Calibration is worth having. It is not worth being unable to start the
        application at all, and a gate the user cannot act on is not a gate.
        """
        return len(self.items) < MIN_DECIDED

    def blocking_reasons(self) -> list[str]:
        """Everything stopping this gate from passing. All of them, at once —
        revealing them one at a time makes the step feel endless."""
        reasons: list[str] = []
        if self.sample_unavailable:
            # STILL a failure of the gate, and deliberately so: a calibration
            # against one posting must never be RECORDED as a calibration.
            # `passed` therefore stays False and `mark_calibrated` keeps
            # refusing it.
            #
            # What changed is who is trapped by that. Finishing onboarding is a
            # separate question from recording a pass — see `can_finish`.
            #
            # WHICH SENTENCE DEPENDS ON WHY. Telling somebody with no
            # subscription to "check a search is switched on" describes a quiet
            # market and sends them to fix the one thing that was never wrong.
            if self.no_feed:
                return [tr("onboarding.calibration_no_feed")]
            return [tr("onboarding.blocker_short_sample",
                       count=len(self.items), needed=MIN_DECIDED)]
        if len(self.decided) < MIN_DECIDED:
            reasons.append(tr("onboarding.blocker_decide", needed=MIN_DECIDED,
                              total=len(self.items),
                              decided=len(self.decided)))
        for item in self.missing_sentences:
            reasons.append(tr("onboarding.blocker_sentence", title=item.title,
                              company=item.company))
        return reasons

    @property
    def passed(self) -> bool:
        """Whether this may be RECORDED as a calibration. Never relax this."""
        return not self.blocking_reasons()

    @property
    def can_finish(self) -> bool:
        """Whether the user may leave onboarding — a different question.

        A gate the user cannot act on is not a gate. When the sample never
        arrived there is nothing on that screen for them to do, so they may
        finish; the calibration simply is not recorded, and runs on a later
        morning. Keeping these two questions apart is the point: conflating
        them either traps the user (what shipped) or records a calibration
        against one posting as though it were eight (what the first fix did).
        """
        return self.passed or self.sample_unavailable


def apply_corrections(brief: str, result: CalibrationResult) -> str:
    """Fold the user's sentences into the fit brief.

    Appended under a dated heading rather than merged into the prose, because
    the provenance is worth keeping: a later reader can see which sentences
    were earned from a real disagreement and which were written up front.
    """
    sentences = [i.brief_sentence.strip() for i in result.disagreements
                 if i.brief_sentence.strip()]
    if not sentences:
        return brief

    stamp = datetime.now(timezone.utc).date().isoformat()
    lines = [brief.rstrip(), "", f"## Learned from calibration, {stamp}", ""]
    lines.extend(f"- {s}" for s in sentences)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Persistence — the gate's state is a setting, so nothing can run around it
# ---------------------------------------------------------------------------

CALIBRATION_KEY = "calibration_passed_at"


def is_calibrated(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM settings WHERE key=?",
                       (CALIBRATION_KEY,)).fetchone()
    return bool(row and row["value"])


def mark_calibrated(conn: sqlite3.Connection, result: CalibrationResult) -> None:
    """Record the pass. Refuses to record a gate that did not actually pass."""
    if not result.passed:
        raise ValueError(
            "calibration has not passed: " + "; ".join(result.blocking_reasons()))
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (CALIBRATION_KEY,
         datetime.now(timezone.utc).isoformat(timespec="seconds")))
    conn.commit()


def save_brief(conn: sqlite3.Connection, body: str) -> int:
    """Store a new fit-brief version. Never updates in place.

    The history is what makes the weekly loop real: every override is a
    sentence the brief was missing, and being able to see when a sentence
    arrived is how a wrong one gets found again.
    """
    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) v FROM documents WHERE kind='fit_brief'"
    ).fetchone()
    version = row["v"] + 1
    conn.execute(
        "INSERT INTO documents(kind, version, body, created_at) "
        "VALUES('fit_brief', ?, ?, ?)",
        (version, body,
         datetime.now(timezone.utc).isoformat(timespec="seconds")))
    conn.commit()
    return version


def complete_calibration(conn: sqlite3.Connection, brief: str,
                         result: CalibrationResult) -> str:
    """Apply the corrections, save the new brief, and open the gate.

    Ordered so a failure leaves the gate SHUT: the brief is written first, and
    `mark_calibrated` raises before recording anything if the gate has not
    actually passed. A half-finished calibration therefore costs the user their
    sentences, never a false pass.
    """
    updated = apply_corrections(brief, result)
    save_brief(conn, updated)
    mark_calibrated(conn, result)
    return updated
