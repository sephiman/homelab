import shutil
import subprocess
from pathlib import Path

import pytest

from fake_host import FakeHost


@pytest.fixture
def host(tmp_path: Path) -> FakeHost:
    if shutil.which("bwrap") is None:
        pytest.skip("bubblewrap (bwrap) is needed to sandbox the wrapper")
    return FakeHost(tmp_path)


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "shop"
    directory.mkdir()
    return directory


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=test", "-c", "user.email=test@example.invalid", *args],
        check=True,
        capture_output=True,
    )
