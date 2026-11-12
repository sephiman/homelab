from decimal import Decimal, InvalidOperation

from price_hunt.errors import InvalidInputError


def parse_euros(text: str) -> int:
    """Euro amount as typed ("14", "14.5", "14,50", "€14", "14€") to cents."""
    cleaned = text.strip().removeprefix("€").removesuffix("€").strip().replace(",", ".")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        raise InvalidInputError(f"not a euro amount: {text!r}") from None
    if not amount.is_finite() or amount <= 0:
        raise InvalidInputError(f"not a positive euro amount: {text!r}")
    return int((amount * 100).to_integral_value())


def format_euros(cents: int) -> str:
    return f"€{cents // 100}.{cents % 100:02d}"


def parse_min_drop(text: str) -> tuple[int | None, int | None]:
    """Parse a minimum-drop threshold ('5', '2.50', '5%', or 'none') into (cents, bps)."""
    stripped = text.strip()
    if stripped.lower() in {"none", "off", "0"}:
        return None, None
    if stripped.endswith("%"):
        raw_pct = stripped[:-1].strip().replace(",", ".")
        try:
            pct = Decimal(raw_pct)
        except InvalidOperation:
            raise InvalidInputError(f"not a percentage: {text!r}") from None
        if not pct.is_finite() or pct <= 0 or pct > 100:
            raise InvalidInputError(f"percentage must be between 0 and 100: {text!r}")
        return None, int((pct * 100).to_integral_value())
    return parse_euros(stripped), None


def format_min_drop(min_drop_cents: int | None, min_drop_bps: int | None) -> str | None:
    if min_drop_cents is not None:
        return format_euros(min_drop_cents)
    if min_drop_bps is not None:
        if min_drop_bps % 100 == 0:
            return f"{min_drop_bps // 100}%"
        return f"{min_drop_bps / 100:.2f}".rstrip("0").rstrip(".") + "%"
    return None
