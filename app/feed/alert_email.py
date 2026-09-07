"""Job-alert emails, dragged onto the app.

This is the universal, zero-cost feed supplement (handoff 0.2). The user drags
digest emails from any job board onto the app as `.eml` files and the cards are
parsed out. No credentials, no mailbox access, works with every provider, and it
is the user handling their own mail.

It is also NOT scraping. Nothing here fetches anything: it reads a file the user
already has. The shipped binary contains no job-board network code and never
visits a job board.

Two things are load-bearing:

  * **Tracking parameters are stripped before the id is derived.** Digest links
    carry per-send tracking, so the same posting in Tuesday's and Thursday's
    digest has two different URLs. Keying on the raw URL would surface one job
    twice, every time, and look like the feed was broken.

  * **A digest snippet is marked as a snippet.** A card carries two lines, not a
    description. Passed to the assessment unmarked it reads as a complete
    posting and invites a confident rejection on requirements the card never
    stated — the exact failure spec 6.7 exists to prevent.
"""
from __future__ import annotations

import email
import email.policy
import hashlib
import html
import mailbox
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse, urlunparse

from app.feed.models import Job

PROVIDER = "alert-email"

#: Appended to every parsed description. The assessment reads this and sets
#: requirement_checked=false rather than rejecting on absent requirements.
SNIPPET_MARKER = (
    "\n\n[ALERT-EMAIL CARD — this is a digest snippet, not the full posting. "
    "Requirements not shown here are NOT CHECKED, never failed.]")

#: Query parameters that identify a posting rather than a send. Everything else
#: is discarded, because everything else varies per email.
MEANINGFUL_PARAMS = {"jk", "jobid", "job_id", "id", "currentjobid", "vjk",
                     "position", "postingid"}

#: Hosts whose links are navigation, not postings.
_CHROME = re.compile(
    r"(unsubscribe|/settings|/help|/legal|/privacy|/preferences|mailto:|"
    r"/feed/?$|/notifications|facebook\.com|twitter\.com|x\.com|"
    r"instagram\.com|linkedin\.com/company/)", re.I)
# `/comm/` was in this list and had to come out: EVERY real LinkedIn digest job
# link is `linkedin.com/comm/jobs/view/<id>`, so the chrome filter was killing
# precisely the links it exists to find. Chrome is already excluded by the
# specific paths above plus the requirement that _JOB_PATH match.

_JOB_PATH = re.compile(
    r"(/jobs?/view/|/viewjob|/job/|/jobs/|/vacancy/|/careers?/|/rc/clk|"
    r"/job-details|/apply/|/opportunit)", re.I)

#: The digest's own furniture links to the same job paths as the postings do —
#: the "Your job alert for ..." header and the "Manage job alerts" footer both
#: survive every URL test. They differ in their TITLE, which is where they are
#: caught. Found against four real digests, where they produced two rows whose
#: company field was raw HTML.
_CHROME_TITLE = re.compile(
    r"^\s*(your job alert|manage job alert|see all job|view all job|"
    r"job alert|unsubscribe|update your)", re.I)

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t ]+")


@dataclass
class ParsedAlert:
    jobs: list[Job] = field(default_factory=list)
    source: str = ""
    #: Named, never silently empty — "no jobs found" and "the file did not
    #: parse" must be distinguishable to the user.
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


#: Mail-security rewriters that wrap the real link in a redirect. The original
#: sits percent-encoded in a query parameter.
#:
#: Microsoft Defender for Office 365 rewrites EVERY url in mail arriving at a
#: protected tenant. Measured against four real LinkedIn digests from Spencer's
#: Exchange account: 0 postings parsed, because the parser looked for
#: `linkedin.com/jobs/view/<id>` and every href was a
#: `gbr01.safelinks.protection.outlook.com` wrapper. The feature worked on
#: fixtures and would have failed for him on every single email — and the error
#: blamed his file ("is this a job-alert email?") rather than naming the cause.
REDIRECT_WRAPPERS = {
    "safelinks.protection.outlook.com": "url",     # Microsoft Defender
    "protect-eu.mimecast.com": "u",                # Mimecast
    "urldefense.proofpoint.com": "u",              # Proofpoint
    "clicktime.symantec.com": "u",                 # Symantec
}


def unwrap_redirect(url: str, _depth: int = 0) -> str:
    """The real destination behind a mail-security redirect.

    Recursive, but bounded: a wrapper can wrap a wrapper when mail crosses two
    filters, and an unbounded loop on a malformed link would hang the parse.
    """
    if _depth > 3:
        return url
    try:
        parts = urlparse(url)
    except ValueError:
        return url
    host = (parts.netloc or "").lower()
    for wrapper, param in REDIRECT_WRAPPERS.items():
        if host.endswith(wrapper) or wrapper in host:
            inner = parse_qs(parts.query).get(param)
            if inner and inner[0]:
                return unwrap_redirect(unquote(inner[0]), _depth + 1)
    return url


def canonical_url(url: str) -> str:
    """Strip per-send tracking, keep what identifies the posting."""
    url = unwrap_redirect(url)
    try:
        parts = urlparse(url)
    except ValueError:
        return url
    query = parse_qs(parts.query)
    kept = {k: v for k, v in query.items() if k.lower() in MEANINGFUL_PARAMS}
    rebuilt = "&".join(f"{k}={v[0]}" for k, v in sorted(kept.items()))
    path = parts.path.rstrip("/")
    return urlunparse((parts.scheme, parts.netloc, path, "", rebuilt, ""))


def job_id_for(url: str) -> str:
    """A stable id for a posting seen only through a digest.

    Derived from the canonical URL, so the same posting in two digests
    deduplicates to one row rather than surfacing twice.
    """
    canonical = canonical_url(url)
    for key in ("currentJobId", "jk", "jobId", "job_id"):
        match = re.search(rf"[?&]{key}=([\w-]+)", canonical, re.I)
        if match:
            return f"{urlparse(canonical).netloc}:{match.group(1)}"
    match = re.search(r"/(?:jobs?/view|viewjob|job)/([\w-]+)", canonical, re.I)
    if match:
        return f"{urlparse(canonical).netloc}:{match.group(1)}"
    return hashlib.sha1(canonical.encode()).hexdigest()[:16]


def _strip_html(fragment: str) -> str:
    text = _TAG.sub(" ", fragment)
    text = html.unescape(text)
    return _WS.sub(" ", text).strip()


def _looks_like_a_posting(url: str) -> bool:
    if not url.lower().startswith("http"):
        return False
    if _CHROME.search(url):
        return False
    return bool(_JOB_PATH.search(url))


def _anchor_blocks(body_html: str) -> list[tuple[str, str, str]]:
    """(url, anchor text, following text) for every anchor in the HTML."""
    out = []
    for match in re.finditer(
            r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            body_html, re.I | re.S):
        url, inner = match.group(1), match.group(2)
        tail = body_html[match.end():match.end() + 600]
        out.append((html.unescape(url), _strip_html(inner), _strip_html(tail)))
    return out


def _company_and_location(text: str) -> tuple[str, str]:
    """Digest cards put company and location on the lines under the title."""
    parts = [p.strip() for p in re.split(r"[·|••]|\s{2,}", text) if p.strip()]
    parts = [p for p in parts
             if not re.match(r"^(view job|apply|see all|\d+ (new )?jobs?)", p, re.I)]
    company = parts[0] if parts else ""
    location = parts[1] if len(parts) > 1 else ""
    # A card's tail often runs into the next card; keep only plausible values.
    if len(company) > 80:
        company = company[:80].rsplit(" ", 1)[0]
    if len(location) > 60:
        location = location[:60].rsplit(" ", 1)[0]
    return company, location


def parse_html(body_html: str, *, source: str = "") -> list[Job]:
    jobs: list[Job] = []
    seen: set[str] = set()

    for url, title, tail in _anchor_blocks(body_html):
        # Unwrap BEFORE testing: a Safe Links wrapper carries the real path
        # percent-encoded in a query parameter, so `/jobs/view/` is not visible
        # to any pattern until the wrapper is removed.
        url = unwrap_redirect(url)
        if not _looks_like_a_posting(url) or not title:
            continue
        if len(title) < 3 or re.match(r"^(view|apply|see|more)\b", title, re.I):
            continue
        if _CHROME_TITLE.match(title):
            continue

        provider_job_id = job_id_for(url)
        if provider_job_id in seen:
            continue
        seen.add(provider_job_id)

        company, location = _company_and_location(tail)
        # Markup in the company field means the surrounding block did not parse
        # as a card. Better an empty employer the reader can see than a tag.
        if "<" in company or ">" in company:
            company = ""
        jobs.append(Job(
            provider=PROVIDER,
            provider_job_id=provider_job_id,
            title=title,
            company=company,
            locations=(location,) if location else (),
            # Marked, so the assessment cannot mistake two lines for a posting.
            description_text=f"{title}. {company}. {location}".strip()
                             + SNIPPET_MARKER,
            url=canonical_url(url),
            raw_criteria={"source": source, "via": "alert-email",
                          "snippet_only": True},
        ))
    return jobs


def _body_of(message) -> tuple[str, str]:
    """(html, plain) — either may be empty."""
    body_html = body_text = ""
    try:
        part = message.get_body(preferencelist=("html",))
        if part is not None:
            body_html = part.get_content()
    except Exception:  # noqa: BLE001
        pass
    try:
        part = message.get_body(preferencelist=("plain",))
        if part is not None:
            body_text = part.get_content()
    except Exception:  # noqa: BLE001
        pass
    return body_html, body_text


def parse_eml(path: Path) -> ParsedAlert:
    """Parse one saved email. A failure is reported, never raised."""
    path = Path(path)
    if not path.exists():
        return ParsedAlert(error=f"{path.name}: the file no longer exists")

    try:
        message = email.message_from_bytes(path.read_bytes(),
                                           policy=email.policy.default)
    except Exception as exc:  # noqa: BLE001
        return ParsedAlert(error=f"{path.name}: could not read the email "
                                 f"({type(exc).__name__})")

    sender = str(message.get("From", ""))
    subject = str(message.get("Subject", ""))
    source = sender or subject or path.name

    body_html, _ = _body_of(message)
    if not body_html:
        return ParsedAlert(
            source=source,
            error=f"{path.name}: no HTML part — job-alert digests are HTML, so "
                  f"save the original email rather than a plain-text copy")

    jobs = parse_html(body_html, source=source)
    if not jobs:
        # Distinguishable from a parse failure on purpose: this one means the
        # file WAS read and genuinely had no job links in it.
        return ParsedAlert(source=source,
                           error=f"{path.name}: no job links found — is this a "
                                 f"job-alert email?")
    return ParsedAlert(jobs=jobs, source=source)


def parse_many(paths: list[Path]) -> tuple[list[Job], list[str]]:
    """Parse several files. Returns (deduplicated jobs, per-file problems).

    Dedup here is by the SAME exact key the feed uses, so a posting that
    arrives in both Tuesday's and Thursday's digest is one row.
    """
    jobs: list[Job] = []
    problems: list[str] = []
    seen: set[tuple[str, str]] = set()

    for path in paths:
        path = Path(path)
        if path.suffix.lower() == ".mbox":
            try:
                for message in mailbox.mbox(str(path)):
                    body_html, _ = _body_of(message)
                    for job in parse_html(body_html or "", source=path.name):
                        if job.dedup_key not in seen:
                            seen.add(job.dedup_key)
                            jobs.append(job)
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{path.name}: could not read the mbox "
                                f"({type(exc).__name__})")
            continue

        result = parse_eml(path)
        if not result.ok:
            problems.append(result.error)
            continue
        for job in result.jobs:
            if job.dedup_key not in seen:
                seen.add(job.dedup_key)
                jobs.append(job)

    return jobs, problems
