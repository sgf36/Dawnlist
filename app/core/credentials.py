"""The operating system's credential store, with its failure made visible.

Every caller used to wrap `keyring` in `except Exception: return None`, so a
credential store that was locked, broken or absent looked exactly like one
holding no key. Two different facts, and they need opposite responses:

  * NO KEY is the user's to fix — paste one in.
  * NO STORE is the machine's — and a licence check that cannot read the key
    has not been refused. Reporting it as "No licence key found" tells a
    paying customer they never bought anything, and skips the grace period
    that exists for exactly this.

Saving was worse: a raw keyring exception escaped into a button handler,
AFTER the key had been verified and a single-use code had been spent.

So reads and writes here RAISE `KeyringUnavailable`, and callers that can
tolerate absence say so explicitly rather than by accident.
"""
from __future__ import annotations


class KeyringUnavailable(RuntimeError):
    """The credential store could not be used at all — not "no such entry"."""


def read(service: str, account: str) -> str | None:
    """The stored secret, None when there is none, or raise when the store fails."""
    try:
        import keyring
        return keyring.get_password(service, account)
    except Exception as exc:  # noqa: BLE001 - every backend fails differently
        raise KeyringUnavailable(str(exc) or type(exc).__name__) from exc


def write(service: str, account: str, value: str) -> None:
    try:
        import keyring
        keyring.set_password(service, account, value)
    except Exception as exc:  # noqa: BLE001
        raise KeyringUnavailable(str(exc) or type(exc).__name__) from exc
