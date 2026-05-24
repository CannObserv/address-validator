# Dependency Version Pinning Policy

## Python (uv + pyproject.toml)

Pin every dependency within a major version boundary: `>=X.Y,<X+1`. No unbounded upper pins.

After each intentional upgrade cycle, update the lower bound to the newly installed version.

Example: after upgrading FastAPI to 0.130.x, update `pyproject.toml` to `fastapi>=0.130,<1`.

Always commit `uv.lock` alongside `pyproject.toml` after any dep change.

Upgrade cadence: `uv lock --upgrade && uv sync` periodically; then update lower bounds.

## JavaScript (npm + package.json)

The admin dashboard's JS toolchain (ESLint, Prettier, Vitest, jsdom) is dev-only — no JS ships to a request path. Because nothing reaches runtime, the JS devDep rules are intentionally looser than the Python rules: caret ranges (`^X.Y.Z`) on devDeps are acceptable risk here, where they would not be in `pyproject.toml`. The Node runtime itself is treated more strictly — see below.

### Node runtime floor

- Pinned in **two** places, both load-bearing:
  - `package.json` → `engines.node` set to `>=X.Y.Z` (current installed patch) — mirrors the Python "update the lower bound to the newly installed version" rule
  - `.npmrc` → `engine-strict=true` (turns `engines` from advisory into enforced)
- `.nvmrc` and `.node-version` track the **exact** patch (`X.Y.Z`, not just `X`) so contributor / CI version managers (nvm, fnm, asdf) all converge on one runtime
- Bump the floor only to a current LTS line. When bumping: install the new Node on the VM **before** raising the floor, otherwise `engine-strict=true` blocks every subsequent `npm install`. Tighten all three files (`engines.node`, `.nvmrc`, `.node-version`) together — drift between them defeats the convergence guarantee

### devDeps

- Caret ranges (`^X.Y.Z`) are fine for devDeps — they don't ship to prod and tolerate minor drift
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
