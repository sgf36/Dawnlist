"""Draft output — the no-credentials design (handoff Part 4).

The app writes RFC 5322 `.eml` files into a folder the user chooses, and opens
them on click. Every mainstream mail client on both platforms opens a `.eml` as
a composable draft, so the user's send click happens in their own mail client.

That is what makes invariant 1 STRUCTURAL rather than a promise: there is no
mailbox credential anywhere in the product, no OAuth, no app password, and no
SMTP code in the binary. `tests/test_no_send.py` asserts the absence of the
sending machinery, so "never sends" is a property of the build, not a policy
someone has to remember.

It also deletes the onboarding session the source system flags as most likely
to need a second attempt, and it sidesteps Google's restricted-scope rules
(an annual paid CASA assessment) entirely, by never raising the question.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from urllib.parse import quote

#: Anything the factsheet could not support is left as a visible placeholder.
#: spec 9.1: if a claim is not on the factsheet, do not make it.
PLACEHOLDER = re.compile(r"\[\[([^\]]+)\]\]")


class NotSendReady(ValueError):
    """Raised when a draft still carries unresolved placeholders."""


@dataclass
class Draft:
    to_name: str
    to_email: str
    subject: str
    body: str
    thread_key: str
    from_name: str = ""
    from_email: str = ""
    #: Set when replying, so the draft threads correctly in the user's client.
    in_reply_to: str | None = None
    references: tuple[str, ...] = ()
    path: Path | None = None

    @property
    def placeholders(self) -> list[str]:
        return PLACEHOLDER.findall(self.body) + PLACEHOLDER.findall(self.subject)

    @property
    def send_ready(self) -> bool:
        """Invariant 2. A gap in the evidence blocks the export, visibly."""
        return not self.placeholders

    def assert_send_ready(self) -> None:
        if not self.send_ready:
            raise NotSendReady(
                "unresolved placeholders: "
                + ", ".join(repr(p) for p in self.placeholders)
                + ". Every factual claim must come from the factsheet; fill "
                  "these from evidence or cut the sentence.")


def states_the_ask(body: str, *, within_sentences: int = 3) -> bool:
    """spec 9.4: say plainly, in the first two or three sentences, that this is
    an individual exploring roles — not a vendor, not a consultancy.

    Two recipients in one afternoon independently read an abstract,
    commentary-led template as a consulting pitch. "I'd value your perspective"
    is not a substitute for stating the ask.
    """
    opening = " ".join(re.split(r"(?<=[.!?])\s+", body.strip())[:within_sentences])
    low = opening.casefold()
    individual = any(p in low for p in (
        "i am an individual", "i'm an individual", "as an individual",
        "in a personal capacity", "personally exploring", "myself",
        "i am exploring", "i'm exploring", "i am looking for", "i'm looking for",
        "my own job search", "on my own behalf"))
    not_selling = any(p in low for p in (
        "not selling", "not a vendor", "not a pitch", "nothing to sell",
        "not a consultancy", "not pitching"))
    return individual or not_selling


def build_eml(draft: Draft) -> EmailMessage:
    msg = EmailMessage()
    msg["To"] = f"{draft.to_name} <{draft.to_email}>" if draft.to_name else draft.to_email
    if draft.from_email:
        msg["From"] = (f"{draft.from_name} <{draft.from_email}>"
                       if draft.from_name else draft.from_email)
    msg["Subject"] = draft.subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="dawnlist.local")
    if draft.in_reply_to:
        msg["In-Reply-To"] = draft.in_reply_to
        refs = " ".join(draft.references or (draft.in_reply_to,))
        msg["References"] = refs
    # X-Unsent tells Outlook and several other clients to open this as an
    # unsent draft rather than as a received message.
    msg["X-Unsent"] = "1"
    msg.set_content(draft.body)
    return msg


def safe_stem(text: str, limit: int = 60) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")
    return (stem[:limit] or "draft").rstrip("-")


def write_draft(draft: Draft, folder: Path, *, allow_placeholders: bool = True) -> Path:
    """Write the draft to `folder`.

    Placeholders are allowed on disk by default — a half-evidenced draft is
    useful to look at — but such a file is named `.NEEDS-EVIDENCE.eml` so it
    cannot be mistaken for a finished one, and `assert_send_ready` still
    refuses it for any send-ready export.
    """
    if not allow_placeholders:
        draft.assert_send_ready()
    folder.mkdir(parents=True, exist_ok=True)
    suffix = ".eml" if draft.send_ready else ".NEEDS-EVIDENCE.eml"
    path = folder / f"{safe_stem(draft.thread_key)}{suffix}"
    path.write_bytes(bytes(build_eml(draft)))
    draft.path = path
    return path


def revise_in_place(draft: Draft, folder: Path, previous: Path | None) -> Path:
    """spec 9.3: one draft per recipient per thread, ever. Revise, never stack.

    The previous file is removed only after the new one is written, so a crash
    between the two leaves two drafts rather than none — the recoverable
    failure of the pair.
    """
    new_path = write_draft(draft, folder)
    if previous and previous.exists() and previous.resolve() != new_path.resolve():
        previous.unlink()
    return new_path


def mailto_url(draft: Draft) -> str:
    """Secondary path for short notes. Also never sends — it opens a composer."""
    return (f"mailto:{quote(draft.to_email)}"
            f"?subject={quote(draft.subject)}&body={quote(draft.body)}")


@dataclass
class DraftSet:
    """Every draft a run produced, with the ones that are not send-ready named."""
    drafts: list[Draft] = field(default_factory=list)

    @property
    def blocked(self) -> list[Draft]:
        return [d for d in self.drafts if not d.send_ready]

    @property
    def counts(self) -> dict[str, int]:
        return {"drafted": len(self.drafts),
                "send_ready": len(self.drafts) - len(self.blocked),
                "needs_evidence": len(self.blocked)}
