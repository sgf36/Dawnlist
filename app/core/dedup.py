"""Deduplication (spec 6.6, invariant 9).

Two failure modes, and they are not symmetrical:

  * double-surfacing the same posting is noise;
  * silently collapsing two different postings DISCARDS REAL WORK, and is the
    worse one.

So: the exact key `(provider, provider_job_id)` is the only thing allowed to
drop a row. Name keys never drop anything — they raise a flag for a human.
"""
from __future__ import annotations

from dataclasses import dataclass

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
