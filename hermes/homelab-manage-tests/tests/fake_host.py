"""Runs the real homelab-manage.sh in a bubblewrap sandbox whose /usr/local/bin holds fake commands.

The wrapper pins PATH with /usr/local/bin first, so the fakes shadow docker, curl, runuser… without
any test hook in the script itself. /etc/homelab-manage and /run are private to the sandbox and it has
no network.
"""

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from fake_command import Json

HERMES_DIR = Path(__file__).resolve().parents[2]
WRAPPER = HERMES_DIR / "homelab-manage.sh"
FAKE_COMMAND = Path(__file__).with_name("fake_command.py")
FAKED_COMMANDS = ("docker", "curl", "runuser", "smartctl", "systemctl", "journalctl", "logger")


@dataclass(frozen=True)
class Container:
    service: str
    name: str
    state: str = "running"
    health: str = ""
    exit_code: int = 0


@dataclass(frozen=True)
class Result:
    returncode: int
    stdout: str
    stderr: str


def _etc_binds() -> list[str]:
    # /etc rebuilt entry by entry on a tmpfs, so /etc/homelab-manage can be mounted into it.
    binds = ["--tmpfs", "/etc"]
    for entry in sorted(Path("/etc").iterdir()):
        if entry.is_symlink():
            binds += ["--symlink", str(entry.readlink()), str(entry)]
        else:
            binds += ["--ro-bind-try", str(entry), str(entry)]
    return binds


@dataclass
class FakeHost:
    root: Path
    world: dict[str, Json] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.bin = self.root / "bin"
        self.etc = self.root / "etc-homelab-manage"
        self.bin.mkdir()
        self.etc.mkdir()
        shutil.copy(WRAPPER, self.bin / "homelab-manage.sh")
        shutil.copy(FAKE_COMMAND, self.bin / "fake_command.py")
        (self.bin / "fake_command.py").chmod(0o755)
        for command in FAKED_COMMANDS:
            (self.bin / command).symlink_to("fake_command.py")

    def add_project(self, name: str, working_dir: Path) -> None:
        projects = self.world.setdefault("projects", {})
        assert isinstance(projects, dict)
        projects[name] = {
            "com.docker.compose.project.working_dir": str(working_dir),
            "com.docker.compose.project.config_files": str(working_dir / "docker-compose.yml"),
        }

    def compose_ps_returns(self, *snapshots: list[Container]) -> None:
        """Each `compose ps` call gets the next snapshot; the last one repeats."""
        self.world["compose_ps"] = [[asdict(container) for container in snapshot] for snapshot in snapshots]

    def mount(self, container: str, destination: str, source: Path) -> None:
        mounts = self.world.setdefault("mounts", {})
        assert isinstance(mounts, dict)
        container_mounts = mounts.setdefault(container, {})
        assert isinstance(container_mounts, dict)
        container_mounts[destination] = str(source)

    def respond(self, url_prefix: str, body: Json) -> None:
        http = self.world.setdefault("http", {})
        assert isinstance(http, dict)
        http[url_prefix] = body

    def disks(self, scan: list[tuple[str, str]], reports: dict[str, Json]) -> None:
        self.world["smart"] = {
            "scan": {"devices": [{"name": name, "type": kind} for name, kind in scan]},
            "devices": reports,
        }

    def configure_home_assistant(self) -> None:
        (self.etc / "ha_auth_header").write_text("Authorization: Bearer test\n")

    def run(self, *args: str) -> Result:
        (self.root / "world.json").write_text(json.dumps(self.world))
        sandbox = [
            "bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/run",
            *_etc_binds(),
            "--bind", str(self.etc), "/etc/homelab-manage",
            "--bind", str(self.bin), "/usr/local/bin",
            "--bind", str(self.root), str(self.root),
            "--setenv", "FAKE_HOST_DIR", str(self.root),
            "--unshare-net", "--die-with-parent",
        ]  # fmt: skip
        completed = subprocess.run(
            [*sandbox, "bash", "/usr/local/bin/homelab-manage.sh", *args],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        return Result(completed.returncode, completed.stdout, completed.stderr)

    def calls(self, command: str) -> list[list[str]]:
        log = self.root / "calls.jsonl"
        if not log.exists():
            return []
        entries = [json.loads(line) for line in log.read_text().splitlines()]
        return [entry["args"] for entry in entries if entry["command"] == command]

    def compose_calls(self, subcommand: str) -> list[list[str]]:
        """Arguments after the subcommand, for each `docker compose <subcommand>` call."""
        found = []
        for args in self.calls("docker"):
            if args[0] == "compose" and subcommand in args:
                found.append(args[args.index(subcommand) + 1 :])
        return found
