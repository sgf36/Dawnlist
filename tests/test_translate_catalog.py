"""The translation tool's failure reporting.

The tool spends money, so nothing here calls the API: the client is a stub that
returns whatever response the test is about. What is under test is what the tool
DOES with a response, which is where it went wrong — a catalogue truncated at
the output budget was reported as "unparseable response", so the fix looked like
a model problem rather than a number the tool sets.
"""
import importlib.util
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "translate_catalog.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("translate_catalog", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tc = load_tool()

ENGLISH = {"a.greeting": "Good morning", "a.count": "{count} jobs"}


class Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class Response:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [Block(text)]
        self.stop_reason = stop_reason


class Client:
    """Returns one canned response, and remembers what it was asked for.

    Shaped as a streaming client because that is what the tool uses: the SDK
    refuses a non-streaming request with a budget this large.
    """

    def __init__(self, response):
        self._response = response
        self.kwargs = None
        self.messages = self

    def stream(self, **kwargs):
        self.kwargs = kwargs
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._response


def test_a_truncated_response_names_the_budget_not_the_model():
    """The JSON is cut off mid-object because thinking spent the budget first.
    Told it is 'unparseable', the operator retries and gets the same result;
    told the budget ran out, they raise it once and move on."""
    client = Client(Response('{"a.greeting": "Bonj', stop_reason="max_tokens"))
    catalogue, problem = tc.translate(client, "French", "fr", ENGLISH)
    assert catalogue is None
    assert "MAX_TOKENS" in problem and "truncated" in problem


def test_unparseable_for_any_other_reason_is_not_blamed_on_the_budget():
    """A positive control for the test above: the same broken JSON, stopped
    normally, must NOT tell the operator to raise a budget that was not the
    problem."""
    client = Client(Response("here you go: {...}", stop_reason="end_turn"))
    catalogue, problem = tc.translate(client, "French", "fr", ENGLISH)
    assert catalogue is None
    assert "MAX_TOKENS" not in problem
    assert "unparseable" in problem


def test_a_dropped_placeholder_is_refused_before_it_reaches_disk():
    """`{count}` missing renders as a KeyError in front of the user, in a
    language the developer cannot read."""
    client = Client(Response(
        '{"a.greeting": "Bonjour", "a.count": "des offres"}'))
    catalogue, problem = tc.translate(client, "French", "fr", ENGLISH)
    assert catalogue is None
    assert "a.count" in problem and "count" in problem


def test_a_good_response_comes_back_with_no_problem():
    client = Client(Response(
        '{"a.greeting": "Bonjour", "a.count": "{count} offres"}'))
    catalogue, problem = tc.translate(client, "French", "fr", ENGLISH)
    assert problem == ""
    assert catalogue == {"a.greeting": "Bonjour", "a.count": "{count} offres"}


def test_the_request_asks_for_a_budget_big_enough_to_think_in():
    """The budget covers thinking AND the JSON. 16000 was not enough for two of
    the 49 locales, and the shortfall was silent."""
    client = Client(Response('{"a.greeting": "Bonjour", "a.count": "{count}"}'))
    tc.translate(client, "French", "fr", ENGLISH)
    assert client.kwargs["max_tokens"] >= 32000


@pytest.mark.parametrize("attr", ["fill", "main", "verify", "context_for"])
def test_the_tool_still_exposes_what_the_pipeline_calls(attr):
    assert callable(getattr(tc, attr))
