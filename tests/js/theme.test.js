import { describe, it, expect, beforeEach, vi } from 'vitest';

const SRC = '../../src/address_validator/static/admin/js/theme.js';

function stubMatchMedia(matches = false) {
    const mql = {
        matches,
        addEventListener: vi.fn(),
    };
    vi.stubGlobal('matchMedia', vi.fn(() => mql));
    return mql;
}

function resetDOM() {
    document.documentElement.className = '';
    localStorage.clear();
}

describe('apply()', () => {
    beforeEach(() => {
        resetDOM();
        vi.resetModules();
        stubMatchMedia();
    });

    it('adds dark class for dark theme', async () => {
        const { apply } = await import(SRC);
        apply('dark');
        expect(document.documentElement.classList.contains('dark')).toBe(true);
    });

    it('removes dark class for light theme', async () => {
        document.documentElement.classList.add('dark');
        const { apply } = await import(SRC);
        apply('light');
        expect(document.documentElement.classList.contains('dark')).toBe(false);
    });
});

describe('toggle click', () => {
    it('toggles dark→light→dark and persists to localStorage', async () => {
        resetDOM();
        vi.resetModules();
        stubMatchMedia();
        localStorage.setItem('theme', 'dark');
        document.body.innerHTML = '<button id="theme-toggle">Toggle</button>';

        await import(SRC);
        const btn = document.getElementById('theme-toggle');

        /* dark → light */
        btn.click();
        expect(document.documentElement.classList.contains('dark')).toBe(false);
        expect(localStorage.getItem('theme')).toBe('light');

        /* light → dark */
        btn.click();
        expect(document.documentElement.classList.contains('dark')).toBe(true);
        expect(localStorage.getItem('theme')).toBe('dark');
    });
});

describe('system preference change', () => {
    beforeEach(() => {
        resetDOM();
        vi.resetModules();
    });

    it('applies system preference when no localStorage override', async () => {
        const mql = stubMatchMedia(false);
        let changeHandler;
        mql.addEventListener = vi.fn((_, handler) => { changeHandler = handler; });

        await import(SRC);

        changeHandler({ matches: true });
        expect(document.documentElement.classList.contains('dark')).toBe(true);
    });

    it('ignores system preference when localStorage is set', async () => {
        localStorage.setItem('theme', 'light');
        const mql = stubMatchMedia(false);
        let changeHandler;
        mql.addEventListener = vi.fn((_, handler) => { changeHandler = handler; });

        await import(SRC);

        changeHandler({ matches: true });
        expect(document.documentElement.classList.contains('dark')).toBe(false);
    });
});
