"""Where a search looks, on what terms, and what it must never return.

Every search a user switched on during setup looked across the whole world.
Seeds were saved switched off, which skipped `save_query`'s country check;
switching one on was a bare UPDATE; and the Worker leaves the country filter
out when the list is empty. The guard written to stop exactly that was bypassed
by the only route a user could take, and every posting it returned was paid for.

Where, on what contract and which employers are ruled out belong to the person,
not to any one job title, so one scope is kept per install. Setup fills it from
what the user said, Settings edits it, and every saved search carries a copy so
a run needs nothing else.

What belongs here is only what can be decided WITHOUT reading a posting. Judging
fit — seniority, function, sector — is the assessment's job. A rule that needs
judgement, applied at the feed, hides real roles before anyone has read them.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.core.pipeline import Gate
from app.feed.models import Job, name_key
from app.i18n import tr

#: The feed's own vocabulary, so a stored value compares directly with a
#: posting's `employment_statuses`.
EMPLOYMENT_TYPES = ("full_time", "part_time", "contract", "temporary",
                    "internship", "freelance")

SETTING_KEY = "search_scope"

#: What people type, mapped to the code the feed indexes. Nothing else is
#: guessed: a wrong country silently searches the wrong market.
_ALIASES = {"UK": "GB"}

_COUNTRY = re.compile(r"^[A-Za-z]{2}$")


def _unique(values, key=lambda v: v):
    seen, out = set(), []
    for value in values:
        if key(value) not in seen:
            seen.add(key(value))
            out.append(value)
    return out


def _texts(values) -> list[str]:
    return [v.strip() for v in values or [] if isinstance(v, str) and v.strip()]


@dataclass(frozen=True)
class SearchScope:
    countries: tuple[str, ...] = ()
    cities: tuple[str, ...] = ()
    employment_types: tuple[str, ...] = ()
    exclude_title_terms: tuple[str, ...] = ()
    exclude_companies: tuple[str, ...] = ()

    @classmethod
    def from_parts(cls, *, countries=None, cities=None, employment_types=None,
                   exclude_title_terms=None, exclude_companies=None) -> "SearchScope":
        codes = [_ALIASES.get(c.upper(), c.upper())
                 for c in _texts(countries) if _COUNTRY.match(c)]
        kinds = [k for k in _texts(employment_types) if k in EMPLOYMENT_TYPES]
        return cls(
            countries=tuple(_unique(codes)),
            cities=tuple(_unique(_texts(cities), key=str.lower)),
            employment_types=tuple(_unique(kinds)),
            exclude_title_terms=tuple(_unique(_texts(exclude_title_terms),
                                              key=str.lower)),
            exclude_companies=tuple(_unique(_texts(exclude_companies),
                                            key=str.lower)))

    @classmethod
    def from_params(cls, params: dict) -> "SearchScope":
        return cls.from_parts(**{name: params.get(name) for name in PARAM_NAMES})

    @property
    def is_set(self) -> bool:
        # A city alone is not a scope: the same name exists in several
        # countries, and the feed would have to guess which one was meant.
        return bool(self.countries)

    def describe(self) -> str:
        return ", ".join([*self.cities, *self.countries])

    def as_params(self) -> dict:
        return {name: list(getattr(self, name)) for name in PARAM_NAMES}

    def with_location(self, other: "SearchScope") -> "SearchScope":
        """This scope's exclusions and contract types, `other`'s location."""
        return SearchScope(countries=other.countries, cities=other.cities,
                           employment_types=self.employment_types,
                           exclude_title_terms=self.exclude_title_terms,
                           exclude_companies=self.exclude_companies)


#: The stored field names, which are also the saved search's param names, so a
#: search and the install-wide scope can never be read with different keys.
PARAM_NAMES = ("countries", "cities", "employment_types",
               "exclude_title_terms", "exclude_companies")


@dataclass(frozen=True)
class SearchPlan:
    """What setup worked out from the user's own words."""
    titles: list[str] = field(default_factory=list)
    scope: SearchScope = SearchScope()


def parse_where(text: str, *, keep: SearchScope = SearchScope()) -> SearchScope:
    """"London, GB" typed in Settings, as a scope. Exclusions are kept from `keep`.

    Two-letter entries are country codes and everything else is a city. A city
    with no country is refused rather than resolved, because the feed would
    otherwise pick one of several places with that name.
    """
    parts = [p.strip() for p in re.split(r"[,;\n]", text or "") if p.strip()]
    if not parts:
        raise ValueError(tr("searches.where_empty"))
    countries = [p for p in parts if _COUNTRY.match(p)]
    if not countries:
        raise ValueError(tr("searches.where_needs_country"))
    where = SearchScope.from_parts(
        countries=countries, cities=[p for p in parts if not _COUNTRY.match(p)])
    return keep.with_location(where)


def load_scope(conn) -> SearchScope:
    row = conn.execute("SELECT value FROM settings WHERE key=?",
                       (SETTING_KEY,)).fetchone()
    if row is None:
        return SearchScope()
    try:
        return SearchScope.from_params(json.loads(row["value"]))
    except (ValueError, TypeError, AttributeError):
        return SearchScope()


def save_scope(conn, scope: SearchScope) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (SETTING_KEY, json.dumps(scope.as_params())))
    conn.commit()


# ---------------------------------------------------------------------------
# Gates. Applied after the fetch, and deliberately able only to REMOVE a
# posting whose own data contradicts the user's stated scope. Unknown is kept.
# ---------------------------------------------------------------------------

def location_gate(countries) -> Gate:
    """The feed filters by country, but a cached or older response may not
    have been, and the feed's own tag is the only thing checked here.

    A posting with no country recorded is kept: an unknown location is not
    evidence of a wrong one.
    """
    wanted = {c.upper() for c in countries}

    def inside(job: Job) -> bool:
        codes = job.raw_criteria.get("country_codes") or []
        return not wanted or not codes or any(
            str(c).upper() in wanted for c in codes)

    return Gate("outside-location", inside,
                "based outside where you want to work")


def employment_gate(kinds) -> Gate:
    """Remove a posting only when none of its contract types is acceptable.

    Blank is kept, because the most relevant posting in one measured sample
    carried no contract type at all. Mixed is kept too, because the feed reads
    types out of benefits text: a full-time role offering a volunteering day
    comes back tagged both `full_time` and `volunteer`.
    """
    wanted = set(kinds)

    def accepted(job: Job) -> bool:
        statuses = job.raw_criteria.get("employment_statuses") or []
        return not wanted or not statuses or any(s in wanted for s in statuses)

    return Gate("contract-type", accepted, "not a contract type you accept")


def excluded_company_gate(companies) -> Gate:
    """The feed's exclusion matches names EXACTLY, so "GIC Pte Ltd" passes a
    "GIC" exclusion there. Compared here on the normalised company name, which
    drops the corporate suffixes that vary between sources. Never a substring
    match: "GIC" is inside "Logic" and "Magic"."""
    banned = {name_key(c, "").split(" :: ")[0] for c in companies}
    banned.discard("")

    def allowed(job: Job) -> bool:
        return name_key(job.company, "").split(" :: ")[0] not in banned

    return Gate("excluded-employer", allowed, "an employer you ruled out")


def scope_gates(scopes) -> list[Gate]:
    """The gates for a run over several searches: the union of what each
    allows, so a posting fetched by one search is not removed by another's
    narrower terms."""
    scopes = list(scopes)
    countries = {c for s in scopes for c in s.countries}
    # Any search with no contract preference accepts every contract type.
    kinds = (set() if any(not s.employment_types for s in scopes)
             else {k for s in scopes for k in s.employment_types})
    companies = {c for s in scopes for c in s.exclude_companies}
    return [location_gate(countries), employment_gate(kinds),
            excluded_company_gate(companies)]
