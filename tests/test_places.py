"""Greater London under the names people actually write (2026-09-30)."""
from __future__ import annotations

import pytest

from app.core.places import is_unresolved, location_matches

IN = ["London, England, United Kingdom", "London Area, United Kingdom",
      "Greater London, England, United Kingdom", "Croydon, England, United Kingdom",
      "Sutton, England, United Kingdom", "Camden Town, England, United Kingdom",
      "Canary Wharf, England", "City Of Westminster, England", "Ilford IG1 4LZ",
      "Romford RM6 6QU", "West Drayton UB7 0HJ", "HA3 0AA, Harrow, England",
      "SW7 4DL", "London Borough of Hammersmith and Fulham, England",
      "Hammersmith and Fulham", "East London, England", "Stratford, England"]
OUT = ["Watford, England, United Kingdom", "Slough, England, United Kingdom",
       "Sutton Coldfield, England", "Royal Sutton Coldfield, England",
       "Kingston Upon Hull, England", "Brentwood, England",
       "Newcastle upon Tyne NE1", "Cirencester, England", "City Of Bristol, England",
       "Greater Manchester, England, United Kingdom", "Edinburgh, Scotland"]


@pytest.mark.parametrize("loc", IN)
def test_greater_london_places_match(loc):
    assert location_matches([loc], ["London"]) is True


@pytest.mark.parametrize("loc", OUT)
def test_elsewhere_does_not_match(loc):
    assert location_matches([loc], ["London"]) is False


@pytest.mark.parametrize("loc", ["United Kingdom", "England, United Kingdom", ""])
def test_a_country_only_location_is_unknown_not_wrong(loc):
    assert location_matches([loc], ["London"]) is None
    assert is_unresolved([loc])


def test_no_city_asked_matches_everything():
    assert location_matches(["Anywhere"], []) is True


def test_a_city_without_a_table_falls_back_to_a_substring():
    assert location_matches(["Manchester, England, United Kingdom"], ["Manchester"]) is True
    assert location_matches(["Leeds, England, United Kingdom"], ["Manchester"]) is False
