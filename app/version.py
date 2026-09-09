"""The marketing version, in one place.

WHY THIS EXISTS
---------------
It was in three, and they disagreed. On 2026-09-09 the app, the PyInstaller
spec and the direct-download builder were all moved to 1.1.0 — and
`packaging/msix/AppxManifest.xml` was not, because it is XML and nothing
pointed at it. CI then built a perfectly good Store package stamped
**1.0.2.0**, which is the version already live: Partner Center refuses a
package whose version is not higher than the published one, so the fix for a
release-blocking defect would have been rejected at upload, days after the
defect went live.

The same shape as the User-Agent constant in `app/core/http.py`: a value every
producer has to remember is not a single source of truth, it is a coincidence
that has held so far. `tests/test_version.py` fails if any of them drift.

FOUR PARTS FOR MSIX, THREE EVERYWHERE ELSE
------------------------------------------
An MSIX Identity version is `major.minor.build.revision` and the **revision
must be 0** for a Store package — Partner Center rejects a non-zero revision
because it reserves that field for its own re-signing. Apple wants the
three-part marketing string. Neither is negotiable, so both are derived here
rather than typed twice.
"""
from __future__ import annotations

import os

#: The marketing version. Bump this and nothing else.
VERSION = "1.1.0"


def marketing_version() -> str:
    """`1.1.0` — what a buyer sees, on every storefront.

    `DAWNLIST_VERSION` overrides it so a build can be cut without a commit;
    the default is the committed truth.
    """
    return os.environ.get("DAWNLIST_VERSION", VERSION)


def msix_version() -> str:
    """`1.1.0.0` — the four-part Identity version an MSIX package carries.

    The revision stays 0 deliberately; see the module docstring.
    """
    parts = marketing_version().split(".")
    while len(parts) < 3:
        parts.append("0")
    return ".".join(parts[:3] + ["0"])
