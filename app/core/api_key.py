"""The user's own Anthropic API key.

**Dawnlist is bring-your-own-key.** The person using it supplies an Anthropic
key, and their reading and drafting bills to their account, not to Spencer's.

That is a pricing decision with a technical consequence, and the consequence is
the point: inference is the cost that scales with how hard someone uses the
app, and it belongs with the person incurring it. A single up-front purchase
cannot fund an open-ended recurring cost, and pretending otherwise makes the
heaviest users the least profitable — exactly backwards.

What Spencer still pays for is the FEED, which is metered per licence in the
Worker and capped there. That is affordable because a job search is finite: the
handoff models a 2–4 month working life per user, and the fair-use caps bound
the tail.

WHY THE KEY IS NOT OPTIONAL, AND WHY THAT IS SAID OUT LOUD
----------------------------------------------------------
A paid app that silently needs a second, separately-paid credential to do
anything is a refund. So the key is:
  * asked for during onboarding, before the first run;
  * VERIFIED when entered, not on first use, so a typo fails where the user can
    still see the box they typed it into;
  * disclosed in the store listing, in the description rather than the
    small print.
"""
from __future__ import annotations

SERVICE = "dawnlist-anthropic"
ACCOUNT = "api-key"

#: What a key looks like. A shape check only — the live check below is what
#: actually decides. This exists to catch the two common paste errors: a
#: truncated copy, and copying a key's NAME from a console instead of its value.
PREFIX = "sk-ant-"


class KeyProblem(RuntimeError):
    """The key is missing, malformed, or was refused."""


def get() -> str | None:
    try:
        import keyring
        return keyring.get_password(SERVICE, ACCOUNT)
    except Exception:  # noqa: BLE001 - a broken keyring is not a missing key
        return None


def store(key: str) -> None:
    import keyring
    keyring.set_password(SERVICE, ACCOUNT, key.strip())


def forget() -> None:
    try:
        import keyring
        keyring.delete_password(SERVICE, ACCOUNT)
    except Exception:  # noqa: BLE001
        pass


def looks_plausible(key: str) -> bool:
    return bool(key) and key.strip().startswith(PREFIX) and len(key.strip()) > 20


def verify(key: str, *, client_factory=None) -> tuple[bool, str]:
    """Check the key against the API. Returns (ok, message).

    `models.list` is a cheap GET and costs nothing, so a user can confirm their
    key without being billed for the privilege.
    """
    key = (key or "").strip()
    if not key:
        return False, "No key entered."
    if not looks_plausible(key):
        return False, (
            f"That does not look like an Anthropic key — they begin {PREFIX!r}. "
            "If you copied a key's name rather than the key itself, re-copy it.")

    try:
        if client_factory is None:
            import anthropic
            client_factory = anthropic.Anthropic
        client = client_factory(api_key=key)
        models = [m.id for m in client.models.list(limit=3)]
    except Exception as exc:  # noqa: BLE001
        name = type(exc).__name__
        if "Authentication" in name or "401" in str(exc):
            return False, "Anthropic rejected that key."
        return False, f"Could not reach Anthropic to check the key ({name})."

    return True, f"Verified — {len(models)} models available."


def require() -> str:
    """The key, or a refusal that says what to do.

    Raised at the door into a run, so the failure is never a confusing error
    from inside the assessment.
    """
    key = get()
    if not key:
        raise KeyProblem(
            "Dawnlist needs your own Anthropic API key to read postings and "
            "draft messages. Add one in Settings — you can create a key at "
            "console.anthropic.com, and you are billed by Anthropic directly "
            "for what Dawnlist reads.")
    return key


#: Rough monthly cost at the operating spec's target volume, so the app can be
#: honest about what a user is signing up to rather than leaving them to find
#: out from a bill. Figures from handoff Part 3.3, verified 2026-09-06.
COST_GUIDANCE = (
    "At about 40 postings a day, reading costs roughly £2 a month on Anthropic's "
    "cheapest model, and drafting a few follow-ups a day adds about £1. Setting "
    "up your factsheet is a one-off £2–4. You pay Anthropic directly; Dawnlist "
    "takes no cut and adds no markup."
)
