import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from agent_context.llm import ChatModel, LlmConfig, LlmError, load_llm_config

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


class FakeCompletions:
    def __init__(self) -> None:
        self.reply: object = {"choices": [{"message": {"content": "hello"}}]}
        self.headers: list[dict[str, str]] = []
        self.bodies: list[dict[str, object]] = []


@pytest.fixture
def completions() -> Iterator[tuple[FakeCompletions, str]]:
    fake = FakeCompletions()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            fake.bodies.append(json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)))))
            fake.headers.append({k.lower(): v for k, v in self.headers.items()})
            payload = json.dumps(fake.reply).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield fake, f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()


def test_chat_model_sends_the_prompt_and_returns_the_reply(completions: tuple[FakeCompletions, str]) -> None:
    fake, url = completions
    model = ChatModel(LlmConfig(url, "test-model", "secret"), session_prefix="page-watch")

    assert model.complete("system text", "user text") == "hello"
    assert fake.bodies[0]["model"] == "test-model"
    assert fake.bodies[0]["messages"] == [
        {"role": "system", "content": "system text"},
        {"role": "user", "content": "user text"},
    ]
    assert fake.headers[0]["authorization"] == "Bearer secret"
    assert fake.headers[0]["x-opencode-session"].startswith("page-watch-")


def test_chat_model_rejects_a_reply_without_content(completions: tuple[FakeCompletions, str]) -> None:
    fake, url = completions
    fake.reply = {"error": "overloaded"}

    with pytest.raises(LlmError, match="no message content"):
        ChatModel(LlmConfig(url, "m", "k"), session_prefix="x").complete("s", "u")
