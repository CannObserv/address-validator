import { describe, it, expect, beforeEach, vi } from 'vitest';

const SRC = '../../src/address_validator/static/admin/js/nav.js';

function setupDOM() {
    document.body.innerHTML = `
        <button id="nav-toggle" aria-expanded="false">
            <span id="nav-icon-open"></span>
            <span id="nav-icon-close" class="hidden"></span>
        </button>
        <nav id="mobile-nav" class="hidden"></nav>
    `;
}

describe('initNav()', () => {
    beforeEach(() => {
        document.body.innerHTML = '';
        vi.resetModules();
    });

    it('returns null when nav elements are missing', async () => {
        const { initNav } = await import(SRC);
        expect(initNav()).toBeNull();
    });

    it('returns closeMenu when elements are present', async () => {
        setupDOM();
        const { initNav } = await import(SRC);
        const result = initNav();
        expect(result).toHaveProperty('closeMenu');
        expect(typeof result.closeMenu).toBe('function');
    });
});

describe('toggle click and Escape key', () => {
    it('opens, closes, and handles Escape', async () => {
        vi.resetModules();
        setupDOM();
        await import(SRC);

        const btn = document.getElementById('nav-toggle');
        const menu = document.getElementById('mobile-nav');

        /* first click opens */
        btn.click();
        expect(menu.classList.contains('hidden')).toBe(false);
        expect(btn.getAttribute('aria-expanded')).toBe('true');
        expect(document.getElementById('nav-icon-open').classList.contains('hidden')).toBe(true);
        expect(document.getElementById('nav-icon-close').classList.contains('hidden')).toBe(false);

        /* second click closes */
        btn.click();
        expect(menu.classList.contains('hidden')).toBe(true);
        expect(btn.getAttribute('aria-expanded')).toBe('false');
        expect(document.getElementById('nav-icon-open').classList.contains('hidden')).toBe(false);
        expect(document.getElementById('nav-icon-close').classList.contains('hidden')).toBe(true);

        /* re-open, then Escape closes and focuses button */
        btn.click();
        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
        expect(menu.classList.contains('hidden')).toBe(true);
        expect(btn.getAttribute('aria-expanded')).toBe('false');
        expect(document.activeElement).toBe(btn);

        /* Escape when already closed is a no-op */
        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
        expect(menu.classList.contains('hidden')).toBe(true);
        expect(btn.getAttribute('aria-expanded')).toBe('false');
    });
});
