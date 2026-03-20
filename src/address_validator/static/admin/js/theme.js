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
