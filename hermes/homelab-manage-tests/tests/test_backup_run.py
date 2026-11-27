import fcntl
from pathlib import Path

from fake_host import FakeHost


def test_backup_run_starts_the_nightly_backup_detached(host: FakeHost, tmp_path: Path) -> None:
    host.mount("backup", "/backups", tmp_path)

    result = host.run("backup-run")

    assert result.returncode == 0, result.stderr
    assert "backup started" in result.stdout
    [execute] = [call for call in host.calls("docker") if call[0] == "exec"]
    assert execute[:3] == ["exec", "--detach", "backup"]
    assert "/usr/local/bin/backup.sh" in execute[-1]


def test_backup_run_is_refused_while_a_backup_holds_the_lock(host: FakeHost, tmp_path: Path) -> None:
    host.mount("backup", "/backups", tmp_path)
    with (tmp_path / ".backup.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)

        result = host.run("backup-run")

    assert result.returncode == 2
    assert "already running" in result.stderr
    assert [call for call in host.calls("docker") if call[0] == "exec"] == []


def test_backup_run_explains_when_the_backup_container_is_missing(host: FakeHost) -> None:
    result = host.run("backup-run")

    assert result.returncode == 2
    assert "no backup container" in result.stderr
