import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from price_hunt.aliexpress import Product
from price_hunt.errors import VerifierError
from price_hunt.settings import hermes_home

_MATCH_ID = re.compile(r"\d{6,}")

# Only providers whose API serves OpenAI-style /chat/completions with a Bearer key; native
# Anthropic and Gemini endpoints can't, so they stay out and the verifier degrades to None.
_PROVIDER_KEY_VARS = {
    "opencode-zen": "OPENCODE_ZEN_API_KEY",
    "opencode-go": "OPENCODE_GO_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "openai-api": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "custom:groq": "GROQ_API_KEY",
}

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


@dataclass(frozen=True)
class LlmConfig:
    base_url: str
    model: str
    api_key: str


class ListingVerifier:
    def __init__(self, config: LlmConfig, timeout_seconds: float = 30) -> None:
        self._config = config
        self._timeout = timeout_seconds

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
        body = json.dumps(
            {
                "model": self._config.model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
        ).encode()
        request = urllib.request.Request(
            f"{self._config.base_url.rstrip('/')}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._config.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            raise VerifierError(f"llm: HTTP {error.code} from {self._config.base_url}") from None
        except (urllib.error.URLError, TimeoutError) as error:
            raise VerifierError(f"llm: {error}") from None
        return self._content_of(raw)

    @staticmethod
    def _content_of(raw: bytes) -> str:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            raise VerifierError("llm: response was not JSON") from None
        if isinstance(payload, dict):
            choices = payload.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                message = choices[0].get("message")
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, str):
                        return content
        raise VerifierError("llm: response had no message content")


def _model_block(text: str) -> dict[str, str]:
    """The keys of the top-level ``model:`` block of Hermes' managed config.yaml."""
    values: dict[str, str] = {}
    in_model = False
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            in_model = line.strip() == "model:"
            continue
        if not in_model:
            continue
        key, sep, value = line.strip().partition(":")
        if sep:
            values[key.strip()] = value.strip().strip("\"'")
    return values


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return values
    for line in lines:
        key, sep, value = line.strip().partition("=")
        if sep and not key.startswith("#"):
            values[key.strip()] = value.strip().strip("\"'")
    return values


def load_llm_config(
    config_path: Path | None = None, environ: Mapping[str, str] = os.environ
) -> LlmConfig | None:
    """Hermes' own model (its managed config) + key from Hermes' .env; None when not runnable."""
    try:
        text = (config_path or Path("/etc/hermes/config.yaml")).read_text()
    except OSError:
        return None
    model = _model_block(text)
    key_var = _PROVIDER_KEY_VARS.get(model.get("provider") or "")
    base_url, model_id = model.get("base_url"), model.get("default")
    if not (base_url and model_id and key_var):
        return None
    api_key = _read_env_file(hermes_home(environ) / ".env").get(key_var)
    if not api_key:
        return None
    return LlmConfig(base_url=base_url, model=model_id, api_key=api_key)
