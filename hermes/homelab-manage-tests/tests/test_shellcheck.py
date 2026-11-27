import shutil
import subprocess
from pathlib import Path

import pytest

from fake_host import HERMES_DIR

SCRIPTS = [
    HERMES_DIR / "homelab-manage.sh",
    HERMES_DIR / "install-host.sh",
    *(HERMES_DIR / "scripts").glob("*.sh"),
]


@pytest.mark.parametrize("script", sorted(SCRIPTS), ids=lambda path: path.name)
def test_script_passes_shellcheck(script: Path) -> None:
    shellcheck = shutil.which("shellcheck")
    assert shellcheck, "shellcheck missing: run the tests with `uv run pytest` (it comes with shellcheck-py)"

    result = subprocess.run([shellcheck, "--shell=bash", str(script)], capture_output=True, text=True)

    assert result.returncode == 0, result.stdout
