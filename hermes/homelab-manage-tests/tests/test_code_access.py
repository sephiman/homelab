from pathlib import Path

import pytest

from conftest import git
from fake_host import FakeHost


@pytest.fixture
def repo(project_dir: Path) -> Path:
    files = {
        "app.py": "PRICE = 10\n",
        "src/Order.kt": "@Entity\nclass Order\n",
        "config/.env.prod": "DB_PASSWORD=committed-by-mistake\n",
        "deploy/server.key": "-----BEGIN PRIVATE KEY-----\n",
        "deploy/db_password.txt": "hunter2 @Entity\n",
    }
    for name, content in files.items():
        (project_dir / name).parent.mkdir(parents=True, exist_ok=True)
        (project_dir / name).write_text(content)
    git(project_dir, "init", "--quiet")
    git(project_dir, "add", ".")
    git(project_dir, "commit", "--quiet", "-m", "initial")
    (project_dir / "app.py").write_text("PRICE = 99  # uncommitted\n")
    (project_dir / ".env").write_text("SECRET=never-committed\n")
    return project_dir


def test_code_show_reads_the_committed_version_not_the_working_tree(host: FakeHost, repo: Path) -> None:
    host.add_project("shop", repo)

    result = host.run("code-show", "shop", "app.py")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "PRICE = 10\n"


def test_code_ls_hides_secret_looking_files(host: FakeHost, repo: Path) -> None:
    host.add_project("shop", repo)

    result = host.run("code-ls", "shop")

    assert result.stdout.split() == ["app.py", "src/Order.kt"]


@pytest.mark.parametrize("path", ["config/.env.prod", "deploy/server.key", "deploy/db_password.txt", ".env"])
def test_code_show_refuses_secret_looking_files(host: FakeHost, repo: Path, path: str) -> None:
    host.add_project("shop", repo)

    result = host.run("code-show", "shop", path)

    assert result.returncode == 2
    assert "secret-looking" in result.stderr
    assert result.stdout == ""


def test_code_grep_leaves_out_matches_in_secret_looking_files(host: FakeHost, repo: Path) -> None:
    host.add_project("shop", repo)

    result = host.run("code-grep", "shop", "@Entity")

    assert result.stdout == "HEAD:src/Order.kt:1:@Entity\n"
