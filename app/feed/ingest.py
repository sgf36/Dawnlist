"""A posting you found somewhere else, brought in by hand.

    parsed = parse_pasted(text, url="https://…")
    job    = parsed.to_job()

WHY THIS EXISTS
---------------
Every comparable tracker's most-used feature is a browser button that saves the
job you are looking at. Dawnlist is a desktop application and has no browser
button, and that gap is real: people find roles through a friend, a newsletter,
a company's own careers page, or somewhere the licensed feed does not reach.
Without this, those postings simply cannot enter the tracker, and a tracker
that cannot hold the job you care most about is not a tracker.

WHY IT DOES NOT FETCH THE URL
-----------------------------
This takes what the user PASTES. It does not go and read the page.

That is a deliberate refusal and not a missing feature. Fetching arbitrary job
pages from a desktop application means requesting sites whose terms often
forbid automated access, from the user's own address, on their behalf — and
the ones that matter most are precisely the ones that block it. An application
that quietly does that on somebody's behalf is making a decision that is not
its to make.

Pasting is also simply better here: it works on a page behind a login, on a
PDF, on an email, and on the internal listing a friend forwarded. The URL is
still accepted, because provenance is worth keeping and it is what makes the
`Apply` link work later.

WHAT IT PROMISES, AND WHAT IT REFUSES TO GUESS
----------------------------------------------
Parsing a job advert out of a copied page is heuristic and will sometimes be
wrong. So the contract is: extract what can be read confidently, and REPORT
what could not be, rather than filling a field with a plausible guess. A
silently wrong company name is worse than an empty one — the empty one gets
corrected, the wrong one gets applied to.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from app.feed.models import Job

#: This provider name is what distinguishes a hand-entered posting from a fed
#: one, everywhere downstream: in dedup, in the board, and in the usage counts
#: (these cost nothing, because nobody was billed for them).
PROVIDER = "pasted"

#: Lines that are page furniture rather than the advert. Cheap to drop and
#: they otherwise become the title, because they are often the first line of a
#: copied page.
FURNITURE = re.compile(
    r"^(?:skip to (?:main )?content|menu|search|sign in|log ?in|register|"
    r"cookies?|accept all|share|save|apply now|back to (?:search|results)|"
    r"home|jobs|careers|©.*|all rights reserved.*)$",
    re.IGNORECASE)

#: "Company: X", "Employer — X", and the rest of the labelled forms.
LABELLED = {
    "title": re.compile(r"^(?:job\s+)?title\s*[:\-–—]\s*(.+)$", re.IGNORECASE),
    "company": re.compile(r"^(?:company|employer|organisation|organization)"
                          r"\s*[:\-–—]\s*(.+)$", re.IGNORECASE),
    "location": re.compile(r"^(?:location|based in|where)\s*[:\-–—]\s*(.+)$",
                           re.IGNORECASE),
    "salary": re.compile(r"^(?:salary|compensation|pay)\s*[:\-–—]\s*(.+)$",
                         re.IGNORECASE),
}

#: "Senior Analyst at Example Group" — the commonest unlabelled form, and the
#: only one worth inferring from. Anything cleverer than this starts producing
#: confident nonsense on ordinary prose.
TITLE_AT_COMPANY = re.compile(r"^(?P<title>.{3,90}?)\s+at\s+(?P<company>.{2,80})$",
                              re.IGNORECASE)

#: Below this a "description" is a headline, not an advert, and assessing it
#: would produce a verdict formed on nothing.
MIN_DESCRIPTION_CHARS = 200


@dataclass(frozen=True)
class ParsedPosting:
    """What could be read, and what could not.

    `unresolved` is the point of the type. A caller must be able to ask the
    user for the company name rather than proceed with a guess.
    """

    title: str = ""
    company: str = ""
    location: str = ""
    salary: str = ""
    description: str = ""
    url: str = ""
    unresolved: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_usable(self) -> bool:
        """Enough to assess. Title and description; company can be filled in.

        Deliberately NOT requiring the company: somebody pasting from a
        recruiter's listing often does not have it, and refusing the posting
        entirely would lose the role over a field the screen does not read.
        """
        return bool(self.title) and len(self.description) >= MIN_DESCRIPTION_CHARS

    def to_job(self) -> Job:
        return Job(
            provider=PROVIDER,
            provider_job_id=stable_id(self.url, self.description),
            title=self.title,
            company=self.company,
            locations=(self.location,) if self.location else (),
            description_text=self.description,
            salary=self.salary or None,
            url=self.url,
            # Kept so the board can show that this one was entered by hand and
            # was not read from the feed. It is the difference between "the
            # feed missed this" and "the feed never saw it".
            raw_criteria={"source": "pasted"},
        )


def stable_id(url: str, description: str) -> str:
    """An id that is the same the second time the same posting is pasted.

    Derived from the URL where there is one, because the same advert copied
    twice picks up different whitespace and would otherwise arrive as two
    jobs. Falls back to the description so a posting with no URL still
    deduplicates against itself.
    """
    basis = url.strip().lower() or re.sub(r"\s+", " ", description).strip().lower()
    return "p" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:20]


def _clean_lines(text: str) -> list[str]:
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or FURNITURE.match(line):
            continue
        out.append(line)
    return out


def _host(url: str) -> str:
    try:
        netloc = urlparse(url).netloc
    except ValueError:
        return ""
    return netloc[4:] if netloc.startswith("www.") else netloc


def parse_pasted(text: str, url: str = "") -> ParsedPosting:
    """Read a copied job advert. Reports what it could not determine."""
    lines = _clean_lines(text)
    found: dict[str, str] = {}

    # 1. Labelled fields first. When somebody has pasted a structured listing
    #    these are right, and no inference should override them.
    for line in lines:
        for key, pattern in LABELLED.items():
            if key in found:
                continue
            m = pattern.match(line)
            if m:
                found[key] = m.group(1).strip()

    # 2. The heading. The first line that is not furniture and not itself a
    #    labelled field is the title far more often than anything else is.
    if "title" not in found:
        for line in lines:
            if any(p.match(line) for p in LABELLED.values()):
                continue
            m = TITLE_AT_COMPANY.match(line)
            if m:
                found["title"] = m.group("title").strip()
                found.setdefault("company", m.group("company").strip())
                break
            if 3 <= len(line) <= 120:
                found["title"] = line
                break

    # 3. The description is everything, not the remainder. A model reading the
    #    advert should see the heading too — and removing lines to be tidy is
    #    how the salary or the closing date goes missing.
    description = "\n".join(lines).strip()

    unresolved = []
    if not found.get("title"):
        unresolved.append("title")
    if not found.get("company"):
        unresolved.append("company")
    if len(description) < MIN_DESCRIPTION_CHARS:
        # Named separately from "title" so the caller can say "paste the whole
        # advert" rather than "fill in the company".
        unresolved.append("description")

    return ParsedPosting(
        title=found.get("title", ""),
        company=found.get("company", "") or _host(url),
        location=found.get("location", ""),
        salary=found.get("salary", ""),
        description=description,
        url=url.strip(),
        unresolved=tuple(unresolved),
    )
