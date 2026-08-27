"""Proves the fake AI is usable offline. This closes assumption A-8.

Five things must be true:

1. it can be created
2. it turns a known sentence into a valid shopping request
3. running it twice gives byte-identical output
4. an unknown question is refused safely — never guessed
5. it makes no network call at all
"""

import json
import socket

import httpx
import pytest

from orderguard.llm import (
    GeminiProvider, GroqProvider, LLMProvider, LLMUnavailable, StubProvider,
    UnsupportedByStub,
)
from orderguard.models import PurchaseIntent


SCHEMA = PurchaseIntent.model_json_schema()


# --- 1. it exists -----------------------------------------------------------

def test_stub_can_be_created():
    stub = StubProvider()
    assert stub.name == "stub"


def test_stub_satisfies_the_provider_interface():
    """Anything used as a provider must offer the same shape."""
    assert isinstance(StubProvider(), LLMProvider)


# --- 2. it produces something we can actually use ---------------------------

def test_known_sentence_becomes_a_valid_shopping_request():
    stub = StubProvider()
    raw = stub.complete(
        system="turn this into a shopping request",
        user="freshcart: two litres of milk and six bananas, budget 500 rupees",
        schema=SCHEMA,
    )

    # The provider returns a plain dict. Validation happens here, at the caller.
    intent = PurchaseIntent(intent_id="i1", user_id="u1", **raw)

    assert intent.merchant == "freshcart"
    assert len(intent.items) == 2
    assert intent.items[0].requested_product == "milk"
    assert intent.items[0].quantity == 2
    assert intent.maximum_total_paise == 50000       # 500 rupees, in paise


def test_matching_ignores_case_and_extra_spaces():
    stub = StubProvider()
    messy = "  FreshCart:   Two Litres Of Milk And Six Bananas,  Budget 500 Rupees  "
    assert stub.complete("s", messy, SCHEMA)["merchant"] == "freshcart"


# --- 3. same input, same output, every time ---------------------------------

def test_repeated_calls_are_byte_identical():
    """If the fake AI wobbled, tests would flake and prove nothing."""
    stub = StubProvider()
    q = "freshcart: two litres of milk and six bananas, budget 500 rupees"

    first = json.dumps(stub.complete("s", q, SCHEMA), sort_keys=True)
    for _ in range(50):
        assert json.dumps(stub.complete("s", q, SCHEMA), sort_keys=True) == first


def test_a_caller_cannot_poison_later_calls():
    """Each call gets its own copy, so mutating one result cannot leak."""
    stub = StubProvider()
    q = "freshcart: add milk"

    first = stub.complete("s", q, SCHEMA)
    first["merchant"] = "somewhere-else"
    first["items"].append({"requested_product": "hacked", "quantity": 99, "unit": "x"})

    second = stub.complete("s", q, SCHEMA)
    assert second["merchant"] == "freshcart"
    assert len(second["items"]) == 1


# --- 4. unknown input is refused, not guessed -------------------------------

def test_unknown_question_is_refused():
    """The stub says 'I don't know'. It never invents an order."""
    with pytest.raises(UnsupportedByStub):
        StubProvider().complete("s", "buy me a helicopter", SCHEMA)


def test_the_refusal_explains_itself():
    with pytest.raises(UnsupportedByStub, match="no answer for"):
        StubProvider().complete("s", "something nobody planned for", SCHEMA)


def test_extra_answers_can_be_added_for_a_test():
    stub = StubProvider(extra_answers={
        "demo: one bread": {
            "merchant": "demo",
            "items": [{"requested_product": "bread", "quantity": 1, "unit": "loaf"}],
            "maximum_total_paise": 10000,
        }
    })
    assert stub.complete("s", "demo: one bread", SCHEMA)["merchant"] == "demo"


# --- 5. no network. at all. -------------------------------------------------

def test_stub_makes_no_network_call(monkeypatch):
    """Break sockets entirely, then use the stub. It must still work.

    This is the test that makes 'runs offline' a fact instead of a claim.
    """
    def no_sockets(*args, **kwargs):
        raise AssertionError("the stub tried to use the network")

    monkeypatch.setattr(socket, "socket", no_sockets)
    monkeypatch.setattr(socket, "create_connection", no_sockets)

    result = StubProvider().complete(
        "s", "freshcart: two litres of milk and six bananas, budget 500 rupees", SCHEMA
    )
    assert result["merchant"] == "freshcart"


def test_stub_works_with_no_api_key(monkeypatch):
    """No key in the environment. Still fine."""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert StubProvider().complete("s", "freshcart: add milk", SCHEMA)["merchant"] == "freshcart"


def test_gemini_provider_uses_structured_output_request_without_a_live_call():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers["x-goog-api-key"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": '{"merchant":"freshcart"}'}]}}]
        })

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GeminiProvider("test-key", "gemini-test", client=client)
    assert provider.complete("system", "user", {"type": "object"}) == {"merchant": "freshcart"}
    assert seen["key"] == "test-key"
    assert seen["body"]["generationConfig"]["responseMimeType"] == "application/json"
    assert seen["body"]["generationConfig"]["responseJsonSchema"] == {"type": "object"}


def test_gemini_provider_fails_safely_for_bad_json():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": "not-json"}]}}]
        })

    provider = GeminiProvider("test-key", "gemini-test", client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(LLMUnavailable):
        provider.complete("system", "user", {"type": "object"})


def test_groq_provider_uses_openai_compatible_json_request_without_a_live_call():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"merchant":"freshcart"}'}}]
        })

    provider = GroqProvider(
        "test-key", "openai/gpt-oss-120b",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert provider.complete("system", "user", {"type": "object"}) == {"merchant": "freshcart"}
    assert seen["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert seen["auth"] == "Bearer test-key"
    assert seen["body"]["response_format"] == {"type": "json_object"}
