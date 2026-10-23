# Coding Agent: Vendor-Agnostic 3-Tier Coding Assistant

A lightweight, vendor-agnostic coding agent that orchestrates a **3-tier model pipeline** (Architect $\rightarrow$ Implementer $\rightarrow$ Verifier) using your existing browser-authenticated subscriptions (**Claude Team** and **Gemini Pro**) and **OpenCode** with its own `opencode/` models (free tier by default; Zen/Go after `opencode auth login`). Each run works on its own local git branch and never pushes.

---

## Architecture Overview

```
                      ┌────────────────────────────────────────┐
                      │              User Prompt               │
                      └──────────────────┬─────────────────────┘
                                         │
                                         ▼
                 ┌──────────────────────────────────────────────────┐
                 │   Tier 1: Architect (interactive chat → plan)    │
                 │  - Evaluates repository context & coding rules   │
                 │  - Runner: Claude Code, Gemini CLI, or OpenCode  │
                 │  - Model: High-reasoning (e.g. Claude Sonnet/Opus│
                 │    or Gemini Pro)                                │
                 └───────────────────────┬──────────────────────────┘
                                         │
                                         ▼
                 ┌──────────────────────────────────────────────────┐
                 │     Plan Gate (after the architect chat)         │
                 │  [1] Approve  [2] Back to architect  [3] Abort   │
                 └───────────────────────┬──────────────────────────┘
                                         │ (Approved)
                                         ▼
                 ┌──────────────────────────────────────────────────┐
                 │          Tier 2: Implementer (Code Edits)        │
                 │  - Executes approved plan & modifies files       │
                 │  - Runner: OpenCode, Gemini, or Claude           │
                 │  - Model: Medium/cost-efficient (e.g. DeepSeek,  │
                 │    Qwen Coder, or Flash)                         │
                 └───────────────────────┬──────────────────────────┘
                                         │
                                         ▼
                 ┌──────────────────────────────────────────────────┐
                 │     Tier 3: Deterministic & Verifier ($0 base)   │
                 │  - Runs pytest / gradle / ruff / git diff        │
                 │  - If clean: finishes without LLM call ($0)      │
                 │  - If tests fail: invokes cheap Verifier model   │
                 │    to diagnose failures and apply surgical patch │
                 └───────────────────────┬──────────────────────────┘
                                         │
                                         ▼
                      ┌────────────────────────────────────────┐
                      │   Commit to local branch (no push)     │
                      └────────────────────────────────────────┘
```

---

## Prerequisites & Provider Authentication

This agent executes local CLI tools headlessly to utilize your authenticated accounts:

### 1. Claude Code (Team Subscription)
* **Auth**: Uses your active browser OAuth session in `~/.claude/`.
* **Binary**: `/home/juanjo/.local/bin/claude` (Installed and verified).
* No Anthropic API secret key required.

### 2. Gemini CLI (Gemini Pro Subscription)
* **Auth**: Uses your browser OAuth session.
* **Binary**: `/usr/bin/gemini` (`@google/gemini-cli`, installed and verified).
* Supports headless execution via `-p` with `--approval-mode=plan` (read-only) and `--approval-mode=auto_edit` (edits only, no shell).

### 3. OpenCode Setup Guide
OpenCode is an open-source terminal coding agent supporting 75+ providers (local models, DeepSeek, OpenAI, Groq, Ollama, etc.).

**Install (Arch/CachyOS, official repo):**
```bash
sudo pacman -S opencode
```
Models come from OpenCode's own provider as `opencode/<model>`; list them with `opencode models opencode`. Without login only the free models are listed; paid Zen/Go models appear after `opencode auth login`.

**Verify installation:**
```bash
which opencode
coding-agent check
```

---

## Configuration & Model Selection

All profiles and models are 100% customizable. You are not locked into hardcoded or obsolete model lists.

### File: `config/config.yaml`

```yaml
active_profile: balanced

# Predefined model and runner combinations
# Claude and Gemini take their CLI aliases (opus/sonnet/haiku, pro/flash/flash-lite), which follow
# new releases; OpenCode takes opencode/<model> (free Zen models) or opencode-go/<model> (OpenCode Go plan);
# list them with `opencode models opencode` / `opencode models opencode-go` after `opencode auth login`.
profiles:
  balanced:
    description: "High-reasoning architect (Claude), efficient implementer (OpenCode), fast verifier (Gemini)"
    architect:
      runner: claude
      model: "opus"
    implementer:
      runner: opencode
      model: "opencode/deepseek-v4.1-flash-free"
    verifier:
      runner: gemini
      model: "flash"

  cost_saver:
    description: "Gemini Pro for architecture, OpenCode for cheap implementation, Gemini Flash-Lite for verification"
    architect:
      runner: gemini
      model: "pro"
    implementer:
      runner: opencode
      model: "opencode/glm-5.3-flash-free"
    verifier:
      runner: gemini
      model: "flash-lite"

  max_quality:
    description: "Claude Code throughout architecture and implementation, Gemini for independent verification"
    architect:
      runner: claude
      model: "opus"
    implementer:
      runner: claude
      model: "sonnet"
    verifier:
      runner: gemini
      model: "flash"

  opencode_only:
    description: "Everything on the OpenCode Go plan: Gemini 4 Argon plans, DeepSeek implements, MiMo verifies"
    # Needs `opencode auth login` with OpenCode Go. Argon allows ~220 requests / 5h on Go (880 on Go Plus);
    # an interactive planning chat spends one per model turn and tool call.
    architect:
      runner: opencode
      model: "opencode-go/gemini-4-argon-contributor"
    implementer:
      runner: opencode
      model: "opencode-go/deepseek-v4.1-flash"
    verifier:
      runner: opencode
      model: "opencode-go/mimo-v2.6-flash"
```

---

## Usage Guide

### 1. Diagnostic Check
Check which runner binaries and credentials are ready on your machine:
```bash
python3 -m src.cli check
```

### 2. View Active Configuration & Models
```bash
python3 -m src.cli config
python3 -m src.cli config --profile cost_saver
```

### 3. Run Full Coding Cycle (Plan Together $\rightarrow$ Implement $\rightarrow$ Verify)
By default planning is a conversation: the architect's own chat UI (Claude Code, Gemini CLI or the OpenCode TUI) opens in your terminal with the brief already sent.
```bash
python3 -m src.cli run --task-file task.md --cwd ~/IdeaProjects/shared-ledger
python3 -m src.cli run "Create a new position validator module" --cwd ~/IdeaProjects/shared-ledger
```
1. Discuss with the architect as long as you like: push back, point it at files, ask for alternatives.
2. When you agree, it writes the plan to `~/.cache/coding-agent/plans/<repo>-<branch>.md` and you exit the chat (`/exit` or Ctrl+D).
3. The plan is shown with three options: **approve & implement**, **back to the architect** (a new chat that revises the saved plan), or **abort** (the branch is removed).
4. The architect chats inside the throwaway worktree, and any file edits it makes there are dropped before the implementer starts, so only the implementer changes code.

Use `--task-file` for long briefs instead of quoting them on the command line.

### 4. Plan Only
Same conversation, without implementing; the agreed plan stays in `~/.cache/coding-agent/plans/`:
```bash
python3 -m src.cli plan --task-file task.md --cwd ~/PycharmProjects/crypto-ambush
```

### Headless Mode (Scripts)
`--headless` on `run` or `plan` skips the chat: the architect answers once and you approve, refine (one line of feedback) or abort at a terminal prompt. With `plan_first_approval: false` in the config it runs without any input.
```bash
python3 -m src.cli run --headless "Add audit logging" --cwd ~/IdeaProjects/tradelog
```

### 5. Runtime CLI Model & Runner Overrides
You can change models and runners on the fly for any tier:
```bash
python3 -m src.cli run "Implement audit logging" \
  --arch-runner claude --arch opus \
  --impl-runner opencode --impl opencode/deepseek-v4.1-flash-free \
  --verify-runner gemini --verify flash \
  --cwd /path/to/project
```

---

## Where the Work Goes: a Local Branch, Never Pushed

`run` never touches your checkout. It:

1. Creates (before the architect chat, or after approval with `--headless`) a local branch `coding-agent/<task-slug>-<timestamp>-<hex>` from `HEAD` and checks it out in a git worktree under `~/.cache/coding-agent/worktrees/` (uncommitted changes in your checkout are not included — you get a warning).
2. Runs the implementer and verifier there. Every agent and check process gets a git config override that rewrites push URLs to an unreachable host, so nothing can push; fetch still works.
3. Runs the project's own checks — root and direct subfolders, so `backend/` + `frontend/` repos are covered: `uv run pytest` / `ruff check` / `ruff format --check` / `mypy` (only the tools the `pyproject.toml` declares), `npm ci` then `npx tsc -b` / `npm test`, `./gradlew test`. On failure the verifier repairs and the checks re-run, up to `max_verifier_iterations`.
4. Commits the result to the branch (`--no-verify`; the message says whether checks passed), removes the worktree, and prints the diff stat. An empty run deletes its branch. The exit code is non-zero unless the checks pass.

Review and merge it yourself: `git diff main...coding-agent/<...>`, then `git merge` or `git branch -D`.

---

## Coding Rules & Standards

The agent enforces your established clean code standards ([`config/coding-rules.md`](config/coding-rules.md)):

* **Safe Testing Machine**: Operates safely with awareness of local test containers vs deploy hosts.
* **Package by Feature**: Organizes files by domain/feature (`position/`, `identity/`, `sync/`), never by layer (`services/`, `utils/`).
* **Clean Code**: Single-purpose units, self-describing names, guard clauses, immutability by default, no dead code.
* **Design Patterns**: Composition over inheritance, strategy patterns, explicit DTO boundaries, centralized exception handling.
* **Minimal Docstrings**: Single-line docstrings only when the *why* is non-obvious; no redundant parameter boilerplate.

---

## Running Unit Tests

```bash
python3 -m unittest discover -s tests -p "test_*.py"
```

`tests/test_workflow.py` drives the real CLI end to end against a throwaway git repo, with `claude`/`gemini`/`opencode` replaced by `tests/fake_agent.py` — no model is ever called.
