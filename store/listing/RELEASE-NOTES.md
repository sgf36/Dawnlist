# ReleaseNotes — en-GB source, for 1.4.0

Release notes are read by people deciding whether to update, and by the
reviewer deciding whether to pass. Everything here is user-facing; nothing
names an internal limit, a build variant change or a server-side detail.

British English, no Oxford commas, no abbreviations.

---

Searches now spread your credits evenly across all your queries, so one
broad search no longer consumes the entire budget.

Seniority filtering now works as configured — postings below your target
level are flagged correctly instead of being let through unchecked.

Tailored CVs and covering letters are now verified against your evidence
before they are saved, so unsupported claims are caught and marked rather
than going out unnoticed.

---

## What this deliberately does not say

- **No mention of the verification mechanism.** The buyer sees that claims
  are caught; how is an implementation detail.
- **No mention of the seniority band table.** The buyer configured a
  seniority level in setup; this release makes it work.
- **No mention of the budget balancer algorithm.** The buyer sees fair
  credit distribution; multi-pass redistribution is internal.
