"""Money handling.

One rule: money is stored and compared as whole paise (integers).

Why: floats round badly. ``0.1 + 0.2`` is not ``0.3`` in binary floating point,
and a payments system that compares amounts with ``==`` cannot afford that.
Integers compare exactly, always.

Rupees only appear when showing something to a human. Everything internal is paise.
"""

from decimal import Decimal, InvalidOperation

__all__ = ["to_paise", "to_rupees", "format_rupees", "MoneyError"]


class MoneyError(ValueError):
    """Raised when a value cannot be turned into paise without losing precision."""


def to_paise(amount: Decimal | int | str) -> int:
    """Convert rupees to whole paise.

    Accepts Decimal, int, or a string like ``"24.99"``.
    **Rejects float** — see the class docstring for why.

        >>> to_paise(Decimal("24.99"))
        2499
        >>> to_paise("500")
        50000
        >>> to_paise(24.99)          # doctest: +IGNORE_EXCEPTION_DETAIL
        Traceback (most recent call last):
        MoneyError: ...

    Raises:
        MoneyError: if given a float, an unparseable value, or an amount with
            more than two decimal places (``₹1.005`` is not a real amount).
    """
    # bool is a subclass of int in Python, so check it before the int branch.
    if isinstance(amount, bool):
        raise MoneyError(f"bool is not a money value: {amount!r}")

    if isinstance(amount, float):
        raise MoneyError(
            f"float is not allowed for money: {amount!r}. "
            f'Use Decimal("{amount}") or the string "{amount}" instead. '
            "Floats round badly and this system compares amounts exactly."
        )

    if isinstance(amount, int):
        return amount * 100

    try:
        d = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise MoneyError(f"not a valid money value: {amount!r}") from exc

    if not d.is_finite():
        raise MoneyError(f"money must be a finite number, got {amount!r}")

    # Reject anything finer than a paisa. Rounding here would silently lose money.
    if -d.as_tuple().exponent > 2:
        raise MoneyError(
            f"{amount!r} has more than 2 decimal places. "
            "The smallest unit of Indian currency is 1 paisa."
        )

    return int(d.scaleb(2))


def to_rupees(paise: int) -> Decimal:
    """Convert whole paise back to rupees, for display only.

        >>> to_rupees(2499)
        Decimal('24.99')
    """
    if isinstance(paise, bool) or not isinstance(paise, int):
        raise MoneyError(f"paise must be an int, got {type(paise).__name__}: {paise!r}")
    return (Decimal(paise) / 100).quantize(Decimal("0.01"))


def format_rupees(paise: int) -> str:
    """Human-readable amount, for messages shown to a person.

        >>> format_rupees(2499)
        '₹24.99'
    """
    return f"₹{to_rupees(paise)}"
