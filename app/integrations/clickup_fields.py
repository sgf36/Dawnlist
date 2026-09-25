"""ClickUp field discovery and mapping for Dawnlist's board template.

Field UUIDs are workspace-specific — two users with the same board template
have different UUIDs for "Stage", "Email", etc. This module discovers them
by name from the user's own list and stores the mapping in the database.

The NAMES come from the Dawnlist ClickUp board template and are the stable
contract. The UUIDs are discovered once at setup and refreshed if the
template changes.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

# The field names the template defines. Matched case-insensitively during
# discovery. A user who renamed a field gets a clear error naming what is
# missing, not a silent skip.
EXPECTED_FIELDS: dict[str, str] = {
    "stage": "Stage",
    "channel": "Channel",
    "application_date": "Application Date",
    "job_posting_link": "Job Posting Link",
    "confirmed_posting": "Confirmed Job Posting",
    "formal_app": "Formal Application",
    "email": "Email",
    "first_name": "First Name",
    "last_name": "Last Name",
    "address": "Address",
    "phone": "Phone",
}

# The MINIMUM fields the sync needs to function. The rest are optional and
# enrich the import when present.
REQUIRED_FIELDS = {"stage", "email"}

# Stage names from the template, matched case-insensitively to dropdown options.
STAGE_BY_NAME: dict[str, int] = {
    "identified": 0,
    "contacted": 1,
    "in dialogue": 2,
    "phone interview": 3,
    "in-person interview": 4,
    "offer": 5,
    "won": 6,
    "lost": 7,
    "on hold": 8,
}

STAGE_NAMES: dict[int, str] = {v: k.title() for k, v in STAGE_BY_NAME.items()}

# Channel names from the template.
CHANNEL_BY_NAME: dict[str, str] = {
    "email": "email",
    "letter": "letter",
    "call": "call",
    "linkedin": "linkedin",
    "whatsapp": "whatsapp",
    "application": "application",
    "other": "other",
    "email + letter": "email_letter",
    "email letter": "email_letter",
    "email_letter": "email_letter",
}

SETTINGS_KEY = "clickup_field_map"


@dataclass
class ClickUpFieldMap:
    """The discovered mapping between template field names and workspace UUIDs."""
    list_id: str
    field_ids: dict[str, str] = field(default_factory=dict)
    stage_options: dict[str, int] = field(default_factory=dict)
    stage_to_option: dict[int, str] = field(default_factory=dict)
    channel_options: dict[str, str] = field(default_factory=dict)

    def field(self, logical_name: str) -> str | None:
        return self.field_ids.get(logical_name)

    def to_json(self) -> str:
        return json.dumps({
            "list_id": self.list_id,
            "field_ids": self.field_ids,
            "stage_options": self.stage_options,
            "stage_to_option": {str(k): v
                                for k, v in self.stage_to_option.items()},
            "channel_options": self.channel_options,
        })

    @classmethod
    def from_json(cls, raw: str) -> ClickUpFieldMap:
        data = json.loads(raw)
        return cls(
            list_id=data["list_id"],
            field_ids=data.get("field_ids", {}),
            stage_options=data.get("stage_options", {}),
            stage_to_option={int(k): v
                             for k, v in data.get("stage_to_option", {}).items()},
            channel_options=data.get("channel_options", {}),
        )

    @property
    def is_usable(self) -> bool:
        return bool(self.list_id and self.field_ids.get("stage"))


def save_field_map(conn: sqlite3.Connection, fmap: ClickUpFieldMap) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (SETTINGS_KEY, fmap.to_json()))
    conn.commit()


def load_field_map(conn: sqlite3.Connection) -> ClickUpFieldMap | None:
    row = conn.execute("SELECT value FROM settings WHERE key=?",
                       (SETTINGS_KEY,)).fetchone()
    if row is None or not row[0]:
        return None
    try:
        return ClickUpFieldMap.from_json(row[0])
    except (json.JSONDecodeError, KeyError):
        return None


def discover_field_map(client, list_id: str) -> ClickUpFieldMap:
    """Read the custom fields from a ClickUp list and map them by name.

    Raises ValueError if required fields are missing — the user sees which
    field names the template expects, not a silent partial import.
    """
    resp = client.get(f"/list/{list_id}/field")
    fields = resp.get("fields", [])

    name_to_field: dict[str, dict] = {}
    for f in fields:
        name_to_field[f.get("name", "").strip().lower()] = f

    field_ids: dict[str, str] = {}
    for logical, template_name in EXPECTED_FIELDS.items():
        match = name_to_field.get(template_name.lower())
        if match:
            field_ids[logical] = match["id"]

    missing = REQUIRED_FIELDS - set(field_ids)
    if missing:
        expected_names = [EXPECTED_FIELDS[m] for m in missing]
        raise ValueError(
            f"ClickUp list is missing required fields: "
            f"{', '.join(expected_names)}. "
            f"These must exist on the list with those exact names.")

    stage_options: dict[str, int] = {}
    stage_to_option: dict[int, str] = {}
    stage_field = name_to_field.get("stage")
    if stage_field:
        for opt in stage_field.get("type_config", {}).get("options", []):
            opt_name = (opt.get("name") or "").strip().lower()
            stage_int = STAGE_BY_NAME.get(opt_name)
            if stage_int is not None:
                opt_id = opt["id"]
                stage_options[opt_id] = stage_int
                stage_to_option[stage_int] = opt_id

    channel_options: dict[str, str] = {}
    channel_field = name_to_field.get("channel")
    if channel_field:
        for opt in channel_field.get("type_config", {}).get("options", []):
            opt_name = (opt.get("name") or "").strip().lower()
            channel = CHANNEL_BY_NAME.get(opt_name)
            if channel:
                channel_options[opt["id"]] = channel

    return ClickUpFieldMap(
        list_id=list_id,
        field_ids=field_ids,
        stage_options=stage_options,
        stage_to_option=stage_to_option,
        channel_options=channel_options,
    )


def extract_custom_field(task: dict, field_id: str | None) -> str | None:
    """Pull a custom field value from a ClickUp task dict."""
    if not field_id:
        return None
    for cf in task.get("custom_fields", []):
        if cf.get("id") == field_id:
            val = cf.get("value")
            if isinstance(val, dict):
                return val.get("orderindex") or val.get("id")
            if isinstance(val, list) and val:
                return val[0].get("id") if isinstance(val[0], dict) else val[0]
            return val
    return None


def extract_dropdown_option(task: dict, field_id: str | None) -> str | None:
    """Get the option UUID from a dropdown custom field."""
    if not field_id:
        return None
    for cf in task.get("custom_fields", []):
        if cf.get("id") == field_id:
            val = cf.get("value")
            if isinstance(val, int):
                opts = cf.get("type_config", {}).get("options", [])
                for opt in opts:
                    if opt.get("orderindex") == val:
                        return opt.get("id")
            elif isinstance(val, str):
                return val
            elif isinstance(val, dict):
                return val.get("id")
    return None
