"""The guard that keeps the suite out of the machine's own credential store.

A negative check — "the read came back empty" — cannot tell a store that is
switched off from one that merely holds nothing, so it would go on passing if
the guard were deleted. These assert what is OBSERVABLE about the guard: which
backend is installed, and that a save followed by a read comes back empty,
which a working store could not do.
"""
import keyring
from keyring.backends.null import Keyring as NullKeyring

from app.core import api_key, credentials


def test_the_installed_backend_is_the_null_one():
    assert isinstance(keyring.get_keyring(), NullKeyring)


def test_a_save_then_read_comes_back_empty():
    """The positive control: a real store returns what was just saved."""
    try:
        keyring.set_password("dawnlist-guard-probe", "probe", "value")
    except Exception:  # noqa: BLE001 - the null backend refuses to save at all
        pass
    assert keyring.get_password("dawnlist-guard-probe", "probe") is None


def test_the_anthropic_key_lookup_finds_nothing():
    """`api_key.get()` is the call that reached a real key in CI."""
    assert api_key.get() is None


def test_a_test_that_needs_a_working_store_gets_a_fake_one(fake_keyring):
    credentials.write("svc", "acct", "secret")
    assert fake_keyring[("svc", "acct")] == "secret"
    assert credentials.read("svc", "acct") == "secret"
