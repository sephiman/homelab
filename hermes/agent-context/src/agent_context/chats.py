import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from agent_context.home import hermes_home, read_env_file

_CHAT_ID = re.compile(r"-?\d+")


class ChatError(Exception):
    pass


@dataclass(frozen=True)
class Chat:
    id: str
    name: str | None

    @property
    def label(self) -> str:
        return f"{self.name} ({self.id})" if self.name else self.id


def _ids(value: str | None) -> list[str]:
    ids = [part.strip() for part in (value or "").split(",")]
    return [chat_id for chat_id in ids if _CHAT_ID.fullmatch(chat_id)]


def _telegram_names(directory_path: Path) -> dict[str, str]:
    """Hermes' channel_directory.json: the Telegram chats it has seen, id → name."""
    try:
        payload = json.loads(directory_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    entries = payload.get("telegram") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return {}
    names: dict[str, str] = {}
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            names[str(entry.get("id"))] = entry["name"]
    return names


@dataclass(frozen=True)
class Chats:
    """The Telegram chats a scheduled job delivers to: install-host.sh creates one per allowed chat."""

    allowed: Sequence[str]
    names: Mapping[str, str]
    session_chat: str | None

    @classmethod
    def load(cls, environ: Mapping[str, str] = os.environ) -> "Chats":
        home = hermes_home(environ)
        env = read_env_file(home / ".env")
        allowed = _ids(env.get("TELEGRAM_ALLOWED_USERS")) + _ids(env.get("TELEGRAM_ALLOWED_CHATS"))
        platform = environ.get("HERMES_SESSION_PLATFORM", "telegram")
        session_chat = environ.get("HERMES_SESSION_CHAT_ID", "").strip() if platform == "telegram" else ""
        return cls(
            tuple(dict.fromkeys(allowed)),
            _telegram_names(home / "channel_directory.json"),
            session_chat or None,
        )

    def chat(self, chat_id: str) -> Chat:
        return Chat(chat_id, self.names.get(chat_id))

    @property
    def default(self) -> Chat:
        """The first allowed user's private chat, where the other scheduled reports go too."""
        if not self.allowed:
            raise ChatError("TELEGRAM_ALLOWED_USERS is not set in Hermes' .env: no chat to deliver to")
        return self.chat(self.allowed[0])

    def resolve(self, reference: str | None) -> Chat:
        """The chat named by *reference* (id or name), else the current one, else the default."""
        if reference is None or not reference.strip():
            return self._allowed(self.session_chat) if self.session_chat else self.default
        wanted = reference.strip()
        if _CHAT_ID.fullmatch(wanted):
            return self._allowed(wanted)
        matches = [chat_id for chat_id, name in self.names.items() if name.casefold() == wanted.casefold()]
        if len(matches) != 1:
            known = ", ".join(sorted(f'"{name}"' for name in self.names.values())) or "none"
            problem = "several chats are" if matches else "no chat is"
            raise ChatError(f"{problem} named {wanted!r}; known chats: {known}")
        return self._allowed(matches[0])

    def _allowed(self, chat_id: str) -> Chat:
        if chat_id not in self.allowed:
            raise ChatError(
                f"chat {chat_id} has no scheduled job to deliver alerts: add it to TELEGRAM_ALLOWED_CHATS "
                "in hermes/.env and run install-host.sh again"
            )
        return self.chat(chat_id)
