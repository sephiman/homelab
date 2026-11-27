import json

import pytest

from fake_host import FakeHost

HA_API = "http://127.0.0.1:8123/api"


@pytest.mark.parametrize(
    ("action", "entity", "service"),
    [
        ("ha-switch", "switch.kettle", "switch/turn_on"),
        ("ha-light", "light.kitchen", "light/turn_on"),
    ],
)
def test_switching_calls_only_the_turn_on_service_of_that_entity(
    host: FakeHost, action: str, entity: str, service: str
) -> None:
    host.configure_home_assistant()
    host.respond(f"{HA_API}/states/{entity}", {"entity_id": entity, "state": "on"})

    result = host.run(action, "on", entity)

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{entity}: on\n"
    post, _ = host.calls("curl")
    assert post[-1] == f"{HA_API}/services/{service}"
    assert json.loads(post[post.index("-d") + 1]) == {"entity_id": entity}


def test_switching_explains_when_home_assistant_is_not_configured(host: FakeHost) -> None:
    result = host.run("ha-switch", "off", "switch.kettle")

    assert result.returncode == 2
    assert "Home Assistant access not configured" in result.stderr
    assert host.calls("curl") == []
