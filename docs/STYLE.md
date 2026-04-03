# Style Guide — Address Validator

A project of [Cannabis Observer](https://cannabis.observer/) 🌱🏛️🔍

This guide codifies the visual design, accessibility standards, responsive patterns, and
performance rules for the Address Validator admin dashboard.

## Brand Identity

### Logo & Wordmark

- **Icon**: `src/address_validator/static/admin/images/cannabis_observer-icon-square.svg` — 510×510px square with green-to-purple radial gradient
- **Wordmark**: `src/address_validator/static/admin/images/cannabis_observer-name.svg` — available but not used in dashboard UI
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
- **Desktop (≥ md)**: persistent left sidebar (192px / `w-48 shrink-0`)

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
