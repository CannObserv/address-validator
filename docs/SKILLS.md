# Skills Reference

Skills are invoked as slash commands (e.g. `/brainstorming`). All available skills are listed in the system prompt at conversation start.

## Development workflow skills

| Skill | Trigger | Purpose |
|---|---|---|
| `/brainstorming` | New feature without prior design | Explore requirements and design options before any code. Vendored obra-superpowers, unforked; project rules in `AGENTS.md` → Design workflow, detail in [Brainstorming on this VM](#brainstorming-on-this-vm) |
| `/writing-plans` | After brainstorming; before multi-step impl | Produce a step-by-step implementation plan |
| `/using-git-worktrees` | Every feature branch | Parallel branch checkouts via git worktree — creation, lifecycle, and cleanup; consult [DEPLOYMENT.md](DEPLOYMENT.md) for the path convention and dev-server command, and `AGENTS.md` → Infrastructure for port assignments |
| `/test-driven-development` | Before writing implementation code | Write failing tests first, then implement |
| `/systematic-debugging` | Any bug or unexpected test failure | Structured root-cause analysis before proposing fixes |
| `/verification-before-completion` | Before claiming done or opening a PR | Run verification commands and confirm output; evidence before assertions |
| `/shipping-work-python-fastapi` | Work is done and merged | Commit, push, close issues, post GH summary. Its Step 1.5 doc-drift gate reads `.skills/doc-sensitive-paths` — a project-tailored list that **replaces** the skill's built-in defaults (which assume a `src/api|models|core/` layout and match almost nothing here). Entries match whole path segments at any depth; every entry must match a tracked file, enforced by `tests/unit/test_doc_sensitive_paths.py`. Re-verify that test after any layout change ([GH #208](https://github.com/CannObserv/address-validator/issues/208)). The advice printed on a hit comes from `.skills/doc-sections`, also tailored — one doc per line, naming the headings to spot-check and the entries routed there; `tests/unit/test_doc_sections.py` enforces that each doc and quoted heading exists and every path-list entry is routed. Re-verify after any doc rename, heading edit or path-list change ([GH #210](https://github.com/CannObserv/address-validator/issues/210)) |

## Brainstorming on this VM

`skills/brainstorming` is a symlink to `skills-vendor/obra-superpowers/skills/brainstorming` — upstream's text, unforked, since #334 (the v6.4.2 fork it replaced fell behind v7.0.0). Our preferences live once, in `AGENTS.md` → Design workflow, as rules that each quote the upstream instruction they replace. A rule that only says "our conventions win" loses to a skill read later, at invocation.

**After a `skills-vendor/obra-superpowers` bump**, `tests/unit/test_brainstorming_rules.py` fails if a quoted phrase is gone from the new `SKILL.md`: upstream reworded it, so requote the rule (and the test's `QUOTED_UPSTREAM`). The doctor no longer reports drift for this skill, because there is no override left to compare; the test also fails if `skills/brainstorming` stops being a symlink.

### Preferences, re-read against v7.0.1 (2026-10-22)

| Preference (ours) | v7.0.1 says | Carried as |
|---|---|---|
| Explicit approval words at every stage; a reply approves only the stage presented | "Approval covers what you showed them" | **Approval** rule |
| Design doc in `docs/plans/` | `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md` | **Doc** rule |
| Commit the doc as `[docs]:` / `#<n> [docs]:` | "committed" | Pointer to `AGENTS.md` → Commit convention |
| `gh issue create` once the doc is approved | No issue step | **Issue** rule |
| `/writing-plans` optional; the user's pick is its own approval stage | "superpowers:writing-plans and no other skill" (HARD-GATE) and "Invoke the superpowers:writing-plans skill" (the must-in-order list, new in v7.0.1) | **Then the user picks** rule. This repo vendors gregoryfoster/skills' `writing-plans`: no `superpowers:` namespace, no execution-method stage |
| New endpoints and schema changes get a written design | Generic "Size the Work" table: a small change gets a short design doc, a project "the full written design" | **Sizing** rule: they alter `models.py` or a drift-tested source of truth |
| Companion reachable through the exe.dev proxy | Local browser, random high port, bare `scripts/` path | **Visual companion** rule → the invocation below |
| Builder check (`builder-check-prompt.md`, new in v7) | A fresh subagent lists a builder's open questions before the doc is handed over | **Adopted** as upstream wrote it; no rule needed |
| Approaches before the doc (new in v7.0.1) | "Lay out the approaches" in a message of its own; the doc records them; even a small change gets a short design doc | **Adopted**; the **Doc** rule places the short doc too |
| Repo-specific "too simple" examples; omitted digraph, task-per-item, `elements-of-style` | Generic examples; none of the three appears | Dropped |

### Visual companion

**This VM is remote: the user's browser is not on this machine.** The companion's default invocation binds `127.0.0.1` on a random ephemeral port, which their browser can never reach. Launch it exactly as below or the tab will not load.

```bash
# From the project root. Port MUST be 3000-9999 — the exe.dev proxy forwards
# only that range, and the script's default random port falls outside it.
# BRAINSTORM_TOKEN is load-bearing: without it a port collision falls back to a
# random 49152-65534 port and still prints a success line (see below).
BRAINSTORM_PORT=3900 BRAINSTORM_TOKEN=$(openssl rand -hex 32) \
  bash skills/brainstorming/scripts/start-server.sh \
  --project-dir "$PWD" --host 0.0.0.0 --url-host address-validator.exe.xyz
```

- **Confirm `"port":3900` in the returned JSON before relaying the URL.** On `EADDRINUSE` the server falls back to a random 49152–65534 port — outside the proxy range, so the link can never load — and prints an ordinary `server-started` line either way (measured: a collision on 3900 bound 54972 and reported success). `BRAINSTORM_TOKEN` makes it refuse that fallback and exit 1, which is why the variable is not optional. The wrapper reports the refusal as `{"error": "Server failed to start within 5 seconds"}`, not as a port message — read that as "the port is taken", pick another free one in 3000–9999, and never drop the variable to get past it.
- **Relay the URL as `https://`.** The script prints `http://address-validator.exe.xyz:3900/?key=…`; the proxy terminates TLS, so the user needs the same URL with `https://`. Swap the scheme by hand before sending it.
- **Do not use `--open`** — it would open a browser on the VM, not the user's machine. Send them the link instead.
- **`--url-host` is this VM's name**, as in the dev-server URL `AGENTS.md` documents (`https://address-validator.exe.xyz:8001/`). Confirm it there rather than trusting the literal above if the VM may have been renamed.
- **Pick a free port.** 8000 is the production service, 8001 the dev server, 4400 libpostal, 3900 the companion ([DEPLOYMENT.md](DEPLOYMENT.md) → Port map).
- **Invoke by the full path** shown above. The companion guide writes `bash scripts/start-server.sh` with a bare `scripts/` path, which does not resolve from the project root ([gregoryfoster/skills#63](https://github.com/gregoryfoster/skills/issues/63)).
- Session content and state land in `.superpowers/brainstorm/` (gitignored).

`visual-companion.md` (upstream) covers screen-writing, event polling and lifecycle — read it before the first screen. Its examples use the bare `scripts/` path and `--open` and assume a local browser, so the flags above override it. Invocation unchanged in v7.0.0 and v7.0.1 (v7.0.1 changed only `SKILL.md`): `start-server.sh` and `visual-companion.md` are byte-identical to v6.4.2, and `server.cjs` changed one line (a `String.replace` callback, #334).

## Review skills

| Skill | Trigger | Purpose |
|---|---|---|
| `/reviewing-code-python-fastapi` | "CR" or "code review" | Structured tiered findings report; implements approved fixes |
| `/reviewing-architecture` | "AR" or "architecture review" | High-level structural health, design principles, maintainability |
| `/enforcing-architecture` | "add a fitness function", "enforce this contract", "lock this rule" | Graduates an accepted AR finding into an executable check (import-linter / module-size gate / OpenAPI drift guard). Delegated to by `/reviewing-architecture` on a `N: fix + fitness` or bare `N: fitness` directive |
| `/security-review` | Security audit of pending branch changes | OWASP-oriented findings on current branch diff |
| `/auditing-ci-cost` | "audit CI", "CI cost", "Actions spend" | Per-job billed-minute census of GitHub Actions spend, incident days separated from the baseline, then a prescription that branches on job COUNT vs job DURATION. Hands an accepted path filter to `/enforcing-architecture`. Needs `gh` + `jq` |
| `/review` | Review a pull request | PR-level review |

## Agent orchestration skills

| Skill | Trigger | Purpose |
|---|---|---|
| `/dispatching-parallel-agents` | 2+ independent tasks | Fan out to parallel agents with no shared state |
| `/subagent-driven-development` | Executing an implementation plan | Run independent plan steps via concurrent agents |
| `/orchestrating-issue-backlog` | Issue triage/batch execution | Prioritize backlog, design parallel-safe execution plan |
| `/using-mayfly-chat` | "mayfly", "open a channel", "join the channel", "chat with <repo>", "agent chat", or a relayed channel URL | Live agent-to-agent exchange over an encrypted, ephemeral [Mayfly Chat](https://mayfly.chat) channel; outcomes land on issues before goodbye. The URL is read, write and delete access: **never commit it** — not in an issue, commit, doc or plan. This repo has no structural test for it, so run the leak check in the skill's `references/security.md` before committing anything a session produced. Needs Node.js 18+ |
| `/coordinating-epics` | "coordinate the epic", "check-in", "I finished #N", "next issue" | Serial, operator-paced epic burn-down: verifies each child from evidence, hands off the next with a brief. Every epic body edit goes through `scripts/epic-edit.sh` (baseline-guarded, compare-and-swap, lint, read-back); each check-in starts with `scripts/scan.sh` (read-only). This clone has no `origin/HEAD`, so pass `scan.sh --ref origin/main` — without it the commit cursor is skipped with only a warning (exit 0). Adopted for [#272](https://github.com/CannObserv/address-validator/issues/272) by its coordinator thread only ([#312](https://github.com/CannObserv/address-validator/issues/312)). Needs `gh` + python3 3.9+ |
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
| `/managing-skills` | Add/update external skill repos | git submodule + symlink pattern for skills-vendor/ |
| `/adopting-dependabot` | "adopt Dependabot", "is Dependabot working", "land the Dependabot PRs" | Maps CI credentials and what the bot can move, writes `.github/dependabot.yml` with a guard test, the merge path, and `check-dependabot.sh` (read-only: config, PR-limit headroom, update runs, graph vs lockfiles, alerts, security updates). Repo settings are the owner's to switch on. Here: config, guards and the pre-ship merge path in [DEPENDENCY-POLICY.md](DEPENDENCY-POLICY.md#dependabot-361) |
| `/init-socraticode` | Re-audit the SocratiCode install; after a SocratiCode upgrade | Idempotent audit re-run — re-validates the manifest, re-measures graph yield, repairs hooks and the policy block. Detail: [SOCRATICODE.md](SOCRATICODE.md) |
| `/curating-context` | "curate context", "trim AGENTS.md"; weekly maintenance | Curates AGENTS.md + its doc tree against the token budget (`.skills/context-budget`, 6,000). Its `context-budget-guard.sh` PostToolUse hook warns on every edit that pushes the file over |

## Host operations skills

| Skill | Trigger | Purpose |
|---|---|---|
| `/patching-hosts` | "patch the host", "OS updates", "security updates", "needrestart" | Probes pending OS/package updates by class against the posture `.skills/patching-hosts` declares, then a gated, held-step apply with a recovery point, a detached reboot chain and post-boot checks. The knob and the first monthly run are [#311](https://github.com/CannObserv/address-validator/issues/311); until the knob lands the probe treats this host as report-only. Every step needs the owner's approval |

## Project-specific skills

| Skill | Trigger | Purpose |
|---|---|---|
| `/train-model` | "train model", "retrain usaddress", "fix parsing" | Interactive 7-step CRF model training pipeline |

## SocratiCode skills

| Skill | Purpose |
|---|---|
| `socraticode:codebase-exploration` | Semantic search, dependency graphs, architecture understanding |
| `socraticode:codebase-management` | Index management, health checks, file watching, context artifacts |

See [SOCRATICODE.md](SOCRATICODE.md) for the "when to use each tool" reference table and how to run the prefetch hook by hand; `AGENTS.md` → Code Exploration Policy carries the negative rule.
