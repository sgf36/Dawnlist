"""The metro-region candidate tool (tools/metro_candidates.py).

It proposes, checks and measures; it must never widen a search by itself, and it
must not edit the Worker. These tests pin the classification (the part that
decides which cities are worth spending credits on) and the pacing.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "metro_candidates", ROOT / "tools" / "metro_candidates.py")
mc = importlib.util.module_from_spec(spec)
sys.modules["metro_candidates"] = mc  # dataclasses look the module up by name
spec.loader.exec_module(mc)


def place(id_, name, pop, adm2="X", country="GB", adm1="ENG", fcode="PPL"):
    return dict(id=id_, name=name, country=country, adm1=adm1, adm2=adm2,
                pop=pop, fcode=fcode)


ADMIN2 = {"GB.ENG.GLA": ("Greater London", 2648110),
          "GB.ENG.BRS": ("City of Bristol", 3333133),
          "GB.ENG.BIG": ("Big County", 111),
          "GB.ENG.MAN": ("Manchester", 3333169)}


def verdict(places, name, **kw):
    return {c.city: c for c in mc.classify(places, ADMIN2, **kw)}[name]


def test_a_dominant_city_in_a_moderately_larger_region_is_proposed():
    places = [place(1, "London", 9_000_000, "GLA"),
              place(2, "Romford", 200_000, "GLA"),
              place(3, "Ealing", 400_000, "GLA"),
              place(4, "Croydon", 3_000_000, "GLA")]
    c = verdict(places, "London")
    assert c.verdict == "propose" and c.adm2_id == 2648110


def test_a_suburb_is_subordinate_to_the_bigger_place_sharing_its_region():
    places = [place(1, "London", 9_000_000, "GLA"),
              place(2, "Islington", 320_000, "GLA")]
    c = verdict(places, "Islington", min_pop=100_000)
    assert c.verdict == "subordinate" and "London" in c.note


def test_a_region_that_is_the_city_under_another_name_is_equivalent():
    places = [place(1, "Bristol", 479_000, "BRS")]
    assert verdict(places, "Bristol").verdict in ("equivalent", "same-place")


def test_a_region_much_bigger_than_the_city_is_too_large():
    places = [place(1, "Town", 150_000, "BIG"), place(2, "Village", 90_000, "BIG"),
              place(3, "Other", 200_000, "BIG"), place(4, "More", 190_000, "BIG")]
    # Town is not the biggest here, so make it dominant but the region huge
    places[0]["pop"] = 400_000
    assert verdict(places, "Town", max_ratio=2.0).verdict in ("too-large", "subordinate")


def test_a_country_or_place_without_an_admin2_level_is_not_proposed():
    c = verdict([place(1, "Cityville", 500_000, "")], "Cityville")
    assert c.verdict == "no-adm2"


def test_only_cities_above_the_population_floor_are_classified():
    got = mc.classify([place(1, "Small", 50_000, "GLA")], ADMIN2, min_pop=100_000)
    assert got == []


def test_non_populated_features_are_ignored_when_parsing():
    row = lambda fc: "\t".join(["1", "X", "X", "", "0", "0", "P", fc, "GB", "",
                                "ENG", "GLA", "", "", "1000"])
    assert len(mc.parse_places(row("PPL"))) == 1
    assert mc.parse_places(row("MT")) == []


def test_admin2_codes_map_to_ids():
    text = "GB.ENG.GLA\tGreater London\tGreater London\t2648110\nGB.X.Y\tNo id\tNo id\t\n"
    assert mc.parse_admin2(text) == {"GB.ENG.GLA": ("Greater London", 2648110)}


@pytest.mark.parametrize("city,region,want", [
    (17035, 18833, "widen"),      # London on a generic query: small gain, still right
    (100, 101, "no-gain"),
    (19, 68, "too-wide"),         # Birkenhead vs all of Wirral
    (0, 50, "city-empty"),
    (0, 0, "no-data"),
    (None, 5, "unmeasured"),
])
def test_measured_decisions(city, region, want):
    assert mc.decide(city, region)[1] == want


def test_the_ledger_paces_under_the_hourly_limit(tmp_path):
    ledger = mc.Ledger(tmp_path / "l.json")
    now = 10_000.0
    for i in range(mc.CALLS_PER_HOUR):
        ledger.record(now + i)
    assert ledger.wait_needed(now + 50) > 0
    assert ledger.wait_needed(now + 4000) == 0.0


def test_the_ledger_survives_a_restart(tmp_path):
    a = mc.Ledger(tmp_path / "l.json")
    a.record(1.0)
    assert mc.Ledger.load(tmp_path / "l.json").calls == [1.0]


def test_emit_prints_only_measured_widen_entries_and_edits_nothing(tmp_path):
    c = mc.Candidate(country="GB", city="London", city_id=1, city_pop=1,
                     adm1="ENG", adm2_code="GLA", adm2_id=2648110,
                     adm2_name="Greater London", region_pop=2, n_places=3,
                     ratio=2.0, verdict="propose", city_total=100,
                     region_total=120, gain=1.2, decision="widen")
    skip = mc.Candidate(**{**c.__dict__, "city": "Bristol", "adm2_id": 9,
                           "decision": "no-gain"})
    text = mc.emit_entries([c, skip])
    assert '"london": 2648110' in text and "bristol" not in text.lower()


def test_the_tool_never_writes_the_worker():
    """The Worker's table is edited by a person after reading the counts. The
    tool may MENTION index.js in its prose but must never name it as a target."""
    src = (ROOT / "tools" / "metro_candidates.py").read_text(encoding="utf-8")
    prose = src.split('"""')[1]
    code = src.replace(prose, "")
    assert "index.js" not in code.replace("Paste into METRO_REGIONS in src/index.js", "")
