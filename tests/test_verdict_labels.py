"""Every verdict the model may return has a label a person can read.

The labels are looked up as `onboarding.app.<verdict>`, so a verdict added to
the schema with no matching key shows the raw key on the board and in
calibration. `judgement-call` shipped that way.
"""
import json
import pathlib

from app import i18n
from app.intelligence.prompts import VERDICT_SCHEMA
from app.main import NOT_ASSESSED

EN = pathlib.Path(i18n.__file__).parent / "resources" / "locales" / "en.json"


def _bucket_enum(node):
    if isinstance(node, dict):
        if "bucket" in node and "enum" in node["bucket"]:
            return node["bucket"]["enum"]
        for child in node.values():
            found = _bucket_enum(child)
            if found:
                return found
    return None


def test_the_schema_still_declares_its_verdicts():
    assert _bucket_enum(VERDICT_SCHEMA), "the guard below would pass on nothing"


def test_every_verdict_has_an_english_label():
    catalogue = json.loads(EN.read_text(encoding="utf-8"))
    verdicts = [*_bucket_enum(VERDICT_SCHEMA), NOT_ASSESSED]
    missing = [v for v in verdicts if f"onboarding.app.{v}" not in catalogue]
    assert missing == []
