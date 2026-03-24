/**
 * theme.js — Dark mode toggle with localStorage persistence.
 *
 * Default: follows prefers-color-scheme. Manual toggle overrides and
 * persists to localStorage. An inline script in <head> handles
 * synchronous init to prevent FOUC; this module registers listeners.
 */
export var KEY = 'theme';

export function apply(theme) {
    document.documentElement.classList.toggle('dark', theme === 'dark');
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

/* Toggle button — event delegation survives hx-boost DOM swaps */
document.addEventListener('click', function (e) {
    if (!e.target.closest('#theme-toggle')) return;
    var next = document.documentElement.classList.contains('dark') ? 'light' : 'dark';
    localStorage.setItem(KEY, next);
    apply(next);
});
