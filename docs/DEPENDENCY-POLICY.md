# Dependency Version Pinning Policy

## Python (uv + pyproject.toml)

Pin every dependency within a major version boundary: `>=X.Y,<X+1`. No unbounded upper pins.

After each intentional upgrade cycle, update the lower bound to the newly installed version.

Example: after upgrading FastAPI to 0.130.x, update `pyproject.toml` to `fastapi>=0.130,<1`.

Always commit `uv.lock` alongside `pyproject.toml` after any dep change.

Upgrade cadence: `uv lock --upgrade && uv sync` periodically; then update lower bounds.

## JavaScript (npm + package.json)

The admin dashboard's JS toolchain (ESLint, Prettier, Vitest, jsdom) is dev-only — no JS ships to a request path.

### Node runtime floor

- Pinned in **two** places, both load-bearing:
  - `package.json` → `engines.node`
  - `.npmrc` → `engine-strict=true` (turns `engines` from advisory into enforced)
- `.nvmrc` and `.node-version` track the same major for contributor / CI version managers
- Bump the floor only to a current LTS line. When bumping: install the new Node on the VM **before** raising the floor, otherwise `engine-strict=true` blocks every subsequent `npm install`

### devDeps

- Caret ranges (`^X.Y.Z`) are fine for devDeps — they don't ship to prod and tolerate minor drift
- Always commit `package-lock.json` alongside `package.json` (mirrors the `uv.lock` rule)
- Use `npm install --save-dev <pkg>` to add; `npm update` to refresh within ranges; `npm outdated` to spot majors needing manual bumps

### Upgrade cadence

Mirror the Python cycle: periodically run `npm update && npm audit fix`, regenerate the lockfile on a fresh `node_modules` if vulns persist (lockfile rewrites often clear transitive findings on their own).
