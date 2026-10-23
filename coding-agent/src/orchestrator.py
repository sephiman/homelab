"""Core multi-tier orchestrator managing plan, execution, and verification."""

from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt

from .checks import CheckCommand, CheckResult, detect_checks, run_check
from .config import AgentConfig
from .context import (
    build_context_bundle,
    format_architect_prompt,
    format_implementer_prompt,
    format_interactive_brief,
    format_verifier_prompt,
)
from .planning import plan_files_for, read_plan
from .runners import get_runner
from .workspace import (
    Workspace,
    branch_diff_stat,
    commit_and_close,
    discard_workspace,
    has_uncommitted_changes,
    open_workspace,
    reset_to_base,
    staged_diff,
)

console = Console()


class AgentOrchestrator:
    """Coordinates the 3-tier model execution pipeline."""

    def __init__(self, config: AgentConfig, cwd: str):
        self.config = config
        self.cwd = Path(cwd)
        self.context = build_context_bundle(cwd=cwd, rules_path=str(config.rules_file))

    def run_plan_only(self, task: str) -> str | None:
        """Runs only the architect tier, read-only, against the current checkout."""
        console.rule("[bold cyan]Architect Tier: Analyzing & Planning[/bold cyan]")
        console.print(
            f"[dim]Runner: [bold]{self.config.architect.runner}[/bold] | Model: [bold]{self.config.architect.model}[/bold][/dim]\n"
        )

        arch_runner = get_runner(self.config.architect.runner, self.config.runner_configs)
        if not arch_runner.is_available():
            console.print(
                f"[bold red]Error:[/bold red] Architect runner binary '{arch_runner.command}' not found on PATH."
            )
            return None

        prompt = format_architect_prompt(task=task, context=self.context)
        with console.status("[cyan]Architect model is generating execution plan...[/cyan]"):
            result = arch_runner.run(
                prompt=prompt,
                cwd=str(self.cwd),
                model=self.config.architect.model,
                read_only=True,
                timeout=self.config.runner_timeout_seconds,
            )

        if not result.success:
            console.print(f"[bold red]Architect phase failed:[/bold red]\n{result.stderr}")
            return None

        plan_content = result.stdout.strip()
        console.print(
            Panel(
                Markdown(plan_content),
                title="[bold green]Architect Plan[/bold green]",
                border_style="green",
            )
        )
        return plan_content

    def plan_interactively(self, task: str) -> str | None:
        """Chats with the architect in a throwaway worktree; the plan file outlives it."""
        workspace = self._open_workspace(task)
        try:
            return self._interactive_plan(task=task, workspace=workspace, accept_label="Accept plan")
        finally:
            discard_workspace(workspace)

    def execute_workflow(self, task: str, interactive: bool = True) -> bool:
        """Plans, then implements and verifies on a fresh local branch; True only when checks pass."""
        if interactive:
            workspace = self._open_workspace(task)
            try:
                plan = self._interactive_plan(task=task, workspace=workspace, accept_label="Approve & implement")
            except BaseException:
                discard_workspace(workspace)
                raise
            if plan is None:
                discard_workspace(workspace)
                console.print("[yellow]Execution aborted; the branch was removed.[/yellow]")
                return False
            if reset_to_base(workspace):
                console.print("[yellow]Dropped file edits the architect made; only the implementer changes code.[/yellow]")
            return self._implement_and_verify(task=task, plan=plan, workspace=workspace)

        headless_plan = self.run_plan_only(task=task)
        if not headless_plan:
            return False
        if self.config.plan_first_approval:
            headless_plan = self._approve_plan(task=task, plan=headless_plan)
            if headless_plan is None:
                console.print("[yellow]Execution aborted by user.[/yellow]")
                return False
        return self._implement_and_verify(task=task, plan=headless_plan, workspace=self._open_workspace(task))

    def _open_workspace(self, task: str) -> Workspace:
        workspace = open_workspace(self.cwd, task)
        console.print(f"[dim]Working on local branch [bold]{workspace.branch}[/bold] in {workspace.worktree}[/dim]\n")
        if has_uncommitted_changes(self.cwd):
            console.print("[yellow]Uncommitted changes in the checkout are not part of the agent's branch.[/yellow]")
        return workspace

    def _interactive_plan(self, task: str, workspace: Workspace, accept_label: str) -> str | None:
        """Hands the terminal to the architect's own chat UI until the user accepts the plan it wrote, or aborts."""
        arch_runner = get_runner(self.config.architect.runner, self.config.runner_configs)
        if not arch_runner.is_available():
            console.print(f"[bold red]Error:[/bold red] Architect runner binary '{arch_runner.command}' not found on PATH.")
            return None

        files = plan_files_for(workspace)
        previous_plan: str | None = None
        while True:
            brief = format_interactive_brief(task, self.context, files.plan, revising=previous_plan is not None)
            files.brief.write_text(brief, encoding="utf-8")
            console.rule("[bold cyan]Architect Tier: Interactive Planning[/bold cyan]")
            console.print(
                f"[dim]Runner: [bold]{self.config.architect.runner}[/bold] | Model: [bold]{self.config.architect.model}[/bold]\n"
                f"Agree on a plan in the chat; the architect saves it to {files.plan}. Exit the chat to continue.[/dim]\n"
            )
            arch_runner.interactive(
                message=f"Read the brief in {files.brief} and follow it.",
                cwd=str(workspace.work_dir),
                model=self.config.architect.model,
                readable_dirs=[str(files.folder)],
            )

            plan = read_plan(files)
            if plan:
                console.print(Panel(Markdown(plan), title="[bold green]Architect Plan[/bold green]", border_style="green"))
                console.print(f"[dim]Saved at {files.plan}[/dim]")
                if not self.config.plan_first_approval:
                    return plan
            else:
                console.print(f"[yellow]No plan was written to {files.plan}.[/yellow]")

            choice = self._ask_after_planning(accept_label if plan else None)
            if choice == "accept":
                return plan
            if choice == "abort":
                return None
            previous_plan = plan

    def _ask_after_planning(self, accept_label: str | None) -> str:
        options = {"2": ("back", "[bold blue]Back to the architect[/bold blue]"), "3": ("abort", "[bold red]Abort[/bold red]")}
        if accept_label:
            options = {"1": ("accept", f"[bold green]{accept_label}[/bold green]")} | options
        for key, (_, label) in options.items():
            console.print(f"[{key}] {label}")
        choice = Prompt.ask("Select an option", choices=list(options), default=next(iter(options)))
        return options[choice][0]

    def _implement_and_verify(self, task: str, plan: str, workspace: Workspace) -> bool:
        implemented = verified = False
        try:
            implemented = self._implement(task=task, plan=plan, workspace=workspace)
            verified = implemented and self._verify_and_repair(workspace)
        finally:
            self._finish(task=task, workspace=workspace, implemented=implemented, verified=verified)
        return verified

    def _approve_plan(self, task: str, plan: str) -> str | None:
        """Interactive gate before touching code; returns the approved plan, or None on abort."""
        while True:
            console.print("\n[bold yellow]Execution Gate: Review Plan[/bold yellow]")
            console.print("[1] [bold green]Approve & Execute Plan[/bold green]")
            console.print("[2] [bold blue]Provide Feedback to Refine Plan[/bold blue]")
            console.print("[3] [bold red]Abort[/bold red]")

            choice = Prompt.ask("Select an option", choices=["1", "2", "3"], default="1")
            if choice == "1":
                return plan
            if choice == "3":
                return None

            feedback = Prompt.ask("[bold cyan]Enter feedback for the architect[/bold cyan]")
            revised_task = f"{task}\n\n### Previous plan\n{plan}\n\n### User feedback on that plan\n{feedback}"
            plan = self.run_plan_only(task=revised_task) or plan

    def _implement(self, task: str, plan: str, workspace: Workspace) -> bool:
        console.rule("[bold magenta]Implementation Tier: Applying Code Changes[/bold magenta]")
        console.print(
            f"[dim]Runner: [bold]{self.config.implementer.runner}[/bold] | Model: [bold]{self.config.implementer.model}[/bold][/dim]\n"
        )

        impl_runner = get_runner(self.config.implementer.runner, self.config.runner_configs)
        if not impl_runner.is_available():
            console.print(
                f"[bold red]Error:[/bold red] Implementer runner '{impl_runner.command}' not found.\n"
                f"[yellow]{self._get_runner_install_hint(impl_runner.command)}[/yellow]"
            )
            return False

        impl_prompt = format_implementer_prompt(task=task, plan=plan, context=self.context)
        with console.status("[magenta]Implementer model is making code modifications...[/magenta]"):
            impl_result = impl_runner.run(
                prompt=impl_prompt,
                cwd=str(workspace.work_dir),
                model=self.config.implementer.model,
                read_only=False,
                timeout=self.config.runner_timeout_seconds,
            )

        if not impl_result.success:
            console.print(f"[bold red]Implementation phase error:[/bold red]\n{impl_result.stderr}")
            if impl_result.stdout:
                console.print(impl_result.stdout)
            return False

        console.print(f"[green]Implementation completed in {impl_result.duration_seconds}s.[/green]\n")
        return True

    def _verify_and_repair(self, workspace: Workspace) -> bool:
        """Runs the project's checks, letting the verifier repair failures up to max_verifier_iterations times."""
        console.rule("[bold blue]Verification Tier: Automated Checks[/bold blue]")
        if not self.config.auto_test_verification:
            console.print("[yellow]Automated checks disabled in config: changes are unverified.[/yellow]")
            return True

        check_plan = detect_checks(workspace.work_dir)
        if not check_plan.checks:
            console.print("[yellow]No checks detected for this project: changes are unverified.[/yellow]")
            return True

        if not self._run_setup(check_plan.setup, workspace):
            return False

        max_repairs = self.config.max_verifier_iterations
        for attempt in range(max_repairs + 1):
            failures = self._run_checks(check_plan.checks, workspace)
            if not failures:
                return True
            if attempt == max_repairs or not self._invoke_verifier_repair(failures, workspace):
                break

        console.print(f"[bold red]Checks still failing after {max_repairs} repair attempt(s).[/bold red]")
        return False

    def _run_setup(self, setup: tuple[CheckCommand, ...], workspace: Workspace) -> bool:
        for command in setup:
            console.print(f"[cyan]Preparing:[/cyan] [bold]{command.describe(workspace.work_dir)}[/bold]")
            result = run_check(command, timeout=self.config.runner_timeout_seconds)
            if not result.passed:
                console.print(f"[bold red]Setup failed, checks cannot run:[/bold red]\n[dim]{result.output[-2000:]}[/dim]")
                return False
        return True

    def _run_checks(self, checks: tuple[CheckCommand, ...], workspace: Workspace) -> list[CheckResult]:
        failures = []
        for command in checks:
            described = command.describe(workspace.work_dir)
            console.print(f"[cyan]Running check:[/cyan] [bold]{described}[/bold]")
            result = run_check(command, timeout=self.config.runner_timeout_seconds)
            if result.passed:
                console.print(f"[bold green]✔ Passed ($0 cost):[/bold green] {described}")
                continue
            console.print(f"[bold yellow]✘ Failed:[/bold yellow] {described}\n[dim]{result.output[-500:]}[/dim]")
            failures.append(result)
        return failures

    def _invoke_verifier_repair(self, failures: list[CheckResult], workspace: Workspace) -> bool:
        """Calls the cheap verifier model to patch check failures; False when it could not run."""
        verify_runner = get_runner(self.config.verifier.runner, self.config.runner_configs)
        if not verify_runner.is_available():
            console.print(
                f"[yellow]Verifier runner '{verify_runner.command}' not available to auto-patch. Manual inspection needed.[/yellow]"
            )
            return False

        console.print(
            f"[dim]Invoking Verifier ({self.config.verifier.runner} / {self.config.verifier.model}) to repair failures...[/dim]"
        )
        prompt = format_verifier_prompt(failures=failures, git_diff=staged_diff(workspace), root=workspace.work_dir)
        with console.status("[blue]Verifier is applying repair patch...[/blue]"):
            res = verify_runner.run(
                prompt=prompt,
                cwd=str(workspace.work_dir),
                model=self.config.verifier.model,
                read_only=False,
                timeout=self.config.runner_timeout_seconds,
            )

        if not res.success:
            console.print(f"[red]Verifier failed to patch:[/red] {res.stderr}")
            return False
        console.print(f"[green]Verifier repair patch applied ({res.duration_seconds}s).[/green]")
        return True

    def _finish(self, task: str, workspace: Workspace, implemented: bool, verified: bool) -> None:
        """Commits the outcome onto the local branch (never pushed) and removes the worktree."""
        console.rule("[bold green]Branch Summary[/bold green]")
        if not implemented:
            outcome = "Implementation incomplete."
        elif verified:
            outcome = "Checks passed."
        else:
            outcome = "Checks failing: needs review."
        title = task.strip().splitlines()[0][:72]

        if not commit_and_close(workspace, f"coding-agent: {title}\n\n{outcome}"):
            console.print("[dim]No changes were made; the branch was removed.[/dim]")
            return

        console.print(f"{outcome} Committed to local branch [bold]{workspace.branch}[/bold] (not pushed).")
        console.print(branch_diff_stat(workspace))
        console.print(f"[dim]Review with: git diff {workspace.base_commit[:12]}...{workspace.branch}[/dim]")

    def _get_runner_install_hint(self, command: str) -> str:
        if command == "opencode":
            return (
                "Install OpenCode: sudo pacman -S opencode\n"
                "Or run with Claude as implementer: --impl-runner claude"
            )
        elif command == "claude":
            return "Install Claude Code: npm install -g @anthropic-ai/claude-code"
        elif command == "gemini":
            return "Install Gemini CLI: npm install -g @google/gemini-cli"
        return f"Install '{command}' and ensure it is available on system PATH."
