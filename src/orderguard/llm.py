"""Talking to an AI model — and not needing one.

Two implementations behind one interface:

``StubProvider``   fake, offline, always gives the same answer. Used by every test.
``GeminiProvider`` and ``GroqProvider`` are real providers. Tests use an
injected transport and never call either service.

The rule this file exists to enforce: **the test suite must pass with no API key
and no internet.** A test that needs the network is a test that fails when the
free tier runs out, or when the wifi drops during a demo.

The other rule: if the model gives an answer we cannot use, that becomes a
question for the user. It never becomes a payment.
"""

import json
import os
from typing import Any, Protocol, runtime_checkable

import httpx

__all__ = [
    "LLMProvider", "StubProvider", "GeminiProvider", "GroqProvider", "provider_from_env",
    "LLMUnavailable", "UnsupportedByStub",
]


class LLMUnavailable(RuntimeError):
    """The model could not be reached, timed out, or returned unusable output.

    Callers must turn this into a clarification or an escalation.
    Never into a payment.
    """


class UnsupportedByStub(LLMUnavailable):
    """The stub was asked something it has no canned answer for.

    Deliberately a hard failure. A stub that guesses would let a test pass for
    the wrong reason, which is worse than no test at all.
    """


@runtime_checkable
class LLMProvider(Protocol):
    """What every provider must offer.

    ``complete`` returns a plain dict. The caller validates it against a strict
    Pydantic model before using it — validation lives with the caller, not here,
    so no provider can weaken it.
    """

    name: str

    def complete(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        ...


class StubProvider:
    """A fake model with a small lookup table.

    Same input always gives the exact same output, so tests never flake.
    Matching ignores case and surrounding spaces, and nothing else.
    """

    name = "stub"

    # Deliberately tiny. Grows only when a test needs a new case.
    _ANSWERS: dict[str, dict[str, Any]] = {
        "freshcart: two litres of milk and six bananas, budget 500 rupees": {
            "merchant": "freshcart",
            "items": [
                {"requested_product": "milk", "quantity": 2, "unit": "litre"},
                {"requested_product": "banana", "quantity": 6, "unit": "piece"},
            ],
            "maximum_total_paise": 50000,
        },
        "freshcart: add milk": {
            "merchant": "freshcart",
            "items": [{"requested_product": "milk", "quantity": 1, "unit": "unit"}],
            "maximum_total_paise": 0,
        },
        "freshcart: two litres of milk": {
            "merchant": "freshcart",
            "items": [{"requested_product": "milk", "quantity": 2, "unit": "litre"}],
            "maximum_total_paise": 0,
        },
    }

    def __init__(self, extra_answers: dict[str, dict[str, Any]] | None = None) -> None:
        self._answers = dict(self._ANSWERS)
        if extra_answers:
            self._answers.update({self._key(k): v for k, v in extra_answers.items()})

    @staticmethod
    def _key(text: str) -> str:
        return " ".join(text.lower().split())

    def complete(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Look the question up. Refuse loudly if it is not in the table.

        Raises:
            UnsupportedByStub: no canned answer. The caller must ask the user
                instead of proceeding.
        """
        answer = self._answers.get(self._key(user))
        if answer is None:
            raise UnsupportedByStub(
                f"stub has no answer for {user!r}. "
                "Add one to StubProvider._ANSWERS, or use a real provider."
            )
        # Return a deep copy so a caller mutating the result cannot poison
        # the next call and make tests depend on execution order.
        return _deep_copy(answer)


def _deep_copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _deep_copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_copy(v) for v in value]
    return value


class GeminiProvider:
    """Small REST provider for Gemini structured output.

    Validation still belongs to the intent compiler. This class guarantees only
    that the provider response is JSON-shaped and converts transport/provider
    failures into ``LLMUnavailable``.
    """

    name = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Gemini API key is required")
        if not model:
            raise ValueError("Gemini model is required")
        self._api_key = api_key
        self._model = model
        self._client = client or httpx.Client(timeout=httpx.Timeout(20.0, connect=8.0))
        self._owned_client = client is None

    def close(self) -> None:
        if self._owned_client:
            self._client.close()

    def complete(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": schema,
                "temperature": 0,
            },
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self._model}:generateContent"
        try:
            response = self._client.post(
                url,
                headers={"x-goog-api-key": self._api_key},
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
            parts = body["candidates"][0]["content"]["parts"]
            text = "".join(part.get("text", "") for part in parts)
            result = json.loads(text)
        except httpx.HTTPStatusError as exc:
            # The generic LLMUnavailable message never reaches the user (see
            # intent_compiler.py), but the response body is the only way to
            # tell an invalid API key apart from a malformed request server-side.
            raise LLMUnavailable(
                f"Gemini HTTP {exc.response.status_code}: {exc.response.text[:500]}"
            ) from exc
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMUnavailable("Gemini returned no usable structured response") from exc
        if not isinstance(result, dict):
            raise LLMUnavailable("Gemini structured response was not an object")
        return result


class GroqProvider:
    """OpenAI-compatible Groq chat-completions provider.

    Groq is selected explicitly through ``LLM_PROVIDER=groq``. The model gets
    a JSON object request, while the caller still performs the strict Pydantic
    validation. The model can suggest fields; it cannot create a payment.
    """

    name = "groq"

    def __init__(
        self,
        api_key: str,
        model: str,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Groq API key is required")
        if not model:
            raise ValueError("Groq model is required")
        self._api_key = api_key
        self._model = model
        self._client = client or httpx.Client(timeout=httpx.Timeout(20.0, connect=8.0))
        self._owned_client = client is None

    def close(self) -> None:
        if self._owned_client:
            self._client.close()

    def complete(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        schema_text = json.dumps(schema, separators=(",", ":"))
        payload = {
            "model": self._model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"{system} Return only valid JSON matching this schema: "
                        f"{schema_text}"
                    ),
                },
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }
        try:
            response = self._client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
            text = body["choices"][0]["message"]["content"]
            result = json.loads(text)
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMUnavailable("Groq returned no usable structured response") from exc
        if not isinstance(result, dict):
            raise LLMUnavailable("Groq structured response was not an object")
        return result


def provider_from_env() -> LLMProvider:
    """Select a provider explicitly; tests and offline runs use the stub."""
    provider = os.getenv("LLM_PROVIDER", "stub").strip().lower()
    if provider == "stub":
        return StubProvider()
    if provider == "gemini":
        return GeminiProvider(
            api_key=os.getenv("LLM_API_KEY", ""),
            model=os.getenv("LLM_MODEL", ""),
        )
    if provider == "groq":
        return GroqProvider(
            api_key=os.getenv("LLM_API_KEY", ""),
            model=os.getenv("LLM_MODEL", ""),
        )
    raise ValueError(f"unsupported LLM_PROVIDER: {provider!r}")
