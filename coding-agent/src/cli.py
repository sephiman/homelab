"""CLI entrypoint for the vendor-agnostic coding agent."""

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .config import build_agent_config, find_config_file
from .errors import AgentError
from .orchestrator import AgentOrchestrator
from .runners import get_runner

console = Console()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="coding-agent",
        description="Vendor-Agnostic 3-Tier Coding Agent (Claude, Gemini, OpenCode)",
    )

    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    # Command: run
    run_parser = subparsers.add_parser("run", help="Run full plan-first coding cycle")
    _add_shared_options(run_parser)
    _add_task_options(run_parser)

    # Command: plan
    plan_parser = subparsers.add_parser("plan", help="Generate architectural plan only without modifying code")
    _add_shared_options(plan_parser)
    _add_task_options(plan_parser)

    # Command: check
    subparsers.add_parser("check", help="Check system runners, binaries, and auth status")

    # Command: config
    cfg_parser = subparsers.add_parser("config", help="Show active configuration, profiles, and models")
    _add_shared_options(cfg_parser)

    return parser


def _add_task_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("task", nargs="*", help="Task description (or use --task-file for a long brief)")
    parser.add_argument("--task-file", help="Read the task from a text/Markdown file")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="One-shot architect with a terminal approval gate instead of a chat (for scripts)",
    )


def read_task(parser: argparse.ArgumentParser, args: argparse.Namespace) -> str:
    if args.task and args.task_file:
        parser.error("give the task inline or with --task-file, not both")
    if not args.task_file:
        task = " ".join(args.task)
    elif not Path(args.task_file).expanduser().is_file():
        parser.error(f"task file '{args.task_file}' does not exist")
    else:
        task = Path(args.task_file).expanduser().read_text(encoding="utf-8")
    if not task.strip():
        parser.error("a task is required: inline text or --task-file")
    return task


def _add_shared_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", "-p", help="Active model profile (e.g. balanced, cost_saver, max_quality)")
    parser.add_argument("--arch", help="Override Architect model name (e.g. opus)")
    parser.add_argument("--impl", help="Override Implementer model name (e.g. opencode/deepseek-v4.1-flash-free)")
    parser.add_argument("--verify", help="Override Verifier model name (e.g. flash)")
    parser.add_argument("--arch-runner", choices=["claude", "gemini", "opencode"], help="Override Architect runner")
    parser.add_argument("--impl-runner", choices=["claude", "gemini", "opencode"], help="Override Implementer runner")
    parser.add_argument("--verify-runner", choices=["claude", "gemini", "opencode"], help="Override Verifier runner")
    parser.add_argument("--cwd", default=".", help="Target working directory (default: current directory)")
    parser.add_argument("--rules", help="Path to custom coding rules markdown file")
    parser.add_argument("--config-file", "-c", help="Path to custom config.yaml")


def handle_check() -> None:
    """Verifies available runner binaries and authentication status."""
    console.rule("[bold cyan]System Runner Diagnostics[/bold cyan]")
    table = Table(title="Runner Availability")
    table.add_column("Runner", style="cyan", no_wrap=True)
    table.add_column("Binary Path", style="dim")
    table.add_column("Status", justify="center")
    table.add_column("Notes", style="white")

    runners = ["claude", "gemini", "opencode"]
    for r in runners:
        adapter = get_runner(r)
        avail = adapter.is_available()
        path = adapter.resolve_path() or "Not found"
        status = "[bold green]INSTALLED[/bold green]" if avail else "[bold yellow]MISSING[/bold yellow]"

        notes = ""
        if r == "claude":
            notes = "Uses team browser OAuth (~/.claude/)" if avail else "Install: npm i -g @anthropic-ai/claude-code"
        elif r == "gemini":
            notes = "Uses browser OAuth session" if avail else "Install: npm i -g @google/gemini-cli"
        elif r == "opencode":
            notes = "Models as opencode/<model>; list with: opencode models opencode" if avail else "Install: sudo pacman -S opencode"

        table.add_row(r, path, status, notes)

    console.print(table)
    console.print("\n[dim]Tip: You can use any installed runner as architect, implementer, or verifier anytime.[/dim]\n")


def handle_config_display(args: argparse.Namespace) -> None:
    """Prints current active profile configuration."""
    cfg = build_agent_config(
        custom_config_path=args.config_file,
        profile_override=args.profile,
        arch_model=args.arch,
        impl_model=args.impl,
        verify_model=args.verify,
        arch_runner=args.arch_runner,
        impl_runner=args.impl_runner,
        verify_runner=args.verify_runner,
        rules_override=args.rules,
    )
    cfg_file = find_config_file(args.config_file)

    console.rule("[bold cyan]Active Agent Configuration[/bold cyan]")
    console.print(f"[bold]Config File:[/bold] {cfg_file}")
    console.print(f"[bold]Active Profile:[/bold] [green]{cfg.active_profile}[/green]")
    console.print(f"[bold]Rules File:[/bold] {cfg.rules_file}\n")

    table = Table(title="3-Tier Model Pipeline")
    table.add_column("Tier", style="bold")
    table.add_column("Runner", style="cyan")
    table.add_column("Model Name", style="magenta")

    table.add_row("Architect (Plan)", cfg.architect.runner, cfg.architect.model or "(default)")
    table.add_row("Implementer (Code)", cfg.implementer.runner, cfg.implementer.model or "(default)")
    table.add_row("Verifier (Test/Fix)", cfg.verifier.runner, cfg.verifier.model or "(default)")

    console.print(table)


def main() -> None:
    try:
        run_cli()
    except AgentError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        sys.exit(2)


def run_cli() -> None:
    parser = build_parser()
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    args = parser.parse_args()

    if args.subcommand == "check":
        handle_check()
        return

    if args.subcommand == "config":
        handle_config_display(args)
        return

    target_cwd = Path(args.cwd).expanduser().resolve()
    if not target_cwd.exists() or not target_cwd.is_dir():
        console.print(f"[bold red]Error:[/bold red] Target directory '{target_cwd}' does not exist.")
        sys.exit(1)

    task_str = read_task(parser, args)
    cfg = build_agent_config(
        custom_config_path=args.config_file,
        profile_override=args.profile,
        arch_model=args.arch,
        impl_model=args.impl,
        verify_model=args.verify,
        arch_runner=args.arch_runner,
        impl_runner=args.impl_runner,
        verify_runner=args.verify_runner,
        rules_override=args.rules,
    )

    orchestrator = AgentOrchestrator(config=cfg, cwd=str(target_cwd))
    interactive = not args.headless

    if args.subcommand == "plan":
        plan = orchestrator.plan_interactively(task=task_str) if interactive else orchestrator.run_plan_only(task=task_str)
        sys.exit(0 if plan else 1)
    elif args.subcommand == "run":
        success = orchestrator.execute_workflow(task=task_str, interactive=interactive)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
