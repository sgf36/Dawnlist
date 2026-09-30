/**
 * Does a posting's free-text location fall inside the place the user searched?
 *
 * A PORT of app/core/places.py. The tables below are GENERATED from it
 * (scratch script gen_places_js.py) and both implementations are tested against
 * ONE shared fixture, tests/fixtures/place_cases.json, so they cannot drift.
 *
 * WHY: LinkedIn's Job Library returns one place name per posting and has no
 * city filter, and Greater London postings are filed as boroughs and districts
 * ("Croydon", "Canary Wharf", "HA3 0AA, Harrow"). A test for the word "London"
 * dropped ~150 of 4,800 UK postings silently. An unknown location is not
 * evidence of a wrong one: country-only locations return null (keep, and mark).
 * Matching is on whole comma-separated tokens, never substrings, so
 * "Sutton Coldfield" and "Kingston upon Hull" do not match "Sutton"/"Kingston".
 */

const COUNTRY_ONLY = new Set(["england", "gb", "great britain", "northern ireland", "scotland", "uk", "united kingdom", "wales"]);
const BOROUGHS = new Set(["barking and dagenham", "barnet", "bexley", "brent", "bromley", "camden", "city of london", "city of westminster", "croydon", "ealing", "enfield", "greenwich", "hackney", "hammersmith and fulham", "haringey", "harrow", "havering", "hillingdon", "hounslow", "islington", "kensington and chelsea", "kingston upon thames", "lambeth", "lewisham", "merton", "newham", "redbridge", "richmond upon thames", "southwark", "sutton", "tower hamlets", "waltham forest", "wandsworth", "westminster"]);
const DISTRICTS = new Set(["acton", "aldgate", "balham", "barbican", "barking", "barkingside", "battersea", "bayswater", "beckenham", "belgravia", "belsize park", "bermondsey", "bethnal green", "bexleyheath", "bloomsbury", "brentford", "brixton", "camden town", "canary wharf", "canning town", "canonbury", "catford", "chadwell heath", "chelsea", "chingford", "chiswick", "clapham", "clerkenwell", "cockfosters", "colindale", "covent garden", "cricklewood", "crouch end", "dagenham", "dalston", "deptford", "dulwich", "earls court", "edgware", "edmonton", "eltham", "erith", "euston", "farringdon", "feltham", "finchley", "forest hill", "fulham", "golders green", "greenwich peninsula", "hammersmith", "hammersmith and fulham", "hampstead", "harlesden", "heathrow", "hendon", "highgate", "holborn", "hornchurch", "hoxton", "ilford", "isleworth", "kensal green", "kensington", "kenton", "kilburn", "king's cross", "kings cross", "kingston", "knightsbridge", "leyton", "limehouse", "liverpool street", "london bridge", "maida vale", "marylebone", "mayfair", "mile end", "mitcham", "moorgate", "morden", "neasden", "northwood", "notting hill", "old street", "orpington", "paddington", "palmers green", "peckham", "pimlico", "pinner", "poplar", "primrose hill", "purley", "putney", "romford", "rotherhithe", "royal docks", "ruislip", "shoreditch", "sidcup", "soho", "south kensington", "southall", "southgate", "st johns wood", "st pancras", "stanmore", "stockwell", "stoke newington", "strand", "stratford", "streatham", "swiss cottage", "sydenham", "temple", "tooting", "tottenham", "twickenham", "upminster", "uxbridge", "vauxhall", "walthamstow", "wapping", "welling", "wembley", "west drayton", "westminster bridge", "whitechapel", "willesden", "wimbledon", "wood green", "woolwich"]);

const LONDON_WORD = /\blondon\b/i;
const OUTWARD = /^(?:(?:EC|WC|NW|SE|SW|E|N|W)\d{1,2}[A-Z]?|(?:BR[1-8]|CR[02-58]|EN[1-5]|HA\d|IG(?:[1-9]|1[01])|KT[1-69]|RM(?:[1-9]|1[0-2])|SM[1-7]|TW(?:[1-9]|1[0-4])|UB(?:[1-9]|1[01]))[A-Z]?)$/;
const INWARD = /^\d[A-Z]{2}$/;
const POSTCODE_TAIL = /\s+[A-Z]{1,2}\d{1,2}[A-Z]?(?:\s+\d[A-Z]{2})?$/i;
const PREFIX = /^(?:london borough of|royal borough of|borough of)\s+/i;

function tokens(location) {
  return String(location || '').split(',').map((t) => t.trim()).filter(Boolean);
}

function clean(token) {
  let t = token.trim().replace(PREFIX, '');
  t = t.replace(POSTCODE_TAIL, '');
  t = t.replace(/\s+area$/i, '');
  return t.trim().toLowerCase();
}

function isLondonPostcode(token) {
  const parts = token.trim().toUpperCase().split(/\s+/).filter(Boolean);
  if (!parts.length || !OUTWARD.test(parts[0])) return false;
  return parts.length === 1 || (parts.length === 2 && INWARD.test(parts[1]));
}

function isUnresolved(locations) {
  const toks = locations.flatMap((l) => tokens(l).map((t) => t.toLowerCase()));
  return toks.length === 0 || toks.every((t) => COUNTRY_ONLY.has(t));
}

function inLondon(location) {
  for (const token of tokens(location)) {
    if (LONDON_WORD.test(token)) return true;
    if (isLondonPostcode(token)) return true;
    const c = clean(token);
    if (BOROUGHS.has(c) || DISTRICTS.has(c)) return true;
  }
  return false;
}

/**
 * true  - a stated place inside one of `cities`
 * false - a stated place that is somewhere else
 * null  - nothing narrower than a country is stated: unknown, keep and mark
 */
export function locationMatches(locations, cities) {
  const wanted = (cities || []).filter((c) => c && c.trim());
  if (!wanted.length) return true;
  const locs = locations || [];
  if (isUnresolved(locs)) return null;
  for (const location of locs) {
    for (const city of wanted) {
      const key = city.trim().toLowerCase();
      if (key === 'london') {
        if (inLondon(location)) return true;
      } else if (location.toLowerCase().includes(key)) {
        return true;
      }
    }
  }
  return false;
}
