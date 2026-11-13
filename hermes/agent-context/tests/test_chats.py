import json
from pathlib import Path

import pytest

from agent_context.chats import Chat, ChatError, Chats

DM = "111111111"
GROUP = "-1009999999999"


@pytest.fixture
def home(tmp_path: Path) -> Path:
    (tmp_path / ".env").write_text(
        f'TELEGRAM_BOT_TOKEN=x\nTELEGRAM_ALLOWED_USERS={DM}\nTELEGRAM_ALLOWED_CHATS="{GROUP}"\n'
    )
    (tmp_path / "channel_directory.json").write_text(
        json.dumps(
            {
                "telegram": [
                    {"id": DM, "name": "Owner", "type": "dm"},
                    {"id": GROUP, "name": "Family", "type": "group"},
                    {"id": "-777", "name": "Strangers", "type": "group"},
                ]
            }
        )
    )
    return tmp_path


def load(home: Path, **session: str) -> Chats:
    return Chats.load({"HERMES_HOME": str(home), **session})


def test_resolves_the_chat_the_request_came_from(home: Path) -> None:
    chats = load(home, HERMES_SESSION_PLATFORM="telegram", HERMES_SESSION_CHAT_ID=GROUP)

    assert chats.resolve(None) == Chat(GROUP, "Family")


def test_falls_back_to_the_first_allowed_user_outside_a_chat(home: Path) -> None:
    assert load(home).resolve(None) == Chat(DM, "Owner")


def test_resolves_a_chat_by_name_ignoring_case(home: Path) -> None:
    assert load(home).resolve("family").label == f"Family ({GROUP})"


def test_rejects_an_unknown_name_and_lists_the_known_ones(home: Path) -> None:
    with pytest.raises(ChatError, match=r"no chat is named 'cousins'; known chats: .*\"Family\""):
        load(home).resolve("cousins")


def test_rejects_a_chat_without_a_scheduled_job(home: Path) -> None:
    with pytest.raises(ChatError, match="TELEGRAM_ALLOWED_CHATS"):
        load(home).resolve("Strangers")


def test_rejects_a_session_chat_that_is_not_allowed(home: Path) -> None:
    chats = load(home, HERMES_SESSION_CHAT_ID="-777")

    with pytest.raises(ChatError, match="chat -777 has no scheduled job"):
        chats.resolve(None)


def test_ignores_sessions_from_other_platforms(home: Path) -> None:
    chats = load(home, HERMES_SESSION_PLATFORM="discord", HERMES_SESSION_CHAT_ID="999")

    assert chats.resolve(None).id == DM


def test_needs_an_allowed_user(tmp_path: Path) -> None:
    with pytest.raises(ChatError, match="TELEGRAM_ALLOWED_USERS"):
        load(tmp_path).resolve(None)
