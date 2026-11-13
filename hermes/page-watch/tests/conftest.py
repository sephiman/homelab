import json
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from agent_context.llm import ChatModel, LlmConfig

import page_watch.cli as cli_module
from page_watch.judge import PageJudge

OWNER_CHAT = "111111111"
FAMILY_CHAT = "-1009999999999"
START = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)

TicketVerdict = Callable[[str], dict[str, object]]


def ticket_verdict(page_text: str) -> dict[str, object]:
    """A stand-in model: tickets are on sale once the page offers to buy them."""
    on_sale = "buy tickets" in page_text.casefold()
    return {
        "met": on_sale,
        "summary": "Tickets are on sale." if on_sale else "Tickets are not on sale yet.",
        "anchors": ["The Band - 14 March 2027"],
        "waiting": [] if on_sale else ["Coming soon"],
        "ready": ["Buy tickets"],
    }


def concert_page(status: str = "Coming soon", footer: str = "© 2026 Venue", extra: str = "") -> str:
    filler = "".join(
        f"<p>Other show number {n} with a long description of the evening.</p>" for n in range(12)
    )
    return (
        "<html><head><title>Venue</title><script>var x = 'Buy tickets';</script></head><body>"
        f"<nav>Home · Agenda</nav>{filler}"
        f"<div class='event'><h2>The Band - 14 March 2027</h2><p>{status}</p>{extra}</div>"
        f"{filler}<footer>{footer}</footer></body></html>"
    )


@dataclass
class FakeWeb:
    """Stands in for the watched sites and for the model's chat completions endpoint."""

    url: str = ""
    pages: dict[str, tuple[int, str]] = field(default_factory=dict)
    fetches: list[str] = field(default_factory=list)
    verdict: TicketVerdict = ticket_verdict
    raw_reply: str | None = None
    model_calls: list[str] = field(default_factory=list)
    now: datetime = START

    def page(self, path: str, html: str, status: int = 200) -> str:
        self.pages[path] = (status, html)
        return f"{self.url}{path}"

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


def _handler(fake: FakeWeb) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            fake.fetches.append(self.path)
            status, html = fake.pages.get(self.path, (404, "<html>Not found</html>"))
            self._send(status, html.encode(), "text/html; charset=utf-8")

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            user = body["messages"][1]["content"]
            fake.model_calls.append(user)
            page_text = user.split("<<<", 1)[1]
            content = fake.raw_reply if fake.raw_reply is not None else json.dumps(fake.verdict(page_text))
            payload = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
            self._send(200, payload, "application/json")

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    return Handler


@pytest.fixture
def web(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeWeb]:
    fake = FakeWeb()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(fake))
    fake.url = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_SESSION_CHAT_ID", raising=False)
    (tmp_path / ".env").write_text(
        f"TELEGRAM_ALLOWED_USERS={OWNER_CHAT}\nTELEGRAM_ALLOWED_CHATS={FAMILY_CHAT}\n"
    )
    (tmp_path / "channel_directory.json").write_text(
        json.dumps({"telegram": [{"id": FAMILY_CHAT, "name": "Family", "type": "group"}]})
    )
    model = ChatModel(LlmConfig(f"{fake.url}/v1", "test-model", "key"), session_prefix="page-watch")
    monkeypatch.setattr(cli_module, "build_judge", lambda: PageJudge(model))
    monkeypatch.setattr(cli_module, "local_zone", lambda: ZoneInfo("Europe/Madrid"))
    monkeypatch.setattr(cli_module, "now_utc", lambda: fake.now)
    yield fake
    server.shutdown()
