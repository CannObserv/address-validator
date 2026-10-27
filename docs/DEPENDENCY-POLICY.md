# Dependency Version Pinning Policy

## Python (uv + pyproject.toml)

Pin every dependency within a major version boundary: `>=X.Y,<X+1`. No unbounded upper pins.

After each intentional upgrade cycle, update the lower bound to the newly installed version.

Example: after upgrading FastAPI to 0.130.x, update `pyproject.toml` to `fastapi>=0.130,<1`.

Always commit `uv.lock` alongside `pyproject.toml` after any dep change.

**Git-sourced deps** (`notifier-client`, #232) are pinned by tag in `[tool.uv.sources]`, per the source repo's release doc. uv does not enforce the `>=` floor against a git source: the floor records intent, and the tag is the pin. To upgrade, change the tag, run `uv sync`, and raise the floor to match.

Upgrade cadence: Dependabot proposes bumps weekly ([below](#dependabot-361)). By hand: `uv lock --upgrade-package <pkg>`, then update lower bounds.

### Auditing (#317)

Audit, lock and sync with **the host's uv**, and never let a different uv (`uvx uv@latest …`) rewrite `uv.lock`. Lock with the uv that the deploy's `uv sync` runs, so the resolution and the `revision` stamp are the ones it would write itself, and a lock diff carries no churn from a version mismatch. The concern isn't readability: the image's uv 0.10.4 reads uv 0.13.1's `revision = 5` (`uv lock --check` exits 0, measured for #318).

- **Where the host's uv lives (#318).** The standalone install is in `~/.local/bin`, and `/usr/local/bin/{uv,uvx}` symlink to it, so root and the units run the same binary.
- **Image-refresh caveat.** The image owns `/usr/local/bin`. If a refresh restores its own uv there, root and the units fall behind while `exedev` keeps the new one, so check `/usr/local/bin/uv --version` after any upgrade.

```bash
uv audit --frozen                       # all groups; add --no-dev for runtime only
uv lock --upgrade-package <pkg> …       # a full --upgrade can cross unrelated majors
uv self update                          # upgrade the host's uv; then `uv --version` via /usr/local/bin too
```

An advisory whose only fix is past a major bound is a decision, not an upgrade. Record it here with its reason:

| Advisory | Package | Fixed in | Held because |
|---|---|---|---|
| _none held_ | | | |

## JavaScript (npm + package.json)

The admin dashboard's JS toolchain (ESLint, Prettier, Vitest, jsdom) is dev-only — no JS ships to a request path. Because nothing reaches runtime, the JS devDep rules are intentionally looser than the Python rules: caret ranges (`^X.Y.Z`) on devDeps are acceptable risk here, where they would not be in `pyproject.toml`. The Node runtime itself is enforced (via `engine-strict=true`) and pinned to an exact patch across `.nvmrc` / `.node-version` — see below.

### Node runtime floor

Three files, three distinct roles:

- **Enforcement** — `package.json` declares the floor as a bounded major range (`>=X.Y.Z <X+1.0.0`); `.npmrc` flips `engines` from advisory to enforced via `engine-strict=true` (without it, `npm install` ignores the floor). The range mirrors the Python `>=major.minor,<major+1` rule, but one decimal tighter: Node is shared VM-wide infra, so contributor-side patch determinism matters more than for per-venv Python deps — this asymmetry is intentional.
- **Convergence** — `.nvmrc` and `.node-version` both hold the **exact** patch (`X.Y.Z`, not just `X`) so contributor / CI version managers (nvm, fnm, asdf) all install the same runtime.
- **Bumping** — only move the floor to a current LTS line. Install the new Node on the VM **before** raising `engines.node`, otherwise `engine-strict=true` blocks every subsequent `npm install`. Move all three files (`engines.node`, `.nvmrc`, `.node-version`) in the same commit — drift between them defeats the convergence guarantee.

### devDeps

- Always commit `package-lock.json` alongside `package.json` (mirrors the `uv.lock` rule)
- Use `npm install --save-dev <pkg>` to add; `npm update` to refresh within ranges; `npm outdated` to spot majors needing manual bumps

### Upgrade cadence

Periodically refresh deps within current semver ranges, then sweep majors separately:

```bash
npm update && npm audit fix             # minors + patches within existing ^X.Y.Z ranges
npx npm-check-updates -u && npm install # bumps majors by rewriting package.json
```

`npm update` alone cannot bump majors — it only refreshes within the caret range already in `package.json`. Use `npm-check-updates` (or a manual edit) for major bumps.

If `npm audit` still reports vulnerabilities after `audit fix`, regenerate the lockfile from scratch — transitive vulns often clear on a fresh resolution:

```bash
rm -rf node_modules package-lock.json && npm install && npm audit
```

This is the recipe that cleared the postcss / vite / ws findings during the Node 24 floor bump (#119).

## Dependabot (#361)

`.github/dependabot.yml` proposes version updates weekly for `uv`, `npm`, `pre-commit` and `github-actions`. Security updates arrive on an advisory, whatever the schedule. Design and rationale: `docs/plans/2026-10-27-dependabot-adoption-design.md`. Guarded by `tests/ci/test_dependabot.py` (the `adopting-dependabot` skill's asset: edit only its ADAPT block) and `tests/ci/test_dependabot_repo.py`.

| Ecosystem | PRs | Lands as |
|---|---|---|
| uv | `dev-tools` group; `runtime` group (minor + patch); one PR per runtime **major** | runtime → deploy; dev → no restart |
| npm | `js-tooling` group (minor + patch); one PR per major | dev-only, no restart |
| pre-commit | one per hook repo (shellcheck only: ruff runs from `uv.lock`) | no restart |
| github-actions | one per action | no restart; dispatch the workflow once |

**Not moved by the bot:** `notifier-client` (a git tag, ignored; bump by hand, per the git-sourced rule above). `context-cadence.yml` is generated: after any `install-cadence.sh` re-render, check `git diff` for `uses:` pins that went backwards and restore them (gregoryfoster/skills#373).

**The PR limits are derived, not guessed.** Past `open-pull-requests-limit` Dependabot opens no more version updates and says so nowhere. The guard tests fail when a new dependency, hook repo or action pushes a batch past a limit: raise it then.

### Merge path

There's no PR CI (`gh pr checks` is always empty), so **pre-ship on the PR's current head is the gate**. Land one PR at a time, because each merge puts the others behind `main`.

```bash
export GH_TOKEN=$(grep '^GH_TOKEN=' .env | cut -d= -f2)
N=<pr>
# 1. Read the release notes for every version between, not just the diff.
# 2. If main moved past the PR's base, rebase, and wait for a NEW head. Reading
#    the old head right after the comment tests a commit that won't land.
OLD=$(gh pr view $N --json headRefOid -q .headRefOid)
git fetch origin && git merge-base --is-ancestor origin/main "$OLD" \
  || gh pr comment $N --body "@dependabot rebase"
until [ "$(gh pr view $N --json headRefOid -q .headRefOid)" != "$OLD" ]; do sleep 30; done  # only after a rebase
SHA=$(gh pr view $N --json headRefOid -q .headRefOid)
BR=$(gh pr view $N --json headRefName -q .headRefName)
# 3. Gate that head in its own worktree.
git fetch origin "$BR:$BR"
bash .claude/skills/using-git-worktrees/scripts/worktree-create.sh "$BR"   # cd into the printed path
uv sync && bash .skills/doctor.sh
npm ci                                      # npm PRs: else npm run finds the MAIN checkout's node_modules
test "$(git rev-parse HEAD)" = "$SHA"
uv run pre-commit clean && uv run pre-commit run --all-files
bash .claude/skills/shipping-work-python-fastapi/scripts/pre-ship.sh
uv audit --frozen && uv lock --check
# 4. Merge exactly the head that was gated.
gh pr merge $N --merge --match-head-commit "$SHA"
# 5. From the main checkout: the doctor ran, so --force.
bash .claude/skills/using-git-worktrees/scripts/worktree-destroy.sh "$BR" --force
```

Then, by what the PR touched:

- **Runtime** (`runtime` group, a runtime major, or a security update to a runtime package): live only after the deploy, which is its own approval. Pull the main checkout, `uv sync`, then `sudo systemctl restart address-validator`, outside the `quiet` ranges in `.skills/patching-hosts` (re-read `date -u` first).
- **Dev, npm, pre-commit**: no restart.
- **`.github/workflows/`**: no PR runs a scheduled-only workflow, so dispatch it once (`gh workflow run context-cadence.yml`; the PAT can't, so the operator does) and read the run.

A bump whose hooks or tests go red isn't landed by pinning it back: report it, and fix forward on a branch of its own. A major is checked against the `>=X.Y,<X+1` form above before merging.

**The trade-off:** every PR costs a pre-ship session. A `pull_request` workflow running ruff and pytest would let each bump gate itself and cut a landing to a review. Weigh it with `auditing-ci-cost` before the bot's volume grows.
