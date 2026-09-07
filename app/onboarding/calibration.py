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

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

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


@dataclass
class CalibrationResult:
    items: list[CalibrationItem] = field(default_factory=list)

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

    def blocking_reasons(self) -> list[str]:
        """Everything stopping this gate from passing. All of them, at once —
        revealing them one at a time makes the step feel endless."""
        reasons: list[str] = []
        if len(self.items) < MIN_DECIDED:
            # A sample too short to calibrate against is a SETUP failure, not a
            # user one, and it must not be phrased as an instruction. Asking
            # for eight decisions out of one is a gate nobody can pass: the
            # Finish button simply never enabled, on the last step of
            # onboarding, with nothing on screen saying why.
            return [
                f"Dawnlist could not fetch enough live postings to calibrate "
                f"against — {len(self.items)} of the {MIN_DECIDED} it needs. "
                f"That is a setup problem rather than anything you have done: "
                f"check a search is switched on and the feed is reachable, "
                f"then try again."]
        if len(self.decided) < MIN_DECIDED:
            reasons.append(
                f"decide at least {MIN_DECIDED} of the {len(self.items)} "
                f"postings ({len(self.decided)} so far)")
        for item in self.missing_sentences:
            reasons.append(
                f"{item.title} at {item.company}: you disagreed, so say what "
                f"sentence, added to the brief, would have got this right — "
                f"a correction without one will not persist")
        return reasons

    @property
    def passed(self) -> bool:
        return not self.blocking_reasons()


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
