"""Render the Mac App Store purchase screen, for the App Review screenshot.

WHY THIS EXISTS. The subscription sits at MISSING_METADATA until Apple has a
screenshot of the screen a customer actually buys on. That screen is
`SubscribePanel`, which only appears in a `mas` build. Capturing it the obvious
way is blocked: macOS refuses to let anything read the display without Screen
Recording permission, and that permission can only be granted by a human
clicking in System Settings — there is no headless route, `screencapture` over
SSH fails with "could not create image from display", and SIP puts the TCC
database out of reach.

Qt sidesteps the problem entirely. `QWidget.grab()` paints the widget into a
pixmap in-process; it never reads the screen, so no permission is involved and
no window ever has to be shown. This is the same mechanism
`tools/render_store.py` already uses for the store set.

RUN IT ON A MAC. The whole value is macOS fonts and macOS control styling.
Rendering on Windows produces a picture of a Mac app that visibly is not one,
which is exactly the flaw `tools/asc_screenshots.py` warns about.

THE PRICE IS NOT INVENTED. It is passed in, and it must match the price
configured in App Store Connect for the storefront the reviewer sees — $79.00
in the USA. Showing a price that differs from what the App Store charges is a
rejection, so this refuses to run without one.

    python tools/render_subscribe.py --price '$79.00' --out dist/review
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication  # noqa: E402


class _Storekit:
    """What StoreKit would report on a Mac that can take the purchase.

    NOT A MOCK OF A HAPPY PATH FOR ITS OWN SAKE. The reviewer needs to see the
    screen as a customer sees it: a price, an enabled Subscribe button, and the
    Restore button Apple requires. `refresh()` reads exactly these three, and a
    panel rendered with `available()` False would instead show "The App Store
    cannot take a purchase on this Mac right now" — true on a build machine,
    and useless as evidence of where the purchase happens.
    """

    def __init__(self, price: str) -> None:
        self._price = price

    def available(self) -> bool:
        return True

    def can_make_payments(self) -> bool:
        return True

    def price(self) -> str:
        return self._price


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--price", required=True,
                    help="exactly as App Store Connect has it, e.g. '$79.00'")
    ap.add_argument("--locale", default="en")
    ap.add_argument("--size", default="1280x800", metavar="WxH",
                    help="OUTPUT pixels; divided by the device pixel ratio")
    ap.add_argument("--out", default="dist/review")
    args = ap.parse_args()

    target = tuple(int(n) for n in args.size.lower().split("x"))

    from app import i18n
    i18n.set_locale(args.locale)
    i18n.clear_cache()

    app = QApplication(sys.argv)

    # `grab()` returns logical size TIMES the device pixel ratio, and runners
    # differ, so hardcoding a logical size makes the output depend on the
    # machine. Same correction as render_store.py.
    dpr = app.primaryScreen().devicePixelRatio() or 1.0
    logical = (round(target[0] / dpr), round(target[1] / dpr))
    print(f"target {target[0]}x{target[1]} at dpr {dpr:g} "
          f"-> logical {logical[0]}x{logical[1]}")

    from app.ui.settings import SETTINGS_STYLESHEET, SubscribePanel
    panel = SubscribePanel(storekit=_Storekit(args.price))
    panel.setStyleSheet(SETTINGS_STYLESHEET)
    # Top-align, as the panel sits in the real Settings window. Without this
    # the layout distributes its rows down the full height and the screenshot
    # shows the same controls floating in unexplained gaps.
    panel.layout().addStretch(1)
    panel.resize(*logical)
    for _ in range(6):
        app.processEvents()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "subscription-review.png"
    shot = panel.grab()
    shot.save(str(path))
    print(f"wrote {path} at {shot.width()}x{shot.height()}")

    # A blank render is the failure this is most likely to have, and it would
    # sail through as a valid PNG. Refuse it here rather than at Apple.
    img = shot.toImage()
    colours = {img.pixel(x, y)
               for x in range(0, img.width(), max(1, img.width() // 40))
               for y in range(0, img.height(), max(1, img.height() // 40))}
    if len(colours) < 3:
        sys.exit(f"render looks blank ({len(colours)} distinct colours)")
    print(f"sanity: {len(colours)} distinct colours sampled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
