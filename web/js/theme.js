// Appearance: light/dark/auto plus an accent palette.
//
// This file is loaded synchronously from <head>, before the body parses,
// because anything later means the browser paints one frame of the default
// dark theme first — a white flash on every launch for light-mode users.
// It therefore cannot depend on app.js, api(), or the DOM.

const THEME_MODES = ['auto', 'dark', 'light'];
const THEME_ACCENTS = [
    { id: 'carrot', label: 'Carrot', dot: '#f4813f' },
    { id: 'ember', label: 'Ember', dot: '#f2603f' },
    { id: 'amber', label: 'Amber', dot: '#e3a33a' },
    { id: 'orchid', label: 'Orchid', dot: '#b57ae0' },
    { id: 'teal', label: 'Teal', dot: '#3fbfa8' },
    { id: 'indigo', label: 'Indigo', dot: '#7c8cf0' },
];
const MODE_KEY = 'carrot.theme';
const ACCENT_KEY = 'carrot.accent';

function readPref(key, allowed, fallback) {
    try {
        const v = localStorage.getItem(key);
        return allowed.includes(v) ? v : fallback;
    } catch (e) {
        return fallback;   // private mode / storage disabled
    }
}

let themeMode = readPref(MODE_KEY, THEME_MODES, 'auto');
let themeAccent = readPref(ACCENT_KEY, THEME_ACCENTS.map(a => a.id), 'carrot');

// "auto" never reaches the DOM — the stylesheet only knows dark and light,
// so it is resolved here and re-resolved when the OS setting changes.
function prefersLight() {
    return !!(window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches);
}

function resolvedTheme() {
    if (themeMode === 'auto') return prefersLight() ? 'light' : 'dark';
    return themeMode;
}

function applyTheme() {
    const resolved = resolvedTheme();
    const root = document.documentElement;
    root.setAttribute('data-theme', resolved);
    root.setAttribute('data-accent', themeAccent);
    // Electron paints the window background from this before the page loads,
    // and it drives the form-control/scrollbar rendering.
    const meta = document.querySelector('meta[name="color-scheme"]');
    if (meta) meta.setAttribute('content', resolved);
    // Tell the desktop shell: it paints the window background before the
    // page loads, and it relays the palette to the quick-ask overlay.
    if (window.carrotAPI && window.carrotAPI.setAppearance) {
        const style = getComputedStyle(root);
        const background = style.getPropertyValue('--bg').trim();
        if (/^#[0-9a-fA-F]{6}$/.test(background)) {
            Promise.resolve(window.carrotAPI.setAppearance({
                background, theme: resolved, accent: themeAccent,
            })).catch(() => {});
        }
    }
    window.dispatchEvent(new CustomEvent('carrot-theme', {
        detail: { mode: themeMode, resolved, accent: themeAccent },
    }));
}

applyTheme();

if (window.matchMedia) {
    const mq = window.matchMedia('(prefers-color-scheme: light)');
    const onChange = () => { if (themeMode === 'auto') applyTheme(); };
    // Safari < 14 only has the deprecated form.
    if (mq.addEventListener) mq.addEventListener('change', onChange);
    else if (mq.addListener) mq.addListener(onChange);
}

// Persistence is local-first: localStorage is what makes the setting apply
// before the first network call. The backend copy is a best-effort mirror so
// the choice survives a cleared cache, and is never waited on.
function persist(key, configKey, value) {
    try { localStorage.setItem(key, value); } catch (e) { /* storage disabled */ }
    if (typeof api === 'function') {
        api(`/api/config/${configKey}`, {
            method: 'PUT', body: JSON.stringify(value),
        }).catch(() => {});
    }
}

function setThemeMode(mode) {
    if (!THEME_MODES.includes(mode)) return;
    themeMode = mode;
    applyTheme();
    persist(MODE_KEY, 'ui_theme', mode);
    renderThemePicker();
}

function setThemeAccent(accent) {
    if (!THEME_ACCENTS.some(a => a.id === accent)) return;
    themeAccent = accent;
    applyTheme();
    persist(ACCENT_KEY, 'ui_accent', accent);
    renderThemePicker();
}

// Pull the server's copy once at startup, for a machine whose localStorage
// was cleared. A stored local choice always wins — it is the more recent
// expression of intent, and it is what already painted.
async function syncThemeFromServer() {
    if (typeof api !== 'function') return;
    let cfg;
    try { cfg = await api('/api/config'); } catch (e) { return; }
    let changed = false;
    let stored = null;
    try { stored = localStorage.getItem(MODE_KEY); } catch (e) { /* ignore */ }
    if (!stored && THEME_MODES.includes(cfg.ui_theme)) {
        themeMode = cfg.ui_theme;
        changed = true;
    }
    let storedAccent = null;
    try { storedAccent = localStorage.getItem(ACCENT_KEY); } catch (e) { /* ignore */ }
    if (!storedAccent && THEME_ACCENTS.some(a => a.id === cfg.ui_accent)) {
        themeAccent = cfg.ui_accent;
        changed = true;
    }
    if (changed) applyTheme();
    renderThemePicker();
}

// ---------- Settings UI ----------

const MODE_DOTS = {
    auto: ['#1e2027', '#f6f4f1'],
    dark: ['#1e2027', '#1e2027'],
    light: ['#f6f4f1', '#f6f4f1'],
};

function renderThemePicker() {
    const modes = document.getElementById('theme-modes');
    if (modes) {
        modes.innerHTML = THEME_MODES.map((mode) => {
            const [a, b] = MODE_DOTS[mode];
            const label = mode === 'auto' ? 'Match system'
                : mode.charAt(0).toUpperCase() + mode.slice(1);
            return `<button type="button" class="theme-swatch" data-mode="${mode}"
                        aria-pressed="${mode === themeMode}">
                      <span class="theme-dot theme-mode-dot"
                            style="--tm-a:${a};--tm-b:${b}"></span>
                      <span>${label}</span>
                    </button>`;
        }).join('');
        modes.querySelectorAll('[data-mode]').forEach((el) => {
            el.onclick = () => setThemeMode(el.dataset.mode);
        });
    }

    const accents = document.getElementById('theme-accents');
    if (accents) {
        accents.innerHTML = THEME_ACCENTS.map(a => `
            <button type="button" class="theme-swatch" data-accent="${a.id}"
                    aria-pressed="${a.id === themeAccent}">
              <span class="theme-dot" style="background:${a.dot}"></span>
              <span>${a.label}</span>
            </button>`).join('');
        accents.querySelectorAll('[data-accent]').forEach((el) => {
            el.onclick = () => setThemeAccent(el.dataset.accent);
        });
    }
}

window.addEventListener('DOMContentLoaded', () => {
    renderThemePicker();
    syncThemeFromServer();
});
