"""Compose marketing-style Store screenshots from rendered app images.

Each output is a branded composite:
  * Dawnlist brand background (CREAM base with TEAL accents)
  * A marketing headline above the app screenshot
  * The app screenshot inside a macOS-style window frame (title bar + shadow)
  * Correct dimensions for each store

Apple Mac App Store: 2560x1600 (16:10)
Microsoft Store: no strict ratio, but the plain renders are already fine —
  this script produces composites for Apple and optionally for Microsoft.

    python tools/compose_marketing.py [--out DIR] [--store apple|microsoft|both]
        [--size WxH] [--locale LOCALE]

Without --size, defaults to 2560x1600 (Apple).

Brand palette (from brand-dawnlist):
    TEAL      = #1E4B45
    GOLD_DEEP = #B07A2E
    CREAM     = #F0ECE4
    INK       = #16212A
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SHOTS_DIR = ROOT / "store" / "screenshots"

# Brand palette
TEAL = (0x1E, 0x4B, 0x45)
GOLD = (0xB0, 0x7A, 0x2E)
CREAM = (0xF0, 0xEC, 0xE4)
INK = (0x16, 0x21, 0x2A)

# Window chrome colours
CHROME_BG = (0xEC, 0xE8, 0xE1)    # Warm grey, slightly darker than CREAM
TRAFFIC_RED = (0xFF, 0x5F, 0x57)
TRAFFIC_YELLOW = (0xFE, 0xBC, 0x2E)
TRAFFIC_GREEN = (0x28, 0xC8, 0x40)

# Headlines — punchy, one line, under 45 chars
HEADLINES: dict[str, str] = {
    "01-shortlist": "Your shortlist, with the reasons",
    "02-needs-review": "It catches its own mistakes",
    "03-board": "Every company, every stage",
    "04-understood": "It reads your CVs first",
    "05-calibration": "It learns your judgement",
    "06-rules": "Rules you actually control",
    "07-settings": "Import, export, test — all here",
}

APPLE_SIZE = (2560, 1600)
MS_SIZE = (2135, 1200)

# Font paths — Segoe UI on Windows; fall back to Arial
FONT_BOLD = None
FONT_LIGHT = None
for candidate_bold in [
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]:
    try:
        ImageFont.truetype(candidate_bold, 12)
        FONT_BOLD = candidate_bold
        break
    except OSError:
        pass

for candidate_light in [
    "C:/Windows/Fonts/segoeuil.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
]:
    try:
        ImageFont.truetype(candidate_light, 12)
        FONT_LIGHT = candidate_light
        break
    except OSError:
        pass


def draw_traffic_lights(draw: ImageDraw.ImageDraw, x: int, y: int,
                        radius: int = 8, spacing: int = 22):
    """The three macOS window-control circles."""
    for i, colour in enumerate((TRAFFIC_RED, TRAFFIC_YELLOW, TRAFFIC_GREEN)):
        cx = x + i * spacing
        draw.ellipse([cx - radius, y - radius, cx + radius, y + radius],
                     fill=colour)


def add_window_frame(screenshot: Image.Image, *,
                     title_bar_h: int = 40,
                     corner_r: int = 12,
                     shadow_offset: int = 8,
                     shadow_blur: int = 20) -> Image.Image:
    """Wrap a screenshot in a macOS-style window frame with a drop shadow."""
    sw, sh = screenshot.size
    framed_w = sw
    framed_h = sh + title_bar_h

    # Build the framed window
    frame = Image.new("RGBA", (framed_w, framed_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(frame)

    # Title bar
    draw.rounded_rectangle(
        [0, 0, framed_w - 1, framed_h - 1],
        radius=corner_r, fill=(*CHROME_BG, 255))

    # Traffic lights
    draw_traffic_lights(draw, 20, title_bar_h // 2, radius=7, spacing=20)

    # Paste the screenshot below the title bar
    frame.paste(screenshot, (0, title_bar_h))

    # Clip bottom corners — draw the bottom portion with rounded rect
    # Actually, just mask the whole thing with a rounded rect
    mask = Image.new("L", (framed_w, framed_h), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle([0, 0, framed_w - 1, framed_h - 1],
                                radius=corner_r, fill=255)
    frame.putalpha(mask)

    # Build a shadow
    shadow_canvas = Image.new("RGBA",
                              (framed_w + shadow_blur * 4,
                               framed_h + shadow_blur * 4),
                              (0, 0, 0, 0))
    shadow_rect = Image.new("RGBA", (framed_w, framed_h), (0, 0, 0, 60))
    shadow_rect.putalpha(mask)
    shadow_canvas.paste(shadow_rect,
                        (shadow_blur * 2 + shadow_offset,
                         shadow_blur * 2 + shadow_offset))
    shadow_canvas = shadow_canvas.filter(ImageFilter.GaussianBlur(shadow_blur))

    # Composite: shadow first, then framed window on top
    shadow_canvas.paste(frame, (shadow_blur * 2, shadow_blur * 2), frame)
    return shadow_canvas


def compose_one(screenshot_path: Path, headline: str, *,
                output_size: tuple[int, int] = APPLE_SIZE) -> Image.Image:
    """Create a marketing composite for one screenshot."""
    out_w, out_h = output_size

    # Background
    canvas = Image.new("RGB", (out_w, out_h), CREAM)
    draw = ImageDraw.Draw(canvas)

    # A subtle gradient accent: thin gold line near the top
    accent_y = int(out_h * 0.02)
    draw.line([(int(out_w * 0.15), accent_y),
               (int(out_w * 0.85), accent_y)],
              fill=GOLD, width=3)

    # Headline text
    headline_font_size = int(out_h * 0.055)  # ~88px at 1600h
    try:
        font = ImageFont.truetype(FONT_BOLD, headline_font_size)
    except (OSError, TypeError):
        font = ImageFont.load_default()

    # Centre the headline in the top portion
    text_bbox = draw.textbbox((0, 0), headline, font=font)
    text_w = text_bbox[2] - text_bbox[0]
    text_x = (out_w - text_w) // 2
    text_y = int(out_h * 0.06)

    draw.text((text_x, text_y), headline, fill=TEAL, font=font)

    # Load and frame the screenshot
    shot = Image.open(screenshot_path).convert("RGBA")

    # Scale the screenshot to fit ~85% of the canvas width,
    # leaving room for the headline and margins
    max_shot_w = int(out_w * 0.85)
    max_shot_h = int(out_h * 0.72)  # Leave room for headline + margins
    scale = min(max_shot_w / shot.width, max_shot_h / shot.height)
    new_w = int(shot.width * scale)
    new_h = int(shot.height * scale)
    shot = shot.resize((new_w, new_h), Image.LANCZOS)

    # Add the window frame
    title_bar_h = int(32 * scale)
    framed = add_window_frame(shot, title_bar_h=max(title_bar_h, 28),
                              corner_r=10, shadow_offset=6, shadow_blur=16)

    # Centre the framed image below the headline
    frame_x = (out_w - framed.width) // 2
    frame_y = int(out_h * 0.18)

    # Paste with alpha
    canvas_rgba = canvas.convert("RGBA")
    canvas_rgba.paste(framed, (frame_x, frame_y), framed)
    return canvas_rgba.convert("RGB")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None, help="output directory")
    ap.add_argument("--size", default=None, metavar="WxH",
                    help="output size, e.g. 2560x1600")
    ap.add_argument("--locale", default="en",
                    help="locale subfolder to read from")
    ap.add_argument("--store", default="apple",
                    choices=["apple", "microsoft", "both"],
                    help="which store's dimensions to target")
    args = ap.parse_args()

    shots_dir = SHOTS_DIR
    if args.locale != "en":
        shots_dir = SHOTS_DIR / args.locale

    if args.size:
        size = tuple(int(n) for n in args.size.lower().split("x"))
    elif args.store == "microsoft":
        size = MS_SIZE
    else:
        size = APPLE_SIZE

    out_dir = Path(args.out) if args.out else ROOT / "store" / "marketing"
    out_dir.mkdir(parents=True, exist_ok=True)

    written = []
    for stem, headline in HEADLINES.items():
        src = shots_dir / f"{stem}.png"
        if not src.exists():
            print(f"SKIP {stem}: {src} not found")
            continue

        result = compose_one(src, headline, output_size=size)
        out_path = out_dir / f"{stem}-marketing.png"
        result.save(str(out_path), "PNG")
        written.append(out_path)
        print(f"wrote {out_path.relative_to(ROOT)}")

    print(f"\n{len(written)} marketing composites in {out_dir.relative_to(ROOT)}")
    print(f"  size: {size[0]}x{size[1]}")

    if args.store == "both":
        # Also produce at Microsoft size
        ms_out = out_dir / "microsoft"
        ms_out.mkdir(parents=True, exist_ok=True)
        for stem, headline in HEADLINES.items():
            src = shots_dir / f"{stem}.png"
            if not src.exists():
                continue
            result = compose_one(src, headline, output_size=MS_SIZE)
            out_path = ms_out / f"{stem}-marketing.png"
            result.save(str(out_path), "PNG")
        print(f"  also wrote Microsoft-size set in {ms_out.relative_to(ROOT)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
