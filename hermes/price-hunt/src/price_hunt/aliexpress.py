import contextlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from html import unescape

from price_hunt.errors import AliExpressError, InvalidInputError
from price_hunt.money import parse_euros
from price_hunt.settings import Settings

SHIP_TO_COUNTRY = "NL"
CURRENCY = "EUR"
LOCALE = "en_US"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)
USUC_COOKIE = f"site=glo&c_tp={CURRENCY}&region={SHIP_TO_COUNTRY}&b_locale={LOCALE}"
_ALIEXPRESS_BASE = "https://www.aliexpress.com"
# Spacing between back-to-back AliExpress hits: bursts are what trips its anti-bot.
_PACE_SECONDS = 1.0

_ITEM_URL = re.compile(r"/item/(\d+)\.html")
_OG_TITLE_RE = re.compile(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"', re.I)
_OG_SUFFIX_RE = re.compile(r"\s*-\s*AliExpress(?:\s+\d+)?\s*$", re.I)
_SOLD_RE = re.compile(r"([\d,]+)\+?\s*sold", re.I)
# Words safe for a search slug: slashes/brackets in listing titles break /w/wholesale- URLs.
_TITLE_WORD = re.compile(r"[a-zA-Z0-9]+")


@dataclass(frozen=True)
class Product:
    product_id: str
    title: str
    price_cents: int
    original_price_cents: int | None
    url: str
    rating: str | None
    sold: int | None


def product_id_in(text: str) -> str | None:
    stripped = text.strip()
    if stripped.isdigit():
        return stripped
    match = _ITEM_URL.search(stripped)
    return match.group(1) if match else None


# --------------------------------------------------------------------------- #
# HTML / JSON extraction
# --------------------------------------------------------------------------- #


def _as_dict(value: object) -> dict[str, object]:
    return {str(key): item for key, item in value.items()} if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def _text(value: object) -> str | None:
    return str(value) if isinstance(value, str | int | float) and str(value) else None


def _extract_json_object(html: str, start: int, max_scan: int = 3_000_000) -> dict[str, object] | None:
    """String-aware brace counting to slice one JSON object out of HTML."""
    depth = 0
    in_string = False
    escape = False
    end = min(start + max_scan, len(html))
    for i in range(start, end):
        ch = html[i]
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(html[start : i + 1])
                except json.JSONDecodeError:
                    return None
                return _as_dict(parsed) or None
    return None


def _extract_init_data(html: str) -> dict[str, object] | None:
    """Locate and parse the ``_init_data_`` object embedded in a search page."""
    start_idx = html.find("init-data-start")
    end_idx = html.find("init-data-end")
    if start_idx != -1 and end_idx != -1:
        data_offset = html.find("data:", start_idx)
        if data_offset != -1 and data_offset < end_idx:
            json_start = html.find("{", data_offset)
            if json_start != -1 and json_start < end_idx:
                result = _extract_json_object(html, json_start, end_idx - json_start + 100)
                if result is not None:
                    return result
    assign_idx = html.find("_dida_config_._init_data_=")
    if assign_idx == -1:
        return None
    data_idx = html.find("data:", assign_idx + 26)
    if data_idx == -1 or data_idx - assign_idx > 50:
        return None
    json_start = html.find("{", data_idx + 5)
    if json_start == -1:
        return None
    return _extract_json_object(html, json_start, 2_000_000)


def _extract_title_from_page(html: str) -> str | None:
    match = _OG_TITLE_RE.search(html)
    if not match:
        return None
    title = unescape(match.group(1))
    return _OG_SUFFIX_RE.sub("", title).strip() or None


# --------------------------------------------------------------------------- #
# Product parsing from _init_data_ items
# --------------------------------------------------------------------------- #


def _parse_sold(text: str | None) -> int | None:
    if not text:
        return None
    match = _SOLD_RE.search(text)
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def _parse_product(raw: object) -> Product | None:
    """One ``_init_data_`` search item → Product, or None when it can't be a EUR offer."""
    if not isinstance(raw, dict):
        return None
    pid = _text(raw.get("productId"))
    if pid is None:
        return None

    title_mod = raw.get("title")
    title = _text(title_mod.get("displayTitle")) if isinstance(title_mod, dict) else _text(title_mod)
    if title is None:
        return None

    prices = _as_dict(raw.get("prices"))
    sale = _as_dict(prices.get("salePrice"))
    original = _as_dict(prices.get("originalPrice"))
    # Only EUR passes: the aep_usuc_f cookie asks for EUR, anything else would mix currencies.
    if sale.get("currencyCode") != CURRENCY:
        return None
    price_text = _text(sale.get("minPrice"))
    if price_text is None:
        return None
    try:
        price_cents = parse_euros(price_text)
    except InvalidInputError:
        return None

    original_price_cents = None
    orig_text = _text(original.get("minPrice"))
    if orig_text is not None:
        with contextlib.suppress(InvalidInputError):
            original_price_cents = parse_euros(orig_text)

    return Product(
        product_id=pid,
        title=title,
        price_cents=price_cents,
        original_price_cents=original_price_cents,
        url=f"{_ALIEXPRESS_BASE}/item/{pid}.html",
        rating=_text(_as_dict(raw.get("evaluation")).get("starRating")),
        sold=_parse_sold(_text(_as_dict(raw.get("trade")).get("tradeDesc"))),
    )


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #


class AliExpressClient:
    def __init__(self, settings: Settings, timeout_seconds: float = 20, base_url: str | None = None) -> None:
        self._settings = settings
        self._timeout = timeout_seconds
        self._base_url = base_url or _ALIEXPRESS_BASE

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cookie": f"aep_usuc_f={USUC_COOKIE}",
            "Referer": f"{self._base_url}/",
        }

    def _pace(self) -> None:
        if _PACE_SECONDS > 0:
            time.sleep(_PACE_SECONDS)

    def _fetch(self, url: str) -> str:
        request = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                html: str = response.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError) as error:
            raise AliExpressError(f"request to {url} failed: {error}") from None
        if "_____tmd_____" in html or "x5secdata" in html:
            raise AliExpressError("blocked by AliExpress anti-bot protection")
        return html

    def search(self, keywords: str, max_price_cents: int | None = None, page_size: int = 20) -> list[Product]:
        slug = urllib.parse.quote(keywords.strip().replace(" ", "-"), safe="-").lower()
        url = f"{self._base_url}/w/wholesale-{slug}.html"
        params: dict[str, str] = {}
        if max_price_cents is not None:
            params["maxPrice"] = f"{max_price_cents // 100}.{max_price_cents % 100:02d}"
        if params:
            url += "?" + urllib.parse.urlencode(params)

        html = self._fetch(url)
        init_data = _extract_init_data(html)
        if init_data is None:
            raise AliExpressError("could not find product data in the AliExpress search page")

        data = _as_dict(init_data.get("data"))
        fields = _as_dict(_as_dict(data.get("root")).get("fields"))
        # An obscure query leaves itemList/content out of the page entirely: that is an empty
        # result, not a broken page (only a page without _init_data_ at all is).
        items = _as_list(_as_dict(_as_dict(fields.get("mods")).get("itemList")).get("content"))

        products: list[Product] = []
        seen: set[str] = set()
        for raw in items:
            product = _parse_product(raw)
            if product is not None and product.product_id not in seen:
                seen.add(product.product_id)
                products.append(product)
            if len(products) >= page_size:
                break
        return products

    def lookup(self, product_id: str) -> Product | None:
        """Look up a single product: fetch its page title, then search for its price."""
        html = self._fetch(f"{self._base_url}/item/{product_id}.html")
        title = _extract_title_from_page(html)
        if title is None:
            return None
        return self._find_in_search(product_id, title)

    def products(self, known: Mapping[str, str]) -> list[Product]:
        """Batch lookup. *known* maps product_id → title (from the watch DB)."""
        found: list[Product] = []
        for index, (product_id, title) in enumerate(known.items()):
            if index:
                self._pace()
            product = self._find_in_search(product_id, title)
            if product is not None:
                found.append(product)
        return found

    def _find_in_search(self, product_id: str, title: str) -> Product | None:
        words = _TITLE_WORD.findall(title)
        if not words:
            return None
        # Try progressively shorter searches, down to all words if the title is short.
        lengths = [n for n in (10, 7, 5) if len(words) >= n] or [len(words)]
        for attempt, n in enumerate(lengths):
            if attempt:
                self._pace()
            results = self.search(" ".join(words[:n]), page_size=60)
            for product in results:
                if product.product_id == product_id:
                    return product
        return None

    def resolve_product_id(self, reference: str) -> str:
        product_id = product_id_in(reference)
        if product_id:
            return product_id
        if not reference.startswith("https://"):
            raise InvalidInputError(f"not an AliExpress product id or https link: {reference!r}")
        request = urllib.request.Request(reference, headers=self._headers())
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                final_url = response.geturl()
        except (urllib.error.URLError, TimeoutError) as error:
            raise InvalidInputError(f"could not open {reference}: {error}") from None
        product_id = product_id_in(final_url)
        if product_id is None:
            raise InvalidInputError(
                f"no AliExpress product in {reference}: paste the full /item/<id>.html link"
            )
        return product_id
