"""Propose, check and MEASURE metro regions for the Worker's METRO_REGIONS table.

WHY THIS EXISTS
---------------
Searching "London" sent only London's city id, so every posting filed under a
borough or district (Romford, West Drayton, Hammersmith and Fulham ...) never
came back: 41 rows against 53 for the whole Greater London region, measured
2026-09-30. The fix was one hand-measured table entry. This tool finds the
OTHER cities where the same gap probably exists, and produces the evidence
needed to decide each one — it never edits the Worker.

THE TABLE STAYS EXPLICIT AND REVIEWED. Nothing here runs at request time, and
nothing here writes `server/dawnlist-feed-worker/src/index.js`. A name rule
was tried and rejected ("Greater Sudbury" is a merged municipality, "Greater
Noida" is another city): the feed bills per row and a search must never be
widened past what the user named. `emit` prints reviewed entries as a snippet
to paste, by a person, after reading the measured counts.

THREE STEPS, each resumable and each cheap
------------------------------------------
  build      GeoNames dumps (free, and the SAME ids TheirStack uses) -> a
             candidate list. For every city above --min-pop it finds the
             admin-2 region the city sits in and classifies it. No credits.
  check      Which candidate ids exist in TheirStack's catalogue. It is a
             SUBSET of GeoNames: the City of Manchester (3333169) and Trafford
             (3333211) are absent, so an unchecked list silently loses places.
  measure    Count postings for the city id and for the region id with one
             cheap query each (limit=1 costs at most 1 credit) and classify
             the gain. Paced under TheirStack's 50-an-hour limit, ledgered so
             it can stop and resume, capped by --max.
  emit       Print the entries that measured as worth widening.

WHAT IT CANNOT DO, said plainly
-------------------------------
It proposes ADMIN regions, not commuting zones. A metro that spans several
admin-2 units (Greater Manchester is ten boroughs; Chicago crosses counties
and states) is classified `multi-region` and left for a FUA-based pass
(GHS-FUA / OECD functional urban areas joined to GeoNames coordinates). That
needs geospatial libraries the app's environment deliberately does not carry,
so it is not attempted here.

Usage:
    python tools/metro_candidates.py build   [--min-pop 100000] [--out DIR]
    python tools/metro_candidates.py check   [--out DIR]
    python tools/metro_candidates.py measure [--country GB] [--max 20] [--out DIR]
    python tools/metro_candidates.py emit    [--out DIR]
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GEONAMES = "https://download.geonames.org/export/dump/"
API = "https://api.theirstack.com"
DEFAULT_OUT = ROOT / "build" / "metro"

#: A region more than this many times the city's own population is a different
#: place ("Cook County" round Chicago is ~3x; a whole province is 20x). Widening
#: to it would buy postings from towns the user did not ask for.
MAX_RATIO = 3.0
#: Below this the region is the city under another name (Glasgow 1.0, Bristol
#: 1.0, Chengdu 1.0): widening cannot add a posting and measuring it would only
#: spend credits to learn that.
MIN_RATIO = 1.10
#: The city must be at least this share of its region, else it is a suburb of a
#: bigger place and the region belongs to that one.
MIN_SHARE = 0.30
#: Measured gain thresholds (region postings / city postings).
#:
#: WIDEN_MIN is LOW on purpose. A region is a strict superset of its city and
#: `classify` already required it to be the same place, so widening cannot lose
#: a row; what the count guards against is an anomaly, not a small gain. And the
#: gain moves with the role mix: London measured 1.29x for "hotel manager"
#: (hotels cluster round Heathrow and the outer boroughs) but only 1.11x for
#: "manager" across every sector, so demanding a big number on a generic query
#: would reject the one entry we know is right.
#: WIDEN_MAX catches a region that is really a different, bigger place:
#: Birkenhead 19 postings against 68 for the whole Wirral borough is 3.6x.
WIDEN_MIN = 1.05
WIDEN_MAX = 3.0

#: TheirStack free tier is 50/hour; stay well inside it.
CALLS_PER_HOUR = 40
MAX_WAIT_SECONDS = 90

#: Cities whose everyday metro is several admin-2 units in the GeoNames tree
#: (the English metropolitan counties are split into boroughs). Seeded by hand,
#: from knowledge rather than data: the tool cannot discover these without the
#: FUA layer, which is exactly why they are listed rather than inferred.
KNOWN_MULTI_REGION = ("manchester", "birmingham", "leeds", "sheffield",
                      "liverpool", "newcastle upon tyne", "bradford")

PLACE_FEATURES = ("PPL", "PPLA", "PPLA2", "PPLA3", "PPLA4", "PPLA5", "PPLC",
                  "PPLG")


# ---------------------------------------------------------------------------
# build: pure classification over GeoNames rows
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    country: str
    city: str
    city_id: int
    city_pop: int
    adm1: str
    adm2_code: str
    adm2_id: int | None
    adm2_name: str
    region_pop: int
    n_places: int
    ratio: float
    verdict: str
    note: str = ""
    # filled by `check`
    city_in_catalogue: bool | None = None
    region_in_catalogue: bool | None = None
    # filled by `measure`
    city_total: int | None = None
    region_total: int | None = None
    gain: float | None = None
    decision: str = ""


def parse_places(text: str) -> list[dict]:
    """GeoNames `cities*.txt` rows, populated places only."""
    out = []
    for line in text.splitlines():
        c = line.split("\t")
        if len(c) < 15 or c[7] not in PLACE_FEATURES:
            continue
        try:
            pop = int(c[14] or 0)
        except ValueError:
            pop = 0
        out.append(dict(id=int(c[0]), name=c[1], country=c[8], adm1=c[10],
                        adm2=c[11], pop=pop, fcode=c[7]))
    return out


def parse_admin2(text: str) -> dict[str, tuple[str, int]]:
    """'GB.ENG.GLA' -> ('Greater London', 2648110)."""
    out = {}
    for line in text.splitlines():
        c = line.split("\t")
        if len(c) >= 4 and c[3].strip().isdigit():
            out[c[0]] = (c[1], int(c[3].strip()))
    return out


def classify(places: list[dict], admin2: dict[str, tuple[str, int]], *,
             min_pop: int = 100_000, max_ratio: float = MAX_RATIO,
             min_share: float = MIN_SHARE,
             min_ratio: float = MIN_RATIO) -> list[Candidate]:
    """One Candidate per city above `min_pop`, with a verdict and the reason.

    Verdicts, in the order they are tested:
      no-adm2         the country has no admin-2 level (or no id for it): the
                      region idea does not apply.
      same-place      the region IS the city (names or ids agree): nothing to add.
      subordinate     a bigger place shares the region; the region is its, not
                      this city's.
      equivalent      the region is under min_ratio x the city: the same place,
                      so widening cannot gain anything.
      too-large       the region is > max_ratio x the city: widening would pull
                      in other towns.
      multi-region    reserved for the FUA pass; set by --known-multi.
      propose         a region of comparable size that the city dominates.
    """
    by_region: dict[tuple, list[dict]] = collections.defaultdict(list)
    for p in places:
        by_region[(p["country"], p["adm1"], p["adm2"])].append(p)

    out: list[Candidate] = []
    for p in places:
        if p["pop"] < min_pop:
            continue
        key = (p["country"], p["adm1"], p["adm2"])
        code = ".".join(key)
        region = by_region[key]
        region_pop = sum(x["pop"] for x in region)
        adm2_name, adm2_id = admin2.get(code, ("", None))
        ratio = round(region_pop / p["pop"], 2) if p["pop"] else 0.0
        cand = Candidate(
            country=p["country"], city=p["name"], city_id=p["id"],
            city_pop=p["pop"], adm1=p["adm1"], adm2_code=p["adm2"],
            adm2_id=adm2_id, adm2_name=adm2_name, region_pop=region_pop,
            n_places=len(region), ratio=ratio, verdict="")
        biggest = max(region, key=lambda x: x["pop"])
        if not p["adm2"] or adm2_id is None:
            cand.verdict, cand.note = "no-adm2", "no admin-2 level for this place"
        elif adm2_id == p["id"] or adm2_name.lower() == p["name"].lower():
            cand.verdict = "same-place"
            cand.note = ("the region is the city; a wider metro, if there is "
                         "one, needs a FUA pass")
        elif biggest["id"] != p["id"] and biggest["pop"] > p["pop"]:
            cand.verdict = "subordinate"
            cand.note = f"{biggest['name']} is larger and shares this region"
        elif p["pop"] / max(region_pop, 1) < min_share:
            cand.verdict, cand.note = "subordinate", "under the share threshold"
        elif ratio < min_ratio:
            cand.verdict = "equivalent"
            cand.note = f"region is only {ratio}x the city"
        elif ratio > max_ratio:
            cand.verdict = "too-large"
            cand.note = f"region is {ratio}x the city"
        else:
            cand.verdict = "propose"
        out.append(cand)
    return sorted(out, key=lambda c: -c.city_pop)


# ---------------------------------------------------------------------------
# measure: classification of counts, and the pacing ledger
# ---------------------------------------------------------------------------

def decide(city_total: int | None, region_total: int | None) -> tuple[float | None, str]:
    """The measured verdict. Counts, not populations, decide whether to widen."""
    if city_total is None or region_total is None:
        return None, "unmeasured"
    if city_total == 0 and region_total == 0:
        return None, "no-data"
    if city_total == 0:
        return None, "city-empty"  # the city id itself matches nothing: investigate
    gain = round(region_total / city_total, 2)
    if gain < WIDEN_MIN:
        return gain, "no-gain"
    if gain > WIDEN_MAX:
        return gain, "too-wide"
    return gain, "widen"


@dataclass
class Ledger:
    """Calls made, kept on disk so a run can stop for the hour and resume."""
    path: Path
    calls: list[float] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "Ledger":
        try:
            return cls(path, list(json.loads(path.read_text())["calls"]))
        except (OSError, ValueError, KeyError):
            return cls(path, [])

    def save(self) -> None:
        self.path.write_text(json.dumps({"calls": self.calls[-500:]}))

    def wait_needed(self, now: float, per_hour: int = CALLS_PER_HOUR) -> float:
        recent = sorted(t for t in self.calls if now - t < 3600)
        if len(recent) < per_hour:
            return 0.0
        return max(0.0, recent[-per_hour] + 3600 - now)

    def record(self, now: float) -> None:
        self.calls.append(now)
        self.save()


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def _download(name: str) -> bytes:
    req = urllib.request.Request(GEONAMES + name,
                                 headers={"User-Agent": "DawnlistTools/1.0"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read()


def cmd_build(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print("downloading GeoNames cities1000 and admin2Codes ...")
    zf = zipfile.ZipFile(io.BytesIO(_download("cities1000.zip")))
    places = parse_places(zf.read("cities1000.txt").decode("utf-8"))
    admin2 = parse_admin2(_download("admin2Codes.txt").decode("utf-8"))
    known_multi = set(args.known_multi or [])
    cands = classify(places, admin2, min_pop=args.min_pop)
    for c in cands:
        if c.city.lower() in known_multi and c.verdict in (
                "propose", "no-adm2", "same-place"):
            c.verdict, c.note = "multi-region", "needs a FUA-based pass"
    (out / "candidates.json").write_text(
        json.dumps([asdict(c) for c in cands], indent=1, ensure_ascii=False),
        encoding="utf-8")
    tally = collections.Counter(c.verdict for c in cands)
    print(f"{len(cands)} cities >= {args.min_pop:,} people: "
          + ", ".join(f"{k} {v}" for k, v in tally.most_common()))
    print(f"wrote {out / 'candidates.json'}")
    return 0


def _load(out: Path) -> list[Candidate]:
    raw = json.loads((out / "candidates.json").read_text(encoding="utf-8"))
    return [Candidate(**r) for r in raw]


def _save(out: Path, cands: list[Candidate]) -> None:
    (out / "candidates.json").write_text(
        json.dumps([asdict(c) for c in cands], indent=1, ensure_ascii=False),
        encoding="utf-8")


def _key() -> str:
    import keyring
    key = keyring.get_password("dawnlist-feed", "api-key")
    if not key:
        raise SystemExit("no TheirStack developer key under keyring "
                         "'dawnlist-feed' / 'api-key'")
    return key


def _request(path: str, *, params=None, body=None, key: str) -> dict:
    from app.core.http import build_request
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    data = json.dumps(body).encode() if body is not None else None
    req = build_request(url, data=data, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)


def cmd_check(args) -> int:
    """Which proposed ids does the TheirStack catalogue actually hold?"""
    out = Path(args.out)
    cands = _load(out)
    only = {x.upper() for x in (args.country or [])}
    todo = [c for c in cands if c.verdict == "propose"
            and (c.city_in_catalogue is None or c.region_in_catalogue is None)
            and (not only or c.country in only)]
    if not todo:
        print("nothing to check")
        return 0
    ids = sorted({c.city_id for c in todo} | {c.adm2_id for c in todo if c.adm2_id})
    ledger = Ledger.load(out / "ledger.json")
    key = _key()
    found: set[int] = set()
    batch = 100
    for i in range(0, len(ids), batch):
        chunk = ids[i:i + batch]
        wait = ledger.wait_needed(time.time())
        if wait > MAX_WAIT_SECONDS:
            print(f"rate limit: resume in {int(wait // 60)} min "
                  f"({i} of {len(ids)} ids checked)")
            break
        if wait:
            time.sleep(wait)
        rows = _request("/v0/catalog/locations", key=key,
                        params={"id": chunk, "limit": len(chunk)})
        ledger.record(time.time())
        rows = rows if isinstance(rows, list) else rows.get("result", rows.get("data", []))
        found.update(int(r["id"]) for r in rows)
        for c in todo:
            if c.city_id in chunk:
                c.city_in_catalogue = c.city_id in found
            if c.adm2_id in chunk:
                c.region_in_catalogue = c.adm2_id in found
        _save(out, cands)
    else:
        for c in todo:
            c.city_in_catalogue = c.city_id in found
            c.region_in_catalogue = c.adm2_id in found
        _save(out, cands)
    ready = [c for c in cands if c.verdict == "propose"
             and c.city_in_catalogue and c.region_in_catalogue]
    gone = [c for c in cands if c.verdict == "propose"
            and (c.city_in_catalogue is False or c.region_in_catalogue is False)]
    print(f"{len(ready)} proposals fully in the catalogue; {len(gone)} lose an id:")
    for c in gone[:15]:
        print(f"  {c.city} ({c.country}): city {'ok' if c.city_in_catalogue else 'MISSING'}"
              f", region {c.adm2_name} {'ok' if c.region_in_catalogue else 'MISSING'}")
    return 0


def _count(key: str, country: str, location_id: int) -> int:
    body = {"job_title_or": ["manager"], "job_country_code_or": [country],
            "job_location_or": [{"id": location_id}],
            "posted_at_max_age_days": 30, "is_closed": False,
            "include_total_results": True, "limit": 1}
    payload = _request("/v1/jobs/search", body=body, key=key)
    return int((payload.get("metadata") or {}).get("total_results") or 0)


def _paced(ledger: Ledger, fn):
    """Run one API call, waiting for a slot; None means 'stop, resume later'."""
    wait = ledger.wait_needed(time.time())
    if wait > MAX_WAIT_SECONDS:
        return None
    if wait:
        time.sleep(wait)
    result = fn()
    ledger.record(time.time())
    return result


def cmd_measure(args) -> int:
    out = Path(args.out)
    cands = _load(out)
    todo = [c for c in cands if c.verdict == "propose"
            and c.city_in_catalogue and c.region_in_catalogue
            and c.decision in ("", "unmeasured")
            and (not args.country or c.country == args.country.upper())]
    if not todo:
        print("nothing to measure (run `check` first, or all are measured)")
        return 0
    ledger = Ledger.load(out / "ledger.json")
    key = _key()
    done = 0
    for c in todo[: args.max]:
        if c.city_total is None:
            c.city_total = _paced(ledger, lambda: _count(key, c.country, c.city_id))
        if c.city_total is not None and c.region_total is None:
            c.region_total = _paced(
                ledger, lambda: _count(key, c.country, c.adm2_id))
        _save(out, cands)
        if c.city_total is None or c.region_total is None:
            print(f"rate limit: resume later; {done} measured this run")
            return 0
        c.gain, c.decision = decide(c.city_total, c.region_total)
        _save(out, cands)
        done += 1
        print(f"  {c.city:<22} {c.country} city {c.city_total:>5}  "
              f"{c.adm2_name[:28]:<28} {c.region_total:>5}  x{c.gain}  {c.decision}")
    return 0


def emit_entries(cands: list[Candidate]) -> str:
    """Reviewed-entry snippet for METRO_REGIONS. Printed, never applied."""
    by_country: dict[str, list[Candidate]] = collections.defaultdict(list)
    for c in cands:
        if c.decision == "widen":
            by_country[c.country].append(c)
    lines = []
    for country in sorted(by_country):
        entries = ", ".join(
            f"{json.dumps(c.city.lower(), ensure_ascii=False)}: {c.adm2_id}"
            for c in sorted(by_country[country], key=lambda c: c.city))
        lines.append(f"  {country}: {{ {entries} }},")
        for c in sorted(by_country[country], key=lambda c: c.city):
            lines.append(f"  // {c.city}: {c.adm2_name} — city {c.city_total} vs "
                         f"region {c.region_total} (x{c.gain}), measured "
                         f"{time.strftime('%Y-%m-%d')}")
    return "\n".join(lines)


def cmd_emit(args) -> int:
    cands = _load(Path(args.out))
    for c in cands:
        # Recomputed from the stored counts, so a threshold change applies to
        # measurements already paid for instead of asking for them again.
        if c.city_total is not None and c.region_total is not None:
            c.gain, c.decision = decide(c.city_total, c.region_total)
    text = emit_entries(cands)
    if not text:
        print("no entry has measured as worth widening yet")
        return 0
    print("// Paste into METRO_REGIONS in src/index.js AFTER reading the counts.")
    print(text)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("build", cmd_build), ("check", cmd_check),
                     ("measure", cmd_measure), ("emit", cmd_emit)):
        p = sub.add_parser(name)
        p.add_argument("--out", default=str(DEFAULT_OUT))
        p.set_defaults(fn=fn)
        if name == "build":
            p.add_argument("--min-pop", type=int, default=100_000)
            p.add_argument("--known-multi", nargs="*", default=list(KNOWN_MULTI_REGION),
                           help="cities known to span several admin-2 units")
        if name == "check":
            p.add_argument("--country", nargs="*", default=[],
                           help="limit to these country codes (the catalogue "
                                "check spends rate-limit calls)")
        if name == "measure":
            p.add_argument("--country", default="")
            p.add_argument("--max", type=int, default=20)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
