"""Talking to an AI model — and not needing one.

Two implementations behind one interface:

``StubProvider``   fake, offline, always gives the same answer. Used by every test.
``GroqProvider``   the real thing. Added at CP-3, not needed yet.

The rule this file exists to enforce: **the test suite must pass with no API key
and no internet.** A test that needs the network is a test that fails when the
free tier runs out, or when the wifi drops during a demo.

The other rule: if the model gives an answer we cannot use, that becomes a
question for the user. It never becomes a payment.
"""

from typing import Any, Protocol, runtime_checkable

__all__ = ["LLMProvider", "StubProvider", "LLMUnavailable", "UnsupportedByStub"]


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
