# Stage 2 — the fit brief becomes a structure, not a paragraph

## What is already right

Three pieces of the setup do what they should, and none of this changes them:

- **The draft-and-argue interview.** The user corrects a draft rather than
  composing from a blank page. `interview.py` says why, and it is correct.
- **The calibration gate.** Ten live postings, the app's verdicts, and the
  question *"what sentence, added here, would have got this right?"* That is
  the transfer-of-judgement step, and it is the single most valuable thing in
  the product.
- **The search plan.** Titles only, countries as ISO codes, location never
  inferred from an employer's headquarters or a language spoken. This is what
  stopped London-only searches returning jobs elsewhere.

## What is wrong

`build_brief_request()` is the only model call in onboarding with **no
`output_config`**. The fit brief comes back as prose, and everything downstream
inherits that:

1. **Nothing can be enforced.** "Cap VP and SVP at Possible" is a sentence the
   assessor may honour on Tuesday and not on Thursday. There is no chokepoint
   that applies it, so it is a remembered rule, which is not a fix.
2. **Corrections accumulate rather than reconcile.** Every calibration
   disagreement appends a sentence. After eight weeks the brief contains
   sentences written against different assumptions, and nothing detects that
   two of them now contradict. The reference criteria this is modelled on went
   through **five hand-written revisions** for exactly this reason — a person
   sat down and reconciled the prose. The app has no such person.
3. **The hard constraints exist twice.** Location, contract type and ruled-out
   employers are in the prose brief *and* in the search plan, extracted
   separately. Two copies of one fact drift, and the drift is silent: the
   search fetches London roles while the brief screens on something else.

## What the brief has to become

A structured object, drafted by the model and revised by the interview. The
shape below is taken from a fit-criteria document that reached this level of
detail over five revisions — it is what "accurate, not flooded" looks like when
written down.

| Field | Holds | Why it cannot stay prose |
|---|---|---|
| `evidence_band` | The seniority range the CV actually evidences, stated as a range | The user's ambition and their evidence disagree; the disagreement must be visible and theirs to settle |
| `seniority_bands` | One row per band (Analyst … C-suite), each with a **verdict ceiling** | A ceiling is arithmetic, not taste. Two bands above the evidence can be worth seeing and must never read as Strong |
| `function_tiers` | `first_class` (can reach Strong) · `adjacent` (ceiling: possible) · `unsupported` (always rejected) | This is the tier system that does most of the screening work, and a ceiling only means something if code applies it |
| `sector_legs` | One to three legs, each a sector definition plus the functions that count inside it | A posting needs to land on only ONE leg. Without legs, "sector fit" is one undifferentiated judgement |
| `geography` | The places the **role must be based**, and an explicit note that the employer's headquarters is irrelevant | The recurring error is inferring location from where someone used to work, an employer's HQ, or a language they speak |
| `contract` | Permanent/contract acceptance, and whether fixed-term is a negative or neutral | Stated once, applied by both the search and the screen |
| `do_not_contact` | Employers, each with a reason | A first-class field. It is not a screen-out of a *kind* of role; it is a named employer, and the reason is often personal |
| `affinity_signals` | Tie-breakers that upgrade a borderline Possible to Strong | These never create a fit alone; encoding that is the point |
| `screen_outs` | Functions ruled out regardless of seniority or employer | The section people skip, and the one that does most of the work |
| `open_questions` | What the model could not settle from the evidence | The interview's back-and-forth engine — see below |
| `calibration_log` | Postings re-scored, the user's verdict, and their words | The audit trail that lets a later revision see *why* a rule exists |

## The two chokepoints

Structure without enforcement is prose with brackets. Two places in code, each
with a test:

**1. The verdict ceiling, applied after the model answers.** The assessor
returns a verdict; a single function then lowers it to the lowest ceiling that
applies — band ceiling, function-tier ceiling, leg ceiling. The model is never
asked to remember a cap. A test walks every band and tier and asserts that a
`strong` from the model comes back as `possible` wherever a ceiling says so,
with a positive control proving an uncapped combination stays `strong`.

**2. The hard constraints, derived once.** The search plan is generated **from
the structured brief**, not from the prose and the aim box separately. Location,
contract types and excluded employers have exactly one source. A test asserts
that a brief naming London produces a search whose countries are `["GB"]` and
whose cities are `["London"]`, and that an employer in `do_not_contact` appears
in `exclude_companies`.

## The interview loop

The user's words: *"the interview should be back-and-forth in the sense the AI
makes recommendations based on its understanding. Not just constant
start-from-scratch responses from the user."*

Each round:

1. The model drafts or revises the structured brief from the CV corpus and
   everything already settled.
2. It populates `open_questions` with **only** what it could not settle — each
   one stated as a recommendation with its reasoning and its alternative, not
   as an open prompt. *"Your CV evidences Senior Associate to Senior Manager.
   Director and above is a genuine stretch, so I would cap it at Possible —
   visible, but never presented as a strong match. The alternative is to keep
   aiming high and let the market decide."*
3. The user answers those questions and nothing else.
4. Their answer is recorded **in their own words** next to the field it
   settled, so a later revision can see what the rule rests on.

A round that produces no open questions ends the interview. A round that
produces more than about six splits over two rounds: a screen of questions is a
form, and people fill forms badly.

## What it costs

Nothing extra from the feed. Every fetch is unchanged: this changes what the
brief *is*, not how many postings are bought. The model calls grow by roughly
one revision round, on the user's own key, against a saving of the postings a
vague brief surfaces and the user then rejects.

## What to build first

In order, each shippable on its own:

1. `FIT_BRIEF_SCHEMA` and the structured draft, with the prose brief kept as a
   rendered view of it. Nothing downstream changes yet.
2. The ceiling chokepoint, with its test. This alone fixes the "flooded with
   poor matches" complaint, because two-bands-above roles stop reading as
   Strong.
3. The search plan derived from the structured brief, removing the second copy
   of the hard constraints.
4. The revision round, replacing the single-pass interview.
5. Calibration writing into `calibration_log` and amending fields, rather than
   appending a sentence.
