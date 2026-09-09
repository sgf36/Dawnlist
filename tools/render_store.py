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
    "03-board": "Every role you are pursuing, and what is due next",
    "04-understood": "It reads your CVs and shows you what it understood",
    "05-calibration": "It learns your judgement before it runs",
    "06-rules": "Screening you control, and it refuses a rule that would hide a real role",
}


def paint(widget, size=None):
    """Lay out and paint without mapping to the display.

    WA_DontShowOnScreen keeps real fonts and real styles while nothing appears
    on the developer's desktop. The offscreen platform plugin would also avoid
    the flash and renders every glyph as tofu.

    `size=None` RATHER THAN `size=SIZE`, and the difference is not stylistic.
    A default argument is evaluated once, when the `def` runs at import, so
    `size=SIZE` captures the module-level value forever and `--size` could
    reassign the global all it liked without changing a single pixel. It did
    exactly that: the flag reported success and produced identical images.
    """
    widget.setAttribute(Qt.WA_DontShowOnScreen, True)
    widget.resize(*(size or SIZE))
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
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--locale", default="en",
                    help="app locale AND fixture locale, e.g. de")
    ap.add_argument("--collect", action="store_true",
                    help="write tools/fixtures/en.json from what this render "
                         "actually asked for, then exit")
    ap.add_argument("--size", default=None, metavar="WxH",
                    help="logical render size, e.g. 1440x900 for the Mac App "
                         "Store. Apple accepts ONLY 1280x800, 1440x900, "
                         "2560x1600 and 2880x1800 — all 16:10, while the "
                         "default 1708x960 is 16:9, so the layout gets more "
                         "vertical room rather than being squeezed.")
    ap.add_argument("--out", default=None,
                    help="output directory, overriding store/screenshots")
    args, _rest = ap.parse_known_args()

    target = None
    if args.size:
        target = tuple(int(n) for n in args.size.lower().split("x"))

    # BOTH must be set, and they are different things. `app.i18n` translates
    # the CHROME — headings, buttons, column labels. `fixture_i18n` translates
    # the demo CONTENT — job titles, locations, verdicts. Setting only the
    # first gives German buttons around English job titles, which is the
    # half-finished look this whole mechanism exists to avoid.
    from app import i18n
    from tools import fixture_i18n
    i18n.set_locale(args.locale)
    i18n.clear_cache()
    # Collection forces a non-English fixture locale: with "en" the translator
    # short-circuits and records nothing, so a collect run would write an
    # empty table and report success.
    fixture_i18n.set_fixture_locale("de" if args.collect else args.locale)

    global OUT
    if args.out:
        OUT = Path(args.out)
    if args.locale != "en":
        OUT = OUT / args.locale

    app = QApplication(sys.argv)

    global SIZE
    if target:
        # --size IS THE OUTPUT SIZE, NOT THE LOGICAL ONE, and that distinction
        # is the whole reason this runs after QApplication exists.
        #
        # `widget.grab()` returns logical size TIMES the device pixel ratio.
        # This developer machine scales at 1.25, so asking for 1440x900
        # logically produced 1800x1125 — and Apple accepts ONLY 1280x800,
        # 1440x900, 2560x1600 and 2880x1800, exactly. A CI runner has a
        # different ratio again, so hardcoding a logical size makes the output
        # depend on which machine rendered it.
        #
        # Dividing by the real ratio lands on the requested pixels anywhere.
        dpr = app.primaryScreen().devicePixelRatio() or 1.0
        SIZE = (round(target[0] / dpr), round(target[1] / dpr))
        print(f"target {target[0]}x{target[1]} at dpr {dpr:g} "
              f"-> logical {SIZE[0]}x{SIZE[1]}")

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

    if args.collect:
        # Built from a REAL render rather than by grepping the renderers, so
        # the table cannot hold a string nothing displays, nor miss one that
        # does.
        import json
        from tools import fixture_i18n as fx
        asked = sorted(fx.asked())
        out = ROOT / "tools" / "fixtures"
        out.mkdir(parents=True, exist_ok=True)
        (out / "en.json").write_text(
            json.dumps({k: k for k in asked}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        print(f"collected {len(asked)} fixture strings -> tools/fixtures/en.json")
        return 0

    for p in written:
        try:
            print("wrote", p.relative_to(ROOT))
        except ValueError:
            print("wrote", p)   # --out may point outside the repo

    try:
        where = OUT.relative_to(ROOT)
    except ValueError:
        where = OUT        # --out may point outside the repo
    print(f"\n{len(written)} screenshots + CAPTIONS.md in {where}")

    if args.locale != "en":
        from tools import fixture_i18n as fx
        missing = fx.report_missing()
        if missing:
            print(f"\n{len(missing)} fixture string(s) FELL BACK TO ENGLISH:")
            for m in missing[:12]:
                print(f"  {m[:78]}")
            print("A half-translated screenshot is worse than an English one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
