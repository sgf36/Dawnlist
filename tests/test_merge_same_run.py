"""One version per role when two providers return the same role in one run.

The hazard is collapsing DIFFERENT roles: a hotel group posts the same title at
many sites. So the merge is guarded (1:1 pairs only, places and text must
agree) and these tests pin each guard, not just the happy path.
"""
from __future__ import annotations

from app.core.dedup import (SAME_TEXT, description_similarity, merge_same_run,
                            same_role)
from app.feed.models import Job

TEXT = ("Lead the duty management team, oversee guest arrivals, handle "
        "complaints, manage rotas, ensure health and safety compliance and "
        "support the general manager with daily reporting across the hotel. ") * 4


def job(provider, jid, *, title="Hotel Duty Manager", company="Whitbread",
        locs=("Edgware, England",), text=TEXT, salary=None, posted="2026-09-06",
        raw=None):
    from datetime import date
    y, m, d = (int(x) for x in posted.split("-"))
    return Job(provider=provider, provider_job_id=jid, title=title,
               company=company, locations=tuple(locs), description_text=text,
               posted_at=date(y, m, d), salary=salary,
               url=f"https://x/{jid}", raw_criteria=raw or {})


def merged(*jobs):
    r = merge_same_run(list(jobs))
    return [j.provider_job_id for j in r.kept], [j.provider_job_id for j, _ in r.superseded], r


def test_the_same_role_from_two_providers_becomes_one():
    kept, dropped, _ = merged(job("theirstack", "1"),
                              job("linkedin-joblibrary", "2", locs=("Edgware, England, United Kingdom",)))
    assert len(kept) == 1 and len(dropped) == 1


def test_the_richer_version_wins_per_role():
    poor = job("theirstack", "T", text=TEXT[:150])              # short description
    rich = job("linkedin-joblibrary", "L", text=TEXT, salary="GBP 40000+")
    kept, dropped, r = merged(poor, rich)
    assert kept == ["L"] and dropped == ["T"]
    assert "kept the richer version" in r.superseded[0][1]


def test_the_winner_can_be_either_provider_role_by_role():
    a1 = job("theirstack", "A1", title="Revenue Manager", company="Acme", locs=("London",), text=TEXT[:150])
    b1 = job("linkedin-joblibrary", "B1", title="Revenue Manager", company="Acme", locs=("London",), text=TEXT, salary="GBP 50000")
    a2 = job("theirstack", "A2", title="Sales Manager", company="Beta", locs=("London",), text=TEXT, salary="GBP 45000", raw={"a": 1, "b": 2})
    b2 = job("linkedin-joblibrary", "B2", title="Sales Manager", company="Beta", locs=("London",), text=TEXT[:150])
    kept, _, _ = merged(a1, b1, a2, b2)
    assert sorted(kept) == ["A2", "B1"]


def test_a_tie_keeps_the_first_listed_provider():
    kept, _, _ = merged(job("theirstack", "T"), job("linkedin-joblibrary", "L"))
    assert kept == ["T"]


# -- the guards: cases that must NOT merge -----------------------------------

def test_a_family_of_similar_roles_is_left_alone():
    """A hotel group's several Duty Manager jobs: cannot be told apart safely."""
    ts = [job("theirstack", f"T{i}", locs=(f"Place{i}, England",)) for i in range(3)]
    li = [job("linkedin-joblibrary", "L1", locs=("Place1, England",))]
    kept, dropped, _ = merged(*ts, *li)
    assert dropped == [] and len(kept) == 4


def test_two_from_the_same_provider_are_never_merged_here():
    kept, dropped, _ = merged(job("theirstack", "1"), job("theirstack", "2"))
    assert dropped == [] and len(kept) == 2


def test_different_places_are_different_roles():
    kept, dropped, _ = merged(job("theirstack", "1", locs=("Edgware, England",)),
                              job("linkedin-joblibrary", "2", locs=("Wimbledon, England",)))
    assert dropped == [] and len(kept) == 2


def test_different_text_under_the_same_title_are_different_roles():
    other = "Manage the spa treatment rooms, therapist rotas, retail stock and guest bookings for the wellness floor. " * 5
    kept, dropped, _ = merged(job("theirstack", "1"),
                              job("linkedin-joblibrary", "2", text=other))
    assert dropped == [] and len(kept) == 2


def test_different_companies_or_titles_never_enter():
    kept, dropped, _ = merged(job("theirstack", "1"),
                              job("linkedin-joblibrary", "2", company="Marriott"))
    assert dropped == []
    kept, dropped, _ = merged(job("theirstack", "1"),
                              job("linkedin-joblibrary", "2", title="Head Chef"))
    assert dropped == []


# -- what counts as the same place / the same text ---------------------------

def test_london_is_written_differently_by_each_provider():
    a = job("theirstack", "1", locs=("London",))
    b = job("linkedin-joblibrary", "2", locs=("London Area, United Kingdom",))
    assert same_role(a, b)[0]


def test_an_unknown_place_is_not_evidence_of_a_different_one():
    a = job("theirstack", "1", locs=("United Kingdom",))
    b = job("linkedin-joblibrary", "2", locs=("Edgware, England",))
    assert same_role(a, b)[0]


def test_a_missing_description_is_judged_on_place_alone():
    a = job("theirstack", "1", text="")
    b = job("linkedin-joblibrary", "2")
    ok, why = same_role(a, b)
    assert ok and "no text" in why


def test_html_entities_do_not_break_the_comparison():
    a = job("theirstack", "1", text="Waitrose & Partners hotel team lead " * 12)
    b = job("linkedin-joblibrary", "2", text="Waitrose &amp; Partners hotel team lead " * 12)
    assert description_similarity(a.description_text, b.description_text) > 0.95


def test_the_threshold_sits_under_the_measured_true_pairs():
    """Real pairs of one role scored 0.83, 0.84 and 0.97 on 2026-09-30."""
    assert SAME_TEXT < 0.83


def test_nothing_disappears_without_a_record():
    _, _, r = merged(job("theirstack", "1"), job("linkedin-joblibrary", "2"))
    assert len(r.superseded) == 1 and "same role as" in r.superseded[0][1]
