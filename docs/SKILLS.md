# Skills Reference

Skills are invoked as slash commands (e.g. `/brainstorming`). All available skills are listed in the system prompt at conversation start.

## Development workflow skills

| Skill | Trigger | Purpose |
|---|---|---|
| `/brainstorming` | New feature without prior design | Explore requirements and design options before any code |
| `/writing-plans` | After brainstorming; before multi-step impl | Produce a step-by-step implementation plan |
| `/using-git-worktrees` | Every feature branch | Creates isolated worktree + uv sync + .env copy; dev server on port 8001 |
| `/test-driven-development` | Before writing implementation code | Write failing tests first, then implement |
| `/systematic-debugging` | Any bug or unexpected test failure | Structured root-cause analysis before proposing fixes |
| `/verification-before-completion` | Before claiming done or opening a PR | Run verification commands and confirm output; evidence before assertions |
| `/shipping-work-claude` | Work is done and merged | Commit, push, close issues, post GH summary |

## Review skills

| Skill | Trigger | Purpose |
|---|---|---|
| `/reviewing-code-claude` | "CR" or "code review" | Structured tiered findings report; implements approved fixes |
| `/reviewing-architecture-claude` | "AR" or "architecture review" | High-level structural health, design principles, maintainability |
| `/security-review` | Security audit of pending branch changes | OWASP-oriented findings on current branch diff |
| `/review` | Review a pull request | PR-level review |

## Agent orchestration skills

| Skill | Trigger | Purpose |
|---|---|---|
| `/dispatching-parallel-agents` | 2+ independent tasks | Fan out to parallel agents with no shared state |
| `/subagent-driven-development` | Executing an implementation plan | Run independent plan steps via concurrent agents |
| `/orchestrating-issue-backlog-claude` | Issue triage/batch execution | Prioritize backlog, design parallel-safe execution plan |
| `/schedule` | Recurring or one-time future task | Create background agents on a cron schedule |
| `/loop` | Recurring interval task in session | Run a prompt or command on a repeating interval |

## Tooling and config skills

| Skill | Trigger | Purpose |
|---|---|---|
| `/update-config` | "From now on when X", hooks, permissions, env vars | Modifies `settings.json` / `settings.local.json` |
| `/keybindings-help` | Rebind keys, add chord shortcuts | Modifies `~/.claude/keybindings.json` |
| `/fewer-permission-prompts` | Too many permission prompts | Scans transcripts and adds an allowlist to `.claude/settings.json` |
| `/simplify` | After implementing a feature | Reviews changed code for reuse, quality, efficiency |
| `/writing-skills` | Create or edit a skill | Authors new skills and verifies before deployment |
| `/managing-skills-claude` | Add/update external skill repos | git submodule + symlink pattern for skills-vendor/ |

## Project-specific skills

| Skill | Trigger | Purpose |
|---|---|---|
| `/train-model` | "train model", "retrain usaddress", "fix parsing" | Interactive 7-step CRF model training pipeline |
| `/init` | New project without CLAUDE.md | Initialize CLAUDE.md with codebase documentation |
| `/claude-api` | Claude API / Anthropic SDK work | Build, debug, optimize; handles prompt caching and model migration |

## SocratiCode skills

| Skill | Purpose |
|---|---|
| `socraticode:codebase-exploration` | Semantic search, dependency graphs, architecture understanding |
| `socraticode:codebase-management` | Index management, health checks, file watching, context artifacts |

### When to use each SocratiCode tool

**Principle: search before reading** — leverage the semantic index rather than consuming raw file content.

| Objective | Tool |
|---|---|
| Understand codebase purpose or feature location | `codebase_search` with broad queries |
| Locate specific functions, constants, or types | `codebase_search` with exact names |
| Find error messages, logs, or regex patterns | grep / ripgrep |
| View file imports and dependents | `codebase_graph_query` |
| Assess impact before code modifications | `codebase_graph_query` |
| Identify breaking changes from a modification | `codebase_impact target=X` |
| Trace execution from entry points | `codebase_flow entrypoint=X` |
| Discover project entry points | `codebase_flow` (no arguments) |
| Analyze callers and callees for a function | `codebase_symbol name=X` |
| List symbols within files | `codebase_symbols file=path` |
| Search symbols project-wide | `codebase_symbols query=X` |
| Detect architectural issues | `codebase_graph_circular`, `codebase_graph_stats` |
| Visualize module structure | `codebase_graph_visualize` |
| Verify index currency | `codebase_status` |
| Explore available project knowledge | `codebase_context` |
| Locate schemas, endpoints, or configs | `codebase_context_search` |
