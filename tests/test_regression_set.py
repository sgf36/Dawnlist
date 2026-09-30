"""Regression set — 40 postings with known-correct verdicts.

Audit rec 9: "Twenty postings with known-correct verdicts and reasons, run
on every prompt change. Every defect above would have been caught by a fixed
set of twenty." Extended to 40 to cover more credential requirements,
language requirements, seniority computation, field-line quoting, and
retry-downgraded flow edge cases.

These exercise the full guard chain (enforce_quote_rule + candidate-fact
guard + retry logic) against fixture postings whose correct outcome is
known. The `send` function is injected, so no network calls are made.

Each case documents:
  - the posting (title, company, description snippet)
  - the model's raw verdict
  - why the guard should pass or catch it

Run with: pytest tests/test_regression_set.py -v
"""
import pytest

from app.feed.models import Job
from app.intelligence.assess import assess, enforce_quote_rule


def _job(jid, title, company, description, **kw):
    return Job(provider="theirstack", provider_job_id=jid, title=title,
               company=company, description_text=description, url="", **kw)


def _send_verdict(ref, bucket, reason, quote=None, checked=True):
    def send(_request):
        return {"verdicts": [{"job_ref": ref, "bucket": bucket,
                              "reason": reason, "disqualifying_quote": quote,
                              "requirement_checked": checked}]}
    return send


# ── 1. Legitimate strong verdict ─────────────────────────────────────────────

BROOKFIELD = _job(
    "1", "Analyst, Asset Management Living and Hospitality", "Brookfield",
    "Brookfield Asset Management seeks an Analyst for its Living and "
    "Hospitality portfolio in London. The role covers hotel-level "
    "operating budgets, capex planning, and investor reporting.")


def test_01_strong_verdict_on_a_genuine_fit_stands():
    report = assess([BROOKFIELD], "brief", "facts",
                    send=_send_verdict("theirstack:1", "strong",
                                      "hospitality asset management, London"))
    assert report.verdicts[0].bucket == "strong"
    assert report.verdicts[0].downgraded_from is None


# ── 2. Fabricated quote caught ───────────────────────────────────────────────

BNP = _job(
    "2", "Financial Controller", "BNP Paribas",
    "We are hiring a Financial Controller to manage regulatory reporting "
    "across our London office. Must hold ACCA/ACA qualification.")


def test_02_fabricated_quote_is_downgraded():
    v = enforce_quote_rule(
        {"bucket": "rejected", "reason": "banking role, not hospitality",
         "disqualifying_quote": "minimum 8 years in investment banking",
         "requirement_checked": True}, BNP)
    assert v.bucket == "judgement-call"
    assert "does not appear" in v.downgrade_reason


# ── 3. Real quote stands ────────────────────────────────────────────────────

CAPCO = _job(
    "3", "WAM Consultant", "Capco",
    "Capco is seeking a Wealth and Asset Management consultant. Requires "
    "10+ years of experience in discretionary and/or advisory Wealth or "
    "Asset Management. Must be based in London.")


def test_03_rejection_quoting_a_real_years_floor_stands():
    v = enforce_quote_rule(
        {"bucket": "rejected", "reason": "years floor too high",
         "disqualifying_quote": "10+ years of experience in discretionary "
         "and/or advisory Wealth or Asset Management",
         "requirement_checked": True}, CAPCO)
    assert v.bucket == "rejected"
    assert v.downgraded_from is None


# ── 4. Category label instead of evidence ────────────────────────────────────

BDO = _job(
    "4", "FS Audit Manager", "BDO UK",
    "Manage audit engagements for financial services clients. "
    "ACCA/ACA/ICAS qualified or overseas equivalent. 5+ years' audit "
    "experience required.")


def test_04_category_argument_without_quote_is_caught():
    v = enforce_quote_rule(
        {"bucket": "rejected",
         "reason": "generic accountancy audit role with no hospitality tie",
         "disqualifying_quote": None,
         "requirement_checked": True}, BDO)
    assert v.bucket == "judgement-call"
    assert "quoted nothing" in v.downgrade_reason


# ── 5. Empty reason on a hospitality posting ─────────────────────────────────

DISNEY = _job(
    "5", "Senior Manager, Hotel New Build Projects", "Disney Parks",
    "Lead hotel operations readiness for new-build resort projects. "
    "Food and beverage, guest services, and onboard revenue.")


def test_05_empty_reason_rejection_is_downgraded():
    v = enforce_quote_rule(
        {"bucket": "rejected", "reason": "",
         "disqualifying_quote": "onboard revenue",
         "requirement_checked": True}, DISNEY)
    assert v.bucket == "judgement-call"
    assert "no stated reason" in v.downgrade_reason


# ── 6. Candidate fact — "overqualified" ──────────────────────────────────────

BLACKSTONE = _job(
    "6", "2027 Analyst Program", "Blackstone",
    "Blackstone Real Estate is hiring for its 2027 Analyst class. "
    "Open to recent graduates or those with up to 2 years of experience.")


def test_06_overqualified_claim_is_downgraded():
    v = enforce_quote_rule(
        {"bucket": "rejected",
         "reason": "candidate is overqualified for this entry-level role",
         "disqualifying_quote": "up to 2 years of experience",
         "requirement_checked": True}, BLACKSTONE)
    assert v.bucket == "judgement-call"
    assert "candidate" in v.downgrade_reason


# ── 7. Candidate fact — computed tenure ──────────────────────────────────────

def test_07_computed_tenure_from_student_work_is_caught():
    v = enforce_quote_rule(
        {"bucket": "rejected",
         "reason": "Spencer is mid-career (10+ years post-Peninsula Beverly "
         "Hills in 2015-2016) and overqualified",
         "disqualifying_quote": "up to 2 years of experience",
         "requirement_checked": True}, BLACKSTONE)
    assert v.bucket == "judgement-call"
    assert "candidate" in v.downgrade_reason


# ── 8. Unchecked requirement may not reject ──────────────────────────────────

TRUNCATED = _job(
    "8", "Hotel Manager", "Mandarin Oriental",
    "The Mandarin Oriental, London is seeking a Hotel Manager to oversee "
    "daily operations of the 181-room property...")


def test_08_unchecked_requirement_is_never_a_rejection():
    v = enforce_quote_rule(
        {"bucket": "rejected", "reason": "description was truncated",
         "disqualifying_quote": None,
         "requirement_checked": False}, TRUNCATED)
    assert v.bucket == "possible"
    assert "not checked" in v.downgrade_reason


# ── 9. Judgement-call passes through untouched ───────────────────────────────

AURICOE_VP = _job(
    "9", "VP, Investment Management", "Auricoe",
    "Lead real estate investment origination across European markets. "
    "The role reports to the Managing Director.")


def test_09_judgement_call_is_not_touched():
    v = enforce_quote_rule(
        {"bucket": "judgement-call",
         "reason": "seniority above the core band",
         "disqualifying_quote": None,
         "requirement_checked": True}, AURICOE_VP)
    assert v.bucket == "judgement-call"
    assert v.downgraded_from is None


# ── 10. Possible verdict passes through ──────────────────────────────────────

CRITERION = _job(
    "10", "Asset Manager", "Criterion Capital",
    "Criterion Capital — developer, asset manager and owner/operator of "
    "long-term hospitality and residential assets. Manage a mixed-use "
    "London portfolio.")


def test_10_possible_verdict_passes_through():
    v = enforce_quote_rule(
        {"bucket": "possible",
         "reason": "hospitality asset manager, partial function match",
         "disqualifying_quote": None,
         "requirement_checked": True}, CRITERION)
    assert v.bucket == "possible"
    assert v.downgraded_from is None


# ── 11. Short fragment is not a real quote ───────────────────────────────────

CLEARWATER = _job(
    "11", "Principal Product Manager", "Clearwater Analytics",
    "Clearwater Analytics seeks a Principal Product Manager with 10+ years "
    "of experience designing and shipping enterprise cloud software.")


def test_11_two_word_fragment_is_not_a_valid_quote():
    v = enforce_quote_rule(
        {"bucket": "rejected", "reason": "software product management",
         "disqualifying_quote": "10+ years",
         "requirement_checked": True}, CLEARWATER)
    assert v.bucket == "judgement-call"
    assert "too short" in v.downgrade_reason


# ── 12. Real credential quote stands ────────────────────────────────────────

SCHRODERS = _job(
    "12", "Greencoat Finance Asset Manager", "Schroders",
    "Manage the financial performance of Schroders Greencoat renewable "
    "energy assets. ACA/ACCA/CIMA qualification required.")


def test_12_credential_requirement_quote_stands():
    v = enforce_quote_rule(
        {"bucket": "rejected", "reason": "requires ACA/ACCA/CIMA",
         "disqualifying_quote": "ACA/ACCA/CIMA qualification required",
         "requirement_checked": True}, SCHRODERS)
    assert v.bucket == "rejected"
    assert v.downgraded_from is None


# ── 13. Language requirement is a valid hard gate ────────────────────────────

BROOKFIELD_DE = _job(
    "13", "German Speaking Client Associate", "Brookfield",
    "Brookfield Asset Management seeks a German-speaking associate for its "
    "European investor relations team. Fluent German required.")


def test_13_language_requirement_is_a_valid_rejection():
    v = enforce_quote_rule(
        {"bucket": "rejected", "reason": "German language requirement",
         "disqualifying_quote": "Fluent German required",
         "requirement_checked": True}, BROOKFIELD_DE)
    assert v.bucket == "rejected"
    assert v.downgraded_from is None


# ── 14. Field-line quote (hard constraint) stands ────────────────────────────

US_ROLE = _job(
    "14", "Hotel General Manager", "Aimbridge Hospitality",
    "Manage a 300-room full-service hotel in downtown Chicago.",
    raw_criteria={"country_codes": ["US"]})


def test_14_country_field_line_is_a_valid_quote():
    v = enforce_quote_rule(
        {"bucket": "rejected", "reason": "brief: UK only",
         "disqualifying_quote": "country: US",
         "requirement_checked": True}, US_ROLE)
    assert v.bucket == "rejected"
    assert v.downgraded_from is None


# ── 15. "underqualified" is a candidate-fact rejection ───────────────────────

SENIOR_ROLE = _job(
    "15", "Managing Director, Real Estate", "Goldman Sachs",
    "Goldman Sachs Asset Management seeks a Managing Director. "
    "20+ years in real estate investment management.")


def test_15_underqualified_claim_is_downgraded():
    v = enforce_quote_rule(
        {"bucket": "rejected",
         "reason": "candidate is underqualified for MD level",
         "disqualifying_quote": "20+ years in real estate investment management",
         "requirement_checked": True}, SENIOR_ROLE)
    assert v.bucket == "judgement-call"
    assert "candidate" in v.downgrade_reason


# ── 16. A posting-focused years-floor reason (no candidate claim) stands ─────

def test_16_years_floor_without_candidate_claim_stands():
    v = enforce_quote_rule(
        {"bucket": "rejected",
         "reason": "years floor exceeds brief range",
         "disqualifying_quote": "20+ years in real estate investment management",
         "requirement_checked": True}, SENIOR_ROLE)
    assert v.bucket == "rejected"
    assert v.downgraded_from is None


# ── 17. Retry-downgraded flow ────────────────────────────────────────────────

RITZ = _job(
    "17", "Health & Safety Manager", "The Ritz London",
    "The Ritz London seeks a Health & Safety Manager to ensure "
    "compliance with all H&S regulations across the hotel. "
    "NEBOSH qualification essential.")


def test_17_downgraded_rejection_gets_retried():
    """A rejection downgraded by the empty-reason guard should be retried,
    giving the model a second chance with full=True."""
    calls = []

    def send(request):
        calls.append(request)
        if len(calls) == 1:
            return {"verdicts": [{"job_ref": "theirstack:17",
                                  "bucket": "rejected", "reason": "",
                                  "disqualifying_quote": "NEBOSH qualification essential",
                                  "requirement_checked": True}]}
        return {"verdicts": [{"job_ref": "theirstack:17",
                              "bucket": "rejected",
                              "reason": "requires NEBOSH, a specialist H&S credential",
                              "disqualifying_quote": "NEBOSH qualification essential",
                              "requirement_checked": True}]}

    report = assess([RITZ], "brief", "facts", send=send)
    assert len(calls) >= 2, "the downgraded verdict should trigger a retry"
    final = report.verdicts[0]
    assert final.bucket == "rejected"
    assert final.reason == "requires NEBOSH, a specialist H&S credential"


# ── 18. Strong verdict on a truncated posting is re-read ─────────────────────

LONG_DESC = "A" * 25_000 + " unique-marker-deep " + "B" * 5_000
FAIRMONT = _job("18", "Night Manager, The Savoy Hotel", "Fairmont",
                LONG_DESC)


def test_18_strong_on_truncated_gets_reread():
    calls = []

    def send(request):
        calls.append(request)
        return {"verdicts": [{"job_ref": "theirstack:18", "bucket": "strong",
                              "reason": "luxury hotel operations",
                              "disqualifying_quote": None,
                              "requirement_checked": True}]}

    report = assess([FAIRMONT], "brief", "facts", send=send)
    assert len(calls) == 2
    assert "unique-marker-deep" in str(calls[1])
    assert report.verdicts[0].full_read is True


# ── 19. Unknown bucket normalised to judgement-call ──────────────────────────

MARRIOTT = _job(
    "19", "Duty Manager, County Hall", "Marriott International",
    "Marriott International seeks a Duty Manager for the London County "
    "Hall hotel. Shift management, guest services, and P&L oversight.")


def test_19_unknown_bucket_becomes_judgement_call():
    v = enforce_quote_rule(
        {"bucket": "maybe", "reason": "unclear seniority"}, MARRIOTT)
    assert v.bucket == "judgement-call"


# ── 20. "candidate has" phrasing is caught ───────────────────────────────────

EMERSON = _job(
    "20", "Senior PM", "Emerson Partners",
    "Emerson Partners seeks a Senior Project Manager. MRICS required, "
    "minimum 10 years' experience in commercial real estate.")


def test_20_candidate_has_phrasing_is_caught():
    v = enforce_quote_rule(
        {"bucket": "rejected",
         "reason": "candidate has insufficient PM credentials for MRICS",
         "disqualifying_quote": "MRICS required",
         "requirement_checked": True}, EMERSON)
    assert v.bucket == "judgement-call"
    assert "candidate" in v.downgrade_reason


# ── 21. CFA credential requirement stands ──────────────────────────────────

ABERDEEN = _job(
    "21", "Investment Director, Real Assets", "abrdn",
    "abrdn seeks an Investment Director for its Real Assets platform. "
    "The successful candidate will lead origination and execution of "
    "direct real estate investments across Europe. CFA charterholder "
    "or equivalent professional qualification required.")


def test_21_cfa_credential_requirement_stands():
    v = enforce_quote_rule(
        {"bucket": "rejected", "reason": "requires CFA",
         "disqualifying_quote": "CFA charterholder or equivalent professional "
         "qualification required",
         "requirement_checked": True}, ABERDEEN)
    assert v.bucket == "rejected"
    assert v.downgraded_from is None


# ── 22. FCA authorisation requirement stands ───────────────────────────────

SAVILLS_IM = _job(
    "22", "Fund Manager", "Savills Investment Management",
    "Savills Investment Management seeks a Fund Manager. Must be FCA "
    "CF30 authorised or hold equivalent regulatory approval. "
    "Responsible for a £500m UK commercial real estate portfolio.")


def test_22_fca_authorisation_requirement_stands():
    v = enforce_quote_rule(
        {"bucket": "rejected", "reason": "FCA CF30 authorisation required",
         "disqualifying_quote": "Must be FCA CF30 authorised or hold equivalent "
         "regulatory approval",
         "requirement_checked": True}, SAVILLS_IM)
    assert v.bucket == "rejected"
    assert v.downgraded_from is None


# ── 23. French language requirement stands ─────────────────────────────────

ACCOR_FR = _job(
    "23", "Responsable Revenue Management", "Accor",
    "Accor recherche un(e) Responsable Revenue Management pour ses "
    "hôtels parisiens. Maîtrise du français indispensable. "
    "Expérience en revenue management hôtelier requise.")


def test_23_french_language_requirement_stands():
    v = enforce_quote_rule(
        {"bucket": "rejected", "reason": "French language requirement",
         "disqualifying_quote": "Maîtrise du français indispensable",
         "requirement_checked": True}, ACCOR_FR)
    assert v.bucket == "rejected"
    assert v.downgraded_from is None


# ── 24. Mandarin language requirement stands ───────────────────────────────

ROSEWOOD_HK = _job(
    "24", "Assistant Director of Revenue", "Rosewood Hotel Group",
    "Rosewood Hotel Group seeks an Assistant Director of Revenue for "
    "its Hong Kong property. Must be fluent in Mandarin and Cantonese. "
    "Revenue management experience in luxury hospitality required.")


def test_24_mandarin_language_requirement_stands():
    v = enforce_quote_rule(
        {"bucket": "rejected", "reason": "Mandarin and Cantonese required",
         "disqualifying_quote": "Must be fluent in Mandarin and Cantonese",
         "requirement_checked": True}, ROSEWOOD_HK)
    assert v.bucket == "rejected"
    assert v.downgraded_from is None


# ── 25. "Spencer is" phrasing is caught ────────────────────────────────────

ARES = _job(
    "25", "Vice President, Real Estate Debt", "Ares Management",
    "Ares Management seeks a VP for its Real Estate Debt team. "
    "Underwriting and origination of senior and mezzanine loans "
    "across European markets. 8+ years of CRE debt experience.")


def test_25_spencer_is_phrasing_is_caught():
    v = enforce_quote_rule(
        {"bucket": "rejected",
         "reason": "Spencer is an equity investor, not a debt specialist",
         "disqualifying_quote": "8+ years of CRE debt experience",
         "requirement_checked": True}, ARES)
    assert v.bucket == "judgement-call"
    assert "candidate" in v.downgrade_reason


# ── 26. "applicant lacks" phrasing is caught ───────────────────────────────

CBRE_IM = _job(
    "26", "Portfolio Manager, EMEA", "CBRE Investment Management",
    "CBRE Investment Management seeks a Portfolio Manager for its "
    "EMEA real estate portfolio. Responsible for asset-level strategy "
    "and NOI performance across 15 assets.")


def test_26_applicant_lacks_phrasing_is_caught():
    v = enforce_quote_rule(
        {"bucket": "rejected",
         "reason": "applicant lacks multi-asset portfolio management experience",
         "disqualifying_quote": None,
         "requirement_checked": True}, CBRE_IM)
    assert v.bucket == "judgement-call"
    assert "candidate" in v.downgrade_reason


# ── 27. Seniority — VP at a bank is in band, should not reject ─────────────

JPMORGAN = _job(
    "27", "Vice President, Real Estate Banking", "J.P. Morgan",
    "J.P. Morgan seeks a Vice President for its Real Estate Banking "
    "team. Coverage of UK and European real estate sponsors. "
    "Financial modelling and deal execution.")


def test_27_vp_seniority_reason_without_candidate_claim_stands():
    v = enforce_quote_rule(
        {"bucket": "possible",
         "reason": "banking-side VP, partial function overlap",
         "disqualifying_quote": None,
         "requirement_checked": True}, JPMORGAN)
    assert v.bucket == "possible"
    assert v.downgraded_from is None


# ── 28. Seniority — "too junior" is a candidate-fact claim ─────────────────

STARWOOD = _job(
    "28", "Analyst, European Acquisitions", "Starwood Capital Group",
    "Starwood Capital Group seeks an Analyst. Financial modelling "
    "and underwriting of European hotel and resort acquisitions. "
    "Recent graduate or 1-2 years of experience.")


def test_28_too_junior_claim_is_downgraded():
    v = enforce_quote_rule(
        {"bucket": "rejected",
         "reason": "candidate is too senior for this analyst-level role",
         "disqualifying_quote": "Recent graduate or 1-2 years of experience",
         "requirement_checked": True}, STARWOOD)
    assert v.bucket == "judgement-call"
    assert "candidate" in v.downgrade_reason


# ── 29. Strong verdict with unchecked requirement passes ───────────────────

FOUR_SEASONS = _job(
    "29", "Director of Finance", "Four Seasons Hotels and Resorts",
    "Four Seasons Hotels and Resorts seeks a Director of Finance "
    "for its London property. Full P&L oversight, budgeting, and "
    "financial reporting for a 190-room luxury hotel.")


def test_29_strong_with_unchecked_requirement_passes():
    v = enforce_quote_rule(
        {"bucket": "strong",
         "reason": "luxury hotel finance leadership, London",
         "disqualifying_quote": None,
         "requirement_checked": False}, FOUR_SEASONS)
    assert v.bucket == "strong"
    assert v.downgraded_from is None


# ── 30. Rejection with fabricated salary quote is caught ───────────────────

IHG = _job(
    "30", "Revenue Manager, UK Managed Hotels", "IHG",
    "IHG seeks a Revenue Manager for its UK managed portfolio. "
    "Revenue strategy, pricing and demand forecasting across "
    "multiple properties. Competitive salary and benefits.")


def test_30_fabricated_salary_quote_is_caught():
    v = enforce_quote_rule(
        {"bucket": "rejected",
         "reason": "salary below brief minimum",
         "disqualifying_quote": "salary: £35,000 per annum",
         "requirement_checked": True}, IHG)
    assert v.bucket == "judgement-call"
    assert "does not appear" in v.downgrade_reason


# ── 31. Rejection quoting a real employment-type field line stands ─────────

PART_TIME = _job(
    "31", "Night Auditor", "The Dorchester",
    "The Dorchester seeks a Night Auditor for overnight front desk "
    "operations. Part-time, 3 nights per week.",
    raw_criteria={"employment_statuses": ["part_time"]})


def test_31_employment_type_field_line_stands():
    v = enforce_quote_rule(
        {"bucket": "rejected", "reason": "brief: full-time only",
         "disqualifying_quote": "employment type: part_time",
         "requirement_checked": True}, PART_TIME)
    assert v.bucket == "rejected"
    assert v.downgraded_from is None


# ── 32. "mid-career" label is a candidate-fact ─────────────────────────────

HENDERSON = _job(
    "32", "Associate Director, Alternatives", "Henderson Park",
    "Henderson Park seeks an Associate Director. Real estate private "
    "equity, pan-European acquisitions and asset management.")


def test_32_mid_career_label_is_caught():
    v = enforce_quote_rule(
        {"bucket": "rejected",
         "reason": "mid-career role below the target seniority",
         "disqualifying_quote": None,
         "requirement_checked": True}, HENDERSON)
    assert v.bucket == "judgement-call"
    assert "candidate" in v.downgrade_reason


# ── 33. Legitimate sector mismatch with real quote stands ──────────────────

DELOITTE = _job(
    "33", "Manager, Forensic Accounting", "Deloitte",
    "Deloitte seeks a Manager in its Forensic Accounting practice. "
    "Investigating financial irregularities and supporting litigation. "
    "ACA/ACCA qualified with forensic accounting experience.")


def test_33_sector_mismatch_with_real_quote_stands():
    v = enforce_quote_rule(
        {"bucket": "rejected",
         "reason": "forensic accounting, not hospitality or real estate",
         "disqualifying_quote": "Investigating financial irregularities and "
         "supporting litigation",
         "requirement_checked": True}, DELOITTE)
    assert v.bucket == "rejected"
    assert v.downgraded_from is None


# ── 34. Retry succeeds on second attempt ───────────────────────────────────

LANGHAM = _job(
    "34", "Director of Sales", "The Langham London",
    "The Langham London seeks a Director of Sales. Drive corporate "
    "and leisure revenue, manage a team of 8, and oversee RFP "
    "responses. Minimum 5 years hotel sales leadership experience.")


def test_34_retry_fixes_fabricated_quote():
    calls = []

    def send(request):
        calls.append(request)
        if len(calls) == 1:
            return {"verdicts": [{"job_ref": "theirstack:34",
                                  "bucket": "rejected",
                                  "reason": "requires 10 years hotel experience",
                                  "disqualifying_quote": "10 years in hotel "
                                  "sales management required",
                                  "requirement_checked": True}]}
        return {"verdicts": [{"job_ref": "theirstack:34",
                              "bucket": "rejected",
                              "reason": "5 years hotel sales leadership floor",
                              "disqualifying_quote": "Minimum 5 years hotel "
                              "sales leadership experience",
                              "requirement_checked": True}]}

    report = assess([LANGHAM], "brief", "facts", send=send)
    assert len(calls) >= 2
    final = report.verdicts[0]
    assert final.bucket == "rejected"
    assert "5 years" in final.reason


# ── 35. Legitimate years floor with compound phrasing stands ───────────────

INVESCO = _job(
    "35", "Managing Director, Real Estate", "Invesco Real Estate",
    "Invesco Real Estate seeks a Managing Director. 15+ years of "
    "direct real estate investment experience across European markets. "
    "Track record in fund management and investor relations.")


def test_35_compound_years_floor_stands():
    v = enforce_quote_rule(
        {"bucket": "rejected",
         "reason": "15-year experience floor exceeds brief range",
         "disqualifying_quote": "15+ years of direct real estate investment "
         "experience across European markets",
         "requirement_checked": True}, INVESCO)
    assert v.bucket == "rejected"
    assert v.downgraded_from is None


# ── 36. Computed "10+ years post-graduation" is caught ─────────────────────

HILTON_GRAD = _job(
    "36", "Graduate Development Programme", "Hilton",
    "Hilton's Graduate Development Programme. Rotational placements "
    "across hotel operations, revenue and commercial functions. "
    "Open to graduates from the class of 2026.")


def test_36_computed_post_graduation_tenure_is_caught():
    v = enforce_quote_rule(
        {"bucket": "rejected",
         "reason": "10+ years post-graduation since Spencer graduated in 2016",
         "disqualifying_quote": "Open to graduates from the class of 2026",
         "requirement_checked": True}, HILTON_GRAD)
    assert v.bucket == "judgement-call"
    assert "candidate" in v.downgrade_reason


# ── 37. Arabic language requirement stands ─────────────────────────────────

JUMEIRAH = _job(
    "37", "Director of Revenue Management", "Jumeirah Group",
    "Jumeirah Group seeks a Director of Revenue Management for its "
    "Dubai portfolio. Must be fluent in Arabic. Experience with "
    "Opera and IDeaS revenue management systems.")


def test_37_arabic_language_requirement_stands():
    v = enforce_quote_rule(
        {"bucket": "rejected", "reason": "Arabic language requirement",
         "disqualifying_quote": "Must be fluent in Arabic",
         "requirement_checked": True}, JUMEIRAH)
    assert v.bucket == "rejected"
    assert v.downgraded_from is None


# ── 38. PE qualification requirement stands ────────────────────────────────

LaSALLE = _job(
    "38", "Head of Research, Europe", "LaSalle Investment Management",
    "LaSalle Investment Management seeks a Head of Research. Lead "
    "the European research function, produce market forecasts and "
    "investment strategy papers. Must hold IPF Diploma or equivalent "
    "real estate research qualification.")


def test_38_ipf_diploma_requirement_stands():
    v = enforce_quote_rule(
        {"bucket": "rejected", "reason": "requires IPF Diploma",
         "disqualifying_quote": "Must hold IPF Diploma or equivalent real "
         "estate research qualification",
         "requirement_checked": True}, LaSALLE)
    assert v.bucket == "rejected"
    assert v.downgraded_from is None


# ── 39. Judgement-call with partial function overlap passes through ─────────

CUSHWAKE = _job(
    "39", "Head of Hospitality Valuation, EMEA", "Cushman & Wakefield",
    "Cushman & Wakefield seeks a Head of Hospitality Valuation for "
    "EMEA. Lead hotel valuation mandates for institutional investors, "
    "lenders and operators. RICS-accredited valuer preferred.")


def test_39_judgement_call_with_partial_overlap_passes():
    v = enforce_quote_rule(
        {"bucket": "judgement-call",
         "reason": "valuation advisory, not asset management, but sector fit",
         "disqualifying_quote": None,
         "requirement_checked": True}, CUSHWAKE)
    assert v.bucket == "judgement-call"
    assert v.downgraded_from is None


# ── 40. Rejection quoting a real posted-date field line stands ─────────────

from datetime import date

STALE_POSTING = _job(
    "40", "Asset Manager, Hospitality", "Patrizia AG",
    "Patrizia AG seeks an Asset Manager for its hospitality "
    "portfolio across Germany and Austria.",
    posted_at=date(2025, 3, 1))


def test_40_stale_posting_date_field_line_stands():
    v = enforce_quote_rule(
        {"bucket": "rejected", "reason": "brief: postings from last 30 days only",
         "disqualifying_quote": "posted: 2025-03-01",
         "requirement_checked": True}, STALE_POSTING)
    assert v.bucket == "rejected"
    assert v.downgraded_from is None
