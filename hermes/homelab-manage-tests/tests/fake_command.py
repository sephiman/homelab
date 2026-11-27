#!/usr/bin/python3 -I
"""Stands in for docker, curl, runuser, smartctl, systemctl and journalctl inside the test sandbox.

Answers come from world.json in $FAKE_HOST_DIR; every call except runuser is appended to calls.jsonl.
"""

import json
import os
import re
import sys
from pathlib import Path

type Json = dict[str, Json] | list[Json] | str | int | float | bool | None

COMPOSE_GLOBAL_OPTIONS = {"--project-name", "--file"}


def _root() -> Path:
    return Path(os.environ["FAKE_HOST_DIR"])


def _as_dict(value: Json) -> dict[str, Json]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Json) -> list[Json]:
    return value if isinstance(value, list) else []


def _print_json(value: Json) -> None:
    print(value if isinstance(value, str) else json.dumps(value))


def _option_value(args: list[str], option: str) -> str:
    return args[args.index(option) + 1] if option in args else ""


def _next_compose_ps(world: dict[str, Json]) -> list[Json]:
    snapshots = _as_list(world.get("compose_ps"))
    if not snapshots:
        return []
    counter = _root() / "compose_ps_calls"
    calls = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(calls + 1))
    return _as_list(snapshots[min(calls, len(snapshots) - 1)])


def _compose(world: dict[str, Json], args: list[str]) -> int:
    index = 0
    while index < len(args) and args[index] in COMPOSE_GLOBAL_OPTIONS:
        index += 2
    subcommand, rest = args[index], args[index + 1 :]
    if subcommand == "ps":
        format_template = _option_value(rest, "--format")
        services = [arg for arg in rest if not arg.startswith("--") and arg != format_template]
        for row in map(_as_dict, _next_compose_ps(world)):
            if not services or row["service"] in services:
                print("\t".join(str(row[key]) for key in ("name", "state", "health", "exit_code")))
    exit_code = _as_dict(world.get("compose_exit")).get(subcommand, 0)
    return exit_code if isinstance(exit_code, int) else 1


def docker(world: dict[str, Json], args: list[str]) -> int:
    if args[0] == "compose":
        return _compose(world, args[1:])
    if args[0] == "ps":
        label = re.search(r'\.Label "([^"]+)"', _option_value(args, "--format"))
        project = _option_value(args, "--filter").removeprefix("label=com.docker.compose.project=")
        details = _as_dict(_as_dict(world.get("projects")).get(project))
        if label and details:
            _print_json(details.get(label.group(1), ""))
        return 0
    if args[0] == "inspect":
        destination = re.search(r'eq \.Destination "([^"]+)"', _option_value(args, "--format"))
        if destination:
            mounts = _as_dict(_as_dict(world.get("mounts")).get(args[-1]))
            _print_json(mounts.get(destination.group(1), ""))
    return 0


def curl(world: dict[str, Json], args: list[str]) -> int:
    url = next(arg for arg in args if arg.startswith("http"))
    for prefix, body in _as_dict(world.get("http")).items():
        if url.startswith(prefix):
            _print_json(body)
    return 0


def smartctl(world: dict[str, Json], args: list[str]) -> int:
    smart = _as_dict(world.get("smart"))
    if "--scan" in args:
        _print_json(smart.get("scan"))
        return 0
    report = _as_dict(smart.get("devices")).get(args[-1])
    if report is None:
        return 2
    _print_json(report)
    return 4  # a warning bit, as real smartctl sets for many healthy disks


HANDLERS = {"docker": docker, "curl": curl, "smartctl": smartctl}


def main() -> int:
    command, args = Path(sys.argv[0]).name, sys.argv[1:]
    if command == "runuser":
        user_command = args[args.index("--") + 1 :]
        os.execvp(user_command[0], user_command)
    with (_root() / "calls.jsonl").open("a") as calls:
        calls.write(json.dumps({"command": command, "args": args}) + "\n")
    handler = HANDLERS.get(command)
    world = _as_dict(json.loads((_root() / "world.json").read_text()))
    return handler(world, args) if handler else 0


if __name__ == "__main__":
    sys.exit(main())
