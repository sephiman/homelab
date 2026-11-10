from decimal import Decimal, InvalidOperation

from price_hunt.errors import InvalidInputError


def parse_euros(text: str) -> int:
    """Euro amount as typed ("14", "14.5", "14,50", "€14") to cents."""
    cleaned = text.strip().removeprefix("€").strip().replace(",", ".")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        raise InvalidInputError(f"not a euro amount: {text!r}") from None
    if not amount.is_finite() or amount <= 0:
        raise InvalidInputError(f"not a positive euro amount: {text!r}")
    return int((amount * 100).to_integral_value())


def format_euros(cents: int) -> str:
    return f"€{cents // 100}.{cents % 100:02d}"
