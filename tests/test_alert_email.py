"""Job-alert digests dragged onto the app. Nothing here fetches anything."""
from pathlib import Path

import pytest

from app.feed.alert_email import (PROVIDER, SNIPPET_MARKER, canonical_url,
                                  job_id_for, parse_eml, parse_html, parse_many)

DIGEST = """\
<html><body>
<p>Your job alert for hospitality strategy</p>

<a href="https://www.example-board.com/jobs/view/3921884471/?refId=AAA111&trackingId=xyz">
  Director of Asset Management
</a>
<div>Round Hill Capital &middot; London, United Kingdom</div>

<a href="https://www.example-board.com/jobs/view/3921884999/?refId=BBB222&trackingId=abc">
  Head of Commercial Strategy
</a>
<div>Rocco Forte Hotels &middot; London</div>

<a href="https://uk.indeed.com/viewjob?jk=a1b2c3d4e5&from=alert&tk=track99">
  Cluster Revenue Manager
</a>
<div>Accor &middot; Manchester</div>

<a href="https://www.example-board.com/comm/unsubscribe?token=zzz">Unsubscribe</a>
<a href="https://www.example-board.com/feed/">See all jobs</a>
<a href="https://twitter.com/exampleboard">Follow us</a>
</body></html>
"""


def write_eml(path: Path, body_html: str, *, subject="Your job alert",
              sender="alerts@example-board.com", plain_only=False) -> Path:
    if plain_only:
        raw = (f"From: {sender}\r\nTo: me@example.com\r\n"
               f"Subject: {subject}\r\nContent-Type: text/plain\r\n\r\n"
               "Three new jobs for you.\r\n")
    else:
        raw = (f"From: {sender}\r\nTo: me@example.com\r\n"
               f"Subject: {subject}\r\nMIME-Version: 1.0\r\n"
               f"Content-Type: text/html; charset=utf-8\r\n\r\n{body_html}")
    path.write_bytes(raw.encode("utf-8"))
    return path


# -- the cards --------------------------------------------------------------
def test_job_cards_are_parsed_out():
    jobs = parse_html(DIGEST)
    titles = [j.title for j in jobs]
    assert "Director of Asset Management" in titles
    assert "Head of Commercial Strategy" in titles
    assert "Cluster Revenue Manager" in titles
    assert len(jobs) == 3


def test_company_and_location_are_read_from_the_card():
    jobs = {j.title: j for j in parse_html(DIGEST)}
    card = jobs["Director of Asset Management"]
    assert card.company == "Round Hill Capital"
    assert card.locations == ("London, United Kingdom",)


def test_navigation_links_are_not_jobs():
    urls = [j.url for j in parse_html(DIGEST)]
    assert not any("unsubscribe" in u for u in urls)
    assert not any("twitter" in u for u in urls)
    assert not any(u.rstrip("/").endswith("/feed") for u in urls)


def test_every_card_uses_the_alert_email_provider():
    assert all(j.provider == PROVIDER for j in parse_html(DIGEST))


# -- the snippet marker -----------------------------------------------------
def test_a_card_is_marked_as_a_snippet():
    """Two lines passed to the assessment unmarked read as a full posting and
    invite a confident rejection on requirements the card never stated."""
    job = parse_html(DIGEST)[0]
    assert SNIPPET_MARKER.strip() in job.description_text
    assert "NOT CHECKED" in job.description_text


def test_the_marker_survives_into_the_assessment_prompt():
    from app.intelligence.prompts import render_posting
    job = parse_html(DIGEST)[0]
    block = render_posting(job.provider_job_id, job.title, job.company,
                           "London", job.description_text)
    assert "NOT CHECKED" in block


# -- tracking parameters, which is the whole dedup story --------------------
def test_tracking_parameters_are_stripped():
    a = "https://b.com/jobs/view/123/?refId=AAA&trackingId=xyz"
    b = "https://b.com/jobs/view/123/?refId=ZZZ&trackingId=999"
    assert canonical_url(a) == canonical_url(b)


def test_meaningful_parameters_are_kept():
    a = "https://uk.indeed.com/viewjob?jk=aaa111&from=alert&tk=t1"
    b = "https://uk.indeed.com/viewjob?jk=bbb222&from=alert&tk=t2"
    assert canonical_url(a) != canonical_url(b)
    assert "jk=aaa111" in canonical_url(a)


def test_the_same_posting_in_two_digests_is_one_row(tmp_path):
    """Digest links carry per-send tracking. Keying on the raw URL would
    surface one job twice, every time, and look like a broken feed."""
    tuesday = DIGEST
    thursday = DIGEST.replace("AAA111", "CCC333").replace("xyz", "later")
    paths = [write_eml(tmp_path / "tue.eml", tuesday),
             write_eml(tmp_path / "thu.eml", thursday)]
    jobs, problems = parse_many(paths)
    assert problems == []
    assert len(jobs) == 3, "the same three postings, not six"


def test_the_id_is_stable_across_tracking_changes():
    a = job_id_for("https://b.com/jobs/view/123/?refId=AAA&trackingId=xyz")
    b = job_id_for("https://b.com/jobs/view/123/?refId=ZZZ")
    assert a == b


def test_different_postings_get_different_ids():
    assert job_id_for("https://b.com/jobs/view/123/") != \
        job_id_for("https://b.com/jobs/view/456/")


# -- files ------------------------------------------------------------------
def test_an_eml_is_parsed(tmp_path):
    result = parse_eml(write_eml(tmp_path / "alert.eml", DIGEST))
    assert result.ok and len(result.jobs) == 3
    assert "example-board.com" in result.source


def test_a_plain_text_only_email_says_what_to_do(tmp_path):
    result = parse_eml(write_eml(tmp_path / "plain.eml", "", plain_only=True))
    assert not result.ok
    assert "save the original email" in result.error


def test_an_email_with_no_job_links_is_distinguishable_from_a_parse_failure(tmp_path):
    """'It was read and had nothing in it' and 'it would not read' are
    different problems with different fixes."""
    path = write_eml(tmp_path / "newsletter.eml",
                     "<html><body><a href='https://x.com/about'>About us</a>"
                     "</body></html>")
    result = parse_eml(path)
    assert not result.ok and "no job links found" in result.error


def test_a_missing_file_is_reported(tmp_path):
    result = parse_eml(tmp_path / "gone.eml")
    assert not result.ok and "no longer exists" in result.error


def test_one_bad_file_never_stops_the_others(tmp_path):
    good = write_eml(tmp_path / "good.eml", DIGEST)
    bad = tmp_path / "missing.eml"
    jobs, problems = parse_many([bad, good])
    assert len(jobs) == 3 and len(problems) == 1


# -- these jobs behave like any other ---------------------------------------
def test_parsed_cards_pass_through_the_screen():
    from app.core.rules import RuleTable
    from app.core.screen import Verdict, screen_all

    table = RuleTable(strong_terms=["strategy"])
    report = screen_all(parse_html(DIGEST), table)
    likely = [r.job.title for r in report.likely]
    assert "Head of Commercial Strategy" in likely
    assert report.counts["screened"] == 3


def test_parsed_cards_dedup_against_the_feed():
    from app.core.dedup import dedup
    jobs = parse_html(DIGEST)
    result = dedup(jobs + jobs)
    assert len(result.unique) == 3 and len(result.exact_duplicates) == 3


# -- mail-security URL rewriting -------------------------------------------
SAFELINK = ("https://gbr01.safelinks.protection.outlook.com/?url="
            "https%3A%2F%2Fwww.linkedin.com%2Fcomm%2Fjobs%2Fview%2F4461835339"
            "%2F%3FtrackingId%3Dabc&data=05%7C02&reserved=0")


def test_a_safelinks_wrapper_is_unwrapped():
    """Microsoft Defender rewrites EVERY url in mail arriving at a protected
    tenant. Measured against four real LinkedIn digests: 0 postings parsed,
    because every href was a safelinks wrapper and the job path was only
    visible percent-encoded inside it."""
    from app.feed.alert_email import unwrap_redirect
    assert unwrap_redirect(SAFELINK).startswith(
        "https://www.linkedin.com/comm/jobs/view/4461835339")


def test_other_mail_filters_unwrap_too():
    from app.feed.alert_email import unwrap_redirect
    for host, param in (("protect-eu.mimecast.com", "u"),
                        ("urldefense.proofpoint.com", "u")):
        wrapped = (f"https://{host}/s/xyz?{param}="
                   "https%3A%2F%2Fexample.com%2Fjobs%2Fview%2F99")
        assert unwrap_redirect(wrapped) == "https://example.com/jobs/view/99"


def test_an_ordinary_url_is_untouched():
    from app.feed.alert_email import unwrap_redirect
    plain = "https://www.linkedin.com/jobs/view/123/?trk=x"
    assert unwrap_redirect(plain) == plain


def test_nested_wrappers_terminate():
    """Two filters in the path wrap the wrapper. Bounded, or a malformed link
    hangs the parse."""
    from app.feed.alert_email import unwrap_redirect
    inner = "https%3A%2F%2Fexample.com%2Fjobs%2Fview%2F1"
    once = f"https://protect-eu.mimecast.com/s/a?u={inner}"
    twice = ("https://gbr01.safelinks.protection.outlook.com/?url="
             + once.replace(":", "%3A").replace("/", "%2F").replace("?", "%3F")
                   .replace("=", "%3D"))
    assert unwrap_redirect(twice) == "https://example.com/jobs/view/1"


def test_a_comm_link_is_a_posting_not_chrome():
    """`/comm/` was in the chrome exclusion list, and EVERY real LinkedIn digest
    job link is `linkedin.com/comm/jobs/view/<id>` — so the filter killed
    precisely the links it exists to find."""
    from app.feed.alert_email import _looks_like_a_posting
    assert _looks_like_a_posting(
        "https://www.linkedin.com/comm/jobs/view/4461835339/")


def test_the_digests_own_furniture_is_not_a_posting():
    """"Your job alert for ..." and "Manage job alerts" link to the same job
    paths as the cards and survive every URL test."""
    from app.feed.alert_email import parse_html
    html = ('<a href="https://www.linkedin.com/comm/jobs/view/1/">'
            'Your job alert for ("asset management")</a><p>x</p>'
            '<a href="https://www.linkedin.com/comm/jobs/view/2/">'
            'Manage job alerts</a><p>y</p>'
            '<a href="https://www.linkedin.com/comm/jobs/view/3/">'
            'Head of Asset Management</a><p>Aprirose &middot; London</p>')
    jobs = parse_html(html, source="test")
    assert [j.title for j in jobs] == ["Head of Asset Management"]


def test_markup_never_reaches_the_employer_field():
    """Better an empty employer the reader can see than a raw tag."""
    from app.feed.alert_email import parse_html
    html = ('<a href="https://www.linkedin.com/comm/jobs/view/9/">'
            'Senior Asset Manager</a><h2 class="text-md">not a company</h2>')
    for job in parse_html(html, source="test"):
        assert "<" not in job.company and ">" not in job.company
