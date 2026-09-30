# Diagnostics — a record of what the app did, for review

Off by default. **The same build either way**: a separate "logging build" would
differ from the shipped one in the ways that matter (package identity, edition
flag, signing), so what it showed would prove little about what customers run.

## Switching it on

Any one of:

| Route | When to use it |
|---|---|
| `--diagnostics` | source runs and command-line launches |
| `DAWNLIST_DIAGNOSTICS=1` | scripts, CI |
| an empty file named `diagnostics.on` in the app-data folder | **a packaged (MSIX) launch**, where you cannot add arguments |

The app-data folder is `%LOCALAPPDATA%\Spencer Fields Software\Dawnlist\`
(the same folder as `dawnlist.sqlite3`). `python -m app.main --doctor` prints
`diagnostics : ON` or `off`.

## Where it goes

`<app data>/diagnostics/dawnlist-YYYY-MM-DD.jsonl` — one JSON object per line,
one file per UTC day, pruned after 7 days, capped at 20 MB a file (a
`log.truncated` line says so). **Nothing is ever sent anywhere.**

## What is recorded

| Event | Carries |
|---|---|
| `app.start` | version, Python, platform, frozen or source, arguments |
| `run.start` / `run.end` | provider, searches, per-stage seconds, the whole funnel, fetch errors |
| `fetch.search` | per search: matched, kept, **scanned**, pages, exhausted, capped, refusal, error, **degraded** (a provider that could not be searched, with its code, e.g. `token_expired`) |
| `pipeline.fetch` … `pipeline.assess` | duration and what each stage produced (gate reasons, screen counts, unread) |
| `http` | every `urllib` request: method, URL (secret query values removed), status, milliseconds; for a 4xx/5xx **the API's own error body** |
| `model.call` | model, stop reason, token counts |
| `run.report` / `run.refused` | what the window was told, and why a run never started |
| `ui.click` / `ui.show` / `ui.tab` | button presses and windows opening — never anything typed |
| `exception.uncaught` / `exception.thread` | type, message, last frames |

## What is never recorded

Redaction happens inside `diagnostics.event()`, the **only** writer, so no call
site can forget it (`tests/test_diagnostics.py` fails the build if another
module writes the log or configures file logging).

- any value whose **key** names a credential (token, key, secret, password,
  licence, authorization, cookie …);
- any value that **looks** like one (Bearer values, `sk-ant-` keys, 32+ hex,
  long unbroken key-like runs);
- email addresses;
- free text (`description`, `body`, `cv`, `letter`, `brief`, `prompt` …) — a
  length and an 8-character hash only. Job descriptions are third-party data
  held under the feed's terms, and a CV is the user's own.

The Anthropic SDK does not go through `urllib`; its calls are recorded as
`model.call` (usage only), and the request and reply bodies are not logged.

## Reading a run

```powershell
Get-Content "$env:LOCALAPPDATA\Spencer Fields Software\Dawnlist\diagnostics\dawnlist-*.jsonl" |
  ConvertFrom-Json | Where-Object event -like 'pipeline.*' |
  Format-Table ts, event, ms, ok
```

The first thing to look at for "it finished very fast" is `fetch.search`:
`matched` is what the feed reported, `scanned` is what this app read before its
own filters, `kept` is what survived.
