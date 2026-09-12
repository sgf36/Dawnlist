"""Whether a newer DIRECT-DOWNLOAD release exists. It never installs anything.

    from app.core import updates
    found = updates.check()          # an Update, or None
    if found:
        ...show a prompt with found.url...

WHY THIS IS GATED ON THE BUILD VARIANT, AND WHY THAT IS A TEST
--------------------------------------------------------------
Only the direct download may check. A Microsoft Store build is updated by the
Store, and a Mac App Store build that reached out for its own updates would be
a rejection — Apple's review has no patience for an app that ships its own
update path around theirs, and Dawnlist has already been rejected once this
week on an automated metadata check that cost a full review cycle.

So `check()` returns None for `store`, `mas`, `none` and `ambiguous`, and
`tests/test_updates.py` asserts it for each rather than trusting this sentence.
A comment cannot fail CI.

WHAT IT WILL NOT DO
-------------------
It does not download the new build. It does not replace anything on disk. It
reads a small JSON file, compares two version strings and hands back a URL for
a person to click. Everything after the click is that person's decision, made
in their browser, against a signed artefact whose SHA-256 is published beside
it. An updater that swaps a binary underneath somebody is a thing to be trusted
with far more care than this one has earned.

IT IS SILENT ON EVERY FAILURE, AND THAT IS THE DESIGN
-----------------------------------------------------
No network, DNS gone, 404, HTML where JSON was expected, a version string that
does not parse, a manifest missing a key: all of them return None and say
nothing. There is no error dialog and no retry storm. The cost of a missed
check is that somebody stays on 1.1.0 a while longer. The cost of a noisy one
is an error box in front of a person who opened a job-search app at 7am because
a web server hiccupped. Those are not close.

THE USER-AGENT IS NOT OPTIONAL HERE
-----------------------------------
Measured against the live host on 2026-09-10:

    Dawnlist/1.1.0 (+https://dawnlist.spencerfields.com)  -> 200
    python-urllib/3.x   (urllib's default)                -> 406 Not Acceptable

The site refuses urllib's default agent outright. That is the same shape as the
Cloudflare 1010 that made every licence check fail in the shipped 1.0.0 build,
and it is why this goes through `app.core.http.build_request` like everything
else — `tests/test_http_client.py` fails the build if any module in `app/`
constructs a `urllib.request.Request` itself. Without that, this file would
have looked correct and returned None forever, which is indistinguishable from
"you are up to date".
"""
from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from app.core.build_variant import variant
from app.version import marketing_version

#: Published by the website. Versioned artefact names on purpose: a stale cache
#: of a "latest" URL is a silently wrong download, and the one thing this must
#: never do is send somebody to the wrong binary.
MANIFEST_URL = "https://dawnlist.spencerfields.com/updates/windows.json"

#: At most one check a day. The manifest is a few hundred bytes, but a desktop
#: app that phones home on every launch is a thing people are right to dislike.
CHECK_INTERVAL_SECONDS = 24 * 60 * 60

#: A manifest larger than this is not a manifest. Bounded before parsing so a
#: misconfigured host serving a large page cannot be read into memory.
MAX_MANIFEST_BYTES = 64 * 1024

#: The only host a download link may point at. The manifest arrives over TLS
#: from this site, but a manifest naming another host — a bad deploy, a typo,
#: an edited file — would put somebody else's binary behind a Dawnlist prompt.
#: "https://" alone allowed any host at all.
DOWNLOAD_HOST = "dawnlist.spencerfields.com"

_SHA256 = re.compile(r"[0-9a-fA-F]{64}")


@dataclass(frozen=True)
class Update:
    """A release newer than this one. `notes_url` may be None."""
    version: str
    url: str
    notes_url: str | None = None
    #: The published SHA-256, lower-case hex, shown in the prompt so the person
    #: can check the file they downloaded against it. None when not published.
    sha256: str | None = None
    #: The download's size in bytes, when the manifest states one.
    size: int | None = None


def _on_download_host(url: str) -> bool:
    """https, the exact host, the default port and no user-info.

    Parsed rather than matched on a prefix: `https://dawnlist.spencerfields.com@x`
    and `https://dawnlist.spencerfields.com.x` both START with the right text
    and go somewhere else.
    """
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    return (parts.scheme == "https" and parts.hostname == DOWNLOAD_HOST
            and parts.username is None and parts.password is None
            and port in (None, 443))


def parse_version(text: str) -> tuple[int, ...] | None:
    """`"1.2.3"` -> `(1, 2, 3)`. None when it is not a version.

    None rather than a guess. A manifest carrying "latest" or "v2.0-beta"
    should stop the comparison, not be coerced into something orderable — the
    failure mode of guessing is prompting every launch for an update that does
    not exist.
    """
    if not isinstance(text, str):
        return None
    parts = text.strip().split(".")
    if not 1 <= len(parts) <= 4:
        return None
    out = []
    for part in parts:
        if not part.isdigit():
            return None
        out.append(int(part))
    return tuple(out)


def is_newer(candidate: str, current: str) -> bool:
    """True only when both parse AND candidate is strictly greater.

    Unequal lengths compare as if zero-padded, so 1.2 and 1.2.0 are the same
    version and neither prompts against the other.
    """
    a, b = parse_version(candidate), parse_version(current)
    if a is None or b is None:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def _state_path() -> Path:
    """Where the last-checked stamp lives. Beside the database, per user."""
    from app.core.db import default_db_path
    return default_db_path().parent / "update-check.json"


def _due(state_path: Path, now: float) -> bool:
    """Whether enough time has passed — and NEVER on the very first run.

    A first launch already asks for an API key, a subscription and ten
    calibration verdicts. An update prompt on top of that is noise, and the
    build was downloaded minutes ago in any case. The absence of a stamp means
    first run: record the time and check tomorrow.
    """
    try:
        last = float(json.loads(state_path.read_text(encoding="utf-8"))["last"])
    except Exception:  # noqa: BLE001 - unreadable, absent or corrupt: same answer
        _stamp(state_path, now)
        return False
    return (now - last) >= CHECK_INTERVAL_SECONDS


def _stamp(state_path: Path, now: float) -> None:
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({"last": now}), encoding="utf-8")
    except Exception:  # noqa: BLE001 - a read-only profile must not break launch
        pass


def fetch_manifest(url: str = MANIFEST_URL, opener=None) -> dict | None:
    """The manifest as a dict, or None. Never raises.

    `opener` is injected by the suite so no test reaches the network — the
    same shape as `entitlement.licence_details`, and for the same reason: the
    1010 fault was invisible precisely because the transport was mocked at the
    wrong layer and the real request was never built.
    """
    import urllib.request

    from app.core.http import build_request

    request = build_request(url, headers={"accept": "application/json"})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=10) as response:
            raw = response.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            return None
        loaded = json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001 - see the module docstring on silence
        return None
    return loaded if isinstance(loaded, dict) else None


def check(*, current: str | None = None, url: str = MANIFEST_URL,
          opener=None, state_path: Path | None = None,
          now: float | None = None, force: bool = False) -> Update | None:
    """An `Update` when a newer direct-download release exists, else None.

    `force` skips the once-a-day gate for a "check now" button; it does NOT
    skip the variant gate, which nothing skips.
    """
    if variant() != "direct":
        return None
    # The manifest describes the WINDOWS download (windows.json). A direct
    # build anywhere else would be offered a Windows zip as its update.
    if sys.platform != "win32":
        return None

    now = time.time() if now is None else now
    path = state_path or _state_path()
    if not force and not _due(path, now):
        return None
    _stamp(path, now)

    manifest = fetch_manifest(url, opener=opener)
    if not manifest:
        return None

    version = manifest.get("version")
    download = manifest.get("url")
    if not isinstance(version, str) or not isinstance(download, str):
        return None
    # An http:// download would be a signed artefact fetched over a channel
    # that can be rewritten in flight, and any other host is not ours.
    if not _on_download_host(download):
        return None
    if not is_newer(version, current or marketing_version()):
        return None

    digest = manifest.get("sha256")
    if digest is not None:
        # Refused, not dropped: a malformed checksum would be shown as the
        # thing to verify the download against.
        if not (isinstance(digest, str) and _SHA256.fullmatch(digest)):
            return None
        digest = digest.lower()
    size = manifest.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        size = None

    notes = manifest.get("notes_url")
    return Update(version=version, url=download,
                  notes_url=notes if isinstance(notes, str)
                  and notes.startswith("https://") else None,
                  sha256=digest, size=size)
