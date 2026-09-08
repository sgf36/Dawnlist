"""Exercise the SHIPPED path against the LIVE service, with the shipped identity.

    python tools/smoke_live.py DAWN-XXXX-XXXX-XXXX-XXXX

Costs nothing. It calls `/v1/plan`, which is a real HTTPS request made by the
real client class through the real transport — and which fetches no postings,
so it spends no feed credits and can be run before every release without
thinking about it.

WHY THIS EXISTS, AND WHY A UNIT TEST WAS NOT ENOUGH
---------------------------------------------------
On 2026-09-08 the application could not reach its own service at all.
Cloudflare refused urllib's default user-agent with error 1010 — "banned based
on your browser's signature" — so every request from every shipped build would
have failed, on every machine.

Both halves of the verification were structurally incapable of seeing it:

  * every unit test INJECTS the transport, so none of them makes a real
    request and none could meet the block;
  * every manual check used `curl`, whose user-agent is not blocked — so the
    manual verification passed while the actual client could not connect.

A unit test now pins the user-agent, and that catches this exact regression.
It cannot catch the NEXT one, because the failure was never really about a
header: it was about no one ever running the shipped client against the real
service. Only a real request proves reachability.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
It does not search. A search costs credits, and a check nobody runs because it
costs money is a check that does not exist. `/v1/plan` traverses the same
client, the same transport, the same headers, the same TLS and the same edge —
everything that failed on 2026-09-08 — and stops before the meter.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python tools/smoke_live.py <licence-key> [base-url]",
              file=sys.stderr)
        print("\nAny active licence works. It fetches nothing and spends "
              "nothing.", file=sys.stderr)
        return 2

    licence = sys.argv[1].strip()

    # Imported here, after the path is set, and by the SAME names the
    # application uses. Reaching for `requests` or a hand-rolled call would
    # test something that is not what ships, which is the whole failure this
    # script exists to prevent.
    from app.feed.managed import DEFAULT_BASE, USER_AGENT, ManagedProvider

    base = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_BASE

    print(f"client  : ManagedProvider — the class the application uses")
    print(f"identity: {USER_AGENT}")
    print(f"service : {base}")
    print()

    provider = ManagedProvider(licence, base=base)
    try:
        status = provider.plan()
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED — {type(exc).__name__}: {exc}", file=sys.stderr)
        print(file=sys.stderr)
        if "1010" in str(exc):
            # Naming it, because the raw error says nothing about either
            # Dawnlist or Cloudflare and cost an evening to recognise once.
            print("Error 1010 is Cloudflare refusing the client's user-agent. "
                  "Check that app/feed/managed.py still sends one — this is "
                  "the exact failure this script was written for.",
                  file=sys.stderr)
        else:
            print("The shipped client cannot reach the live service. Nothing "
                  "in the test suite will catch this: every test injects the "
                  "transport.", file=sys.stderr)
        return 1

    print(f"  plan            {status.plan or '(none assigned)'}")
    print(f"  tier            {status.tier}")
    print(f"  postings/day    {status.postings_per_day}")
    print(f"  used today      {status.postings_used}")
    print(f"  remaining       {status.postings_remaining}")
    print(f"  ladder          {', '.join(p['key'] for p in status.ladder) or '(empty)'}")
    print()

    # Two things worth asserting rather than merely printing, because both have
    # been wrong in production within the last day.
    problems = []
    if any(p["key"] in ("trial", "owner") for p in status.ladder):
        problems.append("the upgrade ladder is advertising a plan nobody can "
                        "buy — `sellable` is not being honoured")
    if status.postings_per_day <= 0:
        problems.append("this licence has no allowance, so a search would be "
                        "refused before it started")

    if problems:
        for line in problems:
            print(f"PROBLEM — {line}", file=sys.stderr)
        return 1

    print("OK — the shipped client reached the live service and read its plan.")
    print("No postings were fetched and no credits were spent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
