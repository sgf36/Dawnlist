"""Deduplication (spec 6.6, invariant 9).

Two failure modes, and they are not symmetrical:

  * double-surfacing the same posting is noise;
  * silently collapsing two different postings DISCARDS REAL WORK, and is the
    worse one.

So: the exact key `(provider, provider_job_id)` is the only thing allowed to
drop a row. Name keys never drop anything — they raise a flag for a human.

Cross-provider merge (added for the LinkedIn Job Library adapter): when two
providers surface the same real-world posting, keep whichever version carries
more useful data and suppress the other.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date

from app.feed.models import Job, name_key


@dataclass(frozen=True)
class NearDuplicate:
    job: Job
    other: Job
    reason: str


@dataclass
class DedupResult:
    unique: list[Job]
    exact_duplicates: list[Job]
    near_duplicates: list[NearDuplicate]

    @property
    def counts(self) -> dict[str, int]:
        return {
            "in": len(self.unique) + len(self.exact_duplicates),
            "unique": len(self.unique),
            "exact_duplicates": len(self.exact_duplicates),
            "near_duplicates_flagged": len(self.near_duplicates),
        }


def dedup(jobs: list[Job], *, already_seen: set[tuple[str, str]] | None = None) -> DedupResult:
    """Drop exact duplicates; FLAG near-duplicates; never merge."""
    already_seen = already_seen or set()
    unique: list[Job] = []
    exact: list[Job] = []
    near: list[NearDuplicate] = []
    by_key: dict[tuple[str, str], Job] = {}
    by_name: dict[str, Job] = {}

    for job in jobs:
        key = job.dedup_key
        if key in by_key or key in already_seen:
            exact.append(job)
            continue

        nk = name_key(job.company, job.title)
        twin = by_name.get(nk)
        if twin is not None and twin.dedup_key != key:
            # Same normalised company+title, different provider id. Could be
            # the same posting relisted, could be two genuine openings. Not our
            # call to make silently.
            near.append(NearDuplicate(
                job, twin,
                f"same name key {nk!r} as {twin.provider}:{twin.provider_job_id}"))

        by_key[key] = job
        by_name.setdefault(nk, job)
        unique.append(job)

    return DedupResult(unique=unique, exact_duplicates=exact, near_duplicates=near)


@dataclass
class CrossProviderResult:
    kept: list[Job]
    superseded: list[tuple[Job, str]]


def _db_job(row: sqlite3.Row) -> Job:
    """Reconstruct a Job from a jobs-table row for richness comparison."""
    locs_raw = row["locations_json"] or "[]"
    try:
        locs = tuple(json.loads(locs_raw))
    except (json.JSONDecodeError, TypeError):
        locs = ()
    raw = {}
    try:
        raw = json.loads(row["raw_criteria_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        pass
    pa = None
    if row["posted_at"]:
        try:
            pa = date.fromisoformat(row["posted_at"])
        except ValueError:
            pass
    return Job(
        provider=row["provider"],
        provider_job_id=row["provider_job_id"],
        title=row["title"],
        company=row["company"],
        locations=locs,
        description_text=row["description_text"] or "",
        posted_at=pa,
        salary=row["salary"],
        url=row["url"] or "",
        raw_criteria=raw,
    )


def cross_provider_merge(
    jobs: list[Job],
    conn: sqlite3.Connection,
) -> CrossProviderResult:
    """Keep whichever version of a posting — new or stored — is richer.

    Matches by name_key across different providers. Within the same provider
    this is a no-op (the exact dedup already handled it).
    """
    if not jobs:
        return CrossProviderResult(kept=[], superseded=[])

    nk_to_new: dict[str, list[Job]] = {}
    for j in jobs:
        nk = name_key(j.company, j.title)
        nk_to_new.setdefault(nk, []).append(j)

    nks = list(nk_to_new.keys())
    existing: dict[str, Job] = {}
    # SQLite has a 999-param limit; batch if needed.
    for i in range(0, len(nks), 500):
        batch = nks[i:i + 500]
        placeholders = ",".join("?" * len(batch))
        for row in conn.execute(
                f"SELECT * FROM jobs WHERE name_key IN ({placeholders})",
                batch):
            existing[row["name_key"]] = _db_job(row)

    kept: list[Job] = []
    superseded: list[tuple[Job, str]] = []
    for j in jobs:
        nk = name_key(j.company, j.title)
        stored = existing.get(nk)
        if stored is None or stored.provider == j.provider:
            kept.append(j)
            continue
        if j.richness_score >= stored.richness_score:
            kept.append(j)
            superseded.append(
                (j, f"supersedes {stored.provider}:{stored.provider_job_id} "
                    f"(score {j.richness_score} >= {stored.richness_score})"))
        else:
            superseded.append(
                (j, f"suppressed by {stored.provider}:{stored.provider_job_id} "
                    f"(score {j.richness_score} < {stored.richness_score})"))

    return CrossProviderResult(kept=kept, superseded=superseded)


def recover_stranded(rows: list[dict],
                     decided_ids: set[tuple[str, str]]) -> list[dict]:
    """Recover an unregistered output file (spec 6.5).

    Two rules, both learned from the real incident:

      * take only POSITIVE rows. The rejected rows in a stranded file carry
        pre-filled rejections nobody reviewed; restoring them auto-rejects
        postings the user never saw.
      * deduplicate the recovery by PROVIDER JOB ID, never by name key. Two
        runs normalised employer strings differently and reported three
        already-decided postings as new; the job-id check cut a 12-row
        recovery to the 8 genuinely stranded.
    """
    out = []
    for row in rows:
        if (row.get("bucket") or "").lower() in ("rejected", "reject"):
            continue
        key = (row.get("provider", ""), str(row.get("provider_job_id", "")))
        if key in decided_ids:
            continue
        out.append(row)
    return out
