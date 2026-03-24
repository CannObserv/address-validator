/**
 * nav.js — Hamburger navigation toggle for mobile viewports.
 *
 * Toggles #mobile-nav visibility, swaps open/close SVG icons,
 * manages aria-expanded state, and closes on Escape key.
 */
export function initNav() {
    var btn = document.getElementById('nav-toggle');
    var menu = document.getElementById('mobile-nav');
    var iconOpen = document.getElementById('nav-icon-open');
    var iconClose = document.getElementById('nav-icon-close');

    if (!btn || !menu) return null;

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

    return { closeMenu: closeMenu };
}

initNav();
