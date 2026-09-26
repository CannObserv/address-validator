---
name: brainstorming
description: "Explores user intent, requirements, and design before any implementation. Use when the user says 'brainstorm', 'design this', or 'let's design'. Agent should also proactively suggest brainstorming when a new feature is requested without prior design discussion."
compatibility: Designed for Claude. Requires git and gh CLI. Python project using FastAPI, Pydantic, uv.
metadata:
  author: gregoryfoster
  version: "6.4.2"
  synced-from: "obra-superpowers v6.4.2 (8ca22dba9a94f28898bbce59f2537ff4d87c747d)"
  triggers: brainstorm, design this, let's design
  overrides: obra-superpowers/brainstorming
  override-reason: "Hard-block variant (explicit approval words at every stage, no implied consent); docs/plans/ path convention; project commit convention for the design doc; GitHub issue opened per design; writing-plans offered, never mandatory (choosing plan vs direct implementation is its own approval stage); visual companion launched with exe.dev proxy flags (remote VM, no local browser)"
---

# Brainstorming Ideas Into Designs — address-validator

Help turn ideas into fully formed designs through collaborative dialogue before any implementation begins.

Classify how much process the request needs, then work your path: understand the context, refine the idea, present a design, get explicit approval.

## Establish shared understanding

The outcome of brainstorming is an understanding the user can recognize and correct, grounded in what they want to accomplish.

1. **Discover intent.** Use the request and available context to identify the intended outcome, who it is for, and what success looks like. When that information is missing, ask one focused question about purpose or intended use before proposing features or an approach. Knowing the kind of service does not tell you why the user wants the change. Gathering missing requirements does not ask them to authorize the task again.
2. **Write back your understanding.** Summarize the intended outcome, relevant constraints, and success criteria in a short note the user can assess. Separate what they said from assumptions. Invite correction and incorporate their answer before treating this as the design brief.
3. **Carry intent into the design.** Preserve the agreed understanding in the selected path's design artifact: the written design doc for architectural work, or the in-chat design/probe for bounded work and spikes. Check proposed features and technical choices against that understanding.

When the request already supplies the purpose and constraints, reflect that understanding instead of asking the same questions again. Keep the note concise; its accuracy and the opportunity to correct it matter.

<HARD-GATE>
Before taking any implementation action — writing code, creating files (other than the design doc and, once the user picks `writing-plans`, the plan), running migrations, installing dependencies, invoking an implementation skill — complete the selected path's approval stages:

- **Spike:** the user approves the question and probe.
- **Bounded:** the user approves the short in-chat design.
- **Architectural:** the user approves the design in chat, then approves the written design doc, then chooses how implementation proceeds — `writing-plans` or direct implementation. Conversational design approval only permits writing the design doc; design-doc approval only permits opening the issue and offering that choice; picking `writing-plans` only permits writing the plan. If they choose `writing-plans`, the user must also explicitly approve the written plan (its Phase 3 review) before implementation.

Every approval is explicit: "approved", "proceed", "looks good", or clearly equivalent. "sounds fine" or "okay" without affirmative intent does not count; silence and follow-up questions never do.

A reply approves the stage actually presented. Approval of an idea or scope does not approve artifacts that do not exist yet. Resume at the earliest incomplete stage; never turn one approval into permission to skip the rest of the path. Read-only project exploration is allowed while stages remain incomplete.

The ceremony scales with the path; the approval gate never does.
</HARD-GATE>

## Three paths

Before your first question, classify the request and say the classification out loud — "this looks bounded, so I'll present a short design here rather than write a design doc" — so the user can override it:

- **Spike** — a feasibility question ("can we…", "is it possible…", "quick and dirty is fine") whose output is an answer, not code you keep. Present the question and what you'll try in 2–3 sentences, get approval, then find out as cheaply as correctness allows. No design doc, no issue. Report findings as a recommendation; anything you built stays labeled throwaway.
- **Bounded** — a well-scoped change to code that already exists in this repo: a new flag, a one-file fix. Understanding the kind of app is not enough — bounded means the flow you are changing is already here to read. If there is no existing flow to change, the task is not bounded. Ask the clarifying questions that matter, present a short design IN CHAT (a few sentences to a few short paragraphs), and STOP. Implementation starts only after the user says yes to that design — a bounded task's approval is as hard a gate as an architectural one. No design doc, no plan document.
- **Architectural** — new projects, new subsystems, new endpoints, schema changes, changes that restructure how components fit together or alter API contracts others depend on. Follow the full process: questions, approaches, sectioned design, written design doc, GitHub issue, then the implementation choice.

When in doubt between two paths, take the heavier one. The ratchet is one-way: hidden complexity discovered mid-task upgrades the path — stop, say so, and step up. Nothing downgrades mid-task.

**This repo's tells.** A change touching `models.py`, `alembic/versions/`, `core/warnings.py`, `core/validation_status.py`, or `PIPELINE_CODE_VERSION` is architectural by default — those are documented single sources of truth with drift tests and migration requirements behind them (see AGENTS.md → Key conventions).

## Anti-pattern: "too simple to need approval"

Every path ends with the user approving the required design before implementation. A one-line fix or a config change may need only two sentences in chat. A new endpoint or a schema change is architectural and requires the written design doc, the issue, and the implementation choice. Scale the artifact to the selected path; complete that path's approval stages before implementation. "Simple" tasks are where unexamined assumptions cause the most wasted work.

## Red flags

| Thought | Reality |
|---|---|
| "This is too simple to need a design" | Follow the selected path: a bounded change gets a short chat design; an architectural change gets the written design doc and the implementation choice. |
| "I'll call it bounded and skip the design doc" | Reaching for a label to skip work IS the doubt — take the heavier path. |
| "It's bounded and the design is obvious — I'll start while they read it" | The gate is the approval, not the design's length. Present, then stop until you hear yes. |
| "I understand this kind of service, so it's bounded" | Bounded measures the repo, not your familiarity. No existing flow to change means architectural. |
| "They approved the design doc, so I can start coding" | Design-doc approval permits the issue and the implementation choice — not implementation. Ask: `writing-plans` or direct? |
| "The spike works, so I'll keep the code" | A spike's output is an answer. Keeping the code is a new request — classify it. |
| "It grew, but I'm almost done — no need to re-classify" | Hidden complexity upgrades the path mid-task. Stop and say so. |
| "They approved the spike, so the follow-up is approved too" | Each task gets its own classification and its own approval. |

## Proactive suggestion

If the user describes a new feature, significant refactor, or new endpoint **without** first asking to brainstorm or design, respond with:

> Before diving in — this sounds like a good candidate for a quick design pass. Want me to run brainstorming first, or do you already have a design in mind?

## Checklists

Classify first, announce the path, then work the items in order.

**Spike:**
1. **Explore project context** — enough to frame the probe
2. **Present question + probe plan** — 2–3 sentences
3. **Get approval** — explicit, as for every path
4. **Investigate** — as cheaply as correctness allows
5. **Report findings** — a recommendation; label anything built as throwaway

**Bounded:**
1. **Explore project context** — AGENTS.md, the files in the area, recent commits
2. **Ask clarifying questions** — one at a time, the ones that matter; write back your understanding (see Establish shared understanding)
3. **Present short design in chat** — approach, files touched, test strategy
4. **Get approval** — STOP and wait for an explicit yes; presenting the design and starting in the same breath skips the gate
5. **Implement** — normal workflow (`test-driven-development` applies); no design doc, no plan document

**Architectural:**
1. **Explore project context** — AGENTS.md, README.md, `git log --oneline -10`, relevant source
2. **Offer the visual companion just-in-time** — NOT upfront; only when a question is genuinely clearer shown than described (see Visual companion below)
3. **Ask clarifying questions** — one at a time; purpose, constraints, success criteria, scope boundaries; write back your understanding (see Establish shared understanding)
4. **Propose 2–3 approaches** — trade-offs, lead with your recommendation
5. **Present design** — in sections scaled to their complexity, approval after each section
6. **Write design doc** — `docs/plans/YYYY-MM-DD-<topic>-design.md`, then commit
7. **Design self-review** — inline check for placeholders, contradictions, ambiguity, scope
8. **User reviews the written doc** — ask before proceeding; wait for explicit approval
9. **Open a GitHub issue** — track the work
10. **Offer the implementation choice** — `writing-plans` or direct implementation; the user's pick is its own approval stage. Do not invoke `writing-plans` unbidden, and do not start implementing before they choose

## The process

The subsections below serve the bounded and architectural paths. A spike stops at "present the probe, get approval". Everything from **Exploring approaches** onward is architectural-path depth — for bounded work, context plus a few questions plus a short in-chat design is the whole process.

**Understanding the idea:**

- Read AGENTS.md, README.md, and recent commits (`git log --oneline -10`) first
- Survey the source files for the area being changed; note existing patterns to preserve or extend
- Before asking detailed questions, assess scope: if the request describes multiple independent subsystems, flag it immediately rather than refining details of something that needs decomposing first
- If it is too large for a single design, help decompose it into sub-projects — what are the independent pieces, how do they relate, what order? Each sub-project then gets its own design → issue → implementation cycle
- Ask questions one at a time; never more than one question per message
- Prefer multiple choice when possible; open-ended when necessary
- Focus on purpose, constraints, success criteria, scope boundaries

**Exploring approaches:**

- Propose 2–3 approaches with trade-offs
- Lead with your recommended option and explain why
- Consider implementation complexity, test surface, and AGENTS.md convention alignment
- YAGNI ruthlessly — remove unnecessary scope from every approach

**Presenting the design:**

- Scale each section to its complexity: a few sentences if straightforward, up to 300 words if nuanced
- Cover the dimensions that apply: API contract changes, service layer impact, data models, migrations, error handling, warnings/status vocabulary, test strategy
- Ask after each section: "Does this look right so far?"
- Revise until the user explicitly approves the full design

**Design for isolation and clarity:**

- Break the work into units with one clear purpose, well-defined interfaces, and independent testability
- For each unit: what does it do, how do you use it, what does it depend on?
- Can someone understand a unit without reading its internals? Can you change the internals without breaking consumers? If not, the boundaries need work
- A file growing large is usually a signal it is doing too much

**Working in existing code:**

- Explore the current structure before proposing changes; follow existing patterns
- Where existing code has problems that affect the work, include targeted improvements in the design
- Don't propose unrelated refactoring — stay focused on what serves the current goal

<HARD-GATE>
Do not proceed to the design doc until the user has explicitly approved the design.
</HARD-GATE>

## After the design (architectural path)

**Write the design doc.** Save the validated design to:

```
docs/plans/YYYY-MM-DD-<topic>-design.md
```

Carry the agreed understanding (outcome, constraints, success criteria) into it. Commit it using the project convention (AGENTS.md → Commit convention):

```
[docs]: add <topic> design doc          # issue number not yet known
#<n> [docs]: add <topic> design doc     # issue number known
```

**Design self-review.** After writing, look at it with fresh eyes:

1. **Placeholder scan** — any "TBD", "TODO", incomplete sections, vague requirements? Fix them.
2. **Internal consistency** — do any sections contradict each other? Does the design match the feature description?
3. **Scope check** — focused enough for a single implementation pass, or does it need decomposition?
4. **Ambiguity check** — could any requirement be read two ways? Pick one and make it explicit.

Fix issues inline. No need to re-review — fix and move on.

**User review gate.** Then ask:

> "Design doc written and committed to `<path>`. Please review it and let me know if you want any changes before we go further."

Wait for the response. If they request changes, make them and re-run the self-review. Only proceed once the user explicitly approves.

**Open a GitHub issue.** After the doc is approved, create the tracking issue:

```bash
gh issue create \
  --title "<topic — concise imperative phrase>" \
  --body "$(cat <<'EOF'
## Summary
<1–3 sentence summary of what was designed>

## Design doc
`docs/plans/YYYY-MM-DD-<topic>-design.md`

## Scope
<bullet list of the key decisions / in-scope items from the design>
EOF
)"
```

- Title: short imperative phrase matching the design topic (e.g. "Add rate-limit header to validate endpoint")
- Report the issue number to the user (e.g. "Opened #42")
- If the issue number was not known at commit time, note the doc path in the issue body rather than amending the commit

**Implementation choice.** Present a summary of what was decided, then offer:

- `writing-plans` to create a detailed implementation plan — appropriate for larger features, **optional**. If chosen, the written plan gets its own explicit approval before any implementation.
- Direct implementation for smaller work

Wait for an explicit pick. Design-doc approval is not a pick, and neither is silence. If `writing-plans` decides no plan is warranted (its Phase 1 "just do the work"), do not start: return to the user for an explicit direct-implementation pick. Do NOT invoke any implementation action without user direction.

## Visual companion

A browser-based companion for showing mockups, diagrams, and visual options during brainstorming. It is a tool, not a mode: accepting it means it is *available* for questions that benefit from visual treatment, not that every question goes through the browser.

**Offer it just-in-time — never upfront.** Wait until a question would genuinely be clearer shown than told: a real mockup, layout, or diagram question, not merely a UI *topic*. The first time that happens, offer it then, as **its own message** — only the offer, no clarifying question or other content alongside it:

> "This next part might be easier if I show you — I can put together mockups, diagrams, and comparisons in a browser tab as we go. It's still new and can be token-intensive. Want me to? I'll open it for you."

Wait for the answer. If they decline, continue text-only and don't offer again unless they raise it.

**Per-question decision.** Even after they accept, decide FOR EACH QUESTION whether to use the browser or the terminal. The test: would the user understand this better by seeing it than reading it?

- **Browser** — mockups, wireframes, layout comparisons, architecture diagrams, side-by-side visual designs
- **Terminal** — requirements questions, conceptual choices, tradeoff lists, A/B/C/D text options, scope decisions

A question about a UI topic is not automatically a visual question. "What should the admin dashboard convey?" is conceptual — terminal. "Which of these two dashboard layouts?" is visual — browser.

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
- **`--url-host` is this VM's name**, as in the dev-server URL AGENTS.md documents (`https://address-validator.exe.xyz:8001/`). Confirm it there rather than trusting the literal above if the VM may have been renamed.
- **Pick a free port.** 8000 is the production service, 8001 the dev server, 4400 libpostal, 3900 the companion (docs/DEPLOYMENT.md).
- **Invoke by the full path** shown above. The companion guide writes `bash scripts/start-server.sh` with a bare `scripts/` path, which does not resolve from the project root ([#63](https://github.com/gregoryfoster/skills/issues/63)).
- Session content and state land in `.superpowers/brainstorm/` (gitignored).

Read [visual-companion.md](visual-companion.md) for the full guide — screen-writing, event polling, and lifecycle — before the first screen. Note it is vendored upstream text: its examples use the bare `scripts/` path and assume a local browser, so the flags above override it.

## Key principles

- **One question at a time** — never overwhelm
- **Write back your understanding** — the user corrects it before it becomes the brief
- **YAGNI** — remove unnecessary scope from every design
- **Explicit approval required** — at every stage, on every path; ambiguity does not count as approval
- **A reply approves the stage presented** — never the stages after it
- **Classify out loud** — the user can only override a path they can see
- **`docs/plans/` is our convention** — not `docs/` root, not project root
- **`writing-plans` is optional** — useful for large features, never a mandatory terminal state; the choice to skip it is still the user's, made explicitly

## Deliberate deviations from obra-superpowers v6.4.2

Recorded so the next sync can tell a deviation from a drift:

| Vendor | Here | Why |
|---|---|---|
| Spike approval may be "a nod" | Explicit approval words at every stage, every path | Hard-block variant — implied consent has burned us |
| `docs/superpowers/specs/` ("spec") | `docs/plans/` ("design doc") | Project convention (AGENTS.md) |
| "Commit the design document" | `[docs]:` / `#<n> [docs]:` prefix | Project commit convention (AGENTS.md) |
| No issue step | `gh issue create` after the doc | Work here is issue-tracked |
| Architectural gate: spec approval → `writing-plans` → plan review → execution method, all mandatory | Design-doc approval → issue → user explicitly picks `writing-plans` or direct implementation; plan approval applies only if they pick `writing-plans`; no execution-method stage | Small architectural changes don't earn a plan doc (#231); upstream's stage discipline is kept — the pick is its own approval, so design-doc approval never becomes a license to code. This repo vendors gregoryfoster/skills' `writing-plans`, which has no execution-method handoff — its Phase 3 plan review is the last stage |
| Bounded examples include "a small endpoint" | Endpoints are architectural; repo tells list the drift-tested sources of truth | New endpoints alter the API contract (`models.py`) |
| "Too simple" example: a new todo-list project | A new endpoint or schema change | Examples drawn from this repo |
| Visual companion assumes a local browser on an ephemeral port | `BRAINSTORM_PORT` in 3000-9999, `--host 0.0.0.0`, `--url-host`, relay as `https://` | Remote VM behind the exe.dev proxy — the default invocation is unreachable |
| Companion guide invokes `bash scripts/start-server.sh` (bare path) | Full path from project root | Bare path does not resolve from the project root ([gregoryfoster/skills#63](https://github.com/gregoryfoster/skills/issues/63)) |
| Process-flow digraph | Omitted | The path checklists carry the same routing |
