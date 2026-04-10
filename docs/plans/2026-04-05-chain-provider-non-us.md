# Chain Provider Non-US Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix non-US validation failing for `usps,google` chain by replacing the `isinstance(provider, GoogleProvider)` check in `validate.py` with a `supports_non_us` protocol property that each provider self-declares.

**Architecture:** Add `supports_non_us: bool` to the `ValidationProvider` protocol. Leaf providers (`NullProvider`, `USPSProvider`) declare `False`; `GoogleProvider` declares `True`; `ChainProvider` delegates via `any(p.supports_non_us for p in self._providers)`; `CachingProvider` delegates to `self._inner.supports_non_us`. The router replaces the `isinstance` check with `provider.supports_non_us`, removing the `GoogleProvider` import from `validate.py`.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, `typing.Protocol` (runtime_checkable)

---

## File Structure

| File | Change |
|---|---|
| `src/address_validator/services/validation/protocol.py` | Add `supports_non_us: bool` attribute to protocol |
| `src/address_validator/services/validation/null_provider.py` | Add `supports_non_us = False` |
| `src/address_validator/services/validation/usps_provider.py` | Add `supports_non_us = False` |
| `src/address_validator/services/validation/google_provider.py` | Add `supports_non_us = True` |
| `src/address_validator/services/validation/chain_provider.py` | Add `supports_non_us` property |
| `src/address_validator/services/validation/cache_provider.py` | Add `supports_non_us` property delegating to `self._inner` |
| `src/address_validator/routers/v1/validate.py` | Replace `isinstance` check; remove `GoogleProvider` import; update docstrings |
| `tests/unit/validation/test_null_provider.py` | Add `test_supports_non_us_is_false` |
| `tests/unit/validation/test_usps_provider.py` | Add `test_supports_non_us_is_false` |
| `tests/unit/validation/test_google_provider.py` | Add `test_supports_non_us_is_true` |
| `tests/unit/validation/test_chain_provider.py` | Add three `supports_non_us` tests |
| `tests/unit/test_validate_router.py` | Update `_make_null_provider`; add chain-provider non-US test |
| `AGENTS.md` | Remove stale "known limitation" note from `google_provider.py` entry |

---

### Task 1: Add `supports_non_us` to protocol and leaf providers

> **Implementation note:** `CachingProvider` wraps any configured provider when `VALIDATION_CACHE_DSN`
> is set — `app.state.registry.get_provider()` returns a `CachingProvider`, not the inner provider
> directly. Without `supports_non_us` delegation on `CachingProvider`, the protocol attribute would
> be missing at runtime for all cache-enabled deployments. Add `supports_non_us` as a property on
> `CachingProvider` (delegating to `self._inner`) alongside the leaf provider changes in this task.
> This was discovered during implementation and added in commit `74e30f3`.

**Files:**
- Modify: `src/address_validator/services/validation/protocol.py`
- Modify: `src/address_validator/services/validation/null_provider.py`
- Modify: `src/address_validator/services/validation/usps_provider.py`
- Modify: `src/address_validator/services/validation/google_provider.py`
- Modify: `src/address_validator/services/validation/cache_provider.py`
- Test: `tests/unit/validation/test_null_provider.py`
- Test: `tests/unit/validation/test_usps_provider.py`
- Test: `tests/unit/validation/test_google_provider.py`

- [ ] **Step 1: Write failing tests for all three leaf providers**

Add to `tests/unit/validation/test_null_provider.py` inside `class TestNullProvider`:

```python
def test_supports_non_us_is_false(self, provider: NullProvider) -> None:
    assert provider.supports_non_us is False
```

Add to `tests/unit/validation/test_usps_provider.py` inside `class TestUSPSProvider`:

```python
def test_supports_non_us_is_false(self, provider: USPSProvider) -> None:
    assert provider.supports_non_us is False
```

Add to `tests/unit/validation/test_google_provider.py` inside `class TestGoogleProvider`:

```python
def test_supports_non_us_is_true(self, provider: GoogleProvider) -> None:
    assert provider.supports_non_us is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/validation/test_null_provider.py::TestNullProvider::test_supports_non_us_is_false tests/unit/validation/test_usps_provider.py::TestUSPSProvider::test_supports_non_us_is_false tests/unit/validation/test_google_provider.py::TestGoogleProvider::test_supports_non_us_is_true -v --no-cov
```

Expected: 3 FAILED with `AttributeError: ... has no attribute 'supports_non_us'`

- [ ] **Step 3: Add `supports_non_us` to the protocol**

In `src/address_validator/services/validation/protocol.py`, add the attribute declaration before the `validate` method:

```python
@runtime_checkable
class ValidationProvider(Protocol):
    """Async interface for address-validation backends.

    All providers receive a fully normalised :class:`~models.StandardizeResponseV1`
    (the result of the parse → standardize pipeline) rather than raw user input.
    The router owns normalisation; providers own validation only.

    Concrete implementations: :class:`~services.validation.null_provider.NullProvider`,
    :class:`~services.validation.usps_provider.USPSProvider`,
    :class:`~services.validation.google_provider.GoogleProvider`.
    """

    supports_non_us: bool
    """True if this provider can validate non-US addresses."""

    async def validate(
        self, std: StandardizeResponseV1, *, raw_input: str | None = None
    ) -> ValidateResponseV1:
        """Validate the standardised address *std* and return an authoritative response.

        Args:
            std: Fully normalised address from the parse → standardize pipeline.
            raw_input: If provided, carries the original pre-parse caller string for
                providers that store it; most providers accept and ignore it.
        """
        ...
```

- [ ] **Step 4: Add `supports_non_us = False` to NullProvider**

In `src/address_validator/services/validation/null_provider.py`, add the class attribute immediately after the class docstring (before `validate`):

```python
class NullProvider:
    """Returns ``validation.status='unavailable'`` for every request.

    Used as the default backend so the service starts cleanly without any
    external credentials.  Suitable for development and environments where
    validation is not yet required.
    """

    supports_non_us = False

    async def validate(
```

- [ ] **Step 5: Add `supports_non_us = False` to USPSProvider**

In `src/address_validator/services/validation/usps_provider.py`, add after the class docstring, before `__init__`. The class starts at line 19. Add the attribute after the closing `"""` of the docstring and before `def __init__`:

```python
    supports_non_us = False
```

- [ ] **Step 6: Add `supports_non_us = True` to GoogleProvider**

In `src/address_validator/services/validation/google_provider.py`, add after the class docstring, before `__init__`:

```python
    supports_non_us = True
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
uv run pytest tests/unit/validation/test_null_provider.py::TestNullProvider::test_supports_non_us_is_false tests/unit/validation/test_usps_provider.py::TestUSPSProvider::test_supports_non_us_is_false tests/unit/validation/test_google_provider.py::TestGoogleProvider::test_supports_non_us_is_true -v --no-cov
```

Expected: 3 PASSED

- [ ] **Step 8: Run full test suite to check no regressions**

```bash
uv run pytest --no-cov -x
```

Expected: all PASSED

- [ ] **Step 9: Commit**

```bash
git add src/address_validator/services/validation/protocol.py \
        src/address_validator/services/validation/null_provider.py \
        src/address_validator/services/validation/usps_provider.py \
        src/address_validator/services/validation/google_provider.py \
        tests/unit/validation/test_null_provider.py \
        tests/unit/validation/test_usps_provider.py \
        tests/unit/validation/test_google_provider.py
git commit -m "#89 feat: add supports_non_us to ValidationProvider protocol and leaf providers"
```

---

### Task 2: Add `supports_non_us` property to ChainProvider

**Files:**
- Modify: `src/address_validator/services/validation/chain_provider.py`
- Test: `tests/unit/validation/test_chain_provider.py`

- [ ] **Step 1: Write failing tests**

Add at the end of `class TestChainProvider` in `tests/unit/validation/test_chain_provider.py`:

```python
def test_supports_non_us_false_when_all_providers_false(self) -> None:
    p1 = AsyncMock()
    p1.supports_non_us = False
    p2 = AsyncMock()
    p2.supports_non_us = False
    chain = ChainProvider(providers=[p1, p2])
    assert chain.supports_non_us is False

def test_supports_non_us_true_when_any_provider_true(self) -> None:
    p1 = AsyncMock()
    p1.supports_non_us = False
    p2 = AsyncMock()
    p2.supports_non_us = True
    chain = ChainProvider(providers=[p1, p2])
    assert chain.supports_non_us is True

def test_supports_non_us_true_when_all_providers_true(self) -> None:
    p1 = AsyncMock()
    p1.supports_non_us = True
    p2 = AsyncMock()
    p2.supports_non_us = True
    chain = ChainProvider(providers=[p1, p2])
    assert chain.supports_non_us is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/validation/test_chain_provider.py::TestChainProvider::test_supports_non_us_false_when_all_providers_false tests/unit/validation/test_chain_provider.py::TestChainProvider::test_supports_non_us_true_when_any_provider_true tests/unit/validation/test_chain_provider.py::TestChainProvider::test_supports_non_us_true_when_all_providers_true -v --no-cov
```

Expected: 3 FAILED with `AttributeError: 'ChainProvider' object has no attribute 'supports_non_us'`

- [ ] **Step 3: Add `supports_non_us` property to ChainProvider**

In `src/address_validator/services/validation/chain_provider.py`, add this property after `__init__` and before `validate`:

```python
    @property
    def supports_non_us(self) -> bool:
        """True if any provider in the chain supports non-US address validation."""
        return any(p.supports_non_us for p in self._providers)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/validation/test_chain_provider.py::TestChainProvider::test_supports_non_us_false_when_all_providers_false tests/unit/validation/test_chain_provider.py::TestChainProvider::test_supports_non_us_true_when_any_provider_true tests/unit/validation/test_chain_provider.py::TestChainProvider::test_supports_non_us_true_when_all_providers_true -v --no-cov
```

Expected: 3 PASSED

- [ ] **Step 5: Run full test suite**

```bash
uv run pytest --no-cov -x
```

Expected: all PASSED

- [ ] **Step 6: Commit**

```bash
git add src/address_validator/services/validation/chain_provider.py \
        tests/unit/validation/test_chain_provider.py
git commit -m "#89 feat: add supports_non_us property to ChainProvider"
```

---

### Task 3: Update router to use `supports_non_us`

**Files:**
- Modify: `src/address_validator/routers/v1/validate.py`
- Test: `tests/unit/test_validate_router.py`

- [ ] **Step 1: Write failing test — chain provider succeeds for non-US**

In `tests/unit/test_validate_router.py`, add a new helper function after `_make_google_provider` (around line 259):

```python
def _make_chain_like_provider(response: ValidateResponseV1) -> AsyncMock:
    """Return a non-GoogleProvider mock with supports_non_us=True (simulates usps,google chain)."""
    provider = AsyncMock()
    provider.validate = AsyncMock(return_value=response)
    provider.supports_non_us = True
    return provider
```

Then add the following test inside `class TestValidateNonUS`:

```python
def test_non_us_chain_provider_with_google_succeeds(self, client: TestClient) -> None:
    # A chain-like provider (e.g. usps,google) that supports_non_us=True must not 422
    provider = _make_chain_like_provider(
        ValidateResponseV1(
            country="DE",
            validation=ValidationResult(status="confirmed"),
        )
    )
    with _mock_registry_with(provider):
        resp = client.post(
            "/api/v1/validate",
            json={
                "components": {"address_line_1": "Unter den Linden 1", "city": "Berlin"},
                "country": "DE",
            },
        )
    assert resp.status_code == 200
    assert resp.json()["validation"]["status"] == "confirmed"
```

- [ ] **Step 2: Run the new test to verify it fails**

```bash
uv run pytest "tests/unit/test_validate_router.py::TestValidateNonUS::test_non_us_chain_provider_with_google_succeeds" -v --no-cov
```

Expected: FAILED — 422 returned because the router still uses `isinstance(provider, GoogleProvider)`

- [ ] **Step 3: Update `_make_null_provider` to set `supports_non_us = False`**

The existing test `test_non_us_components_no_google_provider_returns_422` uses `_make_null_provider`, which returns a plain `AsyncMock()`. After the router change, `AsyncMock().supports_non_us` would be a truthy mock object, breaking that test. Fix by setting the attribute explicitly.

In `tests/unit/test_validate_router.py`, update `_make_null_provider` (around line 248):

```python
def _make_null_provider(response: ValidateResponseV1) -> AsyncMock:
    """Return a mock provider whose validate() coroutine returns *response*."""
    provider = AsyncMock()
    provider.validate = AsyncMock(return_value=response)
    provider.supports_non_us = False
    return provider
```

- [ ] **Step 4: Update the router**

In `src/address_validator/routers/v1/validate.py`, make three changes:

**4a. Remove the `GoogleProvider` import** (line 58):

Delete this line:
```python
from address_validator.services.validation.google_provider import GoogleProvider
```

**4b. Replace the `isinstance` check** (lines 151-160):

Replace:
```python
        provider = request.app.state.registry.get_provider()
        # Note: ChainProvider is not a GoogleProvider instance; usps,google chains 422 for non-US.
        if not isinstance(provider, GoogleProvider):
            raise APIError(
                status_code=422,
                error="country_not_supported",
                message=(
                    "Non-US address validation requires the Google provider. "
                    "Set VALIDATION_PROVIDER=google to enable it."
                ),
            )
```

With:
```python
        provider = request.app.state.registry.get_provider()
        if not provider.supports_non_us:
            raise APIError(
                status_code=422,
                error="country_not_supported",
                message=(
                    "Non-US address validation requires the Google provider. "
                    "Set VALIDATION_PROVIDER=google or VALIDATION_PROVIDER=usps,google."
                ),
            )
```

**4c. Update the module docstring** — change the last sentence of the Non-US paragraph (line 31):

Replace:
```python
Non-US validation requires ``VALIDATION_PROVIDER=google``.
```

With:
```python
Non-US validation requires a provider with ``supports_non_us=True`` —
``VALIDATION_PROVIDER=google`` or any chain containing a Google provider (e.g. ``usps,google``).
```

**4d. Update the OpenAPI description string** (around line 112-113):

Replace:
```python
        "Requires `VALIDATION_PROVIDER=google`.\n\n"
```

With:
```python
        "Requires `VALIDATION_PROVIDER=google` or a chain containing Google (e.g. `usps,google`).\n\n"
```

- [ ] **Step 5: Run all new and affected tests**

```bash
uv run pytest "tests/unit/test_validate_router.py::TestValidateNonUS" -v --no-cov
```

Expected: all PASSED (including the new chain test and the existing `test_non_us_components_no_google_provider_returns_422`)

- [ ] **Step 6: Run full test suite**

```bash
uv run pytest --no-cov -x
```

Expected: all PASSED

- [ ] **Step 7: Run ruff**

```bash
uv run ruff check . --fix && uv run ruff format .
```

Expected: no errors

- [ ] **Step 8: Commit**

```bash
git add src/address_validator/routers/v1/validate.py \
        tests/unit/test_validate_router.py
git commit -m "#89 fix: use supports_non_us instead of isinstance(GoogleProvider) in validate router"
```

---

### Task 4: Update AGENTS.md

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Remove the stale "known limitation" note**

In `AGENTS.md`, find the `google_provider.py` sensitive area entry (line 209). It currently ends with:

```
; `isinstance(provider, GoogleProvider)` check in `validate.py` breaks for `ChainProvider` (known limitation, documented inline)
```

Remove that trailing clause so the entry ends after `_map_response_international``:

The full replacement for just that clause: remove "; `isinstance(provider, GoogleProvider)` check in `validate.py` breaks for `ChainProvider` (known limitation, documented inline)" from the end of the table cell.

- [ ] **Step 2: Run full test suite one final time**

```bash
uv run pytest --no-cov -x
```

Expected: all PASSED

- [ ] **Step 3: Commit**

```bash
git add AGENTS.md
git commit -m "#89 docs: remove stale ChainProvider limitation note from AGENTS.md"
```
