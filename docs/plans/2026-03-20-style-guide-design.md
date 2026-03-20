# Style Guide & Design System — Design Doc

**Issue:** #46
**Date:** 2026-03-20

## Summary

Create `docs/STYLE.md` codifying WCAG 2.1 AA accessibility standards, contemporary UX
patterns, responsive design, and performance guidelines. Implement the Cannabis Observer
brand identity, dark mode, and hamburger navigation across the admin dashboard.

Prototype reference: [CannObserv/wslcb-licensing-tracker STYLE.md](https://github.com/CannObserv/wslcb-licensing-tracker/blob/main/docs/STYLE.md)

## Decisions

| Topic | Decision |
|---|---|
| Brand accent | `co-purple` (#6d4488) replaces blue-600; full palette (50/100/600/700/800) |
| Brand green | `co-green` (#8cbe69) — reserved, not active in UI |
| Dark mode | Full support via `darkMode: 'class'`; `prefers-color-scheme` default, localStorage override |
| Mobile nav | Hamburger/drawer below `md` breakpoint (replaces horizontal scroll) |
| JS strategy | Separate files (`nav.js`, `theme.js`), not inline; loaded at end of `<body>` with `defer` |
| Visualizations | Guidelines included now for upcoming #47 work |
| Scope | Style guide doc + all implementation changes in one issue |

## 1. Brand Identity

### Header
- Cannabis Observer icon SVG (32×32) + "Address Validator" bold text
- Same pattern as prototype: `<img>` + `<span>` in an `<a>`

### Footer
```html
<footer class="border-t border-gray-200 dark:border-gray-700 mt-12">
  <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
    <div class="text-xs text-gray-500 dark:text-gray-400 flex items-center justify-center gap-1">
      A project of
      <a href="https://cannabis.observer/"
         class="inline-flex items-center gap-1 font-medium text-co-purple hover:text-co-purple-700 dark:text-co-purple-100 dark:hover:text-white"
         target="_blank" rel="noopener noreferrer">
        <img src="/static/admin/images/cannabis_observer-icon-square.svg" alt="" class="w-4 h-4">
        Cannabis Observer
      </a>
      <span aria-hidden="true">🌱🏛️🔍</span>
    </div>
  </div>
</footer>
```

### Favicon
Inline SVG data URI (magnifying glass) matching prototype.

### Assets
- `static/admin/images/cannabis_observer-icon-square.svg` — square icon
- `static/admin/images/cannabis_observer-name.svg` — wordmark (available but not used in dashboard)

## 2. Color System

### Tailwind Config Extension
```js
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
```

### Usage Rules
- **Primary accent** (`co-purple`): buttons, links, active nav states, focus rings
- **Semantic colors** (green/yellow/red from default Tailwind): status badges, error rates — never replaced with brand colors
- **Muted text minimum**: `text-gray-600` (light), `text-gray-400` (dark) — ensures WCAG AA contrast

### Template Migration
Replace all `blue-600` → `co-purple`, `blue-700` → `co-purple-700`, `blue-50` → `co-purple-50`,
`blue-500` → `co-purple` in focus rings across all admin templates.

## 3. Dark Mode

### Strategy
- `darkMode: 'class'` in Tailwind config
- `<html>` gets `class="dark"` toggled by JS
- Default: follow `prefers-color-scheme`; manual toggle overrides, persisted to `localStorage`

### Color Mapping
| Element | Light | Dark |
|---|---|---|
| Body background | `bg-gray-50` | `dark:bg-gray-900` |
| Card background | `bg-white` | `dark:bg-gray-800` |
| Card border | `border-gray-200` | `dark:border-gray-700` |
| Primary text | `text-gray-900` | `dark:text-gray-100` |
| Secondary text | `text-gray-500` | `dark:text-gray-400` |
| Header/nav bg | `bg-white` | `dark:bg-gray-800` |
| Table header bg | `bg-gray-50` | `dark:bg-gray-700` |
| Row hover | `hover:bg-gray-50` | `dark:hover:bg-gray-700` |
| Focus ring | `ring-co-purple` | `ring-co-purple-100` |
| Status badges | Light bg + dark text | Same hues, slightly adjusted opacity |

### Toggle UI
Sun/moon icon button in header, next to user email. Minimum 44×44px touch target.

### JS — `static/admin/js/theme.js`
```js
(function () {
  const key = 'theme';
  const html = document.documentElement;

  function apply(theme) {
    html.classList.toggle('dark', theme === 'dark');
  }

  const stored = localStorage.getItem(key);
  if (stored) {
    apply(stored);
  } else {
    apply(window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  }

  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function (e) {
    if (!localStorage.getItem(key)) apply(e.matches ? 'dark' : 'light');
  });

  document.addEventListener('DOMContentLoaded', function () {
    var btn = document.getElementById('theme-toggle');
    if (!btn) return;
    btn.addEventListener('click', function () {
      var next = html.classList.contains('dark') ? 'light' : 'dark';
      localStorage.setItem(key, next);
      apply(next);
    });
  });
}());
```

## 4. Navigation — Hamburger/Drawer

### Pattern
Ported from prototype `base.html`:
- `<button id="nav-toggle">` with hamburger/X SVG icons
- `<div id="mobile-nav" class="hidden md:hidden">` dropdown drawer
- `aria-expanded`, `aria-controls="mobile-nav"`, `aria-label="Open navigation menu"`
- Escape key closes drawer and returns focus to toggle button

### JS — `static/admin/js/nav.js`
```js
(function () {
  var btn = document.getElementById('nav-toggle');
  var menu = document.getElementById('mobile-nav');
  var iconOpen = document.getElementById('nav-icon-open');
  var iconClose = document.getElementById('nav-icon-close');

  if (!btn || !menu) return;

  function closeMenu() {
    menu.classList.add('hidden');
    btn.setAttribute('aria-expanded', 'false');
    iconOpen.classList.remove('hidden');
    iconClose.classList.add('hidden');
  }

  btn.addEventListener('click', function () {
    var open = menu.classList.toggle('hidden');
    btn.setAttribute('aria-expanded', String(!open));
    iconOpen.classList.toggle('hidden', !open);
    iconClose.classList.toggle('hidden', open);
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !menu.classList.contains('hidden')) {
      closeMenu();
      btn.focus();
    }
  });
}());
```

## 5. Responsive Design

### Breakpoints
| Breakpoint | Layout |
|---|---|
| Default (mobile) | Single column, hamburger nav, stacked cards |
| `sm` (640px+) | 2-col stat grids |
| `md` (768px+) | Sidebar nav visible, hamburger hidden, main content area |
| `lg` (1024px+) | 3–4 col stat grids |

### Rules
- Min touch target: 44×44px for all interactive elements
- Detail grids: `grid-cols-1 sm:grid-cols-2`
- Tables: `overflow-x-auto` container, sticky `<thead>` on scroll
- No horizontal scroll on viewport (tables scroll within container)

## 6. Accessibility (WCAG 2.1 AA)

### Color Contrast
- Normal text: 4.5:1 minimum
- Large text (18px+ or 14px+ bold): 3:1 minimum
- UI components and graphical objects: 3:1 minimum

### Focus Management
- All interactive elements: `focus:outline-none focus:ring-2 focus:ring-co-purple-700 focus:ring-offset-1`
- Dark mode: `dark:focus:ring-co-purple-100 dark:ring-offset-gray-800`
- Skip-to-content link: visually hidden, visible on focus, targets `<main>`

### ARIA Patterns
- `<nav aria-label="...">` on all navigation regions
- `<main aria-live="polite">` for HTMX swap targets
- Progress bars: `role="progressbar"` with `aria-valuenow/min/max` + `aria-label`
- Pagination: `<nav aria-label="Pagination">`

### Icons & Emojis
- Decorative SVGs: `aria-hidden="true"`
- Meaningful SVGs: `role="img"` + descriptive `aria-label`
- Decorative emojis: `<span aria-hidden="true">emoji</span>`
- Never use emoji as sole indicator — always pair with text or icon

### Semantic HTML
- `<header>`, `<nav>`, `<main>`, `<footer>` landmark regions
- `<table>` with `<thead>`/`<tbody>` for tabular data
- `<form>` with associated `<label>` elements
- Heading hierarchy: one `<h1>` per page, sequential `<h2>`–`<h4>`

## 7. Data Visualizations

Guidelines for #47 and future chart work:

### Color-Blind Safety
- Never rely on red/green distinction alone
- Use shape, pattern, or label in addition to color
- Recommended palette: blue, orange, teal, magenta (distinguishable in all common deficiencies)

### Motion
- Respect `prefers-reduced-motion: reduce` — disable animations, show static end state
- Transitions should be ≤200ms for UI feedback, ≤500ms for data transitions

### Text Alternatives
- Every chart must have a text alternative: summary `aria-label` or adjacent data table
- Sparklines: `role="img"` + `aria-label` describing trend (e.g. "Requests trending up over 24h")

### Contrast
- Data lines/fills: 3:1 minimum against background
- Axis labels and legends: 4.5:1 minimum
- Grid lines: subtle but visible (gray-300 light / gray-600 dark)

## 8. Performance

### CSS
- Pre-built Tailwind only — never CDN or runtime JIT
- Cache-busted via git SHA: `?v={{ css_version }}`
- Run `npx tailwindcss` build after template changes (pre-commit hook handles this)

### Fonts
- System UI stack — no custom font downloads
- If custom fonts added later: `font-display: swap` required

### Layout Stability
- SVG/images: explicit `width` + `height` attributes to prevent CLS
- HTMX swaps: keep targets tight (swap table rows, not entire sections)
- No layout shift from HTMX indicator show/hide

### Loading
- `<script>` tags at end of `<body>` or with `defer`
- No render-blocking JS in `<head>`
- HTMX loaded from CDN with integrity hash (existing pattern)

## 9. File Changes

| Action | File |
|---|---|
| **Create** | `docs/STYLE.md` |
| **Create** | `src/address_validator/static/admin/images/cannabis_observer-icon-square.svg` |
| **Create** | `src/address_validator/static/admin/images/cannabis_observer-name.svg` |
| **Create** | `src/address_validator/static/admin/js/theme.js` |
| **Create** | `src/address_validator/static/admin/js/nav.js` |
| **Modify** | `tailwind.config.js` — `co-purple`/`co-green` colors, `darkMode: 'class'` |
| **Modify** | `src/address_validator/static/admin/css/input.css` — dark mode base, skip-to-content |
| **Modify** | `src/address_validator/templates/admin/base.html` — brand header/footer, hamburger nav, dark mode toggle, JS imports, `<html class>` |
| **Modify** | `src/address_validator/templates/admin/dashboard.html` — `blue-*` → `co-purple-*`, `dark:` variants |
| **Modify** | `src/address_validator/templates/admin/audit/list.html` — same |
| **Modify** | `src/address_validator/templates/admin/audit/_rows.html` — same |
| **Modify** | `src/address_validator/templates/admin/endpoints/detail.html` — same |
| **Modify** | `src/address_validator/templates/admin/providers/detail.html` — same |
| **Modify** | `AGENTS.md` — reference `docs/STYLE.md` |
| **Rebuild** | `src/address_validator/static/admin/css/tailwind.css` — via build step |
