import pytest

from price_hunt.aliexpress import _parse_sold, product_id_in
from price_hunt.errors import InvalidInputError
from price_hunt.money import format_euros, parse_euros


@pytest.mark.parametrize(
    ("text", "cents"),
    [("14", 1400), ("14.5", 1450), ("14,50", 1450), ("€ 9.99", 999), ("14€", 1400), ("14 €", 1400)],
)
def test_parse_euros_accepts_how_people_type_prices(text: str, cents: int) -> None:
    assert parse_euros(text) == cents


@pytest.mark.parametrize("text", ["", "abc", "0", "-3", "nan"])
def test_parse_euros_rejects_non_positive_or_garbage(text: str) -> None:
    with pytest.raises(InvalidInputError):
        parse_euros(text)


def test_format_euros_keeps_two_decimals() -> None:
    assert format_euros(1405) == "€14.05"


@pytest.mark.parametrize(
    ("text", "product_id"),
    [
        ("1005006789", "1005006789"),
        ("https://www.aliexpress.com/item/1005006789.html?spm=a2g0o", "1005006789"),
        ("https://nl.aliexpress.com/item/1005006789.html", "1005006789"),
        ("https://a.aliexpress.com/_mKx1abc", None),
    ],
)
def test_product_id_in_reads_ids_and_item_links(text: str, product_id: str | None) -> None:
    assert product_id_in(text) == product_id


@pytest.mark.parametrize(
    ("text", "sold"),
    [
        ("1,234 sold", 1234),
        ("100+ sold", 100),
        ("1,000+ sold", 1000),
        ("42 sold", 42),
        ("sold out", None),
        ("no count here", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_sold_reads_aliexpress_sold_texts(text: str | None, sold: int | None) -> None:
    assert _parse_sold(text) == sold
