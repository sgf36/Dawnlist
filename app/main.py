"""Dawnlist entry point.

Two ways in, and they share everything below the surface:

    python -m app.main                 # the app
    python -m app.main --run-once      # the morning run, headless
    python -m app.main --board         # open on the board
    python -m app.main --audit         # print the board audit and exit

The headless form exists so a scheduled run and a hand-run produce identical
state — a scheduler that drives a different code path is a second system that
drifts from the first.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.core import db
from app.core.pipeline import (Gate, permanent_reject_gate, persist,
                               posted_within_gate, run_morning)
from app.core.rules import RuleTable
from app.core.search_scope import (SearchPlan, SearchScope, load_scope,
                                   parse_where, save_scope, scope_gates)
from app.feed.base import SearchQuery
from app.i18n import set_locale, tr

DEFAULT_POSTED_WITHIN_DAYS = 45

#: One page per search per run. A search matching more than this is too broad
#: to be read, and paging on would spend the day's allowance on postings nobody
#: reaches. The run reports the shortfall so the user narrows the search; the
#: app never quietly buys more.
MAX_RESULTS_PER_SEARCH = 100


def _force_utf8_console() -> None:
    """The Windows console is cp1252 and job titles are not.

    Measured in P0: a run died on the first en-dash AFTER the credits had
    already been spent. `--run-once` prints titles, screen reasons and verdicts,
    so this applies to BOTH streams — the first version of this file fixed only
    stdout and an em-dash on stderr still came out as a replacement character.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001 - never block startup on this
                pass


_force_utf8_console()


class NotConfigured(RuntimeError):
    """Raised when a run is attempted before onboarding has produced a brief.

    Deliberately loud. The calibration gate is the transfer-of-judgement step
    that made the original system work, and a run against an empty brief would
    produce a plausible-looking shortlist built on nothing.
    """


def load_settings(conn) -> dict[str, str]:
    return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}


def load_document(conn, kind: str) -> str:
    """The latest version of the fit brief or factsheet."""
    row = conn.execute(
        "SELECT body FROM documents WHERE kind=? ORDER BY version DESC LIMIT 1",
        (kind,)).fetchone()
    return row["body"] if row else ""


#: How many already-held job ids to send with each query, newest first.
#:
#: The delta pull (`discovered_at_gte`) is the primary billing control and
#: handles the ordinary case. This is the second line, and it earns its place
#: on the day the delta mark FAILS TO ADVANCE — which happens deliberately
#: after a failed fetch, so a broken run is never recorded as complete. The
#: next run then re-requests the same window and, without this, re-buys every
#: row in it: ~513 postings at the measured rate.
#:
#: 1,000 is about two days' worth. Larger stops paying for itself, because the
#: list is sent in full with every query in every run.
RECENT_HELD_IDS = 1000


def load_queries(conn) -> list[SearchQuery]:
    import json

    # Rows we have ALREADY BEEN BILLED FOR. The provider does not cache, so a
    # row we hold is re-bought whenever it comes back. Ordered by id DESC
    # because that is insertion order, and insertion order is recency here —
    # `jobs` carries no discovered-at column of its own.
    #
    # Only the FEED's own ids, and only numeric ones. The list is forwarded to
    # TheirStack's `job_id_not`, which is typed as integers, and `jobs` also
    # holds postings the feed never issued: a pasted advert's id is a hash of
    # what the user pasted, and an alert email's is LinkedIn's number. Sent,
    # the first is at best ignored and at worst fails validation for the whole
    # search, and the second silently excludes whichever TheirStack posting
    # happens to share that number. Neither was ever billed, so neither can be
    # re-bought.
    held = tuple(str(r["provider_job_id"]) for r in conn.execute(
        "SELECT provider_job_id FROM jobs "
        " WHERE provider = 'theirstack' AND provider_job_id <> '' "
        "   AND provider_job_id NOT GLOB '*[^0-9]*' "
        " ORDER BY id DESC LIMIT ?",
        (RECENT_HELD_IDS,)))

    out: list[SearchQuery] = []
    for row in conn.execute("SELECT * FROM queries WHERE enabled=1 ORDER BY id"):
        params = json.loads(row["params_json"])
        scope = SearchScope.from_params(params)
        if not scope.is_set:
            # Switched on before a location was required. Never swept, because
            # it would look across the whole world; `morning_run` names it
            # instead, so the user can say where they want to work.
            continue
        since = None
        if row["last_discovered_at"]:
            try:
                since = datetime.fromisoformat(row["last_discovered_at"])
            except ValueError:
                since = None
        out.append(SearchQuery(
            label=row["label"],
            titles=params.get("titles", []),
            countries=list(scope.countries),
            cities=list(scope.cities),
            exclude_title_terms=list(scope.exclude_title_terms),
            exclude_companies=list(scope.exclude_companies),
            companies=params.get("companies", []),
            posted_within_days=params.get("posted_within_days",
                                          DEFAULT_POSTED_WITHIN_DAYS),
            discovered_since=since,
            exclude_job_ids=held,
            max_results=params.get("max_results", MAX_RESULTS_PER_SEARCH),
        ))
    return out


def enabled_scopes(conn) -> list[SearchScope]:
    """The scope of every search a run will sweep, for the post-fetch gates."""
    scopes = [SearchScope.from_params(json.loads(r["params_json"]))
              for r in conn.execute(
                  "SELECT params_json FROM queries WHERE enabled=1")]
    return [s for s in scopes if s.is_set]


def unscoped_enabled_labels(conn) -> list[str]:
    return [r["label"] for r in conn.execute(
                "SELECT label, params_json FROM queries WHERE enabled=1 "
                "ORDER BY id")
            if not SearchScope.from_params(json.loads(r["params_json"])).is_set]


def load_rules(conn) -> RuleTable:
    import json

    from app.core.rules import KillFamily, KillFamilyError

    table = RuleTable()
    for row in conn.execute("SELECT field, term FROM rule_terms"):
        getattr(table, row["field"]).append(row["term"])

    for row in conn.execute("SELECT * FROM kill_families"):
        try:
            table.kill_families.append(KillFamily(
                name=row["name"],
                employers=tuple(json.loads(row["employers_json"])),
                kill_titles=tuple(json.loads(row["kill_json"])),
                saves_titles=tuple(json.loads(row["saves_json"])),
                precedents=tuple(tuple(p) for p in
                                 json.loads(row["precedents_json"])),
                adopted=bool(row["adopted"]),
            ))
        except KillFamilyError as exc:
            # A stored family that no longer satisfies the rules is skipped
            # LOUDLY rather than silently dropped or silently applied.
            print(f"warning: kill family {row['name']!r} is invalid and was "
                  f"not loaded: {exc}", file=sys.stderr)

    # Tier 2 is DERIVED, never typed in. `known_employers` is defined as
    # "employers the user has actually pursued", and the pursue decisions are
    # already recorded — so the rule earns itself from evidence the user
    # generated by working the shortlist, which is what "every entry is
    # earned" was always supposed to mean. Deriving it also means it cannot
    # drift: an employer stops being known when the decision is revised.
    for row in conn.execute(
            "SELECT DISTINCT j.company FROM decisions d "
            "JOIN jobs j ON j.id = d.job_id "
            "WHERE d.kind = 'pursue' AND j.company <> ''"):
        if row["company"] not in table.known_employers:
            table.known_employers.append(row["company"])
    return table


RULE_FIELDS = ("unsupported_titles", "strong_terms", "contextual_terms")


def save_rule_term(conn, field: str, term: str) -> None:
    """Add one screening term.

    `known_employers` is deliberately not writable here: it is derived from
    pursue decisions in `load_rules`, and a hand-typed copy would drift from
    the decisions the moment one was revised.

    A tier-1 term is checked against the user's own pursue history before it
    is admitted. spec 5.3: a kill term that would have removed a role they
    actually chased is not a rule, it is a mistake about to repeat itself.
    """
    from app.core.rules import assert_no_conflicts

    field = field.strip()
    term = term.strip()
    if field not in RULE_FIELDS:
        raise ValueError(f"unknown rule field {field!r}; expected one of "
                         f"{', '.join(RULE_FIELDS)}")
    if not term:
        raise ValueError("a rule term cannot be empty")

    # Admission-time, and against the table the term would JOIN rather than the
    # term alone: a guard that only sees the new term cannot tell that the
    # table as a whole would now remove a role the user chased.
    proposed = load_rules(conn)
    getattr(proposed, field).append(term)
    pursued = [(r["company"], r["title"]) for r in conn.execute(
        "SELECT j.company, j.title FROM decisions d "
        "JOIN jobs j ON j.id = d.job_id WHERE d.kind = 'pursue'")]
    assert_no_conflicts(proposed, pursued)

    conn.execute(
        "INSERT INTO rule_terms(field, term, added_at) VALUES(?,?,?) "
        "ON CONFLICT DO NOTHING",
        (field, term, datetime.now(timezone.utc).isoformat(timespec="seconds")))
    conn.commit()


def forget_rule_term(conn, field: str, term: str) -> None:
    conn.execute("DELETE FROM rule_terms WHERE field=? AND term=?",
                 (field, term.strip()))
    conn.commit()



def decisions_by_kind(conn, kind: str) -> list[tuple[str, str]]:
    """Every (company, title) the user decided this way."""
    return [(r["company"], r["title"]) for r in conn.execute(
        "SELECT j.company, j.title FROM decisions d "
        "JOIN jobs j ON j.id = d.job_id WHERE d.kind = ?", (kind,))]


def refresh_kill_family_proposals(conn) -> int:
    """Notice the shapes in the user's rejections and offer them. Adopts none.

    A family is never typed in: spec 5.3 rule 1 anchors one to at least two
    real rejections and rule 8 makes adoption the user's decision, so the app's
    job is to spot the shape and offer it. `kill_families` was the last table
    the app read and never wrote, which left tier 1b reachable only by editing
    SQLite by hand.

    Re-proposing is safe: a family already present keeps its `adopted` flag, so
    a running of this never quietly re-arms one the user turned down.
    """
    from app.core.rules import propose_families

    table = load_rules(conn)
    families = propose_families(
        rejected=decisions_by_kind(conn, "reject"),
        pursued=decisions_by_kind(conn, "pursue"),
        saves_terms=table.strong_terms + table.contextual_terms)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    added = 0
    for fam in families:
        cur = conn.execute(
            """INSERT INTO kill_families(name, employers_json, kill_json,
                   saves_json, precedents_json, adopted, created_at)
               VALUES(?,?,?,?,?,0,?)
               ON CONFLICT(name) DO UPDATE SET
                   kill_json = excluded.kill_json,
                   saves_json = excluded.saves_json,
                   precedents_json = excluded.precedents_json""",
            (fam.name, json.dumps(list(fam.employers)),
             json.dumps(list(fam.kill_titles)), json.dumps(list(fam.saves_titles)),
             json.dumps([list(p) for p in fam.precedents]), now))
        added += cur.rowcount
    conn.commit()
    return added


def adopt_kill_family(conn, name: str, *, adopted: bool = True) -> None:
    """Arm a proposed family, or stand one down.

    Checked against the pursue history first, exactly like a tier-1 term: a
    family that would have removed a role the user chased is not a rule, and
    the moment to find that out is before it fires rather than after a role
    goes missing.
    """
    from app.core.rules import assert_no_conflicts

    if adopted:
        proposed = load_rules(conn)
        for fam in proposed.kill_families:
            if fam.name == name:
                # Test it AS IF ARMED. An unadopted family never matches, so
                # checking the stored copy would pass every family ever written.
                armed = replace(fam, adopted=True)
                probe = RuleTable(kill_families=[armed])
                assert_no_conflicts(probe, decisions_by_kind(conn, "pursue"))
                break
        else:
            raise LookupError(f"no kill family named {name!r}")

    conn.execute("UPDATE kill_families SET adopted=? WHERE name=?",
                 (int(adopted), name))
    conn.commit()



def save_query(conn, label: str, titles: list[str], *,
               countries: list[str] | None = None,
               scope: SearchScope | None = None,
               enabled: bool = True,
               posted_within_days: int = DEFAULT_POSTED_WITHIN_DAYS) -> None:
    """Store one saved search.

    Nothing created a `queries` row before this. `load_queries` returned an
    empty list, `morning_run` refused with "No saved queries. Add at least one
    before running", and there was no way to add one — so the run could never
    happen at all. The table audit missed it because `mark_queries_run` UPDATEs
    the table: an update is not a create, and a table can be read, written and
    still never gain a row.
    """
    label = label.strip()
    titles = [t.strip() for t in titles if t.strip()]
    scope = scope or SearchScope.from_parts(countries=countries)
    countries = list(scope.countries)
    if not label:
        raise ValueError("a search needs a name")
    if not titles:
        raise ValueError("a search needs at least one job title to look for")
    if not countries and enabled:
        # SCOPE IS A COST CONTROL, and this one is measured rather than assumed.
        #
        # On the 2,000-row sample, an unscoped global sweep put 7.6% of fetched
        # rows in front of the user. Scoped to one country that doubles to
        # 13.7% — the same money buys nearly twice the relevant postings, so
        # this refusal costs the user nothing they wanted.
        #
        # WHAT THIS IS NOT: an industry or seniority filter. Narrowing THOSE at
        # source was measured on the same sample and keeps only 48% of the
        # postings that reach assessment while cutting the corpus to 32% — it
        # buys cost by losing real roles, which is the opposite trade. Do not
        # add one here because it looks like the same idea.
        #
        # A genuinely global search is still available and still supported: name
        # the regions. The requirement is that breadth be DELIBERATE, not the
        # default that a blank field silently produces.
        raise ValueError(
            "a search needs at least one country, so it does not sweep the "
            "whole world by accident. Naming where you are looking roughly "
            "doubles how many of the postings you pay for are ones you would "
            "actually read. To search widely, list the countries you mean.")

    conn.execute(
        """INSERT INTO queries(label, params_json, enabled, created_at)
           VALUES(?,?,?,?)
           ON CONFLICT(label) DO UPDATE SET params_json = excluded.params_json""",
        (label, json.dumps({"titles": titles,
                            **scope.as_params(),
                            "posted_within_days": posted_within_days}),
         int(enabled),
         datetime.now(timezone.utc).isoformat(timespec="seconds")))
    conn.commit()


def forget_query(conn, label: str) -> None:
    conn.execute("DELETE FROM queries WHERE label = ?", (label.strip(),))
    conn.commit()


def enable_query(conn, label: str, *, enabled: bool = True) -> None:
    label = label.strip()
    if enabled:
        # THE CHECK HAS TO LIVE AT THE SWITCH TOO. `save_query` refuses an
        # unscoped search that is switched on, but seeds are saved switched OFF
        # and this UPDATE was the only way to turn one on — so every search
        # switched on during setup looked across the whole world, and every
        # posting it returned was paid for.
        row = conn.execute("SELECT params_json FROM queries WHERE label=?",
                           (label,)).fetchone()
        if row is not None and not SearchScope.from_params(
                json.loads(row["params_json"])).is_set:
            raise ValueError(tr("searches.needs_where", label=label))
    conn.execute("UPDATE queries SET enabled=? WHERE label=?",
                 (int(enabled), label))
    conn.commit()


def apply_scope(conn, scope: SearchScope) -> int:
    """Give every saved search this scope, and keep it for searches added later.

    The repair for installs set up before a location was required: one entry in
    Settings rescopes every search at once, rather than a refused run per search.

    The delta mark is cleared because it records how far the OLD area was read.
    Kept, it would skip every posting in a newly added area that was indexed
    before today; cleared, the next run reads the posted-within window once.
    """
    save_scope(conn, scope)
    rows = conn.execute("SELECT label, params_json FROM queries").fetchall()
    for row in rows:
        params = json.loads(row["params_json"])
        params.update(scope.as_params())
        conn.execute("UPDATE queries SET params_json=?, last_discovered_at=NULL "
                     "WHERE label=?", (json.dumps(params), row["label"]))
    conn.commit()
    return len(rows)


def save_new_search(conn, label: str, titles: list[str]) -> None:
    """A search added in Settings, on the install's own scope."""
    scope = load_scope(conn)
    if not scope.is_set:
        raise ValueError(tr("searches.needs_where_first"))
    save_query(conn, label, titles, scope=scope)


def all_queries(conn) -> list[tuple[str, list[str], bool]]:
    """Every saved search, enabled or not.

    `load_queries` returns only the enabled ones, because that is what a run
    should sweep — but the screen has to show the rest, or a search the user
    turned off looks like one the app lost.
    """
    out = []
    for row in conn.execute("SELECT label, params_json, enabled FROM queries "
                            "ORDER BY label"):
        params = json.loads(row["params_json"])
        out.append((row["label"], params.get("titles", []), bool(row["enabled"])))
    return out


def seed_queries_from_aim(conn, aim: str, *, send=None, titles=None,
                          scope: SearchScope | None = None,
                          brief: str = "") -> int:
    """Give a new user somewhere to start — turned OFF.

    Billing is per job returned, so a seed nobody read is a seed nobody should
    be charged for. The seeds arrive disabled and the user switches on the
    ones that are actually searches.

    WHAT THEY ARE OFFERED HAS TO BE A SEARCH. The first version split the
    typed text on punctuation and kept any run of two to five words, which
    produced this, verbatim, on a real machine:

        [ ] including the underwriting-to-property-implementation seam
        [ ] three kinds investment

    on a screen whose own text says each one costs money per posting it
    returns. Nobody would tick those; the damage is that the user learns at
    the first screen that the app did not understand a word they wrote.

    So the titles come from the model when a key is available — it is a small,
    cheap extraction against a schema — and from `titles_from_aim` when it is
    not. The fallback now refuses anything that does not look like a title,
    and returning NOTHING is a valid answer: the searches page explains how to
    add one, and an empty list is honest where a nonsense list is not.

    `titles` is what the interview screen already worked out in the background
    while the user was reading their draft. Passing it in is what keeps the
    model call off the click: without it this would run the extraction at the
    moment Next is pressed, and the window would stop answering for the length
    of a network round trip.
    """
    if titles is None:
        plan = search_plan(aim, brief=brief, send=send)
        titles, scope = plan.titles, scope or plan.scope
    scope = scope or SearchScope()
    if scope.is_set:
        save_scope(conn, scope)
    else:
        # Setup run again, or a plan that named no location: the scope already
        # kept for this install still applies, so a re-run does not produce a
        # fresh set of searches that look across the whole world.
        stored = load_scope(conn)
        if stored.is_set:
            scope = scope.with_location(stored)

    existing = {label for label, _titles, _on in all_queries(conn)}
    added = 0
    for phrase in titles:
        if phrase.lower() in {e.lower() for e in existing}:
            continue
        save_query(conn, phrase, [phrase], scope=scope, enabled=False)
        existing.add(phrase)
        added += 1
    return added


def search_plan(aim: str, *, brief: str = "", send=None) -> SearchPlan:
    """The search to seed setup with. The model when there is one; rules only
    when there is not.

    `send` returns the reply TEXT (see `build_send`). This used to hand that
    text to `text_of`, which reads `.content`, so every call raised, the
    `except` swallowed it, and every user was offered the rule-based split —
    "full-time roles based", cut out of "full-time roles based in London".
    Nothing reported it, and no test ever reached the model path.
    """
    if send is None:
        return SearchPlan(titles=titles_from_aim(aim))
    if not (aim or "").strip() and not (brief or "").strip():
        return SearchPlan()
    try:
        from app.onboarding.interview import build_search_plan_request, text_of

        reply = send(build_search_plan_request(aim, brief))
        data = json.loads(reply if isinstance(reply, str) else text_of(reply))
    except Exception:  # noqa: BLE001
        # NOTHING, rather than the rule-based split. The split is what put
        # sentence fragments on the searches screen; an empty list is honest,
        # and that screen says how to add a search.
        return SearchPlan()
    titles = [t.strip() for t in data.get("titles") or []
              if isinstance(t, str) and looks_like_a_title(t.strip())]
    return SearchPlan(titles=titles[:8], scope=SearchScope.from_params(data))


#: Splitting on punctuation and on the words that join clauses. Written
#: without regex escapes on purpose: these boundaries are ordinary
#: punctuation, and a pattern nobody can read is a pattern nobody will
#: correct.
SEED_SEPARATORS = (",", ".", ";", chr(10), " and ", " or ")


#: Words that start a clause, not a job title. A fragment beginning with one
#: of these is prose the split happened to cut, never a role.
CLAUSE_OPENERS = frozenset("""
including include includes especially particularly ideally preferably
plus also with without within across around about above below before after
because since while whilst although though however whereas
the a an my our their his her its this that these those
and or but nor for yet so
i we they you it there here what which who whom whose when where why how
am is are was were be been being have has had do does did
looking seeking wanting hoping trying aiming interested keen happy open
""".split())

#: Words that are never part of a job title, wherever they appear. A fragment
#: containing one is a sentence, not a role.
NOT_IN_A_TITLE = frozenset("""
including especially particularly ideally preferably whilst although though
however whereas because since myself really quite very rather somewhat
kinds sort sorts kind seam seams thing things stuff area areas side sides
""".split())

#: Longest a single word in a job title plausibly gets. "Head", "Director",
#: "Underwriting" are titles; "underwriting-to-property-implementation" is a
#: phrase somebody typed while thinking aloud.
MAX_TITLE_WORD = 18

#: Everything from one of these onwards is where the role is, not what it is.
#: "Hotel asset management IN LONDON" is one search and one location, and the
#: location costs money for nothing — no extractor can tell it from a title,
#: which is the original reason the seeds ship switched off.
#:
#: "of" is deliberately absent: "Head of Revenue" and "Director of Operations"
#: are titles, and cutting at "of" would leave "Head" and "Director".
LOCATION_PREPOSITIONS = frozenset(
    "in at near around across within throughout for with on".split())


def trim_to_title(phrase: str) -> str:
    """Drop the trailing where/why clause, keeping the role.

    Without this a perfectly good fragment is thrown away whole for being one
    word too long: "Hotel asset management in London" is five words and so was
    rejected outright, when the first three are exactly the search wanted.
    """
    import re

    words = re.findall("[A-Za-z][A-Za-z&/'-]*", phrase or "")
    for i, word in enumerate(words):
        if word.lower() in LOCATION_PREPOSITIONS:
            return " ".join(words[:i])
    return " ".join(words)


def looks_like_a_title(phrase: str) -> bool:
    """Whether this is plausibly something a job advert is headed with.

    Deliberately strict, and biased towards rejecting. A rejected title costs
    the user nothing — the searches page tells them how to add their own — and
    an accepted one that is not a title costs money the moment it is ticked,
    and costs trust before that.
    """
    import re

    words = re.findall("[A-Za-z][A-Za-z&/'-]*", phrase or "")
    if not 2 <= len(words) <= 4:
        return False
    lowered = [w.lower() for w in words]
    if lowered[0] in CLAUSE_OPENERS:
        return False
    if any(w in NOT_IN_A_TITLE for w in lowered):
        return False
    if any(len(w) > MAX_TITLE_WORD for w in words):
        return False
    # A title is mostly content words. Three joining words out of four is a
    # sentence fragment however it is spelled.
    if sum(1 for w in lowered if w in CLAUSE_OPENERS) > len(words) // 2:
        return False
    return True


def titles_from_aim(aim: str) -> list[str]:
    """Titles pulled out of what the user typed, WITHOUT a model.

    The fallback for a machine with no API key yet. It now returns nothing
    rather than something wrong: every candidate has to survive
    `looks_like_a_title`, and an empty result is a valid answer — the searches
    page explains how to add one, whereas a nonsense search on a screen that
    says each one costs money teaches the user the app misread them.
    """
    parts = [aim or ""]
    for sep in SEED_SEPARATORS:
        parts = [bit for part in parts for bit in part.split(sep)]

    seeds: list[str] = []
    seen: set[str] = set()
    for part in parts:
        phrase = trim_to_title(part)
        if looks_like_a_title(phrase) and phrase.lower() not in seen:
            seen.add(phrase.lower())
            seeds.append(phrase)
    return seeds[:5]

def mark_queries_run(conn, marks: dict[str, datetime]) -> None:
    """Advance the delta high-water mark of each query that fetched.

    handoff 2.1a: billing is per job RETURNED, so re-fetching yesterday's
    postings is re-buying them. `marks` is `RunOutcome.marks`: label -> when
    that query's own fetch began. Every enabled query used to be stamped with
    the end of the run, and only when no query had any problem, so one broken
    search held every search's window where it was.
    """
    db.advance_query_marks(conn, marks)


def build_provider(conn):
    """The feed provider, from the stored settings.

    ORDER MATTERS, and the managed route wins.

    A Dawnlist licence means the user is on a paid plan whose feed is metered
    and capped server-side, and going through the Worker is what makes that
    plan real: the caps, the cross-user cache and the ability to change
    provider without a release all live there. Preferring a raw provider key
    when both are present would take a subscriber's money and then bypass
    everything they are paying for — and spend Spencer's credits off-meter.

    A raw `dawnlist-feed` key is the DEVELOPER path. It is what the coverage
    replays and Spencer's own tooling use, and it is deliberately the fallback
    rather than the default: bring-your-own-feed was assessed and dropped
    (roughly two and a half times the managed price for half the allowance), so
    no customer should ever be on it.
    """
    import keyring

    from app.core.entitlement import stored_licence

    from app.core.build_variant import variant

    build = variant()

    # APPLE GUIDELINE 3.1.1 NAMES LICENCE KEYS EXPLICITLY:
    #
    #   "Apps may not use their own mechanisms to unlock content or
    #    functionality, such as license keys, augmented reality markers,
    #    QR codes, cryptocurrencies and cryptocurrency wallets, etc."
    #
    # The Settings screen already hides the licence panel on a MAS build, so
    # there is no way to TYPE one in. That is not sufficient, because the
    # keyring is per USER and not per application: someone who ran the
    # direct-download build and later installed from the Mac App Store still
    # has a licence sitting in their credential store, and without this the
    # MAS build would find it and quietly use it — unlocking a subscription
    # bought outside Apple's commerce, which is the exact prohibited shape.
    #
    # It is not a hypothetical path. It is what happens to anyone who tries
    # the direct build first, which is precisely who buys from the store next.
    #
    # Windows is different and deliberately not covered here: Microsoft permits
    # third-party commerce, subject to declaring it in Partner Center.
    if build == "mas":
        # The ONLY entitlement route Apple permits here. The receipt is written
        # into the bundle by the App Store, is never seen or typed by the user,
        # and cannot be obtained any other way — so the entitlement originates
        # with Apple throughout, which is what a stored key would not.
        #
        # A stored licence is deliberately NOT consulted, even if one exists:
        # the keyring is per user, so somebody who ran the direct build first
        # still has one, and honouring it here would unlock an Apple build with
        # a subscription bought outside Apple's commerce.
        # ONE EXCEPTION, AND ONLY ONE: a licence GRANTED BY A CODE.
        #
        # 3.1.1 forbids unlocking content with a key in place of Apple's
        # commerce. A comp code is not a purchase — nothing was bought, here or
        # anywhere — so honouring one sells nothing outside the App Store. It
        # is how a reviewer, a friend or the developer gets in, and Wren
        # already ships exactly this distinction on Apple.
        #
        # THE DISTINCTION IS THE SERVER'S TO MAKE, never this machine's. The
        # client cannot tell one licence string from another; the Worker knows,
        # because `licence_roles.from_code` records a grant and
        # `licences.paddle_subscription_id` records a purchase. A PURCHASED
        # licence in the keyring is still refused here, which is the case the
        # guard above was written for: whoever ran the direct build first still
        # has one.
        from app.core.entitlement import exchange_mac_receipt, licence_details
        from app.core.mac_receipt import read_receipt

        granted = stored_licence()
        if granted:
            # `licence_details` answers dict / False / None — a grant, a
            # refusal, or an unreachable server. Only the first can pass here:
            # "could not ask" must never resolve to "probably a grant".
            detail = licence_details(granted)
            if (isinstance(detail, dict)
                    and detail.get("granted_by_code")
                    and not detail.get("purchased")):
                from app.feed.managed import ManagedProvider
                return ManagedProvider(granted)

        receipt = read_receipt()
        if receipt is None:
            raise NotConfigured(
                "This copy has no App Store receipt yet, so there is no job "
                "feed to read. Your board, your brief and everything already "
                "on this machine stay open.")
        mac_licence = exchange_mac_receipt(receipt)
        if not mac_licence:
            raise NotConfigured(
                "The App Store could not confirm an active subscription, so "
                "there is no job feed to read. If you have just subscribed it "
                "can take a moment. Everything already on this machine stays "
                "open.")
        from app.feed.managed import ManagedProvider
        return ManagedProvider(mac_licence)

    licence = stored_licence()
    if licence:
        from app.feed.managed import ManagedProvider
        return ManagedProvider(licence)

    from app.feed.theirstack import TheirStackProvider

    key = keyring.get_password("dawnlist-feed", "api-key")
    if key:
        return TheirStackProvider(key)

    # No licence and no developer key. WHAT TO SAY DEPENDS ON THE BUILD, and
    # getting it wrong is worse than saying nothing.
    #
    # A store build reaches here with the entitlement gate already satisfied —
    # `entitlement.require` treats a store build as entitled BY POSSESSION,
    # because the storefront does not hand the binary to someone who has not
    # bought it. That reasoning holds for a one-time purchase and does NOT hold
    # here: the feed is metered per licence server-side, so possession alone
    # gives the app nothing to meter against and no way to fetch. The customer
    # has paid and cannot run.
    #
    # Telling that person to "enter your licence key" is advice they cannot
    # act on — no key was ever issued to them — and telling them to put a
    # provider key in their keyring is advice for a product they did not buy.
    if build == "store":
        # Windows Store: the licence box IS shown (Microsoft permits
        # third-party commerce), so this is something the user can act on
        # rather than a fault to report.
        raise NotConfigured(
            "No licence key yet, so there is no job feed to read. Enter the "
            "key from your subscription email in Settings. Your board, your "
            "brief and everything already on this machine stay open.")

    raise NotConfigured(
        "No licence key found. Enter the key from your purchase email in "
        "Settings. (Development builds may instead store a provider key under "
        "the keyring service 'dawnlist-feed', account 'api-key'.)")


def save_locale(conn, code: str) -> str:
    """Store the chosen language. THE WRITE THAT DID NOT EXIST.

    `settings["locale"]` was read in three places — the app's own strings, the
    alert-email parser and the voice profile — and written by nothing. The
    only way to select a language was `--locale` on the command line, which no
    Store customer has. Fifty catalogues shipped and every user saw English,
    including the ones who could not read the setting that would have fixed
    it, had one existed.

    Applied immediately so anything built after this point is translated, and
    stored so the rest catches up on the next launch. Qt does not retranslate
    a widget tree that already exists, and rebuilding one mid-wizard would
    throw away what the user has typed into it.
    """
    from app.i18n import LOCALE_CODES

    if code not in LOCALE_CODES:
        raise ValueError(f"unknown locale {code!r}")
    conn.execute(
        "INSERT INTO settings(key, value) VALUES('locale', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (code,))
    conn.commit()
    set_locale(code)
    return code


def wire_quick_menu(source, conn, *, parent=None, on_subscribe=None) -> None:
    """Give a ⋯ menu somewhere to send its three requests.

    `source` is whatever carries the signals — the QuickMenu button on the
    setup wizard, the window itself where the menu lives in the menu bar.
    Taking either means the two surfaces share this wiring rather than
    growing a copy each.
    """
    from PySide6.QtWidgets import QMessageBox

    from app.i18n import SUPPORTED_LOCALES, tr

    def chose(code: str) -> None:
        native = next((n for c, _e, n in SUPPORTED_LOCALES if c == code), code)
        save_locale(conn, code)
        # Said plainly rather than pretended: the menu changed the setting, and
        # the screens already on the display keep the words they were built
        # with. Silently doing nothing visible is what makes a user press it
        # again and conclude it is broken.
        QMessageBox.information(
            parent, tr("menu.language"),
            tr("menu.language_changed", language=native))

    source.language_chosen.connect(chose)
    if on_subscribe is not None:
        source.subscribe_requested.connect(on_subscribe)
    source.settings_requested.connect(
        lambda: open_settings(parent, conn))


def onboarding_entitlement_panel():
    """The screen on which the user actually pays, chosen by build.

    THE STEP THAT DID NOT EXIST. Both panels were written, both were correct,
    and both lived only in Settings — which onboarding never opens and never
    mentions. So a new user on any platform finished setting up without ever
    being asked to subscribe, arrived at calibration, and was told there were
    "not enough live postings to calibrate against". That sentence is true and
    completely misleading: the feed had refused because nothing had been
    bought, and the message describes an empty market.

    Which panel is not a preference:

      mas     Apple forbids licence keys outright (guideline 3.1.1), so the
              Mac App Store build must sell through StoreKit and must offer
              Restore — somebody who paid on another Mac has already paid.
      store   Microsoft permits third-party commerce (10.8.1, 10.8.6) and the
      direct  the listing is free, so both Windows channels take the same
              Paddle key, or an override code redeemed for one.

    Returns None on a build with no usable variant flag rather than guessing:
    showing a Windows key box on a Mac build is the exact shape guideline
    3.1.1 forbids, and guessing wrong is worse than showing nothing.
    """
    from app.core.build_variant import variant

    build = variant()
    if build == "mas":
        from app.ui.settings import SubscribePanel
        return SubscribePanel()
    if build in ("store", "direct"):
        from app.ui.settings import LicencePanel
        return LicencePanel()
    return None


def send_if_configured(conn):
    """`build_send`, or None when no key has been entered yet.

    `api_key.require()` raises, and that is right everywhere the key IS the
    point — a run that cannot assess must say so rather than quietly do less.
    It is wrong for a side errand at the end of onboarding: seeding searches
    is a convenience, and a user who has not added a key yet must still be
    able to finish setting up.
    """
    try:
        return build_send(conn)
    except Exception:  # noqa: BLE001
        return None


def build_send(conn):
    """The assessment transport, on the USER's own Anthropic key.

    Dawnlist is bring-your-own-key, and that is a DATA decision before it is a
    pricing one. Job descriptions, the fit brief and the background factsheet
    are the user's career history. Routing them through Spencer's own
    infrastructure would make him a processor of every buyer's employment
    record — with the retention, breach-notification and international-transfer
    duties that follow — in exchange for saving them one setup step.

    So the call goes straight from the user's machine to Anthropic on the
    user's key. There is no managed route to fall back to, deliberately: a
    fallback is how data starts crossing infrastructure nobody decided it
    should cross.
    """
    import anthropic

    from app.core import api_key

    client = anthropic.Anthropic(api_key=api_key.require())

    def send(request: dict):
        response = client.messages.create(**request)
        return "".join(b.text for b in response.content if b.type == "text")

    return send



class AlertProvider:
    """A feed provider whose postings came from files the user dragged in.

    Wrapping them as a provider means alert digests go through the SAME path as
    a fetched sweep — dedup, the permanent-reject gate, the screen, assessment,
    persistence. A second ingestion path would be a second system that drifts
    from the first, which is the reason the headless run and the windowed run
    already share everything below the surface.
    """

    name = "alert"

    def __init__(self, jobs):
        self._jobs = list(jobs)

    def search(self, query):
        from app.feed.base import FetchResult
        return FetchResult(jobs=self._jobs, pages_fetched=1, exhausted=True,
                           credits_estimate=0)

    def credits_used(self) -> int:
        return 0            # files cost nothing; only the assessment is billed


def ingest_alerts(conn, paths, *, send=None, today: date | None = None):
    """Add postings from job-alert emails. Returns (outcome, problems).

    Deliberately does NOT require saved queries: these postings did not come
    from a query. It does require the brief, the calibration gate and an
    entitlement, exactly as a sweep does — the money is spent on assessment
    either way, and a gate the user can walk round by dragging a file is not a
    gate.
    """
    from app.feed.alert_email import parse_many
    from app.onboarding.calibration import is_calibrated

    brief = load_document(conn, "fit_brief")
    if not brief.strip():
        raise NotConfigured(
            "No fit brief yet. Run onboarding first — postings assessed "
            "against an empty brief produce a shortlist built on nothing.")
    if not is_calibrated(conn):
        raise NotConfigured(
            "Calibration has not been completed. Adding postings by hand does "
            "not skip the step that transfers your judgement into the brief.")

    from app.core.entitlement import require as require_entitlement
    require_entitlement(conn)

    jobs, problems = parse_many([Path(p) for p in paths])
    if not jobs:
        return None, problems or ["no postings were found in those files"]

    from app.ui.adapter import rejected_keys
    seen = {(r["provider"], r["provider_job_id"])
            for r in conn.execute(
                "SELECT provider, provider_job_id FROM seen_jobs")}

    outcome = run_morning(
        conn, AlertProvider(jobs),
        [SearchQuery(label="job alerts", titles=[])],
        load_rules(conn),
        fit_brief=brief, factsheet=load_document(conn, "factsheet"),
        send=send or build_send(conn),
        gates=[permanent_reject_gate(rejected_keys(conn)),
               posted_within_gate(DEFAULT_POSTED_WITHIN_DAYS, today=today)],
        already_seen=seen,
    )
    persist(conn, outcome)
    db.prune_seen(conn)
    return outcome, problems




ALERTS_DIRNAME = "alerts"


def alerts_dir() -> Path:
    """Where job-alert emails live for calibration and for `--add-alerts`.

    Beside the database, never a synced folder. Mirrors `voice_dir()`: the app
    reads files a person put there and never touches a mailbox.
    """
    return db.default_db_path().parent / ALERTS_DIRNAME


def alert_postings(conn, folder: Path | None = None):
    """Postings parsed from job-alert emails on disk, minus anything already
    seen or permanently rejected.

    Used by `calibration_sample` when there is no feed credential, so onboarding
    can be completed without one. Returns [] rather than raising when the folder
    is absent — an empty fallback is a short sample, which the gate already
    reports as a setup problem naming the cause.
    """
    from app.feed.alert_email import parse_many
    from app.ui.adapter import rejected_keys

    folder = folder or alerts_dir()
    if not folder.is_dir():
        return []
    paths = sorted(p for p in folder.iterdir()
                   if p.suffix.lower() in (".eml", ".mbox"))
    if not paths:
        return []

    jobs, _problems = parse_many(paths)
    seen = {(r["provider"], r["provider_job_id"]) for r in
            conn.execute("SELECT provider, provider_job_id FROM seen_jobs")}
    blocked = seen | rejected_keys(conn)
    return [j for j in jobs if (j.provider, j.provider_job_id) not in blocked]


CALIBRATION_SAMPLE = 10

#: Postings fetched to fill that sample, spread across the searches. A few
#: over ten, because the scope gates can remove some. This used to ask the
#: first search alone for 500 (clamped to a 100-row page) to show ten, and
#: every one of those rows was paid for, by every new user.
CALIBRATION_FETCH = 15


def calibration_sample(conn, *, provider=None, send=None):
    """Live postings for the calibration gate, screened and assessed.

    Real postings, or none. The gate is where the user's judgement is
    transferred into the brief, so practising on invented ones would calibrate
    them against fiction — and the app would then run against a brief corrected
    for postings that never existed.

    Returns fewer than `CALIBRATION_SAMPLE` when the feed is short or
    unreachable, and the gate reports that as a setup failure rather than
    asking the user for decisions they cannot make.
    """
    from app.core.screen import screen_all
    from app.intelligence.assess import assess
    from app.onboarding.calibration import CalibrationItem

    jobs = []
    queries = load_queries(conn)
    if queries:
        try:
            feed = provider or build_provider(conn)
        except NotConfigured:
            feed = None
        if feed is not None:
            gates = scope_gates(enabled_scopes(conn))
            per_search = max(3, -(-CALIBRATION_FETCH // len(queries)))
            for query in queries:
                result = feed.search(replace(query, max_results=per_search))
                if result.ok:
                    jobs.extend(j for j in result.jobs
                                if all(g.predicate(j) for g in gates))
                if len(jobs) >= CALIBRATION_SAMPLE:
                    break

    if len(jobs) < CALIBRATION_SAMPLE:
        # Fall back to job-alert emails the user has supplied. These are real
        # postings — the rule is "real postings, or none", not "fetched
        # postings, or none" — and they need no feed credential.
        #
        # Without this, a user who has no feed credential cannot finish
        # onboarding AT ALL: calibration needs ten live postings, the gate
        # needs eight decisions, and there is no third way to obtain them. That
        # blocked every beta tester, and it blocked them on the last screen of
        # setup rather than the first.
        jobs.extend(j for j in alert_postings(conn)
                    if j.provider_job_id not in
                    {x.provider_job_id for x in jobs})

    jobs = jobs[:CALIBRATION_SAMPLE]
    if not jobs:
        return []

    brief = load_document(conn, "fit_brief")
    factsheet = load_document(conn, "factsheet")
    report = screen_all(jobs, load_rules(conn))

    # Assess the survivors so the user is correcting a real verdict. The
    # screened-out ones still appear, marked as such: an over-reaching rule is
    # exactly the thing calibration should catch, and hiding those rows would
    # hide the failure the gate exists to surface.
    verdicts = {}
    likely = [r.job for r in report.likely]
    if likely:
        judged = assess(likely, brief, factsheet, send=send or build_send(conn))
        verdicts = {v.job.provider_job_id: v for v in judged.verdicts}

    items = []
    for result in report.results:
        job = result.job
        verdict = verdicts.get(job.provider_job_id)
        items.append(CalibrationItem(
            job_key=f"{job.provider}:{job.provider_job_id}",
            title=job.title,
            company=job.company,
            description=job.description_text,
            app_verdict=verdict.bucket if verdict else "rejected",
            app_reason=(verdict.reason if verdict
                        else result.reason or "screened out before reading")))
    return items


def morning_run(conn, *, provider=None, send=None, today: date | None = None):
    """One morning run, with the gates the stored state implies."""
    from app.ui.adapter import rejected_keys

    from app.onboarding.calibration import is_calibrated

    brief = load_document(conn, "fit_brief")
    factsheet = load_document(conn, "factsheet")
    if not brief.strip():
        raise NotConfigured(
            "No fit brief yet. Run onboarding first — a run against an empty "
            "brief produces a plausible shortlist built on nothing.")
    if not is_calibrated(conn):
        # The gate is checked HERE, at the only door into a run, rather than in
        # the UI. A gate enforced in a screen is a gate the scheduled run walks
        # straight past.
        raise NotConfigured(
            "Calibration has not been completed. The app must show you ~10 "
            "live postings and have you correct its verdicts before it runs "
            "daily — that is the step that transfers your judgement into the "
            "brief, and without it the shortlist is a guess that looks like an "
            "answer.")

    # The entitlement gate sits HERE, next to the calibration gate, for the
    # same reason: a gate enforced in a screen is one the scheduled run walks
    # straight past. Only the run is gated — onboarding, the board and every
    # screen stay open whether or not anyone has paid.
    from app.core.entitlement import require as require_entitlement
    require_entitlement(conn)

    unscoped = unscoped_enabled_labels(conn)
    if unscoped:
        # Refused by name rather than skipped, because a skipped search is a
        # quiet morning nobody can explain. One entry in Settings fixes every
        # search at once.
        raise NotConfigured(
            "These searches are switched on but say nowhere to look, so they "
            "would search the whole world: " + ", ".join(unscoped) + ". Open "
            "Settings and set where you want to work; it applies to every "
            "search at once.")

    queries = load_queries(conn)
    if not queries:
        raise NotConfigured("No saved queries. Add at least one before running.")

    seen = {(r["provider"], r["provider_job_id"])
            for r in conn.execute("SELECT provider, provider_job_id FROM seen_jobs")}

    gates: list[Gate] = [
        permanent_reject_gate(rejected_keys(conn)),
        posted_within_gate(DEFAULT_POSTED_WITHIN_DAYS, today=today),
        *scope_gates(enabled_scopes(conn)),
    ]

    from app.core import api_key
    from app.core.pipeline import judged_refs, unassessed_likely
    from app.intelligence.assess import anthropic_transport

    # `run_morning` stores what it fetched and screened before any model call,
    # stores verdicts batch by batch, and advances each search's mark once its
    # postings are stored — so an interrupted run has lost nothing it paid
    # for. Postings already judged are skipped, rather than paying the model
    # to read them twice, and postings an earlier run screened in but never
    # judged are queued again before anything new is fetched.
    #
    # The transport is the one that reports WHY a reply ended. `build_send`
    # returns bare text, so a reply cut off at max_tokens or refused reached
    # the assessment looking like a malformed payload.
    outcome = run_morning(
        conn, provider or build_provider(conn), queries, load_rules(conn),
        fit_brief=brief, factsheet=factsheet,
        send=send or anthropic_transport(api_key.require()), gates=gates,
        already_seen=seen,
        already_judged=judged_refs(conn),
        requeued=unassessed_likely(conn),
    )

    # `seen_jobs` is a rolling window, not a permanent record — rejections live
    # in `decisions`, which never expires. Nothing pruned it, so the table grew
    # for the life of the install and every run rebuilt a larger and larger
    # already-seen set to compare against.
    db.prune_seen(conn)
    # Every swept posting's full text was likewise kept for ever, although
    # only one the user decides on is read again. The rows stay; the text of
    # old undecided ones goes.
    db.prune_descriptions(conn)
    return outcome


def print_funnel(outcome) -> None:
    """The whole funnel, never one number without what it excludes."""
    counts = outcome.funnel()
    width = max(len(k) for k in counts)
    for key, value in counts.items():
        print(f"  {key.replace('_', ' '):<{width}}  {value}")
    if outcome.fetch_errors:
        print("\n  FETCH PROBLEMS — this run is incomplete:")
        for err in outcome.fetch_errors:
            print(f"    - {err}")
    if outcome.unscored_queries:
        print(f"\n  no yield rate computed for: "
              f"{', '.join(outcome.unscored_queries)} (fetch failed)")
    loose = outcome.loose_queries()
    if loose:
        print(f"\n  below 10% yield, rewrite rather than widen: {', '.join(loose)}")

    # spec 5.4: watch this between runs. On the command line there is no
    # bar to carry the warning chip, so the standing figure is printed
    # every run - a number seen each morning is one whose movement gets
    # noticed. Below the sample floor it is not a measurement, and is left
    # off rather than printed with a caveat nobody reads.
    from app.core.screen import DRIFT_MIN_SAMPLE
    if outcome.screen and len(outcome.screen.results) >= DRIFT_MIN_SAMPLE:
        print(f"\n  screening rules removed "
              f"{round(outcome.screen.unlikely_share * 100)}% of what was swept")



def drafts_dir() -> Path:
    """Where `.eml` files land. Beside the database, never a synced folder —
    a draft is unsent correspondence and has no business in OneDrive."""
    return db.default_db_path().parent / "drafts"


def outreach_run(conn, *, send=None, today: date | None = None,
                 folder: Path | None = None):
    """Draft everything due. Writes files; sends nothing, ever.

    The board decides what is due and the cadence decides when — this only
    turns that list into files the user opens and sends themselves. There is
    deliberately no transport here and no credential that could grow one.
    """
    from app.outreach.run import due_today, prepare_drafts
    from app.outreach.voice import build_profile

    factsheet = load_document(conn, "factsheet")
    if not factsheet.strip():
        raise NotConfigured(
            "No background factsheet yet. Run onboarding first — every factual "
            "claim in a draft has to come from it, so without one a draft is "
            "either empty or invented.")

    from app.core.entitlement import require as require_entitlement
    require_entitlement(conn)

    folder = folder or drafts_dir()
    folder.mkdir(parents=True, exist_ok=True)

    items = due_today(conn, today=today)
    settings = load_settings(conn)
    return prepare_drafts(
        conn, items, folder=folder, factsheet=factsheet,
        voice=load_voice(conn), send=send or build_send(conn),
        locale=settings.get("locale", "en"), today=today)


#: The user's own CV files, as FILES rather than a table.
#:
#: `documents` is constrained by CHECK to the fit brief and the factsheet, and
#: widening that constraint means rebuilding the table — SQLite cannot alter a
#: CHECK in place. More to the point, the reasoning that put sent mail in a
#: folder applies here too: these are documents a person put somewhere, in
#: their own formats, that Dawnlist reads and never rewrites.
CV_DIRNAME = "cv"


def cv_dir() -> Path:
    return db.default_db_path().parent / CV_DIRNAME


def load_cv_text(folder: Path | None = None) -> str:
    """Every readable CV in the folder, joined.

    Several versions rather than one on purpose: different versions describe
    the same role in different words, and that spread is what a tailored CV
    draws on. Unreadable files are skipped rather than fatal — one scanned PDF
    must not stop the others, which is the same rule onboarding applies.
    """
    from app.onboarding.extract import extract_corpus, gather

    folder = folder or cv_dir()
    if not folder.exists():
        return ""
    result = extract_corpus(gather(folder))
    return "\n\n".join(f"# {d.name}\n\n{d.text}" for d in result.corpus.usable)


def applications_dir() -> Path:
    """Where tailored CVs, letters and interview briefs land.

    Beside the database and never a synced folder, for the same reason as
    `drafts_dir`: a CV carrying an unresolved placeholder has no business in
    OneDrive, where a blocked document and a finished one look identical in a
    file listing on somebody's phone.
    """
    return db.default_db_path().parent / "applications"


def apply_run(conn, job_id: str, *, send=None, folder: Path | None = None,
              want_brief: bool = False):
    """Produce the application pack for one posting already in the tracker.

    `job_id` is `provider:provider_job_id` — the form the board and the review
    window already use, so a posting can be named from what is on screen
    rather than from a row number nobody sees.
    """
    import json

    from app.apply.run import prepare_application
    from app.feed.models import Job
    from app.ui.adapter import split_job_id

    factsheet = load_document(conn, "factsheet")
    if not factsheet.strip():
        raise NotConfigured(
            "No background factsheet yet. Run onboarding first — every claim "
            "in a tailored CV has to come from it, so without one the document "
            "is either empty or invented.")

    # The CV is the SOURCE document, not a generated one. A curriculum vitae
    # written from a factsheet alone is fluent and describes a career nobody
    # had; without the real one there is nothing honest to reorder.
    cv_text = load_cv_text()
    if not cv_text.strip():
        raise NotConfigured(
            f"No curriculum vitae found in {cv_dir()}. Put your CV there — a "
            f"tailored CV reorders your own document, it does not write a new "
            f"one, so without it there is nothing honest to work from.")

    from app.core.entitlement import require as require_entitlement
    require_entitlement(conn)

    provider, provider_job_id = split_job_id(job_id)
    row = conn.execute(
        "SELECT * FROM jobs WHERE provider=? AND provider_job_id=?",
        (provider, provider_job_id)).fetchone()
    if row is None:
        raise NotConfigured(f"No posting {job_id!r} in the tracker.")

    job = Job(
        provider=row["provider"], provider_job_id=row["provider_job_id"],
        title=row["title"], company=row["company"],
        locations=tuple(json.loads(row["locations_json"])),
        description_text=row["description_text"],
        salary=row["salary"], url=row["url"],
        raw_criteria=json.loads(row["raw_criteria_json"]),
    )
    return prepare_application(
        job, factsheet=factsheet, cv_text=cv_text,
        send=send or build_send(conn),
        folder=folder or applications_dir(), want_brief=want_brief)


def apply_for_opportunity(conn, opportunity_id: str, *, want_brief: bool = False,
                          send=None, folder: Path | None = None):
    """Write the application for a board row.

    The board knows an opportunity; `apply_run` knows a posting. This is the
    join, and it is here rather than in the widget because the widget must not
    reach the database — and because this is the one board action that spends
    the user's own tokens.
    """
    row = conn.execute(
        """SELECT j.provider, j.provider_job_id
             FROM opportunities o JOIN jobs j ON j.id = o.job_id
            WHERE o.id = ?""", (opportunity_id,)).fetchone()
    if row is None:
        raise NotConfigured(
            "That opportunity is not linked to a posting, so there is no "
            "advert to write against. Applications are written from the "
            "posting, never from the company name alone.")
    return apply_run(conn, f"{row['provider']}:{row['provider_job_id']}",
                     send=send, folder=folder, want_brief=want_brief)


def add_posting(conn, path: Path, url: str = ""):
    """Bring in a posting found somewhere the feed does not reach.

    Reads a file the user saved by copying a job advert — a page, an email,
    the text of a PDF. Nothing is fetched: `app/feed/ingest.py` explains why
    that is a refusal rather than an omission.
    """
    import json

    from app.feed.ingest import parse_pasted
    from app.feed.models import name_key

    parsed = parse_pasted(path.read_text(encoding="utf-8", errors="replace"),
                          url=url)
    if not parsed.is_usable:
        raise NotConfigured(
            "Could not read a posting from that file — missing "
            + ", ".join(parsed.unresolved)
            + ". Copy the whole advert, including the heading.")

    # Written directly rather than through `pipeline.persist`, which takes a
    # RunOutcome and records screen verdicts. A hand-entered posting has not
    # been screened and must not arrive carrying a verdict it never received:
    # the person chose it deliberately, and the screen exists to thin a sweep
    # nobody asked for.
    job = parsed.to_job()
    conn.execute(
        """INSERT INTO jobs(provider, provider_job_id, title, company,
               locations_json, description_text, salary, url,
               raw_criteria_json, name_key, funnel_status)
           VALUES (?,?,?,?,?,?,?,?,?,?, 'likely')
           ON CONFLICT(provider, provider_job_id) DO UPDATE SET
               title=excluded.title, company=excluded.company,
               description_text=excluded.description_text,
               salary=excluded.salary, url=excluded.url""",
        (job.provider, job.provider_job_id, job.title, job.company,
         json.dumps(list(job.locations)), job.description_text, job.salary,
         job.url, json.dumps(job.raw_criteria),
         name_key(job.company, job.title)))
    conn.commit()
    return parsed


#: Sent mail the user has supplied, for measuring how they write. Files rather
#: than a table, and deliberately: `documents` is constrained to the fit brief
#: and the factsheet, and widening that CHECK to admit a mail archive would put
#: correspondence in the same store as the two documents that govern truth.
#: Dawnlist never reads a mailbox — these are files a person put here.
VOICE_DIRNAME = "voice"


def voice_dir() -> Path:
    return db.default_db_path().parent / VOICE_DIRNAME


def load_voice(conn, folder: Path | None = None):
    """The user's own tone of voice, measured from mail they supplied.

    Falls back to a neutral profile rather than failing: someone who has added
    no sent mail should still get drafts, in nobody's particular voice, rather
    than no drafts at all. Style only — never content. What a past message
    SAID is not evidence for anything a new one may claim; only the factsheet
    is.
    """
    from app.outreach.voice import build_profile, profile_from_files

    settings = load_settings(conn)
    folder = folder or Path(settings.get("voice_dir") or voice_dir())
    if not folder.is_dir():
        return build_profile([])

    paths = sorted(p for p in folder.iterdir()
                   if p.suffix.lower() in {".eml", ".txt", ".md"})
    if not paths:
        return build_profile([])
    return profile_from_files(paths, locale=settings.get("locale", "en"))


def print_outreach(report) -> None:
    counts = report.counts
    width = max(len(k) for k in counts)
    for key, value in counts.items():
        print(f"  {key.replace('_', ' '):<{width}}  {value}")
    for item in report.blocked:
        print(f"    blocked: {item.opportunity.company} — {item.blocked}")
    for draft in report.drafts.blocked:
        print(f"    needs evidence: {draft.to_name} — "
              f"{', '.join(draft.placeholders)}")
    if report.drafts.drafts:
        print(f"\n  written to {drafts_dir()}")
    print("\n  Nothing was sent. Open each file and send it yourself.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dawnlist")
    parser.add_argument("--run-once", action="store_true",
                        help="run the morning sweep headlessly and exit")
    parser.add_argument("--board", action="store_true",
                        help="open on the board rather than the shortlist")
    parser.add_argument("--audit", action="store_true",
                        help="print the board audit and exit")
    parser.add_argument("--doctor", action="store_true",
                        help="print environment diagnostics and exit")
    parser.add_argument("--draft", action="store_true",
                        help="draft every outreach that is due; sends nothing")
    parser.add_argument("--add-alerts", nargs="+", metavar="FILE",
                        help="add postings from saved job-alert emails "
                             "(.eml or .mbox)")
    parser.add_argument("--add-posting", metavar="FILE",
                        help="add one posting from a copied job advert saved "
                             "as a text file; nothing is fetched")
    parser.add_argument("--posting-url", default="",
                        help="the advert's URL, kept with --add-posting for "
                             "provenance")
    parser.add_argument("--apply", metavar="JOB_ID",
                        help="write a tailored CV and covering letter for one "
                             "posting, as provider:id")
    parser.add_argument("--brief", action="store_true",
                        help="with --apply, also write an interview brief")
    parser.add_argument("--settings", action="store_true",
                        help="open settings alone, without the rest of the app")
    parser.add_argument("--onboard", action="store_true",
                        help="open the setup flow, even if already calibrated")
    parser.add_argument("--db", type=Path, default=None,
                        help="database path (defaults to the app data dir)")
    parser.add_argument("--locale", default=None, help="UI locale, e.g. fr")
    args = parser.parse_args(argv)

    conn = db.connect(args.db)
    db.migrate(conn)

    settings = load_settings(conn)
    # A CHOICE, THEN THE MACHINE, THEN ENGLISH — in that order.
    #
    # This read `settings.get("locale", "en")` and nothing ever wrote that
    # setting, so every install of a fifty-language app opened in English
    # whatever the machine was set to. The stored value still wins where the
    # user has picked one, because a person who chose English on a French Mac
    # meant it.
    from app.i18n import system_locale
    set_locale(args.locale or settings.get("locale") or system_locale())

    if args.doctor:
        return _doctor(conn)

    if args.audit:
        from app.core.board_repo import audit_board
        findings = audit_board(conn)
        print(f"{len(findings['scanned'])} opportunities checked")
        for key in ("bounce_corrections", "parity_defects",
                    "duplicate_open_children"):
            for item in findings[key]:
                print(f"  {key}: {item}")
        return 0

    if args.add_posting:
        try:
            parsed = add_posting(conn, Path(args.add_posting),
                                 url=args.posting_url)
        except NotConfigured as exc:
            print(f"not configured: {exc}", file=sys.stderr)
            return 2
        print(f"added: {parsed.title} — {parsed.company or 'company unknown'}")
        if parsed.unresolved:
            # Reported rather than filled in. A silently wrong company name is
            # worse than an empty one: the empty one gets corrected.
            print("could not read: " + ", ".join(parsed.unresolved))
        return 0

    if args.apply:
        from app.apply.run import summarise
        from app.core.api_key import KeyProblem
        from app.core.entitlement import NotEntitled

        try:
            pack = apply_run(conn, args.apply, want_brief=args.brief)
        except KeyProblem as exc:
            print(f"no api key: {exc}", file=sys.stderr)
            return 4
        except NotEntitled as exc:
            print(f"not entitled: {exc}", file=sys.stderr)
            return 3
        except NotConfigured as exc:
            print(f"not configured: {exc}", file=sys.stderr)
            return 2
        print(summarise(pack))
        print(f"\nwritten to {applications_dir()}")
        # Non-zero when a document carries an unsupported claim, so a script
        # cannot treat a blocked CV as a finished one.
        return 0 if pack.complete else 1

    if args.run_once:
        from app.core.api_key import KeyProblem
        from app.core.entitlement import NotEntitled

        try:
            outcome = morning_run(conn)
        except KeyProblem as exc:
            # Exit 4: distinct from "not set up" (2) and "not paid" (3),
            # because the fix is different again.
            print(f"no api key: {exc}", file=sys.stderr)
            return 4
        except NotEntitled as exc:
            # Exit 3, distinct from 2: "not paid" and "not set up" want
            # different responses from whatever is running this.
            print(f"not entitled: {exc}", file=sys.stderr)
            return 3
        except NotConfigured as exc:
            print(f"not configured: {exc}", file=sys.stderr)
            return 2
        print_funnel(outcome)
        # An incomplete run exits non-zero so a scheduler notices. A run that
        # fetched nothing because the endpoint failed must never look like a
        # quiet morning.
        return 0 if outcome.complete else 1

    if args.add_alerts:
        from app.core.api_key import KeyProblem
        from app.core.entitlement import NotEntitled

        try:
            outcome, problems = ingest_alerts(conn, args.add_alerts)
        except KeyProblem as exc:
            print(f"no api key: {exc}", file=sys.stderr)
            return 4
        except NotEntitled as exc:
            print(f"not entitled: {exc}", file=sys.stderr)
            return 3
        except NotConfigured as exc:
            print(f"not configured: {exc}", file=sys.stderr)
            return 2
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        if outcome is None:
            return 1
        print_funnel(outcome)
        return 0 if outcome.complete else 1

    if args.draft:
        from app.core.api_key import KeyProblem
        from app.core.entitlement import NotEntitled

        try:
            report = outreach_run(conn)
        except KeyProblem as exc:
            print(f"no api key: {exc}", file=sys.stderr)
            return 4
        except NotEntitled as exc:
            print(f"not entitled: {exc}", file=sys.stderr)
            return 3
        except NotConfigured as exc:
            print(f"not configured: {exc}", file=sys.stderr)
            return 2
        print_outreach(report)
        return 0

    if args.settings:
        # Reachable without a working key or a passed gate on purpose: the
        # state this is most needed in is the one where nothing else starts.
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)
        from app.ui.branding import apply_icon
        apply_icon(app)
        # Bound, and it looks unused: this local is the ONLY reference to the
        # window, and letting it go collects the widget before exec() runs.
        window = open_settings(conn=conn)  # noqa: F841
        assert window is not None
        return app.exec()

    if args.onboard:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)
        from app.ui.branding import apply_icon
        apply_icon(app)
        return _launch_onboarding(app, conn)

    return _launch_ui(conn, open_board=args.board)


def _doctor(conn) -> int:
    """Environment diagnostics.

    Exists because a frozen build fails differently from a source run: a
    resource read by path at runtime can simply be absent from the bundle, and
    the app degrades quietly rather than crashing. This prints what actually
    resolved, so "it is not translating" is one command to diagnose rather than
    a support thread.
    """
    from app.i18n import LOCALE_CODES, LOCALES_DIR, coverage
    from app.onboarding.calibration import is_calibrated

    from app.core.build_variant import variant

    frozen = getattr(sys, "frozen", False)
    print(f"dawnlist      : {'frozen' if frozen else 'source'}")
    # A Store package that is silently the direct-download build is exactly
    # what the flags exist to prevent, so the variant is reported.
    v = variant()
    print(f"build variant : {v}")
    if v in ("none", "ambiguous"):
        print(f"  WARNING: variant is {v} — this build carries no usable "
              f"variant flag.", file=sys.stderr)
    print(f"database      : {conn.execute('PRAGMA database_list').fetchone()[2]}")
    print(f"locales dir   : {LOCALES_DIR}")
    print(f"locales exist : {LOCALES_DIR.exists()}")

    cov = coverage()
    shipped = sorted(c for c in LOCALE_CODES if cov[c] > 0)
    print(f"catalogues    : {len(shipped)} of {len(LOCALE_CODES)} "
          f"({', '.join(shipped) if shipped else 'none'})")
    if not shipped:
        # The exact failure the spec's `datas` entry exists to prevent.
        print("  WARNING: no catalogues resolved. Every locale will fall back "
              "to English silently. Check the spec collects "
              "app/resources/locales.", file=sys.stderr)

    # Reported because a missing extractor in a frozen build shows up to the
    # user as "no text could be read", which points at their CV rather than at
    # the bundle.
    for module, what in (("pypdf", "PDF"), ("docx", "Word")):
        try:
            __import__(module)
            print(f"{what + ' reading':<14}: available")
        except ImportError:
            print(f"{what + ' reading':<14}: MISSING ({module} not bundled)",
                  file=sys.stderr)

    from app.core.entitlement import check as check_entitlement

    from app.core import api_key as user_key

    from app.core import api_key as user_key

    stored = user_key.get()
    print(f"anthropic key : {'present' if stored else 'MISSING'}")
    if not stored:
        print("  Dawnlist uses your own Anthropic key. Add one in Settings.",
              file=sys.stderr)

    ent = check_entitlement(conn)
    state = ent.source if ent.entitled else "NOT ENTITLED"
    print(f"entitlement   : {state}")
    if not ent.entitled:
        print(f"  {ent.reason}", file=sys.stderr)
    elif ent.source in ("trial", "grace"):
        print(f"  {ent.reason}")

    print(f"calibrated    : {is_calibrated(conn)}")
    print(f"queries       : {len(load_queries(conn))}")
    print(f"fit brief     : {'yes' if load_document(conn, 'fit_brief') else 'no'}")

    # Invariant 13 from the other side: a file on disk that no run produced.
    # Reported rather than deleted — an unregistered draft may be the only copy
    # of something the user wrote, and this is a diagnostic, not a tidy-up.
    # Printed because it is otherwise undiscoverable: the listing says drafts
    # are written in the user's own register, learned from sent mail they add
    # themselves, and this folder is the only way to add it.
    print(f"drafts folder : {drafts_dir()}")
    print(f"voice folder  : {voice_dir()}")

    orphans = db.orphan_outputs(conn, drafts_dir())
    print(f"orphan drafts : {len(orphans)}")
    for path in orphans[:5]:
        print(f"  unregistered: {path.name}")
    if len(orphans) > 5:
        print(f"  ... and {len(orphans) - 5} more")
    return 0 if shipped else 1


def _launch_onboarding(app, conn) -> int:
    """The setup flow. Opened automatically when the gate has not been passed.

    Routing here rather than to an empty shortlist is the honest thing to show
    a new user: the shortlist would be empty anyway, and an empty screen with
    no explanation reads as a broken app rather than an unfinished setup.
    """
    from app.onboarding.calibration import complete_calibration
    from app.onboarding.extract import extract_corpus
    from app.ui.onboarding import OnboardingWizard

    def extract(paths):
        result = extract_corpus(list(paths))
        return [d.name for d in result.corpus.documents], result.warnings

    def sample():
        """The postings to calibrate against — a real pull, or nothing.

        This was a hard-coded single placeholder, and the gate needs eight
        decisions, so the Finish button could never enable: onboarding was
        impossible to complete. Returning a short list is now handled by the
        gate, which names it as a setup failure instead of asking for eight
        decisions out of one.
        """
        return calibration_sample(conn)

    def drafter(paths, aim=""):
        """CVs in, factsheet and brief out, on the user's own key.

        `aim` is what the user says they are looking for. Without it the brief
        is inferred from CVs alone — and a CV records what someone has done,
        never what they want next.
        """
        import json

        from app.core import api_key
        from app.onboarding.extract import extract_corpus
        from app.onboarding.interview import (build_brief_request,
                                              build_factsheet_request,
                                              open_questions, render_factsheet,
                                              text_of)

        import anthropic
        client = anthropic.Anthropic(api_key=api_key.require())

        def call(request):
            # Streamed, not `create`. The SDK REFUSES a non-streaming request
            # whose max_tokens could exceed a ten-minute response — it raises
            # ValueError before anything is sent, so the app would fail on the
            # first real corpus with a message about streaming rather than
            # about CVs. The factsheet needs that budget (see
            # FACTSHEET_MAX_TOKENS), so the transport has to stream.
            with client.messages.stream(**request) as stream:
                return text_of(stream.get_final_message())

        corpus = extract_corpus(list(paths)).corpus
        if not corpus.usable:
            raise RuntimeError("no readable CV text was found")

        raw = call(build_factsheet_request(corpus))
        data = json.loads(raw)
        factsheet = render_factsheet(data)
        brief = call(build_brief_request(corpus, aim))
        return factsheet, brief, open_questions(data)

    wizard = OnboardingWizard(
        extract=extract, sample=sample, drafter=drafter,
        titler=lambda text, brief="": search_plan(
            text, brief=brief, send=send_if_configured(conn)),
        entitlement_panel=onboarding_entitlement_panel(),
        searches=lambda: all_queries(conn),
        where=lambda: load_scope(conn).describe(),
        set_search=lambda label, enabled: enable_query(
            conn, label, enabled=enabled))

    # The ⋯ menu: language, subscription, restore. On the wizard as well as
    # the main window, because the person who needs all three most is the one
    # who has not finished setting up.
    wire_quick_menu(wizard.menu, conn, parent=wizard)

    def save_documents(factsheet, brief):
        # Saved when the user leaves the interview, not when the model returns.
        # What is stored is what they CORRECTED, which is the whole point of
        # showing them a draft rather than a result.
        from app.onboarding.interview import save_document
        if factsheet.strip():
            save_document(conn, "factsheet", factsheet)
        if brief.strip():
            save_document(conn, "fit_brief", brief)

        # Seed searches from what the user said they were looking for, so a
        # new install has something to sweep rather than a run that refuses for
        # want of a query. They arrive switched OFF — see
        # `seed_queries_from_aim` for why that matters to the bill.
        plan = wizard.interview.suggested_plan
        if plan is not None:
            # Already worked out off the UI thread while the draft was read.
            seed_queries_from_aim(conn, wizard.interview.aim.toPlainText(),
                                  titles=plan.titles, scope=plan.scope)
        else:
            # Next was pressed before that finished. Asking now makes this
            # click wait for a round trip. The alternative was the rule-based
            # split, which is what offered sentence fragments as searches, and
            # a short wait is the lesser cost.
            seed_queries_from_aim(conn, wizard.interview.aim.toPlainText(),
                                  brief=brief, send=send_if_configured(conn))

    def finished(result):
        brief = load_document(conn, "fit_brief") or "# Fit brief"
        if result.sample_unavailable:
            # Nothing to record. `complete_calibration` would raise, which is
            # correct — a calibration against no postings is not one — but the
            # user must still be able to finish and start the app. It runs on a
            # later morning, once a search is switched on.
            wizard.close()
            return
        complete_calibration(conn, brief, result)
        wizard.close()

    wizard.documents_ready.connect(save_documents)
    wizard.completed.connect(finished)
    wizard.resize(900, 780)
    wizard.show()
    return app.exec()



def _stored_funnel(conn, run_id) -> dict[str, int]:
    """The funnel bar for a run that is no longer in memory.

    Returned empty when there is no run: the bar states what a number excludes,
    and inventing zeroes would say a run happened and found nothing.
    """
    if run_id is None:
        return {}
    row = conn.execute(
        "SELECT swept, deduped, gated, screened_likely, screened_out, "
        "assessed, left_unread FROM runs WHERE id=?", (run_id,)).fetchone()
    if row is None:
        return {}
    # `gated` on disk, `gated_out` in memory. The funnel bar reads the
    # in-memory names, so the rename happens here rather than in the window —
    # a bar that silently omits a stage looks like a run that skipped it.
    counts = {k: row[k] or 0 for k in row.keys()}
    counts["gated_out"] = counts.pop("gated")
    return counts


def _screen_drift_notes(conn, run_id) -> list[str]:
    """spec 5.4 — say so when the screen's reach moves sharply between runs.

    `unlikely_share` was computed on every report and read by nothing, so a
    rule edit that started removing half the feed looked exactly like a quiet
    week. This is the "say so".

    Compared against the previous run THAT SCREENED SOMETHING, not simply the
    previous row: an outreach run screens nothing and would otherwise read as
    a collapse to zero.
    """
    from app.core.screen import (DRIFT_MIN_SAMPLE, SHARE_DRIFT, share_drift,
                                 unlikely_share_of)
    from app.i18n import tr

    if run_id is None:
        return []
    rows = conn.execute(
        "SELECT screened_likely, screened_out FROM runs "
        " WHERE id <= ? AND (screened_likely + screened_out) >= ? "
        " ORDER BY id DESC LIMIT 2", (run_id, DRIFT_MIN_SAMPLE)).fetchall()
    if len(rows) < 2:
        return []
    now = unlikely_share_of(rows[0]["screened_likely"], rows[0]["screened_out"])
    before = unlikely_share_of(rows[1]["screened_likely"], rows[1]["screened_out"])
    delta = share_drift(now, before)
    if delta is None or abs(delta) < SHARE_DRIFT:
        return []
    return [tr("funnel.screen_drift", now=round(now * 100),
               before=round(before * 100))]


def open_settings(parent=None, conn=None):
    """The settings window, wired to the real keyring.

    Held on the parent rather than returned into a local: a QWidget with no
    reference is garbage-collected the moment the function returns, and the
    window vanishes as fast as it appeared.
    """
    from app.ui.settings import (FamiliesPanel, RulesPanel, SearchesPanel,
                                 SettingsWindow)

    # One Settings window per opener. Every press used to build another, and
    # they stacked exactly over the window beneath, so closing one revealed
    # the next and the way back looked as if it did not exist.
    existing = getattr(parent, "_settings_window", None)
    if existing is not None and existing.isVisible():
        existing.raise_()
        existing.activateWindow()
        return existing

    # The key and licence panels already default to the real keyring and the
    # real redeemer; the injection points exist so the tests can spend nothing.
    # The rules panel cannot default, because the rules are per-user and live
    # in the database — so it is offered only when there is one.
    rules = families = searches = None
    if conn is not None:
        searches = SearchesPanel(
            loader=lambda: all_queries(conn),
            saver=lambda label, titles: save_new_search(conn, label, titles),
            forgetter=lambda label: forget_query(conn, label),
            enabler=lambda label, on: enable_query(conn, label, enabled=on),
            where_loader=lambda: load_scope(conn).describe(),
            where_saver=lambda text: apply_scope(
                conn, parse_where(text, keep=load_scope(conn))))
        families = FamiliesPanel(
            loader=lambda: load_rules(conn).kill_families,
            adopter=lambda name, on: adopt_kill_family(conn, name, adopted=on),
            refresher=lambda: refresh_kill_family_proposals(conn))
        rules = RulesPanel(
            loader=lambda: load_rules(conn),
            saver=lambda field, term: save_rule_term(conn, field, term),
            forgetter=lambda field, term: forget_rule_term(conn, field, term))

    window = SettingsWindow(rules=rules, families=families,
                            searches=searches, home=parent)
    if parent is not None:
        parent._settings_window = window
    window.show()
    return window


def _write_application(window, conn, opportunity_id: str,
                       want_brief: bool) -> None:
    """Run the pack from the board, and say what happened in plain words.

    Failures are SHOWN, never swallowed into a disabled button. This action
    spends the user's own tokens, so silence after pressing it is the one
    outcome that is unacceptable: they would press it again.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox

    from app.apply.run import summarise
    from app.core.api_key import KeyProblem
    from app.core.entitlement import NotEntitled
    from app.i18n import tr

    QApplication.setOverrideCursor(Qt.WaitCursor)
    try:
        pack = apply_for_opportunity(conn, opportunity_id,
                                     want_brief=want_brief)
    except (NotConfigured, KeyProblem, NotEntitled) as exc:
        QApplication.restoreOverrideCursor()
        QMessageBox.warning(window, tr("board.write_application"), str(exc))
        return
    except Exception as exc:  # noqa: BLE001
        QApplication.restoreOverrideCursor()
        QMessageBox.warning(window, tr("board.write_application"),
                            f"{type(exc).__name__}: {exc}")
        return
    QApplication.restoreOverrideCursor()

    # Leads with what is missing rather than with what was produced, and names
    # the folder, because a document written somewhere the user cannot find is
    # the same as one that was not written.
    QMessageBox.information(
        window, tr("board.write_application"),
        summarise(pack) + "\n\n" + str(applications_dir()))


def _added_alerts(window, conn, paths) -> None:
    """Ingest dropped digests, then put the result on screen.

    Reloading is the point: a drop that silently changed the database and left
    the window showing the previous run would look like nothing happened.
    """
    from app.ui.adapter import latest_run_id, rows_from_db

    try:
        outcome, problems = ingest_alerts(conn, paths)
    except Exception as exc:  # noqa: BLE001
        window.funnel.set_counts({}, incomplete_note=str(exc)[:200])
        return

    note = "; ".join(problems) if problems else ""
    if outcome is None:
        window.funnel.set_counts({}, incomplete_note=note or "nothing was added")
        return
    window.load(rows_from_db(conn),
                _stored_funnel(conn, latest_run_id(conn)),
                incomplete_note=note)


def _launch_ui(conn, *, open_board: bool) -> int:
    from PySide6.QtWidgets import QApplication

    from app.onboarding.calibration import is_calibrated
    from app.ui.adapter import connect_window, latest_run_id, rows_from_db
    from app.ui.board import BoardWindow
    from app.ui.board_adapter import board_rows, connect_board
    from app.ui.review import ReviewWindow

    app = QApplication.instance() or QApplication(sys.argv)
    from app.ui.branding import apply_icon
    apply_icon(app)

    if not is_calibrated(conn) and not open_board:
        return _launch_onboarding(app, conn)

    if open_board:
        window = BoardWindow()
        connect_board(window, conn)
        window.application_requested.connect(
            lambda oid, brief: _write_application(window, conn, oid, brief))
        rows, findings = board_rows(conn)
        window.load(rows, findings)
    else:
        window = ReviewWindow()
        # The factsheet and the CV corpus, so the detail pane can say what a
        # posting asks for that the evidence does not support. Costs nothing
        # to compute and needs no key, so it is supplied here rather than
        # behind a button: the pursue-or-reject moment is the only one where
        # it changes anything. Empty until onboarding, and the section stays
        # hidden while it is.
        window.evidence = (load_document(conn, "factsheet") + "\n\n"
                           + load_cv_text()).strip()
        window.settings_requested.connect(lambda: open_settings(window, conn))
        # Language, subscription and Restore from the menu bar. The same three
        # the setup wizard offers, because a user who skipped past them there
        # has to be able to find them afterwards.
        wire_quick_menu(window, conn, parent=window,
                        on_subscribe=lambda: open_settings(window, conn))
        # Restore opens the panel that owns the StoreKit call rather than
        # firing it from here. Apple's requirement is that Restore be
        # findable; doing it from a menu with no visible result would leave
        # the user unable to tell success from silence.
        window.restore_requested.connect(lambda: open_settings(window, conn))
        window.alerts_dropped.connect(
            lambda paths: _added_alerts(window, conn, paths))
        connect_window(window, conn)
        # The last run's shortlist, read back from the database rather than
        # held from a run this process did. Opening the app the morning after
        # is the normal case, and this window used to open empty in it — every
        # posting the run assessed was on disk and nothing put it on screen.
        run_id = latest_run_id(conn)
        window.load(rows_from_db(conn), _stored_funnel(conn, run_id),
                    notes=_screen_drift_notes(conn, run_id))

    window.show()
    _offer_update(window)
    return app.exec()


def _offer_update(parent) -> None:
    """Tell a direct-download user that a newer build exists. Nothing else.

    AFTER `window.show()`, never before. A modal raised while the window is
    still being built is a dialog with nothing behind it, on the one launch a
    person most wants to see their shortlist.

    It offers a LINK. Dawnlist does not download the new build and does not
    replace itself on disk — the artefact on the website is signed and carries
    a published SHA-256, and the person fetches it in their own browser. An
    updater that swaps a binary underneath somebody needs far more trust than
    this one has earned.

    `check()` returns None for a Store or Mac App Store build, so this is a
    no-op there. That gate lives in `app/core/updates.py` and is tested rather
    than assumed, because a Mac build reaching out for its own updates is a
    review rejection. Every failure here is swallowed: an update check must
    never be the reason the application did not open.
    """
    try:
        from app.core.updates import check

        found = check()
        if found is None:
            return

        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtWidgets import QMessageBox

        from app.i18n import tr

        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle(tr("update.title"))
        box.setText(tr("update.available", version=found.version))
        box.setInformativeText(tr("update.body"))
        get = box.addButton(tr("update.get"), QMessageBox.AcceptRole)
        box.addButton(tr("update.later"), QMessageBox.RejectRole)
        box.setDefaultButton(get)
        box.exec()
        if box.clickedButton() is get:
            QDesktopServices.openUrl(QUrl(found.url))
    except Exception:  # noqa: BLE001 - see the docstring: never block the launch
        pass


if __name__ == "__main__":
    raise SystemExit(main())
