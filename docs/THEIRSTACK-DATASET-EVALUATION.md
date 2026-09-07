# TheirStack bulk dataset — evaluation against what Dawnlist needs

**Evaluated 2026-09-07** against the free sample from workspace 200772
(`data.parquet`, 3.6 MB, 2,000 rows, 34 columns). Samples and the data
dictionary are free — no credits, no commitment — so this cost nothing and
committed to nothing. **No tier was purchased.**

The sample was mapped through Dawnlist's own `TheirStackProvider._to_job`, then
run through the real dedup and the real four-tier screen. Nothing below is read
off a pricing page.

## Verdict

**It fits, and one defect had to be fixed to make it fit.** Every field the app
depends on is present at 94–100%, and the description — the field the whole
assessment rests on — is full text at a 2,700-character median.

## What the app needs, and what the sample gives

| Dawnlist field | Dataset column | Present |
|---|---|---|
| `title` | `job_title` | 100% |
| `company` | `company_name` | 100% |
| `description_text` | `description` | 99.2% over 200 chars |
| `url` | `url` (ATS-canonical, falls back to `source_url`) | 100% |
| `posted_at` | `date_posted` | 100% |
| `provider_job_id` | `id` | 100% |
| `locations` | `location` / `short_location` / `long_location` | 94.3% |
| delta pulls | `discovered_at` | 100% |

Description length: median 2,700 characters, p10 1,080, p90 5,666, max 20,677.
One row empty, 15 under 200 characters. That is genuinely full text, not a
truncated teaser — which is what makes the assessment worth paying for.

## The defect, now fixed

**`company` is a JSON-ENCODED STRING in the dataset, not an object.** The
adapter was written against the API, where it is a name. In the dataset,
`row["company"]` is truthy, is a `str`, and passes every "did we get a company"
check while being 700 to 2,200 characters of JSON.

Measured: **all 2,000 rows** would have shown that blob as the employer — on
the board, in the review window's company column, in `known_employers`, in
kill-family matching, in the dedup name key, and in the drafting prompt's
"Company:" line. `_company_name()` now prefers the flat `company_name`, then a
parsed object, then a JSON string it decodes.

Nothing else in the mapping needed changing.

## What it costs to run

Through the real pipeline, with a hospitality/asset-management rule table:

- 2,000 swept -> 2,000 unique (**164 near-duplicates flagged**, 8.2%)
- screen: **100 reach the model, 1,900 killed for nothing** — 5% paid for
- ~86,790 input tokens, about **$0.09** on `claude-haiku-4-5`

The screen is doing the work it exists to do. The 164 near-duplicates and 28
contained-match kills also confirm both of those detectors fire on real data,
not just on fixtures.

## Three findings for the fit brief

**Salary: the structured field understates disclosure by 19 points.** Corrected
after testing rather than left as first measured. The structured columns carry a
figure on 12.1% of rows, but a pay figure appears in the description text on
21.4%, and one or the other on **30.9%**. The first pass reported the field's
number as the market's, which was wrong.

The correction was forced by a live test. Ten real postings with an EMPTY salary
field were assessed against a brief carrying a hard GBP 85,000 floor. Only one
was rejected on pay, and it was rejected correctly: the structured field was
empty and the description said "$80,000 – $90,000 per year", which the model
quoted verbatim as rule 3 requires. Six were marked `requirement_checked=false`
and bucketed `possible` under rule 5, which is exactly the intended behaviour —
silence about pay does not hide a posting.

So the rule that matters is already enforced, and the practical instruction is
the opposite of what the field suggests: **never filter on the structured salary
columns.** Two thirds of the disclosure is in the prose, and the assessment
already reads the prose.

**`seniority` is 100% filled** — `c_level`, `staff`, `senior`, `mid_level`,
`junior`. In this sample 96% sit in `mid_level`/`senior`, so a band pre-filter
buys about 4% for free. Modest, but it costs nothing and it is one of the
questions the brief asks.

**`remote` and `hybrid` are unusable.** Zero rows in this sample flagged either
as true; the dictionary puts them at 6.6% and 8.9%. Location filtering has to
come from `location` and the description, not these flags.

## Notes

- The sample carries **34 columns; the dictionary documents 60.** The full
  delivery may differ from the sample — worth confirming before designing
  against a field that is only in the dictionary.
- Format toggles to CSV for Companies but the Jobs file stayed PARQUET, so a
  dataset route needs a Parquet reader (`pyarrow`) that the app does not
  currently ship. That is a real dependency decision, not a detail.
- The "195 countries" figure comes from this product's own page. It has been
  removed from the store listing: the tier is not bought and the licensing
  answer has not landed, so it is a claim about someone else's product.
