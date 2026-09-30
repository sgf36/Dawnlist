"""Does a posting's free-text location fall inside the place the user searched?

WHY THIS EXISTS. A feed that filters by place id is exact; one that hands back a
location STRING is not, and a string is only as good as whoever typed it.
LinkedIn's Job Library returns one place name per posting and offers no city
filter, and the names for Greater London are the ones people actually use:
"Croydon", "Canary Wharf", "Camden Town", "Tower Hamlets", "London Borough of
Hammersmith and Fulham", "HA3 0AA, Harrow", "West Drayton" (Heathrow). A test for
the word "London" drops every one of them, and drops them silently — the
smaller list reads as a quiet market. Measured 2026-09-30 on 4,800 UK postings:
2,038 said "London"; a further ~150 were Greater London under another name.

The rule is the one the rest of the app follows: an unknown location is not
evidence of a wrong one, so a posting located only as "United Kingdom" or
"England" is KEPT (and marked) rather than dropped, and the assessment reads the
description. Dropping is reserved for postings whose place is KNOWN and is
somewhere else.

Matching is on whole comma-separated place tokens, never substrings, so
"Sutton Coldfield", "Kingston upon Hull" and "Brentwood" do not match while
"Sutton" and "Brent" do. Where a name is ambiguous the table is INCLUSIVE: a
wrong inclusion costs one read, a wrong exclusion costs a role.
"""
from __future__ import annotations

import re

_COUNTRY_ONLY = {"united kingdom", "uk", "england", "scotland", "wales",
                 "northern ireland", "great britain", "gb"}

#: The 32 boroughs plus the City, as people write them.
_BOROUGHS = {
    "barking and dagenham", "barnet", "bexley", "brent", "bromley", "camden",
    "city of london", "croydon", "ealing", "enfield", "greenwich", "hackney",
    "hammersmith and fulham", "haringey", "harrow", "havering", "hillingdon",
    "hounslow", "islington", "kensington and chelsea", "kingston upon thames",
    "lambeth", "lewisham", "merton", "newham", "redbridge",
    "richmond upon thames", "southwark", "sutton", "tower hamlets",
    "waltham forest", "wandsworth", "westminster", "city of westminster",
}

#: Districts and neighbourhoods that appear as a posting's whole location.
#: Names that are also towns elsewhere and are NOT included: Watford, Slough,
#: Brentwood, Bow, Angel, Bank, Victoria, Waterloo, Hayes, Richmond (bare).
_DISTRICTS = {
    "canary wharf", "shoreditch", "soho", "mayfair", "kensington", "chelsea",
    "fulham", "hammersmith", "paddington", "marylebone", "bloomsbury",
    "holborn", "covent garden", "clerkenwell", "farringdon", "aldgate",
    "whitechapel", "bermondsey", "battersea", "clapham", "brixton", "peckham",
    "dulwich", "stratford", "wembley", "heathrow", "west drayton", "uxbridge",
    "southall", "romford", "ilford", "edgware", "stanmore", "wimbledon",
    "putney", "twickenham", "kingston", "barking", "dagenham", "woolwich",
    "canning town", "camden town", "kings cross", "king's cross", "euston",
    "st pancras", "hoxton", "dalston", "kensal green", "notting hill",
    "knightsbridge", "belgravia", "pimlico", "london bridge", "moorgate",
    "liverpool street", "old street", "tottenham", "wood green", "finchley",
    "hendon", "golders green", "hampstead", "highgate", "brentford",
    "chiswick", "acton", "isleworth", "feltham", "ruislip", "northwood",
    "pinner", "purley", "morden", "mitcham", "orpington", "beckenham",
    "bexleyheath", "sidcup", "erith", "welling", "eltham", "catford",
    "sydenham", "forest hill", "deptford", "rotherhithe", "wapping",
    "limehouse", "poplar", "mile end", "bethnal green", "leyton",
    "walthamstow", "chingford", "barkingside", "hornchurch", "upminster",
    "chadwell heath", "edmonton", "southgate", "palmers green", "cockfosters",
    "kilburn", "willesden", "neasden", "harlesden", "cricklewood",
    "colindale", "kenton", "hammersmith and fulham", "vauxhall", "stockwell",
    "streatham", "tooting", "balham", "earls court", "south kensington",
    "bayswater", "maida vale", "st johns wood", "swiss cottage", "belsize park",
    "primrose hill", "crouch end", "stoke newington", "canonbury", "barbican",
    "temple", "strand", "westminster bridge", "royal docks", "greenwich peninsula",
}

_LONDON_WORD = re.compile(r"\blondon\b", re.I)

# Outward codes of London postcode districts. E, EC, N, NW, SE, SW, W, WC are
# wholly London; the outer ones are limited to the districts that are.
_OUTWARD = re.compile(
    r"(?:EC|WC|NW|SE|SW|E|N|W)\d{1,2}[A-Z]?"
    r"|(?:BR[1-8]|CR[02-58]|EN[1-5]|HA\d|IG(?:[1-9]|1[01])|KT[1-69]"
    r"|RM(?:[1-9]|1[0-2])|SM[1-7]|TW(?:[1-9]|1[0-4])|UB(?:[1-9]|1[01]))[A-Z]?")
_INWARD = re.compile(r"\d[A-Z]{2}")
#: A trailing postcode (outward, or outward and inward) on a place name.
_POSTCODE_TAIL = re.compile(
    r"\s+[A-Z]{1,2}\d{1,2}[A-Z]?(?:\s+\d[A-Z]{2})?$", re.I)
_PREFIX = re.compile(
    r"^(?:london borough of|royal borough of|borough of)\s+", re.I)

METROS: dict[str, tuple[set[str], set[str]]] = {
    # name -> (boroughs, districts); a city not listed falls back to substring
    "london": (_BOROUGHS, _DISTRICTS),
}


def _tokens(location: str) -> list[str]:
    out = []
    for raw in location.split(","):
        tok = raw.strip()
        if not tok:
            continue
        out.append(tok)
    return out


def _clean(token: str) -> str:
    tok = _PREFIX.sub("", token.strip())
    tok = _POSTCODE_TAIL.sub("", tok)
    tok = re.sub(r"\s+area$", "", tok, flags=re.I)
    return tok.strip().lower()


def _is_london_postcode(token: str) -> bool:
    """'HA3 0AA' or 'SW7' on its own — a token that IS a postcode."""
    parts = token.strip().upper().split()
    if not parts or not _OUTWARD.fullmatch(parts[0]):
        return False
    return len(parts) == 1 or (len(parts) == 2 and bool(_INWARD.fullmatch(parts[1])))


def is_unresolved(locations) -> bool:
    """True when nothing narrower than a country is stated."""
    toks = [t.lower() for loc in locations for t in _tokens(loc)]
    return not toks or all(t in _COUNTRY_ONLY for t in toks)


def in_metro(location: str, city: str) -> bool:
    key = city.strip().lower()
    boroughs, districts = METROS[key]
    for token in _tokens(location):
        if key == "london" and _LONDON_WORD.search(token):
            return True
        if _is_london_postcode(token):
            return True
        cleaned = _clean(token)
        if cleaned in boroughs or cleaned in districts:
            return True
    return False


def location_matches(locations, cities) -> bool | None:
    """Is a posting in any of `cities`?

    True  - a stated place inside one of them.
    False - a stated place that is somewhere else.
    None  - no place narrower than a country is stated: unknown, so the caller
            keeps it and says so, rather than reading silence as "not London".
    """
    cities = [c for c in cities if c and c.strip()]
    if not cities:
        return True
    if is_unresolved(locations):
        return None
    for location in locations:
        for city in cities:
            key = city.strip().lower()
            if key in METROS:
                if in_metro(location, city):
                    return True
            elif key in location.lower():
                return True
    return False
