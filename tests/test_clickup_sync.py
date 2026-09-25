"""Tests for app.integrations.clickup_sync — ClickUp read/write sync.

These tests use a mock ClickUp client and a synthetic field map.
They never reach real credentials or the live API.
"""
import pytest

from app.core import db
from app.integrations.clickup_fields import ClickUpFieldMap
from app.integrations.clickup_sync import pull_from_clickup


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.sqlite3")
    db.migrate(c)
    yield c
    c.close()


# Synthetic UUIDs for tests — these match nothing real.
_STAGE_ID = "test-stage-field-uuid"
_EMAIL_ID = "test-email-field-uuid"
_FIRST_NAME_ID = "test-first-name-uuid"
_LAST_NAME_ID = "test-last-name-uuid"
_ADDRESS_ID = "test-address-uuid"
_PHONE_ID = "test-phone-uuid"
_APP_DATE_ID = "test-app-date-uuid"

# Synthetic stage option UUIDs
_STAGE_OPT_0 = "opt-identified"
_STAGE_OPT_1 = "opt-contacted"


@pytest.fixture()
def fmap():
    """A field map with synthetic UUIDs, as discover_field_map would produce."""
    return ClickUpFieldMap(
        list_id="test-list-123",
        field_ids={
            "stage": _STAGE_ID,
            "email": _EMAIL_ID,
            "first_name": _FIRST_NAME_ID,
            "last_name": _LAST_NAME_ID,
            "address": _ADDRESS_ID,
            "phone": _PHONE_ID,
            "application_date": _APP_DATE_ID,
        },
        stage_options={_STAGE_OPT_0: 0, _STAGE_OPT_1: 1},
        stage_to_option={0: _STAGE_OPT_0, 1: _STAGE_OPT_1},
        channel_options={},
    )


class FakeClient:
    """Returns fixture tasks without hitting the ClickUp API."""

    def __init__(self, tasks: list[dict]):
        self._tasks = tasks

    def iter_list_tasks(self, list_id, **kwargs):
        yield from self._tasks


def _make_task(task_id, name, stage_orderindex=0, parent=None,
               status="Open", custom_fields=None):
    cf = list(custom_fields or [])
    cf.append({
        "id": _STAGE_ID,
        "value": stage_orderindex,
        "type_config": {
            "options": [
                {"id": _STAGE_OPT_0, "orderindex": 0},
                {"id": _STAGE_OPT_1, "orderindex": 1},
            ]
        }
    })
    return {
        "id": task_id,
        "name": name,
        "parent": parent,
        "status": {"status": status},
        "custom_fields": cf,
    }


def test_import_parent_creates_opportunity(conn, fmap):
    client = FakeClient([
        _make_task("abc123", "Savills — Hotel Manager"),
    ])
    result = pull_from_clickup(conn, client=client, fmap=fmap)
    assert result.imported == 1
    row = conn.execute(
        "SELECT * FROM opportunities WHERE clickup_task_id='abc123'"
    ).fetchone()
    assert row is not None
    assert row["company"] == "Savills — Hotel Manager"


def test_import_child_creates_task(conn, fmap):
    client = FakeClient([
        _make_task("parent1", "Savills"),
        _make_task("child1", "Send follow-up email", parent="parent1"),
    ])
    result = pull_from_clickup(conn, client=client, fmap=fmap)
    assert result.imported == 1  # parent only
    tasks = conn.execute("SELECT * FROM tasks").fetchall()
    assert len(tasks) == 1
    assert tasks[0]["title"] == "Send follow-up email"


def test_import_with_contact_fields(conn, fmap):
    client = FakeClient([
        _make_task("p1", "CBRE", custom_fields=[
            {"id": _FIRST_NAME_ID, "value": "Alice"},
            {"id": _LAST_NAME_ID, "value": "Smith"},
            {"id": _EMAIL_ID, "value": "alice@cbre.com"},
            {"id": _ADDRESS_ID, "value": "10 South Place\nLondon\nEC2M 7EB"},
            {"id": _PHONE_ID, "value": "+44 20 1234 5678"},
        ]),
    ])
    pull_from_clickup(conn, client=client, fmap=fmap)
    contact = conn.execute("SELECT * FROM contacts").fetchone()
    assert contact is not None
    assert contact["name"] == "Alice Smith"
    assert contact["email"] == "alice@cbre.com"
    assert contact["mailing_address"] is not None
    assert contact["phone"] == "+44 20 1234 5678"


def test_duplicate_import_skips(conn, fmap):
    client = FakeClient([_make_task("p1", "Savills")])
    pull_from_clickup(conn, client=client, fmap=fmap)
    result = pull_from_clickup(conn, client=client, fmap=fmap)
    assert result.skipped == 1
    assert result.imported == 0
    count = conn.execute(
        "SELECT COUNT(*) FROM opportunities").fetchone()[0]
    assert count == 1


def test_stage_update_on_reimport(conn, fmap):
    client1 = FakeClient([_make_task("p1", "Savills", stage_orderindex=0)])
    pull_from_clickup(conn, client=client1, fmap=fmap)

    client2 = FakeClient([_make_task("p1", "Savills", stage_orderindex=1)])
    result = pull_from_clickup(conn, client=client2, fmap=fmap)
    assert result.updated == 1
    row = conn.execute(
        "SELECT stage FROM opportunities WHERE clickup_task_id='p1'"
    ).fetchone()
    assert row["stage"] == 1


def test_field_map_round_trip():
    """ClickUpFieldMap serialises and deserialises correctly."""
    fmap = ClickUpFieldMap(
        list_id="abc",
        field_ids={"stage": "s1", "email": "e1"},
        stage_options={"opt1": 0, "opt2": 1},
        stage_to_option={0: "opt1", 1: "opt2"},
        channel_options={"ch1": "email"},
    )
    restored = ClickUpFieldMap.from_json(fmap.to_json())
    assert restored.list_id == "abc"
    assert restored.field_ids == fmap.field_ids
    assert restored.stage_options == fmap.stage_options
    assert restored.stage_to_option == fmap.stage_to_option
    assert restored.is_usable


def test_field_map_save_load(conn):
    """Field map persists to and loads from the settings table."""
    from app.integrations.clickup_fields import save_field_map, load_field_map

    fmap = ClickUpFieldMap(
        list_id="list-42",
        field_ids={"stage": "f1"},
        stage_options={"o1": 0},
        stage_to_option={0: "o1"},
    )
    save_field_map(conn, fmap)
    loaded = load_field_map(conn)
    assert loaded is not None
    assert loaded.list_id == "list-42"
    assert loaded.field("stage") == "f1"
    assert loaded.is_usable


def test_pull_without_field_map_raises(conn):
    """Sync refuses to run when no field map is stored."""
    client = FakeClient([])
    with pytest.raises(RuntimeError, match="not set up"):
        pull_from_clickup(conn, client=client)
