"""Strip invisible Unicode markers from LLM-generated text.

Layer A deterministic cleaning: removes zero-width characters, variation
selectors, tag characters, and other invisible codepoints that AI systems
embed as steganographic watermarks.  Does NOT normalize spaces (preserves
NBSP and CJK ideographic space for email layout).

Uses the vendored text_unicode module from watermarks-remover
(https://github.com/guillaumemeyer/watermarks-remover).
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / ".claude" / "skills" / "clean-user-facing-text" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from text_unicode import clean_text as _clean_unicode  # noqa: E402


def strip_invisible_unicode(text: str) -> str:
    """Remove invisible Unicode carriers from *text*, preserving layout spaces."""
    if not text:
        return text
    cleaned, _stats = _clean_unicode(text, normalize_spaces=False)
    return cleaned
