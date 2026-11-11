from pathlib import Path

import pytest
from conftest import FakeAliExpress

from price_hunt.aliexpress import Product
from price_hunt.llm import ListingVerifier, LlmConfig, load_llm_config

CONFIG = """\
timezone: Europe/Madrid
model:
  default: deepseek-v4.1-flash
  provider: opencode-go
  base_url: https://opencode.ai/zen/go/v1

model_aliases:
  flash:
    model: other-model
"""


@pytest.fixture
def managed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG)
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env").write_text("OPENCODE_GO_API_KEY=go-key\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    return config


def test_load_llm_config_reads_the_managed_model(managed: Path) -> None:
    assert load_llm_config(managed) == LlmConfig(
        base_url="https://opencode.ai/zen/go/v1", model="deepseek-v4.1-flash", api_key="go-key"
    )


def test_load_llm_config_without_the_provider_key(managed: Path) -> None:
    (managed.parent / "home" / ".env").write_text("OTHER_VAR=x\n")

    assert load_llm_config(managed) is None


def test_load_llm_config_ignores_non_openai_compatible_providers(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("model:\n  default: m\n  provider: gemini\n  base_url: https://x/v1\n")

    assert load_llm_config(config) is None


def test_load_llm_config_reads_openai_compatible_custom_providers(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        "model:\n  default: m\n  provider: custom:groq\n  base_url: https://api.groq.com/openai/v1\n"
    )
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env").write_text("GROQ_API_KEY=groq-key\n")

    assert load_llm_config(config, environ={"HERMES_HOME": str(home)}) == LlmConfig(
        base_url="https://api.groq.com/openai/v1", model="m", api_key="groq-key"
    )


def test_load_llm_config_without_a_config_file(tmp_path: Path) -> None:
    assert load_llm_config(tmp_path / "absent.yaml") is None


def test_listing_verifier_asks_the_model_and_reads_matching_ids(
    aliexpress: FakeAliExpress,
) -> None:
    config = LlmConfig(base_url=f"{aliexpress.url}/v1", model="test-model", api_key="secret")
    offers = [
        Product("1005001", "Shelly Plus 1PM", 1349, None, "u", "4.9", 10),
        Product("1005002", "Wall bracket for Shelly", 210, None, "u", None, None),
    ]
    aliexpress.llm_reply = "1005001"

    verifier = ListingVerifier(config)
    aliexpress.llm_reply = "1005001"
    assert verifier.matching_ids("shelly plus 1pm", offers) == {"1005001"}

    aliexpress.llm_reply = "Shelly Plus 1PM"
    assert verifier.product_name("Shelly Plus 1PM - cheap deal") == "Shelly Plus 1PM"

    request = aliexpress.llm_requests[0]
    assert request["model"] == "test-model"
    messages = request["messages"]
    assert isinstance(messages, list) and isinstance(messages[1], dict)
    assert "Product:" in messages[1]["content"]
    assert aliexpress.llm_auth == ["Bearer secret", "Bearer secret"]
