"""Reading a CV corpus off disk.

The failure this module exists to prevent is not "the file would not open" —
that is loud and the user fixes it. It is the **scanned CV**: a PDF that opens
perfectly, has pages, and yields almost no text because every page is an image.
Treated naively it becomes an empty document, joins the corpus, and quietly
contributes nothing to the factsheet. The user is then told their factsheet is
thin and has no idea which file was ignored.

So every file that cannot be read is reported BY NAME with a reason the user
can act on, and a text-poor PDF is diagnosed as a probable scan rather than as
an empty one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.i18n import tr
from app.onboarding.interview import Corpus, CVDocument

SUPPORTED_SUFFIXES = {".pdf", ".docx", ".txt", ".md", ".rtf"}

#: Below this many characters per page, a PDF is almost certainly a scan.
#: A typical CV page carries well over a thousand; a scanned page yields a
#: handful of stray characters from headers or OCR artefacts, not zero.
SCAN_CHARS_PER_PAGE = 120

MB = 1024 * 1024

#: WHAT THESE LIMITS ARE FOR. This module opens files a person dropped onto the
#: window, and both readers below will happily work until memory runs out: a
#: .docx is a zip, so a few hundred kilobytes can unpack to gigabytes, and
#: pypdf will walk a hundred thousand pages one at a time without complaining.
#: Setup then dies, or the machine does, with no message naming a file — and
#: the whole design of this module is that an unreadable file is named.
#:
#: The numbers come from what a CV is, not from what an attacker might send: a
#: long illustrated CV exported from Word runs to a couple of megabytes and a
#: dozen pages, so every limit here is at least an order of magnitude clear of
#: any real document.
MAX_FILE_BYTES = 25 * MB
MAX_PDF_PAGES = 50
MAX_DOCX_UNPACKED_BYTES = 200 * MB

#: A member that expands by more than this is not a document part. Applied only
#: above the floor, because small XML parts legitimately compress enormously —
#: it is the combination of large AND absurdly compressible that never occurs
#: in a real .docx.
MAX_DOCX_RATIO = 100
DOCX_RATIO_FLOOR_BYTES = 1 * MB


@dataclass(frozen=True)
class ExtractionFailure:
    name: str
    reason: str

    def __str__(self) -> str:
        return f"{self.name}: {self.reason}"


@dataclass
class ExtractionResult:
    corpus: Corpus
    failures: list[ExtractionFailure]

    @property
    def warnings(self) -> list[str]:
        """Everything the user should see: corpus-level plus per-file."""
        return [str(f) for f in self.failures] + self.corpus.warnings


def _clean(text: str) -> str:
    """Normalise whitespace without destroying paragraph structure.

    Line breaks inside a CV bullet are noise; blank lines between roles are
    signal. Collapsing everything would merge separate roles into one run of
    text and cost the factsheet its structure.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _read_pdf(path: Path) -> tuple[str, str | None]:
    """Returns (text, failure_reason)."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return "", "pypdf is not installed"

    try:
        reader = PdfReader(str(path))
    except Exception as exc:  # noqa: BLE001
        return "", f"could not open the PDF ({type(exc).__name__})"

    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")
        except Exception:  # noqa: BLE001
            return "", "the PDF is password-protected"

    pages = len(reader.pages)
    if not pages:
        return "", "the PDF has no pages"
    if pages > MAX_PDF_PAGES:
        # Counted BEFORE any page is read. `extract_text` is the expensive
        # call, so a page count is the cheap place to stop.
        return "", tr("onboarding.pdf_too_many_pages", pages=pages,
                      limit=MAX_PDF_PAGES)

    chunks = []
    for page in reader.pages:
        try:
            chunks.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - one bad page must not lose the rest
            continue
    text = _clean("\n\n".join(chunks))

    if len(text) < SCAN_CHARS_PER_PAGE * pages:
        # The important case. Named specifically, because "it did not work" is
        # useless and "this looks like a scan" tells the user what to do.
        return text, (
            f"this looks like a scanned PDF — {len(text)} characters across "
            f"{pages} page{'s' if pages != 1 else ''}. Text cannot be read from "
            f"an image. Export a text PDF from the original document, or add "
            f"the .docx instead")
    return text, None


def _docx_unpacks_safely(path: Path) -> bool:
    """Expand the archive, under a cap, before python-docx is let near it.

    THE DECLARED SIZES ARE NOT CHECKED, because they are a claim the archive
    makes about itself and a hostile one simply lies. This reads each member
    and counts the bytes that actually come out, stopping the moment the total
    passes the cap — so the worst case is bounded by the cap rather than by
    whatever the header says.

    A file that is not a readable zip is not judged here: `docx.Document` names
    it far better than "unpacks to too much" would.
    """
    import zipfile

    try:
        with zipfile.ZipFile(path) as bundle:
            total = 0
            for info in bundle.infolist():
                unpacked = 0
                with bundle.open(info) as member:
                    while chunk := member.read(MB):
                        unpacked += len(chunk)
                        total += len(chunk)
                        if total > MAX_DOCX_UNPACKED_BYTES:
                            return False
                if (unpacked >= DOCX_RATIO_FLOOR_BYTES and info.compress_size
                        and unpacked / info.compress_size > MAX_DOCX_RATIO):
                    return False
    except zipfile.BadZipFile:
        return True
    except Exception:  # noqa: BLE001 - let the reader below name the problem
        return True
    return True


def _read_docx(path: Path) -> tuple[str, str | None]:
    try:
        import docx
    except ImportError:
        return "", "python-docx is not installed"

    if not _docx_unpacks_safely(path):
        return "", tr("onboarding.docx_unpacks_too_big")

    try:
        document = docx.Document(str(path))
    except Exception as exc:  # noqa: BLE001
        return "", f"could not open the document ({type(exc).__name__})"

    parts = [p.text for p in document.paragraphs]
    # CVs very often put dates and employers in a table, and a paragraph-only
    # read silently drops every one of them.
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return _clean("\n".join(parts)), None


def _read_text(path: Path) -> tuple[str, str | None]:
    for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return _clean(path.read_text(encoding=encoding)), None
        except (UnicodeDecodeError, LookupError):
            continue
        except Exception as exc:  # noqa: BLE001
            return "", f"could not read the file ({type(exc).__name__})"
    return "", "could not decode the text in any common encoding"


def extract_one(path: Path) -> tuple[CVDocument | None, ExtractionFailure | None]:
    """Read one file. A failure is returned, never raised and never swallowed."""
    if not path.exists():
        return None, ExtractionFailure(path.name, "the file no longer exists")

    suffix = path.suffix.lower()
    if suffix == ".doc":
        return None, ExtractionFailure(
            path.name,
            "old .doc files cannot be read — open it and save as .docx first")
    if suffix not in SUPPORTED_SUFFIXES:
        return None, ExtractionFailure(
            path.name,
            f"unsupported file type {suffix or '(none)'} — "
            f"use PDF, .docx or plain text")

    # Size first, for every type, because each reader below loads the whole
    # file: `read_text` into one string, pypdf and python-docx into their own
    # object graphs. A person dropping the wrong file — a disk image, a video —
    # is far commoner than an attack, and both end the same way without this.
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        return None, ExtractionFailure(
            path.name,
            tr("onboarding.file_too_large", size=round(size / MB),
               limit=round(MAX_FILE_BYTES / MB)))

    if suffix == ".pdf":
        text, reason = _read_pdf(path)
    elif suffix == ".docx":
        text, reason = _read_docx(path)
    else:
        text, reason = _read_text(path)

    if reason:
        return None, ExtractionFailure(path.name, reason)
    if not text.strip():
        return None, ExtractionFailure(path.name, "no text could be read")

    return CVDocument(name=path.name, text=text), None


def extract_corpus(paths: list[Path]) -> ExtractionResult:
    """Read every file. One unreadable file never stops the others."""
    documents: list[CVDocument] = []
    failures: list[ExtractionFailure] = []

    for path in paths:
        document, failure = extract_one(Path(path))
        if document is not None:
            documents.append(document)
        if failure is not None:
            failures.append(failure)

    return ExtractionResult(corpus=Corpus(documents=documents),
                            failures=failures)


def gather(folder: Path) -> list[Path]:
    """Every candidate file in a folder the user dropped."""
    return sorted(p for p in Path(folder).rglob("*")
                  if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
                  and not p.name.startswith("~$"))   # Word lock files
