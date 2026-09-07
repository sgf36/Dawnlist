"""The Microsoft Store screenshot set, numbered, in one pass.

One QApplication for all of them, so fonts and styles resolve identically
across the set — separate processes can pick up different DPI rounding and the
mismatch only shows when the images sit side by side in the listing.

Every image comes from the real widget with real content. The listing's own
instruction is "take them from the real app, not a mockup", and the rule has
teeth: it is why there is no screenshot of a draft. Drafts are `.eml` files
opened in the user's mail client, so there is no Dawnlist screen to photograph,
and inventing one would promise a screen that does not exist.

    .venv/Scripts/python tools/render_store.py
"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "store" / "screenshots"

#: 1366x768 logical at 1.25 display scaling, so the files land at 2135x1200 —
#: well above the Store's 1366x768 minimum, which is the only dimension rule
#: the Store actually documents for desktop screenshots.
#:
#: 2135x1200 is 1.779, and true 16:9 is 1.778. Earlier text here claimed this
#: was "16:9 to three decimal places" and that a near-miss ratio gets
#: letterboxed; neither was verified and the first is arithmetically wrong.
#: The size is left alone because the layouts are tuned at 1708x960 and the
#: Store imposes no ratio — but do not restate the 16:9 claim.
SIZE = (1708, 960)

CAPTIONS = {
    "01-shortlist": "Your shortlist, with the reasons — and the rejections still visible",
    "02-needs-review": "When a rule reaches too far, it tells you",
    "03-board": "Every company you are pursuing, and what is due next",
    "04-understood": "It reads your CVs and shows you what it understood",
    "05-calibration": "It learns your judgement before it runs",
    "06-rules": "Screening you control, and it refuses a rule that would hide a real role",
}


def paint(widget, size=SIZE):
    """Lay out and paint without mapping to the display.

    WA_DontShowOnScreen keeps real fonts and real styles while nothing appears
    on the developer's desktop. The offscreen platform plugin would also avoid
    the flash and renders every glyph as tofu.
    """
    widget.setAttribute(Qt.WA_DontShowOnScreen, True)
    widget.resize(*size)
    widget.show()
    for _ in range(8):
        QApplication.instance().processEvents()
    return widget


def save(widget, name):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.png"
    widget.grab().save(str(path))
    return path


def main() -> int:
    app = QApplication(sys.argv)
    written = []

    # 1-2. The review window, twice: the shortlist, then the containment tab.
    from render_ui import build_review
    win = build_review()
    paint(win)
    written.append(save(win, "01-shortlist"))

    # The tab whose whole point is that an over-reaching rule is visible.
    win.tabs.setCurrentWidget(win.contained)
    for _ in range(6):
        app.processEvents()
    written.append(save(win, "02-needs-review"))
    win.close()

    # 3. The board.
    from render_board import build_board
    written.append(save(paint(build_board()), "03-board"))

    # 4. The interview: what it understood from the CVs.
    from render_interview import build_interview
    written.append(save(paint(build_interview()), "04-understood"))

    # 5. The calibration gate, part-way through.
    from render_onboarding import build_calibration
    written.append(save(paint(build_calibration()), "05-calibration"))

    # 6. The screening rules, showing a refused term.
    from render_rules import build_rules
    written.append(save(paint(build_rules()), "06-rules"))

    (OUT / "CAPTIONS.md").write_text(
        "# Store screenshot captions\n\n"
        "Upload in this order. Captions are en-GB.\n\n"
        + "\n".join(f"{i}. **{n}.png** — {CAPTIONS[n]}"
                    for i, n in enumerate(sorted(CAPTIONS), 1))
        + "\n\nThere is deliberately no screenshot of a draft: drafts are .eml\n"
          "files opened in the user's own mail client, so there is no Dawnlist\n"
          "screen to photograph and a mockup would promise one that does not\n"
          "exist.\n",
        encoding="utf-8")

    for p in written:
        print("wrote", p.relative_to(ROOT))
    print(f"\n{len(written)} screenshots + CAPTIONS.md in {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
