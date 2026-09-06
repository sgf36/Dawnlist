"""Render the ingest page, with and without extraction warnings."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui.onboarding import IngestPage  # noqa: E402

# Microsoft Store: 1366x768 or larger, 16:9.
STORE_SIZE = (1366, 768)

app = QApplication(sys.argv)
docs = Path(__file__).resolve().parents[1] / "docs"

page = IngestPage()
page.show_corpus(
    ["Spencer-Fields-CV-2026.docx", "Spencer-Fields-CV-hospitality.docx",
     "Spencer-Fields-CV-2019.pdf", "Switzerland-CV-April-2026.docx"],
    ["old-cv-scanned.pdf: this looks like a scanned PDF — 41 characters "
     "across 2 pages. Text cannot be read from an image. Export a text PDF "
     "from the original document, or add the .docx instead",
     "resume.doc: old .doc files cannot be read — open it and save as .docx first"],
)
page.resize(*STORE_SIZE)
page.show()
for _ in range(8):
    app.processEvents()
page.grab().save(str(docs / "onboarding-ingest.png"))
print("wrote", docs / "onboarding-ingest.png")
