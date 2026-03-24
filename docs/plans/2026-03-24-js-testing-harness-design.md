# JS Testing Harness for Admin Scripts

**Issue:** #68
**Date:** 2026-03-24

## Summary

Add minimal JS testing via Vitest + jsdom. Refactor `theme.js` and `nav.js`
from IIFEs to ES modules for direct import in tests.

## Decisions

- **Test runner:** Vitest + jsdom (dev-only Node deps, sub-second runs)
- **Test location:** `tests/js/` — consistent with existing `tests/` convention
- **CI integration:** Standalone `npm test`, decoupled from Python suite
- **Source refactor:** ES modules — removes IIFE wrappers, enables direct import

## Source changes

### theme.js

- Remove IIFE wrapper
- Export `KEY` constant and `apply(theme)` function
- Side-effect code (init, matchMedia listener, click delegation) at module top level
- Template: `<script type="module">` on base.html

FOUC prevention: inline `<head>` snippet is untouched — handles initial paint.
Only tradeoff is sub-ms delay before toggle click handler is registered.

### nav.js

- Remove IIFE wrapper
- Extract logic into exported `initNav()` returning `{ closeMenu }`
- Call `initNav()` at module top level
- Template: `<script type="module">` replaces `defer` — identical timing

### base.html

- `theme.js` script tag: add `type="module"`
- `nav.js` script tag: replace `defer` with `type="module"`

## Test coverage

### theme.test.js

- `apply('dark')` adds `dark` class to `<html>`
- `apply('light')` removes `dark` class
- Toggle click on `#theme-toggle` flips theme and persists to localStorage
- System preference `change` event applies when no localStorage override

### nav.test.js

- Click `#nav-toggle` toggles `hidden` on `#mobile-nav`, swaps icon visibility, sets `aria-expanded`
- Second click closes menu
- Escape key closes menu and focuses button
- Early return (no-op) when `#nav-toggle` or `#mobile-nav` missing

## New files

- `package.json` — `type: "module"`, devDependencies (vitest, jsdom)
- `vitest.config.js` — jsdom environment
- `tests/js/theme.test.js`
- `tests/js/nav.test.js`

## No changes to

- Inline FOUC snippet in base.html `<head>`
- Any Python code or tests
- Pre-commit hooks
