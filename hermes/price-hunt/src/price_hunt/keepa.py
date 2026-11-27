"""Keepa price history of Amazon EU products, through Keepa's public chart (no API key needed)."""

import re
import urllib.parse
from dataclasses import dataclass

from price_hunt.errors import InvalidInputError

# Bare ASINs: ten upper-case letters/digits (books use their ISBN-10, which may end in X).
ASIN_PATTERN = re.compile(r"[A-Z0-9]{9}[A-Z0-9X]")
AMAZON_PRODUCT_PATH = re.compile(r"/(?:dp|gp/product|gp/aw/d|product)/([A-Za-z0-9]{10})(?:[/?]|$)")
CHART_URL = "https://graph.keepa.com/pricehistory.png"
DEFAULT_RANGE_DAYS = 90


@dataclass(frozen=True)
class AmazonStore:
    name: str
    domain: str  # amazon.<domain>
    keepa_domain_id: int
    chart_domain: str


# Keepa numbers its Amazon marketplaces; the chart takes the country code instead.
STORES = (
    AmazonStore("Amazon.nl", "nl", 14, "nl"),
    AmazonStore("Amazon.de", "de", 3, "de"),
    AmazonStore("Amazon.es", "es", 9, "es"),
    AmazonStore("Amazon.fr", "fr", 4, "fr"),
    AmazonStore("Amazon.it", "it", 8, "it"),
)


@dataclass(frozen=True)
class KeepaLinks:
    store: AmazonStore
    product_url: str
    keepa_url: str
    chart_url: str


def asin_in(reference: str) -> str:
    """The ASIN of a bare ASIN or an Amazon product link (amazon.<any>/…/dp/<ASIN>…)."""
    text = reference.strip()
    if ASIN_PATTERN.fullmatch(text.upper()) and not text.startswith(("http", "www.")):
        return text.upper()
    parsed = urllib.parse.urlparse(text if "://" in text else f"https://{text}")
    host = parsed.hostname or ""
    if host.endswith(("amzn.eu", "amzn.to", "a.co")):
        raise InvalidInputError(
            "short Amazon links hide the product: open it and paste the full amazon.… link or its ASIN"
        )
    found = AMAZON_PRODUCT_PATH.search(parsed.path) if ".amazon." in f".{host}" else None
    if found is None:
        raise InvalidInputError(f"not an ASIN or Amazon product link: {reference!r}")
    return found.group(1).upper()


def keepa_links(asin: str, range_days: int = DEFAULT_RANGE_DAYS) -> list[KeepaLinks]:
    if not 1 <= range_days <= 365:
        raise InvalidInputError("range must be 1-365 days")
    links = []
    for store in STORES:
        chart_query = urllib.parse.urlencode(
            {"asin": asin, "domain": store.chart_domain, "range": range_days, "amazon": 1, "new": 1}
        )
        links.append(
            KeepaLinks(
                store=store,
                product_url=f"https://www.amazon.{store.domain}/dp/{asin}",
                keepa_url=f"https://keepa.com/#!product/{store.keepa_domain_id}-{asin}",
                chart_url=f"{CHART_URL}?{chart_query}",
            )
        )
    return links
