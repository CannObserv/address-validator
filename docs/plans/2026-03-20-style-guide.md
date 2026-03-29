# Style Guide & Design System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Cannabis Observer branding, `co-purple` color system, dark mode, hamburger nav, and codify everything in `docs/STYLE.md`.

**Architecture:** Tailwind CSS extended with custom `co-purple`/`co-green` color tokens and `darkMode: 'class'`. Two new JS files (`theme.js`, `nav.js`) handle dark mode toggle and hamburger nav. All six admin templates updated: blue→purple, dark variants added. Brand SVG assets copied from sibling project.

**Tech Stack:** Tailwind CSS (standalone CLI, pre-built), Jinja2, HTMX, vanilla JS

**Design doc:** `docs/plans/2026-03-20-style-guide-design.md`

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `src/address_validator/static/admin/images/cannabis_observer-icon-square.svg` | Brand icon |
| Create | `src/address_validator/static/admin/images/cannabis_observer-name.svg` | Brand wordmark (available, not actively used) |
| Create | `src/address_validator/static/admin/js/theme.js` | Dark mode toggle + localStorage persistence |
| Create | `src/address_validator/static/admin/js/nav.js` | Hamburger nav toggle + Escape key handler |
| Modify | `tailwind.config.js` | Add `co-purple`, `co-green` colors; `darkMode: 'class'` |
| Modify | `src/address_validator/static/admin/css/input.css` | Skip-to-content utility, dark mode base layer |
| Modify | `src/address_validator/templates/admin/base.html` | Brand header/footer, hamburger nav, dark mode toggle, JS imports, `<html class>` support |
| Modify | `src/address_validator/templates/admin/dashboard.html` | `blue-*` → `co-purple-*`, add `dark:` variants |
| Modify | `src/address_validator/templates/admin/audit/list.html` | Same |
| Modify | `src/address_validator/templates/admin/audit/_rows.html` | Same |
| Modify | `src/address_validator/templates/admin/endpoints/detail.html` | Same |
| Modify | `src/address_validator/templates/admin/providers/detail.html` | Same |
| Create | `docs/STYLE.md` | Style guide document |
| Modify | `AGENTS.md` | Add `docs/STYLE.md` reference in architecture tree |
| Rebuild | `src/address_validator/static/admin/css/tailwind.css` | Handled by pre-commit hook |

---

## Task 1: Tailwind Config — Color Tokens and Dark Mode

**Files:**
- Modify: `tailwind.config.js`

- [ ] **Step 1: Update tailwind.config.js with co-purple, co-green, and darkMode**

Replace the entire file content with:

```js
/** @type {import('tailwindcss').Config} */
module.exports = {
    darkMode: 'class',
    content: [
        "./src/address_validator/templates/**/*.html",
    ],
    theme: {
        extend: {
            colors: {
                'co-green': '#8cbe69',
                'co-purple': {
                    DEFAULT: '#6d4488',
                    50:  '#f5f0f8',
                    100: '#ebe1f1',
                    600: '#6d4488',
                    700: '#5a3870',
                    800: '#472c59',
                },
            },
        },
    },
}
```

- [ ] **Step 2: Verify Tailwind builds cleanly**

Run: `scripts/build-css.sh`
Expected: exits 0, `src/address_validator/static/admin/css/tailwind.css` is updated

- [ ] **Step 3: Commit**

```bash
git add tailwind.config.js
git commit -m "#46 feat: add co-purple/co-green color tokens and darkMode class to Tailwind config"
```

---

## Task 2: Brand Assets — SVG Icons

**Files:**
- Create: `src/address_validator/static/admin/images/cannabis_observer-icon-square.svg`
- Create: `src/address_validator/static/admin/images/cannabis_observer-name.svg`

- [ ] **Step 1: Create images directory and copy SVGs**

```bash
mkdir -p src/address_validator/static/admin/images
```

Download the icon and wordmark SVGs from the CannObserv/wslcb-licensing-tracker repo:

```bash
export GH_TOKEN=$(grep GITHUB_TOKEN env | cut -d= -f2)
gh api repos/CannObserv/wslcb-licensing-tracker/contents/static/images/cannabis_observer-icon-square.svg --jq '.content' | base64 -d > src/address_validator/static/admin/images/cannabis_observer-icon-square.svg
gh api repos/CannObserv/wslcb-licensing-tracker/contents/static/images/cannabis_observer-name.svg --jq '.content' | base64 -d > src/address_validator/static/admin/images/cannabis_observer-name.svg
```

- [ ] **Step 2: Verify files exist and are valid SVG**

```bash
head -1 src/address_validator/static/admin/images/cannabis_observer-icon-square.svg
head -1 src/address_validator/static/admin/images/cannabis_observer-name.svg
```

Expected: both start with `<svg` or `<?xml`

- [ ] **Step 3: Commit**

```bash
git add src/address_validator/static/admin/images/
git commit -m "#46 feat: add Cannabis Observer brand SVG assets"
```

---

## Task 3: JavaScript — Dark Mode Toggle (`theme.js`)

**Files:**
- Create: `src/address_validator/static/admin/js/theme.js`

- [ ] **Step 1: Create the JS directory and theme.js**

```bash
mkdir -p src/address_validator/static/admin/js
```

Write `src/address_validator/static/admin/js/theme.js`:

```js
/**
 * theme.js — Dark mode toggle with localStorage persistence.
 *
 * Default: follows prefers-color-scheme. Manual toggle overrides and
 * persists to localStorage. Loaded at end of <body> with defer.
 */
(function () {
    var KEY = 'theme';
    var html = document.documentElement;

    function apply(theme) {
        html.classList.toggle('dark', theme === 'dark');
    }

    /* Restore saved preference or follow system */
    var stored = localStorage.getItem(KEY);
    if (stored) {
        apply(stored);
    } else {
        apply(window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    }

    /* React to OS-level changes when no manual override exists */
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function (e) {
        if (!localStorage.getItem(KEY)) {
            apply(e.matches ? 'dark' : 'light');
        }
    });

    /* Toggle button wired up after DOM ready */
    document.addEventListener('DOMContentLoaded', function () {
        var btn = document.getElementById('theme-toggle');
        if (!btn) return;
        btn.addEventListener('click', function () {
            var next = html.classList.contains('dark') ? 'light' : 'dark';
            localStorage.setItem(KEY, next);
            apply(next);
        });
    });
}());
```

- [ ] **Step 2: Commit**

```bash
git add src/address_validator/static/admin/js/theme.js
git commit -m "#46 feat: add dark mode toggle JS with localStorage persistence"
```

---

## Task 4: JavaScript — Hamburger Navigation (`nav.js`)

**Files:**
- Create: `src/address_validator/static/admin/js/nav.js`

- [ ] **Step 1: Write nav.js**

Write `src/address_validator/static/admin/js/nav.js`:

```js
/**
 * nav.js — Hamburger navigation toggle for mobile viewports.
 *
 * Toggles #mobile-nav visibility, swaps open/close SVG icons,
 * manages aria-expanded state, and closes on Escape key.
 */
(function () {
    var btn = document.getElementById('nav-toggle');
    var menu = document.getElementById('mobile-nav');
    var iconOpen = document.getElementById('nav-icon-open');
    var iconClose = document.getElementById('nav-icon-close');

    if (!btn || !menu) return;

    function closeMenu() {
        menu.classList.add('hidden');
        btn.setAttribute('aria-expanded', 'false');
        if (iconOpen) iconOpen.classList.remove('hidden');
        if (iconClose) iconClose.classList.add('hidden');
    }

    btn.addEventListener('click', function () {
        var wasHidden = menu.classList.toggle('hidden');
        btn.setAttribute('aria-expanded', String(!wasHidden));
        if (iconOpen) iconOpen.classList.toggle('hidden', !wasHidden);
        if (iconClose) iconClose.classList.toggle('hidden', wasHidden);
    });

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && !menu.classList.contains('hidden')) {
            closeMenu();
            btn.focus();
        }
    });
}());
```

- [ ] **Step 2: Commit**

```bash
git add src/address_validator/static/admin/js/nav.js
git commit -m "#46 feat: add hamburger navigation JS for mobile viewports"
```

---

## Task 5: Input CSS — Skip-to-Content and Dark Base Styles

**Files:**
- Modify: `src/address_validator/static/admin/css/input.css`

- [ ] **Step 1: Update input.css**

Replace the file with:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

/* HTMX loading indicator */
.htmx-request .htmx-indicator {
    display: inline-block;
}
.htmx-indicator {
    display: none;
}

/* Skip-to-content link — visible only on focus */
.skip-to-content {
    @apply sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:px-4 focus:py-2 focus:bg-co-purple focus:text-white focus:rounded focus:text-sm focus:font-medium;
}
```

- [ ] **Step 2: Verify Tailwind builds cleanly**

Run: `scripts/build-css.sh`
Expected: exits 0

- [ ] **Step 3: Commit**

```bash
git add src/address_validator/static/admin/css/input.css
git commit -m "#46 feat: add skip-to-content utility to input.css"
```

---

## Task 6: Base Template — Full Overhaul

This is the largest single task. It rewrites `base.html` with: Cannabis Observer branding (header + footer), hamburger nav replacing horizontal scroll, dark mode toggle button, skip-to-content link, JS file imports, and `dark:` variants on all utility classes.

**Files:**
- Modify: `src/address_validator/templates/admin/base.html`

- [ ] **Step 1: Write the failing test — brand elements present**

Add to `tests/unit/test_admin_views.py`:

```python
def test_admin_dashboard_has_brand_elements(client: TestClient, admin_headers: dict) -> None:
    """Dashboard contains Cannabis Observer branding."""
    response = client.get("/admin/", headers=admin_headers)
    html = response.text
    assert "cannabis_observer-icon-square.svg" in html
    assert "Cannabis Observer" in html
    assert "Address Validator" in html


def test_admin_dashboard_has_dark_mode_toggle(client: TestClient, admin_headers: dict) -> None:
    """Dashboard contains a dark mode toggle button."""
    response = client.get("/admin/", headers=admin_headers)
    assert 'id="theme-toggle"' in response.text


def test_admin_dashboard_has_hamburger_nav(client: TestClient, admin_headers: dict) -> None:
    """Dashboard contains hamburger nav elements for mobile."""
    response = client.get("/admin/", headers=admin_headers)
    html = response.text
    assert 'id="nav-toggle"' in html
    assert 'id="mobile-nav"' in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_admin_views.py::test_admin_dashboard_has_brand_elements tests/unit/test_admin_views.py::test_admin_dashboard_has_dark_mode_toggle tests/unit/test_admin_views.py::test_admin_dashboard_has_hamburger_nav --no-cov -x -v`
Expected: FAIL — none of these elements exist in current template

- [ ] **Step 3: Rewrite base.html**

Replace `src/address_validator/templates/admin/base.html` with:

```html
<!DOCTYPE html>
<html lang="en" class="h-full">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}Admin{% endblock %} — Address Validator</title>
    <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Ccircle cx='13' cy='13' r='9' fill='none' stroke='%236d4488' stroke-width='3'/%3E%3Cline x1='20' y1='20' x2='29' y2='29' stroke='%236d4488' stroke-width='3' stroke-linecap='round'/%3E%3C/svg%3E">
    <link rel="stylesheet" href="/static/admin/css/tailwind.css?v={{ css_version }}">
    <script>
    /* Synchronous dark-mode init — prevents FOUC */
    (function(){var t=localStorage.getItem('theme');var d=t?t==='dark':window.matchMedia('(prefers-color-scheme: dark)').matches;document.documentElement.classList.toggle('dark',d)}())
    </script>
    <script src="/static/admin/js/theme.js" defer></script>
    <script src="https://unpkg.com/htmx.org@1.9.12" defer></script>
</head>
<body class="h-full bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-gray-100" hx-boost="true">
    <a href="#main-content" class="skip-to-content">Skip to content</a>
    <header class="bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
        <div class="px-4 py-3 flex items-center justify-between">
            <a href="/admin/" class="flex items-center gap-2">
                <img src="/static/admin/images/cannabis_observer-icon-square.svg" alt="Cannabis Observer" class="w-8 h-8" width="32" height="32">
                <span class="text-lg font-semibold text-gray-800 dark:text-gray-100">Address Validator</span>
            </a>
            <div class="flex items-center gap-3">
                <span class="text-sm text-gray-600 dark:text-gray-400 hidden sm:inline">{{ user.email }}</span>
                {# Dark mode toggle #}
                <button id="theme-toggle" type="button"
                        class="p-2 rounded-md text-gray-500 dark:text-gray-400 hover:text-co-purple dark:hover:text-co-purple-100 hover:bg-gray-100 dark:hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100 focus:ring-offset-1 dark:ring-offset-gray-800 min-h-[44px] min-w-[44px] inline-flex items-center justify-center"
                        aria-label="Toggle dark mode">
                    {# Sun icon (shown in dark mode) #}
                    <svg class="w-5 h-5 hidden dark:block" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"/>
                    </svg>
                    {# Moon icon (shown in light mode) #}
                    <svg class="w-5 h-5 block dark:hidden" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"/>
                    </svg>
                </button>
                <form method="POST" action="/__exe.dev/logout" class="inline">
                    <button type="submit"
                            class="text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 underline focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100 focus:ring-offset-1 dark:ring-offset-gray-800 rounded min-h-[44px] min-w-[44px] inline-flex items-center">
                        Logout
                    </button>
                </form>
                {# Mobile hamburger button #}
                <button id="nav-toggle" type="button"
                        class="md:hidden p-2 rounded-md text-gray-500 dark:text-gray-400 hover:text-co-purple dark:hover:text-co-purple-100 hover:bg-gray-100 dark:hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100 focus:ring-offset-1 dark:ring-offset-gray-800 min-h-[44px] min-w-[44px] inline-flex items-center justify-center"
                        aria-expanded="false" aria-controls="mobile-nav" aria-label="Open navigation menu">
                    <svg id="nav-icon-open" class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"/>
                    </svg>
                    <svg id="nav-icon-close" class="w-6 h-6 hidden" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
                    </svg>
                </button>
            </div>
        </div>
        {# Mobile nav dropdown #}
        <div id="mobile-nav" class="hidden md:hidden border-t border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
            <div class="px-4 py-3 space-y-1">
                <a href="/admin/" class="block py-2 text-sm min-h-[44px] flex items-center {% if active_nav == 'dashboard' %}text-co-purple dark:text-co-purple-100 font-medium{% else %}text-gray-700 dark:text-gray-300 hover:text-co-purple-700 dark:hover:text-co-purple-100{% endif %}">Dashboard</a>
                <a href="/admin/audit/" class="block py-2 text-sm min-h-[44px] flex items-center {% if active_nav == 'audit' %}text-co-purple dark:text-co-purple-100 font-medium{% else %}text-gray-700 dark:text-gray-300 hover:text-co-purple-700 dark:hover:text-co-purple-100{% endif %}">Audit Log</a>
                {% for ep in ['parse', 'standardize', 'validate'] %}
                <a href="/admin/endpoints/{{ ep }}" class="block py-2 text-sm min-h-[44px] flex items-center {% if active_nav == 'endpoint_' + ep %}text-co-purple dark:text-co-purple-100 font-medium{% else %}text-gray-700 dark:text-gray-300 hover:text-co-purple-700 dark:hover:text-co-purple-100{% endif %}">/{{ ep }}</a>
                {% endfor %}
                {% for prov in ['usps', 'google'] %}
                <a href="/admin/providers/{{ prov }}" class="block py-2 text-sm min-h-[44px] flex items-center {% if active_nav == 'provider_' + prov %}text-co-purple dark:text-co-purple-100 font-medium{% else %}text-gray-700 dark:text-gray-300 hover:text-co-purple-700 dark:hover:text-co-purple-100{% endif %}">{{ prov | upper }}</a>
                {% endfor %}
                <div class="py-1 text-xs text-gray-400 font-mono sm:hidden">{{ user.email }}</div>
            </div>
        </div>
    </header>
    <div class="flex flex-col md:flex-row">
        {# Desktop sidebar nav — hidden on mobile (hamburger replaces it) #}
        <nav class="hidden md:block w-56 bg-white dark:bg-gray-800 border-r border-gray-200 dark:border-gray-700 md:min-h-[calc(100vh-57px)]"
             aria-label="Admin navigation">
            <ul class="p-4 flex flex-col gap-1">
                <li><a href="/admin/" class="block px-3 py-2 rounded text-sm font-medium min-h-[44px] flex items-center {% if active_nav == 'dashboard' %}bg-co-purple-50 text-co-purple-700 dark:bg-co-purple-800 dark:text-co-purple-100{% else %}text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700{% endif %} focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100">Dashboard</a></li>
                <li><a href="/admin/audit/" class="block px-3 py-2 rounded text-sm font-medium min-h-[44px] flex items-center {% if active_nav == 'audit' %}bg-co-purple-50 text-co-purple-700 dark:bg-co-purple-800 dark:text-co-purple-100{% else %}text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700{% endif %} focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100">Audit Log</a></li>
                <li class="text-xs text-gray-400 uppercase tracking-wide pt-3 pb-1 px-3">Endpoints</li>
                {% for ep in ['parse', 'standardize', 'validate'] %}
                <li><a href="/admin/endpoints/{{ ep }}" class="block px-3 py-2 rounded text-sm min-h-[44px] flex items-center {% if active_nav == 'endpoint_' + ep %}bg-co-purple-50 text-co-purple-700 dark:bg-co-purple-800 dark:text-co-purple-100{% else %}text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700{% endif %} focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100">/{{ ep }}</a></li>
                {% endfor %}
                <li class="text-xs text-gray-400 uppercase tracking-wide pt-3 pb-1 px-3">Providers</li>
                {% for prov in ['usps', 'google'] %}
                <li><a href="/admin/providers/{{ prov }}" class="block px-3 py-2 rounded text-sm min-h-[44px] flex items-center {% if active_nav == 'provider_' + prov %}bg-co-purple-50 text-co-purple-700 dark:bg-co-purple-800 dark:text-co-purple-100{% else %}text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700{% endif %} focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100">{{ prov | upper }}</a></li>
                {% endfor %}
            </ul>
        </nav>
        <main id="main-content" class="flex-1 p-4 md:p-6" aria-live="polite">
            {% block content %}{% endblock %}
        </main>
    </div>
    <footer class="border-t border-gray-200 dark:border-gray-700 mt-12">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
            <div class="text-xs text-gray-500 dark:text-gray-400 flex items-center justify-center gap-1">
                A project of
                <a href="https://cannabis.observer/"
                   class="inline-flex items-center gap-1 font-medium text-co-purple hover:text-co-purple-700 dark:text-co-purple-100 dark:hover:text-white"
                   target="_blank" rel="noopener noreferrer">
                    <img src="/static/admin/images/cannabis_observer-icon-square.svg" alt="" class="w-4 h-4" width="16" height="16">
                    Cannabis Observer
                </a>
                <span aria-hidden="true">🌱🏛️🔍</span>
            </div>
        </div>
    </footer>
    <script src="/static/admin/js/nav.js" defer></script>
</body>
</html>
```

Key changes from the current `base.html`:
- `<html class="h-full">` — enables dark mode class toggling
- Favicon: inline SVG data URI (purple magnifying glass)
- Inline `<script>` in `<head>` reads `localStorage`/`prefers-color-scheme` synchronously to prevent FOUC
- `theme.js` loaded in `<head>` with `defer` (wires up toggle button after DOM ready)
- Skip-to-content link as first body child
- Header: CO icon + "Address Validator" text, dark mode toggle button, hamburger button (md:hidden)
- Mobile nav: `#mobile-nav` dropdown drawer (hidden by default, toggled by nav.js)
- Desktop nav: `hidden md:block` sidebar (category headers always visible on desktop)
- All `blue-*` → `co-purple-*` with corresponding `dark:` variants
- Footer: "A project of Cannabis Observer" + icon + emoji (aria-hidden)
- `nav.js` loaded at end of body with `defer`

- [ ] **Step 4: Run the new tests**

Run: `uv run pytest tests/unit/test_admin_views.py::test_admin_dashboard_has_brand_elements tests/unit/test_admin_views.py::test_admin_dashboard_has_dark_mode_toggle tests/unit/test_admin_views.py::test_admin_dashboard_has_hamburger_nav --no-cov -x -v`
Expected: PASS

- [ ] **Step 5: Run all existing admin tests to ensure no regressions**

Run: `uv run pytest tests/unit/test_admin_views.py --no-cov -x -v`
Expected: all PASS

Note: the existing `test_audit_htmx_boosted_returns_full_page` test checks for `<nav` in the response — our new template still has `<nav` in the desktop sidebar, so this passes. The `test_*_htmx_nonboosted_returns_partial` tests check that `<nav` is NOT in partial responses — those don't include base.html so they also pass.

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_admin_views.py src/address_validator/templates/admin/base.html
git commit -m "#46 feat: rebrand base template with CO identity, dark mode, hamburger nav"
```

---

## Task 7: Dashboard Template — Color Migration + Dark Variants

**Files:**
- Modify: `src/address_validator/templates/admin/dashboard.html`

- [ ] **Step 1: Update dashboard.html**

Changes required (applied to the existing file):

1. All `text-gray-800` headings → add `dark:text-gray-100`
2. All `bg-white` cards → add `dark:bg-gray-800`
3. All `border-gray-200` → add `dark:border-gray-700`
4. All `text-gray-500` labels → add `dark:text-gray-400`
5. All `text-gray-900` values → add `dark:text-gray-100`
6. All `bg-gray-100` (border-t inside cards) → add `dark:border-gray-600`
7. All `text-gray-400` (breakdown endpoint labels) → add `dark:text-gray-500`
8. `bg-blue-600` (quota progress bar fill) → `bg-co-purple`
9. `bg-gray-200` (progress bar track) → add `dark:bg-gray-600`

Full replacement for `dashboard.html`:

```html
{% extends "admin/base.html" %}
{% block title %}Dashboard{% endblock %}
{% block content %}
<h1 class="text-2xl font-bold text-gray-800 dark:text-gray-100 mb-6">Dashboard</h1>
{% set bd = stats.get("endpoint_breakdown", {}) %}
{% set ep_order = ["/parse", "/standardize", "/validate", "other"] %}
<div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 mb-4">
    {% for label, key, period in [("All Requests", "requests_all", "all"), ("Requests This Week", "requests_week", "week"), ("Requests Today", "requests_today", "today")] %}
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">{{ label }}</p>
        <p class="text-2xl font-bold text-gray-900 dark:text-gray-100">{{ stats.get(key, 0) }}</p>
        {% set period_bd = bd.get(period, {}) %}
        {% if period_bd %}
        <div class="mt-2 border-t border-gray-100 dark:border-gray-600 pt-2 space-y-0.5">
            {% for ep in ep_order %}
            {% if ep in period_bd %}
            <div class="flex justify-between text-xs">
                <span class="text-gray-400 dark:text-gray-500">{{ ep }}</span>
                <span class="text-gray-500 dark:text-gray-400 font-medium">{{ period_bd[ep] }}</span>
            </div>
            {% endif %}
            {% endfor %}
        </div>
        {% endif %}
    </div>
    {% endfor %}
</div>
<div class="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-8">
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">Cache Hit Rate</p>
        <p class="text-2xl font-bold text-gray-900 dark:text-gray-100">
            {% if stats.get("cache_hit_rate") is not none %}{{ "%.1f" | format(stats.cache_hit_rate) }}%{% else %}N/A{% endif %}
        </p>
    </div>
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">Error Rate (Today)</p>
        <p class="text-2xl font-bold {% if stats.get("error_rate") and stats.error_rate > 5 %}text-red-600{% else %}text-gray-900 dark:text-gray-100{% endif %}">
            {% if stats.get("error_rate") is not none %}{{ "%.1f" | format(stats.error_rate) }}%{% else %}N/A{% endif %}
        </p>
    </div>
</div>
{% if quota %}
<h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-3">Validation Provider Quota</h2>
<div class="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-8">
    {% for q in quota %}
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">{{ q.provider | upper }} Daily Quota</p>
        <div class="flex items-baseline gap-2">
            <p class="text-2xl font-bold text-gray-900 dark:text-gray-100">{{ q.remaining }}</p>
            <p class="text-sm text-gray-500 dark:text-gray-400">/ {{ q.limit }}</p>
        </div>
        <div class="mt-2 w-full bg-gray-200 dark:bg-gray-600 rounded-full h-2" role="progressbar"
             aria-valuenow="{{ q.remaining }}" aria-valuemin="0" aria-valuemax="{{ q.limit }}"
             aria-label="{{ q.provider }} quota usage">
            <div class="bg-co-purple h-2 rounded-full"
                 style="width: {{ ((q.remaining / q.limit) * 100) | int if q.limit > 0 else 0 }}%"></div>
        </div>
    </div>
    {% endfor %}
</div>
{% endif %}
{% endblock %}
```

- [ ] **Step 2: Run admin tests**

Run: `uv run pytest tests/unit/test_admin_views.py --no-cov -x -v`
Expected: all PASS

- [ ] **Step 3: Commit**

```bash
git add src/address_validator/templates/admin/dashboard.html
git commit -m "#46 feat: migrate dashboard template to co-purple + dark mode"
```

---

## Task 8: Audit Templates — Color Migration + Dark Variants

**Files:**
- Modify: `src/address_validator/templates/admin/audit/list.html`
- Modify: `src/address_validator/templates/admin/audit/_rows.html`

- [ ] **Step 1: Update audit/list.html**

Changes:
1. `text-gray-800` → add `dark:text-gray-100`
2. `bg-white` → add `dark:bg-gray-800`
3. `border-gray-200` → add `dark:border-gray-700`
4. `border-gray-300` (inputs, pagination) → add `dark:border-gray-600`
5. `bg-gray-50` (thead) → add `dark:bg-gray-700`
6. `text-gray-500` → add `dark:text-gray-400`
7. All `focus:ring-blue-500` → `focus:ring-co-purple-700 dark:focus:ring-co-purple-100`
8. `bg-blue-600` (Filter button) → `bg-co-purple`
9. `hover:bg-blue-700` → `hover:bg-co-purple-700`
10. `hover:bg-gray-100` (pagination) → add `dark:hover:bg-gray-700`
11. Add `dark:bg-gray-800 dark:text-gray-100` to inputs/selects

Full replacement for `audit/list.html`:

```html
{% extends "admin/base.html" %}
{% block title %}Audit Log{% endblock %}
{% block content %}
<h1 class="text-2xl font-bold text-gray-800 dark:text-gray-100 mb-6">Audit Log</h1>

<form class="flex flex-wrap gap-3 mb-6 items-end"
      hx-get="/admin/audit/"
      hx-target="#audit-rows"
      hx-push-url="true">
    <div>
        <label for="client_ip" class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Client IP</label>
        <input type="text" name="client_ip" id="client_ip" value="{{ filters.client_ip or '' }}"
               class="border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 rounded px-2 py-1 text-sm w-40 focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100"
               placeholder="e.g. 10.0.0.1">
    </div>
    <div>
        <label for="endpoint" class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Endpoint</label>
        <select name="endpoint" id="endpoint"
                class="border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 rounded px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100">
            <option value="">All</option>
            {% for ep in ['parse', 'standardize', 'validate', 'health'] %}
            <option value="{{ ep }}" {% if filters.endpoint == ep %}selected{% endif %}>{{ ep }}</option>
            {% endfor %}
        </select>
    </div>
    <div>
        <label for="status_min" class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Min Status</label>
        <input type="number" name="status_min" id="status_min" value="{{ filters.status_min or '' }}"
               class="border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 rounded px-2 py-1 text-sm w-24 focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100"
               placeholder="400" min="100" max="599">
    </div>
    <button type="submit"
            class="bg-co-purple text-white px-4 py-1 rounded text-sm font-medium hover:bg-co-purple-700 focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100 focus:ring-offset-1 dark:ring-offset-gray-800 min-h-[32px]">
        Filter
    </button>
    <a href="/admin/audit/"
       class="text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 underline self-center">Clear</a>
</form>

<div class="overflow-x-auto bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
    <table class="w-full text-left">
        <thead class="sticky top-0 bg-gray-50 dark:bg-gray-700 border-b border-gray-200 dark:border-gray-600 text-xs text-gray-500 dark:text-gray-400 uppercase tracking-wide">
            <tr>
                <th class="px-3 py-2">Time</th>
                <th class="px-3 py-2">IP</th>
                <th class="px-3 py-2">Method</th>
                <th class="px-3 py-2">Endpoint</th>
                <th class="px-3 py-2">Status</th>
                <th class="px-3 py-2 text-right">Latency</th>
                <th class="px-3 py-2">Provider</th>
                <th class="px-3 py-2">Cache</th>
            </tr>
        </thead>
        <tbody id="audit-rows">
            {% include "admin/audit/_rows.html" %}
        </tbody>
    </table>
</div>

{% if total_pages > 1 %}
<nav class="flex items-center gap-2 mt-4 text-sm" aria-label="Pagination">
    {% if page > 1 %}
    <a href="?page={{ page - 1 }}{% if filters.client_ip %}&client_ip={{ filters.client_ip }}{% endif %}{% if filters.endpoint %}&endpoint={{ filters.endpoint }}{% endif %}{% if filters.status_min %}&status_min={{ filters.status_min }}{% endif %}"
       class="px-3 py-1 rounded border border-gray-300 dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100">&laquo; Prev</a>
    {% endif %}
    <span class="text-gray-500 dark:text-gray-400">Page {{ page }} of {{ total_pages }}</span>
    {% if page < total_pages %}
    <a href="?page={{ page + 1 }}{% if filters.client_ip %}&client_ip={{ filters.client_ip }}{% endif %}{% if filters.endpoint %}&endpoint={{ filters.endpoint }}{% endif %}{% if filters.status_min %}&status_min={{ filters.status_min }}{% endif %}"
       class="px-3 py-1 rounded border border-gray-300 dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100">Next &raquo;</a>
    {% endif %}
</nav>
{% endif %}
{% endblock %}
```

- [ ] **Step 2: Update audit/_rows.html**

```html
{% for row in rows %}
<tr class="border-b border-gray-100 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700 text-sm">
    <td class="px-3 py-2 whitespace-nowrap text-gray-500 dark:text-gray-400">{{ row["timestamp"].strftime('%Y-%m-%d %H:%M:%S') if row["timestamp"] else '' }}</td>
    <td class="px-3 py-2 whitespace-nowrap text-gray-700 dark:text-gray-300">{{ row["client_ip"] or '' }}</td>
    <td class="px-3 py-2 whitespace-nowrap text-gray-700 dark:text-gray-300">{{ row["method"] or '' }}</td>
    <td class="px-3 py-2 whitespace-nowrap text-gray-700 dark:text-gray-300">{{ row["endpoint"] or '' }}</td>
    <td class="px-3 py-2 whitespace-nowrap">
        {% if row["status_code"] and row["status_code"] < 400 %}
            <span class="inline-flex items-center gap-1 text-green-700 dark:text-green-400">&#10003; {{ row["status_code"] }}</span>
        {% elif row["status_code"] and row["status_code"] < 500 %}
            <span class="inline-flex items-center gap-1 text-yellow-600 dark:text-yellow-400">&#9650; {{ row["status_code"] }}</span>
        {% elif row["status_code"] %}
            <span class="inline-flex items-center gap-1 text-red-600 dark:text-red-400">&#10005; {{ row["status_code"] }}</span>
        {% endif %}
    </td>
    <td class="px-3 py-2 whitespace-nowrap text-gray-700 dark:text-gray-300 text-right">{% if row["latency_ms"] is not none %}{{ row["latency_ms"] }}ms{% endif %}</td>
    <td class="px-3 py-2 whitespace-nowrap text-gray-700 dark:text-gray-300">{{ row["provider"] or '' }}</td>
    <td class="px-3 py-2 whitespace-nowrap">
        {% if row["cache_hit"] is true %}
            <span class="text-green-600 dark:text-green-400 font-medium">HIT</span>
        {% elif row["cache_hit"] is false %}
            <span class="text-gray-400 dark:text-gray-500">MISS</span>
        {% endif %}
    </td>
</tr>
{% else %}
<tr><td colspan="8" class="px-3 py-8 text-center text-gray-400 dark:text-gray-500">No audit log entries found.</td></tr>
{% endfor %}
```

- [ ] **Step 3: Run admin tests**

Run: `uv run pytest tests/unit/test_admin_views.py --no-cov -x -v`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add src/address_validator/templates/admin/audit/list.html src/address_validator/templates/admin/audit/_rows.html
git commit -m "#46 feat: migrate audit templates to co-purple + dark mode"
```

---

## Task 9: Endpoint Detail Template — Color Migration + Dark Variants

**Files:**
- Modify: `src/address_validator/templates/admin/endpoints/detail.html`

- [ ] **Step 1: Update endpoints/detail.html**

Same pattern as dashboard — all changes are:
1. `text-gray-800` → add `dark:text-gray-100`
2. `bg-white` → add `dark:bg-gray-800`
3. `border-gray-200` → add `dark:border-gray-700`
4. `text-gray-500` → add `dark:text-gray-400`
5. `text-gray-900` → add `dark:text-gray-100`
6. `border-gray-300` → add `dark:border-gray-600`
7. `bg-gray-50` (thead) → add `dark:bg-gray-700`
8. All `focus:ring-blue-500` → `focus:ring-co-purple-700 dark:focus:ring-co-purple-100`
9. `bg-blue-600` → `bg-co-purple`, `hover:bg-blue-700` → `hover:bg-co-purple-700`
10. `hover:bg-gray-100` → add `dark:hover:bg-gray-700`
11. Form inputs: add `bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100`

Full replacement for `endpoints/detail.html`:

```html
{% extends "admin/base.html" %}
{% block title %}/{{ endpoint_name }} — Endpoint{% endblock %}
{% block content %}
<h1 class="text-2xl font-bold text-gray-800 dark:text-gray-100 mb-6">/api/v1/{{ endpoint_name }}</h1>

<div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">Requests Today</p>
        <p class="text-2xl font-bold text-gray-900 dark:text-gray-100">{{ stats.today | default(0) }}</p>
    </div>
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">This Week</p>
        <p class="text-2xl font-bold text-gray-900 dark:text-gray-100">{{ stats.week | default(0) }}</p>
    </div>
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">Avg Latency</p>
        <p class="text-2xl font-bold text-gray-900 dark:text-gray-100">
            {% if stats.get("avg_latency_ms") is not none %}{{ stats.avg_latency_ms }}ms{% else %}N/A{% endif %}
        </p>
    </div>
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">Error Rate</p>
        <p class="text-2xl font-bold {% if stats.get("error_rate") and stats.error_rate > 5 %}text-red-600{% else %}text-gray-900 dark:text-gray-100{% endif %}">
            {% if stats.get("error_rate") is not none %}{{ "%.1f" | format(stats.error_rate) }}%{% else %}N/A{% endif %}
        </p>
    </div>
</div>

{% if stats.status_codes %}
<h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-3">Status Codes</h2>
<div class="flex flex-wrap gap-2 mb-8">
    {% for code, count in stats.status_codes.items() %}
    <span class="inline-flex items-center gap-1 px-3 py-1 rounded-full text-sm font-medium
        {% if code < 400 %}bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300
        {% elif code < 500 %}bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-300
        {% else %}bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300{% endif %}">
        {{ code }}: {{ count }}
    </span>
    {% endfor %}
</div>
{% endif %}

<h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-3">Recent Requests</h2>

<form class="flex flex-wrap gap-3 mb-4 items-end"
      hx-get="/admin/endpoints/{{ endpoint_name }}"
      hx-target="#audit-rows"
      hx-push-url="true">
    <div>
        <label for="client_ip" class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Client IP</label>
        <input type="text" name="client_ip" id="client_ip" value="{{ filters.client_ip or '' }}"
               class="border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 rounded px-2 py-1 text-sm w-40 focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100"
               placeholder="e.g. 10.0.0.1">
    </div>
    <button type="submit"
            class="bg-co-purple text-white px-4 py-1 rounded text-sm font-medium hover:bg-co-purple-700 focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100 focus:ring-offset-1 dark:ring-offset-gray-800 min-h-[32px]">
        Filter
    </button>
    <a href="/admin/endpoints/{{ endpoint_name }}"
       class="text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 underline self-center">Clear</a>
</form>

<div class="overflow-x-auto bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
    <table class="w-full text-left">
        <thead class="sticky top-0 bg-gray-50 dark:bg-gray-700 border-b border-gray-200 dark:border-gray-600 text-xs text-gray-500 dark:text-gray-400 uppercase tracking-wide">
            <tr>
                <th class="px-3 py-2">Time</th>
                <th class="px-3 py-2">IP</th>
                <th class="px-3 py-2">Method</th>
                <th class="px-3 py-2">Endpoint</th>
                <th class="px-3 py-2">Status</th>
                <th class="px-3 py-2 text-right">Latency</th>
                <th class="px-3 py-2">Provider</th>
                <th class="px-3 py-2">Cache</th>
            </tr>
        </thead>
        <tbody id="audit-rows">
            {% include "admin/audit/_rows.html" %}
        </tbody>
    </table>
</div>

{% if total_pages > 1 %}
<nav class="flex items-center gap-2 mt-4 text-sm" aria-label="Pagination">
    {% if page > 1 %}
    <a href="?page={{ page - 1 }}{% if filters.client_ip %}&client_ip={{ filters.client_ip }}{% endif %}"
       class="px-3 py-1 rounded border border-gray-300 dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100">&laquo; Prev</a>
    {% endif %}
    <span class="text-gray-500 dark:text-gray-400">Page {{ page }} of {{ total_pages }}</span>
    {% if page < total_pages %}
    <a href="?page={{ page + 1 }}{% if filters.client_ip %}&client_ip={{ filters.client_ip }}{% endif %}"
       class="px-3 py-1 rounded border border-gray-300 dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100">Next &raquo;</a>
    {% endif %}
</nav>
{% endif %}
{% endblock %}
```

- [ ] **Step 2: Run admin tests**

Run: `uv run pytest tests/unit/test_admin_views.py --no-cov -x -v`
Expected: all PASS

- [ ] **Step 3: Commit**

```bash
git add src/address_validator/templates/admin/endpoints/detail.html
git commit -m "#46 feat: migrate endpoint detail template to co-purple + dark mode"
```

---

## Task 10: Provider Detail Template — Color Migration + Dark Variants

**Files:**
- Modify: `src/address_validator/templates/admin/providers/detail.html`

- [ ] **Step 1: Update providers/detail.html**

Same pattern. Key additions beyond the standard migration:
- `bg-blue-600` (quota progress bar) → `bg-co-purple`
- `bg-gray-200` (progress track) → add `dark:bg-gray-600`
- Validation status badges: add `dark:` variants (same as endpoint status codes)

Full replacement for `providers/detail.html`:

```html
{% extends "admin/base.html" %}
{% block title %}{{ provider_name | upper }} — Provider{% endblock %}
{% block content %}
<h1 class="text-2xl font-bold text-gray-800 dark:text-gray-100 mb-6">{{ provider_name | upper }} Provider</h1>

<div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">Requests Today</p>
        <p class="text-2xl font-bold text-gray-900 dark:text-gray-100">{{ stats.today | default(0) }}</p>
    </div>
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">Total Requests</p>
        <p class="text-2xl font-bold text-gray-900 dark:text-gray-100">{{ stats.total | default(0) }}</p>
    </div>
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">Cache Hit Rate</p>
        <p class="text-2xl font-bold text-gray-900 dark:text-gray-100">
            {% if stats.get("cache_hit_rate") is not none %}{{ "%.1f" | format(stats.cache_hit_rate) }}%{% else %}N/A{% endif %}
        </p>
    </div>
    {% if quota %}
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">Daily Quota</p>
        <div class="flex items-baseline gap-2">
            <p class="text-2xl font-bold text-gray-900 dark:text-gray-100">{{ quota.remaining }}</p>
            <p class="text-sm text-gray-500 dark:text-gray-400">/ {{ quota.limit }}</p>
        </div>
        <div class="mt-2 w-full bg-gray-200 dark:bg-gray-600 rounded-full h-2" role="progressbar"
             aria-valuenow="{{ quota.remaining }}" aria-valuemin="0" aria-valuemax="{{ quota.limit }}"
             aria-label="{{ provider_name }} quota usage">
            <div class="bg-co-purple h-2 rounded-full"
                 style="width: {{ ((quota.remaining / quota.limit) * 100) | int if quota.limit > 0 else 0 }}%"></div>
        </div>
    </div>
    {% else %}
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">Daily Quota</p>
        <p class="text-2xl font-bold text-gray-400 dark:text-gray-500">N/A</p>
    </div>
    {% endif %}
</div>

{% if stats.validation_statuses %}
<h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-3">Validation Statuses</h2>
<div class="flex flex-wrap gap-2 mb-8">
    {% for status, count in stats.validation_statuses.items() %}
    <span class="inline-flex items-center gap-1 px-3 py-1 rounded-full text-sm font-medium
        {% if status == 'confirmed' or status == 'confirmed_missing_secondary' %}bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300
        {% elif status == 'confirmed_bad_secondary' %}bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-300
        {% elif status == 'not_confirmed' %}bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300
        {% else %}bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300{% endif %}">
        {{ status }}: {{ count }}
    </span>
    {% endfor %}
</div>
{% endif %}

<h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-3">Recent Requests</h2>

<form class="flex flex-wrap gap-3 mb-4 items-end"
      hx-get="/admin/providers/{{ provider_name }}"
      hx-target="#audit-rows"
      hx-push-url="true">
    <div>
        <label for="client_ip" class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Client IP</label>
        <input type="text" name="client_ip" id="client_ip" value="{{ filters.client_ip or '' }}"
               class="border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 rounded px-2 py-1 text-sm w-40 focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100"
               placeholder="e.g. 10.0.0.1">
    </div>
    <button type="submit"
            class="bg-co-purple text-white px-4 py-1 rounded text-sm font-medium hover:bg-co-purple-700 focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100 focus:ring-offset-1 dark:ring-offset-gray-800 min-h-[32px]">
        Filter
    </button>
    <a href="/admin/providers/{{ provider_name }}"
       class="text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 underline self-center">Clear</a>
</form>

<div class="overflow-x-auto bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
    <table class="w-full text-left">
        <thead class="sticky top-0 bg-gray-50 dark:bg-gray-700 border-b border-gray-200 dark:border-gray-600 text-xs text-gray-500 dark:text-gray-400 uppercase tracking-wide">
            <tr>
                <th class="px-3 py-2">Time</th>
                <th class="px-3 py-2">IP</th>
                <th class="px-3 py-2">Method</th>
                <th class="px-3 py-2">Endpoint</th>
                <th class="px-3 py-2">Status</th>
                <th class="px-3 py-2 text-right">Latency</th>
                <th class="px-3 py-2">Provider</th>
                <th class="px-3 py-2">Cache</th>
            </tr>
        </thead>
        <tbody id="audit-rows">
            {% include "admin/audit/_rows.html" %}
        </tbody>
    </table>
</div>

{% if total_pages > 1 %}
<nav class="flex items-center gap-2 mt-4 text-sm" aria-label="Pagination">
    {% if page > 1 %}
    <a href="?page={{ page - 1 }}{% if filters.client_ip %}&client_ip={{ filters.client_ip }}{% endif %}"
       class="px-3 py-1 rounded border border-gray-300 dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100">&laquo; Prev</a>
    {% endif %}
    <span class="text-gray-500 dark:text-gray-400">Page {{ page }} of {{ total_pages }}</span>
    {% if page < total_pages %}
    <a href="?page={{ page + 1 }}{% if filters.client_ip %}&client_ip={{ filters.client_ip }}{% endif %}"
       class="px-3 py-1 rounded border border-gray-300 dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100">Next &raquo;</a>
    {% endif %}
</nav>
{% endif %}
{% endblock %}
```

- [ ] **Step 2: Run admin tests**

Run: `uv run pytest tests/unit/test_admin_views.py --no-cov -x -v`
Expected: all PASS

- [ ] **Step 3: Commit**

```bash
git add src/address_validator/templates/admin/providers/detail.html
git commit -m "#46 feat: migrate provider detail template to co-purple + dark mode"
```

---

## Task 11: Tailwind CSS Rebuild

The pre-commit hook rebuilds Tailwind automatically, but we should verify the build works with all new classes.

**Files:**
- Rebuild: `src/address_validator/static/admin/css/tailwind.css`

- [ ] **Step 1: Run full Tailwind build**

Run: `scripts/build-css.sh`
Expected: exits 0

- [ ] **Step 2: Verify key classes are in the built CSS**

Run: `grep -c 'co-purple' src/address_validator/static/admin/css/tailwind.css`
Expected: non-zero count (the built CSS contains our custom color classes)

Run: `grep -c '\.dark ' src/address_validator/static/admin/css/tailwind.css`
Expected: non-zero count (dark mode variants are generated)

- [ ] **Step 3: Commit (if not already staged by pre-commit)**

```bash
git add src/address_validator/static/admin/css/tailwind.css
git commit -m "#46 chore: rebuild Tailwind CSS with co-purple palette and dark mode"
```

---

## Task 12: Write `docs/STYLE.md`

**Files:**
- Create: `docs/STYLE.md`

- [ ] **Step 1: Write the style guide**

Create `docs/STYLE.md` with the following content:

```markdown
# Style Guide — Address Validator

A project of [Cannabis Observer](https://cannabis.observer/) 🌱🏛️🔍

This guide codifies the visual design, accessibility standards, responsive patterns, and
performance rules for the Address Validator admin dashboard.

## Brand Identity

### Logo & Wordmark

- **Icon**: `static/admin/images/cannabis_observer-icon-square.svg` — 510×510px square with green-to-purple radial gradient
- **Wordmark**: `static/admin/images/cannabis_observer-name.svg` — available but not used in dashboard UI
- **Header**: icon (32×32, `w-8 h-8`) + "Address Validator" bold text
- **Footer**: "A project of Cannabis Observer" + icon (16×16) + decorative emoji `🌱🏛️🔍`
- **Favicon**: inline SVG data URI — purple magnifying glass matching brand color

### Brand Colors

| Token | Hex | Usage |
|-------|-----|-------|
| `co-purple` (DEFAULT/600) | `#6d4488` | Primary accent: buttons, links, active nav, progress bars |
| `co-purple-50` | `#f5f0f8` | Active nav background (light mode) |
| `co-purple-100` | `#ebe1f1` | Focus rings (dark mode) |
| `co-purple-700` | `#5a3870` | Hover states, focus rings (light mode) |
| `co-purple-800` | `#472c59` | Active nav background (dark mode) |
| `co-green` | `#8cbe69` | Reserved — not actively used in UI |

### Semantic Status Colors

Strictly for status badges and indicators — **never** replaced with brand colors:

| Meaning | Light Background | Light Text | Dark Background | Dark Text |
|---------|-----------------|------------|-----------------|-----------|
| Success / 2xx | `bg-green-100` | `text-green-800` | `bg-green-900` | `text-green-300` |
| Warning / 4xx | `bg-yellow-100` | `text-yellow-800` | `bg-yellow-900` | `text-yellow-300` |
| Error / 5xx | `bg-red-100` | `text-red-800` | `bg-red-900` | `text-red-300` |
| Neutral | `bg-gray-100` | `text-gray-800` | `bg-gray-700` | `text-gray-300` |

## Dark Mode

### Strategy

- Tailwind `darkMode: 'class'` — toggled via `<html class="dark">`
- Default: follows `prefers-color-scheme`
- Manual toggle persists to `localStorage` key `"theme"`
- Toggle button in header: sun icon (dark mode) / moon icon (light mode)

### Color Mapping

| Element | Light | Dark |
|---------|-------|------|
| Page background | `bg-gray-50` | `bg-gray-900` |
| Card/panel background | `bg-white` | `bg-gray-800` |
| Card border | `border-gray-200` | `border-gray-700` |
| Primary text | `text-gray-900` | `text-gray-100` |
| Secondary text | `text-gray-500` | `text-gray-400` |
| Muted text | `text-gray-400` | `text-gray-500` |
| Table header background | `bg-gray-50` | `bg-gray-700` |
| Row hover | `hover:bg-gray-50` | `hover:bg-gray-700` |
| Input background | `bg-white` | `bg-gray-800` |
| Input border | `border-gray-300` | `border-gray-600` |
| Focus ring | `ring-co-purple-700` | `ring-co-purple-100` |
| Ring offset | (default) | `ring-offset-gray-800` |

## Accessibility — WCAG 2.1 AA

### Color Contrast

- **Normal text** (< 18px or < 14px bold): 4.5:1 minimum
- **Large text** (≥ 18px or ≥ 14px bold): 3:1 minimum
- **UI components and graphical objects**: 3:1 minimum
- **Muted text minimum**: `text-gray-600` (light), `text-gray-400` (dark)

### Focus Management

All interactive elements must have visible focus indicators:

```
focus:outline-none focus:ring-2 focus:ring-co-purple-700 focus:ring-offset-1
dark:focus:ring-co-purple-100 dark:ring-offset-gray-800
```

### Skip-to-Content

First child of `<body>`: a skip link targeting `#main-content`. Visually hidden (`sr-only`),
becomes visible on focus. Uses the `.skip-to-content` utility class from `input.css`.

### Touch Targets

- **Primary interactive elements** (nav links, buttons, toggles): min 44×44px (`min-h-[44px] min-w-[44px]`)
- **Secondary buttons** (pagination, filters): min 32px height (`min-h-[32px]`)

### ARIA Patterns

| Element | Attributes |
|---------|-----------|
| Navigation landmark | `<nav aria-label="Admin navigation">` |
| Main content | `<main id="main-content" aria-live="polite">` |
| Pagination | `<nav aria-label="Pagination">` |
| Progress bar | `role="progressbar"`, `aria-valuenow`, `aria-valuemin`, `aria-valuemax`, `aria-label` |
| Dark mode toggle | `aria-label="Toggle dark mode"` |
| Hamburger nav | `aria-expanded`, `aria-controls="mobile-nav"`, `aria-label="Open navigation menu"` |

### Icons & Emojis

- **Decorative SVGs**: `aria-hidden="true"` — used for sun/moon icons, hamburger icon, etc.
- **Meaningful SVGs**: `role="img"` + descriptive `aria-label`
- **Decorative emojis**: wrap in `<span aria-hidden="true">emoji</span>`
- Never use emoji or icon as the sole conveyor of meaning — always pair with text

### Semantic HTML

- Landmark regions: `<header>`, `<nav>`, `<main>`, `<footer>`
- One `<h1>` per page, sequential heading hierarchy (`<h2>` → `<h3>` → `<h4>`)
- Tables: `<thead>` + `<tbody>`, never `<div>` tables
- Forms: every input has an associated `<label>`

## Responsive Design

### Breakpoints

| Breakpoint | Layout |
|-----------|--------|
| Default (mobile) | Single column, hamburger nav, stacked cards |
| `sm` (640px+) | 2-column stat grids |
| `md` (768px+) | Desktop sidebar nav visible, hamburger hidden |
| `lg` (1024px+) | 3–4 column stat grids |

### Navigation

- **Mobile (< md)**: hamburger button in header → dropdown drawer (`#mobile-nav`)
  - Toggle managed by `static/admin/js/nav.js`
  - Escape key closes drawer and returns focus to toggle button
  - `aria-expanded` tracks open/close state
- **Desktop (≥ md)**: persistent left sidebar (256px / `w-56`)

### Tables

- Container: `overflow-x-auto` (horizontal scroll within card, not viewport)
- Header: `sticky top-0` on `<thead>` for scroll visibility
- Grid detail layouts: `grid-cols-1 sm:grid-cols-2`

## Data Visualizations

Guidelines for charts and sparklines (see #47):

### Color-Blind Safety

- Never rely on red/green distinction alone
- Use shape, pattern, or label in addition to color
- Recommended palette: blue, orange, teal, magenta

### Motion

- Respect `prefers-reduced-motion: reduce` — disable animations, show static end state
- UI feedback transitions: ≤ 200ms
- Data transitions: ≤ 500ms

### Text Alternatives

- Every chart must have a text alternative: summary `aria-label` or adjacent data table
- Sparklines: `role="img"` + `aria-label` describing trend (e.g., "Requests trending up over 24h")

### Contrast

- Data lines/fills: 3:1 minimum against background
- Axis labels and legends: 4.5:1 minimum
- Grid lines: `gray-300` (light) / `gray-600` (dark) — subtle but visible

## Performance

### CSS

- **Pre-built Tailwind only** — never CDN or runtime JIT
- Built via `scripts/build-css.sh` (standalone Tailwind CLI binary)
- Cache-busted via git SHA: `?v={{ css_version }}`
- Pre-commit hook auto-rebuilds and stages on template/CSS/config changes

### Fonts

- System UI stack: `ui-sans-serif, system-ui, sans-serif` — no custom font downloads
- If custom fonts added later: `font-display: swap` required

### Layout Stability

- SVG/images: explicit `width` + `height` attributes to prevent CLS
- HTMX swap targets: keep tight (swap table rows, not entire page sections)
- No layout shift from HTMX `.htmx-indicator` show/hide

### JavaScript

- Separate files in `static/admin/js/`, not inline `<script>` blocks
- Loaded with `defer` attribute
- No render-blocking JS in `<head>` — **one exception**: a tiny inline `<script>` in `<head>` reads `localStorage`/`prefers-color-scheme` and applies the `dark` class synchronously to prevent FOUC
- HTMX loaded from CDN with `defer`

## Technical Rules

### Tailwind Config

- Config: `tailwind.config.js` at project root
- Input: `src/address_validator/static/admin/css/input.css`
- Output: `src/address_validator/static/admin/css/tailwind.css`
- Content glob: `./src/address_validator/templates/**/*.html`
- Custom colors defined in `theme.extend.colors`

### File Locations

| Type | Path |
|------|------|
| Templates | `src/address_validator/templates/admin/` |
| CSS source | `src/address_validator/static/admin/css/input.css` |
| CSS built | `src/address_validator/static/admin/css/tailwind.css` |
| JavaScript | `src/address_validator/static/admin/js/` |
| Images | `src/address_validator/static/admin/images/` |

### Adding New Pages

1. Create template extending `admin/base.html`
2. Set `active_nav` in template context
3. Add nav links in base.html (both desktop sidebar and mobile drawer)
4. Use established component patterns (cards, tables, forms) from this guide
5. Include `dark:` variants on all color utilities
6. Run `scripts/build-css.sh` (or let pre-commit handle it)
```

- [ ] **Step 2: Commit**

```bash
git add docs/STYLE.md
git commit -m "#46 docs: add STYLE.md with WCAG 2.1 AA, dark mode, responsive, and performance guidelines"
```

---

## Task 13: Update AGENTS.md — Reference Style Guide

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Add STYLE.md reference to architecture tree**

In the `## Architecture` section, after the `static/admin/css/` line, add:

```
static/admin/js/    theme.js (dark mode), nav.js (hamburger)
static/admin/images/ Cannabis Observer brand SVGs
```

And in the same tree, after the last line, add a reference line:

```
docs/STYLE.md       visual design, a11y, responsive, and performance standards
```

- [ ] **Step 2: Commit**

```bash
git add AGENTS.md
git commit -m "#46 docs: reference STYLE.md and new static assets in AGENTS.md"
```

---

## Task 14: Full Test Suite + Visual Verification

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest --no-cov -x`
Expected: all tests PASS

- [ ] **Step 2: Run linting**

Run: `uv run ruff check .`
Expected: clean (no errors)

- [ ] **Step 3: Verify the app starts**

Run: `sudo systemctl restart address-validator && sleep 2 && curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/`
Expected: `200`

- [ ] **Step 4: Verify admin dashboard loads with new branding**

Run: `curl -s -H "X-ExeDev-UserID: test" -H "X-ExeDev-Email: test@test.com" http://localhost:8000/admin/ | grep -c "Cannabis Observer"`
Expected: `2` (header icon alt text + footer link)

Run: `curl -s -H "X-ExeDev-UserID: test" -H "X-ExeDev-Email: test@test.com" http://localhost:8000/admin/ | grep -c "theme-toggle"`
Expected: `1`

Run: `curl -s -H "X-ExeDev-UserID: test" -H "X-ExeDev-Email: test@test.com" http://localhost:8000/admin/ | grep -c "nav-toggle"`
Expected: `1`

Run: `curl -s -H "X-ExeDev-UserID: test" -H "X-ExeDev-Email: test@test.com" http://localhost:8000/admin/ | grep -c "co-purple"`
Expected: non-zero (purple classes present in HTML)
