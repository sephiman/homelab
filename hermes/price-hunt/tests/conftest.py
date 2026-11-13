import json
import re
import threading
import urllib.parse
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import price_hunt.aliexpress as aliexpress_module
import price_hunt.cli as cli_module
from price_hunt.aliexpress import Product
from price_hunt.errors import VerifierError

OWNER_CHAT = "111111111"
FAMILY_CHAT = "-1009999999999"


@dataclass
class FakeVerifier:
    """Stands in for the LLM listing verifier; defaults to accepting every offer."""

    verdicts: dict[str, bool] = field(default_factory=dict)
    renames: dict[str, str] = field(default_factory=dict)
    calls: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    name_calls: list[str] = field(default_factory=list)
    error: str | None = None

    def matching_ids(self, query: str, offers: Sequence[Product]) -> set[str]:
        self.calls.append((query, tuple(o.product_id for o in offers)))
        if self.error:
            raise VerifierError(self.error)
        return {o.product_id for o in offers if self.verdicts.get(o.product_id, True)}

    def product_name(self, listing_title: str) -> str | None:
        self.name_calls.append(listing_title)
        return self.renames.get(listing_title, listing_title)


@dataclass
class FakeOffer:
    product_id: str
    title: str
    price: float
    original_price: float
    rating: str
    sold: str


@dataclass
class FakeAliExpress:
    """Stands in for www.aliexpress.com: serves search HTML, product pages and chat completions."""

    url: str
    products: dict[str, FakeOffer] = field(default_factory=dict)
    error: str | None = None
    search_error: str | None = None
    no_item_list: bool = False
    llm_reply: str = ""
    llm_requests: list[dict[str, object]] = field(default_factory=list)
    llm_auth: list[str] = field(default_factory=list)
    llm_headers: list[dict[str, str]] = field(default_factory=list)
    verifier: FakeVerifier = field(default_factory=FakeVerifier)
    requests: list[dict[str, str]] = field(default_factory=list)

    def offer(
        self,
        product_id: str,
        price: str,
        title: str = "Shelly Plus 1PM",
        original_price: str = "19.99",
        rating: str = "4.9",
        sold: str = "1,234 sold",
    ) -> None:
        self.products[product_id] = FakeOffer(
            product_id=product_id,
            title=title,
            price=float(price),
            original_price=float(original_price),
            rating=rating,
            sold=sold,
        )

    def search_html(self, slug: str, params: dict[str, str]) -> str:
        self.requests.append({"kind": "search", "slug": slug, **params})
        if self.error or self.search_error:
            return "<html>_____tmd_____ verification</html>"
        slug_words = set(slug.lower().replace("-", " ").split())
        matching = [p for p in self.products.values() if slug_words & set(p.title.lower().split())]
        if "maxPrice" in params:
            cap = float(params["maxPrice"])
            matching = [p for p in matching if p.price <= cap]

        items = [
            {
                "productId": p.product_id,
                "title": {"displayTitle": p.title},
                "prices": {
                    "salePrice": {
                        "minPrice": p.price,
                        "formattedPrice": f"€ {p.price:.2f}",
                        "currencyCode": "EUR",
                    },
                    "originalPrice": {"minPrice": p.original_price},
                },
                "evaluation": {"starRating": p.rating},
                "trade": {"tradeDesc": p.sold},
            }
            for p in matching
        ]
        mods: dict[str, object] = {} if self.no_item_list else {"itemList": {"content": items}}
        init_data = {
            "data": {
                "root": {
                    "fields": {
                        "mods": mods,
                        "pageInfo": {"totalResults": len(matching), "page": 1},
                    }
                }
            }
        }
        return (
            "<html><!--init-data-start-->"
            f"<script>_dida_config_._init_data_ = data: {json.dumps(init_data)}</script>"
            "<!--init-data-end--></html>"
        )

    def product_page(self, product_id: str) -> str:
        self.requests.append({"kind": "product", "product_id": product_id})
        if self.error:
            return "<html>_____tmd_____ verification</html>"
        product = self.products.get(product_id)
        if product is None:
            return "<html><head></head><body>Not found</body></html>"
        return (
            "<html><head>"
            f'<meta property="og:title" content="{product.title} - AliExpress" />'
            "</head><body></body></html>"
        )


def _handler(fake: FakeAliExpress) -> type[BaseHTTPRequestHandler]:
    _SEARCH = re.compile(r"^/w/wholesale-(.+?)\.html$")
    _ITEM = re.compile(r"^/item/(\d+)\.html$")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            query = dict(urllib.parse.parse_qsl(parsed.query))

            search_match = _SEARCH.match(path)
            item_match = _ITEM.match(path)

            if search_match:
                slug = urllib.parse.unquote(search_match.group(1))
                body = fake.search_html(slug, query).encode()
                self.send_response(200)
            elif item_match:
                body = fake.product_page(item_match.group(1)).encode()
                self.send_response(200)
            else:
                body = b"not found"
                self.send_response(404)

            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            fake.llm_requests.append(json.loads(body))
            fake.llm_auth.append(self.headers.get("Authorization") or "")
            fake.llm_headers.append({k.lower(): v for k, v in self.headers.items()})
            payload = json.dumps({"choices": [{"message": {"content": fake.llm_reply}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            pass

    return Handler


@pytest.fixture
def aliexpress(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeAliExpress]:
    fake = FakeAliExpress(url="")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(fake))
    fake.url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_SESSION_CHAT_ID", raising=False)
    (tmp_path / ".env").write_text(
        f"TELEGRAM_ALLOWED_USERS={OWNER_CHAT}\nTELEGRAM_ALLOWED_CHATS={FAMILY_CHAT}\n"
    )
    (tmp_path / "channel_directory.json").write_text(
        json.dumps({"telegram": [{"id": FAMILY_CHAT, "name": "Family", "type": "group"}]})
    )
    monkeypatch.setattr(aliexpress_module, "ALIEXPRESS_BASE", fake.url)
    monkeypatch.setattr(cli_module, "build_verifier", lambda: fake.verifier)
    yield fake
    server.shutdown()
