"""Employer normalisation — company_key must collapse spelling variants.

Hotel groups appear under many names across providers: "Marriott International",
"Marriott International, Inc.", "Marriott Hotels", "Marriott Careers". These
must all normalise to the same key so that:
  - a known-employer rule at tier 2 fires on every variant
  - a kill family covers every variant without listing each one
  - the dedup near-match detector sees them as one employer

Each group below tests a real cluster of variants seen in feeds.
"""
import pytest

from app.feed.models import company_key


# -- Hotel groups ────────────────────────────────────────────────────────────

class TestMarriott:
    BASE = company_key("Marriott")

    @pytest.mark.parametrize("variant", [
        "Marriott International",
        "Marriott International, Inc.",
        "Marriott International Inc",
        "Marriott Hotels",
        "Marriott Hotels & Resorts",
        "Marriott Careers",
        "MARRIOTT",
    ])
    def test_variants_match(self, variant):
        assert company_key(variant) == self.BASE


class TestIHG:
    BASE = company_key("IHG")

    @pytest.mark.parametrize("variant", [
        "IHG Hotels & Resorts",
        "IHG Hotels and Resorts",
        "IHG Careers",
        "IHG PLC",
    ])
    def test_variants_match(self, variant):
        assert company_key(variant) == self.BASE

    def test_intercontinental_full_name_is_different(self):
        assert company_key("InterContinental Hotels Group") != self.BASE

    def test_intercontinental_is_different_from_ihg(self):
        assert company_key("InterContinental") != self.BASE


class TestAccor:
    BASE = company_key("Accor")

    @pytest.mark.parametrize("variant", [
        "Accor Hotels",
        "Accor Group",
        "Accor SA",
        "ACCOR",
        "Accor International",
    ])
    def test_variants_match(self, variant):
        assert company_key(variant) == self.BASE


class TestHilton:
    BASE = company_key("Hilton")

    @pytest.mark.parametrize("variant", [
        "Hilton Hotels",
        "Hilton Hotels & Resorts",
        "Hilton Careers",
        "Hilton International",
        "Hilton PLC",
    ])
    def test_variants_match(self, variant):
        assert company_key(variant) == self.BASE


class TestFourSeasons:
    BASE = company_key("Four Seasons")

    @pytest.mark.parametrize("variant", [
        "Four Seasons Hotels and Resorts",
        "Four Seasons Hotels & Resorts",
        "Four Seasons Hotels",
        "Four Seasons Resorts",
        "Four Seasons International",
    ])
    def test_variants_match(self, variant):
        assert company_key(variant) == self.BASE


class TestMandarinOriental:
    BASE = company_key("Mandarin Oriental")

    @pytest.mark.parametrize("variant", [
        "Mandarin Oriental Hotel Group",
        "Mandarin Oriental Hotels",
        "Mandarin Oriental International",
        "Mandarin Oriental International Limited",
    ])
    def test_variants_match(self, variant):
        assert company_key(variant) == self.BASE


class TestRosewood:
    BASE = company_key("Rosewood")

    @pytest.mark.parametrize("variant", [
        "Rosewood Hotel Group",
        "Rosewood Hotels & Resorts",
        "Rosewood Hotels",
        "Rosewood Hotels and Resorts",
    ])
    def test_variants_match(self, variant):
        assert company_key(variant) == self.BASE


class TestHyatt:
    BASE = company_key("Hyatt")

    @pytest.mark.parametrize("variant", [
        "Hyatt Hotels",
        "Hyatt Hotels Corporation",
        "Hyatt International",
        "Hyatt Careers",
    ])
    def test_variants_match(self, variant):
        assert company_key(variant) == self.BASE


# -- Real estate / investment managers ───────────────────────────────────────

class TestBrookfield:
    """Brookfield Asset Management and Brookfield Properties are distinct
    entities — the normaliser should NOT collapse them to bare "Brookfield"."""
    BAM = company_key("Brookfield Asset Management")

    @pytest.mark.parametrize("variant", [
        "Brookfield Asset Management Ltd",
        "Brookfield Asset Management Inc.",
        "Brookfield Asset Management Limited",
    ])
    def test_bam_variants_match(self, variant):
        assert company_key(variant) == self.BAM

    def test_bam_is_different_from_bare_brookfield(self):
        assert self.BAM != company_key("Brookfield")

    def test_brookfield_properties_is_different_from_bam(self):
        assert company_key("Brookfield Properties") != self.BAM


class TestJLL:
    """JLL vs "Jones Lang LaSalle" is an abbreviation, not a suffix variant.
    The normaliser is not expected to map abbreviations."""

    def test_jll_suffix_stripping(self):
        assert company_key("JLL") == company_key("JLL Inc")

    def test_full_name_suffix_stripping(self):
        assert company_key("Jones Lang LaSalle") == company_key(
            "Jones Lang LaSalle Incorporated")


class TestSavills:
    BASE = company_key("Savills")

    @pytest.mark.parametrize("variant", [
        "Savills PLC",
        "Savills plc",
        "Savills International",
    ])
    def test_variants_match(self, variant):
        assert company_key(variant) == self.BASE


# -- Corporate suffix stripping ─────────────────────────────────────────────

@pytest.mark.parametrize("a, b", [
    ("Kier Ltd", "Kier Limited"),
    ("CBRE Inc", "CBRE Incorporated"),
    ("Cushman & Wakefield PLC", "Cushman & Wakefield"),
    ("Aman Resorts", "Aman"),
    ("Belmond Ltd", "Belmond Limited"),
    ("Jumeirah Group", "Jumeirah"),
    ("Melia Hotels International", "Melia"),
    ("NH Hotel Group", "NH"),
    ("Radisson Hotel Group", "Radisson"),
])
def test_corporate_suffix_pairs(a, b):
    assert company_key(a) == company_key(b)


# -- Distinctness — names that must NOT collapse ────────────────────────────

@pytest.mark.parametrize("a, b", [
    ("EY", "Bentley"),
    ("EY", "The Walt Disney Company"),
    ("GIC", "Logic"),
    ("Hilton", "Marriott"),
    ("Four Seasons", "Rosewood"),
    ("Aman", "Amana"),
    ("IHG", "InterContinental"),
])
def test_distinct_employers_stay_distinct(a, b):
    assert company_key(a) != company_key(b)


# -- Edge cases ─────────────────────────────────────────────────────────────

def test_empty_string():
    assert company_key("") == ""


def test_none():
    assert company_key(None) == ""


def test_whitespace_only():
    assert company_key("   ") == ""


def test_case_insensitive():
    assert company_key("FOUR SEASONS") == company_key("four seasons")


def test_punctuation_normalised():
    assert company_key("Cushman & Wakefield") == company_key("Cushman and Wakefield")
