# Dependency Version Pinning Policy

Pin every dependency within a major version boundary: `>=X.Y,<X+1`. No unbounded upper pins.

After each intentional upgrade cycle, update the lower bound to the newly installed version.

Example: after upgrading FastAPI to 0.130.x, update `pyproject.toml` to `fastapi>=0.130,<1`.

Always commit `uv.lock` alongside `pyproject.toml` after any dep change.
