import re
from collections.abc import Sequence

from agent_context.llm import ChatModel, LlmConfig, LlmError

from price_hunt.aliexpress import Product
from price_hunt.errors import VerifierError

_MATCH_ID = re.compile(r"\d{6,}")

_MATCH_SYSTEM_PROMPT = (
    "You verify AliExpress listings for a price watcher. The user names one product and lists "
    "search results as 'id: title — price'. Reply with ONLY the ids of listings that are exactly "
    "that product, comma-separated (or nothing when none match). Accessories, mounts, brackets, "
    "cases, covers, cables, tools and spare parts are never the product. A Mini/Lite/Pro/Gen2/"
    "newer-generation variant is a different product."
)

_NAME_SYSTEM_PROMPT = (
    "You extract the product name from an AliExpress listing title. Drop marketing phrases, "
    "bundle counts like '1-4 PCS', compatibilities like 'Work With Alexa', and store names. "
    "Reply with ONLY the product name."
)


class ListingVerifier:
    def __init__(self, config: LlmConfig, timeout_seconds: float = 30) -> None:
        self._model = ChatModel(config, session_prefix="price-hunt", timeout_seconds=timeout_seconds)

    def matching_ids(self, query: str, offers: Sequence[Product]) -> set[str]:
        """The ids of *offers* the model accepts as the exact product named by *query*."""
        if not offers:
            return set()
        lines = "\n".join(f"{o.product_id}: {o.title} — €{o.price_cents / 100:.2f}" for o in offers)
        content = self._complete(_MATCH_SYSTEM_PROMPT, f'Product: "{query}"\nListings:\n{lines}')
        wanted = set(_MATCH_ID.findall(content))
        return {o.product_id for o in offers if o.product_id in wanted}

    def product_name(self, listing_title: str) -> str | None:
        """The clean product name inside an AliExpress listing title, for search queries."""
        content = self._complete(_NAME_SYSTEM_PROMPT, listing_title)
        return content.strip().strip("\"'`").strip() or None

    def _complete(self, system: str, user: str) -> str:
        try:
            return self._model.complete(system, user)
        except LlmError as error:
            raise VerifierError(str(error)) from None
