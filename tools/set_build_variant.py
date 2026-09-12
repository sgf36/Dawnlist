"""Select the build variant before running PyInstaller.

    python tools/set_build_variant.py store     # Microsoft Store
    python tools/set_build_variant.py mas       # Mac App Store
    python tools/set_build_variant.py direct    # direct download
    python tools/set_build_variant.py --show

The variants are mutually exclusive and are signalled by a flag file that the
PyInstaller spec bundles conditionally. This script exists so exactly one is
ever present.

Leaving a flag lying around is the failure worth preventing: once
`store_build.flag` exists in the tree, EVERY later build picks it up, including
the direct-download one — silently, with no error, and only discoverable by
unpacking the artefact. The sibling app shipped a variant of this: build flags
were created in the source tree but never bundled, and the paid build launched
with no licence gate at all.

So this writes one flag and DELETES the others, every time.
"""
from __future__ import annotations

import sys
from pathlib import Path

RESOURCES = Path(__file__).resolve().parents[1] / "app" / "resources"

VARIANTS = {
    "store": "store_build.flag",     # Microsoft Store: Paddle licence key
    "mas": "mas_build.flag",         # Mac App Store: StoreKit subscription
    "direct": "license_required.flag",  # direct download: Paddle licence key
}


def current() -> list[str]:
    return [name for name, flag in VARIANTS.items()
            if (RESOURCES / flag).exists()]


def show() -> int:
    present = current()
    if not present:
        print("variant: none set — the build would carry no variant flag")
        return 1
    if len(present) > 1:
        # Impossible via this script, possible if someone made one by hand.
        print(f"variant: AMBIGUOUS — {', '.join(present)} are all present",
              file=sys.stderr)
        return 1
    print(f"variant: {present[0]}  ({VARIANTS[present[0]]})")
    return 0


def set_variant(name: str) -> int:
    if name not in VARIANTS:
        print(f"unknown variant {name!r}; expected one of "
              f"{', '.join(VARIANTS)}", file=sys.stderr)
        return 2

    RESOURCES.mkdir(parents=True, exist_ok=True)
    for other, flag in VARIANTS.items():
        path = RESOURCES / flag
        if other == name:
            path.write_text(
                f"{name}\n"
                "# Written by tools/set_build_variant.py. Not committed: a\n"
                "# stray flag makes every later build this variant silently.\n",
                encoding="utf-8")
        elif path.exists():
            path.unlink()

    print(f"variant set to {name} ({VARIANTS[name]})")
    print("Now rebuild — the flag is read at BUILD time, not at run time:")
    print("  .venv\\Scripts\\python.exe -m PyInstaller packaging\\build_exe.spec --noconfirm")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("--show", "-s"):
        return show()
    return set_variant(argv[0].lower())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
