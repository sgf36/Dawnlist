"""ClickUp ↔ Dawnlist sync layer.

Read sync: import ClickUp tasks into Dawnlist's opportunities, tasks and
contacts tables.  Write sync: push Dawnlist decisions back to ClickUp.

During the transition ClickUp remains authoritative. Dawnlist reads ClickUp
and mirrors it locally; Dawnlist writes are pushed to ClickUp immediately.

Field UUIDs are discovered per-workspace, not hardcoded — see
`clickup_fields.discover_field_map`.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.integrations.clickup import ClickUpClient
from app.integrations.clickup_fields import (
    ClickUpFieldMap, STAGE_NAMES,
    extract_custom_field, extract_dropdown_option,
    load_field_map,
)


@dataclass
class SyncResult:
    imported: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def pull_from_clickup(conn: sqlite3.Connection,
                      client: ClickUpClient | None = None,
                      fmap: ClickUpFieldMap | None = None,
                      ) -> SyncResult:
    """Import all parent tasks from ClickUp into Dawnlist opportunities."""
    client = client or ClickUpClient()
    fmap = fmap or load_field_map(conn)
    if fmap is None or not fmap.is_usable:
        raise RuntimeError(
            "ClickUp integration not set up. Use Settings to connect "
            "your ClickUp list first.")

    result = SyncResult()
    now = datetime.now(timezone.utc).isoformat()

    for task in client.iter_list_tasks(fmap.list_id, include_closed=True):
        try:
            _import_task(conn, task, now, result, fmap)
        except Exception as e:
            result.errors.append(f"{task.get('id', '?')}: {e}")

    conn.commit()
    return result


def _import_task(conn: sqlite3.Connection, task: dict, now: str,
                 result: SyncResult, fmap: ClickUpFieldMap) -> None:
    """Import a single ClickUp task as an opportunity (if parent) or task."""
    parent_id = task.get("parent")

    if parent_id:
        _import_child_task(conn, task, now, result, fmap)
        return

    task_id = task.get("id", "")
    company = task.get("name", "")
    stage_option = extract_dropdown_option(task, fmap.field("stage"))
    stage = fmap.stage_options.get(stage_option, 0) if stage_option else 0

    closed_at = None
    if stage in (6, 7):  # Won or Lost
        closed_at = now

    existing = conn.execute(
        "SELECT id, stage FROM opportunities WHERE clickup_task_id = ?",
        (task_id,)).fetchone()

    if existing:
        if existing["stage"] != stage:
            conn.execute("""
                UPDATE opportunities
                SET stage = ?, status_mirror = ?, closed_at = ?,
                    clickup_task_id = ?
                WHERE id = ?
            """, (stage, STAGE_NAMES.get(stage, ""),
                  closed_at, task_id, existing["id"]))
            result.updated += 1
        else:
            result.skipped += 1
        opp_id = existing["id"]
    else:
        cur = conn.execute("""
            INSERT INTO opportunities
            (company, stage, status_mirror, category, clickup_task_id,
             created_at)
            VALUES (?, ?, ?, 'opportunity', ?, ?)
        """, (company, stage, STAGE_NAMES.get(stage, ""),
              task_id, now))
        opp_id = cur.lastrowid
        result.imported += 1

    _sync_contact(conn, task, opp_id, now, fmap)


def _import_child_task(conn: sqlite3.Connection, task: dict, now: str,
                       result: SyncResult, fmap: ClickUpFieldMap) -> None:
    """Import a child ClickUp task as a Dawnlist task."""
    parent_clickup_id = task.get("parent")
    parent = conn.execute(
        "SELECT id FROM opportunities WHERE clickup_task_id = ?",
        (parent_clickup_id,)).fetchone()

    if not parent:
        result.skipped += 1
        return

    opp_id = parent["id"]
    task_id = task.get("id", "")
    title = task.get("name", "")

    cu_status = task.get("status", {}).get("status", "").lower()
    if cu_status in ("complete", "closed"):
        status = "complete"
    elif cu_status == "waiting":
        status = "waiting"
    else:
        status = "open"

    due_on = extract_custom_field(task, fmap.field("application_date"))

    existing = conn.execute(
        "SELECT id FROM tasks WHERE opportunity_id = ? AND title = ?",
        (opp_id, title)).fetchone()

    if existing:
        result.skipped += 1
        return

    if status in ("open", "waiting"):
        open_task = conn.execute(
            "SELECT id FROM tasks WHERE opportunity_id = ? "
            "AND status IN ('open', 'waiting')", (opp_id,)).fetchone()
        if open_task:
            result.skipped += 1
            return

    evidence = None
    if status == "complete":
        evidence = f"Imported from ClickUp {task_id} as {cu_status}"

    conn.execute("""
        INSERT INTO tasks (opportunity_id, title, status, due_on,
                          closed_evidence, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (opp_id, title, status, due_on, evidence, now))


def _sync_contact(conn: sqlite3.Connection, task: dict,
                  opp_id: int, now: str, fmap: ClickUpFieldMap) -> None:
    """Sync contact fields from a ClickUp parent task."""
    first = extract_custom_field(task, fmap.field("first_name")) or ""
    last = extract_custom_field(task, fmap.field("last_name")) or ""
    email = extract_custom_field(task, fmap.field("email"))
    phone = extract_custom_field(task, fmap.field("phone"))
    address = extract_custom_field(task, fmap.field("address"))

    if not first and not last and not email:
        return

    name = f"{first} {last}".strip() or first or last

    existing = conn.execute(
        "SELECT id FROM contacts WHERE opportunity_id = ? AND name = ?",
        (opp_id, name)).fetchone()

    if existing:
        conn.execute("""
            UPDATE contacts
            SET email = COALESCE(?, email),
                first_name = COALESCE(?, first_name),
                last_name = COALESCE(?, last_name),
                phone = COALESCE(?, phone),
                mailing_address = COALESCE(?, mailing_address)
            WHERE id = ?
        """, (email, first or None, last or None,
              phone, address, existing["id"]))
        return

    conn.execute("""
        INSERT INTO contacts
        (opportunity_id, name, email, first_name, last_name, phone,
         mailing_address, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (opp_id, name, email, first or None, last or None,
          phone, address, now))


def push_stage_to_clickup(conn: sqlite3.Connection,
                          opportunity_id: int,
                          client: ClickUpClient | None = None,
                          fmap: ClickUpFieldMap | None = None,
                          ) -> None:
    """Push a Dawnlist stage change back to ClickUp."""
    client = client or ClickUpClient()
    fmap = fmap or load_field_map(conn)
    if fmap is None or not fmap.is_usable:
        return

    row = conn.execute(
        "SELECT clickup_task_id, stage FROM opportunities WHERE id = ?",
        (opportunity_id,)).fetchone()

    if not row or not row["clickup_task_id"]:
        return

    stage_field = fmap.field("stage")
    if not stage_field:
        return

    option_uuid = fmap.stage_to_option.get(row["stage"])
    if not option_uuid:
        return

    client.put(f"/task/{row['clickup_task_id']}/field/{stage_field}",
               body={"value": option_uuid})
