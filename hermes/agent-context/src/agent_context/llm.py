import json
import os
import urllib.error
import urllib.request
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from agent_context.home import hermes_home, read_env_file
from agent_context.managed_config import MANAGED_CONFIG, read_managed_config, section_values

# Only providers whose API serves OpenAI-style /chat/completions with a Bearer key; native
# Anthropic and Gemini endpoints can't, so they stay out and callers run without a model.
_PROVIDER_KEY_VARS = {
    "opencode-zen": "OPENCODE_ZEN_API_KEY",
    "opencode-go": "OPENCODE_GO_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "openai-api": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "custom:groq": "GROQ_API_KEY",
}


class LlmError(Exception):
    pass


@dataclass(frozen=True)
class LlmConfig:
    base_url: str
    model: str
    api_key: str


class ChatModel:
    def __init__(self, config: LlmConfig, session_prefix: str, timeout_seconds: float = 30) -> None:
        self._config = config
        self._session_prefix = session_prefix
        self._timeout = timeout_seconds

    def complete(self, system: str, user: str) -> str:
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
                "User-Agent": "OpenAI/Python 1.99.1",
                "x-opencode-session": f"{self._session_prefix}-{uuid.uuid4().hex}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            raise LlmError(f"llm: HTTP {error.code} from {self._config.base_url}") from None
        except (urllib.error.URLError, TimeoutError) as error:
            raise LlmError(f"llm: {error}") from None
        return _content_of(raw)


def _content_of(raw: bytes) -> str:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise LlmError("llm: response was not JSON") from None
    if isinstance(payload, dict):
        choices = payload.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    return content
    raise LlmError("llm: response had no message content")


def load_llm_config(
    config_path: Path = MANAGED_CONFIG, environ: Mapping[str, str] = os.environ
) -> LlmConfig | None:
    """Hermes' own model (its managed config) + key from Hermes' .env; None when not runnable."""
    text = read_managed_config(config_path)
    if text is None:
        return None
    model = section_values(text, "model")
    key_var = _PROVIDER_KEY_VARS.get(model.get("provider") or "")
    base_url, model_id = model.get("base_url"), model.get("default")
    if not (base_url and model_id and key_var):
        return None
    api_key = read_env_file(hermes_home(environ) / ".env").get(key_var)
    if not api_key:
        return None
    return LlmConfig(base_url=base_url, model=model_id, api_key=api_key)
