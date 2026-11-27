from pathlib import Path

import pytest

from fake_host import FakeHost


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(["frobnicate"], id="unknown action"),
        pytest.param(["restart", "../etc"], id="path as project"),
        pytest.param(["restart", "shop;id"], id="shell metacharacters in project"),
        pytest.param(["restart", "shop", "web;id"], id="shell metacharacters in service"),
        pytest.param(["restart", "shop", "-v"], id="option as service"),
        pytest.param(["update", "shop", "web", "db"], id="two services"),
        pytest.param(["code-show", "shop", "../other/app.py"], id="parent folder in code path"),
        pytest.param(["code-show", "shop", "/etc/shadow"], id="absolute code path"),
        pytest.param(["inspect", "-f{{.Config.Env}}"], id="option as container"),
        pytest.param(["metrics", "x" * 501], id="oversized PromQL"),
        pytest.param(["media-queue-remove", "sonarr", "1", "force"], id="unknown media removal flag"),
        pytest.param(["media-queue-remove", "lidarr", "1"], id="unknown media app"),
        pytest.param(["ha-light", "on", "lock.front_door"], id="lock through ha-light"),
        pytest.param(["ha-switch", "on", "light.kitchen"], id="light through ha-switch"),
        pytest.param(["ha-switch", "unlock", "switch.kettle"], id="unknown switch action"),
        pytest.param(["backup-run", "now"], id="argument to backup-run"),
    ],
)
def test_rejects_invalid_arguments_without_touching_anything(
    host: FakeHost, project_dir: Path, args: list[str]
) -> None:
    host.add_project("shop", project_dir)
    host.configure_home_assistant()

    result = host.run(*args)

    assert result.returncode == 2
    assert host.compose_calls("restart") == host.compose_calls("up") == []
    assert host.calls("curl") == []
    assert [call for call in host.calls("docker") if call[0] in ("inspect", "exec")] == []


def test_inspect_never_asks_docker_for_the_environment(host: FakeHost) -> None:
    host.run("inspect", "postgresdb")

    [inspect] = host.calls("docker")
    template = inspect[inspect.index("--format") + 1]
    assert ".Env" not in template
    assert inspect[-1] == "postgresdb"


def test_logs_are_capped_at_500_lines(host: FakeHost, project_dir: Path) -> None:
    host.add_project("shop", project_dir)

    host.run("logs", "shop", "100000")

    [logs] = host.compose_calls("logs")
    assert logs[logs.index("--tail") + 1] == "500"
