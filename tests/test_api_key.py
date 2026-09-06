"""Bring-your-own-key. The user's Anthropic key, and what happens without one."""
import pytest

from app.core import api_key


class FakeModels:
    def __init__(self, ids): self._ids = ids
    def list(self, limit=3): return [type("M", (), {"id": i})() for i in self._ids]


class FakeClient:
    def __init__(self, api_key=None, **kw):
        self.api_key = api_key
        self.models = FakeModels(["claude-opus-5", "claude-sonnet-5"])


def rejecting(**kw):
    class C:
        class models:
            @staticmethod
            def list(limit=3):
                raise RuntimeError("AuthenticationError: 401 invalid x-api-key")
    return C()


def unreachable(**kw):
    class C:
        class models:
            @staticmethod
            def list(limit=3):
                raise ConnectionError("getaddrinfo failed")
    return C()


# -- shape checks -----------------------------------------------------------
def test_a_real_key_shape_is_accepted():
    assert api_key.looks_plausible("sk-ant-api03-" + "x" * 40)


def test_an_obviously_wrong_paste_is_caught_before_the_network():
    # The two common paste errors: a key's NAME, and a truncated copy.
    assert not api_key.looks_plausible("ANTHROPIC_API_KEY")
    assert not api_key.looks_plausible("sk-ant-")
    assert not api_key.looks_plausible("")


def test_the_shape_message_says_what_to_do():
    ok, msg = api_key.verify("ANTHROPIC_API_KEY")
    assert not ok
    assert "sk-ant-" in msg and "re-copy" in msg


# -- live verification ------------------------------------------------------
def test_a_good_key_verifies():
    ok, msg = api_key.verify("sk-ant-api03-" + "x" * 40,
                             client_factory=FakeClient)
    assert ok and "Verified" in msg


def test_verification_uses_the_key_it_was_given():
    seen = {}

    def factory(api_key=None, **kw):
        seen["key"] = api_key
        return FakeClient(api_key=api_key)

    api_key.verify("sk-ant-api03-" + "y" * 40, client_factory=factory)
    assert seen["key"].startswith("sk-ant-api03-")


def test_a_rejected_key_says_so_plainly():
    ok, msg = api_key.verify("sk-ant-api03-" + "x" * 40, client_factory=rejecting)
    assert not ok
    assert "rejected" in msg.lower()


def test_unreachable_is_distinguished_from_rejected():
    """"Anthropic said no" and "I could not ask" need different responses."""
    _, rejected = api_key.verify("sk-ant-api03-" + "x" * 40, client_factory=rejecting)
    _, offline = api_key.verify("sk-ant-api03-" + "x" * 40, client_factory=unreachable)
    assert "rejected" in rejected.lower()
    assert "could not reach" in offline.lower()


def test_an_empty_key_is_refused_without_a_call():
    def explode(**kw):
        raise AssertionError("must not call the API for an empty key")
    ok, _ = api_key.verify("", client_factory=explode)
    assert not ok


# -- the requirement --------------------------------------------------------
def test_require_raises_with_guidance_when_absent(monkeypatch):
    monkeypatch.setattr(api_key, "get", lambda: None)
    with pytest.raises(api_key.KeyProblem) as exc:
        api_key.require()
    message = str(exc.value)
    assert "console.anthropic.com" in message
    assert "billed by Anthropic directly" in message


def test_require_returns_the_key_when_present(monkeypatch):
    monkeypatch.setattr(api_key, "get", lambda: "sk-ant-stored")
    assert api_key.require() == "sk-ant-stored"


# -- no silent fallback -----------------------------------------------------
def test_the_run_never_falls_back_to_ambient_credentials():
    """A fallback to ANTHROPIC_API_KEY would bill whoever's key is in the
    environment — on a developer machine, Spencer's."""
    import ast
    import inspect

    from app import main

    source = inspect.getsource(main.build_send)
    # Check the CODE, not the prose: the docstring names ANTHROPIC_API_KEY
    # precisely to explain why it is not used.
    tree = ast.parse(source.lstrip())
    fn = tree.body[0]
    if (fn.body and isinstance(fn.body[0], ast.Expr)
            and isinstance(fn.body[0].value, ast.Constant)):
        fn.body = fn.body[1:]
    code = ast.unparse(ast.Module(body=fn.body, type_ignores=[]))

    assert "api_key.require()" in code
    assert "ANTHROPIC_API_KEY" not in code, "no ambient-credential fallback"
    assert "getenv" not in code and "environ" not in code
    assert "Anthropic()" not in code, "no zero-arg client: it reads the env"


def test_there_is_no_managed_route_to_fall_back_to():
    """BYO ONLY. A managed route would make Spencer a processor of every
    buyer's employment record; a FALLBACK to one is how data starts crossing
    infrastructure nobody decided it should cross."""
    import pathlib

    app_dir = pathlib.Path(__file__).resolve().parents[1] / "app"
    assert not (app_dir / "core" / "transport.py").exists()

    offenders = []
    for path in app_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "/v1/messages" in text or "workers.dev/v1/messages" in text:
            offenders.append(path.name)
    assert offenders == [], f"a managed inference path survives in {offenders}"


# -- honesty about cost -----------------------------------------------------
def test_the_cost_guidance_is_specific_and_says_who_bills():
    text = api_key.COST_GUIDANCE
    assert "£" in text
    assert "pay Anthropic directly" in text
    assert "no markup" in text
