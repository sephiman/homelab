from pathlib import Path

from fake_host import Container, FakeHost


def test_restart_of_one_service_restarts_only_that_service(host: FakeHost, project_dir: Path) -> None:
    host.add_project("shop", project_dir)
    host.compose_ps_returns([Container("web", "shop-web-1", health="healthy")])

    result = host.run("restart", "shop", "web")

    assert result.returncode == 0, result.stderr
    assert host.compose_calls("restart") == [["web"]]
    assert host.compose_calls("ps") == [["--all", "--format", host.compose_calls("ps")[0][2], "web"]]
    assert "shop-web-1\trunning\thealthy" in result.stdout


def test_restart_without_service_restarts_the_whole_project(host: FakeHost, project_dir: Path) -> None:
    host.add_project("shop", project_dir)
    host.compose_ps_returns([Container("web", "shop-web-1"), Container("db", "shop-db-1")])

    result = host.run("restart", "shop")

    assert result.returncode == 0, result.stderr
    assert host.compose_calls("restart") == [[]]


def test_update_waits_while_containers_start_then_reports_them_healthy(
    host: FakeHost, project_dir: Path
) -> None:
    host.add_project("shop", project_dir)
    host.compose_ps_returns(
        [Container("web", "shop-web-1", health="starting")],
        [Container("web", "shop-web-1", health="healthy")],
    )

    result = host.run("update", "shop")

    assert result.returncode == 0, result.stderr
    assert len(host.compose_calls("ps")) == 2
    assert "not a git checkout, skipping git pull" in result.stdout
    assert host.compose_calls("up") == [["-d", "--build"]]


def test_update_fails_when_a_container_is_unhealthy_after_the_deploy(
    host: FakeHost, project_dir: Path
) -> None:
    host.add_project("shop", project_dir)
    host.compose_ps_returns([Container("web", "shop-web-1", health="unhealthy")])

    result = host.run("update", "shop")

    assert result.returncode != 0
    assert "not healthy after the deploy" in result.stderr
    assert "shop-web-1\trunning\tunhealthy" in result.stdout


def test_update_accepts_a_one_shot_container_that_exited_cleanly(host: FakeHost, project_dir: Path) -> None:
    host.add_project("shop", project_dir)
    host.compose_ps_returns(
        [Container("web", "shop-web-1"), Container("migrate", "shop-migrate-1", state="exited", exit_code=0)]
    )

    assert host.run("update", "shop").returncode == 0


def test_update_fails_when_a_container_exited_with_an_error(host: FakeHost, project_dir: Path) -> None:
    host.add_project("shop", project_dir)
    host.compose_ps_returns([Container("web", "shop-web-1", state="exited", exit_code=1)])

    assert host.run("update", "shop").returncode != 0


def test_update_images_of_one_service_pulls_and_recreates_it_without_building(
    host: FakeHost, project_dir: Path
) -> None:
    host.add_project("shop", project_dir)
    host.compose_ps_returns([Container("web", "shop-web-1")])

    result = host.run("update-images", "shop", "web")

    assert result.returncode == 0, result.stderr
    assert host.compose_calls("pull") == [["web"]]
    assert host.compose_calls("up") == [["-d", "web"]]


def test_restart_of_an_unknown_project_changes_nothing(host: FakeHost) -> None:
    result = host.run("restart", "nope")

    assert result.returncode == 2
    assert "no compose project 'nope'" in result.stderr
    assert host.compose_calls("restart") == []
