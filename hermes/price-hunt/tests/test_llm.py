from agent_context.llm import LlmConfig
from conftest import FakeAliExpress

from price_hunt.aliexpress import Product
from price_hunt.llm import ListingVerifier


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
    assert aliexpress.llm_headers[0]["user-agent"] == "OpenAI/Python 1.99.1"
    assert aliexpress.llm_headers[0]["x-opencode-session"].startswith("price-hunt-")
