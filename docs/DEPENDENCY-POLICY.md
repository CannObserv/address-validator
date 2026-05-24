# Dependency Version Pinning Policy

## Python (uv + pyproject.toml)

Pin every dependency within a major version boundary: `>=X.Y,<X+1`. No unbounded upper pins.

After each intentional upgrade cycle, update the lower bound to the newly installed version.

Example: after upgrading FastAPI to 0.130.x, update `pyproject.toml` to `fastapi>=0.130,<1`.

Always commit `uv.lock` alongside `pyproject.toml` after any dep change.

Upgrade cadence: `uv lock --upgrade && uv sync` periodically; then update lower bounds.

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
