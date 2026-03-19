# src/ Layout Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move all source modules into `src/address_validator/` so imports use the package name and the working-directory shadowing risk is eliminated.

**Architecture:** Create `src/address_validator/` as the single Python package; update all internal and test imports to use the `address_validator.` prefix; configure pytest `pythonpath = ["src"]` so the package is findable without a build step.

**Tech Stack:** Python 3.12, FastAPI, uv, pytest, ruff

---

## File Map

### Created
- `src/address_validator/__init__.py` — empty package marker

### Moved (git mv, no content change)
| From | To |
|------|----|
| `auth.py` | `src/address_validator/auth.py` |
| `logging_filter.py` | `src/address_validator/logging_filter.py` |
| `main.py` | `src/address_validator/main.py` |
| `models.py` | `src/address_validator/models.py` |
| `middleware/` | `src/address_validator/middleware/` |
| `routers/` | `src/address_validator/routers/` |
| `services/` | `src/address_validator/services/` |
| `usps_data/` | `src/address_validator/usps_data/` |

### Modified
- `src/address_validator/main.py` — fix imports
- `src/address_validator/logging_filter.py` — fix import
- `src/address_validator/routers/v1/core.py` — fix import
- `src/address_validator/routers/v1/health.py` — fix import
- `src/address_validator/routers/v1/parse.py` — fix imports
- `src/address_validator/routers/v1/standardize.py` — fix imports
- `src/address_validator/routers/v1/validate.py` — fix imports
- `src/address_validator/services/parser.py` — fix imports
- `src/address_validator/services/standardizer.py` — fix imports
- `src/address_validator/services/validation/_rate_limit.py` — fix import
- `src/address_validator/services/validation/cache_provider.py` — fix imports
- `src/address_validator/services/validation/chain_provider.py` — fix imports
- `src/address_validator/services/validation/factory.py` — fix imports
- `src/address_validator/services/validation/google_client.py` — fix imports
- `src/address_validator/services/validation/google_provider.py` — fix imports
- `src/address_validator/services/validation/null_provider.py` — fix import
- `src/address_validator/services/validation/protocol.py` — fix import
- `src/address_validator/services/validation/usps_provider.py` — fix imports (any)
- `pyproject.toml` — add pythonpath, update coverage source/omit, update isort, update addopts
- `address-validator.service` — update uvicorn entry point
- `tests/conftest.py` — fix app import
- `tests/integration/test_lifespan.py` — fix app import
- `tests/unit/test_auth.py` — fix module import + subprocess PYTHONPATH + subprocess import string
- `tests/unit/test_core.py` — fix import
- `tests/unit/test_parser.py` — fix import
- `tests/unit/test_request_id.py` — fix imports
- `tests/unit/test_standardizer.py` — fix imports
- `tests/unit/test_usps_data.py` — fix imports
- `tests/unit/test_validate_router.py` — fix imports
- `tests/unit/validation/*.py` — fix imports (all files in this directory)

---

## Task 1: Verify baseline

**Files:** (read-only)

- [ ] **Step 1: Run tests to confirm green baseline**

```bash
uv run pytest --no-cov -x -q
```

Expected: all tests pass. If any fail, stop — do not proceed with the migration until the baseline is clean.

- [ ] **Step 2: Note test count**

Record the passing test count so you can compare after the migration.

---

## Task 2: Create src/address_validator/ and move all source files

**Files:**
- Create: `src/address_validator/__init__.py`
- Git-move: all modules listed in the file map above

- [ ] **Step 1: Create the target directory and package marker**

```bash
mkdir -p src/address_validator
touch src/address_validator/__init__.py
```

- [ ] **Step 2: Move top-level source modules**

```bash
git mv auth.py src/address_validator/auth.py
git mv logging_filter.py src/address_validator/logging_filter.py
git mv main.py src/address_validator/main.py
git mv models.py src/address_validator/models.py
```

- [ ] **Step 3: Move packages**

```bash
git mv middleware src/address_validator/middleware
git mv routers src/address_validator/routers
git mv services src/address_validator/services
git mv usps_data src/address_validator/usps_data
```

- [ ] **Step 4: Verify the layout**

```bash
find src/address_validator -name "*.py" | sort | head -40
```

Expected: every source `.py` file appears under `src/address_validator/`, including all sub-packages.

---

## Task 3: Fix internal imports in source files

All bare module references inside `src/address_validator/` must be prefixed with `address_validator.`.

**Pattern mapping:**

| Old import prefix | New import prefix |
|-------------------|-------------------|
| `from models import` | `from address_validator.models import` |
| `from auth import` | `from address_validator.auth import` |
| `from logging_filter import` | `from address_validator.logging_filter import` |
| `from middleware.` | `from address_validator.middleware.` |
| `from routers.` | `from address_validator.routers.` |
| `from services.` | `from address_validator.services.` |
| `import services.` | `import address_validator.services.` |
| `from usps_data.` | `from address_validator.usps_data.` |

- [ ] **Step 1: Apply import rewrites across all source files**

Run each sed in-place substitution:

```bash
# from models → from address_validator.models
find src/address_validator -name "*.py" -exec sed -i \
  's/^from models import/from address_validator.models import/' {} +

# from auth → from address_validator.auth
find src/address_validator -name "*.py" -exec sed -i \
  's/^from auth import/from address_validator.auth import/' {} +

# from logging_filter → from address_validator.logging_filter
find src/address_validator -name "*.py" -exec sed -i \
  's/^from logging_filter import/from address_validator.logging_filter import/' {} +

# from middleware. → from address_validator.middleware.
find src/address_validator -name "*.py" -exec sed -i \
  's/^from middleware\./from address_validator.middleware./g' {} +

# from routers. → from address_validator.routers.
find src/address_validator -name "*.py" -exec sed -i \
  's/^from routers\./from address_validator.routers./g' {} +

# from routers.v1 import → from address_validator.routers.v1 import
find src/address_validator -name "*.py" -exec sed -i \
  's/^from routers\.v1 import/from address_validator.routers.v1 import/g' {} +

# from services. → from address_validator.services.
find src/address_validator -name "*.py" -exec sed -i \
  's/^from services\./from address_validator.services./g' {} +

# import services. → import address_validator.services.
find src/address_validator -name "*.py" -exec sed -i \
  's/^import services\./import address_validator.services./g' {} +

# from usps_data. → from address_validator.usps_data.
find src/address_validator -name "*.py" -exec sed -i \
  's/^from usps_data\./from address_validator.usps_data./g' {} +
```

- [ ] **Step 2: Verify no bare first-party imports remain in source**

```bash
grep -rn "^from models import\|^from auth import\|^from logging_filter import\|^from middleware\.\|^from routers\.\|^from services\.\|^import services\.\|^from usps_data\." src/address_validator/
```

Expected: no output.

- [ ] **Step 3: Spot-check a few key files manually**

Read `src/address_validator/main.py` and `src/address_validator/services/validation/factory.py` to confirm the imports look correct.

---

## Task 4: Update pyproject.toml

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Read the current pyproject.toml**

Open `pyproject.toml` and verify current content before editing.

- [ ] **Step 2: Add pytest pythonpath and update cov addopts**

Change the `[tool.pytest.ini_options]` section from:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
addopts = "--cov=. --cov-report=term-missing --cov-fail-under=80"
```

To:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
pythonpath = ["src"]
addopts = "--cov=src/address_validator --cov-report=term-missing --cov-fail-under=80"
```

- [ ] **Step 3: Update coverage source and omit**

Change the `[tool.coverage.run]` section from:

```toml
[tool.coverage.run]
source = ["."]
omit = [
    ".venv/*",
    "tests/*",
    "usps_data/__init__.py",
    "services/__init__.py",
    "routers/__init__.py",
    "routers/v1/__init__.py",
    "middleware/__init__.py",
]
branch = true
```

To:

```toml
[tool.coverage.run]
source = ["src/address_validator"]
omit = [
    "src/address_validator/usps_data/__init__.py",
    "src/address_validator/services/__init__.py",
    "src/address_validator/routers/__init__.py",
    "src/address_validator/routers/v1/__init__.py",
    "src/address_validator/middleware/__init__.py",
]
branch = true
```

- [ ] **Step 4: Update isort known-first-party**

Change:

```toml
[tool.ruff.lint.isort]
known-first-party = ["auth", "logging_filter", "main", "middleware", "models", "routers", "services", "usps_data"]
```

To:

```toml
[tool.ruff.lint.isort]
known-first-party = ["address_validator"]
```

---

## Task 5: Update systemd unit

**Files:**
- Modify: `address-validator.service`

- [ ] **Step 1: Update the ExecStart line**

Change:

```ini
ExecStart=/home/exedev/address-validator/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
```

To:

```ini
ExecStart=/home/exedev/address-validator/.venv/bin/uvicorn address_validator.main:app --host 0.0.0.0 --port 8000
```

---

## Task 6: Fix test imports

All test files import from the old bare module names. Apply the same prefix rewrites.

- [ ] **Step 1: Fix app import in conftest and integration tests**

```bash
# from main import app → from address_validator.main import app
find tests -name "*.py" -exec sed -i \
  's/^from main import app/from address_validator.main import app/' {} +
```

- [ ] **Step 2: Fix all other first-party imports in tests**

```bash
# from models → from address_validator.models
find tests -name "*.py" -exec sed -i \
  's/^from models import/from address_validator.models import/' {} +

# import auth → import address_validator.auth as auth
find tests -name "*.py" -exec sed -i \
  's/^import auth$/import address_validator.auth as auth/' {} +

# from routers. → from address_validator.routers.
find tests -name "*.py" -exec sed -i \
  's/^from routers\./from address_validator.routers./g' {} +

# from services. → from address_validator.services.
find tests -name "*.py" -exec sed -i \
  's/^from services\./from address_validator.services./g' {} +

# import services. → import address_validator.services.
find tests -name "*.py" -exec sed -i \
  's/^import services\./import address_validator.services./g' {} +

# from usps_data. → from address_validator.usps_data.
find tests -name "*.py" -exec sed -i \
  's/^from usps_data\./from address_validator.usps_data./g' {} +

# from logging_filter → from address_validator.logging_filter
find tests -name "*.py" -exec sed -i \
  's/^from logging_filter import/from address_validator.logging_filter import/' {} +

# from middleware. → from address_validator.middleware.
find tests -name "*.py" -exec sed -i \
  's/^from middleware\./from address_validator.middleware./g' {} +
```

- [ ] **Step 3: Fix test_auth.py subprocess test — PYTHONPATH and import string**

Open `tests/unit/test_auth.py` and make two targeted edits:

**Edit 1** — change the subprocess PYTHONPATH from the project root to `src/`:

```python
# Old:
env["PYTHONPATH"] = _PROJECT_ROOT

# New:
env["PYTHONPATH"] = str(Path(_PROJECT_ROOT) / "src")
```

Apply to both subprocess test methods (`test_module_importable_without_api_key` and `test_module_importable_with_empty_api_key`). The second method uses a dict literal:

```python
# Old:
env = {**os.environ, "API_KEY": "", "PYTHONPATH": _PROJECT_ROOT}

# New:
env = {**os.environ, "API_KEY": "", "PYTHONPATH": str(Path(_PROJECT_ROOT) / "src")}
```

**Edit 2** — change the import string in the subprocess `-c` argument:

```python
# Old:
[sys.executable, "-c", "import auth"],

# New:
[sys.executable, "-c", "import address_validator.auth"],
```

Apply to both subprocess test methods.

- [ ] **Step 4: Verify no bare first-party imports remain in tests**

```bash
grep -rn "^from models import\|^from auth import\|^from logging_filter import\|^from middleware\.\|^from routers\.\|^from services\.\|^import services\.\|^from usps_data\.\|^import auth$\|^from main import" tests/
```

Expected: no output.

---

## Task 7: Verify and commit

- [ ] **Step 1: Run ruff lint**

```bash
uv run ruff check .
```

Expected: no errors. If there are isort errors from the import reorder, run:

```bash
uv run ruff check . --fix
```

- [ ] **Step 2: Run ruff format check**

```bash
uv run ruff format --check .
```

If there are formatting issues, apply them:

```bash
uv run ruff format .
```

- [ ] **Step 3: Run full test suite**

```bash
uv run pytest --no-cov -x -q
```

Expected: same test count as baseline, all passing.

- [ ] **Step 4: Run with coverage**

```bash
uv run pytest -q
```

Expected: coverage ≥ 80% (baseline ~93%), all tests pass.

- [ ] **Step 5: Smoke-test the uvicorn entry point resolves**

```bash
PYTHONPATH=src uv run python -c "from address_validator.main import app; print('OK')"
```

Expected: prints `OK`.

- [ ] **Step 6: Commit**

```bash
git add src/ pyproject.toml address-validator.service tests/conftest.py tests/
git commit -m "#39 refactor: migrate to src/ layout"
```
