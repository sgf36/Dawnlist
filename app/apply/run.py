"""Everything you need for one application, produced in one pass.

    pack = prepare_application(job, factsheet=..., cv_text=..., send=...,
                               folder=...)

WHY IT IS ONE CALL AND NOT FOUR
-------------------------------
The gap analysis, the tailored CV, the covering letter and the interview brief
all read the same posting against the same evidence, and three of them want
the gap analysis as input. Producing them separately means the person runs
four things in the right order, and the order matters: a CV written without
knowing which requirements are unmet is the one that reaches for "exposure to"
as a way of implying one.

So the gap analysis runs first, always, and its result is handed to the rest.
It costs nothing, so there is no case for skipping it.

WHAT IS OPTIONAL, AND WHY THE BRIEF IS NOT PRODUCED BY DEFAULT
--------------------------------------------------------------
The brief is for an interview that has not been offered yet. Producing one for
every posting spends the user's own tokens on a conversation that will mostly
not happen, so it is asked for by name — from the board, when something
becomes real.

FAILURE IS REPORTED, NEVER SUBSTITUTED
--------------------------------------
If a request fails, the pack carries the error and that document is absent.
Nothing falls back to a generic version: a covering letter that quietly became
a template is worse than none, because it goes out looking finished.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.apply.documents import (ApplyDocument, build_document_request,
                                 parse_document, write_document)
from app.apply.interview import (InterviewBrief, build_brief_request,
                                 parse_brief)
from app.apply.keywords import GapReport, analyse
from app.feed.models import Job


@dataclass
class ApplicationPack:
    job: Job
    gaps: GapReport
    cv: ApplyDocument | None = None
    letter: ApplyDocument | None = None
    brief: InterviewBrief | None = None
    #: One line per failure, in the order they happened. Never silently empty
    #: when something went wrong.
    errors: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> list[ApplyDocument]:
        """Documents carrying an unsupported claim, which must not be sent."""
        return [d for d in (self.cv, self.letter)
                if d is not None and not d.send_ready]

    @property
    def complete(self) -> bool:
        return not self.errors and not self.blocked


def _evidence(factsheet: str, cv_text: str) -> str:
    return f"{factsheet}\n\n{cv_text}"


def prepare_application(job: Job, *, factsheet: str, cv_text: str, send,
                        folder: Path | None = None,
                        want_cv: bool = True, want_letter: bool = True,
                        want_brief: bool = False) -> ApplicationPack:
    """Produce the pack. `send(request) -> payload` is injected, as elsewhere.

    Nothing here spends money by itself and nothing reaches Dawnlist's
    servers: every request goes to Anthropic on the user's own key, exactly as
    assessment and drafting already do.
    """
    # THE SOURCE CV IS REQUIRED, and the guard belongs here rather than only
    # at the caller. `main.apply_run` already refuses an empty one — and the
    # first real run went straight past it, because the verification called
    # this function directly and handed it "". The result looked completely
    # fine: a fluent, well-structured CV written out of the factsheet alone.
    #
    # That is the failure this package is supposed to make impossible. A
    # factsheet is a distillation; a CV reordered from it is a NEW document
    # describing a career nobody had, however true each individual line is.
    # "Reorder and re-emphasise; do not rewrite history" cannot hold when
    # there is no history to reorder.
    #
    # A guard that exists only at one entry point is not a guard.
    if want_cv and not cv_text.strip():
        raise ValueError(
            "No curriculum vitae text. A tailored CV reorders the person's own "
            "document; without it the model writes a new one from the "
            "factsheet, which reads as a CV and is not theirs.")

    gaps = analyse(job.description_text, _evidence(factsheet, cv_text))
    pack = ApplicationPack(job=job, gaps=gaps)

    common = dict(title=job.title, company=job.company,
                  description=job.description_text,
                  factsheet=factsheet, cv_text=cv_text, gaps=gaps)

    for kind, wanted in (("cv", want_cv), ("letter", want_letter)):
        if not wanted:
            continue
        try:
            payload = send(build_document_request(kind, **common))
        except Exception as exc:  # noqa: BLE001
            # Named by kind so "the letter failed" is distinguishable from
            # "the CV failed" without reading a traceback.
            pack.errors.append(f"{kind} failed: {type(exc).__name__}: {exc}")
            continue
        doc = parse_document(kind, payload, company=job.company,
                             posting_title=job.title)
        setattr(pack, kind, doc)
        if folder is not None:
            write_document(doc, folder)

    if want_brief:
        try:
            payload = send(build_brief_request(**common))
        except Exception as exc:  # noqa: BLE001
            pack.errors.append(f"brief failed: {type(exc).__name__}: {exc}")
        else:
            pack.brief = parse_brief(payload, gaps=gaps)
            if folder is not None and not pack.brief.is_empty:
                folder.mkdir(parents=True, exist_ok=True)
                from app.outreach.drafts import safe_stem
                stem = safe_stem(f"{job.company}-{job.title}")
                (folder / f"{stem}-interview-brief.md").write_text(
                    pack.brief.body + "\n", encoding="utf-8")

    return pack


def summarise(pack: ApplicationPack) -> str:
    """One paragraph a person can read without opening anything.

    Leads with what is missing rather than what was produced. "Two documents
    written" is not the useful fact; "you cannot evidence two stated
    requirements" is.
    """
    lines = []
    if pack.gaps.is_empty:
        lines.append("No stated requirements could be read from this posting.")
    else:
        missing = [r.phrase for r in pack.gaps.missing_required]
        if missing:
            lines.append("Not supported by your evidence: "
                         + ", ".join(missing) + ".")
        else:
            lines.append("Your evidence covers every stated requirement.")

    for doc in (pack.cv, pack.letter):
        if doc is None:
            continue
        state = "ready" if doc.send_ready else "BLOCKED: " + "; ".join(
            doc.placeholders)
        lines.append(f"{doc.kind}: {state}")

    # The brief was omitted from this summary entirely, and on the first live
    # run it produced no file and said nothing — a request was made, tokens
    # were spent, and the only evidence was a folder with one fewer file than
    # expected. Anything ASKED FOR must appear here, including when it did not
    # arrive: a silent absence is the failure this whole module is written
    # against.
    if pack.brief is not None:
        lines.append("interview brief: "
                     + ("ready" if not pack.brief.is_empty else
                        "EMPTY — the request returned nothing usable"))

    lines.extend(pack.errors)
    return "\n".join(lines)
