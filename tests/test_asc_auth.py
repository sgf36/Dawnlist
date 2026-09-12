"""How the App Store Connect token is built.

None of this calls Apple. What is under test is the two token SHAPES and where
the key id comes from — the two things that, got wrong, produce an identical
401 saying the token is not properly configured. That message sends you looking
at the key when the fault is in how it was signed.

Measured against a real individual key on 2026-09-12: the team shape returned
401 and the individual shape returned 200 with exactly one app.
"""
import importlib.util
import pathlib

import pytest

ASC = pathlib.Path(__file__).resolve().parents[1] / "tools" / "asc.py"


def load():
    spec = importlib.util.spec_from_file_location("asc", ASC)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


asc = load()


# -- the two token shapes ---------------------------------------------------
def test_an_individual_key_is_signed_with_sub_and_no_issuer():
    """An individual key inherits its user's app restriction, which is the
    whole reason for using one. Apple rejects it if `iss` is present."""
    payload = asc.claims(None, 1_700_000_000)
    assert payload["sub"] == "user"
    assert "iss" not in payload


def test_a_team_key_is_signed_with_the_issuer_and_no_sub():
    """Positive control for the test above: the other shape really is other.
    A test that only checked the individual shape would pass if `claims`
    ignored its argument entirely."""
    payload = asc.claims("65aee88f-46c4-4daf-8238-5dc37263d06b", 1_700_000_000)
    assert payload["iss"] == "65aee88f-46c4-4daf-8238-5dc37263d06b"
    assert "sub" not in payload


def test_an_empty_issuer_is_not_a_team_key(monkeypatch):
    """`ASC_ISSUER_ID=` in a shell profile sets it to the empty string, which
    is a configuration that looks present and behaves absent."""
    monkeypatch.setenv(asc.ISSUER_ENV, "")
    assert asc.issuer() is None


def test_the_clock_is_backdated_and_the_token_expires():
    now = 1_700_000_000
    payload = asc.claims(None, now)
    assert payload["iat"] < now, "an iat in Apple's future is a 401"
    assert payload["exp"] > now
    assert payload["exp"] - payload["iat"] <= 20 * 60, "Apple's ceiling"
    assert payload["aud"] == "appstoreconnect-v1"


# -- where the key id comes from --------------------------------------------
@pytest.mark.parametrize("name,expected", [
    ("AuthKey_4CU796U485.p8", "4CU796U485"),   # team key, Apple's naming
    ("ApiKey_6DI5OSWOPW6L.p8", "6DI5OSWOPW6L"),  # individual key
])
def test_the_key_id_is_read_off_the_filename(name, expected, tmp_path,
                                             monkeypatch):
    """Hard-coding it meant pointing ASC_KEY_PATH at a new key still sent the
    OLD key's id, and Apple blamed the new key for the old one's name."""
    monkeypatch.delenv(asc.KEY_ID_ENV, raising=False)
    assert asc.key_id(tmp_path / name) == expected


def test_an_override_wins_over_the_filename(tmp_path, monkeypatch):
    monkeypatch.setenv(asc.KEY_ID_ENV, "OVERRIDDEN")
    assert asc.key_id(tmp_path / "AuthKey_4CU796U485.p8") == "OVERRIDDEN"


def test_a_renamed_key_is_refused_by_name_not_guessed(tmp_path, monkeypatch):
    """Guessing would produce a 401 from Apple. Refusing says which file and
    which variable to set."""
    monkeypatch.delenv(asc.KEY_ID_ENV, raising=False)
    with pytest.raises(SystemExit) as exc:
        asc.key_id(tmp_path / "key.p8")
    assert "key.p8" in str(exc.value) and asc.KEY_ID_ENV in str(exc.value)


def test_a_missing_key_file_is_named(tmp_path, monkeypatch):
    """A missing key and a wrong key both come back from Apple as the same
    401, so the local check has to be the one that tells them apart."""
    monkeypatch.setenv(asc.KEY_ENV, str(tmp_path / "nope.p8"))
    with pytest.raises(SystemExit) as exc:
        asc.key_path()
    assert "nope.p8" in str(exc.value)


def test_no_credential_is_hard_coded_in_the_module():
    """The repository is PUBLIC. A key id or an issuer id checked in here is
    not a secret, but it is a default nobody chose and it is how the wrong key
    keeps being used after a rotation."""
    source = ASC.read_text(encoding="utf-8")
    body = source.split('"""', 2)[-1]  # the module docstring may cite them
    assert "4CU796U485" not in body
    assert "65aee88f-46c4-4daf-8238-5dc37263d06b" not in body
