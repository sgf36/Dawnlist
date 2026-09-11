"""Which build this is, read from the bundled flag.

The flag is chosen at BUILD time by `tools/set_build_variant.py` and bundled by
the PyInstaller spec. This module is the only thing that reads it, so the
question "which variant am I?" has one answer rather than several.

`entitlement.check()` reads `variant()` to decide WHICH purchase to ask about —
a Paddle licence on `store` and `direct`, Apple's subscription on `mas` — and
refuses `none` and `ambiguous` outright. No variant is entitled by possession:
both store listings are free, so having the app proves nothing was paid.

The variant is also OBSERVABLE: `--doctor` reports it, because a Store package
that is silently the direct-download build is exactly the failure the flags are
for, and an unbundled flag is invisible without something that looks for it.
"""
from __future__ import annotations

from pathlib import Path

RESOURCES = Path(__file__).resolve().parent.parent / "resources"

FLAGS = {
    "store": "store_build.flag",
    "mas": "mas_build.flag",
    "direct": "license_required.flag",
}


def variant() -> str:
    """'store', 'mas', 'direct', 'none' or 'ambiguous'.

    Ambiguous is reported rather than resolved: two flags means the build was
    made wrong, and picking one would hide that.
    """
    present = [name for name, flag in FLAGS.items() if (RESOURCES / flag).exists()]
    if not present:
        return "none"
    if len(present) > 1:
        return "ambiguous"
    return present[0]


def is_store_build() -> bool:
    return variant() == "store"
